"""
Unit + integration tests for the Round-2 7-rule checkbox fill engine
(``src/engine/fill_rules``) and its wiring into ``_prepare_field_data``.

The rule functions are pure: each is exercised with mock ``Participant``
objects (a ``role`` attribute holding a simple ``.value`` object), both
positive, negative and edge cases. A final integration test drives a real
``DealData`` + ``MappingConfig`` through ``FormFillerPipeline._prepare_field_data``
to prove the point engine is actually wired (Evidence Gate).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

from src.engine.fill_rules import (  # noqa: E402
    Point,
    PointResult,
    RULE_REGISTRY,
    RuleContext,
    evaluate_point,
)


# ──────────────────────────────────────────────────────────────────────────
# Mock helpers
# ──────────────────────────────────────────────────────────────────────────


class _Role:
    """Enum-stand-in: has a ``.value`` like ParticipantRole."""

    def __init__(self, value: str):
        self.value = value


class _P:
    """Mock Participant exposing only ``.role`` (with ``.value``)."""

    def __init__(self, role_value: str):
        self.role = _Role(role_value)


def _ctx(
    participants=None,
    products=None,
    loan_purpose=None,
    product_name=None,
    canonical=None,
) -> RuleContext:
    return RuleContext(
        active_participants=participants if participants is not None else [],
        products=products or [],
        loan_purpose=loan_purpose,
        product_name=product_name,
        canonical_values=canonical or {},
    )


def _point(rule_type: int, blocks, params=None, point_id="PT", framework="*") -> Point:
    return Point(
        point_id=point_id,
        framework=framework,
        blocks=blocks,
        rule_type=rule_type,
        params=params or {},
    )


# ──────────────────────────────────────────────────────────────────────────
# Registry / dispatch
# ──────────────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_all_seven_rules_registered(self):
        assert sorted(RULE_REGISTRY.keys()) == [1, 2, 3, 4, 5, 6, 7]

    def test_evaluate_point_unknown_rule_type_returns_empty(self):
        pt = _point(rule_type=99, blocks=[{"block_id": "b", "members": ["x"]}])
        res = evaluate_point(pt, _ctx([_P("adós")]))
        assert res.ticks == {}

    def test_evaluate_point_dispatches_to_registered_fn(self):
        pt = _point(rule_type=1, blocks=[{"block_id": "b", "members": ["x"]}])
        res = evaluate_point(pt, _ctx([_P("adós")]))
        assert res.ticks == {"x": "igen"}
        assert isinstance(res, PointResult)
        assert res.point_id == "PT"


class TestRuleContextRole:
    def test_role_uses_value_when_present(self):
        assert RuleContext.__dataclass_fields__  # sanity: dataclass
        ctx = _ctx()
        assert ctx.role(_P("adós")) == "adós"

    def test_role_falls_back_to_str_when_no_value(self):
        class _Plain:
            role = "adóstárs"  # plain string, no .value

        ctx = _ctx()
        assert ctx.role(_Plain()) == "adóstárs"


# ──────────────────────────────────────────────────────────────────────────
# Rule 1 — feltétel nélküli teljes körű pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule1CheckAll:
    def test_with_active_participants_ticks_all(self):
        pt = _point(1, [
            {"block_id": "b1", "members": ["m1", "m2"]},
            {"block_id": "b2", "members": ["m3"]},
        ])
        res = RULE_REGISTRY[1](pt, _ctx([_P("adós")]))
        assert res.ticks == {"m1": "igen", "m2": "igen", "m3": "igen"}

    def test_without_participants_ticks_none(self):
        pt = _point(1, [{"block_id": "b1", "members": ["m1", "m2"]}])
        res = RULE_REGISTRY[1](pt, _ctx([]))
        assert res.ticks == {"m1": "nem", "m2": "nem"}

    def test_empty_blocks_yields_empty_ticks(self):
        pt = _point(1, [])
        res = RULE_REGISTRY[1](pt, _ctx([_P("adós")]))
        assert res.ticks == {}


# ──────────────────────────────────────────────────────────────────────────
# Rule 2 — szerepkörös, többszörös pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule2RoleMulticheck:
    BLOCKS = [{"block_id": "b1", "members": ["role_ados", "fedez_1", "role_adostars", "fedez_2"]}]

    def test_single_role_ticks_only_its_members(self):
        params = {"role_to_ticks": {
            "adós": ["role_ados", "fedez_1"],
            "adóstárs": ["role_adostars", "fedez_2"],
        }}
        pt = _point(2, self.BLOCKS, params)
        res = RULE_REGISTRY[2](pt, _ctx([_P("adós")]))
        assert res.ticks == {
            "role_ados": "igen", "fedez_1": "igen",
            "role_adostars": "nem", "fedez_2": "nem",
        }

    def test_multiple_roles_tick_both_sets(self):
        params = {"role_to_ticks": {
            "adós": ["role_ados", "fedez_1"],
            "adóstárs": ["role_adostars", "fedez_2"],
        }}
        pt = _point(2, self.BLOCKS, params)
        res = RULE_REGISTRY[2](pt, _ctx([_P("adós"), _P("adóstárs")]))
        assert res.ticks == {
            "role_ados": "igen", "fedez_1": "igen",
            "role_adostars": "igen", "fedez_2": "igen",
        }

    def test_unknown_role_ticks_nothing(self):
        params = {"role_to_ticks": {"adós": ["role_ados"]}}
        pt = _point(2, self.BLOCKS, params)
        res = RULE_REGISTRY[2](pt, _ctx([_P("kezes")]))
        # every member starts "nem"; kezes maps to nothing
        assert set(res.ticks.values()) == {"nem"}
        assert res.ticks["role_ados"] == "nem"

    def test_missing_params_yields_all_nem(self):
        pt = _point(2, self.BLOCKS, {})  # no role_to_ticks
        res = RULE_REGISTRY[2](pt, _ctx([_P("adós")]))
        assert set(res.ticks.values()) == {"nem"}


# ──────────────────────────────────────────────────────────────────────────
# Rule 3 — részleges blokk-pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule3PartialBlock:
    BLOCKS = [
        {"block_id": "b1", "members": ["a1", "a2"]},
        {"block_id": "b2", "members": ["b1", "b2"]},
        {"block_id": "b3", "members": ["c1"]},
    ]

    def test_only_the_active_block_ticks(self):
        pt = _point(3, self.BLOCKS, {"active_block": "b2"})
        res = RULE_REGISTRY[3](pt, _ctx([_P("adós")]))
        assert res.ticks == {
            "a1": "nem", "a2": "nem",
            "b1": "igen", "b2": "igen",
            "c1": "nem",
        }

    def test_without_participants_active_block_still_nem(self):
        pt = _point(3, self.BLOCKS, {"active_block": "b2"})
        res = RULE_REGISTRY[3](pt, _ctx([]))
        assert res.ticks["b1"] == "nem" and res.ticks["b2"] == "nem"
        assert res.ticks["a1"] == "nem"

    def test_active_block_id_not_present_all_nem(self):
        pt = _point(3, self.BLOCKS, {"active_block": "does-not-exist"})
        res = RULE_REGISTRY[3](pt, _ctx([_P("adós")]))
        assert set(res.ticks.values()) == {"nem"}


# ──────────────────────────────────────────────────────────────────────────
# Rule 4 — kétlépéses / összetett pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule4TwoStep:
    BLOCKS = [{"block_id": "b1", "members": ["q1", "q2", "role_field", "closing_field"]}]

    def test_with_participants_step1_plus_role_plus_closing(self):
        params = {
            "step1_value": "nem vagyok",
            "closing_field": "closing_field",
            "role_field": "role_field",
            "closing_value": "igen",
        }
        pt = _point(4, self.BLOCKS, params)
        res = RULE_REGISTRY[4](pt, _ctx([_P("adós")]))
        assert res.ticks == {
            "q1": "nem vagyok", "q2": "nem vagyok",
            "role_field": "igen", "closing_field": "igen",
        }

    def test_without_participants_role_field_nem(self):
        params = {
            "closing_field": "closing_field",
            "role_field": "role_field",
        }
        pt = _point(4, self.BLOCKS, params)
        res = RULE_REGISTRY[4](pt, _ctx([]))
        # step1 default "nem vagyok", closing default "igen", role_field "nem"
        assert res.ticks["q1"] == "nem vagyok"
        assert res.ticks["role_field"] == "nem"
        assert res.ticks["closing_field"] == "igen"

    def test_defaults_when_params_missing(self):
        # closing_field / role_field absent → they land as keys with default
        # values, ordinary members get the default step1 value.
        blocks = [{"block_id": "b1", "members": ["q1"]}]
        pt = _point(4, blocks, {})
        res = RULE_REGISTRY[4](pt, _ctx([_P("adós")]))
        assert res.ticks["q1"] == "nem vagyok"
        # role_field=None key still set to "igen" (active), closing=None set "igen"
        assert res.ticks.get(None) == "igen"


# ──────────────────────────────────────────────────────────────────────────
# Rule 5 — termékfüggő feltételes pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule5ProductConditional:
    BLOCKS = [{"block_id": "b1", "members": ["prod_ados", "prod_adostars"]}]

    def test_matching_product_ticks_all(self):
        pt = _point(5, self.BLOCKS, {"condition_value": "Évnyerő"})
        res = RULE_REGISTRY[5](pt, _ctx([_P("adós")], product_name="Évnyerő"))
        assert res.ticks == {"prod_ados": "igen", "prod_adostars": "igen"}

    def test_non_matching_product_ticks_none(self):
        pt = _point(5, self.BLOCKS, {"condition_value": "Évnyerő"})
        res = RULE_REGISTRY[5](pt, _ctx([_P("adós")], product_name="Piaci hitel"))
        assert res.ticks == {"prod_ados": "nem", "prod_adostars": "nem"}

    def test_case_insensitive_match(self):
        pt = _point(5, self.BLOCKS, {"condition_value": "évnyerő"})
        res = RULE_REGISTRY[5](pt, _ctx([_P("adós")], product_name="ÉVNyerő"))
        assert res.ticks["prod_ados"] == "igen"

    def test_match_without_participants_still_nem(self):
        pt = _point(5, self.BLOCKS, {"condition_value": "Évnyerő"})
        res = RULE_REGISTRY[5](pt, _ctx([], product_name="Évnyerő"))
        assert res.ticks == {"prod_ados": "nem", "prod_adostars": "nem"}

    def test_none_product_name_no_crash_all_nem(self):
        pt = _point(5, self.BLOCKS, {"condition_value": "Évnyerő"})
        res = RULE_REGISTRY[5](pt, _ctx([_P("adós")], product_name=None))
        assert set(res.ticks.values()) == {"nem"}


# ──────────────────────────────────────────────────────────────────────────
# Rule 6 — hitelcél-függő feltételes pipázás
# ──────────────────────────────────────────────────────────────────────────


class TestRule6LoanPurposeConditional:
    BLOCKS = [{"block_id": "b1", "members": ["lp_a", "lp_b"]}]

    def test_matching_loan_purpose_ticks_all(self):
        pt = _point(6, self.BLOCKS, {"condition_value": "új ingatlan vásárlás"})
        res = RULE_REGISTRY[6](pt, _ctx([_P("adós")], loan_purpose="Új ingatlan vásárlás"))
        assert res.ticks == {"lp_a": "igen", "lp_b": "igen"}

    def test_non_matching_loan_purpose_ticks_none(self):
        pt = _point(6, self.BLOCKS, {"condition_value": "új ingatlan vásárlás"})
        res = RULE_REGISTRY[6](pt, _ctx([_P("adós")], loan_purpose="építés"))
        assert res.ticks == {"lp_a": "nem", "lp_b": "nem"}

    def test_none_loan_purpose_no_crash_all_nem(self):
        pt = _point(6, self.BLOCKS, {"condition_value": "x"})
        res = RULE_REGISTRY[6](pt, _ctx([_P("adós")], loan_purpose=None))
        assert set(res.ticks.values()) == {"nem"}


# ──────────────────────────────────────────────────────────────────────────
# Rule 7 — többblokkos, blokkonként eltérő pipázási minta
# ──────────────────────────────────────────────────────────────────────────


class TestRule7MultiBlock:
    BLOCKS = [
        {"block_id": "b1", "members": ["x1"]},
        {"block_id": "b2", "members": ["x2"]},
        {"block_id": "b3", "members": ["x3"]},
    ]

    def test_each_block_uses_its_own_value(self):
        params = {"block_rules": {
            "b1": {"value": "hozzájárulok"},
            "b2": {"value": "igen"},
            "b3": {"value": "1. sor"},
        }}
        pt = _point(7, self.BLOCKS, params)
        res = RULE_REGISTRY[7](pt, _ctx([_P("adós")]))
        assert res.ticks == {
            "x1": "hozzájárulok", "x2": "igen", "x3": "1. sor",
        }

    def test_without_participants_all_nem(self):
        params = {"block_rules": {"b1": {"value": "igen"}, "b2": {"value": "igen"}}}
        pt = _point(7, self.BLOCKS, params)
        res = RULE_REGISTRY[7](pt, _ctx([]))
        assert set(res.ticks.values()) == {"nem"}

    def test_block_without_rule_defaults_to_igen(self):
        params = {"block_rules": {"b1": {"value": "igen"}}}  # b2, b3 unspecified
        pt = _point(7, self.BLOCKS, params)
        res = RULE_REGISTRY[7](pt, _ctx([_P("adós")]))
        assert res.ticks["x1"] == "igen"
        assert res.ticks["x2"] == "igen"  # default value
        assert res.ticks["x3"] == "igen"  # default value


# ──────────────────────────────────────────────────────────────────────────
# Framework independence guard (the spec's core invariant)
# ──────────────────────────────────────────────────────────────────────────


class TestFrameworkIndependence:
    def test_engine_source_has_no_framework_names(self):
        src = (PROJECT_ROOT / "src" / "engine" / "fill_rules.py").read_text(
            encoding="utf-8"
        ).lower()
        # The engine must branch only on rule_type; never on a concrete
        # framework token. (Case-insensitive, substring check.)
        for token in ("alap", "csok", "otp", "babavaro", "plussz"):
            assert token not in src, f"framework token {token!r} leaked into engine"


# ──────────────────────────────────────────────────────────────────────────
# Integration: the point engine is wired into _prepare_field_data
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture
def pipeline(tmp_path):
    from src.integrations.salesforce_client import SalesforceClient
    from src.main import FormFillerPipeline

    return FormFillerPipeline(
        sf_client=SalesforceClient(
            mock_mode=True,
            mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data",
        ),
        output_dir=tmp_path,
    )


def _integration_deal():
    from src.models.canonical_model import (
        Address, DealData, LoanDetails, Participant, ParticipantRole,
    )

    return DealData(
        deal_id="R2-INT",
        loan=LoanDetails(
            loan_amount=5_000_000,
            loan_term_months=240,
            loan_purpose="lakásvásárlás",
            product_name="Évnyerő",
        ),
        participants=[
            Participant(
                role=ParticipantRole.BORROWER,
                name="Teszt Anna",
                address=Address(zip_code="1000", city="Bp", street="Utca", house_number="1"),
            ),
            Participant(
                role=ParticipantRole.CO_BORROWER,
                name="Teszt Béla",
                address=Address(zip_code="1000", city="Bp", street="Utca", house_number="1"),
            ),
        ],
        products=["piaci_hitel"],
    )


def _integration_mapping():
    from src.ai.field_recognizer import MappingConfig

    return MappingConfig(
        bank_name="OTP", form_name="t", form_type="acroform",
        fields=[],
        points=[
            {  # Rule 1 — unconditional tick-all
                "point_id": "P1",
                "framework": "*",
                "rule_type": 1,
                "blocks": [{"block_id": "P1.main", "members": ["p1_a", "p1_b"]}],
                "params": {},
            },
            {  # Rule 2 — role-based multi-check
                "point_id": "P2",
                "framework": "*",
                "rule_type": 2,
                "blocks": [{"block_id": "P2.main",
                            "members": ["r_ados", "r_fedez1", "r_adostars", "r_fedez2"]}],
                "params": {"role_to_ticks": {
                    "adós": ["r_ados", "r_fedez1"],
                    "adóstárs": ["r_adostars", "r_fedez2"],
                }},
            },
            {  # Rule 5 — product-conditional (matches Évnyerő)
                "point_id": "P5",
                "framework": "*",
                "rule_type": 5,
                "blocks": [{"block_id": "P5.main", "members": ["p5_x"]}],
                "params": {"condition_value": "Évnyerő"},
            },
            {  # Rule 5 — product-conditional (does NOT match)
                "point_id": "P5b",
                "framework": "*",
                "rule_type": 5,
                "blocks": [{"block_id": "P5b.main", "members": ["p5b_y"]}],
                "params": {"condition_value": "Másik termék"},
            },
            {  # Rule 6 — loan-purpose-conditional (matches lakásvásárlás)
                "point_id": "P6",
                "framework": "*",
                "rule_type": 6,
                "blocks": [{"block_id": "P6.main", "members": ["p6_z"]}],
                "params": {"condition_value": "lakásvásárlás"},
            },
        ],
    )


class TestPrepareFieldDataIntegration:
    def test_point_ticks_land_in_field_data(self, pipeline):
        fd = pipeline._prepare_field_data(_integration_deal(), _integration_mapping())
        # Rule 1: all members ticked (active participants present)
        assert fd["p1_a"] == "igen" and fd["p1_b"] == "igen"
        # Rule 2: borrower → r_ados+r_fedez1, coborrower → r_adostars+r_fedez2
        assert fd["r_ados"] == "igen"
        assert fd["r_fedez1"] == "igen"
        assert fd["r_adostars"] == "igen"
        assert fd["r_fedez2"] == "igen"
        # Rule 5: matching product → igen
        assert fd["p5_x"] == "igen"
        # Rule 5b: non-matching product → nem
        assert fd["p5b_y"] == "nem"
        # Rule 6: matching loan purpose → igen
        assert fd["p6_z"] == "igen"

    def test_points_round_trip_through_mapping_json(self, tmp_path, pipeline):
        """points declared in JSON survive to_dict/from_dict and still fire."""
        from src.ai.field_recognizer import MappingConfig

        mapping = _integration_mapping()
        dumped = mapping.to_dict()
        rebuilt = MappingConfig.from_dict(dumped)
        assert rebuilt.points == mapping.points

        fd = pipeline._prepare_field_data(_integration_deal(), rebuilt)
        assert fd["p1_a"] == "igen"
        assert fd["r_ados"] == "igen"
        assert fd["p5_x"] == "igen"

    def test_mapping_without_points_unchanged(self, pipeline):
        """A mapping with no points behaves exactly as before (no crash)."""
        from src.ai.field_recognizer import (
            FieldType, MappingConfidence, MappingConfig, RecognizedField,
        )

        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[RecognizedField("borrower_name", "n", FieldType.TEXT,
                                    "Contact.Name", MappingConfidence.HIGH, 1)],
        )
        deal = _integration_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        # canonical field still resolved; no point keys present
        assert fd.get("borrower_name") == "Teszt Anna"
        assert "p1_a" not in fd

    def test_unknown_rule_type_is_skipped(self, pipeline, caplog):
        """An unknown rule_type logs a warning and is skipped, not crashed."""
        from src.ai.field_recognizer import MappingConfig

        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[],
            points=[{
                "point_id": "PX", "framework": "*", "rule_type": 42,
                "blocks": [{"block_id": "PX.main", "members": ["px_a"]}],
                "params": {},
            }],
        )
        fd = pipeline._prepare_field_data(_integration_deal(), mapping)
        # unknown rule → no tick emitted
        assert "px_a" not in fd

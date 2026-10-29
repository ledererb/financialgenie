"""Cutover Task 5 — safe declaration rebuild and legal-rule table.

These tests pin the SAFE declaration policy for the OTP
``altalanos_nyilatkozatok`` (general declarations) form:

* the mapping represents the real AcroForm widgets truthfully — 207 checkbox
  widgets and 5 text fields, widget types taken from the actual PDF;
* existing labels / pages / coordinates are preserved by the deterministic
  rebuild (``scripts/rebuild_altalanos_nyilatkozatok_mapping.py``);
* reviewed role/signature points use ``points`` / rule 2 with INDEXED role
  addressing (``adós``, ``adóstárs_1..3``, ``zálogkötelezett_1..2``,
  ``haszonélvező_1..2``) and tick only their own column;
* non-role legal choices (KHR / NAV / pénznem / cselekvőképesség /
  felhatalmazás / personal-status declarations) stay canonical-less and
  MANUAL — no automatic true/false from any category, default or
  product-condition rule;
* consent checkboxes can only ever be filled through explicit canonical CRM
  consent paths — never from a LegalClassifier category default;
* the LegalClassifier cannot overwrite points-managed fields;
* the pipeline boundary lists unresolved declarations as manual decisions
  instead of silently ticking them.
"""

import json
import logging
from pathlib import Path

import pytest

from src.ai.field_recognizer import MappingConfig
from src.integrations.salesforce_client import SalesforceClient
from src.main import FormFillerPipeline

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PDF_PATH = PROJECT_ROOT / "documents" / "otp_bank" / "split" / "altalanos_nyilatkozatok.pdf"
MAPPING_PATH = (
    PROJECT_ROOT / "src" / "mapping" / "altalanos_nyilatkozatok_mapping.json"
)
LEGAL_DEFAULTS_PATH = PROJECT_ROOT / "src" / "mapping" / "legal_defaults.json"

#: The four reviewed, pure acknowledgement ("tudomásul veszem / megértettem")
#: declaration points. Everything else — consents, felhatalmazások, KHR / NAV
#: choices, capacity, currency, personal-status and conditional declarations —
#: stays manual by policy (see the rebuild script's ALLOWLIST rationale).
REVIEWED_POINT_IDS = {
    "A_NY_2@p1#1",
    "A_NY_3@p2#1",
    "A_NY_211014_1@p4#1",
    "A_NY_15@p5#2",
}

#: Union of every field managed by the reviewed points (the ONLY checkboxes
#: the pipeline may tick automatically on this form).
REVIEWED_MEMBERS = {
    # 1. pont — Előzetes tájékoztatás (6 widgets: applicant + collateral rows)
    "A_NY_2_kölcsönigénylő",
    "A_NY_2_társigénylő_1",
    "A_NY_2_társigénylő_2",
    "A_NY_2_társigénylő_3",
    "A_NY_2_fedezet_tulaj_1",
    "A_NY_2_fedezet_tulaj_2",
    # 2. pont — Adatkezelési tájékoztatás tudomásulvétele (8 widgets).
    # NOTE: the PDF itself carries Acrobat-renamed duplicate column names —
    # ``A_NY_3_társigénylő_3`` is physically the 2nd co-applicant column and
    # ``A_NY_3_társigénylő_3-2`` the 3rd — so the role index comes from the
    # column order, not the name digit.
    "A_NY_3_kölcsönigénylő",
    "A_NY_3_társigénylő_1",
    "A_NY_3_társigénylő_3",
    "A_NY_3_társigénylő_3-2",
    "A_NY_3_fedezet_tulaj_1",
    "A_NY_3_fedezet_tulaj_2",
    "A_NY_3_fedezet_haszonélvező_1",
    "A_NY_3_fedezet_haszonélvező_2",
    # 9. pont — hitelképesség-vizsgálati információk (4 widgets)
    "A_NY_211014_1_Igénylő",
    "A_NY_211014_1_Társigénylő_1",
    "A_NY_211014_1_Társigénylő_2",
    "A_NY_211014_1_Társigénylő_3",
    # 14. pont — fogyatékossággal élő ügyfelek tájékoztatása (4 widgets)
    "A_NY_15_kölcsönigénylő.2-1",
    "A_NY_15_társigénylő_1.2-2",
    "A_NY_15_társigénylő_2.2-3",
    "A_NY_15_társigénylő_3.2-4",
}

#: Fields that previously carried bogus canonical mappings (signature-column
#: ticks driven by deal booleans / citizenship values). They must be
#: canonical-less after the rebuild.
BOGUS_CANONICAL_FIELDS = {
    "A_NY_5-2_fedezet_haszonélvező_3": "deal.has_usufructuary",
    "A_NY_5-2_fedezet_haszonélvező_4": "deal.has_usufructuary",
    "A_NY_2223_haszonelvezo1": "deal.has_usufructuary",
    "A_NY_2223_haszonelvezo2": "deal.has_usufructuary",
    "A_NY_12_kölcsönigénylő.0": "participant.borrower.citizenship",
    "A_NY_12_társigénylő_1.0": "participant.borrower.citizenship",
    "A_NY_12_társigénylő_2.0": "participant.borrower.citizenship",
    "A_NY_12_társigénylő_3.0": "participant.borrower.citizenship",
}


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _load_mapping_json() -> dict:
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _pdf_widgets() -> list[dict]:
    """(name, page, type, on_states) for every widget annotation in the PDF."""
    import fitz

    widgets = []
    doc = fitz.open(PDF_PATH)
    for page in doc:
        for w in page.widgets():
            states = []
            if w.field_type_string == "CheckBox":
                states = [
                    s for s in (w.button_states().get("normal") or []) if s != "Off"
                ]
            widgets.append(
                {
                    "name": w.field_name,
                    "page": page.number + 1,
                    "type": w.field_type_string,
                    "states": states,
                    "x": round(w.rect.x0, 2),
                    "y": round(w.rect.y0, 2),
                }
            )
    return widgets


def _resolve_widget(entry_name: str, widgets: list[dict]) -> dict | None:
    """Resolve a mapping entry name to its widget.

    Plain names match the widget name; ``base___state`` virtual names match
    the widget of ``base`` whose on-state is ``state``.
    """
    if "___" in entry_name:
        base, suffix = entry_name.rsplit("___", 1)
        matches = [w for w in widgets if w["name"] == base and suffix in w["states"]]
        return matches[0] if len(matches) == 1 else None
    matches = [w for w in widgets if w["name"] == entry_name]
    return matches[0] if matches else None


def _mock_deal():
    """Deal with indexed roles: 1 borrower, 2 co-borrowers, 2 mortgagors,
    1 beneficiary. No consent booleans are set (consent must stay manual)."""
    from src.models.canonical_model import DealData, LoanDetails, Participant, ParticipantRole

    return DealData(
        deal_id="DECL-SAFE-1",
        loan=LoanDetails(loan_amount=10_000_000, loan_purpose="lakásvásárlás"),
        participants=[
            Participant(role=ParticipantRole.BORROWER, name="Adós Anna"),
            Participant(role=ParticipantRole.CO_BORROWER, name="Társ Egy"),
            Participant(role=ParticipantRole.CO_BORROWER, name="Társ Kettő"),
            Participant(role=ParticipantRole.MORTGAGOR, name="Zálog Egy"),
            Participant(role=ParticipantRole.MORTGAGOR, name="Zálog Kettő"),
            Participant(role=ParticipantRole.BENEFICIARY, name="Haszon Egy"),
        ],
        products=["piaci_hitel"],
    )


@pytest.fixture
def pipeline(tmp_path):
    return FormFillerPipeline(
        sf_client=SalesforceClient(
            mock_mode=True,
            mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data",
        ),
        output_dir=tmp_path,
    )


# ──────────────────────────────────────────────────────────────────────────
# 1. Truthful widget types / groups from the actual PDF
# ──────────────────────────────────────────────────────────────────────────


class TestMappingTruthfulWidgetTypes:
    def test_type_counts_match_actual_pdf(self):
        from collections import Counter

        mapping = _load_mapping_json()
        counts = Counter(f["field_type"] for f in mapping["fields"])
        assert counts == {"checkbox": 207, "text": 5}
        assert len(mapping["fields"]) == 212

    def test_every_entry_resolves_to_exactly_one_real_widget(self):
        widgets = _pdf_widgets()
        mapping = _load_mapping_json()
        for f in mapping["fields"]:
            w = _resolve_widget(f["pdf_field_name"], widgets)
            assert w is not None, f"no widget for {f['pdf_field_name']!r}"
            expected = "checkbox" if w["type"] == "CheckBox" else "text"
            assert f["field_type"] == expected, f["pdf_field_name"]

    def test_pages_match_widget_pages(self):
        widgets = _pdf_widgets()
        mapping = _load_mapping_json()
        for f in mapping["fields"]:
            w = _resolve_widget(f["pdf_field_name"], widgets)
            assert f["page_number"] == w["page"], f["pdf_field_name"]

    def test_labels_preserved_and_no_invented_coordinates(self):
        mapping = _load_mapping_json()
        for f in mapping["fields"]:
            assert isinstance(f["label"], str) and f["label"].strip(), f["pdf_field_name"]
            # The pre-rebuild mapping had no coordinates — the rebuild must
            # not invent any.
            assert f["coordinates"] is None, f["pdf_field_name"]
        by_name = {f["pdf_field_name"]: f for f in mapping["fields"]}
        assert by_name["A_NY_2_kölcsönigénylő"]["label"] == "A Ny 2 Kölcsönigénylő"
        assert by_name["A NY_kelt"]["label"] == "A Ny Kelt"


# ──────────────────────────────────────────────────────────────────────────
# 2. Reviewed role/signature points (rule 2, indexed role addressing)
# ──────────────────────────────────────────────────────────────────────────


class TestReviewedRolePoints:
    def test_exactly_the_reviewed_points_exist(self):
        mapping = _load_mapping_json()
        points = mapping.get("points") or []
        assert {p["point_id"] for p in points} == REVIEWED_POINT_IDS
        assert all(p["rule_type"] == 2 for p in points)

    def test_union_of_point_members_is_the_reviewed_set(self):
        mapping = _load_mapping_json()
        members = set()
        for p in mapping.get("points") or []:
            for blk in p.get("blocks", []):
                members.update(blk.get("members", []))
        assert members == REVIEWED_MEMBERS

    def test_indexed_role_addressing_keys(self):
        mapping = _load_mapping_json()
        points = {p["point_id"]: p for p in mapping.get("points") or []}
        p3 = points["A_NY_3@p2#1"]
        assert p3["params"]["role_to_ticks"] == {
            "adós": ["A_NY_3_kölcsönigénylő"],
            # Acrobat rename: the 2nd/3rd co-applicant columns carry _3 / _3-2;
            # column ORDER provides the index, not the name digit.
            "adóstárs_1": ["A_NY_3_társigénylő_1"],
            "adóstárs_2": ["A_NY_3_társigénylő_3"],
            "adóstárs_3": ["A_NY_3_társigénylő_3-2"],
            "zálogkötelezett_1": ["A_NY_3_fedezet_tulaj_1"],
            "zálogkötelezett_2": ["A_NY_3_fedezet_tulaj_2"],
            "haszonélvező_1": ["A_NY_3_fedezet_haszonélvező_1"],
            "haszonélvező_2": ["A_NY_3_fedezet_haszonélvező_2"],
        }

    def test_no_choice_or_consent_family_in_points(self):
        mapping = _load_mapping_json()
        forbidden = ("KHR", "NAV", "Pénznem", "Cselekvő", "___", "211011", "2223")
        for p in mapping.get("points") or []:
            for blk in p.get("blocks", []):
                for m in blk.get("members", []):
                    for token in forbidden:
                        assert token not in m, (m, token)

    def test_every_point_member_is_a_real_single_yes_widget(self):
        widgets = _pdf_widgets()
        mapping = _load_mapping_json()
        for p in mapping.get("points") or []:
            for blk in p.get("blocks", []):
                for m in blk.get("members", []):
                    w = _resolve_widget(m, widgets)
                    assert w is not None and w["states"] == ["Yes"], m


# ──────────────────────────────────────────────────────────────────────────
# 3. Bogus canonical mappings removed from signature columns
# ──────────────────────────────────────────────────────────────────────────


class TestCanonicalHygiene:
    def test_signature_columns_are_canonical_less(self):
        mapping = _load_mapping_json()
        by_name = {f["pdf_field_name"]: f for f in mapping["fields"]}
        for name in BOGUS_CANONICAL_FIELDS:
            assert by_name[name]["canonical_field"] is None, name

    def test_no_deal_boolean_or_citizenship_paths_on_checkboxes(self):
        mapping = _load_mapping_json()
        for f in mapping["fields"]:
            if f["field_type"] != "checkbox":
                continue
            assert f["canonical_field"] is None, f["pdf_field_name"]

    def test_kelt_keeps_explicit_path_with_truthful_text_type(self):
        mapping = _load_mapping_json()
        by_name = {f["pdf_field_name"]: f for f in mapping["fields"]}
        kelt = by_name["A NY_kelt"]
        assert kelt["field_type"] == "text"
        assert kelt["canonical_field"] == "participant.borrower.date_of_signature"


# ──────────────────────────────────────────────────────────────────────────
# 4. Safe legal defaults — nothing automatic
# ──────────────────────────────────────────────────────────────────────────


class TestSafeLegalDefaults:
    def test_all_categories_manual_or_crm_consent_with_null_defaults(self):
        with open(LEGAL_DEFAULTS_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config["categories"], "categories table must exist"
        for name, cat in config["categories"].items():
            assert cat.get("default_value", "missing") is None, name
            assert cat.get("apply_when") in {"manual", "crm_consent"}, name

    def test_no_conditional_rules_and_no_boolean_overrides(self):
        with open(LEGAL_DEFAULTS_PATH, "r", encoding="utf-8") as f:
            config = json.load(f)
        assert config.get("conditional_rules") == []
        for key, value in (config.get("overrides") or {}).items():
            if key.startswith("_"):
                continue
            assert not isinstance(value, bool), key

    def test_apply_defaults_never_auto_values_unknown_choices(self):
        """KHR / NAV / pénznem / capacity / consent / decline / conditional
        categories produce NO value under the shipped config — manual."""
        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        classifier = LegalClassifier()
        deal = DealData(
            deal_id="D",
            loan=LoanDetails(loan_amount=1),
            products=["csok_plusz", "piaci_hitel"],
        )
        classified = {
            "A_NY_KHR_1_igénylő___1,2": "consent",
            "A_NY_KHR_2_igénylő 3___Yes": "data_sharing",
            "A_NY_Pénznem___Yes": "bank_info",
            "A_NY_Pénznem_tipus___1,2": "conditional",
            "A_NY_Nyil_Cselekvőképesség_Ptk_Kölcsönigénylő___Yes": "decline",
            "A_NY_Nyil_Cselekvőképtelen_Társigénylő_1___no": "property_state",
            "A_NY_4_kölcsönigénylő": "consent",
            "A_NY_2223_kölcsönigénylő 2": "consent",
            "A_NY_11_kölcsönigénylő": "consent",
            "Something_new": "unknown",
        }
        assert classifier.apply_defaults(classified, deal) == {}

    def test_apply_defaults_protects_points_managed_fields(self):
        """Even a reviewed per-field override must not touch a points-managed
        field — the point engine owns those checkboxes."""
        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        classifier = LegalClassifier()
        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        points_managed = {"A_NY_2_kölcsönigénylő", "A_NY_3_társigénylő_1"}
        classified = {
            "A_NY_2_kölcsönigénylő": "consent",
            "A_NY_3_társigénylő_1": "decline",
        }
        out = classifier.apply_defaults(classified, deal, points_managed=points_managed)
        assert "A_NY_2_kölcsönigénylő" not in out
        assert "A_NY_3_társigénylő_1" not in out

    def test_apply_defaults_points_guard_beats_explicit_override(self, tmp_path):
        config = json.loads(LEGAL_DEFAULTS_PATH.read_text(encoding="utf-8"))
        config["overrides"] = {"A_NY_2_kölcsönigénylő": True}
        override_path = tmp_path / "legal_defaults_override.json"
        override_path.write_text(json.dumps(config), encoding="utf-8")

        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        classifier = LegalClassifier(config_path=override_path)
        out = classifier.apply_defaults(
            {"A_NY_2_kölcsönigénylő": "consent"},
            DealData(deal_id="D", loan=LoanDetails(loan_amount=1)),
            points_managed={"A_NY_2_kölcsönigénylő"},
        )
        assert out == {}

    def test_extract_unmapped_checkboxes_excludes_point_members(self):
        from src.ai.legal_classifier import extract_unmapped_checkboxes

        mapping = _load_mapping_json()
        config = MappingConfig.load(MAPPING_PATH)
        unmapped = {u["pdf_field_name"] for u in extract_unmapped_checkboxes(config)}
        assert mapping["points"], "mapping must have points"
        for p in mapping["points"]:
            for blk in p["blocks"]:
                for m in blk["members"]:
                    assert m not in unmapped, m
        # KHR choices remain candidates for classification (then manual):
        assert "A_NY_KHR_1_igénylő___1,2" in unmapped


# ──────────────────────────────────────────────────────────────────────────
# 5. Rule 2 — indexed role addressing in the fill engine
# ──────────────────────────────────────────────────────────────────────────


class TestRule2IndexedRoleAddressing:
    def _ctx(self, roles):
        from src.engine.fill_rules import RuleContext

        participants = [
            type("P", (), {"role": type("R", (), {"value": r})()})() for r in roles
        ]
        return RuleContext(
            active_participants=participants,
            products=[],
            loan_purpose=None,
            product_name=None,
            canonical_values={},
        )

    def _point(self, params):
        from src.engine.fill_rules import Point

        return Point(
            point_id="T",
            framework="*",
            blocks=[{"block_id": "T.main", "members": sorted(
                {m for members in params["role_to_ticks"].values() for m in members}
            )}],
            rule_type=2,
            params=params,
        )

    def test_indexed_roles_tick_only_their_column(self):
        from src.engine.fill_rules import evaluate_point

        params = {
            "role_to_ticks": {
                "adós": ["col_ados"],
                "adóstárs_1": ["col_tars1"],
                "adóstárs_2": ["col_tars2"],
                "adóstárs_3": ["col_tars3"],
                "zálogkötelezett_1": ["col_zalog1"],
                "haszonélvező_1": ["col_haszon1"],
            }
        }
        ctx = self._ctx(["adós", "adóstárs", "adóstárs", "zálogkötelezett", "haszonélvező"])
        res = evaluate_point(self._point(params), ctx)
        assert res.ticks == {
            "col_ados": "igen",
            "col_tars1": "igen",   # 1st co-borrower → column 1 only
            "col_tars2": "igen",   # 2nd co-borrower → column 2 only
            "col_tars3": "nem",    # no 3rd co-borrower
            "col_zalog1": "igen",
            "col_haszon1": "igen",
        }

    def test_plain_role_key_still_supported(self):
        from src.engine.fill_rules import evaluate_point

        params = {"role_to_ticks": {"adós": ["role_ados"], "adóstárs": ["role_tars"]}}
        ctx = self._ctx(["adós", "adóstárs"])
        res = evaluate_point(self._point(params), ctx)
        assert res.ticks["role_ados"] == "igen"
        assert res.ticks["role_tars"] == "igen"

    def test_second_occurrence_without_key_ticks_nothing(self):
        from src.engine.fill_rules import evaluate_point

        params = {"role_to_ticks": {"adóstárs_1": ["col_tars1"]}}
        ctx = self._ctx(["adóstárs", "adóstárs"])
        # 1st occurrence → adóstárs_1 (ticks); 2nd occurrence has no
        # adóstárs_2 key → ticks nothing extra (and never re-ticks column 1).
        res = evaluate_point(self._point(params), ctx)
        assert res.ticks == {"col_tars1": "igen"}


# ──────────────────────────────────────────────────────────────────────────
# 6. Pipeline boundary — manual listing, no silent ticks
# ──────────────────────────────────────────────────────────────────────────


class TestLegalBoundaryInPipeline:
    def test_fill_legal_declarations_reports_manual_and_ticks_nothing(
        self, tmp_path, monkeypatch, pipeline
    ):
        """With the shipped safe config the classifier produces no values;
        the boundary must return them as MANUAL decisions and leave every
        checkbox in the PDF untouched."""
        import fitz

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        mapping = MappingConfig.load(MAPPING_PATH)
        pdf_copy = tmp_path / "decl.pdf"
        pdf_copy.write_bytes(PDF_PATH.read_bytes())

        deal = _mock_deal()
        pipeline._fill_legal_declarations(pdf_copy, mapping, deal)

        doc = fitz.open(pdf_copy)
        for page in doc:
            for w in page.widgets():
                if w.field_type_string != "CheckBox":
                    continue
                states = [s for s in (w.button_states().get("normal") or []) if s != "Off"]
                # No checkbox may be silently ticked by the legal layer.
                assert w.field_value in (None, "Off", ""), (
                    w.field_name,
                    w.field_value,
                    states,
                )

    def test_fill_legal_declarations_returns_manual_list(
        self, tmp_path, monkeypatch, pipeline
    ):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        mapping = MappingConfig.load(MAPPING_PATH)
        pdf_copy = tmp_path / "decl.pdf"
        pdf_copy.write_bytes(PDF_PATH.read_bytes())

        deal = _mock_deal()
        info = pipeline._fill_legal_declarations(pdf_copy, mapping, deal)
        assert isinstance(info, dict)
        assert info["modified"] == 0
        manual_names = {m["pdf_field_name"] for m in info["manual"]}
        # Unknown KHR / NAV / consent choices are listed as manual decisions:
        for name in (
            "A_NY_KHR_1_igénylő___1,2",
            "A_NY_KHR_2_igénylő 3___Yes",
            "A_NY_Pénznem___Yes",
            "A_NY_4_kölcsönigénylő",  # 3. pont felhatalmazás (consent) → manual
            "A_NY_11_kölcsönigénylő",  # 11. pont munkavállalói hozzájárulás → manual
        ):
            assert name in manual_names, name
        # Points-managed fields are NOT manual — the point engine owns them:
        for name in ("A_NY_2_kölcsönigénylő", "A_NY_3_fedezet_tulaj_1"):
            assert name not in manual_names, name

    def test_run_for_deal_surfaces_manual_declarations(
        self, tmp_path, monkeypatch, pipeline
    ):
        """run_for_deal must expose the manual declaration list on its result."""
        mapping = MappingConfig.load(MAPPING_PATH)

        captured = {}

        def fake_fill_legal(pdf_path, mapping_cfg, deal):
            info = {
                "modified": 0,
                "manual": [
                    {"pdf_field_name": "A_NY_KHR_1_igénylő___1,2", "label": "KHR", "page_number": 7}
                ],
            }
            captured["info"] = info
            return info

        monkeypatch.setattr(pipeline, "_fill_legal_declarations", fake_fill_legal)

        import types

        fake_assembler = types.SimpleNamespace(
            is_master_pdf=lambda path: False,
        )
        monkeypatch.setattr("src.main.DocumentAssembler", lambda *a, **k: fake_assembler)

        out_path = tmp_path / "out.pdf"
        out_path.write_bytes(b"%PDF-1.4\n%%EOF")
        monkeypatch.setattr(
            pipeline,
            "_fill_pdf",
            lambda template, deal, field_data, cfg, instance_fields=None, participant_override=None: (
                out_path,
                list(field_data),
                [],
            ),
        )
        monkeypatch.setattr(
            pipeline,
            "_check_completeness",
            lambda deal, cfg: types.SimpleNamespace(
                status="complete", blocking_issues=[], warnings=[]
            ),
        )
        monkeypatch.setattr(
            pipeline,
            "_prepare_field_data",
            lambda deal, cfg, **kw: {},
        )

        result = pipeline.run_for_deal(
            deal_id=pipeline.sf_client.list_deals()[0]["deal_id"],
            template_pdf=tmp_path / "template.pdf",
            mapping_config=mapping,
            upload_to_sf=False,
        )
        assert result["success"] is True, result
        assert result["manual_declarations"] == captured["info"]["manual"]


# ──────────────────────────────────────────────────────────────────────────
# 7. Offline declaration-package preview — manual, not silently ticked
# ──────────────────────────────────────────────────────────────────────────


class TestOfflineDeclarationPreview:
    def test_prepare_field_data_ticks_only_reviewed_role_columns(self, pipeline):
        mapping = MappingConfig.load(MAPPING_PATH)
        fd = pipeline._prepare_field_data(_mock_deal(), mapping)

        # Reviewed point columns: active roles ticked, inactive unticked.
        assert fd["A_NY_2_kölcsönigénylő"] == "igen"          # adós
        assert fd["A_NY_2_társigénylő_1"] == "igen"           # adóstárs_1
        assert fd["A_NY_2_társigénylő_2"] == "igen"           # adóstárs_2
        assert fd["A_NY_2_társigénylő_3"] == "nem"            # no 3rd co-borrower
        assert fd["A_NY_2_fedezet_tulaj_1"] == "igen"         # zálogkötelezett_1
        assert fd["A_NY_2_fedezet_tulaj_2"] == "igen"         # zálogkötelezett_2
        assert fd["A_NY_3_fedezet_haszonélvező_1"] == "igen"  # haszonélvező_1
        assert fd["A_NY_3_fedezet_haszonélvező_2"] == "nem"   # no 2nd beneficiary
        assert fd["A_NY_211014_1_Igénylő"] == "igen"
        assert fd["A_NY_15_társigénylő_2.2-3"] == "igen"

        # NOTHING outside the reviewed members may receive a tick.
        ticked = {k for k, v in fd.items() if v == "igen"}
        assert ticked <= REVIEWED_MEMBERS, sorted(ticked - REVIEWED_MEMBERS)[:10]

    def test_manual_choice_families_absent_from_preview(self, pipeline):
        mapping = MappingConfig.load(MAPPING_PATH)
        fd = pipeline._prepare_field_data(_mock_deal(), mapping)
        for name in (
            "A_NY_KHR_1_igénylő___1,2",
            "A_NY_KHR_2_igénylő 3___Yes",
            "A_NY_KHR_2_igénylő 3___no",
            "A_NY_Pénznem___Yes",
            "A_NY_Pénznem___no",
            "A_NY_Pénznem_tipus___1,2",
            "A_NY_Nyil_Cselekvőképesség_Ptk_Kölcsönigénylő___Yes",
            "A_NY_Nyil_Cselekvőképesség_Ptk_Kölcsönigénylő___no",
            "A_NY_4_kölcsönigénylő",       # 3. pont — felhatalmazás (consent)
            "A_NY_5_kölcsönigénylő",       # 4. pont — biztosító felhatalmazás
            "A_NY_11_kölcsönigénylő",      # 11. pont — munkáltatói hozzájárulás
            "A_NY_17_kölcsönigénylő.0",    # MNB energetikai hozzájárulás
            "A_NY_2223_kölcsönigénylő 2",  # 22. pont — engedményes felhatalmazás
            "A_NY_12_kölcsönigénylő.0",    # rezidensi nyilatkozat
            "A_NY_19_kölcsönigénylő",      # építésügyi nyilatkozat
            "A_NY_2303_Igenylo",          # türelmi idő nyilatkozat
            "ANY_211011_Igénylő 2",        # KHR tájékoztató átvétel
            "A_NY_KHR_3_kölcsönigénylő",   # KHR megőrzés nyilatkozat
        ):
            assert name not in fd, name

    def test_declaration_package_generation_offline(self, pipeline, tmp_path):
        """End-to-end offline package: fill the real PDF from the preview
        field_data; exactly the ACTIVE participants' reviewed columns may end
        up ticked — nothing else."""
        import fitz

        from src.engine.pdf_filler import AcroFormFiller

        mapping = MappingConfig.load(MAPPING_PATH)
        deal = _mock_deal()
        fd = pipeline._prepare_field_data(deal, mapping)

        # Expected ticks: the reviewed points' columns of the active roles.
        active_role_keys = {"adós", "adóstárs_1", "adóstárs_2", "zálogkötelezett_1", "zálogkötelezett_2", "haszonélvező_1"}
        expected = set()
        for p in mapping.points:
            for role, members in p["params"]["role_to_ticks"].items():
                if role in active_role_keys:
                    expected.update(members)

        out = tmp_path / "declaration_package.pdf"
        filler = AcroFormFiller()
        result = filler.fill(PDF_PATH, out, fd, {})
        assert not result.errors, result.errors

        doc = fitz.open(out)
        ticked_widgets = set()
        for page in doc:
            for w in page.widgets():
                if w.field_type_string != "CheckBox":
                    continue
                if w.field_value not in (None, "Off", ""):
                    ticked_widgets.add(w.field_name)
        assert ticked_widgets == expected, (
            sorted(ticked_widgets - expected),
            sorted(expected - ticked_widgets),
        )


# ──────────────────────────────────────────────────────────────────────────
# 8. Deterministic rebuild script
# ──────────────────────────────────────────────────────────────────────────


class TestRebuildScript:
    def test_rebuild_is_deterministic_and_idempotent(self):
        from scripts.rebuild_altalanos_nyilatkozatok_mapping import (
            rebuild_mapping,
        )

        current = _load_mapping_json()
        first = rebuild_mapping(PDF_PATH, current)
        second = rebuild_mapping(PDF_PATH, first)
        assert first == second
        assert first == current, "on-disk mapping must equal a fresh rebuild"

    def test_rebuild_refuses_widget_count_mismatch(self):
        from scripts.rebuild_altalanos_nyilatkozatok_mapping import (
            RebuildError,
            rebuild_mapping,
        )

        mapping = _load_mapping_json()
        # Simulate a mutated PDF source: one checkbox field entry removed.
        broken = json.loads(json.dumps(mapping))
        broken["fields"] = broken["fields"][:-1]
        with pytest.raises(RebuildError):
            rebuild_mapping(PDF_PATH, broken)

    def test_rebuild_preserves_all_original_entries(self):
        from scripts.rebuild_altalanos_nyilatkozatok_mapping import (
            rebuild_mapping,
        )

        mapping = _load_mapping_json()
        rebuilt = rebuild_mapping(PDF_PATH, mapping)
        assert len(rebuilt["fields"]) == len(mapping["fields"]) == 212
        assert [f["pdf_field_name"] for f in rebuilt["fields"]] == [
            f["pdf_field_name"] for f in mapping["fields"]
        ]


# ──────────────────────────────────────────────────────────────────────────
# 9. Review fix 2 — legacy always/conditional modes are rejected in code
# ──────────────────────────────────────────────────────────────────────────


class TestLegacyAutoModeRejection:
    """Even an outdated/unsafe legal_defaults.json carrying legacy
    ``apply_when: always`` / ``conditional`` entries with ``default_value:
    true`` (and product-condition rules) must produce NO tick — the safe
    policy is enforced in code, not only in the shipped config.
    (Coverage request from review: pins the existing rejection branch.)"""

    def _unsafe_config(self, tmp_path):
        config = json.loads(LEGAL_DEFAULTS_PATH.read_text(encoding="utf-8"))
        config["categories"]["consent"] = {
            "default_value": True,
            "apply_when": "always",
            "description": "stale legacy auto-consent",
        }
        config["categories"]["conditional"] = {
            "default_value": True,
            "apply_when": "conditional",
            "description": "stale legacy product-conditioned tick",
        }
        config["conditional_rules"] = [
            {
                "if": {"products_contains": "csok_plusz"},
                "category": "conditional",
                "default_value": True,
            }
        ]
        path = tmp_path / "unsafe_legal_defaults.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_always_and_conditional_defaults_produce_no_tick(self, tmp_path):
        import logging

        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        classifier = LegalClassifier(config_path=self._unsafe_config(tmp_path))
        deal = DealData(
            deal_id="D",
            loan=LoanDetails(loan_amount=1),
            products=["csok_plusz"],  # would match the stale conditional rule
        )
        classified = {
            "X_auto_consent": "consent",        # apply_when: always, true
            "Y_auto_conditional": "conditional",  # apply_when: conditional, true
        }
        out = classifier.apply_defaults(classified, deal)
        assert out == {}

    def test_rejection_branch_is_logged(self, tmp_path, caplog):
        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        classifier = LegalClassifier(config_path=self._unsafe_config(tmp_path))
        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        with caplog.at_level(logging.WARNING, logger="src.ai.legal_classifier"):
            classifier.apply_defaults({"X_auto_consent": "consent"}, deal)
        rejected = [
            r for r in caplog.records
            if "X_auto_consent" in r.message and "automatikus" in r.message
        ]
        assert rejected, "the legacy-mode rejection warning must be logged"


# ──────────────────────────────────────────────────────────────────────────
# 10. Review fix 3 — source-PDF geometry pin for column-order indexing
# ──────────────────────────────────────────────────────────────────────────


class TestSourceGeometryPins:
    """The indexed role addressing derives the occurrence index from COLUMN
    ORDER (x) inside a point, because the PDF carries Acrobat-renamed
    duplicate names. These tests pin the source geometry directly so the
    index derivation does not rest solely on the derived mapping output."""

    def test_a_ny_3_role_columns_x_order(self):
        widgets = _pdf_widgets()
        by_name = {w["name"]: w for w in widgets if w["page"] == 2}

        ados = by_name["A_NY_3_kölcsönigénylő"]
        tars1 = by_name["A_NY_3_társigénylő_1"]      # 1st co-applicant column
        tars3 = by_name["A_NY_3_társigénylő_3"]      # Acrobat rename: 2nd column
        tars3b = by_name["A_NY_3_társigénylő_3-2"]   # Acrobat rename: 3rd column

        # Same applicant row on page 2…
        assert ados["y"] == tars1["y"] == tars3["y"] == tars3b["y"]
        # …in strictly left-to-right column order Kölcsönigénylő < Társ(1) < Társ(2) < Társ(3).
        assert ados["x"] < tars1["x"] < tars3["x"] < tars3b["x"]

    def test_a_ny_3_renamed_columns_are_single_yes_checkbox_row(self):
        widgets = _pdf_widgets()
        page2 = [
            w for w in widgets
            if w["page"] == 2 and w["name"].startswith("A_NY_3_") and "társigénylő" in w["name"]
        ]
        assert {w["name"] for w in page2} == {
            "A_NY_3_társigénylő_1",
            "A_NY_3_társigénylő_3",
            "A_NY_3_társigénylő_3-2",
        }
        for w in page2:
            assert w["type"] == "CheckBox"
            assert w["states"] == ["Yes"]


# ──────────────────────────────────────────────────────────────────────────
# 11. Review fix 4 — consent override asymmetry closed
# ──────────────────────────────────────────────────────────────────────────


class TestConsentOverrideAsymmetry:
    """A boolean override must not tick a crm_consent-classified field on its
    own — explicit CRM canonical evidence (a mapped consent canonical path
    resolving to an explicit True from the deal) is required."""

    def _config_with_override(self, tmp_path, field, value=True):
        config = json.loads(LEGAL_DEFAULTS_PATH.read_text(encoding="utf-8"))
        config["overrides"] = {field: value}
        path = tmp_path / "override_legal_defaults.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def test_stale_override_cannot_tick_consent_field(self, tmp_path):
        """Stale/unsafe override on a consent-classified field (3. pont
        felhatalmazás) is refused without CRM canonical evidence."""
        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        cfg = self._config_with_override(tmp_path, "A_NY_4_kölcsönigénylő", True)
        classifier = LegalClassifier(config_path=cfg)
        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        out = classifier.apply_defaults({"A_NY_4_kölcsönigénylő": "consent"}, deal)
        assert out == {}

    def test_override_ticks_consent_only_with_explicit_crm_evidence(self, tmp_path):
        from src.ai.legal_classifier import (
            LegalClassifier,
            explicit_crm_consent_evidence,
        )
        from src.ai.field_recognizer import (
            FieldType,
            MappingConfidence,
            MappingConfig,
            RecognizedField,
        )
        from src.models.canonical_model import DealData, LoanDetails, Participant, ParticipantRole

        cfg = self._config_with_override(tmp_path, "consent_box", True)
        mapping = MappingConfig(
            bank_name="OTP",
            form_name="t",
            form_type="acroform",
            fields=[
                RecognizedField(
                    pdf_field_name="consent_box",
                    label="KHR consent",
                    field_type=FieldType("checkbox"),
                    canonical_field="participant.borrower.khr_consent",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                )
            ],
        )
        deal = DealData(
            deal_id="D",
            loan=LoanDetails(loan_amount=1),
            participants=[
                Participant(
                    role=ParticipantRole.BORROWER,
                    name="Adós Anna",
                    consents={"khr": True},
                )
            ],
        )
        evidence = explicit_crm_consent_evidence(mapping, deal)
        assert evidence == {"consent_box"}

        classifier = LegalClassifier(config_path=cfg)
        # Without evidence → refused…
        assert classifier.apply_defaults({"consent_box": "consent"}, deal) == {}
        # …with explicit CRM canonical evidence → the override may tick.
        assert classifier.apply_defaults(
            {"consent_box": "consent"}, deal, crm_consent_evidence=evidence
        ) == {"consent_box": True}

    def test_explicit_crm_consent_evidence_requires_explicit_true(self):
        from src.ai.legal_classifier import explicit_crm_consent_evidence
        from src.ai.field_recognizer import (
            FieldType,
            MappingConfidence,
            MappingConfig,
            RecognizedField,
        )
        from src.models.canonical_model import DealData, LoanDetails, Participant, ParticipantRole

        def _mapping():
            return MappingConfig(
                bank_name="OTP",
                form_name="t",
                form_type="acroform",
                fields=[
                    RecognizedField(
                        pdf_field_name="consent_box",
                        label="KHR consent",
                        field_type=FieldType("checkbox"),
                        canonical_field="participant.borrower.khr_consent",
                        confidence=MappingConfidence.MANUAL,
                        page_number=1,
                    )
                ],
            )

        def _deal(consents):
            return DealData(
                deal_id="D",
                loan=LoanDetails(loan_amount=1),
                participants=[
                    Participant(
                        role=ParticipantRole.BORROWER,
                        name="Adós Anna",
                        consents=consents,
                    )
                ],
            )

        # Explicit True → evidence; explicit False / missing → no evidence.
        assert explicit_crm_consent_evidence(_mapping(), _deal({"khr": True})) == {"consent_box"}
        assert explicit_crm_consent_evidence(_mapping(), _deal({"khr": False})) == set()
        assert explicit_crm_consent_evidence(_mapping(), _deal({})) == set()

    def test_non_consent_override_still_works(self, tmp_path):
        """Overrides on non-consent categories (e.g. bank_info/manual) keep
        the explicit reviewed-override semantics."""
        from src.ai.legal_classifier import LegalClassifier
        from src.models.canonical_model import DealData, LoanDetails

        config = json.loads(LEGAL_DEFAULTS_PATH.read_text(encoding="utf-8"))
        config["overrides"] = {"reviewed_bank_info_box": False}
        path = tmp_path / "legal_defaults_non_consent.json"
        path.write_text(json.dumps(config), encoding="utf-8")

        classifier = LegalClassifier(config_path=path)
        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        out = classifier.apply_defaults({"reviewed_bank_info_box": "bank_info"}, deal)
        assert out == {"reviewed_bank_info_box": False}


# ──────────────────────────────────────────────────────────────────────────
# 12. Review fix 4b — rule 2 plain/indexed key coexistence warning
# ──────────────────────────────────────────────────────────────────────────


class TestRule2PlainAndIndexedCoexistence:
    def _ctx_roles(self):
        from src.engine.fill_rules import RuleContext

        participants = [
            type("P", (), {"role": type("R", (), {"value": r})()})()
            for r in ["adóstárs", "adóstárs"]
        ]
        return RuleContext(
            active_participants=participants,
            products=[],
            loan_purpose=None,
            product_name=None,
            canonical_values={},
        )

    def test_warning_fires_when_plain_and_indexed_keys_coexist(self, caplog):
        import logging

        from src.engine.fill_rules import Point, evaluate_point

        params = {
            "role_to_ticks": {
                "adóstárs": ["legacy_column"],   # plain (legacy) key
                "adóstárs_1": ["col_1"],         # indexed keys
                "adóstárs_2": ["col_2"],
            }
        }
        point = Point(
            point_id="COEX",
            framework="*",
            blocks=[
                {
                    "block_id": "COEX.main",
                    "members": ["legacy_column", "col_1", "col_2"],
                }
            ],
            rule_type=2,
            params=params,
        )
        with caplog.at_level(logging.WARNING, logger="src.engine.fill_rules"):
            res = evaluate_point(point, self._ctx_roles())
        warnings = [r for r in caplog.records if "adóstárs" in r.message]
        assert warnings, "coexistence of plain and indexed role keys must warn"
        # Indexed keys are authoritative for their occurrences.
        assert res.ticks == {"legacy_column": "nem", "col_1": "igen", "col_2": "igen"}

    def test_no_warning_without_coexistence(self, caplog):
        import logging

        from src.engine.fill_rules import Point, evaluate_point

        point = Point(
            point_id="CLEAN",
            framework="*",
            blocks=[{"block_id": "CLEAN.main", "members": ["col_1", "col_2"]}],
            rule_type=2,
            params={"role_to_ticks": {"adóstárs_1": ["col_1"], "adóstárs_2": ["col_2"]}},
        )
        with caplog.at_level(logging.WARNING, logger="src.engine.fill_rules"):
            evaluate_point(point, self._ctx_roles())
        assert not [
            r for r in caplog.records if "adóstárs" in r.message
        ], "clean indexed-only tables must not warn"


# ──────────────────────────────────────────────────────────────────────────
# 13. Review fix 4a — pipeline boundary wires the CRM consent evidence
# ──────────────────────────────────────────────────────────────────────────


class TestBoundaryWiresConsentEvidence:
    def test_fill_legal_declarations_passes_evidence_and_points_managed(
        self, tmp_path, monkeypatch, pipeline
    ):
        """_fill_legal_declarations must hand apply_defaults BOTH safety
        guards: the points-managed set and the explicit CRM consent
        evidence computed from the mapping's consent canonical paths."""
        captured = {}

        class FakeClassifier:
            def __init__(self, *a, **k):
                pass

            def classify_batch(self, fields):
                return {f["pdf_field_name"]: "consent" for f in fields}

            def apply_defaults(
                self, classified, deal, points_managed=None, crm_consent_evidence=None
            ):
                captured["points_managed"] = points_managed
                captured["crm_consent_evidence"] = crm_consent_evidence
                captured["n_classified"] = len(classified)
                return {}

        monkeypatch.setattr("src.main.LegalClassifier", FakeClassifier)

        mapping = MappingConfig.load(MAPPING_PATH)
        pdf_copy = tmp_path / "decl.pdf"
        pdf_copy.write_bytes(PDF_PATH.read_bytes())

        info = pipeline._fill_legal_declarations(pdf_copy, mapping, _mock_deal())
        assert info["modified"] == 0

        assert captured["points_managed"] == REVIEWED_MEMBERS
        # The rebuilt mapping wires no consent canonical path and the mock
        # deal has no consent booleans → evidence is an (empty) set, but it
        # IS wired (not None).
        assert captured["crm_consent_evidence"] == set()


# ──────────────────────────────────────────────────────────────────────────
# 14. Final cleanup fix 2 — malformed/missing canonical paths never crash
# ──────────────────────────────────────────────────────────────────────────


class TestConsentEvidenceMalformedPaths:
    """explicit_crm_consent_evidence must tolerate mappings carrying
    malformed or unregistered canonical paths (registry.get raises
    ValueError for malformed, KeyError for unknown) — such fields simply
    contribute no evidence."""

    def _mapping_with(self, canonical):
        from src.ai.field_recognizer import (
            FieldType,
            MappingConfidence,
            MappingConfig,
            RecognizedField,
        )

        return MappingConfig(
            bank_name="OTP",
            form_name="t",
            form_type="acroform",
            fields=[
                RecognizedField(
                    pdf_field_name="odd_box",
                    label="odd",
                    field_type=FieldType("checkbox"),
                    canonical_field=canonical,
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                )
            ],
        )

    def test_malformed_canonical_path_is_ignored(self):
        from src.ai.legal_classifier import explicit_crm_consent_evidence
        from src.models.canonical_model import DealData, LoanDetails

        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        for malformed in ("Contact.!!", "not a path", "participant..khr_consent", ""):
            evidence = explicit_crm_consent_evidence(
                self._mapping_with(malformed), deal
            )
            assert evidence == set(), malformed

    def test_unregistered_canonical_path_is_ignored(self):
        from src.ai.legal_classifier import explicit_crm_consent_evidence
        from src.models.canonical_model import DealData, LoanDetails

        deal = DealData(deal_id="D", loan=LoanDetails(loan_amount=1))
        for unknown in (
            "participant.borrower.not_a_registered_field",
            "deal.khr_consent",  # consents live on participants only
        ):
            evidence = explicit_crm_consent_evidence(
                self._mapping_with(unknown), deal
            )
            assert evidence == set(), unknown

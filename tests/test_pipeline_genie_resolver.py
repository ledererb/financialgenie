"""Cutover Task 2 — resolver-backed pipeline consumers over Genie mappings.

These tests drive ``FormFillerPipeline._prepare_field_data`` (and the document
instance consumers) with synthetic mappings whose ``canonical_field`` values
are registry-approved canonical paths — the exact shape the Task 1 migrator
produces. They prove that:

* every consumer (direct fields, checkbox groups, fill rules, points,
  character groups, date/digit fragments, role instances) resolves values
  through ``CanonicalResolver`` with an explicit ``(role, index)`` context;
* PDF field names containing "társ" NEVER select participants — only the
  canonical path and the explicit context do;
* guarantor/mortgagor document instances never receive co-borrower data and
  never generate co-borrower (társigénylő) page copies.
"""

import logging
import types
from datetime import date
from pathlib import Path

import pytest

from src.ai.field_recognizer import (
    FieldType,
    MappingConfidence,
    MappingConfig,
    RecognizedField,
)
from src.canonical.resolver import ResolutionContext
from src.engine.completeness_checker import CompletenessStatus
from src.integrations.salesforce_client import SalesforceClient
from src.main import FormFillerPipeline
from src.models.canonical_model import (
    Address,
    DealData,
    ExistingLoan,
    IncomeEntry,
    LoanDetails,
    Participant,
    ParticipantRole,
    Property,
    PropertyType,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ──────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────


def make_genie_deal() -> DealData:
    """A deal exercising every canonical namespace the resolver covers."""

    borrower = Participant(
        role=ParticipantRole.BORROWER,
        name="Kovács Anna",
        contact_id="C-BORROWER",
        birth_date=date(1985, 5, 12),
        gender="nő",
        marital_status="házas",
        citizenship="magyar",
        tax_id="8123456789",
        monthly_income=520_000,
        income_bearing=True,
        address=Address(
            zip_code="1123", city="Budapest", street="Kossuth", house_number="9"
        ),
        incomes=[
            IncomeEntry(
                income_type="bér",
                amount=520_000,
                currency="HUF",
                bank_name="OTP",
                destination="bankszámla",
                account_number="INCOME-1",
                sequence=1,
            ),
            IncomeEntry(
                income_type="bérlési díj",
                amount=80_000,
                currency="HUF",
                bank_name="Raiffeisen",
                destination="bankszámla",
                account_number="INCOME-2",
                sequence=2,
            ),
        ],
        existing_loans=[
            ExistingLoan(
                loan_type="személyi hitel",
                bank_name="CIB",
                account_number="CIB-1",
                original_amount=3_000_000,
                outstanding_amount=1_500_000,
                monthly_payment=45_000,
                currency="HUF",
                refinance=False,
                sequence=1,
            ),
        ],
    )
    first_co = Participant(
        role=ParticipantRole.CO_BORROWER,
        name="Szabó Béla",
        birth_date=date(1980, 1, 2),
        marital_status="nős",
        address=Address(
            zip_code="2000", city="Szentendre", street="Rákóczi", house_number="12"
        ),
    )
    second_co = Participant(
        role=ParticipantRole.CO_BORROWER,
        name="Nagy Erika",
        birth_date=date(1992, 3, 4),
        marital_status="egyedülálló",
        income_bearing=False,
        address=Address(
            zip_code="1051", city="Budapest", street="Váci", house_number="7"
        ),
        incomes=[
            IncomeEntry(
                income_type="kizárt jövedelem",
                amount=999_000,
                currency="HUF",
                bank_name="Excluded Bank",
                destination="készpénz",
                account_number="EXCLUDED",
                sequence=1,
            ),
        ],
        existing_loans=[
            ExistingLoan(
                loan_type="autóhitel",
                bank_name="Erste",
                account_number="ERSTE-1",
                original_amount=2_000_000,
                outstanding_amount=700_000,
                monthly_payment=20_000,
                currency="HUF",
                refinance=True,
                sequence=1,
            ),
        ],
    )
    first_guarantor = Participant(
        role=ParticipantRole.GUARANTOR, name="Kezes Károly", tax_id="8098765432"
    )
    second_guarantor = Participant(role=ParticipantRole.GUARANTOR, name="Kezes Márta")
    first_mortgagor = Participant(role=ParticipantRole.MORTGAGOR, name="Zálog Zoltán")
    second_mortgagor = Participant(
        role=ParticipantRole.MORTGAGOR, name="Zálog Zoltánné"
    )
    return DealData(
        deal_id="GENIE-1",
        loan=LoanDetails(
            loan_amount=30_000_000,
            loan_term_months=240,
            loan_purpose="lakásvásárlás",
            product_name="Piaci lakáshitel",
            product_type="piaci_hitel",
            csok_amount=5_000_000,
        ),
        participants=[
            borrower,
            first_co,
            second_co,
            first_guarantor,
            second_guarantor,
            first_mortgagor,
            second_mortgagor,
        ],
        properties=[
            Property(
                address=Address(
                    zip_code="1123", city="Budapest", street="Kossuth", house_number="9"
                ),
                parcel_number="12345/6/A",
                estimated_value=85_000_000,
                property_type=PropertyType.APARTMENT,
                ingatlan_szerepe="célIngatlan",
            ),
            Property(
                address=Address(
                    zip_code="2080", city="Pilisvörösvár", street="Petőfi", house_number="3"
                ),
                parcel_number="99/1",
                property_type=PropertyType.HOUSE,
                ingatlan_szerepe="pótfedezet",
            ),
        ],
        products=["piaci_hitel"],
        account={"name": "Kovács-Család Kft."},
    )


def _field(
    pdf_name,
    canonical,
    *,
    label="",
    field_type="text",
    page=1,
    fragment=None,
    checkbox_group=None,
    fill_rule=None,
):
    return RecognizedField(
        pdf_field_name=pdf_name,
        label=label,
        field_type=FieldType(field_type),
        canonical_field=canonical,
        confidence=MappingConfidence.MANUAL,
        page_number=page,
        checkbox_group=checkbox_group,
        fill_rule=fill_rule,
        fragment=fragment,
    )


def _mapping(fields, *, character_groups=None, points=None):
    return MappingConfig(
        bank_name="Teszt Bank",
        form_name="genie_szintetikus",
        form_type="acroform",
        fields=list(fields),
        character_groups=list(character_groups or []),
        points=list(points or []),
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
# 1. Direct canonical fields resolve exact values through the resolver
# ──────────────────────────────────────────────────────────────────────────


class TestDirectCanonicalFields:
    def test_borrower_co_borrower_and_explicit_index_values(self, pipeline):
        mapping = _mapping(
            [
                _field("ados_nev", "participant.borrower.name"),
                _field("tars_nev", "participant.co_borrower.name"),
                _field("tars2_nev", "participant.co_borrower[2].name"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["ados_nev"] == "Kovács Anna"
        assert fd["tars_nev"] == "Szabó Béla"
        assert fd["tars2_nev"] == "Nagy Erika"

    def test_guarantor_and_mortgagor_direct_fields(self, pipeline):
        mapping = _mapping(
            [
                _field("kezes_nev", "participant.guarantor.name"),
                _field("kezes_adoazon", "participant.guarantor.tax_id"),
                _field("zalog_nev", "participant.mortgagor.name"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["kezes_nev"] == "Kezes Károly"
        assert fd["kezes_adoazon"] == "8098765432"
        assert fd["zalog_nev"] == "Zálog Zoltán"

    def test_account_and_deal_fields_exact(self, pipeline):
        mapping = _mapping(
            [
                _field("ugyfel_nev", "account.name"),
                _field("hitel_osszeg", "deal.loan_amount"),
                _field("hitel_cel", "deal.loan_purpose"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["ugyfel_nev"] == "Kovács-Család Kft."
        assert fd["hitel_osszeg"] == "30 000 000"
        assert fd["hitel_cel"] == "lakásvásárlás"

    def test_indexed_and_default_properties(self, pipeline):
        mapping = _mapping(
            [
                _field("ing_irsz_2", "property[2].address.zip_code"),
                _field("ing_telepules", "property.address.city"),
                _field("ing_hrsz", "property.parcel_number"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["ing_irsz_2"] == "2080"
        assert fd["ing_telepules"] == "Budapest"
        assert fd["ing_hrsz"] == "12345/6/A"

    def test_income_rows_exact_values_and_money_formatting(self, pipeline):
        mapping = _mapping(
            [
                _field("jov1_osszeg", "participant.borrower.income[1].amount"),
                _field("jov2_tipus", "participant.borrower.income[2].income_type"),
                _field("jov1_bank", "participant.borrower.income[1].bank_name"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["jov1_osszeg"] == "520 000"
        assert fd["jov2_tipus"] == "bérlési díj"
        assert fd["jov1_bank"] == "OTP"

    def test_existing_loan_rows_exact_values(self, pipeline):
        mapping = _mapping(
            [
                _field("hitel1_fennallo", "participant.borrower.existing_loan[1].outstanding_amount"),
                _field("hitel1_torleszto", "participant.borrower.existing_loan[1].monthly_payment"),
                _field("hitel1_bank", "participant.borrower.existing_loan[1].bank_name"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["hitel1_fennallo"] == "1 500 000"
        assert fd["hitel1_torleszto"] == "45 000"
        assert fd["hitel1_bank"] == "CIB"

    def test_date_field_is_central_formatted(self, pipeline):
        mapping = _mapping(
            [_field("szul_datum", "participant.co_borrower[1].birth_date")]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["szul_datum"] == "1980.01.02"

    def test_boolean_deal_flags_fill_checkboxes_as_igen_nem(self, pipeline):
        mapping = _mapping(
            [
                _field("van_zalogkov", "deal.has_mortgagor", field_type="checkbox"),
                _field("van_haszonelvez", "deal.has_usufructuary", field_type="checkbox"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["van_zalogkov"] == "igen"
        assert fd["van_haszonelvez"] == "nem"

    def test_missing_instance_yields_no_value(self, pipeline):
        mapping = _mapping([_field("tars3_nev", "participant.co_borrower[3].name")])
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert "tars3_nev" not in fd


# ──────────────────────────────────────────────────────────────────────────
# 2. PDF field names never select participants
# ──────────────────────────────────────────────────────────────────────────


class TestPdfNamesNeverSelectParticipants:
    def test_tars_named_pdf_field_with_borrower_path_resolves_borrower(self, pipeline):
        mapping = _mapping(
            [_field("SZA_IG_név-társ", "participant.borrower.name")]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["SZA_IG_név-társ"] == "Kovács Anna"

    def test_tars_named_checkbox_group_uses_path_selected_participant(self, pipeline):
        group = {"group_id": "allapot", "group_label": "Családi állapot"}
        mapping = _mapping(
            [
                _field(
                    "allapot_hazas-társ",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Házas"},
                ),
                _field(
                    "allapot_egyedul-társ",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Egyedülálló"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        # Borrower is "házas" → the Házas option ticks, the other stays "nem".
        assert fd["allapot_hazas-társ"] == "igen"
        assert fd["allapot_egyedul-társ"] == "nem"

    def test_tars_named_char_group_members_use_borrower_value(self, pipeline):
        mapping = _mapping(
            [],
            character_groups=[
                {
                    "group_id": "ados_irsz",
                    "canonical_field": "participant.borrower.address.zip_code",
                    "member_fields": ["irsz_tars_1", "irsz_tars_2", "irsz_tars_3", "irsz_tars_4"],
                    "direction": "left_to_right",
                    "separator": "",
                }
            ],
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["irsz_tars_1"] == "1"
        assert fd["irsz_tars_2"] == "1"
        assert fd["irsz_tars_3"] == "2"
        assert fd["irsz_tars_4"] == "3"


# ──────────────────────────────────────────────────────────────────────────
# 3. Explicit (role, index) context
# ──────────────────────────────────────────────────────────────────────────


class TestExplicitResolutionContext:
    def test_context_selects_second_co_borrower_for_base_paths(self, pipeline):
        mapping = _mapping(
            [
                _field("tars_nev", "participant.co_borrower.name"),
                _field("ados_nev", "participant.borrower.name"),
                _field("ados_nev_1", "participant.borrower[1].name"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(),
            mapping,
            context=ResolutionContext(participant=("co_borrower", 2)),
        )
        assert fd["tars_nev"] == "Nagy Erika"
        # F2 — per-applicant selection substitutes BASE participant paths
        # regardless of their declared base role (legacy copy semantics)…
        assert fd["ados_nev"] == "Nagy Erika"
        # …while an explicit index stays fixed.
        assert fd["ados_nev_1"] == "Kovács Anna"

    def test_context_property_index_selects_second_property(self, pipeline):
        mapping = _mapping(
            [
                _field("ing_hrsz", "property.parcel_number"),
                _field("ing_hrsz_1", "property[1].parcel_number"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(),
            mapping,
            context=ResolutionContext(property_index=2),
        )
        assert fd["ing_hrsz"] == "99/1"
        assert fd["ing_hrsz_1"] == "12345/6/A"

    def test_participant_override_is_wrapped_into_context(self, pipeline):
        mapping = _mapping(
            [
                _field("tars_nev", "participant.co_borrower.name"),
                _field("ados_nev", "participant.borrower.name"),
                _field("ados_nev_1", "participant.borrower[1].name"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1
        )
        assert fd["tars_nev"] == "Nagy Erika"
        # F2 — the wrapped selection fills borrower-flavored base paths with
        # the selected co-borrower's data (legacy per-applicant copy parity).
        assert fd["ados_nev"] == "Nagy Erika"
        assert fd["ados_nev_1"] == "Kovács Anna"


class TestRunForDealContextForwarding:
    @staticmethod
    def _stubbed_pipeline(pipeline, deal, tmp_path):
        pipeline.normalizer = types.SimpleNamespace(normalize_deal=lambda raw: deal)
        pipeline.sf_client = types.SimpleNamespace(
            get_deal=lambda deal_id: {"deal_id": deal_id}
        )
        out = tmp_path / "out.pdf"
        pipeline._fill_pdf = lambda *a, **k: (out, [], [])
        pipeline._fill_legal_declarations = lambda *a, **k: 0
        template = tmp_path / "template.pdf"
        template.write_bytes(b"%PDF-1.4")
        return template

    def test_explicit_context_reaches_prepare_field_data(self, pipeline, tmp_path):
        deal = make_genie_deal()
        mapping = _mapping([_field("tars_nev", "participant.co_borrower.name")])
        template = self._stubbed_pipeline(pipeline, deal, tmp_path)
        captured = {}
        real_prepare = pipeline._prepare_field_data

        def spy(deal_arg, mapping_arg, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return real_prepare(deal_arg, mapping_arg, **kwargs)

        pipeline._prepare_field_data = spy
        result = pipeline.run_for_deal(
            "GENIE-1",
            template,
            mapping_config=mapping,
            upload_to_sf=False,
            context=ResolutionContext(participant=("co_borrower", 2)),
        )
        assert result["success"] is True, result["issues"]
        assert captured.get("context") == ResolutionContext(
            participant=("co_borrower", 2)
        )

    def test_without_context_the_context_kwarg_is_passed_as_none(self, pipeline, tmp_path):
        """Cutover Task 3 (additional requirement 5): a ``context`` átadás
        FELTÉTEL NÉLKÜLI — kontextus nélküli hívásnál explicit ``None``
        érkezik a ``_prepare_field_data``-hoz (a régi elhagyó-kwarg viselkedés
        a legacy stubok miatt volt szükséges; az aktiválással törölve)."""
        deal = make_genie_deal()
        mapping = _mapping([_field("tars_nev", "participant.co_borrower.name")])
        template = self._stubbed_pipeline(pipeline, deal, tmp_path)
        captured = {}
        real_prepare = pipeline._prepare_field_data

        def spy(deal_arg, mapping_arg, **kwargs):
            captured.clear()
            captured.update(kwargs)
            return real_prepare(deal_arg, mapping_arg, **kwargs)

        pipeline._prepare_field_data = spy
        result = pipeline.run_for_deal(
            "GENIE-1", template, mapping_config=mapping, upload_to_sf=False
        )
        assert result["success"] is True, result["issues"]
        assert "context" in captured
        assert captured["context"] is None


# ──────────────────────────────────────────────────────────────────────────
# 4. Date / digit fragments
# ──────────────────────────────────────────────────────────────────────────


class TestFragments:
    def test_date_fragments_split_birth_date(self, pipeline):
        mapping = _mapping(
            [
                _field(
                    "szul_ev",
                    "participant.co_borrower[1].birth_date",
                    fragment="year",
                ),
                _field(
                    "szul_ho",
                    "participant.co_borrower[1].birth_date",
                    fragment="month",
                ),
                _field(
                    "szul_nap",
                    "participant.co_borrower[1].birth_date",
                    fragment="day",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["szul_ev"] == "1980"
        assert fd["szul_ho"] == "01"
        assert fd["szul_nap"] == "02"

    def test_digit_fragments_are_one_based(self, pipeline):
        mapping = _mapping(
            [
                _field("ado_1", "participant.borrower.tax_id", fragment="digit:1"),
                _field("ado_3", "participant.borrower.tax_id", fragment="digit:3"),
                _field("ado_10", "participant.borrower.tax_id", fragment="digit:10"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["ado_1"] == "8"
        assert fd["ado_3"] == "2"
        assert fd["ado_10"] == "9"

    def test_date_fragment_inferred_from_pdf_name(self, pipeline):
        mapping = _mapping(
            [_field("Kelt_ing_adatlap_év", "participant.co_borrower[1].birth_date")]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["Kelt_ing_adatlap_év"] == "1980"


# ──────────────────────────────────────────────────────────────────────────
# 5. Checkbox groups
# ──────────────────────────────────────────────────────────────────────────


class TestCheckboxGroups:
    def test_group_ticks_matching_option_only(self, pipeline):
        group = {"group_id": "allapot", "group_label": "Családi állapot"}
        mapping = _mapping(
            [
                _field(
                    "allapot_hazas",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Házas"},
                ),
                _field(
                    "allapot_egyedul",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Egyedülálló"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["allapot_hazas"] == "igen"
        assert fd["allapot_egyedul"] == "nem"

    def test_co_borrower_group_selected_by_path_without_tars_marker(self, pipeline):
        group = {"group_id": "allapot_tars", "group_label": "Társ állapota"}
        mapping = _mapping(
            [
                _field(
                    "csaladi_allapot_1",
                    "participant.co_borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Nős"},
                ),
                _field(
                    "csaladi_allapot_2",
                    "participant.co_borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Nőtlen"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        # First co-borrower is "nős" — selected purely by the canonical path.
        assert fd["csaladi_allapot_1"] == "igen"
        assert fd["csaladi_allapot_2"] == "nem"

    def test_radio_group_export_value_written_to_base_name(self, pipeline):
        group = {"group_id": "neme", "group_label": "Neme"}
        mapping = _mapping(
            [
                _field(
                    "Neme___F",
                    "participant.borrower.gender",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Nő"},
                ),
                _field(
                    "Neme___M",
                    "participant.borrower.gender",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Férfi"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["Neme___F"] == "igen"
        assert fd["Neme"] == "F"
        # Legacy parity: an unticked radio export option gets no value at all.
        assert "Neme___M" not in fd

    def test_csok_amount_bool_group_derivation(self, pipeline):
        group = {"group_id": "csok", "group_label": "CSÖK igénylés"}
        mapping = _mapping(
            [
                _field(
                    "csok_igen",
                    "deal.csok_amount",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Igen"},
                ),
                _field(
                    "csok_nem",
                    "deal.csok_amount",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Nem"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        # csok_amount = 5 000 000 > 0 → "true" → the Igen option ticks.
        assert fd["csok_igen"] == "igen"
        assert fd["csok_nem"] == "nem"

    def test_ingatlan_szerepe_bool_group_derivations(self, pipeline):
        fedezet = {"group_id": "szerepe_fedezet", "group_label": "Fedezet"}
        cel = {"group_id": "szerepe_cel", "group_label": "A hitel célja ingatlan"}
        mapping = _mapping(
            [
                _field(
                    "fedezeti_igen",
                    "property.ingatlan_szerepe",
                    field_type="checkbox",
                    checkbox_group={**fedezet, "match_value": "Igen"},
                ),
                _field(
                    "fedezeti_nem",
                    "property.ingatlan_szerepe",
                    field_type="checkbox",
                    checkbox_group={**fedezet, "match_value": "Nem"},
                ),
                _field(
                    "celing_igen",
                    "property[2].ingatlan_szerepe",
                    field_type="checkbox",
                    checkbox_group={**cel, "match_value": "Igen"},
                ),
                _field(
                    "celing_nem",
                    "property[2].ingatlan_szerepe",
                    field_type="checkbox",
                    checkbox_group={**cel, "match_value": "Nem"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        # Property 1 ("célIngatlan") under a collateral question → Igen.
        assert fd["fedezeti_igen"] == "igen"
        assert fd["fedezeti_nem"] == "nem"
        # Property 2 ("pótfedezet") under a loan-purpose question → Nem.
        assert fd["celing_nem"] == "igen"
        assert fd["celing_igen"] == "nem"

    def test_egyeb_text_field_suppressed_when_standard_option_matched(self, pipeline):
        group = {"group_id": "allamp", "group_label": "Állampolgárság"}
        mapping = _mapping(
            [
                _field(
                    "allamp_magyar",
                    "participant.borrower.citizenship",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Magyar"},
                ),
                _field(
                    "allamp_egyeb",
                    "participant.borrower.citizenship",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Egyéb"},
                ),
                _field("allamp_egyeb_szoveg", "participant.borrower.citizenship"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["allamp_magyar"] == "igen"
        # The "Egyéb: ____" companion text field stays empty.
        assert "allamp_egyeb_szoveg" not in fd


# ──────────────────────────────────────────────────────────────────────────
# 6. Fill rules
# ──────────────────────────────────────────────────────────────────────────


class TestFillRules:
    #: Canonical sentinel so every rule test runs inside the resolver-backed
    #: branch (a mapping without canonical paths would stay on the legacy one).
    SENTINEL = ("ados_nev", "participant.borrower.name", "Kovács Anna")

    def _mapping_with_rules(self, rule_fields):
        fields = [_field(self.SENTINEL[0], self.SENTINEL[1])] + list(rule_fields)
        return _mapping(fields)

    def _assert_sentinel(self, fd):
        assert fd[self.SENTINEL[0]] == self.SENTINEL[2]

    def test_static_rule(self, pipeline):
        mapping = self._mapping_with_rules(
            [
                _field(
                    "alairas",
                    None,
                    fill_rule={"type": "static", "value": "igen"},
                )
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        self._assert_sentinel(fd)
        assert fd["alairas"] == "igen"

    def test_per_participant_rule(self, pipeline):
        mapping = self._mapping_with_rules(
            [
                _field(
                    "minden_aktiv",
                    None,
                    fill_rule={"type": "per_participant", "value": "igen"},
                )
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        self._assert_sentinel(fd)
        assert fd["minden_aktiv"] == "igen"

    def test_role_based_rule_ticks_for_guarantor(self, pipeline):
        mapping = self._mapping_with_rules(
            [
                _field(
                    "van_kezes",
                    None,
                    fill_rule={"type": "role_based", "roles": ["kezes"], "value": "igen"},
                ),
                _field(
                    "van_medico",
                    None,
                    fill_rule={"type": "role_based", "roles": ["orvos"], "value": "igen"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        self._assert_sentinel(fd)
        assert fd["van_kezes"] == "igen"
        assert "van_medico" not in fd

    def test_conditional_rule_resolves_canonical_path(self, pipeline):
        mapping = self._mapping_with_rules(
            [
                _field(
                    "cel_megfelel",
                    None,
                    fill_rule={
                        "type": "conditional",
                        "sf_field": "deal.loan_purpose",
                        "match": "Lakásvásárlás",
                        "value": "igen",
                    },
                ),
                _field(
                    "cel_mas",
                    None,
                    fill_rule={
                        "type": "conditional",
                        "sf_field": "deal.loan_purpose",
                        "match": "Refinanszírozás",
                        "value": "igen",
                    },
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        self._assert_sentinel(fd)
        assert fd["cel_megfelel"] == "igen"
        assert "cel_mas" not in fd

    def test_conditional_rule_with_legacy_sf_field_warns_and_skips(
        self, pipeline, caplog
    ):
        """F3: an sf_field that is not a registry-approved canonical path can
        never match — it must warn and surface a notice, not stay silent."""
        mapping = self._mapping_with_rules(
            [
                _field(
                    "cel_regi",
                    None,
                    fill_rule={
                        "type": "conditional",
                        "sf_field": "Contact.Loan_Purpose__c",
                        "match": "Lakásvásárlás",
                        "value": "igen",
                    },
                ),
            ]
        )
        notices = []
        with caplog.at_level(logging.WARNING, logger="src.main"):
            fd = pipeline._prepare_field_data(
                make_genie_deal(), mapping, notices=notices
            )
        assert "cel_regi" not in fd
        assert any("Contact.Loan_Purpose__c" in r.message for r in caplog.records)
        assert any("Contact.Loan_Purpose__c" in n for n in notices), notices

    def test_conditional_rule_with_empty_sf_field_warns_and_skips(
        self, pipeline, caplog
    ):
        mapping = self._mapping_with_rules(
            [
                _field(
                    "cel_ures",
                    None,
                    fill_rule={
                        "type": "conditional",
                        "sf_field": "",
                        "match": "Lakásvásárlás",
                        "value": "igen",
                    },
                ),
            ]
        )
        notices = []
        with caplog.at_level(logging.WARNING, logger="src.main"):
            fd = pipeline._prepare_field_data(
                make_genie_deal(), mapping, notices=notices
            )
        assert "cel_ures" not in fd
        assert any(
            "cel_ures" in r.message and "sf_field" in r.message
            for r in caplog.records
        )
        assert notices

    def test_legacy_match_value_rule_uses_resolver_value(self, pipeline):
        mapping = _mapping(
            [
                _field(
                    "neme_no",
                    "participant.borrower.gender",
                    fill_rule={"match_value": "Nő"},
                ),
                _field(
                    "neme_ferfi",
                    "participant.borrower.gender",
                    fill_rule={"match_value": "Férfi"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["neme_no"] == "igen"
        assert "neme_ferfi" not in fd


# ──────────────────────────────────────────────────────────────────────────
# 7. Points engine (all seven rule types)
# ──────────────────────────────────────────────────────────────────────────


class TestPointsEngine:
    #: Every points test carries a direct canonical sentinel field so the
    #: mapping is classified Genie-style and the point engine provably runs
    #: inside the resolver-backed branch (not the legacy compatibility branch).
    SENTINEL = ("ados_nev", "participant.borrower.name", "Kovács Anna")

    def _fd(self, pipeline, points, fields=None):
        all_fields = [_field(self.SENTINEL[0], self.SENTINEL[1])]
        all_fields.extend(fields or [])
        mapping = _mapping(all_fields, points=points)
        return pipeline._prepare_field_data(make_genie_deal(), mapping)

    def _assert_sentinel(self, fd):
        assert fd[self.SENTINEL[0]] == self.SENTINEL[2]

    def test_rule_1_unconditional_tick_all(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P1",
                    "framework": "*",
                    "rule_type": 1,
                    "blocks": [{"block_id": "P1.main", "members": ["p1_a", "p1_b"]}],
                    "params": {},
                }
            ],
        )
        self._assert_sentinel(fd)
        assert fd["p1_a"] == "igen" and fd["p1_b"] == "igen"

    def test_rule_2_role_multicheck(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P2",
                    "framework": "*",
                    "rule_type": 2,
                    "blocks": [
                        {
                            "block_id": "P2.main",
                            "members": ["r2_ados", "r2_tars", "r2_kezes"],
                        }
                    ],
                    "params": {
                        "role_to_ticks": {
                            "adós": ["r2_ados"],
                            "adóstárs": ["r2_tars"],
                            "kezes": ["r2_kezes"],
                        }
                    },
                }
            ],
        )
        self._assert_sentinel(fd)
        assert fd["r2_ados"] == "igen"
        assert fd["r2_tars"] == "igen"
        assert fd["r2_kezes"] == "igen"

    def test_rule_3_auto_group_matches_canonical_value(self, pipeline):
        fields = [
            _field("allamp_magyar", "participant.borrower.citizenship", field_type="checkbox"),
            _field("allamp_egyeb", "participant.borrower.citizenship", field_type="checkbox"),
        ]
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "AUTO_ALLAMP",
                    "framework": "*",
                    "rule_type": 3,
                    "_source": "auto_group",
                    "blocks": [
                        {"block_id": "Magyar", "members": ["allamp_magyar"]},
                        {"block_id": "Egyéb", "members": ["allamp_egyeb"]},
                    ],
                    "params": {},
                }
            ],
            fields=fields,
        )
        self._assert_sentinel(fd)
        assert fd["allamp_magyar"] == "igen"
        assert fd["allamp_egyeb"] == "nem"

    def test_rule_4_two_step(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P4",
                    "framework": "*",
                    "rule_type": 4,
                    "blocks": [
                        {
                            "block_id": "P4.main",
                            "members": ["p4_a", "p4_zaro", "p4_szerep"],
                        }
                    ],
                    "params": {
                        "step1_value": "nem vagyok",
                        "closing_field": "p4_zaro",
                        "role_field": "p4_szerep",
                        "closing_value": "igen",
                    },
                }
            ],
        )
        self._assert_sentinel(fd)
        assert fd["p4_a"] == "nem vagyok"
        assert fd["p4_szerep"] == "igen"
        assert fd["p4_zaro"] == "igen"

    def test_rule_5_product_conditional(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P5",
                    "framework": "*",
                    "rule_type": 5,
                    "blocks": [{"block_id": "P5.main", "members": ["p5_x"]}],
                    "params": {"condition_value": "Piaci lakáshitel"},
                },
                {
                    "point_id": "P5b",
                    "framework": "*",
                    "rule_type": 5,
                    "blocks": [{"block_id": "P5b.main", "members": ["p5b_y"]}],
                    "params": {"condition_value": "Másik termék"},
                },
            ],
        )
        self._assert_sentinel(fd)
        assert fd["p5_x"] == "igen"
        assert fd["p5b_y"] == "nem"

    def test_rule_6_loan_purpose_conditional(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P6",
                    "framework": "*",
                    "rule_type": 6,
                    "blocks": [{"block_id": "P6.main", "members": ["p6_z"]}],
                    "params": {"condition_value": "lakásvásárlás"},
                }
            ],
        )
        self._assert_sentinel(fd)
        assert fd["p6_z"] == "igen"

    def test_rule_7_multi_block_pattern(self, pipeline):
        fd = self._fd(
            pipeline,
            [
                {
                    "point_id": "P7",
                    "framework": "*",
                    "rule_type": 7,
                    "blocks": [
                        {"block_id": "B1", "members": ["p7_b1"]},
                        {"block_id": "B2", "members": ["p7_b2"]},
                    ],
                    "params": {
                        "block_rules": {"B1": {"value": "igen"}, "B2": {"value": "nem"}}
                    },
                }
            ],
        )
        self._assert_sentinel(fd)
        assert fd["p7_b1"] == "igen"
        assert fd["p7_b2"] == "nem"


# ──────────────────────────────────────────────────────────────────────────
# 8. Character groups
# ──────────────────────────────────────────────────────────────────────────


class TestCharacterGroups:
    def test_declared_group_expands_co_borrower_zip(self, pipeline):
        mapping = _mapping(
            [],
            character_groups=[
                {
                    "group_id": "co2_irsz",
                    "canonical_field": "participant.co_borrower[2].address.zip_code",
                    "member_fields": ["co2_irsz_1", "co2_irsz_2", "co2_irsz_3", "co2_irsz_4"],
                    "direction": "left_to_right",
                    "separator": "",
                }
            ],
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["co2_irsz_1"] == "1"
        assert fd["co2_irsz_2"] == "0"
        assert fd["co2_irsz_3"] == "5"
        assert fd["co2_irsz_4"] == "1"

    def test_runtime_inferred_group_splits_borrower_zip(self, pipeline):
        mapping = _mapping(
            [
                _field("ados_irsz_part0", "participant.borrower.address.zip_code"),
                _field("ados_irsz_part1", "participant.borrower.address.zip_code"),
                _field("ados_irsz_part2", "participant.borrower.address.zip_code"),
                _field("ados_irsz_part3", "participant.borrower.address.zip_code"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["ados_irsz_part0"] == "1"
        assert fd["ados_irsz_part1"] == "1"
        assert fd["ados_irsz_part2"] == "2"
        assert fd["ados_irsz_part3"] == "3"

    def test_runtime_inferred_group_gated_by_canonical_single_value_code(
        self, pipeline
    ):
        """Cutover Task 3: az egyszeri-érték kódmezők (adóazonosító stb.)
        CANONICAL path alapján kapnak runtime karaktercsoportot — a régi
        ``Contact.*``-kulcsos készlet (Lead.PostalCode, Contact.Tax_ID__c, …)
        helyett parse-alapú mező-egyeztetés, PDF-név kulcsszó nélkül is."""
        mapping = _mapping(
            [
                _field("titkosKod_1", "participant.borrower.tax_id"),
                _field("titkosKod_2", "participant.borrower.tax_id"),
                _field("titkosKod_3", "participant.borrower.tax_id"),
            ]
        )
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping)
        assert fd["titkosKod_1"] == "8"
        assert fd["titkosKod_2"] == "1"
        assert fd["titkosKod_3"] == "2"


# ──────────────────────────────────────────────────────────────────────────
# 9. Income exclusion through resolver policy + notices
# ──────────────────────────────────────────────────────────────────────────


class TestIncomeExclusionAndNotices:
    def test_income_rows_blank_for_income_bearing_false_participant(self, pipeline):
        mapping = _mapping(
            [
                _field("tars2_jov", "participant.co_borrower[2].income[1].amount"),
                _field(
                    "tars2_hitel", "participant.co_borrower[2].existing_loan[1].bank_name"
                ),
                _field("ados_jov", "participant.borrower.income[1].amount"),
            ]
        )
        notices = []
        fd = pipeline._prepare_field_data(make_genie_deal(), mapping, notices=notices)
        assert "tars2_jov" not in fd
        # Existing loans of the same participant are NOT income rows.
        assert fd["tars2_hitel"] == "Erste"
        # The borrower's income is unaffected.
        assert fd["ados_jov"] == "520 000"
        assert any(
            "Nagy Erika" in n and "jövedelem nélkül bevont" in n for n in notices
        ), notices

    def test_account_pool_notice_preserved(self, pipeline):
        deal = make_genie_deal()
        deal.account_pool_extra = ["Extrás Elek"]
        mapping = _mapping([_field("ados_nev", "participant.borrower.name")])
        notices = []
        pipeline._prepare_field_data(deal, mapping, notices=notices)
        assert any("Extrás Elek" in n for n in notices), notices

    def test_borrower_form_income_notice_under_co_borrower_selection(self, pipeline):
        """Re-review fix: a borrower-flavored mapping filled as co_borrower[2]
        (income_bearing=False) blanks the income paths through the F2
        substitution — the notice must follow the EFFECTIVE selected
        participant, not the mapping path's declared role."""
        mapping = _mapping(
            [
                _field("ados_nev", "participant.borrower.name"),
                _field("ados_jov", "participant.borrower.income[1].amount"),
            ]
        )
        notices = []
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1, notices=notices
        )
        assert fd["ados_nev"] == "Nagy Erika"
        assert "ados_jov" not in fd  # a kiválasztott adóstárs jövedelem nélküli
        assert any(
            "Nagy Erika" in n and "jövedelem nélkül bevont" in n for n in notices
        ), notices

    def test_same_role_selection_income_notice(self, pipeline):
        mapping = _mapping(
            [_field("tars_jov", "participant.co_borrower.income[1].amount")]
        )
        notices = []
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1, notices=notices
        )
        assert "tars_jov" not in fd
        assert any(
            "Nagy Erika" in n and "jövedelem nélkül bevont" in n for n in notices
        ), notices

    def test_out_of_range_selection_emits_no_income_notice(self, pipeline):
        """A selected-but-missing participant blanks the base income paths but
        is nobody's data — no exclusion notice may be emitted."""
        mapping = _mapping(
            [
                _field("ados_nev", "participant.borrower.name"),
                _field("ados_jov", "participant.borrower.income[1].amount"),
            ]
        )
        notices = []
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=4, notices=notices
        )
        assert "ados_jov" not in fd
        assert "ados_nev" not in fd
        assert not any("jövedelem nélkül bevont" in n for n in notices), notices

    def test_no_context_income_notice_behavior_preserved(self, pipeline):
        """Without a selection the notice stays keyed by the declared path:
        the excluded co-borrower[2] explicit path notifies, a borrower base
        path (borrower not excluded) does not."""
        explicit = _mapping(
            [_field("tars2_jov", "participant.co_borrower[2].income[1].amount")]
        )
        notices = []
        pipeline._prepare_field_data(make_genie_deal(), explicit, notices=notices)
        assert any("Nagy Erika" in n for n in notices), notices

        base_borrower = _mapping(
            [_field("ados_jov", "participant.borrower.income[1].amount")]
        )
        notices2 = []
        pipeline._prepare_field_data(
            make_genie_deal(), base_borrower, notices=notices2
        )
        assert not any("jövedelem nélkül bevont" in n for n in notices2), notices2


# ──────────────────────────────────────────────────────────────────────────
# 10. Role instances (document copies) through the resolver
# ──────────────────────────────────────────────────────────────────────────


class TestRoleInstancesCanonical:
    @staticmethod
    def _instance(section, instance, role, base_fields):
        from src.engine.document_assembler import InstanceFieldMap

        return InstanceFieldMap(
            section=section, instance=instance, role=role, base_fields=base_fields
        )

    def test_co_borrower_instances_resolve_by_context(self, pipeline):
        from src.engine.document_assembler import INSTANCE_ROLE_CO_BORROWER

        mapping = _mapping(
            [
                _field("SZA_nev", "participant.co_borrower.name"),
                _field("SZA_irsz", "participant.co_borrower.address.zip_code"),
                _field("SZA_szul_ev", "participant.co_borrower.birth_date", fragment="year"),
            ]
        )
        deal = make_genie_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["SZA_nev"] == "Szabó Béla"
        fd = pipeline._apply_instance_data(
            fd,
            deal,
            mapping,
            [
                self._instance(
                    "sza_ig_tarsigenylő_2",
                    2,
                    INSTANCE_ROLE_CO_BORROWER,
                    ["SZA_nev", "SZA_irsz", "SZA_szul_ev"],
                ),
                self._instance(
                    "sza_ig_tarsigenylő_3",
                    3,
                    INSTANCE_ROLE_CO_BORROWER,
                    ["SZA_nev", "SZA_irsz", "SZA_szul_ev"],
                ),
            ],
        )
        assert fd["SZA_nev_2"] == "Nagy Erika"
        assert fd["SZA_irsz_2"] == "1051"
        assert fd["SZA_szul_ev_2"] == "1992"
        # No third co-borrower exists → no _3 keys.
        assert "SZA_nev_3" not in fd
        assert "SZA_irsz_3" not in fd

    def test_guarantor_instance_gets_guarantor_data_not_co_borrower(self, pipeline):
        from src.engine.document_assembler import INSTANCE_ROLE_GUARANTOR

        mapping = _mapping([_field("KEZ_nev", "participant.guarantor.name")])
        deal = make_genie_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["KEZ_nev"] == "Kezes Károly"
        fd = pipeline._apply_instance_data(
            fd,
            deal,
            mapping,
            [
                self._instance(
                    "sza_esz_kezes_2", 2, INSTANCE_ROLE_GUARANTOR, ["KEZ_nev"]
                )
            ],
        )
        assert fd["KEZ_nev_2"] == "Kezes Márta"
        assert fd["KEZ_nev_2"] != "Szabó Béla"
        assert fd["KEZ_nev_2"] != "Nagy Erika"

    def test_mortgagor_instance_gets_mortgagor_data_not_co_borrower(self, pipeline):
        from src.engine.document_assembler import INSTANCE_ROLE_MORTGAGOR

        mapping = _mapping([_field("ZAL_nev", "participant.mortgagor.name")])
        deal = make_genie_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["ZAL_nev"] == "Zálog Zoltán"
        fd = pipeline._apply_instance_data(
            fd,
            deal,
            mapping,
            [
                self._instance(
                    "sza_esz_zalogkovetezett_2",
                    2,
                    INSTANCE_ROLE_MORTGAGOR,
                    ["ZAL_nev"],
                )
            ],
        )
        assert fd["ZAL_nev_2"] == "Zálog Zoltánné"
        assert fd["ZAL_nev_2"] not in ("Szabó Béla", "Nagy Erika")

    def test_property_instance_gets_second_property(self, pipeline):
        from src.engine.document_assembler import INSTANCE_ROLE_PROPERTY

        mapping = _mapping([_field("IA_telepules", "property.address.city")])
        deal = make_genie_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["IA_telepules"] == "Budapest"
        fd = pipeline._apply_instance_data(
            fd,
            deal,
            mapping,
            [
                self._instance(
                    "ingatlan_adatlap_2", 2, INSTANCE_ROLE_PROPERTY, ["IA_telepules"]
                )
            ],
        )
        assert fd["IA_telepules_2"] == "Pilisvörösvár"


# ──────────────────────────────────────────────────────────────────────────
# 11. Document-instance roles and page duplication
# ──────────────────────────────────────────────────────────────────────────


class TestDocumentInstanceRoles:
    def test_instance_role_constants_and_canonical_roles(self):
        import src.engine.document_assembler as da

        assert getattr(da, "INSTANCE_ROLE_MORTGAGOR", None) == "mortgagor"
        role_map = getattr(da, "INSTANCE_ROLE_TO_CANONICAL", None)
        assert role_map is not None, "INSTANCE_ROLE_TO_CANONICAL is missing"
        assert role_map[da.INSTANCE_ROLE_CO_BORROWER] == "co_borrower"
        assert role_map[da.INSTANCE_ROLE_GUARANTOR] == "guarantor"
        assert role_map[da.INSTANCE_ROLE_BENEFICIARY] == "usufructuary"
        assert role_map[da.INSTANCE_ROLE_MORTGAGOR] == "mortgagor"

    def test_kezes_and_zalog_sections_classify_to_their_own_roles(self):
        from src.engine.document_assembler import (
            INSTANCE_ROLE_CO_BORROWER,
            INSTANCE_ROLE_GUARANTOR,
            INSTANCE_ROLE_MORTGAGOR,
            INSTANCE_ROLE_PROPERTY,
            _classify_instance_role,
        )

        assert _classify_instance_role("sza_esz_kezes_2") == INSTANCE_ROLE_GUARANTOR
        assert _classify_instance_role("sza_esz_zalogkötelezett_2") == INSTANCE_ROLE_MORTGAGOR
        assert _classify_instance_role("zalog_adatlap_2") == INSTANCE_ROLE_MORTGAGOR
        # Unchanged classifications:
        assert _classify_instance_role("sza_ig_tarsigenylő_2") == INSTANCE_ROLE_CO_BORROWER
        assert _classify_instance_role("ingatlan_adatlap_2") == INSTANCE_ROLE_PROPERTY

    def test_guarantors_and_mortgagors_do_not_generate_co_borrower_pages(self):
        import src.main as main_module
        from src.engine.document_assembler import (
            DocumentAssembler,
            ProductType,
        )

        applicant_count = getattr(main_module, "_applicant_participant_count", None)
        assert applicant_count is not None, (
            "_applicant_participant_count is missing from src.main"
        )
        deal = make_genie_deal()
        # 7 active participants, but only the 3 applicants drive adatlap copies.
        assert len(deal.active_participants) == 7
        assert applicant_count(deal) == 3

        plan = DocumentAssembler()._build_page_plan(
            [ProductType.PIACI_HITEL], applicant_count(deal), 1
        )
        tars_sections = sorted(
            {e["section"] for e in plan if "tarsigenylő" in e["section"]}
        )
        assert tars_sections == ["sza_ig_tarsigenylő_1", "sza_ig_tarsigenylő_2"]


# ──────────────────────────────────────────────────────────────────────────
# 11b. Review F1 — branch-specific assembly participant count
# ──────────────────────────────────────────────────────────────────────────


class TestAssemblyParticipantCount:
    @staticmethod
    def _stubbed_pipeline(pipeline, deal, tmp_path, monkeypatch):
        from src.engine.document_assembler import AssemblyResult, DocumentAssembler

        pipeline.normalizer = types.SimpleNamespace(normalize_deal=lambda raw: deal)
        pipeline.sf_client = types.SimpleNamespace(
            get_deal=lambda deal_id: {"deal_id": deal_id}
        )
        out = tmp_path / "out.pdf"
        out.write_bytes(b"%PDF-1.4")
        pipeline._fill_pdf = lambda *a, **k: (out, [], [])
        pipeline._fill_legal_declarations = lambda *a, **k: 0
        captured = {}

        def fake_assemble(self, master_pdf, products, num_participants,
                          num_properties, output_path=None):
            captured["num_participants"] = num_participants
            return AssemblyResult(output_path=Path(master_pdf), instance_fields=[])

        monkeypatch.setattr(DocumentAssembler, "assemble", fake_assemble)
        monkeypatch.setattr(DocumentAssembler, "is_master_pdf", lambda self, p: True)
        template = tmp_path / "template.pdf"
        template.write_bytes(b"%PDF-1.4")
        return template, captured

    # Cutover Task 3 rationale: a ``test_legacy_mapping_assembly_counts_all_
    # active_participants`` legacy-paritás teszt törölve — a Task 2-es
    # ideiglenes legacy ág (és az ág-specifikus assembly-számlálás) az
    # aktiválással megszűnt; a legacy mappingeket a pipeline elutasítja.
    # Az egyetlen érvényesesség: minden aktív mappingre az IGÉNYLŐ-számítás
    # érvényes (lásd az alábbi canonical tesztet).

    def test_canonical_mapping_assembly_counts_applicants_only(
        self, pipeline, tmp_path, monkeypatch
    ):
        deal = make_genie_deal()
        template, captured = self._stubbed_pipeline(
            pipeline, deal, tmp_path, monkeypatch
        )
        mapping = _mapping(
            [_field("ados_nev", "participant.borrower.name")]
        )  # canonical
        result = pipeline.run_for_deal(
            "GENIE-1", template, mapping_config=mapping, upload_to_sf=False
        )
        assert result["success"] is True, result["issues"]
        # F1 + Task 3 aktiválás: mindig csak igénylők — kezes/zálogkötelezett
        # sosem generál társigénylő oldalt.
        assert captured["num_participants"] == 3


# ──────────────────────────────────────────────────────────────────────────
# 11c. Review F2 — per-applicant copy semantics for migrated mappings
# ──────────────────────────────────────────────────────────────────────────


class TestPerApplicantCopySemantics:
    def test_borrower_flavored_mapping_shows_selected_applicant(self, pipeline):
        """Legacy copy parity: a borrower-flavored mapping filled for the 2nd
        applicant shows the selected co-borrower in every base path."""
        mapping = _mapping(
            [
                _field("ados_nev", "participant.borrower.name"),
                _field("ados_szul", "participant.borrower.birth_date"),
                _field("ados_nev_1", "participant.borrower[1].name"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1
        )
        assert fd["ados_nev"] == "Nagy Erika"
        assert fd["ados_szul"] == "1992.03.04"
        # Explicit index untouched by the selection.
        assert fd["ados_nev_1"] == "Kovács Anna"

    def test_out_of_range_selection_blanks_base_paths(self, pipeline):
        """A selected-but-missing participant blanks the substituted base
        paths — no fallback to the borrower."""
        mapping = _mapping(
            [
                _field("ados_nev", "participant.borrower.name"),
                _field("tars_nev", "participant.co_borrower.name"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=4
        )  # → ("co_borrower", 5) — only 2 co-borrowers exist
        assert "ados_nev" not in fd
        assert "tars_nev" not in fd
        assert "Kovács Anna" not in fd.values()

    def test_co_borrower_flavored_mapping_and_income_policy(self, pipeline):
        """Same-role selection keeps working, and the selected participant's
        income-exclusion policy applies to substituted income paths."""
        mapping = _mapping(
            [
                _field("tars_nev", "participant.co_borrower.name"),
                _field("tars_jov", "participant.co_borrower.income[1].amount"),
                _field("tars_jov_1", "participant.co_borrower[1].income[1].amount"),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1
        )
        assert fd["tars_nev"] == "Nagy Erika"
        # Selected applicant (co-borrower #2) is income-excluded → blank.
        assert "tars_jov" not in fd
        # Explicit [1] stays on the non-excluded first co-borrower… who has no
        # income rows either → also blank; the borrower's rows prove the
        # policy only follows the selection.
        assert "tars_jov_1" not in fd

    def test_checkbox_group_follows_selection(self, pipeline):
        group = {"group_id": "allapot", "group_label": "Családi állapot"}
        mapping = _mapping(
            [
                _field(
                    "allapot_hazas",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Házas"},
                ),
                _field(
                    "allapot_egyedul",
                    "participant.borrower.marital_status",
                    field_type="checkbox",
                    checkbox_group={**group, "match_value": "Egyedülálló"},
                ),
            ]
        )
        fd = pipeline._prepare_field_data(
            make_genie_deal(), mapping, participant_override=1
        )
        # Selected applicant (Nagy Erika) is "egyedülálló".
        assert fd["allapot_egyedul"] == "igen"
        assert fd["allapot_hazas"] == "nem"

    def test_instance_data_substitutes_borrower_flavored_mapping(self, pipeline):
        from src.engine.document_assembler import (
            InstanceFieldMap,
            INSTANCE_ROLE_CO_BORROWER,
        )

        mapping = _mapping([_field("SZA_nev", "participant.borrower.name")])
        deal = make_genie_deal()
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["SZA_nev"] == "Kovács Anna"
        fd = pipeline._apply_instance_data(
            fd,
            deal,
            mapping,
            [
                InstanceFieldMap(
                    section="sza_ig_tarsigenylő_2",
                    instance=2,
                    role=INSTANCE_ROLE_CO_BORROWER,
                    base_fields=["SZA_nev"],
                )
            ],
        )
        # Legacy copy semantics: the 2nd társigénylő page of a borrower-style
        # template shows the 2nd co-borrower.
        assert fd["SZA_nev_2"] == "Nagy Erika"


# ──────────────────────────────────────────────────────────────────────────
# 11d. Review F4 — product_gate persistence and runtime semantics
# ──────────────────────────────────────────────────────────────────────────


class TestProductGate:
    def test_product_gate_survives_mappingconfig_round_trip(self):
        from src.ai.field_recognizer import MappingConfig

        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="hitel_osszeg_31",
                    label="3.1 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="tamogatott",
                )
            ]
        )
        rebuilt = MappingConfig.from_dict(mapping.to_dict())
        assert rebuilt.fields[0].product_gate == "tamogatott"

    def test_dict_mapping_gate_off_leaves_field_empty(self, pipeline):
        deal = make_genie_deal()  # products = ["piaci_hitel"]
        dict_mapping = {
            "bank_name": "X", "form_name": "y", "form_type": "acroform",
            "fields": [
                {"pdf_field_name": "hitel_osszeg_31", "label": "3.1",
                 "field_type": "text", "canonical_field": "deal.loan_amount",
                 "confidence": "manual", "page_number": 1,
                 "product_gate": "tamogatott"},
                {"pdf_field_name": "ados_nev", "label": "Név",
                 "field_type": "text", "canonical_field": "participant.borrower.name",
                 "confidence": "manual", "page_number": 1},
            ],
        }
        fd = pipeline._prepare_field_data(deal, dict_mapping)
        assert fd["ados_nev"] == "Kovács Anna"
        # Piaci deal → the támogatott (3.1) section stays empty.
        assert "hitel_osszeg_31" not in fd

    def test_object_mapping_gate_on_fills_field(self, pipeline):
        deal = make_genie_deal()
        # Cutover Task 3 (additional requirement 4): a gate-aktiválás MINDEN
        # explicit canonical termék-mezőből áll (deal.products + product_type
        # + product_name) — egy valóban CSÖK-only ügylethez a piaci-jellegű
        # product_type/product_name értékeket is tisztázni kell.
        deal.products = ["csok_plusz"]
        deal.loan.product_type = "csok_plusz"
        deal.loan.product_name = "CSÖK Plusz lakáshitel"
        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="hitel_osszeg_31",
                    label="3.1 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="tamogatott",
                ),
                RecognizedField(
                    pdf_field_name="hitel_osszeg_32",
                    label="3.2 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="piaci",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        # CSÖK-only deal: 3.1 filled, 3.2 (piaci) empty.
        assert fd["hitel_osszeg_31"] == "30 000 000"
        assert "hitel_osszeg_32" not in fd

    def test_product_name_alone_activates_its_gate(self, pipeline):
        """Requirement 4: deal.product_name önmagában is explicit termék-forrás
        (pl. a products lista üres, de a product_name piaci)."""
        deal = make_genie_deal()
        deal.products = []
        deal.loan.product_type = ""
        deal.loan.product_name = "Piaci lakáshitel"
        from src.main import _active_product_gates

        assert _active_product_gates(deal) == frozenset({"piaci"})

    def test_product_type_alone_activates_support_gate(self, pipeline):
        deal = make_genie_deal()
        deal.products = []
        deal.loan.product_name = ""
        deal.loan.product_type = "babaváró kölcsön"
        from src.main import _active_product_gates

        assert _active_product_gates(deal) == frozenset({"tamogatott"})

    def test_all_product_fields_absent_blank_gates_with_notice(self, pipeline):
        """Requirement 4: ha products/product_type/product_name MIND hiányzik,
        a gate-elt szekciók üresek maradnak és explicit notice jelzi — nincs
        csendes alapértelmezés."""
        deal = make_genie_deal()
        deal.products = []
        deal.loan.product_type = ""
        deal.loan.product_name = ""
        from src.main import _active_product_gates

        notices: list = []
        assert _active_product_gates(deal, notices=notices) == frozenset()
        assert any(
            "deal.products" in notice and "product_gate" in notice.replace(
                "product_gate-elt", "product_gate"
            )
            or "termékjelleg" in notice
            for notice in notices
        ), notices

        # A pipeline-szinten is: a gate-elt mező üres marad.
        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="hitel_osszeg_31",
                    label="3.1 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="tamogatott",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(deal, mapping, notices=notices)
        assert "hitel_osszeg_31" not in fd
        assert any("termékjelleg" in notice for notice in notices)

    def test_both_products_activate_both_gates(self, pipeline):
        deal = make_genie_deal()
        deal.products = ["piaci_hitel", "csok_plusz"]
        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="hitel_osszeg_31",
                    label="3.1 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="tamogatott",
                ),
                RecognizedField(
                    pdf_field_name="hitel_osszeg_32",
                    label="3.2 Hitelösszeg",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_amount",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    product_gate="piaci",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["hitel_osszeg_31"] == "30 000 000"
        assert fd["hitel_osszeg_32"] == "30 000 000"

    def test_gated_fill_rule_entry_skipped_when_gate_off(self, pipeline):
        deal = make_genie_deal()  # products = ["piaci_hitel"]
        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="cel_31",
                    label="3.1 cél",
                    field_type=FieldType.TEXT,
                    canonical_field="deal.loan_purpose",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    fill_rule={"type": "static", "value": "igen"},
                    product_gate="tamogatott",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        assert "cel_31" not in fd

    def test_gated_checkbox_group_skipped_entirely_when_gate_off(self, pipeline):
        """Real migrated shape: the 3.1 (tamogatott) loan-purpose radio group
        stays completely untouched on a piaci-only deal — no tick, no 'nem'."""
        deal = make_genie_deal()  # products = ["piaci_hitel"]
        group = {"group_id": "kolcson_celja_31", "group_label": "Kölcsön célja (3.1)"}
        mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="cel_31_uj",
                    label="3.1 Új lakás vásárlása",
                    field_type=FieldType.CHECKBOX,
                    canonical_field="deal.loan_purpose",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    checkbox_group={**group, "match_value": "Lakásvásárlás"},
                    product_gate="tamogatott",
                ),
                RecognizedField(
                    pdf_field_name="cel_31_hasznalt",
                    label="3.1 Használt lakás vásárlása",
                    field_type=FieldType.CHECKBOX,
                    canonical_field="deal.loan_purpose",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                    checkbox_group={**group, "match_value": "Használt lakás vásárlása"},
                    product_gate="tamogatott",
                ),
            ]
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        assert "cel_31_uj" not in fd
        assert "cel_31_hasznalt" not in fd

    def test_point_engine_does_not_tick_gated_off_members(self, pipeline):
        """A point rule listing a product-gated member must not re-fill it
        when the deal's products keep that section inactive."""
        deal = make_genie_deal()  # products = ["piaci_hitel"] → tamogatott off
        gated_member = RecognizedField(
            pdf_field_name="cel_31_pipa",
            label="3.1 cél pipa",
            field_type=FieldType.CHECKBOX,
            canonical_field="deal.loan_purpose",
            confidence=MappingConfidence.MANUAL,
            page_number=1,
            product_gate="tamogatott",
        )
        mapping = _mapping(
            [gated_member, _field("ados_nev", "participant.borrower.name")],
            points=[
                {
                    "point_id": "P1",
                    "framework": "*",
                    "rule_type": 1,
                    "blocks": [
                        {"block_id": "P1.main", "members": ["cel_31_pipa", "p1_sima"]}
                    ],
                    "params": {},
                }
            ],
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["p1_sima"] == "igen"
        # A nem aktív szekció tagját a pont-motor sem tölti újra.
        assert "cel_31_pipa" not in fd


# ──────────────────────────────────────────────────────────────────────────
# 12. Legacy mappings keep working (temporary compatibility branch)
# ──────────────────────────────────────────────────────────────────────────


# Cutover Task 3 rationale: a ``TestLegacyCompatibilityBranch`` canary
# („a legacy Contact.Name mapping még tölt az ideiglenes ágon") törölve —
# a Task 2-es ideiglenes kompatibilitási ágot az aktiválás törölte. Az
# elvárt viselkedés most az ELUTASÍTÁS: legacy canonical mappingre a
# ``_prepare_field_data`` ValueError-t dob (lásd
# tests/test_no_legacy_canonical.py — audit + runtime-raise tesztek).


# ──────────────────────────────────────────────────────────────────────────
# Cutover Task 3 — explicit Genie required paths in the completeness check
# (additional requirement 3: no hardcoded Contact.* list, no PDF-name
# inference; missing values stay diagnostics, never fabricated values)
# ──────────────────────────────────────────────────────────────────────────


class TestCanonicalCompleteness:
    @staticmethod
    def _complete_deal() -> DealData:
        deal = make_genie_deal()
        # A Borrower complete required-field set (make_genie_deal borrower
        # has no phone/birth_name/birth_place by default).
        borrower = deal.borrowers[0]
        borrower.phone = "+36301234567"
        borrower.birth_name = "Kovács Anna Szűz"
        borrower.birth_place = "Budapest"
        borrower.mother_name = "Teszt Anya"
        # Required co-borrower fields: name + birth_date exist on both
        # co-borrowers; tax_id is missing on both — supply it for the
        # COMPLETE case.
        deal.co_borrowers[0].tax_id = "8111111111"
        deal.co_borrowers[1].tax_id = "8122222222"
        return deal

    def test_complete_deal_is_complete(self, pipeline):
        report = pipeline._check_completeness(
            self._complete_deal(), _mapping([_field("ados_nev", "participant.borrower.name")])
        )
        assert report.status == CompletenessStatus.COMPLETE, [
            (i.field_path, i.message) for i in report.issues
        ]
        # Every required path was actually checked (12 required paths; the
        # co-borrower ones double for the two existing co-borrowers).
        assert report.checked_fields >= 12

    def test_missing_borrower_value_is_a_blocking_genie_path_diagnostic(self, pipeline):
        deal = self._complete_deal()
        deal.borrowers[0].tax_id = None
        report = pipeline._check_completeness(deal, _mapping([]))
        blocking = {issue.field_path for issue in report.blocking_issues}
        assert "participant.borrower[1].tax_id" in blocking
        assert report.status == CompletenessStatus.INCOMPLETE

    def test_every_existing_co_borrower_is_checked(self, pipeline):
        deal = self._complete_deal()
        # Both co-borrowers keep names; only the SECOND lacks tax_id now.
        deal.co_borrowers[1].tax_id = None
        report = pipeline._check_completeness(deal, _mapping([]))
        blocking = {issue.field_path for issue in report.blocking_issues}
        assert "participant.co_borrower[2].tax_id" in blocking
        assert "participant.co_borrower[1].tax_id" not in blocking

    def test_no_fabricated_values_and_no_contact_paths(self, pipeline):
        deal = self._complete_deal()
        deal.borrowers[0].birth_place = ""
        report = pipeline._check_completeness(deal, _mapping([]))
        blocking = [i for i in report.blocking_issues]
        assert blocking, "empty birth_place must stay a diagnostic"
        # Diagnostics carry Genie paths — never Contact.* aliases, never a
        # fabricated value that would make the deal look complete.
        assert all(
            not i.field_path.startswith(("Contact.", "Lead.", "Opportunity.", "Account."))
            for i in report.issues
        )
        assert report.status == CompletenessStatus.INCOMPLETE

    def test_single_borrower_deal_has_no_co_borrower_diagnostics(self, pipeline):
        """Review fix (defect 1): required participant paths apply ONLY to
        EXISTING (active) participants. A single-borrower deal must not emit
        phantom blocking ``participant.co_borrower`` MISSING issues — the
        absent role is skipped entirely."""
        deal = self._complete_deal()
        single = DealData(
            deal_id=deal.deal_id,
            loan=deal.loan,
            participants=[deal.borrowers[0]],
            properties=deal.properties,
            products=deal.products,
            account=deal.account,
        )
        report = pipeline._check_completeness(single, _mapping([]))

        co_issues = [
            issue
            for issue in report.issues
            if issue.field_path.startswith("participant.co_borrower")
        ]
        assert co_issues == [], [(i.field_path, i.message) for i in co_issues]
        # A complete single-borrower deal is COMPLETE — no phantom driver.
        assert report.status == CompletenessStatus.COMPLETE, [
            (i.field_path, i.message) for i in report.issues
        ]

    def test_inactive_only_co_borrower_role_counts_as_absent(self, pipeline):
        """Review fix (defect 1): a role whose every participant is INACTIVE
        is absent for required-path purposes — no phantom co-borrower issues,
        status not driven by them."""
        deal = self._complete_deal()
        for co in deal.co_borrowers:
            co.is_active = False
        report = pipeline._check_completeness(deal, _mapping([]))

        co_issues = [
            issue
            for issue in report.issues
            if issue.field_path.startswith("participant.co_borrower")
        ]
        assert co_issues == [], [(i.field_path, i.message) for i in co_issues]
        # The (complete, active) borrower alone decides: no phantom blocker.
        assert report.status == CompletenessStatus.COMPLETE, [
            (i.field_path, i.message) for i in report.issues
        ]


# ═══════════════════════════════════════════════════════════════════════════
# Cutover Task 4 — required-path fail-loudly + product-gate keyword metadata
# ═══════════════════════════════════════════════════════════════════════════

class TestRequiredCanonicalPathsFailLoudly:
    """Additional requirement 4: ismeretlen kötelező canonical útvonal esetén
    a pipeline HANGOSAN hibázik (import-time validáció + _check_completeness
    raise), nem csendben kihagyja a vizsgálatot."""

    def test_module_import_validates_the_required_tuple(self):
        from src.main import validate_required_canonical_paths

        # A valódi tuple registry-approved (különben maga az import megbukna).
        validate_required_canonical_paths()

    def test_unknown_required_path_fails_validation_loudly(self):
        from src.main import validate_required_canonical_paths

        with pytest.raises(ValueError, match="not_in_registry"):
            validate_required_canonical_paths(
                ("deal.loan_amount", "participant.borrower.not_in_registry")
            )

    def test_check_completeness_raises_on_unknown_required_path(
        self, pipeline, monkeypatch
    ):
        import src.main as main_module

        monkeypatch.setattr(
            main_module,
            "_REQUIRED_CANONICAL_PATHS",
            ("participant.borrower.not_in_registry",),
        )
        with pytest.raises(ValueError, match="not_in_registry"):
            pipeline._check_completeness(make_genie_deal(), _mapping([]))

    def test_check_completeness_still_completes_for_valid_paths(self, pipeline):
        deal = TestCanonicalCompleteness._complete_deal()
        report = pipeline._check_completeness(deal, _mapping([]))
        assert report.status == CompletenessStatus.COMPLETE, [
            (i.field_path, i.message) for i in report.issues
        ]
        assert report.checked_fields >= 12


class TestProductGateKeywordMetadata:
    """Additional requirement 5: a product-gate besorolás review-olt, explicit
    kulcsszó-metadata alapján whole-token illesztéssel dolgozik — nincs
    substring túlbekapás, és a jelen-lévő-de-illesztés-nélkül explicit
    termékértékekre warning szól."""

    def test_keyword_table_is_reviewed_explicit_metadata(self):
        from src.main import _PRODUCT_GATE_KEYWORDS

        assert isinstance(_PRODUCT_GATE_KEYWORDS, dict)
        assert set(_PRODUCT_GATE_KEYWORDS.values()) == {"piaci", "tamogatott"}
        # Reviewed Hungarian product vocabulary (folded forms).
        for keyword in ("piaci", "csok", "falusi csok", "babavaro",
                        "kamattamogatott", "otthon start", "otthonteremto"):
            assert keyword in _PRODUCT_GATE_KEYWORDS, keyword

    def test_explicit_product_values_activate_their_gates(self):
        from src.main import _active_product_gates

        for values, expected in (
            (["piaci_hitel"], frozenset({"piaci"})),
            (["Piaci lakáshitel"], frozenset({"piaci"})),
            (["csok_plusz"], frozenset({"tamogatott"})),
            (["falusi CSOK"], frozenset({"tamogatott"})),
            (["Babaváró"], frozenset({"tamogatott"})),
            (["kamattamogatott"], frozenset({"tamogatott"})),
            (["Otthon Start"], frozenset({"tamogatott"})),
            (["piaci_hitel", "csok_plusz"], frozenset({"piaci", "tamogatott"})),
        ):
            deal = make_genie_deal()
            deal.products = list(values)
            deal.loan.product_type = ""
            deal.loan.product_name = ""
            notices: list = []
            assert _active_product_gates(deal, notices=notices) == expected, values
            assert not notices, (values, notices)

    def test_no_substring_over_trigger(self):
        from src.main import _active_product_gates

        for value in ("piacivezer", "babavarok", "csokkal", "csokkal_kiegeszito",
                      "piacimatch"):
            deal = make_genie_deal()
            deal.products = [value]
            deal.loan.product_type = ""
            deal.loan.product_name = ""
            notices: list = []
            assert _active_product_gates(deal, notices=notices) == frozenset(), value
            # Substring non-matches are present-but-unmatched → warning.
            assert notices, value

    def test_present_but_unmatched_product_values_warn(self):
        from src.main import _active_product_gates

        deal = make_genie_deal()
        deal.products = ["zold_lakashitel"]
        deal.loan.product_type = ""
        deal.loan.product_name = ""
        notices: list = []
        assert _active_product_gates(deal, notices=notices) == frozenset()
        assert any("zold_lakashitel" in notice for notice in notices), notices

    def test_absent_product_fields_still_warn_with_the_all_absent_notice(self):
        from src.main import _active_product_gates

        deal = make_genie_deal()
        deal.products = []
        deal.loan.product_type = ""
        deal.loan.product_name = ""
        notices: list = []
        assert _active_product_gates(deal, notices=notices) == frozenset()
        assert any("deal.products" in notice for notice in notices), notices


class TestLegacyMappingErrorIsExplicit:
    """Additional requirement 6: az elavult (legacy) mapping elutasítása
    dedikált hibatípussal történik, amit a /api/pdf/fill és /api/pdf/validate
    operátor-barát 422-re fordít (nem generikus 500)."""

    def test_legacy_error_subclasses_valueerror(self):
        from src.main import LegacyCanonicalMappingError

        assert issubclass(LegacyCanonicalMappingError, ValueError)

    def test_prepare_field_data_raises_dedicated_legacy_error(self, pipeline):
        from src.main import LegacyCanonicalMappingError

        legacy_mapping = _mapping(
            [
                RecognizedField(
                    pdf_field_name="nev",
                    label="Név",
                    field_type=FieldType.TEXT,
                    canonical_field="Contact.Name",
                    confidence=MappingConfidence.MANUAL,
                    page_number=1,
                )
            ]
        )
        with pytest.raises(LegacyCanonicalMappingError):
            pipeline._prepare_field_data(make_genie_deal(), legacy_mapping)

"""Evidence tests for src-only fixes (M6, M8, M9, M10).

These tests import ONLY ``src.*`` modules to avoid colliding on the ``config``
module name (backend/config.py vs the project ``config/`` package).
"""
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── M8: case-insensitive canonical fuzzy match ─────────────────────────────
# Cutover Task 4: a fuzzy match FORMA-tolerancia (kisbetű, index-jelölés),
# kizárólag registry-approved Genie útvonalakra — a régi SF-neves katalógus
# és az "AI által kitalált prefix elfogadása" logika törölve.
class TestM8FuzzyMatchCaseInsensitive:
    def test_lowercase_matches_mixed_case_catalog(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"participant.borrower.name", "property.address.zip_code"}
        assert FieldRecognizer._fuzzy_match_canonical(
            "Participant.Borrower.Name", valid
        ) == "participant.borrower.name"

    def test_original_case_still_matches(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"participant.borrower.name"}
        assert FieldRecognizer._fuzzy_match_canonical(
            "participant.borrower.name", valid
        ) == "participant.borrower.name"

    def test_explicit_index_snaps_to_base_catalog_key(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"participant.borrower.name"}
        out = FieldRecognizer._fuzzy_match_canonical(
            "participant.borrower[1].name", valid
        )
        assert out == "participant.borrower.name"


# ── M9: DocumentAssembler aborts on out-of-range master pages ──────────────
class TestM9AssemblerAborts:
    def test_raises_on_out_of_range_plan(self, tmp_path):
        import pikepdf
        from src.engine.document_assembler import DocumentAssembler, ProductType

        # A 1-page "master" is far smaller than the default page plan needs.
        master = tmp_path / "tiny_master.pdf"
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page()
        pdf.save(master)
        pdf.close()

        assembler = DocumentAssembler()
        with pytest.raises(ValueError, match="oldal tartomány"):
            assembler.assemble(
                master_pdf=master,
                products=[ProductType.PIACI_HITEL],
                num_participants=2,
                num_properties=1,
                output_path=tmp_path / "out.pdf",
            )


# ── M10: Permanent address composite (floor + door) ────────────────────────
# Cutover Task 3 rationale: the legacy ``_address_to_dict`` Contact.* producer
# is deleted; the M10 invariant (the composite address carries floor + door)
# lives in the canonical model's ``Address.full_address`` and is surfaced via
# the registry source ``participant.address.full_address`` (see also
# test_canonical_migration_map.py::test_permanent_address_resolves_nested_…).
class TestM10PermanentAddress:
    def test_full_address_includes_floor_and_door(self):
        from src.models.canonical_model import Address
        from src.canonical.resolver import CanonicalResolver
        from src.models.canonical_model import (
            DealData, LoanDetails, Participant, ParticipantRole,
        )

        addr = Address(
            zip_code="1037", city="Budapest", street="Bécsi út",
            house_number="120", floor="3", door="5",
        )
        deal = DealData(
            deal_id="M10",
            loan=LoanDetails(),
            participants=[
                Participant(role=ParticipantRole.BORROWER, name="Adós", address=addr)
            ],
        )
        resolver = CanonicalResolver()
        full = resolver.resolve(deal, "participant.borrower.permanent_address")
        # The composite address must carry floor + door (the M10 guarantee).
        assert full == addr.full_address
        assert "3. em." in full, full
        assert "5. ajtó" in full, full
        assert "1037" in full and "Budapest" in full, full

    def test_mailing_address_stays_distinct_from_permanent(self):
        from src.models.canonical_model import Address
        from src.canonical.resolver import CanonicalResolver
        from src.models.canonical_model import (
            DealData, LoanDetails, Participant, ParticipantRole,
        )

        permanent = Address(zip_code="1000", city="X", street="Y", house_number="1")
        mailing = Address(zip_code="2000", city="M", street="N", house_number="2")
        deal = DealData(
            deal_id="M10B",
            loan=LoanDetails(),
            participants=[
                Participant(
                    role=ParticipantRole.BORROWER,
                    name="Adós",
                    address=permanent,
                    mailing_address=mailing,
                )
            ],
        )
        resolver = CanonicalResolver()
        # Explicit values only: no mailing ← primary fallback, no invention.
        assert resolver.resolve(deal, "participant.borrower.permanent_address") == (
            permanent.full_address
        )
        assert resolver.resolve(deal, "participant.borrower.mailing_address.city") == "M"


# ── M6: run_for_deal / _fill_pdf returns real filled_fields ────────────────
class TestM6FilledFields:
    def test_fill_pdf_returns_filled_and_skipped(self, tmp_path):
        from src.main import FormFillerPipeline
        from src.integrations.salesforce_client import SalesforceClient
        from src.ai.field_recognizer import (
            MappingConfig, RecognizedField, FieldType, MappingConfidence,
        )

        acroform = PROJECT_ROOT / "samples" / "acroform_sample.pdf"
        if not acroform.exists():
            pytest.skip("acroform_sample.pdf missing")

        pipeline = FormFillerPipeline(
            sf_client=SalesforceClient(
                mock_mode=True,
                mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data",
            ),
            output_dir=tmp_path,
        )
        deals = pipeline.sf_client.list_deals()
        assert deals, "dummy data missing"
        deal_id = deals[0]["deal_id"]
        raw = pipeline.sf_client.get_deal(deal_id)
        deal = pipeline.normalizer.normalize_deal(raw)

        # Cutover Task 4: registry-approved Genie canonical (a pipeline a
        # legacy Contact.* mappinget elutasítja).
        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[RecognizedField("borrower_name", "n", FieldType.TEXT,
                                    "participant.borrower.name",
                                    MappingConfidence.HIGH, 1)],
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        out, filled, skipped = pipeline._fill_pdf(acroform, deal, fd, mapping)
        assert Path(out).exists()
        assert isinstance(filled, list)
        assert isinstance(skipped, list)
        # The mapped field carries a value → it must appear in filled.
        assert "borrower_name" in filled, filled


# ── Visual & Salesforce Coverage Audit Fixes ───────────────────────────────
class TestVisualAndSalesforceCoverageFixes:
    def test_non_numeric_free_text_skipped_in_money_character_group(self):
        from src.engine.pdf_filler import expand_character_groups
        grp = {
            "group_id": "g_teher",
            "canonical_field": "Lead.Ingatlan_terhek__c",
            "member_fields": ["b1", "b2", "b3", "b4"],
            "direction": "right_to_left",
            "separator": "",
        }
        # Free text ("Nincs teher") must NOT be scattered letter-by-letter into Ft digit boxes
        assert expand_character_groups({"Lead.Ingatlan_terhek__c": "Nincs teher"}, [grp]) == {}
        # Numeric value ("450 000") IS distributed right-to-left
        out = expand_character_groups({"Lead.Ingatlan_terhek__c": "2 500"}, [grp])
        assert out == {"b1": "2", "b2": "5", "b3": "0", "b4": "0"}

    # Cutover Task 3 rationale (additional requirement 6): a két legacy
    # érték-heurisztika teszt („Határozatlan" → szerződés-vég dátum törlése,
    # Salutation → nem találgatás név alapján + cégforma/foglalkozás
    # átírások) törölve — ezeket a heurisztikákat szándékosan NEM portoltuk.
    # A canonical pipeline kizárólag explicit canonical értékeket tölt; a
    # nemek a ``participant.gender``, a foglalkozás/cégforma a saját explicit
    # mezőjéből kerül a nyomtatványra. Ha egy nyomtatvány magyar feliratot
    # vár, az a registry-formatting / adapter feladata tesztekkel — sosem
    # PDF-név vagy szabadszöveg sejtés.
    def test_canonical_pipeline_does_not_rewrite_explicit_values(self):
        from src.canonical.resolver import CanonicalResolver
        from src.models.canonical_model import (
            DealData, LoanDetails, Participant, ParticipantRole,
        )

        p = Participant(
            role=ParticipantRole.BORROWER,
            name="Teszt János",
            employment_type="Határozatlan",
            contract_end_date="2028-12-31",
            current_employment_started="2016-04-01",
            personal_id="123456AB",
        )
        deal = DealData(
            deal_id="D-EXPLICIT",
            loan=LoanDetails(),
            participants=[p],
        )
        resolver = CanonicalResolver()
        # Explicit values resolve verbatim — no heuristic rewrites, no guessing.
        assert resolver.resolve(
            deal, "participant.borrower.contract_end_date"
        ) == "2028-12-31"
        assert resolver.resolve(
            deal, "participant.borrower.employment_type"
        ) == "Határozatlan"
        # The personal ID stays the explicit ID-card number (no 11-digit
        # personal-number invention).
        assert resolver.resolve(deal, "participant.borrower.personal_id") == (
            "123456AB"
        )


def test_live_list_deals_returns_minimal_flattened_dto_with_fake_salesforce():
    from src.integrations.salesforce_client import SalesforceClient

    client = SalesforceClient.__new__(SalesforceClient)
    client._mock_mode = False
    queries = []

    class FakeSalesforce:
        def query(self, query):
            queries.append(query)
            return {
                "records": [
                    {
                        "Id": "0061",
                        "Name": "Demo",
                        "StageName": "Open",
                        "CloseDate": "2026-12-31",
                        "Account": {"Name": "Demo Account"},
                        "AnnualRevenue": 123,
                    },
                    {
                        "Id": "0062",
                        "Name": "No Account",
                        "StageName": "New",
                        "CloseDate": None,
                        "Account": None,
                    },
                ]
            }

    client._sf = FakeSalesforce()

    assert client.list_deals() == [
        {
            "Id": "0061",
            "Name": "Demo",
            "StageName": "Open",
            "CloseDate": "2026-12-31",
            "AccountName": "Demo Account",
        },
        {
            "Id": "0062",
            "Name": "No Account",
            "StageName": "New",
            "CloseDate": None,
            "AccountName": None,
        },
    ]
    assert queries == [
        "SELECT Id, Name, StageName, CloseDate, Account.Name "
        "FROM Opportunity ORDER BY CreatedDate DESC LIMIT 500"
    ]

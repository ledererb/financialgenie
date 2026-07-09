"""Evidence tests for src-only fixes (M6, M8, M9, M10).

These tests import ONLY ``src.*`` modules to avoid colliding on the ``config``
module name (backend/config.py vs the project ``config/`` package).
"""
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ── M8: case-insensitive canonical fuzzy match ─────────────────────────────
class TestM8FuzzyMatchCaseInsensitive:
    def test_lowercase_matches_mixed_case_catalog(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"Contact.Name", "Lead.Ingatlan_irsz__c"}
        # "contact.name" must now match "Contact.Name".
        assert FieldRecognizer._fuzzy_match_canonical("contact.name", valid) == "Contact.Name"

    def test_original_case_still_matches(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"Contact.Name"}
        assert FieldRecognizer._fuzzy_match_canonical("Contact.Name", valid) == "Contact.Name"

    def test_returns_original_cased_key_on_suffix_match(self):
        from src.ai.field_recognizer import FieldRecognizer
        valid = {"Contact.Average_monthly_net_income__c"}
        out = FieldRecognizer._fuzzy_match_canonical(
            "contact.average_monthly_net_income__c", valid
        )
        assert out == "Contact.Average_monthly_net_income__c"


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


# ── M10: Permanent_address__c populated from full_address ──────────────────
class TestM10PermanentAddress:
    def test_permanent_address_includes_floor_and_door(self):
        from src.models.canonical_model import Address
        from src.main import FormFillerPipeline

        addr = Address(
            zip_code="1037", city="Budapest", street="Bécsi út",
            house_number="120", floor="3", door="5",
        )
        # Bypass __init__ so we don't need a real SF client.
        pipeline = FormFillerPipeline.__new__(FormFillerPipeline)
        d = pipeline._address_to_dict(addr, "address")
        assert "Contact.Permanent_address__c" in d
        full = d["Contact.Permanent_address__c"]
        # The composite address must carry floor + door (the M10 guarantee).
        # Address.full_address uses the "em." / "ajtó" abbreviations.
        assert "3. em." in full, full
        assert "5. ajtó" in full, full
        assert "1037" in full and "Budapest" in full, full

    def test_mailing_address_does_not_set_permanent(self):
        from src.models.canonical_model import Address
        from src.main import FormFillerPipeline

        addr = Address(zip_code="1000", city="X", street="Y", house_number="1")
        pipeline = FormFillerPipeline.__new__(FormFillerPipeline)
        d = pipeline._address_to_dict(addr, "mailing_address")
        # The permanent composite is only for the primary address namespace.
        assert "Contact.Permanent_address__c" not in d


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

        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[RecognizedField("borrower_name", "n", FieldType.TEXT,
                                    "Contact.Name", MappingConfidence.HIGH, 1)],
        )
        fd = pipeline._prepare_field_data(deal, mapping)
        out, filled, skipped = pipeline._fill_pdf(acroform, deal, fd, mapping)
        assert Path(out).exists()
        assert isinstance(filled, list)
        assert isinstance(skipped, list)
        # The mapped field carries a value → it must appear in filled.
        assert "borrower_name" in filled, filled

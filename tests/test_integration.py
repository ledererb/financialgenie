"""
FinancialGenie – Integrációs teszt: Teljes pipeline
"""

import json
import pytest
from pathlib import Path

from src.main import FormFillerPipeline
from src.integrations.salesforce_client import SalesforceClient
from src.normalizer.data_normalizer import DataNormalizer
from src.ai.field_recognizer import MappingConfig, FieldRecognizer

PROJECT_ROOT = Path(__file__).parent.parent


@pytest.fixture
def dummy_data_dir():
    return PROJECT_ROOT / "samples" / "dummy_data"


@pytest.fixture
def acroform_pdf():
    return PROJECT_ROOT / "samples" / "acroform_sample.pdf"


@pytest.fixture
def flat_pdf():
    return PROJECT_ROOT / "samples" / "flat_sample.pdf"


@pytest.fixture
def otp_mapping():
    mapping_path = PROJECT_ROOT / "src" / "mapping" / "otp_acroform_mapping.json"
    return MappingConfig.load(mapping_path)


@pytest.fixture
def pipeline(dummy_data_dir, tmp_path):
    return FormFillerPipeline(
        sf_client=SalesforceClient(mock_mode=True, mock_data_dir=dummy_data_dir),
        output_dir=tmp_path,
    )


class TestEndToEndPipeline:
    """End-to-end tesztek a teljes pipeline-ra."""

    @pytest.mark.skipif(
        not (PROJECT_ROOT / "samples" / "dummy_data").exists(),
        reason="Dummy adatok nem generáltak – futtasd: python scripts/generate_dummy_data.py"
    )
    def test_simple_scenario_fills_pdf(self, pipeline, acroform_pdf, otp_mapping):
        """Egyszerű forgatókönyv: 1 adós, 1 ingatlan."""
        if not acroform_pdf.exists():
            pytest.skip("AcroForm minta PDF nem elérhető")

        deals = pipeline.sf_client.list_deals()
        assert len(deals) > 0

        deal_id = deals[0]["deal_id"]
        result = pipeline.run_for_deal(deal_id, acroform_pdf, otp_mapping)

        assert result["success"] is True
        assert result["output_path"] is not None
        assert Path(result["output_path"]).exists()

    def test_mock_salesforce_returns_deals(self, pipeline):
        """Mock Salesforce kliens visszaad ügyleteket."""
        deals = pipeline.sf_client.list_deals()
        assert len(deals) >= 1

    def test_stage_update(self, pipeline):
        """Stage váltás működik mock módban."""
        deals = pipeline.sf_client.list_deals()
        deal_id = deals[0]["deal_id"]

        success = pipeline.sf_client.update_stage(deal_id, "Dokumentáció kész")
        assert success is True

        stage = pipeline.sf_client.get_deal_stage(deal_id)
        assert stage == "Dokumentáció kész"


class TestFieldRecognizer:
    """AI mezőfelismerő tesztek (heurisztikus mód, API nélkül)."""

    def test_acroform_recognition(self, acroform_pdf):
        """AcroForm mezők felismerése."""
        if not acroform_pdf.exists():
            pytest.skip("AcroForm minta PDF nem elérhető")

        recognizer = FieldRecognizer()  # API nélkül → heurisztikus mód
        mapping = recognizer.recognize(acroform_pdf, mode="acroform")

        assert mapping.form_type == "acroform"
        assert len(mapping.fields) > 0
        assert mapping.bank_name == "OTP Bank"

    def test_auto_mode_detects_acroform(self, acroform_pdf):
        """Auto mód felismeri az AcroForm PDF-et."""
        if not acroform_pdf.exists():
            pytest.skip("AcroForm minta PDF nem elérhető")

        recognizer = FieldRecognizer()
        mapping = recognizer.recognize(acroform_pdf, mode="auto")
        assert mapping.form_type == "acroform"

    def test_flat_recognition(self, flat_pdf):
        """Lapos PDF felismerése (mock mód)."""
        if not flat_pdf.exists():
            pytest.skip("Flat minta PDF nem elérhető")

        recognizer = FieldRecognizer()
        mapping = recognizer.recognize(flat_pdf, mode="overlay")

        assert mapping.form_type == "flat"
        # Lapos PDF-ben szövegkeresés alapján kellene mezőket találnia
        assert mapping.bank_name == "OTP Bank"


class TestMappingConfig:
    """Mapping konfiguráció tesztek."""

    def test_load_mapping(self, otp_mapping):
        assert otp_mapping.bank_name == "OTP Bank"
        assert len(otp_mapping.fields) > 0
        assert otp_mapping.approved is True

    def test_mapping_dict(self, otp_mapping):
        md = otp_mapping.mapping_dict
        assert "borrower_name" in md
        assert md["borrower_name"] == "participant.name"
        assert "loan_amount" in md

    def test_save_and_load_roundtrip(self, otp_mapping, tmp_path):
        save_path = tmp_path / "test_mapping.json"
        otp_mapping.save(save_path)

        loaded = MappingConfig.load(save_path)
        assert loaded.bank_name == otp_mapping.bank_name
        assert len(loaded.fields) == len(otp_mapping.fields)

    def test_high_confidence_fields(self, otp_mapping):
        high = otp_mapping.high_confidence_fields
        assert len(high) > 0
        assert all(f.confidence.value == "high" for f in high)

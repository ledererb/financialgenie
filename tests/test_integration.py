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

    def test_run_for_deal_resolves_mapping_automatically(self, pipeline, acroform_pdf):
        """Verify run_for_deal resolves mapping automatically when mapping_config is None."""
        if not acroform_pdf.exists():
            pytest.skip("AcroForm minta PDF nem elérhető")

        deals = pipeline.sf_client.list_deals()
        assert len(deals) > 0
        deal_id = deals[0]["deal_id"]

        # First verify we can run _resolve_mapping directly
        resolved = pipeline._resolve_mapping(acroform_pdf)
        assert resolved is not None
        assert resolved.bank_name == "OTP Bank"

        # Now run the full pipeline without passing mapping_config
        result = pipeline.run_for_deal(deal_id, acroform_pdf, mapping_config=None)
        assert result["success"] is True
        assert result["output_path"] is not None
        assert Path(result["output_path"]).exists()

    def test_resolve_mapping_generates_if_missing(self, pipeline, tmp_path):
        """Verify _resolve_mapping automatically generates a mapping file if it does not exist."""
        # Create a new dummy pdf file under samples/
        dummy_pdf = PROJECT_ROOT / "samples" / "temporary_test_form.pdf"
        # Copy acroform_pdf to dummy_pdf
        import shutil
        acroform_path = PROJECT_ROOT / "samples" / "acroform_sample.pdf"
        if not acroform_path.exists():
            pytest.skip("acroform_sample.pdf is missing")
            
        shutil.copy(acroform_path, dummy_pdf)
        
        # Expected mapping path: src/mapping/temporary_test_form_mapping.json
        expected_mapping_path = PROJECT_ROOT / "src" / "mapping" / "temporary_test_form_mapping.json"
        if expected_mapping_path.exists():
            expected_mapping_path.unlink()

        try:
            # Resolve mapping config – should automatically recognize and save it
            mapping = pipeline._resolve_mapping(dummy_pdf)
            assert mapping is not None
            assert expected_mapping_path.exists()
            assert len(mapping.fields) > 0
        finally:
            # Clean up files
            if dummy_pdf.exists():
                dummy_pdf.unlink()
            if expected_mapping_path.exists():
                expected_mapping_path.unlink()


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
        assert md["borrower_name"] == "Contact.Name"
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


class TestAcroFormFillerKidsAndFallback:
    """Tesztek a /Kids bejárásra és a field_data feloldási fallback-re."""

    def test_resolve_field_value_with_pdf_field_name_fallback(self):
        from src.engine.pdf_filler import AcroFormFiller
        filler = AcroFormFiller()
        mapping = {"pdf_field": "canonical_field"}
        
        # 1. Ha a field_data kanonikus kulccsal van kitöltve (hagyományos eset)
        fd_canonical = {"canonical_field": "value1"}
        canon, val = filler._resolve_field_value("pdf_field", mapping, fd_canonical)
        assert canon == "canonical_field"
        assert val == "value1"

        # 2. Ha a field_data közvetlenül PDF mezőnévvel van kitöltve (pipeline eset)
        fd_pdf = {"pdf_field": "value2"}
        canon, val = filler._resolve_field_value("pdf_field", mapping, fd_pdf)
        assert canon == "canonical_field"
        assert val == "value2"

    def test_fill_fields_recursive_handles_kids_without_t(self, tmp_path):
        import pikepdf
        from src.engine.pdf_filler import AcroFormFiller, FillingResult
        
        # Létrehozunk egy egyszerű PDF-et AcroForm mezővel, aminek vannak /Kids widgetjei
        pdf = pikepdf.Pdf.new()
        page = pdf.add_blank_page()
        
        # Létrehozunk egy szülő mezőt névvel (/T) és típussal (/FT)
        parent_field = pikepdf.Dictionary(
            T=pikepdf.String("TestField"),
            FT=pikepdf.Name("/Tx")
        )
        
        # Létrehozunk egy widgetet név nélkül
        widget = pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Widget"),
            Rect=pikepdf.Array([10, 10, 100, 100]),
            Parent=parent_field
        )
        
        parent_field.Kids = pikepdf.Array([widget])
        
        # Hozzáadjuk a widgetet az oldalhoz, és a szülőt az AcroForm-hoz
        page.Annots = pdf.make_indirect(pikepdf.Array([widget]))
        
        acroform = pikepdf.Dictionary(
            Fields=pikepdf.Array([pdf.make_indirect(parent_field)])
        )
        pdf.Root.AcroForm = pdf.make_indirect(acroform)
        
        pdf_path = tmp_path / "kids_test.pdf"
        out_path = tmp_path / "kids_test_filled.pdf"
        pdf.save(pdf_path)
        pdf.close()
        
        # Futtatjuk a kitöltést
        filler = AcroFormFiller()
        mapping = {"TestField": "canonical.field"}
        field_data = {"TestField": "SuccessValue"}
        
        result = filler.fill(pdf_path, out_path, field_data, mapping)
        assert result.success is True
        assert "TestField" in result.filled_fields
        
        # Ellenőrizzük, hogy a beírt érték helyes-e
        with pikepdf.open(out_path) as pdf_filled:
            f = pdf_filled.Root.AcroForm.Fields[0]
            assert str(f.V) == "SuccessValue"

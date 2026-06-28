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


class TestCharBoxDistribution:
    """Karakter-szintű doboz-szétosztás tesztek (értékbecslés PDF)."""

    def _make_mapping(self, fields_spec):
        """fields_spec: lista (pdf_name, canonical, width, y, x)"""
        from src.ai.field_recognizer import (
            RecognizedField, FieldType, MappingConfidence,
        )
        return MappingConfig(
            bank_name="Test",
            form_name="test",
            form_type="acroform",
            fields=[
                RecognizedField(
                    pdf_field_name=name,
                    label=name,
                    field_type=FieldType.TEXT,
                    canonical_field=canon,
                    confidence=MappingConfidence.HIGH,
                    page_number=1,
                    coordinates={"x": x, "y": y, "width": w, "height": 19.0},
                )
                for (name, canon, w, y, x) in fields_spec
            ],
        )

    def test_digits_only_income_distribution(self):
        """Havi jövedelem 6 keskeny dobozba → 1 számjegy dobozonként."""
        mapping = self._make_mapping([
            (f"income_{i}", "participant.monthly_income", 13.7, 500.0, 100 + i * 16)
            for i in range(6)
        ])
        fd = {f"income_{i}": "450 000" for i in range(6)}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        assert [fd[f"income_{i}"] for i in range(6)] == list("450000")

    def test_phone_keeps_plus_separators_stripped(self):
        """Telefonszám: a '+' és szóközök leesnek, csak számjegy marad."""
        mapping = self._make_mapping([
            (f"phone_{i}", "participant.phone", 15.1, 100.0, 100 + i * 16)
            for i in range(11)
        ])
        fd = {f"phone_{i}": "+36 20 611 3485" for i in range(11)}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        assert "".join(fd[f"phone_{i}"] for i in range(11)) == "36206113485"

    def test_wide_field_keeps_full_value(self):
        """A széles (≥20pt) mező nem vesz részt a szétosztásban."""
        mapping = self._make_mapping([
            ("wide", "participant.phone", 264.5, 100.0, 0),
            *((f"n_{i}", "participant.phone", 15.1, 100.0, 300 + i * 16) for i in range(11)),
        ])
        fd = {n: "+36 20 611 3485" for n in ["wide"] + [f"n_{i}" for i in range(11)]}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        # széles megtartja a teljes értéket
        assert fd["wide"] == "+36 20 611 3485"
        # keskenyek számjegyenként
        assert "".join(fd[f"n_{i}"] for i in range(11)) == "36206113485"

    def test_different_rows_not_merged(self):
        """Két különböző sorban lévő keskeny doboz nem keveredik össze."""
        mapping = self._make_mapping([
            *((f"r1_{i}", "participant.monthly_income", 13.7, 500.0, 100 + i * 16) for i in range(3)),
            *((f"r2_{i}", "participant.monthly_income", 13.7, 600.0, 100 + i * 16) for i in range(3)),
        ])
        fd = {n: "450 000" for n in [f"r1_{i}" for i in range(3)] + [f"r2_{i}" for i in range(3)]}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        assert [fd[f"r1_{i}"] for i in range(3)] == ["4", "5", "0"]
        assert [fd[f"r2_{i}"] for i in range(3)] == ["4", "5", "0"]

    def test_personal_id_keeps_letters(self):
        """Személyi igazolvány: betűk megtartva, írásjelek eldobva."""
        mapping = self._make_mapping([
            (f"id_{i}", "participant.personal_id", 15.1, 100.0, 100 + i * 16)
            for i in range(8)
        ])
        fd = {f"id_{i}": "123456AB" for i in range(8)}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        assert "".join(fd[f"id_{i}"] for i in range(8)) == "123456AB"

    def test_explode_for_boxes_helpers(self):
        p = FormFillerPipeline()
        assert p._explode_for_boxes("participant.monthly_income", "450 000") == list("450000")
        assert p._explode_for_boxes("participant.birth_date", "1985.05.12") == list("19850512")
        assert p._explode_for_boxes("participant.personal_id", "123-456/AB") == list("123456AB")
        assert p._explode_for_boxes("participant.name", "Kovács János") == list("KovácsJános")

    def test_no_coordinates_is_noop(self):
        """Ha egy mezőnek nincsenek koordinátái (pl. OTP v5), nem történik semmi."""
        from src.ai.field_recognizer import (
            RecognizedField, FieldType, MappingConfidence,
        )
        mapping = MappingConfig(
            bank_name="OTP", form_name="v5", form_type="acroform",
            fields=[
                RecognizedField(
                    pdf_field_name="f1", label="x", field_type=FieldType.TEXT,
                    canonical_field="participant.name",
                    confidence=MappingConfidence.HIGH, page_number=1,
                    coordinates=None,
                ),
            ],
        )
        fd = {"f1": "Kovács János"}
        FormFillerPipeline()._distribute_char_fields(fd, mapping)
        assert fd == {"f1": "Kovács János"}

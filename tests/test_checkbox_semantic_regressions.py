"""Production-mapping regressions for citizenship and income row selection."""

import json
from pathlib import Path

import pytest

from src.ai.field_recognizer import MappingConfig
from src.main import FormFillerPipeline
from src.models.canonical_model import DealData, LoanDetails, Participant, ParticipantRole
from src.integrations.salesforce_client import SalesforceClient


ROOT = Path(__file__).resolve().parents[1]


def _pipeline():
    return FormFillerPipeline(
        sf_client=SalesforceClient(mock_mode=True, mock_data_dir=ROOT / "samples" / "dummy_data"),
        output_dir=ROOT / "output",
    )


def _deal(**participant_values):
    values = {"role": ParticipantRole.BORROWER, "name": "Teszt Adós"}
    values.update(participant_values)
    return DealData(
        deal_id="TEST-CHECKBOX-SEMANTICS",
        loan=LoanDetails(),
        participants=[Participant(**values)],
    )


def _mapping(name):
    return MappingConfig.from_dict(
        json.loads((ROOT / "src" / "mapping" / name).read_text(encoding="utf-8"))
    )


def test_hungarian_citizenship_selects_standard_option_and_clears_other():
    data = _pipeline()._prepare_field_data(
        _deal(citizenship="magyar"), _mapping("sza_ig_igenylo_mapping.json")
    )

    assert data.get("SZA_IG_állampolgárság___Yes") == "igen"
    assert data.get("SZA_IG_állampolgárság___no") in (None, "nem")
    assert not data.get("SZA_IG_állampolgárság_egyéb")


def test_foreign_income_uses_foreign_row_and_non_otp_destination():
    data = _pipeline()._prepare_field_data(
        _deal(
            income_type="Külföldről származó munkabér",
            monthly_income=3_915_000,
            account_holding_bank="Revolut",
        ),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert data.get("SZA_IG_jövedelem7") == "igen"
    assert data.get("SZA_IG_jövedelem_összeg7") == "3 915 000"
    assert data.get("SZA_IG_jövedelem_érk7___2,3") == "igen"
    assert data.get("SZA_IG_jövedelem_érk7___1,2") in (None, "nem")
    assert data.get("SZA_IG_jövedelem1") == "nem"
    assert not data.get("SZA_IG_jövedelem_összeg1")


def test_domestic_income_stays_on_domestic_row():
    data = _pipeline()._prepare_field_data(
        _deal(
            income_type="Munkabér (belföldről)",
            monthly_income=3_915_000,
            account_holding_bank="OTP Bank",
        ),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert data.get("SZA_IG_jövedelem1") == "igen"
    assert data.get("SZA_IG_jövedelem_összeg1") == "3 915 000"
    assert data.get("SZA_IG_jövedelem_érk1___1,2") == "igen"
    assert data.get("SZA_IG_jövedelem7") == "nem"
    assert not data.get("SZA_IG_jövedelem_összeg7")


def test_missing_income_type_does_not_invent_a_row_selection():
    data = _pipeline()._prepare_field_data(
        _deal(monthly_income=3_915_000, account_holding_bank="Revolut"),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert data.get("SZA_IG_jövedelem1") in (None, "nem")
    assert data.get("SZA_IG_jövedelem7") in (None, "nem")
    assert not data.get("SZA_IG_jövedelem_összeg1")
    assert not data.get("SZA_IG_jövedelem_összeg7")
    assert not data.get("SZA_IG_jövedelem_érk7___1,2")
    assert not data.get("SZA_IG_jövedelem_érk7___2,3")


def test_missing_bank_does_not_invent_destination():
    data = _pipeline()._prepare_field_data(
        _deal(income_type="Külföldről származó munkabér", monthly_income=3_915_000),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert data.get("SZA_IG_jövedelem7") == "igen"
    assert data.get("SZA_IG_jövedelem_összeg7") == "3 915 000"
    assert data.get("SZA_IG_jövedelem_érk7___1,2") in (None, "nem")
    assert data.get("SZA_IG_jövedelem_érk7___2,3") in (None, "nem")
    assert data.get("SZA_IG_jövedelem_érk7___3,4") in (None, "nem")


def test_zero_amounts_stay_blank_on_unselected_income_rows():
    data = _pipeline()._prepare_field_data(
        _deal(
            income_type="Munkabér (belföldről)",
            monthly_income=3_915_000,
            other_monthly_income=0,
            cafeteria_bonus=0,
            income_from_self_owned=0,
        ),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert not data.get("SZA_IG_jövedelem_összeg2")
    assert not data.get("SZA_IG_jövedelem_összeg3")
    assert not data.get("SZA_IG_jövedelem_összeg4")


def test_other_income_amount_appears_only_on_matching_row():
    data = _pipeline()._prepare_field_data(
        _deal(
            income_type="Családi pótlék",
            other_monthly_income=50_000,
            cafeteria_bonus=0,
            income_from_self_owned=0,
        ),
        _mapping("sza_ig_igenylo_mapping.json"),
    )

    assert data.get("SZA_IG_jövedelem_összeg2") == "50 000"
    assert not data.get("SZA_IG_jövedelem_összeg3")
    assert not data.get("SZA_IG_jövedelem_összeg4")


def test_co_borrower_range_consent_selects_otp_destination():
    deal = _deal(
        income_type="Munkabér (belföldről)",
        monthly_income=308_000,
        jovairas_vallalasa="igen, 0-499 ezer Ft",
    )
    deal.participants[0].role = ParticipantRole.CO_BORROWER
    data = _pipeline()._prepare_field_data(
        deal, _mapping("sza_ig_tarsigenylo_mapping.json")
    )

    assert data.get("SZA_IG_jövedelem_érk1-társ___1,2") == "igen"
    assert data.get("SZA_IG_jövedelem_érk1-társ___2,3") in (None, "nem")

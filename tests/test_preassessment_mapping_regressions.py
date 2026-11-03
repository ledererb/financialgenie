"""Regressions for the Balogh pre-assessment field mapping and Salesforce gaps."""

import json
from pathlib import Path

from src.ai.field_recognizer import MappingConfig
from src.canonical.resolver import CanonicalResolver
from src.integrations.salesforce_client import SalesforceClient
from src.main import FormFillerPipeline
from src.models.canonical_model import DealData, LoanDetails, Participant, ParticipantRole
from src.normalizer.data_normalizer import DataNormalizer
from tests.test_salesforce_normalizer_live_shape import DescribeFakeSalesforce, make_schema


ROOT = Path(__file__).resolve().parents[1]


def _pipeline():
    return FormFillerPipeline(
        sf_client=SalesforceClient(mock_mode=True, mock_data_dir=ROOT / "samples" / "dummy_data"),
        output_dir=ROOT / "output",
    )


def _mapping():
    return MappingConfig.from_dict(
        json.loads(
            (ROOT / "src/mapping/Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json").read_text()
        )
    )


def _deal(**participant_values):
    values = {"role": ParticipantRole.BORROWER, "name": "Teszt Adós"}
    values.update(participant_values)
    return DealData(deal_id="TEST-PREASSESSMENT", loan=LoanDetails(), participants=[Participant(**values)])


def _deal_with_property_comment(comment):
    schema = make_schema(["Id", "Name"], ["Id", "Ingatlan_megjegyzes__c"])
    opportunity = {
        "Id": "006PROPERTYCOMMENT1",
        "AccountId": "001PROPERTYCOMMENT",
        "Opportunity_Contact_Name__c": "003PROPERTYCOMMENT",
    }
    contact = {"Id": "003PROPERTYCOMMENT", "Name": "Property Adós"}
    prop = {
        "Id": "a00PROPERTYCOMMENT",
        "Name": "Célingatlan",
        "Ingatlan_megjegyzes__c": comment,
    }
    fake = DescribeFakeSalesforce(
        opportunity,
        contact,
        prop,
        {"Id": "001PROPERTYCOMMENT", "Name": "Property Account"},
    )
    client = SalesforceClient(mock_mode=True, mock_data_dir=Path("/tmp"), schema=schema)
    client._mock_mode = False
    client._sf = fake
    return DataNormalizer().normalize_deal(client.get_deal(opportunity["Id"]))


def test_property_parcel_number_falls_back_to_ingatlan_megjegyzes():
    deal = _deal_with_property_comment("12345/6 A/1")

    assert deal.properties[0].parcel_number == "12345/6 A/1"
    assert CanonicalResolver().resolve_text(deal, "property.parcel_number") == "12345/6 A/1"


def test_property_parcel_number_ignores_descriptive_comment():
    deal = _deal_with_property_comment("Részben tetőtéri, tulajdoni lapon 68 m2")

    assert deal.properties[0].parcel_number == ""
    assert CanonicalResolver().resolve_text(deal, "property.parcel_number") == ""


def test_purchase_price_uses_digit_group():
    deal = _deal()
    deal.loan.purchase_price = 250_000_000
    data = _pipeline()._prepare_field_data(deal, _mapping())

    for index, value in enumerate("250000000", 1):
        assert data.get(f"Text Field {1171 + index}") == value


def test_residence_status_drives_legal_title_checkboxes():
    deal = _deal(residence_status="tulajdonos")
    data = _pipeline()._prepare_field_data(deal, _mapping())

    assert data.get("Check Box 38___1,2") == "igen"
    assert data.get("Check Box 38___2,3") in (None, "nem")


def test_borrower_phone_character_group_contains_all_digits():
    for phone in ("+36123456789", "+36 12 345 6789"):
        deal = _deal(phone=phone)
        data = _pipeline()._prepare_field_data(deal, _mapping())

        for index, value in enumerate("36123456789", 1):
            assert data.get(f"Text Field {345 + index}") == value


def test_second_declaration_uses_signature_date_not_handover_date():
    deal = _deal(date_of_signature="2026-11-03", date_of_handover="2025-01-01")
    data = _pipeline()._prepare_field_data(deal, _mapping())

    assert data.get("Text Field 1255") == "2026.11.03"


def test_date_character_groups_apply_year_month_day_fragments():
    deal = _deal(
        birth_date="1985-08-15",
        date_of_notification_for_residence="2018-09-26",
    )
    deal.participants.append(
        Participant(
            role=ParticipantRole.CO_BORROWER,
            name="Teszt Adóstárs",
            birth_date="1989-05-05",
            date_of_notification_for_residence="2018-09-26",
        )
    )
    data = _pipeline()._prepare_field_data(deal, _mapping())

    def assert_cells(start, expected):
        for index, value in enumerate(expected):
            assert data.get(f"Text Field {start + index}") == value

    assert_cells(307, "1985")
    assert_cells(311, "08")
    assert_cells(313, "15")
    assert_cells(709, "1989")
    assert_cells(713, "05")
    assert_cells(715, "05")
    assert_cells(357, "2018")
    assert_cells(361, "09")
    assert_cells(724, "2018")
    assert_cells(728, "09")


def test_presence_and_prefix_boolean_checkboxes():
    deal = _deal(
        current_debt_credit_limit="existing credit card",
        jovairas_vallalasa="igen, 0-499 ezer Ft",
    )
    deal.participants.append(
        Participant(
            role=ParticipantRole.CO_BORROWER,
            name="Teszt Adóstárs",
            mobile_phone="+36123456789",
            current_debt_credit_limit="existing credit card",
            jovairas_vallalasa="igen, 0-499 ezer Ft",
        )
    )
    data = _pipeline()._prepare_field_data(deal, _mapping())

    assert data.get("Check Box 85___Yes") == "igen"
    assert data.get("Check Box 72___Yes") == "igen"
    assert data.get("Check Box 74___Yes") == "igen"
    assert data.get("Check Box 139___Yes") == "igen"
    assert data.get("Check Box 141___Yes") == "igen"
    assert data.get("Check Box 191___Yes") == "igen"


def test_negative_text_does_not_drive_presence_checkboxes():
    for negative_value in (
        "nincs",
        "Nem",
        "0 Ft",
        "-",
        "nincs hitelkártya",
        "nem, nincs",
        "NEM KELL",
        "–",
        "--",
        "...",
        "N/A.",
    ):
        deal = _deal(current_debt_credit_limit=negative_value)
        deal.participants.append(
            Participant(
                role=ParticipantRole.CO_BORROWER,
                name="Teszt Adóstárs",
                mobile_phone=negative_value,
                current_debt_credit_limit=negative_value,
            )
        )
        data = _pipeline()._prepare_field_data(deal, _mapping())

        assert data.get("Check Box 72___Yes") is None
        assert data.get("Check Box 85___Yes") is None
        assert data.get("Check Box 139___Yes") is None

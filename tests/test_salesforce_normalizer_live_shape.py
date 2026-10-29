"""Raw Salesforce-shaped normalization tests with no live org access."""

from __future__ import annotations

from datetime import date
import json
import logging

import pytest

from src.canonical.resolver import CanonicalResolver
from src.integrations.salesforce_client import SalesforceClient
from src.integrations.salesforce_schema import SalesforceSchema
from src.models.canonical_model import PropertyType
from src.normalizer.data_normalizer import DataNormalizer


@pytest.fixture
def live_shape_raw():
    return {
        "Id": "006LIVESHAPE",
        "StageName": "Dokumentáció készítés",
        "AccountId": "001LIVEACCOUNT",
        "_opportunity": {
            "Id": "006LIVESHAPE",
            "StageName": "Dokumentáció készítés",
            "AccountId": "001LIVEACCOUNT",
            "Hitel_sszeg__c": "42000000",
            "Loan_Term__c": "252",
            "Interest_Period__c": "10 év fix",
            "Hitelc_l__c": "Lakásvásárlás",
            "Term_k__c": "OTP Piaci Lakáshitel",
            "Product_Type__c": "piaci_hitel",
            "Down_Payment__c": "12000000",
            "Monthly_Payment__c": "111111",
            "Purchase_Price__c": "54000000",
            "Discount_Action__c": "2026 őszi akció",
            "Refinance_Account__c": "11773009-00112233-00000000",
            "Bank__c": "Példa Jelzálogbank",
            "Approve_in_the_Bank_Application_OTP__c": False,
            "CreatedDate": "2026-10-27T12:34:56.000Z",
            "Description": "Nyers Opportunity megjegyzés",
        },
        "_account": {
            "Id": "001LIVEACCOUNT",
            "Name": "Példa Ügyfél Kft.",
            "AccountNumber": "ACC-2026-001",
            "Phone": "+36 1 555 0101",
            "Fax": "+36 1 555 0102",
            "Industry": "Pénzügy",
            "Website": "https://example.invalid",
            "Description": "Account leírás",
            "BillingStreet": "Fő utca 1.",
            "BillingCity": "Budapest",
            "BillingPostalCode": "1011",
            "BillingState": "Budapest",
            "BillingCountry": "Magyarország",
            "ShippingStreet": "Kossuth utca 2.",
            "ShippingCity": "Szentendre",
            "ShippingPostalCode": "2000",
            "ShippingCountry": "Magyarország",
        },
        "Participants__r": {
            "records": [
                {
                    "role": "adós",
                    "role_index": None,
                    "contact_id": "003LIVEBORROWER",
                    "income_bearing": True,
                    "_incomes": [],
                    "_existing_loans": [],
                    "_contact": {
                        "Id": "003LIVEBORROWER",
                        "Name": "Élő Adós",
                        "FirstName": "Élő",
                        "LastName": "Adós",
                        "Szuletesi_nev__c": "Születési Élő Adós",
                        "Mother_s_Name__c": "Élő Anyja",
                        "Place_of_Birth__c": "Budapest",
                        "Date_of_birth__c": "1985.03.15",
                        "Personal_ID__c": "750315AB",
                        "ID_Card_Number__c": "123456AB",
                        "Address_Card_Number__c": "654321CD",
                        "OtherPostalCode": "1052",
                        "OtherCity": "Budapest",
                        "OtherStreet": "Váci utca 10.",
                        "MailingPostalCode": "2000",
                        "MailingCity": "Szentendre",
                        "MailingStreet": "Duna korzó 3.",
                        "Phone": "+36 30 111 2222",
                        "MobilePhone": "+36 20 333 4444",
                        "Email": "borrower@example.invalid",
                        "Name_of_employer__c": "Példa Munkáltató Kft.",
                        "Employer_Tax_Number__c": "12345678-2-42",
                        "Employer_Company_Form__c": "Kft.",
                        "Credit_Commitment__c": "nincs",
                        "Average_monthly_net_income__c": "650000.75",
                        "Household_Size__c": "4",
                        "Dependents_count__c": "3",
                        "Children_Count__c": "2",
                        "Nem__c": "nő",
                        "AssistantName": "Ügyintéző Példa",
                        "Affordable_monthly_installments__c": "44444.50",
                        "Monthly_Payment_details__c": "Önerős törlesztési részletek",
                        "KHR_Consent__c": True,
                        "NAV_Consent__c": "Nem",
                        "AML_Consent__c": "Igen",
                        "Data_Processing_Consent__c": "Ismeretlen",
                    },
                }
            ]
        },
        "Properties__r": {
            "records": [
                {
                    "_property": {
                        "Id": "a00LIVEPROPERTY",
                        "Name": "Célingatlan",
                        "Property_Type__c": "ház",
                        "Ingatlan_reszletes_tipus__c": "tégla családi ház",
                        "Ingatlan_hrsz__c": "12345/1/A/2",
                        "Ingatlan_alapterulet__c": "125.5",
                        "Property_value__c": "55000000",
                        "Purchase_price__c": "54000000",
                        "Ingatlan_irsz__c": 2030,
                        "Ingatlan_telepules__c": "Érd",
                        "Ingatlan_kozterulet_neve__c": "Kossuth Lajos",
                        "Ingatlan_Kozterulet_jellege__c": "utca",
                        "Ingatlan_hazszam__c": 12,
                        "Ingatlan_emelet__c": "földszint",
                        "Ingatlan_epites_eve__c": "2008",
                        "Ingatlan_szobak_szama__c": "4",
                        "Ingatlan_allapot__c": "jó",
                        "Ingatlan_futes__c": "gázkazán",
                        "Ingatlan_lift__c": False,
                        "Ingatlan_biztositas__c": "lakásbiztosítás",
                        "Ingatlan_energetika__c": "CC",
                        "Ingatlan_terhek__c": "tehermentes",
                        "Ingatlan_szerepe__c": "Célingatlan",
                        "Ingatlan_jellege__c": "lakóház",
                        "Ingatlan_megye__c": "Pest",
                        "Ingatlan_megjegyzes__c": "Nem helyrajzi szám",
                        "Ingatlan_neve__c": "Zöld kertes ház",
                    }
                }
            ]
        },
    }


def test_raw_salesforce_shape_populates_canonical_stores(live_shape_raw):
    deal = DataNormalizer().normalize_deal(live_shape_raw)
    resolve = CanonicalResolver().resolve

    assert deal.deal_id == "006LIVESHAPE"
    assert deal.created_at == date(2026, 10, 27)
    assert resolve(deal, "deal.loan_amount") == 42_000_000
    assert resolve(deal, "deal.loan_term_months") == 252
    assert resolve(deal, "deal.monthly_payment") == 111_111
    assert resolve(deal, "deal.discount_action") == "2026 őszi akció"
    assert resolve(deal, "deal.refinance_account") == "11773009-00112233-00000000"
    assert resolve(deal, "deal.bank_name") == "Példa Jelzálogbank"
    assert resolve(deal, "deal.bank_application_approved") is False
    assert resolve(deal, "deal.notes") == "Nyers Opportunity megjegyzés"

    expected_account = {
        "name": "Példa Ügyfél Kft.",
        "account_number": "ACC-2026-001",
        "phone": "+36 1 555 0101",
        "fax": "+36 1 555 0102",
        "industry": "Pénzügy",
        "website": "https://example.invalid",
        "description": "Account leírás",
        "billing_street": "Fő utca 1.",
        "billing_city": "Budapest",
        "billing_postal_code": "1011",
        "billing_state": "Budapest",
        "billing_country": "Magyarország",
        "shipping_street": "Kossuth utca 2.",
        "shipping_city": "Szentendre",
        "shipping_postal_code": "2000",
        "shipping_country": "Magyarország",
    }
    assert deal.account == expected_account
    for field, value in expected_account.items():
        assert resolve(deal, f"account.{field}") == value

    participant = deal.participants[0]
    assert participant.contact_id == "003LIVEBORROWER"
    assert participant.name == "Élő Adós"
    assert participant.birth_date == date(1985, 3, 15)
    assert participant.monthly_income == 650_000
    assert participant.address.zip_code == "1052"
    assert participant.address.city == "Budapest"
    assert participant.address.street == "Váci utca 10."
    assert participant.mailing_address.zip_code == "2000"
    assert participant.mailing_address.city == "Szentendre"
    assert participant.mailing_address.street == "Duna korzó 3."
    assert resolve(deal, "participant.borrower.personal_number") == "750315AB"
    assert resolve(deal, "participant.borrower.employer_tax_number") == "12345678-2-42"
    assert resolve(deal, "participant.borrower.employer_company_form") == "Kft."
    assert resolve(deal, "participant.borrower.credit_commitment") == "nincs"
    assert resolve(deal, "participant.borrower.household_size") == 4
    assert resolve(deal, "participant.borrower.dependents_count") == 3
    assert resolve(deal, "participant.borrower.children_count") == 2
    assert resolve(deal, "participant.borrower.gender") == "nő"
    assert resolve(deal, "participant.borrower.first_name") == "Élő"
    assert resolve(deal, "participant.borrower.last_name") == "Adós"
    assert resolve(deal, "participant.borrower.assistant_name") == "Ügyintéző Példa"
    assert resolve(deal, "participant.borrower.affordable_monthly_installments") == 44_444.5

    assert participant.consents == {"khr": True, "nav": False, "aml": True}
    assert resolve(deal, "participant.borrower.khr_consent") is True
    assert resolve(deal, "participant.borrower.nav_consent") is False
    assert resolve(deal, "participant.borrower.aml_consent") is True
    assert resolve(deal, "participant.borrower.data_processing_consent") is None

    prop = deal.properties[0]
    assert prop.property_id == "a00LIVEPROPERTY"
    assert prop.property_type == PropertyType.HOUSE
    assert prop.property_type_detail == "tégla családi ház"
    assert prop.parcel_number == "12345/1/A/2"
    assert prop.number_of_rooms == 4
    assert prop.extra["ingatlan_megjegyzes"] == "Nem helyrajzi szám"
    assert prop.extra["ingatlan_neve"] == "Zöld kertes ház"
    assert resolve(deal, "property.address.zip_code") == "2030"
    assert resolve(deal, "property.address.city") == "Érd"
    assert resolve(deal, "property.address.street") == "Kossuth Lajos utca"
    assert resolve(deal, "property.address.house_number") == "12"
    assert resolve(deal, "property.address.floor") == "földszint"
    assert resolve(deal, "property.area_sqm") == 125.5
    assert resolve(deal, "property.estimated_value") == 55_000_000
    assert resolve(deal, "property.year_built") == 2008
    assert resolve(deal, "property.condition") == "jó"
    assert resolve(deal, "property.heating") == "gázkazán"
    assert resolve(deal, "property.lift") is False
    assert resolve(deal, "property.insurance") == "lakásbiztosítás"
    assert resolve(deal, "property.region") == "Pest"
    assert resolve(deal, "property.street_type") == "utca"


def test_missing_loan_term_resolves_blank_and_never_fabricates_a_default(live_shape_raw):
    """Task 5: unknown/missing loan term stays None at every level."""
    from src.models.canonical_model import LoanDetails

    live_shape_raw["_opportunity"].pop("Loan_Term__c", None)
    live_shape_raw.pop("Loan_Term__c", None)

    deal = DataNormalizer().normalize_deal(live_shape_raw)
    resolve = CanonicalResolver().resolve
    resolver = CanonicalResolver()

    assert deal.loan.loan_term_months is None
    assert deal.loan.loan_term_years is None
    assert resolve(deal, "deal.loan_term_months") is None
    assert resolve(deal, "deal.loan_term_years") is None
    assert resolver.resolve_text(deal, "deal.loan_term_months") == ""
    assert resolver.resolve_text(deal, "deal.loan_term_years") == ""
    # Model-level default is no longer a fabricated 20-year term.
    assert LoanDetails().loan_term_months is None
    assert LoanDetails().loan_term_years is None
    # Explicit data still wins.
    live_shape_raw["_opportunity"]["Loan_Term__c"] = "252"
    explicit = DataNormalizer().normalize_deal(live_shape_raw)
    assert explicit.loan.loan_term_months == 252


def test_duplicate_map_targets_use_explicit_deterministic_precedence(live_shape_raw):
    """Task 8: duplicate canonical targets resolve by documented precedence.

    Curated precedence mirrors the adapter's explicit source chains
    (custom field first); every other duplicate target is first-present-wins
    in the deterministic generated-map order instead of last-alphabetical.
    """
    opportunity = live_shape_raw["_opportunity"]
    contact = live_shape_raw["Participants__r"]["records"][0]["_contact"]

    # Curated: the org's own loan-amount field beats the standard Amount.
    opportunity["Amount"] = "1"
    opportunity["Hitel_sszeg__c"] = "42000000"
    # Curated: StageName is the authoritative stage, not the legacy copy.
    opportunity["gylet_st_tusz__c"] = "Régi státusz"
    # Curated: Description is preferred, remark__c only fills the gap.
    opportunity["Description"] = ""
    opportunity["remark__c"] = "Explicit megjegyzés"
    # Default first-present-wins: Personal_ID__c precedes Personal_Number__c.
    contact["Personal_ID__c"] = "750315AB"
    contact["Personal_Number__c"] = "99999999"
    # Explicit months beat a years-derived term (Loan_Term__c claims first).
    opportunity["Term_in_year__c"] = "20"

    deal = DataNormalizer().normalize_deal(live_shape_raw)

    assert deal.loan.loan_amount == 42_000_000
    assert deal.salesforce_stage == "Dokumentáció készítés"
    assert deal.notes == "Explicit megjegyzés"
    assert deal.participants[0].personal_number == "750315AB"
    assert deal.loan.loan_term_months == 252


def test_duplicate_map_targets_fall_back_when_preferred_source_is_absent(
    live_shape_raw,
):
    """Task 8: an absent preferred source lets the equivalent fallback fill."""
    opportunity = live_shape_raw["_opportunity"]
    opportunity.pop("Hitel_sszeg__c")
    opportunity["Amount"] = "42000000"
    opportunity.pop("StageName")
    live_shape_raw.pop("StageName")
    opportunity["gylet_st_tusz__c"] = "Fallback státusz"

    deal = DataNormalizer().normalize_deal(live_shape_raw)

    assert deal.loan.loan_amount == 42_000_000
    assert deal.salesforce_stage == "Fallback státusz"


def test_four_monthly_payment_concepts_stay_independent(live_shape_raw):
    live_shape_raw["Participants__r"]["records"][0]["existing_loans"] = [
        {"loan_type": "mortgage", "monthly_payment": 33_333, "sequence": 1}
    ]

    deal = DataNormalizer().normalize_deal(live_shape_raw)
    participant = deal.participants[0]
    resolve = CanonicalResolver().resolve

    values = {
        "contact_details": resolve(deal, "participant.borrower.monthly_payment_details"),
        "requested_loan": resolve(deal, "deal.monthly_payment"),
        "existing_loan": resolve(
            deal, "participant.borrower.existing_loan[1].monthly_payment"
        ),
        "affordable": resolve(deal, "participant.borrower.affordable_monthly_installments"),
    }
    assert values == {
        "contact_details": "Önerős törlesztési részletek",
        "requested_loan": 111_111,
        "existing_loan": 33_333,
        "affordable": 44_444.5,
    }
    assert participant.monthly_installment_payment is None


@pytest.mark.parametrize(
    "raw_value,expected",
    [
        (True, True),
        (False, False),
        ("Igen", True),
        ("Nem", False),
        ("true", True),
        ("false", False),
        ("yes", True),
        ("no", False),
        ("1", True),
        ("0", False),
        ("Ismeretlen", None),
        ("", None),
        (None, None),
        ("jelenléte nem bizonyít hozzájárulást", None),
    ],
)
def test_consents_require_explicit_boolean_or_picklist_evidence(live_shape_raw, raw_value, expected):
    contact = live_shape_raw["Participants__r"]["records"][0]["_contact"]
    contact["KHR_Consent__c"] = raw_value
    for field in ("NAV_Consent__c", "AML_Consent__c", "Data_Processing_Consent__c"):
        contact.pop(field)

    participant = DataNormalizer().normalize_deal(live_shape_raw).participants[0]

    if expected is None:
        assert "khr" not in participant.consents
    else:
        assert participant.consents == {"khr": expected}
    assert set(participant.consents) <= {"khr"}


def test_populated_string_backed_contact_dates_become_iso_strings(live_shape_raw):
    contact = live_shape_raw["Participants__r"]["records"][0]["_contact"]
    contact.update(
        {
            "Contract_end_date__c": "2027-01-02",
            "Contract_start_date__c": "2025.02.03",
            "Current_employment_started__c": "2024-03-04T12:30:00.000Z",
            "Date_of_handover__c": "2026-05-06",
            "Date_of_signature__c": "2026.06.07",
            "Date_of_transfer__c": "2026-07-08T09:10:11",
            "Date_of_notification_for_residence__c": "2020-09-10",
        }
    )

    deal = DataNormalizer().normalize_deal(live_shape_raw)
    participant = deal.participants[0]

    assert {
        "contract_end_date": participant.contract_end_date,
        "contract_start_date": participant.contract_start_date,
        "current_employment_started": participant.current_employment_started,
        "date_of_handover": participant.date_of_handover,
        "date_of_signature": participant.date_of_signature,
        "date_of_transfer": participant.date_of_transfer,
        "date_of_notification_for_residence": participant.date_of_notification_for_residence,
    } == {
        "contract_end_date": "2027-01-02",
        "contract_start_date": "2025-02-03",
        "current_employment_started": "2024-03-04",
        "date_of_handover": "2026-05-06",
        "date_of_signature": "2026-06-07",
        "date_of_transfer": "2026-07-08",
        "date_of_notification_for_residence": "2020-09-10",
    }
    assert participant.birth_date == date(1985, 3, 15)
    assert deal.created_at == date(2026, 10, 27)


@pytest.mark.parametrize(
    "raw_type",
    ["családi ház", "CSALÁDI HÁZ", "használt lakóház", "HaSzNáLt LaKóHáZ"],
)
def test_property_type_aliases_keep_normalized_enum_and_detail(live_shape_raw, raw_type):
    raw_property = live_shape_raw["Properties__r"]["records"][0]["_property"]
    raw_property["Property_Type__c"] = raw_type
    raw_property["Ingatlan_reszletes_tipus__c"] = "Részletes típus marad"

    prop = DataNormalizer().normalize_deal(live_shape_raw).properties[0]

    assert prop.property_type == PropertyType.HOUSE
    assert prop.property_type_detail == "Részletes típus marad"


class DescribeFakeResource:
    def __init__(self, record):
        self.record = record

    def get(self, record_id):
        return self.record


class DescribeFakeSalesforce:
    def __init__(self, opportunity, contact, prop, account):
        self.Opportunity = DescribeFakeResource(opportunity)
        self.Account = DescribeFakeResource(account)
        self.contact = contact
        self.prop = prop
        self.queries = []

    def query(self, soql):
        self.queries.append(soql)
        if "FROM OpportunityContactRole" in soql:
            return {"records": []}
        if "FROM Contact" in soql and "WHERE Id IN" in soql:
            return {"records": [self.contact]}
        if "FROM Contact" in soql and "WHERE AccountId" in soql:
            return {"records": []}
        if "FROM Opportunity_Property_Role__c" in soql:
            return {"records": []}
        if "FROM Property__c" in soql and "WHERE Account__c" in soql:
            return {"records": [self.prop]}
        raise AssertionError(f"Unexpected fake Salesforce query: {soql}")


def make_schema(contact_fields, property_fields):
    def fields(names):
        return {name: {"type": "string"} for name in names}

    return SalesforceSchema.from_dict(
        {
            "org_id": "00DFAKE",
            "generated_at": "2026-10-28T00:00:00Z",
            "objects": {
                "Contact": {"fields": fields(contact_fields)},
                "Property__c": {"fields": fields(property_fields)},
            },
        }
    )


def selected_fields(soql):
    return soql.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(", ")


def test_live_soql_uses_describe_available_fields_and_aggregates_warnings(tmp_path, caplog):
    contact_fields = ["Id", "Name", "Email", "KHR_Consent__c"]
    property_fields = ["Id", "Name", "Property_Type__c", "Ingatlan_hrsz__c"]
    schema = make_schema(contact_fields, property_fields)
    opportunity = {
        "Id": "006DESCRIBE",
        "AccountId": "001DESCRIBE0000000",
        "Opportunity_Contact_Name__c": "003DESCRIBE0000000",
    }
    contact = {
        "Id": "003DESCRIBE0000000",
        "Name": "Describe Adós",
        "Email": "describe@example.invalid",
        "KHR_Consent__c": False,
    }
    prop = {
        "Id": "a00DESCRIBE",
        "Name": "Describe Ingatlan",
        "Property_Type__c": "lakás",
        "Ingatlan_hrsz__c": "42/1",
    }
    fake = DescribeFakeSalesforce(
        opportunity,
        contact,
        prop,
        {"Id": "001DESCRIBE0000000", "Name": "Describe Account"},
    )
    client = SalesforceClient(
        mock_mode=True,
        mock_data_dir=tmp_path,
        schema=schema,
    )
    client._mock_mode = False
    client._sf = fake

    with caplog.at_level(logging.WARNING):
        raw = client.get_deal(opportunity["Id"])

    contact_queries = [q for q in fake.queries if "FROM Contact" in q]
    property_queries = [q for q in fake.queries if "FROM Property__c" in q]
    assert contact_queries
    assert property_queries
    for query in contact_queries:
        assert selected_fields(query) == contact_fields
        assert "FirstName" not in query
        assert "Birthdate" not in query
    for query in property_queries:
        assert selected_fields(query) == property_fields
        assert "Ingatlan_telepules__c" not in query
        assert "Ingatlan_allapot__c" not in query

    contact_warnings = [r for r in caplog.records if "Contact missing fields" in r.getMessage()]
    property_warnings = [r for r in caplog.records if "Property__c missing fields" in r.getMessage()]
    assert len(contact_warnings) == 1
    assert len(property_warnings) == 1
    assert "FirstName" in contact_warnings[0].getMessage()
    assert "LastName" in contact_warnings[0].getMessage()
    assert "Ingatlan_telepules__c" in property_warnings[0].getMessage()
    assert "Ingatlan_allapot__c" in property_warnings[0].getMessage()

    participant = raw["Participants__r"]["records"][0]
    assert participant["contact_id"] == "003DESCRIBE0000000"
    assert participant["name"] == "Describe Adós"
    assert participant["_contact"] == contact
    assert "FirstName" not in participant["_contact"]
    assert raw["Properties__r"]["records"][0]["_property"] == prop


def test_missing_described_object_skips_invalid_static_query(tmp_path):
    schema = make_schema(["Id", "Name"], [])
    opportunity = {
        "Id": "006NOPROPERTY",
        "Opportunity_Contact_Name__c": "003DESCRIBE0000000",
    }
    contact = {"Id": "003DESCRIBE0000000", "Name": "Describe Adós"}
    prop = {"Id": "a00SHOULDNOTQUERY"}
    fake = DescribeFakeSalesforce(opportunity, contact, prop, {"Id": "001UNUSED"})
    client = SalesforceClient(mock_mode=True, mock_data_dir=tmp_path, schema=schema)
    client._mock_mode = False
    client._sf = fake

    raw = client.get_deal(opportunity["Id"])

    assert not any("FROM Property__c" in query for query in fake.queries)
    assert raw["Properties__r"]["records"] == []


def test_live_schema_none_without_local_snapshot_fails_closed_before_network(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("SF_SCHEMA_PATH", raising=False)
    monkeypatch.setattr(
        "src.integrations.salesforce_client._SF_SCHEMA_DEFAULT_PATH",
        tmp_path / "missing-snapshot.json",
        raising=False,
    )
    network_calls = []

    def forbidden_salesforce(**kwargs):
        network_calls.append(kwargs)
        raise AssertionError("Network boundary must not be reached without a schema")

    monkeypatch.setattr("simple_salesforce.Salesforce", forbidden_salesforce)

    with pytest.raises(RuntimeError, match="sync_sf_schema.py"):
        SalesforceClient(username="fake-user", password="fake-password", mock_mode=False)

    assert network_calls == []


@pytest.mark.parametrize("configured_path", [True, False])
def test_live_schema_none_loads_local_approved_snapshot_and_filters_soql(
    monkeypatch, tmp_path, configured_path
):
    contact_fields = ["Id", "Name", "Email"]
    property_fields = ["Id", "Property_Type__c", "Ingatlan_hrsz__c"]
    schema_document = make_schema(contact_fields, property_fields).to_dict()
    snapshot_path = tmp_path / "approved-snapshot.json"
    snapshot_path.write_text(
        json.dumps(schema_document, ensure_ascii=False), encoding="utf-8"
    )
    if configured_path:
        monkeypatch.setenv("SF_SCHEMA_PATH", str(snapshot_path))
        monkeypatch.setattr(
            "src.integrations.salesforce_client._SF_SCHEMA_DEFAULT_PATH",
            tmp_path / "missing-default.json",
            raising=False,
        )
    else:
        monkeypatch.delenv("SF_SCHEMA_PATH", raising=False)
        monkeypatch.setattr(
            "src.integrations.salesforce_client._SF_SCHEMA_DEFAULT_PATH",
            snapshot_path,
            raising=False,
        )

    opportunity = {
        "Id": "006LOCALSCHEMA",
        "AccountId": "001LOCALSCHEMA0000",
        "Opportunity_Contact_Name__c": "003LOCALSCHEMA0000",
    }
    contact = {
        "Id": "003LOCALSCHEMA0000",
        "Name": "Helyi Séma Adós",
        "Email": "local.schema@example.invalid",
    }
    prop = {
        "Id": "a00LOCALSCHEMA",
        "Property_Type__c": "lakás",
        "Ingatlan_hrsz__c": "42/1",
    }
    fake = DescribeFakeSalesforce(
        opportunity,
        contact,
        prop,
        {"Id": "001LOCALSCHEMA0000", "Name": "Helyi Séma Account"},
    )
    monkeypatch.setattr("simple_salesforce.Salesforce", lambda **kwargs: fake)

    client = SalesforceClient(
        username="fake-user", password="fake-password", mock_mode=False
    )
    raw = client.get_deal(opportunity["Id"])

    assert client._schema is not None
    assert client._schema.org_id == "00DFAKE"
    assert raw["Participants__r"]["records"][0]["contact_id"] == "003LOCALSCHEMA0000"
    for query in fake.queries:
        if "FROM Contact" in query:
            assert selected_fields(query) == contact_fields
            assert "FirstName" not in query
        if "FROM Property__c" in query:
            assert selected_fields(query) == property_fields
            assert "Ingatlan_telepules__c" not in query


def test_mock_mode_does_not_require_a_schema_snapshot(monkeypatch, tmp_path):
    monkeypatch.delenv("SF_SCHEMA_PATH", raising=False)
    monkeypatch.setattr(
        "src.integrations.salesforce_client._SF_SCHEMA_DEFAULT_PATH",
        tmp_path / "missing-snapshot.json",
        raising=False,
    )

    client = SalesforceClient(mock_mode=True, mock_data_dir=tmp_path, schema=None)

    assert client._schema is None

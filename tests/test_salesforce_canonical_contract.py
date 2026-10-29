"""End-to-end fake Salesforce adapter to canonical resolver contract."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
from pathlib import Path

import pytest

from src.canonical.registry import canonical_registry
from src.canonical.resolver import CanonicalResolver
from src.integrations.salesforce_client import SalesforceClient
from src.models.canonical_model import ParticipantRole
from src.normalizer.data_normalizer import DataNormalizer


ROOT = Path(__file__).resolve().parents[1]
DEAL_ID = "006000000000000001"
ACCOUNT_ID = "001000000000000001"
PROPERTY_ID = "a00000000000000001"
BORROWER_ID = "003000000000000001"
CO_BORROWER_ID = "003000000000000002"
GUARANTOR_ID = "003000000000000003"
MORTGAGOR_ID = "003000000000000004"
USUFRUCTUARY_ID = "003000000000000005"


class FakeResource:
    def __init__(self, record):
        self.record = record

    def get(self, record_id):
        assert record_id == self.record["Id"]
        return deepcopy(self.record)


class FakeContractSalesforce:
    """Read-only raw Salesforce package with no production fallback data."""

    def __init__(self):
        self.Opportunity = FakeResource(
            {
                "Id": DEAL_ID,
                "AccountId": ACCOUNT_ID,
                "Name": "Canonical contract deal",
                "StageName": "Dokumentáció készítés",
                "Opportunity_Contact_Name__c": BORROWER_ID,
                "First_Co_debtor__c": CO_BORROWER_ID,
                "Guarantor__c": GUARANTOR_ID,
                "Mortgagor__c": MORTGAGOR_ID,
                "Usufructuary__c": USUFRUCTUARY_ID,
                "Hitel_sszeg__c": "44000000",
                "Loan_Term__c": "300",
                "Monthly_Payment__c": "111111",
                "Bank__c": "Példa Jelzálogbank",
                "CreatedDate": "2026-10-27T12:34:56.000Z",
                "Description": "End-to-end adapter contract",
            }
        )
        self.Account = FakeResource(
            {
                "Id": ACCOUNT_ID,
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
            }
        )
        self.contacts = [
            {
                "Id": BORROWER_ID,
                "Name": "Adós Példa",
                "FirstName": "Adós",
                "LastName": "Példa",
                "Monthly_Payment_details__c": "Külön törlesztési megjegyzés",
                "Affordable_monthly_installments__c": "44444.50",
                "KHR_Consent__c": True,
                "NAV_Consent__c": False,
                "AML_Consent__c": "Ismeretlen",
                "Data_Processing_Consent__c": "a mező jelenléte nem bizonyíték",
            },
            *[
                {
                    "Id": contact_id,
                    "Name": name,
                    "FirstName": name.split(" ", 1)[0],
                    "LastName": name.split(" ", 1)[1],
                }
                for contact_id, name in (
                    (CO_BORROWER_ID, "Társ Példa"),
                    (GUARANTOR_ID, "Kezes Példa"),
                    (MORTGAGOR_ID, "Zálog Példa"),
                    (USUFRUCTUARY_ID, "Haszonélvező Példa"),
                )
            ],
        ]
        self.incomes = [
            {
                "Id": "aINC00000000000001",
                "CreatedDate": "2026-01-01T00:00:00.000Z",
                "Contact__c": BORROWER_ID,
                "Sorszam__c": 1,
                "Jovedelem_tipusa__c": "munkabér",
                "Osszeg__c": "650000.75",
                "Penznem__c": "HUF",
                "Erkezes_helye__c": "bankszámla",
                "Bank__c": "Jövedelem Bank",
                "Szamlaszam__c": "INCOME-ACCOUNT-1",
            }
        ]
        self.loans = [
            {
                "Id": "aLOAN0000000000001",
                "CreatedDate": "2026-02-01T00:00:00.000Z",
                "Contact__c": BORROWER_ID,
                "Sorszam__c": 1,
                "Hiteltipus__c": "jelzáloghitel",
                "Eredeti_osszeg__c": "20000000",
                "Fennallo_tartozas__c": "15000000",
                "Havi_torleszto__c": "33333.25",
                "Penznem__c": "HUF",
                "Kivaltando__c": False,
                "Bank__c": "Korábbi Bank",
                "Szamlaszam__c": "LOAN-ACCOUNT-1",
            }
        ]
        self.property = {
            "Id": PROPERTY_ID,
            "Name": "Célingatlan",
            "Property_Type__c": "családi ház",
            "Ingatlan_reszletes_tipus__c": "tégla családi ház",
            "Ingatlan_hrsz__c": "12345/1/A/2",
            "Ingatlan_alapterulet__c": "125.5",
            "Property_value__c": "55000000",
            "Ingatlan_irsz__c": 2030,
            "Ingatlan_telepules__c": "Érd",
            "Ingatlan_kozterulet_neve__c": "Kossuth Lajos",
            "Ingatlan_Kozterulet_jellege__c": "utca",
            "Ingatlan_hazszam__c": 12,
            "Ingatlan_epites_eve__c": "2008",
            "Ingatlan_szobak_szama__c": "4",
            "Ingatlan_allapot__c": "jó",
            "Ingatlan_futes__c": "gázkazán",
            "Ingatlan_lift__c": False,
            "Ingatlan_biztositas__c": "lakásbiztosítás",
            "Ingatlan_szerepe__c": "Célingatlan",
        }
        self.queries: list[str] = []

    def query(self, soql):
        self.queries.append(soql)
        if "FROM OpportunityContactRole" in soql:
            return {"records": []}
        if "FROM Contact" in soql and "WHERE Id IN" in soql:
            return {"records": deepcopy(self.contacts)}
        if "FROM Contact" in soql and "WHERE AccountId" in soql:
            return {"records": []}
        if "FROM Jovedelem__c" in soql:
            return {"records": deepcopy(self.incomes)}
        if "FROM Meglevo_hitel__c" in soql:
            return {"records": deepcopy(self.loans)}
        if "FROM Opportunity_Property_Role__c" in soql:
            return {
                "records": [
                    {
                        "Property__c": PROPERTY_ID,
                        "Ingatlan_szerepe__c": "Célingatlan",
                    }
                ]
            }
        if "FROM Property__c" in soql and "WHERE Id IN" in soql:
            return {"records": [deepcopy(self.property)]}
        raise AssertionError(f"Unexpected fake Salesforce query: {soql}")


class ContractSchema:
    """Describe boundary that can withhold complete related-object capabilities."""

    def __init__(self, missing_objects=()):
        self.missing_objects = set(missing_objects)

    def has_object(self, object_name):
        return object_name not in self.missing_objects

    def available_fields(self, object_name, candidates):
        if object_name in self.missing_objects:
            return []
        return list(candidates)


def normalize_fake_package(*, missing_objects=()):
    fake = FakeContractSalesforce()
    client = SalesforceClient.__new__(SalesforceClient)
    client._mock_mode = False
    client._sf = fake
    client._schema = ContractSchema(missing_objects)
    raw = client.get_deal(DEAL_ID)
    assert raw is not None
    deal = DataNormalizer().normalize_deal(raw)
    return raw, fake, deal


def test_fake_salesforce_contract_resolves_non_null_sentinels_for_every_namespace():
    _, _, deal = normalize_fake_package()
    resolve = CanonicalResolver().resolve
    expected = {
        "deal.notes": "End-to-end adapter contract",
        "deal.loan_amount": 44_000_000,
        "deal.bank_name": "Példa Jelzálogbank",
        "account.name": "Példa Ügyfél Kft.",
        "participant.borrower.name": "Adós Példa",
        "participant.co_borrower[1].name": "Társ Példa",
        "participant.guarantor[1].name": "Kezes Példa",
        "participant.mortgagor[1].name": "Zálog Példa",
        "participant.usufructuary[1].name": "Haszonélvező Példa",
        "participant.borrower.affordable_monthly_installments": 44_444.5,
        "participant.borrower.khr_consent": True,
        "property.parcel_number": "12345/1/A/2",
        "participant.borrower.income[1].amount": 650_000.75,
        "participant.borrower.existing_loan[1].monthly_payment": 33_333.25,
    }

    resolved = {path: resolve(deal, path) for path in expected}

    assert all(value is not None for value in resolved.values()), resolved
    assert resolved == expected
    assert {
        canonical_registry.get(path).source.partition(".")[0] for path in resolved
    } == {
        "participant",
        "property",
        "loan",
        "deal",
        "income",
        "existing_loan",
        "account",
        "extra",
        "consents",
    }


def test_fake_salesforce_contract_preserves_exact_company_account_fields():
    _, _, deal = normalize_fake_package()
    expected = {
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

    assert deal.account == expected
    resolve = CanonicalResolver().resolve
    assert {field: resolve(deal, f"account.{field}") for field in expected} == expected


def test_fake_salesforce_contract_separates_roles_payments_and_consent_evidence():
    _, _, deal = normalize_fake_package()
    resolve = CanonicalResolver().resolve

    assert [participant.role for participant in deal.participants] == [
        ParticipantRole.BORROWER,
        ParticipantRole.CO_BORROWER,
        ParticipantRole.GUARANTOR,
        ParticipantRole.MORTGAGOR,
        ParticipantRole.BENEFICIARY,
    ]
    assert deal.guarantors[0].contact_id == GUARANTOR_ID
    assert deal.get_participants_by_role(ParticipantRole.MORTGAGOR)[0].contact_id == MORTGAGOR_ID
    assert deal.guarantors[0].contact_id != MORTGAGOR_ID

    payments = {
        "contact_details": resolve(deal, "participant.borrower.monthly_payment_details"),
        "requested_loan": resolve(deal, "deal.monthly_payment"),
        "existing_loan": resolve(
            deal, "participant.borrower.existing_loan[1].monthly_payment"
        ),
        "affordable": resolve(deal, "participant.borrower.affordable_monthly_installments"),
    }
    assert payments == {
        "contact_details": "Külön törlesztési megjegyzés",
        "requested_loan": 111_111,
        "existing_loan": 33_333.25,
        "affordable": 44_444.5,
    }
    assert deal.participants[0].monthly_installment_payment is None

    borrower = deal.participants[0]
    assert borrower.consents == {"khr": True, "nav": False}
    assert resolve(deal, "participant.borrower.khr_consent") is True
    assert resolve(deal, "participant.borrower.nav_consent") is False
    assert resolve(deal, "participant.borrower.aml_consent") is None
    assert resolve(deal, "participant.borrower.data_processing_consent") is None
    assert all(participant.consents == {} for participant in deal.participants[1:])


def test_missing_related_capabilities_warn_actionably_without_fabrication(caplog):
    missing = {"Jovedelem__c", "Meglevo_hitel__c"}
    with caplog.at_level(logging.WARNING):
        raw, fake, deal = normalize_fake_package(missing_objects=missing)

    for object_name in missing:
        warnings = [
            record.getMessage()
            for record in caplog.records
            if object_name in record.getMessage()
            and "scripts/sync_sf_schema.py" in record.getMessage()
            and "describe-only" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert not any(f"FROM {object_name}" in query for query in fake.queries)
    assert all(
        participant["_incomes"] == [] and participant["_existing_loans"] == []
        for participant in raw["Participants__r"]["records"]
    )
    assert all(
        participant.incomes == [] and participant.existing_loans == []
        for participant in deal.participants
    )
    resolve = CanonicalResolver().resolve
    assert resolve(deal, "participant.borrower.income[1].amount") is None
    assert resolve(deal, "participant.borrower.existing_loan[1].monthly_payment") is None
    assert resolve(deal, "account.name") == "Példa Ügyfél Kft."
    assert resolve(deal, "property.parcel_number") == "12345/1/A/2"


def test_legacy_mock_json_deserializes_without_runtime_legacy_aliases(monkeypatch):
    def forbidden_legacy_route(*args, **kwargs):
        raise AssertionError("Legacy mock JSON must not enter a Salesforce alias route")

    monkeypatch.setattr(DataNormalizer, "_normalize_salesforce", forbidden_legacy_route)
    monkeypatch.setattr(DataNormalizer, "_apply_field_map", forbidden_legacy_route)
    resolve = CanonicalResolver().resolve

    for path in sorted((ROOT / "samples" / "dummy_data").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        deal = DataNormalizer().normalize_deal(raw)
        assert deal.deal_id == raw["deal_id"]
        assert resolve(deal, "deal.loan_amount") == raw["loan"]["loan_amount"]

    assert all(
        not field["source"].startswith(("Contact.", "Lead.", "Opportunity."))
        for field in canonical_registry.canonical_fields()
    )

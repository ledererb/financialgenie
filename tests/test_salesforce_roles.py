"""Offline fake-Salesforce tests for ordered participant role resolution."""

from __future__ import annotations

import re

import pytest

from src.integrations.salesforce_client import SalesforceClient
from src.normalizer.data_normalizer import DataNormalizer
from src.models.canonical_model import ParticipantRole


class FakeResource:
    def __init__(self, record=None):
        self.record = record or {}

    def get(self, record_id):
        assert record_id == self.record.get("Id")
        return self.record


class FakeSalesforce:
    """Small read-only fake keyed by the SOQL object and record IDs."""

    def __init__(self, opportunity, contacts, ocr_records=(), account=None):
        self.Opportunity = FakeResource(opportunity)
        self.Account = FakeResource(account or {"Id": opportunity.get("AccountId")})
        self.contacts = {record["Id"]: dict(record) for record in contacts}
        self.ocr_records = list(ocr_records)
        self.account = account
        self.queries: list[str] = []

    def query(self, soql):
        self.queries.append(soql)
        if "FROM OpportunityContactRole" in soql:
            return {"records": list(self.ocr_records)}
        if "FROM Contact" in soql and "WHERE Id IN" in soql:
            ids = re.findall(r"'([A-Za-z0-9]+)'", soql.split("WHERE Id IN", 1)[1])
            return {
                "records": [self.contacts[contact_id] for contact_id in ids if contact_id in self.contacts]
            }
        if "FROM Contact" in soql and "WHERE AccountId" in soql:
            return {"records": []}
        if "FROM Opportunity_Property_Role__c" in soql:
            return {"records": []}
        if "FROM Property__c" in soql:
            return {"records": []}
        raise AssertionError(f"Unexpected fake Salesforce query: {soql}")


class AllowAllSchema:
    """Role-focused fake: static field filtering is covered separately."""

    def available_fields(self, object_name, candidates):
        return list(candidates)


def make_client(fake):
    client = SalesforceClient.__new__(SalesforceClient)
    client._mock_mode = False
    client._sf = fake
    client._schema = AllowAllSchema()
    return client


def contact(contact_id, name):
    first, last = name.split(" ", 1)
    return {"Id": contact_id, "FirstName": first, "LastName": last}


def test_lookup_only_roles_keep_mortgagor_separate_from_guarantor():
    opportunity = {
        "Id": "006LOOKUP",
        "Opportunity_Contact_Name__c": "003BORROWER0000000",
        "First_Co_debtor__c": "003CO1000000000000",
        "Second_Co_debtor__c": "003CO2",
        "Third_Co_debtor__c": "003CO3",
        "Mortgagor__c": "003MORTGAGOR",
        "Usufructuary__c": "003USUFRUCT",
    }
    fake = FakeSalesforce(opportunity, [])

    roles, flags = make_client(fake)._resolve_participant_roles(opportunity, opportunity["Id"])

    assert roles == {
        "adós": "003BORROWER0000000",
        "adóstárs_1": "003CO1000000000000",
        "adóstárs_2": "003CO2",
        "adóstárs_3": "003CO3",
        "zálogkötelezett_1": "003MORTGAGOR",
        "haszonélvező_1": "003USUFRUCT",
    }
    assert flags == {}


def test_ocr_only_roles_retain_multiple_instances_and_income_flags():
    opportunity = {"Id": "006OCR"}
    ocr = [
        {"ContactId": "003BORROWER0000000", "Role": "Adós-jovedelemmel"},
        {"ContactId": "003CO2", "Role": "Adóstárs 2 - jövedelem nélkül bevont"},
        {"ContactId": "003CO1000000000000", "Role": "Adóstárs1-jövedelemmel"},
        {"ContactId": "003GUAR1", "Role": "Kezes 1"},
        {"ContactId": "003GUAR2", "Role": "Kezes 2"},
        {"ContactId": "003MORT1", "Role": "Zálogkötelezett 1"},
        {"ContactId": "003MORT2", "Role": "Zálogkötelezett 2"},
        {"ContactId": "003BENEF1", "Role": "Haszonélvező 1"},
        {"ContactId": "003BENEF2", "Role": "Haszonélvező 2"},
    ]
    fake = FakeSalesforce(opportunity, [], ocr)

    roles, flags = make_client(fake)._resolve_participant_roles(opportunity, opportunity["Id"])

    assert roles == {
        "adós": "003BORROWER0000000",
        "adóstárs_1": "003CO1000000000000",
        "adóstárs_2": "003CO2",
        "kezes_1": "003GUAR1",
        "kezes_2": "003GUAR2",
        "zálogkötelezett_1": "003MORT1",
        "zálogkötelezett_2": "003MORT2",
        "haszonélvező_1": "003BENEF1",
        "haszonélvező_2": "003BENEF2",
    }
    assert flags == {
        "adós": True,
        "adóstárs_2": False,
        "adóstárs_1": True,
    }


def test_mixed_merge_does_not_hide_lookup_co_borrowers_or_repeat_borrower():
    opportunity = {
        "Id": "006MIXED",
        "Opportunity_Contact_Name__c": "003BORROWER0000000",
        "First_Co_debtor__c": "003LOOKUP1",
        "Second_Co_debtor__c": "003BORROWER0000000",
        "Third_Co_debtor__c": "003LOOKUP3",
    }
    ocr = [
        {"ContactId": "003BORROWER0000000", "Role": "Adós-jovedelemmel"},
        {"ContactId": "003BORROWER0000000", "Role": "Adóstárs 1-jovedelemmel"},
        {"ContactId": "003OCR1", "Role": "Adóstárs 1-jövedelemmel"},
        {"ContactId": "003LOOKUP1", "Role": "Adóstárs 2-jovedelemmel"},
        {"ContactId": "003LOOKUP1", "Role": "Adóstárs 3-jovedelemmel"},
    ]
    fake = FakeSalesforce(opportunity, [], ocr)

    roles, flags = make_client(fake)._resolve_participant_roles(opportunity, opportunity["Id"])

    assert roles == {
        "adós": "003BORROWER0000000",
        "adóstárs_1": "003LOOKUP1",
        "adóstárs_2": "003LOOKUP3",
        "adóstárs_3": "003OCR1",
    }
    assert list(roles.values()).count("003BORROWER0000000") == 1
    assert list(roles.values()).count("003LOOKUP1") == 1
    assert flags["adóstárs_1"] is True
    assert flags["adóstárs_3"] is True


def test_same_contact_can_hold_two_distinct_non_borrower_roles():
    opportunity = {
        "Id": "006DUALROLE",
        "Opportunity_Contact_Name__c": "003BORROWER0000000",
        "Mortgagor__c": "003SHARED",
    }
    ocr = [
        {"ContactId": "003SHARED", "Role": "Kezes 1"},
        {"ContactId": "003SHARED", "Role": "Kezes 2"},
        {"ContactId": "003SHARED", "Role": "Zálogkötelezett 2"},
    ]
    fake = FakeSalesforce(opportunity, [], ocr)

    roles, _ = make_client(fake)._resolve_participant_roles(opportunity, opportunity["Id"])

    assert roles == {
        "adós": "003BORROWER0000000",
        "kezes_1": "003SHARED",
        "zálogkötelezett_1": "003SHARED",
    }


@pytest.mark.parametrize(
    "opportunity,ocr,expected_names",
    [
        (
            {
                "Id": "006RAWLOOKUP",
                "Opportunity_Contact_Name__c": "003BORROWER0000000",
                "First_Co_debtor__c": "003CO1000000000000",
                "Mortgagor__c": "003MORT00000000000",
            },
            [],
            {"003BORROWER0000000", "003CO1000000000000", "003MORT00000000000"},
        ),
        (
            {"Id": "006RAWOCR"},
            [
                {"ContactId": "003BORROWER0000000", "Role": "Adós"},
                {"ContactId": "003CO1000000000000", "Role": "Adóstárs 1"},
                {"ContactId": "003MORT00000000000", "Role": "Zálogkötelezett 1"},
            ],
            {"003BORROWER0000000", "003CO1000000000000", "003MORT00000000000"},
        ),
    ],
)
def test_raw_participants_have_ids_explicit_indices_and_real_names(opportunity, ocr, expected_names):
    contacts = [
        contact("003BORROWER0000000", "Adós Példa"),
        contact("003CO1000000000000", "Társ Példa"),
        contact("003MORT00000000000", "Zálog Példa"),
    ]
    fake = FakeSalesforce(opportunity, contacts, ocr)

    raw = make_client(fake).get_deal(opportunity["Id"])
    participants = raw["Participants__r"]["records"]

    assert {p["contact_id"] for p in participants} == expected_names
    assert all(p["name"] and p["name"] not in expected_names for p in participants)
    assert all(p["_incomes"] == [] and p["_existing_loans"] == [] for p in participants)
    assert [p["role_index"] for p in participants] == [None, 1, 1]
    assert all(p["_contact"]["Id"] == p["contact_id"] for p in participants)

    deal = DataNormalizer().normalize_deal(raw)
    assert [p.contact_id for p in deal.participants] == [
        "003BORROWER0000000",
        "003CO1000000000000",
        "003MORT00000000000",
    ]
    assert deal.participants[-1].role == ParticipantRole.MORTGAGOR

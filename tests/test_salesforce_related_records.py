"""Offline related-record, ownership, normalization, and Property tests."""

from __future__ import annotations

from copy import deepcopy
import logging

import pytest

from src.canonical.resolver import CanonicalResolver
from src.integrations.salesforce_client import SalesforceClient
from src.integrations.salesforce_schema import SalesforceSchema, load_field_map
from src.models.canonical_model import PropertyType
from src.normalizer.data_normalizer import DataNormalizer


BORROWER_ID = "003RELATEDBORROWER"
CO_BORROWER_ID = "003RELATEDCO000000"
ACCOUNT_ID = "001RELATEDACCOUNT0"
FIELD_MAP = load_field_map()


def required_related_fields(object_name):
    return ["Id", "CreatedDate", *FIELD_MAP[object_name]]


def make_schema(
    *,
    include_income=True,
    include_loan=True,
    income_fields=None,
    loan_fields=None,
    property_fields=(),
    contact_fields=("Id", "Name", "FirstName", "LastName", "Email"),
):
    def fields(names):
        return {name: {"type": "string"} for name in names}

    objects = {
        "Contact": {"fields": fields(contact_fields)},
        "Property__c": {"fields": fields(property_fields)},
    }
    if include_income:
        objects["Jovedelem__c"] = {
            "fields": fields(
                required_related_fields("Jovedelem__c")
                if income_fields is None
                else income_fields
            )
        }
    if include_loan:
        objects["Meglevo_hitel__c"] = {
            "fields": fields(
                required_related_fields("Meglevo_hitel__c")
                if loan_fields is None
                else loan_fields
            )
        }
    return SalesforceSchema.from_dict(
        {
            "org_id": "00DFAKE",
            "generated_at": "2026-10-28T00:00:00Z",
            "objects": objects,
        }
    )


class FakeResource:
    def __init__(self, record):
        self.record = record

    def get(self, record_id):
        assert record_id == self.record.get("Id")
        return self.record


class FakeRelatedSalesforce:
    """Read-only fake returning deliberately interleaved related rows."""

    def __init__(
        self,
        opportunity,
        contacts,
        *,
        incomes=(),
        loans=(),
        properties=(),
        pool=(),
        account=None,
    ):
        self.Opportunity = FakeResource(opportunity)
        self.Account = FakeResource(
            account or {"Id": opportunity.get("AccountId"), "Name": "Related Account"}
        )
        self.contacts = list(contacts)
        self.incomes = list(incomes)
        self.loans = list(loans)
        self.properties = list(properties)
        self.pool = list(pool)
        self.queries = []

    def query(self, soql):
        self.queries.append(soql)
        if "FROM OpportunityContactRole" in soql:
            return {"records": []}
        if "FROM Contact" in soql and "WHERE Id IN" in soql:
            return {"records": deepcopy(self.contacts)}
        if "FROM Contact" in soql and "WHERE AccountId" in soql:
            return {"records": deepcopy(self.pool)}
        if "FROM Jovedelem__c" in soql:
            return {"records": deepcopy(self.incomes)}
        if "FROM Meglevo_hitel__c" in soql:
            return {"records": deepcopy(self.loans)}
        if "FROM Opportunity_Property_Role__c" in soql:
            return {"records": []}
        if "FROM Property__c" in soql:
            return {"records": deepcopy(self.properties)}
        raise AssertionError(f"Unexpected fake Salesforce query: {soql}")


def make_client(fake, schema=None):
    client = SalesforceClient.__new__(SalesforceClient)
    client._mock_mode = False
    client._sf = fake
    client._schema = schema if schema is not None else make_schema()
    return client


def contact(contact_id, name):
    first, last = name.split(" ", 1)
    return {
        "Id": contact_id,
        "Name": name,
        "FirstName": first,
        "LastName": last,
    }


def opportunity(**overrides):
    value = {
        "Id": "006RELATED",
        "AccountId": ACCOUNT_ID,
        "Name": "Related record deal",
        "Opportunity_Contact_Name__c": BORROWER_ID,
        "First_Co_debtor__c": CO_BORROWER_ID,
    }
    value.update(overrides)
    return value


def income_row(contact_id, sequence, **overrides):
    row = {
        "Id": f"aINC{contact_id[-2:]}{sequence:02d}",
        "CreatedDate": f"2026-01-{sequence:02d}T00:00:00.000Z",
        "Contact__c": contact_id,
        "Sorszam__c": sequence,
        "Jovedelem_tipusa__c": "munkabér",
        "Osszeg__c": str(sequence * 100_000),
        "Penznem__c": "HUF",
        "Erkezes_helye__c": "bankszámla",
        "Bank__c": "Példa Bank",
        "Szamlaszam__c": f"ACC-{sequence:02d}",
    }
    row.update(overrides)
    return row


def loan_row(record_id, contact_id, sequence, created, **overrides):
    row = {
        "Id": record_id,
        "CreatedDate": created,
        "Contact__c": contact_id,
        "Sorszam__c": sequence,
        "Hiteltipus__c": "jelzáloghitel",
        "Eredeti_osszeg__c": "20000000",
        "Fennallo_tartozas__c": "15000000",
        "Havi_torleszto__c": "125000.50",
        "Penznem__c": "HUF",
        "Kivaltando__c": False,
        "Bank__c": "Korábbi Bank",
        "Szamlaszam__c": "LOAN-ACCOUNT-1",
    }
    row.update(overrides)
    return row


def related_fixture():
    incomes = []
    for index, sequence in enumerate(range(12, 0, -1)):
        overrides = {}
        if sequence == 1:
            overrides = {
                "Id": "aINCFULL",
                "Osszeg__c": "100000.75",
                "Penznem__c": "EUR",
                "Jovedelem_tipusa__c": "cafeteria",
                "Erkezes_helye__c": "kézbesítés",
                "Bank__c": "Teljes Bank",
                "Szamlaszam__c": "FULL-ACCOUNT",
            }
        elif sequence == 12:
            overrides = {"Osszeg__c": None, "Penznem__c": None}
        incomes.append(income_row(BORROWER_ID, sequence, **overrides))
        if index < 2:
            incomes.append(
                income_row(
                    CO_BORROWER_ID,
                    index + 1,
                    Id=f"aINCCO{index + 1}",
                    Bank__c="Társ Bank",
                )
            )

    loans = [
        loan_row("aLOAN002", BORROWER_ID, 2, "2026-02-02T00:00:00.000Z"),
        loan_row(
            "aLOANCO1",
            CO_BORROWER_ID,
            1,
            "2026-02-01T00:00:00.000Z",
            Bank__c="Társ Hitel Bank",
            Kivaltando__c=True,
        ),
        loan_row(
            "aLOAN001B",
            BORROWER_ID,
            1,
            "2026-01-02T00:00:00.000Z",
            Kivaltando__c=True,
        ),
        loan_row(
            "aLOAN001A",
            BORROWER_ID,
            1,
            "2026-01-01T00:00:00.000Z",
            Fennallo_tartozas__c=None,
            Havi_torleszto__c="0",
        ),
    ]
    fake = FakeRelatedSalesforce(
        opportunity(),
        [contact(BORROWER_ID, "Adós Példa"), contact(CO_BORROWER_ID, "Társ Példa")],
        incomes=incomes,
        loans=loans,
    )
    raw = make_client(fake).get_deal("006RELATED")
    return raw, fake


def selected_fields(soql):
    return soql.split("SELECT ", 1)[1].split(" FROM ", 1)[0].split(", ")


def test_related_queries_batch_contact_ids_and_use_approved_describe_fields():
    _, fake = related_fixture()

    for object_name in ("Jovedelem__c", "Meglevo_hitel__c"):
        queries = [query for query in fake.queries if f"FROM {object_name}" in query]
        assert len(queries) == 1
        query = queries[0]
        assert selected_fields(query) == required_related_fields(object_name)
        assert f"'{BORROWER_ID}', '{CO_BORROWER_ID}'" in query
        assert "WHERE Contact__c IN" in query
        assert query.endswith("ORDER BY Sorszam__c, CreatedDate, Id")


def test_related_rows_have_contact_ownership_stable_order_and_all_raw_fields():
    raw, _ = related_fixture()
    borrower, co_borrower = raw["Participants__r"]["records"]

    assert len(borrower["_incomes"]) == 12
    assert [row["Sorszam__c"] for row in borrower["_incomes"]] == list(range(1, 13))
    assert {row["Contact__c"] for row in borrower["_incomes"]} == {BORROWER_ID}
    assert len(co_borrower["_incomes"]) == 2
    assert {row["Contact__c"] for row in co_borrower["_incomes"]} == {CO_BORROWER_ID}

    full_income = borrower["_incomes"][0]
    assert full_income == {
        "Id": "aINCFULL",
        "CreatedDate": "2026-01-01T00:00:00.000Z",
        "Contact__c": BORROWER_ID,
        "Sorszam__c": 1,
        "Jovedelem_tipusa__c": "cafeteria",
        "Osszeg__c": "100000.75",
        "Penznem__c": "EUR",
        "Erkezes_helye__c": "kézbesítés",
        "Bank__c": "Teljes Bank",
        "Szamlaszam__c": "FULL-ACCOUNT",
    }
    assert borrower["_incomes"][-1]["Osszeg__c"] is None
    assert borrower["_incomes"][-1]["Penznem__c"] is None

    assert [row["Id"] for row in borrower["_existing_loans"]] == [
        "aLOAN001A",
        "aLOAN001B",
        "aLOAN002",
    ]
    assert all(row["Contact__c"] == BORROWER_ID for row in borrower["_existing_loans"])
    assert borrower["_existing_loans"][0]["Kivaltando__c"] is False
    assert borrower["_existing_loans"][0]["Fennallo_tartozas__c"] is None
    assert [row["Id"] for row in co_borrower["_existing_loans"]] == ["aLOANCO1"]


def test_normalizer_and_resolver_preserve_related_values_and_row_ownership():
    raw, _ = related_fixture()
    deal = DataNormalizer().normalize_deal(raw)
    borrower, co_borrower = deal.participants
    resolve = CanonicalResolver().resolve

    assert len(borrower.incomes) == 12
    assert borrower.incomes[0].model_dump() == {
        "income_type": "cafeteria",
        "amount": 100000.75,
        "currency": "EUR",
        "destination": "kézbesítés",
        "bank_name": "Teljes Bank",
        "account_number": "FULL-ACCOUNT",
        "sequence": 1,
    }
    assert borrower.incomes[-1].amount is None
    assert borrower.incomes[-1].currency is None
    assert borrower.existing_loans[0].refinance is False
    assert borrower.existing_loans[0].outstanding_amount is None
    assert borrower.existing_loans[0].monthly_payment == 0
    assert borrower.existing_loans[0].currency == "HUF"

    assert resolve(deal, "participant.borrower.income[1].amount") == 100000.75
    assert resolve(deal, "participant.borrower.income[12].amount") is None
    assert resolve(deal, "participant.borrower.income[12].currency") is None
    assert resolve(deal, "participant.borrower.existing_loan[1].refinance") is False
    assert resolve(deal, "participant.co_borrower[1].income[1].bank_name") == "Társ Bank"
    assert resolve(
        deal, "participant.co_borrower[1].existing_loan[1].bank_name"
    ) == "Társ Hitel Bank"
    assert len(co_borrower.incomes) == 2


@pytest.mark.parametrize("object_name", ["Jovedelem__c", "Meglevo_hitel__c"])
def test_missing_related_object_warns_once_and_never_queries_or_fabricates(
    object_name, caplog
):
    schema = make_schema(**{f"include_{'income' if object_name == 'Jovedelem__c' else 'loan'}": False})
    fake = FakeRelatedSalesforce(
        opportunity(),
        [contact(BORROWER_ID, "Adós Példa"), contact(CO_BORROWER_ID, "Társ Példa")],
        incomes=[income_row(BORROWER_ID, 1)],
        loans=[loan_row("aLOAN", BORROWER_ID, 1, "2026-01-01")],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake, schema).get_deal("006RELATED")

    warnings = [
        record.getMessage()
        for record in caplog.records
        if object_name in record.getMessage() and "sync_sf_schema.py" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert not any(f"FROM {object_name}" in query for query in fake.queries)
    output_key = "_incomes" if object_name == "Jovedelem__c" else "_existing_loans"
    assert all(
        participant[output_key] == []
        for participant in raw["Participants__r"]["records"]
    )


@pytest.mark.parametrize(
    "object_name,missing_field",
    [
        ("Jovedelem__c", "Contact__c"),
        ("Jovedelem__c", "Bank__c"),
        ("Meglevo_hitel__c", "Contact__c"),
        ("Meglevo_hitel__c", "Kivaltando__c"),
    ],
)
def test_missing_related_lookup_or_required_field_skips_invalid_query(
    object_name, missing_field, caplog
):
    fields = [
        field
        for field in required_related_fields(object_name)
        if field != missing_field
    ]
    schema = make_schema(
        **{
            "income_fields" if object_name == "Jovedelem__c" else "loan_fields": fields
        }
    )
    fake = FakeRelatedSalesforce(
        opportunity(),
        [contact(BORROWER_ID, "Adós Példa"), contact(CO_BORROWER_ID, "Társ Példa")],
        incomes=[income_row(BORROWER_ID, 1)],
        loans=[loan_row("aLOAN", BORROWER_ID, 1, "2026-01-01")],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake, schema).get_deal("006RELATED")

    warnings = [
        record.getMessage()
        for record in caplog.records
        if object_name in record.getMessage()
        and missing_field in record.getMessage()
        and "sync_sf_schema.py" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert not any(f"FROM {object_name}" in query for query in fake.queries)
    output_key = "_incomes" if object_name == "Jovedelem__c" else "_existing_loans"
    assert all(
        participant[output_key] == []
        for participant in raw["Participants__r"]["records"]
    )


def test_account_pool_scalar_enrichment_never_merges_related_rows():
    borrower = contact(BORROWER_ID, "Azonos Név")
    donor = contact("003DONOR0000000000", "Azonos Név")
    donor["Email"] = "donor@example.invalid"
    fake = FakeRelatedSalesforce(
        opportunity(First_Co_debtor__c=None),
        [borrower],
        pool=[borrower, donor],
        incomes=[income_row("003DONOR0000000000", 1)],
        loans=[loan_row("aDONORLOAN", "003DONOR0000000000", 1, "2026-01-01")],
    )

    raw = make_client(fake).get_deal("006RELATED")
    participant = raw["Participants__r"]["records"][0]

    assert participant["email"] == "donor@example.invalid"
    assert participant["_incomes"] == []
    assert participant["_existing_loans"] == []


def test_malformed_related_contact_id_is_rejected_without_rows(caplog):
    malformed_id = "003A'OR'X'='1"
    fake = FakeRelatedSalesforce(
        opportunity(
            Opportunity_Contact_Name__c=malformed_id,
            First_Co_debtor__c=None,
        ),
        [contact(malformed_id, "Biztonság Példa")],
        incomes=[income_row("003AORX1", 1)],
        loans=[loan_row("aMALFORMED", "003AORX1", 1, "2026-01-01")],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake).get_deal("006RELATED")

    # The malformed lookup ID never enters any SOQL: no Contact, related or
    # pool query is emitted with it, and the participant is dropped entirely.
    assert not any(
        "FROM Contact" in query and "WHERE Id IN" in query
        for query in fake.queries
    )
    assert not any("FROM Jovedelem__c" in query for query in fake.queries)
    assert not any("FROM Meglevo_hitel__c" in query for query in fake.queries)
    assert raw["Participants__r"]["records"] == []
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "Contact ID" in record.getMessage()
        and "15 or 18" in record.getMessage()
        and malformed_id in record.getMessage()
    ]
    assert len(warnings) == 1


def test_distinct_malformed_ids_cannot_collide_into_shared_related_rows():
    stripped_collision_id = "003ABCDEFGHIJKLMNO"
    first_malformed = "003A'BCDEFGHIJKLMNO"
    second_malformed = "003'ABCDEFGHIJKLMNO"
    fake = FakeRelatedSalesforce(
        opportunity(
            Opportunity_Contact_Name__c=first_malformed,
            First_Co_debtor__c=second_malformed,
        ),
        [
            contact(first_malformed, "Első Példa"),
            contact(second_malformed, "Második Példa"),
        ],
        incomes=[income_row(stripped_collision_id, 1)],
        loans=[loan_row("aCOLLISION", stripped_collision_id, 1, "2026-01-01")],
    )

    raw = make_client(fake).get_deal("006RELATED")

    assert raw["Participants__r"]["records"] == []
    assert not any("FROM Jovedelem__c" in query for query in fake.queries)
    assert not any("FROM Meglevo_hitel__c" in query for query in fake.queries)


def test_contact_lookup_ids_are_sanitized_before_any_soql(caplog):
    """Task 1: mixed valid 15/18-char and malformed lookup IDs in one deal."""
    valid_15 = "003RELATEDBORRO"
    valid_18 = "003RELATEDCO000000"
    quote_id = "003A'OR'1='1"
    apostrophe_id = "003O'Brian's Loan"
    fake = FakeRelatedSalesforce(
        opportunity(
            Opportunity_Contact_Name__c=quote_id,
            First_Co_debtor__c=valid_15,
            Second_Co_debtor__c=apostrophe_id,
            Third_Co_debtor__c=valid_18,
        ),
        [contact(valid_15, "Adós Példa"), contact(valid_18, "Társ Példa")],
        incomes=[income_row(valid_15, 1)],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake).get_deal("006RELATED")

    contact_query = next(
        query
        for query in fake.queries
        if "FROM Contact" in query and "WHERE Id IN" in query
    )
    assert f"'{valid_15}'" in contact_query
    assert f"'{valid_18}'" in contact_query
    assert quote_id not in contact_query
    assert apostrophe_id not in contact_query
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "Contact ID" in record.getMessage() and "15 or 18" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert quote_id in warnings[0] and apostrophe_id in warnings[0]
    assert [p["contact_id"] for p in raw["Participants__r"]["records"]] == [
        valid_15,
        valid_18,
    ]


def test_all_malformed_contact_ids_emit_no_contact_soql(caplog):
    quote_id = "bad' OR '1'='1"
    fake = FakeRelatedSalesforce(
        opportunity(
            Opportunity_Contact_Name__c=quote_id,
            First_Co_debtor__c="also not an id",
        ),
        [],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake).get_deal("006RELATED")

    # No Id-filtered Contact query is emitted; the Account pool query is
    # unaffected and returns no participants for the malformed lookups.
    assert not any(
        "FROM Contact" in query and "WHERE Id IN" in query
        for query in fake.queries
    )
    assert not any(
        "FROM Jovedelem__c" in query or "FROM Meglevo_hitel__c" in query
        for query in fake.queries
    )
    assert raw["Participants__r"]["records"] == []
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "Contact ID" in record.getMessage() and "15 or 18" in record.getMessage()
    ]
    assert len(warnings) == 1


def test_malformed_account_id_is_dropped_without_account_soql(caplog):
    malformed_account_id = "001BAD'OR'ACCOUNT"
    fake = FakeRelatedSalesforce(
        opportunity(
            AccountId=malformed_account_id,
            Opportunity_Contact_Name__c=BORROWER_ID,
            First_Co_debtor__c=None,
        ),
        [contact(BORROWER_ID, "Adós Példa")],
        properties=[
            {
                "Id": "a00SHOULDNOTQUERY",
                "Property_Type__c": "lakás",
            }
        ],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake).get_deal("006RELATED")

    assert not any("WHERE AccountId" in query for query in fake.queries)
    assert not any("Account__c =" in query for query in fake.queries)
    assert raw["AccountId"] is None
    assert raw["_account"] == {}
    assert raw["Properties__r"]["records"] == []
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "AccountId" in record.getMessage() and "15 or 18" in record.getMessage()
    ]
    assert len(warnings) == 1
    assert malformed_account_id in warnings[0]


def _checksum_suffix(base15: str) -> str:
    """Independent re-implementation of the Salesforce 15→18 checksum."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    suffix = ""
    for start in (0, 5, 10):
        value = 0
        for offset, char in enumerate(base15[start : start + 5]):
            if char.isupper():
                value += 1 << offset
        suffix += alphabet[value]
    return suffix


def test_related_rows_match_15_and_18_char_contact_ids():
    """Task 4: a valid 15-char match must not silently lose its 18-char rows."""
    borrower_15 = "003RELATEDBORRO"
    borrower_18 = borrower_15 + _checksum_suffix(borrower_15)
    co_borrower_15 = "003RELATEDCO000"
    co_borrower_18 = co_borrower_15 + _checksum_suffix(co_borrower_15)
    assert len(borrower_18) == 18 and len(co_borrower_18) == 18

    fake = FakeRelatedSalesforce(
        opportunity(
            Opportunity_Contact_Name__c=borrower_15,
            First_Co_debtor__c=co_borrower_18,
        ),
        [contact(borrower_15, "Adós Példa"), contact(co_borrower_18, "Társ Példa")],
        # Borrower rows are owned by the 18-char form of his 15-char ID ...
        incomes=[income_row(borrower_18, 1)],
        # ... and the co-borrower's loan rows by the 15-char form of his ID.
        loans=[loan_row("aLOANCO18", co_borrower_15, 1, "2026-01-01")],
    )

    raw = make_client(fake).get_deal("006RELATED")
    borrower, co_borrower = raw["Participants__r"]["records"]

    assert borrower["contact_id"] == borrower_15
    assert co_borrower["contact_id"] == co_borrower_18
    assert [row["Contact__c"] for row in borrower["_incomes"]] == [borrower_18]
    assert [row["Contact__c"] for row in co_borrower["_existing_loans"]] == [
        co_borrower_15
    ]
    # The exact participant identity is never rewritten to the other form.
    assert borrower["contact_id"] != borrower_18


def test_opportunity_name_is_never_used_as_deal_notes():
    """Task 6: notes come only from explicit description/notes fields."""
    raw, _ = related_fixture()  # Name present, Description/remark__c absent
    assert raw["Description"] is None
    deal = DataNormalizer().normalize_deal(raw)
    assert deal.notes is None


def test_deal_notes_come_from_explicit_remark_field():
    fake = FakeRelatedSalesforce(
        opportunity(Description=None, remark__c="Kifejezetten megjegyzés"),
        [contact(BORROWER_ID, "Adós Példa")],
    )

    raw = make_client(fake).get_deal("006RELATED")

    assert raw["Description"] == "Kifejezetten megjegyzés"


def test_non_numeric_property_postal_code_stays_text_with_warning(caplog):
    """Task 7: a non-numeric Ingatlan_irsz__c must not abort the whole deal."""
    prop = {
        "Id": "a00TEXTZIP00000000",
        "Name": "Célingatlan",
        "Property_Type__c": "lakás",
        "Ingatlan_irsz__c": "H-2030",
        "Ingatlan_telepules__c": "Érd",
        "Ingatlan_kozterulet_neve__c": "Kossuth Lajos",
        "Ingatlan_hazszam__c": 8,
    }
    schema = make_schema(
        property_fields=list(FIELD_MAP["Property__c"]),
        contact_fields=("Id", "Name", "FirstName", "LastName", "Email"),
    )
    fake = FakeRelatedSalesforce(
        opportunity(First_Co_debtor__c=None),
        [contact(BORROWER_ID, "Adós Példa")],
        properties=[prop],
    )

    with caplog.at_level(logging.WARNING):
        raw = make_client(fake, schema).get_deal("006RELATED")

    assert raw is not None
    normalized = DataNormalizer().normalize_deal(raw).properties[0]
    assert normalized.address.zip_code == "H-2030"
    warnings = [
        record.getMessage()
        for record in caplog.records
        if "Ingatlan_irsz__c" in record.getMessage() and "H-2030" in record.getMessage()
    ]
    assert len(warnings) == 1


def test_loan_term_is_none_without_an_explicit_source():
    """Task 5: a missing loan term must stay None, never default to 240."""
    raw, _ = related_fixture()  # no Contact Term_in_year_c__c, no opp term

    assert raw["Loan_Term__c"] is None
    deal = DataNormalizer().normalize_deal(raw)
    assert deal.loan.loan_term_months is None


def test_loan_term_comes_only_from_explicit_contact_term_in_years():
    borrower = contact(BORROWER_ID, "Adós Példa")
    borrower["Term_in_year_c__c"] = "20"
    fake = FakeRelatedSalesforce(
        opportunity(First_Co_debtor__c=None),
        [borrower],
    )

    raw = make_client(fake).get_deal("006RELATED")

    assert raw["Loan_Term__c"] == 240
    deal = DataNormalizer().normalize_deal(raw)
    assert deal.loan.loan_term_months == 240


def test_property_describe_candidates_and_normalization_keep_semantics_distinct():
    prop = {
        "Id": "a00RELATEDPROPERTY",
        "Name": "Célingatlan",
        "Property_Type__c": "családi ház",
        "Ingatlan_reszletes_tipus__c": "tégla családi ház",
        "Ingatlan_hrsz__c": "4321/7",
        "Ingatlan_alapterulet__c": "140.5",
        "Property_value__c": "65000000",
        "Purchase_price__c": "63000000",
        "Ingatlan_irsz__c": 2030,
        "Ingatlan_telepules__c": "Érd",
        "Ingatlan_kozterulet_neve__c": "Kossuth Lajos",
        "Ingatlan_Kozterulet_jellege__c": "utca",
        "Ingatlan_hazszam__c": 8,
        "Ingatlan_emelet__c": "földszint",
        "Ingatlan_epites_eve__c": "2008",
        "Ingatlan_hasznalatbaveteli__c": "2009",
        "Ingatlan_szobak_szama__c": "5",
        "Ingatlan_allapot__c": "felújított",
        "Ingatlan_futes__c": "cirkó",
        "Ingatlan_lift__c": False,
        "Ingatlan_biztositas__c": "lakásbiztosítás",
        "Ingatlan_energetika__c": "BB",
        "Ingatlan_terhek__c": "tehermentes",
        "Ingatlan_szerepe__c": "Célingatlan",
        "Ingatlan_jellege__c": "lakóház",
        "Ingatlan_megye__c": "Pest",
        "Ingatlan_megjegyzes__c": "Megjegyzés, nem helyrajzi szám",
        "Ingatlan_neve__c": "Kertes ház",
        "Ingatlan_osztatlan__c": "nem",
        "Account__c": "001RELATED",
    }
    schema = make_schema(property_fields=list(FIELD_MAP["Property__c"]))
    fake = FakeRelatedSalesforce(
        opportunity(First_Co_debtor__c=None),
        [contact(BORROWER_ID, "Adós Példa")],
        properties=[prop],
    )

    raw = make_client(fake, schema).get_deal("006RELATED")
    property_query = next(query for query in fake.queries if "FROM Property__c" in query)
    required = {
        "Ingatlan_szobak_szama__c",
        "Ingatlan_allapot__c",
        "Ingatlan_futes__c",
        "Ingatlan_epites_eve__c",
        "Ingatlan_lift__c",
        "Ingatlan_biztositas__c",
        "Ingatlan_reszletes_tipus__c",
    }
    assert required <= set(selected_fields(property_query))

    raw["Loan_Term__c"] = 252
    deal = DataNormalizer().normalize_deal(raw)
    normalized = deal.properties[0]
    resolve = CanonicalResolver().resolve

    assert normalized.property_id == prop["Id"]
    assert normalized.property_type == PropertyType.HOUSE
    assert normalized.property_type_detail == "tégla családi ház"
    assert normalized.parcel_number == "4321/7"
    assert normalized.extra["ingatlan_megjegyzes"] == "Megjegyzés, nem helyrajzi szám"
    assert normalized.number_of_rooms == 5
    assert normalized.condition == "felújított"
    assert normalized.heating == "cirkó"
    assert normalized.year_built == 2008
    assert normalized.lift is False
    assert normalized.insurance == "lakásbiztosítás"
    assert normalized.extra["ingatlan_hasznalatbaveteli"] == "2009"
    assert normalized.epites_eve == ""
    assert normalized.extra["ingatlan_neve"] == "Kertes ház"
    assert normalized.extra["ingatlan_osztatlan"] == "nem"
    assert resolve(deal, "property.region") == "Pest"
    assert deal.loan.loan_term_months == 252

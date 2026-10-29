"""Tests for the canonical scalar resolver and extended model contracts."""

from dataclasses import replace
from datetime import date

import pytest

from src.canonical.registry import CanonicalRegistry, canonical_registry
from src.canonical.resolver import CanonicalResolver, ResolutionContext
from src.models.canonical_model import (
    Address,
    DealData,
    ExistingLoan,
    IncomeEntry,
    LoanDetails,
    Participant,
    ParticipantRole,
    Property,
)


LIMITS = {"participant": 3, "property": 9, "related": 12}


def make_field(path, source, field_type="string", repeat="none"):
    return {
        "path": path,
        "label": "Teszt mező",
        "type": field_type,
        "source": source,
        "repeat": repeat,
    }


def make_resolver(*fields):
    registry = CanonicalRegistry(
        {"version": 1, "limits": dict(LIMITS), "fields": list(fields)}
    )
    return CanonicalResolver(registry=registry)


def make_participant_field(field_name, field_type="string"):
    return make_field(
        f"participant.{{role}}.{field_name}",
        f"participant.{field_name}",
        field_type=field_type,
        repeat="participant",
    )


def make_deal_with_two_co_borrowers():
    borrower = Participant(
        role=ParticipantRole.BORROWER,
        name="Adós Példa",
        contact_id="CONTACT-BORROWER",
        gender="nő",
        salutation="Ms.",
        household_size=4,
        dependents_count=3,
        children_count=2,
        personal_id="PERSONAL-ID",
        personal_number="PERSONAL-NUMBER",
        id_card_number="ID-CARD",
        id_document_type="útlevél",
        employer="Példa Munkáltató",
        employer_tax_number="12345678-2-42",
        employer_company_form="Kft.",
        credit_commitment="nincs",
        jovairas_vallalasa="igen",
        extra={"audit_note": "szereplő extra"},
    )
    first = Participant(
        role=ParticipantRole.CO_BORROWER,
        name="Első Társ",
        contact_id="CONTACT-CO-1",
        birth_date=date(1980, 1, 2),
        address=Address(zip_code="1051", city="Budapest"),
        income_bearing=True,
    )
    second = Participant(
        role=ParticipantRole.CO_BORROWER,
        name="Második Társ",
        contact_id="CONTACT-CO-2",
        address=Address(zip_code="2000", city="Szentendre"),
        income_bearing=False,
    )
    mortgagor = Participant(
        role=ParticipantRole.MORTGAGOR,
        name="Zálog Kötelezett",
        contact_id="CONTACT-MORTGAGOR",
    )
    guarantor = Participant(
        role=ParticipantRole.GUARANTOR,
        name="Külön Kezes",
        contact_id="CONTACT-GUARANTOR",
    )
    return DealData(
        deal_id="DEAL-1",
        loan=LoanDetails(loan_amount=30_000_000),
        participants=[borrower, first, second, mortgagor, guarantor],
        properties=[
            Property(
                property_id="PROPERTY-1",
                address=Address(zip_code="1052", city="Budapest"),
                parcel_number="12345/1/A/2",
                extra={"audit_note": "első ingatlan extra"},
            ),
            Property(
                property_id="PROPERTY-2",
                address=Address(zip_code="2000", city="Szentendre"),
                parcel_number="6789/2",
                extra={"audit_note": "második ingatlan extra"},
            ),
        ],
        account={"name": "Kanonikus Account"},
        extra={"audit_note": "ügylet extra"},
    )


def test_context_selects_current_participant_without_pdf_name_heuristics():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()
    context = ResolutionContext(participant=("co_borrower", 2))

    assert resolver.resolve_text(deal, "participant.co_borrower.name", context) == "Második Társ"
    assert resolver.resolve_text(deal, "participant.co_borrower[1].name", context) == "Első Társ"
    assert resolver.resolve_text(deal, "participant.co_borrower[2].name") == "Második Társ"


def test_contact_id_uses_the_same_participant_context_and_explicit_index_wins():
    deal = make_deal_with_two_co_borrowers()
    resolver = make_resolver(make_participant_field("contact_id"))
    context = ResolutionContext(participant=("co_borrower", 2))

    assert resolver.resolve(deal, "participant.co_borrower.contact_id", context) == "CONTACT-CO-2"
    assert resolver.resolve(deal, "participant.co_borrower[1].contact_id", context) == "CONTACT-CO-1"


def test_mortgagor_is_distinct_from_guarantor_and_co_borrower():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()

    assert resolver.resolve_text(deal, "participant.mortgagor.name") == "Zálog Kötelezett"
    assert resolver.resolve_text(deal, "participant.guarantor.name") == "Külön Kezes"
    assert resolver.resolve_text(deal, "participant.co_borrower.name") == "Első Társ"


def test_mismatched_participant_context_does_not_replace_the_requested_role():
    deal = make_deal_with_two_co_borrowers()
    context = ResolutionContext(participant=("mortgagor", 1))

    assert CanonicalResolver().resolve_text(
        deal, "participant.co_borrower.name", context
    ) == "Első Társ"


def test_inactive_participants_are_skipped_before_role_indexing():
    deal = make_deal_with_two_co_borrowers()
    deal.participants.append(
        Participant(
            role=ParticipantRole.CO_BORROWER,
            name="Harmadik Aktív Társ",
        )
    )
    deal.participants[1].is_active = False
    resolver = CanonicalResolver()

    assert resolver.resolve_text(deal, "participant.co_borrower.name") == "Második Társ"
    assert resolver.resolve_text(deal, "participant.co_borrower[2].name") == "Harmadik Aktív Társ"
    assert resolver.resolve(deal, "participant.co_borrower[3].name") is None


def test_participant_nested_address_uses_the_selected_instance():
    deal = make_deal_with_two_co_borrowers()
    context = ResolutionContext(participant=("co_borrower", 2))

    assert CanonicalResolver().resolve_text(
        deal, "participant.co_borrower.address.city", context
    ) == "Szentendre"


def test_property_context_override_and_explicit_index():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()
    context = ResolutionContext(property_index=2)

    assert resolver.resolve_text(deal, "property.parcel_number", context) == "6789/2"
    assert resolver.resolve_text(deal, "property.address.city", context) == "Szentendre"
    assert resolver.resolve_text(deal, "property[1].parcel_number", context) == "12345/1/A/2"
    assert resolver.resolve_text(deal, "property[2].address.city") == "Szentendre"


def test_deal_money_and_canonical_account_values():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()

    assert resolver.resolve(deal, "deal.loan_amount") == 30_000_000
    assert resolver.resolve_text(deal, "deal.loan_amount") == "30 000 000"
    assert resolver.resolve_text(deal, "account.name") == "Kanonikus Account"


def test_account_resolution_does_not_fall_back_to_salesforce_api_names():
    deal = make_deal_with_two_co_borrowers()
    deal.account = {"Name": "Legacy Account"}

    assert CanonicalResolver().resolve(deal, "account.name") is None


@pytest.mark.parametrize(
    "field_name,expected",
    [
        ("gender", "nő"),
        ("household_size", 4),
        ("dependents_count", 3),
        ("children_count", 2),
        ("id_document_type", "útlevél"),
        ("employer_tax_number", "12345678-2-42"),
        ("credit_commitment", "nincs"),
    ],
)
def test_audit_scalars_are_separate_fields(field_name, expected):
    deal = make_deal_with_two_co_borrowers()
    field_type = "integer" if isinstance(expected, int) else "string"
    resolver = make_resolver(make_participant_field(field_name, field_type))
    path = f"participant.borrower.{field_name}"

    assert resolver.resolve(deal, path) == expected
    assert resolver.resolve_text(deal, path) == str(expected)


def test_ambiguous_audit_values_do_not_collapse_into_each_other():
    participant = make_deal_with_two_co_borrowers().participants[0]

    assert participant.gender != participant.salutation
    assert len({participant.household_size, participant.dependents_count, participant.children_count}) == 3
    assert len({participant.personal_id, participant.personal_number, participant.id_card_number, participant.id_document_type}) == 4
    assert participant.employer_tax_number != participant.employer
    assert participant.credit_commitment != participant.jovairas_vallalasa


def test_extra_sources_are_scoped_to_deal_participant_and_property():
    deal = make_deal_with_two_co_borrowers()
    resolver = make_resolver(
        make_field("deal.audit_note", "extra.audit_note"),
        make_field(
            "participant.{role}.audit_note",
            "extra.audit_note",
            repeat="participant",
        ),
        make_field(
            "property.audit_note",
            "extra.audit_note",
            repeat="property",
        ),
    )
    deal.properties[0].extra["audit_note"] = "ingatlan extra"

    assert resolver.resolve_text(deal, "deal.audit_note") == "ügylet extra"
    assert resolver.resolve_text(deal, "participant.borrower.audit_note") == "szereplő extra"
    assert resolver.resolve_text(deal, "property.audit_note") == "ingatlan extra"


def test_date_and_boolean_formatting_are_applied_by_resolve_text():
    deal = make_deal_with_two_co_borrowers()
    resolver = make_resolver(
        make_participant_field("birth_date", "date"),
        make_participant_field("income_bearing", "boolean"),
    )

    assert resolver.resolve(deal, "participant.co_borrower[1].birth_date") == date(1980, 1, 2)
    assert resolver.resolve_text(deal, "participant.co_borrower[1].birth_date") == "1980.01.02"
    assert resolver.resolve_text(deal, "participant.co_borrower[1].income_bearing") == "Igen"
    assert resolver.resolve(deal, "participant.co_borrower[2].income_bearing") is False
    assert resolver.resolve_text(deal, "participant.co_borrower[2].income_bearing") == "Nem"


def test_missing_values_and_missing_instances_resolve_to_blank_text():
    deal = make_deal_with_two_co_borrowers()
    resolver = make_resolver(make_participant_field("birth_date", "date"))

    assert resolver.resolve(deal, "participant.co_borrower[2].birth_date") is None
    assert resolver.resolve_text(deal, "participant.co_borrower[2].birth_date") == ""
    assert CanonicalResolver().resolve(deal, "participant.co_borrower[3].name") is None
    assert CanonicalResolver().resolve_text(deal, "participant.co_borrower[3].name") == ""


def test_date_fragments_do_not_mutate_the_canonical_path_or_value():
    deal = make_deal_with_two_co_borrowers()
    resolver = make_resolver(make_participant_field("birth_date", "date"))
    path = "participant.co_borrower[1].birth_date"

    assert resolver.resolve_text(deal, path, fragment="year") == "1980"
    assert resolver.resolve_text(deal, path, fragment="month") == "01"
    assert resolver.resolve_text(deal, path, fragment="day") == "02"
    assert path == "participant.co_borrower[1].birth_date"
    assert resolver.resolve(deal, path) == date(1980, 1, 2)
    assert resolver.resolve_text(deal, path) == "1980.01.02"


def test_digit_fragment_is_one_based_and_blank_out_of_range():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()

    assert resolver.resolve_text(deal, "property.parcel_number", fragment="digit:4") == "4"
    assert resolver.resolve_text(deal, "property.parcel_number", fragment="digit:0") == ""
    assert resolver.resolve_text(deal, "property.parcel_number", fragment="digit:99") == ""


def test_unknown_fragment_is_rejected_instead_of_returning_full_value():
    deal = make_deal_with_two_co_borrowers()

    with pytest.raises(ValueError, match="Unknown canonical fragment"):
        CanonicalResolver().resolve_text(
            deal, "property.parcel_number", fragment="initials"
        )


@pytest.mark.parametrize(
    "path",
    [
        "Contact.Name",
        "Lead.Ingatlan_irsz__c",
        "Opportunity.Amount",
        "Contact.Name-társ",
        "participant.co_borrower.name-társ",
    ],
)
def test_legacy_path_is_not_resolved(path):
    deal = make_deal_with_two_co_borrowers()

    with pytest.raises(ValueError):
        CanonicalResolver().resolve(deal, path)


def test_unregistered_model_attributes_are_not_resolved():
    deal = make_deal_with_two_co_borrowers()

    with pytest.raises(ValueError):
        CanonicalResolver().resolve(deal, "participant.borrower.is_active")


@pytest.mark.parametrize(
    "concrete_path,bad_source",
    [
        ("deal.loan_amount", "participant.name"),
        ("deal.notes", "account.name"),
        ("account.name", "deal.notes"),
        ("account.name", "extra.name"),
        ("participant.borrower.name", "loan.loan_amount"),
        ("property.parcel_number", "participant.name"),
        ("participant.borrower.income[1].amount", "existing_loan.outstanding_amount"),
        ("participant.borrower.income[1].amount", "extra.amount"),
        ("participant.borrower.existing_loan[1].bank_name", "income.bank_name"),
    ],
)
def test_resolver_rejects_incompatible_path_source_ownership(concrete_path, bad_source):
    registry = CanonicalRegistry(
        {
            "version": 1,
            "limits": dict(LIMITS),
            "fields": [
                make_field("deal.loan_amount", "loan.loan_amount", "money"),
                make_field("deal.notes", "deal.notes"),
                make_field("account.name", "account.name"),
                make_field(
                    "participant.{role}.name",
                    "participant.name",
                    repeat="participant",
                ),
                make_field(
                    "property.parcel_number",
                    "property.parcel_number",
                    repeat="property",
                ),
                make_field(
                    "participant.{role}.income.amount",
                    "income.amount",
                    "money",
                    "income",
                ),
                make_field(
                    "participant.{role}.existing_loan.bank_name",
                    "existing_loan.bank_name",
                    repeat="existing_loan",
                ),
            ],
        }
    )
    registry._fields[concrete_path] = replace(
        registry.get(concrete_path), source=bad_source
    )
    deal = make_related_row_deal()

    with pytest.raises(ValueError, match="does not belong"):
        CanonicalResolver(registry).resolve(deal, concrete_path)


def test_extended_models_store_audit_values_and_task_3_rows():
    income = IncomeEntry(
        income_type="salary",
        amount=350_000,
        currency="HUF",
        destination="bank",
        bank_name="Példa Bank",
        account_number="123",
        sequence=1,
    )
    existing_loan = ExistingLoan(
        loan_type="mortgage",
        bank_name="Régi Bank",
        account_number="456",
        original_amount=20_000_000,
        outstanding_amount=10_000_000,
        monthly_payment=80_000,
        currency="HUF",
        refinance=False,
        sequence=1,
    )
    participant = Participant(
        role=ParticipantRole.BORROWER,
        name="Adós",
        contact_id="CONTACT-1",
        consents={"khr": True},
        extra={"custom": "participant"},
        incomes=[income],
        existing_loans=[existing_loan],
    )
    prop = Property(
        property_id="PROPERTY-1",
        property_type_detail="tégla lakás",
        condition="jó",
        heating="gáz",
        lift=True,
        insurance="kötelező",
        extra={"custom": "property"},
    )
    deal = DealData(
        deal_id="DEAL-EXT",
        loan=LoanDetails(),
        participants=[participant],
        properties=[prop],
        extra={"custom": "deal"},
    )

    assert ParticipantRole.MORTGAGOR.value == "zálogkötelezett"
    assert deal.participants[0].contact_id == "CONTACT-1"
    assert deal.participants[0].consents == {"khr": True}
    assert deal.participants[0].incomes == [income]
    assert deal.participants[0].existing_loans == [existing_loan]
    assert deal.participants[0].existing_loans[0].refinance is False
    assert deal.properties[0].property_type_detail == "tégla lakás"
    assert deal.properties[0].lift is True
    assert deal.extra == {"custom": "deal"}


def test_old_mock_json_deserialization_gets_safe_extension_defaults():
    deal = DealData.model_validate(
        {
            "deal_id": "OLD-MOCK",
            "loan": {"loan_amount": 1_000_000},
            "participants": [{"role": "adós", "name": "Régi Adós"}],
            "properties": [{"parcel_number": "1/2"}],
        }
    )
    other = Participant(role=ParticipantRole.CO_BORROWER, name="Másik")

    participant = deal.participants[0]
    assert participant.contact_id is None
    assert participant.consents == {}
    assert participant.extra == {}
    assert participant.incomes == []
    assert participant.existing_loans == []
    assert deal.properties[0].property_id is None
    assert deal.properties[0].extra == {}
    assert deal.extra == {}

    participant.incomes.append(IncomeEntry(amount=1))
    participant.consents["khr"] = True
    participant.extra["custom"] = "only first"
    assert other.incomes == []
    assert other.consents == {}
    assert other.extra == {}


RELATED_FIELD_TYPES = {
    "income": {
        "income_type": "string",
        "amount": "money",
        "currency": "string",
        "destination": "string",
        "bank_name": "string",
        "account_number": "string",
        "sequence": "integer",
    },
    "existing_loan": {
        "loan_type": "string",
        "bank_name": "string",
        "account_number": "string",
        "original_amount": "money",
        "outstanding_amount": "money",
        "monthly_payment": "money",
        "currency": "string",
        "refinance": "boolean",
        "sequence": "integer",
    },
}


def make_related_row_deal():
    deal = make_deal_with_two_co_borrowers()
    borrower = deal.participants[0]
    borrower.income_bearing = True
    borrower.incomes = [
        IncomeEntry(
            income_type=f"type-{row:02}",
            amount=row * 100_000,
            currency="HUF" if row % 2 else "EUR",
            destination=f"destination-{row:02}",
            bank_name=f"Bank {row:02}",
            account_number=f"INCOME-{row:02}",
            sequence=13 - row,
        )
        for row in range(1, 13)
    ]

    first_co_borrower = deal.participants[1]
    first_co_borrower.income_bearing = False
    first_co_borrower.incomes = [
        IncomeEntry(
            income_type="excluded",
            amount=999_999,
            currency="EUR",
            destination="cash",
            bank_name="Excluded Bank",
            account_number="EXCLUDED",
            sequence=99,
        )
    ]
    first_co_borrower.existing_loans = [
        ExistingLoan(
            loan_type="mortgage",
            bank_name="Régi Bank 1",
            account_number="LOAN-1",
            original_amount=20_000_000,
            outstanding_amount=10_000_000,
            monthly_payment=80_000,
            currency="HUF",
            refinance=False,
            sequence=2,
        ),
        ExistingLoan(
            loan_type="personal",
            bank_name="Régi Bank 2",
            account_number="LOAN-2",
            original_amount=5_000_000,
            outstanding_amount=2_500_000,
            monthly_payment=45_000,
            currency="EUR",
            refinance=True,
            sequence=1,
        ),
    ]
    deal.loan.monthly_payment = 777_777
    deal.participants[2].income_bearing = True
    return deal


def test_default_registry_defines_every_supported_related_field():
    for collection, fields in RELATED_FIELD_TYPES.items():
        for field_name, field_type in fields.items():
            path = f"participant.borrower[2].{collection}[12].{field_name}"
            field = canonical_registry.get(path)

            assert field.path == path
            assert field.source == f"{collection}.{field_name}"
            assert field.type == field_type
            assert field.repeat == collection


def test_twelve_income_rows_keep_exact_list_order_and_formatting():
    deal = make_related_row_deal()
    resolver = CanonicalResolver()

    for row in range(1, 13):
        prefix = f"participant.borrower.income[{row}]"
        assert resolver.resolve(deal, f"{prefix}.income_type") == f"type-{row:02}"
        assert resolver.resolve(deal, f"{prefix}.amount") == row * 100_000
        assert resolver.resolve(deal, f"{prefix}.currency") == ("HUF" if row % 2 else "EUR")
        assert resolver.resolve(deal, f"{prefix}.destination") == f"destination-{row:02}"
        assert resolver.resolve(deal, f"{prefix}.bank_name") == f"Bank {row:02}"
        assert resolver.resolve(deal, f"{prefix}.account_number") == f"INCOME-{row:02}"
        assert resolver.resolve(deal, f"{prefix}.sequence") == 13 - row

    assert resolver.resolve_text(deal, "participant.borrower.income[1].amount") == "100 000"
    assert resolver.resolve_text(deal, "participant.borrower.income[12].amount") == "1 200 000"
    assert resolver.resolve_text(deal, "participant.borrower.income[12].currency") == "EUR"


def test_existing_loan_rows_use_their_own_list_not_requested_loan_details():
    deal = make_related_row_deal()
    resolver = CanonicalResolver()
    expected_rows = [
        {
            "loan_type": "mortgage",
            "bank_name": "Régi Bank 1",
            "account_number": "LOAN-1",
            "original_amount": 20_000_000,
            "outstanding_amount": 10_000_000,
            "monthly_payment": 80_000,
            "currency": "HUF",
            "refinance": False,
            "sequence": 2,
        },
        {
            "loan_type": "personal",
            "bank_name": "Régi Bank 2",
            "account_number": "LOAN-2",
            "original_amount": 5_000_000,
            "outstanding_amount": 2_500_000,
            "monthly_payment": 45_000,
            "currency": "EUR",
            "refinance": True,
            "sequence": 1,
        },
    ]

    for row, expected in enumerate(expected_rows, start=1):
        for field_name, value in expected.items():
            path = f"participant.co_borrower[1].existing_loan[{row}].{field_name}"
            assert resolver.resolve(deal, path) == value

    prefix = "participant.co_borrower[1].existing_loan"
    assert resolver.resolve_text(deal, f"{prefix}[1].original_amount") == "20 000 000"
    assert resolver.resolve_text(deal, f"{prefix}[2].outstanding_amount") == "2 500 000"
    assert resolver.resolve_text(deal, f"{prefix}[1].monthly_payment") == "80 000"
    assert resolver.resolve_text(deal, f"{prefix}[2].currency") == "EUR"
    assert resolver.resolve_text(deal, f"{prefix}[1].refinance") == "Nem"
    assert resolver.resolve_text(deal, f"{prefix}[2].refinance") == "Igen"


def test_missing_related_rows_are_blank_without_cross_participant_fallback():
    deal = make_related_row_deal()
    resolver = CanonicalResolver()
    paths = [
        "participant.co_borrower[1].existing_loan[3].bank_name",
        "participant.co_borrower[2].existing_loan[1].bank_name",
        "participant.co_borrower[2].income[1].amount",
    ]

    for path in paths:
        assert resolver.resolve(deal, path) is None
        assert resolver.resolve_text(deal, path) == ""


def test_related_rows_use_participant_context_and_explicit_index_wins():
    deal = make_related_row_deal()
    resolver = CanonicalResolver()
    context = ResolutionContext(participant=("co_borrower", 2))

    assert resolver.resolve(
        deal, "participant.co_borrower.existing_loan[1].bank_name", context
    ) is None
    assert resolver.resolve(
        deal, "participant.co_borrower[1].existing_loan[1].bank_name", context
    ) == "Régi Bank 1"
    assert resolver.resolve(
        deal,
        "participant.borrower.income[12].account_number",
        ResolutionContext(participant=("borrower", 1)),
    ) == "INCOME-12"


def test_income_bearing_false_blanks_only_income_values():
    deal = make_related_row_deal()
    resolver = CanonicalResolver()

    for field_name in RELATED_FIELD_TYPES["income"]:
        path = f"participant.co_borrower[1].income[1].{field_name}"
        assert resolver.resolve(deal, path) is None
        assert resolver.resolve_text(deal, path) == ""

    assert resolver.resolve_text(deal, "participant.co_borrower[1].name") == "Első Társ"
    assert resolver.resolve_text(
        deal, "participant.co_borrower[1].existing_loan[1].bank_name"
    ) == "Régi Bank 1"
    assert resolver.resolve(
        deal, "participant.co_borrower[1].existing_loan[1].monthly_payment"
    ) == 80_000


@pytest.mark.parametrize("income_bearing", [True, None])
def test_only_explicit_income_bearing_false_excludes_income_rows(income_bearing):
    deal = make_related_row_deal()
    participant = deal.participants[2]
    participant.income_bearing = income_bearing
    participant.incomes = [IncomeEntry(amount=123_456, currency="HUF")]
    path = "participant.co_borrower[2].income[1].amount"

    assert CanonicalResolver().resolve(deal, path) == 123_456
    assert CanonicalResolver().resolve_text(deal, path) == "123 456"

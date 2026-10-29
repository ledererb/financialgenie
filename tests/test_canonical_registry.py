"""Tests for the Genie canonical path grammar and field registry."""

import pytest

from src.canonical.paths import parse_path
from src.canonical.registry import CanonicalRegistry, canonical_registry


ROLES = ("borrower", "co_borrower", "guarantor", "mortgagor", "usufructuary")
LIMITS = {"participant": 3, "property": 9, "related": 12}


def make_field(path="deal.loan_amount", **overrides):
    definition = {
        "path": path,
        "label": "Hitelösszeg",
        "type": "money",
        "source": "loan.loan_amount",
        "repeat": "none",
    }
    definition.update(overrides)
    return definition


def make_document(*fields):
    return {"version": 1, "limits": dict(LIMITS), "fields": list(fields)}


def make_registry(*fields):
    return CanonicalRegistry(make_document(*fields))


@pytest.mark.parametrize(
    "path,role,index",
    [
        ("participant.co_borrower.name", "co_borrower", None),
        ("participant.co_borrower[2].name", "co_borrower", 2),
        ("participant.mortgagor[3].income[12].amount", "mortgagor", 3),
    ],
)
def test_parse_participants(path, role, index):
    parsed = parse_path(path)

    assert parsed.namespace == "participant"
    assert parsed.role == role
    assert parsed.participant_index == index
    assert parsed.property_index is None


@pytest.mark.parametrize("role", ROLES)
def test_parse_every_approved_role(role):
    parsed = parse_path(f"participant.{role}[3].name")

    assert parsed.role == role
    assert parsed.participant_index == 3
    assert parsed.field == "name"


@pytest.mark.parametrize(
    "path,namespace,field",
    [
        ("deal.loan_amount", "deal", "loan_amount"),
        ("account.name", "account", "name"),
        ("property.parcel_number", "property", "parcel_number"),
        ("property.address.city", "property", "address.city"),
    ],
)
def test_parse_deal_account_and_property(path, namespace, field):
    parsed = parse_path(path)

    assert parsed.namespace == namespace
    assert parsed.field == field
    assert parsed.role is None
    assert parsed.participant_index is None
    assert parsed.collection is None
    assert parsed.collection_index is None
    assert parsed.property_index is None


def test_parse_related_and_property():
    related = parse_path("participant.borrower.existing_loan[2].bank_name")
    assert related.collection == "existing_loan"
    assert related.collection_index == 2
    assert related.participant_index is None
    assert related.field == "bank_name"

    property_path = parse_path("property[3].address.city")
    assert property_path.property_index == 3
    assert property_path.field == "address.city"


@pytest.mark.parametrize("collection,field", [("income", "amount"), ("existing_loan", "bank_name")])
def test_parse_related_boundaries(collection, field):
    parsed = parse_path(f"participant.usufructuary[3].{collection}[12].{field}")

    assert parsed.role == "usufructuary"
    assert parsed.participant_index == 3
    assert parsed.collection == collection
    assert parsed.collection_index == 12
    assert parsed.field == field


@pytest.mark.parametrize(
    "path",
    [
        "Contact.Name",
        "Lead.Ingatlan_irsz__c",
        "Opportunity.Amount",
        "participant.co_borrower.name-társ",
        "participant.unknown.name",
        "participant.co_borrower[0].name",
        "property[10].parcel_number",
        "participant.borrower.income[13].amount",
        "deal..amount",
        "",
        " deal.loan_amount",
        "deal.loan_amount ",
        "Deal.loan_amount",
        "deal",
        "deal.loan.amount",
        "participant.borrower",
        "participant.borrower[4].name",
        "participant.borrower[01].name",
        "participant.borrower[+1].name",
        "participant.borrower[-1].name",
        "participant.borrower.name.extra",
        "participant.borrower.address.unknown",
        "participant.borrower.income.amount",
        "participant.borrower.income[0].amount",
        "participant.borrower.income[01].amount",
        "participant.borrower.income[1].amount.extra",
        "participant.borrower.existing_loan[13].bank_name",
        "property[0].parcel_number",
        "property[01].parcel_number",
        "property[1].address.city.extra",
        "property.address.unknown",
        "account..name",
        "account.name.extra",
    ],
)
def test_reject_invalid_paths(path):
    with pytest.raises(ValueError):
        parse_path(path)


@pytest.mark.parametrize("path", [None, 1, []])
def test_reject_non_string_paths(path):
    with pytest.raises(ValueError):
        parse_path(path)


def test_module_singleton_and_hungarian_labels():
    assert isinstance(canonical_registry, CanonicalRegistry)
    assert canonical_registry.get("participant.co_borrower.name").label == "Teljes név"
    assert canonical_registry.get("property.parcel_number").label == "Helyrajzi szám"


def test_get_returns_expanded_field_metadata():
    field = canonical_registry.get("participant.co_borrower[2].name")

    assert field.path == "participant.co_borrower[2].name"
    assert field.type == "string"
    assert field.source == "participant.name"
    assert field.repeat == "participant"


@pytest.mark.parametrize(
    "path",
    [
        "Contact.Name",
        "participant.co_borrower.name-társ",
        "participant.unknown.name",
        "deal.unregistered",
        "participant.borrower.income[13].amount",
    ],
)
def test_validate_rejects_legacy_malformed_and_unregistered_paths(path):
    assert canonical_registry.validate(path) is False


def test_default_registry_contains_only_unique_valid_canonical_paths():
    fields = canonical_registry.canonical_fields()

    assert fields
    assert all(isinstance(field, dict) for field in fields)
    paths = [field["path"] for field in fields]
    assert len(paths) == len(set(paths))
    for path in paths:
        parse_path(path)
        assert canonical_registry.validate(path) is True
        assert "Contact" not in path
        assert "Lead" not in path
        assert "Opportunity" not in path
        assert "-társ" not in path


def test_participant_template_expands_base_and_all_explicit_indexes():
    registry = make_registry(
        make_field(
            "participant.{role}.name",
            label="Teljes név",
            type="string",
            source="participant.name",
            repeat="participant",
        )
    )
    paths = {field["path"] for field in registry.canonical_fields()}

    assert len(paths) == len(ROLES) * 4
    for role in ROLES:
        assert f"participant.{role}.name" in paths
        assert {f"participant.{role}[{index}].name" for index in (1, 2, 3)} <= paths


def test_property_template_expands_base_and_all_explicit_indexes():
    registry = make_registry(
        make_field(
            "property.parcel_number",
            label="Helyrajzi szám",
            type="string",
            source="property.parcel_number",
            repeat="property",
        )
    )
    paths = {field["path"] for field in registry.canonical_fields()}

    assert paths == {"property.parcel_number"} | {
        f"property[{index}].parcel_number" for index in range(1, 10)
    }


@pytest.mark.parametrize(
    "repeat,field,source",
    [
        ("income", "amount", "income.amount"),
        ("existing_loan", "bank_name", "existing_loan.bank_name"),
    ],
)
def test_related_template_expands_participants_and_twelve_rows(repeat, field, source):
    registry = make_registry(
        make_field(
            f"participant.{{role}}.{repeat}.{field}",
            label="Kapcsolódó adat",
            type="string",
            source=source,
            repeat=repeat,
        )
    )
    paths = {item["path"] for item in registry.canonical_fields()}

    assert len(paths) == len(ROLES) * 4 * 12
    assert f"participant.borrower.{repeat}[1].{field}" in paths
    assert f"participant.borrower[3].{repeat}[12].{field}" in paths
    assert f"participant.usufructuary[2].{repeat}[7].{field}" in paths
    assert f"participant.borrower.{repeat}.{field}" not in paths


def test_duplicate_template_paths_are_rejected():
    field = make_field("deal.loan_amount")

    with pytest.raises(ValueError):
        make_registry(field, dict(field))


def test_duplicate_paths_created_by_expansion_are_rejected():
    template = make_field(
        "participant.{role}.name",
        label="Teljes név",
        type="string",
        source="participant.name",
        repeat="participant",
    )
    concrete = make_field(
        "participant.borrower.name",
        label="Adós neve",
        type="string",
        source="participant.name",
    )

    with pytest.raises(ValueError):
        make_registry(template, concrete)


@pytest.mark.parametrize("field_type", ["object", "", None])
def test_unknown_types_are_rejected(field_type):
    with pytest.raises(ValueError):
        make_registry(make_field(type=field_type))


@pytest.mark.parametrize(
    "source",
    [
        "Contact.Name",
        "Lead.Ingatlan_irsz__c",
        "Opportunity.Amount",
        "unknown.name",
        "participant..name",
        "participant.name-társ",
        "participant[1].name",
        "",
        None,
    ],
)
def test_unknown_sources_are_rejected(source):
    with pytest.raises(ValueError):
        make_registry(make_field(source=source))


@pytest.mark.parametrize(
    "path,source,repeat,concrete_path",
    [
        ("deal.notes", "deal.notes", "none", "deal.notes"),
        ("deal.loan_amount", "loan.loan_amount", "none", "deal.loan_amount"),
        ("deal.audit_note", "extra.audit_note", "none", "deal.audit_note"),
        ("account.name", "account.name", "none", "account.name"),
        ("participant.{role}.name", "participant.name", "participant", "participant.borrower.name"),
        ("participant.{role}.address.city", "participant.address.city", "participant", "participant.borrower.address.city"),
        ("participant.{role}.gender", "extra.gender", "participant", "participant.borrower.gender"),
        ("participant.{role}.khr_consent", "consents.khr", "participant", "participant.borrower.khr_consent"),
        ("property.parcel_number", "property.parcel_number", "property", "property.parcel_number"),
        ("property.audit_note", "extra.audit_note", "property", "property.audit_note"),
        ("participant.{role}.income.amount", "income.amount", "income", "participant.borrower.income[1].amount"),
        ("participant.{role}.existing_loan.bank_name", "existing_loan.bank_name", "existing_loan", "participant.borrower.existing_loan[1].bank_name"),
    ],
)
def test_approved_model_source_namespaces_are_accepted(path, source, repeat, concrete_path):
    registry = make_registry(
        make_field(path=path, source=source, repeat=repeat)
    )

    assert registry.get(concrete_path).source == source


@pytest.mark.parametrize(
    "path,source,repeat",
    [
        ("participant.{role}.name", "participant.unknown_model_attribute", "participant"),
        ("participant.{role}.permanent_address", "participant.address.unknown_model_attribute", "participant"),
        ("property.parcel_number", "property.unknown_model_attribute", "property"),
        ("property.permanent_address", "property.address.unknown_model_attribute", "property"),
        ("deal.loan_amount", "loan.unknown_model_attribute", "none"),
        ("deal.notes", "deal.unknown_model_attribute", "none"),
        ("participant.{role}.income.amount", "income.unknown_model_attribute", "income"),
        ("participant.{role}.existing_loan.bank_name", "existing_loan.unknown_model_attribute", "existing_loan"),
    ],
)
def test_unknown_typed_model_sources_are_rejected(path, source, repeat):
    with pytest.raises(ValueError, match="Unknown canonical field source"):
        make_registry(make_field(path=path, source=source, repeat=repeat))


@pytest.mark.parametrize(
    "path,source,repeat,concrete_path",
    [
        ("participant.{role}.permanent_address", "participant.address.full_address", "participant", "participant.borrower.permanent_address"),
        ("participant.{role}.permanent_address", "participant.mailing_address.full_address", "participant", "participant.borrower.permanent_address"),
        ("property.permanent_address", "property.address.full_address", "property", "property.permanent_address"),
        ("deal.loan_term_years", "loan.loan_term_years", "none", "deal.loan_term_years"),
        ("deal.has_mortgagor", "deal.has_mortgagor", "none", "deal.has_mortgagor"),
        ("deal.has_usufructuary", "deal.has_usufructuary", "none", "deal.has_usufructuary"),
    ],
)
def test_nested_and_derived_property_sources_are_accepted(path, source, repeat, concrete_path):
    registry = make_registry(
        make_field(path=path, source=source, repeat=repeat)
    )

    assert registry.get(concrete_path).source == source


def test_dynamic_dictionary_sources_remain_allowed():
    registry = make_registry(
        make_field(path="deal.dynamic_extra", source="extra.dynamic_value"),
        make_field(path="account.dynamic_account", source="account.dynamic_value"),
        make_field(
            path="participant.{role}.dynamic_consent",
            source="consents.dynamic_value",
            repeat="participant",
        ),
    )

    assert registry.get("deal.dynamic_extra").source == "extra.dynamic_value"
    assert registry.get("account.dynamic_account").source == "account.dynamic_value"
    assert registry.get("participant.borrower.dynamic_consent").source == "consents.dynamic_value"


@pytest.mark.parametrize(
    "path,source,repeat",
    [
        ("deal.loan_amount", "participant.name", "none"),
        ("deal.loan_amount", "property.parcel_number", "none"),
        ("deal.loan_amount", "income.amount", "none"),
        ("deal.notes", "account.name", "none"),
        ("account.name", "deal.notes", "none"),
        ("account.name", "extra.name", "none"),
        ("participant.{role}.name", "loan.loan_amount", "participant"),
        ("participant.{role}.name", "account.name", "participant"),
        ("property.parcel_number", "participant.name", "property"),
        ("property.parcel_number", "deal.notes", "property"),
        ("participant.{role}.income.amount", "existing_loan.outstanding_amount", "income"),
        ("participant.{role}.income.amount", "extra.amount", "income"),
        ("participant.{role}.existing_loan.bank_name", "income.bank_name", "existing_loan"),
    ],
)
def test_incompatible_path_source_ownership_is_rejected(path, source, repeat):
    with pytest.raises(ValueError, match="does not belong"):
        make_registry(make_field(path=path, source=source, repeat=repeat))


def test_unknown_repeat_mode_is_rejected():
    with pytest.raises(ValueError):
        make_registry(make_field(repeat="unknown"))


@pytest.mark.parametrize("label", ["", "   ", None])
def test_missing_hungarian_label_is_rejected(label):
    with pytest.raises(ValueError):
        make_registry(make_field(label=label))


@pytest.mark.parametrize("key", ["version", "limits", "fields"])
def test_registry_requires_version_limits_and_fields(key):
    document = make_document(make_field())
    del document[key]

    with pytest.raises(ValueError):
        CanonicalRegistry(document)


@pytest.mark.parametrize("key,value", [("participant", 4), ("property", 10), ("related", 13)])
def test_registry_limits_cannot_exceed_parser_limits(key, value):
    document = make_document(make_field())
    document["limits"][key] = value

    with pytest.raises(ValueError):
        CanonicalRegistry(document)

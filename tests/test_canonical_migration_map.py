"""Offline legacy translation and exhaustive canonical registry coverage."""

import ast
from dataclasses import FrozenInstanceError
from datetime import date
import json
from pathlib import Path

import pytest

from scripts.canonical_migration_map import (
    Translation,
    definition_for_translation,
    translate_legacy_path,
)
from scripts.build_canonical_registry import (
    _mapping_occurrences as builder_mapping_occurrences,
    build_registry,
    write_registry,
)
from src.canonical.paths import parse_path
from src.canonical.registry import CanonicalRegistry, canonical_registry
from src.canonical.resolver import CanonicalResolver
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


ROOT = Path(__file__).resolve().parents[1]
RECOGNIZER = ROOT / "src" / "ai" / "field_recognizer.py"
MAPPING_DIR = ROOT / "src" / "mapping"
FIELDS_JSON = ROOT / "src" / "canonical" / "fields.json"
# Cutover Task 3: az aktív mappingek már migrált (Genie canonical) állapotban
# élnek. Az aktiválás ELŐTTI legacy állapot audited snapshotja — ez a
# fordítási-térkép regressziós bázisa (ugyanaz az állapot, amit a Task 1-es
# dry-run audit és a registry-építés vizsgált).
PREACTIVATION_DIR = ROOT / ".superpowers" / "sdd" / "mapping-preactivation"
COMPANY_MAPPING = PREACTIVATION_DIR / (
    "v_szamu_fuggelek_penzugyi_szolgaltatas_kozvetiteseben_valo_"
    "kozremukodesre_vonatkozo_ugyfel_ceg_nyilatkozat_20250601_mapping.json"
)
LIVE_COMPANY_MAPPING = MAPPING_DIR / (
    "v_szamu_fuggelek_penzugyi_szolgaltatas_kozvetiteseben_valo_"
    "kozremukodesre_vonatkozo_ugyfel_ceg_nyilatkozat_20250601_mapping.json"
)


def static_canonical_fields():
    """The recognizer's canonical catalog.

    Cutover Task 4: the static Salesforce-named dict literal is deleted; the
    catalog is DERIVED from the shared Genie registry at import time
    (role-expanded registry templates), so the AST walk is gone and every
    entry is registry-valid by construction.
    """
    from src.ai.field_recognizer import CANONICAL_FIELDS

    return dict(CANONICAL_FIELDS)


def mapping_occurrences():
    occurrences = []

    def walk(value, filename, field_lookup, inherited_label="", inherited_pdf_name=""):
        if isinstance(value, dict):
            label = value.get("label") or inherited_label
            pdf_name = (
                value.get("pdf_field_name")
                or value.get("group_id")
                or inherited_pdf_name
            )
            if value.get("canonical_field") and value.get("member_fields"):
                member_fields = [
                    field_lookup.get(name)
                    for name in value["member_fields"]
                ]
                member_labels = [
                    field["label"]
                    for field in member_fields
                    if field and field.get("label")
                ]
                if member_labels:
                    label = member_labels[0]
                if value["member_fields"]:
                    pdf_name = value["member_fields"][0]
            for key, child in value.items():
                is_canonical = key == "canonical" or key.endswith("canonical_field")
                if is_canonical and child is not None:
                    occurrences.append(
                        (child, label or "", pdf_name or "", f"{filename}:{key}")
                    )
                else:
                    walk(child, filename, field_lookup, label, pdf_name)
        elif isinstance(value, list):
            for child in value:
                walk(child, filename, field_lookup, inherited_label, inherited_pdf_name)

    for path in sorted(MAPPING_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        field_lookup = {
            field.get("pdf_field_name"): field
            for field in document.get("fields", [])
        }
        walk(document, path.name, field_lookup)
    return occurrences


def all_occurrences():
    static = [
        (path, label, "", "CANONICAL_FIELDS")
        for path, label in static_canonical_fields().items()
    ]
    return static + mapping_occurrences()


def test_translation_is_frozen_and_has_separate_metadata():
    translation = Translation("deal.loan_amount")

    assert translation.fragment is None
    assert translation.product_gate is None
    with pytest.raises(FrozenInstanceError):
        translation.path = "deal.other"


@pytest.mark.parametrize(
    "legacy,expected",
    [
        ("Lead.Ingatlan_megjegyzes__c", "property.parcel_number"),
        ("Lead.Tervezett_onero__c", "deal.down_payment"),
        ("Contact.Nem__c", "participant.borrower.gender"),
        ("Contact.Name-társ", "participant.co_borrower.name"),
        ("Lead.Ingatlan_irsz__c_2", "property[2].address.zip_code"),
        ("loan.refinance_account", "deal.refinance_account"),
        ("Opportunity.Mortgagor__c", "deal.has_mortgagor"),
    ],
)
def test_exact_semantic_translations(legacy, expected):
    assert translate_legacy_path(legacy).path == expected


def _actual_company_field(pdf_field_name):
    # Cutover Task 3: legacy (pre-activation) snapshot input — the translation
    # map's semantics are anchored to the audited legacy state.
    document = json.loads(COMPANY_MAPPING.read_text(encoding="utf-8"))
    matches = [
        field
        for field in document["fields"]
        if field.get("pdf_field_name") == pdf_field_name
    ]
    assert len(matches) == 1
    return matches[0]


def _live_company_field(pdf_field_name):
    """The same field in the ACTIVE (migrated) mapping document."""
    document = json.loads(LIVE_COMPANY_MAPPING.read_text(encoding="utf-8"))
    matches = [
        field
        for field in document["fields"]
        if field.get("pdf_field_name") == pdf_field_name
    ]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize(
    "pdf_field_name,legacy,label,expected",
    [
        (
            "Szövegmező 2",
            "Contact.Name",
            "Alulírott (jogi személy) – Cégnév",
            "account.name",
        ),
        (
            "Szövegmező 4",
            "Contact.OtherStreet",
            "Székhely (jogi személy)",
            "account.billing_street",
        ),
        (
            "Szövegmező 12",
            "Contact.Phone",
            "Telefonszám (jogi személy)",
            "account.phone",
        ),
        (
            "Szövegmező 39",
            "Contact.OtherStreet",
            "Székhely (vállalkozás – záradék)",
            "account.billing_street",
        ),
    ],
)
def test_actual_legal_entity_occurrences_map_to_account(
    pdf_field_name, legacy, label, expected
):
    field = _actual_company_field(pdf_field_name)
    assert field["canonical_field"] == legacy
    assert field["label"] == label

    translation = translate_legacy_path(
        legacy, label=field["label"], pdf_field_name=pdf_field_name
    )
    assert translation.path == expected
    assert definition_for_translation(legacy, translation)["source"] == expected
    # Cutover Task 3: az AKTÍV (migrált) mapping már a canonical útvonalat
    # hordozza — a fenti fordítás eredménye él a fájlban.
    assert _live_company_field(pdf_field_name)["canonical_field"] == expected


@pytest.mark.parametrize(
    "pdf_field_name",
    ["Szövegmező 1", "Szövegmező 32", "Szövegmező 44"],
)
def test_actual_natural_person_names_remain_participant_scoped(pdf_field_name):
    field = _actual_company_field(pdf_field_name)
    assert field["canonical_field"] == "Contact.Name"

    assert translate_legacy_path(
        "Contact.Name",
        label=field["label"],
        pdf_field_name=pdf_field_name,
    ).path == "participant.borrower.name"
    # Cutover Task 3: az aktív mapping a migrált canonical útvonalat hordozza.
    assert _live_company_field(pdf_field_name)["canonical_field"] == (
        "participant.borrower.name"
    )


def test_unqualified_contact_street_and_phone_remain_participant_scoped():
    assert translate_legacy_path("Contact.OtherStreet").path == (
        "participant.borrower.address.street"
    )
    assert translate_legacy_path("Contact.Phone").path == "participant.borrower.phone"


def test_date_fragment_is_metadata_not_a_canonical_suffix():
    assert translate_legacy_path(
        "Contact.Date_of_notification_for_residence__c#nap"
    ) == Translation(
        path="participant.borrower.residence_notification_date",
        fragment="day",
    )
    assert translate_legacy_path(
        "Contact.Date_of_notification_for_residence__c#ev-társ"
    ) == Translation(
        path="participant.co_borrower.residence_notification_date",
        fragment="year",
    )
    assert translate_legacy_path(
        "Contact.Current_employment_started__c#ho"
    ).fragment == "month"


@pytest.mark.parametrize(
    "legacy,gate",
    [
        ("Contact.Term_in_year_c#piaci", "piaci"),
        ("Opportunity.Hitel_sszeg__c#tamogatott", "tamogatott"),
        ("Opportunity.Insurance__c#piaci", "piaci"),
    ],
)
def test_product_gate_is_preserved_separately(legacy, gate):
    translation = translate_legacy_path(legacy)

    assert translation.product_gate == gate
    assert "#" not in translation.path
    assert translation.fragment is None


def test_count_uses_pdf_label_for_children_dependents_and_household():
    children = translate_legacy_path(
        "Contact.Numer_and_Age_of_Children__c#count",
        label="Ebből gyermekek száma (fő)",
    )
    dependents = translate_legacy_path(
        "Contact.Numer_and_Age_of_Children__c#count",
        label="Eltartottak száma",
    )
    co_children = translate_legacy_path(
        "Contact.Numer_and_Age_of_Children__c#count-társ",
        label="Ebből gyermekek száma (fő)",
    )
    household = translate_legacy_path(
        "Contact.Dependents_count__c",
        label="Mekkora a háztartás létszáma? (fő)",
    )

    assert children.path == "participant.borrower.children_count"
    assert dependents.path == "participant.borrower.dependents_count"
    assert co_children.path == "participant.co_borrower.children_count"
    assert household.path == "participant.borrower.household_size"
    assert children.path != "participant.borrower.household_size"
    assert dependents.path != "participant.borrower.household_size"


def test_unsuffixed_children_details_label_preserves_details_and_model_source():
    legacy = "Contact.Numer_and_Age_of_Children__c"
    translation = translate_legacy_path(
        legacy, label="Gyermekek száma és életkora"
    )

    assert translation == Translation("participant.borrower.children_details")
    definition = definition_for_translation(legacy, translation)
    assert definition["source"] == "participant.numer_and_age_of_children"


def test_count_fragment_can_explicitly_select_household_size():
    translation = translate_legacy_path(
        "Contact.Numer_and_Age_of_Children__c#count",
        label="Háztartás létszáma",
    )

    assert translation.path == "participant.borrower.household_size"


@pytest.mark.parametrize(
    "label",
    ["", "Ismeretlen felirat", "Gyermekek és eltartottak száma"],
)
def test_count_fragment_rejects_missing_unknown_or_ambiguous_labels(label):
    with pytest.raises(ValueError):
        translate_legacy_path(
            "Contact.Numer_and_Age_of_Children__c#count", label=label
        )


def _snapshot_mapping_occurrences():
    """Mapping occurrences over the PRE-ACTIVATION (legacy) snapshot.

    Cutover Task 3: the active mappings carry Genie paths now; the label /
    member-field driven translation semantics are anchored to the audited
    legacy snapshot (identical to what the Task 1 dry-run audit covered).
    """
    occurrences = []

    def walk(value, filename, field_lookup, inherited_label="", inherited_pdf_name=""):
        if isinstance(value, dict):
            label = value.get("label") or inherited_label
            pdf_name = (
                value.get("pdf_field_name")
                or value.get("group_id")
                or inherited_pdf_name
            )
            if value.get("canonical_field") and value.get("member_fields"):
                member_fields = [
                    field_lookup.get(name)
                    for name in value["member_fields"]
                ]
                member_labels = [
                    field["label"]
                    for field in member_fields
                    if field and field.get("label")
                ]
                if member_labels:
                    label = member_labels[0]
                if value["member_fields"]:
                    pdf_name = value["member_fields"][0]
            for key, child in value.items():
                is_canonical = key == "canonical" or key.endswith("canonical_field")
                if is_canonical and child is not None:
                    occurrences.append(
                        (child, label or "", pdf_name or "", f"{filename}:{key}")
                    )
                else:
                    walk(child, filename, field_lookup, label, pdf_name)
        elif isinstance(value, list):
            for child in value:
                walk(child, filename, field_lookup, inherited_label, inherited_pdf_name)

    for path in sorted(PREACTIVATION_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        field_lookup = {
            field.get("pdf_field_name"): field
            for field in document.get("fields", [])
        }
        walk(document, path.name, field_lookup)
    return occurrences


def test_character_groups_derive_labels_and_roles_from_member_fields():
    occurrences = {
        (legacy, label, pdf_name)
        for legacy, label, pdf_name, _ in _snapshot_mapping_occurrences()
    }
    expected = [
        (
            "Contact.Dependents_count__c-társ",
            "Háztartás létszáma (1. számjegy)",
            "Text Field 718",
            "participant.co_borrower.household_size",
        ),
        (
            "Contact.Dependents_count__c-társ",
            "Ebből az eltartottak száma (1. számjegy)",
            "Text Field 720",
            "participant.co_borrower.dependents_count",
        ),
        (
            "Contact.Numer_and_Age_of_Children__c#count-társ",
            "Gyermekek száma (1. számjegy)",
            "Text Field 722",
            "participant.co_borrower.children_count",
        ),
    ]

    for legacy, label, pdf_name, canonical_path in expected:
        assert (legacy, label, pdf_name) in occurrences
        assert translate_legacy_path(
            legacy, label=label, pdf_field_name=pdf_name
        ).path == canonical_path

    # Cutover Task 3: az AKTÍV mapping karaktercsoportjai a fordított canonical
    # értékeket hordozzák (member_labels és a member_fields változatlanok).
    live_document = json.loads(
        (
            MAPPING_DIR
            / "Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json"
        ).read_text(encoding="utf-8")
    )
    live_groups = {
        group["group_id"]: group["canonical_field"]
        for group in live_document.get("character_groups", [])
    }
    assert live_groups["chargroup_contact_dependents_count_c_p6_12"] == (
        "participant.co_borrower.household_size"
    )
    assert live_groups["chargroup_contact_dependents_count_c_p6_13"] == (
        "participant.co_borrower.dependents_count"
    )
    assert live_groups["chargroup_contact_numer_and_age_of_children_c_p6_14"] == (
        "participant.co_borrower.children_count"
    )


def test_salutation_and_gender_remain_distinct_except_for_gender_pdf_labels():
    assert translate_legacy_path("Contact.Salutation").path.endswith(".salutation")
    assert translate_legacy_path(
        "Contact.Salutation", label="Neme: Nő"
    ).path == "participant.borrower.gender"
    assert translate_legacy_path(
        "Contact.Salutation-társ", label="Neme: Férfi"
    ).path == "participant.co_borrower.gender"


def test_property_indexes_are_bounded_and_only_apply_to_property_translations():
    assert translate_legacy_path(
        "Lead.Ingatlan_irsz__c_9"
    ).path == "property[9].address.zip_code"
    with pytest.raises(ValueError):
        translate_legacy_path("Lead.Ingatlan_irsz__c_10")
    with pytest.raises(ValueError):
        translate_legacy_path("Contact.Name_2")


def test_unknown_or_malformed_legacy_entries_are_rejected():
    for path in ("Contact.Not_a_real_field", "", None, "Lead.Ingatlan_irsz__c#nap"):
        with pytest.raises(ValueError):
            translate_legacy_path(path)


def test_every_unique_inventory_entry_resolves():
    """Cutover Task 4 contract: a recognizer-katalógus maga is registry-approved
    Genie útvonalakból áll — az inventáryban (katalógus + aktív mappingek)
    NINCS feloldatlan és NINCS legacy bejegyzés többé."""
    static = static_canonical_fields()
    occurrences = all_occurrences()
    unique_paths = {path for path, _, _, _ in occurrences}

    unresolved = [
        path for path in sorted(unique_paths)
        if not canonical_registry.validate(path)
    ]

    assert unresolved == []
    # The registry-derived catalog covers every participant role plus the
    # deal/property/account namespaces.
    assert len(static) > 250
    for role in ("borrower", "co_borrower", "guarantor", "mortgagor", "usufructuary"):
        assert f"participant.{role}.name" in static
    assert "deal.loan_amount" in static and "property.parcel_number" in static
    assert "account.name" in static


def test_every_actual_mapping_occurrence_resolves_to_a_registered_path():
    registry = CanonicalRegistry(build_registry())
    unresolved = []
    invalid = []
    occurrences = all_occurrences()
    canonical_occurrences = 0

    for legacy, label, pdf_name, origin in occurrences:
        if canonical_registry.validate(legacy):
            # Cutover Task 3: migrated (already canonical) occurrence — it
            # must be registered, nothing to translate.
            canonical_occurrences += 1
            if not registry.validate(legacy):
                invalid.append((origin, legacy, legacy))
            continue
        try:
            translated = translate_legacy_path(
                legacy, label=label, pdf_field_name=pdf_name
            )
            parse_path(translated.path)
            if not registry.validate(translated.path):
                invalid.append((origin, legacy, translated.path))
        except ValueError as error:
            unresolved.append((origin, legacy, str(error)))

    # Cutover Task 5 (safe declaration rebuild): 1222 - 8 bogus declaration
    # canonicals = 1214. The visual-E2E income-row fix then adds four explicit
    # canonical fields for the previously unmapped foreign-income row:
    # type, amount and two destination options => 1218 occurrences.
    assert len(mapping_occurrences()) == 1218
    assert unresolved == []
    assert invalid == []
    # Every mapped entry in the active mappings AND every recognizer-catalog
    # entry is a registered Genie path (Cutover Task 4: the catalog itself is
    # canonical, so the whole inventory validates directly).
    assert canonical_occurrences == len(all_occurrences())


def test_duplicate_legacy_targets_have_one_consistent_registry_definition():
    document = build_registry()
    registry = CanonicalRegistry(document)
    template_paths = [field["path"] for field in document["fields"]]

    assert translate_legacy_path("Contact.Loan_amount__c").path == "deal.loan_amount"
    assert translate_legacy_path("Opportunity.Hitel_sszeg__c").path == "deal.loan_amount"
    assert translate_legacy_path("loan.loan_amount").path == "deal.loan_amount"
    assert len(template_paths) == len(set(template_paths))
    assert registry.get("deal.loan_amount").type == "money"


def test_registry_build_and_serialization_are_byte_identical():
    first = json.dumps(build_registry(), ensure_ascii=False, indent=2) + "\n"
    second = json.dumps(build_registry(), ensure_ascii=False, indent=2) + "\n"

    assert first == second
    assert json.loads(FIELDS_JSON.read_text(encoding="utf-8")) == build_registry()
    assert FIELDS_JSON.read_text(encoding="utf-8") == first


def test_writer_preserves_bytes_and_mtime_when_content_is_unchanged(tmp_path):
    destination = tmp_path / "fields.json"

    assert write_registry(destination) is True
    first_bytes = destination.read_bytes()
    first_mtime = destination.stat().st_mtime_ns
    assert write_registry(destination) is False

    assert destination.read_bytes() == first_bytes
    assert destination.stat().st_mtime_ns == first_mtime


def test_complete_registry_covers_org_audit_concepts():
    registry = CanonicalRegistry(build_registry())
    expected = {
        "participant.borrower.gender",
        "participant.borrower.personal_number",
        "participant.borrower.id_document_type",
        "participant.borrower.household_size",
        "participant.borrower.children_count",
        "participant.borrower.khr_consent",
        "participant.borrower.nav_consent",
        "participant.borrower.aml_consent",
        "participant.borrower.employer_tax_number",
        "participant.borrower.employer_company_form",
        "property.condition",
        "property.heating",
        "property.year_built",
        "property.lift",
        "property.insurance",
        "participant.borrower.income[12].amount",
        "participant.borrower.existing_loan[12].account_number",
        "deal.discount_action",
        "deal.refinance_account",
        "property.number_of_rooms",
        "property.property_type_detail",
        "property.property_id",
        "deal.created_at",
    }

    assert all(registry.validate(path) for path in expected)


def test_adapter_populated_concepts_are_registered_and_resolvable():
    """Task 3: adapter-populated concepts must exist in the core registry."""
    document = build_registry()
    registry = CanonicalRegistry(document)
    resolver = CanonicalResolver(registry)
    expected = {
        "property.number_of_rooms": ("Szobák száma", "integer", "property.number_of_rooms"),
        "property.property_type_detail": (
            "Részletes ingatlantípus",
            "string",
            "property.property_type_detail",
        ),
        "property.property_id": ("Ingatlan azonosító", "string", "property.property_id"),
        "deal.created_at": (
            "Ügylet létrehozásának dátuma",
            "date",
            "deal.created_at",
        ),
    }

    for path, (label, field_type, source) in expected.items():
        assert registry.validate(path)
        field = registry.get(path)
        assert (field.label, field.type, field.source) == (label, field_type, source)
        if path.startswith("property."):
            assert registry.validate(f"property[1].{path.partition('.')[2]}")
    assert registry.get("property.number_of_rooms").repeat == "property"
    assert registry.get("deal.created_at").repeat == "none"

    deal = DealData(
        deal_id="ADAPTER-CONCEPTS",
        loan=LoanDetails(),
        created_at=date(2026, 10, 27),
        properties=[
            Property(
                property_id="a00000000000000001",
                property_type_detail="tégla családi ház",
                number_of_rooms=4,
            )
        ],
    )
    assert resolver.resolve(deal, "deal.created_at") == date(2026, 10, 27)
    assert resolver.resolve(deal, "property.property_id") == "a00000000000000001"
    assert resolver.resolve(deal, "property.property_type_detail") == "tégla családi ház"
    assert resolver.resolve(deal, "property.number_of_rooms") == 4
    assert resolver.resolve(deal, "property[1].number_of_rooms") == 4


def test_derived_and_consent_values_resolve_from_the_canonical_model():
    deal = DealData(
        deal_id="TASK-4",
        loan=LoanDetails(loan_term_months=240, discount_action="Loyalty"),
        participants=[
            Participant(
                role=ParticipantRole.BORROWER,
                name="Adós",
                consents={"khr": False, "nav": True, "aml": False},
            ),
            Participant(role=ParticipantRole.MORTGAGOR, name="Zálog"),
        ],
        properties=[Property()],
    )
    resolver = CanonicalResolver(CanonicalRegistry(build_registry()))

    assert resolver.resolve(deal, "deal.loan_term_years") == 20
    assert resolver.resolve_text(deal, "deal.loan_term_years") == "20"
    assert resolver.resolve(deal, "deal.discount_action") == "Loyalty"
    assert resolver.resolve(deal, "deal.has_mortgagor") is True
    assert resolver.resolve(deal, "deal.has_usufructuary") is False
    assert resolver.resolve(deal, "participant.borrower.khr_consent") is False
    assert resolver.resolve_text(deal, "participant.borrower.khr_consent") == "Nem"
    assert resolver.resolve(deal, "participant.borrower.nav_consent") is True
    assert resolver.resolve(deal, "participant.borrower.aml_consent") is False


def test_permanent_address_resolves_nested_full_address_with_floor_and_door():
    address = Address(
        zip_code="1051",
        city="Budapest",
        district="II",
        street="Fő utca",
        house_number="1/B",
        floor="3",
        door="4",
    )
    deal = DealData(
        deal_id="FULL-ADDRESS",
        loan=LoanDetails(),
        participants=[
            Participant(
                role=ParticipantRole.BORROWER,
                name="Adós",
                address=address,
            )
        ],
        properties=[Property()],
    )
    registry = CanonicalRegistry(build_registry())
    resolver = CanonicalResolver(registry)
    path = "participant.borrower.permanent_address"

    assert resolver.resolve(deal, path) == address.full_address
    assert resolver.resolve_text(deal, path) == (
        "1051, Budapest, II. kerület, Fő utca 1/B, 3. em., 4. ajtó"
    )
    assert registry.get(path).source == "participant.address.full_address"


def _representative_path(definition):
    repeat = definition["repeat"]
    if repeat == "participant":
        return definition["path"].replace("{role}", "borrower")
    if repeat in {"income", "existing_loan"}:
        field = definition["path"].rsplit(".", 1)[1]
        return f"participant.borrower.{repeat}[1].{field}"
    return definition["path"]


def _sentinel(definition):
    field_type = definition["type"]
    if field_type == "date":
        return date(2001, 2, 3)
    if field_type == "boolean":
        return True
    if field_type == "integer":
        return 7
    if field_type == "number":
        return 12.5
    if field_type == "money":
        return 123456
    return f"sentinel:{definition['path']}"


def _populate_sentinel(deal, definition):
    source = definition["source"]
    namespace, _, field = source.partition(".")
    sentinel = _sentinel(definition)
    participant = deal.participants[0]
    prop = deal.properties[0]

    if namespace == "participant":
        if field == "role":
            return participant.role
        if field in {"is_active", "income_bearing"}:
            setattr(participant, field, True)
            return True
        if field.startswith(("address.", "mailing_address.")):
            address_name, attribute = field.split(".", 1)
            address = getattr(participant, address_name)
            if attribute == "full_address":
                return address.full_address
            setattr(address, attribute, sentinel)
            return sentinel
        if field == "permanent_country_category":
            participant.other_country = "Magyarország"
            return "Magyarország"
        if field == "birth_place_and_date":
            participant.birth_place = "Budapest"
            participant.birth_date = date(2001, 2, 3)
            return "Budapest, 2001.02.03"
        if field == "mailing_address_relation":
            participant.mailing_address = participant.address.model_copy(deep=True)
            return "Megegyezik"
        setattr(participant, field, sentinel)
        return sentinel
    if namespace == "property":
        if field.startswith("address."):
            attribute = field.split(".", 1)[1]
            if attribute == "full_address":
                return prop.address.full_address
            setattr(prop.address, attribute, sentinel)
            return sentinel
        setattr(prop, field, sentinel)
        return sentinel
    if namespace == "loan":
        if field == "loan_term_months":
            deal.loan.loan_term_months = 240
            return 240
        if field == "loan_term_years":
            deal.loan.loan_term_months = 240
            return 20
        setattr(deal.loan, field, sentinel)
        return sentinel
    if namespace == "deal":
        if field in {"has_mortgagor", "has_usufructuary"}:
            return True
        setattr(deal, field, sentinel)
        return sentinel
    if namespace == "income":
        setattr(participant.incomes[0], field, sentinel)
        return sentinel
    if namespace == "existing_loan":
        setattr(participant.existing_loans[0], field, sentinel)
        return sentinel
    if namespace == "account":
        deal.account[field] = sentinel
        return sentinel
    if namespace == "consents":
        participant.consents[field] = True
        return True
    if namespace == "extra":
        if definition["repeat"] == "participant":
            participant.extra[field] = sentinel
        elif definition["repeat"] == "property":
            prop.extra[field] = sentinel
        else:
            deal.extra[field] = sentinel
        return sentinel
    raise AssertionError(f"Unhandled source namespace: {source}")


def test_every_registry_template_resolves_a_non_null_sentinel():
    document = build_registry()
    registry = CanonicalRegistry(document)
    borrower = Participant(
        role=ParticipantRole.BORROWER,
        name="Adós",
        address=Address(
            zip_code="1051",
            city="Budapest",
            street="Fő utca",
            house_number="1/B",
            floor="3",
            door="4",
        ),
        mailing_address=Address(zip_code="2000", city="Szentendre"),
        incomes=[IncomeEntry()],
        existing_loans=[ExistingLoan()],
        income_bearing=True,
    )
    deal = DealData(
        deal_id="SENTINELS",
        loan=LoanDetails(loan_term_months=240),
        participants=[
            borrower,
            Participant(role=ParticipantRole.MORTGAGOR, name="Zálog"),
            Participant(role=ParticipantRole.BENEFICIARY, name="Haszonélvező"),
        ],
        properties=[Property(address=Address(zip_code="1011", city="Budapest"))],
    )
    resolver = CanonicalResolver(registry)
    failures = []
    seen_templates = set()
    source_namespaces = set()

    for definition in document["fields"]:
        path = _representative_path(definition)
        expected = _populate_sentinel(deal, definition)
        actual = resolver.resolve(deal, path)
        if actual is None or actual != expected:
            failures.append((path, definition["source"], expected, actual))
        seen_templates.add(definition["path"])
        source_namespaces.add(definition["source"].split(".", 1)[0])

    assert failures == []
    assert seen_templates == {field["path"] for field in document["fields"]}
    assert source_namespaces == {
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


def test_production_registry_and_resolver_do_not_import_migration_or_recognizer():
    for relative in ("src/canonical/registry.py", "src/canonical/resolver.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "canonical_migration_map" not in source
        assert "field_recognizer" not in source

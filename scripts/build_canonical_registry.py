"""Build the complete canonical registry from curated and legacy definitions.

The builder is deterministic and offline.  Since Cutover Task 4 the
recognizer's catalog is derived from the shared registry (no static literal),
so the builder imports the live module; every catalog entry is an
already-canonical occurrence that merges from the existing registry.  It reads
all mapping JSON files and writes only when the formatted document changed.
"""

import ast
import json
import re
from dataclasses import asdict
from pathlib import Path

from scripts.canonical_migration_map import (
    definition_for_translation,
    explicit_legacy_fields,
    translate_legacy_path,
)
from src.canonical.paths import parse_path
from src.canonical.registry import CanonicalRegistry, canonical_registry


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_PATH = ROOT / "src" / "canonical" / "fields.json"
_RECOGNIZER_PATH = ROOT / "src" / "ai" / "field_recognizer.py"
_MAPPING_DIR = ROOT / "src" / "mapping"
_LIMITS = {"participant": 3, "property": 9, "related": 12}


def _field(path, label, field_type, source, repeat):
    return {
        "path": path,
        "label": label,
        "type": field_type,
        "source": source,
        "repeat": repeat,
    }


# Tasks 1 and 3 are the curated Genie foundation.  Keeping these definitions
# independent of the generated output makes repeated builds truly idempotent.
_CURATED_FIELDS = (
    _field("deal.loan_amount", "Hitelösszeg", "money", "loan.loan_amount", "none"),
    _field("account.name", "Ügyfél neve", "string", "account.name", "none"),
    _field("participant.{role}.name", "Teljes név", "string", "participant.name", "participant"),
    _field("participant.{role}.address.city", "Állandó lakcím – település", "string", "participant.address.city", "participant"),
    _field("property.parcel_number", "Helyrajzi szám", "string", "property.parcel_number", "property"),
    _field("property.address.city", "Ingatlan település", "string", "property.address.city", "property"),
    _field("participant.{role}.income.income_type", "Jövedelem típusa", "string", "income.income_type", "income"),
    _field("participant.{role}.income.amount", "Jövedelem összege", "money", "income.amount", "income"),
    _field("participant.{role}.income.currency", "Jövedelem pénzneme", "string", "income.currency", "income"),
    _field("participant.{role}.income.destination", "Jövedelem érkezésének helye", "string", "income.destination", "income"),
    _field("participant.{role}.income.bank_name", "Jövedelem bankja", "string", "income.bank_name", "income"),
    _field("participant.{role}.income.account_number", "Jövedelem számlaszáma", "string", "income.account_number", "income"),
    _field("participant.{role}.income.sequence", "Jövedelem sorrendje", "integer", "income.sequence", "income"),
    _field("participant.{role}.existing_loan.loan_type", "Meglévő hitel típusa", "string", "existing_loan.loan_type", "existing_loan"),
    _field("participant.{role}.existing_loan.bank_name", "Meglévő hitel bankja", "string", "existing_loan.bank_name", "existing_loan"),
    _field("participant.{role}.existing_loan.account_number", "Meglévő hitel számlaszáma", "string", "existing_loan.account_number", "existing_loan"),
    _field("participant.{role}.existing_loan.original_amount", "Meglévő hitel eredeti összege", "money", "existing_loan.original_amount", "existing_loan"),
    _field("participant.{role}.existing_loan.outstanding_amount", "Meglévő hitel fennálló tartozása", "money", "existing_loan.outstanding_amount", "existing_loan"),
    _field("participant.{role}.existing_loan.monthly_payment", "Meglévő hitel havi törlesztője", "money", "existing_loan.monthly_payment", "existing_loan"),
    _field("participant.{role}.existing_loan.currency", "Meglévő hitel pénzneme", "string", "existing_loan.currency", "existing_loan"),
    _field("participant.{role}.existing_loan.refinance", "Meglévő hitel kiváltandó", "boolean", "existing_loan.refinance", "existing_loan"),
    _field("participant.{role}.existing_loan.sequence", "Meglévő hitel sorrendje", "integer", "existing_loan.sequence", "existing_loan"),
    # Adapter-populated concepts the Salesforce client fills directly.  These
    # are registered here (not via legacy translations) because the adapter
    # owns their semantics: stable Property Id, room count, the detailed
    # property type, and the deal creation date.
    _field("property.number_of_rooms", "Szobák száma", "integer", "property.number_of_rooms", "property"),
    _field("property.property_type_detail", "Részletes ingatlantípus", "string", "property.property_type_detail", "property"),
    _field("property.property_id", "Ingatlan azonosító", "string", "property.property_id", "property"),
    _field("deal.created_at", "Ügylet létrehozásának dátuma", "date", "deal.created_at", "none"),
)


def _static_catalog():
    """The recognizer's canonical catalog (registry-derived since Cutover Task 4).

    The recognizer no longer carries a static Salesforce-named dict literal —
    its catalog is derived from the shared Genie registry at import time, so
    the builder reads the live module. Every entry is an already-canonical
    occurrence that merges back from the existing registry (template recovery
    from concrete paths), keeping the rebuild byte-identical.
    """
    from src.ai.field_recognizer import CANONICAL_FIELDS

    if not isinstance(CANONICAL_FIELDS, dict) or not CANONICAL_FIELDS:
        raise ValueError("Recognizer canonical catalog is empty or malformed")
    return dict(CANONICAL_FIELDS)


def _character_group_context(group, field_lookup):
    member_names = group.get("member_fields") or []
    canonical = group["canonical_field"]
    first_pdf_name = member_names[0] if member_names else group["group_id"]
    member_labels = [
        (name, (field_lookup.get(name) or {}).get("label"))
        for name in member_names
    ]
    is_count_group = (
        "Dependents_count__c" in canonical
        or "Numer_and_Age_of_Children__c" in canonical
    )
    if is_count_group:
        missing = [name for name, label in member_labels if not label]
        if not member_names or missing:
            raise ValueError(
                f"Missing member-field labels for count group {group['group_id']!r}: {missing!r}"
            )
        translations = {
            translate_legacy_path(canonical, label=label, pdf_field_name=name)
            for name, label in member_labels
        }
        if len(translations) != 1:
            raise ValueError(
                f"Ambiguous member-field labels for count group {group['group_id']!r}"
            )
        return member_labels[0][1], first_pdf_name

    first_label = next((label for _, label in member_labels if label), "")
    return first_label, first_pdf_name


def _mapping_occurrences():
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
                label, pdf_name = _character_group_context(value, field_lookup)
            for key, child in value.items():
                is_canonical = key == "canonical" or key.endswith("canonical_field")
                if is_canonical and child is not None:
                    occurrences.append((child, label or "", pdf_name or "", f"{filename}:{key}"))
                else:
                    walk(child, filename, field_lookup, label, pdf_name)
        elif isinstance(value, list):
            for child in value:
                walk(child, filename, field_lookup, inherited_label, inherited_pdf_name)

    for path in sorted(_MAPPING_DIR.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        field_lookup = {
            field.get("pdf_field_name"): field
            for field in document.get("fields", [])
        }
        walk(document, path.name, field_lookup)
    return occurrences


def _inventory():
    occurrences = [
        (path, label, "", "CANONICAL_FIELDS")
        for path, label in _static_catalog().items()
    ]
    occurrences.extend(_mapping_occurrences())
    known_paths = {path for path, _, _, _ in occurrences}
    for path, label in explicit_legacy_fields().items():
        if path not in known_paths:
            occurrences.append((path, label, "", "explicit org audit"))
    return occurrences


def _merge(definitions, definition, origin):
    path = definition["path"]
    previous = definitions.get(path)
    if previous is not None and previous != definition:
        raise ValueError(
            f"Conflicting canonical definitions for {path!r} from {origin}: "
            f"{previous!r} != {definition!r}"
        )
    definitions[path] = definition


# Cutover Task 3: a registry-DOCUMENTban a repeat-es mezők sablonként élnek
# (``participant.{role}.…``), míg a kibontott registry konkrét path-okat
# ad vissza. Egy már-canonical mapping-előfordulás definíciójának visszaírásához
# a konkrét path-ból újra elő kell állítani a sablont.
_CONCRETE_PARTICIPANT_RE = re.compile(
    r"^participant\.(?:borrower|co_borrower|guarantor|mortgagor|usufructuary)"
    r"(?:\[\d+\])?"
)
_CONCRETE_PROPERTY_RE = re.compile(r"^property(?:\[\d+\])?\.")
_CONCRETE_RELATED_RE = re.compile(
    r"^participant\.(?:borrower|co_borrower|guarantor|mortgagor|usufructuary)"
    r"(?:\[\d+\])?\.(?P<collection>income|existing_loan)(?:\[\d+\])?\."
)


def _concrete_to_template(concrete: str) -> str:
    """Return the registry-document template for a concrete canonical path."""

    field = canonical_registry.get(concrete)
    if field.repeat == "none":
        return concrete
    if field.repeat == "participant":
        return _CONCRETE_PARTICIPANT_RE.sub("participant.{role}", concrete, count=1)
    if field.repeat == "property":
        return _CONCRETE_PROPERTY_RE.sub("property.", concrete, count=1)
    return _CONCRETE_RELATED_RE.sub(
        lambda match: f"participant.{match['collection']}.", concrete, count=1
    ).replace("participant.", "participant.{role}.", 1)


def build_registry() -> dict:
    """Return the complete versioned registry with zero unresolved inputs."""

    definitions = {}
    for curated in _CURATED_FIELDS:
        _merge(definitions, dict(curated), "curated Genie foundation")

    translations = []
    for legacy, label, pdf_name, origin in _inventory():
        # Cutover Task 3: az aktív mappingek már Genie canonical útvonalakat
        # hordoznak. Egy registry-approved canonical előfordulás definíciója
        # a meglévő registryből érkezik (amely az aktiválás ELŐTTI legacy
        # fordításokból készült) — nincs újrafordítás, csak konzisztencia-
        # ellenőrzés. A legacy (SF-neves) előfordulások továbbra is fordítódnak.
        if canonical_registry.validate(legacy):
            try:
                registered = canonical_registry.get(legacy)
            except KeyError as error:
                raise ValueError(
                    f"Canonical entry {legacy!r} at {origin} is grammatical "
                    f"but missing from the current registry: {error}"
                ) from error
            definition = asdict(registered)
            definition["path"] = _concrete_to_template(legacy)
            _merge(definitions, definition, f"{origin}:{legacy}")
            translations.append((legacy, legacy, origin))
            continue
        try:
            translation = translate_legacy_path(
                legacy, label=label, pdf_field_name=pdf_name
            )
        except ValueError as error:
            raise ValueError(f"Unresolved legacy entry {legacy!r} at {origin}: {error}") from error
        parse_path(translation.path)
        definition = definition_for_translation(legacy, translation, label)
        _merge(definitions, definition, f"{origin}:{legacy}")
        translations.append((legacy, translation.path, origin))

    generated_paths = sorted(definitions.keys() - {field["path"] for field in _CURATED_FIELDS})
    document = {
        "version": 1,
        "limits": dict(_LIMITS),
        "fields": [dict(field) for field in _CURATED_FIELDS]
        + [definitions[path] for path in generated_paths],
    }
    registry = CanonicalRegistry(document)
    invalid = [
        (legacy, path, origin)
        for legacy, path, origin in translations
        if not registry.validate(path)
    ]
    if invalid:
        raise ValueError(f"Translated paths missing from registry: {invalid!r}")
    return document


def formatted_registry() -> str:
    return json.dumps(build_registry(), ensure_ascii=False, indent=2) + "\n"


def write_registry(path: str | Path | None = None) -> bool:
    """Write formatted registry bytes only when they differ; return changed."""

    destination = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    content = formatted_registry()
    try:
        if destination.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    destination.write_text(content, encoding="utf-8")
    return True


if __name__ == "__main__":
    changed = write_registry()
    generated = build_registry()
    concrete = CanonicalRegistry(generated).canonical_fields()
    print(f"registry_templates={len(generated['fields'])}")
    print(f"concrete_fields={len(concrete)}")
    print("unresolved=0")
    print(f"changed={str(changed).lower()}")

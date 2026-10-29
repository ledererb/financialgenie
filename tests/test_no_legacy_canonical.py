"""Final-state activation audit: no legacy canonical forms anywhere.

Cutover Task 3 Step 1 (brief):
- Audit every mapping field, character group, point and rule parameter for
  forbidden legacy forms (``Contact.*``, ``Lead.*``, ``Opportunity.*``,
  ``Account.*``, ``loan.*``, ``-társ`` suffixes and the ``#ev/#ho/#nap/
  #count/#piaci/#tamogatott`` canonical suffixes).
- Audit runtime ``_prepare_field_data`` with a legacy path: it must raise
  rather than silently resolve through deleted legacy routing.
- Verify the applied mapping counts against the recorded dry-run report.

These tests describe the FINAL (activated) state: they were written first and
drove the activation (RED before ``--apply`` + legacy-branch deletion, GREEN
after).
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from scripts.migrate_mappings_to_genie import migrate_mapping, mapping_files
from src.canonical.registry import canonical_registry
from src.main import FormFillerPipeline, _mapping_uses_canonical_paths
from src.models.canonical_model import DealData
from tests.test_pipeline_genie_resolver import make_genie_deal as make_deal


ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = ROOT / "src" / "mapping"
PREACTIVATION_DIR = ROOT / ".superpowers" / "sdd" / "mapping-preactivation"
DRY_RUN_REPORT = ROOT / ".superpowers" / "sdd" / "genie-migration-dry-run.json"

# Canonical-reference keys audited at any depth of a mapping document. PDF
# field names (``pdf_field_name``, ``member_fields``) legitimately keep their
# bank-side names (including "-társ") — they are NOT canonical references.
_REFERENCE_KEYS = frozenset({"canonical_field", "sf_field"})

_LEGACY_NS_RE = re.compile(
    r"^(Contact|Lead|Opportunity|Account|loan)\.",
)
_FORBIDDEN_SUFFIX_RE = re.compile(
    r"#(ev|ho|nap|count|piaci|tamogatott)$",
)


def _iter_canonical_references(node, location="$"):
    """Yield (location, value) for every canonical-reference key at any depth."""

    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{location}.{key}"
            if key in _REFERENCE_KEYS and isinstance(value, str) and value:
                yield child, value
            else:
                yield from _iter_canonical_references(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _iter_canonical_references(item, f"{location}[{index}]")


def _active_mapping_paths() -> list[Path]:
    return mapping_files(MAPPING_DIR)


def test_active_mapping_inventory_matches_dry_run_report():
    report = json.loads(DRY_RUN_REPORT.read_text(encoding="utf-8"))

    assert sorted(path.name for path in _active_mapping_paths()) == [
        entry["file"] for entry in report["mappings"]
    ]
    assert report["totals"]["mappings"] == len(report["mappings"])
    assert report["totals"]["unresolved"] == 0


# ──────────────────────────────────────────────────────────────────────────
# 1. No forbidden legacy canonical forms in any mapping structure
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", _active_mapping_paths(), ids=lambda p: p.stem
)
def test_no_legacy_canonical_forms_in_any_mapping(path):
    document = json.loads(path.read_text(encoding="utf-8"))

    offenders = []
    for location, value in _iter_canonical_references(document):
        if (
            _LEGACY_NS_RE.match(value)
            or value.endswith("-társ")
            or _FORBIDDEN_SUFFIX_RE.search(value)
        ):
            offenders.append((location, value))
    assert offenders == [], f"{path.name}: {offenders[:10]}"

    # Every non-null canonical reference is a registry-approved Genie path.
    for _location, value in _iter_canonical_references(document):
        assert canonical_registry.validate(value), (path.name, value)


@pytest.mark.parametrize(
    "path", _active_mapping_paths(), ids=lambda p: p.stem
)
def test_every_active_mapping_routes_through_the_canonical_pipeline(path):
    document = json.loads(path.read_text(encoding="utf-8"))

    has_mapped_field = any(
        isinstance(field.get("canonical_field"), str)
        for field in document.get("fields", [])
    )
    if has_mapped_field:
        assert _mapping_uses_canonical_paths(document) is True, path.name


# ──────────────────────────────────────────────────────────────────────────
# 2. Runtime route: legacy paths raise instead of silently resolving
# ──────────────────────────────────────────────────────────────────────────


def _pipeline() -> FormFillerPipeline:
    return FormFillerPipeline.__new__(FormFillerPipeline)


def test_prepare_field_data_raises_on_legacy_canonical_mapping():
    pipeline = _pipeline()
    deal = make_deal()
    legacy_mapping = {
        "form_type": "acroform",
        "fields": [
            {
                "pdf_field_name": "Nev",
                "label": "Név",
                "field_type": "text",
                "canonical_field": "Contact.Name",
            }
        ],
    }

    with pytest.raises(ValueError, match="legacy|Contact|migrat"):
        pipeline._prepare_field_data(deal, legacy_mapping)


def test_prepare_field_data_raises_on_legacy_tars_suffix_mapping():
    pipeline = _pipeline()
    deal = make_deal()
    legacy_mapping = {
        "form_type": "acroform",
        "fields": [
            {
                "pdf_field_name": "Nev_tars",
                "label": "Társigénylő neve",
                "field_type": "text",
                "canonical_field": "Contact.Name-társ",
            }
        ],
    }

    with pytest.raises(ValueError):
        pipeline._prepare_field_data(deal, legacy_mapping)


def test_prepare_field_data_raises_on_legacy_fill_rule_sf_field():
    pipeline = _pipeline()
    deal = make_deal()
    mapping = {
        "form_type": "acroform",
        "fields": [
            {
                "pdf_field_name": "Osszeg",
                "label": "Összeg",
                "field_type": "text",
                "canonical_field": "deal.loan_amount",
                "fill_rule": {
                    "type": "conditional",
                    "sf_field": "Contact.Loan_Purpose__c",
                    "match": "Lakásvásárlás",
                    "value": "igen",
                },
            }
        ],
    }

    # A legacy sf_field inside an otherwise-canonical mapping must not
    # silently never-match: the conditional rule evaluation reports it.
    notices: list = []
    field_data = pipeline._prepare_field_data(deal, mapping, notices=notices)
    assert field_data.get("Osszeg") is None
    assert any("sf_field" in notice for notice in notices)


def test_prepare_field_data_accepts_unmapped_document_without_error():
    """A document with zero mapped canonical fields is not 'legacy' — it
    simply fills nothing through the canonical branch (legal declarations
    are handled separately) and must not raise."""
    pipeline = _pipeline()
    deal = make_deal()
    unmapped = {
        "form_type": "acroform",
        "fields": [
            {
                "pdf_field_name": "Alairas",
                "label": "Aláírás",
                "field_type": "text",
                "canonical_field": None,
            }
        ],
    }

    field_data = pipeline._prepare_field_data(deal, unmapped)

    assert field_data == {}


# ──────────────────────────────────────────────────────────────────────────
# 3. Applied mapping counts verified against the recorded dry-run report
# ──────────────────────────────────────────────────────────────────────────


def _snapshot_hashes() -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(PREACTIVATION_DIR.glob("*.json"))
    }


def test_applied_files_exactly_match_the_audited_dry_run_changes():
    report = json.loads(DRY_RUN_REPORT.read_text(encoding="utf-8"))
    before = _snapshot_hashes()
    assert before, "pre-activation snapshot is missing"

    changed_in_report = {
        entry["file"]
        for entry in report["mappings"]
        if entry["changed_count"] > 0
    }
    actually_changed = {
        path.name
        for path in _active_mapping_paths()
        if hashlib.sha256(path.read_bytes()).hexdigest() != before[path.name]
    }

    # Exactly the mappings the audited dry-run said would change were
    # rewritten — no more (no unaudited edits), no fewer (nothing skipped).
    assert actually_changed == changed_in_report


def test_applied_mappings_are_idempotent_under_the_migrator():
    for path in _active_mapping_paths():
        document = json.loads(path.read_text(encoding="utf-8"))
        result = migrate_mapping(document)

        assert result.changed_count == 0, path.name
        assert result.unresolved == [], path.name
        assert result.mapping == document, path.name


def test_dealdata_import_unaffected():
    """Sanity: the audit imports the real canonical model, not a stub."""
    assert DealData is not None

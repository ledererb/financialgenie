"""Deterministic legacy-to-Genie mapping migrator with a dry-run audit.

Cutover Task 1: migration tooling only.  This script never activates the
migrated mappings: ``--dry-run`` (the default) computes everything in memory,
``--apply`` rewrites the mapping JSON in place and is guarded so it refuses to
run twice.  All semantics come from ``scripts.canonical_migration_map``
(``Translation`` objects produced by ``translate_legacy_path``); the migrator
adds no mapping decisions of its own.

Guarantees:

* PDF-level metadata (``pdf_field_name``, ``coordinates``, ``checkbox_group``,
  ``fill_rule``, ``page_number``, ``labels``, ``character_groups`` members,
  ``points``, ``rule_sets``) is preserved byte-for-byte.
* Date suffixes (``#ev``/``#ho``/``#nap``) become ``fragment`` metadata.
* Product suffixes (``#piaci``/``#tamogatott``) become explicit
  ``product_gate`` metadata next to the untouched ``fill_rule``.
* Participant/property indexes become explicit path indexes
  (``participant.co_borrower[1].…``, ``property[2].…``).
* Unknown legacy entries are reported as ``unresolved`` and abort the whole
  apply with a report; dry runs always succeed and only audit.
* Migration is idempotent: canonical values are detected and left unchanged,
  so a second pass changes nothing and ``--apply`` refuses to run twice.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # Allow direct execution: python3 scripts/migrate_mappings_to_genie.py
    sys.path.insert(0, str(ROOT))

from scripts.canonical_migration_map import (  # noqa: E402
    Translation,
    _LEGACY_FIELDS,
    _base_and_suffix,
    translate_legacy_path,
)
from src.canonical.registry import canonical_registry  # noqa: E402


MAPPING_DIR = ROOT / "src" / "mapping"
DEFAULT_REPORT_PATH = ROOT / ".superpowers" / "sdd" / "genie-migration-dry-run.json"

_PROPERTY_INDEX_RE = re.compile(r"^(?P<base>.+)_(?P<index>[1-9])$")


@dataclass(frozen=True)
class MigrationResult:
    """One migrated mapping document plus its audit records.

    Every audit record is a plain dict so reports serialize unchanged:
    ``unresolved`` entries carry ``reason``; ``semantic_corrections``,
    ``fragment_changes``, ``group_changes`` and ``product_gates`` carry the
    legacy value, the resulting canonical path and the entry location.
    ``nested_changes`` (Cutover Task 3) records translated canonical
    references inside conditional ``fill_rule.sf_field``, point rules/params
    and checkbox/rule metadata.
    """

    mapping: dict
    pdf_path: str | None = None
    changed_count: int = 0
    unresolved: list = field(default_factory=list)
    semantic_corrections: list = field(default_factory=list)
    fragment_changes: list = field(default_factory=list)
    group_changes: list = field(default_factory=list)
    product_gates: list = field(default_factory=list)
    nested_changes: list = field(default_factory=list)


# Cutover Task 3 (additional requirement 1): keys whose string value is a
# canonical path reference when they appear NESTED inside fill rules, point
# rules/params or checkbox/rule metadata. The top-level ``canonical_field``
# of a field/character-group entry is handled separately (fragment and
# product-gate semantics); every occurrence below those top levels is
# translated by the same deterministic translation map.
_NESTED_REFERENCE_KEYS = frozenset({"canonical_field", "sf_field"})


def _legacy_structure(legacy: str) -> tuple[str | None, str | None]:
    """Return the static default path and structural correction reason.

    The default path is what the legacy base name maps to with no label
    context and no structural suffix; a translated path differing from it is
    recorded as a semantic correction for human review.
    """

    try:
        base, role, suffix = _base_and_suffix(legacy)
    except ValueError:
        return None, None
    reason = None
    if role == "co_borrower":
        reason = "co-borrower role suffix"
    elif suffix == "count":
        reason = "count target from label"
    indexed = _PROPERTY_INDEX_RE.fullmatch(base)
    if indexed is not None and indexed["base"] in _LEGACY_FIELDS:
        base = indexed["base"]
        reason = reason or "explicit property index"
    entry = _LEGACY_FIELDS.get(base)
    if entry is None:
        return None, None
    return entry.translation.path, reason or "label context"


def _resolve_entry(
    legacy: object, label: str, pdf_name: str
) -> tuple[Translation | None, str | None]:
    """Translate one legacy value or explain why it cannot be translated."""

    if not isinstance(legacy, str) or not legacy:
        return None, f"canonical_field must be a non-empty string: {legacy!r}"
    if canonical_registry.validate(legacy):
        return None, None  # Already migrated; idempotence guard.
    try:
        translation = translate_legacy_path(
            legacy, label=label, pdf_field_name=pdf_name
        )
    except ValueError as error:
        return None, str(error)
    if not canonical_registry.validate(translation.path):
        return None, (
            f"Translated path missing from canonical registry: {translation.path}"
        )
    return translation, None


def _group_context(group: dict, field_lookup: dict) -> tuple[str, str]:
    """Derive (label, pdf field) context from a character group's members."""

    member_names = group.get("member_fields") or []
    label = next(
        (
            (field_lookup.get(name) or {}).get("label")
            for name in member_names
        ),
        None,
    )
    pdf_name = member_names[0] if member_names else (group.get("group_id") or "")
    return str(label or ""), str(pdf_name or "")


def _migrate_nested_references(
    node: object,
    *,
    location: str,
    label: str,
    pdf_name: str,
    audits: dict,
) -> None:
    """Translate canonical references nested in rules/metadata, in place.

    Cutover Task 3 (additional requirement 1): every ``canonical_field`` /
    ``sf_field`` key that appears BELOW the top level of a field or character
    group — inside conditional ``fill_rule`` dicts, point rules/params (and
    their blocks) and checkbox/rule metadata — is translated with the same
    deterministic translation map, inheriting the owning entry's label and
    PDF field name as context. Already-canonical values are left unchanged
    (idempotence); anything that fails to translate is recorded in
    ``unresolved`` and aborts a later apply exactly like a top-level failure.
    """

    if isinstance(node, dict):
        for key, value in list(node.items()):
            child_location = f"{location}.{key}"
            if (
                key in _NESTED_REFERENCE_KEYS
                and isinstance(value, str)
                and value
            ):
                translation, reason = _resolve_entry(value, label, pdf_name)
                if reason is not None:
                    audits["unresolved"].append(
                        {
                            "location": child_location,
                            "pdf_field_name": pdf_name,
                            "label": label,
                            "legacy": value,
                            "reason": reason,
                        }
                    )
                elif translation is not None:
                    node[key] = translation.path
                    audits["nested_changes"].append(
                        {
                            "location": child_location,
                            "pdf_field_name": pdf_name,
                            "label": label,
                            "legacy": value,
                            "canonical": translation.path,
                        }
                    )
                    audits["changed_count"] += 1
                continue
            _migrate_nested_references(
                value,
                location=child_location,
                label=label,
                pdf_name=pdf_name,
                audits=audits,
            )
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _migrate_nested_references(
                item,
                location=f"{location}[{index}]",
                label=label,
                pdf_name=pdf_name,
                audits=audits,
            )


def _apply_translation(
    entry: dict,
    legacy: str,
    translation: Translation,
    *,
    location: str,
    pdf_name: str,
    label: str,
    audits: dict,
) -> None:
    """Rewrite one entry in place and append the audit records."""

    changed = False

    if entry.get("canonical_field") != translation.path:
        entry["canonical_field"] = translation.path
        changed = True

    previous_fragment = entry.get("fragment")
    if translation.fragment is not None:
        # Audit every date-suffix decode, even when the stored fragment
        # already agrees: the explicit fragment is part of the migration.
        if previous_fragment != translation.fragment:
            entry["fragment"] = translation.fragment
            changed = True
        audits["fragment_changes"].append(
            {
                "location": location,
                "pdf_field_name": pdf_name,
                "legacy": legacy,
                "canonical": translation.path,
                "fragment": translation.fragment,
                "previous": previous_fragment,
            }
        )

    if translation.product_gate is not None:
        entry["product_gate"] = translation.product_gate
        changed = True
        audits["product_gates"].append(
            {
                "location": location,
                "pdf_field_name": pdf_name,
                "legacy": legacy,
                "canonical": translation.path,
                "product_gate": translation.product_gate,
            }
        )

    default_path, reason = _legacy_structure(legacy)
    if default_path is not None and translation.path != default_path:
        audits["semantic_corrections"].append(
            {
                "location": location,
                "pdf_field_name": pdf_name,
                "label": label,
                "legacy": legacy,
                "canonical": translation.path,
                "reason": reason,
            }
        )

    if changed:
        audits["changed_count"] += 1


def migrate_mapping(data: dict, *, pdf_path: str | None = None) -> MigrationResult:
    """Migrate one mapping document deterministically without mutating it."""

    mapping = copy.deepcopy(data)
    audits = {
        "changed_count": 0,
        "unresolved": [],
        "semantic_corrections": [],
        "fragment_changes": [],
        "group_changes": [],
        "product_gates": [],
        "nested_changes": [],
    }

    fields = mapping.get("fields") if isinstance(mapping, dict) else None
    fields = fields if isinstance(fields, list) else []

    for index, entry in enumerate(fields):
        if not isinstance(entry, dict):
            continue
        legacy = entry.get("canonical_field")
        location = f"fields[{index}]"
        label = str(entry.get("label") or "")
        pdf_name = str(entry.get("pdf_field_name") or "")
        # Nested rule references (fill_rule.sf_field, checkbox/rule metadata)
        # translate independently of the entry's own canonical mapping.
        _migrate_nested_references(
            entry.get("fill_rule"),
            location=f"{location}.fill_rule",
            label=label,
            pdf_name=pdf_name,
            audits=audits,
        )
        _migrate_nested_references(
            entry.get("checkbox_group"),
            location=f"{location}.checkbox_group",
            label=label,
            pdf_name=pdf_name,
            audits=audits,
        )
        if legacy is None:
            continue
        translation, reason = _resolve_entry(legacy, label, pdf_name)
        if reason is not None:
            audits["unresolved"].append(
                {
                    "location": location,
                    "pdf_field_name": pdf_name,
                    "label": label,
                    "legacy": legacy,
                    "reason": reason,
                }
            )
            continue
        if translation is None:
            continue
        _apply_translation(
            entry,
            legacy,
            translation,
            location=location,
            pdf_name=pdf_name,
            label=label,
            audits=audits,
        )

    groups = mapping.get("character_groups") if isinstance(mapping, dict) else None
    if isinstance(groups, list):
        field_lookup = {
            field.get("pdf_field_name"): field
            for field in fields
            if isinstance(field, dict)
        }
        for index, group in enumerate(groups):
            if not isinstance(group, dict):
                continue
            legacy = group.get("canonical_field")
            if legacy is None:
                continue
            location = f"character_groups[{index}]"
            label, pdf_name = _group_context(group, field_lookup)
            translation, reason = _resolve_entry(legacy, label, pdf_name)
            if reason is not None:
                audits["unresolved"].append(
                    {
                        "location": location,
                        "pdf_field_name": pdf_name,
                        "label": label,
                        "legacy": legacy,
                        "reason": reason,
                    }
                )
                continue
            if translation is None:
                continue
            before = json.dumps(group.get("canonical_field"), ensure_ascii=False)
            _apply_translation(
                group,
                legacy,
                translation,
                location=location,
                pdf_name=pdf_name,
                label=label,
                audits=audits,
            )
            after = json.dumps(group.get("canonical_field"), ensure_ascii=False)
            if before != after:
                audits["group_changes"].append(
                    {
                        "location": location,
                        "group_id": group.get("group_id"),
                        "legacy": legacy,
                        "canonical": group.get("canonical_field"),
                    }
                )

    # Cutover Task 3 (additional requirement 1): canonical path references
    # inside point rules / params (and their block metadata), wherever present.
    points = mapping.get("points") if isinstance(mapping, dict) else None
    if isinstance(points, list):
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                continue
            _migrate_nested_references(
                point,
                location=f"points[{index}]",
                label=str(point.get("label") or ""),
                pdf_name=str(point.get("point_id") or ""),
                audits=audits,
            )

    return MigrationResult(
        mapping=mapping,
        pdf_path=pdf_path,
        changed_count=audits["changed_count"],
        unresolved=audits["unresolved"],
        semantic_corrections=audits["semantic_corrections"],
        fragment_changes=audits["fragment_changes"],
        group_changes=audits["group_changes"],
        product_gates=audits["product_gates"],
        nested_changes=audits["nested_changes"],
    )


# ──────────────────────────────────────────────────────────────────────────
# CLI: dry-run audit and guarded apply
# ──────────────────────────────────────────────────────────────────────────


def mapping_files(mapping_dir: Path = MAPPING_DIR) -> list[Path]:
    """Return every active mapping document (JSON with a ``fields`` list)."""

    files = []
    for path in sorted(Path(mapping_dir).glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(document, dict) and isinstance(document.get("fields"), list):
            files.append(path)
    return files


def _select_files(files: list[Path], only: list[str] | None) -> list[Path] | None:
    if not only:
        return files
    by_name = {path.name: path for path in files}
    by_stem = {path.stem: path for path in files}
    by_form = {
        path.stem.removesuffix("_mapping"): path for path in files
    }
    selected = []
    for name in only:
        path = by_name.get(name) or by_stem.get(name) or by_form.get(name)
        if path is None:
            print(f"error: no mapping matches --only {name!r}", file=sys.stderr)
            return None
        if path not in selected:
            selected.append(path)
    return sorted(selected)


def _build_report(
    results: list[tuple[Path, MigrationResult]], *, mode: str, mapping_dir: Path
) -> dict:
    totals = {
        "mappings": len(results),
        "entries_changed": 0,
        "unresolved": 0,
        "semantic_corrections": 0,
        "fragment_changes": 0,
        "group_changes": 0,
        "product_gates": 0,
        "nested_changes": 0,
    }
    mappings = []
    for path, result in results:
        mappings.append(
            {
                "file": path.name,
                "form_name": result.mapping.get("form_name"),
                "pdf_path": result.pdf_path,
                "total_fields": len(result.mapping.get("fields") or []),
                "changed_count": result.changed_count,
                "unresolved": result.unresolved,
                "semantic_corrections": result.semantic_corrections,
                "fragment_changes": result.fragment_changes,
                "group_changes": result.group_changes,
                "product_gates": result.product_gates,
                "nested_changes": result.nested_changes,
            }
        )
        totals["entries_changed"] += result.changed_count
        totals["unresolved"] += len(result.unresolved)
        totals["semantic_corrections"] += len(result.semantic_corrections)
        totals["fragment_changes"] += len(result.fragment_changes)
        totals["group_changes"] += len(result.group_changes)
        totals["product_gates"] += len(result.product_gates)
        totals["nested_changes"] += len(result.nested_changes)
    return {
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mapping_dir": str(mapping_dir),
        "mappings": mappings,
        "totals": totals,
    }


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Migrate legacy canonical_field values in mapping JSON files to "
            "Genie canonical paths. Dry-run audit by default; --apply is "
            "guarded and refuses to run twice."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="audit only; no mapping file is modified (default)",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write migrated mappings in place; aborts on unresolved entries",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="write the JSON audit report to this path",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        default=None,
        metavar="NAME",
        help="restrict to mapping file names or stems",
    )
    parser.add_argument(
        "--mapping-dir",
        type=Path,
        default=MAPPING_DIR,
        help=argparse.SUPPRESS,  # test/future-activation hook, defaults to src/mapping
    )
    args = parser.parse_args(argv)

    try:
        files = _select_files(mapping_files(args.mapping_dir), args.only)
    except (OSError, json.JSONDecodeError) as error:
        print(f"error: cannot read mappings: {error}", file=sys.stderr)
        return 1
    if not files:
        return 1

    results = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        results.append((path, migrate_mapping(data)))

    mode_name = "apply" if args.apply else "dry-run"
    report = _build_report(results, mode=mode_name, mapping_dir=args.mapping_dir)
    totals = report["totals"]

    if args.apply:
        if totals["unresolved"]:
            report_path = args.report or DEFAULT_REPORT_PATH
            _write_report(report_path, report)
            print(
                f"apply aborted: {totals['unresolved']} unresolved legacy "
                f"entries; nothing written; report: {report_path}"
            )
            return 2
        if totals["entries_changed"] == 0:
            if args.report is not None:
                _write_report(args.report, report)
            print(
                "apply refused: every selected mapping is already migrated; "
                "nothing to do"
            )
            return 3
        written = 0
        for path, result in results:
            if result.changed_count == 0:
                continue
            path.write_text(
                json.dumps(result.mapping, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            written += 1
        if args.report is not None:
            _write_report(args.report, report)
        print(
            f"mode=apply mappings={totals['mappings']} files_written={written} "
            f"entries_changed={totals['entries_changed']} unresolved=0"
        )
        return 0

    if args.report is not None:
        _write_report(args.report, report)
    print(
        f"mode=dry-run mappings={totals['mappings']} "
        f"entries_changed={totals['entries_changed']} "
        f"unresolved={totals['unresolved']} "
        f"semantic_corrections={totals['semantic_corrections']} "
        f"fragment_changes={totals['fragment_changes']} "
        f"group_changes={totals['group_changes']} "
        f"product_gates={totals['product_gates']} "
        f"nested_changes={totals['nested_changes']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

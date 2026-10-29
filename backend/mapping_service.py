"""
Mapping service: load/save/update the src/mapping/*.json files.

The mapping JSON format is the existing one produced by FieldRecognizer
(see src/ai/field_recognizer.py MappingConfig.to_dict). This service extends
it transparently with an optional top-level "character_groups" array
(spec §9.3) without breaking the existing pipeline loader.

All field records exchanged with the frontend use this shape:
    {
      "pdf_field_name": str,
      "label": str,
      "field_type": str,            # text|number|date|checkbox|dropdown|money|character_split
      "canonical_field": str | None,
      "confidence": str,            # high|medium|low|manual
      "page_number": int,
      "coordinates": {"x","y","width","height"} | None,
      "notes": str | None,
    }
"""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from config import MAPPING_DIR, log

# Cutover Task 4: the canonical catalog is the shared Genie registry
# (src/canonical/fields.json) — the recognizer's static Salesforce-named
# CANONICAL_FIELDS dict and the SF_FIELD_TYPES type table are deleted.
from src.canonical.registry import canonical_registry

# PLAN_CHECKBOX_GROUPS.md §3.5 — checkbox-csoport konzisztencia validáló.
# Importolt modul-szintű függvény; nem blokkolja a mentést, csak jelez.
try:
    from ai.field_recognizer import _validate_checkbox_groups  # type: ignore
except Exception:  # pragma: no cover - defensive
    _validate_checkbox_groups = None


_LOCK = threading.Lock()

#: Fragment roles the fill engine understands (src.main._apply_fragment):
#: date pieces + 1-based digit boxes. Anything else is a stale mapping artifact.
_DATE_FRAGMENTS = frozenset({"year", "month", "day"})
_DIGIT_FRAGMENT_RE = re.compile(r"digit:[1-9][0-9]*\Z")


class InvalidCanonicalFieldError(ValueError):
    """A mapping document carries a non-registry canonical path."""


class InvalidFragmentError(ValueError):
    """A mapping field carries a fragment role the fill engine cannot apply."""


def _canonical_references(value: Any, location: str = "") -> Iterator[tuple[Any, str]]:
    """Yield ``(value, location)`` for every canonical-carrying key, deeply.

    Mirrors the migration audit's occurrence walk: any ``sf_field`` key or key
    ending in ``canonical_field`` (top-level fields, nested ``fill_rule`` /
    checkbox metadata, ``character_groups``, ``points[]`` params) must hold a
    registry-approved Genie path or None.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}" if location else key
            if key == "sf_field" or key.endswith("canonical_field"):
                yield child, child_location
            else:
                yield from _canonical_references(child, child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_location = f"{location}[{index}]"
            yield from _canonical_references(child, child_location)


def _validate_fragment_role(fragment: Any, location: str) -> None:
    if fragment is None:
        return
    valid = (
        isinstance(fragment, str)
        and (fragment in _DATE_FRAGMENTS or _DIGIT_FRAGMENT_RE.fullmatch(fragment))
    )
    if not valid:
        raise InvalidFragmentError(
            f"Érvénytelen fragment {fragment!r} itt: {location} — "
            f"engedélyezett: year | month | day | digit:N (1-alapú). "
            "A régi '#ev/#ho/#nap' canonical suffixok a migrációval fragment "
            "metadata-ra váltottak."
        )


class MappingService:
    """CRUD over mapping JSON files."""

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def load(self, pdf_id: str) -> dict:
        """Load a mapping; return an empty template if file is missing."""
        path = self._mapping_path(pdf_id)
        if not path.exists():
            return self._empty_template(pdf_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log.error("mapping load failed (%s): %s", path, e)
            return self._empty_template(pdf_id)

        # Backfill any missing top-level keys so the frontend always sees a
        # consistent shape.
        data.setdefault("bank_name", "")
        data.setdefault("form_name", Path(pdf_id).stem)
        data.setdefault("form_type", "acroform")
        data.setdefault("approved", False)
        data.setdefault("approved_by", None)
        data.setdefault("notes", None)
        data.setdefault("page_structure", {})
        data.setdefault("fields", [])
        data.setdefault("character_groups", [])
        data.setdefault("points", [])
        data["_mapping_file"] = path.relative_to(MAPPING_DIR.parent.parent).as_posix()
        return data

    def save(self, pdf_id: str, mapping: dict, original_mtime: float | None = None) -> dict:
        """Persist the full mapping. Returns {"saved_at", "mtime", "path"}.

        Cutover Task 4: the full document is validated against the shared
        Genie registry BEFORE anything is written — every canonical-carrying
        reference (fields, character_groups, nested fill_rule/point sf_field)
        must be a registry-approved path and every fragment role must be one
        the fill engine applies. Legacy/invalid values raise
        InvalidCanonicalFieldError / InvalidFragmentError (the API maps both
        to a 422 with an operator-facing message); nothing partial is saved.
        """
        path = self._mapping_path(pdf_id)
        # Conflict detection: if the file changed on disk since we loaded it.
        if path.exists() and original_mtime is not None:
            current = path.stat().st_mtime
            if abs(current - original_mtime) > 0.001:
                raise FileConflictError(
                    f"Mapping file was modified externally (disk mtime {current}, "
                    f"expected {original_mtime}). Reload first."
                )

        # Strip editor-only metadata before writing.
        clean = {k: v for k, v in mapping.items() if not k.startswith("_")}
        clean.setdefault("character_groups", [])
        clean["approved"] = bool(clean.get("approved", False))
        if clean.get("form_type") not in ("acroform", "flat"):
            clean["form_type"] = "acroform"

        self.validate_mapping_document(clean)

        MAPPING_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

        mtime = path.stat().st_mtime
        log.info("mapping saved: %s (%d fields, %d char groups)",
                 path, len(clean.get("fields", [])), len(clean.get("character_groups", [])))

        # PLAN_CHECKBOX_GROUPS.md §5.1 — opcionális checkbox-csoport
        # konzisztencia ellenőrzés a mentés után. Nem blokkolja a mentést,
        # csak logger.warning-ot jelez, hogy a review során látszódjon.
        if _validate_checkbox_groups is not None:
            try:
                errs = _validate_checkbox_groups(clean.get("fields", []))
                for e in errs:
                    log.warning("checkbox_group: %s", e)
            except Exception as exc:  # pragma: no cover - defensive
                log.debug("checkbox_group validation skipped: %s", exc)

        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "mtime": mtime,
            "_mtime": mtime,
            "path": str(path.relative_to(MAPPING_DIR.parent.parent)),
        }

    # ------------------------------------------------------------------
    # Field-level CRUD (operates on the in-memory mapping dict)
    # ------------------------------------------------------------------
    def update_field(self, mapping: dict, name: str, update: dict) -> dict:
        for f in mapping.get("fields", []):
            if f.get("pdf_field_name") == name:
                for k, v in update.items():
                    f[k] = v
                return f
        raise KeyError(f"field not found: {name}")

    def add_field(self, mapping: dict, field: dict) -> dict:
        fields = mapping.setdefault("fields", [])
        name = field["pdf_field_name"]
        if any(f.get("pdf_field_name") == name for f in fields):
            raise ValueError(f"duplicate field name: {name}")
        # FIX L2 — mirror the C2 FieldUpdate behaviour: persist fill_rule and
        # checkbox_group so newly added fields (checkbox groups / static
        # rules) survive the create round-trip instead of being dropped.
        record = {
            "pdf_field_name": name,
            "label": field.get("label", name),
            "field_type": field.get("field_type", "text"),
            "canonical_field": field.get("canonical_field"),
            "confidence": field.get("confidence", "manual"),
            "page_number": field.get("page_number", 1),
            "coordinates": field.get("coordinates"),
            "notes": field.get("notes"),
            "fill_rule": field.get("fill_rule"),
            "checkbox_group": field.get("checkbox_group"),
        }
        fields.append(record)
        return record

    def delete_field(self, mapping: dict, name: str) -> bool:
        fields = mapping.get("fields", [])
        before = len(fields)
        mapping["fields"] = [f for f in fields if f.get("pdf_field_name") != name]
        return len(mapping["fields"]) < before

    # ------------------------------------------------------------------
    # Character groups (spec §6, §9.3)
    # ------------------------------------------------------------------
    def create_group(self, mapping: dict, req: dict) -> dict:
        groups = mapping.setdefault("character_groups", [])
        group_id = req.get("group_id") or req.get("group_name") or self._next_group_id(groups)
        if any(g.get("group_id") == group_id for g in groups):
            raise ValueError(f"group already exists: {group_id}")

        members = list(req.get("member_fields", []))
        if not members:
            raise ValueError("character group must have at least one member field")

        group = {
            "group_id": group_id,
            "group_name": req.get("group_name", group_id),
            "field_type": "character_split",
            "canonical_field": req.get("canonical_field"),
            "member_fields": members,
            "direction": req.get("direction", "left_to_right"),
            "separator": req.get("separator", ""),
        }
        groups.append(group)
        return group

    def update_group(self, mapping: dict, group_id: str, req: dict) -> dict:
        groups = mapping.get("character_groups", [])
        for g in groups:
            if g.get("group_id") == group_id:
                for k in ("group_name", "canonical_field", "member_fields", "direction", "separator", "field_type"):
                    if k in req:
                        g[k] = req[k]
                if not g.get("member_fields"):
                    raise ValueError("character group must have at least one member field")
                return g
        raise KeyError(f"group not found: {group_id}")

    def delete_group(self, mapping: dict, group_id: str) -> bool:
        groups = mapping.get("character_groups", [])
        before = len(groups)
        mapping["character_groups"] = [g for g in groups if g.get("group_id") != group_id]
        return len(mapping["character_groups"]) < before

    def suggest_groups(self, pdf_fields: list[dict]) -> list[dict]:
        """
        Heuristic auto-detection of potential character-split groups
        (spec §6.2). Looks for sequences of similarly-named, adjacent fields
        of the same type with sequential numeric suffixes.
        """
        import re

        # Group candidates by (page, type, base-without-trailing-digit).
        buckets: dict[tuple, list[dict]] = {}
        for f in pdf_fields:
            name = f.get("pdf_field_name", "")
            m = re.match(r"^(.*?)[-_ ]?(\d+)\s*$", name)
            if not m:
                continue
            base = m.group(1)
            try:
                idx = int(m.group(2))
            except ValueError:
                continue
            page = f.get("page_number", 1)
            ftype = f.get("field_type", "text")
            # Skip if there's only one index in this base on this page (handled later).
            buckets.setdefault((base, page, ftype), []).append({**f, "_idx": idx})

        suggestions = []
        for (base, page, ftype), items in buckets.items():
            if len(items) < 3:
                continue
            items.sort(key=lambda x: x["_idx"])
            idxs = [it["_idx"] for it in items]
            # Sequential or near-sequential run?
            if idxs == list(range(min(idxs), max(idxs) + 1)):
                # Physical adjacency sanity check (similar Y, sequential X).
                xs = sorted(it["rect"]["x"] for it in items if it.get("rect"))
                ys = [it["rect"]["y"] for it in items if it.get("rect")]
                y_spread = (max(ys) - min(ys)) if ys else 0
                if y_spread <= max(30.0, (items[0].get("rect", {}).get("height", 0) * 2)):
                    suggestions.append(
                        {
                            "group_id": f"suggested_{base}_{page}".replace(" ", "_"),
                            "group_name": base,
                            "field_type": "character_split",
                            "canonical_field": None,
                            "member_fields": [it["pdf_field_name"] for it in items],
                            "direction": "left_to_right",
                            "separator": "",
                            "page_number": page,
                            "suggested": True,
                        }
                    )
        return suggestions

    # ------------------------------------------------------------------
    # Canonical catalog (shared Genie registry)
    # ------------------------------------------------------------------
    def canonical_fields(self) -> list[dict]:
        """Registry entries with type/label/path metadata (no legacy paths).

        Cutover Task 4: the full concrete expansion of the shared Genie
        registry (thousands of indexed participant/property/income/
        existing_loan paths) in deterministic registry order. The static
        SF_FIELD_TYPES table is deleted — ``type`` is the registry's own
        scalar type (string|integer|number|boolean|date|money).

        Review fix 4: the registry expansion is immutable, so the payload is
        built ONCE (module-level cache) and the same list object is served
        on every request — callers must treat it as read-only.
        """
        return _canonical_fields_payload()


    # ------------------------------------------------------------------
    # Registry validation (consistent with src/canonical parser)
    # ------------------------------------------------------------------
    def validate_mapping_document(self, mapping: dict) -> None:
        """Validate every canonical reference and fragment role in a document.

        Raises:
            InvalidCanonicalFieldError: a canonical value is not a
                registry-approved Genie path (legacy Salesforce name,
                ``-társ`` suffix, ``#gate`` suffix, invented namespace, …).
            InvalidFragmentError: a ``fragment`` is not year|month|day|digit:N.
        """
        invalid: list[str] = []
        for value, location in _canonical_references(mapping):
            if value is None or value == "":
                continue
            if not isinstance(value, str) or not canonical_registry.validate(value):
                invalid.append(f"{location}: {value!r}")

        if invalid:
            raise InvalidCanonicalFieldError(
                "A mapping elavult vagy ismeretlen canonical értékeket hordoz "
                "(a pipeline kizárólag registry-approved Genie útvonalakat "
                "fogad): " + "; ".join(invalid) + ". Futtasd a "
                "scripts/migrate_mappings_to_genie.py --apply migrációt, vagy "
                "javítsd a mezőket a Mapping Studio-ban."
            )

        fields = mapping.get("fields") or []
        for index, field in enumerate(fields):
            if isinstance(field, dict):
                _validate_fragment_role(
                    field.get("fragment"), f"fields[{index}].fragment"
                )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _mapping_path(self, pdf_id: str) -> Path:
        from config import mapping_path_for

        return mapping_path_for(pdf_id)

    def _empty_template(self, pdf_id: str) -> dict:
        return {
            "bank_name": "",
            "form_name": Path(pdf_id).stem,
            "form_type": "acroform",
            "approved": False,
            "approved_by": None,
            "notes": None,
            "page_structure": {},
            "fields": [],
            "character_groups": [],
            "points": [],
            "_mapping_file": None,
        }

    def _next_group_id(self, groups: list[dict]) -> str:
        i = 1
        existing = {g.get("group_id") for g in groups}
        while f"char_group_{i}" in existing:
            i += 1
        return f"char_group_{i}"


def _canonical_fields_payload() -> list[dict]:
    """Build the canonical-fields endpoint payload once (cached).

    Review fix 4: the registry expansion is immutable, so this list is built
    on first use and shared by every request; callers must treat it (and its
    dicts) as read-only.
    """
    global _CANONICAL_FIELDS_CACHE
    if _CANONICAL_FIELDS_CACHE is None:
        _CANONICAL_FIELDS_CACHE = [
            {
                "path": field["path"],
                "label": field["label"],
                "type": field["type"],
                "source": field["source"],
                "repeat": field["repeat"],
            }
            for field in canonical_registry.canonical_fields()
        ]
    return _CANONICAL_FIELDS_CACHE


#: Immutable-after-build cache for the canonical-fields endpoint payload.
_CANONICAL_FIELDS_CACHE: list[dict] | None = None


class FileConflictError(Exception):
    """Raised when the on-disk mapping file was modified externally."""


mapping_service = MappingService()

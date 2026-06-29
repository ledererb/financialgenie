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
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from config import MAPPING_DIR, log

# Canonical field catalog imported from the existing AI module so the
# frontend autocomplete dropdown always matches the pipeline's vocabulary.
try:
    from ai.field_recognizer import CANONICAL_FIELDS  # type: ignore
except Exception:  # pragma: no cover - defensive
    CANONICAL_FIELDS = {}


_LOCK = threading.Lock()


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
        data["_mapping_file"] = path.relative_to(MAPPING_DIR.parent.parent).as_posix()
        return data

    def save(self, pdf_id: str, mapping: dict, original_mtime: float | None = None) -> dict:
        """Persist the full mapping. Returns {"saved_at", "mtime", "path"}."""
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

        MAPPING_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            tmp = path.with_suffix(path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(clean, f, ensure_ascii=False, indent=2)
            tmp.replace(path)

        mtime = path.stat().st_mtime
        log.info("mapping saved: %s (%d fields, %d char groups)",
                 path, len(clean.get("fields", [])), len(clean.get("character_groups", [])))
        return {
            "saved_at": datetime.now().isoformat(timespec="seconds"),
            "mtime": mtime,
            "path": str(path.relative_to(MAPPING_DIR.parent.parent)),
        }

    # ------------------------------------------------------------------
    # Field-level CRUD (operates on the in-memory mapping dict)
    # ------------------------------------------------------------------
    def update_field(self, mapping: dict, name: str, update: dict) -> dict:
        for f in mapping.get("fields", []):
            if f.get("pdf_field_name") == name:
                for k in ("canonical_field", "field_type", "confidence", "notes", "coordinates"):
                    if k in update:
                        f[k] = update[k]
                return f
        raise KeyError(f"field not found: {name}")

    def add_field(self, mapping: dict, field: dict) -> dict:
        fields = mapping.setdefault("fields", [])
        name = field["pdf_field_name"]
        if any(f.get("pdf_field_name") == name for f in fields):
            raise ValueError(f"duplicate field name: {name}")
        record = {
            "pdf_field_name": name,
            "label": field.get("label", name),
            "field_type": field.get("field_type", "text"),
            "canonical_field": field.get("canonical_field"),
            "confidence": field.get("confidence", "manual"),
            "page_number": field.get("page_number", 1),
            "coordinates": field.get("coordinates"),
            "notes": field.get("notes"),
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
    # Canonical catalog
    # ------------------------------------------------------------------
    def canonical_fields(self) -> list[dict]:
        return [
            {"path": path, "label": label}
            for path, label in sorted(CANONICAL_FIELDS.items())
        ]

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
            "_mapping_file": None,
        }

    def _next_group_id(self, groups: list[dict]) -> str:
        i = 1
        existing = {g.get("group_id") for g in groups}
        while f"char_group_{i}" in existing:
            i += 1
        return f"char_group_{i}"


class FileConflictError(Exception):
    """Raised when the on-disk mapping file was modified externally."""


mapping_service = MappingService()

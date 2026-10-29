"""Deterministic rebuild of the ``altalanos_nyilatkozatok`` declaration mapping.

Cutover Task 5 — Safe declaration rebuild and legal-rule table.

The rebuild derives the FIELD TYPES and the reviewed role/signature POINTS
from the actual AcroForm widgets of
``documents/otp_bank/split/altalanos_nyilatkozatok.pdf``:

* every mapping entry keeps its existing ``pdf_field_name``, ``label``,
  ``page_number``, ``coordinates``, ``confidence`` and other metadata
  (preserve-by-contract), but its ``field_type`` becomes the TRUTHFUL type
  of the widget it resolves to — 207 checkbox widgets + 5 text fields;
* ``base___state`` virtual names stay 1:1 with the widget whose single
  on-state is ``state`` (radio-style export-state addressing);
* every checkbox entry becomes canonical-less: signature-column ticks are
  driven by role membership (points/rule 2), never by deal booleans or
  citizenship values. The ``A NY_kelt`` text field keeps its explicit
  ``participant.borrower.date_of_signature`` path;
* reviewed role/signature points (rule 2, indexed role addressing:
  ``adós``, ``adóstárs_1..3``, ``zálogkötelezett_1..2``,
  ``haszonélvező_1..2``) are generated ONLY for the groups in ALLOWLIST —
  pure acknowledgement points that every signing participant must tick.
  Consents (felhatalmazás / hozzájárulás), KHR / NAV choices, currency,
  legal-capacity, personal-status and conditional declarations stay
  canonical-less and MANUAL (no automatic value from any category).

The script is deterministic and idempotent: same PDF + same source mapping
in, byte-identical mapping out. It refuses to run when the mapping and the
PDF widgets do not form a 1:1 bijection.

CLI:
    python3 scripts/rebuild_altalanos_nyilatkozatok_mapping.py --dry-run
    python3 scripts/rebuild_altalanos_nyilatkozatok_mapping.py --apply
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = PROJECT_ROOT / "documents" / "otp_bank" / "split" / "altalanos_nyilatkozatok.pdf"
DEFAULT_MAPPING = (
    PROJECT_ROOT / "src" / "mapping" / "altalanos_nyilatkozatok_mapping.json"
)

#: Expected widget shape of the source PDF (asserted, never guessed).
EXPECTED_WIDGET_COUNTS = {"CheckBox": 207, "Text": 5}

#: Rows of the same role grid that are closer than this many points belong to
#: the same numbered declaration point (applicant row + collateral row).
ROW_MERGE_GAP_PT = 30.0


class RebuildError(RuntimeError):
    """The mapping and the PDF widgets cannot be reconciled."""


@dataclass
class Widget:
    name: str
    page: int
    type: str  # "CheckBox" | "Text"
    x: float
    y: float
    on_states: list[str] = dc_field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────
# Role-column addressing
# ──────────────────────────────────────────────────────────────────────────

#: Role token → canonical role KIND (unindexed). The occurrence index is NOT
#: taken from the name digit: Acrobat renames duplicated field names (e.g.
#: ``A_NY_3_társigénylő_3`` sits in the 2nd co-applicant column and
#: ``A_NY_3_társigénylő_3-2`` in the 3rd), so the index is derived from the
#: column order inside the point instead. Order matters (longest first).
_ROLE_TOKENS: list[tuple[str, str]] = [
    # collateral owner (zálogkötelezett) columns
    (r"fedezet_tulajdonosa(?:_\d+)?", "zálogkötelezett"),
    (r"fedezet_tulaj(?:_\d+)?", "zálogkötelezett"),
    (r"fedezet\ tul\d+", "zálogkötelezett"),
    # usufructuary (haszonélvező) columns
    (r"fedezet_haszonélvezője(?:_\d+)?", "haszonélvező"),
    (r"fedezet_haszonélvező(?:_\d+)?", "haszonélvező"),
    (r"haszonelvezo\d+", "haszonélvező"),
    # loan applicant (adós) column
    (r"kölcsönigénylő", "adós"),
    (r"kolcsonigenylo", "adós"),
    (r"Igénylő", "adós"),
    (r"Igenylo", "adós"),
    # co-applicant (adóstárs) columns
    (r"társigénylő(?:_\d+)?", "adóstárs"),
    (r"tarsigenylo(?:_\d+)?", "adóstárs"),
    (r"Társgénylő(?:_\d+)?", "adóstárs"),
    (r"társ_\d+", "adóstárs"),
]

#: Characters that may trail a role token in a widget name (AcroForm naming
#: junk like ``A_NY_15_társigénylő_1.2-2`` or ``..._kölcsönigénylő 2``).
_JUNK_TAIL = re.compile(r"^[\s.\-/0-9]*$")

_LETTER = re.compile(r"[A-Za-zÀ-ž]")


def _role_kind(name: str) -> tuple[str, str] | None:
    """Split a widget name into ``(prefix, role_kind)``.

    Returns None when no clearly-identified role column ends the name.
    Matching is case-insensitive (the form mixes ``társigénylő`` and
    ``Társigénylő`` column spellings across pages).
    """
    for pattern, kind in _ROLE_TOKENS:
        for m in re.finditer(pattern, name, re.IGNORECASE):
            start, end = m.span()
            # The character before the role token must not be a letter, so a
            # prefix glued to the token (``A_NY_17társigénylő_1``) still
            # splits correctly at the digit boundary.
            if start > 0 and _LETTER.match(name[start - 1]):
                continue
            if not _JUNK_TAIL.match(name[end:]):
                continue
            prefix = name[:start].rstrip("_ ")
            if not prefix:
                return None
            return prefix, kind
    return None


# ──────────────────────────────────────────────────────────────────────────
# Reviewed role/signature points (the ONLY automatically tickable groups)
# ──────────────────────────────────────────────────────────────────────────

#: group_id → review rationale. The rebuild generates rule-2 points for
#: exactly these groups. Group id format: ``<prefix>@p<page>#<cluster>``
#: where the cluster index counts the y-clusters of the prefix on the page
#: top→bottom.
ALLOWLIST: dict[str, str] = {
    "A_NY_2@p1#1": (
        "1. pont – Előzetes tájékoztatás (tudomásulvétel): pure "
        "acknowledgement of the mandatory pre-application information; "
        "every signing participant ticks their own column."
    ),
    "A_NY_3@p2#1": (
        "2. pont – Adatkezelési tájékoztatás tudomásulvétele: pure "
        "acknowledgement (megismertem) of the data-processing notice; "
        "no consent choice is made here."
    ),
    "A_NY_211014_1@p4#1": (
        "9. pont – hitelképesség-vizsgálati információk tudomásulvétele: "
        "pure acknowledgement of the credit-assessment information notice."
    ),
    "A_NY_15@p5#2": (
        "14. pont – fogyatékossággal élő ügyfelek tájékoztatása: pure "
        "acknowledgement that the accessibility information was provided."
    ),
}

#: Everything NOT in ALLOWLIST stays manual, in particular: 3./4./5./11./15a.
#: (felhatalmazás/hozzájárulás = consent → explicit CRM consent path only),
#: KHR (21. + 20. pont), NAV, Pénznem, cselekvőképesség (igen/nem), 7. pont
#: (vagyok/nem vagyok), 8./10./12./18./19. (conditional / personal-status
#: declarations) and the adósságrendezési criminal declaration.


# ──────────────────────────────────────────────────────────────────────────
# Widget extraction
# ──────────────────────────────────────────────────────────────────────────


def extract_widgets(pdf_path: Path) -> list[Widget]:
    import fitz

    widgets: list[Widget] = []
    doc = fitz.open(pdf_path)
    for page in doc:
        for w in page.widgets():
            states: list[str] = []
            if w.field_type_string == "CheckBox":
                states = [
                    s for s in (w.button_states().get("normal") or []) if s != "Off"
                ]
            widgets.append(
                Widget(
                    name=w.field_name,
                    page=page.number + 1,
                    type=w.field_type_string,
                    x=round(w.rect.x0, 2),
                    y=round(w.rect.y0, 2),
                    on_states=sorted(states),
                )
            )
    doc.close()

    counts: dict[str, int] = {}
    for w in widgets:
        counts[w.type] = counts.get(w.type, 0) + 1
    if counts != EXPECTED_WIDGET_COUNTS:
        raise RebuildError(
            f"widget type counts {counts} != expected {EXPECTED_WIDGET_COUNTS}"
        )
    return widgets


def _resolve_widget(entry_name: str, widgets: list[Widget]) -> Widget | None:
    """Resolve a mapping entry name to its single widget (1:1 bijection)."""
    if "___" in entry_name:
        base, suffix = entry_name.rsplit("___", 1)
        matches = [w for w in widgets if w.name == base and suffix in w.on_states]
        return matches[0] if len(matches) == 1 else None
    matches = [w for w in widgets if w.name == entry_name]
    return matches[0] if len(matches) == 1 else None


# ──────────────────────────────────────────────────────────────────────────
# Point generation
# ──────────────────────────────────────────────────────────────────────────


def _candidate_groups(widgets: list[Widget]) -> dict[str, list[tuple[str, Widget]]]:
    """Group single-'Yes' role-column widgets into declaration points.

    A candidate widget is a checkbox whose base name has exactly one widget
    with the single on-state ``Yes`` (a pure acknowledgement tick — no
    igen/nem or option-choice structure).
    """
    by_name: dict[str, list[Widget]] = {}
    for w in widgets:
        by_name.setdefault(w.name, []).append(w)

    candidates: list[tuple[str, str, Widget]] = []  # (prefix, role_kind, widget)
    for name, group in by_name.items():
        if len(group) != 1:
            continue  # choice family: multiple widgets share the name
        w = group[0]
        if w.type != "CheckBox" or w.on_states != ["Yes"]:
            continue
        parsed = _role_kind(name)
        if parsed is None:
            continue
        prefix, kind = parsed
        candidates.append((prefix, kind, w))

    # Cluster rows: (page, prefix) → y-clusters separated by > ROW_MERGE_GAP_PT.
    rows: dict[tuple[int, str], dict[float, list[tuple[str, Widget]]]] = {}
    for prefix, kind, w in candidates:
        rows.setdefault((w.page, prefix), {}).setdefault(w.y, []).append((kind, w))

    groups: dict[str, list[tuple[str, Widget]]] = {}
    for (page, prefix), y_rows in sorted(rows.items()):
        ys = sorted(y_rows)
        clusters: list[list[float]] = [[ys[0]]]
        for y in ys[1:]:
            if y - clusters[-1][-1] <= ROW_MERGE_GAP_PT:
                clusters[-1].append(y)
            else:
                clusters.append([y])
        for seq, cluster in enumerate(clusters, start=1):
            group_id = f"{prefix}@p{page}#{seq}"
            members: list[tuple[str, Widget]] = []
            for y in cluster:
                members.extend(sorted(y_rows[y], key=lambda rw: rw[1].x))
            groups[group_id] = members
    return groups


def _build_points(widgets: list[Widget]) -> list[dict]:
    groups = _candidate_groups(widgets)
    unknown = set(ALLOWLIST) - set(groups)
    if unknown:
        raise RebuildError(f"allowlist groups missing from PDF: {sorted(unknown)}")

    points: list[dict] = []
    for group_id in sorted(ALLOWLIST):
        members = groups[group_id]
        # Indexed role addressing: the role KIND comes from the widget name;
        # the occurrence index comes from the member order inside the point —
        # y-cluster-major (rows top→bottom), then x left→right within the row
        # (see _candidate_groups) — Acrobat's renamed duplicate digits are
        # not trusted.
        kind_seen: dict[str, int] = {}
        role_to_ticks: dict[str, list[str]] = {}
        for kind, w in members:
            kind_seen[kind] = kind_seen.get(kind, 0) + 1
            role_key = kind if kind == "adós" else f"{kind}_{kind_seen[kind]}"
            bucket = role_to_ticks.setdefault(role_key, [])
            if w.name in bucket:
                raise RebuildError(f"duplicate member {w.name} in {group_id}")
            bucket.append(w.name)
        if kind_seen.get("adós", 0) > 1:
            raise RebuildError(f"multiple adós columns in {group_id}")
        # Structural guard: every reviewed acknowledgement point addresses
        # the applicant grid — a silently dropped column family (e.g. a new
        # name spelling) must abort the rebuild, not narrow the point.
        if kind_seen.get("adós", 0) != 1 or kind_seen.get("adóstárs", 0) < 1:
            raise RebuildError(
                f"reviewed group {group_id} lacks the applicant grid: {kind_seen}"
            )
        points.append(
            {
                "point_id": group_id,
                "framework": "*",
                "rule_type": 2,
                "blocks": [
                    {
                        "block_id": f"{group_id}.main",
                        "members": [w.name for _, w in members],
                    }
                ],
                "params": {"role_to_ticks": role_to_ticks},
                "review_note": ALLOWLIST[group_id],
            }
        )
    return points


# ──────────────────────────────────────────────────────────────────────────
# Rebuild
# ──────────────────────────────────────────────────────────────────────────

REBUILD_NOTE = (
    "Cutover Task 5 determinisztikus rebuild: a widget típusok és a felülvizsgált "
    "role/signature pontok a valódi AcroFormból származnak (207 checkbox + 5 text). "
    "A nem-role jogi választások (KHR/NAV/pénznem/cselekvőképesség/felhatalmazás/"
    "személyes állapotú nyilatkozatok) canonical-nélküliek és kézi döntések — "
    "kategória-alapú automatikus pipálás nincs. Újragenerálás: "
    "scripts/rebuild_altalanos_nyilatkozatok_mapping.py --apply."
)


def rebuild_mapping(pdf_path: Path | str, mapping: dict) -> dict:
    """Rebuild the mapping deterministically from the real PDF widgets.

    Preserves every field's name/label/page/coordinates/confidence and the
    top-level metadata; rewrites ``field_type`` truthfully, strips checkbox
    canonical mappings, and regenerates the reviewed rule-2 points.

    Raises:
        RebuildError: the mapping entries and the PDF widgets do not form a
            1:1 bijection, or the widget counts deviate from the expectation.
    """
    pdf_path = Path(pdf_path)
    widgets = extract_widgets(pdf_path)

    used: set[int] = set()
    new_fields: list[dict] = []
    for entry in mapping.get("fields", []):
        name = entry["pdf_field_name"]
        w = _resolve_widget(name, widgets)
        if w is None:
            raise RebuildError(f"mapping entry {name!r} has no single matching widget")
        if id(w) in used:
            raise RebuildError(f"widget of {name!r} matched by more than one entry")
        used.add(id(w))
        if entry.get("page_number") != w.page:
            raise RebuildError(
                f"page mismatch for {name!r}: mapping={entry.get('page_number')} pdf={w.page}"
            )
        new_fields.append(
            {
                "pdf_field_name": name,
                "label": entry["label"],
                "field_type": "checkbox" if w.type == "CheckBox" else "text",
                # Signature-column ticks are points-managed, never canonical.
                "canonical_field": None
                if w.type == "CheckBox"
                else entry.get("canonical_field"),
                "confidence": entry.get("confidence"),
                "page_number": entry.get("page_number"),
                "coordinates": entry.get("coordinates"),
                "notes": entry.get("notes"),
                "checkbox_group": entry.get("checkbox_group"),
                "fill_rule": entry.get("fill_rule"),
                "fragment": entry.get("fragment"),
                "product_gate": entry.get("product_gate"),
            }
        )
    if len(used) != len(widgets):
        raise RebuildError(
            f"bijection broken: {len(used)} entries cover {len(widgets)} widgets"
        )

    rebuilt = {
        "bank_name": mapping.get("bank_name"),
        "form_name": mapping.get("form_name"),
        "form_type": mapping.get("form_type"),
        "approved": mapping.get("approved", False),
        "approved_by": mapping.get("approved_by"),
        "notes": REBUILD_NOTE,
        "page_structure": mapping.get("page_structure", {}),
        "fields": new_fields,
        "character_groups": mapping.get("character_groups", []),
        "points": _build_points(widgets),
        "rule_sets": mapping.get("rule_sets", []),
    }
    _self_check(rebuilt)
    return rebuilt


def _self_check(rebuilt: dict) -> None:
    """Fail loudly if the rebuild output violates the safety contract."""
    fields = rebuilt["fields"]
    n_checkbox = sum(1 for f in fields if f["field_type"] == "checkbox")
    n_text = sum(1 for f in fields if f["field_type"] == "text")
    if (n_checkbox, n_text) != (207, 5):
        raise RebuildError(f"truthful type counts broken: {n_checkbox}/{n_text}")
    for f in fields:
        if f["field_type"] == "checkbox" and f["canonical_field"]:
            raise RebuildError(f"checkbox with canonical_field: {f['pdf_field_name']}")
    for p in rebuilt["points"]:
        if p["rule_type"] != 2:
            raise RebuildError(f"non-rule-2 point: {p['point_id']}")
        for blk in p["blocks"]:
            for m in blk["members"]:
                if "___" in m:
                    raise RebuildError(f"virtual choice name in points: {m}")


# ──────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="only report whether the rebuild differs (default)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the rebuilt mapping (default: dry-run)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 0 only if the on-disk mapping equals a fresh rebuild",
    )
    args = parser.parse_args(argv)

    with open(args.mapping, "r", encoding="utf-8") as f:
        current = json.load(f)

    rebuilt = rebuild_mapping(args.pdf, current)

    if args.check:
        if rebuilt != current:
            print("MISMATCH: on-disk mapping differs from a deterministic rebuild", file=sys.stderr)
            return 1
        print("OK: mapping is up to date with the PDF widgets")
        return 0

    out_path = args.output or args.mapping
    if args.apply:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(rebuilt, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"rebuilt mapping written: {out_path}")
    else:
        same = rebuilt == current
        print(
            f"dry-run: rebuild {'matches' if same else 'DIFFERS from'} the on-disk mapping "
            f"({len(rebuilt['fields'])} fields, {len(rebuilt['points'])} reviewed points)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

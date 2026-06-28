#!/usr/bin/env python3
"""
scripts/analyze_pdf.py – Generikus PDF analizáló.

Bármilyen banki nyomtatvány PDF szerkezetét kinyeri (AcroForm mezők,
lapos PDF placeholder-ek, oldalonkénti feliratok), és egy strukturált
JSON-t ír a stdout-ra. Célja, hogy gyors döntést támogasson arról,
milyen kitöltési módot (acroform / overlay / analyze_first) érdemes
használni az adott PDF-hez.

Használat:
    python3 scripts/analyze_pdf.py <path/to/pdf>

Kimenet: JSON a stdout-ra (példa):
    {
      "filename": "elozetes_ertekbecsles.pdf",
      "page_count": 18,
      "has_acroform": true,
      "acroform_fields": 1029,
      "field_names": ["Check Box 1", "Text Field 1", ...],
      "field_types": {"Check Box 1": "checkbox", "Text Field 1": "text"},
      "name_quality": "generic" | "descriptive" | "mixed",
      "flat_placeholders": {
        "underlines": 0,
        "checkbox_boxes": 0,
        "dots": 0
      },
      "sample_labels_per_page": [
        {"page": 1, "labels": ["Ingatlan címe", "Helyrajzi szám", ...]},
        ...
      ],
      "recommended_mode": "acroform" | "overlay" | "analyze_first",
      "guess": "A PDF valószínűleg egy OTP-s nyomtatvány, 18 oldal, 1029 generikus AcroForm mezővel."
    }
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import fitz

# Projekt gyökér – engedélyezi a `from scripts.analyze_flat_pdf import ...`
# formájú importot, ha valaki a projekt gyökeréből futtatja.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Flat PDF placeholder detektáló logika újrahasznosítése.
try:
    from scripts.analyze_flat_pdf import detect_placeholders as _detect_flat_placeholders
except Exception:  # pragma: no cover - csak hibás sys.path esetén
    _detect_flat_placeholders = None


# ---------------------------------------------------------------------------
# AcroForm mező detektálás
# ---------------------------------------------------------------------------

def _widget_type_name(widget) -> str:
    try:
        t = widget.field_type
    except Exception:
        return "unknown"
    mapping = {
        getattr(fitz, "PDF_WIDGET_TYPE_TEXT", 7): "text",
        getattr(fitz, "PDF_WIDGET_TYPE_CHECKBOX", 2): "checkbox",
        getattr(fitz, "PDF_WIDGET_TYPE_RADIOBUTTON", 5): "radio",
        getattr(fitz, "PDF_WIDGET_TYPE_LISTBOX", 4): "listbox",
        getattr(fitz, "PDF_WIDGET_TYPE_COMBOBOX", 3): "combobox",
        getattr(fitz, "PDF_WIDGET_TYPE_SIGNATURE", 6): "signature",
        getattr(fitz, "PDF_WIDGET_TYPE_BUTTON", 1): "button",
    }
    return mapping.get(t, f"type_{t}")


def _extract_acroform_fields(doc) -> tuple[list[str], dict[str, str]]:
    """
    Visszaadja az AcroForm mezők neveit és típusait.

    Returns:
        (field_names, field_types) ahol a field_types egy
        {field_name: type_string} dict.
    """
    names: list[str] = []
    types: dict[str, str] = {}
    seen: set[str] = set()

    for page in doc:
        try:
            widgets = list(page.widgets() or [])
        except Exception:
            widgets = []
        for w in widgets:
            try:
                name = w.field_name or ""
            except Exception:
                name = ""
            if not name:
                continue
            if name in seen:
                continue
            seen.add(name)
            names.append(name)
            types[name] = _widget_type_name(w)

    return names, types


# ---------------------------------------------------------------------------
# Mezőnév minőség becslése
# ---------------------------------------------------------------------------

_GENERIC_PATTERNS = [
    re.compile(r"^\s*(check box|text field|list box|combo box|radio button)\b", re.I),
    re.compile(r"^\s*(checkbox|text|field|mező|mezok|mezo)\s*\d*\s*$", re.I),
    re.compile(r"^\s*\d+\s*$"),
    re.compile(r"^\s*(field|text|cb|chk|fld)[-_\s]?\d+\s*$", re.I),
]


def _classify_name_quality(field_names: list[str]) -> str:
    """
    Becsli a mezőnevek minőségét:
      - "descriptive": a nevek hordoznak jelentést (pl. "Hiteligénylő neve")
      - "generic":     a nevek helyettesítők ("Text Field 1", "Check Box 42")
      - "mixed":        vegyes
    """
    if not field_names:
        return "descriptive"  # nincs AcroForm → nem releváns

    generic = 0
    descriptive = 0
    for n in field_names:
        is_generic = any(p.search(n) for p in _GENERIC_PATTERNS)
        if is_generic:
            generic += 1
        else:
            descriptive += 1

    total = len(field_names)
    if generic == 0:
        return "descriptive"
    if descriptive == 0:
        return "generic"
    # Vegyes eset: arány alapján döntünk
    if generic / total >= 0.7:
        return "generic"
    if descriptive / total >= 0.7:
        return "descriptive"
    return "mixed"


# ---------------------------------------------------------------------------
# Oldalonkénti feliratok mintavételezése
# ---------------------------------------------------------------------------

def _extract_block_labels(page, limit: int = 10) -> list[str]:
    """
    Kinyeri az oldal szövegblokkjait és visszatér egy rövid listával,
    amely a nyomtatvány feliratainak mintáját adja.

    A blokkok gyakran többsorosak; az első nem-üres sort tartjuk meg,
    levágjuk a felesleges whitespace-t.
    """
    labels: list[str] = []
    try:
        blocks = page.get_text("blocks") or []
    except Exception:
        return labels

    for b in blocks:
        if len(b) < 5:
            continue
        text = b[4] if isinstance(b[4], str) else ""
        # Első nem-üres sor
        first_line = ""
        for line in text.splitlines():
            line = line.strip()
            if line:
                first_line = line
                break
        if not first_line:
            continue
        # Túl rövid (1-2 karakter) fragmentumokat kihagyunk
        if len(first_line) < 3:
            continue
        labels.append(first_line[:120])
        if len(labels) >= limit:
            break
    return labels


# ---------------------------------------------------------------------------
# Flat placeholder statisztika
# ---------------------------------------------------------------------------

def _count_flat_placeholders(doc) -> dict[str, int]:
    """
    Visszaszámlálja az AcroForm nélküli (vagy azzal párhuzamosan lévő)
    lapos placeholder-eket minden oldalon.

    Returns:
        {"underlines": N, "checkbox_boxes": M, "dots": K}
    """
    stats = {"underlines": 0, "checkbox_boxes": 0, "dots": 0}

    if _detect_flat_placeholders is None:
        # Nem tudjuk újrahasznosíteni a logikát – közvetlenül fitz-zel
        # számoljuk meg a jellemző mintákat.
        for page in doc:
            try:
                stats["dots"] += len(page.search_for("…"))
            except Exception:
                pass
            try:
                stats["checkbox_boxes"] += len(page.search_for("□"))
            except Exception:
                pass
            try:
                # Legalább 3 egymás melletti aláhúzás
                stats["underlines"] += len(page.search_for("___"))
            except Exception:
                pass
        return stats

    # Újrahasznosított detektorral pontosabb számítás.
    for page_index, page in enumerate(doc):
        try:
            placeholders = _detect_flat_placeholders(page, page_index)
        except Exception:
            placeholders = []
        for p in placeholders:
            ptype = p.get("type")
            if ptype == "checkbox":
                stats["checkbox_boxes"] += 1
            elif ptype == "text":
                # A detektor nem különbozteti meg a `…` és `__` mintákat;
                # de a width alapján durva becslést adhatunk. Mivel a
                # feladat három kategóriát kér, a text placeholder-eket
                # az `…` és `__` keresések eredményeivel egészítjük ki.
                pass
        # Kiegészítő direkt keresések a pontosabb bontásért.
        try:
            stats["dots"] += len(page.search_for("…"))
        except Exception:
            pass
        try:
            stats["underlines"] += len(page.search_for("___"))
        except Exception:
            pass

    return stats


# ---------------------------------------------------------------------------
# Ajánlott mód + emberi tipp
# ---------------------------------------------------------------------------

def _recommend_mode(
    has_acroform: bool,
    acroform_count: int,
    name_quality: str,
    flat_stats: dict[str, int],
) -> str:
    """
    Heurisztikus javaslat a kitöltési módra.

      - "acroform":      érdemes AcroForm kitöltést használni
      - "overlay":       lapos PDF overlay koordinátákkal
      - "analyze_first": nem egyértelmű, mélyebb elemzés kell
    """
    flat_total = flat_stats["underlines"] + flat_stats["checkbox_boxes"] + flat_stats["dots"]

    if has_acroform and acroform_count > 0:
        # AcroForm van, de ha a nevek teljesen generikusak, az AI
        # klasszifikációt javasolhatunk. Még mindig az acroform kitöltő
        # a legjobb választás, csak a mapping mellé kell AI.
        return "acroform"

    if flat_total > 0:
        return "overlay"

    return "analyze_first"


def _build_guess(
    filename: str,
    page_count: int,
    has_acroform: bool,
    acroform_count: int,
    name_quality: str,
    flat_stats: dict[str, int],
    recommended_mode: str,
) -> str:
    """Emberi olvasásra szánt egy mondatos összefoglaló."""
    flat_total = flat_stats["underlines"] + flat_stats["checkbox_boxes"] + flat_stats["dots"]

    parts: list[str] = []
    parts.append(f"A PDF ({filename}, {page_count} oldal)")

    if has_acroform and acroform_count > 0:
        quality_hu = {
            "generic": "generikus",
            "descriptive": "beszédes",
            "mixed": "vegyes",
        }.get(name_quality, name_quality)
        parts.append(
            f"{acroform_count} {quality_hu} AcroForm mezővel rendelkezik"
        )
    else:
        if flat_total > 0:
            parts.append(
                f"nincs AcroForm-ja, de {flat_total} lapos placeholder "
                f"({flat_stats['dots']} pont, {flat_stats['underlines']} "
                f"aláhúzás, {flat_stats['checkbox_boxes']} checkbox) található"
            )
        else:
            parts.append("nem tartalmaz sem AcroForm mezőt, sem felismerhető placeholder-t")

    mode_hu = {
        "acroform": "AcroForm kitöltést",
        "overlay": "overlay (lapos PDF) kitöltést",
        "analyze_first": "előzetes mélyebb elemzést",
    }.get(recommended_mode, recommended_mode)
    parts.append(f"→ {mode_hu} javasolt")

    return ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Fő elemzés
# ---------------------------------------------------------------------------

def analyze_pdf(pdf_path: Path, label_sample_per_page: int = 10) -> dict:
    """
    Fő belépési pont: visszaadja a PDF strukturált analízisét egy dict-ben.

    Args:
        pdf_path: a PDF útvonala
        label_sample_per_page: ennyi feliratot tartunk meg oldalonként
    """
    import fitz  # biztonsági újbóli import (korábban már modul-szinten)

    if not pdf_path.exists():
        raise SystemExit(f"Nem található a PDF: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    page_count = len(doc)

    # AcroForm mezők
    field_names, field_types = _extract_acroform_fields(doc)
    has_acroform = bool(field_names)
    acroform_count = len(field_names)
    name_quality = _classify_name_quality(field_names)

    # Flat placeholder statisztika
    flat_stats = _count_flat_placeholders(doc)

    # Oldalonkénti feliratok
    sample_labels_per_page: list[dict] = []
    for i, page in enumerate(doc):
        labels = _extract_block_labels(page, limit=label_sample_per_page)
        if labels:
            sample_labels_per_page.append({"page": i + 1, "labels": labels})

    doc.close()

    recommended_mode = _recommend_mode(
        has_acroform=has_acroform,
        acroform_count=acroform_count,
        name_quality=name_quality,
        flat_stats=flat_stats,
    )
    guess = _build_guess(
        filename=pdf_path.name,
        page_count=page_count,
        has_acroform=has_acroform,
        acroform_count=acroform_count,
        name_quality=name_quality,
        flat_stats=flat_stats,
        recommended_mode=recommended_mode,
    )

    return {
        "filename": pdf_path.name,
        "page_count": page_count,
        "has_acroform": has_acroform,
        "acroform_fields": acroform_count,
        "field_names": field_names,
        "field_types": field_types,
        "name_quality": name_quality,
        "flat_placeholders": flat_stats,
        "sample_labels_per_page": sample_labels_per_page,
        "recommended_mode": recommended_mode,
        "guess": guess,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(
        description=(
            "Generikus PDF analizáló – AcroForm mezők, lapos PDF "
            "placeholder-ek és oldalonkénti feliratok kinyerése. "
            "Kimenet: JSON a stdout-ra."
        ),
    )
    ap.add_argument(
        "pdf",
        type=Path,
        help="A vizsgálandó PDF útvonala",
    )
    ap.add_argument(
        "--labels-per-page",
        type=int,
        default=10,
        help="Maximális felirat minta oldalonként (default: 10)",
    )
    ap.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentálás (default: 2; 0 = tömör)",
    )
    args = ap.parse_args()

    result = analyze_pdf(args.pdf, label_sample_per_page=args.labels_per_page)
    indent = args.indent if args.indent > 0 else None
    print(json.dumps(result, ensure_ascii=False, indent=indent))


if __name__ == "__main__":
    main()

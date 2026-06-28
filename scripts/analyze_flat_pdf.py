#!/usr/bin/env python3
"""
scripts/analyze_flat_pdf.py – Flat PDF overlay mező-detektor.

Lapos (AcroForm nélküli) banki nyilatkozat PDF-eket elemez PyMuPDF-fel:
kinyeri a kitöltendő helyeket (`……`, `____`, `□` checkboxok) a
pozíciójukkal és a környező szöveggel (label), majd DeepSeek V4 Flash
segítségével kanonikus mezőkre képezi le őket.

Output: egy `type: "overlay"` JSON a `src/mapping/` mappába, amelyet
a `src/main.py` overlay módban használ közvetlenül az `OverlayFiller`-rel.

Példa kimenet:
    {
      "pdf": "partner_nyilatkozat.pdf",
      "type": "overlay",
      "placements": [
        {
          "pdf_field_name": "flat_1_1",
          "label": "Hiteligénylő neve",
          "field_type": "text",
          "canonical_field": "participant.name",
          "x": 165.0,
          "y": 587.0,
          "page_index": 0,
          "font_size": 10.0,
          "confidence": "high"
        }
      ]
    }

Használat:
    python3 scripts/analyze_flat_pdf.py [--pdf partner_nyilatkozat.pdf]
    python3 scripts/analyze_flat_pdf.py --all
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

# Projekt gyökér
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env betöltése
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / "config" / ".env")
except Exception:
    pass

from src.ai.field_recognizer import CANONICAL_FIELDS

logger = logging.getLogger("analyze_flat_pdf")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

AI_MODEL = "deepseek-v4-flash"
API_URL = "https://api.deepseek.com/v1/chat/completions"

# Kanonikus mezőlista a prompt számára
CANONICAL_LIST = "\n".join(f"  - {k}: {v}" for k, v in CANONICAL_FIELDS.items())

# Kiegészítő canonical mezők, amik nem szerepelnek a CANONICAL_FIELDS-ben,
# de a nyilatkozatoknál kellenek (dátum, aláírás, régió, stb.).
EXTRA_CANONICAL = [
    "legal.signature_date",
    "legal.signature_place",
    "legal.signature_name",
    "employer.name",
    "employer.company_registration_number",
    "employer.tax_id",
    "loan.product_type_name",
    "loan.application_number",
    "property.ownership_share",
    "property.encumbrance",
    "region.name",
    "branch.name",
]
EXTRA_LIST = "\n".join(f"  - {k}" for k in EXTRA_CANONICAL)


# ---------------------------------------------------------------------------
# Placeholder detektálás
# ---------------------------------------------------------------------------

def _group_rects_by_row(rects: list, gap: float = 12.0) -> list[list]:
    """
    Egy sorban lévő (közeli) téglalapokat csoportosítja.

    Példa: több `…` karakter ugyanabban a sorban egyetlen placeholder-t alkot,
    ha egymás mellett vannak (gap < 12 pont).
    """
    if not rects:
        return []
    rows: dict[int, list] = {}
    for r in rects:
        rows.setdefault(round(r.y0), []).append(r)

    groups: list[list] = []
    for y in sorted(rows.keys()):
        rs = sorted(rows[y], key=lambda r: r.x0)
        cur = [rs[0]]
        for prev, nxt in zip(rs, rs[1:]):
            if nxt.x0 - prev.x1 < gap:
                cur.append(nxt)
            else:
                groups.append(cur)
                cur = [nxt]
        groups.append(cur)
    return groups


def detect_placeholders(page, page_index: int) -> list[dict]:
    """
    Visszaadja az oldalon talált placeholder-eket (text + checkbox).

    Minden placeholder dict:
        {
          "type": "text" | "checkbox",
          "x": <placeholder bal x>,        # fitz koordináta
          "y_top": <placeholder felső y>,  # fitz top-down
          "y_bot": <placeholder alsó y>,
          "width": <placeholder szélesség>,
          "page_index": page_index,
          "rect": [x0, y0, x1, y1],
        }
    """
    out: list[dict] = []

    # --- Szöveg placeholder-ek (……, ____ ). A `…` a magyar nyomtatványoknál
    # a leggyakoribb; `__` (aláhúzás) tartalékként.
    ell = page.search_for("…")
    for group in _group_rects_by_row(ell, gap=12.0):
        x0 = group[0].x0
        y0 = min(r.y0 for r in group)
        x1 = max(r.x1 for r in group)
        y1 = max(r.y1 for r in group)
        out.append({
            "type": "text",
            "x": x0,
            "y_top": y0,
            "y_bot": y1,
            "width": x1 - x0,
            "page_index": page_index,
            "rect": [x0, y0, x1, y1],
        })

    # Aláhúzást is detektáljuk (__ minták)
    for char in ("_",):
        rects = page.search_for(char * 2)  # legalább két egymás melletti _
        for group in _group_rects_by_row(rects, gap=12.0):
            x0 = group[0].x0
            y0 = min(r.y0 for r in group)
            x1 = max(r.x1 for r in group)
            y1 = max(r.y1 for r in group)
            # csak ha valóban hosszú (legalább 30 pont)
            if (x1 - x0) < 30:
                continue
            # ha már ez a hely ellipsis-ként felismerésre került, skip
            if any(
                abs(p["x"] - x0) < 10 and abs(p["y_top"] - y0) < 6
                for p in out
                if p["type"] == "text"
            ):
                continue
            out.append({
                "type": "text",
                "x": x0,
                "y_top": y0,
                "y_bot": y1,
                "width": x1 - x0,
                "page_index": page_index,
                "rect": [x0, y0, x1, y1],
            })

    # --- Checkboxok (□)
    cb = page.search_for("□")
    for r in cb:
        out.append({
            "type": "checkbox",
            "x": r.x0,
            "y_top": r.y0,
            "y_bot": r.y1,
            "width": r.width,
            "page_index": page_index,
            "rect": list(r),
        })

    # x szerinti sorrend (balról jobbra, fentről le)
    out.sort(key=lambda p: (p["page_index"], p["y_top"], p["x"]))
    return out


def label_for_placeholder(page, placeholder: dict, page_text_lines: list[tuple]) -> str:
    """
    Megpróbálja meghatározni egy placeholder label-jét.

    Heurisztika:
    1. Ugyanabban a sorban lévő szöveg (a placeholder előtt / után).
       Különösen fontos a zárójelben lévő "(Hiteligénylő neve)" minta.
    2. Ha a placeholder egy címke után van (pl. "Név:"), a korábbi
       szöveget használjuk.
    3. Ha semmi sem található, visszaad üres string-et (AI-t kéri).
    """
    py = placeholder["y_top"]
    px = placeholder["x"]
    line_text = ""

    # Ugyanabban a sormagasságban lévő szavak
    for (x0, y0, x1, y1, word, _b, _l, _w) in page_text_lines:
        if abs(y0 - py) < 8:
            line_text += word + " "

    line_text = line_text.strip()
    if not line_text:
        return ""

    # Ha van "(...)" zárójeles címke, azt részesítsük
    m = re.search(r"\(([^)]+)\)", line_text)
    if m:
        return m.group(1).strip()

    # Ha van "címke:" a sorban, a kettőspont utáni részt / vagy magát a címkét
    m2 = re.search(r"([A-ZÁÉÍÓÖŐÚÜŰa-záéíóöőúüű][\wÁÉÍÓÖŐÚÜŰáéíóöőúüű /\.]{2,40}):", line_text)
    if m2:
        return m2.group(1).strip()

    # Egyébként a sor eleje (a placeholder előtti szöveg)
    return line_text[:80]


def gather_full_page_text(page) -> str:
    """Az oldal teljes szövege (rövidítve a kontextushoz)."""
    return page.get_text("text")[:2000]


# ---------------------------------------------------------------------------
# DeepSeek klasszifikáció
# ---------------------------------------------------------------------------

def deepseek_chat(messages: list[dict], system: str | None = None,
                  max_tokens: int = 4096, json_mode: bool = True) -> str:
    import requests

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY hiányzik")

    full = []
    if system:
        full.append({"role": "system", "content": system})
    full.extend(messages)

    payload = {
        "model": AI_MODEL,
        "messages": full,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def extract_json_obj(text: str) -> dict:
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
    if m and m.group(1).strip().startswith("{"):
        return json.loads(m.group(1))
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])
    return json.loads(text)


SYSTEM_PROMPT = (
    "Te egy magyar banki nyilatkozat/nyomtatvány mező-elemző AI vagy. "
    "Lapos (AcroForm nélküli) PDF-ekben lévő kitöltendő helyeket kell "
    "kanonikus adatmezőkre leképezni. Minden placeholder-hez megkapod a "
    "következő információkat:\n"
    "  - id: egyedi azonosító (p1, p2, ...)\n"
    "  - type: text vagy checkbox\n"
    "  - label: a placeholder-hez tartozó felirat a nyomtatványon\n"
    "  - line: a sor teljes szövege, ahol a placeholder van\n"
    "  - page: oldal index\n\n"
    f"Kanonikus mezők (lehetőségek):\n{CANONICAL_LIST}\n"
    f"{EXTRA_LIST}\n\n"
    "Szabályok:\n"
    "1. CSAK JSON-t adj vissza: "
    "{\"<id>\": {\"canonical\": \"...\" | null, \"type\": \"text|checkbox|date\", "
    "\"label\": \"rövid magyar címke\"}, ...}\n"
    "2. `canonical` = a legjobban illeszkedő kanonikus mezőnév a listából, "
    "vagy `null` ha nem kell adattal kitölteni (pl. tanú aláírás, üres sor).\n"
    "3. Ha a label dátumot jelent ('Kelt', 'Dátum', 'Hely, idő'), akkor "
    "canonical='legal.signature_date', type='date'.\n"
    "4. Checkbox-nál: csak akkor adj meg canonical-t, ha egyértelműen "
    "lefordítható (pl. első lakás igen/nem, vásárlási hitelcél hozzátartozó). "
    "Egyébként `null` és type='checkbox'.\n"
    "5. Ha több hasonló mező van (pl. 3 lakás cím), a sorszámot jelezd a "
    "label-ben, de ugyanazt a canonical-t használd.\n"
)


def classify_placeholders_batch(
    batch: list[dict],
    page_index: int,
    page_text: str,
) -> dict[str, dict]:
    """
    Elküldi a placeholder batch-et a DeepSeek-nek.
    Visszatér: {placeholder_id: {canonical, type, label}}
    """
    items = []
    for p in batch:
        line_excerpt = p.get("line_text", "")[:200]
        items.append(
            f'- id="{p["id"]}" type={p["type"]} '
            f'label="{p.get("label", "")}" '
            f'line="{line_excerpt}"'
        )
    block = "\n".join(items)

    user_msg = (
        f"Ezek a(z) {page_index + 1}. oldalon lévő kitöltendő helyek. "
        f"Az oldal szövege:\n"
        f"\"\"\"\n{page_text[:1800]}\n\"\"\"\n\n"
        f"Placeholderek ({len(batch)} db):\n{block}\n\n"
        "Kérlek osztályozd őket."
    )

    try:
        text = deepseek_chat(
            messages=[{"role": "user", "content": user_msg}],
            system=SYSTEM_PROMPT,
            max_tokens=4096,
            json_mode=True,
        )
        if not text:
            return {}
        return extract_json_obj(text)
    except Exception as e:
        logger.warning(
            "  DeepSeek hiba (oldal %d): %s", page_index + 1, str(e)[:160]
        )
        return {}


# ---------------------------------------------------------------------------
# Fő analízis
# ---------------------------------------------------------------------------

def analyze_pdf(pdf_path: Path, out_path: Path, batch_size: int = 20) -> dict:
    """
    Végignéz egy PDF-et és generál egy overlay mapping JSON-t.

    Returns:
        A generált mapping dict (így is elmentve `out_path`-ra).
    """
    import fitz

    logger.info("PDF: %s", pdf_path.name)
    if not pdf_path.exists():
        raise SystemExit(f"Nem található: {pdf_path}")

    doc = fitz.open(str(pdf_path))

    all_placements: list[dict] = []
    counter = 0  # globális placeholder számláló

    for page_index, page in enumerate(doc):
        page_height = page.rect.height
        logger.info(
            "Oldal %d/%d (w=%.0f, h=%.0f)",
            page_index + 1,
            len(doc),
            page.rect.width,
            page_height,
        )

        # Oldal szövegének kinyerése word-szinten (a label heurisztikához)
        page_words = page.get_text("words") or []
        page_text = gather_full_page_text(page)

        placeholders = detect_placeholders(page, page_index)
        logger.info(
            "  Felismert placeholder: %d (text=%d, checkbox=%d)",
            len(placeholders),
            sum(1 for p in placeholders if p["type"] == "text"),
            sum(1 for p in placeholders if p["type"] == "checkbox"),
        )

        # Label heurisztika
        for p in placeholders:
            p["label"] = label_for_placeholder(page, p, page_words)
            p["line_text"] = ""
            for (x0, y0, x1, y1, word, _b, _l, _w) in page_words:
                if abs(y0 - p["y_top"]) < 8:
                    p["line_text"] += word + " "
            p["line_text"] = p["line_text"].strip()
            counter += 1
            p["id"] = f"p{counter}"

        # DeepSeek klasszifikáció batch-ekben
        for i in range(0, len(placeholders), batch_size):
            batch = placeholders[i:i + batch_size]
            res = classify_placeholders_batch(batch, page_index, page_text)
            time.sleep(0.4)
            for p in batch:
                info = res.get(p["id"], {})
                canonical = info.get("canonical") or None
                ftype = info.get("type") or p["type"]
                label = info.get("label") or p["label"] or f"mező {p['id']}"

                # PDF bottom-up y koordináta (reportlab drawString-hez).
                # A baseline-t a placeholder alsó élénél szeretnénk, ezért:
                #   pdf_y = page_height - fitz_y_bot
                # Kis emelés (+1.5) hogy a szöveg szépen illeszkedjen.
                pdf_y = page_height - p["y_bot"] + 1.5

                # Font size becslése: a placeholder magasságából
                font_size = min(max(p["y_bot"] - p["y_top"], 8.0), 11.0)

                placement = {
                    "pdf_field_name": f"flat_{page_index + 1}_{p['id']}",
                    "label": label,
                    "field_type": ftype,
                    "canonical_field": canonical,
                    "confidence": "high" if canonical else "low",
                    "x": round(p["x"], 1),
                    "y": round(pdf_y, 1),
                    "page_index": page_index,
                    "font_size": round(font_size, 1),
                }
                all_placements.append(placement)
                logger.info(
                    "    %-3s %-9s → %-30s | %s",
                    p["id"],
                    ftype,
                    canonical or "(nincs)",
                    label[:40],
                )

    doc.close()

    # Mapping dict összeállítása
    mapping = {
        "pdf": pdf_path.name,
        "type": "overlay",
        "bank_name": "OTP Bank",
        "form_name": pdf_path.stem,
        "notes": (
            f"PyMuPDF placeholder detektor + DeepSeek V4 Flash klasszifikáció. "
            f"{len(all_placements)} mező, "
            f"{sum(1 for p in all_placements if p['canonical_field'])} leképezve."
        ),
        "placements": all_placements,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    logger.info("Mentve: %s", out_path)

    mapped = sum(1 for p in all_placements if p["canonical_field"])
    print(f"\n=== {pdf_path.name} overlay mapping kész ===")
    print(f"Összes mező: {len(all_placements)}")
    print(f"Leképezve:   {mapped}")
    print(f"Output:      {out_path}")
    return mapping


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_PDFS = [
    "partner_nyilatkozat.pdf",
    "hozzajarulo_nyilatkozat.pdf",
    "zold_lakashitel_nyilatkozat.pdf",
    "CSOK_afa_igazolas.pdf",
]

# Stem → output fájlnév mapping
OUT_NAMES = {
    "partner_nyilatkozat": "partner_nyilatkozat_overlay.json",
    "hozzajarulo_nyilatkozat": "hozzajarulo_nyilatkozat_overlay.json",
    "zold_lakashitel_nyilatkozat": "zold_lakashitel_overlay.json",
    "CSOK_afa_igazolas": "CSOK_afa_igazolas_overlay.json",
}


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--pdf", type=Path, default=None,
        help="Egy konkrét PDF feldolgozása",
    )
    ap.add_argument(
        "--all", action="store_true",
        help="Az összes alapértelmezett flat PDF feldolgozása",
    )
    ap.add_argument(
        "--out", type=Path, default=None,
        help="Output JSON fájl (csak --pdf mellett)",
    )
    ap.add_argument(
        "--batch-size", type=int, default=20,
        help="DeepSeek batch méret (default: 20)",
    )
    args = ap.parse_args()

    mapping_dir = PROJECT_ROOT / "src" / "mapping"

    if args.all:
        for name in DEFAULT_PDFS:
            pdf_path = PROJECT_ROOT / name
            stem = pdf_path.stem
            out_name = OUT_NAMES.get(stem, f"{stem}_overlay.json")
            out_path = mapping_dir / out_name
            try:
                analyze_pdf(pdf_path, out_path, batch_size=args.batch_size)
            except Exception as e:
                logger.error("Hiba %s feldolgozásakor: %s", name, e)
        return

    if args.pdf is None:
        ap.error("Vagy --pdf vagy --all szükséges")

    pdf_path = args.pdf
    stem = pdf_path.stem
    out_path = args.out or mapping_dir / OUT_NAMES.get(
        stem, f"{stem}_overlay.json"
    )
    analyze_pdf(pdf_path, out_path, batch_size=args.batch_size)


if __name__ == "__main__":
    main()

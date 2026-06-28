#!/usr/bin/env python3
"""
Context-alapú mezőfelismerés az "Előzetes értékbecslés" PDF-hez.

A nyomtatvány 1029 AcroForm mezője MIND teljesen generikus
("Check Box NNN" / "Text Field NNN"), nulla szemantikus információval.
Ez a script a `scripts/recognize_v_fuggelek.py` mintájára működik:

  1. pikepdf-del kinyeri a mezőneveket + oldalukat + FT típusukat.
  2. PyMuPDF-fel (fitz) megkeresi a mező widget-jét és annak rect-jét,
     majd a környező szövegből (label + kontextus) állít fel egy leírást.
  3. Oldalanként batch-ben elküldi DeepSeek-nek, hogy klasszifikálja
     a mezőket a kanonikus modellre (vagy `null`-ra, ha nem képezhető le).
  4. Összefésüli az eredményt és elmenti
     `src/mapping/elozetes_ertekbecsles_mapping.json` formátumban.

A 16-18. oldalak tele vannak checkbox nyilatkozatokkal (jogi hozzájárulások,
elfogadások) — ezek a `legal.*` kategóriába esnek. A pipeline lentebb a
`LegalClassifier`-rel automatikusan pipálja a canonical nélküli
checkbox-okat, így a legtöbb jogi mezőt itt `canonical=null`-ra hagyjuk,
és csak a dátum/hely/aláíró mezőket (legal.signature_*) képezzük le.

Használat:
    python3 scripts/recognize_ertekbecsles.py [--pdf elozetes_ertekbecsles.pdf]

A `DEEPSEEK_API_KEY` környezeti változót (vagy config/.env-et) használja.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

# Projekt gyökér
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env betöltése (ha van)
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / "config" / ".env")
except Exception:
    pass

from src.ai.field_recognizer import (
    FieldType,
    MappingConfidence,
    MappingConfig,
    RecognizedField,
)

logger = logging.getLogger("recognize_ertekbecsles")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")

DEFAULT_PDF = PROJECT_ROOT / "elozetes_ertekbecsles.pdf"
DEFAULT_OUT = PROJECT_ROOT / "src" / "mapping" / "elozetes_ertekbecsles_mapping.json"
AI_MODEL = "deepseek-v4-flash"
API_URL = "https://api.deepseek.com/v1/chat/completions"


# --- AcroForm mezők kinyerése pikepdf + PyMuPDF widget pozícióval ---


def extract_fields_with_position(pdf_path: Path) -> list[dict]:
    """
    Visszaad egy listát dict-eket:
      {
        "name": "Text Field 12",
        "type": "text|checkbox",
        "page": 3,                      # 1-indexelt
        "rect": [x0, y0, x1, y1],      # PDF pont koordináták (fitz)
      }
    """
    import pikepdf
    import fitz

    doc = fitz.open(str(pdf_path))
    pdf = pikepdf.open(str(pdf_path))

    # objgen → page index (0-based) térkép
    page_objgen_to_idx: dict[tuple, int] = {}
    for i, page in enumerate(pdf.pages):
        try:
            page_objgen_to_idx[page.objgen] = i
        except Exception:
            pass

    # fitz widget-ek oldalanként (rect + field name)
    widget_by_page_name: dict[tuple[int, str], list[float]] = {}
    for page_idx, page in enumerate(doc):
        for w in page.widgets() or []:
            name = w.field_name or ""
            if not name:
                continue
            rect = list(w.rect)
            widget_by_page_name.setdefault((page_idx, name), rect)

    out: list[dict] = []
    acroform = pdf.Root.get("/AcroForm")
    fields_arr = acroform.get("/Fields") if acroform is not None else None
    if fields_arr is None:
        logger.error("Nincs AcroForm a PDF-ben!")
        return out

    def walk(f, parent_name: str = ""):
        """Rekurzívan bejárja a (gyerek) mezőket is."""
        try:
            raw_name = str(f.get("/T", ""))
            name = f"{parent_name}.{raw_name}" if parent_name and raw_name else raw_name
            ft = str(f.get("/FT", ""))
            page_idx = None
            if "/P" in f:
                pref = f["/P"]
                try:
                    page_idx = page_objgen_to_idx.get(pref.objgen)
                except Exception:
                    page_idx = None

            rect = None
            lookup_names = [raw_name, name]
            if page_idx is not None:
                for ln in lookup_names:
                    rect = widget_by_page_name.get((page_idx, ln))
                    if rect:
                        break
                if rect is None:
                    for pi in range(len(doc)):
                        for ln in lookup_names:
                            r = widget_by_page_name.get((pi, ln))
                            if r is not None:
                                rect = r
                                page_idx = pi
                                break
                        if rect:
                            break

            is_leaf = bool(ft) or "/Kids" not in f
            if is_leaf and name:
                ftype = "checkbox" if ft == "/Btn" else "text"
                out.append({
                    "name": name,
                    "type": ftype,
                    "page": (page_idx + 1) if page_idx is not None else 1,
                    "rect": rect,
                })
            if "/Kids" in f:
                for k in f["/Kids"]:
                    walk(k, name or parent_name)
        except Exception as e:
            logger.debug("Mező olvasás hiba: %s", e)

    for fref in fields_arr:
        walk(fref)

    doc.close()
    pdf.close()
    return out


def gather_context_for_fields(doc, fields: list[dict]) -> None:
    """
    Minden mezőhöz kigyűjti:
      - `nearby`: a mező rect-je körüli 80 pontos sugarú szövegek,
                  a távolság alapján rendezve (legközelebbi elől).
      - `page_text`: az oldal teljes szövege (rövidítve).
    A `fields` listát helyben módosítja (in-place).
    """
    for f in fields:
        page_idx = f.get("page", 1) - 1
        rect = f.get("rect")
        nearby = ""
        if rect and 0 <= page_idx < len(doc):
            page = doc[page_idx]
            x0, y0, x1, y1 = rect
            cx = (x0 + x1) / 2
            cy = (y0 + y1) / 2
            radius = 80.0
            words = []
            for w in page.get_text("words") or []:
                wx = (w[0] + w[2]) / 2
                wy = (w[1] + w[3]) / 2
                dist = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
                if dist <= radius:
                    words.append((dist, w[4]))
            words.sort(key=lambda t: t[0])
            nearby = " ".join(w for _, w in words[:25])

        page_text = ""
        if 0 <= page_idx < len(doc):
            page_text = doc[page_idx].get_text("text")[:1500]

        f["nearby"] = nearby
        f["page_text_excerpt"] = page_text


# --- DeepSeek hívás ---


def deepseek_chat(messages: list[dict], system: str | None = None,
                  max_tokens: int = 8192, json_mode: bool = True) -> str:
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
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload, timeout=180,
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


# --- Klasszifikáció ---

# A pipeline által ismert canonical kategóriák. A task által javasolt mezőket
# a valódi pipeline namespace-re képezzük: deal.* → loan.*, property.city →
# property.address.city, property.valuation → property.estimated_value, stb.
CANONICAL_GUIDE = """
Kanonikus mezők (lehetőségek) — ezeket preferáld, ha illeszkedik:
Személy (participant.*):
  - participant.name: Szereplő teljes neve
  - participant.birth_name: Születési név
  - participant.mother_name: Anyja neve
  - participant.birth_place: Születési hely
  - participant.birth_date: Születési dátum
  - participant.personal_id: Személyi igazolvány szám
  - participant.id_card_number: Személyazonosító kártya szám (címkártya)
  - participant.tax_id: Adóazonosító jel
  - participant.phone: Telefonszám
  - participant.email: E-mail cím
  - participant.address.full_address: Állandó lakcím (teljes)
  - participant.address.zip_code: Irányítószám
  - participant.address.city: Település
  - participant.address.street: Utca
  - participant.address.house_number: Házszám
  - participant.employer: Munkáltató
  - participant.monthly_income: Havi nettó jövedelem
  - participant.citizenship: Állampolgárság
  - participant.marital_status: Családi állapot
  - participant.education: Végzettség
  - participant.dependents: Eltartottak száma
Ingatlan (property.*):
  - property.address.full_address: Ingatlan címe (teljes)
  - property.address.zip_code: Ingatlan irányítószám
  - property.address.city: Ingatlan település
  - property.address.street: Ingatlan utca
  - property.address.house_number: Ingatlan házszám
  - property.parcel_number: Helyrajzi szám
  - property.area_sqm: Terület (m²)
  - property.property_type: Ingatlan típusa
  - property.estimated_value: Becsült érték / forgalmi érték / értékbecslés (Ft)
  - property.year_built: Építés éve
  - property.number_of_rooms: Szobák száma
Hitel / ügylet (loan.*):
  - loan.loan_amount: Hitelösszeg (Ft)
  - loan.loan_term_months: Futamidő (hónap)
  - loan.loan_purpose: Hitel célja
  - loan.product_name: Termék neve
  - loan.down_payment: Önerő (Ft)
Jogi (legal.*):
  - legal.signature_date: Kelt / dátum
  - legal.signature_place: Kelt helye
  - legal.signature_name: Aláíró neve
"""


def classify_batch(batch: list[dict], page_no: int, page_text: str) -> dict[str, dict]:
    """
    Elküld egy batch mezőt a DeepSeek-nek, visszatér:
       { pdf_field_name: {"canonical": "...", "type": "...", "label": "..."} }
    A `canonical` lehet `null` ha nem felismerhető.
    """
    fields_desc = []
    for f in batch:
        rect_str = ""
        if f.get("rect"):
            rect_str = f" rect=[x0={f['rect'][0]:.0f}, y0={f['rect'][1]:.0f}, x1={f['rect'][2]:.0f}, y1={f['rect'][3]:.0f}]"
        fields_desc.append(
            f'- name="{f["name"]}" type={f["type"]} nearby="{f.get("nearby","")[:200]}"{rect_str}'
        )
    fields_block = "\n".join(fields_desc)

    system = (
        "Te egy magyar banki nyomtatvány mező-elemző AI vagy. Egy OTP Bank "
        "'Előzetes értékbecslés' (előzetes ingatlan-értékbecslés / "
        "hitelfedezet-értékelés) PDF AcroForm mezőit osztályozod. A mezőnevek "
        "teljesen generikusak ('Check Box NNN' / 'Text Field NNN'), ezért a "
        "`nearby` szöveg és a pozíció alapján döntsd el, mit tartalmaznak.\n\n"
        f"{CANONICAL_GUIDE}\n\n"
        "Szabályok:\n"
        "1. CSAK JSON objektumot adj: "
        "{\"<pdf_field_name>\": {\"canonical\": \"...|null\", \"type\": \"text|checkbox|date|number\", \"label\": \"rövid magyar címke\"}, ...}\n"
        "2. `canonical` = a legjobban illeszkedő kanonikus mező, vagy `null` ha nem kell adattal kitölteni.\n"
        "3. Dátum és 'Hely, dátum' mezők → canonical=\"legal.signature_date\", label='Kelt / dátum' "
        "(hely mező → legal.signature_place).\n"
        "4. Jogi / hozzájáruló / elfogadó jellegű checkbox-ok (pipa dobozok, nyilatkozatok) "
        "→ canonical=null, type=checkbox, label rövid leírással. Ezeket a pipeline "
        "később automatikusan kategóriákra osztja és pipálja.\n"
        "5. Ha egy mező egyértelműen személyre / ingatlanra / hitelre utal (név, "
        "születési adatok, cím, adószám, ingatlan címe, alapterület, becsült érték, "
        "hitelösszeg, futamidő), akkor a megfelelő canonical mezőre képezd le.\n"
        "6. Értékbecslő / szakértő / banki referenciaszám, ügyintéző neve, sablon "
        "azonosító → canonical=null, de adj részletes label-t.\n"
    )

    user_msg = (
        f"Ezek a(z) {page_no}. oldalon lévő mezők. Az oldal teljes szövege:\n"
        f"\"\"\"\n{page_text}\n\"\"\"\n\n"
        f"Mezők ({len(batch)} db):\n{fields_block}\n\n"
        "Kérlek osztályozd őket."
    )

    try:
        text = deepseek_chat(
            messages=[{"role": "user", "content": user_msg}],
            system=system, max_tokens=8192, json_mode=True,
        )
        if not text:
            return {}
        return extract_json_obj(text)
    except Exception as e:
        logger.warning("  DeepSeek hiba (page %d): %s", page_no, str(e)[:160])
        return {}


# --- Fő logika ---


def is_generic(name: str) -> bool:
    nm = name.lower()
    return (
        nm.startswith("check box ") or nm.startswith("text field ")
        or nm.startswith("jelölőnégyzet ") or nm.startswith("szövegmező ")
    )


def main(pdf_path: Path = DEFAULT_PDF, out_path: Path = DEFAULT_OUT,
         batch_size: int = 25) -> None:
    import fitz

    logger.info("PDF: %s", pdf_path)
    if not pdf_path.exists():
        raise SystemExit(f"Nem található: {pdf_path}")

    fields = extract_fields_with_position(pdf_path)
    logger.info("AcroForm mezők: %d", len(fields))

    # Pozíció + kontextus kinyerése
    doc = fitz.open(str(pdf_path))
    gather_context_for_fields(doc, fields)
    doc.close()

    named = [f for f in fields if not is_generic(f["name"])]
    generic = [f for f in fields if is_generic(f["name"])]
    logger.info("  Név alapján ismert: %d, generikus: %d", len(named), len(generic))

    # Oldalak szerint csoportosítjuk a generikus mezőket
    by_page: dict[int, list[dict]] = {}
    for f in generic:
        by_page.setdefault(f["page"], []).append(f)

    classification: dict[str, dict] = {}
    total_batches = 0
    for page_no in sorted(by_page.keys()):
        batch = by_page[page_no]
        logger.info("Klasszifikálok: %d. oldal, %d mező", page_no, len(batch))
        page_text = batch[0].get("page_text_excerpt", "")
        for i in range(0, len(batch), batch_size):
            sub = batch[i:i + batch_size]
            res = classify_batch(sub, page_no, page_text)
            classification.update(res)
            total_batches += 1
            time.sleep(0.5)
        logger.info("  eddig klasszifikálva: %d", len(classification))

    # MappingConfig összeállítása
    recognized: list[RecognizedField] = []

    for f in fields:
        name = f["name"]
        info = classification.get(name, {})
        canonical = info.get("canonical") or None
        ftype = info.get("type") or f.get("type") or "text"
        label = info.get("label") or name

        try:
            ft_enum = FieldType(ftype)
        except ValueError:
            ft_enum = FieldType.TEXT

        conf = MappingConfidence.MEDIUM if canonical else MappingConfidence.LOW

        recognized.append(RecognizedField(
            pdf_field_name=name,
            label=label,
            field_type=ft_enum,
            canonical_field=canonical,
            confidence=conf,
            page_number=f["page"],
            coordinates=_rect_to_coords(f.get("rect")),
            notes="context-based classification" if canonical else "nem képezhető le",
        ))

    mapping = MappingConfig(
        bank_name="OTP Bank",
        form_name="elozetes_ertekbecsles",
        form_type="acroform",
        fields=recognized,
        notes=(
            f"Context-based AI felismerés ({len(recognized)} mező, "
            f"{sum(1 for r in recognized if r.canonical_field)} leképezve, "
            f"{total_batches} API hívás)."
        ),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mapping.save(out_path)
    logger.info("Mentve: %s", out_path)

    mapped = sum(1 for r in recognized if r.canonical_field)
    cb = sum(1 for r in recognized if r.field_type == FieldType.CHECKBOX)
    print(f"\n=== Előzetes értékbecslés mapping kész ===")
    print(f"Összes mező:      {len(recognized)}")
    print(f"Leképezve:        {mapped}")
    print(f"Checkbox (Btn):   {cb}")
    print(f"API hívások:      {total_batches}")
    print(f"Output:           {out_path}")


def _rect_to_coords(rect):
    if not rect:
        return None
    x0, y0, x1, y1 = rect
    return {
        "x": round(x0, 1),
        "y": round(y0, 1),
        "width": round(x1 - x0, 1),
        "height": round(y1 - y0, 1),
    }


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--batch-size", type=int, default=25)
    args = ap.parse_args()
    main(args.pdf, args.out, args.batch_size)

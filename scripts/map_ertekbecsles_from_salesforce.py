#!/usr/bin/env python3
"""
Előzetes értékbecslés – 3. stratégia: Salesforce adatokból visszafejtés.

A 1029 generikus AcroForm mező ("Check Box NNN" / "Text Field NNN") nincs
szemantikusan elnevezve. Ez a script ahelyett, hogy vakon klasszifikálna,
kiindul a Salesforce-ból érkező konkrét adatokból:

  1. Lekéri a deal adatait (Salesforce sandbox, `--deal` ID).
  2. Kinyeri a canonical mezőket és azok értékeit (participant.*, property.*,
     loan.*).
  3. PyMuPDF + pikepdf segítségével minden AcroForm mezőhöz meghatározza:
       - pozíciót (bbox),
       - a környezetében lévő szöveget (label),
       - az oldal szövegrészletét (kontextus).
  4. Minden canonical mezőhöz, aminek van értéke, megkeresi azt a PDF
     mezőt, amelynek környezetében szerepel a hozzá tartozó magyar label
     (alias-alapú, ékezet- és kisbetű-agnosztikus egyezés).
  5. Ha a label matching nem fedez elég mezőt, ráfut a 2. stratégia:
     DeepSeek V4 Flash oldalankénti batch klasszifikáció
     (`thinking: disabled`, BATCH_SIZE=40, max_tokens=2000).
  6. Összefésüli a találatokat a meglévő mapping-gel (a korábban jó
     leképezéseket megtartja), és elmenti
     `src/mapping/elozetes_ertekbecsles_mapping.json` formátumban.

Használat:
    python3 scripts/map_ertekbecsles_from_salesforce.py
    python3 scripts/map_ertekbecsles_from_salesforce.py --deal 006WB00000LwTDgYAN
    python3 scripts/map_ertekbecsles_from_salesforce.py --no-ai   # csak label matching

A `config/.env`-ből olvassa a Salesforce creds-eket (SF_USERNAME, SF_PASSWORD,
SF_SECURITY_TOKEN, SF_DOMAIN) és a DeepSeek kulcsot (DEEPSEEK_API_KEY).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Optional

# Projekt gyökér
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# .env betöltése
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
from src.integrations.salesforce_client import SalesforceClient

logger = logging.getLogger("map_ertekbecsles_sf")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)

DEFAULT_PDF = PROJECT_ROOT / "elozetes_ertekbecsles.pdf"
DEFAULT_OUT = PROJECT_ROOT / "src" / "mapping" / "elozetes_ertekbecsles_mapping.json"
DEFAULT_DEAL = "006WB00000LwTDgYAN"

AI_MODEL = "deepseek-v4-flash"
API_URL = "https://api.deepseek.com/v1/chat/completions"


# =========================================================================
# Normalizáló helper
# =========================================================================

def _norm(s: str) -> str:
    """Ékezet- és kisbetű-agnosztikus normalizálás (NFD + lowercase)."""
    if not s:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in nfd if not unicodedata.combining(ch)).lower()


# =========================================================================
# Canonical alias tábla — magyar nyomtatvány címkék → canonical mező.
#
# Ezeket a címkéket keressük a PDF mezők környezetében (label / nearby text).
# A canonical kulcsok ILLESZKEDNEK a pipeline `_prepare_field_data` által
# használt namespace-hez (participant.*, property.*, loan.*, legal.*).
# =========================================================================

LABEL_ALIASES: dict[str, list[str]] = {
    # --- Személy (participant.*) ---
    "participant.name": [
        "igénylő neve", "név", "ügyfél neve", "kölcsönigénylő neve",
        "munkavállaló neve", "ügyfél neve:",
    ],
    "participant.birth_name": ["születési név", "szül. név", "leánykori név"],
    "participant.mother_name": [
        "anyja neve", "anya neve", "anyja leánykori neve", "anyja születési neve",
    ],
    "participant.birth_date": [
        "születési idő", "születési dátum", "szül. idő", "date of birth",
        "születési év",
    ],
    "participant.birth_place": ["születési hely", "szül. hely"],
    "participant.personal_id": [
        "személyi igazolvány", "személyi szám", "személyazonosító",
        "személyazonosító dokumentum",
    ],
    "participant.id_card_number": [
        "címkártya", " személyi ", "okmány azonosító", "címkártya száma",
    ],
    "participant.tax_id": [
        "adóazonosító jel", "adóazonosító", "adószám",
    ],
    "participant.phone": ["telefonszám", "telefon", "mobil", "mobil telefonszám"],
    "participant.email": ["e-mail", "email", "elektronikus levelezési cím"],
    "participant.employer": [
        "munkáltató neve", "munkahely", "foglalkoztató neve", "munkáltató",
    ],
    "participant.occupation": ["foglalkozás", "munkakör", "beosztás"],
    "participant.monthly_income": [
        "havi nettó jövedelem", "havi jövedelem", "rendszeres havi jövedelem",
        "nettó jövedelem",
    ],
    "participant.dependents": ["eltartottak", "eltartottak száma", "gyermekek száma"],
    "participant.marital_status": ["családi állapot"],
    "participant.education": ["végzettség", "iskolai végzettség"],
    "participant.citizenship": ["állampolgárság"],
    "participant.address.full_address": [
        "állandó lakcím", "lakcím", "állandó lakcím:", "értesítési cím",
    ],
    "participant.address.zip_code": ["irányítószám", "ir.szám", "irsz"],
    "participant.address.city": ["település"],
    "participant.address.street": [
        "közterület neve", "utca", "utca, házszám", "utca, házszám, lépcsőház",
    ],
    "participant.address.house_number": ["házszám"],

    # --- Ingatlan (property.*) ---
    "property.address.full_address": [
        "ingatlan címe", "ingatlan címe:", "fedezet címe", "fedezeti ingatlan címe",
        "értékelt ingatlan címe", "tulajdon címe",
    ],
    "property.address.zip_code": ["ingatlan irányítószám", "ingatlan irsz"],
    "property.address.city": ["ingatlan település", "ingatlan város"],
    "property.address.street": ["ingatlan utca", "ingatlan közterület"],
    "property.address.house_number": ["ingatlan házszám"],
    "property.parcel_number": ["helyrajzi szám", "hrsz", "hrsZ."],
    "property.area_sqm": ["alapterület", "m²", "négyzetméter", "terület (m"],
    "property.property_type": ["ingatlan típusa", "ingatlan jellege", "tulajdoni hányad"],
    "property.estimated_value": [
        "becsült érték", "értékbecslés", "forgalmi érték", "piaci érték",
        "becsült forgalmi érték",
    ],
    "property.year_built": ["építés éve", "épült"],
    "property.number_of_rooms": ["szobák száma", "szobaszám", "fél szoba"],

    # --- Hitel / ügylet (loan.*) ---
    "loan.loan_amount": [
        "kölcsön összege", "hitel összege", "igényelt hitel", "igényelt összeg",
        "hitelösszeg", "kölcsönigény",
    ],
    "loan.loan_purpose": ["hitel célja", "kölcsön célja", "hitelfelvétel célja", "cél:"],
    "loan.loan_term_months": ["futamidő", "futamidő (hónap)", "futamidő hónap"],
    "loan.product_name": ["termék neve", "termék"],

    # --- Jogi (legal.*) ---
    "legal.signature_date": [
        "dátum", "keltezés", "aláírás dátuma", "kelt", "kitöltés dátuma",
        "éve", "hónapja", "napja",  # "Kelt ... év ... hónap ... nap"
    ],
    "legal.signature_place": ["aláírás helye", "hely", "kelt:"],
    "legal.signature_name": ["aláíró neve", "aláírás", "név (aláíró)"],
}


# =========================================================================
# 1. Salesforce adatok lekérése + canonical field-értékek kinyerése
# =========================================================================

def fetch_sf_canonical_values(deal_id: str) -> dict[str, str]:
    """
    Lekéri a deal adatait a Salesforce sandbox-ból és visszaad egy
    canonical_field → érték string dict-et (szöveges formázással, ahogy a
    pipeline `_prepare_field_data` teszi).

    Csak a BORROWER (adós) és az első Property adatait gyűjti össze, mert
    a PDF 'Előzetes értékbecslés' nyomtatvány a személyi adatlap
    főigénylőre vonatkozik (a társigénylőkhöz külön oldalak tartoznak, amit
    a mapping `-társ` suffix-szel old meg).
    """
    from config import settings

    sf = SalesforceClient(
        username=settings.SF_USERNAME,
        password=settings.SF_PASSWORD,
        security_token=settings.SF_SECURITY_TOKEN,
        domain=settings.SF_DOMAIN,
        mock_mode=False,
    )
    data = sf.get_deal(deal_id)
    if data is None:
        raise RuntimeError(f"Salesforce deal nem található: {deal_id}")

    parts = data.get("Participants__r", {}).get("records", [])
    props = data.get("Properties__r", {}).get("records", [])

    # Borrower (első adós) keresése
    borrower = next(
        (p for p in parts if (p.get("role") or "").lower().startswith("adós")
         and p.get("role") != "adóstárs"),
        None,
    )
    if borrower is None and parts:
        borrower = parts[0]

    values: dict[str, str] = {}

    def put(canonical: str, val: Any) -> None:
        if val is None:
            return
        s = str(val).strip()
        if s and s.lower() not in ("none", "null"):
            values[canonical] = s

    if borrower:
        put("participant.name", borrower.get("name"))
        put("participant.birth_name", borrower.get("birth_name"))
        put("participant.mother_name", borrower.get("mother_name"))
        put("participant.birth_place", borrower.get("birth_place"))
        put("participant.birth_date", borrower.get("birth_date"))
        put("participant.personal_id", borrower.get("personal_id"))
        put("participant.tax_id", borrower.get("tax_id"))
        put("participant.id_card_number", borrower.get("id_card_number"))
        put("participant.phone", borrower.get("phone"))
        put("participant.email", borrower.get("email"))
        put("participant.employer", borrower.get("employer"))
        put("participant.occupation", borrower.get("occupation"))
        put("participant.monthly_income", borrower.get("monthly_income"))
        put("participant.dependents", borrower.get("dependents"))
        put("participant.marital_status", borrower.get("marital_status"))
        put("participant.education", borrower.get("education"))
        put("participant.citizenship", borrower.get("citizenship"))
        put("participant.employment_type", borrower.get("employment_type"))

        addr = borrower.get("address") or {}
        put("participant.address.zip_code", addr.get("zip_code"))
        put("participant.address.city", addr.get("city"))
        put("participant.address.street", addr.get("street"))
        put("participant.address.house_number", addr.get("house_number"))
        if addr.get("zip_code") and addr.get("city"):
            full = f"{addr['zip_code']} {addr['city']}"
            if addr.get("street"):
                full += f", {addr['street']}"
            if addr.get("house_number"):
                full += f" {addr['house_number']}"
            values["participant.address.full_address"] = full

    # Ingatlan (első)
    if props:
        pr = props[0]
        put("property.property_type", pr.get("property_type"))
        put("property.parcel_number", pr.get("parcel_number"))
        put("property.area_sqm", pr.get("area_sqm"))
        put("property.estimated_value", pr.get("estimated_value"))
        paddr = pr.get("address") or {}
        put("property.address.zip_code", paddr.get("zip_code"))
        put("property.address.city", paddr.get("city"))
        put("property.address.street", paddr.get("street"))
        put("property.address.house_number", paddr.get("house_number"))
        if paddr.get("zip_code") and paddr.get("city"):
            full = f"{paddr['zip_code']} {paddr['city']}"
            if paddr.get("street"):
                full += f", {paddr['street']}"
            if paddr.get("house_number"):
                full += f" {paddr['house_number']}"
            values["property.address.full_address"] = full

    # Hitel
    put("loan.loan_amount", data.get("Amount__c"))
    put("loan.loan_term_months", data.get("Loan_Term__c"))
    put("loan.loan_purpose", data.get("Loan_Purpose__c"))
    put("loan.product_name", data.get("Product_Name__c"))

    return values


# =========================================================================
# 2. PDF pozíciók + label-ek kinyerése
# =========================================================================

def extract_fields_with_position(pdf_path: Path) -> list[dict]:
    """
    Visszaad: [{name, type, page, rect}] – 1-indexelt oldal, PDF pont koordináták.
    (Ugyanaz a logika, mint scripts/recognize_ertekbecsles.py-ben.)
    """
    import pikepdf
    import fitz

    doc = fitz.open(str(pdf_path))
    pdf = pikepdf.open(str(pdf_path))

    page_objgen_to_idx: dict[tuple, int] = {}
    for i, page in enumerate(pdf.pages):
        try:
            page_objgen_to_idx[page.objgen] = i
        except Exception:
            pass

    widget_by_page_name: dict[tuple[int, str], list[float]] = {}
    for page_idx, page in enumerate(doc):
        for w in page.widgets() or []:
            name = w.field_name or ""
            if not name:
                continue
            widget_by_page_name.setdefault((page_idx, name), list(w.rect))

    out: list[dict] = []
    acroform = pdf.Root.get("/AcroForm")
    fields_arr = acroform.get("/Fields") if acroform is not None else None
    if fields_arr is None:
        logger.error("Nincs AcroForm a PDF-ben!")
        return out

    def walk(f, parent_name: str = ""):
        try:
            raw_name = str(f.get("/T", ""))
            name = f"{parent_name}.{raw_name}" if parent_name and raw_name else raw_name
            ft = str(f.get("/FT", ""))
            page_idx = None
            if "/P" in f:
                try:
                    page_idx = page_objgen_to_idx.get(f["/P"].objgen)
                except Exception:
                    page_idx = None

            rect = None
            for ln in (raw_name, name):
                if page_idx is not None:
                    rect = widget_by_page_name.get((page_idx, ln))
                    if rect:
                        break
            if rect is None:
                for pi in range(len(doc)):
                    for ln in (raw_name, name):
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


def extract_field_labels(doc, fields: list[dict], radius: float = 90.0) -> None:
    """
    Minden mezőhöz meghatározza a környezetében lévő szöveget (label).

    Két dolgot tölt fel in-place a `fields` listában:
      - `nearby`:  a mező rect-je körüli `radius` sugarú szavak, bal/fent
                   prioritással rendezve (a label általában balra vagy a
                   mező felett van).
      - `label`:   a legközelebbi értelmes szövegblokk (a nearby első
                   40 szava), ami a tényleges címkét adja.
      - `page_text`: az oldal teljes szövege (rövidítve) – kontextus az
                   AI fallback-hez.
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
            words = []
            for w in page.get_text("words") or []:
                wx = (w[0] + w[2]) / 2
                wy = (w[1] + w[3]) / 2
                dist = ((wx - cx) ** 2 + (wy - cy) ** 2) ** 0.5
                if dist <= radius:
                    # Preferencia: balra (wx < x0) vagy felette (wy < y0)
                    pref = 0.0
                    if wx < x0:
                        pref -= 30.0      # balra lévő szöveg "közelebb"
                    if wy < y0:
                        pref -= 20.0      # felette lévő szöveg "közelebb"
                    words.append((dist + pref, w[4]))
            words.sort(key=lambda t: t[0])
            nearby = " ".join(w for _, w in words[:40])

        page_text = ""
        if 0 <= page_idx < len(doc):
            page_text = doc[page_idx].get_text("text")[:1800]

        f["nearby"] = nearby
        f["label"] = nearby[:120]
        f["page_text"] = page_text


# =========================================================================
# 3. Salesforce ↔ PDF label matching (3. stratégia)
# =========================================================================

# Salesforce canonical értékek amik checkbox típusú PDF mezőhöz tartoznak.
# (Jelenleg nincs ilyen — az összes felhasznált SF mező szöveges/numerikus.)
CHECKBOX_CANONICALS: set[str] = set()


def _expected_pdf_type(canonical: str) -> str:
    """Visszaadja, milyen típusú PDF mezőhöz kell a canonical-t rendelni."""
    if canonical in CHECKBOX_CANONICALS:
        return "checkbox"
    return "text"


def match_canonical_to_fields(
    sf_values: dict[str, str],
    fields: list[dict],
    existing_mapped: set[str],
    min_score: int = 5,
) -> dict[str, tuple[str, str]]:
    """
    Minden canonical mezőhöz, aminek van értéke, megkeresi a legjobban
    illeszkedő PDF mezőt a label alias-ok alapján.

    Szabályok:
      - Szöveges/numerikus canonical értékeket CSAK TEXT mezőkhöz rendeljük
        (a "Check Box NN" szakasz-jelölőket így automatikusan kihagyjuk).
      - A legjobb találat kötelezően egy meghatározott típusú mező legyen.
      - Az első nyertes mezőt "lefoglalja" a canonical — így egy név nem
        kerülhet több canonical-hoz sem.

    Args:
        sf_values: canonical_field → érték (a Salesforce-ból).
        fields: PDF mezők pozíció + label adatokkal.
        existing_mapped: már leképezett PDF mezőnevek halmaza
                         (ezeket nem írjuk felül).
        min_score: minimális alias-egyezési pontszám (hossz alapú).

    Returns:
        pdf_field_name → (canonical_field, confidence) dict.
    """
    # Előre normalizált alias-ok canonical kulcsonként
    norm_aliases: dict[str, list[str]] = {}
    for canonical, aliases in LABEL_ALIASES.items():
        norm_aliases[canonical] = [_norm(a) for a in aliases]

    # Mezőnként normalizált label + típus, csak a még leképezetlenek
    # (és csak olyan típus, ami a canonical-hoz illik).
    field_pool: dict[str, dict] = {}  # fname -> {norm_label, type}
    for f in fields:
        nm = f.get("name", "")
        if nm in existing_mapped:
            continue
        ftype = f.get("type", "text")
        field_pool[nm] = {
            "norm_label": _norm(f.get("nearby", "") + " " + f.get("label", "")),
            "type": ftype,
        }

    result: dict[str, tuple[str, str]] = {}

    # Canonical-okat specifikusság szerinti sorrendben dolgozzuk fel
    # (a hosszabb alias-sal rendelkezők előnyben) — így a specifikusabb
    # címke (pl. "havi nettó jövedelem") nem veszik el egy általánosabbal
    # (pl. "jövedelem") szemben.
    canonical_order = sorted(
        sf_values.keys(),
        key=lambda c: -max((len(a) for a in norm_aliases.get(c, [])), default=0),
    )

    for canonical in canonical_order:
        if canonical not in sf_values or not sf_values[canonical]:
            continue
        aliases = norm_aliases.get(canonical)
        if not aliases:
            continue
        wanted_type = _expected_pdf_type(canonical)

        best_field: Optional[str] = None
        best_score: float = 0.0
        for fname, info in field_pool.items():
            if info["type"] != wanted_type:
                continue
            norm_label = info["norm_label"]
            if not norm_label:
                continue
            score = 0.0
            for a in aliases:
                if not a:
                    continue
                if a in norm_label:
                    # Hosszabb alias = specifikusabb = magasabb pontszám
                    s = float(len(a))
                    if norm_label.strip() == a:
                        s += 50.0  # teljes egyezés bónusz
                    score = max(score, s)
            if score > best_score:
                best_score = score
                best_field = fname

        if best_field and best_score >= min_score:
            confidence = "high" if best_score >= 10 else "medium"
            result[best_field] = (canonical, confidence)
            # foglalt jelölése, hogy más canonical ne vehesse el
            field_pool.pop(best_field, None)

    return result


# =========================================================================
# 4. DeepSeek AI fallback (2. stratégia)
# =========================================================================

CANONICAL_GUIDE = """
Kanonikus mezők (lehetőségek) — ezeket preferáld, ha illeszkedik:
Személy (participant.*):
  - participant.name, participant.birth_name, participant.mother_name
  - participant.birth_place, participant.birth_date
  - participant.personal_id, participant.id_card_number, participant.tax_id
  - participant.phone, participant.email, participant.employer
  - participant.monthly_income, participant.dependents
  - participant.marital_status, participant.education, participant.citizenship
  - participant.address.full_address, participant.address.zip_code
  - participant.address.city, participant.address.street, participant.address.house_number
Ingatlan (property.*):
  - property.address.full_address, property.address.zip_code, property.address.city
  - property.address.street, property.address.house_number
  - property.parcel_number, property.area_sqm, property.property_type
  - property.estimated_value, property.year_built, property.number_of_rooms
Hitel / ügylet (loan.*):
  - loan.loan_amount, loan.loan_term_months, loan.loan_purpose, loan.product_name
Jogi (legal.*):
  - legal.signature_date, legal.signature_place, legal.signature_name
"""


def deepseek_chat(messages: list[dict], system: str | None = None,
                  max_tokens: int = 2000, json_mode: bool = True) -> str:
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
        timeout=180,
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


def ai_classify_batch(batch: list[dict], page_no: int, page_text: str) -> dict[str, dict]:
    """Oldalanként egy batch mező → DeepSeek → {name: {canonical, type, label}}."""
    fields_desc = []
    for f in batch:
        rect_str = ""
        if f.get("rect"):
            rect_str = (
                f" rect=[x0={f['rect'][0]:.0f}, y0={f['rect'][1]:.0f}, "
                f"x1={f['rect'][2]:.0f}, y1={f['rect'][3]:.0f}]"
            )
        fields_desc.append(
            f'- name="{f["name"]}" type={f["type"]} '
            f'nearby="{f.get("nearby", "")[:200]}"{rect_str}'
        )
    fields_block = "\n".join(fields_desc)

    system = (
        "Te egy magyar banki nyomtatvány mező-elemző AI vagy. Egy OTP Bank "
        "'Előzetes értékbecslés' PDF AcroForm mezőit osztályozod. A mezőnevek "
        "teljesen generikusak ('Check Box NNN' / 'Text Field NNN'), ezért a "
        "`nearby` szöveg és a pozíció alapján döntsd el, mit tartalmaznak.\n\n"
        f"{CANONICAL_GUIDE}\n\n"
        "Szabályok:\n"
        "1. CSAK JSON objektumot adj: "
        "{\"<pdf_field_name>\": {\"canonical\": \"...|null\", "
        "\"type\": \"text|checkbox|date|number\", \"label\": \"rövid magyar címke\"}, ...}\n"
        "2. `canonical` = a legjobban illeszkedő kanonikus mező, vagy `null` ha nem kell adattal kitölteni.\n"
        "3. Jogi / hozzájáruló / elfogadó checkbox-ok → canonical=null, type=checkbox.\n"
        "4. Ha egyértelműen személyre / ingatlanra / hitelre utal, "
        "a megfelelő canonical mezőre képezd le.\n"
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
            system=system,
            max_tokens=2000,
            json_mode=True,
        )
        if not text:
            return {}
        return extract_json_obj(text)
    except Exception as e:
        logger.warning("  DeepSeek hiba (page %d): %s", page_no, str(e)[:160])
        return {}


# =========================================================================
# 5. Mapping összeállítás + mentés
# =========================================================================

def is_generic(name: str) -> bool:
    nm = name.lower()
    return (
        nm.startswith("check box ") or nm.startswith("text field ")
        or nm.startswith("jelölőnégyzet ") or nm.startswith("szövegmező ")
    )


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


def build_mapping(
    fields: list[dict],
    sf_match: dict[str, tuple[str, str]],
    ai_match: dict[str, dict],
    existing: MappingConfig,
) -> MappingConfig:
    """
    Összefésüli a meglévő mapping-et a az új label-match és AI besorolásokkal.

    Sorrend:
      1. Meglévő canonical hozzárendeléseket MEGTARTJUK.
      2. Label-match találatokat hozzáadjuk (ahol még nincs canonical).
      3. AI besorolásokat hozzáadjuk a még mindig üres mezőkhöz.
    """
    # Gyors index: pdf_field_name → (canonical, confidence, source)
    new_assign: dict[str, tuple[Optional[str], str, str]] = {}
    for fname, (canonical, conf) in sf_match.items():
        new_assign[fname] = (canonical, conf, "salesforce-label-match")
    for fname, info in ai_match.items():
        if fname in new_assign:
            continue
        canonical = info.get("canonical")
        if not canonical:
            continue
        new_assign[fname] = (canonical, "medium", "ai-batch")

    recognized: list[RecognizedField] = []

    # Létező RecognizedField-eket dict-be tesszük név alapján
    existing_by_name: dict[str, RecognizedField] = {
        f.pdf_field_name: f for f in existing.fields
    }
    # Oldal/rect index a `fields` (PDF) listából, hogy koordinátát tudjunk adni
    pdf_by_name: dict[str, dict] = {f["name"]: f for f in fields}

    # Minden PDF mező feldolgozása
    all_names = list(pdf_by_name.keys())
    # Bekről érkező sorrend tartása
    for f in fields:
        name = f["name"]
        ex = existing_by_name.get(name)

        # 1. Ha van már canonical a meglévő mapping-ben, megtartjuk
        if ex and ex.canonical_field:
            recognized.append(_rebuild_field(ex, f, keep_existing=True))
            continue

        # 2. Label-match vagy AI besorolás
        assign = new_assign.get(name)
        if assign:
            canonical, conf, source = assign
            recognized.append(RecognizedField(
                pdf_field_name=name,
                label=_derive_label(f, canonical),
                field_type=FieldType(f.get("type", "text") or "text"),
                canonical_field=canonical,
                confidence=MappingConfidence(conf),
                page_number=f.get("page", 1),
                coordinates=_rect_to_coords(f.get("rect")),
                notes=f"{source}",
            ))
            continue

        # 3. Nem képezhető le — de legalább a koordinátát megtartjuk
        if ex:
            recognized.append(_rebuild_field(ex, f, keep_existing=False))
        else:
            recognized.append(RecognizedField(
                pdf_field_name=name,
                label=_derive_label(f, None),
                field_type=FieldType(f.get("type", "text") or "text"),
                canonical_field=None,
                confidence=MappingConfidence.LOW,
                page_number=f.get("page", 1),
                coordinates=_rect_to_coords(f.get("rect")),
                notes="nem képezhető le",
            ))

    mapped = sum(1 for r in recognized if r.canonical_field)
    sf_mapped = sum(1 for r in recognized if (r.notes or "").startswith("salesforce"))
    ai_mapped = sum(1 for r in recognized if (r.notes or "").startswith("ai"))

    notes = (
        f"Salesforce-based label matching + AI fallback "
        f"({len(recognized)} mező, {mapped} leképezve, "
        f"{sf_mapped} label-match, {ai_mapped} AI fallback)."
    )

    return MappingConfig(
        bank_name=existing.bank_name or "OTP Bank",
        form_name=existing.form_name or "elozetes_ertekbecsles",
        form_type=existing.form_type or "acroform",
        fields=recognized,
        page_structure=existing.page_structure or {},
        approved=False,
        approved_by=None,
        notes=notes,
    )


def _derive_label(f: dict, canonical: Optional[str]) -> str:
    """Rövid label-t ad a mezőhöz a nearby szövegből."""
    nearby = (f.get("nearby") or "").strip()
    if nearby:
        return nearby[:80]
    if canonical:
        return canonical.split(".")[-1]
    return f.get("name", "")


def _rebuild_field(ex: RecognizedField, pdf_f: dict, keep_existing: bool) -> RecognizedField:
    """Egy meglévő RecognizedField újraépítése a friss pozícióval."""
    return RecognizedField(
        pdf_field_name=ex.pdf_field_name,
        label=ex.label or _derive_label(pdf_f, ex.canonical_field),
        field_type=ex.field_type,
        canonical_field=ex.canonical_field if keep_existing else None,
        confidence=ex.confidence if keep_existing else MappingConfidence.LOW,
        page_number=ex.page_number or pdf_f.get("page", 1),
        coordinates=ex.coordinates or _rect_to_coords(pdf_f.get("rect")),
        notes=ex.notes,
    )


# =========================================================================
# Fő logika
# =========================================================================

def main(
    pdf_path: Path = DEFAULT_PDF,
    out_path: Path = DEFAULT_OUT,
    deal_id: str = DEFAULT_DEAL,
    use_ai: bool = True,
    batch_size: int = 40,
    ai_min_unmapped: int = 8,
) -> None:
    import fitz

    logger.info("PDF: %s", pdf_path)
    if not pdf_path.exists():
        raise SystemExit(f"Nem található: {pdf_path}")

    # --- Meglévő mapping betöltése ---
    existing = MappingConfig.load(out_path) if out_path.exists() else MappingConfig(
        bank_name="OTP Bank",
        form_name="elozetes_ertekbecsles",
        form_type="acroform",
        notes="üres kezdeti mapping",
    )
    existing_mapped = {
        f.pdf_field_name for f in existing.fields if f.canonical_field
    }
    logger.info(
        "Meglévő mapping: %d mező, %d már leképezve",
        len(existing.fields), len(existing_mapped),
    )

    # --- 1. Salesforce adatok ---
    logger.info("☁️  Salesforce adatlekérés: %s", deal_id)
    sf_values = fetch_sf_canonical_values(deal_id)
    logger.info("   Canonical mezők értékkel: %d", len(sf_values))
    for k, v in sorted(sf_values.items()):
        logger.info("     %s = %s", k, v[:60])

    # --- 2. PDF pozíciók + label-ek ---
    logger.info("📄 AcroForm mezők kinyerése...")
    fields = extract_fields_with_position(pdf_path)
    logger.info("   Összes mező: %d", len(fields))

    doc = fitz.open(str(pdf_path))
    extract_field_labels(doc, fields, radius=90.0)
    doc.close()

    # --- 3. Salesforce ↔ PDF label matching ---
    logger.info("🔗 3. stratégia: Salesforce-based label matching")
    sf_match = match_canonical_to_fields(sf_values, fields, existing_mapped)
    logger.info("   Label-match találatok: %d", len(sf_match))
    for fname, (canonical, conf) in sorted(sf_match.items()):
        logger.info("     %s → %s (%s)", fname, canonical, conf)

    # --- 4. AI fallback (2. stratégia) ---
    ai_match: dict[str, dict] = {}
    if use_ai and os.getenv("DEEPSEEK_API_KEY"):
        logger.info("🤖 2. stratégia: DeepSeek AI fallback (BATCH_SIZE=%d)", batch_size)
        # Csak olyan oldalakat küldünk, ahol még sok leképezetlen TEXT mező van
        still_unmapped_by_page: dict[int, list[dict]] = {}
        assigned_names = set(sf_match.keys()) | existing_mapped
        for f in fields:
            if f.get("type") != "text":
                continue
            if f["name"] in assigned_names:
                continue
            still_unmapped_by_page.setdefault(f.get("page", 1), []).append(f)

        total_batches = 0
        for page_no in sorted(still_unmapped_by_page.keys()):
            batch = still_unmapped_by_page[page_no]
            if len(batch) < ai_min_unmapped:
                continue
            logger.info(
                "  AI: %d. oldal, %d leképezetlen TEXT mező",
                page_no, len(batch),
            )
            page_text = batch[0].get("page_text", "")
            for i in range(0, len(batch), batch_size):
                sub = batch[i:i + batch_size]
                res = ai_classify_batch(sub, page_no, page_text)
                ai_match.update(res)
                total_batches += 1
                time.sleep(0.4)
            logger.info(
                "    eddig AI-besorolva: %d (ebből canonical: %d)",
                len(ai_match),
                sum(1 for v in ai_match.values() if v.get("canonical")),
            )
        logger.info(
            "   AI fallback kész: %d API hívás, %d besorolás (%d canonical)",
            total_batches,
            len(ai_match),
            sum(1 for v in ai_match.values() if v.get("canonical")),
        )
    elif use_ai:
        logger.warning(
            "   DEEPSEEK_API_KEY hiányzik – AI fallback kihagyva "
            "(csak label matching eredmények mentve)."
        )

    # --- 5. Mapping építés + mentés ---
    logger.info("🧩 Mapping összeállítása...")
    mapping = build_mapping(fields, sf_match, ai_match, existing)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    mapping.save(out_path)
    logger.info("💾 Mentve: %s", out_path)

    # --- Összesítés ---
    mapped = sum(1 for r in mapping.fields if r.canonical_field)
    sf_mapped = sum(1 for r in mapping.fields if (r.notes or "").startswith("salesforce"))
    ai_mapped = sum(1 for r in mapping.fields if (r.notes or "").startswith("ai"))
    cb = sum(1 for r in mapping.fields if r.field_type == FieldType.CHECKBOX)

    print("\n" + "=" * 60)
    print("📊 Előzetes értékbecslés – Mapping eredmény")
    print("=" * 60)
    print(f"PDF sablon:        {pdf_path.name}")
    print(f"Salesforce deal:   {deal_id}")
    print(f"SF canonical adat: {len(sf_values)} mező")
    print(f"Label-match találat:{len(sf_match):4}")
    print(f"AI fallback találat:{len(ai_match):4}")
    print("-" * 60)
    print(f"Összes mező:       {len(mapping.fields)}")
    print(f"Leképezve (canonical):{mapped:4}")
    print(f"  - label-match:   {sf_mapped:4}")
    print(f"  - AI fallback:   {ai_mapped:4}")
    print(f"Checkbox (Btn):    {cb:4}")
    print(f"Output:            {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--deal", type=str, default=DEFAULT_DEAL)
    ap.add_argument("--no-ai", action="store_true",
                    help="Csak label matching, AI fallback kihagyása")
    ap.add_argument("--batch-size", type=int, default=40)
    ap.add_argument("--ai-min-unmapped", type=int, default=8,
                    help="Min. leképezetlen TEXT mező oldalon az AI futáshoz")
    args = ap.parse_args()
    main(
        pdf_path=args.pdf,
        out_path=args.out,
        deal_id=args.deal,
        use_ai=not args.no_ai,
        batch_size=args.batch_size,
        ai_min_unmapped=args.ai_min_unmapped,
    )

"""
FinancialGenie – AI mezőfelismerő réteg

DeepSeek V4 Flash API-val felismeri egy üres banki nyomtatvány kitöltendő mezőit,
és leképezi azokat a kanonikus adatmodellre. Az eredmény egy mapping-konfiguráció,
amelyet emberi jóváhagyás után a determinisztikus kitöltőmotor használ.

Ez a komponens teszi gyorsan bővíthetővé a rendszert további bankokra:
új nyomtatvány → AI felismerés → mapping → jóváhagyás → kész.

Használat:
    python -m src.ai.field_recognizer samples/acroform_sample.pdf
    python -m src.ai.field_recognizer samples/flat_sample.pdf --mode overlay
"""

import json
import logging
import base64
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


#: A DeepSeek API-hoz használt modell azonosító (app-level DeepSeek V4 migráció).
#: V4 Pro-t használunk minden AI híváshoz – nagyobb kontextusablak (64K vs 8K),
#: megbízhatóbb JSON mód és pontosabb magyar nyelvű label-felismerés.
#: Lassabb, de a klasszifikációs feladatra a pontosság a lényeg.
AI_MODEL: str = "deepseek-v4-pro"


def _normalize_key(s: str) -> str:
    """
    Ékezet- és formátum-agnosztikus kulcs-normalizálás (2b).

    A magyar nyomtatványok PDF-mezőnevei gyakran tartalmaznak ékezeteket
    (`név`, `állandó_lakcím`), és ezek kódolása (NFC vs. NFD), valamint a
    betűméret/kiemelés változhat a PDF generátorától függően. Ez a helper:
      1. Unicode NORMAL FORM D-ra bontja a stringet (szétválasztja a
         kombináló jeleket az alap karakterektől),
      2. Eldobja a kombináló jeleket (csak az alap karakterek maradnak),
      3. Kisbetűsíti az egészet.

    Így `"SZA_IG_név"`, `"SZA_IG_nev"`, `"sza_ig_név"` mind ugyanarra a
    canonical kulcsra ("sza_ig_nev") képezhető.
    """
    if s is None:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    no_accents = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return no_accents.lower()


# --- A kanonikus modell mezőkatalógusa ---

CANONICAL_FIELDS = {
    # Hiteladatok
    "loan.loan_amount": "Hitelösszeg (Ft)",
    "loan.loan_term_months": "Futamidő (hónap)",
    "loan.interest_period": "Kamatperiódus",
    "loan.loan_purpose": "Hitel célja",
    "loan.product_name": "Termék neve",
    "loan.down_payment": "Önerő (Ft)",
    "loan.monthly_payment": "Havi törlesztő (Ft)",

    # Szereplő adatok (prefix: participant[i])
    "participant.name": "Szereplő teljes neve",
    "participant.birth_name": "Születési név",
    "participant.mother_name": "Anyja neve",
    "participant.birth_place": "Születési hely",
    "participant.birth_date": "Születési dátum",
    "participant.personal_id": "Személyi igazolvány szám",
    "participant.tax_id": "Adóazonosító jel",
    "participant.id_card_number": "Személyi szám",
    "participant.address.full_address": "Állandó lakcím (teljes)",
    "participant.address.zip_code": "Irányítószám",
    "participant.address.city": "Település",
    "participant.address.street": "Utca",
    "participant.address.house_number": "Házszám",
    "participant.phone": "Telefonszám",
    "participant.email": "E-mail cím",
    "participant.employer": "Munkáltató",
    "participant.monthly_income": "Havi nettó jövedelem",
    "participant.role": "Szerep (adós/adóstárs/kezes/haszonélvező)",

    # Ingatlan adatok (prefix: property[i])
    "property.address.full_address": "Ingatlan címe (teljes)",
    "property.address.zip_code": "Ingatlan irányítószám",
    "property.address.city": "Ingatlan település",
    "property.address.street": "Ingatlan utca",
    "property.address.house_number": "Ingatlan házszám",
    "property.parcel_number": "Helyrajzi szám",
    "property.area_sqm": "Terület (m²)",
    "property.property_type": "Ingatlan típusa",
    "property.estimated_value": "Becsült érték (Ft)",
    "property.year_built": "Építés éve",
    "property.number_of_rooms": "Szobák száma",
}


def _get_available_deal_fields(deal_data) -> list[str]:
    """
    Visszaadja azokat a canonical mezőket (CANONICAL_FIELDS kulcsok),
    amelyekhez a deal-ben ténylegesen van adat.

    Ez a "data-first" szűrő a dinamikus mapping-hez: csak ezekhez
    érdemes AI mapping-et készíteni, különben a PDF üres mezőket
    töltenénk ki.
    """
    available: list[str] = []
    if deal_data is None:
        return available

    # Hiteladatok
    loan = getattr(deal_data, "loan", None)
    loan_checks = {
        "loan.loan_amount": getattr(loan, "loan_amount", None),
        "loan.loan_term_months": getattr(loan, "loan_term_months", None),
        "loan.interest_period": getattr(loan, "interest_period", None),
        "loan.loan_purpose": getattr(loan, "loan_purpose", None),
        "loan.product_name": getattr(loan, "product_name", None),
        "loan.product_type": getattr(loan, "product_type", None),
        "loan.down_payment": getattr(loan, "down_payment", None),
        "loan.monthly_payment": getattr(loan, "monthly_payment", None),
        "loan.purchase_price": getattr(loan, "purchase_price", None),
        "loan.csok_amount": getattr(loan, "csok_amount", None),
        "loan.afa_support": getattr(loan, "afa_support", None),
        "loan.housing_savings": getattr(loan, "housing_savings", None),
        "loan.refinance_account": getattr(loan, "refinance_account", None),
    }
    for cf, val in loan_checks.items():
        if val not in (None, "", 0):
            available.append(cf)

    # Szereplő adatok (első adós + első adóstárs)
    for participant in getattr(deal_data, "active_participants", [])[:2]:
        prefix = "participant"
        p_checks = {
            f"{prefix}.name": getattr(participant, "name", None),
            f"{prefix}.birth_name": getattr(participant, "birth_name", None),
            f"{prefix}.mother_name": getattr(participant, "mother_name", None),
            f"{prefix}.birth_place": getattr(participant, "birth_place", None),
            f"{prefix}.birth_date": getattr(participant, "birth_date", None),
            f"{prefix}.personal_id": getattr(participant, "personal_id", None),
            f"{prefix}.tax_id": getattr(participant, "tax_id", None),
            f"{prefix}.id_card_number": getattr(participant, "id_card_number", None),
            f"{prefix}.phone": getattr(participant, "phone", None),
            f"{prefix}.email": getattr(participant, "email", None),
            f"{prefix}.employer": getattr(participant, "employer", None),
            f"{prefix}.monthly_income": getattr(participant, "monthly_income", None),
        }
        address = getattr(participant, "address", None)
        if address is not None:
            p_checks[f"{prefix}.address.full_address"] = getattr(address, "full_address", None)
            p_checks[f"{prefix}.address.zip_code"] = getattr(address, "zip_code", None)
            p_checks[f"{prefix}.address.city"] = getattr(address, "city", None)
            p_checks[f"{prefix}.address.street"] = getattr(address, "street", None)
            p_checks[f"{prefix}.address.house_number"] = getattr(address, "house_number", None)
        for cf, val in p_checks.items():
            if val not in (None, "", 0) and cf not in available:
                available.append(cf)

    # Ingatlan adatok (első ingatlan)
    properties = getattr(deal_data, "properties", []) or []
    if properties:
        prop = properties[0]
        pr_checks = {
            "property.parcel_number": getattr(prop, "parcel_number", None),
            "property.area_sqm": getattr(prop, "area_sqm", None),
            "property.estimated_value": getattr(prop, "estimated_value", None),
            "property.year_built": getattr(prop, "year_built", None),
            "property.number_of_rooms": getattr(prop, "number_of_rooms", None),
        }
        paddress = getattr(prop, "address", None)
        if paddress is not None:
            pr_checks["property.address.full_address"] = getattr(paddress, "full_address", None)
            pr_checks["property.address.zip_code"] = getattr(paddress, "zip_code", None)
            pr_checks["property.address.city"] = getattr(paddress, "city", None)
            pr_checks["property.address.street"] = getattr(paddress, "street", None)
        for cf, val in pr_checks.items():
            if val not in (None, "", 0) and cf not in available:
                available.append(cf)

    return available


class FieldType(str, Enum):
    """PDF mező típusa."""
    TEXT = "text"
    CHECKBOX = "checkbox"
    DATE = "date"
    NUMBER = "number"
    DROPDOWN = "dropdown"


class MappingConfidence(str, Enum):
    """AI mapping-bizonyosság szintje."""
    HIGH = "high"       # 90%+ biztos
    MEDIUM = "medium"   # 60-90% biztos
    LOW = "low"         # 60% alatt – emberi felülvizsgálat kell


@dataclass
class RecognizedField:
    """Egy felismert PDF mező az AI által."""
    pdf_field_name: str                    # Mező neve a PDF-ben (AcroForm) vagy azonosító
    label: str                             # A mező felirata a nyomtatványon
    field_type: FieldType                  # Mező típusa
    canonical_field: Optional[str]         # Leképezés a kanonikus modellre
    confidence: MappingConfidence          # AI bizonyosság
    page_number: int                       # Melyik oldalon van
    coordinates: Optional[dict] = None     # Overlay módhoz: {x, y, width, height}
    notes: Optional[str] = None            # AI megjegyzés


@dataclass
class MappingConfig:
    """
    Teljes mapping-konfiguráció egy nyomtatványhoz.
    Ez az AI output, amit emberi jóváhagyás után a kitöltőmotor használ.
    """
    bank_name: str
    form_name: str
    form_type: str                         # "acroform" vagy "flat"
    fields: list[RecognizedField] = field(default_factory=list)
    page_structure: dict = field(default_factory=dict)  # Melyik oldal milyen típusú
    approved: bool = False                 # Emberi jóváhagyás megtörtént-e
    approved_by: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        """Szeriálizáció JSON-ba."""
        return {
            "bank_name": self.bank_name,
            "form_name": self.form_name,
            "form_type": self.form_type,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "notes": self.notes,
            "page_structure": self.page_structure,
            "fields": [
                {
                    "pdf_field_name": f.pdf_field_name,
                    "label": f.label,
                    "field_type": f.field_type.value,
                    "canonical_field": f.canonical_field,
                    "confidence": f.confidence.value,
                    "page_number": f.page_number,
                    "coordinates": f.coordinates,
                    "notes": f.notes,
                }
                for f in self.fields
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MappingConfig":
        """Deszeriálizáció JSON-ból."""
        fields = [
            RecognizedField(
                pdf_field_name=f["pdf_field_name"],
                label=f["label"],
                field_type=FieldType(f["field_type"]),
                canonical_field=f.get("canonical_field"),
                confidence=MappingConfidence(f["confidence"]),
                page_number=f["page_number"],
                coordinates=f.get("coordinates"),
                notes=f.get("notes"),
            )
            for f in data.get("fields", [])
        ]
        return cls(
            bank_name=data["bank_name"],
            form_name=data["form_name"],
            form_type=data["form_type"],
            fields=fields,
            page_structure=data.get("page_structure", {}),
            approved=data.get("approved", False),
            approved_by=data.get("approved_by"),
            notes=data.get("notes"),
        )

    def save(self, path: Path):
        """Mentés JSON fájlba."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"Mapping konfiguráció mentve: {path}")

    @classmethod
    def load(cls, path: Path) -> "MappingConfig":
        """Betöltés JSON fájlból."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @property
    def high_confidence_fields(self) -> list[RecognizedField]:
        return [f for f in self.fields if f.confidence == MappingConfidence.HIGH]

    @property
    def needs_review_fields(self) -> list[RecognizedField]:
        return [f for f in self.fields if f.confidence != MappingConfidence.HIGH]

    @property
    def mapping_dict(self) -> dict[str, str]:
        """PDF mező → kanonikus mező leképezés (csak jóváhagyott, high confidence)."""
        return {
            f.pdf_field_name: f.canonical_field
            for f in self.fields
            if f.canonical_field is not None
        }


class FieldRecognizer:
    """
    AI-alapú mezőfelismerő – DeepSeek V4 Flash API-val.

    Egy üres PDF nyomtatványt elemez, felismeri a kitöltendő mezőket,
    és leképezi azokat a kanonikus adatmodellre.
    """

    SYSTEM_PROMPT = """Te egy banki nyomtatvány-elemző AI vagy. A feladatod:

1. Elemezd az üres banki nyomtatvány képét/tartalmát
2. Azonosítsd az összes kitöltendő mezőt (név, cím, dátum, stb.)
3. Mindegyik mezőt képezd le a kanonikus adatmodell megfelelő mezőjére

A kanonikus modell mezői:
{canonical_fields}

Válaszolj JSON formátumban az alábbi struktúrával:
{{
  "form_name": "A nyomtatvány neve",
  "page_structure": {{
    "1": "global",     // minden ügyletnél egyszer
    "2": "per_participant",  // szereplőnként ismétlődik
    "3": "per_property"      // ingatlanonként ismétlődik
  }},
  "fields": [
    {{
      "pdf_field_name": "a mező neve vagy azonosítója a PDF-ben",
      "label": "a mező felirata magyarul",
      "field_type": "text|checkbox|date|number|dropdown",
      "canonical_field": "a kanonikus modell megfelelő mezője (pl. participant.name)",
      "confidence": "high|medium|low",
      "page_number": 1,
      "notes": "opcionális megjegyzés"
    }}
  ]
}}

Fontos szabályok:
- Ha a mező szereplő-specifikus, használd a "participant." prefixet
- Ha ingatlan-specifikus, használd a "property." prefixet
- Ha nem vagy biztos a leképezésben, jelöld "low" confidence-szel
- Az ismétlődő blokkok (pl. adós, adóstárs) ugyanazokra a participant mezőkre képeződnek"""

    def __init__(self, api_key: str = None):
        """
        Inicializálás DeepSeek API kulccsal.

        Args:
            api_key: DeepSeek API kulcs. Ha None, a DEEPSEEK_API_KEY env-ből olvassa.
        """
        import os

        if api_key is None:
            api_key = os.getenv("DEEPSEEK_API_KEY", "")

        self._api_key = api_key or ""
        # A `_client` flag megőrzi a korábbi "van-e elérhető AI" szematikát:
        # a pipeline多处 `if self._client:` ellenőrzésekkel dönti el, hogy
        # fusson-e az AI ág vagy a heurisztikus/mock fallback.
        if not self._api_key:
            logger.warning(
                "DEEPSEEK_API_KEY nincs beállítva. "
                "Az AI mezőfelismerés nem fog működni – használd a mock módot."
            )
            self._client = None
        else:
            self._client = True

    # ------------------------------------------------------------------
    # DeepSeek API helper (chat/completions, OpenAI-kompatibilis formátum)
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_content(blocks: list) -> list:
        """
        Anthropic-style content blokkok → OpenAI/DeepSeek chat-completions
        content formátum. Képeket base64 data-URI-ként (image_url) küldünk.
        """
        out = []
        for b in blocks:
            t = b.get("type")
            if t == "text":
                out.append({"type": "text", "text": b["text"]})
            elif t == "image":
                src = b.get("source", {}) or {}
                media = src.get("media_type", "image/png")
                data = src.get("data", "")
                out.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{media};base64,{data}"},
                })
        return out

    def _deepseek_chat(
        self,
        messages: list,
        system: str | None = None,
        max_tokens: int = 8192,
        json_mode: bool = True,
    ) -> str:
        """
        Egy DeepSeek chat/completions hívás. Visszaadja az assistant válasz
        szövegét, vagy üres stringet hiba esetén.

        Args:
            messages: user/assistant üzenetek (content lehet str vagy blokk-lista).
            system: opcionális system prompt.
            max_tokens: válasz token limit.
            json_mode: ha True, `response_format: {"type": "json_object"}`-t küld.
                Megköveteli, hogy a prompt JSON objektumot kérjen (a hívó
                promptjai mind tartalmaznak JSON instrukciót).
        """
        import requests

        full_messages = []
        if system:
            full_messages.append({"role": "system", "content": system})

        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")
            if isinstance(content, list):
                content = self._convert_content(content)
            full_messages.append({"role": role, "content": content})

        # Detektáljuk van-e kép input – `response_format: json_object`
        # nem kompatibilis az image inputtal (DeepSeek hibát dob).
        has_images = any(
            isinstance(m.get("content"), list)
            and any(b.get("type") == "image" for b in m["content"])
            for m in messages
        )

        payload = {
            "model": AI_MODEL,
            "messages": full_messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            # DeepSeek V4 Flash thinking mode alapból ENABLED, ami felemészti a
            # max_tokens keretet reasoning-ra és csonka választ eredményez.
            # Kikapcsolása determinisztikus JSON outputot garantál.
            "thinking": {"type": "disabled"},
        }
        if json_mode and not has_images:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = requests.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=120,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"] or ""
        except Exception as e:
            logger.error(f"DeepSeek API hiba: {str(e)[:160]}")
            return ""

    def recognize_acroform(self, pdf_path: Path) -> MappingConfig:
        """
        AcroForm PDF mezőinek felismerése.

        Először kinyeri a PDF form mezőneveit (pikepdf-el),
        majd az AI segítségével leképezi a kanonikus modellre.
        """
        import pikepdf

        logger.info(f"AcroForm mezőfelismerés: {pdf_path}")

        # 1. AcroForm mezők kinyerése
        pdf_fields = self._extract_acroform_fields(pdf_path)
        logger.info(f"  {len(pdf_fields)} AcroForm mező találva")

        if not pdf_fields:
            logger.warning("Nem találtam AcroForm mezőket – lehet, hogy lapos PDF?")
            return self._empty_mapping(pdf_path, "acroform")

        # 2. Heurisztikus alap-leképezés (mindig fut, OTP exact map-el)
        base_mapping = self._heuristic_map_fields(pdf_fields, pdf_path, "acroform")

        # 3. AI leképezés – batch módban ha sok a mező
        if self._client:
            if len(pdf_fields) > 200:
                ai_mapping = self._ai_batch_recognize(pdf_path, pdf_fields)
            else:
                ai_mapping = self._ai_map_fields(pdf_path, pdf_fields, "acroform")

            if ai_mapping:
                # AI eredmények összefésülése a heurisztikussal
                base_mapping = self._merge_mappings(base_mapping, ai_mapping)

        return base_mapping

    def _ai_batch_recognize(
        self, pdf_path: Path, pdf_fields: list[dict]
    ) -> MappingConfig | None:
        """
        Batch AI felismerés nagy nyomtatványokhoz.
        A mezőket szekciókra bontja és külön-külön küldi el Claude-nak.
        """
        from collections import defaultdict
        import time

        logger.info(f"🔄 Batch AI felismerés ({len(pdf_fields)} mező)")

        # Mezők csoportosítása prefix szerint
        groups = defaultdict(list)
        for f in pdf_fields:
            name = f["name"]
            # Prefix meghatározása
            if name.startswith("SZA_IG_"):
                # Igénylő vs társigénylő szétválasztás
                if "-társ" in name:
                    groups["SZA_társigénylő"].append(f)
                else:
                    groups["SZA_igénylő"].append(f)
            elif name.startswith("CSOKPLUSZ"):
                groups["CSOK_Plusz"].append(f)
            elif name.startswith("CSOK_"):
                groups["CSOK"].append(f)
            elif name.startswith("TA_"):
                groups["Társadós"].append(f)
            elif name.startswith("AFA"):
                groups["ÁFA"].append(f)
            elif name.startswith("IA_"):
                groups["Ingatlan"].append(f)
            elif name.startswith(("KTKA", "LAHI", "LAKHK")):
                groups["Hitel_költség"].append(f)
            elif name.startswith("A_NY_") or name.startswith("A NY_") or name.startswith("ANY_"):
                groups["Általános_nyilatkozat"].append(f)
            elif name.startswith("MA_"):
                groups["Meghatalmazás"].append(f)
            elif name.startswith(("videk", "otthon")):
                groups["Vidéki_otthon"].append(f)
            else:
                groups["Egyéb"].append(f)

        logger.info(f"  {len(groups)} szekció: " +
                    ", ".join(f"{k}({len(v)})" for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))))

        # PDF oldalak képei (egyszer generáljuk)
        images = self._pdf_to_images(pdf_path)

        # Kanonikus mezők leírása
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        all_fields = []
        batch_count = 0
        MAX_FIELDS_PER_BATCH = 80

        for section_name, section_fields in sorted(groups.items(), key=lambda x: -len(x[1])):
            if len(section_fields) == 0:
                continue

            # Nagy szekciók felosztása sub-batchekre
            sub_batches = []
            for i in range(0, len(section_fields), MAX_FIELDS_PER_BATCH):
                sub_batches.append(section_fields[i:i + MAX_FIELDS_PER_BATCH])

            for sub_idx, sub_fields in enumerate(sub_batches):
                batch_count += 1
                sub_label = f"{section_name}" if len(sub_batches) == 1 else f"{section_name} ({sub_idx+1}/{len(sub_batches)})"
                logger.info(f"  📦 [{batch_count}] {sub_label}: {len(sub_fields)} mező...")

                # Releváns oldalak kiválasztása (max 5/batch a token megtakarítás érdekében)
                pages = set()
                for f in sub_fields:
                    p = f.get("page", 1)
                    if isinstance(p, int):
                        pages.add(p - 1)  # 0-indexed
                relevant_pages = sorted(pages)[:5]

                # Kompakt system prompt – minimális output kérés
                system = f"""Te egy banki nyomtatvány mező-elemző AI vagy.

A feladatod: párosítsd az AcroForm PDF mezőneveket az alábbi kanonikus mezőkkel.

Kanonikus mezők:
{canonical_desc}

FONTOS SZABÁLYOK:
1. Csak JSON tömböt adj válaszul, más szöveget NE
2. Minden elem: {{"f": "pdf_mező_neve", "c": "kanonikus.mező", "t": "text|checkbox|date|number", "p": oldalszám}}
3. Ha egy mező NEM képezhető le egyetlen kanonikus mezőre sem, HAGYD KI a listából
4. Használj tömör, pontos leképezéseket
5. A "participant." prefix alatt személyi adatok vannak, "loan." alatt hiteladatok, "property." alatt ingatlan adatok

Ez a(z) '{section_name}' szekció."""

                content = []

                # Releváns oldal képek (max 5)
                for idx in relevant_pages:
                    if idx < len(images):
                        content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": images[idx]},
                        })
                        content.append({"type": "text", "text": f"{idx+1}. oldal"})

                # Szekció mezőnevei – kompakt formátum
                fields_text = f"AcroForm mezők ({len(sub_fields)} db):\n"
                for f in sub_fields:
                    fields_text += f"  {f['name']} | {f['type']} | p{f.get('page', '?')}\n"
                content.append({"type": "text", "text": fields_text})

                try:
                    response_text = self._deepseek_chat(
                        messages=[{"role": "user", "content": content}],
                        system=system,
                        max_tokens=8192,
                        json_mode=False,
                    )

                    # Válasz feldolgozás
                    if response_text:
                        try:
                            json_str = self._extract_json(response_text)
                            ai_items = json.loads(json_str)
                            # Kompakt formátum → RecognizedField
                            # (json_mode=False: válasz lehet JSON tömb.)
                            if isinstance(ai_items, dict):
                                ai_items = list(ai_items.values())
                            for item in ai_items:
                                if isinstance(item, dict) and item.get("c"):
                                    all_fields.append(RecognizedField(
                                        pdf_field_name=item.get("f", ""),
                                        label=item.get("f", "").replace("_", " ").title(),
                                        field_type=FieldType(item.get("t", "text")),
                                        canonical_field=item["c"],
                                        confidence=MappingConfidence.MEDIUM,
                                        page_number=item.get("p", 1),
                                    ))
                            mapped = sum(1 for item in ai_items if isinstance(item, dict) and item.get("c"))
                            logger.info(f"    ✅ {mapped} mező leképezve")
                        except (json.JSONDecodeError, ValueError) as e:
                            logger.warning(f"    ⚠️ JSON hiba: {str(e)[:80]}")
                    else:
                        logger.warning(f"    ⚠️ Üres válasz")

                except Exception as e:
                    logger.error(f"    ❌ API hiba: {str(e)[:80]}")

                # Rate limit
                time.sleep(1)

        if not all_fields:
            logger.warning("Batch AI: egyetlen szekció sem adott eredményt")
            return None

        total_mapped = sum(1 for f in all_fields if f.canonical_field)
        logger.info(f"🏁 Batch AI összesítés: {total_mapped}/{len(all_fields)} mező leképezve ({batch_count} API hívás)")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="acroform",
            fields=all_fields,
            notes=f"AI batch felismerés ({len(groups)} szekció, {batch_count} API hívás).",
        )

    def _merge_mappings(
        self, base: MappingConfig, ai: MappingConfig
    ) -> MappingConfig:
        """Heurisztikus és AI mapping összefésülése. AI felülírja a heurisztikust."""
        # AI mezők index-be
        ai_by_name = {f.pdf_field_name: f for f in ai.fields}

        merged = []
        for base_field in base.fields:
            if base_field.pdf_field_name in ai_by_name:
                ai_field = ai_by_name[base_field.pdf_field_name]
                # AI felülírja ha van canonical_field
                if ai_field.canonical_field:
                    merged.append(ai_field)
                elif base_field.canonical_field:
                    merged.append(base_field)
                else:
                    merged.append(ai_field)  # AI label/notes még hasznos
            else:
                merged.append(base_field)

        # AI-ból ami nincs a base-ben
        base_names = {f.pdf_field_name for f in base.fields}
        for ai_field in ai.fields:
            if ai_field.pdf_field_name not in base_names:
                merged.append(ai_field)

        total_mapped = sum(1 for f in merged if f.canonical_field)
        logger.info(f"📊 Merge eredmény: {total_mapped}/{len(merged)} mező leképezve")

        return MappingConfig(
            bank_name=base.bank_name or ai.bank_name,
            form_name=base.form_name or ai.form_name,
            form_type=base.form_type,
            fields=merged,
            page_structure=ai.page_structure or base.page_structure,
            notes=f"Összefésült mapping: heurisztikus + AI ({total_mapped} leképezve).",
        )

    def recognize_flat(self, pdf_path: Path) -> MappingConfig:
        """
        Lapos (nem kitölthető) PDF mezőinek felismerése.

        Kétlépéses megközelítés:
        1. Text-anchor: a PDF struktúrából pontozott sorok (……) pozícióinak
           kinyerése – pixel-pontos koordináták
        2. AI szemantikus: Claude párosítja a label + kontextust a kanonikus mezőkkel
        """
        import fitz
        logger.info(f"Lapos PDF mezőfelismerés (text-anchor): {pdf_path}")

        # 1. Kitöltendő mezők kinyerése a PDF szöveg struktúrájából
        anchor_fields = self._extract_text_anchors(pdf_path)
        logger.info(f"  {len(anchor_fields)} kitöltendő mező találva (pontozott sorok)")

        if not anchor_fields:
            logger.warning("  Nem találtam pontozott sorokat – fallback AI módra")
            if self._client:
                return self._ai_recognize_flat_pdf(pdf_path)
            return self._mock_flat_recognition(pdf_path)

        # 2. AI szemantikus mapping (label → kanonikus mező)
        if self._client:
            canonical_map = self._ai_map_flat_labels(anchor_fields, pdf_path)
        else:
            canonical_map = {}

        # 3. RecognizedField-ek összeállítása pixel-pontos koordinátákkal
        fields = []
        for af in anchor_fields:
            canonical = canonical_map.get(af["id"], None)
            fields.append(RecognizedField(
                pdf_field_name=af["id"],
                label=af["label"],
                field_type=FieldType.TEXT,
                canonical_field=canonical,
                confidence=MappingConfidence.HIGH if canonical else MappingConfidence.LOW,
                page_number=af["page"],
                coordinates={
                    "x": af["x"],
                    "y": af["y"],
                    "width": af["width"],
                    "height": af["font_size"],
                },
                notes=af.get("context", ""),
            ))

        mapped = sum(1 for f in fields if f.canonical_field)
        logger.info(f"  ✅ {mapped}/{len(fields)} mező leképezve (text-anchor + AI)")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="flat",
            fields=fields,
            notes=f"Text-anchor felismerés: {len(fields)} mező, {mapped} leképezve.",
        )

    def _extract_text_anchors(self, pdf_path: Path) -> list[dict]:
        """
        PDF struktúrából kitöltendő mezők kinyerése.
        A pontozott sorok (……) jelölik a kitöltendő területeket.
        Pixel-pontos koordinátákat ad vissza.
        """
        import fitz
        import re

        doc = fitz.open(str(pdf_path))
        fields = []
        field_id = 0

        for page_idx, page in enumerate(doc):
            text_dict = page.get_text("dict")
            all_spans = []

            for block in text_dict["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        all_spans.append(span)

            for i, span in enumerate(all_spans):
                text = span["text"]
                if "…" not in text:
                    continue

                bbox = span["bbox"]  # [x0, y0, x1, y1]
                font_size = span["size"]

                # Pontsor pozíció
                dot_match = re.search(r'[…]{2,}', text)
                if not dot_match:
                    continue

                pre_text = text[:dot_match.start()].strip()
                post_text = text[dot_match.end():].strip("() ,;. ")

                # Pontsor x pozíciójának precíz meghatározása
                # Ha van pre_text, a pontsor annak VÉGÉTŐL indul
                if pre_text:
                    # Számítsuk ki a pre_text szélességét a font méretéből
                    char_w = font_size * 0.48
                    fill_x = bbox[0] + len(pre_text) * char_w + char_w
                else:
                    fill_x = bbox[0]

                fill_y = bbox[1]
                fill_w = bbox[2] - fill_x

                # Kontextus: előtte + utána lévő szöveg (5 span)
                context_parts = []
                for j in range(max(0, i-2), min(len(all_spans), i+3)):
                    s = all_spans[j]
                    t = s["text"].strip()
                    if t and "…" not in t:
                        context_parts.append(t)
                context = " ".join(context_parts)[:200]

                # Label keresése – sorrendben:
                label = ""

                # 1. Közvetlenül utána dőlt span (zárójelben, pl. "(Hiteligénylő neve)")
                for j in range(i+1, min(i+3, len(all_spans))):
                    s = all_spans[j]
                    if abs(s["bbox"][1] - bbox[1]) > 5:
                        break  # Más soron van
                    if "Ital" in s.get("font", ""):
                        candidate = s["text"].strip("() ,;.")
                        if len(candidate) > 2 and "…" not in candidate:
                            label = candidate
                            break

                # 2. Pre-text (pl. "Kelt:", "Név:", "Születési hely")
                if not label and pre_text:
                    clean = pre_text.rstrip(":").strip()
                    if len(clean) > 1 and "…" not in clean:
                        label = clean

                # 3. Post-text ha nem pontsor
                if not label and post_text and len(post_text) > 2 and "…" not in post_text:
                    label = post_text

                # 4. Kontextus alapú fallback
                if not label:
                    label = f"mező_{field_id + 1}"

                field_id += 1
                fields.append({
                    "id": f"flat_{page_idx+1}_{field_id}",
                    "label": label,
                    "x": round(fill_x, 1),
                    "y": round(fill_y, 1),
                    "width": round(max(fill_w, 20), 1),
                    "font_size": round(font_size, 1),
                    "page": page_idx + 1,
                    "context": context,
                })

        doc.close()
        return fields

    def _ai_map_flat_labels(
        self, anchor_fields: list[dict], pdf_path: Path
    ) -> dict:
        """
        AI szemantikus mapping: label + kontextus → kanonikus mező.
        Csak egyszer hívja az AI-t, nem küld képeket – tisztán szöveges.
        """
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        fields_desc = "\n".join(
            f'  - id="{f["id"]}" label="{f["label"]}" context="{f["context"][:100]}"'
            for f in anchor_fields
        )

        prompt = f"""Párosítsd az alábbi banki nyomtatvány mezőket a kanonikus mezőkkel.

Kanonikus mezők:
{canonical_desc}

Nyomtatvány mezők:
{fields_desc}

Válaszolj CSAK JSON objektummal: {{"mező_id": "kanonikus.mező", ...}}
Ha egy mező nem képezhető le, hagyd ki.
PDF: {pdf_path.name}"""

        try:
            response_text = self._deepseek_chat(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                json_mode=True,
            )

            if response_text:
                json_str = self._extract_json(response_text)
                return json.loads(json_str)
        except Exception as e:
            logger.warning(f"  AI mapping hiba: {str(e)[:80]}")

        return {}

    def recognize(self, pdf_path: Path, mode: str = "auto") -> MappingConfig:
        """
        Automatikus felismerés: megpróbálja kideríteni a PDF típusát.

        Args:
            pdf_path: PDF fájl elérési útja
            mode: "auto", "acroform", vagy "overlay"
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF nem található: {pdf_path}")

        if mode == "acroform":
            return self.recognize_acroform(pdf_path)
        elif mode == "overlay":
            return self.recognize_flat(pdf_path)
        else:
            # Auto-detect: van-e AcroForm mező?
            fields = self._extract_acroform_fields(pdf_path)
            if fields:
                logger.info("AcroForm mezők találva – AcroForm módban folytatom")
                return self.recognize_acroform(pdf_path)
            else:
                logger.info("Nincs AcroForm mező – lapos PDF módban folytatom")
                return self.recognize_flat(pdf_path)

    # ------------------------------------------------------------------
    # Dinamikus klasszifikáció (V4 Pro) – futási idejű mezőfelismerés
    # ------------------------------------------------------------------
    #
    # Ezek a metódusok akkor hívódnak, ha egy PDF-hez nincs előre
    # elkészített mapping JSON (pl. új nyomtatvány), és a pipeline a
    # `--dynamic-mapping` kapcsolóval fut. Nem küldenek PDF képeket az
    # AI-nak – tisztán szöveges (mezőnév + label + típus) input, így
    # gyorsak és olcsók. 100-200 mező egy batch-ben feldolgozható.

    #: Maximális mezőszám egy DeepSeek API hívásban. A V4 Pro 64K
    #: kontextusablaka és JSON módja miatt ez bőven elegendő, de
    #: token-költség és válasz-stabilitás szempontjából limitáljuk.
    DYNAMIC_BATCH_SIZE: int = 100

    def dynamic_classify_fields(
        self,
        fields: list[RecognizedField],
        canonical_model_fields: list[str],
        salesforce_field_descriptions: dict[str, str],
        pdf_name: str = "",
    ) -> list[RecognizedField]:
        """
        FUTÁSI IDEJŰ mezőklasszifikáció DeepSeek V4 Pro-val.

        Bemenet: ismeretlen PDF mezők (label, típus, oldalszám).
        Kimenet: canonical_field-dal feltöltött RecognizedField-ek.

        NEM küld PDF képeket az AI-nak – tisztán szöveges, gyors, olcsó.
        100-200 mező egy batch-ben feldolgozható; a `DYNAMIC_BATCH_SIZE`
        feletti mezőhalmazt több API hívásra bontjuk.

        Args:
            fields: Még leképezetlen PDF mezők (RecognizedField lista).
                A `canonical_field` itt tipikusan None; a `pdf_field_name`
                és `label` már ki van töltve (a heurisztikus pass által).
            canonical_model_fields: Azon canonical mezők, amelyekhez a
                deal-ben ténylegesen van adat (csak ezekhez érdemes
                mapping-et készíteni – "data-first" elv).
            salesforce_field_descriptions: {canonical_field: leírás} dict
                a kanonikus mezők emberi olvasatú leírásával (pl. a
                CANONICAL_FIELDS katalógusból).
            pdf_name: A PDF neve (csak logoláshoz).

        Returns:
            Ugyanaz a `fields` lista, de a `canonical_field` és
            `confidence` mezőkkel frissítve ott, ahol az AI talált
            egyértelmű leképezést. Ahol nem egyértelmű, ott None marad.
        """
        if not self._client:
            logger.warning(
                "dynamic_classify_fields: DEEPSEEK_API_KEY hiányzik – "
                "AI klasszifikáció kihagyva."
            )
            return fields

        # Csak a még leképezetlen mezőket küldjük az AI-nak.
        unmapped = [f for f in fields if not f.canonical_field]
        if not unmapped:
            logger.info("dynamic_classify_fields: nincs leképezetlen mező")
            return fields

        if not canonical_model_fields:
            logger.warning(
                "dynamic_classify_fields: nincsenek elérhető canonical "
                "mezők (Salesforce adat üres?) – AI klasszifikáció kihagyva."
            )
            return fields

        # Canonical mezők leírásainak összeállítása (csak az elérhetőket).
        canonical_desc_lines = []
        for cf in canonical_model_fields:
            desc = salesforce_field_descriptions.get(cf, "")
            canonical_desc_lines.append(f"  - {cf}: {desc}" if desc else f"  - {cf}")
        canonical_desc = "\n".join(canonical_desc_lines)

        system_prompt = (
            "Te egy banki nyomtatvány mező-klasszifikációs AI vagy.\n"
            "A feladatod: az alábbi PDF mezőkhöz rendeld hozzá a megfelelő "
            "canonical mezőt az alábbi listából.\n\n"
            f"Kanonikus mezők:\n{canonical_desc}\n\n"
            'Válaszul csak egy JSON objektumot adj: {"items": [{"pdf_field_name": "...", "canonical_field": "..."}]}\n'
            "Ahol nem egyértelmű a leképezés, HAGYD KI (ne tippelj "
            "LOW confidence-szel). Csak egyértelmű találatokat adj vissza. "
            'Ha egyetlen mezőt sem tudsz leképezni, üres objektumot adj: {"items": []}'
        )

        # Index aRecognizedField-re, hogy az eredményt vissza tudjuk írni.
        by_name = {f.pdf_field_name: f for f in unmapped}

        total_mapped = 0
        batch_idx = 0
        # Mezők darabolása DYNAMIC_BATCH_SIZE darabos batchekre.
        for start in range(0, len(unmapped), self.DYNAMIC_BATCH_SIZE):
            batch = unmapped[start:start + self.DYNAMIC_BATCH_SIZE]
            batch_idx += 1
            logger.info(
                "  🤖 V4 Pro batch %d/%d: %d mező (összes %d)",
                batch_idx,
                (len(unmapped) + self.DYNAMIC_BATCH_SIZE - 1)
                // self.DYNAMIC_BATCH_SIZE,
                len(batch),
                len(unmapped),
            )

            # User üzenet: felsorolja a mezőket (név, label, típus, oldal).
            field_lines = []
            for f in batch:
                label = (f.label or "").strip()
                ftype = f.field_type.value if hasattr(f.field_type, "value") else str(f.field_type)
                field_lines.append(
                    f'  - "{f.pdf_field_name}" | label="{label}" | '
                    f"típus={ftype} | oldal={f.page_number}"
                )
            user_msg = (
                f"PDF: {pdf_name}\n"
                f"Feldolgozandó mezők ({len(batch)} db):\n"
                + "\n".join(field_lines)
            )

            try:
                response_text = self._deepseek_chat(
                    messages=[{"role": "user", "content": user_msg}],
                    system=system_prompt,
                    max_tokens=8192,
                    json_mode=True,
                )
            except Exception as e:
                logger.error("  V4 Pro API hiba: %s", str(e)[:160])
                continue

            if not response_text:
                logger.warning("  V4 Pro batch %d: üres válasz", batch_idx)
                continue

            # A válasz egy JSON objektum, ami tartalmaz egy tömböt (json_mode
            # kötelező). Több formátumot is támogatunk: {"items": [...]} vagy
            # {"results": [...]} vagy közvetlenül [...].
            try:
                json_str = self._extract_json(response_text)
                parsed = json.loads(json_str)
            except (ValueError, json.JSONDecodeError) as e:
                logger.warning("  V4 Pro JSON hiba: %s", str(e)[:120])
                continue

            if isinstance(parsed, dict):
                # Próbáljuk a gyakori wrapper kulcsokból kivenni a tömböt.
                items = None
                for key in ("items", "results", "fields", "mappings", "data"):
                    if isinstance(parsed.get(key), list):
                        items = parsed[key]
                        break
                if items is None:
                    # Ha a dict maga pdf_field_name → canonical_field térkép,
                    # abból is felépíthetjük a listát.
                    items = [
                        {"pdf_field_name": k, "canonical_field": v}
                        for k, v in parsed.items()
                        if isinstance(v, str)
                    ]
            elif isinstance(parsed, list):
                items = parsed
            else:
                items = []

            batch_mapped = 0
            valid_canonical = set(canonical_model_fields)
            for item in items:
                if not isinstance(item, dict):
                    continue
                pdf_name_item = item.get("pdf_field_name")
                canonical = item.get("canonical_field")
                if not pdf_name_item or not canonical:
                    continue
                # Csak érvényes canonical mezőket fogadunk el (az AI
                # négyzetesen kitalálhat ismeretlen neveket – ezeket
                # eldobjuk, hogy a pipeline ne próbáljon meg nem létező
                # adatot feloldani).
                if canonical not in valid_canonical:
                    continue
                target = by_name.get(pdf_name_item)
                if target is None:
                    continue
                target.canonical_field = canonical
                target.confidence = MappingConfidence.MEDIUM
                batch_mapped += 1

            total_mapped += batch_mapped
            logger.info(
                "    ✅ batch %d: %d/%d mező leképezve",
                batch_idx, batch_mapped, len(batch),
            )

        logger.info(
            "🏁 dynamic_classify_fields: %d/%d mező leképezve V4 Pro-val",
            total_mapped, len(unmapped),
        )
        return fields

    def run_dynamic_mapping(
        self,
        pdf_path: Path,
        fields_to_map: list[dict],
        deal_data: "DealData",
    ) -> dict[str, str]:
        """
        Teljes dinamikus mapping workflow magas szintű burkoló.

        3 fázis:
          1. PASS 1 – exact/heurisztikus match a FieldRecognizer beépített
             OTP/kulcsszó térképével (0 AI cost).
          2. PASS 2 – Salesforce data-first: csak azokat a canonical mezőket
             kínáljuk fel az AI-nak, amelyekhez a deal-ben ténylegesen van
             adat (csökkenti a hallucinációt).
          3. PASS 3 – AI klasszifikáció (DeepSeek V4 Pro) a fennmaradó
             leképezetlen mezőkre, szöveges inputtal (nincs PDF kép).

        Args:
            pdf_path: A feldolgozandó PDF útvonala (csak AcroForm-mezők
                kinyeréséhez és logoláshoz kell).
            fields_to_map: PDF-mező dict lista a scripts/analyze_pdf
                kimenet formátumában ({name, type, page, ...}). Ez az,
                amit a pipeline még nem tudott leképezni.
            deal_data: Normalizált DealData (a canonical mezők
                rendelkezésre állásának ellenőrzéséhez).

        Returns:
            {pdf_field_name: canonical_field} dict – csak az egyértelmű
            leképezések kerülnek bele.
        """
        from src.models.canonical_model import DealData  # típus-tipp

        logger.info("🔄 run_dynamic_mapping: %s (%d mező)", pdf_path.name, len(fields_to_map))

        # --- 1. Heurisztikus alap-mapping (0 AI cost) --------------------
        base_mapping = self._heuristic_map_fields(
            fields_to_map, Path(pdf_path), "acroform"
        )
        fields = base_mapping.fields
        mapped_count = sum(1 for f in fields if f.canonical_field)
        logger.info(
            "  PASS 1 (exact/heurisztika): %d/%d mező leképezve",
            mapped_count, len(fields),
        )

        # --- 2. Salesforce data-first: elérhető canonical mezők -----------
        available = _get_available_deal_fields(deal_data)
        if not available:
            logger.warning(
                "  run_dynamic_mapping: a deal-ben nincs feldolgozható adat "
                "– AI klasszifikáció kihagyva."
            )
            return {f.pdf_field_name: f.canonical_field for f in fields if f.canonical_field}

        # --- 3. AI klasszifikáció (V4 Pro) a fennmaradó mezőkre -----------
        self.dynamic_classify_fields(
            fields=fields,
            canonical_model_fields=sorted(available),
            salesforce_field_descriptions=CANONICAL_FIELDS,
            pdf_name=pdf_path.name,
        )

        result = {f.pdf_field_name: f.canonical_field for f in fields if f.canonical_field}
        logger.info(
            "  run_dynamic_mapping kész: %d/%d mező leképezve összesen",
            len(result), len(fields),
        )
        return result


    def _extract_acroform_fields(self, pdf_path: Path) -> list[dict]:
        """AcroForm mezők kinyerése pikepdf-el."""
        import pikepdf

        fields = []
        try:
            with pikepdf.open(pdf_path) as pdf:
                if "/AcroForm" not in pdf.Root:
                    return fields

                acroform = pdf.Root["/AcroForm"]
                if "/Fields" not in acroform:
                    return fields

                for field_ref in acroform["/Fields"]:
                    try:
                        f = field_ref
                        field_info = {
                            "name": str(f.get("/T", "")),
                            "type": str(f.get("/FT", "")),
                            "value": str(f.get("/V", "")),
                        }
                        # Próbáljuk kideríteni melyik oldalon van
                        if "/P" in f:
                            page_ref = f["/P"]
                            for i, page in enumerate(pdf.pages):
                                if page.objgen == page_ref.objgen:
                                    field_info["page"] = i + 1
                                    break
                        fields.append(field_info)
                    except Exception as e:
                        logger.debug(f"Mező olvasási hiba: {e}")
                        continue
        except Exception as e:
            logger.error(f"PDF olvasási hiba: {e}")

        return fields

    def _ai_map_fields(
        self, pdf_path: Path, pdf_fields: list[dict], form_type: str
    ) -> MappingConfig:
        """AI-alapú mezőleképezés Claude API-val."""
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        system = self.SYSTEM_PROMPT.format(canonical_fields=canonical_desc)

        # PDF oldalak konvertálása képekké az AI számára
        # Maximum 20 oldal képet küldünk (nagy nyomtatványcsomagoknál)
        images = self._pdf_to_images(pdf_path)
        max_images = 20
        if len(images) > max_images:
            logger.info(
                f"  {len(images)} oldal → csak az első {max_images} oldal képét küldjük "
                f"(a mezőneveket mind elküldjük)"
            )
            # Az első 15 + az utolsó 5 oldalt vesszük
            selected_indices = list(range(min(15, len(images)))) + list(range(max(len(images)-5, 15), len(images)))
            selected_indices = sorted(set(selected_indices))[:max_images]
        else:
            selected_indices = list(range(len(images)))

        # Message összeállítása
        content = []

        # PDF képek csatolása (csak a kiválasztottak)
        for i in selected_indices:
            if i < len(images):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": images[i],
                    },
                })
                content.append({
                    "type": "text",
                    "text": f"Ez a(z) {i+1}. oldal (összesen {len(images)} oldalból).",
                })

        # Mezőnevek csatolása (AcroForm esetén – MINDIG az összeset)
        if pdf_fields:
            fields_text = f"Az AcroForm mezők nevei a PDF-ben (összesen {len(pdf_fields)} mező):\n"
            for f in pdf_fields:
                fields_text += f"  - {f['name']} (típus: {f['type']}, oldal: {f.get('page', '?')})\n"
            content.append({"type": "text", "text": fields_text})

        content.append({
            "type": "text",
            "text": "Kérlek elemezd ezt a banki nyomtatványt és készítsd el a mező-leképezést a kanonikus modellre.",
        })

        try:
            response_text = self._deepseek_chat(
                messages=[{"role": "user", "content": content}],
                system=system,
                max_tokens=16384,
                json_mode=True,
            )

            if not response_text:
                logger.error("AI válasz nem tartalmaz szöveget")
                return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

            logger.debug(f"AI válasz (első 500 karakter): {response_text[:500]}")
            json_str = self._extract_json(response_text)
            ai_result = json.loads(json_str)

            return self._parse_ai_response(ai_result, pdf_path, form_type)

        except json.JSONDecodeError as e:
            logger.error(f"AI válasz JSON parse hiba: {e}")
            logger.debug(f"Nyers válasz: {response_text[:1000]}")
            logger.info("Visszaesés heurisztikus leképezésre")
            return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)
        except Exception as e:
            logger.error(f"AI API hiba: {e}")
            logger.info("Visszaesés heurisztikus leképezésre")
            return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

    def _ai_recognize_flat_pdf(self, pdf_path: Path) -> MappingConfig:
        """Lapos PDF AI-alapú felismerése koordinátákkal."""
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        system = self.SYSTEM_PROMPT.format(canonical_fields=canonical_desc)
        system += """

Ez egy lapos (nem kitölthető) PDF. A mezőkhöz add meg a koordinátákat is
(x, y, width, height pixelben), ahol a szöveget el kell helyezni.
A koordináta-rendszer bal felső sarokban indul (0,0)."""

        images = self._pdf_to_images(pdf_path)

        content = []
        for i, img_data in enumerate(images):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_data,
                },
            })
            content.append({
                "type": "text",
                "text": f"Ez a(z) {i+1}. oldal. Azonosítsd a kitöltendő mezőket és add meg a koordinátáikat.",
            })

        try:
            response_text = self._deepseek_chat(
                messages=[{"role": "user", "content": content}],
                system=system,
                max_tokens=16384,
                json_mode=True,
            )

            json_str = self._extract_json(response_text)
            ai_result = json.loads(json_str)

            return self._parse_ai_response(ai_result, pdf_path, "flat")

        except Exception as e:
            logger.error(f"AI API hiba (flat): {e}")
            return self._mock_flat_recognition(pdf_path)

    def _pdf_to_images(self, pdf_path: Path) -> list[str]:
        """PDF oldalak konvertálása base64 PNG képekké."""
        import fitz  # PyMuPDF

        images = []
        try:
            doc = fitz.open(str(pdf_path))
            for page in doc:
                # 150 DPI-s renderelés
                mat = fitz.Matrix(150 / 72, 150 / 72)
                pix = page.get_pixmap(matrix=mat)
                img_data = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                images.append(img_data)
            doc.close()
        except Exception as e:
            logger.error(f"PDF → kép konverziós hiba: {e}")

        return images

    def _extract_json(self, text: str) -> str:
        """JSON blokk kinyerése a szövegből (```json ... ``` vagy sima JSON)."""
        import re

        # 1. Próbáljuk ```json ... ``` code block-ból kinyerni
        json_block = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_block:
            return json_block.group(1).strip()

        # 2. Próbáljuk ``` ... ``` code block-ból
        code_block = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
        if code_block:
            candidate = code_block.group(1).strip()
            if candidate.startswith('{'):
                return candidate

        # 3. Keressük az első { és a hozzá tartozó záró } karaktert
        brace_depth = 0
        start_idx = None
        for i, ch in enumerate(text):
            if ch == '{':
                if brace_depth == 0:
                    start_idx = i
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0 and start_idx is not None:
                    return text[start_idx:i + 1]

        # 4. Utolsó fallback: az első { és utolsó }
        if '{' in text and '}' in text:
            start = text.index('{')
            end = text.rindex('}') + 1
            return text[start:end]

        raise ValueError(f"Nem található JSON a válaszban. Válasz (első 300 karakter): {text[:300]}")

    def _parse_ai_response(
        self, ai_result: dict, pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """AI válasz parse-olása MappingConfig-gá."""
        fields = []
        for f_data in ai_result.get("fields", []):
            fields.append(RecognizedField(
                pdf_field_name=f_data["pdf_field_name"],
                label=f_data.get("label", ""),
                field_type=FieldType(f_data.get("field_type", "text")),
                canonical_field=f_data.get("canonical_field"),
                confidence=MappingConfidence(f_data.get("confidence", "medium")),
                page_number=f_data.get("page_number", 1),
                coordinates=f_data.get("coordinates"),
                notes=f_data.get("notes"),
            ))

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=ai_result.get("form_name", pdf_path.stem),
            form_type=form_type,
            fields=fields,
            page_structure=ai_result.get("page_structure", {}),
        )

    def _heuristic_map_fields(
        self, pdf_fields: list[dict], pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """Heurisztikus mezőleképezés mezőnevek alapján."""
        logger.info("Heurisztikus mezőleképezés (AI nélkül)")

        # Általános kulcsszó → kanonikus leképezés
        KEYWORD_MAP = {
            # Személyi adatok
            "nev": "participant.name",
            "name": "participant.name",
            "szul_nev": "participant.birth_name",
            "szuletesi_nev": "participant.birth_name",
            "birth_name": "participant.birth_name",
            "anyja_neve": "participant.mother_name",
            "mother_name": "participant.mother_name",
            "szul_hely": "participant.birth_place",
            "szuletesi_hely": "participant.birth_place",
            "birth_place": "participant.birth_place",
            "szul_datum": "participant.birth_date",
            "szuletesi_datum": "participant.birth_date",
            "birth_date": "participant.birth_date",
            "szemelyi_szam": "participant.personal_id",
            "szem_ig": "participant.personal_id",
            "personal_id": "participant.personal_id",
            "adoazonosito": "participant.tax_id",
            "adoaz": "participant.tax_id",
            "tax_id": "participant.tax_id",
            # Cím
            "lakcim": "participant.address.full_address",
            "address": "participant.address.full_address",
            "iranyitoszam": "participant.address.zip_code",
            "irsz": "participant.address.zip_code",
            "zip": "participant.address.zip_code",
            "zip_code": "participant.address.zip_code",
            "telepules": "participant.address.city",
            "varos": "participant.address.city",
            "city": "participant.address.city",
            "utca": "participant.address.street",
            "kozterulet": "participant.address.street",
            "street": "participant.address.street",
            "hazszam": "participant.address.house_number",
            "hsz": "participant.address.house_number",
            "house_number": "participant.address.house_number",
            # Kontakt
            "telefon": "participant.phone",
            "telefonszam": "participant.phone",
            "phone": "participant.phone",
            "email": "participant.email",
            "e_mail": "participant.email",
            # Munka
            "munkaltato": "participant.employer",
            "munkahely": "participant.employer",
            "employer": "participant.employer",
            "jovedelem": "participant.monthly_income",
            "havi_netto": "participant.monthly_income",
            "monthly_income": "participant.monthly_income",
            # Hitel
            "hitelosszeg": "loan.loan_amount",
            "kolcson_osszeg": "loan.loan_amount",
            "igenyelt_kolcson": "loan.loan_amount",
            "loan_amount": "loan.loan_amount",
            "futamido": "loan.loan_term_months",
            "loan_term": "loan.loan_term_months",
            "kamatperiodus": "loan.interest_period",
            "hitel_celja": "loan.loan_purpose",
            "loan_purpose": "loan.loan_purpose",
            "onero": "loan.down_payment",
            "torleszto": "loan.monthly_payment",
            # Ingatlan
            "helyrajzi_szam": "property.parcel_number",
            "hrsz": "property.parcel_number",
            "parcel_number": "property.parcel_number",
            "terulet": "property.area_sqm",
            "alapterulet": "property.area_sqm",
            "area": "property.area_sqm",
            "ingatlan_tipus": "property.property_type",
            "becsult_ertek": "property.estimated_value",
            "forgalmi_ertek": "property.estimated_value",
        }

        # OTP-specifikus mező → kanonikus leképezés (valódi mezőnevek a PDF-ből)
        OTP_EXACT_MAP = {
            # === SZA_IG_ – Személyi adatok (Igénylő) ===
            "SZA_IG_név": "participant.name",
            "SZA_IG_szül_név": "participant.birth_name",
            "SZA_IG_anyja_neve": "participant.mother_name",
            "SZA_IG_szül_hely": "participant.birth_place",
            "SZA_IG_szül_év": "participant.birth_date",
            "SZA_IG_szül_hónap": "participant.birth_date",
            "SZA_IG_szül_nap": "participant.birth_date",
            "SZA_IG_személyiszám": "participant.personal_id",
            "SZA_IG_személyazonosíó_szám": "participant.id_card_number",
            "SZA_IG_adóazonosító": "participant.tax_id",
            "SZA_IG_állandó_lakcím_irányító": "participant.address.zip_code",
            "SZA_IG_állandó_lakcim_település": "participant.address.city",
            "SZA_IG_állandó_lakcim_utca": "participant.address.street",
            "SZA_IG_levelezési_cím_irányító": "participant.mailing_address.zip_code",
            "SZA_IG_levelezési_cím_település": "participant.mailing_address.city",
            "SZA_IG_levelezési_lakcim_utca": "participant.mailing_address.street",
            "SZA_IG_mobil": "participant.phone",
            "SZA_IG_email_cím": "participant.email",
            "SZA_IG_eltartottak": "participant.dependents",
            "SZA_IG_kata": "participant.kata_status",
            "SZA_IG_létszám": "participant.employee_count",
            "SZA_IG_mióta_lakik_év": "participant.residence_since",
            "SZA_IG_mióta_lakik_hónap": "participant.residence_since",
            "SZA_IG_Elsődleges_Szerep": "participant.role",
            "SZA_IG_neme": "participant.gender",
            "SZA_IG_állampolgárság": "participant.citizenship",
            "SZA_IG_családiállapot": "participant.marital_status",
            "SZA_IG_dokumentum": "participant.id_document_type",
            "SZA_IG_végzettség": "participant.education",
            "SZA_IG_foglalkoztatás": "participant.employment_type",
            "SZA_IG_lakcim_ország": "participant.address.country",
            "SZA_IG_levelezési_cím": "participant.mailing_address_same",
            "SZA_IG_nav": "participant.nav_declaration",
            # === SZA_IG_ – Társigénylő (-társ suffix) ===
            "SZA_IG_név-társ": "participant.name",
            "SZA_IG_szül_név-társ": "participant.birth_name",
            "SZA_IG_anyja_neve-társ": "participant.mother_name",
            "SZA_IG_szül_hely-társ": "participant.birth_place",
            "SZA_IG_szül_év-társ": "participant.birth_date",
            "SZA_IG_szül_hónap-társ": "participant.birth_date",
            "SZA_IG_szül_nap-társ": "participant.birth_date",
            "SZA_IG_személyiszám-társ": "participant.personal_id",
            "SZA_IG_személyazonosíó_szám-társ": "participant.id_card_number",
            "SZA_IG_adóazonosító-társ": "participant.tax_id",
            "SZA_IG_állandó_lakcím_irányító-társ": "participant.address.zip_code",
            "SZA_IG_állandó_lakcim_település-társ": "participant.address.city",
            "SZA_IG_állandó_lakcim_utca-társ": "participant.address.street",
            "SZA_IG_mobil-társ": "participant.phone",
            "SZA_IG_email_cím-társ": "participant.email",
            "SZA_IG_Elsődleges_Szerep-társ": "participant.role",
            "SZA_IG_neme-társ": "participant.gender",
            "SZA_IG_állampolgárság-társ": "participant.citizenship",
            "SZA_IG_családiállapot-társ": "participant.marital_status",
            "SZA_IG_dokumentum-társ": "participant.id_document_type",
            "SZA_IG_végzettség-társ": "participant.education",
            "SZA_IG_foglalkoztatás-társ": "participant.employment_type",
            "SZA_IG_eltartottak-társ": "participant.dependents",
            # === IA_ – Ingatlan adatok ===
            "IA_ingatlanhasználat": "property.usage_type",
            "IA_Bérletidíj": "property.rental_fee",
            "IA_Bérletidíj_EUR": "property.rental_fee_eur",
            "IA_Kapcsolattartó neve": "property.contact_name",
            "IA_Telefonszám": "property.contact_phone",
            # === LAHI/KTKA – Lakáshitel/Költségkalkuláció ===
            "LAHI_KTKA_Igényelt_kölcsön": "loan.loan_amount",
            "LAHI_KTKA_Vásárolt_ingatlan_ár": "loan.purchase_price",
            "LAHI_KTKA_Önerő_kp": "loan.down_payment",
            "LAHI_KTKA_CSOK": "loan.csok_amount",
            "LAHI_KTKA_Afa": "loan.afa_support",
            "LAHI_KTKA_Lakástakarék": "loan.housing_savings",
            "LAHI_KTKA_Kiváltandó_kölcsön_szlaszám": "loan.refinance_account",
            "LAHI_KTKA_Kölcsön_tipusa_OTP": "loan.product_type",
            # === MA_ – Munkáltatói/meghatalmazás ===
            "MA_IG_adós_neve": "participant.name",
            "MA_IG_munáltató_neve": "participant.employer",
            "MA_IG_munkavállaló_neve": "participant.name",
            "MA_IG_vállalkozás_neve": "participant.business_name",
            "MA_IG_munáltató_adósz": "participant.employer_tax_id",
            "MA_IG_vállalkozás_adószám": "participant.business_tax_id",
        }

        fields = []
        # OTP exact map előzetesen normalizálva (2b): az ékezetes/regiszter-
        # érzékeny kulcsokat (`név`, `állandó_lakcím`) az `_normalize_key`
        # helper-rel ékezet- és kisbetű-mentes formára hozzuk, így a PDF-ből
        # érkező tetszőleges kódolású/kisbetűs mezőnév is megtalálja a map-ot.
        otp_exact_map_normalized = {
            _normalize_key(k): v for k, v in OTP_EXACT_MAP.items()
        }
        for pdf_field in pdf_fields:
            field_name = pdf_field["name"]
            canonical = None
            confidence = MappingConfidence.LOW

            # 1. Próbáljuk az OTP exact map-ből – normalizált kulccsal.
            normalized = _normalize_key(field_name)
            if normalized in otp_exact_map_normalized:
                canonical = otp_exact_map_normalized[normalized]
                confidence = MappingConfidence.HIGH
            else:
                # 2. Próbáljuk a kulcsszó alapú map-ből
                clean_name = field_name.lower()
                # Prefixek eltávolítása
                for prefix in [
                    "borrower_", "co_borrower_", "coborrower_", "guarantor_",
                    "property_", "loan_", "sza_ig_", "sza_", "ia_", "ta_",
                    "lahi_", "ktka_", "lahi_ktka_",
                ]:
                    clean_name = clean_name.replace(prefix, "")
                # Társigénylő suffix eltávolítása
                for suffix in ["-társ", "_társ", "-tars", "_tars"]:
                    clean_name = clean_name.replace(suffix, "")

                canonical = KEYWORD_MAP.get(clean_name)
                if canonical:
                    confidence = MappingConfidence.HIGH

            # 3. Típus detektálás
            fl = field_name.lower()
            if any(kw in fl for kw in ["datum", "date", "szul_", "év", "hó", "nap"]):
                field_type = FieldType.DATE
            elif any(kw in fl for kw in ["osszeg", "ertek", "összeg", "érték", "ár", "díj"]):
                field_type = FieldType.NUMBER
            elif pdf_field.get("type") == "/Btn":
                field_type = FieldType.CHECKBOX
            else:
                field_type = FieldType.TEXT

            fields.append(RecognizedField(
                pdf_field_name=field_name,
                label=field_name.replace("_", " ").title(),
                field_type=field_type,
                canonical_field=canonical,
                confidence=confidence,
                page_number=pdf_field.get("page", 1),
            ))

        mapped = sum(1 for f in fields if f.canonical_field)
        logger.info(f"  Heurisztikus: {mapped}/{len(fields)} mező leképezve")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type=form_type,
            fields=fields,
            notes="Heurisztikus leképezés (AI API nem elérhető). Felülvizsgálat szükséges.",
        )

    def _mock_flat_recognition(self, pdf_path: Path) -> MappingConfig:
        """Mock lapos PDF felismerés (tesztelési célra, AI nélkül)."""
        logger.info("Mock lapos PDF felismerés (AI nélkül)")

        import fitz
        doc = fitz.open(str(pdf_path))
        num_pages = len(doc)

        # Alapvető oldal-struktúra feltételezés
        page_structure = {}
        if num_pages >= 1:
            page_structure["1"] = "global"
        if num_pages >= 2:
            page_structure["2"] = "per_participant"
        if num_pages >= 3:
            page_structure["3"] = "per_property"

        # Szöveg kinyerése az oldalakról – mezők beazonosítása
        fields = []
        field_labels_hu = [
            ("Név", "participant.name", FieldType.TEXT),
            ("Születési név", "participant.birth_name", FieldType.TEXT),
            ("Anyja neve", "participant.mother_name", FieldType.TEXT),
            ("Születési hely", "participant.birth_place", FieldType.TEXT),
            ("Születési dátum", "participant.birth_date", FieldType.DATE),
            ("Személyi szám", "participant.personal_id", FieldType.TEXT),
            ("Adóazonosító", "participant.tax_id", FieldType.TEXT),
            ("Lakcím", "participant.address.full_address", FieldType.TEXT),
            ("Irányítószám", "participant.address.zip_code", FieldType.TEXT),
            ("Település", "participant.address.city", FieldType.TEXT),
            ("Telefonszám", "participant.phone", FieldType.TEXT),
            ("E-mail", "participant.email", FieldType.TEXT),
            ("Hitelösszeg", "loan.loan_amount", FieldType.NUMBER),
            ("Futamidő", "loan.loan_term_months", FieldType.NUMBER),
            ("Helyrajzi szám", "property.parcel_number", FieldType.TEXT),
            ("Terület", "property.area_sqm", FieldType.NUMBER),
        ]

        for page_num in range(num_pages):
            page = doc[page_num]
            page_text = page.get_text()

            for label, canonical, ftype in field_labels_hu:
                if label.lower() in page_text.lower():
                    # Megpróbáljuk megtalálni a label pozícióját
                    text_instances = page.search_for(label)
                    if text_instances:
                        rect = text_instances[0]
                        # A kitöltendő terület a labeltől jobbra/alatta
                        fields.append(RecognizedField(
                            pdf_field_name=f"flat_{canonical.replace('.', '_')}_{page_num+1}",
                            label=label,
                            field_type=ftype,
                            canonical_field=canonical,
                            confidence=MappingConfidence.MEDIUM,
                            page_number=page_num + 1,
                            coordinates={
                                "x": float(rect.x1 + 5),
                                "y": float(rect.y0),
                                "width": 200.0,
                                "height": float(rect.height),
                            },
                        ))

        doc.close()

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="flat",
            fields=fields,
            page_structure=page_structure,
            notes="Mock felismerés szövegkeresés alapján. Felülvizsgálat szükséges.",
        )

    def _empty_mapping(self, pdf_path: Path, form_type: str) -> MappingConfig:
        """Üres mapping konfiguráció (mezők nem találhatók)."""
        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type=form_type,
            notes="Nem találtam kitöltendő mezőket. Manuális konfiguráció szükséges.",
        )


def print_mapping_summary(mapping: MappingConfig):
    """Mapping konfiguráció összefoglaló kiírása."""
    print(f"\n{'='*60}")
    print(f"📋 Mapping konfiguráció: {mapping.form_name}")
    print(f"   Bank: {mapping.bank_name}")
    print(f"   Típus: {mapping.form_type}")
    print(f"   Jóváhagyva: {'✓' if mapping.approved else '✗ – felülvizsgálat szükséges'}")
    print(f"{'='*60}")

    if mapping.page_structure:
        print(f"\n📄 Oldalstruktúra:")
        for page, ptype in mapping.page_structure.items():
            print(f"   {page}. oldal: {ptype}")

    print(f"\n🔍 Felismert mezők ({len(mapping.fields)} db):")
    for f in mapping.fields:
        icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}[f.confidence.value]
        canonical = f.canonical_field or "❓ nem leképezett"
        print(f"   {icon} [{f.page_number}. oldal] {f.label}")
        print(f"      PDF: {f.pdf_field_name} → {canonical}")
        if f.coordinates:
            print(f"      Koordináták: x={f.coordinates['x']:.0f}, y={f.coordinates['y']:.0f}")
        if f.notes:
            print(f"      📝 {f.notes}")

    needs_review = mapping.needs_review_fields
    if needs_review:
        print(f"\n⚠️  {len(needs_review)} mező igényel felülvizsgálatot")

    print()


# --- CLI ---

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="AI mezőfelismerő – banki nyomtatvány → mapping konfiguráció"
    )
    parser.add_argument("pdf_path", type=Path, help="Üres PDF nyomtatvány elérési útja")
    parser.add_argument(
        "--mode", "-m",
        choices=["auto", "acroform", "overlay"],
        default="auto",
        help="Felismerési mód (default: auto)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output mapping JSON fájl (default: src/mapping/<pdf_name>_mapping.json)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="DeepSeek API kulcs (default: DEEPSEEK_API_KEY env)",
    )

    args = parser.parse_args()

    # Mezőfelismerés
    recognizer = FieldRecognizer(api_key=args.api_key)
    mapping = recognizer.recognize(args.pdf_path, mode=args.mode)

    # Összefoglaló
    print_mapping_summary(mapping)

    # Mentés
    output_path = args.output
    if output_path is None:
        project_root = Path(__file__).parent.parent.parent
        output_path = project_root / "src" / "mapping" / f"{args.pdf_path.stem}_mapping.json"

    mapping.save(output_path)
    print(f"💾 Mapping mentve: {output_path}")

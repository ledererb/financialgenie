"""
FinancialGenie – AI mezőfelismerő réteg

Claude Sonnet 4 API-val felismeri egy üres banki nyomtatvány kitöltendő mezőit,
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
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


#: Az Anthropic API-hoz használt Claude modell azonosító (2a).
#: Korábban a "claude-sonnet-4-6" string volt 4 helyen hardcode-olva;
#: most egyetlen helyen, modul-szinten van deklarálva, így egyszerűen
#: frissíthető, ha új modell-verzió érkezik.
AI_MODEL: str = "claude-sonnet-4-6"


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
# Cutover Task 4: a korábbi, Salesforce API-neveket tartalmazó statikus
# ``CANONICAL_FIELDS`` / ``SF_FIELD_TYPES`` katalógus törölve — a felismerő
# (promptok, heurisztikák, autocomplete) kizárólag a megosztott Genie
# canonical registry (``src/canonical/fields.json``) szókincsét használja.
# A katalógus a registry SABLONJAIBOL épül szerepkörönként kifejtve:
#   - repeat="none"          → path változatlan (deal.*, account.*)
#   - repeat="participant"   → participant.<role>.<mező> mind az 5 szerepre
#   - repeat="property"      → property.<mező> (index nélküli alapalak)
#   - repeat="income"/"existing_loan" → participant.<role>.<collection>[1].<mező>
# Így minden kulcs registry-approved canonical path — a felismert mapping
# az aktivált (Genie-only) pipeline-ban azonnal használható.

from src.canonical.registry import canonical_registry  # noqa: E402

#: Review fix 3: a szereplő-lista a registry-ből érkezik (egyetlen forrás:
#: ``src.canonical.paths.PARTICIPANT_ROLES``), a recognizer nem másolja.
_PARTICIPANT_ROLES = canonical_registry.roles()


def _build_canonical_catalog() -> dict[str, str]:
    """Registry-sablonok → szerepkör-kifejtett canonical path → magyar label."""

    catalog: dict[str, str] = {}
    for definition in canonical_registry.definitions():
        template = definition["path"]
        repeat = definition["repeat"]
        label = definition["label"]
        if repeat == "none":
            catalog[template] = label
        elif repeat == "participant":
            for role in _PARTICIPANT_ROLES:
                catalog[template.replace("{role}", role)] = label
        elif repeat == "property":
            catalog[template] = label
        else:  # income | existing_loan — 1-es (reprezentatív) indexű sor
            field = template.rsplit(".", 1)[1]
            for role in _PARTICIPANT_ROLES:
                catalog[f"participant.{role}.{repeat}[1].{field}"] = label
    return catalog


#: A felismerő AI és a heurisztikák kizárólagos canonical szókincse: minden
#: kulcs registry-approved Genie path (ellenőrzés:
#: tests/test_recognizer_genie_registry.py).
CANONICAL_FIELDS: dict[str, str] = _build_canonical_catalog()


def _canonical_catalog_description() -> str:
    """A canonical szókincs prompt-barát, determinisztikus leírása."""

    return "\n".join(
        f"  - {key}: {desc}" for key, desc in sorted(CANONICAL_FIELDS.items())
    )


def _sanitize_canonical(canonical) -> Optional[str]:
    """Csak registry-approved canonical path enged át; minden más None.

    A felismerő kimenetében (AI válasz, fuzzy match, posztprocessz) sosem
    szerepelhet kitalált névtér vagy legacy Salesforce-név — az aktivált
    pipeline ezeket elutasítaná (Cutover Task 3).
    """
    if not isinstance(canonical, str) or not canonical:
        return None
    return canonical if canonical_registry.validate(canonical) else None


def _sanitize_fill_rule(fill_rule):
    """A fill_rule-ben lévő canonical hivatkozás (sf_field) is registry-only.

    None / nem-dict szabályváltozat érintetlenül visszajön; egy érvénytelen
    ``sf_field`` None-ra vált (a szabály típusa/más mezői megmaradnak), hogy
    az auto-létrehozott mapping mentése (mapping_service.save registry-
    validációja) sosem bukjon el a felismerő saját kimenetén.
    """
    if not isinstance(fill_rule, dict):
        return fill_rule
    if "sf_field" not in fill_rule:
        return fill_rule
    sanitized = dict(fill_rule)
    sanitized["sf_field"] = _sanitize_canonical(fill_rule.get("sf_field"))
    return sanitized


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
    MANUAL = "manual"   # Felhasználó által kézzel beállított


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
    checkbox_group: Optional[dict] = None   # {"group_id": str, "group_label": str|None,
                                             #  "option_value": str|None, "option_label": str|None,
                                             #  "match_value": str|None (deprecated alias → option_value)}
    fill_rule: Optional[dict] = None         # {"type": "static"|"per_participant"|"conditional"|"role_based", "value": str, ...}
    # C8 (birth-date / date fragmentation ground-work): when a single logical
    # date (e.g. birth date) is split across several PDF fields (year / month /
    # day, or Nth digit), `fragment` records which piece this field carries so
    # the fill engine can reassemble it. Values: "year", "month", "day", or
    # "digit:N". Survives JSON round-trip via MappingConfig.to_dict/from_dict.
    fragment: Optional[str] = None
    # Cutover Task 2 review F4 — product section gate ("piaci" | "tamogatott").
    # Migrated from the legacy `#piaci` / `#tamogatott` canonical suffixes by
    # scripts.migrate_mappings_to_genie; the canonical fill pipeline skips the
    # entry (or its whole checkbox group) when the deal's products do not
    # activate the gate. Survives JSON round-trip via MappingConfig.
    product_gate: Optional[str] = None


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
    # FIX M5 — character-split groups (spec §6, §9.3): a canonical value
    # (e.g. postal code) split character-by-character across several digit-box
    # PDF fields. The pipeline reads this to expand each group before filling.
    # Each entry: {"group_id", "group_name", "field_type": "character_split",
    # "canonical_field", "member_fields": [...], "direction", "separator"}.
    # Stored as plain dicts so the existing JSON mapping files (edited via the
    # mapping editor backend) round-trip without bespoke dataclasses.
    character_groups: list[dict] = field(default_factory=list)
    # Round-2 fill engine (spec: hiteligénylési kitöltési szabályok): logical
    # form "points" (numbered questions spanning checkbox blocks) and the
    # reusable "rule_sets". Each point selects one of 7 rule types in
    # src/engine/fill_rules. Stored as plain dicts so the mapping JSON files
    # and the editor backend round-trip without bespoke dataclasses. Default
    # empty → old mappings keep loading unchanged (purely additive).
    points: list[dict] = field(default_factory=list)
    rule_sets: list[dict] = field(default_factory=list)

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
                    "checkbox_group": f.checkbox_group,
                    "fill_rule": f.fill_rule,
                    "fragment": f.fragment,
                    "product_gate": f.product_gate,
                }
                for f in self.fields
            ],
            # M5: persist character groups so AI-generated/heuristic mappings
            # and user edits survive a save→load cycle.
            "character_groups": self.character_groups,
            # Round-2: persist points / rule_sets so the 7-rule fill engine
            # config survives a save→load cycle.
            "points": self.points,
            "rule_sets": self.rule_sets,
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
                checkbox_group=f.get("checkbox_group"),
                fill_rule=f.get("fill_rule"),
                fragment=f.get("fragment"),
                product_gate=f.get("product_gate"),
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
            # M5: load character groups (default [] for older mapping files).
            character_groups=list(data.get("character_groups", [])),
            # Round-2: load points / rule_sets (default [] for older mappings).
            points=list(data.get("points", [])),
            rule_sets=list(data.get("rule_sets", [])),
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


def _group_meta(f: RecognizedField) -> Optional[dict]:
    """Visszaadja a mező checkbox_group dict-jét függetlenül attól, hogy a
    mező RecognizedField dataclass vagy raw dict (mapping JSON-ból)."""
    if isinstance(f, dict):
        return f.get("checkbox_group")
    return getattr(f, "checkbox_group", None)


def _char_group_stem(label: str) -> str | None:
    """Comb-doboz label-törzs kinyerése: a '(N. jegy)' / '(N. karakter)' jellegű
    sorszámlevágás utáni közös törzs. None, ha a label generikus fallback
    (pl. 'Text Field 316') vagy nem jegy-sorozat."""
    import re as _re
    if not label:
        return None
    stem = _re.sub(r"\s*\(\s*\d+\.\s*(?:jegy|karakter|számjegy)\s*\)\s*$", "", label, flags=_re.IGNORECASE).strip()
    if stem == label.strip():
        return None  # nem volt jegy-suffix
    return stem or None


#: Label-törzs kulcsszó → canonical mező (comb dobozokhoz, ha az AI nem adott
#: canonicalt az egyes jegyeknek). Cutover Task 4: minden érték registry-approved
#: Genie path (a régi Contact.*/Lead.* SF-nevek helyett).
_CHAR_STEM_CANONICAL_RULES = [
    # "nap"-ot is tartalmazó szöveg előbb jöjjön, mint a hónap!
    ("hónap", "participant.borrower.birth_date"),
    ("adóazonosító", "participant.borrower.tax_id"),
    ("személyazonosító", "participant.borrower.personal_id"),
    ("okmány száma", "participant.borrower.personal_id"),
    ("igazolvány száma", "participant.borrower.personal_id"),
    ("dokumentum száma", "participant.borrower.personal_id"),
    ("ingatlan irányítószám", "property.address.zip_code"),
    ("irányítószám", "participant.borrower.address.zip_code"),
    ("születési idő - év", "participant.borrower.birth_date"),
    ("születési idő - nap", "participant.borrower.birth_date"),
    ("születési dátum - év", "participant.borrower.birth_date"),
    ("születési dátum - nap", "participant.borrower.birth_date"),
    (" - év", "participant.borrower.birth_date"),
    (" - nap", "participant.borrower.birth_date"),
]


def _stem_to_canonical(stem: str) -> str | None:
    low = stem.lower()
    for keyword, canonical in _CHAR_STEM_CANONICAL_RULES:
        if keyword in low and canonical in CANONICAL_FIELDS:
            return canonical
    return None


def _flush_char_run(run, page, canonical_by_name, label_by_name, groups, members_all):
    """Egy karakterdoboz-sor lezárása és character_grouppá alakítása.

    Két út:
      1. A sor label-törzs ('(N. jegy)' nélküli közös rész) alapján szétválik
         azonos törzsű részsorokra; ≥2 tag + feloldható canonical → csoport.
      2. Törzs nélküli (generikus label) sor: ≥4 tag + mind azonos canonical → csoport.
    """
    import re as _re
    if len(run) < 2:
        return

    # Részsorokra bontás label-törzs szerint (x-sorrend megmarad)
    segments: list[tuple[str | None, list[str]]] = []
    for name in run:
        stem = _char_group_stem(label_by_name.get(name, ""))
        if segments and segments[-1][0] == stem:
            segments[-1][1].append(name)
        else:
            segments.append((stem, [name]))

    for stem, members in segments:
        canon_set = {canonical_by_name.get(n) for n in members}
        canon_set.discard(None)
        canonical = next(iter(canon_set)) if len(canon_set) == 1 else None
        if canonical is None and stem:
            canonical = _stem_to_canonical(stem)
        if not canonical:
            continue
        # Guard: törzs nélküli (generikus label) szegmensben több KÜLÖNBÖZŐ
        # valódi label → nem comb sor, hanem pl. táblázat-oszlopok, amiket az
        # AI véletlenül ugyanarra a canonicalra mappelt (hamis pozitív).
        if stem is None:
            real_labels = {
                label_by_name.get(n, "").strip()
                for n in members
                if label_by_name.get(n, "").strip()
                and label_by_name.get(n, "").strip() != n.replace("_", " ").title()
            }
            if len(real_labels) > 1:
                continue
        # Küszöb: törzzsel ≥2 tag, törzs nélkül ≥4 tag
        if len(members) < (2 if stem else 4):
            continue
        slug = _re.sub(r"[^a-z0-9]+", "_", canonical.lower()).strip("_")
        c_low = canonical.lower()
        # A jobb-balra sorrendű (számérték) csoportok felismerése Genie
        # canonical nevekből (Cutover Task 4: a régi SF-név kulcsszavak —
        # pl. "hitel_sszeg", "jovedelem" — már sosem fordulnak elő).
        is_rtl = any(
            kw in c_low
            for kw in (
                "loan_amount",
                "monthly_income",
                "other_monthly_income",
                "monthly_payment",
                "affordable_monthly_installments",
                "down_payment",
                "purchase_price",
                "estimated_value",
                "csok_amount",
                "afa_support",
                "housing_savings",
                "otthon_start_amount",
                "children_count",
                "dependents_count",
                "household_size",
                "loan_term",
                "area_sqm",
            )
        )
        groups.append({
            "group_id": f"chargroup_{slug}_p{page}_{len(groups)}",
            "canonical_field": canonical,
            "member_fields": list(members),
            "direction": "right_to_left" if is_rtl else "left_to_right",
            "separator": "",
        })
        members_all.update(members)


def _detect_character_groups(pdf_fields, recognized_fields):
    """Egy sorban álló, egységes méretű karakterdobozok (comb mezők) felismerése.

    A banki nyomtatványokon az azonosítók, irányítószámok, dátumok gyakran
    külön egykarakteres dobozokból állnak. Ha ilyen dobozok sorát nem
    csoportosítjuk, a teljes érték minden dobozba bekerülne — ehelyett
    character_groupot generálunk, így a filler karakterenként osztja szét
    (expand_character_groups, pdf_filler.py). A felismerés geometriai
    (egy sor, egységes dobozméret, kis hézag), a canonical a tagmezők
    canonicaljából vagy a label-törzs kulcsszavaiból származik.

    Visszaad: (character_groups lista, tagmező-nevek halmaza).
    """
    rect_by_name = {}
    for f in pdf_fields:
        name = f.get("name")
        rect = f.get("rect")
        if name and rect and f.get("type") == "text":
            rect_by_name[name] = (f.get("page", 1), rect)

    canonical_by_name = {}
    label_by_name = {}
    for f in recognized_fields:
        label_by_name[f.pdf_field_name] = getattr(f, "label", "") or ""
        if f.canonical_field:
            canonical_by_name[f.pdf_field_name] = f.canonical_field

    by_page = {}
    for name, (page, rect) in rect_by_name.items():
        by_page.setdefault(page, []).append((name, rect))

    groups = []
    members_all = set()

    for page, items in by_page.items():
        # Sorokra bontás y-közép szerint (10 px tűrés)
        rows = []
        for name, rect in sorted(items, key=lambda it: (it[1][1], it[1][0])):
            cy = rect[1] + rect[3] / 2
            for row in rows:
                ry = row[0][1][1] + row[0][1][3] / 2
                if abs(cy - ry) <= 10:
                    row.append((name, rect))
                    break
            else:
                rows.append([(name, rect)])

        for row in rows:
            row.sort(key=lambda it: it[1][0])
            run = []
            prev_rect = None
            for name, rect in row:
                w, h = rect[2], rect[3]
                is_box = 12 <= w <= 70 and 12 <= h <= 80
                if is_box:
                    if run and prev_rect is not None:
                        pw, ph = prev_rect[2], prev_rect[3]
                        gap = rect[0] - (prev_rect[0] + pw)
                        same_size = (abs(w - pw) <= max(6.0, 0.35 * pw)
                                     and abs(h - ph) <= max(6.0, 0.35 * ph))
                        adjacent = -2.0 <= gap <= max(w, pw)
                        if not (same_size and adjacent):
                            _flush_char_run(run, page, canonical_by_name, label_by_name, groups, members_all)
                            run = []
                    run.append(name)
                    prev_rect = rect
                else:
                    _flush_char_run(run, page, canonical_by_name, label_by_name, groups, members_all)
                    run = []
                    prev_rect = None
            _flush_char_run(run, page, canonical_by_name, label_by_name, groups, members_all)

    return groups, members_all


def _postprocess_recognized_fields(
    fields: list[RecognizedField],
    char_groups: list[dict] | None = None,
) -> None:
    """Felismert mezők automatikus utófeldolgozása és javítása:

    1. Karakterdoboz-csoportok (character_groups) tagmezőin megtartjuk /
       visszatöltjük a csoport canonical_field-jét (hogy a UI-ban is látszódjon,
       és futásidőben a _infer_runtime_character_groups is felismerje).
    2. Helyrajzi szám (hrsz) szövegmezőket automatikusan a
       property.parcel_number mezőre irányítja.
    3. Szétbontott dátummezőkhöz (év / hónap / nap) automatikusan beállítja a
       megfelelő string fragment-et.
    4. Fejléc / navigációs / belső technikai checkboxokról (low confidence,
       checkbox_group nélkül) leveszi a téves canonical_field leképezést.

    Cutover Task 4 (requirement 1): minden itt beírt canonical érték
    registry-approved Genie path — legacy SF-név vagy kitalált névtér
    sosem kerül a kimenetbe.
    """
    import re as _re

    member_to_canon: dict[str, str] = {}
    for grp in (char_groups or []):
        c_field = grp.get("canonical_field")
        if c_field:
            for m in (grp.get("member_fields") or []):
                member_to_canon[m] = c_field

    for f in fields:
        # 1. Karakterdoboz tagmező canonical visszatöltése
        if f.pdf_field_name in member_to_canon and not f.canonical_field:
            f.canonical_field = member_to_canon[f.pdf_field_name]

        norm_name = _normalize_key(f.pdf_field_name or "")
        norm_lbl = _normalize_key(f.label or "")
        norm_notes = _normalize_key(f.notes or "")
        combined = f"{norm_name} {norm_lbl}"

        # 2. Helyrajzi szám automatikus leképezése (audited fordítás:
        #    Lead.Ingatlan_megjegyzes__c → property.parcel_number)
        if f.field_type == FieldType.TEXT and (
            "helyrajzi" in combined or _re.search(r"\bhrsz\b", combined)
        ):
            f.canonical_field = "property.parcel_number"
            f.confidence = MappingConfidence.HIGH

        # 3. Dátum-komponens (év/hónap/nap) automatikus fragment beállítása
        #    (Cutover Task 4: a régi Contact.Birthdate_year/_month/_day
        #    pszeudo-canonicalok megszűntek — a fragment-kizárásra nincs
        #    szükség, a részdobozok ugyanazt a full-date canonicalt viszik
        #    fragment metadata-val.)
        if (
            f.canonical_field
            and not f.fragment
            and any(
                kw in f.canonical_field.lower()
                for kw in ("date", "started", "birth")
            )
        ):
            for text_cand in (norm_name, norm_lbl):
                tokens = [t for t in _re.split(r"[^a-z0-9]+", text_cand) if t]
                if not tokens:
                    continue
                last = tokens[-1]
                if last in ("ev", "eve", "year", "yyyy") or ("ev" in tokens and "ho" not in tokens and "honap" not in tokens and "nap" not in tokens):
                    f.fragment = "year"
                    break
                if last in ("ho", "honap", "month", "mm") or ("honap" in tokens and "ev" not in tokens and "nap" not in tokens):
                    f.fragment = "month"
                    break
                if last in ("nap", "napja", "day", "dd") and "honap" not in tokens:
                    f.fragment = "day"
                    break

        # 4. Fejléc / navigációs / technikai checkboxok tisztítása
        if (
            f.field_type == FieldType.CHECKBOX
            and f.confidence == MappingConfidence.LOW
            and not f.checkbox_group
            and not f.fill_rule
        ):
            if any(
                kw in f"{norm_lbl} {norm_notes}"
                for kw in ("fejlec", "navigacio", "oldalszam", "oldal jelolo", "belso elem")
            ):
                f.canonical_field = None


def _normalize_checkbox_group(cg_raw) -> Optional[dict]:
    """PLAN §3.3 — AI válaszból érkező checkbox_group normalizálása.

    Kezeli a teljes kulcsokat (group_id/group_label/option_value/option_label,
    amit a SYSTEM_PROMPT kér), a rövidített kulcsokat (g/gl/ov/ol, amit a
    _ai_batch_recognize prompt használ), és a deprecated alias-t (match_value/mv).
    Visszatér a kanonikus 4-kulcsos alakkal, vagy None ha nincs group_id.
    """
    if not cg_raw or not isinstance(cg_raw, dict):
        return None
    gid = cg_raw.get("group_id") or cg_raw.get("g")
    if not gid:
        return None
    ov = cg_raw.get("option_value")
    if ov is None:
        ov = cg_raw.get("ov")
    if ov is None:
        ov = cg_raw.get("match_value")
    if ov is None:
        ov = cg_raw.get("mv")
    gl = cg_raw.get("group_label")
    if gl is None:
        gl = cg_raw.get("gl")
    ol = cg_raw.get("option_label")
    if ol is None:
        ol = cg_raw.get("ol")
    return {
        "group_id": gid,
        "group_label": gl,
        "option_value": ov,
        "option_label": ol,
    }



def _field_canonical(f) -> Optional[str]:
    if isinstance(f, dict):
        return f.get("canonical_field")
    return getattr(f, "canonical_field", None)


def _field_page(f) -> int:
    if isinstance(f, dict):
        return f.get("page_number", 1)
    return getattr(f, "page_number", 1)


def _validate_checkbox_groups(fields) -> list[str]:
    """Visszatér a checkbox-csoport konzisztencia-hibák listájával (üres = OK).

    Ellenőrzések (PLAN_CHECKBOX_GROUPS.md §3.5):
    - Egy group_id-n belül minden tagnak azonos canonical_fieldje van-e.
    - Egy group_id-n belül minden option_value egyedi-e (nincs duplikáció).
    - group_label konzisztens (ugyanaz minden tagon) — ha meg van adva.

    Nem fail-hard: a hívó (logger.warning) jelzi a találatot, hogy a review
    során látszódjon.
    """
    errors: list[str] = []
    # group_id → list[fields]
    by_group: dict[str, list] = {}
    for f in fields:
        g = _group_meta(f)
        if not g or not g.get("group_id"):
            continue
        by_group.setdefault(g["group_id"], []).append(f)

    for gid, members in by_group.items():
        if len(members) < 1:
            continue
        # canonical_field konzisztencia
        canonicals = {_field_canonical(m) for m in members}
        if len(canonicals) > 1:
            errors.append(
                f"checkbox_group '{gid}': a tagok különböző canonical_field-re "
                f"mutatnak: {sorted(c for c in canonicals if c)}"
            )
        # option_value egyediség
        seen_ov: dict[str, str] = {}  # option_value → first pdf_field_name
        for m in members:
            g = _group_meta(m)
            ov = g.get("option_value") if g else None
            if not ov:
                continue
            if ov in seen_ov:
                first = seen_ov[ov]
                pdf = m.get("pdf_field_name") if isinstance(m, dict) else m.pdf_field_name
                errors.append(
                    f"checkbox_group '{gid}': duplikált option_value '{ov}' "
                    f"({first} és {pdf})"
                )
            else:
                seen_ov[ov] = (
                    m.get("pdf_field_name") if isinstance(m, dict) else m.pdf_field_name
                )
        # group_label konzisztencia (ha meg van adva)
        labels = {
            (_group_meta(m).get("group_label"))
            for m in members
            if _group_meta(m) and _group_meta(m).get("group_label")
        }
        if len(labels) > 1:
            errors.append(
                f"checkbox_group '{gid}': inkonzisztens group_label: {sorted(labels)}"
            )
    return errors


class FieldRecognizer:
    """
    AI-alapú mezőfelismerő – Claude Sonnet 4 API-val.

    Egy üres PDF nyomtatványt elemez, felismeri a kitöltendő mezőket,
    és leképezi azokat a kanonikus adatmodellre.
    """

    SYSTEM_PROMPT = """Te egy banki nyomtatvány-elemző AI vagy. A feladatod:

1. Elemezd az üres banki nyomtatvány képét/tartalmát
2. Azonosítsd az összes kitöltendő mezőt (név, cím, dátum, stb.)
3. Mindegyik mezőt képezd le a Genie canonical mezőkre (namespace.mező formátum)

A Genie canonical mezők (CSAK ezek használhatók):
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
      "canonical_field": "Genie canonical útvonal (pl. participant.borrower.name, deal.loan_amount)",
      "confidence": "high|medium|low",
      "page_number": 1,
      "notes": "opcionális megjegyzés",
      "checkbox_group": {{
        "group_id": "stabil snake_case azonosító, közös a csoport minden tagján (pl. 'highest_education')",
        "group_label": "a KÉRDÉS felirata (pl. 'Legmagasabb iskolai végzettség')",
        "option_value": "az option érték, ami ezt az opciót kiválasztja (pl. 'Felsőfokú')",
        "option_label": "az opció felirata a PDF-en (általában = option_value)"
      }},
      "fill_rule": {{"match_value": "Az a mezőérték, ami kiválasztja ezt a konkrét opciót (pl. 'Férfi' vagy 'Házas')"}}
    }}
  ]
}}

Fontos szabályok:
- Használd a Genie canonical útvonalakat PONTOSAN a fenti listából (pl. participant.borrower.name, property.address.zip_code)
- A szereplőket a participant névtér szerepe különbözteti meg: adós = participant.borrower.*, adóstárs/társigénylő = participant.co_borrower.*, kezes = participant.guarantor.*, zálogkötelezett = participant.mortgagor.*, haszonélvező = participant.usufructuary.*
- Ingatlan-specifikus mezők a property.* névtérben vannak (pl. property.address.city, property.parcel_number)
- Ügylet-szintű mezők a deal.* névtérben (pl. deal.loan_amount, deal.loan_purpose, deal.loan_term_years)
- Ha nem vagy biztos a leképezésben, jelöld "low" confidence-szel vagy canonical_field: null
- HA EGY MEZŐ NINCS a canonical listában, KÖTELEZŐ null-t adni — sosem találd ki új útvonalat!
- **Rádiógombok / Kölcsönösen kizáró Checkbox-csoportok**: Ha egy kérdés alatt több checkbox opció van (pl. "Mi a legmagasabb iskolai végzettsége? ☐ Felsőfokú ☐ Szakképesítés ☐ Érettségi ☐ 8 általános"), ez egy EXKLÚZÍV csoport. MINDEN tagnak add meg a `checkbox_group`-ot ugyanazzal `group_id`-val és `group_label`-lel, de KÜLÖNBÖZŐ `option_value`/`option_label`-lel. A `group_label` a kérdés szövege. Ha a `pdf_field_name` végén pl. `___1,2` vagy `___Yes` szerepel, az azt jelenti, hogy ez egy rádiógomb — ilyenkor MÁSOLD BE a bbox koordinátákat, keresd meg a képen a feliratot, és töltsd ki a `checkbox_group` mezőket!"""

    def __init__(self, api_key: str = None):
        """
        Inicializálás Anthropic API kulccsal.

        Args:
            api_key: Anthropic API kulcs. Ha None, a ANTHROPIC_API_KEY env-ből olvassa.
        """
        if api_key is None:
            import os
            from pathlib import Path
            from dotenv import load_dotenv
            # Try to load .env from project config directory
            root = Path(__file__).resolve().parent.parent.parent
            env_path = root / "config" / ".env"
            if env_path.exists():
                load_dotenv(env_path)
            api_key = os.getenv("ANTHROPIC_API_KEY", "")

        if not api_key:
            logger.warning(
                "ANTHROPIC_API_KEY nincs beállítva. "
                "Az AI mezőfelismerés nem fog működni – használd a mock módot."
            )
            self._client = None
        else:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                logger.error("anthropic csomag nincs telepítve: pip install anthropic")
                self._client = None
        self.progress_callback = None  # Optional[Callable[[int, int, str], None]]

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

        # 3. AI leképezés – mindig az annotált piros keretes batch módot használjuk
        ai_mapping = None
        if self._client:
            ai_mapping = self._ai_batch_recognize(pdf_path, pdf_fields)

        if ai_mapping:
            # AI eredmények összefésülése a heurisztikussal
            base_mapping = self._merge_mappings(base_mapping, ai_mapping, pdf_fields=pdf_fields)
        else:
            char_groups, _ = _detect_character_groups(pdf_fields, base_mapping.fields)
            _postprocess_recognized_fields(base_mapping.fields, char_groups)
            if char_groups:
                base_mapping.character_groups = char_groups

        return base_mapping

    def _ai_batch_recognize(
        self, pdf_path: Path, pdf_fields: list[dict]
    ) -> MappingConfig | None:
        """
        Batch AI felismerés nagy nyomtatványokhoz.
        Mezőket OLDAL szerint csoportosítja,
        majd kétlépcsős AI-val azonosít.

        Vision-enhanced: mezők bounding box-ait piros számozott
        keretként rajzolja rá az oldal képére, hogy az AI a
        vizuális kontextusból is felismerje a generikus mezőneveket.
        """
        from collections import defaultdict
        import time
        import re

        logger.info(f"🔄 Batch AI felismerés ({len(pdf_fields)} mező)")

        # ── 1. PDF megnyitása – képeket batch-enként generálunk ──────────
        import fitz
        page_texts: list[str] = []
        try:
            doc = fitz.open(str(pdf_path))
            for page in doc:
                page_texts.append(page.get_text()[:3000])
            doc.close()
            logger.info(f"  📄 {len(page_texts)} oldal szövege kinyerve")
        except Exception as e:
            logger.warning(f"  ⚠️ PDF szöveg kinyerés sikertelen: {e}")

        # ── 2. Mezők csoportosítása OLDAL szerint ───────────────────────
        page_groups: dict[int, list[dict]] = defaultdict(list)
        for f in pdf_fields:
            page_num = f.get("page", 1)
            page_groups[page_num].append(f)

        logger.info(f"  {len(page_groups)} oldal mezőkkel")

        # ── 3. Kanonikus mezők leírása (SF schema-ból) ───────────────────
        canonical_desc = _canonical_catalog_description()

        # ── 4. Oldal-alapú batch feldolgozás ────────────────────────────
        all_fields: list[RecognizedField] = []
        batch_count = 0
        MAX_FIELDS_PER_BATCH = 60

        # Build canonical lookup for fuzzy matching
        canonical_keys = set(CANONICAL_FIELDS.keys())

        for page_num in sorted(page_groups.keys()):
            page_fields = page_groups[page_num]
            page_idx = page_num - 1

            sub_batches = [page_fields[i:i + MAX_FIELDS_PER_BATCH]
                           for i in range(0, len(page_fields), MAX_FIELDS_PER_BATCH)]

            for sub_idx, sub_fields in enumerate(sub_batches):
                batch_count += 1
                sub_label = f"p{page_num}" if len(sub_batches) == 1 else f"p{page_num} ({sub_idx+1}/{len(sub_batches)})"
                fields_with_bbox = sum(1 for f in sub_fields if "rect" in f)
                logger.info(f"  📦 [{batch_count}] {sub_label}: {len(sub_fields)} mező ({fields_with_bbox} bbox-al) 📸...")

                # Compute total_batches for progress reporting
                total_batches = sum(
                    max(1, (len(page_groups[p]) + MAX_FIELDS_PER_BATCH - 1) // MAX_FIELDS_PER_BATCH)
                    for p in page_groups
                )


                # ── Vision-enhanced: annotated page image ────────────
                # Draw numbered red rectangles on the page at each field's
                # bounding box, so the AI can visually identify generic fields.
                annotated_image_b64 = None
                try:
                    doc = fitz.open(str(pdf_path))
                    if 0 <= page_idx < len(doc):
                        page = doc[page_idx]
                        # Draw red numbered boxes for fields with bounding boxes
                        # rect is [x, y, width, height] in RENDERED PIXELS
                        # (150 dpi, top-left origin) — convert to PDF points
                        # for drawing on the page.
                        px_to_pt = 72.0 / 150.0
                        box_count = 0
                        for i, f in enumerate(sub_fields, 1):
                            if "rect" in f:
                                r = f["rect"]  # [x, y, w, h] rendered pixels
                                rect = fitz.Rect(
                                    r[0] * px_to_pt,
                                    r[1] * px_to_pt,
                                    (r[0] + r[2]) * px_to_pt,
                                    (r[1] + r[3]) * px_to_pt,
                                )
                                # Red rectangle outline
                                page.draw_rect(rect, color=(1, 0, 0), width=1.5)
                                # Number label above the box
                                label_pt = fitz.Point(rect.x0 + 1, rect.y0 - 1)
                                page.insert_text(
                                    label_pt, str(i),
                                    fontsize=7, color=(1, 0, 0),
                                )
                                box_count += 1
                        # Render annotated page at 150 DPI
                        mat = fitz.Matrix(150 / 72, 150 / 72)
                        pix = page.get_pixmap(matrix=mat)
                        annotated_image_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                        if box_count > 0:
                            logger.info(f"    📸 Annotált kép: {box_count} piros keret rajzolva")
                    doc.close()
                except Exception as e:
                    logger.warning(f"    ⚠️ Annotált kép generálás sikertelen: {e}")

                # Build the system prompt with vision-enhanced instructions
                system = f"""Output ONLY a raw JSON array. No markdown, no explanation, no code fences.

You are mapping PDF form field names to Genie canonical paths for a Hungarian mortgage broker system.
The canonical paths follow the format: namespace.field (e.g., participant.borrower.name, property.address.zip_code, deal.loan_amount).
Participants are distinguished by ROLE: applicant = participant.borrower.*, co-applicant (társigénylő/adóstárs) = participant.co_borrower.*,
guarantor = participant.guarantor.*, mortgagor = participant.mortgagor.*, usufructuary = participant.usufructuary.*.
Properties live in the property.* namespace; deal-level data in the deal.* namespace.

VISION INSTRUCTIONS:
The page image has NUMBERED RED BOXES drawn around form fields.
Each field in the list below has a NUMBER (e.g., #1, #2, ...).
LOOK AT THE RED BOX with that number on the image to see the LABEL next to the field.
This is CRITICAL for fields with generic names like "Text_Field_43" or "Szövegmező 123" — 
the RED BOX shows you WHERE the field is, and the printed label next to it tells you WHAT it contains.

GENIE CANONICAL PATHS (use exactly these keys):
{canonical_desc}

EXAMPLES of correct mappings:
  {{"f": "SZA_IG_név", "c": "participant.borrower.name", "t": "text"}} — "név" = applicant name
  {{"f": "SZA_IG_szül_név", "c": "participant.borrower.birth_name", "t": "text"}} — "szül név" = birth name
  {{"f": "SZA_IG_anyja_neve", "c": "participant.borrower.mother_name", "t": "text"}} — "anyja neve" = mother's name
  {{"f": "SZA_IG_szül_hely", "c": "participant.borrower.birth_place", "t": "text"}} — "szül hely" = birthplace
  {{"f": "SZA_IG_szül_dátum", "c": "participant.borrower.birth_date", "t": "date"}} — "szül dátum" = birth date
  {{"f": "SZA_IG_irányítószám", "c": "participant.borrower.address.zip_code", "t": "text"}} — "irsz" = zip code
  {{"f": "SZA_IG_személyi_szám", "c": "participant.borrower.personal_number", "t": "text"}}
  {{"f": "SZA_IG_adóazonosító", "c": "participant.borrower.tax_id", "t": "text"}}
  {{"f": "SZA_IG_email", "c": "participant.borrower.email", "t": "text"}}
  {{"f": "SZA_IG_mobil", "c": "participant.borrower.mobile_phone", "t": "text"}}
  {{"f": "SZA_IG_munkáltató", "c": "participant.borrower.employer", "t": "text"}}
  {{"f": "SZA_IG_jövedelem", "c": "participant.borrower.monthly_income", "t": "number"}}
  {{"f": "SZA_IG_név-társ", "c": "participant.co_borrower.name", "t": "text"}} — "-társ" suffix = co-applicant!
  {{"f": "SZA_IG_adóazonosító-társ", "c": "participant.co_borrower.tax_id", "t": "text"}}
  {{"f": "IA_település", "c": "property.address.city", "t": "text"}} — "IA" = ingatlan adatlap (property)
  {{"f": "IA_irsz", "c": "property.address.zip_code", "t": "text"}}
  {{"f": "IA_alapterület", "c": "property.area_sqm", "t": "number"}}
  {{"f": "IA_helyrajzi", "c": "property.parcel_number", "t": "text"}} — helyrajzi szám / hrsz
  {{"f": "LAHI_összeg", "c": "deal.loan_amount", "t": "number"}} — "LAHI" = lakáshitel (deal level)

CHECKBOX GROUP DETECTION (CRITICAL — do this for EVERY mutually-exclusive option group):
When you see a question followed by multiple checkboxes, each next to a different option
(e.g. "Mi a legmagasabb iskolai végzettsége?  ☐ Felsőfokú  ☐ Szakképesítés  ☐ Érettségi  ☐ 8 általános"),
this is a RADIO/EXCLUSIVE group. For EACH checkbox in the group, add ALL of:
  "g"   — group_id: stable snake_case id shared by all members (e.g. "highest_education")
  "gl"  — group_label: the QUESTION text (e.g. "Legmagasabb iskolai végzettség")
  "ov"  — option_value: the value this option represents (e.g. "Felsőfokú")
  "ol"  — option_label: the option text printed next to this checkbox (e.g. "Felsőfokú")

Rules:
- Every member of an exclusive group MUST share the SAME "g" and "gl", but have DIFFERENT "ov"/"ol".
- "ov" must be the stored value of the canonical field the group maps to (e.g. participant.borrower.education = "Felsőfokú").
- If a checkbox is a standalone yes/no (not part of a picklist group), do NOT set "g" — leave it as a plain checkbox.
- Look at the RED BOXES: options belonging to one question are visually clustered (same row or stacked column).

Example (full group, 4 members):
  {{"f": "végzettség___1,2", "c": "participant.borrower.education", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Felsőfokú", "ol": "Felsőfokú"}}
  {{"f": "végzettség___2,3", "c": "participant.borrower.education", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Szakképesítés", "ol": "Szakképesítés"}}
  {{"f": "végzettség___3,4", "c": "participant.borrower.education", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Érettségi", "ol": "Érettségi"}}
  {{"f": "végzettség___4,5", "c": "participant.borrower.education", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "8 általános", "ol": "8 általános"}}

RULES:
- Map a field ONLY when an EXACT semantic match exists in the Genie canonical list above.
- NEVER guess or approximate! If no exact semantic match exists (e.g. room counts, elevator, heating type, currency code "deviza", "Írástudatlan/Látássérült", "Egyéb dokumentum szöveges", document copy counts on cover page), MUST set "c": null!
- NEVER invent a path that is not in the canonical list — unknown values are dropped, not guessed.
- property.parcel_number is ONLY for parcel number (helyrajzi szám / hrsz) — NEVER map other property questions to it!
- property.address.street is for the property street address ("Utca, házszám").
- Use the canonical paths exactly as shown in the canonical list.
- For GENERIC field names (e.g., "Text_Field_43", "Szövegmező 123", "Check_Box_5"), ALWAYS look at the RED BOX on the image to find the label.
- Use PDF field name prefixes as hints: SZA_IG = személyi (participant.borrower.* fields), "-társ" suffix = társigénylő (participant.co_borrower.*), IA = ingatlan (property.* fields), CSOK = family subsidy (deal.csok_*), LAHI = lakáshitel (deal.* fields), MA = munkáltatói (participant.borrower.employer* fields)
- When a field belongs to a co-borrower (társigénylő / adóstárs section or "-társ" suffix), map it to the participant.co_borrower.* variant of the path.

Output format: [{{"f": "field_name", "l": "human readable Hungarian label from the printed text next to the field", "c": "canonical.path", "t": "text|checkbox|date|number", "p": {page_num}}}]
For checkbox groups add: "g": "group_id", "gl": "group_label", "ov": "option_value", "ol": "option_label"
The "l" label is REQUIRED for every field — read the printed label next to the RED BOX on the image
(e.g. "Személyazonosító igazolvány száma", "Anyja neve"). Never just repeat the field name.

Page {page_num}. RESPOND WITH ONLY THE JSON ARRAY."""



                content = []

                # Send the annotated image (with numbered red boxes)
                if annotated_image_b64:
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": annotated_image_b64},
                    })

                # Page text for context
                if page_idx < len(page_texts) and page_texts[page_idx].strip():
                    text_preview = page_texts[page_idx][:2500]
                    content.append({"type": "text", "text": f"Page text content:\n{text_preview}"})

                # Field names with numbered reference to RED BOXES on image
                fields_text = f"PDF form fields on this page ({len(sub_fields)} fields).\n"
                fields_text += "Fields with RED BOXES on the image are marked with their box number.\n\n"
                for i, f in enumerate(sub_fields, 1):
                    has_bbox = "rect" in f
                    box_ref = f"  #{i}  " if has_bbox else "  --  "
                    bbox_hint = " ← SEE RED BOX on image" if has_bbox else ""
                    fields_text += f"{box_ref}{f['name']} (type: {f['type']}){bbox_hint}\n"
                content.append({"type": "text", "text": fields_text})

                try:
                    response = self._client.messages.create(
                        model=AI_MODEL,
                        max_tokens=8192,
                        temperature=0.0,
                        system=system,
                        messages=[
                            {"role": "user", "content": content},
                        ],
                    )

                    response_text = ""
                    for block in response.content:
                        if hasattr(block, "text"):
                            response_text = block.text
                            break

                    if response_text and response_text.strip():
                        # Log raw response for debugging
                        logger.debug(f"    📝 Raw AI response (first 500): {response_text[:500]}")
                        
                        for attempt in range(2):
                            try:
                                json_str = self._extract_json(response_text) if attempt == 0 else response_text.strip()
                                ai_items = json.loads(json_str)
                                if not isinstance(ai_items, list):
                                    ai_items = ai_items.get("fields", []) if isinstance(ai_items, dict) else []
                                
                                mapped = 0
                                skipped = 0
                                for item in ai_items:
                                    if not isinstance(item, dict):
                                        continue
                                    
                                    field_name = item.get("f", "")
                                    canonical = item.get("c")
                                    
                                    if not field_name:
                                        continue
                                    
                                    # Fuzzy match canonical field (registry-only:
                                    # nincs találat → a canonical elutasítása,
                                    # a mező nem-lemappeltként él tovább).
                                    if canonical and canonical not in canonical_keys:
                                        best = self._fuzzy_match_canonical(canonical, canonical_keys)
                                        if best:
                                            logger.debug(f"    🔄 Fuzzy: '{canonical}' → '{best}'")
                                            canonical = best
                                        else:
                                            logger.debug(
                                                "    ✋ Nem registry-canonical AI érték elutasítva: %r",
                                                canonical,
                                            )
                                            canonical = None
                                    
                                    t_val = item.get("t", "text")
                                    try:
                                        f_type = FieldType(t_val)
                                    except ValueError:
                                        f_type = FieldType.TEXT
                                    
                                    ai_label = (item.get("l") or "").strip()
                                    if not canonical:
                                        skipped += 1
                                        if ai_label:
                                            all_fields.append(RecognizedField(
                                                pdf_field_name=field_name,
                                                label=ai_label,
                                                field_type=f_type,
                                                canonical_field=None,
                                                confidence=MappingConfidence.LOW,
                                                page_number=item.get("p", page_num),
                                            ))
                                        continue
                                    
                                    all_fields.append(RecognizedField(
                                        pdf_field_name=field_name,
                                        label=ai_label or field_name.replace("_", " ").title(),
                                        field_type=f_type,
                                        canonical_field=canonical,
                                        confidence=MappingConfidence.MEDIUM,
                                        page_number=item.get("p", page_num),
                                        checkbox_group=_normalize_checkbox_group(item),
                                        fill_rule=_sanitize_fill_rule(item.get("fill_rule")),
                                    ))
                                    mapped += 1
                                
                                logger.info(f"    ✅ {mapped} mező leképezve, {skipped} null/skip")
                                # Fire progress callback so the UI can update
                                if self.progress_callback is not None:
                                    try:
                                        self.progress_callback(batch_count, total_batches, sub_label)
                                    except Exception:
                                        pass
                                break
                            except (json.JSONDecodeError, ValueError) as e:
                                if attempt == 0:
                                    logger.debug(f"    JSON parse attempt 1 failed: {str(e)[:80]}, retrying raw...")
                                    continue
                                logger.warning(f"    ⚠️ JSON hiba (2 kísérlet után): {str(e)[:80]}")
                                logger.info(f"    Nyers válasz (első 500): {response_text[:500]}")
                    else:
                        logger.warning(f"    ⚠️ Üres válasz")

                except Exception as e:
                    logger.error(f"    ❌ API hiba: {str(e)[:120]}")

                # Rate limit
                time.sleep(1)

        if not all_fields:
            logger.warning("Batch AI: egyetlen oldal sem adott eredményt")
            return None

        total_mapped = sum(1 for f in all_fields if f.canonical_field)
        logger.info(f"🏁 Batch AI összesítés: {total_mapped}/{len(all_fields)} mező leképezve ({batch_count} API hívás)")

        # PLAN §3.5 — checkbox-csoport konzisztencia ellenőrzés (nem blokkol)
        group_errors = _validate_checkbox_groups(all_fields)
        for e in group_errors:
            logger.warning("⚠️ checkbox_group: %s", e)

        # M5-auto: karakterdoboz-csoportok geometriai felismerése
        # (egy sorban lévő, azonos canonicalra mappelt kis dobozok →
        #  character_groups, hogy a filler karakterenként ossza szét)
        char_groups, char_members = _detect_character_groups(pdf_fields, all_fields)
        _postprocess_recognized_fields(all_fields, char_groups)
        if char_members:
            logger.info(f"🧩 {len(char_groups)} karakterdoboz-csoport felismerve ({len(char_members)} tagmező)")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="acroform",
            fields=all_fields,
            character_groups=char_groups,
            notes=f"AI batch felismerés ({len(page_groups)} oldal, {batch_count} API hívás).",
        )

    @staticmethod
    def _fuzzy_match_canonical(candidate: str, valid_keys: set[str]) -> str | None:
        """Snap an AI-returned canonical path to a valid catalog key.

        Cutover Task 4: the match is a FORMAT tolerance (case, spaces,
        hyphens→underscores), never semantic guessing. The old behaviour that
        accepted AI-invented prefixes (``csok.``, ``document.``,
        ``participant.<unknown>`` …) as-is is DELETED: every accepted value
        must be a registry-approved Genie path, unknown values return None and
        the caller drops them.
        """
        if not isinstance(candidate, str):
            return None
        lower_index: dict[str, str] = {k.lower(): k for k in valid_keys}

        c = candidate.lower().strip().replace(" ", "_").replace("-", "_")
        if not c:
            return None
        # Exact (case-insensitive) hit → return the original-cased key.
        if c in lower_index:
            return lower_index[c]

        # Tolerate an explicit participant/property index the catalog omits:
        # "participant.borrower[1].name" ↔ "participant.borrower.name".
        stripped = re.sub(r"\[\d+\]", "", c)
        if stripped != c and stripped in lower_index:
            return lower_index[stripped]
        with_index = c.replace("[", "")
        if with_index != c and with_index in lower_index:
            return lower_index[with_index]

        return None

    def _merge_mappings(
        self,
        base: MappingConfig,
        ai: MappingConfig,
        pdf_fields: list[dict] | None = None,
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
                    if ai_field.label and (
                        not base_field.label
                        or base_field.label == base_field.pdf_field_name.replace("_", " ").title()
                    ):
                        base_field.label = ai_field.label
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

        # Merge character_groups from base and ai, plus re-detect if pdf_fields provided
        char_groups: list[dict] = []
        seen_members: set[str] = set()
        for grp in list(ai.character_groups or []) + list(base.character_groups or []):
            members = grp.get("member_fields", [])
            if members and not any(m in seen_members for m in members):
                char_groups.append(grp)
                seen_members.update(members)

        if pdf_fields:
            extra_groups, _ = _detect_character_groups(pdf_fields, merged)
            for grp in extra_groups:
                members = grp.get("member_fields", [])
                if members and not any(m in seen_members for m in members):
                    char_groups.append(grp)
                    seen_members.update(members)

        _postprocess_recognized_fields(merged, char_groups)

        total_mapped = sum(1 for f in merged if f.canonical_field)
        logger.info(f"📊 Merge eredmény: {total_mapped}/{len(merged)} mező leképezve, {len(char_groups)} character_group")

        # PLAN §3.5 — checkbox-csoport konzisztencia ellenőrzés a merge után
        group_errors = _validate_checkbox_groups(merged)
        for e in group_errors:
            logger.warning("⚠️ checkbox_group (merge): %s", e)

        return MappingConfig(
            bank_name=base.bank_name or ai.bank_name,
            form_name=base.form_name or ai.form_name,
            form_type=base.form_type,
            fields=merged,
            character_groups=char_groups,
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
                    "height": af["height"],
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
            text_dict = page.get_text("rawdict")
            all_spans = []

            for block in text_dict["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        all_spans.append(span)

            for i, span in enumerate(all_spans):
                if "chars" not in span or not span["chars"]:
                    continue
                text = "".join([c["c"] for c in span["chars"]])
                if not any(char in text for char in ("…", ".", "_", "□", "☐", "☒")):
                    continue

                bbox = span["bbox"]  # [x0, y0, x1, y1]
                font_size = span["size"]

                # Find all dot/underscore matches in the span text (at least 4 characters long)
                dot_matches = list(re.finditer(r'[…._]{4,}', text))
                # Find all checkboxes
                cb_matches = list(re.finditer(r'[□☐☒]', text))

                # Combine and sort matches
                matches = []
                for m in dot_matches:
                    matches.append((m, "text"))
                for m in cb_matches:
                    matches.append((m, "checkbox"))
                matches.sort(key=lambda x: x[0].start())

                if not matches:
                    continue

                prev_end = 0
                for match_idx, (match_obj, match_type) in enumerate(matches):
                    start, end = match_obj.start(), match_obj.end()

                    # pre_text is the text preceding the current match since the previous match end
                    pre_text = text[prev_end:start].strip("() ,;:._")
                    # post_text is the text after the current match until the next match start or end of text
                    next_start = matches[match_idx + 1][0].start() if match_idx + 1 < len(matches) else len(text)
                    post_text = text[end:next_start].strip("() ,;._ ")

                    # Precise coordinates using character bboxes
                    char_start = span["chars"][start]
                    char_end = span["chars"][end - 1]

                    fill_x = char_start["bbox"][0]
                    fill_y = char_start["bbox"][1]
                    fill_w = char_end["bbox"][2] - fill_x
                    fill_h = char_start["bbox"][3] - char_start["bbox"][1]

                    # Kontextus: előtte + utána lévő szöveg (5 span)
                    context_parts = []
                    for j in range(max(0, i-2), min(len(all_spans), i+3)):
                        s = all_spans[j]
                        if "chars" in s and s["chars"]:
                            t = "".join([c["c"] for c in s["chars"]]).strip()
                            if t and not any(char in t for char in ("…", ".", "_", "□", "☐", "☒")):
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
                            s_text = "".join([c["c"] for c in s["chars"]]) if "chars" in s else ""
                            candidate = s_text.strip("() ,;._")
                            if len(candidate) > 2 and not any(char in candidate for char in ("…", ".", "_", "□", "☐", "☒")):
                                label = candidate
                                break

                    # 2. Pre-text (pl. "Kelt:", "Név:", "Születési hely")
                    if not label and pre_text:
                        clean = pre_text
                        if "(" in clean:
                            clean = clean.split("(")[-1]
                        clean = clean.rstrip(":").strip("() ,;:._")
                        if len(clean) > 1 and not any(char in clean for char in ("…", ".", "_", "□", "☐", "☒")):
                            label = clean

                    # 3. Post-text ha nem pontsor
                    if not label and post_text and len(post_text) > 2 and not any(char in post_text for char in ("…", ".", "_", "□", "☐", "☒")):
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
                        "width": round(max(fill_w, 8), 1) if match_type == "checkbox" else round(max(fill_w, 20), 1),
                        "height": round(fill_h, 1),
                        "page": page_idx + 1,
                        "context": context,
                    })

                    # Update prev_end for the next match on the same line
                    prev_end = end

        doc.close()
        return fields

    @staticmethod
    def _sanitize_label_map(raw: dict) -> dict:
        """Registry-szűrés egy AI ``{mező_id: canonical}`` válaszra.

        Cutover Task 4 (requirement 1): a nem registry-approved canonical
        értékek kikerülnek a mapből — a hozzájuk tartozó mezők nem-lemappeltként
        élnek tovább, a felismert mapping sosem tartalmaz elavult/kitalált
        canonicalt.
        """
        if not isinstance(raw, dict):
            return {}
        sanitized: dict = {}
        for key, value in raw.items():
            clean = _sanitize_canonical(value)
            if clean is not None:
                sanitized[key] = clean
            elif value:
                logger.debug("  ✋ AI label-map érték elutasítva: %r → %r", key, value)
        return sanitized

    def _ai_map_flat_labels(
        self, anchor_fields: list[dict], pdf_path: Path
    ) -> dict:
        """
        AI szemantikus mapping: label + kontextus → kanonikus mező.
        Csak egyszer hívja az AI-t, nem küld képeket – tisztán szöveges.
        """
        canonical_desc = _canonical_catalog_description()

        fields_desc = "\n".join(
            f'  - id="{f["id"]}" label="{f["label"]}" context="{f["context"][:100]}"'
            for f in anchor_fields
        )

        prompt = f"""Párosítsd az alábbi banki nyomtatvány mezőket a Genie kanonikus mezőkkel.

Genie kanonikus mezők (CSAK ezek, pontos alakban):
{canonical_desc}

Nyomtatvány mezők:
{fields_desc}

Válaszolj CSAK JSON objektummal: {{"mező_id": "canonical.path", ...}}
Ha egy mező nem képezhető le a fenti listából, hagyd ki — sosem találd ki új útvonalat.
PDF: {pdf_path.name}"""

        try:
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text = block.text
                    break

            if response_text:
                json_str = self._extract_json(response_text)
                return self._sanitize_label_map(json.loads(json_str))
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

    def _extract_acroform_fields(self, pdf_path: Path) -> list[dict]:
        """AcroForm mezők kinyerése.

        FIX L8 — korábban a ``backend/pdf_service``-t importálta, ami egy
        rétegsértő függőség volt (``src/`` → ``backend/``). A kinyerő logika
        most a ``src/engine/acroform_field_extractor`` modulban él, így az AI
        réteg nem a backend-re támaszkodik.
        """
        from src.engine.acroform_field_extractor import extract_acroform_fields

        fields: list[dict] = []
        try:
            extracted = extract_acroform_fields(pdf_path)
            for ext in extracted:
                rect_dict = ext.get("rect", {})
                fields.append({
                    "name": ext.get("pdf_field_name"),
                    "type": ext.get("field_type"),
                    "value": ext.get("value", ""),
                    "page": ext.get("page_number", 1),
                    "rect": [
                        rect_dict.get("x", 0),
                        rect_dict.get("y", 0),
                        rect_dict.get("width", 0),
                        rect_dict.get("height", 0)
                    ]
                })
            logger.info(f"{len(fields)} AcroForm mező kinyerve (src/engine).")
        except Exception as e:
            logger.error(f"PDF olvasási hiba (acroform_field_extractor): {e}")

        return fields

    def _ai_map_fields(
        self, pdf_path: Path, pdf_fields: list[dict], form_type: str
    ) -> MappingConfig:
        """AI-alapú mezőleképezés Claude API-val."""
        canonical_desc = _canonical_catalog_description()

        system = self.SYSTEM_PROMPT.format(canonical_fields=canonical_desc)

        # PDF oldalak konvertálása képekké az AI számára (Mindig elküldjük, hogy a checkboxok melletti szövegeket el tudja olvasni)
        images = []
        images = self._pdf_to_images(pdf_path)

        selected_indices = []
        if images:
            max_images = 20
            if len(images) > max_images:
                logger.info(
                    f"  {len(images)} oldal → csak az első {max_images} oldal képét küldjük "
                    f"(a mezőneveket mind elküldjük)"
                )
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
                rect_str = f", bbox: {f['rect']}" if f.get('rect') else ""
                fields_text += f"  - {f['name']} (típus: {f['type']}, oldal: {f.get('page', '?')}{rect_str})\n"
            content.append({"type": "text", "text": fields_text})

        content.append({
            "type": "text",
            "text": "Kérlek elemezd ezt a banki nyomtatványt és készítsd el a mező-leképezést a kanonikus modellre.",
        })

        try:
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=16384,
                system=system,
                messages=[{"role": "user", "content": content}],
            )

            # A válaszban több content block is lehet (thinking + text)
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text = block.text
                    break

            if not response_text:
                logger.error("AI válasz nem tartalmaz szöveget")
                return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

            logger.debug(f"AI válasz (első 500 karakter): {response_text[:500]}")
            json_str = self._extract_json(response_text)
            import json_repair
            ai_result = json_repair.loads(json_str)

            return self._parse_ai_response(ai_result, pdf_path, form_type)

        except Exception as e:
            logger.error(f"AI API hiba: {e}")
            logger.info("Visszaesés heurisztikus leképezésre")
            return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

    def _ai_recognize_flat_pdf(self, pdf_path: Path) -> MappingConfig:
        """Lapos PDF AI-alapú felismerése koordinátákkal."""
        canonical_desc = _canonical_catalog_description()

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
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=16384,
                system=system,
                messages=[{"role": "user", "content": content}],
            )

            response_text = response.content[0].text
            json_str = self._extract_json(response_text)
            import json_repair
            ai_result = json_repair.loads(json_str)

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
            if candidate.startswith('{') or candidate.startswith('['):
                return candidate

        # 3. Ha a szöveg JSON tömbbel kezdődik → keressük a [ és ] párját
        stripped = text.strip()
        if stripped.startswith('['):
            bracket_depth = 0
            for i, ch in enumerate(stripped):
                if ch == '[':
                    bracket_depth += 1
                elif ch == ']':
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        return stripped[:i + 1]

        # 4. Keressük az első { és a hozzá tartozó záró } karaktert
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

        # 5. Utolsó fallback: az első [ és utolsó ] (tömb) vagy { és utolsó }
        if '[' in text and ']' in text:
            start = text.index('[')
            end = text.rindex(']') + 1
            return text[start:end]

        if '{' in text and '}' in text:
            start = text.index('{')
            end = text.rindex('}') + 1
            return text[start:end]

        raise ValueError(f"Nem található JSON a válaszban. Válasz (első 300 karakter): {text[:300]}")

    def _parse_ai_response(
        self, ai_result, pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """AI válasz parse-olása MappingConfig-gá.

        Handles multiple response formats Claude may return:
        - ``{"form_name": ..., "fields": [...]}`` — the canonical format
          requested by SYSTEM_PROMPT.
        - ``[{...}, {...}]`` — a bare list of field objects (common when
          the AI ignores the wrapper and returns just the array).
        - ``{"fields": {...}}`` — a dict where fields is not a list.
        """
        # Normalize ai_result to (fields_list, form_name, page_structure)
        page_structure = {}

        if isinstance(ai_result, list):
            # Bare list of field objects — treat the whole thing as fields.
            raw_fields = ai_result
            form_name = pdf_path.stem
            logger.info("AI válasz: lista formátum (%d mező)", len(raw_fields))
        elif isinstance(ai_result, dict):
            # Dict — may have "fields" key, or may BE a single field.
            raw_fields_data = ai_result.get("fields", [])
            if isinstance(raw_fields_data, dict):
                # Single field wrapped in a dict (edge case).
                raw_fields = [raw_fields_data]
            else:
                raw_fields = raw_fields_data
            form_name = ai_result.get("form_name", pdf_path.stem)
            page_structure = ai_result.get("page_structure", {})
            logger.info("AI válasz: dict formátum (%d mező)", len(raw_fields))
        else:
            logger.error(
                "AI válasz ismeretlen formátum: %s (type=%s)",
                str(ai_result)[:200],
                type(ai_result).__name__,
            )
            raw_fields = []
            form_name = pdf_path.stem

        fields = []
        for f_data in raw_fields:
            if not isinstance(f_data, dict):
                logger.warning("Mezőadat nem dict: %s — kihagyva", str(f_data)[:100])
                continue
            pdf_name = f_data.get("pdf_field_name") or f_data.get("f") or f_data.get("name")
            if not pdf_name:
                continue
            fields.append(RecognizedField(
                pdf_field_name=pdf_name,
                label=f_data.get("label", f_data.get("l", "")),
                field_type=FieldType(f_data.get("field_type", f_data.get("t", "text"))),
                # Cutover Task 4: nem registry-canonical AI érték → None
                # (a mező nem-lemappeltként él tovább, sosem kerül legacy
                # név a kimeneti mappingbe).
                canonical_field=_sanitize_canonical(
                    f_data.get("canonical_field") or f_data.get("c")
                ),
                confidence=MappingConfidence(
                    f_data.get("confidence", f_data.get("conf", "medium"))
                ),
                page_number=f_data.get("page_number", f_data.get("p", 1)),
                coordinates=f_data.get("coordinates"),
                notes=f_data.get("notes"),
                checkbox_group=_normalize_checkbox_group(f_data.get("checkbox_group")),
                fill_rule=_sanitize_fill_rule(f_data.get("fill_rule")),
            ))

        if not fields:
            logger.warning(
                "AI válasz parse: 0 mező kinyerve (ai_result type=%s)",
                type(ai_result).__name__,
            )

        # PLAN §3.5 — checkbox-csoport konzisztencia ellenőrzés
        group_errors = _validate_checkbox_groups(fields)
        for e in group_errors:
            logger.warning("⚠️ checkbox_group (parse): %s", e)

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=form_name,
            form_type=form_type,
            fields=fields,
            page_structure=page_structure,
        )

    def _heuristic_map_fields(
        self, pdf_fields: list[dict], pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """Heurisztikus mezőleképezés mezőnevek alapján."""
        logger.info("Heurisztikus mezőleképezés (AI nélkül)")

        # Általános kulcsszó → kanonikus leképezés.
        # Cutover Task 4: minden érték registry-approved Genie path — a régi
        # Salesforce-neves értékeket a canonical_migration_map audited
        # fordításaival (keyword ∘ audited map) komponáltuk át. A kulcsszavas
        # ág prefix/suffix-eltávolítás UTÁN fut, ahol a "-társ" jelzés már
        # elveszett — ezért ez az ág mindig ADÓS (borrower) scope-ot ad.
        KEYWORD_MAP = {
            # Személyi adatok (adós)
            "nev": "participant.borrower.name",
            "name": "participant.borrower.name",
            "szul_nev": "participant.borrower.birth_name",
            "szuletesi_nev": "participant.borrower.birth_name",
            "birth_name": "participant.borrower.birth_name",
            "anyja_neve": "participant.borrower.mother_name",
            "mother_name": "participant.borrower.mother_name",
            "szul_hely": "participant.borrower.birth_place",
            "szuletesi_hely": "participant.borrower.birth_place",
            "birth_place": "participant.borrower.birth_place",
            "szul_datum": "participant.borrower.birth_date",
            "szuletesi_datum": "participant.borrower.birth_date",
            "birth_date": "participant.borrower.birth_date",
            "szemelyi_szam": "participant.borrower.personal_id",
            "szem_ig": "participant.borrower.personal_id",
            "personal_id": "participant.borrower.personal_id",
            "adoazonosito": "participant.borrower.tax_id",
            "adoaz": "participant.borrower.tax_id",
            "tax_id": "participant.borrower.tax_id",
            # Cím (adós)
            "lakcim": "participant.borrower.permanent_address",
            "address": "participant.borrower.permanent_address",
            "iranyitoszam": "participant.borrower.address.zip_code",
            "irsz": "participant.borrower.address.zip_code",
            "zip": "participant.borrower.address.zip_code",
            "zip_code": "participant.borrower.address.zip_code",
            "telepules": "participant.borrower.mailing_address.city",
            "varos": "participant.borrower.mailing_address.city",
            "city": "participant.borrower.mailing_address.city",
            "utca": "participant.borrower.mailing_address.street",
            "kozterulet": "participant.borrower.mailing_address.street",
            "street": "participant.borrower.mailing_address.street",
            "hazszam": "participant.borrower.mailing_address.street",
            "hsz": "participant.borrower.mailing_address.street",
            "house_number": "participant.borrower.mailing_address.street",
            # Kontakt
            "telefon": "participant.borrower.phone",
            "telefonszam": "participant.borrower.phone",
            "phone": "participant.borrower.phone",
            "email": "participant.borrower.email",
            "e_mail": "participant.borrower.email",
            # Munka
            "munkaltato": "participant.borrower.employer",
            "munkahely": "participant.borrower.employer",
            "employer": "participant.borrower.employer",
            "jovedelem": "participant.borrower.monthly_income",
            "havi_netto": "participant.borrower.monthly_income",
            "monthly_income": "participant.borrower.monthly_income",
            # Hitel (ügylet-szintű)
            "hitelosszeg": "deal.loan_amount",
            "kolcson_osszeg": "deal.loan_amount",
            "igenyelt_kolcson": "deal.loan_amount",
            "loan_amount": "deal.loan_amount",
            "futamido": "deal.loan_term_years",
            "loan_term": "deal.loan_term_years",
            "kamatperiodus": "deal.interest_period",
            "hitel_celja": "deal.loan_purpose",
            "loan_purpose": "deal.loan_purpose",
            "onero": "deal.down_payment",
            "torleszto": "participant.borrower.affordable_monthly_installments",
            # Ingatlan (property névtér)
            # (helyrajzi szám / hrsz a property.parcel_number mezőre képeződik)
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

        # OTP-specifikus mező → kanonikus leképezés (valódi mezőnevek a PDF-ből).
        # Cutover Task 4: Genie canonical útvonalak (audited fordítás); a
        # "-társ" (társigénylő) mezők participant.co_borrower.* célra mutatnak
        # — a régi "Contact.X-társ" suffix-forma megszűnt.
        OTP_EXACT_MAP = {
            # === SZA_IG_ – Személyi adatok (Igénylő) ===
            "SZA_IG_név": "participant.borrower.name",
            "SZA_IG_szül_név": "participant.borrower.birth_name",
            "SZA_IG_anyja_neve": "participant.borrower.mother_name",
            "SZA_IG_szül_hely": "participant.borrower.birth_place",
            # C8: az év/hónap/nap dobozok a participant.borrower.birth_date
            # (teljes dátum) kanonikushoz tartoznak, fragment=year|month|day
            # darabolja fel.
            "SZA_IG_szül_év": "participant.borrower.birth_date",
            "SZA_IG_szül_hónap": "participant.borrower.birth_date",
            "SZA_IG_szül_nap": "participant.borrower.birth_date",
            "SZA_IG_személyiszám": "participant.borrower.personal_number",
            "SZA_IG_személyazonosíó_szám": "participant.borrower.personal_id",
            "SZA_IG_adóazonosító": "participant.borrower.tax_id",
            "SZA_IG_állandó_lakcím_irányító": "participant.borrower.address.zip_code",
            "SZA_IG_állandó_lakcim_település": "participant.borrower.address.city",
            "SZA_IG_állandó_lakcim_utca": "participant.borrower.address.street",
            "SZA_IG_levelezési_cím_irányító": "participant.borrower.mailing_address.zip_code",
            "SZA_IG_levelezési_cím_település": "participant.borrower.mailing_address.city",
            "SZA_IG_levelezési_lakcim_utca": "participant.borrower.mailing_address.street",
            "SZA_IG_mobil": "participant.borrower.mobile_phone",
            "SZA_IG_email_cím": "participant.borrower.email",
            "SZA_IG_eltartottak": "participant.borrower.children_count",
            "SZA_IG_kata": "participant.borrower.self_employment_details",
            "SZA_IG_létszám": "participant.borrower.household_size",
            "SZA_IG_mióta_lakik_év": "participant.borrower.residence_notification_date",
            "SZA_IG_mióta_lakik_hónap": "participant.borrower.residence_notification_date",
            "SZA_IG_Elsődleges_Szerep": "participant.borrower.relation",
            "SZA_IG_neme": "participant.borrower.salutation",
            "SZA_IG_állampolgárság": "participant.borrower.citizenship",
            "SZA_IG_családiállapot": "participant.borrower.marital_status",
            "SZA_IG_dokumentum": "participant.borrower.personal_id",
            "SZA_IG_végzettség": "participant.borrower.education",
            "SZA_IG_foglalkoztatás": "participant.borrower.foglalkozas_tipusa",
            "SZA_IG_lakcim_ország": "participant.borrower.mailing_country",
            "SZA_IG_levelezési_cím": "participant.borrower.mailing_address.street",
            "SZA_IG_nav": "participant.borrower.description",
            # === SZA_IG_ – Társigénylő (-társ suffix) → co_borrower névtér ===
            "SZA_IG_név-társ": "participant.co_borrower.name",
            "SZA_IG_szül_név-társ": "participant.co_borrower.birth_name",
            "SZA_IG_anyja_neve-társ": "participant.co_borrower.mother_name",
            "SZA_IG_szül_hely-társ": "participant.co_borrower.birth_place",
            "SZA_IG_szül_év-társ": "participant.co_borrower.birth_date",
            "SZA_IG_szül_hónap-társ": "participant.co_borrower.birth_date",
            "SZA_IG_szül_nap-társ": "participant.co_borrower.birth_date",
            "SZA_IG_személyiszám-társ": "participant.co_borrower.personal_number",
            "SZA_IG_személyazonosíó_szám-társ": "participant.co_borrower.personal_id",
            "SZA_IG_adóazonosító-társ": "participant.co_borrower.tax_id",
            "SZA_IG_állandó_lakcím_irányító-társ": "participant.co_borrower.address.zip_code",
            "SZA_IG_állandó_lakcim_település-társ": "participant.co_borrower.mailing_address.city",
            "SZA_IG_állandó_lakcim_utca-társ": "participant.co_borrower.mailing_address.street",
            "SZA_IG_mobil-társ": "participant.co_borrower.phone",
            "SZA_IG_email_cím-társ": "participant.co_borrower.email",
            "SZA_IG_Elsődleges_Szerep-társ": "participant.co_borrower.relation",
            "SZA_IG_neme-társ": "participant.co_borrower.salutation",
            "SZA_IG_állampolgárság-társ": "participant.co_borrower.citizenship",
            "SZA_IG_családiállapot-társ": "participant.co_borrower.marital_status",
            "SZA_IG_dokumentum-társ": "participant.co_borrower.personal_id",
            "SZA_IG_végzettség-társ": "participant.co_borrower.education",
            "SZA_IG_foglalkoztatás-társ": "participant.co_borrower.foglalkozas_tipusa",
            "SZA_IG_eltartottak-társ": "participant.co_borrower.children_count",
            # === IA_ – Ingatlan adatok (property névtér) ===
            "IA_cím_isz": "property.address.zip_code",
            "IA_cím_település": "property.address.city",
            "IA_cím_utca": "property.address.street",
            "IA_helyrazi_szam": "property.parcel_number",
            "IA_hasznos_terület": "property.area_sqm",
            "IA_ingatlanhasználat": "property.ingatlan_szerepe",
            "IA_Kapcsolattartó neve": "participant.borrower.name",
            "IA_Telefonszám": "participant.borrower.other_phone",
            # === LAHI/KTKA – Lakáshitel/Költségkalkuláció (deal névtér) ===
            "LAHI_KTKA_Igényelt_kölcsön": "deal.loan_amount",
            "LAHI_KTKA_Vásárolt_ingatlan_ár": "property.purchase_price",
            "LAHI_KTKA_Önerő_kp": "deal.down_payment",
            "LAHI_KTKA_CSOK": "deal.csok_amount",
            "LAHI_KTKA_Afa": "deal.afa_support",
            "LAHI_KTKA_Lakástakarék": "deal.housing_savings",
            "LAHI_KTKA_Kiváltandó_kölcsön_szlaszám": "deal.refinance_account",
            "LAHI_KTKA_Kölcsön_tipusa_OTP": "deal.product_type",
            # === MA_ – Munkáltatói/meghatalmazás ===
            "MA_IG_adós_neve": "participant.borrower.name",
            "MA_IG_munáltató_neve": "participant.borrower.employer",
            "MA_IG_munkavállaló_neve": "participant.borrower.name",
            "MA_IG_vállalkozás_neve": "participant.borrower.employer",
            "MA_IG_munáltató_adósz": "participant.borrower.employer_tax_number",
            "MA_IG_vállalkozás_adószám": "participant.borrower.employer_tax_number",
        }

        # C8 (date fragmentation ground-work): these OTP PDF fields each carry
        # only a PIECE of a single logical date (birth date / residence-since),
        # which the fill engine must later reassemble. Keys mirror OTP_EXACT_MAP
        # field names; values are the fragment role: "year" | "month" | "day".
        OTP_DATE_FRAGMENTS = {
            # Születési dátum (adós)
            "SZA_IG_szül_év": "year",
            "SZA_IG_szül_hónap": "month",
            "SZA_IG_szül_nap": "day",
            # Születési dátum (társigénylő)
            "SZA_IG_szül_év-társ": "year",
            "SZA_IG_szül_hónap-társ": "month",
            "SZA_IG_szül_nap-társ": "day",
            # Lakóhely jogcíme – mióta lakik ott (szintén szétdarabolt dátum)
            "SZA_IG_mióta_lakik_év": "year",
            "SZA_IG_mióta_lakik_hónap": "month",
        }

        fields = []
        # OTP exact map előzetesen normalizálva (2b): az ékezetes/regiszter-
        # érzékeny kulcsokat (`név`, `állandó_lakcím`) az `_normalize_key`
        # helper-rel ékezet- és kisbetű-mentes formára hozzuk, így a PDF-ből
        # érkező tetszőleges kódolású/kisbetűs mezőnév is megtalálja a map-ot.
        otp_exact_map_normalized = {
            _normalize_key(k): v for k, v in OTP_EXACT_MAP.items()
        }
        # C8: same normalization for the date-fragment lookup so an
        # accent/case-variant PDF field name still finds its fragment role.
        otp_date_fragments_normalized = {
            _normalize_key(k): v for k, v in OTP_DATE_FRAGMENTS.items()
        }
        for pdf_field in pdf_fields:
            field_name = pdf_field["name"]
            canonical = None
            confidence = MappingConfidence.LOW
            fragment = None

            # 1. Próbáljuk az OTP exact map-ből – normalizált kulccsal.
            normalized = _normalize_key(field_name)
            if normalized in otp_exact_map_normalized:
                canonical = otp_exact_map_normalized[normalized]
                confidence = MappingConfidence.HIGH
                # C8: ha ez egy szétdarabolt dátummező (év/hónap/nap), jegyezzük fel.
                fragment = otp_date_fragments_normalized.get(normalized)
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
                fragment=fragment,
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
        # Cutover Task 4: Genie canonical útvonalak (audited fordítás).
        field_labels_hu = [
            ("Név", "participant.borrower.name", FieldType.TEXT),
            ("Születési név", "participant.borrower.birth_name", FieldType.TEXT),
            ("Anyja neve", "participant.borrower.mother_name", FieldType.TEXT),
            ("Születési hely", "participant.borrower.birth_place", FieldType.TEXT),
            ("Születési dátum", "participant.borrower.birth_date", FieldType.DATE),
            ("Személyi szám", "participant.borrower.personal_number", FieldType.TEXT),
            ("Adóazonosító", "participant.borrower.tax_id", FieldType.TEXT),
            ("Lakcím", "participant.borrower.permanent_address", FieldType.TEXT),
            ("Irányítószám", "participant.borrower.address.zip_code", FieldType.TEXT),
            ("Település", "participant.borrower.mailing_address.city", FieldType.TEXT),
            ("Telefonszám", "participant.borrower.phone", FieldType.TEXT),
            ("E-mail", "participant.borrower.email", FieldType.TEXT),
            ("Hitelösszeg", "deal.loan_amount", FieldType.NUMBER),
            ("Futamidő", "deal.loan_term_years", FieldType.NUMBER),
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
        help="Anthropic API kulcs (default: ANTHROPIC_API_KEY env)",
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

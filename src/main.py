"""
FinancialGenie – Fő pipeline (end-to-end)

Összefogja a teljes folyamatot:
1. Trigger (Salesforce stage-váltás vagy manuális indítás)
2. Adatlekérés (Salesforce / mock)
3. Normalizálás (kanonikus modellbe)
4. Bankspecifikus leképezés (mapping konfiguráció alapján)
5. Szerep- és példányszám-logika
6. Determinisztikus kitöltés (AcroForm / overlay)
7. Teljességellenőrzés
8. Visszaírás (Salesforce / lokális mentés)

Használat:
    # Teljes pipeline futtatása dummy adatokkal
    python -m src.main

    # Adott forgatókönyv
    python -m src.main --scenario complex

    # AI mezőfelismerés + kitöltés
    python -m src.main --recognize samples/acroform_sample.pdf --scenario simple
"""

import json
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

# Projekt gyökér
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models.canonical_model import DealData, ParticipantRole
from src.canonical.paths import parse_path
from src.canonical.registry import canonical_registry
from src.canonical.resolver import CanonicalResolver, ResolutionContext
from src.normalizer.data_normalizer import DataNormalizer
from src.integrations.salesforce_client import SalesforceClient
from src.engine.document_assembler import (
    DocumentAssembler, ProductType,
    INSTANCE_ROLE_CO_BORROWER, INSTANCE_ROLE_GUARANTOR, INSTANCE_ROLE_BENEFICIARY,
    INSTANCE_ROLE_MORTGAGOR, INSTANCE_ROLE_PROPERTY, INSTANCE_ROLE_TO_CANONICAL,
)
from src.engine.pdf_filler import AcroFormFiller, OverlayFiller, TextPlacement, fmt_money, expand_character_groups
from src.engine.completeness_checker import (
    CompletenessChecker,
    CompletenessStatus,
    FieldIssue,
    IssueType,
)
from src.engine.role_instance_logic import RoleInstancePlanner, ParticipantRole as _RRole
from src.ai.field_recognizer import (
    FieldRecognizer,
    MappingConfig,
    print_mapping_summary,
    _normalize_key,
)
from src.ai.legal_classifier import (
    LegalClassifier,
    explicit_crm_consent_evidence,
    extract_unmapped_checkboxes,
    fill_legal_declarations_on_pdf,
    points_managed_fields,
)

logger = logging.getLogger(__name__)


# === Cutover Task 2/3: resolver-backed (Genie canonical) pipeline ===
# A mapping is Genie-style when at least one mapped canonical value is a
# registry-approved canonical path (the shape scripts.migrate_mappings_to_genie
# produces and applies to every active src/mapping/*.json). Cutover Task 3
# deleted the temporary legacy compatibility branch: legacy SF-name mappings
# are rejected (see _mapping_has_legacy_canonical) instead of silently
# resolved.
_CANONICAL_ROLE_ENUM = {
    "borrower": ParticipantRole.BORROWER,
    "co_borrower": ParticipantRole.CO_BORROWER,
    "guarantor": ParticipantRole.GUARANTOR,
    "mortgagor": ParticipantRole.MORTGAGOR,
    "usufructuary": ParticipantRole.BENEFICIARY,
}

# Cutover Task 3 (additional requirement 3) — explicit Genie kötelező útvonalak
# a teljességellenőrzéshez. A régi hardcoded ``Contact.*`` lista helyett minden
# kötelező mező registry-approved canonical path; a hiányzó érték
# diagnosztika/notice, sosem gyártott érték. Az igénylői személyes mezők az
# ÖSSZES adósra/adóstársra vonatkoznak (a létező szereplőkre), a hitelmezők
# ügylet-szintűek.
# Cutover Task 4 (additional requirement 4): a tuple maga is VALIDÁLVA van —
# import-időben (startup) és így minden tesztben is hangosan elbukik, ha egy
# kötelező útvonal nincs a registry-ben (elírás/törölt mező), ahelyett hogy a
# teljességellenőrzés csendben átugraná.
_REQUIRED_CANONICAL_PATHS: tuple[str, ...] = (
    "deal.loan_amount",
    "deal.loan_term_years",
    "participant.borrower.name",
    "participant.borrower.birth_name",
    "participant.borrower.mother_name",
    "participant.borrower.birth_date",
    "participant.borrower.birth_place",
    "participant.borrower.tax_id",
    "participant.borrower.phone",
    "participant.co_borrower.name",
    "participant.co_borrower.birth_date",
    "participant.co_borrower.tax_id",
)


def validate_required_canonical_paths(
    paths: tuple[str, ...] = _REQUIRED_CANONICAL_PATHS,
) -> None:
    """Fail loudly when a required completeness path is not in the registry.

    Additional requirement 4: an unknown required path is a configuration
    defect — it must abort startup (module import) instead of silently
    skipping the completeness check at runtime.
    """
    unknown = [path for path in paths if not canonical_registry.validate(path)]
    if unknown:
        raise ValueError(
            "A _REQUIRED_CANONICAL_PATHS lista ismeretlen canonical útvonalakat "
            "tartalmaz (nincsenek a Genie registry-ben): "
            + ", ".join(repr(path) for path in unknown)
        )


validate_required_canonical_paths()


class LegacyCanonicalMappingError(ValueError):
    """A mapping still carries legacy (Salesforce-named) canonical values.

    Cutover Task 3 rejects such mappings in the activated pipeline; Cutover
    Task 4 (additional requirement 6) gives the rejection a dedicated type so
    the API layer can translate it into an operator-facing 422 instead of a
    generic 500.
    """

# Canonical equivalents of the legacy "Egyéb: ____" picklist canonicals: when a
# standard (non-"Egyéb") option matched in a checkbox group, the companion
# free-text field mapped to the same participant attribute stays empty.
_EGYEB_PARTICIPANT_FIELDS = frozenset({
    "citizenship",
    "employer_company_type",
    "foglalkozas_tipusa",
    "legal_title",
    "marital_status",
    "education",
})


def _genie_mapping_entries(mapping) -> list[dict]:
    """Normalize mapping fields (dicts or RecognizedField) into plain dicts."""

    fields = (
        mapping.get("fields", [])
        if isinstance(mapping, dict)
        else getattr(mapping, "fields", [])
    )
    entries: list[dict] = []
    for f in fields:
        if isinstance(f, dict):
            f_type = f.get("field_type") or "text"
            entries.append({
                "pdf_field_name": f.get("pdf_field_name") or "",
                "label": f.get("label") or "",
                "field_type": str(getattr(f_type, "value", f_type) or "text"),
                "canonical_field": f.get("canonical_field"),
                "fragment": f.get("fragment"),
                "checkbox_group": f.get("checkbox_group"),
                "fill_rule": f.get("fill_rule"),
                "product_gate": f.get("product_gate"),
                "page_number": int(f.get("page_number") or 1),
            })
        else:
            f_type_raw = getattr(f, "field_type", "text")
            entries.append({
                "pdf_field_name": getattr(f, "pdf_field_name", "") or "",
                "label": getattr(f, "label", "") or "",
                "field_type": str(getattr(f_type_raw, "value", f_type_raw) or "text"),
                "canonical_field": getattr(f, "canonical_field", None),
                "fragment": getattr(f, "fragment", None),
                "checkbox_group": getattr(f, "checkbox_group", None),
                "fill_rule": getattr(f, "fill_rule", None),
                "product_gate": getattr(f, "product_gate", None),
                "page_number": int(getattr(f, "page_number", 1) or 1),
            })
    return entries


def _mapping_uses_canonical_paths(mapping) -> bool:
    """True when any mapped canonical value is a registered canonical path."""

    def _check(canonical) -> bool:
        return isinstance(canonical, str) and canonical_registry.validate(canonical)

    for entry in _genie_mapping_entries(mapping):
        if _check(entry["canonical_field"]):
            return True
    groups = (
        mapping.get("character_groups", [])
        if isinstance(mapping, dict)
        else (getattr(mapping, "character_groups", None) or [])
    )
    for group in groups:
        if isinstance(group, dict) and _check(group.get("canonical_field")):
            return True
    return False


def _mapping_has_legacy_canonical(mapping) -> bool:
    """True when any canonical value is NOT a registry-approved path.

    Cutover Task 3: a mapping that still carries ``Contact.*``/``Lead.*``/
    ``Opportunity.*``/``Account.*``/``loan.*`` names, ``-társ`` suffixes or
    ``#…`` canonical suffixes (even mixed with valid paths) is NOT silently
    resolved — the pipeline raises. A mapping with no mapped canonical at
    all (all null) is not legacy; it simply fills nothing through the
    canonical branch.
    """

    def _check(canonical) -> bool:
        return isinstance(canonical, str) and bool(
            canonical
        ) and not canonical_registry.validate(canonical)

    for entry in _genie_mapping_entries(mapping):
        if _check(entry["canonical_field"]):
            return True
    groups = (
        mapping.get("character_groups", [])
        if isinstance(mapping, dict)
        else (getattr(mapping, "character_groups", None) or [])
    )
    for group in groups:
        if isinstance(group, dict) and _check(group.get("canonical_field")):
            return True
    return False


def _canonical_field_matches(canonical: str, namespace: str, field: str) -> bool:
    """Parse-based canonical path match (handles explicit indexes)."""

    try:
        parsed = parse_path(canonical)
    except ValueError:
        return False
    return parsed.namespace == namespace and parsed.field == field


def _resolve_canonical_text(
    resolver: CanonicalResolver,
    deal: DealData,
    path: str,
    context: ResolutionContext | None,
    fragment: str | None = None,
) -> str:
    """Canonical path feloldása per-applicant kiválasztási szemantikával.

    Cutover Task 2 review F2 — ha a kontextus kiválaszt egy résztvevőt
    („adott igénylő példánydokumentuma"), akkor minden ALAP participant path
    (explicit index nélküli, tetszőleges deklarált szereppel) a KIVÁLASZTOTT
    résztvevő adatait kapja — a legacy példánymásolat-viselkedés reprodukálása
    migrált mappingekre. Az explicit indexelt path-ok fixek maradnak
    (resolver garancia), a tartományon kívüli kiválasztás pedig üresen
    hagyja a behelyettesített path-okat (nincs adós-fallback).
    """

    selected = context.participant if context is not None else None
    if selected is not None:
        try:
            parsed = parse_path(path)
        except ValueError:
            pass
        else:
            if parsed.namespace == "participant" and parsed.participant_index is None:
                role, index = selected
                rest = path.split(".", 2)[2]
                path = f"participant.{role}[{index}].{rest}"
    return resolver.resolve_text(deal, path, context, fragment=fragment)


# A gate-kulcsszavak review-olt explicit metadata-táblája: lásd
# ``_PRODUCT_GATE_KEYWORDS`` (Cutover Task 4, additional requirement 5). A
# korábbi ``_PRODUCT_GATE_SUPPORT_KEYWORDS`` substring-lista törölve — a
# besorolás whole-token illesztéssel dolgozik, és a jelen-lévő-de-nem-illeszkedő
# explicit termékértékekre warning szól.


# Cutover Task 4 (additional requirement 5) — REVIEW-OLT EXPLICIT KULCSSZÓ-
# METADATA a product-gate besoroláshoz. Minden kulcsszó ékezet-mentesített
# (folded) forma, szóhatáron illesztve: a "csok" NEM tüzel "csokkal"-ra, a
# "piaci" NEM tüzel "piacivezer"-re. Új terméknév csak ebben a táblában
# engedélyezhető (explicit review-val), a pipeline sosem találgat.
_PRODUCT_GATE_KEYWORDS: dict[str, str] = {
    "piaci": "piaci",
    # Kamattámogatott jellegű termékek (CSÖK, Babaváró, Otthonteremtő, …).
    "csok": "tamogatott",
    "csok plusz": "tamogatott",
    "falusi csok": "tamogatott",
    "babavaro": "tamogatott",
    "kamattamogatott": "tamogatott",
    "kamattamogatas": "tamogatott",
    "otthon start": "tamogatott",
    "otthon_start": "tamogatott",
    "otthonteremto": "tamogatott",
    "otthonfelujito": "tamogatott",
    "osp": "tamogatott",
}
_PRODUCT_GATE_KEYWORD_RES = {
    keyword: re.compile(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])")
    for keyword in _PRODUCT_GATE_KEYWORDS
}


def _active_product_gates(
    deal: DealData, notices: list | None = None
) -> frozenset[str]:
    """Canonical termék-gate-ek kizárólag explicit canonical deal mezőkből.

    Forrásmezők (minden más ignorált): ``deal.products`` (modell attribútum),
    ``deal.product_type`` és ``deal.product_name`` (registry-approved
    canonical paths, a resolveren keresztül). A besorolás a review-olt
    ``_PRODUCT_GATE_KEYWORDS`` metadata szerint WHOLE-TOKEN illesztéssel
    történik (nincs substring túlbekapás). Ha mindhárom forrás hiányzik, a
    gate-elt bejegyzések üresen maradnak egy explicit warninggal; ha az
    explicit értékek jelen vannak, de egyik kulcsszó sem illeszkedik, az
    szintén warning — a pipeline sosem talál ki terméket.
    """

    products = [str(p) for p in (getattr(deal, "products", []) or [])]
    resolver = CanonicalResolver()
    product_type = resolver.resolve(deal, "deal.product_type") or ""
    product_name = resolver.resolve(deal, "deal.product_name") or ""
    explicit_values = [p for p in products if p.strip()] + [
        str(product_type).strip(),
        str(product_name).strip(),
    ]
    explicit_values = [v for v in explicit_values if v]

    if not explicit_values:
        msg = (
            "Az ügylet termékjellegét leíró explicit canonical mezők "
            "(deal.products, deal.product_type, deal.product_name) mind "
            "hiányoznak — a product_gate-elt szekciók üresen maradnak, "
            "a pipeline nem találgat."
        )
        logger.warning("   ⚠️ %s", msg)
        if notices is not None and msg not in notices:
            notices.append(msg)
        return frozenset()

    gates: set[str] = set()
    matched_values: set[str] = set()
    for value in explicit_values:
        folded = _fold_text(value)
        for keyword, gate in _PRODUCT_GATE_KEYWORDS.items():
            if _PRODUCT_GATE_KEYWORD_RES[keyword].search(folded):
                gates.add(gate)
                matched_values.add(value)

    unmatched = [value for value in explicit_values if value not in matched_values]
    if unmatched:
        msg = (
            "Explicit termékjelleg-adat(ok) jelen vannak, de egyik sem "
            "illeszkedik a review-olt product-gate kulcsszó-metadatára "
            "(_PRODUCT_GATE_KEYWORDS): "
            + ", ".join(repr(v) for v in unmatched)
            + " — a rájuk vonatkozó product_gate-elt szekciók üresen "
            "maradnak. Új terméknév only explicit metadata-bővítéssel "
            "engedélyezett."
        )
        logger.warning("   ⚠️ %s", msg)
        if notices is not None and msg not in notices:
            notices.append(msg)

    return frozenset(gates)

def _applicant_participant_count(deal: DealData) -> int:
    """Applicants (adós + adóstárs) that drive személyi adatlap duplication.

    Guarantors, mortgagors and beneficiaries are non-applicant participants:
    they must never generate extra társigénylő (co-borrower) page copies in the
    document assembly plan.
    """
    return len(deal.borrowers) + len(deal.co_borrowers)


def _collateral_participant_instances(deal: DealData) -> list[tuple[str, int]]:
    """Actual SZA ESZ participants as canonical ``(role, 1-based index)`` pairs."""
    instances: list[tuple[str, int]] = []
    for canonical_role, role_enum in (
        ("mortgagor", ParticipantRole.MORTGAGOR),
        ("usufructuary", ParticipantRole.BENEFICIARY),
    ):
        participants = deal.get_participants_by_role(role_enum)
        instances.extend(
            (canonical_role, index)
            for index in range(1, len(participants) + 1)
        )
    return instances


def _fold_text(s: str) -> str:
    """Ékezet-mentes, kisbetűs, trimmelt szöveg (egyeztetéshez)."""
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").strip().lower())
        if unicodedata.category(c) != "Mn"
    )


def _apply_fragment(value, fragment):
    """Extract a single fragment (year/month/day/digit:N) from a value.

    FIX C8 — date / digit box fragmentation.

    Some PDF form fields carry only a PIECE of a larger logical value:
      - ``fragment="year"|"month"|"day"``  → one box of a split date
        (canonical value e.g. ``"1985.05.12"`` → ``"1985"`` / ``"05"`` / ``"12"``)
      - ``fragment="digit:N"``             → the Nth character (1-based) of a
        split numeric value (e.g. postal code ``"1123"`` → ``digit:1`` = ``"1"``)

    The canonical value may arrive in several formats: ``"1985.05.12"`` (HU),
    ``"1985-05-12"`` (ISO), ``1985`` (year only). All are normalized.

    This function never raises: on any parse problem it returns ``""`` so the
    box is simply left empty (matching the spec's "leave the rest empty").

    Args:
        value: The full logical value (string or scalar).
        fragment: The fragment role: ``None`` | ``"year"`` | ``"month"`` |
            ``"day"`` | ``"digit:N"`` (N is 1-based).

    Returns:
        The extracted fragment as a string, or the original value when no
        fragment is requested, or ``""`` when the fragment cannot be extracted.
    """
    if not fragment:
        return value
    if value is None:
        return ""
    s = str(value).strip()
    if s == "":
        return ""
    frag = str(fragment).strip().lower()

    if frag in ("year", "month", "day"):
        parts = re.split(r"[.\-/]", s)
        # Year-only value (e.g. "1985" / 1985): fills the year box, leaves
        # month/day empty. A single all-numeric token is treated as a year.
        if len(parts) == 1 and parts[0].isdigit():
            return parts[0] if frag == "year" else ""
        if len(parts) >= 3:
            y, m, d = parts[0], parts[1], parts[2]
            if frag == "year":
                return y if (y and y.isdigit()) else ""
            if frag == "month":
                return m if (m and m.isdigit()) else ""
            return d if (d and d.isdigit()) else ""
        # Wrong arity (e.g. "1985.03"): cannot reliably split → leave empty.
        return ""

    if frag.startswith("digit:"):
        n_str = frag[len("digit:"):]
        try:
            n = int(n_str)  # 1-based character position per spec
        except ValueError:
            return ""
        idx = n - 1
        if 0 <= idx < len(s):
            return s[idx]
        # Index out of range (e.g. postal code shorter than expected).
        return ""

    # Unknown fragment kind: return value unchanged (forward-compatible).
    return value


def _infer_date_fragment(pdf_field_name: str, label: str = "") -> str | None:
    """Automatikusan kikövetkezteti a dátum-komponenst ('year', 'month', 'day')
    a PDF mezőnévből vagy feliratból, ha a mappingben a fragment=None maradt
    (pl. 'Kelt_ing_adatlap_év', 'CSOK_31hó-1', 'Kelt – nap').
    """
    for raw in (pdf_field_name or "", label or ""):
        if not raw:
            continue
        folded = _fold_text(raw)
        # Split on non-alpha AND digit-to-alpha boundaries (e.g. "31ev" -> "31", "ev")
        tokens = re.findall(r"[a-z]+", folded)
        if not tokens:
            continue
        # Only infer if the component token is near the end or preceded by date/kelt/szul context
        last_tok = tokens[-1]
        if last_tok in ("honap", "ho", "month"):
            return "month"
        if last_tok in ("nap", "day") and "napos" not in tokens:
            return "day"
        if last_tok in ("ev", "year") and "eves" not in tokens:
            return "year"
        # Check non-final tokens if explicitly separated (e.g. "kelt_ev_1")
        if any(t in ("kelt", "datum", "szul", "szuletesi", "date", "csok", "szerzodes") for t in tokens):
            if any(t in ("honap", "ho", "month") for t in tokens):
                return "month"
            if any(t in ("nap", "day") for t in tokens) and "napos" not in tokens:
                return "day"
            if any(t in ("ev", "year") for t in tokens) and "eves" not in tokens:
                return "year"
    return None


# Cutover Task 3: a régi legacy-kulcsos ``_SINGLE_VALUE_CODE_CANONICALS``
# készlet (Lead.PostalCode, Contact.Tax_ID__c, …) canonical-path predikátummá
# portolva — a runtime karaktercsoport-becslés CANONICAL mezőjelleg alapján
# kap kaput, nem Salesforce-nevekből. (A születési év/hónap/nap dobozok a
# migrált ``fragment`` metaadatból töltődnek, nem karaktercsoportból.)
_SINGLE_VALUE_CODE_FIELDS = frozenset({
    "tax_id", "personal_id", "id_card_number",
})
_SINGLE_VALUE_CODE_POSTAL_FIELDS = frozenset({
    "zip_code", "billing_postal_code", "shipping_postal_code",
})


def _is_single_value_code_path(canonical: str) -> bool:
    """True, ha a canonical path egy egyszeri-érték kódmező (irányítószám,
    adóazonosító, szem./lakcímkártya szám) — runtime char-group jelölt."""

    try:
        parsed = parse_path(canonical)
    except ValueError:
        return False
    if parsed.namespace == "participant" and parsed.field in (
        _SINGLE_VALUE_CODE_FIELDS
    ):
        return True
    return parsed.field in _SINGLE_VALUE_CODE_POSTAL_FIELDS or (
        parsed.field.startswith("address.")
        and parsed.field.split(".", 1)[1] in _SINGLE_VALUE_CODE_POSTAL_FIELDS
    )


def _infer_runtime_character_groups(mapping) -> list[dict]:
    """Automatikusan felismeri a karakterdoboz-csoportokat (pl. irányítószám
    4 külön mezőben: postalCodePart0..3 vagy '(1. jegy)'..'(4. jegy)') akkor is,
    ha a mapping JSON-ban a character_groups üres maradt vagy a felhasználó
    utólag mappelte a mezőket.
    """
    existing = list(
        (mapping.get("character_groups") if isinstance(mapping, dict) else getattr(mapping, "character_groups", None))
        or []
    )
    already_grouped: set[str] = set()
    for grp in existing:
        members = grp.get("member_fields", []) if isinstance(grp, dict) else getattr(grp, "member_fields", [])
        if members:
            already_grouped.update(members)

    fields = mapping.get("fields", []) if isinstance(mapping, dict) else getattr(mapping, "fields", [])
    buckets: dict[tuple[int, str], list[tuple[str, str]]] = {}
    for f in fields:
        if isinstance(f, dict):
            pdf_name = f.get("pdf_field_name") or ""
            canon = f.get("canonical_field") or ""
            ftype = f.get("field_type") or "text"
            label = f.get("label") or ""
            page = int(f.get("page_number") or 1)
            cbg = f.get("checkbox_group")
            fr = f.get("fill_rule")
        else:
            pdf_name = getattr(f, "pdf_field_name", "") or ""
            canon = getattr(f, "canonical_field", "") or ""
            ftype_raw = getattr(f, "field_type", "text")
            ftype = getattr(ftype_raw, "value", str(ftype_raw or "text"))
            label = getattr(f, "label", "") or ""
            page = int(getattr(f, "page_number", 1) or 1)
            cbg = getattr(f, "checkbox_group", None)
            fr = getattr(f, "fill_rule", None)

        if (
            not pdf_name
            or not canon
            or pdf_name in already_grouped
            or ftype in ("checkbox", "dropdown")
            or "___" in pdf_name
            or cbg
            or fr
        ):
            continue
        buckets.setdefault((page, canon), []).append((pdf_name, label))

    inferred = list(existing)
    for (page, canon), items in buckets.items():
        if len(items) < 2:
            continue
        # 1. eset: a feliratokban "(1. jegy)", "(2. jegy)" / "karakter" / "számjegy" szerepel
        jegy_matches = []
        for pdf_name, label in items:
            m = re.search(r"\(\s*(\d+)\.\s*(?:jegy|karakter|sz[aá]mjegy)\s*\)", label, flags=re.IGNORECASE)
            if m:
                jegy_matches.append((int(m.group(1)), pdf_name))
        if len(jegy_matches) >= 2 and len(jegy_matches) == len(items):
            jegy_matches.sort(key=lambda x: x[0])
            members = [ name for _, name in jegy_matches ]
            slug = re.sub(r"[^a-z0-9]+", "_", canon.lower()).strip("_")
            inferred.append({
                "group_id": f"auto_chargroup_{slug}_p{page}_{len(inferred)}",
                "canonical_field": canon,
                "member_fields": members,
                "direction": "left_to_right",
                "separator": "",
            })
            already_grouped.update(members)
            continue

        # 2. eset: szekvenciális mezőnevek (pl. postalCodePart0..3, irsz_1..4)
        seq_matches = []
        prefixes = set()
        for pdf_name, _ in items:
            m = re.match(r"^(.*?)(?:part|_|\[|\.)?(\d+)\]?$", pdf_name, flags=re.IGNORECASE)
            if m:
                prefixes.add(m.group(1).lower())
                seq_matches.append((int(m.group(2)), pdf_name))
        if len(seq_matches) == len(items) and len(prefixes) == 1:
            pref = next(iter(prefixes))
            has_box_kw = any(kw in pref for kw in ("postalcode", "irsz", "zip", "jegy", "digit", "char", "adoazonosito", "tax"))
            if has_box_kw or _is_single_value_code_path(canon):
                seq_matches.sort(key=lambda x: x[0])
                idxs = [i for i, _ in seq_matches]
                # Check consecutive indices (e.g. 0,1,2,3 or 1,2,3,4)
                if idxs == list(range(idxs[0], idxs[0] + len(idxs))):
                    members = [name for _, name in seq_matches]
                    slug = re.sub(r"[^a-z0-9]+", "_", canon.lower()).strip("_")
                    inferred.append({
                        "group_id": f"auto_chargroup_{slug}_p{page}_{len(inferred)}",
                        "canonical_field": canon,
                        "member_fields": members,
                        "direction": "left_to_right",
                        "separator": "",
                    })
                    already_grouped.update(members)

    return inferred


def _fill_rule_match_values(rule) -> list[str]:
    """Declared canonical values for an untyped match rule, including aliases."""
    if not isinstance(rule, dict) or rule.get("type"):
        return []
    aliases = rule.get("aliases") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    if not isinstance(aliases, (list, tuple)):
        aliases = []
    values = []
    for value in [rule.get("match_value"), *aliases]:
        if isinstance(value, str) and value.strip() and value.strip() not in values:
            values.append(value.strip())
    return values


def _opt_matches(option: str, value: str, option_label: str = "") -> bool:
    """Picklist- és rádiógomb-opció egyezés: pontos (kisbetűs), prefix,
    valamint bankközi szemantikus szinonimák (ingatlantípus,
    hitelcél, szerepkör, boolean igen/nem ↔ true/false). Ékezet- és
    írásjelfüggetlen, az egymást követő whitespace-eket egységesíti."""
    import unicodedata

    def _norm(s: str) -> str:
        folded = "".join(
            c for c in unicodedata.normalize("NFD", str(s or "").strip().lower())
            if unicodedata.category(c) != "Mn"
        )
        return re.sub(r"[^a-z0-9]+", " ", folded).strip()

    opt, val = _norm(option), _norm(value)
    opt_lbl = _norm(option_label) if option_label else ""
    if not opt or not val:
        return False

    # 1. Boolean szinonimák (igen/true/yes/1 vs nem/false/no/0)
    _TRUE_SET = {"igen", "true", "yes", "1"}
    _FALSE_SET = {"nem", "false", "no", "0"}
    if opt in _TRUE_SET and val in _TRUE_SET:
        return True
    if opt in _FALSE_SET and val in _FALSE_SET:
        return True

    # 2. Szerepkör szinonimák
    _ROLE_SYNONYMS = {
        "adostars": "tarsigenylo",
        "tarsigenylo": "adostars",
        "ados": "igenylo",
        "igenylo": "ados",
    }
    if opt == val or _ROLE_SYNONYMS.get(opt) == val:
        return True

    # 3. Okmánytípus minta (6 számjegy + 2 betű -> személyi igazolvány)
    if opt == "szemelyi igazolvany" and re.fullmatch(r"\d{6}[a-z]{2}", val.replace(" ", "")):
        return True

    # 4. Ingatlan típusa / jellege szinonimák (bankfüggetlen: OTP, CIB, K&H, Erste, MBH)
    _RESIDENTIAL_VALS = {
        "lakas", "haz", "lakohaz", "csaladi haz", "tarsashazi lakas",
        "hasznalt lakohaz", "uj lakohaz", "hasznalt lakas", "uj lakas",
        "ikerhaz", "sorhaz", "udulo", "telek", "epitesi telek", "lakoingatlan",
    }
    if opt == "lakoingatlan" and val in _RESIDENTIAL_VALS:
        return True
    if opt.startswith("nem ") and val in _RESIDENTIAL_VALS:
        return False
    # Társasházi lakás vs Lakó-üdülő ingatlan telekkel / Családi ház vs Építési telek
    if opt in ("tarsashazi lakas", "lakas") and val in ("lakas", "tarsashazi lakas", "hasznalt lakas", "uj lakas"):
        return True
    if opt in ("lako udulo ingatlan telekkel", "csaladi haz", "lakohaz", "onallo csaladi haz") and val in (
        "haz", "lakohaz", "csaladi haz", "hasznalt lakohaz", "uj lakohaz", "ikerhaz", "sorhaz", "udulo"
    ):
        return True
    if opt in ("epitesi telek", "telek") and val in ("telek", "epitesi telek"):
        return True

    # 5. Hitelcél szinonimák (pl. SF 'Használt lakóingatlan vásárlás' -> 'Lakásvásárlás' / 'Vásárlás')
    combined_opt = f"{opt} {opt_lbl}".strip()
    if "vasarlas" in val and "vasarlas" in combined_opt and "kivaltas" not in val:
        # Ha az opció kifejezetten csak új vagy csak használt vásárlásra vonatkozik, ellenőrizzük:
        opt_has_uj = bool(re.search(r"\buj\b", combined_opt))
        opt_has_hasznalt = "hasznalt" in combined_opt
        val_has_uj = bool(re.search(r"\buj\b", val))
        val_has_hasznalt = "hasznalt" in val
        if opt_has_uj and not opt_has_hasznalt and val_has_hasznalt and not val_has_uj:
            return False
        if opt_has_hasznalt and not opt_has_uj and val_has_uj and not val_has_hasznalt:
            return False
        return True
    if "epites" in val and "vasarlas" not in val and opt in ("epites", "uj epites", "lakasepites"):
        return True
    if "bovites" in val and "bovites" in combined_opt:
        return True
    if ("korszerusites" in val or "felujitas" in val) and ("korszerusites" in combined_opt or "felujitas" in combined_opt):
        return True

    # 6. Prefix egyezés (az elválasztójelek már normalizált whitespace-ek)
    if len(opt) >= 3 and len(val) >= 3 and not opt.startswith("nem ") and not val.startswith("nem "):
        return opt.startswith(val) or val.startswith(opt)
    return False


def _field_canonical_lookup(mapping) -> dict:
    """PDF-mezőnév → canonical_field térkép a mapping-ből (dict vagy MappingConfig)."""
    fields = mapping.get("fields", []) if isinstance(mapping, dict) else getattr(mapping, "fields", [])
    out = {}
    for f in fields:
        if isinstance(f, dict):
            name = f.get("pdf_field_name")
            canon = f.get("canonical_field")
        else:
            name = getattr(f, "pdf_field_name", None)
            canon = getattr(f, "canonical_field", None)
        if name:
            out[name] = canon
    return out


def _infer_canonical_from_blocks(p_def: dict, mapping) -> str | None:
    """PLAN §5.3 — kikövetkezteti a canonical mezőt, amihez a csoport tartozik.

    A blokkok members PDF-mezőinek canonical_field-jéből (a mapping-ből
    kikeresve). Mindegyik blokk ugyanoda mutat, elég az első member.
    """
    lookup = _field_canonical_lookup(mapping)
    for blk in p_def.get("blocks", []):
        for member in blk.get("members", []):
            canon = lookup.get(member)
            if canon:
                return canon
    return None


def _values_match(block_id, actual_value) -> bool:
    """PLAN §5.3 — kisbetűs, ékezet-normalizált egyezés (felsőfokú ~= Felsofoku)."""
    if actual_value is None:
        return False
    return _normalize_key(str(block_id)) == _normalize_key(str(actual_value))


class FormFillerPipeline:
    """
    A nyomtatványkitöltő fő pipeline-ja.
    
    Összekapcsolja a Salesforce integrációt, normalizálót,
    kitöltőmotort és az AI réteget.
    """

    def __init__(
        self,
        sf_client: SalesforceClient = None,
        normalizer: DataNormalizer = None,
        output_dir: Path = None,
    ):
        self.sf_client = sf_client or SalesforceClient(mock_mode=True)
        self.normalizer = normalizer or DataNormalizer()
        self.output_dir = output_dir or PROJECT_ROOT / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_mapping(self, template_pdf: Path, force_recreate: bool = False) -> MappingConfig:
        """
        Mapping konfiguráció feloldása a sablon PDF-hez.
        Ha létezik a mapping és force_recreate=False, betölti. Ha nem, automatikusan legenerálja.
        """
        try:
            from backend.config import mapping_path_for
        except ImportError:
            def mapping_path_for(pdf_id: str) -> Path:
                stem = Path(pdf_id).stem
                return PROJECT_ROOT / "src" / "mapping" / f"{stem}_mapping.json"

        try:
            pdf_id = str(template_pdf.relative_to(PROJECT_ROOT))
        except ValueError:
            pdf_id = template_pdf.name

        mapping_path = mapping_path_for(pdf_id)
        if mapping_path.exists() and not force_recreate:
            logger.info(f"📋 Meglévő mapping betöltése: {mapping_path.name}")
            return MappingConfig.load(mapping_path)

        if force_recreate:
            logger.info(f"🤖 Mapping kényszerített újragenerálása ({mapping_path.name}) – Automatikus mezőfelismerés indítása...")
        else:
            logger.info(f"🤖 Mapping nem található ({mapping_path.name}) – Automatikus mezőfelismerés indítása...")
        recognizer = FieldRecognizer()
        mapping = recognizer.recognize(template_pdf, mode="auto")

        if force_recreate and mapping_path.exists():
            try:
                old_mapping = MappingConfig.load(mapping_path)
                logger.info(f"🔄 Összefésülés a meglévő {len(old_mapping.fields)} mezővel ({mapping_path.name})...")
                
                old_fields_by_name = {f.pdf_field_name: f for f in old_mapping.fields}
                merged_fields = []
                
                for new_f in mapping.fields:
                    old_f = old_fields_by_name.get(new_f.pdf_field_name)
                    if old_f:
                        # Ha a régi mező már mappelve van (canonical_field vagy fill_rule), megtartjuk az eddigi szabályokat
                        if getattr(old_f, "canonical_field", None) or getattr(old_f, "fill_rule", None):
                            merged_fields.append(old_f)
                        else:
                            merged_fields.append(new_f)
                        del old_fields_by_name[new_f.pdf_field_name]
                    else:
                        merged_fields.append(new_f)
                
                # Megtartjuk azokat a régi mezőket, amik nincsenek a mostani kivonatolt PDF-ben
                for old_f in old_fields_by_name.values():
                    merged_fields.append(old_f)
                    
                mapping.fields = merged_fields
                if old_mapping.character_groups:
                    existing_gids = {
                        (g.get("group_id") if isinstance(g, dict) else getattr(g, "group_id", ""))
                        for g in old_mapping.character_groups
                    }
                    combined_groups = list(old_mapping.character_groups)
                    for ng in (mapping.character_groups or []):
                        ngid = ng.get("group_id") if isinstance(ng, dict) else getattr(ng, "group_id", "")
                        if ngid and ngid not in existing_gids:
                            combined_groups.append(ng)
                    mapping.character_groups = combined_groups
                logger.info(f"   ✓ Eredmény: {len(mapping.fields)} mező (korábbi szabályok megtartva).")
            except Exception as e:
                logger.warning(f"⚠️ Hiba a meglévő mapping összefésülésekor: {e}")

        mapping.save(mapping_path)
        logger.info(f"✅ Automatikus mezőfelismerés sikeres, elmentve: {mapping_path.name}")
        return mapping

    def run_for_deal(
        self,
        deal_id: str,
        template_pdf: Path,
        mapping_config: MappingConfig = None,
        force_recreate_mapping: bool = False,
        participant_override: int | None = None,
        upload_to_sf: bool = False,
        context: ResolutionContext | None = None,
        package_values: dict | None = None,
    ) -> dict:
        """
        Teljes pipeline futtatása egy ügylethez.

        Args:
            deal_id: Salesforce ügylet azonosító
            template_pdf: A kitöltendő PDF sablon
            mapping_config: Mező-leképezés konfiguráció (opcionális)
            force_recreate_mapping: Mapping kényszerített újragenerálása
            participant_override: Ha meg van adva (0, 1, 2), a sima mezők
                az adott indexű adóstárs adatait kapják (co_borrowers[i]).
                None = adós adatai.
            upload_to_sf: Ha True (vagy mock_mode=True), a generált PDF-et
                csatolja a Salesforce Opportunity-hoz és frissíti a stage-et.
            context: Explicit ``(role, index)`` feloldási kontextus
                (Cutover Task 2). A ``participant_override``-ot belsőleg
                erre csomagoljuk; a PDF-mezőnevek sosem választanak
                résztvevőt.
            package_values: Opcionális, PDF-mezőnév szerinti végleges
                csomag-szintű felülírások (például a ténylegesen generált
                dokumentummanifesztből számolt fedlap-darabszámok).

        Returns:
            Eredmény dict: {success, output_path, issues, ...}
        """
        result = {
            "deal_id": deal_id,
            "success": False,
            "output_path": None,
            "issues": [],
            "timestamp": datetime.now().isoformat(),
        }

        # Resolve mapping_config if not provided
        if mapping_config is None:
            try:
                mapping_config = self._resolve_mapping(template_pdf, force_recreate=force_recreate_mapping)
            except Exception as e:
                result["issues"].append(f"Mapping feloldási hiba: {e}")
                logger.error(f"   ✗ Mapping feloldási hiba: {e}")
                return result

        # 1. Adatlekérés
        logger.info(f"📥 1. Adatlekérés: {deal_id}")
        raw_data = self.sf_client.get_deal(deal_id)
        if raw_data is None:
            result["issues"].append(f"Ügylet nem található: {deal_id}")
            return result

        # 2. Normalizálás
        logger.info("🔄 2. Normalizálás")
        try:
            deal = self.normalizer.normalize_deal(raw_data)
        except Exception as e:
            result["issues"].append(f"Normalizálási hiba: {e}")
            return result

        logger.info(f"   Ügylet: {deal.deal_id}")
        logger.info(f"   Aktív szereplők: {len(deal.active_participants)}")
        logger.info(f"   Ingatlanok: {len(deal.properties)}")

        # 3. Teljességellenőrzés (professional CompletenessChecker)
        logger.info("✅ 3. Teljességellenőrzés")
        completeness = self._check_completeness(deal, mapping_config)
        if completeness.status != CompletenessStatus.COMPLETE:
            for issue in completeness.blocking_issues:
                logger.warning(f"   ⚠️ [BLOKKOLÓ] {issue.field_path} – {issue.message}")
                result["issues"].append(f"Hiányzó mező: {issue.field_path}")
            for warn in completeness.warnings:
                logger.warning(f"   ⚠️ {warn.field_path} – {warn.message}")

        # 4. Mezőadatok összeállítása
        logger.info("📋 4. Mezőadatok összeállítása")
        notices: list = []
        # Cutover Task 3 (additional requirement 5): a ``context`` átadás
        # FELTÉTEL NÉLKÜLI — a participant_override-ot a
        # _prepare_field_data csomagolja kontextussá.
        prepare_kwargs = {
            "participant_override": participant_override,
            "raw_opportunity": raw_data.get("_opportunity") or raw_data.get("Opportunity__c") or raw_data.get("opportunity") or raw_data,
            "notices": notices,
            "context": context,
        }
        if package_values is not None:
            prepare_kwargs["package_values"] = package_values
        field_data = self._prepare_field_data(
            deal, mapping_config, **prepare_kwargs
        )
        result["notices"] = notices
        logger.info(f"   {len(field_data)} mező kitöltve")

        # 5. PDF kitöltés
        logger.info("📝 5. PDF kitöltés")
        try:
            assembler = DocumentAssembler()
            actual_template = template_pdf
            instance_fields = None
            # Master PDF detektálás: a DocumentAssembler.is_master_pdf dönti el,
            # nem egy `page_count == 97` magic number (dokumentum-agnosztikus).
            if assembler.is_master_pdf(template_pdf):
                logger.info(
                    "   📂 Master PDF észlelve (≥%d oldal) → Automatikus Document Assembly (darabolás)...",
                    assembler.MASTER_PDF_PAGE_COUNT,
                )
                temp_assembled_path = self.output_dir / f"assembled_{deal.deal_id}.pdf"
                
                products_enum = []
                for p in deal.products:
                    try:
                        products_enum.append(ProductType(p))
                    except ValueError:
                        pass
                if not products_enum:
                    # Cutover Task 3 (additional requirement 4): nincs csendes
                    # termék-alapértelmezés — a PIACI_HITEL assembly-fallback
                    # mellett explicit notice jelzi, hogy az ügylet termékei
                    # nem határozták meg a dokumentum-összeállítást.
                    products_enum = [ProductType.PIACI_HITEL]
                    fallback_note = (
                        "Az ügylet products listája üres vagy ismeretlen "
                        "termékeket tartalmaz — a dokumentum-összeállítás a "
                        "PIACI_HITEL alapértelmezett szekciókkal készült."
                    )
                    logger.warning("   ⚠️ %s", fallback_note)
                    if fallback_note not in notices:
                        notices.append(fallback_note)

                # Cutover Task 3 (Review F1 aktiválás): a személyi
                # adatlap-példányok száma mindig az IGÉNYLŐK száma (adós +
                # adóstárs) — a nem-igénylő szerepek (kezes, zálogkötelezett,
                # haszonélvező) sosem készítenek társigénylő-példányt.
                assembly_participants = _applicant_participant_count(deal)
                assembly = assembler.assemble(
                    master_pdf=template_pdf,
                    products=products_enum,
                    num_participants=assembly_participants,
                    num_properties=len(deal.properties),
                    output_path=temp_assembled_path,
                    collateral_participants=_collateral_participant_instances(deal),
                )
                actual_template = assembly.output_path
                # C6: a darabolás során a duplikált oldalakon átnevezett _N
                # mezők leírása — a kitöltésnek ezekhez a megfelelő (N-edik)
                # példány adatait kell rendelnie.
                instance_fields = assembly.instance_fields

            output_path, filled_fields, skipped_fields = self._fill_pdf(
                actual_template, deal, field_data, mapping_config,
                instance_fields=instance_fields,
                participant_override=participant_override,
            )
            result["output_path"] = str(output_path)
            # FIX M6 — surface the real filled/skipped field names so the API
            # response is no longer a ghost-empty list.
            result["filled_fields"] = filled_fields
            result["skipped_fields"] = skipped_fields
            logger.info(f"   ✓ Kitöltött PDF: {output_path}")

            # 5b. Jogi nyilatkozatok kezelése (safe declaration policy,
            # Cutover Task 5). A canonical mapping MELLÉ jön: a points-managed
            # role/signature mezőket a pont-motor már kitöltötte; a maradék
            # leképezetlen nyilatkozatokra NINNEK automatikus értékek — a
            # feloldatlanokat kézi döntésként listázzuk (validation list).
            try:
                legal_info = self._fill_legal_declarations(
                    output_path, mapping_config, deal
                )
                # Backward compatibility: a lépés korábban int-et adott vissza.
                if isinstance(legal_info, dict):
                    legal_count = legal_info.get("modified", 0)
                    result["manual_declarations"] = legal_info.get("manual", [])
                    if result["manual_declarations"]:
                        logger.info(
                            "   ⚠️ Jogi nyilatkozat: %d kézi döntés listázva "
                            "(manual_declarations).",
                            len(result["manual_declarations"]),
                        )
                else:
                    legal_count = int(legal_info or 0)
                if legal_count:
                    logger.info(
                        "   ✓ Jogi nyilatkozat: %d checkbox módosítva", legal_count
                    )
            except Exception as e:
                result["issues"].append(f"Jogi nyilatkozat hiba: {e}")
                logger.warning(f"   ⚠️ Jogi nyilatkozat hiba: {e}")
        except Exception as e:
            result["issues"].append(f"PDF kitöltési hiba: {e}")
            logger.error(f"   ✗ PDF kitöltési hiba: {e}")
            return result

        # 6. Visszaírás Salesforce-ba (csak explicit kérésre vagy mock módban)
        if upload_to_sf or getattr(self.sf_client, "_mock_mode", False):
            logger.info("📤 6. Visszaírás")
            try:
                filename = f"OTP_nyomtatvany_{deal.deal_id}_{datetime.now():%Y%m%d_%H%M}.pdf"
                self.sf_client.attach_pdf(deal.deal_id, output_path, filename)
                self.sf_client.update_stage(deal.deal_id, "Dokumentáció kész – review")
                logger.info("   ✓ PDF csatolva, stage frissítve")
            except Exception as e:
                result["issues"].append(f"Visszaírási hiba: {e}")
                logger.warning(f"   ⚠️ Visszaírási hiba: {e}")

        result["success"] = True
        return result

    def _check_completeness(self, deal: DealData, mapping: MappingConfig):
        """
        Teljességellenőrzés explicit Genie canonical kötelező utvonalakkal.

        Cutover Task 3 (additional requirement 3): a régi hardcoded
        ``Contact.*`` lista és a raw PDF-névből való következtetés is törölve.
        A kötelező mezők explicit canonical path-ok; az értékeket a
        ``CanonicalResolver`` olvassa (a mapping függetlenül — a hiányzó
        érték mindig diagnosztika/notice, SOSEM gyártott érték). A
        strukturális ellenőrzéseket (van adós, van ingatlan, …) továbbra is a
        professional CompletenessChecker végzi.
        """
        checker = CompletenessChecker(run_suspicious_checks=True)
        # Strukturális ellenőrzések üres required-listával (nincs mező-útvonal
        # átadva — azokat lentebb a canonical resolver ellenőrzi).
        report = checker.check(deal, [])
        resolver = CanonicalResolver()

        for template in _REQUIRED_CANONICAL_PATHS:
            parsed = parse_path(template)
            if parsed.namespace == "participant":
                role_enum = _CANONICAL_ROLE_ENUM[parsed.role]
                same_role = [
                    participant
                    for participant in deal.active_participants
                    if participant.role == role_enum
                ]
                if not same_role:
                    # Review fix (defect 1): a required participant path csak
                    # a LÉTEZŐ (aktív) résztvevőkre vonatkozik. Üres szerep-
                    # lista (nincs adóstárs, vagy csak inaktív van) NEM ad
                    # fantom MISSING diagnosztikát — a szerep saját maga nem
                    # hiányzó mező. (A "van adós?" strukturális ellenőrzést a
                    # CompletenessChecker végzi.)
                    continue
                targets = [
                    (
                        f"participant.{parsed.role}[{index}].{parsed.field}",
                        ResolutionContext(
                            participant=(parsed.role, index)
                        ),
                    )
                    for index in range(1, len(same_role) + 1)
                ]
            else:
                targets = [(template, None)]

            for path, resolution_context in targets:
                report.checked_fields += 1
                try:
                    value = resolver.resolve(deal, path, resolution_context)
                except ValueError as error:
                    # Additional requirement 4: ismeretlen kötelező útvonal =
                    # konfigurációs hiba — HANGOS hiba, nem csendes kihagyás.
                    raise ValueError(
                        "Teljességellenőrzés: ismeretlen canonical kötelező "
                        f"útvonal: {path!r} ({error})"
                    ) from error
                if value is None:
                    report.issues.append(
                        FieldIssue(
                            field_path=path,
                            issue_type=IssueType.MISSING,
                            message=f"A(z) '{path}' mező hiányzik.",
                            severity=1,
                        )
                    )
                elif isinstance(value, str) and not value.strip():
                    report.issues.append(
                        FieldIssue(
                            field_path=path,
                            issue_type=IssueType.EMPTY,
                            message=f"A(z) '{path}' mező üres.",
                            severity=1,
                        )
                    )

        report.status = CompletenessStatus.INCOMPLETE if (
            report.blocking_issues
        ) else (
            CompletenessStatus.WARNING if report.warnings
            else CompletenessStatus.COMPLETE
        )
        return report

    def _prepare_field_data(
        self,
        deal: DealData,
        mapping: MappingConfig,
        participant_override: int | None = None,
        raw_opportunity: dict | None = None,
        notices: list | None = None,
        context: ResolutionContext | None = None,
        package_values: dict | None = None,
    ) -> dict:
        """
        Kanonikus adatokból mező-értékpárok összeállítása a CanonicalResolveren.

        Cutover Task 3 — a pipeline CSAK Genie canonical mappingeket fogad:
        minden fogyasztó (direkt mező, checkbox-csoport, fill rule, pont,
        karaktercsoport, dátum/számjegy fragment, példány-mezők) a
        ``CanonicalResolver``-en keresztül oldja fel a registry-approved
        canonical path-okat explicit ``(role, index)`` kontextussal. A
        PDF-mezőnevek SOSEM választanak résztvevőt.

        A Task 2-es ideiglenes legacy kompatibilitási ágat ez a feladat
        törölte: egy legacy (Salesforce-neves) canonical mapping itt
        ``ValueError``-t dob ahelyett, hogy csendben feloldana — a migrálás
        a ``scripts/migrate_mappings_to_genie --apply`` audited futtatása.

        ``raw_opportunity`` a legacy ág maradványa: elfogadott, de már nem
        használt (a canonical ág a normalizált DealData-ból dolgozik).
        """
        # participant_override → explicit (role, index) kontextus csomagolása.
        # A resolver csak az azonos szerepű, index nélküli path-okon
        # helyettesít; eltérő szerepű path-ra sosem "szivárog" át.
        if context is None and participant_override is not None:
            context = ResolutionContext(
                participant=("co_borrower", participant_override + 1)
            )

        if _mapping_has_legacy_canonical(mapping):
            raise LegacyCanonicalMappingError(
                "Legacy (Salesforce-neves) canonical mapping — a pipeline csak "
                "Genie canonical path-okat fogad. Futtasd a "
                "scripts/migrate_mappings_to_genie.py --apply audited migrációt."
            )

        return self._prepare_field_data_canonical(
            deal,
            mapping,
            context=context,
            notices=notices,
            package_values=package_values,
        )

    # =====================================================================
    # Resolver-backed consumers (Genie canonical mappings)
    # =====================================================================


    def _prepare_field_data_canonical(
        self,
        deal: DealData,
        mapping: MappingConfig,
        context: ResolutionContext | None = None,
        notices: list | None = None,
        package_values: dict | None = None,
    ) -> dict:
        """Resolver-alapú mezőadatok Genie canonical mappingekhez.

        Minden fogyasztó — direkt mező, checkbox-csoport, fill rule, pont,
        karaktercsoport, dátum/számjegy fragment — a ``CanonicalResolver``-en
        keresztül oldja fel a registry-approved canonical path-okat az explicit
        ``(role, index)`` kontextussal. A PDF-mezőnevek SOSEM választanak
        résztvevőt. A jövedelem-kizárás a resolver policy-jén keresztül
        érvényesül (``income_bearing=False`` szereplő jövedlemsorai üresek) és
        notice-t kap. Nincs borrower/co-borrower dict duplikáció.
        """
        resolver = CanonicalResolver()
        entries = _genie_mapping_entries(mapping)
        loan = deal.loan
        field_data: dict = {}
        active_gates = _active_product_gates(deal, notices=notices)

        def resolve_text(path: str, fragment: str | None = None) -> str:
            try:
                return _resolve_canonical_text(resolver, deal, path, context, fragment)
            except ValueError as error:
                logger.warning("Canonical feloldási hiba: %s", error)
                return ""

        # Account kontakt-pool notice (a legacy ággal azonos üzenet).
        if notices is not None and getattr(deal, "account_pool_extra", None):
            extra = deal.account_pool_extra
            msg = (
                f"Az Accounton {len(extra)} további kontakt szerepel, aki nem "
                f"résztvevője az ügyletnek: {', '.join(extra[:5])}"
                + (" …" if len(extra) > 5 else "")
            )
            if msg not in notices:
                notices.append(msg)

        # Minden hivatkozott canonical path előre feloldva (szabály/pont motor).
        referenced: list[str] = []
        seen_paths: set[str] = set()

        def _refer(path) -> None:
            if (
                isinstance(path, str)
                and path not in seen_paths
                and canonical_registry.validate(path)
            ):
                seen_paths.add(path)
                referenced.append(path)

        for entry in entries:
            _refer(entry["canonical_field"])
            rule = entry["fill_rule"]
            if isinstance(rule, dict):
                _refer(rule.get("sf_field"))
        declared_groups = (
            mapping.get("character_groups", [])
            if isinstance(mapping, dict)
            else (getattr(mapping, "character_groups", None) or [])
        )
        for group in declared_groups:
            if isinstance(group, dict):
                _refer(group.get("canonical_field"))
        values_by_path: dict[str, str] = {path: resolve_text(path) for path in referenced}

        # Jövedelem-kizárás notice a resolver policy mellett (a hatékony,
        # kontextussal kiválasztott résztvevőre számolva).
        self._note_canonical_income_exclusions(deal, entries, notices, context=context)

        # Karaktercsoport-tagok (deklarált + futásidőben felismert csoportok).
        runtime_char_groups = _infer_runtime_character_groups(mapping)
        char_group_members: set[str] = set()
        for grp in runtime_char_groups:
            members = (
                grp.get("member_fields", [])
                if isinstance(grp, dict)
                else getattr(grp, "member_fields", [])
            )
            if members:
                char_group_members.update(members)

        # === Direkt mezők — resolver + kontextus ===
        for entry in entries:
            canonical = entry["canonical_field"]
            if not canonical:
                continue
            pdf_name = entry["pdf_field_name"]
            if pdf_name in char_group_members:
                continue
            if "___" in pdf_name or entry["checkbox_group"] or entry["fill_rule"]:
                continue  # a csoport- / szabálymotor tölti
            # F4 — nem aktív termék-szekció (product_gate) mezője üres marad.
            gate = entry["product_gate"]
            if gate and gate not in active_gates:
                logger.debug(
                    "product_gate %s inaktív — %s mező kihagyva", gate, pdf_name
                )
                continue
            try:
                parsed = parse_path(canonical)
            except ValueError:
                logger.warning(
                    "Nem canonical path a Genie mappingben: %r (%s)",
                    canonical, pdf_name,
                )
                continue
            # A résztvevő "kapcsolat" mezője szövegesen sosem töltendő
            # (a legacy Contact.Relation__c skip megfelelője).
            if parsed.namespace == "participant" and parsed.field == "relation":
                continue
            value = resolve_text(canonical, entry["fragment"])
            if not value:
                continue
            if not entry["fragment"] and re.match(
                r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", value
            ):
                inferred = _infer_date_fragment(pdf_name, entry["label"])
                if inferred:
                    value = _apply_fragment(value, inferred)
                    if not value:
                        continue
            if entry["field_type"] == "checkbox":
                folded = _fold_text(str(value))
                if folded in ("igen", "true", "yes", "1"):
                    field_data[pdf_name] = "igen"
                elif folded in ("nem", "false", "no", "0"):
                    field_data[pdf_name] = "nem"
            else:
                field_data[pdf_name] = value

        # === Checkbox csoportok — a canonical path választ résztvevőt ===
        groups: dict[tuple[str, str], list] = {}
        matched_standard_canonicals: set[str] = set()
        for entry in entries:
            cbg = entry["checkbox_group"]
            canonical = entry["canonical_field"]
            if not cbg or not canonical:
                continue
            gid = cbg.get("group_id", "") if isinstance(cbg, dict) else ""
            if not gid:
                continue
            groups.setdefault((gid, canonical), []).append((entry, cbg))

        for (gid, canonical), group_items in groups.items():
            # F4 — a csoport által deklarált product_gate-ek: ha a deal
            # termékei egyiket sem aktiválják, a teljes szekció-csoport
            # érintetlen marad (nincs pipa, nincs 'nem' sem — a legacy
            # gate-elt kulcs hiányának megfelelője).
            group_gates = {
                entry_item["product_gate"]
                for entry_item, _cbg in group_items
                if entry_item["product_gate"]
            }
            if group_gates and not group_gates.issubset(active_gates):
                logger.debug(
                    "product_gate %s inaktív — %s csoport kihagyva",
                    sorted(group_gates), gid,
                )
                continue
            explicit_best = None
            for entry_item, cbg_item in group_items:
                rule = entry_item["fill_rule"]
                if isinstance(rule, dict) and rule.get("type"):
                    ev = self._eval_fill_rule(
                        rule, values_by_path, values_by_path, values_by_path,
                        deal, values_by_path.get(canonical),
                    )
                    if ev == "igen":
                        explicit_best = (entry_item, cbg_item)
                        break

            sf_value = values_by_path.get(canonical, "")

            # Igen/Nem csoportok levezetése (a legacy logika canonical párjai).
            opt_tokens = {
                _fold_text(
                    (cbg_i.get("match_value") or cbg_i.get("option_value")
                     or cbg_i.get("option_label") or "")
                )
                for _, cbg_i in group_items if isinstance(cbg_i, dict)
            } - {""}
            is_bool_group = bool(opt_tokens) and opt_tokens.issubset(
                {"igen", "nem", "true", "false", "yes", "no", "1", "0"}
            )
            if is_bool_group:
                folded_sf = _fold_text(str(sf_value or ""))
                if folded_sf not in ("igen", "nem", "true", "false", "yes", "no", "1", "0"):
                    if canonical in ("deal.csok_amount", "deal.afa_support"):
                        digits = re.sub(r"[^\d]", "", str(sf_value or ""))
                        has_support = bool(digits and int(digits) > 0)
                        if not has_support and canonical == "deal.csok_amount":
                            prod_txt = _fold_text(
                                f"{loan.product_name or ''} "
                                f"{' '.join(str(p) for p in (getattr(deal, 'products', []) or []))}"
                            )
                            has_support = "csok" in prod_txt
                        sf_value = "true" if has_support else "false"
                    elif (
                        _canonical_field_matches(canonical, "property", "ingatlan_szerepe")
                        and folded_sf
                    ):
                        first_fn = group_items[0][0]["pdf_field_name"].lower()
                        grp_lbl = _fold_text(
                            (group_items[0][1].get("group_label", "")
                             if isinstance(group_items[0][1], dict) else "")
                            + " " + gid + " " + first_fn
                        )
                        if "collateral" in grp_lbl or "fedezet" in grp_lbl:
                            sf_value = (
                                "igen"
                                if ("celingatlan" in folded_sf
                                    or "potfedezet" in folded_sf
                                    or "fedezet" in folded_sf)
                                else ""
                            )
                        elif ("loanpurpose" in grp_lbl
                              or "hitel celja" in grp_lbl
                              or "hitelcel" in grp_lbl):
                            if "celingatlan" in folded_sf:
                                sf_value = "igen"
                            elif "potfedezet" in folded_sf:
                                sf_value = "nem"
                        elif ("residental" in grp_lbl
                              or "residential" in grp_lbl
                              or "laknak" in grp_lbl):
                            jelleg_fold = _fold_text(
                                resolve_text("property.property_type")
                            )
                            if ("celingatlan" in folded_sf
                                    and any(w in jelleg_fold for w in (
                                        "lakas", "lakohaz", "haz", "ikerhaz",
                                        "sorhaz", "udulo",
                                    ))):
                                sf_value = "igen"
                            elif "potfedezet" in folded_sf:
                                sf_value = "nem"

            if explicit_best is None and not sf_value:
                continue

            sf_value_lower = str(sf_value or "").strip().lower()
            best_item = explicit_best
            # Static "Egyéb" is only a fallback: a canonical standard option
            # must win when it matches. Other typed rules retain priority.
            if (
                best_item is not None
                and best_item[0].get("fill_rule", {}).get("type") == "static"
            ):
                best_item = None
            best_score = -1
            if best_item is None and sf_value_lower:
                for entry_item, cbg_item in group_items:
                    match_values = _fill_rule_match_values(entry_item["fill_rule"])
                    if isinstance(cbg_item, dict):
                        match_values = [
                            cbg_item.get("option_value", ""),
                            cbg_item.get("match_value", ""),
                            *match_values,
                        ]
                    opt_lbl = (
                        cbg_item.get("option_label", "")
                        if isinstance(cbg_item, dict) else ""
                    )
                    for match_val in match_values:
                        if not isinstance(match_val, str):
                            continue
                        if _opt_matches(match_val, sf_value_lower, opt_lbl):
                            score = min(
                                len(_fold_text(match_val or opt_lbl)),
                                len(_fold_text(sf_value_lower)),
                            )
                            if score > best_score:
                                best_score = score
                                best_item = (entry_item, cbg_item)
            if best_item is None:
                best_item = explicit_best
            if best_item is not None:
                matched_cbg = best_item[1]
                matched_mv = (
                    (matched_cbg.get("match_value")
                     or matched_cbg.get("option_value", ""))
                    if isinstance(matched_cbg, dict) else ""
                )
                if "egyeb" not in _fold_text(matched_mv):
                    matched_standard_canonicals.add(canonical)
            for entry_item, _cbg in group_items:
                pdf_name = entry_item["pdf_field_name"]
                if best_item is not None and entry_item is best_item[0]:
                    if "___" in pdf_name:
                        base_name, export_val = pdf_name.split("___", 1)
                        field_data[base_name] = export_val
                        field_data[pdf_name] = "igen"
                    else:
                        field_data[pdf_name] = "igen"
                else:
                    if "___" not in pdf_name:
                        field_data[pdf_name] = "nem"

        # === Fill Rule engine — canonical_value a resolverből ===
        for entry in entries:
            fill_rule = entry["fill_rule"]
            if not fill_rule:
                continue
            cbg = entry["checkbox_group"]
            if (
                isinstance(cbg, dict)
                and cbg.get("group_id")
                and isinstance(fill_rule, dict)
                and not fill_rule.get("type")
                and fill_rule.get("match_value") is not None
            ):
                continue  # a checkbox csoport feloldás már kezelte
            if (
                isinstance(fill_rule, dict)
                and fill_rule.get("type") == "static"
                and entry["canonical_field"] in matched_standard_canonicals
                and "egyeb" in _fold_text(
                    str(
                        (cbg or {}).get("option_value", "")
                        or (cbg or {}).get("option_label", "")
                        or entry["pdf_field_name"]
                    )
                )
            ):
                continue  # a standard opció már kiválasztott; Egyéb csak fallback
            pdf_name = entry["pdf_field_name"]
            # F4 — nem aktív termék-szekció szabálya sem tölt.
            gate = entry["product_gate"]
            if gate and gate not in active_gates:
                logger.debug(
                    "product_gate %s inaktív — %s szabály kihagyva", gate, pdf_name
                )
                continue
            # F3 — a canonical conditional szabály sf_field-je csak registry-
            # approved canonical path lehet; egy legacy (még nem fordított)
            # kulcs sosem egyezne — ezt NEM hagyjuk csendben.
            if isinstance(fill_rule, dict) and fill_rule.get("type") == "conditional":
                sf_field = fill_rule.get("sf_field", "")
                if not sf_field or not canonical_registry.validate(sf_field):
                    msg = (
                        f"A(z) '{pdf_name}' mező feltételes fill_rule "
                        f"'sf_field'-je ({sf_field!r}) nem registry-approved "
                        f"canonical path — a szabály kihagyva, a mező üres "
                        f"marad. (Legacy→canonical fordítás a Task 3 "
                        f"aktiválásban.)"
                    )
                    logger.warning("   ⚠️ %s", msg)
                    if notices is not None and msg not in notices:
                        notices.append(msg)
                    continue
            canonical = entry["canonical_field"]
            canonical_value = values_by_path.get(canonical) if canonical else None
            try:
                value = self._eval_fill_rule(
                    fill_rule, values_by_path, values_by_path, values_by_path,
                    deal, canonical_value,
                )
                if value is not None:
                    field_data[pdf_name] = value
                    if "___" in pdf_name and value == "igen":
                        base_name, export_val = pdf_name.split("___", 1)
                        field_data[base_name] = export_val
                    if (
                        value == "igen"
                        and canonical
                        and isinstance(fill_rule, dict)
                        and fill_rule.get("match_value")
                        and not fill_rule.get("type")
                        and "egyeb" not in _fold_text(str(fill_rule.get("match_value", "")))
                    ):
                        matched_standard_canonicals.add(canonical)
            except Exception as e:
                logger.debug(f"fill_rule error on {pdf_name}: {e}")

        # === Round-2 point-level fill engine — canonical_values a resolverből ===
        if isinstance(mapping, dict):
            points = mapping.get("points") or []
        else:
            points = getattr(mapping, "points", None)
            if points is None:
                _to_dict = getattr(mapping, "to_dict", None)
                points = (_to_dict().get("points") if callable(_to_dict) else None) or []
        if points:
            from src.engine.fill_rules import RuleContext, RULE_REGISTRY, Point
            # Re-review guard: a pont-motor sem töltheti újra a nem aktív
            # termék-szekció (product_gate) mezőit.
            gated_off_fields = {
                entry["pdf_field_name"]
                for entry in entries
                if entry["product_gate"] and entry["product_gate"] not in active_gates
            }
            ctx = RuleContext(
                active_participants=deal.active_participants,
                products=getattr(deal, "products", []) or [],
                loan_purpose=loan.loan_purpose,
                product_name=loan.product_name,
                canonical_values=values_by_path,
            )
            for p_def in points:
                rt = p_def.get("rule_type")
                if rt is None:
                    continue
                fn = RULE_REGISTRY.get(rt)
                if fn is None:
                    logger.warning(
                        "ismeretlen rule_type %s a %s ponton",
                        rt, p_def.get("point_id"),
                    )
                    continue

                p_def_eff = p_def
                is_auto = (
                    p_def.get("_source") == "auto_group"
                    or str(p_def.get("point_id", "")).startswith("AUTO_")
                )
                if is_auto and rt == 3:
                    canonical_field = _infer_canonical_from_blocks(p_def, mapping)
                    actual_value = (
                        ctx.canonical_values.get(canonical_field)
                        if canonical_field else None
                    )
                    matched_block = next(
                        (
                            b.get("block_id") for b in p_def.get("blocks", [])
                            if _values_match(b.get("block_id"), actual_value)
                        ),
                        None,
                    )
                    if (
                        matched_block is not None
                        and canonical_field
                        and "egyeb" not in _fold_text(matched_block)
                    ):
                        matched_standard_canonicals.add(canonical_field)
                    if matched_block is None:
                        logger.info(
                            "auto_group pont %s: a canonical érték %r nem illik "
                            "egyik opcióra sem (mező: %s) — minden checkbox 'nem'",
                            p_def.get("point_id"),
                            actual_value, canonical_field,
                        )
                    p_def_eff = {
                        **p_def,
                        "params": {**p_def.get("params", {}), "active_block": matched_block},
                    }

                point = Point(
                    point_id=p_def_eff["point_id"],
                    framework=p_def_eff.get("framework", "*"),
                    blocks=p_def_eff.get("blocks", []),
                    rule_type=rt,
                    params=p_def_eff.get("params", {}),
                )
                result = fn(point, ctx)
                for pdf_field, tick_value in result.ticks.items():
                    if pdf_field in gated_off_fields:
                        logger.debug(
                            "point %s kihagyta a product-gate-elt mezőt: %s",
                            point.point_id, pdf_field,
                        )
                        continue
                    if pdf_field in field_data and field_data[pdf_field] != tick_value:
                        logger.debug(
                            "point %s felülírja a %s mezőt: %r -> %r",
                            point.point_id, pdf_field,
                            field_data[pdf_field], tick_value,
                        )
                    field_data[pdf_field] = tick_value

        # === "Egyéb: ____" kiegészítő mezők elnyomása standard egyezéskor ===
        # Csak szabad szöveges kiegészítő mezőket nyomunk el — a checkbox
        # tagokat (pl. auto_group pontok által pipált opciók) megtartjuk.
        if matched_standard_canonicals:
            for entry in entries:
                canonical = entry["canonical_field"]
                if not canonical or canonical not in matched_standard_canonicals:
                    continue
                pdf_name = entry["pdf_field_name"]
                if (
                    entry["field_type"] == "checkbox"
                    or "___" in pdf_name
                    or entry["checkbox_group"]
                    or entry["fill_rule"]
                    or pdf_name in char_group_members
                ):
                    continue
                try:
                    parsed = parse_path(canonical)
                except ValueError:
                    continue
                if (
                    parsed.namespace == "participant"
                    and parsed.field in _EGYEB_PARTICIPANT_FIELDS
                ) or "egyeb" in _fold_text(pdf_name):
                    field_data.pop(pdf_name, None)

        # === M5: karakter-csoportok — a canonical path értékéből bontva ===
        for grp in runtime_char_groups:
            if not isinstance(grp, dict):
                continue
            canonical = grp.get("canonical_field") or ""
            members = grp.get("member_fields") or []
            if not members or not canonical:
                continue
            if canonical not in values_by_path:
                values_by_path[canonical] = resolve_text(canonical)
            field_data.update(
                expand_character_groups({canonical: values_by_path[canonical]}, [grp])
            )

        # Administrative package values are final, PDF-name-keyed overrides.
        # Unlike canonical deal data they can populate otherwise unmapped
        # fields and can intentionally contain zero/empty values.
        if package_values:
            field_data.update(package_values)

        return field_data

    def _note_canonical_income_exclusions(
        self,
        deal: DealData,
        entries: list[dict],
        notices: list | None,
        context: ResolutionContext | None = None,
    ) -> None:
        """Notice a resolver általi jövedelem-kizárásról (policy a resolverben).

        A ``CanonicalResolver`` az ``income_bearing=False`` szereplő
        jövedelemsorait üresen oldja fel; itt csak jelezzük a felhasználó
        felé, mert a notice a legacy ág garanciája.

        Re-review fix: a számolás a HATÁKONY résztvevőt követi. F2
        helyettesítéssel az ALAP (index nélküli) participant income path-ok a
        KIVÁLASZTOTT résztvevőre oldódnak fel — a notice is ahhoz tartozik.
        Explicit indexelt path-ok (és a kontextus nélküli futás) a deklarált
        szerepkör szerint számolódnak, mint korábban.
        """
        if notices is None:
            return
        selected = context.participant if context is not None else None
        selected_income_paths = 0
        declared_income_paths: dict[str, int] = {}
        for entry in entries:
            canonical = entry["canonical_field"]
            if not canonical:
                continue
            try:
                parsed = parse_path(canonical)
            except ValueError:
                continue
            if parsed.namespace == "participant" and parsed.collection == "income":
                if selected is not None and parsed.participant_index is None:
                    selected_income_paths += 1
                else:
                    declared_income_paths[parsed.role] = (
                        declared_income_paths.get(parsed.role, 0) + 1
                    )
        if not selected_income_paths and not declared_income_paths:
            return
        for participant in deal.active_participants:
            if participant.income_bearing is not False:
                continue
            role_name = next(
                (
                    role
                    for role, role_enum in _CANONICAL_ROLE_ENUM.items()
                    if role_enum == participant.role
                ),
                "",
            )
            count = declared_income_paths.get(role_name, 0)
            if selected is not None and selected_income_paths:
                same_role = [
                    p for p in deal.active_participants if p.role == participant.role
                ]
                position = same_role.index(participant) + 1
                if role_name == selected[0] and position == selected[1]:
                    count += selected_income_paths
            if not count:
                continue
            msg = (
                f"{participant.name} ({participant.role.value}) a Salesforce-ban "
                f"'jövedelem nélkül bevont' szerepben szerepel — "
                f"{count} jövedelem-jellegű mező üresen marad a nyomtatványon."
            )
            logger.warning("   ⚠️ %s", msg)
            if msg not in notices:
                notices.append(msg)

    def _eval_fill_rule(self, rule, all_data: dict, borrower: dict, co_borrower: dict, deal, canonical_value: str | None = None) -> str | None:
        """
        Evaluate a single fill_rule expression.

        rule can be:
        - dict with type=static, value=igen (standard format per brief)
        - dict with match_value=Magyar (legacy format, checkbox radio option)
        - str: legacy format (backward compat), treated as static value

        For legacy ``match_value`` rules (radio button options), the checkbox
        is checked (returns "igen") if the field's canonical_field value from
        the deal data matches the ``match_value``. This is passed in via
        *canonical_value* by the caller.
        """
        if not rule:
            return None

        # Legacy string support (backward compat)
        if isinstance(rule, str):
            return rule

        if not isinstance(rule, dict):
            return None

        rule_type = rule.get("type", "")

        # Legacy match_value format (radio button option): checked if the
        # canonical field's deal value matches any declared value or alias.
        match_values = _fill_rule_match_values(rule)
        if match_values and not rule_type:
            if canonical_value is not None:
                if any(
                    _opt_matches(match_value, str(canonical_value))
                    for match_value in match_values
                ):
                    return "igen"
            return None

        value = rule.get("value", "")
        value_from = rule.get("value_from", "")
        if value_from:
            value = all_data.get(value_from, "")

        # static — always this value
        if rule_type == "static":
            return value

        # per_participant — value for every active participant
        # (the fill engine runs per-deal, so this just returns the value
        # if there are active participants)
        if rule_type == "per_participant":
            if deal.active_participants:
                return value
            return None

        # conditional — SF field value must match
        if rule_type == "conditional":
            sf_field = rule.get("sf_field", "")
            match = rule.get("match", "")
            match_not = rule.get("match_not", "")
            sf_value = all_data.get(sf_field, "") or borrower.get(sf_field, "")
            required_field = rule.get("required_field", "")
            required_match = rule.get("required_match", "")
            if required_field:
                required_value = all_data.get(required_field, "") or borrower.get(required_field, "")
                if not required_value or not _opt_matches(str(required_match), str(required_value)):
                    return None
            if match_not:
                if sf_value and not _opt_matches(str(match_not), str(sf_value)):
                    return value
                return None
            if sf_value and _opt_matches(str(match), str(sf_value)):
                return value
            return None

        # role_based — value if any active participant has one of the roles
        if rule_type == "role_based":
            roles = rule.get("roles", [])
            roles_lower = [r.lower() for r in roles]
            for p in deal.active_participants:
                if p.role.value.lower() in roles_lower:
                    return value
            return None

        return None

    def _fill_pdf(
        self,
        template_pdf: Path,
        deal: DealData,
        field_data: dict,
        mapping: MappingConfig,
        instance_fields: list | None = None,
        participant_override: int | None = None,
    ) -> tuple[Path, list[str], list[str]]:
        """
        PDF kitöltés a professional engine osztályokkal (AcroFormFiller / OverlayFiller).
        """
        if instance_fields:
            field_data = self._apply_instance_data(field_data, deal, mapping, instance_fields)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        override_tag = f"_co{participant_override + 1}" if participant_override is not None else ""
        output_filename = f"filled_{deal.deal_id}_{template_pdf.stem}{override_tag}_{timestamp}.pdf"
        output_path = self.output_dir / output_filename

        if mapping.form_type == "acroform":
            filler = AcroFormFiller(flatten=False)
            pdf_to_canonical = mapping.mapping_dict
            result = filler.fill(
                template_path=template_pdf,
                output_path=output_path,
                field_data=field_data,
                mapping=pdf_to_canonical,
            )
            if not result.success:
                logger.warning(
                    "AcroForm kitöltés figyelmeztetések: %s", result.summary
                )
                if not output_path.exists():
                    raise RuntimeError(f"AcroForm kitöltés sikertelen: {'; '.join(str(e) for e in result.errors)}")
        else:
            placements: dict[str, TextPlacement] = {}
            overlay_field_data: dict[str, str] = {}

            import fitz
            doc = fitz.open(str(template_pdf))

            for f in mapping.fields:
                if not f.coordinates:
                    continue
                is_checkbox = (f.field_type == "checkbox")
                if not is_checkbox and not f.canonical_field:
                    continue

                page_idx = max(int(f.page_number) - 1, 0)
                if page_idx < len(doc):
                    page_h = float(doc[page_idx].rect.height)
                else:
                    page_h = 842.0  # Fallback to standard A4 height

                coords = f.coordinates
                coord_w = float(coords.get("width", 12.0) or 12.0)
                coord_h = float(coords.get("height", 12.0) or 12.0)
                y_top_left = float(coords.get("y", 0.0))

                # Invert Y axis: ReportLab y0 is from bottom, mapping y0 is from top
                y_bottom_left = page_h - y_top_left - coord_h + 3

                placements[f.pdf_field_name] = TextPlacement(
                    x=float(coords.get("x", 0.0)),
                    y=y_bottom_left,
                    font_size=10.0,
                    page_index=page_idx,
                    field_type="checkbox" if is_checkbox else "text",
                    width=coord_w,
                    height=coord_h,
                )

                if f.pdf_field_name in field_data:
                    val = field_data[f.pdf_field_name]
                    if val:
                        overlay_field_data[f.pdf_field_name] = val
            doc.close()

            filler = OverlayFiller()
            result = filler.fill(
                template_path=template_pdf,
                output_path=output_path,
                field_data=overlay_field_data,
                mapping=placements,
            )
            if not result.success:
                logger.warning(
                    "Overlay kitöltés figyelmeztetések: %s", result.summary
                )
                if not output_path.exists():
                    raise RuntimeError(f"Overlay kitöltés sikertelen: {'; '.join(str(e) for e in result.errors)}")

        return output_path, list(result.filled_fields), list(result.skipped_fields)

    # =========================================================================
    # C6 — Példány-alapú mező-feloldás (multi-instance participants/properties)
    # =========================================================================

    def _apply_instance_data(
        self,
        field_data: dict,
        deal: DealData,
        mapping: MappingConfig,
        instance_fields: list,
    ) -> dict:
        """C6: kiegészíti a field_data-t a duplikált példányok ``_N`` mezőivel.

        Cutover Task 3: a legacy (SF-kulcsos) felő fele törölve — minden
        példány-mező a ``CanonicalResolver``-en keresztül oldódik fel
        (lásd ``_apply_instance_data_canonical``)."""
        return self._apply_instance_data_canonical(
            field_data, deal, mapping, instance_fields
        )

    def _apply_instance_data_canonical(
        self,
        field_data: dict,
        deal: DealData,
        mapping: MappingConfig,
        instance_fields: list,
    ) -> dict:
        """Cutover Task 2 — példány-mezők resolver + explicit (role, index).

        Minden duplikált oldali ``_N`` mező a saját szerepe (co_borrower /
        guarantor / mortgagor / usufructuary / property) N-edik példányának
        adatait kapja a ``CanonicalResolver``-en keresztül — sosem
        co-borrower adatokat kezes/zálogkötelezett oldalakra.
        """
        resolver = CanonicalResolver()
        canonical_by_base: dict[str, str] = {}
        frag_by_base: dict[str, str] = {}
        virtual_sources_by_base: dict[str, list[str]] = {}
        for entry in _genie_mapping_entries(mapping):
            base = entry["pdf_field_name"]
            canonical = entry["canonical_field"]
            if (
                base
                and isinstance(canonical, str)
                and canonical_registry.validate(canonical)
            ):
                canonical_by_base[base] = canonical
            if base and entry["fragment"]:
                frag_by_base[base] = entry["fragment"]
            if base and "___" in base:
                actual_base = base.split("___", 1)[0]
                virtual_sources_by_base.setdefault(actual_base, []).append(base)

        result = dict(field_data)  # ne mutáljuk a hívó szótárát
        assigned = 0
        for im in instance_fields:
            canonical_role = INSTANCE_ROLE_TO_CANONICAL.get(
                im.role, INSTANCE_ROLE_CO_BORROWER
            )
            role_index = im.role_index if im.role_index is not None else im.instance
            if canonical_role == "property":
                instance_ctx = ResolutionContext(property_index=role_index)
            else:
                instance_ctx = ResolutionContext(
                    participant=(canonical_role, role_index)
                )

            if canonical_role in ("mortgagor", "usufructuary"):
                # SZA ESZ contains radio/checkbox groups whose real AcroForm
                # names are base fields while the mapping uses virtual
                # ``base___export`` entries. Resolve the WHOLE participant copy
                # through the normal direct/group/fill-rule engine, then rebind
                # its prepared values to this master page's actual field names.
                instance_data = self._prepare_field_data(
                    deal, mapping, context=instance_ctx
                )
                for base in im.base_fields:
                    target_name = (
                        base if im.instance == 1 else f"{base}_{im.instance}"
                    )
                    result.pop(target_name, None)
                    virtual_sources = virtual_sources_by_base.get(base, [])
                    if im.instance == 1:
                        for source in virtual_sources:
                            result.pop(source, None)
                    if base in instance_data:
                        result[target_name] = instance_data[base]
                        assigned += 1
                    if im.instance == 1:
                        for source in virtual_sources:
                            if source in instance_data:
                                result[source] = instance_data[source]
                continue

            for base in im.base_fields:
                target_name = base if im.instance == 1 else f"{base}_{im.instance}"
                if im.instance == 1:
                    # The first SZA ESZ page keeps unsuffixed AcroForm names.
                    # Remove borrower values even when the selected collateral
                    # participant has no value, preventing cross-role leakage.
                    result.pop(target_name, None)
                canonical = canonical_by_base.get(base)
                if not canonical:
                    continue
                try:
                    # F2 — a példány-kontextus ugyanazt a per-applicant
                    # kiválasztási szemantikát alkalmazza: az alap (index
                    # nélküli) participant path-ok a példány résztvevőjének
                    # adatait kapják; explicit index fix marad.
                    raw = _resolve_canonical_text(
                        resolver, deal, canonical, instance_ctx,
                        frag_by_base.get(base),
                    )
                except ValueError as error:
                    logger.debug(
                        "instance canonical feloldási hiba (%s): %s", base, error
                    )
                    continue
                if raw is None or raw == "":
                    continue
                result[target_name] = raw
                assigned += 1

        if assigned:
            logger.info(
                "   C6: %d átnevezett _N mezőhöz példányadat rendelve "
                "(canonical resolver)",
                assigned,
            )
        return result

    def _fill_legal_declarations(
        self,
        pdf_path: Path,
        mapping: MappingConfig,
        deal: DealData,
    ) -> dict:
        """
        Jogi nyilatkozat checkbox-ok kezelése a már kitöltött PDF-en
        (canonical mapping MELLÉ) — Cutover Task 5 safe declaration policy.

        Lépések:
        1. Kinyeri a canonical_field nélküli checkbox-okat a mapping-ből
           (points-managed role/signature mezők KIMARADNAK — azokat a
           pont-motor kezelte).
        2. LegalClassifier.classify_batch() → kategóriák.
        3. LegalClassifier.apply_defaults(deal, points_managed) → értékek.
           A v2.0 safe táblával ez gyakorlatilag üres (nincs automatikus
           true/false); csak felülvizsgált explicit override-okat tartalmaz.
        4. Beírja az értékeket a PDF AcroForm checkbox-aiba, a feloldatlan
           nyilatkozatokat pedig kézi döntésként listázza.

        Returns:
            {"modified": int, "manual": [{"pdf_field_name", "label",
             "page_number", "category"}, ...]}
        """
        points_managed = points_managed_fields(mapping)
        unmapped = extract_unmapped_checkboxes(mapping)
        if not unmapped:
            logger.debug("Nincsenek leképezetlen checkbox-ok – jogi lépés kihagyva.")
            return {"modified": 0, "manual": []}

        classifier = LegalClassifier()
        classified = classifier.classify_batch(unmapped)
        # Review fix 4: consent override-ok kizárólag explicit CRM canonical
        # evidence mellett élhetnek — a mapping consent canonical útjai és a
        # deal consents adatai alapján számoljuk a fedezeti halmazt.
        crm_evidence = explicit_crm_consent_evidence(mapping, deal)
        values = classifier.apply_defaults(
            classified,
            deal,
            points_managed=points_managed,
            crm_consent_evidence=crm_evidence,
        )

        manual = [
            {
                "pdf_field_name": u["pdf_field_name"],
                "label": u["label"],
                "page_number": u["page_number"],
                "category": classified.get(u["pdf_field_name"], "unknown"),
            }
            for u in unmapped
            if u["pdf_field_name"] not in values
        ]
        logger.info(
            "   Jogi nyilatkozat: %d mező vizsgálva, %d automatikus érték "
            "(safe policy), %d KÉZI döntés listázva.",
            len(unmapped),
            len(values),
            len(manual),
        )

        modified = 0
        if values:
            modified = fill_legal_declarations_on_pdf(Path(pdf_path), values)
            if modified:
                logger.info(
                    "   Jogi nyilatkozat: %d/%d checkbox beállítva.",
                    modified,
                    len(values),
                )
            else:
                logger.info(
                    "   Jogi nyilatkozat: %d érték kalkulálva, de a PDF-ben "
                    "nem található a hozzá tartozó AcroForm checkbox.",
                    len(values),
                )
        return {"modified": modified, "manual": manual}

    # =========================================================================
    # ELAVULT metódusok – korábban inline pikepdf/PyMuPDF logikát tartalmaztak.
    # A consolidation után a professional engine osztályok veszik át a helyüket
    # (AcroFormFiller / OverlayFiller a src/engine/pdf_filler.py-ban).
    # Kikommentelt másolat itt marad referenciaként / biztonsági mentésként.
    # =========================================================================

    # def _fill_acroform(self, template: Path, output: Path, field_data: dict):
    #     """[ELAVULT] AcroForm PDF kitöltés pikepdf-el – most már AcroFormFiller."""
    #     import pikepdf
    #     with pikepdf.open(template) as pdf:
    #         if "/AcroForm" in pdf.Root:
    #             acroform = pdf.Root["/AcroForm"]
    #             if "/Fields" in acroform:
    #                 for field_ref in acroform["/Fields"]:
    #                     try:
    #                         f = field_ref
    #                         field_name = str(f.get("/T", ""))
    #                         if field_name in field_data:
    #                             value = field_data[field_name]
    #                             f["/V"] = pikepdf.String(value)
    #                             if "/AP" in f:
    #                                 del f["/AP"]
    #     ...

    # def _fill_overlay(self, template, output, field_data, mapping):
    #     """[ELAVULT] Overlay kitöltés – most már OverlayFiller."""
    #     import fitz
    #     doc = fitz.open(str(template))
    #     ...

    def run_ai_recognition(self, pdf_path: Path, mode: str = "auto") -> MappingConfig:
        """
        AI mezőfelismerés futtatása egy üres nyomtatványra.
        
        Args:
            pdf_path: Üres PDF nyomtatvány
            mode: "auto", "acroform", vagy "overlay"
            
        Returns:
            MappingConfig a felismert mezőkkel
        """
        recognizer = FieldRecognizer()
        mapping = recognizer.recognize(pdf_path, mode=mode)
        
        # Mapping mentése
        mapping_path = PROJECT_ROOT / "src" / "mapping" / f"{pdf_path.stem}_mapping.json"
        mapping.save(mapping_path)
        
        return mapping


def main():
    """Fő belépési pont – CLI és demo futtatás."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="FinancialGenie – Banki nyomtatványkitöltő PoC"
    )
    parser.add_argument(
        "--scenario", "-s",
        default="simple",
        help="Dummy forgatókönyv (default: simple)",
    )
    parser.add_argument(
        "--recognize", "-r",
        type=Path,
        default=None,
        help="AI mezőfelismerés futtatása ezen a PDF-en",
    )
    parser.add_argument(
        "--template", "-t",
        type=Path,
        default=None,
        help="PDF sablon a kitöltéshez",
    )
    parser.add_argument(
        "--mapping", "-m",
        type=Path,
        default=None,
        help="Mapping konfiguráció JSON",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=Path,
        default=None,
        help="Output könyvtár",
    )
    parser.add_argument(
        "--salesforce", "-sf",
        action="store_true",
        help="Valódi Salesforce sandbox használata",
    )
    parser.add_argument(
        "--deal", "-d",
        type=str,
        default=None,
        help="Kifejezett Salesforce Opportunity ID",
    )

    args = parser.parse_args()

    # FIX L7 — the CLI used raw print() for all user-facing output, bypassing
    # the logging configuration and making it impossible to capture/redirect.
    # Use a dedicated "cli" logger so output respects the configured handlers
    # and log level (basicConfig is set above).
    cli = logging.getLogger("cli")

    cli.info("=" * 60)
    cli.info("🧞 FinancialGenie – Banki nyomtatványkitöltő PoC")
    cli.info("=" * 60)

    # Pipeline inicializálása
    # Dummy adatok generálása ha nincs
    dummy_dir = PROJECT_ROOT / "samples" / "dummy_data"
    if not dummy_dir.exists() or not list(dummy_dir.glob("*.json")):
        cli.info("📦 Dummy adatok generálása...")
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from generate_dummy_data import generate_all_scenarios
        generate_all_scenarios(dummy_dir)

    # Salesforce kliens inicializálása
    if args.salesforce:
        from config import settings
        cli.info("☁️  Kapcsolódás a Salesforce Sandbox-hoz...")
        sf_client = SalesforceClient(
            username=settings.SF_USERNAME,
            password=settings.SF_PASSWORD,
            security_token=settings.SF_SECURITY_TOKEN,
            domain=settings.SF_DOMAIN,
            mock_mode=False
        )
    else:
        sf_client = SalesforceClient(mock_mode=True, mock_data_dir=dummy_dir)

    pipeline = FormFillerPipeline(
        sf_client=sf_client,
        output_dir=args.output_dir or PROJECT_ROOT / "output",
    )

    # 1. Template PDF feloldása először
    template_pdf = args.template
    if template_pdf is None:
        samples_dir = PROJECT_ROOT / "samples"
        acroform = samples_dir / "acroform_sample.pdf"
        flat = samples_dir / "flat_sample.pdf"
        if acroform.exists():
            template_pdf = acroform
        elif flat.exists():
            template_pdf = flat
        else:
            cli.warning("Nincs elérhető PDF sablon. Futtasd előbb:")
            cli.warning("   python scripts/generate_sample_pdfs.py")
            cli.warning("   A pipeline a dummy adatok generálásáig fut.")
            # Csak dummy adatok és normalizálás demo
            deals = pipeline.sf_client.list_deals()
            if deals:
                deal_id = deals[0]["deal_id"]
                raw = pipeline.sf_client.get_deal(deal_id)
                deal = pipeline.normalizer.normalize_deal(raw)
                cli.info("📊 Demo ügylet normalizálva:")
                cli.info("   ID: %s", deal.deal_id)
                cli.info(
                    "   Hitel: %s Ft, %s hónap",
                    fmt_money(deal.loan.loan_amount),
                    deal.loan.loan_term_months
                    if deal.loan.loan_term_months is not None
                    else "ismeretlen",
                )
                cli.info("   Szereplők: %d", len(deal.active_participants))
                for p in deal.active_participants:
                    cli.info("     - %s (%s)", p.name, p.role.value)
                cli.info("   Ingatlanok: %d", len(deal.properties))
                for prop in deal.properties:
                    cli.info("     - %s (%s)", prop.address.full_address, prop.property_type.value)
            return

    # 2. AI mezőfelismerés (ha kifejezetten kérték a --recognize argumentummal)
    mapping = None
    if args.recognize:
        cli.info("🤖 AI mezőfelismerés futtatása: %s", args.recognize)
        mapping = pipeline.run_ai_recognition(args.recognize)
        print_mapping_summary(mapping)

    # 3. Mapping betöltése vagy automatikus feloldása
    if mapping is None:
        if args.mapping:
            mapping = MappingConfig.load(args.mapping)
            cli.info("📋 Kifejezett mapping betöltve: %s", args.mapping.name)
        else:
            try:
                mapping = pipeline._resolve_mapping(template_pdf)
            except Exception as e:
                # Végső fallback ha valamiért teljesen meghiúsul az auto-felismerés
                cli.warning("Nem sikerült feloldani a mappinget: %s", e)
                mapping = MappingConfig(
                    bank_name="OTP Bank",
                    form_name="demo",
                    form_type="acroform",
                    notes="Nincs mapping – kanonikus mezőnevek használata",
                )
                cli.info("📋 Nincs mapping konfiguráció – demo mód")

    # 4. Pipeline futtatása
    cli.info("🚀 Pipeline indítása...")
    cli.info("   Sablon: %s", template_pdf)
    cli.info("   Forgatókönyv: %s", args.scenario)

    # Ügylet kiválasztása
    if args.deal:
        deal_id = args.deal
    else:
        deals = pipeline.sf_client.list_deals()
        if not deals:
            cli.warning("Nincs elérhető ügylet")
            return
        deal_id = deals[0]["deal_id"]
    result = pipeline.run_for_deal(deal_id, template_pdf, mapping)

    # Eredmény
    cli.info("=" * 60)
    if result["success"]:
        cli.info("✅ Sikeres kitöltés!")
        cli.info("   Output: %s", result["output_path"])
    else:
        cli.error("❌ Kitöltés sikertelen")

    if result["issues"]:
        cli.warning("Problémák (%d):", len(result["issues"]))
        for issue in result["issues"]:
            cli.warning("   - %s", issue)

    cli.info("⏱️  Timestamp: %s", result["timestamp"])
    cli.info("=" * 60)


if __name__ == "__main__":
    main()

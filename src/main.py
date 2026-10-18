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
from src.normalizer.data_normalizer import DataNormalizer
from src.integrations.salesforce_client import SalesforceClient
from src.engine.document_assembler import (
    DocumentAssembler, ProductType,
    INSTANCE_ROLE_CO_BORROWER, INSTANCE_ROLE_GUARANTOR, INSTANCE_ROLE_BENEFICIARY,
    INSTANCE_ROLE_PROPERTY,
)
from src.engine.pdf_filler import AcroFormFiller, OverlayFiller, TextPlacement, fmt_money, expand_character_groups
from src.engine.completeness_checker import CompletenessChecker, CompletenessStatus
from src.engine.role_instance_logic import RoleInstancePlanner, ParticipantRole as _RRole
from src.utils.gender import guess_gender
from src.ai.field_recognizer import (
    FieldRecognizer,
    MappingConfig,
    print_mapping_summary,
    _normalize_key,
)
from src.ai.legal_classifier import (
    LegalClassifier,
    extract_unmapped_checkboxes,
    fill_legal_declarations_on_pdf,
)

logger = logging.getLogger(__name__)


# Jövedelem-jellegű kanonikus mezők — ezek kerülnek kizárásra a kitöltésből,
# ha egy szereplő az SF OpportunityContactRole-ban "jövedelem nélkül bevont"
# jelöléssel szerepel (Participant.income_bearing is False).
_INCOME_CANONICAL_KEYS = frozenset({
    "Contact.Average_monthly_net_income__c",
    "Contact.Other_monthly_income__c",
    "Contact.Other_income__c",
    "Contact.Income_type__c",
    "Contact.Cafeteria_bonus__c",
    "Contact.Divident__c",
    "Contact.Income_from_self_owned_company__c",
    "Contact.Self_employment_details__c",
    "Contact.Jovairas_vallalasa__c",
})


# === Érték-fordítás, dátum-fragmentek, konstrukció-osztályozás (pipeline) ===
# A mapping-ben deklarált canonical kulcsok egy része NEM nyers CRM-érték,
# hanem származtatott/fordított érték. Három eszköz:
#   1) _VALUE_TRANSLATIONS: canonical → {forrásérték → célérték} szótár
#      (pl. SF Salutation "Mr." → a nyomtatvány "Férfi" opciója).
#   2) Dátum-fragment kulcsok: ``<canonical>#ev`` / ``#ho`` (digit-boxok).
#   3) Konstrukció-gate: ``<canonical>#piaci`` / ``#tamogatott`` kulcsok,
#      amelyek csak akkor kapnak értéket, ha a hitel konstrukciótípusa
#      alapján az adott szekció (3.1 államilag kamattámogatott / 3.2 piaci)
#      tényleg kitöltendő.

_VALUE_TRANSLATIONS: dict[str, dict[str, str]] = {
    "Contact.Salutation": {
        "mr": "Férfi", "mr.": "Férfi", "ur": "Férfi",
        "ms": "Nő", "ms.": "Nő", "mrs": "Nő", "mrs.": "Nő",
        "miss": "Nő", "holgy": "Nő", "asszony": "Nő",
    },
    "Contact.Employer_s_company_type__c": {
        "kft": "Korlátolt felelősségű társaság",
        "kft.": "Korlátolt felelősségű társaság",
        "korlatolt felelossegu tarsasag": "Korlátolt felelősségű társaság",
        "zrt": "Részvénytársaság",
        "zrt.": "Részvénytársaság",
        "nyrt": "Részvénytársaság",
        "nyrt.": "Részvénytársaság",
        "reszvenytarsasag": "Részvénytársaság",
        "bt": "Közkereseti társaság vagy betéti társaság",
        "bt.": "Közkereseti társaság vagy betéti társaság",
        "kkt": "Közkereseti társaság vagy betéti társaság",
        "kkt.": "Közkereseti társaság vagy betéti társaság",
        "beteti tarsasag": "Közkereseti társaság vagy betéti társaság",
        "kozkereseti tarsasag": "Közkereseti társaság vagy betéti társaság",
    },
    "Contact.Foglalkozas_tipusa__c": {
        "alkalmazott vezeto": "Vezető alkalmazott (felső vagy középvezető)",
        "vezeto alkalmazott": "Vezető alkalmazott (felső vagy középvezető)",
        "vezeto allasu": "Vezető alkalmazott (felső vagy középvezető)",
        "vezeto": "Vezető alkalmazott (felső vagy középvezető)",
        "alkalmazott": "Szellemi munkát végző alkalmazott",
        "szellemi alkalmazott": "Szellemi munkát végző alkalmazott",
        "szellemi munkat vegzo alkalmazott": "Szellemi munkát végző alkalmazott",
        "fizikai alkalmazott": "Fizikai munkát végző alkalmazott",
        "fizikai munkat vegzo alkalmazott": "Fizikai munkát végző alkalmazott",
    },
}

# Konstrukciótípus → a nyomtatvány "Választott konstrukció típusa" opciófelirata
_CONSTRUCTION_OPTION_LABELS: dict[str, str] = {
    "tamogatott": "Államilag kamattámogatott forint lakáshitel(ek)",
    "piaci": "Piaci kamatozású forint lakáshitel(ek)",
    "mindketto": "Államilag kamattámogatott és Piaci kamatozású forint lakáshitel együttes igénylése",
}


def _fold_text(s: str) -> str:
    """Ékezet-mentes, kisbetűs, trimmelt szöveg (egyeztetéshez)."""
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").strip().lower())
        if unicodedata.category(c) != "Mn"
    )


def _translate_value(canonical: str, value: str) -> str:
    """Canonical-szintű értékfordítás (pl. Salutation → nem). Ismeretlen
    értékre az eredetit adja vissza."""
    table = _VALUE_TRANSLATIONS.get(canonical)
    if not table or not value:
        return value
    return table.get(_fold_text(value), value)


def _date_parts(value: str) -> tuple[str, str, str]:
    """Dátum-szövegből (YYYY-MM-DD / YYYY.MM.DD / YYYY.MM / YYYY) (év, hónap, nap)."""
    if not value:
        return "", "", ""
    s = str(value).strip()
    m3 = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", s)
    if m3:
        return m3.group(1), m3.group(2).zfill(2), m3.group(3).zfill(2)
    m2 = re.match(r"^(\d{4})[-./](\d{1,2})", s)
    if m2:
        return m2.group(1), m2.group(2).zfill(2), ""
    m1 = re.match(r"^(\d{4})$", s)
    if m1:
        return m1.group(1), "", ""
    return "", "", ""


def _date_year_month(value: str) -> tuple[str, str]:
    """Dátum-szövegből (YYYY-MM-DD / YYYY.MM.DD / YYYY.MM) (év, hónap) pár.
    Nem dátum jellegű inputra ("", "")."""
    y, m, _ = _date_parts(value)
    return y, m


def _classify_construction(loan, deal=None) -> str:
    """Hitel konstrukciótípusának besorolása.

    Forrás: LoanDetails.product_type (SF Product_Type__c), fallbackként a
    product_name kulcsszavai, majd az ügylet products / notes mezői.
    Visszatérés: "tamogatott" | "piaci" | "mindketto" | "" (nem állapítható meg).
    """
    pt = _fold_text(getattr(loan, "product_type", "") or "")
    pn = _fold_text(getattr(loan, "product_name", "") or "")
    sub_kw = (
        "csok", "falusi", "babavaro", "kamattamogat",
        "otthon start", "otthon_start", "otthonteremt", "otthonfelujit", "osp",
    )

    def _match(text: str) -> str:
        if not text:
            return ""
        has_sub = any(k in text for k in sub_kw)
        has_mkt = "piaci" in text
        if has_sub and has_mkt:
            return "mindketto"
        if has_sub:
            return "tamogatott"
        if has_mkt:
            return "piaci"
        return ""

    res = _match(f"{pt} {pn}".strip())
    if res:
        return res

    # Fallback: deal.products és deal.notes (pl. Opportunity.Name = "Szabó Jolán OSP")
    if deal is not None:
        prods = " ".join(_fold_text(str(p)) for p in (getattr(deal, "products", []) or []))
        notes = _fold_text(getattr(deal, "notes", "") or "")
        res = _match(f"{prods} {notes}".strip())
        if res:
            return res

    # Terméknév-fallback: sima lakás-/jelzáloghitel → piaci konstrukció
    if any(k in pn for k in ("lakashitel", "jelzalog", "hitel")):
        return "piaci"
    return ""


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
                mapping.character_groups = old_mapping.character_groups
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
        field_data = self._prepare_field_data(
            deal, mapping_config, participant_override=participant_override,
            raw_opportunity=raw_data.get("_opportunity") or raw_data.get("Opportunity__c") or raw_data.get("opportunity") or raw_data,
            notices=notices,
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
                    products_enum = [ProductType.PIACI_HITEL]
                
                assembly = assembler.assemble(
                    master_pdf=template_pdf,
                    products=products_enum,
                    num_participants=len(deal.active_participants),
                    num_properties=len(deal.properties),
                    output_path=temp_assembled_path
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

            # 5b. Jogi nyilatkozatok automatikus kitöltése (AI + config hibrid).
            # A canonical mapping MELLÉ jön: a leképezetlen checkbox-okat kategóriák
            # szerint pipálja be (consent → true, decline → false, stb.).
            try:
                legal_count = self._fill_legal_declarations(
                    output_path, mapping_config, deal
                )
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
        Teljességellenőrzés a professional CompletenessChecker-rel.

        A kötelező mezők a mapping-ből származnak (a canonical_field hivatkozások),
        kiegészítve a strukturális ellenőrzésekkel (van adós, van ingatlan stb.).
        """
        required_fields = [
            "Contact.Loan_amount__c",
            "Contact.Loan_period__c",
            "Contact.Name",
            "Contact.Szuletesi_nev__c",
            "Contact.Mother_s_Name__c",
            "Contact.Birthdate",
            "Contact.Place_of_Birth__c",
            "Contact.Tax_ID__c",
            "Contact.MobilePhone",
        ]
        checker = CompletenessChecker(run_suspicious_checks=True)
        report = checker.check(deal, required_fields)
        return report

    def _prepare_field_data(self, deal: DealData, mapping: MappingConfig, participant_override: int | None = None, raw_opportunity: dict | None = None, notices: list | None = None) -> dict:
        """
        Kanonikus adatokból mező-értékpárok összeállítása.
        A mapping alapján a PDF mezőnevekre képezi le az értékeket.
        
        Speciális logika:
        - OTP nyomtatványoknál a -társ suffix a társigénylő mezőit jelöli
        - participant.role checkbox-ok nem kapnak szövegértéket
        - Az igénylő és társigénylő adatai külön canonical namespace-ben
        """
        field_data = {}

        # === Kanonikus mezők összeállítása ===
        borrower_data = {}
        co_borrower_data = {}

        borrowers = deal.borrowers
        co_borrowers = deal.co_borrowers

        borrower = borrowers[0] if borrowers else None
        co_borrower = co_borrowers[0] if co_borrowers else None

        # Account kontakt-pool: az ügyfélhez tartozik, de nem ügylet-szereplő
        # (minden hívási útvonalon fusson — validate, generate, field_values)
        if notices is not None and getattr(deal, "account_pool_extra", None):
            extra = deal.account_pool_extra
            msg = (
                f"Az Accounton {len(extra)} további kontakt szerepel, aki nem "
                f"résztvevője az ügyletnek: {', '.join(extra[:5])}"
                + (" …" if len(extra) > 5 else "")
            )
            if msg not in notices:
                notices.append(msg)

        for participant, target in (
            (borrower, "borrower"),
            (co_borrower, "co_borrower"),
        ):
            if participant is None:
                continue
            p_data = self._participant_to_dict(participant)

            if participant.address:
                p_data.update(self._address_to_dict(participant.address, "address"))
            if participant.mailing_address:
                p_data.update(self._address_to_dict(participant.mailing_address, "mailing_address"))
            elif participant.address:
                for mk, mv in self._address_to_dict(participant.address, "mailing_address").items():
                    if not p_data.get(mk):
                        p_data[mk] = mv

            # Jövedelem nélkül bevont szereplő: jövedelem-mezők ürítése
            p_data = self._apply_income_exclusion(participant, p_data, notices)

            if target == "borrower":
                borrower_data = p_data
            else:
                co_borrower_data = p_data

        # Per-applicant override: ha a dokumentumot az adott adóstárs
        # adataival kell kitölteni (pl. társigénylő lap N. példánya),
        # akkor a "sima" (nem -társ) mezők is a megfelelő adóstárs adatait
        # kapják — a borrower_data-t felülírjuk a co_borrower adataival.
        if participant_override is not None and participant_override < len(co_borrowers):
            override_participant = co_borrowers[participant_override]
            borrower_data = self._apply_income_exclusion(
                override_participant,
                dict(self._participant_to_dict(override_participant)),
                notices,
            )
            if override_participant.address:
                borrower_data.update(self._address_to_dict(override_participant.address, "address"))
            if override_participant.mailing_address:
                borrower_data.update(self._address_to_dict(override_participant.mailing_address, "mailing_address"))
            elif override_participant.address:
                for mk, mv in self._address_to_dict(override_participant.address, "mailing_address").items():
                    if not borrower_data.get(mk):
                        borrower_data[mk] = mv

        # Kezes (guarantor) és haszonélvező (beneficiary) mezők hozzáadása.
        # A mapping Contact.Mortgagor__c és Contact.Usufructuary__c canonical
        # mezőkhöz "Igen"-t vár ha létezik a szereplő.
        guarantors = deal.guarantors if hasattr(deal, "guarantors") else []
        beneficiaries = deal.beneficiaries if hasattr(deal, "beneficiaries") else []
        if guarantors:
            borrower_data["Contact.Mortgagor__c"] = "Igen"
        if beneficiaries:
            borrower_data["Contact.Usufructuary__c"] = "Igen"

        # Hiteladatok – a kanonikus modellből származnak (1c: új mezők)
        loan = deal.loan
        loan_amount_fmt = fmt_money(loan.loan_amount)
        loan_purpose = loan.loan_purpose or ""

        def _money_or_blank(v) -> str:
            return fmt_money(v) if v else ""

        term_years_str = (
            str(loan.loan_term_months // 12)
            if loan.loan_term_months and loan.loan_term_months % 12 == 0
            else ""
        )

        # Contact-level loan fields (merged into borrower_data below)
        contact_loan_data = {
            "Contact.Loan_amount__c": loan_amount_fmt,
            "Contact.Loan_period__c": str(loan.loan_term_months) if loan.loan_term_months else "",
            # Futamidő években (mindkét névváltozattal: szimpla és dupla alulvonás)
            "Contact.Term_in_year_c": term_years_str,
            "Contact.Term_in_year_c__c": term_years_str,
            "Contact.Interest_Period__c": loan.interest_period or "",
            "Contact.Loan_Purpose__c": loan_purpose,
            # H4: Contact.* mezők, amik a hitelből származnak.
            "Contact.Monthly_Payment_details__c": _money_or_blank(loan.monthly_payment),
            "Contact.State_Support__c": _money_or_blank(loan.afa_support),
        }
        # Merge Contact-level loan fields into borrower (and co-borrower) dicts
        # without clobbering existing Contact-level values with empty strings.
        for k, v in contact_loan_data.items():
            if v or not borrower_data.get(k):
                borrower_data[k] = v
            if co_borrower_data and (v or not co_borrower_data.get(k)):
                co_borrower_data[k] = v

        # Opportunity-level loan fields + loan.* canonical namespace (H4)
        loan_data = {
            "Opportunity.Hitel_sszeg__c": loan_amount_fmt,
            "Opportunity.Hitelc_l__c": loan_purpose,
            "Opportunity.Term_k__c": loan.product_name or "",
            "loan.loan_amount": loan_amount_fmt,
            "loan.loan_purpose": loan_purpose,
            "loan.monthly_payment": _money_or_blank(loan.monthly_payment),
            "loan.down_payment": _money_or_blank(loan.down_payment),
            "loan.purchase_price": _money_or_blank(loan.purchase_price),
            "loan.csok_amount": _money_or_blank(loan.csok_amount),
            "loan.afa_support": _money_or_blank(loan.afa_support),
            "loan.housing_savings": _money_or_blank(loan.housing_savings),
            "loan.product_type": loan.product_type or "",
            "loan.refinance_account": loan.refinance_account or "",
        }

        if raw_opportunity:
            for k, v in raw_opportunity.items():
                if v is not None and v != "" and not k.startswith("attributes"):
                    loan_data.setdefault(f"Opportunity.{k}", str(v))

        # Ellenőrizzük, hogy a mapping-ben van-e külön házszám mező
        mapping_fields_list = mapping.get("fields", []) if isinstance(mapping, dict) else getattr(mapping, "fields", [])
        has_separate_hazszam = any(
            (f.get("canonical_field") if isinstance(f, dict) else getattr(f, "canonical_field", ""))
            in ("Lead.Ingtalan_hazszam__c", "Lead.Ingatlan_hazszam__c")
            for f in mapping_fields_list
        )

        # Ingatlan adatok → Lead fields
        prop_data = {}
        for i, prop in enumerate(deal.properties):
            street_val = prop.address.street or ""
            hn_val = prop.address.house_number or ""
            if has_separate_hazszam or not hn_val or street_val.endswith(hn_val):
                kozterulet_val = street_val
            else:
                kozterulet_val = f"{street_val} {hn_val}".strip()

            pd = {
                "Lead.Ingatlan_irsz__c": prop.address.zip_code,
                "Lead.Ingatlan_telepules__c": prop.address.city,
                "Lead.Ingatlan_kozterulet_neve__c": kozterulet_val,
                "Lead.Ingtalan_hazszam__c": hn_val,
                "Lead.Ingatlan_hazszam__c": hn_val,
                "Lead.Ingatlan_megjegyzes__c": prop.parcel_number,
                "Lead.Ingatlan_alapterulet__c": str(prop.area_sqm) if prop.area_sqm else "",
                "Lead.Ingatlan_jellege__c": prop.property_type.value,
                "Lead.Estimated__c": fmt_money(prop.estimated_value),
                "Lead.Ingatlan_emelet__c": getattr(prop, "address", None).floor if getattr(prop, "address", None) else "",
                "Lead.Form_Type__c": getattr(prop, "form_type", "") or "",
                "Lead.Ingatlan_energetika__c": getattr(prop, "energetika", "") or "",
                "Lead.Ingatlan_terhek__c": getattr(prop, "terhek", "") or "",
                "Lead.Ingatlan_szerepe__c": getattr(prop, "ingatlan_szerepe", "") or "",
                "Lead.Term_in_year__c": str(getattr(prop, "epites_eve", "") or ""),
            }
            if i == 0:
                prop_data = pd
            else:
                # Második+ ingatlan: _N suffix azonos kanonikus kulcsokra
                suffix = f"_{i+1}"
                for k, v in pd.items():
                    prop_data[f"{k}{suffix}"] = v

        prop_data.setdefault("Lead.Tervezett_onero__c", _money_or_blank(loan.down_payment))
        prop_data.setdefault("Lead.Purchase_price__c", _money_or_blank(loan.purchase_price))
        prop_data.setdefault("Lead.Tervezett_CSOK_Plusz__c", _money_or_blank(loan.csok_amount))

        # Account.* névtér — az ügyfél Account rekordjának nyers mezői.
        account_data: dict = {}
        for k, v in (getattr(deal, "account", None) or {}).items():
            if v is None or v == "" or isinstance(v, (dict, list)):
                continue
            account_data[f"Account.{k}"] = str(v)

        # === Származtatott értékek: nem, konstrukció-gate, fordítások ===
        self._enrich_derived_values(
            borrower_data, co_borrower_data, loan_data, deal, notices
        )

        # Ha participant_override aktív, a -társ mezők is az adott adóstárs
        # (borrower_data-ba töltött) adatait kapják.
        effective_co_data = borrower_data if participant_override is not None else co_borrower_data

        # Karakter-csoport (digit box) tagmezők — ezeket a 1-karakteres
        # mezőket CSAK az expand_character_groups töltheti, különben a
        # teljes felbontatlan string (pl. "2020-03-01", "Havi torleszto...")
        # belekerülne az egyes számjegy-dobozokba.
        char_group_members: set[str] = set()
        for grp in (getattr(mapping, "character_groups", None) or []):
            members = grp.get("member_fields", []) if isinstance(grp, dict) else getattr(grp, "member_fields", [])
            if members:
                char_group_members.update(members)

        # === Mapping alkalmazása – SF-kulcs alapú routing ===
        for f in mapping.fields:
            if not f.canonical_field:
                continue

            pdf_name = f.pdf_field_name
            canonical = f.canonical_field

            if pdf_name in char_group_members:
                continue

            # Contact.Relation__c → checkbox, szöveggel nem töltjük
            if canonical == "Contact.Relation__c":
                continue

            # Radio-group checkbox opciók (___suffix, checkbox_group vagy fill_rule):
            # ezeket nem szövegként kell kitölteni, hanem a checkbox group
            # / fill_rule logika kezeli (match_value → "igen" + base name).
            if "___" in pdf_name or getattr(f, 'checkbox_group', None) or getattr(f, 'fill_rule', None):
                continue

            if canonical.startswith("Contact."):
                canon_tars = canonical.endswith("-társ")
                is_co_borrower = (
                    canon_tars or
                    "-társ" in pdf_name or
                    "társigénylő" in pdf_name.lower() or
                    "tarsigenylő" in pdf_name.lower() or
                    "SZA_IG_" in pdf_name and "-társ" in pdf_name
                )
                lookup_key = canonical[:-len("-társ")] if canon_tars else canonical
                source = effective_co_data if is_co_borrower else borrower_data
                if lookup_key in source and source[lookup_key]:
                    field_data[pdf_name] = source[lookup_key]

            elif canonical.startswith("Lead."):
                if canonical in prop_data and prop_data[canonical]:
                    field_data[pdf_name] = prop_data[canonical]

            elif canonical.startswith("Account."):
                if canonical in account_data and account_data[canonical]:
                    field_data[pdf_name] = account_data[canonical]

            elif canonical.startswith("Opportunity."):
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

            elif canonical.startswith("loan."):
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

        # === Checkbox group resolution ===
        # Group checkbox fields by (group_id, canonical, is_co_field), then tick
        # only the one whose match_value matches the SF picklist value.
        groups: dict[tuple, list] = {}
        matched_standard_canonicals: set[tuple[str, bool]] = set()
        for f in mapping.fields:
            cbg = getattr(f, 'checkbox_group', None)
            if isinstance(f, dict):
                cbg = f.get('checkbox_group')
            if not cbg:
                continue
            canonical = f.canonical_field if hasattr(f, 'canonical_field') else f.get('canonical_field')
            if not canonical:
                continue
            gid = cbg.get('group_id', '') if isinstance(cbg, dict) else ''
            if gid:
                fn_low = (f.pdf_field_name if hasattr(f, 'pdf_field_name') else f.get('pdf_field_name', '')).lower()
                is_co_fn = ('coborrower' in fn_low or '-társ' in fn_low or 'tarsigenyl' in fn_low or 'társigényl' in fn_low)
                gkey = (gid, canonical, is_co_fn)
                if gkey not in groups:
                    groups[gkey] = []
                groups[gkey].append((f, cbg))

        for (gid, canonical, is_co_fn), group_items in groups.items():
            sf_value = None
            if canonical.startswith(('Contact.', 'participant.')):
                canon_tars = canonical.endswith('-társ')
                lookup_key = canonical[:-len('-társ')] if canon_tars else canonical
                if is_co_fn or canon_tars:
                    sf_value = effective_co_data.get(lookup_key, '') if effective_co_data else ''
                else:
                    sf_value = borrower_data.get(lookup_key, '')
                    if not borrower_data and effective_co_data:
                        sf_value = effective_co_data.get(lookup_key, '')
            elif canonical.startswith('Lead.'):
                sf_value = prop_data.get(canonical, '')
            elif canonical.startswith('Account.'):
                sf_value = account_data.get(canonical, '')
            elif canonical.startswith('Opportunity.'):
                sf_value = loan_data.get(canonical, '')
            elif canonical.startswith('loan.'):
                sf_value = loan_data.get(canonical, '')

            if not sf_value:
                continue

            sf_value_lower = str(sf_value).strip().lower()

            def _opt_matches(option: str, value: str) -> bool:
                """Picklist-opció egyezés: pontos (kisbetűs), vagy az opció
                '/'-előtti szegmense egyezik az értékkel (pl. SF 'házas' ≈
                opció 'Házas/bejegyzett élettárs'). Ékezet- és kötőjel-független."""
                import unicodedata
                def _norm(s: str) -> str:
                    folded = "".join(
                        c for c in unicodedata.normalize("NFD", s.strip().lower())
                        if unicodedata.category(c) != "Mn"
                    )
                    return re.sub(r"[^a-z0-9/]+", " ", folded).strip()
                opt, val = _norm(option), _norm(value)
                if not opt or not val:
                    return False
                _ROLE_SYNONYMS = {
                    "adostars": "tarsigenylo",
                    "tarsigenylo": "adostars",
                    "ados": "igenylo",
                    "igenylo": "ados",
                }
                if opt == val or _ROLE_SYNONYMS.get(opt) == val:
                    return True
                if opt == "szemelyi igazolvany" and re.fullmatch(r"\d{6}[a-z]{2}", val.replace(" ", "")):
                    return True
                if opt.split("/", 1)[0].strip() == val:
                    return True
                if len(opt) >= 3 and len(val) >= 3:
                    return opt.startswith(val) or val.startswith(opt)
                return False

            best_item = None
            best_score = -1
            for f_item, cbg_item in group_items:
                pdf_name = f_item.pdf_field_name if hasattr(f_item, 'pdf_field_name') else f_item.get('pdf_field_name', '')
                match_val = (cbg_item.get('match_value') or cbg_item.get('option_value', '')) if isinstance(cbg_item, dict) else ''
                if _opt_matches(match_val, sf_value_lower):
                    score = min(len(_fold_text(match_val)), len(_fold_text(sf_value_lower)))
                    if score > best_score:
                        best_score = score
                        best_item = (f_item, cbg_item)
            if best_item is not None:
                matched_cbg = best_item[1]
                matched_mv = (matched_cbg.get('match_value') or matched_cbg.get('option_value', '')) if isinstance(matched_cbg, dict) else ''
                if "egyeb" not in _fold_text(matched_mv):
                    base_canon = canonical[:-len('-társ')] if canonical.endswith('-társ') else canonical
                    matched_standard_canonicals.add((base_canon, is_co_fn or canonical.endswith('-társ')))
            for f_item, cbg_item in group_items:
                pdf_name = f_item.pdf_field_name if hasattr(f_item, 'pdf_field_name') else f_item.get('pdf_field_name', '')
                if best_item is not None and f_item is best_item[0]:
                    if "___" in pdf_name:
                        base_name, export_val = pdf_name.split("___", 1)
                        field_data[base_name] = export_val
                        field_data[pdf_name] = 'igen'  # For completeness tracking
                    else:
                        field_data[pdf_name] = 'igen'
                else:
                    if "___" not in pdf_name:
                        field_data[pdf_name] = 'nem'

        # === Fill Rule engine ===
        all_data = {**borrower_data, **loan_data, **prop_data, **account_data}

        for f in mapping.fields:
            fill_rule = getattr(f, 'fill_rule', None)
            if isinstance(f, dict):
                fill_rule = f.get('fill_rule')
            if not fill_rule:
                continue

            pdf_name = f.pdf_field_name if hasattr(f, 'pdf_field_name') else f.get('pdf_field_name', '')
            canonical = f.canonical_field if hasattr(f, 'canonical_field') else f.get('canonical_field')
            canonical_value = None
            is_co = False
            lookup_key = canonical
            if canonical:
                canon_tars = canonical.endswith("-társ")
                lookup_key = canonical[:-len("-társ")] if canon_tars else canonical
                is_co = (
                    canon_tars
                    or "-társ" in pdf_name
                    or "társigénylő" in pdf_name.lower()
                    or "tarsigenylő" in pdf_name.lower()
                )
                if is_co:
                    canonical_value = effective_co_data.get(lookup_key) if effective_co_data else None
                else:
                    canonical_value = all_data.get(lookup_key) or borrower_data.get(lookup_key) or (effective_co_data.get(lookup_key) if effective_co_data else None)
            try:
                value = self._eval_fill_rule(fill_rule, all_data, borrower_data, effective_co_data, deal, canonical_value)
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
                        matched_standard_canonicals.add((lookup_key, is_co))
            except Exception as e:
                logger.debug(f"fill_rule error on {pdf_name}: {e}")

        # === Round-2 point-level fill engine ===
        if isinstance(mapping, dict):
            points = mapping.get("points") or []
        else:
            points = getattr(mapping, "points", None)
            if points is None:
                _to_dict = getattr(mapping, "to_dict", None)
                points = (_to_dict().get("points") if callable(_to_dict) else None) or []
        if points:
            from src.engine.fill_rules import RuleContext, RULE_REGISTRY, Point
            ctx = RuleContext(
                active_participants=deal.active_participants,
                products=getattr(deal, "products", []) or [],
                loan_purpose=loan.loan_purpose,
                product_name=loan.product_name,
                canonical_values={
                    **effective_co_data, **borrower_data, **loan_data, **prop_data, **account_data,
                    # "-társ" szuffixes canonicalok (2. igénylő szekció,
                    # generikus mezőnevek) az adóstárs értékeire mutatnak
                    **{f"{k}-társ": v for k, v in effective_co_data.items()
                       if isinstance(k, str) and k.startswith("Contact.")},
                },
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
                    actual_value = ctx.canonical_values.get(canonical_field) if canonical_field else None
                    matched_block = next(
                        (
                            b.get("block_id") for b in p_def.get("blocks", [])
                            if _values_match(b.get("block_id"), actual_value)
                        ),
                        None,
                    )
                    if matched_block is not None and canonical_field and "egyeb" not in _fold_text(matched_block):
                        canon_tars = canonical_field.endswith("-társ")
                        base_canon = canonical_field[:-len("-társ")] if canon_tars else canonical_field
                        matched_standard_canonicals.add((base_canon, canon_tars))
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
                    if pdf_field in field_data and field_data[pdf_field] != tick_value:
                        logger.debug(
                            "point %s felülírja a %s mezőt: %r -> %r",
                            point.point_id, pdf_field,
                            field_data[pdf_field], tick_value,
                        )
                    field_data[pdf_field] = tick_value

        # Ha egy picklisthez tartozó checkbox-csoportban egy standard (nem "Egyéb")
        # opció illeszkedett, akkor az ugyanarra a canonical_field-re mappelt
        # "Egyéb: _____" kiegészítő szövegmezőt ne töltsük ki a standard értékkel
        # (pl. Állampolgárság = "Magyar" pipálva → az "Egyéb állampolgárság" szövegmező üres marad).
        if matched_standard_canonicals:
            _EGYEB_PICKLIST_CANONICALS = {
                "Contact.Citizenship__c",
                "Contact.Employer_s_company_type__c",
                "Contact.Foglalkozas_tipusa__c",
                "Contact.Legal_Title_of_permanent_residence__c",
                "Contact.Marital_Status__c",
                "Contact.Highest_Educational_Qualification__c",
            }
            for f in mapping.fields:
                canonical = f.canonical_field if hasattr(f, "canonical_field") else f.get("canonical_field")
                if not canonical:
                    continue
                pdf_name = f.pdf_field_name if hasattr(f, "pdf_field_name") else f.get("pdf_field_name", "")
                if (
                    "___" in pdf_name
                    or getattr(f, "checkbox_group", None)
                    or getattr(f, "fill_rule", None)
                    or pdf_name in char_group_members
                ):
                    continue
                canon_tars = canonical.endswith("-társ")
                base_canon = canonical[:-len("-társ")] if canon_tars else canonical
                is_co = (
                    canon_tars
                    or "-társ" in pdf_name
                    or "társigénylő" in pdf_name.lower()
                    or "tarsigenylő" in pdf_name.lower()
                )
                if (base_canon, is_co) in matched_standard_canonicals:
                    ctx_snip = (getattr(f, "context_snippet", "") or "") if hasattr(f, "context_snippet") else (f.get("context_snippet", "") if isinstance(f, dict) else "")
                    if "egyeb" in _fold_text(pdf_name) or "egyeb" in _fold_text(ctx_snip) or base_canon in _EGYEB_PICKLIST_CANONICALS:
                        field_data.pop(pdf_name, None)

        # === C8: Apply per-field fragment transforms (date / digit splitting) ===
        for f in mapping.fields:
            fragment = getattr(f, 'fragment', None)
            if isinstance(f, dict):
                fragment = f.get('fragment')
            if not fragment:
                continue
            pdf_name = f.pdf_field_name if hasattr(f, 'pdf_field_name') else f.get('pdf_field_name', '')
            if pdf_name in field_data and field_data[pdf_name]:
                field_data[pdf_name] = _apply_fragment(field_data[pdf_name], fragment)

        # === M5: Character-split groups (digit boxes / comb text) ===
        char_groups = getattr(mapping, 'character_groups', None)
        if char_groups:
            for grp in char_groups:
                if isinstance(grp, dict):
                    canonical = grp.get('canonical_field') or ''
                    members = grp.get('member_fields') or []
                else:
                    canonical = getattr(grp, 'canonical_field', '') or ''
                    members = getattr(grp, 'member_fields', []) or []
                if not members or not canonical:
                    continue
                canon_tars = canonical.endswith('-társ')
                if canon_tars:
                    canonical = canonical[:-len('-társ')]
                if canonical.startswith('Contact.'):
                    all_co = all(
                        ('társ' in str(m).lower()) or ('coborrower' in str(m).lower())
                        for m in members
                    )
                    if all_co or canon_tars:
                        src = effective_co_data if effective_co_data else {}
                    else:
                        src = borrower_data if borrower_data else (effective_co_data or {})
                elif canonical.startswith('Lead.'):
                    src = prop_data
                elif canonical.startswith('Account.'):
                    src = account_data
                elif canonical.startswith(('Opportunity.', 'loan.')):
                    src = loan_data
                else:
                    src = all_data
                if isinstance(grp, dict):
                    grp_eff = {**grp, 'canonical_field': canonical}
                elif hasattr(grp, 'to_dict'):
                    grp_eff = {**grp.to_dict(), 'canonical_field': canonical}
                else:
                    grp_eff = {
                        'group_id': getattr(grp, 'group_id', ''),
                        'canonical_field': canonical,
                        'member_fields': members,
                        'direction': getattr(grp, 'direction', 'left_to_right'),
                        'separator': getattr(grp, 'separator', ''),
                    }
                field_data.update(expand_character_groups(src, [grp_eff]))

        return field_data

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
        # canonical field's deal value matches this option's match_value.
        match_value = rule.get("match_value")
        if match_value is not None and not rule_type:
            if canonical_value is not None:
                if str(canonical_value).strip().lower() == str(match_value).strip().lower():
                    return "igen"
            return None

        value = rule.get("value", "")

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
            sf_value = all_data.get(sf_field, "") or borrower.get(sf_field, "")
            if sf_value and str(sf_value).strip().lower() == str(match).strip().lower():
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

    def _apply_income_exclusion(self, participant, p_data: dict, notices: list | None) -> dict:
        """Jövedelem nélkül bevont szereplő (income_bearing=False) jövedelem-
        jellegű mezőit üríti, hogy azok ne kerüljenek a nyomtatványra.
        None (jelöletlen szerep) esetén nem nyúl az adatokhoz — ilyenkor
        nem veszítünk adatot, minden kitöltődik."""
        if getattr(participant, "income_bearing", None) is not False:
            return p_data
        stripped = sorted(k for k in _INCOME_CANONICAL_KEYS if p_data.get(k))
        for k in stripped:
            p_data[k] = ""
        if stripped:
            role_val = getattr(getattr(participant, "role", None), "value", "") or "résztvevő"
            msg = (
                f"{getattr(participant, 'name', '?')} ({role_val}) a Salesforce-ban "
                f"'jövedelem nélkül bevont' szerepben szerepel — "
                f"{len(stripped)} jövedelem-jellegű mező üresen marad a nyomtatványon."
            )
            logger.warning("   ⚠️ %s [%s]", msg, ", ".join(stripped))
            if notices is not None:
                notices.append(msg)
        return p_data

    def _enrich_derived_values(
        self,
        borrower_data: dict,
        co_borrower_data: dict,
        loan_data: dict,
        deal,
        notices: list | None = None,
    ) -> None:
        """Származtatott/fordított kanonikus értékek előállítása.

        1) Contact.Salutation: SF megszólítás → nyomtatvány nem-opció
           ("Férfi"/"Nő"). Ha a Salutation üres, magyar keresztnév-tábla
           alapján becslünk — ezt mindig notice-szal jelezzük, mert nem
           CRM-adat.
        2) Konstrukció-gate: a ``#piaci`` / ``#tamogatott`` szuffixes
           canonical kulcsok csak a hitel típusának megfelelő szekcióhoz
           kapnak értéket (3.1 államilag kamattámogatott / 3.2 piaci).
           A ``loan.product_type`` értéket a konstrukció-opció feliratára
           cseréljük, hogy a "Választott konstrukció típusa" checkbox
           bepipálható legyen.
        """
        # 1) Nem (Salutation fordítás + név-alapú fallback) és egyéb értékfordítások
        for data in (borrower_data, co_borrower_data):
            if not data:
                continue
            raw_sal = (data.get("Contact.Salutation") or "").strip()
            translated = _translate_value("Contact.Salutation", raw_sal) if raw_sal else ""
            guess = guess_gender(data.get("Contact.Name") or "")
            if translated in ("Férfi", "Nő"):
                if guess and guess != translated:
                    data["Contact.Salutation"] = guess
                    msg = (
                        f"{data.get('Contact.Name') or 'A szereplő'} neme név "
                        f"alapján javítva ({guess}) — a Salesforce Salutation mező "
                        f"({raw_sal} → {translated}) ellentmond a keresztnévnek."
                    )
                    logger.warning(msg)
                    if notices is not None and msg not in notices:
                        notices.append(msg)
                else:
                    data["Contact.Salutation"] = translated
            else:
                # Üres VAGY nem-gender Salutation (pl. "Dr.") → név-fallback
                if guess:
                    data["Contact.Salutation"] = guess
                    sal_note = (
                        f"a Salesforce Salutation mező üres"
                        if not raw_sal
                        else f"a Salesforce Salutation értéke ({raw_sal}) nem hordoz nemet"
                    )
                    msg = (
                        f"{data.get('Contact.Name') or 'A szereplő'} neme név "
                        f"alapján becsülve ({guess}) — {sal_note}, "
                        f"ellenőrizd a nyomtatványon."
                    )
                    logger.warning(msg)
                    if notices is not None and msg not in notices:
                        notices.append(msg)

            # Cégforma fordítása (pl. "Kft." → "Korlátolt felelősségű társaság")
            raw_comp = (data.get("Contact.Employer_s_company_type__c") or "").strip()
            if raw_comp:
                data["Contact.Employer_s_company_type__c"] = _translate_value(
                    "Contact.Employer_s_company_type__c", raw_comp
                )

            # Foglalkozás típusa fordítása (pl. "Alkalmazott vezető" → "Vezető alkalmazott (felső vagy középvezető)")
            raw_occ = (data.get("Contact.Foglalkozas_tipusa__c") or "").strip()
            if raw_occ:
                data["Contact.Foglalkozas_tipusa__c"] = _translate_value(
                    "Contact.Foglalkozas_tipusa__c", raw_occ
                )

        # 2) Konstrukció-gate
        loan = deal.loan
        construction = _classify_construction(loan, deal)
        if not construction:
            msg = (
                "A hitel konstrukciótípusa nem állapítható meg "
                "(Product_Type__c és terméknév alapján sem) — a 3.1/3.2 "
                "konstrukciós szekciók és a konstrukciótípus-checkbox "
                "üresen marad."
            )
            logger.warning(msg)
            if notices is not None and msg not in notices:
                notices.append(msg)
            return

        loan_data["loan.product_type"] = _CONSTRUCTION_OPTION_LABELS[construction]
        gates = []
        if construction in ("tamogatott", "mindketto"):
            gates.append("tamogatott")
        if construction in ("piaci", "mindketto"):
            gates.append("piaci")

        amount = loan_data.get("Opportunity.Hitel_sszeg__c", "")
        purpose = loan_data.get("Opportunity.Hitelc_l__c", "")
        insurance = loan_data.get("Opportunity.Insurance__c", "")
        term_years = borrower_data.get("Contact.Term_in_year_c", "")
        for gate in gates:
            if amount:
                loan_data[f"Opportunity.Hitel_sszeg__c#{gate}"] = amount
            if insurance:
                loan_data[f"Opportunity.Insurance__c#{gate}"] = insurance
            if purpose:
                loan_data[f"Opportunity.Hitelc_l__c#{gate}"] = purpose
                for data in (borrower_data, co_borrower_data):
                    if data:
                        data[f"Contact.Loan_Purpose__c#{gate}"] = purpose
            if term_years:
                for data in (borrower_data, co_borrower_data):
                    if data:
                        data[f"Contact.Term_in_year_c#{gate}"] = term_years
                        data[f"Contact.Term_in_year_c__c#{gate}"] = term_years

    def _participant_to_dict(self, p) -> dict:
        """Participant → kanonikus dict. Csak SF-ből származó adatok kerülnek ide."""
        _pid_clean = re.sub(r"[\s\-]", "", str(p.personal_id or ""))
        d = {
            "Contact.Name": p.name,
            "Contact.Szuletesi_nev__c": p.birth_name or "",
            "Contact.Mother_s_Name__c": p.mother_name or "",
            "Contact.Place_of_Birth__c": p.birth_place or "",
            "Contact.Birthdate": p.birth_date.strftime("%Y.%m.%d") if p.birth_date else "",
            "Contact.Birthdate_year": p.birth_date.strftime("%Y") if p.birth_date else "",
            "Contact.Birthdate_month": p.birth_date.strftime("%m") if p.birth_date else "",
            "Contact.Birthdate_day": p.birth_date.strftime("%d") if p.birth_date else "",
            # SF-ben a személyazonosító igazolvány száma (pl. 234567IJ) a
            # Contact.ID_Card_Number__c mezőben van (p.personal_id), míg a
            # lakcímkártya száma a Contact.Address_Card_Number__c-ben (p.id_card_number).
            # A régi 11 jegyű személyi azonosító szám (Contact.Personal_ID__c) csak akkor
            # töltendő, ha valóban 11 számjegyű személyi számról van szó (ne duplikálja a szem.ig. számot).
            "Contact.ID_Card_Number__c": p.personal_id or getattr(p, "id_card_number", "") or "",
            "Contact.Personal_ID__c": _pid_clean if re.fullmatch(r"\d{11}", _pid_clean) else "",
            "Contact.Address_Card_Number__c": getattr(p, "id_card_number", "") or "",
            "Contact.Tax_ID__c": p.tax_id or "",
            "Contact.MobilePhone": p.phone.replace("+", "").replace(" ", "").replace("-", "") if p.phone else "",
            "Contact.Phone": p.phone.replace("+", "").replace(" ", "").replace("-", "") if p.phone else "",
            "Contact.Email": p.email or "",
            "Contact.Name_of_employer__c": p.employer or "",
            "Contact.Average_monthly_net_income__c": fmt_money(p.monthly_income),
            "Contact.Relation__c": p.role.value,
            "participant.role": p.role.value,
            # SF-ből töltjük — ha nincs adat, üresen hagyjuk
            "Contact.Citizenship__c": getattr(p, "citizenship", "") or "",
            "Contact.Marital_Status__c": getattr(p, "marital_status", "") or "",
            "Contact.Highest_Educational_Qualification__c": getattr(p, "education", "") or "",
            "Contact.Income_type__c": getattr(p, "income_type", "") or "",
            "Contact.Dependents_count__c": str(p.dependents_count) if getattr(p, "dependents_count", None) is not None else "",
        }

        emp_type_val = getattr(p, "employment_type", "") or ""
        contract_end_val = getattr(p, "contract_end_date", "") or ""
        # Határozatlan idejű munkaviszony esetén a "Határozott munkaviszony vége" mező üresen marad
        if "hatarozatlan" in _fold_text(emp_type_val):
            contract_end_val = ""

        contract_start_val = getattr(p, "contract_start_date", "") or getattr(p, "current_employment_started", "") or ""
        curr_emp_start_val = getattr(p, "current_employment_started", "") or contract_start_val

        # Extra Contact mezők (SOQL-ból jönnek, lehetnek üresek)
        extra_mappings = {
            "Contact.Salutation": getattr(p, "salutation", "") or "",
            "Contact.Occupation__c": getattr(p, "occupation", "") or "",
            "Contact.Foglalkozas_tipusa__c": getattr(p, "foglalkozas_tipusa", "") or "",
            "Contact.Employment_Type_c__c": emp_type_val,
            "Contact.Legal_Title_of_permanent_residence__c": getattr(p, "legal_title", "") or "",
            "Contact.Jovairas_vallalasa__c": getattr(p, "jovairas_vallalasa", "") or "",
            "Contact.Loan_Purpose__c": getattr(p, "loan_purpose", "") or "",
            "Contact.Interest_Period__c": getattr(p, "interest_period", "") or "",
            "Contact.Other_monthly_income__c": fmt_money(getattr(p, "other_monthly_income", None)) if getattr(p, "other_monthly_income", None) else "",
            "Contact.Current_debt_Credit_limit__c": getattr(p, "current_debt_credit_limit", "") or "",
            "Contact.Current_employment_started__c": curr_emp_start_val,
            "Contact.Date_of_notification_for_residence__c": getattr(p, "date_of_notification_for_residence", "") or "",
            "Contact.Date_of_signature__c": getattr(p, "date_of_signature", "") or "",
            "Contact.Description": getattr(p, "description", "") or "",
            "Contact.MailingCity": getattr(p, "mailing_city", "") or "",
            "Contact.MailingPostalCode": getattr(p, "mailing_postal_code", "") or "",
            "Contact.MailingStreet": getattr(p, "mailing_street", "") or "",
            "Contact.OtherCity": getattr(p, "other_city", "") or "",
            "Contact.OtherStreet": getattr(p, "other_street", "") or "",
            "Contact.OtherCountry": getattr(p, "other_country", "") or "",
            "Contact.Account_holding_bank__c": getattr(p, "account_holding_bank", "") or "",
            "Contact.Cafeteria_bonus__c": fmt_money(getattr(p, "cafeteria_bonus", None)) if getattr(p, "cafeteria_bonus", None) else "",
            "Contact.Continuous_TB_2_years__c": getattr(p, "continuous_tb_2_years", "") or "",
            "Contact.Contract_end_date__c": contract_end_val,
            "Contact.Contract_start_date__c": contract_start_val,
            "Contact.Date_of_handover__c": getattr(p, "date_of_handover", "") or "",
            "Contact.Date_of_transfer__c": getattr(p, "date_of_transfer", "") or "",
            "Contact.Description_of_loan_purpose_comments__c": getattr(p, "description_of_loan_purpose", "") or "",
            "Contact.Divident__c": fmt_money(getattr(p, "divident", None)) if getattr(p, "divident", None) else "",
            "Contact.Employee_registration_c__c": getattr(p, "employee_registration", "") or "",
            "Contact.Employer_s_company_type__c": getattr(p, "employer_company_type", "") or "",
            "Contact.Income_from_self_owned_company__c": fmt_money(getattr(p, "income_from_self_owned", None)) if getattr(p, "income_from_self_owned", None) else "",
            "Contact.Industry__c": getattr(p, "industry", "") or "",
            "Contact.Monthly_Payment_details__c": getattr(p, "monthly_payment_details", "") or "",
            "Contact.Monthly_installment_payment__c": fmt_money(getattr(p, "monthly_installment_payment", None)) if getattr(p, "monthly_installment_payment", None) else "",
            "Contact.Mortgagor__c": getattr(p, "mortgagor", "") or "",
            "Contact.Numer_and_Age_of_Children__c": getattr(p, "numer_and_age_of_children", "") or "",
            "Contact.Other_income__c": fmt_money(getattr(p, "other_income", None)) if getattr(p, "other_income", None) else "",
            "Contact.Other_monthly_deductions__c": fmt_money(getattr(p, "other_monthly_deductions", None)) if getattr(p, "other_monthly_deductions", None) else "",
            "Contact.Own_resources__c": fmt_money(getattr(p, "own_resources", None)) if getattr(p, "own_resources", None) else "",
            "Contact.Property_50pct_ownership_details__c": getattr(p, "property_50pct_ownership_details", "") or "",
            "Contact.Property_encumbrances__c": getattr(p, "property_encumbrances", "") or "",
            "Contact.Property_value__c": getattr(p, "property_value", "") or "",
            "Contact.Self_employment_details__c": getattr(p, "self_employment_details", "") or "",
            "Contact.State_Support__c": getattr(p, "state_support", "") or "",
            "Contact.Town_or_possibly_the_exact_address__c": getattr(p, "town_or_possibly_exact_address", "") or "",
            "Contact.Usufructuary__c": getattr(p, "usufructuary", "") or "",
            "Contact.What_type_of_loan__c": getattr(p, "what_type_of_loan", "") or "",
            "Contact.Ownership_in_employer__c": getattr(p, "ownership_in_employer", "") or "",
            "Contact.Preferalt_bank__c": getattr(p, "preferalt_bank", "") or "",
            "Contact.Residence_Status__c": getattr(p, "residence_status", "") or "",
        }
        d.update(extra_mappings)

        # Dátum-fragmentek (#ev / #ho / #nap) minden releváns dátummezőhöz
        for date_canon in (
            "Contact.Date_of_notification_for_residence__c",
            "Contact.Current_employment_started__c",
            "Contact.Contract_start_date__c",
            "Contact.Contract_end_date__c",
        ):
            dy, dm, dd = _date_parts(d.get(date_canon, ""))
            d[f"{date_canon}#ev"] = dy
            d[f"{date_canon}#ho"] = dm
            d[f"{date_canon}#nap"] = dd

        # Gyermekek száma fragment (pl. "1, 5 éves" → "1")
        children_raw = d.get("Contact.Numer_and_Age_of_Children__c", "")
        m_ch = re.match(r"^\s*(\d+)", str(children_raw))
        d["Contact.Numer_and_Age_of_Children__c#count"] = (
            m_ch.group(1) if m_ch else d.get("Contact.Dependents_count__c", "")
        )
        return d


    def _address_to_dict(self, addr, prefix: str = "address") -> dict:
        """Address → kanonikus dict."""
        if prefix == "mailing_address":
            return {
                "Contact.MailingStreet": f"{addr.street} {addr.house_number}".strip(),
                "Contact.MailingCity": addr.city,
                "Contact.MailingPostalCode": addr.zip_code,
            }
        else:
            return {
                "Contact.OtherStreet": f"{addr.street} {addr.house_number}".strip(),
                "Contact.OtherCity": addr.city,
                "Contact.ZIP__c": addr.zip_code,
                "Contact.Permanent_address__c": addr.full_address,
            }

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
        """C6: kiegészíti a field_data-t a duplikált példányok ``_N`` mezőivel."""
        pdf_to_canonical = mapping.mapping_dict
        frag_by_base: dict[str, str] = {}
        for f in mapping.fields:
            base = f.pdf_field_name if hasattr(f, 'pdf_field_name') else f.get('pdf_field_name', '')
            frag = getattr(f, 'fragment', None) if not isinstance(f, dict) else f.get('fragment')
            if base and frag:
                frag_by_base[base] = frag

        result = dict(field_data)  # ne mutáljuk a hívó szótárát

        for im in instance_fields:
            instance_idx = im.instance - 1  # 0-based: 2. példány → idx 1
            data_dict = self._instance_data_dict(deal, im.role, instance_idx)
            if not data_dict:
                continue

            for base in im.base_fields:
                canonical = pdf_to_canonical.get(base)
                if not canonical:
                    continue
                raw = data_dict.get(canonical)
                if raw is None or raw == "":
                    continue
                frag = frag_by_base.get(base)
                if frag:
                    raw = _apply_fragment(raw, frag)
                    if raw is None or raw == "":
                        continue
                result[f"{base}_{im.instance}"] = raw

        if len(result) != len(field_data):
            logger.info(
                "   C6: %d átnevezett _N mezőhöz példányadat rendelve",
                len(result) - len(field_data),
            )
        return result

    def _instance_data_dict(self, deal: DealData, role: str, instance_idx: int) -> dict:
        """Visszaadja a megadott szerep + sorszámú példány kanonikus adatait."""
        if role == INSTANCE_ROLE_PROPERTY:
            return self._property_to_dict_indexed(deal, instance_idx)
        participant = self._instance_participant(deal, role, instance_idx)
        if participant is None:
            return {}
        data = self._participant_to_dict(participant)
        if participant.address:
            data.update(self._address_to_dict(participant.address, "address"))
            if not participant.mailing_address:
                for mk, mv in self._address_to_dict(participant.address, "mailing_address").items():
                    if not data.get(mk):
                        data[mk] = mv
        return data

    @staticmethod
    def _instance_participant(deal: DealData, role: str, idx: int):
        """Kiválasztja az idx-edik (0-based) aktív szereplőt a megadott szerepben."""
        if role == INSTANCE_ROLE_GUARANTOR:
            lst = deal.guarantors
        elif role == INSTANCE_ROLE_BENEFICIARY:
            lst = deal.get_participants_by_role(ParticipantRole.BENEFICIARY)
        else:  # INSTANCE_ROLE_CO_BORROWER (és ismeretlen → alapértelmezett)
            lst = deal.co_borrowers
        return lst[idx] if 0 <= idx < len(lst) else None

    def _property_to_dict_indexed(self, deal: DealData, idx: int) -> dict:
        """Az idx-edik (0-based) ingatlan adatai kanonikus dict-ként (Lead.*)."""
        if not (0 <= idx < len(deal.properties)):
            return {}
        prop = deal.properties[idx]
        return self._property_to_dict(prop)

    @staticmethod
    def _property_to_dict(prop) -> dict:
        """Egy ingatlan adatai kanonikus dict-ként (Lead.* namespace)."""
        street_val = prop.address.street or ""
        hn_val = prop.address.house_number or ""
        kozterulet_val = street_val if (not hn_val or street_val.endswith(hn_val)) else f"{street_val} {hn_val}".strip()
        return {
            "Lead.Ingatlan_irsz__c": prop.address.zip_code,
            "Lead.Ingatlan_telepules__c": prop.address.city,
            "Lead.Ingatlan_kozterulet_neve__c": kozterulet_val,
            "Lead.Ingtalan_hazszam__c": hn_val,
            "Lead.Ingatlan_hazszam__c": hn_val,
            "Lead.Ingatlan_megjegyzes__c": prop.parcel_number,
            "Lead.Ingatlan_alapterulet__c": str(prop.area_sqm) if prop.area_sqm else "",
            "Lead.Ingatlan_jellege__c": prop.property_type.value,
            "Lead.Estimated__c": fmt_money(prop.estimated_value),
            "Lead.Ingatlan_emelet__c": getattr(prop, "address", None).floor if getattr(prop, "address", None) else "",
            "Lead.Form_Type__c": getattr(prop, "form_type", "") or "",
            "Lead.Ingatlan_energetika__c": getattr(prop, "energetika", "") or "",
            "Lead.Ingatlan_terhek__c": getattr(prop, "terhek", "") or "",
            "Lead.Ingatlan_szerepe__c": getattr(prop, "ingatlan_szerepe", "") or "",
            "Lead.Term_in_year__c": str(getattr(prop, "epites_eve", "") or ""),
        }

    def _fill_legal_declarations(
        self,
        pdf_path: Path,
        mapping: MappingConfig,
        deal: DealData,
    ) -> int:
        """
        Jogi nyilatkozat checkbox-ok automatikus kitöltése a már
        kitöltött PDF-en (canonical mapping MELLÉ).

        Lépések:
        1. Kinyeri a canonical_field nélküli checkbox-okat a mapping-ből.
        2. LegalClassifier.classify_batch() → kategóriák.
        3. LegalClassifier.apply_defaults(deal) → true/false értékek.
        4. Beírja az értékeket a PDF AcroForm checkbox-aiba.

        Returns:
            Módosított checkbox mezők száma.
        """
        unmapped = extract_unmapped_checkboxes(mapping)
        if not unmapped:
            logger.debug("Nincsenek leképezetlen checkbox-ok – jogi lépés kihagyva.")
            return 0

        classifier = LegalClassifier()
        classified = classifier.classify_batch(unmapped)
        values = classifier.apply_defaults(classified, deal)

        if not values:
            logger.info(
                "   Jogi nyilatkozat: %d mező vizsgálva, de egyetlen sem "
                "illeszkedett egy 'always' vagy 'conditional' szabályhoz.",
                len(unmapped),
            )
            return 0

        modified = fill_legal_declarations_on_pdf(Path(pdf_path), values)
        if modified:
            logger.info(
                "   Jogi nyilatkozat: %d/%d checkbox beállítva "
                "(vizsgált leképezetlen: %d).",
                modified,
                len(values),
                len(unmapped),
            )
        else:
            logger.info(
                "   Jogi nyilatkozat: %d érték kalkulálva, de a PDF-ben nem "
                "található a hozzá tartozó AcroForm checkbox (mapping: %d "
                "leképezetlen checkbox).",
                len(values),
                len(unmapped),
            )
        return modified

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
                cli.info("   Hitel: %s Ft, %s hónap", fmt_money(deal.loan.loan_amount), deal.loan.loan_term_months)
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

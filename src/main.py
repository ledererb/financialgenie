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
from src.engine.document_assembler import DocumentAssembler, ProductType
from src.engine.pdf_filler import AcroFormFiller, OverlayFiller, TextPlacement
from src.engine.completeness_checker import CompletenessChecker, CompletenessStatus
from src.engine.role_instance_logic import RoleInstancePlanner, ParticipantRole as _RRole
from src.ai.field_recognizer import (
    FieldRecognizer,
    FieldType,
    MappingConfig,
    MappingConfidence,
    RecognizedField,
    print_mapping_summary,
)
from src.ai.legal_classifier import (
    LegalClassifier,
    extract_unmapped_checkboxes,
    fill_legal_declarations_on_pdf,
)
# TODO: cseréld le a FormFillerPipeline.run_for_deal() hívást a
# PipelineOrchestrator.run() hívására, hogy a teljes útvonal a modularizált
# 4-pass pipeline-on menjen át (pass1_data → pass2_mapping → pass3_fill →
# pass4_writeback). Jelenleg csak programmatic API-ként elérhető az import.
from src.pipeline import PipelineOrchestrator, PipelineResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mapping betöltés — támogatja a klasszikus MappingConfig formátumot ÉS
# az új `type: "overlay"` formátumot (placements[]) a lapos PDF-ekhez.
# ---------------------------------------------------------------------------

def _overlay_to_mapping_config(data: dict) -> MappingConfig:
    """
    Az új `{"type": "overlay", "placements": [...]}` formátum MappingConfig-ká
    alakítása úgy, hogy a pipeline lentebb változatlanul tudjon dolgozni vele.

    A placements-ben lévő `x`/`y`/`font_size`/`page_index` mezőket a
    RecognizedField.coordinates dict-be csomagoljuk. A `form_type="overlay"`
    jelzi a kitöltőnek, hogy a y koordináta már PDF (bottom-up) rendszerben van
    (ellentétben a régi `form_type="flat"`-tel, ahol fitz top-down y volt és
    egy y-offset transzformációt alkalmaztunk).
    """
    fields: list[RecognizedField] = []
    for p in data.get("placements", []):
        ftype_raw = p.get("field_type", "text")
        try:
            ft_enum = FieldType(ftype_raw)
        except ValueError:
            ft_enum = FieldType.TEXT
        try:
            conf = MappingConfidence(p.get("confidence", "medium"))
        except ValueError:
            conf = MappingConfidence.MEDIUM

        page_index = int(p.get("page_index", 0))
        fields.append(RecognizedField(
            pdf_field_name=p.get("pdf_field_name", f"flat_{page_index}_{len(fields)+1}"),
            label=p.get("label", ""),
            field_type=ft_enum,
            canonical_field=p.get("canonical_field"),
            confidence=conf,
            page_number=page_index + 1,  # MappingConfig 1-indexelt
            coordinates={
                "x": float(p.get("x", 0.0)),
                "y": float(p.get("y", 0.0)),
                "page_index": page_index,
                "font_size": float(p.get("font_size", 10.0) or 10.0),
                "overlay_field_type": ftype_raw,
            },
            notes=f"overlay field ({ftype_raw})",
        ))

    return MappingConfig(
        bank_name=data.get("bank_name", "OTP Bank"),
        form_name=data.get("form_name", data.get("pdf", "overlay")),
        form_type="overlay",
        fields=fields,
        notes=data.get("notes"),
    )


def _load_mapping_config(path: Path) -> MappingConfig:
    """
    Mapping JSON betöltése. Két formátumot támogat:

    1. Klasszikus MappingConfig (bank_name, form_name, form_type, fields).
    2. Új `type: "overlay"` formátum (pdf, placements[]) — automatikusan
       MappingConfig-ká alakítjuk `form_type="overlay"`-yel.

    Az utóbbi akkor használatos, amikor egy lapos PDF-hez a
    `scripts/analyze_flat_pdf.py` generált overlay koordinátákat.
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") == "overlay" and "placements" in data:
        logger.info("Overlay formátumú mapping felismerve: %s", path.name)
        return _overlay_to_mapping_config(data)

    return MappingConfig.from_dict(data)


def _resolve_template_for_mapping(mapping_path: Path) -> Path | None:
    """
    Egy mapping JSON-hez megkeresi a hozzá tartozó PDF sablont.

    Feloldási sorrend:
      1. `type: "overlay"` formátum `pdf` mezője (relatív út a projekt gyökérhöz).
      2. `form_name + ".pdf"` a projekt gyökérben.
      3. A mapping fájlnevéből levezetve (`<stem>_mapping` / `_overlay` suffix
         levágása) + ".pdf".
      4. `samples/` könyvtárban való keresés ugyanezekkel a nevekkel.

    Ha egyik sem található, `None`-nal térünk vissza (a hívó átugorja).
    """
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    candidates: list[Path] = []

    if data.get("type") == "overlay" and data.get("pdf"):
        candidates.append(PROJECT_ROOT / data["pdf"])

    form_name = (data.get("form_name") or "").strip()
    if form_name:
        candidates.append(PROJECT_ROOT / f"{form_name}.pdf")
        candidates.append((PROJECT_ROOT / "samples" / f"{form_name}.pdf"))

    stem = mapping_path.stem
    for suffix in ("_mapping", "_overlay"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    candidates.append(PROJECT_ROOT / f"{stem}.pdf")
    candidates.append(PROJECT_ROOT / "samples" / f"{stem}.pdf")

    for c in candidates:
        try:
            if c.exists() and c.is_file():
                return c
        except Exception:
            continue
    return None


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

    def run_for_deal(
        self,
        deal_id: str,
        template_pdf: Path,
        mapping_config: MappingConfig,
    ) -> dict:
        """
        Teljes pipeline futtatása egy ügylethez.
        
        Args:
            deal_id: Salesforce ügylet azonosító
            template_pdf: A kitöltendő PDF sablon
            mapping_config: Mező-leképezés konfiguráció
            
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
        field_data = self._prepare_field_data(deal, mapping_config)
        logger.info(f"   {len(field_data)} mező kitöltve")

        # 5. PDF kitöltés
        logger.info("📝 5. PDF kitöltés")
        try:
            assembler = DocumentAssembler()
            actual_template = template_pdf
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
                
                assembler.assemble(
                    master_pdf=template_pdf,
                    products=products_enum,
                    num_participants=len(deal.active_participants),
                    num_properties=len(deal.properties),
                    output_path=temp_assembled_path
                )
                actual_template = temp_assembled_path
                
            output_path = self._fill_pdf(actual_template, deal, field_data, mapping_config)
            result["output_path"] = str(output_path)
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

        # 6. Visszaírás Salesforce-ba
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
            "loan.loan_amount",
            "loan.loan_term_months",
            "participant.*.name",
            "participant.*.birth_name",
            "participant.*.mother_name",
            "participant.*.birth_date",
            "participant.*.birth_place",
            "participant.*.tax_id",
            "participant.*.phone",
            "property.*.parcel_number",
        ]
        checker = CompletenessChecker(run_suspicious_checks=True)
        report = checker.check(deal, required_fields)
        return report

    def _prepare_field_data(self, deal: DealData, mapping: MappingConfig) -> dict:
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
        # Igénylő (borrower) adatai → "borrower.*"
        # Társigénylő (co_borrower) adatai → "co_borrower.*"
        #
        # A szerep-routing a RoleInstancePlanner-rel történik (role-alapú),
        # nem pedig pozíció-alapú (i==0, i==1) indexeléssel. Így dokumentum-
        # agnosztikus marad a logika: bármelyik szereplő, akinek az első
        # BORROWER szerepe van, automatikusan igénylő lesz, a többi CO_BORROWER.
        borrower_data = {}
        co_borrower_data = {}

        borrowers = deal.borrowers
        co_borrowers = deal.co_borrowers

        borrower = borrowers[0] if borrowers else None
        co_borrower = co_borrowers[0] if co_borrowers else None

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
                p_data.update(self._address_to_dict(participant.address, "mailing_address"))

            if target == "borrower":
                borrower_data = p_data
            else:
                co_borrower_data = p_data

        # Hiteladatok – a kanonikus modellből származnak (1c: új mezők)
        loan = deal.loan
        loan_data = {
            "loan.loan_amount": f"{loan.loan_amount:,}".replace(",", " ") if loan.loan_amount else "",
            "loan.loan_term_months": str(loan.loan_term_months) if loan.loan_term_months else "",
            "loan.interest_period": loan.interest_period or "",
            "loan.loan_purpose": loan.loan_purpose or "",
            "loan.product_name": loan.product_name or "",
            "loan.product_type": loan.product_type or "",
            "loan.down_payment": f"{loan.down_payment:,}".replace(",", " ") if loan.down_payment else "",
            "loan.monthly_payment": f"{loan.monthly_payment:,}".replace(",", " ") if loan.monthly_payment else "",
            "loan.purchase_price": f"{loan.purchase_price:,}".replace(",", " ") if loan.purchase_price else "",
            "loan.csok_amount": f"{loan.csok_amount:,}".replace(",", " ") if loan.csok_amount else "",
            "loan.afa_support": f"{loan.afa_support:,}".replace(",", " ") if loan.afa_support else "",
            "loan.housing_savings": f"{loan.housing_savings:,}".replace(",", " ") if loan.housing_savings else "",
            "loan.refinance_account": loan.refinance_account or "",
        }

        # Ingatlan adatok
        prop_data = {}
        for i, prop in enumerate(deal.properties):
            pd = {
                "property.address.full_address": prop.address.full_address,
                "property.address.zip_code": prop.address.zip_code,
                "property.address.city": prop.address.city,
                "property.address.street": f"{prop.address.street} {prop.address.house_number}",
                "property.address.house_number": prop.address.house_number,
                "property.parcel_number": prop.parcel_number,
                "property.area_sqm": str(prop.area_sqm) if prop.area_sqm else "",
                "property.property_type": prop.property_type.value,
                "property.estimated_value": f"{prop.estimated_value:,}".replace(",", " ") if prop.estimated_value else "",
                "property.year_built": str(prop.year_built) if prop.year_built else "",
                "property.number_of_rooms": str(prop.number_of_rooms) if prop.number_of_rooms else "",
                "property.usage_type": "",
                "property.rental_fee": "",
                "property.rental_fee_eur": "",
                "property.contact_name": "",
                "property.contact_phone": "",
            }
            if i == 0:
                prop_data = pd

        # === Mapping alkalmazása – OTP-specifikus routing ===
        for f in mapping.fields:
            if not f.canonical_field:
                continue

            pdf_name = f.pdf_field_name
            canonical = f.canonical_field

            # Melyik participant-hoz tartozik a mező?
            # Figyelem: a mapping participant.* ÉS participant[0].* /
            # participant[1].* formátumban is adhatja meg a canonical nevet
            # (indexelt = társigénylő). Mindkettőt kezelni kell.
            # participant.role → checkbox, szöveggel nem töltjük.
            if canonical in ("participant.role", "participant[1].role"):
                continue
            if canonical.startswith("participant.") or canonical.startswith("participant["):
                # OTP convention: -társ suffix = társigénylő
                is_co_borrower = (
                    "-társ" in pdf_name or
                    "társigénylő" in pdf_name.lower() or
                    "tarsigenylő" in pdf_name.lower() or
                    "SZA_IG_" in pdf_name and "-társ" in pdf_name
                )
                source = co_borrower_data if is_co_borrower else borrower_data
                # A mapping participant[0]/participant[1] indexelt canonical
                # neveket használ, de a borrower_data/co_borrower_data dict
                # participant.* kulcsokat tartalmaz. Indexet levesszük a
                # lookup előtt: participant[1].name → participant.name
                source_key = canonical
                if canonical.startswith("participant[") and "." in canonical:
                    source_key = "participant." + canonical.split(".", 1)[1]
                if source_key in source and source[source_key]:
                    val = source[source_key]
                    # Fix 1: dátumkomponens szétválasztás (szül. év/hónap/nap,
                    # mióta_lakik_év/hónap). A teljes dátum helyett csak a
                    # pdf_name által kért komponenst írjuk be.
                    if source_key in (
                        "participant.birth_date",
                        "participant.residence_since",
                    ):
                        split = self._split_date_component(pdf_name, val)
                        if split is not None:
                            val = split
                    field_data[pdf_name] = val

            elif canonical.startswith("loan."):
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

            elif canonical.startswith("property."):
                if canonical in prop_data and prop_data[canonical]:
                    field_data[pdf_name] = prop_data[canonical]

            # --- Új canonical kategóriák az overlay / lapos PDF-ekhez ---
            # Ezeket a scripts/analyze_flat_pdf.py generálta nyilatkozatok
            # használják (legal.*, employer.*, region.*, branch.*).
            elif canonical.startswith("legal."):
                val = self._resolve_legal(canonical, borrower_data, co_borrower_data)
                if val:
                    field_data[pdf_name] = val

            elif canonical.startswith("employer."):
                # employer.* adatait a borrower participant adataiból
                # vezetjük le (munkaviszony/jövedelem nyilatkozatok).
                emp_map = {
                    "employer.name": borrower_data.get("participant.employer", ""),
                    "employer.tax_id": borrower_data.get("participant.employer_tax_id", ""),
                    "employer.company_registration_number": borrower_data.get("participant.business_tax_id", ""),
                }
                if emp_map.get(canonical):
                    field_data[pdf_name] = emp_map[canonical]

            elif canonical in ("region.name", "branch.name"):
                # Ezeket nem tároljuk a deal-ben; a kitöltésnél kihagyjuk.
                pass

        # === Fix 3: jövedelem mezők canonical_field=null esetre ===
        # A mappingben a SZA_IG_jövedelem* mezők canonical_field-je null,
        # de a participant.monthly_income rendelkezésre áll. Név-alapú
        # felismeréssel kitöltjük az első jövedelemsort (összeg/deviza/checkbox).
        self._fill_income_fields(field_data, mapping, borrower_data, co_borrower_data)

        # === Karakter-szintű (1 doboz = 1 karakter) mezők szétosztása ===
        # Az értékbecslés PDF-ben sok sorban kis (13.7–15.1 pt széles) dobozok
        # találhatók, amelyek egy-egy karaktert várnak. A fenti mapping-ciklus
        # viszont minden egyes dobozba beírja a TELJES canonical értéket (pl.
        # minden "Text Field 412..446" → "450 000"). Ez a lépés felismeri a
        # keskeny dobozokat (ugyanazon canonical + ugyanabban a sorban), és
        # karakterenként szétosztja köztük az értéket.
        self._distribute_char_fields(field_data, mapping)

        return field_data

    def _fill_income_fields(
        self,
        field_data: dict,
        mapping: MappingConfig,
        borrower_data: dict,
        co_borrower_data: dict,
    ) -> None:
        """
        Fix 3: a canonical_field nélküli jövedelem mezőket név-alapján
        felismerve kitölti a participant.monthly_income értékével.

        Kezelt minták (borrower + `-társ` co-borrower változat):
        - `...jövedelem_összeg1` → havi jövedelem formázott értéke
        - `...jövedelem_deviza1` → "HUF"
        - `...jövedelem1` (checkbox) → "Yes" (első jövedelemtípus bepipálva)
        - `...jövedelem_érk1` → "Yes" (bankszámlás érkezés, alapértelmezett)
        """
        # Regex-ek: a `1` indexet kötjük, hogy csak az első jövedelemsort
        # töltsük (több jövedelemforrás esetén a többi marad üresen).
        patterns = [
            # (regex, value-fn)
            (
                re.compile(r"jövedelem_összeg1(?:-társ)?$", re.IGNORECASE),
                lambda inc: inc,
            ),
            (
                re.compile(r"jövedelem_deviza1(?:-társ)?$", re.IGNORECASE),
                lambda inc: "HUF" if inc else "",
            ),
            (
                re.compile(r"jövedelem1(?:-társ)?$", re.IGNORECASE),
                lambda inc: "Yes" if inc else "",
            ),
            (
                re.compile(r"jövedelem_érk1(?:-társ)?$", re.IGNORECASE),
                lambda inc: "Yes" if inc else "",
            ),
        ]

        for f in mapping.fields:
            pdf_name = f.pdf_field_name
            if not pdf_name or pdf_name in field_data:
                continue
            is_co = "-társ" in pdf_name
            src = co_borrower_data if is_co else borrower_data
            income = src.get("participant.monthly_income", "")
            if not income:
                continue
            for pat, val_fn in patterns:
                if pat.search(pdf_name):
                    val = val_fn(income)
                    if val:
                        field_data[pdf_name] = val
                    break

    # --- karakter-szintű dobozok küszöbértékei ---
    CHAR_BOX_MAX_WIDTH = 20.0   # pt – ennél keskenyebb = 1 karakteres doboz
    CHAR_BOX_ROW_TOLERANCE = 5.0  # pt – y-irányú tűrés azonos sorként

    # Canonical-ek, ahol a dobozok CSAK számjegyet várnak (szeparátorok: szóköz,
    # pont, per, plusz, kötőjel – mind kihagyásra kerül).
    _DIGITS_ONLY_CANONICALS = frozenset({
        "participant.monthly_income",
        "participant.tax_id",
        "participant.dependents",
        "participant.birth_date",
        "participant.residence_since",
        "participant.phone",
        "loan.loan_amount",
        "loan.loan_term_months",
        "loan.down_payment",
        "loan.monthly_payment",
        "loan.purchase_price",
        "loan.csok_amount",
        "loan.afa_support",
        "loan.housing_savings",
        "property.estimated_value",
        "property.area_sqm",
        "property.year_built",
        "property.number_of_rooms",
        "property.parcel_number",
    })

    # Canonical-ek, ahol számjegy + betű is előfordulhat (személyi igazolvány,
    # rendszám stb.) – csak a szóközt és írásjeleket távolítjuk el.
    _ALNUM_CANONICALS = frozenset({
        "participant.personal_id",
        "participant.id_card_number",
    })

    def _explode_for_boxes(self, canonical: str, value: str) -> list[str]:
        """
        Egy canonical értéket karakterekre bont a dobozelrendezéshez.

        - Numerikus/dátum canonicaloknál (lásd _DIGITS_ONLY_CANONICALS) csak
          a számjegyeket tartjuk meg (szóköz, pont, per, plusz, kötőjel eldobva).
        - Az _ALNUM_CANONICALS-nél számjegy + betű, írásjelek nélkül.
        - Minden másnál csak a szóközt dobjuk el (betűk, írásjelek maradnak).
        """
        if value is None:
            return []
        s = str(value)
        if canonical in self._DIGITS_ONLY_CANONICALS:
            return [c for c in s if c.isdigit()]
        if canonical in self._ALNUM_CANONICALS:
            return [c for c in s if c.isalnum()]
        return [c for c in s if not c.isspace()]

    def _distribute_char_fields(
        self,
        field_data: dict,
        mapping: MappingConfig,
    ) -> None:
        """
        Karakter-szintű dobozok szétosztása.

        Felismeri a mapping-ben azokat a keskeny (< CHAR_BOX_MAX_WIDTH pt)
        szövegdobozokat, amelyek
          (a) ugyanahhoz a canonical_field-hez tartoznak, ÉS
          (b) ugyanabban a sorban helyezkednek el (azonos page, y ± tűrés),
        és karakterenként (1 doboz = 1 karakter) szétosztja köztük a canonical
        értékét. A széles mezők (pl. a teljes telefonszámot mutató doboz) nem
        keskenyek, így kimaradnak és megtartják a teljes értéket.

        Mindkét mapping-formátumot kezeli, mert a MappingConfig már
        egységesíti az OTP v5-ös (`coordinates: null`) és az értékbecslés
        (`coordinates: {x,y,width,height}`) formátumot: ahol nincsenek
        koordináták vagy a mező széles, ott egyszerűen nem történik semmi.
        """
        # 1) Keskeny, canonical-lal rendelkező mezők gyűjtése pozícióval.
        #    pdf_field_name -> (canonical, page, y, x)
        candidates: dict[str, tuple[str, int, float, float]] = {}
        for f in mapping.fields:
            if not f.canonical_field or not f.coordinates:
                continue
            coords = f.coordinates
            width = float(coords.get("width", 0) or 0)
            if width <= 0 or width >= self.CHAR_BOX_MAX_WIDTH:
                continue
            if (f.field_type or FieldType.TEXT) == FieldType.CHECKBOX:
                continue
            candidates[f.pdf_field_name] = (
                f.canonical_field,
                int(f.page_number or 1),
                float(coords.get("y", 0) or 0),
                float(coords.get("x", 0) or 0),
            )

        if not candidates:
            return

        # 2) (canonical, page) csoportonként sorokba (y-cluster) rendezés.
        by_page_canon: dict[tuple[str, int], list[str]] = {}
        for name, (canon, page, _y, _x) in candidates.items():
            by_page_canon.setdefault((canon, page), []).append(name)

        groups: dict[tuple, list[str]] = {}
        for (canon, page), names in by_page_canon.items():
            names.sort(key=lambda n: (candidates[n][2], candidates[n][3]))
            current_row_y: float | None = None
            row_idx = 0
            for n in names:
                y = candidates[n][2]
                if current_row_y is None or abs(y - current_row_y) > self.CHAR_BOX_ROW_TOLERANCE:
                    current_row_y = y
                    row_idx += 1
                groups.setdefault((canon, page, row_idx), []).append(n)

        # 3) Minden egynél több dobozos csoportban karakterenként szétosztunk.
        modified_rows = 0
        for (canon, _page, _row), names in groups.items():
            if len(names) <= 1:
                continue
            # A teljes érték: bármelyik dobozból kiolvasható (a mapping-ciklus
            # mindegyikbe ugyanazt írta). Ha egy sem szerepel field_data-ben
            # (pl. nincs adat), akkor nem nyúlunk hozzá.
            full_val = None
            for n in names:
                if n in field_data:
                    full_val = field_data[n]
                    break
            if full_val is None:
                continue

            chars = self._explode_for_boxes(canon, str(full_val))
            for i, n in enumerate(names):
                field_data[n] = chars[i] if i < len(chars) else ""
            modified_rows += 1

        if modified_rows:
            logger.info(
                "   🔤 Karakter-doboz szétosztás: %d sor, példa canonical: %s",
                modified_rows,
                next(iter(groups))[0],
            )

    def _resolve_legal(
        self,
        canonical: str,
        borrower_data: dict,
        co_borrower_data: dict,
    ) -> str:
        """
        Feloldja a legal.* canonical mezőket a nyilatkozatok overlay
        kitöltéséhez.

        - legal.signature_date → mai dátum (a kitöltés pillanatában)
        - legal.signature_place → üres (nem tudjuk automatikusan)
        - legal.signature_name  → igénylő neve
        """
        if canonical == "legal.signature_date":
            return datetime.now().strftime("%Y.%m.%d.")
        if canonical == "legal.signature_name":
            return borrower_data.get("participant.name", "")
        if canonical == "legal.signature_place":
            return ""
        return ""

    def _participant_to_dict(self, p) -> dict:
        """Participant → kanonikus dict."""
        # employment_type inferencia: ha van munkáltató, alkalmazott
        employment_type = p.employment_type or ("alkalmazott" if p.employer else "")
        d = {
            "participant.name": p.name,
            "participant.birth_name": p.birth_name or "",
            "participant.mother_name": p.mother_name or "",
            "participant.birth_place": p.birth_place or "",
            "participant.birth_date": p.birth_date.strftime("%Y.%m.%d") if p.birth_date else "",
            "participant.personal_id": p.personal_id or "",
            "participant.tax_id": p.tax_id or "",
            "participant.id_card_number": p.id_card_number or "",
            "participant.phone": p.phone or "",
            "participant.email": p.email or "",
            "participant.employer": p.employer or "",
            "participant.monthly_income": f"{p.monthly_income:,}".replace(",", " ") if p.monthly_income else "",
            "participant.role": p.role.value,
            "participant.gender": p.gender or "",
            "participant.citizenship": p.citizenship or "magyar",
            "participant.marital_status": p.marital_status or "",
            "participant.id_document_type": p.id_document_type or "",
            "participant.education": p.education or "",
            "participant.employment_type": employment_type,
            "participant.dependents": str(p.dependents) if p.dependents is not None else "",
            "participant.employee_count": str(p.employee_count) if p.employee_count is not None else "",
            "participant.nav_declaration": "Yes" if p.nav_declaration else "",
            "participant.mailing_address_same": "Yes" if p.mailing_address_same else "",
            "participant.residence_since": p.residence_since.strftime("%Y.%m.%d") if p.residence_since else "",
            "participant.business_name": p.business_name or "",
            "participant.business_tax_id": p.business_tax_id or "",
            "participant.employer_tax_id": p.employer_tax_id or "",
            "participant.kata_status": "",
        }
        return d

    def _address_to_dict(self, addr, prefix: str = "address") -> dict:
        """Address → kanonikus dict.

        Fix 5: a PDF külön mezőket tartalmazhat utca és házszám számára.
        A `street` mező tartalmazza a kombinált "utca házszám" formát,
        a `house_number` csak a házszámot. Ha a street eredetileg nem
        tartalmazza a házszámot, automatikusan hozzáfűzzük.
        """
        combined_street = addr.street
        if addr.house_number and addr.house_number not in (addr.street or ""):
            combined_street = f"{addr.street} {addr.house_number}".strip()
        return {
            f"participant.{prefix}.full_address": addr.full_address,
            f"participant.{prefix}.zip_code": addr.zip_code,
            f"participant.{prefix}.city": addr.city,
            f"participant.{prefix}.street": combined_street,
            f"participant.{prefix}.house_number": addr.house_number,
            f"participant.{prefix}.country": "Magyarország",
        }

    @staticmethod
    def _split_date_component(pdf_name: str, date_str: str) -> str | None:
        """
        Fix 1: ha a pdf_name egy dátum év/hónap/nap komponensére hivatkozik,
        visszaadja a dátum megfelelő részét; egyébként None.

        Támogatott pdf_name kulcsszavak:
          - év / year / _ev  → évszám (pl. "1978")
          - hónap / honap / month → hónap (pl. "12")
          - nap / day → nap (pl. "28")

        Támogatott dátumformátumok: "1978.12.28", "1978-12-28", "1978. 12. 28."
        """
        if not date_str:
            return None
        name = pdf_name.lower()
        parsed = None
        for fmt in ("%Y.%m.%d", "%Y-%m-%d", "%Y. %m. %d."):
            try:
                parsed = datetime.strptime(date_str.strip(), fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
        if "év" in name or "year" in name or name.endswith("_ev") or name.endswith("_ev."):
            return str(parsed.year)
        if "hónap" in name or "honap" in name or "month" in name:
            return str(parsed.month)
        if "nap" in name or "day" in name:
            return str(parsed.day)
        return None

    def _fill_pdf(
        self,
        template_pdf: Path,
        deal: DealData,
        field_data: dict,
        mapping: MappingConfig,
    ) -> Path:
        """
        PDF kitöltés a professional engine osztályokkal (AcroFormFiller / OverlayFiller).

        A korábbi inline pikepdf/PyMuPDF logika kiváltva – a konzolidált
        implementáció a src/engine/pdf_filler.py-ban él.
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"filled_{deal.deal_id}_{timestamp}.pdf"
        output_path = self.output_dir / output_filename

        if mapping.form_type == "acroform":
            filler = AcroFormFiller(flatten=False)
            # Korábban az AcroFormFiller canonical-kulcsú field_data-t várt,
            # és a mapping (pdf_field → canonical) alapján oldotta fel az
            # értékeket. Ez a megközelítés összeomlott, amikor több PDF mező
            # ugyanarra a canonical névre hivatkozott (pl. szül_év / szül_hónap
            # / szül_nap → participant.birth_date): a canonical_field_data
            # dict-ben csak egyetlen érték maradhatott, így mindhárom mező
            # ugyanazt a teljes dátumot kapta.
            #
            # Most identity mapping-et használunk: field_data már pdf_field_name
            # kulccsal van indexelve (a _prepare_field_data-ban), és a mapping
            # saját magára mutat. Így minden PDF mező a saját egyedi értékét
            # kapja (évként "1978", hónapként "12", napként "28").
            identity_mapping = {name: name for name in field_data}
            result = filler.fill(
                template_path=template_pdf,
                output_path=output_path,
                field_data=field_data,
                mapping=identity_mapping,
            )
            if not result.success:
                logger.warning(
                    "AcroForm kitöltés figyelmeztetések: %s", result.summary
                )
        else:
            # Overlay / lapos PDF: a mapping-ben lévő koordináták alapján
            # TextPlacement objektumokat építünk a OverlayFiller számára.
            #
            # Két overlay formátumot támogatunk:
            # - form_type == "overlay" (új, scripts/analyze_flat_pdf.py által
            #   generált): y már PDF (bottom-up) koordináta, font_size explicit,
            #   page_index 0-alapú a coordinates-ben.
            # - form_type == "flat" (régi FieldRecognizer overlay): fitz
            #   top-down y, alkalmazni kell a +height-3 y-offset transzformációt.
            #
            # A placements dict-et pdf_field_name-alapú egyedi kulccsal
            # indexeljük (`canonical__pdf_field_name`), hogy elkerüljük az
            # ütközést ha ugyanaz a canonical_field többször szerepel (pl. két
            # igénylő ugyanabban a nyilatkozatban).
            is_new_overlay = mapping.form_type == "overlay"
            placements: dict[str, TextPlacement] = {}
            field_values: dict[str, str] = {}

            for f in mapping.fields:
                if not (f.coordinates and f.canonical_field):
                    continue
                coords = f.coordinates
                overlay_field_type = coords.get("overlay_field_type") or (
                    f.field_type.value if f.field_type else "text"
                )

                if is_new_overlay:
                    x = float(coords.get("x", 0.0))
                    # y már PDF bottom-up baseline koordináta
                    y = float(coords.get("y", 0.0))
                    font_size = float(coords.get("font_size", 10.0) or 10.0)
                    page_index = int(
                        coords.get("page_index", max(int(f.page_number) - 1, 0))
                    )
                else:
                    # régi "flat": fitz top-down y → y-offset transzformáció
                    x = float(coords.get("x", 0.0))
                    y = float(coords.get("y", 0.0)) + float(
                        coords.get("height", 12.0) or 12.0
                    ) - 3
                    font_size = 10.0
                    page_index = max(int(f.page_number) - 1, 0)

                key = f"{f.canonical_field}__{f.pdf_field_name}"
                placements[key] = TextPlacement(
                    x=x, y=y, font_size=font_size, page_index=page_index,
                )

                raw_val = field_data.get(f.pdf_field_name)

                # Checkbox overlay: csak "X"-et rajzolunk ha truthy,
                # egyébként kihagyjuk.
                if overlay_field_type == "checkbox":
                    if raw_val and str(raw_val).strip().lower() in (
                        "1", "true", "yes", "igen", "x", "on",
                    ):
                        field_values[key] = "X"
                    continue

                if raw_val:
                    field_values[key] = str(raw_val)

            filler = OverlayFiller()
            result = filler.fill(
                template_path=template_pdf,
                output_path=output_path,
                field_data=field_values,
                mapping=placements,
            )
            if not result.success:
                logger.warning(
                    "Overlay kitöltés figyelmeztetések: %s", result.summary
                )

        return output_path

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


# Az összes ismert banki template PDF a working dir-ben, amelyeket a `--all`
# batch mód sorrendben feldolgoz. A `.gitignore` kizárja a PDF-eket, így ezek
# nem kerülnek commit-ra; itt csak a fájlnevek szerepelnek (nem az útvonalak).
ALL_TEMPLATE_PDFS: list[str] = [
    "OTP_Igenylesi_Dokumentumok_v5.pdf",
    "V_fuggelek.pdf",
    "partner_nyilatkozat.pdf",
    "hozzajarulo_nyilatkozat.pdf",
    "zold_lakashitel_nyilatkozat.pdf",
    "CSOK_afa_igazolas.pdf",
    "elozetes_ertekbecsles.pdf",
]


def _resolve_mapping_for_template(template_path: Path) -> Path | None:
    """
    Egy template PDF-hez megkeresi a hozzá tartozó mapping JSON-t.

    Feloldási sorrend:
      1. Pontos névegyezés: `{stem}_mapping.json`
      2. Pontos névegyezés: `{stem}_overlay.json`
      3. Prefix fallback: ha a template stem hosszabb, és van olyan mapping,
         amelynek stem-je (suffix nélkül) a template stem prefixe (pl.
         `zold_lakashitel_nyilatkozat.pdf` → `zold_lakashitel_overlay.json`).
      4. Fordított prefix fallback: ha a mapping stem hosszabb.

    Visszatér a mapping Path-jával vagy `None`-lal ha nincs találat.
    """
    mapping_dir = PROJECT_ROOT / "src" / "mapping"
    stem = template_path.stem

    # 1–2: pontos névegyezés (preferáljuk a _mapping.json-t)
    for suffix in ("_mapping.json", "_overlay.json"):
        candidate = mapping_dir / f"{stem}{suffix}"
        if candidate.exists():
            return candidate

    # 3–4: prefix fallback (mindkét irányban)
    def _stem_key(p: Path) -> str:
        n = p.name
        for suf in ("_mapping.json", "_overlay.json"):
            if n.endswith(suf):
                return n[: -len(suf)]
        return p.stem

    all_mappings = list(mapping_dir.glob("*_mapping.json")) + list(
        mapping_dir.glob("*_overlay.json")
    )
    for mp in all_mappings:
        k = _stem_key(mp)
        if k and (stem.startswith(k) or k.startswith(stem)):
            return mp

    return None


def _run_all_mappings(pipeline: "FormFillerPipeline", args) -> None:
    """
    Batch (`--all`) mód: végigmegy a `ALL_TEMPLATE_PDFS` listán, minden PDF-hez
    automatikusan kiválasztja a hozzá tartozó mapping-et (`_resolve_mapping_for_template`),
    és lefuttatja a pipeline-t. A végén összegző táblázatot ír ki.

    A kitöltőmotor (AcroFormFiller vs OverlayFiller) a mapping `type` mezője
    alapján dől el:
      - `"type": "overlay"` → OverlayFiller TextPlacement-ekkel
      - nincs `type` / bármi más → AcroFormFiller

    A `--deal` és `--salesforce` ugyanúgy működik, mint az egyszeres módban.
    Az output PDF-ek az `output/` mappába kerülnek.
    """
    # deal kiválasztása (ugyanaz a logika, mint a single módban)
    if args.deal:
        deal_id = args.deal
    else:
        deals = pipeline.sf_client.list_deals()
        if not deals:
            print("⚠️  Nincs elérhető ügylet")
            return
        deal_id = deals[0]["deal_id"]

    # Csak azokat a template-eket dolgozzuk fel, amelyek tényleg léteznek.
    templates: list[Path] = []
    missing: list[str] = []
    for name in ALL_TEMPLATE_PDFS:
        p = PROJECT_ROOT / name
        if p.exists():
            templates.append(p)
        else:
            missing.append(name)

    print(f"\n🚀 Batch mód: {len(templates)} template PDF feldolgozása (deal: {deal_id})")
    print("=" * 70)
    if missing:
        print(f"⚠️  Hiányzó PDF-ek (átugorva): {', '.join(missing)}")

    results = []
    for template in templates:
        # Mapping feloldása fájlnév-alapú egyezéssel
        mp = _resolve_mapping_for_template(template)

        if mp is None:
            print(f"\n⏭️  {template.name}: nincs hozzá mapping – átugorva")
            results.append({
                "template": template.name,
                "mapping": "(nincs)",
                "filler": "-",
                "status": "skipped",
                "reason": "no mapping",
                "output": None,
                "issues": 0,
            })
            continue

        # Mapping betöltése
        try:
            mapping = _load_mapping_config(mp)
        except Exception as e:
            print(f"\n⚠️  {template.name}: mapping betöltési hiba ({mp.name}): {e}")
            results.append({
                "template": template.name,
                "mapping": mp.name,
                "filler": "-",
                "status": "error",
                "reason": str(e),
                "output": None,
                "issues": 0,
            })
            continue

        filler = "overlay" if mapping.form_type == "overlay" else "acroform"
        print(f"\n📄 {template.name}  →  {mp.name}  ({filler})")

        try:
            res = pipeline.run_for_deal(deal_id, template, mapping)
        except Exception as e:
            print(f"   ✗ Kivétel: {e}")
            res = {
                "success": False,
                "issues": [f"kivétel: {e}"],
                "output_path": None,
            }

        results.append({
            "template": template.name,
            "mapping": mp.name,
            "filler": filler,
            "status": "ok" if res.get("success") else "failed",
            "reason": "",
            "output": res.get("output_path"),
            "issues": len(res.get("issues", [])),
        })

    # --- Összesítés ---
    print(f"\n{'=' * 70}")
    print(f"📊 Batch összesítés ({len(results)} template)")
    print(f"{'-' * 70}")
    print(f"{'Template':40} {'Filler':9} {'Státusz':8} {'Issues':6}")
    print(f"{'-' * 70}")
    for r in results:
        status = r["status"]
        mark = {"ok": "✅", "failed": "❌", "skipped": "⏭️", "error": "⚠️"}.get(
            status, "?"
        )
        filler = r.get("filler", "-")
        issues = r.get("issues", 0)
        print(
            f"{r['template'][:40]:40} {filler:9} {mark} {status:6} {issues:>6}"
        )
    ok = sum(1 for r in results if r["status"] == "ok")
    print(f"{'-' * 70}")
    print(f"Sikeres: {ok}/{len(results)}")
    # Output fájlok listája
    outputs = [r["output"] for r in results if r.get("output")]
    if outputs:
        print(f"\n📁 Generált PDF-ek (output/):")
        for o in outputs:
            try:
                print(f"   - {Path(o).name}")
            except Exception:
                print(f"   - {o}")
    print("=" * 70)


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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Batch mód: végigmegy az összes src/mapping/*.json fájlon, "
             "megkeresi a hozzájuk tartozó PDF-et és mindegyikre lefuttatja "
             "a pipeline-t (AcroForm vagy overlay a `type` mező alapján).",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("🧞 FinancialGenie – Banki nyomtatványkitöltő PoC")
    print("=" * 60)

    # Pipeline inicializálása
    # Dummy adatok generálása ha nincs
    dummy_dir = PROJECT_ROOT / "samples" / "dummy_data"
    if not dummy_dir.exists() or not list(dummy_dir.glob("*.json")):
        print("\n📦 Dummy adatok generálása...")
        sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
        from generate_dummy_data import generate_all_scenarios
        generate_all_scenarios(dummy_dir)

    # Salesforce kliens inicializálása
    if args.salesforce:
        from config import settings
        print("\n☁️  Kapcsolódás a Salesforce Sandbox-hoz...")
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

    # --- Batch mód: az összes mapping feldolgozása ---
    if args.all:
        _run_all_mappings(pipeline, args)
        return

    # 1. AI mezőfelismerés (ha kérték)
    mapping = None
    if args.recognize:
        print(f"\n🤖 AI mezőfelismerés: {args.recognize}")
        mapping = pipeline.run_ai_recognition(args.recognize)
        print_mapping_summary(mapping)

    # 2. Mapping betöltése
    if mapping is None:
        if args.mapping:
            mapping = _load_mapping_config(args.mapping)
        else:
            # Keressük az elérhető mapping-eket
            mapping_dir = PROJECT_ROOT / "src" / "mapping"
            # Előnyben részesítjük az *_overlay.json fájlokat, ha a template
            # flat PDF. Minden más *_mapping.json marad a klasszikus útvonal.
            mappings = list(mapping_dir.glob("*_mapping.json"))
            overlays = list(mapping_dir.glob("*_overlay.json"))
            if overlays:
                mapping = _load_mapping_config(overlays[0])
                print(f"\n📋 Overlay mapping betöltve: {overlays[0].name}")
            elif mappings:
                mapping = _load_mapping_config(mappings[0])
                print(f"\n📋 Mapping betöltve: {mappings[0].name}")
            else:
                # Üres mapping (közvetlen kanonikus nevek használata)
                mapping = MappingConfig(
                    bank_name="OTP Bank",
                    form_name="demo",
                    form_type="acroform",
                    notes="Nincs mapping – kanonikus mezőnevek használata",
                )
                print("\n📋 Nincs mapping konfiguráció – demo mód")

    # 3. Template PDF
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
            print("\n⚠️  Nincs elérhető PDF sablon. Futtasd előbb:")
            print("   python scripts/generate_sample_pdfs.py")
            print("\n   A pipeline a dummy adatok generálásáig fut.")
            # Csak dummy adatok és normalizálás demo
            deals = pipeline.sf_client.list_deals()
            if deals:
                deal_id = deals[0]["deal_id"]
                raw = pipeline.sf_client.get_deal(deal_id)
                deal = pipeline.normalizer.normalize_deal(raw)
                print(f"\n📊 Demo ügylet normalizálva:")
                print(f"   ID: {deal.deal_id}")
                print(f"   Hitel: {deal.loan.loan_amount:,} Ft, {deal.loan.loan_term_months} hónap")
                print(f"   Szereplők: {len(deal.active_participants)}")
                for p in deal.active_participants:
                    print(f"     - {p.name} ({p.role.value})")
                print(f"   Ingatlanok: {len(deal.properties)}")
                for prop in deal.properties:
                    print(f"     - {prop.address.full_address} ({prop.property_type.value})")
            return

    # 4. Pipeline futtatása
    print(f"\n🚀 Pipeline indítása...")
    print(f"   Sablon: {template_pdf}")
    print(f"   Forgatókönyv: {args.scenario}")

    # Ügylet kiválasztása
    if args.deal:
        deal_id = args.deal
    else:
        deals = pipeline.sf_client.list_deals()
        if not deals:
            print("⚠️  Nincs elérhető ügylet")
            return
        deal_id = deals[0]["deal_id"]
    result = pipeline.run_for_deal(deal_id, template_pdf, mapping)

    # Eredmény
    print(f"\n{'='*60}")
    if result["success"]:
        print(f"✅ Sikeres kitöltés!")
        print(f"   Output: {result['output_path']}")
    else:
        print(f"❌ Kitöltés sikertelen")

    if result["issues"]:
        print(f"\n⚠️  Problémák ({len(result['issues'])}):")
        for issue in result["issues"]:
            print(f"   - {issue}")

    print(f"\n⏱️  Timestamp: {result['timestamp']}")
    print("=" * 60)


if __name__ == "__main__":
    main()

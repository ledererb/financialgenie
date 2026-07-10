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
    ) -> dict:
        """
        Teljes pipeline futtatása egy ügylethez.
        
        Args:
            deal_id: Salesforce ügylet azonosító
            template_pdf: A kitöltendő PDF sablon
            mapping_config: Mező-leképezés konfiguráció (opcionális, automatikusan feloldódik)
            force_recreate_mapping: Mapping kényszerített újragenerálása (AI automatikus futtatása)
            
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
        field_data = self._prepare_field_data(deal, mapping_config)
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
        loan_amount_fmt = fmt_money(loan.loan_amount)
        loan_purpose = loan.loan_purpose or ""

        # FIX H4: a LoanDetails-ben normalizált mezők (monthly_payment,
        # down_payment, product_type, housing_savings, refinance_account,
        # purchase_price, csok_amount, afa_support) korábban SOHA nem
        # jutottak el a kitöltő-diktumba. Most három névtérben is
        # felszínre hozzuk őket, hogy a mapping-ek (akár a loan.*
        # kanonikus kulcsokat, akár a megfelelő Contact.*/Lead.* mezőket
        # használják) elérjék az értékeket.
        def _money_or_blank(v) -> str:
            # Az opcionális hitelmezőknél None → "" (a kitöltő a ""-t
            # "skip"-ként kezeli, ellentétben a fmt_money "0"-jával, ami
            # hamis pozitív kitöltést eredményezne).
            return fmt_money(v) if v else ""

        # Contact-level loan fields (merged into borrower_data below)
        contact_loan_data = {
            "Contact.Loan_amount__c": loan_amount_fmt,
            "Contact.Loan_period__c": str(loan.loan_term_months) if loan.loan_term_months else "",
            "Contact.Interest_Period__c": loan.interest_period or "",
            "Contact.Loan_Purpose__c": loan_purpose,
            # H4: Contact.* mezők, amik a hitelből származnak.
            "Contact.Monthly_Payment_details__c": _money_or_blank(loan.monthly_payment),
            "Contact.State_Support__c": _money_or_blank(loan.afa_support),
        }
        # Merge Contact-level loan fields into borrower (and co-borrower) dicts
        borrower_data.update(contact_loan_data)
        if co_borrower_data:
            co_borrower_data.update(contact_loan_data)

        # Opportunity-level loan fields + loan.* canonical namespace (H4)
        loan_data = {
            "Opportunity.Hitel_sszeg__c": loan_amount_fmt,
            "Opportunity.Hitelc_l__c": loan_purpose,
            "Opportunity.Term_k__c": loan.product_name or "",
            # H4: loan.* kanonikus kulcsok (az OTP mapping ezeket használja,
            # pl. LAHI_KTKA_Lakástakarék → loan.housing_savings).
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

        # Ingatlan adatok → Lead fields
        prop_data = {}
        for i, prop in enumerate(deal.properties):
            pd = {
                "Lead.Ingatlan_irsz__c": prop.address.zip_code,
                "Lead.Ingatlan_telepules__c": prop.address.city,
                "Lead.Ingatlan_kozterulet_neve__c": f"{prop.address.street} {prop.address.house_number}",
                "Lead.Ingtalan_hazszam__c": prop.address.house_number,
                "Lead.Ingatlan_megjegyzes__c": prop.parcel_number,
                "Lead.Ingatlan_alapterulet__c": str(prop.area_sqm) if prop.area_sqm else "",
                "Lead.Ingatlan_jellege__c": prop.property_type.value,
                "Lead.Estimated__c": fmt_money(prop.estimated_value),
            }
            if i == 0:
                prop_data = pd

        # H4: a hitelből származó Lead.* mezők (önerő, vételár, CSÖK) a
        # prop_data-ba kerülnek, mert a Lead.* routing onnan olvas.
        # Ezek ügylet-szintű, nem ingatlan-szintű mezők, de a mapping-ek
        # Lead.* kulcsokként hivatkoznak rájuk.
        prop_data.setdefault("Lead.Tervezett_onero__c", _money_or_blank(loan.down_payment))
        prop_data.setdefault("Lead.Purchase_price__c", _money_or_blank(loan.purchase_price))
        prop_data.setdefault("Lead.Tervezett_CSOK_Plusz__c", _money_or_blank(loan.csok_amount))

        # === Mapping alkalmazása – SF-kulcs alapú routing ===
        for f in mapping.fields:
            if not f.canonical_field:
                continue

            pdf_name = f.pdf_field_name
            canonical = f.canonical_field

            # Contact.Relation__c → checkbox, szöveggel nem töltjük
            if canonical == "Contact.Relation__c":
                continue

            if canonical.startswith("Contact."):
                # OTP convention: -társ suffix = társigénylő
                is_co_borrower = (
                    "-társ" in pdf_name or
                    "társigénylő" in pdf_name.lower() or
                    "tarsigenylő" in pdf_name.lower() or
                    "SZA_IG_" in pdf_name and "-társ" in pdf_name
                )
                source = co_borrower_data if is_co_borrower else borrower_data
                if canonical in source and source[canonical]:
                    field_data[pdf_name] = source[canonical]

            elif canonical.startswith("Lead."):
                if canonical in prop_data and prop_data[canonical]:
                    field_data[pdf_name] = prop_data[canonical]

            elif canonical.startswith("Opportunity."):
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

            elif canonical.startswith("loan."):
                # H4: loan.* canonical namespace (e.g. loan.housing_savings).
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

        # === Checkbox group resolution ===
        # Group checkbox fields by group_id, then tick only the one
        # whose match_value matches the SF picklist value.
        groups: dict[str, list] = {}
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
                if gid not in groups:
                    groups[gid] = []
                groups[gid].append((f, cbg))

        for gid, group_items in groups.items():
            # All items in a group share the same canonical field
            first_f, first_cbg = group_items[0]
            canonical = first_f.canonical_field if hasattr(first_f, 'canonical_field') else first_f.get('canonical_field', '')

            # Resolve the SF value — for Contact.* fields, check BOTH
            # borrower and co_borrower data, preferring the one that
            # matches the field name pattern (coborrower_ → co_borrower_data).
            sf_value = None
            if canonical.startswith('Contact.'):
                # Determine which participant's data to use based on field name pattern
                # Check if ALL fields in this group are "coborrower" fields
                all_co = all(
                    'coborrower' in (fi.pdf_field_name if hasattr(fi, 'pdf_field_name') else fi.get('pdf_field_name', '')).lower()
                    for fi, _ in group_items
                )
                if all_co and co_borrower_data:
                    sf_value = co_borrower_data.get(canonical, '')
                else:
                    sf_value = borrower_data.get(canonical, '')
                    # Fallback: if borrower doesn't have it, try co_borrower
                    if not sf_value and co_borrower_data:
                        sf_value = co_borrower_data.get(canonical, '')
            elif canonical.startswith('Lead.'):
                sf_value = prop_data.get(canonical, '')
            elif canonical.startswith('Opportunity.'):
                sf_value = loan_data.get(canonical, '')
            elif canonical.startswith('loan.'):
                sf_value = loan_data.get(canonical, '')

            if not sf_value:
                continue

            sf_value_lower = str(sf_value).strip().lower()
            for f_item, cbg_item in group_items:
                pdf_name = f_item.pdf_field_name if hasattr(f_item, 'pdf_field_name') else f_item.get('pdf_field_name', '')
                match_val = cbg_item.get('match_value', '') if isinstance(cbg_item, dict) else ''
                if match_val.strip().lower() == sf_value_lower:
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
        # fill_rule is a dict/object on each mapping field that provides
        # rule-based filling for fields without a canonical_field.
        # Supported types:
        #   {"type": "static", "value": "igen"}
        #   {"type": "per_participant", "value": "igen"}
        #   {"type": "conditional", "sf_field": "...", "match": "...", "value": "igen"}
        #   {"type": "role_based", "roles": ["adós", "adóstárs"], "value": "igen"}
        all_data = {**borrower_data, **loan_data, **prop_data}

        for f in mapping.fields:
            fill_rule = getattr(f, 'fill_rule', None)
            if isinstance(f, dict):
                fill_rule = f.get('fill_rule')
            if not fill_rule:
                continue

            pdf_name = f.pdf_field_name if hasattr(f, 'pdf_field_name') else f.get('pdf_field_name', '')
            try:
                value = self._eval_fill_rule(fill_rule, all_data, borrower_data, co_borrower_data, deal)
                if value is not None:
                    field_data[pdf_name] = value
            except Exception as e:
                logger.debug(f"fill_rule error on {pdf_name}: {e}")

        # === Round-2 point-level fill engine ===
        # A mapping deklarálhat "points"-okat (sorszámozott kérdések /
        # checkbox-blokkok) a mező-szintű szabályok fölött. Minden pont a
        # src/engine/fill_rules 7 szabálytípusának egyikét használja. A motor
        # keretfüggetlen: csak rule_type alapján ágazik el. A keretspecifikus
        # döntés (melyik pont melyik szabályt használja) konfiguráció, nem kód.
        # Ha egy pont tick-je felülír egy korábbi kanonikus értéket, azt
        # naplózzuk, hogy a mapping-konfliktusok napvilágra kerüljenek.
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
                canonical_values={**borrower_data, **co_borrower_data, **loan_data, **prop_data},
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

                # PLAN §5.3 — runtime active_block feloldás auto-generált
                # checkbox-csoport pontoknál (rule_type 3). A Contact tényleges
                # canonical értéke határozza meg, melyik opció (blokk) aktív.
                # Egyezik ha _source="auto_group" VAGY point_id "AUTO_" prefixű
                # (frontend-generált pontok is).
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

        # === C8: Apply per-field fragment transforms (date / digit splitting) ===
        # Some PDF fields carry only a PIECE of a larger logical value
        # (fragment = "year"|"month"|"day"|"digit:N"). The routing above stored
        # the FULL value under the pdf_field_name (the field's canonical points
        # at the parent value, e.g. Contact.Birthdate = "1985.05.12"); here we
        # extract just the requested fragment so each box receives only its
        # piece (year="1985", month="05", day="12") instead of the whole date.
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
        # A single canonical value (e.g. postal code "1123") is split
        # character-by-character across several one-char AcroForm boxes.
        # The mapping declares each group with its member field names; here we
        # resolve the group's canonical value from the right data namespace
        # (borrower vs co_borrower vs Lead vs loan) and expand it so each box
        # receives its single character. Mirrors the Contact.* routing above
        # (-társ suffix ⇒ co-borrower data).
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
                if canonical.startswith('Contact.'):
                    all_co = all(
                        ('társ' in str(m).lower()) or ('coborrower' in str(m).lower())
                        for m in members
                    )
                    if all_co and co_borrower_data:
                        src = co_borrower_data
                    else:
                        src = borrower_data if borrower_data else (co_borrower_data or {})
                elif canonical.startswith('Lead.'):
                    src = prop_data
                elif canonical.startswith(('Opportunity.', 'loan.')):
                    src = loan_data
                else:
                    src = all_data
                field_data.update(expand_character_groups(src, [grp]))

        return field_data

    def _eval_fill_rule(self, rule, all_data: dict, borrower: dict, co_borrower: dict, deal) -> str | None:
        """
        Evaluate a single fill_rule expression.
        
        rule can be:
        - dict: {"type": "static", "value": "igen"} (standard format per brief)
        - str: legacy format (backward compat) — treated as static value
        """
        if not rule:
            return None

        # Legacy string support (backward compat)
        if isinstance(rule, str):
            return rule

        if not isinstance(rule, dict):
            return None

        rule_type = rule.get("type", "")
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

    def _participant_to_dict(self, p) -> dict:
        """Participant → kanonikus dict. Csak SF-ből származó adatok kerülnek ide."""
        d = {
            "Contact.Name": p.name,
            "Contact.Szuletesi_nev__c": p.birth_name or "",
            "Contact.Mother_s_Name__c": p.mother_name or "",
            "Contact.Place_of_Birth__c": p.birth_place or "",
            "Contact.Birthdate": p.birth_date.strftime("%Y.%m.%d") if p.birth_date else "",
            "Contact.Birthdate_year": p.birth_date.strftime("%Y") if p.birth_date else "",
            "Contact.Birthdate_month": p.birth_date.strftime("%m") if p.birth_date else "",
            "Contact.Birthdate_day": p.birth_date.strftime("%d") if p.birth_date else "",
            "Contact.ID_Card_Number__c": p.personal_id or "",
            "Contact.Tax_ID__c": p.tax_id or "",
            "Contact.MobilePhone": p.phone.replace("+", "").replace(" ", "").replace("-", "") if p.phone else "",
            "Contact.Email": p.email or "",
            "Contact.Name_of_employer__c": p.employer or "",
            "Contact.Average_monthly_net_income__c": fmt_money(p.monthly_income),
            "Contact.Relation__c": p.role.value,
            # SF-ből töltjük — ha nincs adat, üresen hagyjuk
            "Contact.Citizenship__c": getattr(p, "citizenship", "") or "",
            "Contact.Marital_Status__c": getattr(p, "marital_status", "") or "",
            "Contact.Highest_Educational_Qualification__c": getattr(p, "education", "") or "",
            "Contact.Income_type__c": getattr(p, "income_type", "") or "",
            "Contact.Dependents_count__c": str(p.dependents_count) if getattr(p, "dependents_count", None) is not None else "",
        }
        return d


    def _address_to_dict(self, addr, prefix: str = "address") -> dict:
        """Address → kanonikus dict.

        A tartós (állandó) lakcím a ``Contact.ZIP__c`` mezőre képeződik,
        a levelezési cím viszont a ``Contact.MailingPostalCode`` mezőre.
        Korábban mindkettő ``Contact.ZIP__c``-t használt, ami akkor
        néma felülíráshoz vezetett, ha a két cím irányítószáma eltért.

        FIX M10 — a ``Contact.Permanent_address__c`` composite mező a teljes
        címet (köztük emelet/ajtó/település/irányítószám) várja. Korábban ez
        a mező sosem került kitöltésre, így az állandó lakcím emelet- és
        ajtó-adatai elvesztek. Most a ``Address.full_address``-ből (ami már
        tartalmazza ezeket) töltjük.
        """
        if prefix == "mailing_address":
            return {
                "Contact.MailingStreet": f"{addr.street} {addr.house_number}",
                "Contact.MailingCity": addr.city,
                "Contact.MailingPostalCode": addr.zip_code,
            }
        else:
            return {
                "Contact.OtherStreet": f"{addr.street} {addr.house_number}",
                "Contact.OtherCity": addr.city,
                "Contact.ZIP__c": addr.zip_code,
                # M10: a composite SF textarea mező a teljes címet kapja,
                # így az emelet/ajtó/kerület nem vész el.
                "Contact.Permanent_address__c": addr.full_address,
            }

    def _fill_pdf(
        self,
        template_pdf: Path,
        deal: DealData,
        field_data: dict,
        mapping: MappingConfig,
        instance_fields: list | None = None,
    ) -> tuple[Path, list[str], list[str]]:
        """
        PDF kitöltés a professional engine osztályokkal (AcroFormFiller / OverlayFiller).

        A korábbi inline pikepdf/PyMuPDF logika kiváltva – a konzolidált
        implementáció a src/engine/pdf_filler.py-ban él.

        Args:
            instance_fields: C6 — a DocumentAssembler által visszaadott
                ``InstanceFieldMap`` lista (duplikált oldalakon átnevezett
                ``_N`` mezők). Ha meg van adva, a kitöltés előtt a field_data
                kiegészül a megfelelő N-edik példány adataival (több adóstárs /
                ingatlan / kezes).

        Returns:
            ``(output_path, filled_fields, skipped_fields)`` — FIX M6: a korábban
            elvesző ``FillingResult.filled_fields`` / ``skipped_fields`` most
            visszajut a hívóhoz (``run_for_deal`` és az API), így a
            ``/api/pdf/fill`` válasza valódi listákat ad vissza az üres
            ``[]`` helyett.
        """
        # C6: ha darabolás történt, bővítsük ki a field_data-t az N-edik
        # példányok (adóstárs #2, ingatlan #2, …) adataival, amelyek az
        # átnevezett ``_N`` mezőkhöz tartoznak.
        if instance_fields:
            field_data = self._apply_instance_data(field_data, deal, mapping, instance_fields)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"filled_{deal.deal_id}_{timestamp}.pdf"
        output_path = self.output_dir / output_filename

        if mapping.form_type == "acroform":
            filler = AcroFormFiller(flatten=False)
            # AcroFormFiller dict[str,str] mapping-et vár (pdf_field_name → canonical).
            # A mapping.fields-ből kinyerjük ezt a leképezést.
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
        else:
            # Overlay / lapos PDF: a mapping-ben lévő koordináták alapján
            # TextPlacement objektumokat építünk a OverlayFiller számára.
            #
            # Az OverlayFiller canonical mező → érték formátumban várja a
            # field_data-t, és `placements[canonical] = TextPlacement` formátumban
            # a koordinátákat. A _prepare_field_data viszont pdf_field_name alapú
            # field_data-t ad vissza — ezért mindkettőt canonical-alapúvá
            # transzformáljuk a mapping.fields segítségével.
            placements: dict[str, TextPlacement] = {}
            canonical_field_data: dict[str, str] = {}
            
            # Load template PDF to determine page heights for Y axis inversion
            import fitz
            doc = fitz.open(str(template_pdf))
            
            for f in mapping.fields:
                if not (f.coordinates and f.canonical_field):
                    continue
                
                page_idx = max(int(f.page_number) - 1, 0)
                if page_idx < len(doc):
                    page_h = float(doc[page_idx].rect.height)
                else:
                    page_h = 842.0  # Fallback to standard A4 height
                
                coords = f.coordinates
                y_top_left = float(coords.get("y", 0.0))
                h = float(coords.get("height", 12.0) or 12.0)
                
                # Invert Y axis: ReportLab y0 is from bottom, mapping y0 is from top
                y_bottom_left = page_h - y_top_left - h + 3
                
                placements[f.canonical_field] = TextPlacement(
                    x=float(coords.get("x", 0.0)),
                    y=y_bottom_left,
                    font_size=10.0,
                    page_index=page_idx,
                )
                
                # pdf_field_name → value override
                if f.pdf_field_name in field_data:
                    val = field_data[f.pdf_field_name]
                    if val:
                        canonical_field_data[f.canonical_field] = val
            doc.close()

            filler = OverlayFiller()
            result = filler.fill(
                template_path=template_pdf,
                output_path=output_path,
                field_data=canonical_field_data,
                mapping=placements,
            )
            if not result.success:
                logger.warning(
                    "Overlay kitöltés figyelmeztetések: %s", result.summary
                )

        # FIX M6 — surface the actual filled/skipped field names so the API
        # does not return ghost-empty lists on a successful fill.
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

        A ``DocumentAssembler.assemble`` a többpéldányos szekciók
        (több adóstárs / ingatlan / kezes) duplikált oldalain átnevezte az
        AcroForm mezőket ``<base>_<instance>`` alakra, hogy függetlenek
        legyenek. Ez a metódus a mapping alapján feloldja, hogy melyik
        átnevezett mezőhöz melyik (N-edik) szereplő/ingatlan adata tartozik,
        és beírja a ``field_data``-ba.

        Példa: 3 adóstárs esetén az ``sza_ig_tarsigenylő_2``/``_3`` szekciókon
        a ``borrower_name_2`` → ``co_borrowers[1].Contact.Name``,
        ``borrower_name_3`` → ``co_borrowers[2].Contact.Name``.
        """
        pdf_to_canonical = mapping.mapping_dict
        # base pdf name -> fragment (C8: a többedik példány mezői is öröklik)
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
                # C8: fragment öröklése a base mezőről (pl. dátum-dobozok).
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
                data.update(self._address_to_dict(participant.address, "mailing_address"))
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
        """Egy ingatlan adatai kanonikus dict-ként (Lead.* namespace).

        Megegyezik a ``_prepare_field_data``-ban használt ingatlan-diktával,
        kivonva az ügylet-szintű hitel-mezőket (önerő/vételár/CSÖK), amelyek
        nem ingatlan-specifikusak.
        """
        return {
            "Lead.Ingatlan_irsz__c": prop.address.zip_code,
            "Lead.Ingatlan_telepules__c": prop.address.city,
            "Lead.Ingatlan_kozterulet_neve__c": f"{prop.address.street} {prop.address.house_number}",
            "Lead.Ingtalan_hazszam__c": prop.address.house_number,
            "Lead.Ingatlan_megjegyzes__c": prop.parcel_number,
            "Lead.Ingatlan_alapterulet__c": str(prop.area_sqm) if prop.area_sqm else "",
            "Lead.Ingatlan_jellege__c": prop.property_type.value,
            "Lead.Estimated__c": fmt_money(prop.estimated_value),
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

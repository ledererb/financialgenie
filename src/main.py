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
from src.ai.field_recognizer import FieldRecognizer, MappingConfig, print_mapping_summary

logger = logging.getLogger(__name__)


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
        loan_amount_fmt = f"{loan.loan_amount:,}".replace(",", " ") if loan.loan_amount else ""
        loan_purpose = loan.loan_purpose or ""

        # Contact-level loan fields (merged into borrower_data below)
        contact_loan_data = {
            "Contact.Loan_amount__c": loan_amount_fmt,
            "Contact.Loan_period__c": str(loan.loan_term_months) if loan.loan_term_months else "",
            "Contact.Interest_Period__c": loan.interest_period or "",
            "Contact.Loan_Purpose__c": loan_purpose,
        }
        # Merge Contact-level loan fields into borrower (and co-borrower) dicts
        borrower_data.update(contact_loan_data)
        if co_borrower_data:
            co_borrower_data.update(contact_loan_data)

        # Opportunity-level loan fields
        loan_data = {
            "Opportunity.Hitel_sszeg__c": loan_amount_fmt,
            "Opportunity.Hitelc_l__c": loan_purpose,
            "Opportunity.Term_k__c": loan.product_name or "",
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
                "Lead.Estimated__c": f"{prop.estimated_value:,}".replace(",", " ") if prop.estimated_value else "",
            }
            if i == 0:
                prop_data = pd

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

        # === Checkbox group resolution ===
        # Group checkbox fields by group_id, then tick only the one
        # whose match_value matches the SF picklist value.
        groups: dict[str, list] = {}
        for f in mapping.fields:
            cbg = getattr(f, 'checkbox_group', None) or (f.checkbox_group if hasattr(f, 'checkbox_group') else None)
            if not cbg:
                # Also check dict-style fields (loaded from JSON)
                if isinstance(f, dict):
                    cbg = f.get('checkbox_group')
                    if not cbg:
                        continue
                else:
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
            
            # Resolve the SF value
            sf_value = None
            if canonical.startswith('Contact.'):
                sf_value = borrower_data.get(canonical, '')
            elif canonical.startswith('Lead.'):
                sf_value = prop_data.get(canonical, '')
            elif canonical.startswith('Opportunity.'):
                sf_value = loan_data.get(canonical, '')
            
            if not sf_value:
                continue
            
            sf_value_lower = str(sf_value).strip().lower()
            for f_item, cbg_item in group_items:
                pdf_name = f_item.pdf_field_name if hasattr(f_item, 'pdf_field_name') else f_item.get('pdf_field_name', '')
                match_val = cbg_item.get('match_value', '') if isinstance(cbg_item, dict) else ''
                if match_val.strip().lower() == sf_value_lower:
                    field_data[pdf_name] = 'igen'
                else:
                    field_data[pdf_name] = 'nem'

        return field_data

    def _participant_to_dict(self, p) -> dict:
        """Participant → kanonikus dict. Csak SF-ből származó adatok kerülnek ide."""
        d = {
            "Contact.Name": p.name,
            "Contact.Szuletesi_nev__c": p.birth_name or "",
            "Contact.Mother_s_Name__c": p.mother_name or "",
            "Contact.Place_of_Birth__c": p.birth_place or "",
            "Contact.Birthdate": p.birth_date.strftime("%Y.%m.%d") if p.birth_date else "",
            "Contact.ID_Card_Number__c": p.personal_id or "",
            "Contact.Tax_ID__c": p.tax_id or "",
            "Contact.MobilePhone": p.phone or "",
            "Contact.Email": p.email or "",
            "Contact.Name_of_employer__c": p.employer or "",
            "Contact.Average_monthly_net_income__c": f"{p.monthly_income:,}".replace(",", " ") if p.monthly_income else "",
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
        """Address → kanonikus dict."""
        if prefix == "mailing_address":
            return {
                "Contact.MailingStreet": f"{addr.street} {addr.house_number}",
                "Contact.MailingCity": addr.city,
                "Contact.ZIP__c": addr.zip_code,
            }
        else:
            return {
                "Contact.OtherStreet": f"{addr.street} {addr.house_number}",
                "Contact.OtherCity": addr.city,
                "Contact.ZIP__c": addr.zip_code,
            }

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

        return output_path

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

    # 2. AI mezőfelismerés (ha kifejezetten kérték a --recognize argumentummal)
    mapping = None
    if args.recognize:
        print(f"\n🤖 AI mezőfelismerés futtatása: {args.recognize}")
        mapping = pipeline.run_ai_recognition(args.recognize)
        print_mapping_summary(mapping)

    # 3. Mapping betöltése vagy automatikus feloldása
    if mapping is None:
        if args.mapping:
            mapping = MappingConfig.load(args.mapping)
            print(f"\n📋 Kifejezett mapping betöltve: {args.mapping.name}")
        else:
            try:
                mapping = pipeline._resolve_mapping(template_pdf)
            except Exception as e:
                # Végső fallback ha valamiért teljesen meghiúsul az auto-felismerés
                print(f"\n⚠️ Nem sikerült feloldani a mappinget: {e}")
                mapping = MappingConfig(
                    bank_name="OTP Bank",
                    form_name="demo",
                    form_type="acroform",
                    notes="Nincs mapping – kanonikus mezőnevek használata",
                )
                print("\n📋 Nincs mapping konfiguráció – demo mód")

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

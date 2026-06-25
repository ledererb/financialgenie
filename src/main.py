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

        # 3. Teljességellenőrzés
        logger.info("✅ 3. Teljességellenőrzés")
        completeness = self._check_completeness(deal, mapping_config)
        if completeness["missing"]:
            logger.warning(f"   ⚠️ {len(completeness['missing'])} hiányzó mező")
            for field_name in completeness["missing"]:
                logger.warning(f"      - {field_name}")
            result["issues"].extend(
                [f"Hiányzó mező: {f}" for f in completeness["missing"]]
            )

        # 4. Mezőadatok összeállítása
        logger.info("📋 4. Mezőadatok összeállítása")
        field_data = self._prepare_field_data(deal, mapping_config)
        logger.info(f"   {len(field_data)} mező kitöltve")

        # 5. PDF kitöltés
        logger.info("📝 5. PDF kitöltés")
        try:
            output_path = self._fill_pdf(template_pdf, deal, field_data, mapping_config)
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

    def _check_completeness(self, deal: DealData, mapping: MappingConfig) -> dict:
        """Teljességellenőrzés: hiányzó mezők azonosítása."""
        missing = []

        # Kötelező hitel mezők
        if not deal.loan.loan_amount:
            missing.append("loan.loan_amount")
        if not deal.loan.loan_term_months:
            missing.append("loan.loan_term_months")

        # Kötelező szereplő mezők
        for i, p in enumerate(deal.active_participants):
            prefix = f"participant[{i}]"
            if not p.name:
                missing.append(f"{prefix}.name")
            if not p.birth_name:
                missing.append(f"{prefix}.birth_name")
            if not p.mother_name:
                missing.append(f"{prefix}.mother_name")
            if not p.birth_date:
                missing.append(f"{prefix}.birth_date")
            if not p.birth_place:
                missing.append(f"{prefix}.birth_place")
            if not p.tax_id:
                missing.append(f"{prefix}.tax_id")
            if not p.address:
                missing.append(f"{prefix}.address")
            if not p.phone:
                missing.append(f"{prefix}.phone")

        # Kötelező ingatlan mezők
        for i, prop in enumerate(deal.properties):
            prefix = f"property[{i}]"
            if not prop.parcel_number:
                missing.append(f"{prefix}.parcel_number")
            if not prop.address:
                missing.append(f"{prefix}.address")

        return {"missing": missing, "total_required": len(missing) + 10}

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
        borrower_data = {}
        co_borrower_data = {}

        for i, participant in enumerate(deal.active_participants):
            p_data = self._participant_to_dict(participant)

            if participant.address:
                p_data.update(self._address_to_dict(participant.address, "address"))
            if participant.mailing_address:
                p_data.update(self._address_to_dict(participant.mailing_address, "mailing_address"))
            elif participant.address:
                p_data.update(self._address_to_dict(participant.address, "mailing_address"))

            if i == 0:
                borrower_data = p_data
            elif i == 1:
                co_borrower_data = p_data

        # Hiteladatok
        loan = deal.loan
        loan_data = {
            "loan.loan_amount": f"{loan.loan_amount:,}".replace(",", " ") if loan.loan_amount else "",
            "loan.loan_term_months": str(loan.loan_term_months) if loan.loan_term_months else "",
            "loan.interest_period": loan.interest_period or "",
            "loan.loan_purpose": loan.loan_purpose or "",
            "loan.product_name": loan.product_name or "",
            "loan.product_type": loan.product_name or "",
            "loan.down_payment": f"{loan.down_payment:,}".replace(",", " ") if loan.down_payment else "",
            "loan.monthly_payment": f"{loan.monthly_payment:,}".replace(",", " ") if loan.monthly_payment else "",
            "loan.purchase_price": "",
            "loan.csok_amount": "",
            "loan.afa_support": "",
            "loan.housing_savings": "",
            "loan.refinance_account": "",
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

            # participant.role → checkbox, szöveggel nem töltjük
            if canonical == "participant.role":
                continue

            # Melyik participant-hoz tartozik a mező?
            if canonical.startswith("participant."):
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

            elif canonical.startswith("loan."):
                if canonical in loan_data and loan_data[canonical]:
                    field_data[pdf_name] = loan_data[canonical]

            elif canonical.startswith("property."):
                if canonical in prop_data and prop_data[canonical]:
                    field_data[pdf_name] = prop_data[canonical]

        return field_data

    def _participant_to_dict(self, p) -> dict:
        """Participant → kanonikus dict."""
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
            "participant.gender": "",
            "participant.citizenship": "magyar",
            "participant.marital_status": "",
            "participant.id_document_type": "",
            "participant.education": "",
            "participant.employment_type": "",
            "participant.dependents": "",
            "participant.employee_count": "",
            "participant.nav_declaration": "",
            "participant.mailing_address_same": "",
            "participant.residence_since": "",
            "participant.business_name": "",
            "participant.business_tax_id": "",
            "participant.employer_tax_id": "",
            "participant.kata_status": "",
        }
        return d

    def _address_to_dict(self, addr, prefix: str = "address") -> dict:
        """Address → kanonikus dict."""
        return {
            f"participant.{prefix}.full_address": addr.full_address,
            f"participant.{prefix}.zip_code": addr.zip_code,
            f"participant.{prefix}.city": addr.city,
            f"participant.{prefix}.street": f"{addr.street} {addr.house_number}",
            f"participant.{prefix}.house_number": addr.house_number,
            f"participant.{prefix}.country": "Magyarország",
        }

    def _fill_pdf(
        self,
        template_pdf: Path,
        deal: DealData,
        field_data: dict,
        mapping: MappingConfig,
    ) -> Path:
        """
        PDF kitöltés a mapping típusa szerint.
        
        AcroForm: közvetlen mezőfeltöltés pikepdf-el
        Flat/overlay: koordináta-alapú szöveg elhelyezés
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_filename = f"filled_{deal.deal_id}_{timestamp}.pdf"
        output_path = self.output_dir / output_filename

        if mapping.form_type == "acroform":
            self._fill_acroform(template_pdf, output_path, field_data)
        else:
            self._fill_overlay(template_pdf, output_path, field_data, mapping)

        return output_path

    def _fill_acroform(self, template: Path, output: Path, field_data: dict):
        """AcroForm PDF kitöltés pikepdf-el."""
        import pikepdf

        with pikepdf.open(template) as pdf:
            if "/AcroForm" in pdf.Root:
                acroform = pdf.Root["/AcroForm"]
                if "/Fields" in acroform:
                    for field_ref in acroform["/Fields"]:
                        try:
                            f = field_ref
                            field_name = str(f.get("/T", ""))
                            if field_name in field_data:
                                value = field_data[field_name]
                                f["/V"] = pikepdf.String(value)
                                # Appearance flag – a viewer újrarajzolja
                                if "/AP" in f:
                                    del f["/AP"]
                                logger.debug(f"   Mező kitöltve: {field_name} = {value[:50]}")
                        except Exception as e:
                            logger.debug(f"   Mező kitöltési hiba: {e}")

            pdf.save(output)

    def _fill_overlay(
        self, template: Path, output: Path, field_data: dict, mapping: MappingConfig
    ):
        """Lapos PDF overlay kitöltés – szövegréteg ráhelyezése."""
        import fitz  # PyMuPDF

        doc = fitz.open(str(template))

        for f in mapping.fields:
            if f.coordinates and f.pdf_field_name and f.pdf_field_name in field_data:
                value = field_data[f.pdf_field_name]
                if not value:
                    continue

                page_idx = f.page_number - 1
                if 0 <= page_idx < len(doc):
                    page = doc[page_idx]
                    coords = f.coordinates
                    point = fitz.Point(coords["x"], coords["y"] + coords.get("height", 12))
                    page.insert_text(
                        point,
                        str(value),
                        fontsize=10,
                        fontname="helv",
                        color=(0, 0, 0),
                    )
                    logger.debug(f"   Overlay: {f.label} = {str(value)[:50]} @ ({coords['x']}, {coords['y']})")

        doc.save(str(output))
        doc.close()

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

    # 1. AI mezőfelismerés (ha kérték)
    mapping = None
    if args.recognize:
        print(f"\n🤖 AI mezőfelismerés: {args.recognize}")
        mapping = pipeline.run_ai_recognition(args.recognize)
        print_mapping_summary(mapping)

    # 2. Mapping betöltése
    if mapping is None:
        if args.mapping:
            mapping = MappingConfig.load(args.mapping)
        else:
            # Keressük az elérhető mapping-eket
            mapping_dir = PROJECT_ROOT / "src" / "mapping"
            mappings = list(mapping_dir.glob("*_mapping.json"))
            if mappings:
                mapping = MappingConfig.load(mappings[0])
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

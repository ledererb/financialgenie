"""
Éles OTP PDF kitöltési teszt – a valódi 97 oldalas nyomtatvány
kitöltése minta ügyfél adatokkal.
"""
import json
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

from src.models.canonical_model import (
    DealData, LoanDetails, Participant, ParticipantRole,
    Property, PropertyType, Address,
)
from src.ai.field_recognizer import MappingConfig
from src.main import FormFillerPipeline

PROJECT_ROOT = Path(__file__).parent.parent


def create_sample_deal() -> DealData:
    """Valósághű magyar minta ügylet OTP jelzáloghitelhez."""
    return DealData(
        deal_id="OTP-2026-001234",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=35_000_000,
            loan_term_months=240,
            interest_period="5 éves fix",
            loan_purpose="használt lakás vásárlás",
            product_name="OTP Lakáshitel",
            down_payment=15_000_000,
            monthly_payment=198_500,
        ),
        participants=[
            # Igénylő (adós)
            Participant(
                role=ParticipantRole.BORROWER,
                name="Kovács István",
                birth_name="Kovács István",
                mother_name="Nagy Mária",
                birth_place="Budapest",
                birth_date=date(1985, 3, 15),
                personal_id="1 850315 0123",
                tax_id="8123456789",
                id_card_number="123456AB",
                address=Address(
                    zip_code="1132",
                    city="Budapest",
                    street="Visegrádi utca",
                    house_number="14",
                    floor="3",
                    door="12",
                ),
                mailing_address=Address(
                    zip_code="1132",
                    city="Budapest",
                    street="Visegrádi utca",
                    house_number="14",
                    floor="3",
                    door="12",
                ),
                phone="+36 30 123 4567",
                email="kovacs.istvan@email.hu",
                employer="Teszt Informatika Kft.",
                monthly_income=650_000,
            ),
            # Társigénylő (adóstárs)
            Participant(
                role=ParticipantRole.CO_BORROWER,
                name="Kovács-Szabó Anna",
                birth_name="Szabó Anna",
                mother_name="Tóth Katalin",
                birth_place="Debrecen",
                birth_date=date(1988, 7, 22),
                personal_id="2 880722 0456",
                tax_id="8234567890",
                id_card_number="654321CD",
                address=Address(
                    zip_code="1132",
                    city="Budapest",
                    street="Visegrádi utca",
                    house_number="14",
                    floor="3",
                    door="12",
                ),
                phone="+36 20 987 6543",
                email="szabo.anna@email.hu",
                employer="Magyar Posta Zrt.",
                monthly_income=480_000,
            ),
        ],
        properties=[
            Property(
                address=Address(
                    zip_code="1065",
                    city="Budapest",
                    street="Nagymező utca",
                    house_number="22",
                    floor="2",
                    door="5",
                ),
                parcel_number="24567/1/A/23",
                area_sqm=68.5,
                property_type=PropertyType.APARTMENT,
                estimated_value=50_000_000,
                year_built=1935,
                number_of_rooms=3,
            ),
        ],
        created_at=date.today(),
        notes="Teszt kitöltés – éles OTP nyomtatvány PoC",
    )


def prepare_extended_field_data(deal: DealData, mapping: MappingConfig) -> dict:
    """
    Kibővített mezőérték-összeállítás a 374 leképezett OTP mezőhöz.
    A pipeline _prepare_field_data-ját kiegészíti az új kanonikus mezőkkel.
    """
    field_data = {}

    # === Kanonikus → Érték mapping ===
    canonical_to_value = {}

    # --- Hitel adatok ---
    loan = deal.loan
    canonical_to_value.update({
        "loan.loan_amount": f"{loan.loan_amount:,}".replace(",", " "),
        "loan.loan_term_months": str(loan.loan_term_months),
        "loan.interest_period": loan.interest_period or "",
        "loan.loan_purpose": loan.loan_purpose or "",
        "loan.product_name": loan.product_name or "",
        "loan.product_type": loan.product_name or "",
        "loan.down_payment": f"{loan.down_payment:,}".replace(",", " ") if loan.down_payment else "",
        "loan.monthly_payment": f"{loan.monthly_payment:,}".replace(",", " ") if loan.monthly_payment else "",
        "loan.purchase_price": f"{50_000_000:,}".replace(",", " "),  # = ingatlan becsült érték
        "loan.csok_amount": "",
        "loan.afa_support": "",
        "loan.housing_savings": "",
        "loan.refinance_account": "",
    })

    # --- Személyi adatok (minden aktív szereplőre) ---
    for i, p in enumerate(deal.active_participants):
        # Születési dátum szétbontása
        b_year = str(p.birth_date.year) if p.birth_date else ""
        b_month = f"{p.birth_date.month:02d}" if p.birth_date else ""
        b_day = f"{p.birth_date.day:02d}" if p.birth_date else ""

        p_data = {
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
            # Kibővített mezők
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

        # Állandó lakcím
        if p.address:
            p_data.update({
                "participant.address.full_address": p.address.full_address,
                "participant.address.zip_code": p.address.zip_code,
                "participant.address.city": p.address.city,
                "participant.address.street": f"{p.address.street} {p.address.house_number}",
                "participant.address.house_number": p.address.house_number,
                "participant.address.country": "Magyarország",
            })

        # Levelezési cím
        mail = p.mailing_address or p.address
        if mail:
            p_data.update({
                "participant.mailing_address.zip_code": mail.zip_code,
                "participant.mailing_address.city": mail.city,
                "participant.mailing_address.street": f"{mail.street} {mail.house_number}",
            })

        # Első szereplő (igénylő) → prefix nélkül
        if i == 0:
            for key, val in p_data.items():
                canonical_to_value[key] = val

        # Indexelt verziók
        prefix = f"participant[{i}]"
        for key, val in p_data.items():
            canonical_to_value[f"{prefix}.{key.split('.', 1)[1]}"] = val

    # --- Ingatlan adatok ---
    for i, prop in enumerate(deal.properties):
        prop_data = {
            "property.address.full_address": prop.address.full_address,
            "property.address.zip_code": prop.address.zip_code,
            "property.address.city": prop.address.city,
            "property.address.street": f"{prop.address.street} {prop.address.house_number}",
            "property.address.house_number": prop.address.house_number,
            "property.parcel_number": prop.parcel_number,
            "property.area_sqm": f"{prop.area_sqm}" if prop.area_sqm else "",
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
            for key, val in prop_data.items():
                canonical_to_value[key] = val

        prefix = f"property[{i}]"
        for key, val in prop_data.items():
            canonical_to_value[f"{prefix}.{key.split('.', 1)[1]}"] = val

    # === Mapping alkalmazása: kanonikus → PDF mező ===
    filled_count = 0
    for pdf_field_name, canonical_field in mapping.mapping_dict.items():
        if canonical_field in canonical_to_value:
            value = canonical_to_value[canonical_field]
            if value:
                field_data[pdf_field_name] = value
                filled_count += 1

    logger.info(f"  {filled_count} mező kap értéket a {len(mapping.mapping_dict)} mappelt mezőből")
    return field_data


def main():
    print("=" * 60)
    print("🧪 ÉLES OTP PDF KITÖLTÉSI TESZT")
    print("=" * 60)

    # 1. Mapping betöltése
    mapping_path = PROJECT_ROOT / "src" / "mapping" / "otp_piaci_hitel_mapping.json"
    mapping = MappingConfig.load(mapping_path)
    print(f"\n📋 Mapping: {mapping_path.name}")
    mapped = sum(1 for f in mapping.fields if f.canonical_field)
    print(f"   {mapped} leképezett mező")

    # 2. Minta adatok
    deal = create_sample_deal()
    print(f"\n👤 Ügyfél: {deal.participants[0].name}")
    print(f"   Társigénylő: {deal.participants[1].name}")
    print(f"   Hitel: {deal.loan.loan_amount:,} Ft, {deal.loan.loan_term_months} hó")
    print(f"   Ingatlan: {deal.properties[0].address.full_address}")

    # 3. Mezőadatok összeállítása
    print(f"\n📋 Mezőadatok összeállítása...")
    field_data = prepare_extended_field_data(deal, mapping)
    print(f"   {len(field_data)} PDF mező kitöltve")

    # 4. PDF kitöltés
    template_pdf = PROJECT_ROOT / "otp" / "Piaci hitel" / "Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf"
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    output_pdf = output_dir / f"OTP_kitoltott_{deal.deal_id}.pdf"

    print(f"\n📝 PDF kitöltés...")
    print(f"   Sablon: {template_pdf.name} ({template_pdf.stat().st_size / 1024:.0f} KB)")

    import pikepdf

    filled = 0
    with pikepdf.open(template_pdf) as pdf:
        if "/AcroForm" in pdf.Root:
            acroform = pdf.Root["/AcroForm"]
            if "/Fields" in acroform:
                for field_ref in acroform["/Fields"]:
                    try:
                        f = field_ref
                        field_name = str(f.get("/T", ""))
                        if field_name in field_data:
                            value = field_data[field_name]
                            field_type = str(f.get("/FT", ""))

                            if field_type == "/Btn":
                                # Checkbox/radio – nem töltjük szöveggel
                                continue
                            else:
                                f["/V"] = pikepdf.String(value)
                                # Appearance flag reset
                                if "/AP" in f:
                                    del f["/AP"]
                                filled += 1
                    except Exception as e:
                        logger.debug(f"Mező hiba: {e}")

        pdf.save(output_pdf)

    print(f"   ✅ {filled} szövegmező kitöltve")
    print(f"   💾 Output: {output_pdf}")
    print(f"   📦 Méret: {output_pdf.stat().st_size / 1024:.0f} KB")

    # 5. Ellenőrzés – kitöltött mezők kiolvasása
    print(f"\n🔍 Kitöltés ellenőrzése...")
    verified = 0
    with pikepdf.open(output_pdf) as pdf:
        for field_ref in pdf.Root["/AcroForm"]["/Fields"]:
            f = field_ref
            name = str(f.get("/T", ""))
            val = str(f.get("/V", ""))
            if val and val != "()":
                verified += 1
                if verified <= 20:
                    print(f"   ✅ {name} = {val[:60]}")
        if verified > 20:
            print(f"   ... és még {verified - 20} kitöltött mező")

    print(f"\n{'='*60}")
    print(f"📊 Összesítés:")
    print(f"   Template: 97 oldal, 1743 AcroForm mező")
    print(f"   Leképezett: {mapped}")
    print(f"   Kitöltött (szöveg): {filled}")
    print(f"   Verified: {verified}")
    print(f"   Output: {output_pdf}")
    print("=" * 60)


if __name__ == "__main__":
    main()


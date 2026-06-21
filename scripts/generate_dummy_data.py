"""
FinancialGenie – Dummy adatgenerátor

Tesztelési célú, valósághű magyar ügyféladatokat generál
a kanonikus adatmodellben. Salesforce sandbox nélkül is
használható a PoC fejlesztéséhez.

Használat:
    python scripts/generate_dummy_data.py
    python scripts/generate_dummy_data.py --scenario multi_borrower
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

# Projekt gyökér hozzáadása a path-hoz
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.models.canonical_model import (
    DealData, LoanDetails, Participant, ParticipantRole,
    Property, PropertyType, Address
)

# --- Magyar nevek és adatok ---

FIRST_NAMES_MALE = [
    "László", "István", "József", "János", "Zoltán", "Sándor",
    "Gábor", "Ferenc", "Attila", "Péter", "Tamás", "Tibor",
    "András", "Csaba", "Imre", "Balázs", "Gyula", "Miklós",
]

FIRST_NAMES_FEMALE = [
    "Mária", "Erzsébet", "Katalin", "Ilona", "Éva", "Anna",
    "Zsuzsanna", "Margit", "Judit", "Ágnes", "Andrea", "Erika",
    "Krisztina", "Mónika", "Tímea", "Anita", "Gabriella", "Nikolett",
]

LAST_NAMES = [
    "Nagy", "Kovács", "Tóth", "Szabó", "Horváth", "Varga",
    "Kiss", "Molnár", "Németh", "Farkas", "Balogh", "Papp",
    "Takács", "Juhász", "Lakatos", "Mészáros", "Oláh", "Simon",
    "Rácz", "Fekete", "Szilágyi", "Török", "Vincze", "Hegedűs",
]

CITIES = [
    ("1011", "Budapest"), ("1025", "Budapest"), ("1035", "Budapest"),
    ("1044", "Budapest"), ("1052", "Budapest"), ("1063", "Budapest"),
    ("1074", "Budapest"), ("1082", "Budapest"), ("1093", "Budapest"),
    ("1106", "Budapest"), ("1117", "Budapest"), ("1122", "Budapest"),
    ("1132", "Budapest"), ("2000", "Szentendre"), ("2030", "Érd"),
    ("2100", "Gödöllő"), ("2040", "Budaörs"), ("3000", "Miskolc"),
    ("4000", "Debrecen"), ("5000", "Szolnok"), ("6000", "Kecskemét"),
    ("6720", "Szeged"), ("7400", "Kaposvár"), ("7621", "Pécs"),
    ("8000", "Székesfehérvár"), ("9000", "Győr"), ("9700", "Szombathely"),
]

STREETS = [
    "Kossuth Lajos utca", "Petőfi Sándor utca", "Rákóczi út",
    "Ady Endre utca", "Dózsa György út", "Széchenyi tér",
    "Béke utca", "Fő utca", "Jókai Mór utca", "Bartók Béla út",
    "Deák Ferenc utca", "Arany János utca", "Móricz Zsigmond körtér",
    "Bajcsy-Zsilinszky út", "Vörösmarty tér", "Nádor utca",
    "Damjanich utca", "Thököly út", "Múzeum körút", "Váci utca",
]

EMPLOYERS = [
    "OTP Bank Nyrt.", "Magyar Telekom Nyrt.", "MOL Nyrt.",
    "Richter Gedeon Nyrt.", "Wizz Air Hungary Kft.", "Audi Hungaria Zrt.",
    "Samsung SDI Hungary Zrt.", "BKV Zrt.", "MÁV Zrt.",
    "Budapest Főváros Önkormányzata", "Magyar Posta Zrt.",
    "Tesco-Global Áruházak Zrt.", "Mercedes-Benz Manufacturing Kft.",
    "Continental Automotive Hungary Kft.", "Bosch Csoport Magyarország",
]


def _random_date(start_year: int, end_year: int) -> date:
    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _random_address() -> Address:
    zip_code, city = random.choice(CITIES)
    return Address(
        zip_code=zip_code,
        city=city,
        street=random.choice(STREETS),
        house_number=str(random.randint(1, 120)),
        floor=str(random.randint(1, 10)) if random.random() > 0.4 else None,
        door=str(random.randint(1, 40)) if random.random() > 0.5 else None,
    )


def _random_personal_id() -> str:
    """Generál egy valósághű (de nem valódi) személyi számot."""
    return f"{random.randint(1,8)}{random.randint(100000, 999999)}{random.choice('ABCD')}{random.choice('EFGH')}"


def _random_tax_id() -> str:
    """Generál egy 10 jegyű adóazonosító jelet."""
    return f"8{random.randint(100000000, 999999999)}"


def _random_id_card() -> str:
    """Generál egy személyi igazolvány számot."""
    prefix = random.choice(["", ""])
    return f"{random.randint(100000, 999999)}{random.choice('ABCDE')}{random.choice('FGHIJ')}"


def _generate_participant(role: ParticipantRole, is_male: bool = None) -> Participant:
    """Egy valósághű szereplőt generál."""
    if is_male is None:
        is_male = random.choice([True, False])
    
    first_names = FIRST_NAMES_MALE if is_male else FIRST_NAMES_FEMALE
    last = random.choice(LAST_NAMES)
    first = random.choice(first_names)
    name = f"{last} {first}"
    
    # Születési név (nőknél leánykori név)
    if not is_male and random.random() > 0.3:
        birth_last = random.choice(LAST_NAMES)
        birth_name = f"{birth_last} {first}"
    else:
        birth_name = name
    
    # Anyja neve
    mother_last = random.choice(LAST_NAMES)
    mother_first = random.choice(FIRST_NAMES_FEMALE)
    
    birth_date = _random_date(1960, 2000)
    
    return Participant(
        role=role,
        name=name,
        birth_name=birth_name,
        mother_name=f"{mother_last} {mother_first}",
        birth_place=random.choice(CITIES)[1],
        birth_date=birth_date,
        personal_id=_random_personal_id(),
        tax_id=_random_tax_id(),
        id_card_number=_random_id_card(),
        address=_random_address(),
        mailing_address=_random_address() if random.random() > 0.6 else None,
        phone=f"+36 {random.choice(['20', '30', '70'])} {random.randint(100, 999)} {random.randint(1000, 9999)}",
        email=f"{first.lower()}.{last.lower()}@{random.choice(['gmail.com', 'freemail.hu', 'citromail.hu', 'outlook.hu'])}",
        employer=random.choice(EMPLOYERS),
        monthly_income=random.randint(250, 1200) * 1000,
        is_active=True,
    )


def _generate_property() -> Property:
    """Egy valósághű fedezeti ingatlant generál."""
    prop_type = random.choice(list(PropertyType))
    zip_code, city = random.choice(CITIES)
    
    if prop_type == PropertyType.APARTMENT:
        area = random.randint(35, 120)
        rooms = random.randint(1, 4)
    elif prop_type == PropertyType.HOUSE:
        area = random.randint(60, 250)
        rooms = random.randint(2, 7)
    elif prop_type == PropertyType.LAND:
        area = random.randint(300, 5000)
        rooms = None
    else:
        area = random.randint(30, 200)
        rooms = random.randint(1, 5)
    
    return Property(
        address=Address(
            zip_code=zip_code,
            city=city,
            street=random.choice(STREETS),
            house_number=str(random.randint(1, 80)),
            floor=str(random.randint(1, 10)) if prop_type == PropertyType.APARTMENT else None,
            door=str(random.randint(1, 30)) if prop_type == PropertyType.APARTMENT else None,
        ),
        parcel_number=f"{random.randint(1000, 99999)}/{random.randint(1, 20)}/{random.choice('ABCD')}/{random.randint(1, 5)}",
        area_sqm=float(area),
        property_type=prop_type,
        estimated_value=random.randint(15, 150) * 1_000_000,
        year_built=random.randint(1960, 2024) if prop_type != PropertyType.LAND else None,
        number_of_rooms=rooms,
    )


# --- Előre definiált forgatókönyvek ---

def scenario_simple() -> DealData:
    """Egyszerű eset: 1 adós, 1 ingatlan."""
    borrower = _generate_participant(ParticipantRole.BORROWER)
    prop = _generate_property()
    loan_amount = random.randint(10, 60) * 1_000_000
    
    return DealData(
        deal_id=f"SF-{random.randint(100000, 999999)}",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=loan_amount,
            loan_term_months=random.choice([120, 180, 240, 300, 360]),
            interest_period=random.choice(["5 év fix", "10 év fix", "végig fix"]),
            loan_purpose="Lakásvásárlás",
            product_name="OTP Lakáshitel",
            down_payment=int(loan_amount * random.uniform(0.2, 0.4)),
            monthly_payment=int(loan_amount / random.randint(200, 400)),
        ),
        participants=[borrower],
        properties=[prop],
        created_at=_random_date(2025, 2026),
    )


def scenario_multi_borrower() -> DealData:
    """Több szereplős eset: adós + adóstárs + kezes."""
    borrower = _generate_participant(ParticipantRole.BORROWER, is_male=True)
    co_borrower = _generate_participant(ParticipantRole.CO_BORROWER, is_male=False)
    guarantor = _generate_participant(ParticipantRole.GUARANTOR)
    
    prop = _generate_property()
    loan_amount = random.randint(20, 80) * 1_000_000
    
    return DealData(
        deal_id=f"SF-{random.randint(100000, 999999)}",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=loan_amount,
            loan_term_months=random.choice([180, 240, 300, 360]),
            interest_period="10 év fix",
            loan_purpose="Lakásvásárlás",
            product_name="OTP Lakáshitel Plus",
            down_payment=int(loan_amount * 0.3),
            monthly_payment=int(loan_amount / 300),
        ),
        participants=[borrower, co_borrower, guarantor],
        properties=[prop],
        created_at=_random_date(2025, 2026),
    )


def scenario_multi_property() -> DealData:
    """Több ingatlanos eset: 1 adós, 2 ingatlan."""
    borrower = _generate_participant(ParticipantRole.BORROWER)
    
    return DealData(
        deal_id=f"SF-{random.randint(100000, 999999)}",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=random.randint(30, 100) * 1_000_000,
            loan_term_months=240,
            interest_period="5 év fix",
            loan_purpose="Lakásvásárlás + felújítás",
            product_name="OTP Lakáshitel",
        ),
        participants=[borrower],
        properties=[_generate_property(), _generate_property()],
        created_at=_random_date(2025, 2026),
    )


def scenario_complex() -> DealData:
    """
    Komplex eset: 2 adóstárs + haszonélvező + 2 ingatlan + 1 inaktív szereplő.
    Ez a legteljesebb teszt-forgatókönyv.
    """
    borrower = _generate_participant(ParticipantRole.BORROWER, is_male=True)
    co_borrower = _generate_participant(ParticipantRole.CO_BORROWER, is_male=False)
    beneficiary = _generate_participant(ParticipantRole.BENEFICIARY)
    
    # Inaktív "kuka" szereplő – ezt ki kell szűrnie a rendszernek
    inactive = _generate_participant(ParticipantRole.CO_BORROWER, is_male=True)
    inactive.is_active = False
    inactive.name = f"[TÖRÖLT] {inactive.name}"
    
    loan_amount = random.randint(40, 120) * 1_000_000
    
    return DealData(
        deal_id=f"SF-{random.randint(100000, 999999)}",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=loan_amount,
            loan_term_months=360,
            interest_period="végig fix",
            loan_purpose="Új építésű lakás vásárlás",
            product_name="OTP Zöld Otthon Lakáshitel",
            down_payment=int(loan_amount * 0.25),
            monthly_payment=int(loan_amount / 350),
        ),
        participants=[borrower, co_borrower, beneficiary, inactive],
        properties=[_generate_property(), _generate_property()],
        created_at=_random_date(2025, 2026),
        notes="Komplex ügylet: több szereplő, több ingatlan, van inaktív szereplő is.",
    )


def scenario_missing_data() -> DealData:
    """
    Hiányos adatokkal rendelkező eset – a teljességellenőrzés tesztelésére.
    Több kötelező mező hiányzik.
    """
    borrower = Participant(
        role=ParticipantRole.BORROWER,
        name="Kovács István",
        # birth_name hiányzik
        mother_name="Nagy Mária",
        # birth_place hiányzik
        birth_date=date(1985, 3, 15),
        # personal_id hiányzik
        tax_id="8123456789",
        address=Address(
            zip_code="1052",
            city="Budapest",
            street="Váci utca",
            house_number="10",
        ),
        # phone hiányzik
        # email hiányzik
    )
    
    return DealData(
        deal_id=f"SF-{random.randint(100000, 999999)}",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=30_000_000,
            loan_term_months=240,
            # interest_period hiányzik
        ),
        participants=[borrower],
        properties=[_generate_property()],
        created_at=date.today(),
        notes="Hiányos adatokkal – teljességellenőrzés tesztelésére.",
    )


SCENARIOS = {
    "simple": scenario_simple,
    "multi_borrower": scenario_multi_borrower,
    "multi_property": scenario_multi_property,
    "complex": scenario_complex,
    "missing_data": scenario_missing_data,
}


def generate_all_scenarios(output_dir: Path = None) -> dict[str, DealData]:
    """Minden forgatókönyvet legenerál és JSON-be ment."""
    if output_dir is None:
        output_dir = PROJECT_ROOT / "samples" / "dummy_data"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    results = {}
    for name, factory in SCENARIOS.items():
        deal = factory()
        results[name] = deal
        
        # JSON mentés
        output_file = output_dir / f"{name}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(deal.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
        
        print(f"✓ {name}: {output_file}")
        print(f"  Ügylet: {deal.deal_id}")
        print(f"  Szereplők: {len(deal.active_participants)} aktív, "
              f"{len(deal.participants) - len(deal.active_participants)} inaktív")
        print(f"  Ingatlanok: {len(deal.properties)}")
        print()
    
    return results


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Dummy ügyletadatok generálása")
    parser.add_argument(
        "--scenario", "-s",
        choices=list(SCENARIOS.keys()) + ["all"],
        default="all",
        help="Melyik forgatókönyvet generáljuk (default: all)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output könyvtár (default: samples/dummy_data/)",
    )
    
    args = parser.parse_args()
    
    if args.scenario == "all":
        generate_all_scenarios(args.output)
    else:
        output_dir = args.output or PROJECT_ROOT / "samples" / "dummy_data"
        output_dir.mkdir(parents=True, exist_ok=True)
        deal = SCENARIOS[args.scenario]()
        output_file = output_dir / f"{args.scenario}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(deal.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
        print(f"✓ {args.scenario}: {output_file}")

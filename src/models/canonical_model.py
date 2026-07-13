"""
FinancialGenie – Kanonikus adatmodell

Bankfüggetlen, belső adatséma, amelybe minden ügyletadat normalizálva kerül.
Ez a modell független az egyes bankok nyomtatványstruktúrájától:
új bank hozzáadásakor csak egy új mapping-konfiguráció kell.

Entitások:
- DealData: a teljes ügylet (hitel + szereplők + ingatlanok)
- Participant: egy szereplő (adós, adóstárs, kezes, haszonélvező)
- Property: egy fedezeti ingatlan
- LoanDetails: hitelparaméterek
- Address: cím (közös struktúra személyekhez és ingatlanokhoz)
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from enum import Enum
from datetime import date


class ParticipantRole(str, Enum):
    """Szereplő szerepe az ügyletben."""
    BORROWER = "adós"
    CO_BORROWER = "adóstárs"
    GUARANTOR = "kezes"
    BENEFICIARY = "haszonélvező"


class Address(BaseModel):
    """Magyarországi cím struktúra."""
    zip_code: str = Field(..., description="Irányítószám")
    city: str = Field(..., description="Település")
    street: str = Field(..., description="Utca/tér/út")
    house_number: str = Field(..., description="Házszám")
    floor: Optional[str] = Field(None, description="Emelet")
    door: Optional[str] = Field(None, description="Ajtó")
    district: Optional[str] = Field(None, description="Kerület (Budapest, római szám I-XXIII)")

    @property
    def full_address(self) -> str:
        """Teljes cím egyetlen stringként."""
        parts = [self.zip_code, self.city]
        if self.district:
            parts.append(f"{self.district}. kerület")
        parts.append(f"{self.street} {self.house_number}".strip())
        if self.floor:
            parts.append(f"{self.floor}. em.")
        if self.door:
            parts.append(f"{self.door}. ajtó")
        return ", ".join(parts)


class Participant(BaseModel):
    """
    Egy szereplő az ügyletben.
    
    Az OTP nyomtatványokon a személyes adatok blokk szereplőnként ismétlődik.
    Az is_active mező jelzi, hogy a szereplő releváns-e (nem „kuka").
    """
    role: ParticipantRole = Field(..., description="Szerep az ügyletben")
    name: str = Field(..., description="Teljes név")
    birth_name: Optional[str] = Field(None, description="Születési név")
    mother_name: Optional[str] = Field(None, description="Anyja neve")
    birth_place: Optional[str] = Field(None, description="Születési hely")
    birth_date: Optional[date] = Field(None, description="Születési dátum")
    personal_id: Optional[str] = Field(None, description="Személyi igazolvány szám")
    tax_id: Optional[str] = Field(None, description="Adóazonosító jel")
    id_card_number: Optional[str] = Field(None, description="Személyi szám")
    address: Optional[Address] = Field(None, description="Állandó lakcím")
    mailing_address: Optional[Address] = Field(None, description="Levelezési cím")
    phone: Optional[str] = Field(None, description="Telefonszám")
    email: Optional[str] = Field(None, description="E-mail cím")
    employer: Optional[str] = Field(None, description="Munkáltató neve")
    monthly_income: Optional[int] = Field(None, description="Havi nettó jövedelem (Ft)")
    marital_status: Optional[str] = Field(None, description="Családi állapot (Contact.Marital_Status__c)")
    citizenship: Optional[str] = Field(None, description="Állampolgárság (Contact.Citizenship__c)")
    dependents_count: Optional[int] = Field(None, description="Eltartottak száma (Contact.Dependents_count__c)")
    education: Optional[str] = Field(None, description="Legmagasabb végzettség (Contact.Highest_Educational_Qualification__c)")
    income_type: Optional[str] = Field(None, description="Jövedelem típusa (Contact.Income_type__c)")
    is_active: bool = Field(True, description="Aktív szereplő-e (nem kuka)")
    # Extra Contact mezők (SOQL-ból, lehetnek üresek)
    salutation: Optional[str] = Field(None, description="Megszólítás")
    occupation: Optional[str] = Field(None, description="Foglalkozás")
    foglalkozas_tipusa: Optional[str] = Field(None, description="Foglalkoztatás típusa")
    employment_type: Optional[str] = Field(None, description="Munkaviszony típusa")
    relation: Optional[str] = Field(None, description="Kapcsolat")
    legal_title: Optional[str] = Field(None, description="Lakcímre vonatkozó jogcím")
    jovairas_vallalasa: Optional[str] = Field(None, description="Jóváírás vállalása")
    loan_purpose: Optional[str] = Field(None, description="Hitel célja")
    interest_period: Optional[str] = Field(None, description="Kamatperiódus")
    other_monthly_income: Optional[float] = Field(None, description="Egyéb havi jövedelem")
    current_debt_credit_limit: Optional[str] = Field(None, description="Jelenlegi adósság")
    date_of_notification_for_residence: Optional[str] = Field(None, description="Lakcím bejelentés dátuma")
    date_of_signature: Optional[str] = Field(None, description="Aláírás dátuma")
    description: Optional[str] = Field(None, description="Leírás")
    mailing_city: Optional[str] = Field(None, description="Levelezési cím - város")
    mailing_postal_code: Optional[str] = Field(None, description="Levelezési cím - irányítószám")
    mailing_street: Optional[str] = Field(None, description="Levelezési cím - utca")
    other_city: Optional[str] = Field(None, description="Egyéb cím - város")
    other_street: Optional[str] = Field(None, description="Egyéb cím - utca")
    other_country: Optional[str] = Field(None, description="Egyéb cím - ország")
    mobile_phone: Optional[str] = Field(None, description="Mobiltelefonszám")
    account_holding_bank: Optional[str] = Field(None, description="Számlavezető bank")
    cafeteria_bonus: Optional[float] = Field(None, description="Cafeteria")
    continuous_tb_2_years: Optional[str] = Field(None, description="TB folytonosság 2 év")
    contract_end_date: Optional[str] = Field(None, description="Szerződés vége")
    contract_start_date: Optional[str] = Field(None, description="Szerződés kezdete")
    date_of_handover: Optional[str] = Field(None, description="Átadás dátuma")
    date_of_transfer: Optional[str] = Field(None, description="Utalás dátuma")
    description_of_loan_purpose: Optional[str] = Field(None, description="Hitelcél leírása")
    divident: Optional[float] = Field(None, description="Osztalék")
    employee_registration: Optional[str] = Field(None, description="Munkavállalói regisztráció")
    employer_company_type: Optional[str] = Field(None, description="Munkáltató cégforma")
    income_from_self_owned: Optional[float] = Field(None, description="Saját cégből származó jövedelem")
    industry: Optional[str] = Field(None, description="Iparág")
    monthly_payment_details: Optional[str] = Field(None, description="Havi törlesztő részletezés")
    monthly_installment_payment: Optional[float] = Field(None, description="Havi törlesztő")
    mortgagor: Optional[str] = Field(None, description="Zálogkötelezett")
    numer_and_age_of_children: Optional[str] = Field(None, description="Gyermekek száma és kora")
    other_income: Optional[float] = Field(None, description="Egyéb jövedelem")
    other_monthly_deductions: Optional[float] = Field(None, description="Egyéb havi levonások")
    own_resources: Optional[float] = Field(None, description="Saját forrás")
    property_50pct_ownership_details: Optional[str] = Field(None, description="50% tulajdonosi részletek")
    property_encumbrances: Optional[str] = Field(None, description="Ingatlan terhek")
    property_value: Optional[str] = Field(None, description="Ingatlan érték")
    self_employment_details: Optional[str] = Field(None, description="Önfoglalkoztatás részletek")
    state_support: Optional[str] = Field(None, description="Állami támogatás")
    town_or_possibly_exact_address: Optional[str] = Field(None, description="Város vagy pontos cím")
    usufructuary: Optional[str] = Field(None, description="Haszonélvező")
    what_type_of_loan: Optional[str] = Field(None, description="Hitel típusa")
    contact_loan_amount: Optional[str] = Field(None, description="Hitelösszeg (Contact)")
    contact_loan_period: Optional[str] = Field(None, description="Hitel futamideje (Contact)")

    @property
    def is_borrower(self) -> bool:
        return self.role == ParticipantRole.BORROWER

    @property
    def is_co_borrower(self) -> bool:
        return self.role == ParticipantRole.CO_BORROWER


class PropertyType(str, Enum):
    """Ingatlan típusa."""
    APARTMENT = "lakás"
    HOUSE = "ház"
    LAND = "telek"
    COMMERCIAL = "üzlethelyiség"


class Property(BaseModel):
    """
    Fedezeti ingatlan adatai.

    Az OTP nyomtatványon az ingatlanrész ingatlanonként ismétlődik.
    """
    address: Address = Field(..., description="Ingatlan címe")
    parcel_number: str = Field(..., description="Helyrajzi szám")
    area_sqm: Optional[float] = Field(None, description="Terület (m²)")
    property_type: PropertyType = Field(PropertyType.APARTMENT, description="Ingatlan típusa")
    estimated_value: Optional[int] = Field(None, description="Becsült érték (Ft)")
    year_built: Optional[int] = Field(None, description="Építés éve")
    number_of_rooms: Optional[int] = Field(None, description="Szobák száma")
    # Extra Property mezők (CIB és más bank nyomtatványok)
    purchase_price: Optional[int] = Field(None, description="Vételár (Ft)")
    form_type: Optional[str] = Field(None, description="Építmény típusa")
    energetika: Optional[str] = Field(None, description="Energetikai besorolás")
    terhek: Optional[str] = Field(None, description="Teherjog")
    ingatlan_szerepe: Optional[str] = Field(None, description="Ingatlan szerepe")
    epites_eve: Optional[str] = Field(None, description="Építés éve (szöveg)")


class LoanDetails(BaseModel):
    """Hitelparaméterek."""
    loan_amount: int = Field(..., description="Hitelösszeg (Ft)")
    loan_term_months: int = Field(..., description="Futamidő (hónap)")
    interest_period: Optional[str] = Field(None, description="Kamatperiódus (pl. 5 év fix)")
    loan_purpose: Optional[str] = Field(None, description="Hitel célja (pl. lakásvásárlás)")
    product_name: Optional[str] = Field(None, description="Termék neve (pl. OTP Lakáshitel)")
    product_type: Optional[str] = Field(None, description="Termék típusa (pl. piaci_hitel, csok_plusz)")
    down_payment: Optional[int] = Field(None, description="Önerő (Ft)")
    monthly_payment: Optional[int] = Field(None, description="Becsült havi törlesztő (Ft)")
    purchase_price: Optional[int] = Field(None, description="Vételár (Ft)")
    csok_amount: Optional[int] = Field(None, description="CSÖK támogatás összege (Ft)")
    afa_support: Optional[int] = Field(None, description="ÁFA támogatás összege (Ft)")
    housing_savings: Optional[int] = Field(None, description="Lakástakarék összeg (Ft)")
    refinance_account: Optional[str] = Field(None, description="Refinanszírozott hitel számlaszáma")


class DealData(BaseModel):
    """
    Teljes ügylet – ez a kanonikus adatmodell gyökéreleme.
    
    Egy DealData tartalmaz minden adatot, ami a nyomtatványkitöltéshez
    szükséges: hitelparamétereket, szereplőket és ingatlanokat.
    """
    deal_id: str = Field(..., description="Ügylet azonosító (Salesforce ID)")
    salesforce_stage: Optional[str] = Field(None, description="Salesforce stage")
    loan: LoanDetails = Field(..., description="Hitelparaméterek")
    participants: List[Participant] = Field(
        default_factory=list, description="Ügylet szereplői"
    )
    properties: List[Property] = Field(
        default_factory=list, description="Fedezeti ingatlanok"
    )
    products: List[str] = Field(
        default_factory=lambda: ["piaci_hitel"],
        description="Igényelt termékek listája (pl. piaci_hitel, csok_plusz, otthon_start)",
    )
    created_at: Optional[date] = Field(None, description="Ügylet létrehozása")
    notes: Optional[str] = Field(None, description="Megjegyzések")

    @property
    def active_participants(self) -> List[Participant]:
        """Aktív (nem kuka) szereplők."""
        return [p for p in self.participants if p.is_active]

    @property
    def borrowers(self) -> List[Participant]:
        """Adósok listája."""
        return [p for p in self.active_participants if p.is_borrower]

    @property
    def co_borrowers(self) -> List[Participant]:
        """Adóstársak listája."""
        return [p for p in self.active_participants if p.is_co_borrower]

    @property
    def guarantors(self) -> List[Participant]:
        """Kezesek listája."""
        return [
            p for p in self.active_participants
            if p.role == ParticipantRole.GUARANTOR
        ]

    def get_participants_by_role(self, role: ParticipantRole) -> List[Participant]:
        """Adott szerepű aktív szereplők lekérése."""
        return [p for p in self.active_participants if p.role == role]

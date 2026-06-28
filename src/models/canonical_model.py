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

    @property
    def full_address(self) -> str:
        """Teljes cím egyetlen stringként."""
        parts = [self.zip_code, self.city, f"{self.street} {self.house_number}"]
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
    citizenship: Optional[str] = Field(None, description="Állampolgárság")
    employment_type: Optional[str] = Field(None, description="Foglalkoztatás típusa (határozott/határozatlan idejű)")
    education: Optional[str] = Field(None, description="Legmagasabb iskolai végzettség")
    marital_status: Optional[str] = Field(None, description="Családi állapot")
    dependents: Optional[int] = Field(None, description="Eltartottak száma")
    residence_since: Optional[date] = Field(None, description="Mióta lakik a lakóhelyén (dátum)")
    gender: Optional[str] = Field(None, description="Nem (férfi/nő)")
    id_document_type: Optional[str] = Field(None, description="Személyi igazolvány típusa")
    business_name: Optional[str] = Field(None, description="Cégnév / vállalkozás neve (ha nem alkalmazott)")
    business_tax_id: Optional[str] = Field(None, description="Cég adószáma")
    employee_count: Optional[int] = Field(None, description="Alkalmazottak száma (vállalkozóknál)")
    employer_tax_id: Optional[str] = Field(None, description="Munkáltató adószáma")
    mailing_address_same: Optional[bool] = Field(None, description="Levelezési cím megegyezik az állandóval")
    nav_declaration: Optional[bool] = Field(None, description="NAV nyilatkozat megtörtént")
    is_active: bool = Field(True, description="Aktív szereplő-e (nem kuka)")

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
    contact_name: Optional[str] = Field(None, description="Kapcsolattartó neve (ingatlanhoz)")
    contact_phone: Optional[str] = Field(None, description="Kapcsolattartó telefonja")
    rental_fee: Optional[int] = Field(None, description="Bérleti díj (Ft)")
    rental_fee_eur: Optional[int] = Field(None, description="Bérleti díj (EUR)")
    usage_type: Optional[str] = Field(None, description="Használat típusa (saját/bérbeadott/egyéb)")


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

"""
FinancialGenie – AI mezőfelismerő réteg

Claude Sonnet 4 API-val felismeri egy üres banki nyomtatvány kitöltendő mezőit,
és leképezi azokat a kanonikus adatmodellre. Az eredmény egy mapping-konfiguráció,
amelyet emberi jóváhagyás után a determinisztikus kitöltőmotor használ.

Ez a komponens teszi gyorsan bővíthetővé a rendszert további bankokra:
új nyomtatvány → AI felismerés → mapping → jóváhagyás → kész.

Használat:
    python -m src.ai.field_recognizer samples/acroform_sample.pdf
    python -m src.ai.field_recognizer samples/flat_sample.pdf --mode overlay
"""

import json
import logging
import base64
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


#: Az Anthropic API-hoz használt Claude modell azonosító (2a).
#: Korábban a "claude-sonnet-4-6" string volt 4 helyen hardcode-olva;
#: most egyetlen helyen, modul-szinten van deklarálva, így egyszerűen
#: frissíthető, ha új modell-verzió érkezik.
AI_MODEL: str = "claude-sonnet-4-6"


def _normalize_key(s: str) -> str:
    """
    Ékezet- és formátum-agnosztikus kulcs-normalizálás (2b).

    A magyar nyomtatványok PDF-mezőnevei gyakran tartalmaznak ékezeteket
    (`név`, `állandó_lakcím`), és ezek kódolása (NFC vs. NFD), valamint a
    betűméret/kiemelés változhat a PDF generátorától függően. Ez a helper:
      1. Unicode NORMAL FORM D-ra bontja a stringet (szétválasztja a
         kombináló jeleket az alap karakterektől),
      2. Eldobja a kombináló jeleket (csak az alap karakterek maradnak),
      3. Kisbetűsíti az egészet.

    Így `"SZA_IG_név"`, `"SZA_IG_nev"`, `"sza_ig_név"` mind ugyanarra a
    canonical kulcsra ("sza_ig_nev") képezhető.
    """
    if s is None:
        return ""
    nfd = unicodedata.normalize("NFD", s)
    no_accents = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return no_accents.lower()


# --- A kanonikus modell mezőkatalógusa ---
# Auto-generated from Salesforce org schema (2026-06-29).
# Keys are SF API names in Object.Field format.
# The AI maps PDF form fields → these SF field keys directly.
# Includes: all fillable types + reference lookups.
# Excludes: url (doc uploads), location (geo), system metadata, Lat/Long.

CANONICAL_FIELDS = {
    # ════════════════════════════════════════════════════════════
    # Contact — személyi adatok (156 fields)
    # ════════════════════════════════════════════════════════════
    "Contact.AccountId": "Account ID",
    "Contact.Account_holding_bank__c": "Számlavezető bank",
    "Contact.Affordable_monthly_installments__c": "Vállalható havi törlesztő",
    "Contact.AssistantName": "Assistant's Name",
    "Contact.AssistantPhone": "Asst. Phone",
    "Contact.Average_monthly_net_income__c": "Nettó jövedelem mértéke",
    "Contact.Bank__c": "Bank",
    "Contact.Banki_ugyintezo_fiok__c": "Fiók",
    "Contact.Banki_ugyintezo_pozicio__c": "Pozíció",
    "Contact.Banki_ugyintezo_terulet__c": "Terület",
    "Contact.Birthdate": "Születési dátum",
    "Contact.Cafeteria_bonus__c": "Cafeteria, bónusz",
    "Contact.Campaign__c": "Campaign",
    "Contact.Citizenship__c": "Állampolgárság",
    "Contact.Concern__c": "Concern",
    "Contact.Continuous_TB_2_years__c": "OEP/TB jogviszony 2 év",
    "Contact.Contract_end_date__c": "Határozott szerződés vége",
    "Contact.Contract_start_date__c": "Határozott szerződés kezdete",
    "Contact.Current_debt_Credit_limit__c": "Fennálló hitelek részletezve",
    "Contact.Current_employment_started__c": "Munkaviszony/vállalkozás kezdete",
    "Contact.Date_of_birth__c": "Születési idő",
    "Contact.Date_of_handover__c": "Date of handover",
    "Contact.Date_of_notification_for_residence__c": "Mióta lakik a lakóhelyén",
    "Contact.Date_of_signature__c": "Date of signature",
    "Contact.Date_of_transfer__c": "Date of transfer",
    "Contact.Department": "Department",
    "Contact.Dependents_count__c": "Eltartottak száma",
    "Contact.Description": "Contact Description",
    "Contact.Description_of_loan_purpose_comments__c": "Description of loan purpose, comments",
    "Contact.Display_Name__c": "Display_Name",
    "Contact.Divident__c": "Divident",
    "Contact.Email": "Email",
    "Contact.EmailBouncedDate": "Email Bounced Date",
    "Contact.EmailBouncedReason": "Email Bounced Reason",
    "Contact.Employee_registration_c__c": "Employee registration",
    "Contact.Employer_s_company_type__c": "Employer's company type",
    "Contact.Employment_Type_c__c": "Munkaviszony határozott/határozatlan",
    "Contact.Estimated_market_value__c": "Estimated market value",
    "Contact.Fax": "Business Fax",
    "Contact.Financial_Institution__c": "Financial Institution",
    "Contact.FirstName": "Keresztnév",
    "Contact.First_Co_debtor__c": "First Co-debtor",
    "Contact.Foglalkozas_tipusa__c": "Foglalkozás típusa",
    "Contact.HasOptedOutOfEmail": "Email Opt Out",
    "Contact.Highest_Educational_Qualification__c": "Legmagasabb iskolai végzettség",
    "Contact.HomePhone": "Otthoni telefon",
    "Contact.ID_Card_Number__c": "Személyi igazolvány szám",
    "Contact.Income_from_self_owned_company__c": "Income from self-owned company",
    "Contact.Income_type__c": "Jövedelem típusa",
    "Contact.IndividualId": "Individual ID",
    "Contact.Industry__c": "Industry",
    "Contact.Interest_Period__c": "Kamatperiódus",
    "Contact.Interest_rate__c": "Kamatláb",
    "Contact.IsEmailBounced": "Is Email Bounced",
    "Contact.IsPriorityRecord": "Important",
    "Contact.Jovairas_vallalasa__c": "Jóváírás vállalása",
    "Contact.LastName": "Vezetéknév",
    "Contact.Last_4_nr_of_ID__c": "Ig. szám utolsó 4 jegye",
    "Contact.LeadSource": "Lead Source",
    "Contact.Lead_Priority__c": "Lead Priority",
    "Contact.Legal_Title_of_permanent_residence__c": "Lakóhely jogcíme",
    "Contact.Loan_Purpose__c": "Hitel célja",
    "Contact.Loan_amount__c": "Hitelösszeg",
    "Contact.Loan_period__c": "Futamidő",
    "Contact.MailingCity": "Levelezési település",
    "Contact.MailingCountry": "Levelezési ország",
    "Contact.MailingCountryCode": "Levelezési ország kód",
    "Contact.MailingPostalCode": "Levelezési irányítószám",
    "Contact.MailingState": "Levelezési megye",
    "Contact.MailingStateCode": "Levelezési megye kód",
    "Contact.MailingStreet": "Levelezési utca",
    "Contact.Marital_Status__c": "Családi állapot",
    "Contact.Missing_documents__c": "Missing documents",
    "Contact.MobilePhone": "Mobilszám",
    "Contact.Monthly_Payment_details__c": "Havi törlesztő részletei",
    "Contact.Mortgagor__c": "Zálogkötelezett",
    "Contact.Mother_s_Name__c": "Anyja születési neve",
    "Contact.Name": "Teljes név",
    "Contact.Name_of_employer__c": "Munkáltató neve",
    "Contact.Nem_preferalt_bank__c": "Nem preferált bank",
    "Contact.Next_step__c": "Next step",
    "Contact.Numer_and_Age_of_Children__c": "Gyermekek száma és életkora",
    "Contact.Occupation__c": "Foglalkozás",
    "Contact.OtherCity": "Egyéb cím település",
    "Contact.OtherCountry": "Egyéb cím ország",
    "Contact.OtherCountryCode": "Egyéb cím ország kód",
    "Contact.OtherPhone": "Egyéb telefon",
    "Contact.OtherPostalCode": "Egyéb cím irányítószám",
    "Contact.OtherState": "Egyéb cím megye",
    "Contact.OtherStateCode": "Egyéb cím megye kód",
    "Contact.OtherStreet": "Egyéb cím utca",
    "Contact.Other_income__c": "Egyéb jövedelmi infó",
    "Contact.Other_monthly_deductions__c": "Jövedelmet terhelő egyéb levonások",
    "Contact.Other_monthly_income__c": "Egyéb havi jövedelem",
    "Contact.OwnerId": "Owner ID",
    "Contact.Ownership_in_employer__c": "Tulajdonrésze a munkáltatóban",
    "Contact.Partneri_bank__c": "Partneri bank",
    "Contact.Permanent_address__c": "Állandó lakcím",
    "Contact.Phone": "Telefonszám",
    "Contact.Place_of_Birth__c": "Születési hely",
    "Contact.Preferalt_bank__c": "Preferált bank",
    "Contact.Property_50pct_ownership_details__c": "50%+ lakóingatlan tulajdon",
    "Contact.Property_Type__c": "Ingatlan típusa",
    "Contact.Property_encumbrances__c": "Ingatlan terhek",
    "Contact.Property_value__c": "Becsült piaci érték",
    "Contact.Purchase_price_construction_cost__c": "Vételár / építési költség",
    "Contact.Re_payment_fee__c": "Előtörlesztési díj",
    "Contact.Referral_fee_percentage__c": "Referral fee percentage",
    "Contact.Referral_name__c": "Referral name",
    "Contact.Regular_referral__c": "Regular referral",
    "Contact.Related_Bank__c": "Kapcsolódó bank",
    "Contact.Relation__c": "Kapcsolat típusa",
    "Contact.ReportsToId": "Reports To ID",
    "Contact.Residence_Status__c": "Jogviszony a lakóhelyén",
    "Contact.Salutation": "Megszólítás",
    "Contact.Second_Co_debtor__c": "Second Co-debtor",
    "Contact.Secondary_Email__c": "Másodlagos email",
    "Contact.Self_employment_details__c": "Egyéni vállalkozás jövedelmi részletek",
    "Contact.Session_ID__c": "Session ID",
    "Contact.State_Support__c": "Állami támogatás",
    "Contact.Szuletesi_nev__c": "Születési név",
    "Contact.Tax_ID__c": "Adóazonosító jel",
    "Contact.Term_in_year_c__c": "Futamidő (év)",
    "Contact.Third_Co_debtor__c": "Third Co-debtor",
    "Contact.Title": "Title",
    "Contact.Town_or_possibly_the_exact_address__c": "Ingatlan település vagy cím",
    "Contact.Usufructuary__c": "Haszonélvező",
    "Contact.What_type_of_loan__c": "Hitel típusa",
    "Contact.X2_Affordable_monthly_installments__c": "2. hitel vállalható törlesztő",
    "Contact.X2_Interest_Period__c": "2. hitel kamatperiódus",
    "Contact.X2_Loan_Purpose__c": "2. hitel célja",
    "Contact.X2_Loan_amount__c": "2. hitel összeg",
    "Contact.X2_Term_in_year__c": "2. hitel futamidő",
    "Contact.X2_What_type_of_loan__c": "2. hitel típusa",
    "Contact.X3_Affordable_monthly_installments__c": "3. hitel vállalható törlesztő",
    "Contact.X3_Interest_Period__c": "3. hitel kamatperiódus",
    "Contact.X3_Loan_Purpose__c": "3. hitel célja",
    "Contact.X3_Loan_amount__c": "3. hitel összeg",
    "Contact.X3_Term_in_year__c": "3. hitel futamidő",
    "Contact.X3_What_type_of_loan__c": "3. hitel típusa",
    "Contact.Year_of_birthdate__c": "Születési év",
    "Contact.ZIP__c": "Irányítószám",

    # ════════════════════════════════════════════════════════════
    # Lead — lead-only fields (not on Contact)
    # ════════════════════════════════════════════════════════════
    "Lead.AnnualRevenue": "Annual Revenue",
    "Lead.City": "City",
    "Lead.Company": "Company",
    "Lead.Country": "Country",
    "Lead.CountryCode": "Country Code",
    "Lead.Country__c": "Country",
    "Lead.Egyeb_forras_info__c": "Egyéb forrás infó",
    "Lead.Elmult_90_napos_hiteligenyles__c": "Elmúlt 90 napos hiteligénylés",
    "Lead.Employee_registration__c": "Employee registration",
    "Lead.Employment_Type__c": "Munkaviszony határozott/határozatlan",
    "Lead.Estimated__c": "Becsült piaci érték",
    "Lead.Form_Type__c": "Form_Type",
    "Lead.Industry": "Industry",
    "Lead.Ingatlan_Kozterulet_jellege__c": "Közterület jellege",
    "Lead.Ingatlan_alapterulet__c": "Hasznos alapterület",
    "Lead.Ingatlan_emelet__c": "Épület, lépcsőház, emelet, ajtó",
    "Lead.Ingatlan_energetika__c": "Energetikai besorolás",
    "Lead.Ingatlan_irsz__c": "Ingatlan irányítószáma",
    "Lead.Ingatlan_jellege__c": "Ingatlan jellege",
    "Lead.Ingatlan_kozterulet_neve__c": "Közterület neve",
    "Lead.Ingatlan_megjegyzes__c": "Ingatlan megjegyzés",
    "Lead.Ingatlan_szerepe__c": "Ingatlan szerepe",
    "Lead.Ingatlan_telepules__c": "Ingatlan település",
    "Lead.Ingatlan_terhek__c": "Ingatlan terhek",
    "Lead.Ingtalan_hazszam__c": "Házszám",
    "Lead.PostalCode": "Irányítószám",
    "Lead.Preferalt_futamido__c": "Preferált futamidő",
    "Lead.Preferalt_kamatperiodus__c": "Preferált kamatperiódus",
    "Lead.Purchase_price__c": "Vételár",
    "Lead.Related_Account__c": "Related Account",
    "Lead.State": "Megye",
    "Lead.Status": "Státusz",
    "Lead.Street": "Utca",
    "Lead.Term_in_year__c": "Futamidő (év)",
    "Lead.Tervezett_CSOK_Plusz__c": "Tervezett CSOK Plusz",
    "Lead.Tervezett_Otthon_Start__c": "Tervezett Otthon Start",
    "Lead.Tervezett_onero__c": "Tervezett önerő",
    "Lead.Tervezett_piaci_hitel__c": "Tervezett piaci hitel",

    # ════════════════════════════════════════════════════════════
    # Opportunity — ALL fields
    # ════════════════════════════════════════════════════════════
    "Opportunity.AccountId": "Account ID",
    "Opportunity.Account__c": "Tippadó neve",
    "Opportunity.Amount": "Amount",
    "Opportunity.Approve_in_the_Bank_Application_OTP__c": "Approve in the Bank Application (OTP)",
    "Opportunity.Attended_the_call__c": "Attended the call",
    "Opportunity.Bank_Approval__c": "Bank Approval",
    "Opportunity.Bank__c": "Potenciális bankok",
    "Opportunity.Banki_ugyintezo_uj_c__c": "Banki ügyintéző (új)",
    "Opportunity.CampaignId": "Campaign ID",
    "Opportunity.Category__c": "Category",
    "Opportunity.Client_accepted_the_offer__c": "Offer acceptance",
    "Opportunity.CloseDate": "Close Date",
    "Opportunity.ContactId": "Contact ID",
    "Opportunity.Contact_Name__c": "Contact Name",
    "Opportunity.Contract_signed_date__c": "Szerződés aláírás dátuma",
    "Opportunity.Csabi_gylete__c": "Csabi ügylete?",
    "Opportunity.Date_when_bank_received_all_documents__c": "Date when bank received all documents",
    "Opportunity.Description": "Description",
    "Opportunity.Fill_and_send_back_request_form__c": "Fill and send back-request form",
    "Opportunity.First_Co_debtor__c": "First Co-debtor",
    "Opportunity.Flag__c": "Flag",
    "Opportunity.Hitel_sszeg__c": "Hitelösszeg",
    "Opportunity.Hitelc_l__c": "Hitelcél",
    "Opportunity.Hitelez_bank__c": "Hitelező bank",
    "Opportunity.Insurance__c": "Biztosítás",
    "Opportunity.Jutal_k_ideje__c": "Jutalék ideje",
    "Opportunity.Jutal_k_kifizetve__c": "Jutalék kifizetve?",
    "Opportunity.Kamat__c": "Kamat",
    "Opportunity.LeadSource": "Lead Source",
    "Opportunity.Lead_Priority__c": "Lead Priority",
    "Opportunity.Mortgagor__c": "Zálogkötelezett",
    "Opportunity.Name": "Opportunity Name",
    "Opportunity.NextStep": "Next Step",
    "Opportunity.Opportunity_Contact_Name__c": "Opportunity Contact Name",
    "Opportunity.Opportunity_ID__c": "Opportunity ID",
    "Opportunity.OwnerId": "Owner ID",
    "Opportunity.Pre_valuation_has_been_ordered__c": "Pre-valuation has been ordered",
    "Opportunity.Pre_valuation_order_bank__c": "Pre-valuation order bank",
    "Opportunity.Pre_valuation_order_date__c": "Pre-valuation order date",
    "Opportunity.Re_evaluate_the_quote__c": "Re-evaluate the quote",
    "Opportunity.Second_Co_debtor__c": "Second Co-debtor",
    "Opportunity.Session_ID__c": "Session ID",
    "Opportunity.Sign_all_documents__c": "Sign all documents",
    "Opportunity.Sign_all_documents_needed__c": "Sign all documents needed",
    "Opportunity.StageName": "Stage",
    "Opportunity.Term_k__c": "Termék",
    "Opportunity.Third_Co_debtor__c": "Third Co-debtor",
    "Opportunity.Tippad_kifizetve__c": "Tippadó kifizetve",
    "Opportunity.Tippado_uj__c": "Tippadó (új)",
    "Opportunity.Type": "Opportunity Type",
    "Opportunity.Usufructuary__c": "Haszonélvező",
    "Opportunity.Which_documents_are_missing__c": "Hiányzó dokumentumok",
    "Opportunity.bankiugyintezoneve__c": "Banki ügyintéző neve",
    "Opportunity.gylet_kezel_je__c": "Ügylet kezelője",
    "Opportunity.gylet_st_tusz__c": "Ügylet státusz",
    "Opportunity.remark__c": "Megjegyzés",
}


class FieldType(str, Enum):
    """PDF mező típusa."""
    TEXT = "text"
    CHECKBOX = "checkbox"
    DATE = "date"
    NUMBER = "number"
    DROPDOWN = "dropdown"


class MappingConfidence(str, Enum):
    """AI mapping-bizonyosság szintje."""
    HIGH = "high"       # 90%+ biztos
    MEDIUM = "medium"   # 60-90% biztos
    LOW = "low"         # 60% alatt – emberi felülvizsgálat kell


@dataclass
class RecognizedField:
    """Egy felismert PDF mező az AI által."""
    pdf_field_name: str                    # Mező neve a PDF-ben (AcroForm) vagy azonosító
    label: str                             # A mező felirata a nyomtatványon
    field_type: FieldType                  # Mező típusa
    canonical_field: Optional[str]         # Leképezés a kanonikus modellre
    confidence: MappingConfidence          # AI bizonyosság
    page_number: int                       # Melyik oldalon van
    coordinates: Optional[dict] = None     # Overlay módhoz: {x, y, width, height}
    notes: Optional[str] = None            # AI megjegyzés
    checkbox_group: Optional[dict] = None   # {"group_id": str, "match_value": str}


@dataclass
class MappingConfig:
    """
    Teljes mapping-konfiguráció egy nyomtatványhoz.
    Ez az AI output, amit emberi jóváhagyás után a kitöltőmotor használ.
    """
    bank_name: str
    form_name: str
    form_type: str                         # "acroform" vagy "flat"
    fields: list[RecognizedField] = field(default_factory=list)
    page_structure: dict = field(default_factory=dict)  # Melyik oldal milyen típusú
    approved: bool = False                 # Emberi jóváhagyás megtörtént-e
    approved_by: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        """Szeriálizáció JSON-ba."""
        return {
            "bank_name": self.bank_name,
            "form_name": self.form_name,
            "form_type": self.form_type,
            "approved": self.approved,
            "approved_by": self.approved_by,
            "notes": self.notes,
            "page_structure": self.page_structure,
            "fields": [
                {
                    "pdf_field_name": f.pdf_field_name,
                    "label": f.label,
                    "field_type": f.field_type.value,
                    "canonical_field": f.canonical_field,
                    "confidence": f.confidence.value,
                    "page_number": f.page_number,
                    "coordinates": f.coordinates,
                    "notes": f.notes,
                    "checkbox_group": f.checkbox_group,
                }
                for f in self.fields
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MappingConfig":
        """Deszeriálizáció JSON-ból."""
        fields = [
            RecognizedField(
                pdf_field_name=f["pdf_field_name"],
                label=f["label"],
                field_type=FieldType(f["field_type"]),
                canonical_field=f.get("canonical_field"),
                confidence=MappingConfidence(f["confidence"]),
                page_number=f["page_number"],
                coordinates=f.get("coordinates"),
                notes=f.get("notes"),
                checkbox_group=f.get("checkbox_group"),
            )
            for f in data.get("fields", [])
        ]
        return cls(
            bank_name=data["bank_name"],
            form_name=data["form_name"],
            form_type=data["form_type"],
            fields=fields,
            page_structure=data.get("page_structure", {}),
            approved=data.get("approved", False),
            approved_by=data.get("approved_by"),
            notes=data.get("notes"),
        )

    def save(self, path: Path):
        """Mentés JSON fájlba."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"Mapping konfiguráció mentve: {path}")

    @classmethod
    def load(cls, path: Path) -> "MappingConfig":
        """Betöltés JSON fájlból."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @property
    def high_confidence_fields(self) -> list[RecognizedField]:
        return [f for f in self.fields if f.confidence == MappingConfidence.HIGH]

    @property
    def needs_review_fields(self) -> list[RecognizedField]:
        return [f for f in self.fields if f.confidence != MappingConfidence.HIGH]

    @property
    def mapping_dict(self) -> dict[str, str]:
        """PDF mező → kanonikus mező leképezés (csak jóváhagyott, high confidence)."""
        return {
            f.pdf_field_name: f.canonical_field
            for f in self.fields
            if f.canonical_field is not None
        }


class FieldRecognizer:
    """
    AI-alapú mezőfelismerő – Claude Sonnet 4 API-val.

    Egy üres PDF nyomtatványt elemez, felismeri a kitöltendő mezőket,
    és leképezi azokat a kanonikus adatmodellre.
    """

    SYSTEM_PROMPT = """Te egy banki nyomtatvány-elemző AI vagy. A feladatod:

1. Elemezd az üres banki nyomtatvány képét/tartalmát
2. Azonosítsd az összes kitöltendő mezőt (név, cím, dátum, stb.)
3. Mindegyik mezőt képezd le a Salesforce API mezőkre (Object.FieldName formátum)

A Salesforce kanonikus mezők:
{canonical_fields}

Válaszolj JSON formátumban az alábbi struktúrával:
{{
  "form_name": "A nyomtatvány neve",
  "page_structure": {{
    "1": "global",     // minden ügyletnél egyszer
    "2": "per_participant",  // szereplőnként ismétlődik
    "3": "per_property"      // ingatlanonként ismétlődik
  }},
  "fields": [
    {{
      "pdf_field_name": "a mező neve vagy azonosítója a PDF-ben",
      "label": "a mező felirata magyarul",
      "field_type": "text|checkbox|date|number|dropdown",
      "canonical_field": "Object.FieldName (pl. Contact.Name, Lead.Ingatlan_irsz__c)",
      "confidence": "high|medium|low",
      "page_number": 1,
      "notes": "opcionális megjegyzés"
    }}
  ]
}}

Fontos szabályok:
- Használd a Salesforce Object.FieldName formátumot (pl. Contact.FirstName, Lead.Ingatlan_telepules__c)
- Ha ingatlan-specifikus, használd a Lead.Ingatlan_* mezőket
- Ha nem vagy biztos a leképezésben, jelöld "low" confidence-szel
- Az ismétlődő blokkok (pl. adós, adóstárs) ugyanazokra a Contact.* mezőkre képeződnek"""

    def __init__(self, api_key: str = None):
        """
        Inicializálás Anthropic API kulccsal.

        Args:
            api_key: Anthropic API kulcs. Ha None, a ANTHROPIC_API_KEY env-ből olvassa.
        """
        if api_key is None:
            import os
            from pathlib import Path
            from dotenv import load_dotenv
            # Try to load .env from project config directory
            root = Path(__file__).resolve().parent.parent.parent
            env_path = root / "config" / ".env"
            if env_path.exists():
                load_dotenv(env_path)
            api_key = os.getenv("ANTHROPIC_API_KEY", "")

        if not api_key:
            logger.warning(
                "ANTHROPIC_API_KEY nincs beállítva. "
                "Az AI mezőfelismerés nem fog működni – használd a mock módot."
            )
            self._client = None
        else:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=api_key)
            except ImportError:
                logger.error("anthropic csomag nincs telepítve: pip install anthropic")
                self._client = None
        self.progress_callback = None  # Optional[Callable[[int, int, str], None]]

    def recognize_acroform(self, pdf_path: Path) -> MappingConfig:
        """
        AcroForm PDF mezőinek felismerése.

        Először kinyeri a PDF form mezőneveit (pikepdf-el),
        majd az AI segítségével leképezi a kanonikus modellre.
        """
        import pikepdf

        logger.info(f"AcroForm mezőfelismerés: {pdf_path}")

        # 1. AcroForm mezők kinyerése
        pdf_fields = self._extract_acroform_fields(pdf_path)
        logger.info(f"  {len(pdf_fields)} AcroForm mező találva")

        if not pdf_fields:
            logger.warning("Nem találtam AcroForm mezőket – lehet, hogy lapos PDF?")
            return self._empty_mapping(pdf_path, "acroform")

        # 2. Heurisztikus alap-leképezés (mindig fut, OTP exact map-el)
        base_mapping = self._heuristic_map_fields(pdf_fields, pdf_path, "acroform")

        # 3. AI leképezés – batch módban ha sok a mező
        if self._client:
            if len(pdf_fields) > 200:
                ai_mapping = self._ai_batch_recognize(pdf_path, pdf_fields)
            else:
                ai_mapping = self._ai_map_fields(pdf_path, pdf_fields, "acroform")

            if ai_mapping:
                # AI eredmények összefésülése a heurisztikussal
                base_mapping = self._merge_mappings(base_mapping, ai_mapping)

        return base_mapping

    def _ai_batch_recognize(
        self, pdf_path: Path, pdf_fields: list[dict]
    ) -> MappingConfig | None:
        """
        Batch AI felismerés nagy nyomtatványokhoz.
        Mezőket OLDAL szerint csoportosítja,
        majd kétlépcsős AI-val azonosít.

        Vision-enhanced: mezők bounding box-ait piros számozott
        keretként rajzolja rá az oldal képére, hogy az AI a
        vizuális kontextusból is felismerje a generikus mezőneveket.
        """
        from collections import defaultdict
        import time
        import re

        logger.info(f"🔄 Batch AI felismerés ({len(pdf_fields)} mező)")

        # ── 1. PDF megnyitása – képeket batch-enként generálunk ──────────
        import fitz
        page_texts: list[str] = []
        try:
            doc = fitz.open(str(pdf_path))
            for page in doc:
                page_texts.append(page.get_text()[:3000])
            doc.close()
            logger.info(f"  📄 {len(page_texts)} oldal szövege kinyerve")
        except Exception as e:
            logger.warning(f"  ⚠️ PDF szöveg kinyerés sikertelen: {e}")

        # ── 2. Mezők csoportosítása OLDAL szerint ───────────────────────
        page_groups: dict[int, list[dict]] = defaultdict(list)
        for f in pdf_fields:
            page_num = f.get("page", 1)
            page_groups[page_num].append(f)

        logger.info(f"  {len(page_groups)} oldal mezőkkel")

        # ── 3. Kanonikus mezők leírása (SF schema-ból) ───────────────────
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        # ── 4. Oldal-alapú batch feldolgozás ────────────────────────────
        all_fields: list[RecognizedField] = []
        batch_count = 0
        MAX_FIELDS_PER_BATCH = 60

        # Build canonical lookup for fuzzy matching
        canonical_keys = set(CANONICAL_FIELDS.keys())

        for page_num in sorted(page_groups.keys()):
            page_fields = page_groups[page_num]
            page_idx = page_num - 1

            sub_batches = [page_fields[i:i + MAX_FIELDS_PER_BATCH]
                           for i in range(0, len(page_fields), MAX_FIELDS_PER_BATCH)]

            for sub_idx, sub_fields in enumerate(sub_batches):
                batch_count += 1
                sub_label = f"p{page_num}" if len(sub_batches) == 1 else f"p{page_num} ({sub_idx+1}/{len(sub_batches)})"
                fields_with_bbox = sum(1 for f in sub_fields if "rect" in f)
                logger.info(f"  📦 [{batch_count}] {sub_label}: {len(sub_fields)} mező ({fields_with_bbox} bbox-al) 📸...")

                # Compute total_batches for progress reporting
                total_batches = sum(
                    max(1, (len(page_groups[p]) + MAX_FIELDS_PER_BATCH - 1) // MAX_FIELDS_PER_BATCH)
                    for p in page_groups
                )


                # ── Vision-enhanced: annotated page image ────────────
                # Draw numbered red rectangles on the page at each field's
                # bounding box, so the AI can visually identify generic fields.
                annotated_image_b64 = None
                try:
                    doc = fitz.open(str(pdf_path))
                    if 0 <= page_idx < len(doc):
                        page = doc[page_idx]
                        # Draw red numbered boxes for fields with bounding boxes
                        box_count = 0
                        for i, f in enumerate(sub_fields, 1):
                            if "rect" in f:
                                r = f["rect"]  # [x0, y0, x1, y1] in PDF points
                                rect = fitz.Rect(r[0], r[1], r[2], r[3])
                                # Red rectangle outline
                                page.draw_rect(rect, color=(1, 0, 0), width=1.5)
                                # Number label above the box
                                label_pt = fitz.Point(rect.x0 + 1, rect.y0 - 1)
                                page.insert_text(
                                    label_pt, str(i),
                                    fontsize=7, color=(1, 0, 0),
                                )
                                box_count += 1
                        # Render annotated page at 150 DPI
                        mat = fitz.Matrix(150 / 72, 150 / 72)
                        pix = page.get_pixmap(matrix=mat)
                        annotated_image_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                        if box_count > 0:
                            logger.info(f"    📸 Annotált kép: {box_count} piros keret rajzolva")
                    doc.close()
                except Exception as e:
                    logger.warning(f"    ⚠️ Annotált kép generálás sikertelen: {e}")

                # Build the system prompt with vision-enhanced instructions
                system = f"""Output ONLY a raw JSON array. No markdown, no explanation, no code fences.

You are mapping PDF form field names to Salesforce API fields for a Hungarian mortgage broker company.
The canonical fields follow the format: Object.FieldName (e.g., Contact.FirstName, Lead.Ingatlan_irsz__c, Opportunity.Hitel_sszeg__c).

VISION INSTRUCTIONS:
The page image has NUMBERED RED BOXES drawn around form fields.
Each field in the list below has a NUMBER (e.g., #1, #2, ...).
LOOK AT THE RED BOX with that number on the image to see the LABEL next to the field.
This is CRITICAL for fields with generic names like "Text_Field_43" or "Szövegmező 123" — 
the RED BOX shows you WHERE the field is, and the printed label next to it tells you WHAT it contains.

SALESFORCE CANONICAL FIELDS (use exactly these keys):
{canonical_desc}

EXAMPLES of correct mappings:
  {{"f": "SZA_IG_név", "c": "Contact.Name", "t": "text"}} — "név" = name → Contact.Name
  {{"f": "SZA_IG_szül_név", "c": "Contact.Szuletesi_nev__c", "t": "text"}} — "szül név" = birth name
  {{"f": "SZA_IG_anyja_neve", "c": "Contact.Mother_s_Name__c", "t": "text"}} — "anyja neve" = mother's name
  {{"f": "SZA_IG_szül_hely", "c": "Contact.Place_of_Birth__c", "t": "text"}} — "szül hely" = birthplace
  {{"f": "SZA_IG_szül_dátum", "c": "Contact.Birthdate", "t": "date"}} — "szül dátum" = birth date
  {{"f": "SZA_IG_állandó_lakcím", "c": "Contact.Permanent_address__c", "t": "text"}} — "lakcím" = address
  {{"f": "SZA_IG_irányítószám", "c": "Contact.ZIP__c", "t": "text"}} — "irsz" = zip code
  {{"f": "SZA_IG_település", "c": "Contact.MailingCity", "t": "text"}}
  {{"f": "SZA_IG_személyi_szám", "c": "Contact.ID_Card_Number__c", "t": "text"}}
  {{"f": "SZA_IG_adóazonosító", "c": "Contact.Tax_ID__c", "t": "text"}}
  {{"f": "SZA_IG_email", "c": "Contact.Email", "t": "text"}}
  {{"f": "SZA_IG_mobil", "c": "Contact.MobilePhone", "t": "text"}}
  {{"f": "SZA_IG_munkáltató", "c": "Contact.Name_of_employer__c", "t": "text"}}
  {{"f": "SZA_IG_jövedelem", "c": "Contact.Average_monthly_net_income__c", "t": "number"}}
  {{"f": "SZA_IG_foglalkoztatás", "c": "Contact.Income_type__c", "t": "text"}}
  {{"f": "SZA_IG_családi_állapot", "c": "Contact.Marital_Status__c", "t": "text"}}
  {{"f": "SZA_IG_végzettség", "c": "Contact.Highest_Educational_Qualification__c", "t": "text"}}
  {{"f": "IA_település", "c": "Lead.Ingatlan_telepules__c", "t": "text"}} — "IA" = ingatlan adatlap
  {{"f": "IA_irsz", "c": "Lead.Ingatlan_irsz__c", "t": "text"}}
  {{"f": "IA_alapterület", "c": "Lead.Ingatlan_alapterulet__c", "t": "number"}}
  {{"f": "LAHI_összeg", "c": "Opportunity.Hitel_sszeg__c", "t": "number"}} — "LAHI" = lakáshitel
  {{"f": "CSOK_tervezett", "c": "Lead.Tervezett_CSOK_Plusz__c", "t": "text"}}

CHECKBOX GROUP DETECTION:
If you see multiple checkboxes next to options like "☐ lakás ☐ ház ☐ telek" that represent
a SINGLE CHOICE from a picklist, include a "g" (group) key with a short group_id, and "mv" (match_value)
with the option label that would trigger this checkbox.
Example: {{"f": "IT_checkbox_1", "c": "Lead.Ingatlan_jellege__c", "t": "checkbox", "g": "property_type", "mv": "lakás"}}

RULES:
- Map EVERY field to the BEST matching Salesforce field from the list above.
- Use the Object.FieldName format exactly as shown in the canonical list.
- For fields you CANNOT map to any SF field, set "c" to null.
- For GENERIC field names (e.g., "Text_Field_43", "Szövegmező 123", "Check_Box_5"), ALWAYS look at the RED BOX on the image to find the label.
- Use PDF field name prefixes as hints: SZA_IG = személyi (Contact fields), IA = ingatlan (Lead.Ingatlan_* fields), CSOK = family subsidy (Lead.Tervezett_CSOK_*), LAHI = lakáshitel (Opportunity fields), MA = munkáltatói (Contact employer fields)
- Checkbox fields: if the label is a yes/no question or a selection option, map to the nearest matching SF field.
- When multiple people (adós/adóstárs) share the same field structure, they ALL map to the same Contact.* fields.

Output format: [{{"f": "field_name", "c": "Object.FieldName", "t": "text|checkbox|date|number", "p": {page_num}}}]
For checkbox groups add: "g": "group_id", "mv": "match_value"

Page {page_num}. RESPOND WITH ONLY THE JSON ARRAY."""

                content = []

                # Send the annotated image (with numbered red boxes)
                if annotated_image_b64:
                    content.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": annotated_image_b64},
                    })

                # Page text for context
                if page_idx < len(page_texts) and page_texts[page_idx].strip():
                    text_preview = page_texts[page_idx][:2500]
                    content.append({"type": "text", "text": f"Page text content:\n{text_preview}"})

                # Field names with numbered reference to RED BOXES on image
                fields_text = f"PDF form fields on this page ({len(sub_fields)} fields).\n"
                fields_text += "Fields with RED BOXES on the image are marked with their box number.\n\n"
                for i, f in enumerate(sub_fields, 1):
                    has_bbox = "rect" in f
                    box_ref = f"  #{i}  " if has_bbox else "  --  "
                    bbox_hint = " ← SEE RED BOX on image" if has_bbox else ""
                    fields_text += f"{box_ref}{f['name']} (type: {f['type']}){bbox_hint}\n"
                content.append({"type": "text", "text": fields_text})

                try:
                    response = self._client.messages.create(
                        model=AI_MODEL,
                        max_tokens=8192,
                        system=system,
                        messages=[
                            {"role": "user", "content": content},
                        ],
                    )

                    response_text = ""
                    for block in response.content:
                        if hasattr(block, "text"):
                            response_text = block.text
                            break

                    if response_text and response_text.strip():
                        # Log raw response for debugging
                        logger.debug(f"    📝 Raw AI response (first 500): {response_text[:500]}")
                        
                        for attempt in range(2):
                            try:
                                json_str = self._extract_json(response_text) if attempt == 0 else response_text.strip()
                                ai_items = json.loads(json_str)
                                if not isinstance(ai_items, list):
                                    ai_items = ai_items.get("fields", []) if isinstance(ai_items, dict) else []
                                
                                mapped = 0
                                skipped = 0
                                for item in ai_items:
                                    if not isinstance(item, dict):
                                        continue
                                    
                                    field_name = item.get("f", "")
                                    canonical = item.get("c")
                                    
                                    if not field_name:
                                        continue
                                    
                                    # Fuzzy match canonical field
                                    if canonical and canonical not in canonical_keys:
                                        # Try to find closest match
                                        best = self._fuzzy_match_canonical(canonical, canonical_keys)
                                        if best:
                                            logger.debug(f"    🔄 Fuzzy: '{canonical}' → '{best}'")
                                            canonical = best
                                    
                                    if not canonical:
                                        skipped += 1
                                        continue
                                    
                                    t_val = item.get("t", "text")
                                    try:
                                        f_type = FieldType(t_val)
                                    except ValueError:
                                        f_type = FieldType.TEXT
                                    
                                    all_fields.append(RecognizedField(
                                        pdf_field_name=field_name,
                                        label=field_name.replace("_", " ").title(),
                                        field_type=f_type,
                                        canonical_field=canonical,
                                        confidence=MappingConfidence.MEDIUM,
                                        page_number=item.get("p", page_num),
                                        checkbox_group=(
                                            {"group_id": item["g"], "match_value": item.get("mv", "")}
                                            if item.get("g") else None
                                        ),
                                    ))
                                    mapped += 1
                                
                                logger.info(f"    ✅ {mapped} mező leképezve, {skipped} null/skip")
                                # Fire progress callback so the UI can update
                                if self.progress_callback is not None:
                                    try:
                                        self.progress_callback(batch_count, total_batches, sub_label)
                                    except Exception:
                                        pass
                                break
                            except (json.JSONDecodeError, ValueError) as e:
                                if attempt == 0:
                                    logger.debug(f"    JSON parse attempt 1 failed: {str(e)[:80]}, retrying raw...")
                                    continue
                                logger.warning(f"    ⚠️ JSON hiba (2 kísérlet után): {str(e)[:80]}")
                                logger.info(f"    Nyers válasz (első 500): {response_text[:500]}")
                    else:
                        logger.warning(f"    ⚠️ Üres válasz")

                except Exception as e:
                    logger.error(f"    ❌ API hiba: {str(e)[:120]}")

                # Rate limit
                time.sleep(1)

        if not all_fields:
            logger.warning("Batch AI: egyetlen oldal sem adott eredményt")
            return None

        total_mapped = sum(1 for f in all_fields if f.canonical_field)
        logger.info(f"🏁 Batch AI összesítés: {total_mapped}/{len(all_fields)} mező leképezve ({batch_count} API hívás)")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="acroform",
            fields=all_fields,
            notes=f"AI batch felismerés ({len(page_groups)} oldal, {batch_count} API hívás).",
        )

    @staticmethod
    def _fuzzy_match_canonical(candidate: str, valid_keys: set[str]) -> str | None:
        """Try to match an AI-returned canonical key to a valid one."""
        # Normalize
        c = candidate.lower().strip().replace(" ", "_").replace("-", "_")
        if c in valid_keys:
            return c
        
        # Try common variations
        # e.g. "participant.full_name" → "participant.name"
        # e.g. "property.address" → "property.address.full_address"
        for key in valid_keys:
            # Substring match
            if c in key or key in c:
                return key
            # Same suffix
            c_parts = c.split(".")
            k_parts = key.split(".")
            if len(c_parts) >= 2 and len(k_parts) >= 2 and c_parts[0] == k_parts[0] and c_parts[-1] == k_parts[-1]:
                return key
        
        # If it starts with a valid prefix, accept it as-is (AI invented a reasonable field)
        for prefix in ("participant.", "property.", "loan.", "csok.", "document.", "declaration.", "signature.", "vat.", "appraisal."):
            if c.startswith(prefix):
                return c
        
        return None

    def _merge_mappings(
        self, base: MappingConfig, ai: MappingConfig
    ) -> MappingConfig:
        """Heurisztikus és AI mapping összefésülése. AI felülírja a heurisztikust."""
        # AI mezők index-be
        ai_by_name = {f.pdf_field_name: f for f in ai.fields}

        merged = []
        for base_field in base.fields:
            if base_field.pdf_field_name in ai_by_name:
                ai_field = ai_by_name[base_field.pdf_field_name]
                # AI felülírja ha van canonical_field
                if ai_field.canonical_field:
                    merged.append(ai_field)
                elif base_field.canonical_field:
                    merged.append(base_field)
                else:
                    merged.append(ai_field)  # AI label/notes még hasznos
            else:
                merged.append(base_field)

        # AI-ból ami nincs a base-ben
        base_names = {f.pdf_field_name for f in base.fields}
        for ai_field in ai.fields:
            if ai_field.pdf_field_name not in base_names:
                merged.append(ai_field)

        total_mapped = sum(1 for f in merged if f.canonical_field)
        logger.info(f"📊 Merge eredmény: {total_mapped}/{len(merged)} mező leképezve")

        return MappingConfig(
            bank_name=base.bank_name or ai.bank_name,
            form_name=base.form_name or ai.form_name,
            form_type=base.form_type,
            fields=merged,
            page_structure=ai.page_structure or base.page_structure,
            notes=f"Összefésült mapping: heurisztikus + AI ({total_mapped} leképezve).",
        )

    def recognize_flat(self, pdf_path: Path) -> MappingConfig:
        """
        Lapos (nem kitölthető) PDF mezőinek felismerése.

        Kétlépéses megközelítés:
        1. Text-anchor: a PDF struktúrából pontozott sorok (……) pozícióinak
           kinyerése – pixel-pontos koordináták
        2. AI szemantikus: Claude párosítja a label + kontextust a kanonikus mezőkkel
        """
        import fitz
        logger.info(f"Lapos PDF mezőfelismerés (text-anchor): {pdf_path}")

        # 1. Kitöltendő mezők kinyerése a PDF szöveg struktúrájából
        anchor_fields = self._extract_text_anchors(pdf_path)
        logger.info(f"  {len(anchor_fields)} kitöltendő mező találva (pontozott sorok)")

        if not anchor_fields:
            logger.warning("  Nem találtam pontozott sorokat – fallback AI módra")
            if self._client:
                return self._ai_recognize_flat_pdf(pdf_path)
            return self._mock_flat_recognition(pdf_path)

        # 2. AI szemantikus mapping (label → kanonikus mező)
        if self._client:
            canonical_map = self._ai_map_flat_labels(anchor_fields, pdf_path)
        else:
            canonical_map = {}

        # 3. RecognizedField-ek összeállítása pixel-pontos koordinátákkal
        fields = []
        for af in anchor_fields:
            canonical = canonical_map.get(af["id"], None)
            fields.append(RecognizedField(
                pdf_field_name=af["id"],
                label=af["label"],
                field_type=FieldType.TEXT,
                canonical_field=canonical,
                confidence=MappingConfidence.HIGH if canonical else MappingConfidence.LOW,
                page_number=af["page"],
                coordinates={
                    "x": af["x"],
                    "y": af["y"],
                    "width": af["width"],
                    "height": af["height"],
                },
                notes=af.get("context", ""),
            ))

        mapped = sum(1 for f in fields if f.canonical_field)
        logger.info(f"  ✅ {mapped}/{len(fields)} mező leképezve (text-anchor + AI)")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="flat",
            fields=fields,
            notes=f"Text-anchor felismerés: {len(fields)} mező, {mapped} leképezve.",
        )

    def _extract_text_anchors(self, pdf_path: Path) -> list[dict]:
        """
        PDF struktúrából kitöltendő mezők kinyerése.
        A pontozott sorok (……) jelölik a kitöltendő területeket.
        Pixel-pontos koordinátákat ad vissza.
        """
        import fitz
        import re

        doc = fitz.open(str(pdf_path))
        fields = []
        field_id = 0

        for page_idx, page in enumerate(doc):
            text_dict = page.get_text("rawdict")
            all_spans = []

            for block in text_dict["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        all_spans.append(span)

            for i, span in enumerate(all_spans):
                if "chars" not in span or not span["chars"]:
                    continue
                text = "".join([c["c"] for c in span["chars"]])
                if not any(char in text for char in ("…", ".", "_", "□", "☐", "☒")):
                    continue

                bbox = span["bbox"]  # [x0, y0, x1, y1]
                font_size = span["size"]

                # Find all dot/underscore matches in the span text (at least 4 characters long)
                dot_matches = list(re.finditer(r'[…._]{4,}', text))
                # Find all checkboxes
                cb_matches = list(re.finditer(r'[□☐☒]', text))

                # Combine and sort matches
                matches = []
                for m in dot_matches:
                    matches.append((m, "text"))
                for m in cb_matches:
                    matches.append((m, "checkbox"))
                matches.sort(key=lambda x: x[0].start())

                if not matches:
                    continue

                prev_end = 0
                for match_idx, (match_obj, match_type) in enumerate(matches):
                    start, end = match_obj.start(), match_obj.end()

                    # pre_text is the text preceding the current match since the previous match end
                    pre_text = text[prev_end:start].strip("() ,;:._")
                    # post_text is the text after the current match until the next match start or end of text
                    next_start = matches[match_idx + 1][0].start() if match_idx + 1 < len(matches) else len(text)
                    post_text = text[end:next_start].strip("() ,;._ ")

                    # Precise coordinates using character bboxes
                    char_start = span["chars"][start]
                    char_end = span["chars"][end - 1]

                    fill_x = char_start["bbox"][0]
                    fill_y = char_start["bbox"][1]
                    fill_w = char_end["bbox"][2] - fill_x
                    fill_h = char_start["bbox"][3] - char_start["bbox"][1]

                    # Kontextus: előtte + utána lévő szöveg (5 span)
                    context_parts = []
                    for j in range(max(0, i-2), min(len(all_spans), i+3)):
                        s = all_spans[j]
                        if "chars" in s and s["chars"]:
                            t = "".join([c["c"] for c in s["chars"]]).strip()
                            if t and not any(char in t for char in ("…", ".", "_", "□", "☐", "☒")):
                                context_parts.append(t)
                    context = " ".join(context_parts)[:200]

                    # Label keresése – sorrendben:
                    label = ""

                    # 1. Közvetlenül utána dőlt span (zárójelben, pl. "(Hiteligénylő neve)")
                    for j in range(i+1, min(i+3, len(all_spans))):
                        s = all_spans[j]
                        if abs(s["bbox"][1] - bbox[1]) > 5:
                            break  # Más soron van
                        if "Ital" in s.get("font", ""):
                            s_text = "".join([c["c"] for c in s["chars"]]) if "chars" in s else ""
                            candidate = s_text.strip("() ,;._")
                            if len(candidate) > 2 and not any(char in candidate for char in ("…", ".", "_", "□", "☐", "☒")):
                                label = candidate
                                break

                    # 2. Pre-text (pl. "Kelt:", "Név:", "Születési hely")
                    if not label and pre_text:
                        clean = pre_text
                        if "(" in clean:
                            clean = clean.split("(")[-1]
                        clean = clean.rstrip(":").strip("() ,;:._")
                        if len(clean) > 1 and not any(char in clean for char in ("…", ".", "_", "□", "☐", "☒")):
                            label = clean

                    # 3. Post-text ha nem pontsor
                    if not label and post_text and len(post_text) > 2 and not any(char in post_text for char in ("…", ".", "_", "□", "☐", "☒")):
                        label = post_text

                    # 4. Kontextus alapú fallback
                    if not label:
                        label = f"mező_{field_id + 1}"

                    field_id += 1
                    fields.append({
                        "id": f"flat_{page_idx+1}_{field_id}",
                        "label": label,
                        "x": round(fill_x, 1),
                        "y": round(fill_y, 1),
                        "width": round(max(fill_w, 8), 1) if match_type == "checkbox" else round(max(fill_w, 20), 1),
                        "height": round(fill_h, 1),
                        "page": page_idx + 1,
                        "context": context,
                    })

                    # Update prev_end for the next match on the same line
                    prev_end = end

        doc.close()
        return fields

    def _ai_map_flat_labels(
        self, anchor_fields: list[dict], pdf_path: Path
    ) -> dict:
        """
        AI szemantikus mapping: label + kontextus → kanonikus mező.
        Csak egyszer hívja az AI-t, nem küld képeket – tisztán szöveges.
        """
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        fields_desc = "\n".join(
            f'  - id="{f["id"]}" label="{f["label"]}" context="{f["context"][:100]}"'
            for f in anchor_fields
        )

        prompt = f"""Párosítsd az alábbi banki nyomtatvány mezőket a kanonikus mezőkkel.

Kanonikus mezők:
{canonical_desc}

Nyomtatvány mezők:
{fields_desc}

Válaszolj CSAK JSON objektummal: {{"mező_id": "kanonikus.mező", ...}}
Ha egy mező nem képezhető le, hagyd ki.
PDF: {pdf_path.name}"""

        try:
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )

            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text = block.text
                    break

            if response_text:
                json_str = self._extract_json(response_text)
                return json.loads(json_str)
        except Exception as e:
            logger.warning(f"  AI mapping hiba: {str(e)[:80]}")

        return {}

    def recognize(self, pdf_path: Path, mode: str = "auto") -> MappingConfig:
        """
        Automatikus felismerés: megpróbálja kideríteni a PDF típusát.

        Args:
            pdf_path: PDF fájl elérési útja
            mode: "auto", "acroform", vagy "overlay"
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF nem található: {pdf_path}")

        if mode == "acroform":
            return self.recognize_acroform(pdf_path)
        elif mode == "overlay":
            return self.recognize_flat(pdf_path)
        else:
            # Auto-detect: van-e AcroForm mező?
            fields = self._extract_acroform_fields(pdf_path)
            if fields:
                logger.info("AcroForm mezők találva – AcroForm módban folytatom")
                return self.recognize_acroform(pdf_path)
            else:
                logger.info("Nincs AcroForm mező – lapos PDF módban folytatom")
                return self.recognize_flat(pdf_path)

    def _extract_acroform_fields(self, pdf_path: Path) -> list[dict]:
        """AcroForm mezők kinyerése pikepdf-el, oldalszám fitz widget API-ból."""
        import pikepdf

        fields = []
        try:
            with pikepdf.open(pdf_path) as pdf:
                if "/AcroForm" not in pdf.Root:
                    return fields

                acroform = pdf.Root["/AcroForm"]
                if "/Fields" not in acroform:
                    return fields

                for field_ref in acroform["/Fields"]:
                    try:
                        f = field_ref
                        field_info = {
                            "name": str(f.get("/T", "")),
                            "type": str(f.get("/FT", "")),
                            "value": str(f.get("/V", "")),
                        }
                        # Extract bounding box for vision-enhanced recognition
                        if "/Rect" in f:
                            try:
                                rect = f["/Rect"]
                                field_info["rect"] = [float(rect[i]) for i in range(4)]
                            except Exception:
                                pass
                        # Próbáljuk kideríteni melyik oldalon van
                        if "/P" in f:
                            page_ref = f["/P"]
                            for i, page in enumerate(pdf.pages):
                                if page.objgen == page_ref.objgen:
                                    field_info["page"] = i + 1
                                    break
                        fields.append(field_info)
                    except Exception as e:
                        logger.debug(f"Mező olvasási hiba: {e}")
                        continue
        except Exception as e:
            logger.error(f"PDF olvasási hiba: {e}")

        # Always run fitz widget fallback for page numbers AND rects
        # (needed for vision-enhanced recognition with annotated images)
        missing_pages = sum(1 for f in fields if "page" not in f)
        missing_rects = sum(1 for f in fields if "rect" not in f)
        if (missing_pages > 0 or missing_rects > 0) and fields:
            logger.info(f"  🔄 Fitz widget fallback: {missing_pages} oldal + {missing_rects} rect hiányzik")
            try:
                import fitz
                import re as _re
                doc = fitz.open(str(pdf_path))
                widget_pages: dict[str, int] = {}
                widget_rects: dict[str, tuple] = {}
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    for w in page.widgets():
                        name = w.field_name
                        if name:
                            if name not in widget_pages:
                                widget_pages[name] = page_num + 1
                            if name not in widget_rects and w.rect:
                                widget_rects[name] = (
                                    w.rect.x0, w.rect.y0, w.rect.x1, w.rect.y1
                                )
                doc.close()
                # Patch page numbers: exact match first, then strip trailing '-N' suffix
                _suffix_re = _re.compile(r'[-]\d+$')
                patched = 0
                for f in fields:
                    if "page" not in f:
                        name = f["name"]
                        if name in widget_pages:
                            f["page"] = widget_pages[name]
                            patched += 1
                        else:
                            # Try stripping trailing '-1', '-2' etc. (duplicated fields)
                            base_name = _suffix_re.sub('', name)
                            if base_name != name and base_name in widget_pages:
                                f["page"] = widget_pages[base_name]
                                patched += 1
                    # Also patch missing rects from fitz widgets
                    if "rect" not in f:
                        name = f["name"]
                        if name in widget_rects:
                            f["rect"] = list(widget_rects[name])
                        else:
                            base_name = _suffix_re.sub('', name)
                            if base_name in widget_rects:
                                f["rect"] = list(widget_rects[base_name])
                logger.info(f"  ✅ {patched} mező oldalszáma feloldva fitz widget API-ból")
                rects_patched = sum(1 for f in fields if "rect" in f)
                logger.info(f"  ✅ {rects_patched}/{len(fields)} mező rendelkezik bbox-al")
            except Exception as e:
                logger.warning(f"  Fitz widget fallback hiba: {e}")

        return fields

    def _ai_map_fields(
        self, pdf_path: Path, pdf_fields: list[dict], form_type: str
    ) -> MappingConfig:
        """AI-alapú mezőleképezés Claude API-val."""
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        system = self.SYSTEM_PROMPT.format(canonical_fields=canonical_desc)

        # PDF oldalak konvertálása képekké az AI számára (csak flat PDF esetén szükséges)
        images = []
        if form_type == "flat":
            images = self._pdf_to_images(pdf_path)

        selected_indices = []
        if images:
            max_images = 20
            if len(images) > max_images:
                logger.info(
                    f"  {len(images)} oldal → csak az első {max_images} oldal képét küldjük "
                    f"(a mezőneveket mind elküldjük)"
                )
                selected_indices = list(range(min(15, len(images)))) + list(range(max(len(images)-5, 15), len(images)))
                selected_indices = sorted(set(selected_indices))[:max_images]
            else:
                selected_indices = list(range(len(images)))

        # Message összeállítása
        content = []

        # PDF képek csatolása (csak a kiválasztottak)
        for i in selected_indices:
            if i < len(images):
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": images[i],
                    },
                })
                content.append({
                    "type": "text",
                    "text": f"Ez a(z) {i+1}. oldal (összesen {len(images)} oldalból).",
                })

        # Mezőnevek csatolása (AcroForm esetén – MINDIG az összeset)
        if pdf_fields:
            fields_text = f"Az AcroForm mezők nevei a PDF-ben (összesen {len(pdf_fields)} mező):\n"
            for f in pdf_fields:
                fields_text += f"  - {f['name']} (típus: {f['type']}, oldal: {f.get('page', '?')})\n"
            content.append({"type": "text", "text": fields_text})

        content.append({
            "type": "text",
            "text": "Kérlek elemezd ezt a banki nyomtatványt és készítsd el a mező-leképezést a kanonikus modellre.",
        })

        try:
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=16384,
                system=system,
                messages=[{"role": "user", "content": content}],
            )

            # A válaszban több content block is lehet (thinking + text)
            response_text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    response_text = block.text
                    break

            if not response_text:
                logger.error("AI válasz nem tartalmaz szöveget")
                return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

            logger.debug(f"AI válasz (első 500 karakter): {response_text[:500]}")
            json_str = self._extract_json(response_text)
            ai_result = json.loads(json_str)

            return self._parse_ai_response(ai_result, pdf_path, form_type)

        except json.JSONDecodeError as e:
            logger.error(f"AI válasz JSON parse hiba: {e}")
            logger.debug(f"Nyers válasz: {response_text[:1000]}")
            logger.info("Visszaesés heurisztikus leképezésre")
            return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)
        except Exception as e:
            logger.error(f"AI API hiba: {e}")
            logger.info("Visszaesés heurisztikus leképezésre")
            return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)

    def _ai_recognize_flat_pdf(self, pdf_path: Path) -> MappingConfig:
        """Lapos PDF AI-alapú felismerése koordinátákkal."""
        canonical_desc = "\n".join(
            f"  - {key}: {desc}" for key, desc in CANONICAL_FIELDS.items()
        )

        system = self.SYSTEM_PROMPT.format(canonical_fields=canonical_desc)
        system += """

Ez egy lapos (nem kitölthető) PDF. A mezőkhöz add meg a koordinátákat is
(x, y, width, height pixelben), ahol a szöveget el kell helyezni.
A koordináta-rendszer bal felső sarokban indul (0,0)."""

        images = self._pdf_to_images(pdf_path)

        content = []
        for i, img_data in enumerate(images):
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": img_data,
                },
            })
            content.append({
                "type": "text",
                "text": f"Ez a(z) {i+1}. oldal. Azonosítsd a kitöltendő mezőket és add meg a koordinátáikat.",
            })

        try:
            response = self._client.messages.create(
                model=AI_MODEL,
                max_tokens=16384,
                system=system,
                messages=[{"role": "user", "content": content}],
            )

            response_text = response.content[0].text
            json_str = self._extract_json(response_text)
            ai_result = json.loads(json_str)

            return self._parse_ai_response(ai_result, pdf_path, "flat")

        except Exception as e:
            logger.error(f"AI API hiba (flat): {e}")
            return self._mock_flat_recognition(pdf_path)

    def _pdf_to_images(self, pdf_path: Path) -> list[str]:
        """PDF oldalak konvertálása base64 PNG képekké."""
        import fitz  # PyMuPDF

        images = []
        try:
            doc = fitz.open(str(pdf_path))
            for page in doc:
                # 150 DPI-s renderelés
                mat = fitz.Matrix(150 / 72, 150 / 72)
                pix = page.get_pixmap(matrix=mat)
                img_data = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                images.append(img_data)
            doc.close()
        except Exception as e:
            logger.error(f"PDF → kép konverziós hiba: {e}")

        return images

    def _extract_json(self, text: str) -> str:
        """JSON blokk kinyerése a szövegből (```json ... ``` vagy sima JSON)."""
        import re

        # 1. Próbáljuk ```json ... ``` code block-ból kinyerni
        json_block = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
        if json_block:
            return json_block.group(1).strip()

        # 2. Próbáljuk ``` ... ``` code block-ból
        code_block = re.search(r'```\s*(.*?)\s*```', text, re.DOTALL)
        if code_block:
            candidate = code_block.group(1).strip()
            if candidate.startswith('{') or candidate.startswith('['):
                return candidate

        # 3. Ha a szöveg JSON tömbbel kezdődik → keressük a [ és ] párját
        stripped = text.strip()
        if stripped.startswith('['):
            bracket_depth = 0
            for i, ch in enumerate(stripped):
                if ch == '[':
                    bracket_depth += 1
                elif ch == ']':
                    bracket_depth -= 1
                    if bracket_depth == 0:
                        return stripped[:i + 1]

        # 4. Keressük az első { és a hozzá tartozó záró } karaktert
        brace_depth = 0
        start_idx = None
        for i, ch in enumerate(text):
            if ch == '{':
                if brace_depth == 0:
                    start_idx = i
                brace_depth += 1
            elif ch == '}':
                brace_depth -= 1
                if brace_depth == 0 and start_idx is not None:
                    return text[start_idx:i + 1]

        # 5. Utolsó fallback: az első [ és utolsó ] (tömb) vagy { és utolsó }
        if '[' in text and ']' in text:
            start = text.index('[')
            end = text.rindex(']') + 1
            return text[start:end]

        if '{' in text and '}' in text:
            start = text.index('{')
            end = text.rindex('}') + 1
            return text[start:end]

        raise ValueError(f"Nem található JSON a válaszban. Válasz (első 300 karakter): {text[:300]}")

    def _parse_ai_response(
        self, ai_result: dict, pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """AI válasz parse-olása MappingConfig-gá."""
        fields = []
        for f_data in ai_result.get("fields", []):
            fields.append(RecognizedField(
                pdf_field_name=f_data["pdf_field_name"],
                label=f_data.get("label", ""),
                field_type=FieldType(f_data.get("field_type", "text")),
                canonical_field=f_data.get("canonical_field"),
                confidence=MappingConfidence(f_data.get("confidence", "medium")),
                page_number=f_data.get("page_number", 1),
                coordinates=f_data.get("coordinates"),
                notes=f_data.get("notes"),
            ))

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=ai_result.get("form_name", pdf_path.stem),
            form_type=form_type,
            fields=fields,
            page_structure=ai_result.get("page_structure", {}),
        )

    def _heuristic_map_fields(
        self, pdf_fields: list[dict], pdf_path: Path, form_type: str
    ) -> MappingConfig:
        """Heurisztikus mezőleképezés mezőnevek alapján."""
        logger.info("Heurisztikus mezőleképezés (AI nélkül)")

        # Általános kulcsszó → kanonikus leképezés
        KEYWORD_MAP = {
            # Személyi adatok
            "nev": "participant.name",
            "name": "participant.name",
            "szul_nev": "participant.birth_name",
            "szuletesi_nev": "participant.birth_name",
            "birth_name": "participant.birth_name",
            "anyja_neve": "participant.mother_name",
            "mother_name": "participant.mother_name",
            "szul_hely": "participant.birth_place",
            "szuletesi_hely": "participant.birth_place",
            "birth_place": "participant.birth_place",
            "szul_datum": "participant.birth_date",
            "szuletesi_datum": "participant.birth_date",
            "birth_date": "participant.birth_date",
            "szemelyi_szam": "participant.personal_id",
            "szem_ig": "participant.personal_id",
            "personal_id": "participant.personal_id",
            "adoazonosito": "participant.tax_id",
            "adoaz": "participant.tax_id",
            "tax_id": "participant.tax_id",
            # Cím
            "lakcim": "participant.address.full_address",
            "address": "participant.address.full_address",
            "iranyitoszam": "participant.address.zip_code",
            "irsz": "participant.address.zip_code",
            "zip": "participant.address.zip_code",
            "zip_code": "participant.address.zip_code",
            "telepules": "participant.address.city",
            "varos": "participant.address.city",
            "city": "participant.address.city",
            "utca": "participant.address.street",
            "kozterulet": "participant.address.street",
            "street": "participant.address.street",
            "hazszam": "participant.address.house_number",
            "hsz": "participant.address.house_number",
            "house_number": "participant.address.house_number",
            # Kontakt
            "telefon": "participant.phone",
            "telefonszam": "participant.phone",
            "phone": "participant.phone",
            "email": "participant.email",
            "e_mail": "participant.email",
            # Munka
            "munkaltato": "participant.employer",
            "munkahely": "participant.employer",
            "employer": "participant.employer",
            "jovedelem": "participant.monthly_income",
            "havi_netto": "participant.monthly_income",
            "monthly_income": "participant.monthly_income",
            # Hitel
            "hitelosszeg": "loan.loan_amount",
            "kolcson_osszeg": "loan.loan_amount",
            "igenyelt_kolcson": "loan.loan_amount",
            "loan_amount": "loan.loan_amount",
            "futamido": "loan.loan_term_months",
            "loan_term": "loan.loan_term_months",
            "kamatperiodus": "loan.interest_period",
            "hitel_celja": "loan.loan_purpose",
            "loan_purpose": "loan.loan_purpose",
            "onero": "loan.down_payment",
            "torleszto": "loan.monthly_payment",
            # Ingatlan
            "helyrajzi_szam": "property.parcel_number",
            "hrsz": "property.parcel_number",
            "parcel_number": "property.parcel_number",
            "terulet": "property.area_sqm",
            "alapterulet": "property.area_sqm",
            "area": "property.area_sqm",
            "ingatlan_tipus": "property.property_type",
            "becsult_ertek": "property.estimated_value",
            "forgalmi_ertek": "property.estimated_value",
        }

        # OTP-specifikus mező → kanonikus leképezés (valódi mezőnevek a PDF-ből)
        OTP_EXACT_MAP = {
            # === SZA_IG_ – Személyi adatok (Igénylő) ===
            "SZA_IG_név": "participant.name",
            "SZA_IG_szül_név": "participant.birth_name",
            "SZA_IG_anyja_neve": "participant.mother_name",
            "SZA_IG_szül_hely": "participant.birth_place",
            "SZA_IG_szül_év": "participant.birth_date",
            "SZA_IG_szül_hónap": "participant.birth_date",
            "SZA_IG_szül_nap": "participant.birth_date",
            "SZA_IG_személyiszám": "participant.personal_id",
            "SZA_IG_személyazonosíó_szám": "participant.id_card_number",
            "SZA_IG_adóazonosító": "participant.tax_id",
            "SZA_IG_állandó_lakcím_irányító": "participant.address.zip_code",
            "SZA_IG_állandó_lakcim_település": "participant.address.city",
            "SZA_IG_állandó_lakcim_utca": "participant.address.street",
            "SZA_IG_levelezési_cím_irányító": "participant.mailing_address.zip_code",
            "SZA_IG_levelezési_cím_település": "participant.mailing_address.city",
            "SZA_IG_levelezési_lakcim_utca": "participant.mailing_address.street",
            "SZA_IG_mobil": "participant.phone",
            "SZA_IG_email_cím": "participant.email",
            "SZA_IG_eltartottak": "participant.dependents",
            "SZA_IG_kata": "participant.kata_status",
            "SZA_IG_létszám": "participant.employee_count",
            "SZA_IG_mióta_lakik_év": "participant.residence_since",
            "SZA_IG_mióta_lakik_hónap": "participant.residence_since",
            "SZA_IG_Elsődleges_Szerep": "participant.role",
            "SZA_IG_neme": "participant.gender",
            "SZA_IG_állampolgárság": "participant.citizenship",
            "SZA_IG_családiállapot": "participant.marital_status",
            "SZA_IG_dokumentum": "participant.id_document_type",
            "SZA_IG_végzettség": "participant.education",
            "SZA_IG_foglalkoztatás": "participant.employment_type",
            "SZA_IG_lakcim_ország": "participant.address.country",
            "SZA_IG_levelezési_cím": "participant.mailing_address_same",
            "SZA_IG_nav": "participant.nav_declaration",
            # === SZA_IG_ – Társigénylő (-társ suffix) ===
            "SZA_IG_név-társ": "participant.name",
            "SZA_IG_szül_név-társ": "participant.birth_name",
            "SZA_IG_anyja_neve-társ": "participant.mother_name",
            "SZA_IG_szül_hely-társ": "participant.birth_place",
            "SZA_IG_szül_év-társ": "participant.birth_date",
            "SZA_IG_szül_hónap-társ": "participant.birth_date",
            "SZA_IG_szül_nap-társ": "participant.birth_date",
            "SZA_IG_személyiszám-társ": "participant.personal_id",
            "SZA_IG_személyazonosíó_szám-társ": "participant.id_card_number",
            "SZA_IG_adóazonosító-társ": "participant.tax_id",
            "SZA_IG_állandó_lakcím_irányító-társ": "participant.address.zip_code",
            "SZA_IG_állandó_lakcim_település-társ": "participant.address.city",
            "SZA_IG_állandó_lakcim_utca-társ": "participant.address.street",
            "SZA_IG_mobil-társ": "participant.phone",
            "SZA_IG_email_cím-társ": "participant.email",
            "SZA_IG_Elsődleges_Szerep-társ": "participant.role",
            "SZA_IG_neme-társ": "participant.gender",
            "SZA_IG_állampolgárság-társ": "participant.citizenship",
            "SZA_IG_családiállapot-társ": "participant.marital_status",
            "SZA_IG_dokumentum-társ": "participant.id_document_type",
            "SZA_IG_végzettség-társ": "participant.education",
            "SZA_IG_foglalkoztatás-társ": "participant.employment_type",
            "SZA_IG_eltartottak-társ": "participant.dependents",
            # === IA_ – Ingatlan adatok ===
            "IA_ingatlanhasználat": "property.usage_type",
            "IA_Bérletidíj": "property.rental_fee",
            "IA_Bérletidíj_EUR": "property.rental_fee_eur",
            "IA_Kapcsolattartó neve": "property.contact_name",
            "IA_Telefonszám": "property.contact_phone",
            # === LAHI/KTKA – Lakáshitel/Költségkalkuláció ===
            "LAHI_KTKA_Igényelt_kölcsön": "loan.loan_amount",
            "LAHI_KTKA_Vásárolt_ingatlan_ár": "loan.purchase_price",
            "LAHI_KTKA_Önerő_kp": "loan.down_payment",
            "LAHI_KTKA_CSOK": "loan.csok_amount",
            "LAHI_KTKA_Afa": "loan.afa_support",
            "LAHI_KTKA_Lakástakarék": "loan.housing_savings",
            "LAHI_KTKA_Kiváltandó_kölcsön_szlaszám": "loan.refinance_account",
            "LAHI_KTKA_Kölcsön_tipusa_OTP": "loan.product_type",
            # === MA_ – Munkáltatói/meghatalmazás ===
            "MA_IG_adós_neve": "participant.name",
            "MA_IG_munáltató_neve": "participant.employer",
            "MA_IG_munkavállaló_neve": "participant.name",
            "MA_IG_vállalkozás_neve": "participant.business_name",
            "MA_IG_munáltató_adósz": "participant.employer_tax_id",
            "MA_IG_vállalkozás_adószám": "participant.business_tax_id",
        }

        fields = []
        # OTP exact map előzetesen normalizálva (2b): az ékezetes/regiszter-
        # érzékeny kulcsokat (`név`, `állandó_lakcím`) az `_normalize_key`
        # helper-rel ékezet- és kisbetű-mentes formára hozzuk, így a PDF-ből
        # érkező tetszőleges kódolású/kisbetűs mezőnév is megtalálja a map-ot.
        otp_exact_map_normalized = {
            _normalize_key(k): v for k, v in OTP_EXACT_MAP.items()
        }
        for pdf_field in pdf_fields:
            field_name = pdf_field["name"]
            canonical = None
            confidence = MappingConfidence.LOW

            # 1. Próbáljuk az OTP exact map-ből – normalizált kulccsal.
            normalized = _normalize_key(field_name)
            if normalized in otp_exact_map_normalized:
                canonical = otp_exact_map_normalized[normalized]
                confidence = MappingConfidence.HIGH
            else:
                # 2. Próbáljuk a kulcsszó alapú map-ből
                clean_name = field_name.lower()
                # Prefixek eltávolítása
                for prefix in [
                    "borrower_", "co_borrower_", "coborrower_", "guarantor_",
                    "property_", "loan_", "sza_ig_", "sza_", "ia_", "ta_",
                    "lahi_", "ktka_", "lahi_ktka_",
                ]:
                    clean_name = clean_name.replace(prefix, "")
                # Társigénylő suffix eltávolítása
                for suffix in ["-társ", "_társ", "-tars", "_tars"]:
                    clean_name = clean_name.replace(suffix, "")

                canonical = KEYWORD_MAP.get(clean_name)
                if canonical:
                    confidence = MappingConfidence.HIGH

            # 3. Típus detektálás
            fl = field_name.lower()
            if any(kw in fl for kw in ["datum", "date", "szul_", "év", "hó", "nap"]):
                field_type = FieldType.DATE
            elif any(kw in fl for kw in ["osszeg", "ertek", "összeg", "érték", "ár", "díj"]):
                field_type = FieldType.NUMBER
            elif pdf_field.get("type") == "/Btn":
                field_type = FieldType.CHECKBOX
            else:
                field_type = FieldType.TEXT

            fields.append(RecognizedField(
                pdf_field_name=field_name,
                label=field_name.replace("_", " ").title(),
                field_type=field_type,
                canonical_field=canonical,
                confidence=confidence,
                page_number=pdf_field.get("page", 1),
            ))

        mapped = sum(1 for f in fields if f.canonical_field)
        logger.info(f"  Heurisztikus: {mapped}/{len(fields)} mező leképezve")

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type=form_type,
            fields=fields,
            notes="Heurisztikus leképezés (AI API nem elérhető). Felülvizsgálat szükséges.",
        )

    def _mock_flat_recognition(self, pdf_path: Path) -> MappingConfig:
        """Mock lapos PDF felismerés (tesztelési célra, AI nélkül)."""
        logger.info("Mock lapos PDF felismerés (AI nélkül)")

        import fitz
        doc = fitz.open(str(pdf_path))
        num_pages = len(doc)

        # Alapvető oldal-struktúra feltételezés
        page_structure = {}
        if num_pages >= 1:
            page_structure["1"] = "global"
        if num_pages >= 2:
            page_structure["2"] = "per_participant"
        if num_pages >= 3:
            page_structure["3"] = "per_property"

        # Szöveg kinyerése az oldalakról – mezők beazonosítása
        fields = []
        field_labels_hu = [
            ("Név", "participant.name", FieldType.TEXT),
            ("Születési név", "participant.birth_name", FieldType.TEXT),
            ("Anyja neve", "participant.mother_name", FieldType.TEXT),
            ("Születési hely", "participant.birth_place", FieldType.TEXT),
            ("Születési dátum", "participant.birth_date", FieldType.DATE),
            ("Személyi szám", "participant.personal_id", FieldType.TEXT),
            ("Adóazonosító", "participant.tax_id", FieldType.TEXT),
            ("Lakcím", "participant.address.full_address", FieldType.TEXT),
            ("Irányítószám", "participant.address.zip_code", FieldType.TEXT),
            ("Település", "participant.address.city", FieldType.TEXT),
            ("Telefonszám", "participant.phone", FieldType.TEXT),
            ("E-mail", "participant.email", FieldType.TEXT),
            ("Hitelösszeg", "loan.loan_amount", FieldType.NUMBER),
            ("Futamidő", "loan.loan_term_months", FieldType.NUMBER),
            ("Helyrajzi szám", "property.parcel_number", FieldType.TEXT),
            ("Terület", "property.area_sqm", FieldType.NUMBER),
        ]

        for page_num in range(num_pages):
            page = doc[page_num]
            page_text = page.get_text()

            for label, canonical, ftype in field_labels_hu:
                if label.lower() in page_text.lower():
                    # Megpróbáljuk megtalálni a label pozícióját
                    text_instances = page.search_for(label)
                    if text_instances:
                        rect = text_instances[0]
                        # A kitöltendő terület a labeltől jobbra/alatta
                        fields.append(RecognizedField(
                            pdf_field_name=f"flat_{canonical.replace('.', '_')}_{page_num+1}",
                            label=label,
                            field_type=ftype,
                            canonical_field=canonical,
                            confidence=MappingConfidence.MEDIUM,
                            page_number=page_num + 1,
                            coordinates={
                                "x": float(rect.x1 + 5),
                                "y": float(rect.y0),
                                "width": 200.0,
                                "height": float(rect.height),
                            },
                        ))

        doc.close()

        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type="flat",
            fields=fields,
            page_structure=page_structure,
            notes="Mock felismerés szövegkeresés alapján. Felülvizsgálat szükséges.",
        )

    def _empty_mapping(self, pdf_path: Path, form_type: str) -> MappingConfig:
        """Üres mapping konfiguráció (mezők nem találhatók)."""
        return MappingConfig(
            bank_name="OTP Bank",
            form_name=pdf_path.stem,
            form_type=form_type,
            notes="Nem találtam kitöltendő mezőket. Manuális konfiguráció szükséges.",
        )


def print_mapping_summary(mapping: MappingConfig):
    """Mapping konfiguráció összefoglaló kiírása."""
    print(f"\n{'='*60}")
    print(f"📋 Mapping konfiguráció: {mapping.form_name}")
    print(f"   Bank: {mapping.bank_name}")
    print(f"   Típus: {mapping.form_type}")
    print(f"   Jóváhagyva: {'✓' if mapping.approved else '✗ – felülvizsgálat szükséges'}")
    print(f"{'='*60}")

    if mapping.page_structure:
        print(f"\n📄 Oldalstruktúra:")
        for page, ptype in mapping.page_structure.items():
            print(f"   {page}. oldal: {ptype}")

    print(f"\n🔍 Felismert mezők ({len(mapping.fields)} db):")
    for f in mapping.fields:
        icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}[f.confidence.value]
        canonical = f.canonical_field or "❓ nem leképezett"
        print(f"   {icon} [{f.page_number}. oldal] {f.label}")
        print(f"      PDF: {f.pdf_field_name} → {canonical}")
        if f.coordinates:
            print(f"      Koordináták: x={f.coordinates['x']:.0f}, y={f.coordinates['y']:.0f}")
        if f.notes:
            print(f"      📝 {f.notes}")

    needs_review = mapping.needs_review_fields
    if needs_review:
        print(f"\n⚠️  {len(needs_review)} mező igényel felülvizsgálatot")

    print()


# --- CLI ---

if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="AI mezőfelismerő – banki nyomtatvány → mapping konfiguráció"
    )
    parser.add_argument("pdf_path", type=Path, help="Üres PDF nyomtatvány elérési útja")
    parser.add_argument(
        "--mode", "-m",
        choices=["auto", "acroform", "overlay"],
        default="auto",
        help="Felismerési mód (default: auto)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output mapping JSON fájl (default: src/mapping/<pdf_name>_mapping.json)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API kulcs (default: ANTHROPIC_API_KEY env)",
    )

    args = parser.parse_args()

    # Mezőfelismerés
    recognizer = FieldRecognizer(api_key=args.api_key)
    mapping = recognizer.recognize(args.pdf_path, mode=args.mode)

    # Összefoglaló
    print_mapping_summary(mapping)

    # Mentés
    output_path = args.output
    if output_path is None:
        project_root = Path(__file__).parent.parent.parent
        output_path = project_root / "src" / "mapping" / f"{args.pdf_path.stem}_mapping.json"

    mapping.save(output_path)
    print(f"💾 Mapping mentve: {output_path}")

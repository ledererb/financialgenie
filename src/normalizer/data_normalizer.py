"""
FinancialGenie – Adatnormalizáló

Salesforce nyers adatok → kanonikus modell transzformáció.

A normalizáló feladata:
- Salesforce objektumok leképezése a belső modellre
- Dátumformátumok egységesítése
- Névkonvenciók alkalmazása
- Hiányzó mezők kezelése
"""

import logging
from datetime import date, datetime
from typing import Optional

from src.models.canonical_model import (
    DealData, LoanDetails, Participant, ParticipantRole,
    Property, PropertyType, Address
)

logger = logging.getLogger(__name__)


# Salesforce szerepnevek → kanonikus role mapping
SF_ROLE_MAP = {
    "adós": ParticipantRole.BORROWER,
    "ados": ParticipantRole.BORROWER,
    "borrower": ParticipantRole.BORROWER,
    "főadós": ParticipantRole.BORROWER,
    "foadós": ParticipantRole.BORROWER,
    "primary_borrower": ParticipantRole.BORROWER,
    "adóstárs": ParticipantRole.CO_BORROWER,
    "adostars": ParticipantRole.CO_BORROWER,
    "co_borrower": ParticipantRole.CO_BORROWER,
    "társadós": ParticipantRole.CO_BORROWER,
    "kezes": ParticipantRole.GUARANTOR,
    "guarantor": ParticipantRole.GUARANTOR,
    "haszonélvező": ParticipantRole.BENEFICIARY,
    "haszonelvezo": ParticipantRole.BENEFICIARY,
    "beneficiary": ParticipantRole.BENEFICIARY,
}

# Salesforce ingatlan típusok → kanonikus mapping
SF_PROPERTY_TYPE_MAP = {
    "lakás": PropertyType.APARTMENT,
    "lakas": PropertyType.APARTMENT,
    "apartment": PropertyType.APARTMENT,
    "társasházi lakás": PropertyType.APARTMENT,
    "használt társasházi lakás": PropertyType.APARTMENT,
    "használt lakás, osztatlan közös tulajdonban": PropertyType.APARTMENT,
    "újépítésű lakás": PropertyType.APARTMENT,
    "egyéb": PropertyType.APARTMENT,
    "ház": PropertyType.HOUSE,
    "haz": PropertyType.HOUSE,
    "house": PropertyType.HOUSE,
    "családi ház": PropertyType.HOUSE,
    "használt lakóház": PropertyType.HOUSE,
    "újépítésű ház": PropertyType.HOUSE,
    "nyaraló, üdülő, hétvégi ház": PropertyType.HOUSE,
    "telek": PropertyType.LAND,
    "land": PropertyType.LAND,
    "üzlethelyiség": PropertyType.COMMERCIAL,
    "uzlethelyiseg": PropertyType.COMMERCIAL,
    "commercial": PropertyType.COMMERCIAL,
    "iroda": PropertyType.COMMERCIAL,
}


class DataNormalizer:
    """
    Salesforce nyers adatok normalizálása kanonikus modellbe.
    
    Kétféle bemenetet kezel:
    1. JSON dict (mock módban a dummy adatokból)
    2. Salesforce API válasz (éles módban)
    """

    def normalize_deal(self, raw_data: dict) -> DealData:
        """
        Nyers ügyletadatok normalizálása.
        
        Args:
            raw_data: Salesforce-ból vagy JSON-ból érkező nyers adatok
            
        Returns:
            Normalizált DealData
        """
        # Ha már kanonikus formátumban van (dummy adatok)
        if "loan" in raw_data and "participants" in raw_data:
            return self._normalize_canonical_json(raw_data)
        
        # Salesforce formátum
        return self._normalize_salesforce(raw_data)

    def _normalize_canonical_json(self, data: dict) -> DealData:
        """Kanonikus JSON formátumból (dummy adatok) normalizálás."""
        try:
            return DealData.model_validate(data)
        except Exception as e:
            logger.error(f"Kanonikus JSON validációs hiba: {e}")
            raise

    def _normalize_salesforce(self, sf_data: dict) -> DealData:
        """Salesforce API válaszból normalizálás."""
        # Hiteladatok
        loan = LoanDetails(
            loan_amount=self._safe_int(sf_data.get("Amount__c") or sf_data.get("Amount")),
            loan_term_months=self._safe_int(sf_data.get("Loan_Term__c", 240)),
            interest_period=sf_data.get("Interest_Period__c"),
            loan_purpose=sf_data.get("Loan_Purpose__c"),
            product_name=sf_data.get("Product_Name__c"),
            down_payment=self._safe_int(sf_data.get("Down_Payment__c")),
            monthly_payment=self._safe_int(sf_data.get("Monthly_Payment__c")),
        )

        # Szereplők
        participants = []
        for p_data in sf_data.get("Participants__r", {}).get("records", []):
            participant = self._normalize_participant(p_data)
            if participant:
                participants.append(participant)

        # Ingatlanok
        properties = []
        for prop_data in sf_data.get("Properties__r", {}).get("records", []):
            prop = self._normalize_property(prop_data)
            if prop:
                properties.append(prop)

        return DealData(
            deal_id=sf_data.get("Id", "unknown"),
            salesforce_stage=sf_data.get("StageName"),
            loan=loan,
            participants=participants,
            properties=properties,
            created_at=self._parse_date(sf_data.get("CreatedDate")),
            notes=sf_data.get("Description"),
        )

    def _normalize_participant(self, p_data: dict) -> Optional[Participant]:
        """Egy szereplő normalizálása."""
        role_str = (p_data.get("Role__c") or p_data.get("role", "")).lower().strip()
        role = SF_ROLE_MAP.get(role_str)
        
        if role is None:
            logger.warning(f"Ismeretlen szerep: '{role_str}' – kihagyva")
            return None

        name = p_data.get("Name__c") or p_data.get("name", "")
        if not name:
            logger.warning("Névtelen szereplő – kihagyva")
            return None

        address = None
        if p_data.get("Address__c") or p_data.get("address"):
            addr_data = p_data.get("Address__c") or p_data.get("address", {})
            if isinstance(addr_data, dict):
                address = Address(
                    zip_code=str(addr_data.get("zip_code", "")),
                    city=addr_data.get("city", ""),
                    street=addr_data.get("street", ""),
                    house_number=str(addr_data.get("house_number", "")),
                    floor=addr_data.get("floor"),
                    door=addr_data.get("door"),
                )

        return Participant(
            role=role,
            name=name,
            birth_name=p_data.get("Birth_Name__c") or p_data.get("birth_name"),
            mother_name=p_data.get("Mother_Name__c") or p_data.get("mother_name"),
            birth_place=p_data.get("Birth_Place__c") or p_data.get("birth_place"),
            birth_date=self._parse_date(
                p_data.get("Birth_Date__c") or p_data.get("birth_date")
            ),
            personal_id=p_data.get("Personal_ID__c") or p_data.get("personal_id"),
            tax_id=p_data.get("Tax_ID__c") or p_data.get("tax_id"),
            id_card_number=p_data.get("ID_Card__c") or p_data.get("id_card_number"),
            address=address,
            phone=p_data.get("Phone__c") or p_data.get("phone"),
            email=p_data.get("Email__c") or p_data.get("email"),
            employer=p_data.get("Employer__c") or p_data.get("employer"),
            monthly_income=self._safe_int(
                p_data.get("Monthly_Income__c") or p_data.get("monthly_income")
            ),
            is_active=p_data.get("Is_Active__c", True) if "Is_Active__c" in p_data 
                       else p_data.get("is_active", True),
        )

    def _normalize_property(self, prop_data: dict) -> Optional[Property]:
        """Egy ingatlan normalizálása."""
        type_str = (prop_data.get("Property_Type__c") or 
                   prop_data.get("property_type", "lakás")).lower().strip()
        prop_type = SF_PROPERTY_TYPE_MAP.get(type_str, PropertyType.APARTMENT)

        addr_data = prop_data.get("Address__c") or prop_data.get("address", {})
        if isinstance(addr_data, dict):
            address = Address(
                zip_code=str(addr_data.get("zip_code", "")),
                city=addr_data.get("city", ""),
                street=addr_data.get("street", ""),
                house_number=str(addr_data.get("house_number", "")),
                floor=addr_data.get("floor"),
                door=addr_data.get("door"),
            )
        else:
            logger.warning("Ingatlan cím hiányzik")
            return None

        return Property(
            address=address,
            parcel_number=prop_data.get("Parcel_Number__c") or prop_data.get("parcel_number", ""),
            area_sqm=self._safe_float(
                prop_data.get("Area__c") or prop_data.get("area_sqm")
            ),
            property_type=prop_type,
            estimated_value=self._safe_int(
                prop_data.get("Estimated_Value__c") or prop_data.get("estimated_value")
            ),
            year_built=self._safe_int(
                prop_data.get("Year_Built__c") or prop_data.get("year_built")
            ),
            number_of_rooms=self._safe_int(
                prop_data.get("Rooms__c") or prop_data.get("number_of_rooms")
            ),
        )

    @staticmethod
    def _parse_date(value) -> Optional[date]:
        """Dátum parse-olás különböző formátumokból."""
        if value is None:
            return None
        if isinstance(value, date):
            return value
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, str):
            for fmt in ["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y.%m.%d", "%d.%m.%Y"]:
                try:
                    return datetime.strptime(value.split(".")[0] if "T" in value else value, fmt).date()
                except ValueError:
                    continue
            # ISO formátum fallback
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
            logger.warning(f"Dátum parse hiba: '{value}'")
        return None

    @staticmethod
    def _safe_int(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

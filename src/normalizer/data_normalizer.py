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
from typing import Any, Optional

from src.canonical.registry import canonical_registry
from src.integrations.salesforce_schema import load_field_map
from src.models.canonical_model import (
    DealData, LoanDetails, Participant, ParticipantRole,
    Property, PropertyType, Address
)

logger = logging.getLogger(__name__)

_SF_FIELD_MAP = load_field_map()
_INT_TARGETS = frozenset({
    "loan.loan_amount", "loan.loan_term_months", "loan.down_payment",
    "loan.monthly_payment", "loan.purchase_price", "loan.csok_amount",
    "loan.afa_support", "loan.housing_savings",
    "participant.monthly_income", "participant.household_size",
    "participant.dependents_count", "participant.children_count",
    "property.estimated_value", "property.purchase_price",
    "property.year_built", "property.number_of_rooms",
})
_DATE_TARGETS = frozenset({"deal.created_at", "participant.birth_date"})
_ISO_STRING_DATE_TARGETS = frozenset({
    "participant.contract_end_date",
    "participant.contract_start_date",
    "participant.current_employment_started",
    "participant.date_of_handover",
    "participant.date_of_signature",
    "participant.date_of_transfer",
    "participant.date_of_notification_for_residence",
})
_PRESERVED_TARGETS = frozenset({
    "deal.has_mortgagor", "deal.has_usufructuary",
    "participant.address.full_address", "property.property_type",
})
_CONTACT_ROLE_LOOKUPS = frozenset({
    "First_Co_debtor__c", "Second_Co_debtor__c", "Third_Co_debtor__c",
})

#: Curated precedence for canonical targets that several equivalent API fields
#: feed (Task 8). Each tuple lists the preferred API-field order; it mirrors
#: the adapter's own explicit source chains (e.g. ``Hitel_sszeg__c or Amount``).
#: Targets not listed here fall back to the deterministic generated-map order
#: with *first present source wins* — never last-alphabetical-wins.
_CURATED_TARGET_PRECEDENCE = {
    "Contact": {
        "participant.address.zip_code": ("ZIP__c", "OtherPostalCode"),
        "participant.birth_date": ("Date_of_birth__c", "Birthdate"),
    },
    "Opportunity": {
        "loan.loan_amount": ("Hitel_sszeg__c", "Amount"),
        "deal.notes": ("Description", "remark__c"),
        "deal.salesforce_stage": ("StageName", "gylet_st_tusz__c"),
        "deal.deal_id": ("Id", "Opportunity_ID__c"),
        "loan.product_name": ("Term_k__c", "Product_Name__c"),
    },
}


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
    "zálogkötelezett": ParticipantRole.MORTGAGOR,
    "zalogkotelezett": ParticipantRole.MORTGAGOR,
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

    Megjegyzés: a normalizáló CRUD-agnosztikus. A `_normalize_salesforce` csupán
    az SF-specifikus oszlopneveket képezi a kanonikus mezőkre, de semmilyen
    üzleti logikát nem tartalmaz, ami más CRM-re ne adaptálható lenne.
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

    @staticmethod
    def _set_nested(store: dict, path: str, value: Any) -> None:
        parts = path.split(".")
        current = store
        for part in parts[:-1]:
            nested = current.get(part)
            if not isinstance(nested, dict):
                nested = {}
                current[part] = nested
            current = nested
        current[parts[-1]] = value

    def _convert_mapped_value(self, rule: dict, target: str, value: Any) -> Any:
        if value is None:
            return None
        if target in _INT_TARGETS:
            return self._safe_int(value)
        if target in _DATE_TARGETS:
            return self._parse_date(value)
        if target in _ISO_STRING_DATE_TARGETS:
            parsed = self._parse_date(value)
            return parsed.isoformat() if parsed is not None else None

        field_type = None
        canonical_path = rule.get("path")
        if canonical_path:
            try:
                field_type = canonical_registry.get(canonical_path).type
            except KeyError:
                pass
        if field_type == "boolean":
            return self._parse_explicit_boolean(value)
        if field_type == "integer":
            return self._safe_int(value)
        if field_type in ("money", "number"):
            return self._safe_float(value)
        if field_type == "date":
            return self._parse_date(value)
        if field_type == "string":
            return str(value)
        return value

    def _apply_field_map(self, object_name: str, raw: dict, destinations: dict) -> None:
        """Apply generated Salesforce rules to canonical mutable stores.

        Duplicate-key precedence (Task 8): when several API fields map to one
        canonical target, the winner is the first source that actually carries
        a value, in curated-precedence order (``_CURATED_TARGET_PRECEDENCE``)
        or, without a curated override, in the deterministic generated-map
        order. Later equivalent sources never overwrite an already-written
        non-empty value; an explicitly empty string does not claim the target,
        so a populated equivalent may still fill it.
        """
        if not isinstance(raw, dict):
            return
        rules = _SF_FIELD_MAP[object_name]
        curated = _CURATED_TARGET_PRECEDENCE.get(object_name, {})
        priority_apis = [
            api_name
            for preferred in curated.values()
            for api_name in preferred
            if api_name in rules
        ]
        priority = set(priority_apis)
        ordered_apis = priority_apis + [
            api_name for api_name in rules if api_name not in priority
        ]
        claimed: set = set()
        for api_name in ordered_apis:
            rule = rules[api_name]
            if api_name not in raw:
                continue
            if object_name == "Contact" and api_name in _CONTACT_ROLE_LOOKUPS:
                continue
            target = rule["target"]
            if target in _PRESERVED_TARGETS:
                continue
            root, separator, path = target.partition(".")
            if not separator or root not in destinations:
                continue
            if target == "loan.loan_term_years":
                claim = (root, "loan_term_months")
            elif path in ("extra", "consents"):
                claim = (root, path, rule.get("key"))
            else:
                claim = (root, path)
            if claim in claimed:
                continue
            value = self._convert_mapped_value(rule, target, raw[api_name])
            if value is None:
                continue
            if target == "loan.loan_term_years":
                years = self._safe_float(raw[api_name])
                if years is not None:
                    destinations[root]["loan_term_months"] = int(years * 12)
                    claimed.add(claim)
                continue
            if path in ("extra", "consents"):
                key = rule.get("key")
                if key:
                    store = destinations[root].setdefault(path, {})
                    store[key] = value
                claimed.add(claim)
                continue
            self._set_nested(destinations[root], path, value)
            if value != "":
                claimed.add(claim)

    def _normalize_related_rows(
        self, object_name: str, target_root: str, rows: list
    ) -> list[dict]:
        """Convert raw related rows through their approved generated-map rules."""
        normalized = []
        for raw in rows or []:
            values = {}
            self._apply_field_map(object_name, raw, {target_root: values})
            normalized.append(values)
        return normalized

    def _normalize_salesforce(self, sf_data: dict) -> DealData:
        """Salesforce API válaszból normalizálás."""
        # Hiteladatok (1c: új kanonikus mezők feltérképezése). A futamidő
        # kizárólag explicit adat: hiányzó érték None marad, nincs alapértelmezett
        # (korábban kitalált 240 hónap) futamidő.
        loan = LoanDetails(
            loan_amount=self._safe_int(sf_data.get("Amount__c") or sf_data.get("Amount")) or 0,
            loan_term_months=self._safe_int(sf_data.get("Loan_Term__c")),
            interest_period=sf_data.get("Interest_Period__c"),
            loan_purpose=sf_data.get("Loan_Purpose__c"),
            product_name=sf_data.get("Product_Name__c"),
            product_type=sf_data.get("Product_Type__c"),
            down_payment=self._safe_int(sf_data.get("Down_Payment__c")),
            monthly_payment=self._safe_int(sf_data.get("Monthly_Payment__c")),
            purchase_price=self._safe_int(sf_data.get("Purchase_Price__c")),
            csok_amount=self._safe_int(sf_data.get("CSOK_Amount__c") or sf_data.get("Csok__c")),
            afa_support=self._safe_int(sf_data.get("AFA_Support__c") or sf_data.get("Afa__c")),
            housing_savings=self._safe_int(
                sf_data.get("Housing_Savings__c") or sf_data.get("Lakastakarek__c")
            ),
            refinance_account=sf_data.get("Refinance_Account__c"),
        )
        opportunity_source = dict(sf_data)
        opportunity_source.update(sf_data.get("_opportunity") or {})
        loan_values = loan.model_dump()
        deal_values = {
            "deal_id": sf_data.get("Id", "unknown"),
            "salesforce_stage": sf_data.get("StageName"),
            "created_at": self._parse_date(sf_data.get("CreatedDate")),
            "notes": sf_data.get("Description"),
            "account_id": sf_data.get("AccountId"),
            "extra": dict(sf_data.get("extra") or {}),
        }
        self._apply_field_map(
            "Opportunity",
            opportunity_source,
            {"loan": loan_values, "deal": deal_values},
        )
        loan = LoanDetails(**loan_values)

        account_raw = sf_data.get("_account") or {}
        account_values = {
            rule["target"].removeprefix("account."): account_raw[api_name]
            for api_name, rule in _SF_FIELD_MAP["Account"].items()
            if api_name in account_raw and rule["target"].startswith("account.")
        }
        self._apply_field_map(
            "Account", account_raw, {"account": account_values}
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

        # Termékek meghatározása (piaci_hitel / otthon_start / csok_plusz / falusi_csok)
        raw_products = sf_data.get("products")
        if isinstance(raw_products, list) and raw_products:
            products = [str(x) for x in raw_products]
        else:
            opp_raw = sf_data.get("_opportunity") or {}
            blob = " ".join(
                str(x or "")
                for x in (
                    loan.product_name,
                    loan.product_type,
                    sf_data.get("Description"),
                    opp_raw.get("Name"),
                )
            ).lower()
            products = []
            if any(k in blob for k in ("otthon start", "otthon_start", "otthonteremt", "osp")):
                products.append("otthon_start")
            if "falusi" in blob:
                products.append("falusi_csok")
            elif "csok" in blob or "csök" in blob:
                products.append("csok_plusz")
            if not products or "piaci" in blob:
                if "piaci_hitel" not in products:
                    products.append("piaci_hitel")

        return DealData(
            **deal_values,
            loan=loan,
            participants=participants,
            properties=properties,
            products=products,
            account_pool_extra=sf_data.get("_account_pool_extra") or [],
            account=account_values,
        )

    def _normalize_participant(self, p_data: dict) -> Optional[Participant]:
        """Egy szereplő normalizálása."""
        role_str = (p_data.get("Role__c") or p_data.get("role", "")).lower().strip()
        role = SF_ROLE_MAP.get(role_str)
        
        if role is None:
            logger.warning(f"Ismeretlen szerep: '{role_str}' – kihagyva")
            return None

        contact_data = p_data.get("_contact") or {}
        first_name = contact_data.get("FirstName") or p_data.get("FirstName") or ""
        last_name = contact_data.get("LastName") or p_data.get("LastName") or ""
        fallback_name = f"{first_name} {last_name}".strip()
        name = (
            p_data.get("Name__c")
            or p_data.get("name")
            or contact_data.get("Name")
            or p_data.get("Name")
            or fallback_name
        )
        if not name:
            logger.warning("Névtelen szereplő – kihagyva")
            return None

        address = None
        if p_data.get("Address__c") or p_data.get("address"):
            addr_data = p_data.get("Address__c") or p_data.get("address", {})
            if isinstance(addr_data, dict) and any(
                addr_data.get(k) for k in ("zip_code", "city", "street", "house_number")
            ):
                address = Address(
                    zip_code=str(addr_data.get("zip_code") or ""),
                    city=addr_data.get("city") or "",
                    street=addr_data.get("street") or "",
                    house_number=str(addr_data.get("house_number") or ""),
                    floor=addr_data.get("floor"),
                    door=addr_data.get("door"),
                    district=addr_data.get("district"),
                )

        participant = Participant(
            role=role,
            name=name,
            contact_id=p_data.get("contact_id") or p_data.get("Id"),
            income_bearing=p_data.get("income_bearing"),
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
            marital_status=p_data.get("Marital_Status__c") or p_data.get("marital_status"),
            citizenship=p_data.get("Citizenship__c") or p_data.get("citizenship"),
            dependents_count=self._safe_int(
                p_data.get("Dependents_count__c") or p_data.get("dependents_count")
            ),
            education=p_data.get("Highest_Educational_Qualification__c")
            or p_data.get("education"),
            income_type=p_data.get("Income_type__c") or p_data.get("income_type"),
            is_active=p_data.get("Is_Active__c", True) if "Is_Active__c" in p_data
                       else p_data.get("is_active", True),
            # Extra Contact mezők
            salutation=p_data.get("salutation") or "",
            occupation=p_data.get("occupation") or "",
            foglalkozas_tipusa=p_data.get("foglalkozas_tipusa") or "",
            employment_type=p_data.get("employment_type") or "",
            relation=p_data.get("relation") or "",
            legal_title=p_data.get("legal_title") or "",
            jovairas_vallalasa=p_data.get("jovairas_vallalasa") or "",
            loan_purpose=p_data.get("loan_purpose") or "",
            interest_period=p_data.get("interest_period") or "",
            other_monthly_income=self._safe_float(p_data.get("other_monthly_income")),
            current_debt_credit_limit=p_data.get("current_debt_credit_limit") or "",
            date_of_notification_for_residence=p_data.get("date_of_notification_for_residence") or "",
            date_of_signature=p_data.get("date_of_signature") or "",
            description=p_data.get("description") or "",
            mailing_city=p_data.get("mailing_city") or "",
            mailing_postal_code=p_data.get("mailing_postal_code") or "",
            mailing_street=p_data.get("mailing_street") or "",
            other_city=p_data.get("other_city") or "",
            other_street=p_data.get("other_street") or "",
            other_country=p_data.get("other_country") or "",
            mobile_phone=p_data.get("mobile_phone") or "",
            account_holding_bank=p_data.get("account_holding_bank") or "",
            cafeteria_bonus=self._safe_float(p_data.get("cafeteria_bonus")),
            continuous_tb_2_years=p_data.get("continuous_tb_2_years") or "",
            contract_end_date=p_data.get("contract_end_date") or "",
            contract_start_date=p_data.get("contract_start_date") or "",
            date_of_handover=p_data.get("date_of_handover") or "",
            date_of_transfer=p_data.get("date_of_transfer") or "",
            description_of_loan_purpose=p_data.get("description_of_loan_purpose") or "",
            divident=self._safe_float(p_data.get("divident")),
            employee_registration=p_data.get("employee_registration") or "",
            employer_company_type=p_data.get("employer_company_type") or "",
            income_from_self_owned=self._safe_float(p_data.get("income_from_self_owned")),
            industry=p_data.get("industry") or "",
            monthly_payment_details=p_data.get("monthly_payment_details") or "",
            monthly_installment_payment=self._safe_float(p_data.get("monthly_installment_payment")),
            mortgagor=p_data.get("mortgagor") or "",
            numer_and_age_of_children=p_data.get("numer_and_age_of_children") or "",
            other_income=self._safe_float(p_data.get("other_income")),
            other_monthly_deductions=self._safe_float(p_data.get("other_monthly_deductions")),
            own_resources=self._safe_float(p_data.get("own_resources")),
            property_50pct_ownership_details=p_data.get("property_50pct_ownership_details") or "",
            property_encumbrances=p_data.get("property_encumbrances") or "",
            property_value=str(p_data.get("property_value")) if p_data.get("property_value") else "",
            self_employment_details=p_data.get("self_employment_details") or "",
            state_support=p_data.get("state_support") or "",
            town_or_possibly_exact_address=p_data.get("town_or_possibly_exact_address") or "",
            usufructuary=p_data.get("usufructuary") or "",
            what_type_of_loan=p_data.get("what_type_of_loan") or "",
            contact_loan_amount=self._safe_float(p_data.get("contact_loan_amount")),
            contact_loan_period=self._safe_float(p_data.get("contact_loan_period")),
            current_employment_started=(
                p_data.get("current_employment_started")
                or p_data.get("Current_employment_started__c")
                or p_data.get("contract_start_date")
                or ""
            ),
            ownership_in_employer=p_data.get("ownership_in_employer") or p_data.get("Ownership_in_employer__c") or "",
            preferalt_bank=p_data.get("preferalt_bank") or p_data.get("Preferalt_bank__c") or "",
            residence_status=p_data.get("residence_status") or p_data.get("Residence_Status__c") or "",
        )
        values = participant.model_dump()
        self._apply_field_map(
            "Contact", contact_data or p_data, {"participant": values}
        )
        values["name"] = name
        values["contact_id"] = p_data.get("contact_id") or values.get("contact_id")
        if p_data.get("_incomes") or "incomes" not in p_data:
            values["incomes"] = self._normalize_related_rows(
                "Jovedelem__c", "income", p_data.get("_incomes")
            )
        else:
            values["incomes"] = p_data.get("incomes") or values["incomes"]
        if p_data.get("_existing_loans") or "existing_loans" not in p_data:
            values["existing_loans"] = self._normalize_related_rows(
                "Meglevo_hitel__c", "existing_loan", p_data.get("_existing_loans")
            )
        else:
            values["existing_loans"] = p_data.get("existing_loans") or values["existing_loans"]
        if "Monthly_installment_payment__c" in contact_data:
            values["monthly_installment_payment"] = self._safe_float(
                contact_data["Monthly_installment_payment__c"]
            )
        return Participant(**values)

    def _normalize_property(self, prop_data: dict) -> Optional[Property]:
        """Egy ingatlan normalizálása."""
        raw_property = prop_data.get("_property") or {}
        type_str = (
            raw_property.get("Property_Type__c")
            or prop_data.get("Property_Type__c")
            or prop_data.get("property_type", "lakás")
        ).lower().strip()
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
                district=addr_data.get("district"),
            )
        else:
            logger.warning("Ingatlan cím hiányzik")
            return None

        prop = Property(
            property_id=prop_data.get("property_id") or raw_property.get("Id"),
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
            purchase_price=self._safe_int(prop_data.get("purchase_price")),
            form_type=prop_data.get("form_type") or "",
            energetika=prop_data.get("energetika") or "",
            terhek=prop_data.get("terhek") or "",
            ingatlan_szerepe=prop_data.get("ingatlan_szerepe") or "",
            epites_eve=str(prop_data.get("epites_eve") or ""),
        )
        values = prop.model_dump()
        self._apply_field_map(
            "Property__c", raw_property or prop_data, {"property": values}
        )
        values["property_id"] = (
            prop_data.get("property_id") or values.get("property_id")
        )
        street_type = values.get("extra", {}).get("street_type")
        street = values.get("address", {}).get("street") or ""
        if street_type and street and not street.lower().endswith(street_type.lower()):
            values["address"]["street"] = f"{street} {street_type}"
        return Property(**values)

    @staticmethod
    def _parse_explicit_boolean(value) -> Optional[bool]:
        """Parse only explicit checkbox/picklist evidence; never infer consent."""
        if isinstance(value, bool):
            return value
        if type(value) is int and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().casefold()
            if normalized in ("true", "yes", "igen", "1"):
                return True
            if normalized in ("false", "no", "nem", "0"):
                return False
        return None

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

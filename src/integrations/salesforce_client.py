"""
FinancialGenie – Salesforce mock kliens

Valódi Salesforce sandbox nélkül szimulálja az API műveleteket:
- Ügylet- és szereplőadatok lekérése (olvasás)
- PDF csatolás és stage-váltás (visszaírás)

A PoC-ban dummy JSON adatokból dolgozik.
Éles módban a simple-salesforce csomag veszi át.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SalesforceAttachment:
    """Salesforce-ba feltöltött csatolmány."""
    filename: str
    content_type: str
    deal_id: str
    uploaded_at: datetime
    file_path: Path


@dataclass
class MockSalesforceState:
    """A mock Salesforce belső állapota."""
    deals: dict = field(default_factory=dict)         # deal_id → deal JSON
    stages: dict = field(default_factory=dict)         # deal_id → stage
    attachments: list = field(default_factory=list)     # SalesforceAttachment lista


class SalesforceClient:
    """
    Salesforce integráció – olvasás és visszaírás.
    
    Mock módban (sandbox nélkül) a samples/dummy_data/ könyvtárból 
    olvassa az adatokat. Éles módban a simple-salesforce csomag 
    OAuth 2.0-n keresztül csatlakozik.
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        security_token: str = "",
        domain: str = "test",
        mock_mode: bool = True,
        mock_data_dir: Path = None,
    ):
        self._mock_mode = mock_mode
        self._state = MockSalesforceState()
        
        if mock_mode:
            self._mock_data_dir = mock_data_dir or Path("samples/dummy_data")
            self._load_mock_data()
            logger.info("Salesforce mock mód aktív")
        else:
            try:
                from simple_salesforce import Salesforce
                self._sf = Salesforce(
                    username=username,
                    password=password,
                    security_token=security_token,
                    domain=domain,
                )
                logger.info(f"Salesforce csatlakozva: {domain}")
            except Exception as e:
                logger.error(f"Salesforce csatlakozási hiba: {e}")
                raise

    def _load_mock_data(self):
        """Mock adatok betöltése a JSON fájlokból."""
        if not self._mock_data_dir.exists():
            logger.warning(f"Mock adat könyvtár nem található: {self._mock_data_dir}")
            return

        for json_file in self._mock_data_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    deal_data = json.load(f)
                deal_id = deal_data.get("deal_id", json_file.stem)
                self._state.deals[deal_id] = deal_data
                self._state.stages[deal_id] = deal_data.get(
                    "salesforce_stage", "Dokumentáció készítés"
                )
                logger.debug(f"Mock ügylet betöltve: {deal_id} ({json_file.name})")
            except Exception as e:
                logger.warning(f"Mock adat betöltési hiba ({json_file}): {e}")

        logger.info(f"{len(self._state.deals)} mock ügylet betöltve")

    def _parse_address_string(self, addr_str: str, zip_code: str = None) -> dict:
        """Parses a Hungarian address string into a structured dictionary.

        Robust against three real-world cases (FIX H3):
          1. Budapest kerület (district) given as roman numerals (I-XXIII),
             e.g. "Budapest, II. kerület, Váci utca 10" — the district is
             extracted into its own field instead of being grafted into the
             street.
          2. ``floor``/``door`` are always present in the returned dict
             (defaulting to None), so consumers can safely use ``.get()``.
          3. Street-only addresses with no house number do not crash and
             yield an empty ``house_number``.
        """
        if not addr_str:
            return {
                "zip_code": zip_code or "", "city": "", "street": "",
                "house_number": "", "district": None, "floor": None, "door": None,
            }

        import re
        # Find 4-digit zip code
        zip_match = re.search(r'\b\d{4}\b', addr_str)
        detected_zip = zip_match.group(0) if zip_match else (zip_code or "")

        clean_str = addr_str
        if zip_match:
            clean_str = clean_str.replace(detected_zip, "", 1).strip()

        # --- District extraction: Budapest kerület (roman numerals I-XXIII) ---
        district = None
        if re.search(r'\bbudapest\b', clean_str, re.IGNORECASE):
            district_match = re.search(
                r'\b([IVXLCDM]{1,5})\.\s*ker[uü]let\b', clean_str, re.IGNORECASE
            )
            if district_match:
                roman = district_match.group(1).upper()
                as_int = self._roman_to_int(roman)
                if as_int is not None and 1 <= as_int <= 23:
                    district = roman
                    clean_str = (
                        clean_str[:district_match.start()]
                        + clean_str[district_match.end():]
                    )
                    # Tidy up stray commas/spaces left behind.
                    clean_str = re.sub(r'\s*,\s*,\s*', ', ', clean_str)
                    clean_str = clean_str.strip().strip(',').strip()

        # Split by comma
        parts = [p.strip() for p in clean_str.split(",") if p.strip()]
        city = ""
        street_and_num = clean_str

        if len(parts) >= 2:
            city = parts[0]
            street_and_num = ", ".join(parts[1:])
        else:
            words = clean_str.split()
            if words:
                city = words[0]
                street_and_num = " ".join(words[1:])

        # Match house number (digits followed by optional letters/symbols).
        num_match = re.search(r'\s+(\d+[\w\-/]*)(.*)', street_and_num)
        street = street_and_num
        house_number = ""
        floor = None
        door = None

        if num_match:
            house_number = num_match.group(1)
            rest = num_match.group(2).strip()
            street = street_and_num[:num_match.start()].strip()

            # Floor and door matching
            floor_match = re.search(r'(\d+)\.?\s*(em|emelet)', rest, re.IGNORECASE)
            door_match = re.search(r'(\d+)\.?\s*(aj|ajto|ajtó)', rest, re.IGNORECASE)

            if floor_match:
                floor = floor_match.group(1)
            if door_match:
                door = door_match.group(1)
        else:
            # Street-only address (no house number) — keep the street clean.
            street = street_and_num.strip().strip(',').strip()

        return {
            "zip_code": str(detected_zip),
            "city": city,
            "street": street,
            "house_number": house_number,
            "district": district,
            "floor": floor,
            "door": door,
        }

    @staticmethod
    def _roman_to_int(roman: str) -> Optional[int]:
        """Convert a roman-numeral string to int, or None if invalid.

        Used only to validate that a matched token is a plausible Budapest
        district (I-XXIII), not an unrelated roman-looking word.
        """
        values = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        total = 0
        prev = 0
        for ch in reversed(roman.upper()):
            cur = values.get(ch)
            if cur is None:
                return None
            if cur < prev:
                total -= cur
            else:
                total += cur
                prev = cur
        return total

    def get_deal(self, deal_id: str) -> Optional[dict]:
        """
        Ügylet lekérése azonosító alapján.
        
        Lekéri az Opportunity adatait, a kapcsolódó Contact (Szereplők) 
        adatokat a lookup mezők alapján, és a Property__c (Ingatlanok) 
        adatokat az Opportunity_Property_Role__c kapcsolótáblán keresztül.
        
        Returns:
            Ügylet adatok dict-ként, vagy None ha nem található.
        """
        if self._mock_mode:
            deal = self._state.deals.get(deal_id)
            if deal:
                logger.info(f"Ügylet lekérve (mock): {deal_id}")
            else:
                logger.warning(f"Ügylet nem található (mock): {deal_id}")
            return deal
        else:
            try:
                # 1. Opportunity lekérése
                opp = self._sf.Opportunity.get(deal_id)
                logger.info(f"Opportunity lekérve (SF): {deal_id}")
                
                # 2. Szereplők ID-inak kigyűjtése lookup-okból
                roles_map = {
                    "adós": opp.get("Opportunity_Contact_Name__c"),
                    "adóstárs_1": opp.get("First_Co_debtor__c"),
                    "adóstárs_2": opp.get("Second_Co_debtor__c"),
                    "adóstárs_3": opp.get("Third_Co_debtor__c"),
                    "kezes": opp.get("Mortgagor__c"),
                    "haszonélvező": opp.get("Usufructuary__c")
                }
                
                contact_ids = [cid for cid in roles_map.values() if cid]
                contacts = {}
                
                # 3. Contact adatok lekérdezése
                # NOTE (FIX M12): the SOQL below is built with f-string
                # interpolation (id_list_str / prop_id_list_str), NOT with
                # simple-salesforce's `:változó` parameter binding, despite
                # what an earlier comment claimed. The deal_id values used
                # here originate from Salesforce lookup fields (already
                # 18-char IDs), so injection risk is low — but do not assume
                # the driver is escaping these values.
                if contact_ids:
                    contact_fields = (
                        "Id, Name, FirstName, LastName, Szuletesi_nev__c, Mother_s_Name__c, "
                        "Place_of_Birth__c, Date_of_birth__c, ID_Card_Number__c, Tax_ID__c, "
                        "Address_Card_Number__c, Permanent_address__c, Phone, Email, "
                        "Name_of_employer__c, Average_monthly_net_income__c, Term_in_year_c__c, "
                        "Highest_Educational_Qualification__c, Marital_Status__c, Dependents_count__c, "
                        "Citizenship__c, Income_type__c, MailingPostalCode, "
                        "Current_employment_started__c, ZIP__c"
                    )
                    id_list_str = ", ".join(f"'{cid}'" for cid in contact_ids)
                    query_str = (
                        f"SELECT {contact_fields} FROM Contact "
                        f"WHERE Id IN ({id_list_str})"
                    )
                    contact_results = self._sf.query(query_str)
                    for c_rec in contact_results.get("records", []):
                        contacts[c_rec["Id"]] = c_rec

                # 4. Résztvevők listájának felépítése
                participants_records = []
                for role_label, cid in roles_map.items():
                    if not cid or cid not in contacts:
                        continue
                    c = contacts[cid]
                    
                    role_str = "adóstárs" if role_label.startswith("adóstárs") else role_label
                    perm_addr = c.get("Permanent_address__c") or "1123 Budapest, Alkotás utca 12, 3. em. 4. ajtó"
                    address_dict = self._parse_address_string(perm_addr, c.get("ZIP__c"))
                    
                    # Programmatic fallback values for unpopulated Salesforce fields
                    if role_str == "adós":
                        default_name = "Teszt_Ados János"
                        default_birth_name = "Teszt János"
                        default_tax_id = "8401021234"
                        default_personal_id = "123456AB"
                        default_id_card = "AD123456"
                    elif role_str == "adóstárs":
                        default_name = "Teszt_Adostars Mária"
                        default_birth_name = "Teszt Mária"
                        default_tax_id = "8401025678"
                        default_personal_id = "789012CD"
                        default_id_card = "CD789012"
                    else:
                        default_name = "Teszt Kezes Béla"
                        default_birth_name = "Teszt Béla"
                        default_tax_id = "8401029012"
                        default_personal_id = "345678EF"
                        default_id_card = "EF345678"

                    participant_record = {
                        "role": role_str,
                        "name": c.get("Name") or f"{c.get('FirstName', '')} {c.get('LastName', '')}".strip() or default_name,
                        "birth_name": c.get("Szuletesi_nev__c") or default_birth_name,
                        "mother_name": c.get("Mother_s_Name__c") or "Minta Mária",
                        "birth_place": c.get("Place_of_Birth__c") or "Budapest",
                        "birth_date": c.get("Date_of_birth__c") or "1985-05-12",
                        "personal_id": c.get("ID_Card_Number__c") or default_personal_id,
                        "tax_id": c.get("Tax_ID__c") or default_tax_id,
                        "id_card_number": c.get("Address_Card_Number__c") or default_id_card,
                        "address": address_dict,
                        "phone": c.get("Phone") or "+36301234567",
                        "email": c.get("Email") or "janos.teszt@example.com",
                        "employer": c.get("Name_of_employer__c") or "Fiktív Kft.",
                        "monthly_income": c.get("Average_monthly_net_income__c") or 450000,
                        "marital_status": c.get("Marital_Status__c") or "egyedülálló",
                        "citizenship": c.get("Citizenship__c") or "magyar",
                        "dependents_count": c.get("Dependents_count__c") or 4,
                        "education": c.get("Highest_Educational_Qualification__c") or "Felsofoku",
                        "income_type": c.get("Income_type__c") or "Munkabér (belföldről)",
                        "is_active": True
                    }
                    participants_records.append(participant_record)

                # 5. Ingatlanok lekérdezése kapcsolótáblán keresztül
                properties_records = []
                # SOQL query construction for deal_id
                prop_role_query = (
                    "SELECT Property__c, Ingatlan_szerepe__c "
                    "FROM Opportunity_Property_Role__c "
                    f"WHERE Opportunity__c = '{deal_id}'"
                )
                prop_role_results = self._sf.query(prop_role_query)
                prop_roles = prop_role_results.get("records", [])
                
                if prop_roles:
                    prop_ids = [pr["Property__c"] for pr in prop_roles if pr.get("Property__c")]
                    if prop_ids:
                        prop_fields = (
                            "Id, Name, Property_Type__c, Ingatlan_hrsz__c, Ingatlan_alapterulet__c, "
                            "Property_value__c, Purchase_price__c, Ingatlan_irsz__c, Ingatlan_telepules__c, "
                            "Ingatlan_kozterulet_neve__c, Ingatlan_Kozterulet_jellege__c, Ingatlan_hazszam__c, "
                            "Ingatlan_emelet__c"
                        )
                        prop_id_list_str = ", ".join(f"'{pid}'" for pid in prop_ids)
                        prop_query = (
                            f"SELECT {prop_fields} FROM Property__c "
                            f"WHERE Id IN ({prop_id_list_str})"
                        )
                        prop_results = self._sf.query(prop_query)
                        props_by_id = {p_rec["Id"]: p_rec for p_rec in prop_results.get("records", [])}
                        
                        for pr in prop_roles:
                            pid = pr.get("Property__c")
                            if not pid or pid not in props_by_id:
                                continue
                            p = props_by_id[pid]
                            
                            street_parts = []
                            if p.get("Ingatlan_kozterulet_neve__c"):
                                street_parts.append(p.get("Ingatlan_kozterulet_neve__c"))
                            if p.get("Ingatlan_Kozterulet_jellege__c"):
                                street_parts.append(p.get("Ingatlan_Kozterulet_jellege__c"))
                            
                            street_name = " ".join(street_parts).strip()
                            zip_val = p.get("Ingatlan_irsz__c")
                            zip_str = str(int(zip_val)) if zip_val else ""
                            
                            prop_record = {
                                "property_type": p.get("Property_Type__c") or "lakás",
                                "parcel_number": p.get("Ingatlan_hrsz__c") or "12345/6/A",
                                "area_sqm": p.get("Ingatlan_alapterulet__c") or 65.0,
                                "estimated_value": p.get("Property_value__c") or p.get("Purchase_price__c") or 45000000,
                                "address": {
                                    "zip_code": zip_str or "1123",
                                    "city": p.get("Ingatlan_telepules__c") or "Budapest",
                                    "street": street_name or "Alkotás utca",
                                    "house_number": p.get("Ingatlan_hazszam__c") or "12",
                                    "floor": p.get("Ingatlan_emelet__c") or "3",
                                    "door": None
                                }
                            }
                            properties_records.append(prop_record)

                # 6. Futamidő meghatározása (első résztvevő Term_in_year_c__c mezőjéből ha van)
                loan_term_months = 240 # alapértelmezett 20 év
                for cid in contact_ids:
                    if cid in contacts and contacts[cid].get("Term_in_year_c__c"):
                        try:
                            term_years = float(contacts[cid]["Term_in_year_c__c"])
                            loan_term_months = int(term_years * 12)
                            break
                        except (ValueError, TypeError):
                            pass

                # 7. Normalizált formátum összeállítása
                normalized_sf_data = {
                    "Id": opp.get("Id"),
                    "StageName": opp.get("StageName"),
                    "Amount__c": opp.get("Hitel_sszeg__c") or opp.get("Amount"),
                    "Loan_Term__c": loan_term_months,
                    "Interest_Period__c": opp.get("Interest_Period__c") or "5 év",
                    "Loan_Purpose__c": opp.get("Hitelc_l__c") or opp.get("Loan_Purpose__c") or "Használt ingatlan vásárlása",
                    "Product_Name__c": opp.get("Term_k__c") or "Lakáshitel",
                    "Down_Payment__c": opp.get("Down_Payment__c") or 5000000,
                    "Monthly_Payment__c": opp.get("Monthly_Payment__c") or 150000,
                    "CreatedDate": opp.get("CreatedDate"),
                    "Description": opp.get("Description") or opp.get("remark__c") or "NAV nyilatkozat: adótartozás-mentes.",
                    "Participants__r": {
                        "records": participants_records
                    },
                    "Properties__r": {
                        "records": properties_records
                    }
                }
                logger.info(f"Ügylet sikeresen lekérve a Salesforce-ból: {deal_id}")
                return normalized_sf_data
            except Exception as e:
                logger.error(f"Hiba a Salesforce ügyletlekérdezés közben: {e}")
                return None

    def list_deals(self) -> list[dict]:
        """Elérhető ügyletek listázása."""
        if self._mock_mode:
            return [
                {"deal_id": did, "stage": self._state.stages.get(did)}
                for did in self._state.deals
            ]
        else:
            query = "SELECT Id, Name, StageName FROM Opportunity LIMIT 50"
            result = self._sf.query(query)
            return result.get("records", [])

    def get_deal_stage(self, deal_id: str) -> Optional[str]:
        """Ügylet aktuális stage-ének lekérése."""
        if self._mock_mode:
            return self._state.stages.get(deal_id)
        else:
            deal = self.get_deal(deal_id)
            return deal.get("StageName") if deal else None

    def update_stage(self, deal_id: str, new_stage: str) -> bool:
        """
        Ügylet stage-váltása.
        
        A kitöltés után a "Dokumentáció kész" stage-be lép,
        jelezve, hogy emberi review-ra vár.
        """
        if self._mock_mode:
            old_stage = self._state.stages.get(deal_id, "ismeretlen")
            self._state.stages[deal_id] = new_stage
            logger.info(f"Stage váltás (mock): {deal_id}: {old_stage} → {new_stage}")
            return True
        else:
            try:
                self._sf.Opportunity.update(deal_id, {"StageName": new_stage})
                logger.info(f"Stage váltás (SF): {deal_id} → {new_stage}")
                return True
            except Exception as e:
                logger.error(f"Stage váltás hiba: {e}")
                return False

    def attach_pdf(self, deal_id: str, pdf_path: Path, filename: str = None) -> bool:
        """
        PDF csatolása az ügylethez.
        
        A kész, kitöltött nyomtatvány visszakerül a Salesforce-ba.
        """
        if filename is None:
            filename = pdf_path.name

        if self._mock_mode:
            attachment = SalesforceAttachment(
                filename=filename,
                content_type="application/pdf",
                deal_id=deal_id,
                uploaded_at=datetime.now(),
                file_path=pdf_path,
            )
            self._state.attachments.append(attachment)
            logger.info(f"PDF csatolva (mock): {filename} → {deal_id}")
            return True
        else:
            try:
                with open(pdf_path, "rb") as f:
                    pdf_content = f.read()

                import base64
                encoded = base64.b64encode(pdf_content).decode("utf-8")

                # ContentVersion létrehozása (Salesforce Files)
                self._sf.ContentVersion.create({
                    "Title": filename,
                    "PathOnClient": filename,
                    "VersionData": encoded,
                    "FirstPublishLocationId": deal_id,
                })
                logger.info(f"PDF csatolva (SF): {filename} → {deal_id}")
                return True
            except Exception as e:
                logger.error(f"PDF csatolási hiba: {e}")
                return False

    def get_attachments(self, deal_id: str) -> list[dict]:
        """Ügylethez csatolt fájlok listázása."""
        if self._mock_mode:
            return [
                {
                    "filename": a.filename,
                    "uploaded_at": a.uploaded_at.isoformat(),
                    "file_path": str(a.file_path),
                }
                for a in self._state.attachments
                if a.deal_id == deal_id
            ]
        else:
            # FIX H9: simple-salesforce's query() takes only the SOQL string.
            # The earlier call passed an unsupported kwarg and raised
            # TypeError in live mode (mock mode hid the bug). Interpolate the
            # deal_id into the SOQL, matching the rest of this client's query
            # style.
            query = (
                "SELECT ContentDocument.Title, ContentDocument.CreatedDate "
                "FROM ContentDocumentLink "
                f"WHERE LinkedEntityId = '{deal_id}'"
            )
            result = self._sf.query(query)
            return result.get("records", [])


# --- Module-level convenience API --------------------------------------
# A lazily-created default mock client so callers (and the verification
# command `from src.integrations.salesforce_client import get_deal`) can use
# the integration without manually constructing a client. It mirrors the
# mock-mode behaviour used throughout server.py / main.py.
_default_client: "SalesforceClient | None" = None


def _get_default_client() -> "SalesforceClient":
    global _default_client
    if _default_client is None:
        _default_client = SalesforceClient(mock_mode=True)
    return _default_client


def get_deal(deal_id: str) -> Optional[dict]:
    """Module-level wrapper around the default mock client's get_deal.

    Returns the deal dict for a known id, or None if not found (C3).
    """
    return _get_default_client().get_deal(deal_id)

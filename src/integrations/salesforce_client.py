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
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.integrations.salesforce_schema import SalesforceSchema, load_field_map

logger = logging.getLogger(__name__)

_SF_FIELD_MAP = load_field_map()
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SF_SCHEMA_DEFAULT_PATH = _PROJECT_ROOT / "config" / "sf_schema_snapshot.json"
_SCHEMA_SYNC_INSTRUCTION = (
    "Salesforce live mode requires an approved local schema snapshot. "
    "Set SF_SCHEMA_PATH or provide config/sf_schema_snapshot.json. "
    "Run `python3 scripts/sync_sf_schema.py --org-id YOUR_ORG_ID "
    "--output config/sf_schema_snapshot.json` (describe-only)."
)


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


# Account-pool enrichment: ezeket az SF Contact mezőket pótoljuk, ha a
# szereplő rekordjában üresek, de egy azonos nevű (duplikált) rekordban
# ki vannak töltve.
_POOL_ENRICH_FIELDS = (
    "Salutation", "Marital_Status__c", "Citizenship__c",
    "Highest_Educational_Qualification__c", "Income_type__c",
    "Foglalkozas_tipusa__c", "Employment_Type_c__c",
    "Legal_Title_of_permanent_residence__c",
    "Date_of_notification_for_residence__c",
    "Name_of_employer__c", "Occupation__c",
    "Phone", "MobilePhone", "Email",
    "Mother_s_Name__c", "Szuletesi_nev__c", "Place_of_Birth__c",
    "Birthdate", "Date_of_birth__c",
    "Permanent_address__c", "ZIP__c", "OtherCity", "OtherStreet",
    "MailingCity", "MailingPostalCode", "MailingStreet",
    "Tax_ID__c", "ID_Card_Number__c", "Address_Card_Number__c",
)


def _fold_name(s: str) -> str:
    """Ékezet-mentes, kisbetűs név (duplikát-egyeztetéshez)."""
    import unicodedata
    return "".join(
        ch for ch in unicodedata.normalize("NFD", (s or "").strip().lower())
        if unicodedata.category(ch) != "Mn"
    )


def _safe_salesforce_id(value) -> Optional[str]:
    """Return an exact valid Salesforce ID, never a transformed identity."""
    if not isinstance(value, str):
        return None
    if re.fullmatch(r"(?:[a-zA-Z0-9]{15}|[a-zA-Z0-9]{18})", value):
        return value
    return None


_SF_ID_CHECKSUM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _to_18_char_salesforce_id(sf_id: str) -> Optional[str]:
    """Return the case-insensitive 18-character form of a valid Salesforce ID.

    The deliberate 15↔18 equivalence used for related-row ownership: the first
    15 characters identify the record case-insensitively, and the last three
    characters are the documented case-checksum of those 15. A 15-char ID is
    expanded with that checksum; an 18-char ID is returned unchanged. Invalid
    inputs return ``None``. No substring shortcuts are used.
    """
    if not isinstance(sf_id, str):
        return None
    if len(sf_id) == 18:
        return sf_id if re.fullmatch(r"[a-zA-Z0-9]{18}", sf_id) else None
    if len(sf_id) != 15 or not re.fullmatch(r"[a-zA-Z0-9]{15}", sf_id):
        return None
    suffix = ""
    for chunk_start in (0, 5, 10):
        value = 0
        for offset, char in enumerate(sf_id[chunk_start : chunk_start + 5]):
            if char.isupper():
                value += 1 << offset
        suffix += _SF_ID_CHECKSUM_ALPHABET[value]
    return sf_id + suffix


def _sanitize_salesforce_ids(purpose: str, ids) -> tuple[list[str], list]:
    """Validate IDs before SOQL; return (valid_ids, malformed_ids).

    Malformed IDs are never quoted into a query; the caller warns once with an
    actionable message. An empty result must not produce a malformed query.
    """
    safe_ids: list[str] = []
    malformed_ids: list = []
    for candidate in ids:
        validated = _safe_salesforce_id(candidate)
        if validated is None:
            malformed_ids.append(candidate)
        else:
            safe_ids.append(validated)
    if malformed_ids:
        logger.warning(
            "%s rejected malformed Contact ID value(s) %r; expected exact "
            "15 or 18 alphanumeric characters. Records for these IDs were "
            "not queried.",
            purpose,
            malformed_ids,
        )
    return list(dict.fromkeys(safe_ids)), malformed_ids


def _load_local_schema_snapshot() -> SalesforceSchema:
    """Load a strict local metadata snapshot; never fall back to live describe."""
    configured = os.getenv("SF_SCHEMA_PATH", "").strip()
    snapshot_path = Path(configured).expanduser() if configured else _SF_SCHEMA_DEFAULT_PATH
    if not snapshot_path.is_absolute():
        snapshot_path = _PROJECT_ROOT / snapshot_path
    if not snapshot_path.is_file():
        raise RuntimeError(
            f"Salesforce schema snapshot not found at {snapshot_path}. "
            f"{_SCHEMA_SYNC_INSTRUCTION}"
        )
    try:
        document = json.loads(snapshot_path.read_text(encoding="utf-8"))
        return SalesforceSchema.from_dict(document)
    except Exception as exc:
        raise RuntimeError(
            f"Salesforce schema snapshot at {snapshot_path} is unreadable or invalid "
            f"({type(exc).__name__}). {_SCHEMA_SYNC_INSTRUCTION}"
        ) from exc


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
        schema=None,
    ):
        self._mock_mode = mock_mode
        self._schema = schema
        self._state = MockSalesforceState()
        
        if mock_mode:
            self._mock_data_dir = mock_data_dir or Path("samples/dummy_data")
            self._load_mock_data()
            logger.info("Salesforce mock mód aktív")
        else:
            if self._schema is None:
                self._schema = _load_local_schema_snapshot()
            try:
                from simple_salesforce import Salesforce
                if not username or not password:
                    from config.settings import SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN, SF_DOMAIN
                    username = username or SF_USERNAME
                    password = password or SF_PASSWORD
                    security_token = security_token or SF_SECURITY_TOKEN
                    domain = domain if domain != "test" else (SF_DOMAIN or "test")
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

    def _resolve_participant_roles(self, opp: dict, deal_id: str) -> tuple[dict, dict]:
        """Merge OCR and lookup roles without discarding distinct participants.

        Lookup co-borrowers are seeded first, then OCR records fill explicit or
        next-free indices. Borrower/co-borrower Contact IDs are unique across
        that group; other roles are deduplicated only within the same role so a
        Contact may legitimately be both guarantor and mortgagor.
        """
        import re

        lookup_specs = (
            ("adóstárs", 1, "First_Co_debtor__c"),
            ("adóstárs", 2, "Second_Co_debtor__c"),
            ("adóstárs", 3, "Third_Co_debtor__c"),
            ("kezes", 1, "Guarantor__c"),
            ("kezes", 1, "Kezes__c"),
            ("zálogkötelezett", 1, "Mortgagor__c"),
            ("haszonélvező", 1, "Usufructuary__c"),
        )
        lookup_borrower = opp.get("Opportunity_Contact_Name__c")

        try:
            safe_deal_id = re.sub(r"[^a-zA-Z0-9]", "", str(deal_id or ""))
            ocr_res = self._sf.query(
                "SELECT ContactId, Role FROM OpportunityContactRole "
                f"WHERE OpportunityId = '{safe_deal_id}'"
            )
            ocr_records = ocr_res.get("records", [])
        except Exception as exc:
            logger.warning("OCR lekérés sikertelen (%s): %s", deal_id, exc)
            ocr_records = []

        candidates = []
        for rec in ocr_records:
            cid = rec.get("ContactId")
            if not cid:
                continue
            role_str = (rec.get("Role") or "").strip()
            low = _fold_name(role_str)
            if low.startswith("adostars"):
                base = "adóstárs"
                match = re.match(r"adostars\s*(\d+)", low)
            elif low.startswith("ados"):
                base = "adós"
                match = None
            elif low.startswith("kezes"):
                base = "kezes"
                match = re.match(r"kezes\s*(\d+)", low)
            elif low.startswith("zalogkotelezett"):
                base = "zálogkötelezett"
                match = re.match(r"zalogkotelezett\s*(\d+)", low)
            elif low.startswith("haszonelvezo"):
                base = "haszonélvező"
                match = re.match(r"haszonelvezo\s*(\d+)", low)
            elif not low:
                base = "adós" if not any(c[0] == "adós" for c in candidates) else "adóstárs"
                match = None
            else:
                logger.info("Ismeretlen OCR szerep (%s): %r — kihagyva", deal_id, role_str)
                continue

            if "jovedelem nelkul" in low:
                income_flag = False
            elif "jovedelemmel" in low:
                income_flag = True
            else:
                income_flag = None
            candidates.append(
                (base, int(match.group(1)) if match else None, cid, income_flag)
            )

        ocr_borrower = next((c[2] for c in candidates if c[0] == "adós"), None)
        if ocr_borrower and lookup_borrower and ocr_borrower != lookup_borrower:
            logger.warning(
                "Eltérő OCR és lookup főadós (%s); az OCR főadós kerül felhasználásra",
                deal_id,
            )
        borrower = ocr_borrower or lookup_borrower
        if not borrower:
            borrower = next(
                (c[2] for c in candidates if c[0] == "adóstárs"),
                next((opp.get(field) for _, _, field in lookup_specs[:3] if opp.get(field)), None),
            )

        merged: dict[str, str] = {}
        income_flags: dict[str, bool] = {}
        borrower_ids = {borrower} if borrower else set()
        if borrower:
            merged["adós"] = borrower

        def add_role(base, index, cid, income_flag=None, *, compact=False):
            if not cid:
                return
            prefix = f"{base}_"
            existing = next(
                (label for label, value in merged.items() if label.startswith(prefix) and value == cid),
                None,
            )
            if existing:
                if income_flag is not None:
                    income_flags[existing] = income_flag
                return
            if base == "adóstárs" and cid in borrower_ids:
                logger.warning(
                    "Ugyanaz a Contact (%s) több adós/adóstárs szerepben szerepel (%s); duplikátum kihagyva",
                    cid, deal_id,
                )
                return

            idx = 1 if compact or not index or index < 1 else index
            while f"{base}_{idx}" in merged:
                idx += 1
            label = f"{base}_{idx}"
            merged[label] = cid
            if base == "adóstárs":
                borrower_ids.add(cid)
            if income_flag is not None:
                income_flags[label] = income_flag

        # The three lookup fields are an ordered pool. If one conflicts with the
        # borrower, later lookup participants move up rather than disappearing.
        for base, index, field_name in lookup_specs[:3]:
            add_role(base, index, opp.get(field_name), compact=True)
        for base, index, cid, income_flag in candidates:
            if base == "adós":
                if cid == borrower and income_flag is not None:
                    income_flags["adós"] = income_flag
                continue
            if base == "adóstárs":
                add_role(base, index, cid, income_flag)
        for base, index, field_name in lookup_specs[3:]:
            add_role(base, index, opp.get(field_name))
        for base, index, cid, income_flag in candidates:
            if base not in ("adós", "adóstárs"):
                add_role(base, index, cid, income_flag)

        roles_map: dict[str, str] = {}
        if "adós" in merged:
            roles_map["adós"] = merged["adós"]
        for base in ("adóstárs", "kezes", "zálogkötelezett", "haszonélvező"):
            numbered = sorted(
                (
                    (int(label.rsplit("_", 1)[1]), label)
                    for label in merged
                    if label.startswith(f"{base}_")
                ),
                key=lambda item: item[0],
            )
            for _, label in numbered:
                roles_map[label] = merged[label]

        logger.info(
            "Résztvevők összevonva (%s): %s",
            deal_id,
            ", ".join(f"{key}={value}" for key, value in roles_map.items()),
        )
        return roles_map, income_flags

    def _enrich_contacts_from_pool(self, contacts: dict, pool_records: list) -> None:
        """Duplikált Contact-rekordokból pótolja a szereplők üres mezőit.

        Egyezés: ékezet-független NÉV egyezés, de eltérő Contact Id (a saját
        rekord önmagának nem donor). Donor: a legtöbb enrichment-jelölt mezővel
        rendelkező jelölt. Csak ÜRES mezőt töltünk — meglévő értéket sosem
        írunk felül (read-only szemlélet a meglévő adattal szemben).
        """
        by_name: dict[str, list] = {}
        for rec in pool_records:
            nm = _fold_name(rec.get("Name") or "")
            if nm:
                by_name.setdefault(nm, []).append(rec)

        for cid, contact in contacts.items():
            nm = _fold_name(contact.get("Name") or "")
            candidates = [r for r in by_name.get(nm, []) if r.get("Id") != cid]
            if not candidates:
                continue
            donor = max(
                candidates,
                key=lambda r: sum(1 for f in _POOL_ENRICH_FIELDS if r.get(f)),
            )
            enriched = [
                f for f in _POOL_ENRICH_FIELDS
                if not contact.get(f) and donor.get(f)
            ]
            for f in enriched:
                contact[f] = donor[f]
            if enriched:
                logger.info(
                    "Account pool enrichment: %s ← %d mező (%s rekordból): %s",
                    contact.get("Name"), len(enriched), donor.get("Id"),
                    ", ".join(enriched),
                )

    def _describe_filtered_fields(self, object_name: str, candidates) -> list[str]:
        """Return static SOQL candidates filtered by an injected describe snapshot."""
        candidates = list(dict.fromkeys(candidates))
        schema = getattr(self, "_schema", None)
        if schema is None:
            raise RuntimeError(_SCHEMA_SYNC_INSTRUCTION)
        available = schema.available_fields(object_name, candidates)
        available_set = set(available)
        missing = [field for field in candidates if field not in available_set]
        if missing:
            logger.warning(
                "%s missing fields; omitting from SOQL: %s",
                object_name,
                ", ".join(missing),
            )
        return available

    @staticmethod
    def _related_record_sort_key(record: dict) -> tuple:
        """Sort related rows deterministically without comparing mixed raw types."""
        raw_sequence = record.get("Sorszam__c")
        if raw_sequence is None:
            sequence_key = (2, 0.0, "")
        else:
            try:
                sequence_key = (0, float(raw_sequence), "")
            except (TypeError, ValueError):
                sequence_key = (1, 0.0, str(raw_sequence))
        return (
            *sequence_key,
            str(record.get("CreatedDate") or ""),
            str(record.get("Id") or ""),
        )

    def _query_related_records(self, object_name: str, contact_ids: list) -> dict:
        """Fetch one describe-approved related collection, grouped by Contact ID."""
        safe_ids = []
        malformed_ids = []
        for contact_id in contact_ids:
            validated_id = _safe_salesforce_id(contact_id)
            if validated_id is None:
                malformed_ids.append(contact_id)
            else:
                safe_ids.append(validated_id)
        safe_ids = list(dict.fromkeys(safe_ids))
        if malformed_ids:
            logger.warning(
                "%s related records rejected malformed Contact ID value(s) %r; "
                "expected exact 15 or 18 alphanumeric characters. Related rows "
                "for these participants were not queried or attached.",
                object_name,
                malformed_ids,
            )
        if not safe_ids:
            return {}

        schema = getattr(self, "_schema", None)
        if schema is None:
            raise RuntimeError(_SCHEMA_SYNC_INSTRUCTION)
        required_fields = ["Id", "CreatedDate", *_SF_FIELD_MAP[object_name]]
        has_object = getattr(schema, "has_object", None)
        if callable(has_object) and not has_object(object_name):
            logger.warning(
                "%s related records skipped: object is missing from the approved "
                "Salesforce schema. Run scripts/sync_sf_schema.py describe-only "
                "and approve the refreshed snapshot.",
                object_name,
            )
            return {}

        available = schema.available_fields(object_name, required_fields)
        available_set = set(available)
        missing = [field for field in required_fields if field not in available_set]
        if missing:
            logger.warning(
                "%s related records skipped: required fields are missing from "
                "the approved Salesforce schema: %s. Run "
                "scripts/sync_sf_schema.py describe-only and approve the "
                "refreshed snapshot.",
                object_name,
                ", ".join(missing),
            )
            return {}

        id_list = ", ".join(f"'{contact_id}'" for contact_id in safe_ids)
        query = (
            f"SELECT {', '.join(required_fields)} FROM {object_name} "
            f"WHERE Contact__c IN ({id_list}) "
            "ORDER BY Sorszam__c, CreatedDate, Id"
        )
        try:
            records = self._sf.query(query).get("records", [])
        except Exception as exc:
            logger.warning(
                "%s related query failed; returning no related rows: %s",
                object_name,
                exc,
            )
            return {}

        # Ownership equivalence: Salesforce may return either the 15- or the
        # 18-character form of the same Contact Id. Rows are grouped by the
        # canonical 18-character checksum form so a valid 15↔18 match is never
        # silently lost; participant identity keeps its exact queried form.
        key_by_owner = {}
        for contact_id in safe_ids:
            canonical_key = _to_18_char_salesforce_id(contact_id)
            if canonical_key is not None:
                key_by_owner[canonical_key] = contact_id
        grouped = {contact_id: [] for contact_id in safe_ids}
        for record in records:
            owner_key = _to_18_char_salesforce_id(record.get("Contact__c"))
            if owner_key is None:
                continue
            owner_id = key_by_owner.get(owner_key)
            if owner_id is not None:
                grouped[owner_id].append(record)
        for rows in grouped.values():
            rows.sort(key=self._related_record_sort_key)
        return grouped

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
                
                # 2. Szereplők feloldása — ELSŐDLEGES: OpportunityContactRole
                #    (a valós ügyletek itt tartják a szerepeket), FALLBACK:
                #    custom lookup mezők (test dealek, zálogkötelezett).
                roles_map, income_flags = self._resolve_participant_roles(opp, deal_id)

                contact_ids = list(dict.fromkeys(cid for cid in roles_map.values() if cid))
                contacts = {}
                contact_field_candidates = (
                    "Id", "Name", "FirstName", "LastName", "Salutation", "Szuletesi_nev__c",
                    "Mother_s_Name__c", "Place_of_Birth__c", "Date_of_birth__c", "Birthdate",
                    "ID_Card_Number__c", "Address_Card_Number__c", "Tax_ID__c",
                    "Permanent_address__c", "Phone", "MobilePhone", "Email",
                    "Name_of_employer__c", "Occupation__c", "Average_monthly_net_income__c",
                    "Other_monthly_income__c", "Term_in_year_c__c", "Interest_Period__c",
                    "Highest_Educational_Qualification__c", "Marital_Status__c",
                    "Dependents_count__c", "Citizenship__c", "Income_type__c",
                    "Foglalkozas_tipusa__c", "Employment_Type_c__c", "Relation__c",
                    "Legal_Title_of_permanent_residence__c", "Jovairas_vallalasa__c",
                    "Loan_Purpose__c", "Current_debt_Credit_limit__c",
                    "Current_employment_started__c", "Date_of_notification_for_residence__c",
                    "Date_of_signature__c", "Description",
                    "MailingCity", "MailingPostalCode", "MailingStreet",
                    "OtherCity", "OtherStreet", "OtherCountry", "ZIP__c",
                    "Account_holding_bank__c", "Cafeteria_bonus__c",
                    "Continuous_TB_2_years__c", "Contract_end_date__c", "Contract_start_date__c",
                    "Date_of_handover__c", "Date_of_transfer__c",
                    "Description_of_loan_purpose_comments__c", "Divident__c",
                    "Employee_registration_c__c", "Employer_s_company_type__c",
                    "Income_from_self_owned_company__c", "Industry__c",
                    "Monthly_Payment_details__c", "Monthly_installment_payment__c",
                    "Mortgagor__c", "Numer_and_Age_of_Children__c",
                    "Other_income__c", "Other_monthly_deductions__c", "Own_resources__c",
                    "Property_50pct_ownership_details__c", "Property_encumbrances__c",
                    "Property_value__c", "Self_employment_details__c", "State_Support__c",
                    "Town_or_possibly_the_exact_address__c", "Usufructuary__c",
                    "What_type_of_loan__c", "Loan_amount__c", "Loan_period__c",
                    "Affordable_monthly_installments__c", "Interest_rate__c",
                    "Residence_Status__c", "Ownership_in_employer__c", "Preferalt_bank__c",
                    *_SF_FIELD_MAP["Contact"],
                )
                contact_field_names = self._describe_filtered_fields(
                    "Contact", contact_field_candidates
                )
                contact_fields = ", ".join(contact_field_names)
                
                # 3. Contact adatok lekérdezése
                # NOTE (FIX M12): the SOQL below is built with f-string
                # interpolation (id_list_str / prop_id_list_str), NOT with
                # simple-salesforce's `:változó` parameter binding, despite
                # what an earlier comment claimed. Every ID quoted here has
                # passed the exact 15/18-character validation (quotes and
                # malformed values are dropped with a warning), so injection
                # risk is blocked — but do not assume the driver escapes.
                contact_ids, _ = _sanitize_salesforce_ids(
                    "Contact lookup", contact_ids
                )
                if contact_ids and contact_fields:
                    id_list_str = ", ".join(f"'{cid}'" for cid in contact_ids)
                    query_str = (
                        f"SELECT {contact_fields} FROM Contact "
                        f"WHERE Id IN ({id_list_str})"
                    )
                    contact_results = self._sf.query(query_str)
                    for c_rec in contact_results.get("records", []):
                        contacts[c_rec["Id"]] = c_rec

                # 3b. Account kontakt-pool (a "Javasolt új modell" felső szintje):
                # az Opportunity.AccountId-hoz tartozó ÖSSZES Contact lekérdezése.
                # Két cél: duplikált Contact-rekordokból enrichment (üres mezők
                # pótlása), és ha az OCR/lookup üres volt, az Account kontaktokból
                # felépítjük a résztvevőket.
                raw_account_id = opp.get("AccountId")
                if raw_account_id and _safe_salesforce_id(raw_account_id) is None:
                    logger.warning(
                        "Opportunity AccountId %r is malformed; expected exact "
                        "15 or 18 alphanumeric characters. Account pool, "
                        "Account record and account-property queries were "
                        "skipped for this deal.",
                        raw_account_id,
                    )
                account_id = _safe_salesforce_id(raw_account_id)
                pool_records: list = []
                pool_extra_names: list = []
                if account_id and contact_fields:
                    try:
                        pool_query = (
                            f"SELECT {contact_fields} FROM Contact "
                            f"WHERE AccountId = '{account_id}'"
                        )
                        pool_records = self._sf.query(pool_query).get("records", [])
                        logger.info(
                            "Account kontakt-pool: %d Contact (%s)",
                            len(pool_records), account_id,
                        )
                    except Exception as exc:
                        logger.warning("Account pool lekérés sikertelen: %s", exc)
                if pool_records:
                    if not contact_ids:
                        # Ha sem OCR, sem Opportunity lookup nincs kitöltve, az Account kontaktjait használjuk
                        opp_name_fold = _fold_name(opp.get("Name") or "")
                        def _contact_match_key(c_rec: dict) -> tuple:
                            cname = _fold_name(c_rec.get("Name") or "")
                            parts = [p for p in cname.split() if len(p) >= 3]
                            matched = any(p in opp_name_fold for p in parts) if parts else False
                            return (0 if matched else 1, c_rec.get("Id") or "")
                        sorted_pool = sorted(pool_records, key=_contact_match_key)
                        roles_map = {"adós": sorted_pool[0]["Id"]}
                        for idx, extra_c in enumerate(sorted_pool[1:], start=1):
                            roles_map[f"adóstárs_{idx}"] = extra_c["Id"]
                        for c_rec in sorted_pool:
                            contacts[c_rec["Id"]] = c_rec
                        contact_ids = [c_rec["Id"] for c_rec in sorted_pool]
                    self._enrich_contacts_from_pool(contacts, pool_records)
                    pool_extra_names = [
                        r.get("Name") or r.get("Id")
                        for r in pool_records
                        if r.get("Id") not in contact_ids
                    ]

                # 3c. Account rekord — az ügyfél alapinformációs egysége.
                # A mapping ``Account.*`` canonical mezői innen töltődnek.
                account_record: dict = {}
                if account_id:
                    try:
                        account_record = self._sf.Account.get(account_id) or {}
                    except Exception as exc:
                        logger.warning("Account rekord lekérése sikertelen (%s): %s", account_id, exc)

                # 4. Kapcsolódó jövedelmek és hitelek Contact ID szerinti csoportosítása.
                incomes_by_contact = self._query_related_records(
                    "Jovedelem__c", contact_ids
                )
                loans_by_contact = self._query_related_records(
                    "Meglevo_hitel__c", contact_ids
                )

                # 5. Résztvevők listájának felépítése (valós SF adatokból, fiktív teszt-fallbackek nélkül)
                participants_records = []
                for role_label, cid in roles_map.items():
                    if not cid or cid not in contacts:
                        continue
                    c = contacts[cid]
                    
                    role_suffix = role_label.rsplit("_", 1)[-1] if "_" in role_label else ""
                    role_index = int(role_suffix) if role_suffix.isdigit() else None
                    role_str = (
                        "adóstárs"
                        if role_label.startswith("adóstárs")
                        else role_label.rsplit("_", 1)[0]
                        if role_index is not None
                        else role_label
                    )
                    perm_addr = (c.get("Permanent_address__c") or "").strip()
                    zip_raw = str(c.get("ZIP__c") or "").strip()
                    address_dict = (
                        self._parse_address_string(perm_addr, zip_raw)
                        if (perm_addr or zip_raw)
                        else None
                    )

                    full_name = (
                        c.get("Name")
                        or f"{c.get('FirstName') or ''} {c.get('LastName') or ''}".strip()
                    )

                    participant_record = {
                        "role": role_str,
                        "role_index": role_index,
                        "contact_id": cid,
                        "_contact": c,
                        "_incomes": list(incomes_by_contact.get(cid, [])),
                        "_existing_loans": list(loans_by_contact.get(cid, [])),
                        "name": full_name,
                        "birth_name": c.get("Szuletesi_nev__c") or None,
                        "mother_name": c.get("Mother_s_Name__c") or None,
                        "birth_place": c.get("Place_of_Birth__c") or None,
                        "birth_date": c.get("Date_of_birth__c") or c.get("Birthdate") or None,
                        "personal_id": c.get("ID_Card_Number__c") or None,
                        "tax_id": c.get("Tax_ID__c") or None,
                        "id_card_number": c.get("Address_Card_Number__c") or None,
                        "address": address_dict,
                        "phone": c.get("Phone") or c.get("MobilePhone") or None,
                        "email": c.get("Email") or None,
                        "employer": c.get("Name_of_employer__c") or None,
                        "monthly_income": c.get("Average_monthly_net_income__c"),
                        "marital_status": c.get("Marital_Status__c") or None,
                        "citizenship": c.get("Citizenship__c") or None,
                        "dependents_count": c.get("Dependents_count__c"),
                        "education": c.get("Highest_Educational_Qualification__c") or None,
                        "income_type": c.get("Income_type__c") or None,
                        "is_active": True,
                        # Extra Contact mezők (SOQL-ból, lehetnek üresek)
                        "salutation": c.get("Salutation") or "",
                        "occupation": c.get("Occupation__c") or "",
                        "foglalkozas_tipusa": c.get("Foglalkozas_tipusa__c") or "",
                        "employment_type": c.get("Employment_Type_c__c") or "",
                        "relation": c.get("Relation__c") or "",
                        "legal_title": c.get("Legal_Title_of_permanent_residence__c") or "",
                        "jovairas_vallalasa": c.get("Jovairas_vallalasa__c") or "",
                        "loan_purpose": c.get("Loan_Purpose__c") or "",
                        "interest_period": c.get("Interest_Period__c") or "",
                        "other_monthly_income": c.get("Other_monthly_income__c") or 0,
                        "current_debt_credit_limit": c.get("Current_debt_Credit_limit__c") or "",
                        "current_employment_started": c.get("Current_employment_started__c") or "",
                        "date_of_notification_for_residence": c.get("Date_of_notification_for_residence__c") or "",
                        "date_of_signature": c.get("Date_of_signature__c") or "",
                        "description": c.get("Description") or "",
                        "mailing_city": c.get("MailingCity") or "",
                        "mailing_postal_code": c.get("MailingPostalCode") or "",
                        "mailing_street": c.get("MailingStreet") or "",
                        "other_city": c.get("OtherCity") or "",
                        "other_street": c.get("OtherStreet") or "",
                        "other_country": c.get("OtherCountry") or "",
                        "mobile_phone": c.get("MobilePhone") or "",
                        # ÜresSF mezők — továbbítjuk, a mapping tudja, hogy üresek
                        "account_holding_bank": c.get("Account_holding_bank__c") or "",
                        "cafeteria_bonus": c.get("Cafeteria_bonus__c") or 0,
                        "continuous_tb_2_years": c.get("Continuous_TB_2_years__c") or "",
                        "contract_end_date": c.get("Contract_end_date__c") or "",
                        "contract_start_date": c.get("Contract_start_date__c") or "",
                        "date_of_handover": c.get("Date_of_handover__c") or "",
                        "date_of_transfer": c.get("Date_of_transfer__c") or "",
                        "description_of_loan_purpose": c.get("Description_of_loan_purpose_comments__c") or "",
                        "divident": c.get("Divident__c") or 0,
                        "employee_registration": c.get("Employee_registration_c__c") or "",
                        "employer_company_type": c.get("Employer_s_company_type__c") or "",
                        "income_from_self_owned": c.get("Income_from_self_owned_company__c") or 0,
                        "industry": c.get("Industry__c") or "",
                        "monthly_payment_details": c.get("Monthly_Payment_details__c") or "",
                        "monthly_installment_payment": c.get("Monthly_installment_payment__c"),
                        "mortgagor": c.get("Mortgagor__c") or "",
                        "numer_and_age_of_children": c.get("Numer_and_Age_of_Children__c") or "",
                        "other_income": c.get("Other_income__c") or 0,
                        "other_monthly_deductions": c.get("Other_monthly_deductions__c") or 0,
                        "own_resources": c.get("Own_resources__c") or 0,
                        "property_50pct_ownership_details": c.get("Property_50pct_ownership_details__c") or "",
                        "property_encumbrances": c.get("Property_encumbrances__c") or "",
                        "property_value": str(c.get("Property_value__c")) if c.get("Property_value__c") is not None else "",
                        "self_employment_details": c.get("Self_employment_details__c") or "",
                        "state_support": c.get("State_Support__c") or "",
                        "town_or_possibly_exact_address": c.get("Town_or_possibly_the_exact_address__c") or "",
                        "usufructuary": c.get("Usufructuary__c") or "",
                        "what_type_of_loan": c.get("What_type_of_loan__c") or "",
                        "contact_loan_amount": c.get("Loan_amount__c"),
                        "contact_loan_period": c.get("Loan_period__c"),
                        "ownership_in_employer": c.get("Ownership_in_employer__c") or "",
                        "preferalt_bank": c.get("Preferalt_bank__c") or "",
                        "residence_status": c.get("Residence_Status__c") or "",
                    }
                    participant_record["income_bearing"] = income_flags.get(role_label)
                    participants_records.append(participant_record)

                # 5. Ingatlanok lekérdezése:
                #    Elsődleges: Opportunity_Property_Role__c kapcsolótábla
                #    Fallback: Property__c WHERE Account__c = account_id (a valós
                #    sandbox ügyleteknél az ingatlanok közvetlenül az Accountra
                #    vannak kötve, és az Ingatlan_szerepe__c a Property__c-n él).
                properties_records = []
                prop_field_candidates = (
                    "Id", "Name", "Property_Type__c", "Ingatlan_hrsz__c", "Ingatlan_alapterulet__c",
                    "Property_value__c", "Purchase_price__c", "Ingatlan_irsz__c", "Ingatlan_telepules__c",
                    "Ingatlan_kozterulet_neve__c", "Ingatlan_Kozterulet_jellege__c", "Ingatlan_hazszam__c",
                    "Ingatlan_emelet__c", "Ingatlan_energetika__c", "Ingatlan_terhek__c",
                    "Ingatlan_szerepe__c", "Ingatlan_jellege__c", "Ingatlan_megjegyzes__c",
                    "Ingatlan_osztatlan__c", "Ingatlan_neve__c", "Ingatlan_hasznalatbaveteli__c",
                    *_SF_FIELD_MAP["Property__c"],
                )
                prop_fields = ", ".join(
                    self._describe_filtered_fields("Property__c", prop_field_candidates)
                )
                import re as _re
                safe_deal_id = _re.sub(r"[^a-zA-Z0-9]", "", str(deal_id or ""))
                prop_roles = []
                try:
                    prop_role_query = (
                        "SELECT Property__c, Ingatlan_szerepe__c "
                        "FROM Opportunity_Property_Role__c "
                        f"WHERE Opportunity__c = '{safe_deal_id}'"
                    )
                    prop_role_results = self._sf.query(prop_role_query)
                    prop_roles = prop_role_results.get("records", [])
                except Exception as exc:
                    logger.info("Opportunity_Property_Role__c nem elérhető (%s), Account Property__c fallback: %s", deal_id, exc)

                ordered_props: list[tuple[dict, str]] = []
                if prop_roles:
                    prop_ids = [
                        _re.sub(r"[^a-zA-Z0-9]", "", str(pr["Property__c"]))
                        for pr in prop_roles if pr.get("Property__c")
                    ]
                    if prop_ids and prop_fields:
                        prop_id_list_str = ", ".join(f"'{pid}'" for pid in prop_ids)
                        prop_query = (
                            f"SELECT {prop_fields} FROM Property__c "
                            f"WHERE Id IN ({prop_id_list_str})"
                        )
                        prop_results = self._sf.query(prop_query)
                        props_by_id = {p_rec["Id"]: p_rec for p_rec in prop_results.get("records", [])}
                        for pr in prop_roles:
                            pid = pr.get("Property__c")
                            if pid and pid in props_by_id:
                                p_rec = props_by_id[pid]
                                role_override = pr.get("Ingatlan_szerepe__c") or p_rec.get("Ingatlan_szerepe__c") or ""
                                ordered_props.append((p_rec, role_override))
                elif account_id and prop_fields:
                    try:
                        acc_prop_query = (
                            f"SELECT {prop_fields} FROM Property__c "
                            f"WHERE Account__c = '{account_id}'"
                        )
                        acc_props = self._sf.query(acc_prop_query).get("records", [])
                        # Sorrend: Célingatlan előre, Pótfedezet másodiknak
                        def _prop_sort_key(p_item: dict) -> tuple:
                            sz = (p_item.get("Ingatlan_szerepe__c") or "").lower()
                            if "cél" in sz or "cel" in sz:
                                return (0, p_item.get("Id") or "")
                            if "pót" in sz or "pot" in sz:
                                return (1, p_item.get("Id") or "")
                            return (2, p_item.get("Id") or "")

                        for p_rec in sorted(acc_props, key=_prop_sort_key):
                            ordered_props.append((p_rec, p_rec.get("Ingatlan_szerepe__c") or ""))
                        if ordered_props:
                            logger.info(
                                "Account ingatlan-fallback: %d Property__c (%s)",
                                len(ordered_props), account_id,
                            )
                    except Exception as exc:
                        logger.warning("Account Property__c lekérése sikertelen (%s): %s", account_id, exc)

                for p, role_val in ordered_props:
                    kozterulet_nev = (p.get("Ingatlan_kozterulet_neve__c") or "").strip()
                    kozterulet_jelleg = (p.get("Ingatlan_Kozterulet_jellege__c") or "").strip()
                    street_parts = []
                    if kozterulet_nev:
                        street_parts.append(kozterulet_nev)
                    if kozterulet_jelleg and not kozterulet_nev.lower().endswith(kozterulet_jelleg.lower()):
                        street_parts.append(kozterulet_jelleg)

                    street_name = " ".join(street_parts).strip()
                    # String-preserving postal code: numeric values are
                    # canonicalized via int(), but a non-numeric postal code
                    # must stay text (with a warning) instead of aborting the
                    # whole deal through int().
                    zip_raw = p.get("Ingatlan_irsz__c")
                    if zip_raw is None or zip_raw == "":
                        zip_str = ""
                    else:
                        try:
                            zip_str = str(int(zip_raw))
                        except (TypeError, ValueError):
                            zip_str = str(zip_raw).strip()
                            logger.warning(
                                "Ingatlan_irsz__c %r on Property %s is not "
                                "numeric; preserved as text instead of a "
                                "4-digit postal code.",
                                zip_str,
                                p.get("Id") or p.get("Name"),
                            )

                    prop_record = {
                        "property_id": p.get("Id"),
                        "_property": p,
                        "property_type": p.get("Property_Type__c") or "lakás",
                        "parcel_number": p.get("Ingatlan_hrsz__c") or "",
                        "area_sqm": p.get("Ingatlan_alapterulet__c"),
                        "estimated_value": p.get("Property_value__c") or p.get("Purchase_price__c"),
                        "purchase_price": p.get("Purchase_price__c"),
                        "form_type": p.get("Ingatlan_jellege__c", "") or "",
                        "energetika": p.get("Ingatlan_energetika__c", "") or "",
                        "terhek": p.get("Ingatlan_terhek__c", "") or "",
                        "ingatlan_szerepe": role_val or p.get("Ingatlan_szerepe__c", "") or "",
                        # Ambiguous legacy consumer (Lead.Term_in_year__c) must stay empty.
                        "epites_eve": "",
                        "ingatlan_megjegyzes": p.get("Ingatlan_megjegyzes__c", "") or "",
                        "ingatlan_neve": p.get("Ingatlan_neve__c", "") or "",
                        "ingatlan_osztatlan": p.get("Ingatlan_osztatlan__c", "") or "",
                        "address": {
                            "zip_code": zip_str,
                            "city": p.get("Ingatlan_telepules__c") or "",
                            "street": street_name,
                            "house_number": str(p.get("Ingatlan_hazszam__c") or ""),
                            "floor": p.get("Ingatlan_emelet__c") or None,
                            "door": None,
                        },
                    }
                    properties_records.append(prop_record)

                # 6. Futamidő meghatározása (első résztvevő Term_in_year_c__c
                # mezőjéből). A futamidő kizárólag explicit adat lehet: ha
                # sehol nincs kitöltve, None marad — sosem találunk ki 240
                # hónapot.
                loan_term_months = None
                for cid in contact_ids:
                    if cid in contacts and contacts[cid].get("Term_in_year_c__c"):
                        try:
                            term_years = float(contacts[cid]["Term_in_year_c__c"])
                            loan_term_months = int(term_years * 12)
                            break
                        except (ValueError, TypeError):
                            pass

                # Ha az Opportunity-n nincs Purchase_Price__c, de a célingatlanon van
                first_prop_purchase = next(
                    (pr.get("purchase_price") for pr in properties_records if pr.get("purchase_price")),
                    None,
                )

                # 7. Normalizált formátum összeállítása (valós SF adatokból, fiktív fallback nélkül)
                normalized_sf_data = {
                    "Id": opp.get("Id"),
                    "StageName": opp.get("StageName"),
                    "Amount__c": opp.get("Hitel_sszeg__c") or opp.get("Amount") or 0,
                    "Loan_Term__c": loan_term_months,
                    "Interest_Period__c": opp.get("Interest_Period__c") or None,
                    "Loan_Purpose__c": opp.get("Hitelc_l__c") or opp.get("Loan_Purpose__c") or None,
                    "Product_Name__c": opp.get("Term_k__c") or opp.get("Name") or None,
                    "Product_Type__c": opp.get("Product_Type__c") or opp.get("Term_k__c") or None,
                    "Down_Payment__c": opp.get("Down_Payment__c"),
                    "Monthly_Payment__c": opp.get("Monthly_Payment__c"),
                    "Purchase_Price__c": opp.get("Purchase_Price__c") or first_prop_purchase,
                    "CSOK_Amount__c": opp.get("CSOK_Amount__c") or opp.get("Csok__c"),
                    "AFA_Support__c": opp.get("AFA_Support__c") or opp.get("Afa__c"),
                    "Housing_Savings__c": opp.get("Housing_Savings__c") or opp.get("Lakastakarek__c"),
                    "CreatedDate": opp.get("CreatedDate"),
                    # Notes come only from explicit description/notes fields;
                    # the Opportunity Name is a label, not a note.
                    "Description": opp.get("Description") or opp.get("remark__c") or None,
                    "Participants__r": {
                        "records": participants_records
                    },
                    "Properties__r": {
                        "records": properties_records
                    },
                    "AccountId": account_id,
                    "_account_pool_extra": pool_extra_names,
                    "_account": {
                        k: v for k, v in account_record.items()
                        if not k.startswith("attributes") and v is not None
                    },
                    "_opportunity": opp,
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
            query = (
                "SELECT Id, Name, StageName, CloseDate, Account.Name "
                "FROM Opportunity ORDER BY CreatedDate DESC LIMIT 500"
            )
            result = self._sf.query(query)
            return [
                {
                    "Id": deal.get("Id"),
                    "Name": deal.get("Name"),
                    "StageName": deal.get("StageName"),
                    "CloseDate": deal.get("CloseDate"),
                    "AccountName": (deal.get("Account") or {}).get("Name"),
                }
                for deal in result.get("records", [])
            ]

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

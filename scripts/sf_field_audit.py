#!/usr/bin/env python3
"""
Salesforce mező-audit a FinancialGenie projekthez.

Két dolgot vizsgál (read-only, csak SOQL SELECT + describe):
  1. Létezik-e a Salesforce-ban az a mező, amire a kód hivatkozik?
  2. A létező mezők populáltak-e az sandboxbeli rekordokon?

Nem módosít semmit.
"""

import sys
import os
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
env_path = PROJECT_ROOT / ".env"
if not env_path.exists():
    env_path = PROJECT_ROOT / "config" / ".env"
load_dotenv(env_path)


# ── Mezők, amiket a salesforce_client.py ténylegesen lekérdez ───────────────

OPPORTUNITY_FIELDS = [
    "Id", "Name", "StageName", "Hitel_sszeg__c", "Amount",
    "Interest_Period__c", "Hitelc_l__c", "Loan_Purpose__c", "Term_k__c",
    "Down_Payment__c", "Monthly_Payment__c", "CreatedDate", "Description",
    "remark__c",
    "Opportunity_Contact_Name__c", "First_Co_debtor__c",
    "Second_Co_debtor__c", "Third_Co_debtor__c", "Mortgagor__c", "Usufructuary__c",
]

CONTACT_FIELDS = [
    "Id", "Name", "FirstName", "LastName", "Szuletesi_nev__c", "Mother_s_Name__c",
    "Place_of_Birth__c", "Date_of_birth__c", "ID_Card_Number__c", "Tax_ID__c",
    "Address_Card_Number__c", "Permanent_address__c", "Phone", "Email",
    "Name_of_employer__c", "Average_monthly_net_income__c", "Term_in_year_c__c",
    "Highest_Educational_Qualification__c", "Marital_Status__c", "Dependents_count__c",
    "Citizenship__c", "Income_type__c", "MailingPostalCode",
    "Current_employment_started__c", "ZIP__c",
]

PROPERTY_FIELDS = [
    "Id", "Name", "Property_Type__c", "Ingatlan_hrsz__c", "Ingatlan_alapterulet__c",
    "Property_value__c", "Purchase_price__c", "Ingatlan_irsz__c", "Ingatlan_telepules__c",
    "Ingatlan_kozterulet_neve__c", "Ingatlan_Kozterulet_jellege__c", "Ingatlan_hazszam__c",
    "Ingatlan_emelet__c",
]


def _is_empty(val):
    return val is None or val == "" or val == 0


def describe_object(sf, obj_name):
    """Lekéri egy objektum mezőinek nevét. Visszaadja a halmazt."""
    try:
        desc = sf.__getattr__(obj_name).describe()
        return {f["name"] for f in desc["fields"]}
    except Exception as e:
        print(f"  ⚠ describe sikertelen ({obj_name}): {e}")
        return None


def check_fields_exist(obj_name, expected_fields, available_fields):
    """Jelzi, ha a kód által hivatkozott mező nem létezik az objektumon."""
    if available_fields is None:
        return None
    missing = [f for f in expected_fields if f not in available_fields]
    if missing:
        print(f"\n  ❌ NEM LÉTEZŐ mezők a(z) {obj_name} objektumon (a kód hivatkozik rájuk):")
        for m in missing:
            print(f"       – {m}")
    else:
        print(f"\n  ✓ Minden hivatkozott mező létezik a(z) {obj_name} objektumon.")
    return missing


def audit_opportunities(sf, available_fields):
    print("\n" + "=" * 70)
    print("OPPORTUNITY MEZŐ AUDIT")
    print("=" * 70)

    check_fields_exist("Opportunity", OPPORTUNITY_FIELDS, available_fields)

    # Csak a létező mezőket kérdezzük le
    queryable = [f for f in OPPORTUNITY_FIELDS if available_fields is None or f in available_fields]
    fields_str = ", ".join(queryable)
    query = f"SELECT {fields_str} FROM Opportunity LIMIT 50"
    try:
        result = sf.query(query)
    except Exception as e:
        print(f"\n  ❌ SOQL hiba: {e}")
        return []
    records = result.get("records", [])
    print(f"\nÖsszes Opportunity a sandboxban (max 50): {len(records)}")

    total_count_result = sf.query("SELECT COUNT() FROM Opportunity")
    print(f"Összes Opportunity ténylegesen: {total_count_result.get('totalSize', '?')}")

    if not records:
        print("  ⚠ Nincs egyetlen Opportunity sem a sandboxban!")
        return records

    empty_counts = defaultdict(int)
    filled_counts = defaultdict(int)
    per_record_status = []

    for rec in records:
        oid = rec.get("Id", "?")
        name = rec.get("Name", "?")
        empties = []
        for f in OPPORTUNITY_FIELDS:
            if f in ("Id", "Name"):
                continue
            if available_fields and f not in available_fields:
                continue  # nem létezik, nem vizsgáljuk
            if _is_empty(rec.get(f)):
                empty_counts[f] += 1
                empties.append(f)
            else:
                filled_counts[f] += 1
        per_record_status.append((oid, name, empties))

    print(f"\n{'Mező':<35} {'Populált':>10} {'Üres':>8} {'Üres %':>8}")
    print("-" * 65)
    for f in OPPORTUNITY_FIELDS:
        if f in ("Id", "Name"):
            continue
        if available_fields and f not in available_fields:
            print(f"  {f:<33} {'—':>10} {'NEM LÉTEZIK':>8}")
            continue
        filled = filled_counts.get(f, 0)
        empty = empty_counts.get(f, 0)
        total = filled + empty
        pct = f"{(empty/total*100):.0f}%" if total else "—"
        flag = " ⚠" if (total > 0 and (empty == total or empty > total/2)) else ""
        print(f"  {f:<33} {filled:>10} {empty:>8} {pct:>8}{flag}")

    print("\nÜres mezők ügyletenként:")
    for oid, name, empties in per_record_status:
        if empties:
            print(f"  • {name} ({oid}): {len(empties)} üres mező")
            for e in empties:
                print(f"      – {e}")
        else:
            print(f"  • {name} ({oid}): minden mező populált ✓")

    return records


def audit_contacts(sf, opp_records, available_fields):
    print("\n" + "=" * 70)
    print("CONTACT MEZŐ AUDIT")
    print("=" * 70)

    check_fields_exist("Contact", CONTACT_FIELDS, available_fields)

    contact_ids = set()
    lookup_fields = [
        "Opportunity_Contact_Name__c", "First_Co_debtor__c",
        "Second_Co_debtor__c", "Third_Co_debtor__c", "Mortgagor__c", "Usufructuary__c",
    ]
    opp_contact_map = {}
    for rec in opp_records:
        opp_name = rec.get("Name", "?")
        for lf in lookup_fields:
            cid = rec.get(lf)
            if cid:
                contact_ids.add(cid)
                opp_contact_map.setdefault(cid, []).append(f"{opp_name}→{lf}")

    if not contact_ids:
        print("\n  ⚠ Egyetlen Opportunity-n sincs Contact lookup kitöltve!")
        print("    (nincs adós / adóstárs / kezes / haszonélvező megadva)")
        # Akkor is megnézzük az összes Contactot, ha van
        try:
            all_count = sf.query("SELECT COUNT() FROM Contact")["totalSize"]
            print(f"  (A sandboxban összesen {all_count} Contact van.)")
        except Exception:
            pass
        return []

    print(f"\nÖsszes hivatkozott Contact ID: {len(contact_ids)}")

    queryable = [f for f in CONTACT_FIELDS if available_fields is None or f in available_fields]
    fields_str = ", ".join(queryable)
    id_list = ", ".join(f"'{cid}'" for cid in contact_ids)
    query = f"SELECT {fields_str} FROM Contact WHERE Id IN ({id_list})"
    try:
        result = sf.query(query)
    except Exception as e:
        print(f"\n  ❌ SOQL hiba: {e}")
        return []
    records = result.get("records", [])
    print(f"Lekérdezett Contact rekordok: {len(records)}")

    if not records:
        print("  ⚠ Egyik hivatkozott Contact sem található!")
        return []

    empty_counts = defaultdict(int)
    filled_counts = defaultdict(int)
    per_record_status = []

    for rec in records:
        cid = rec.get("Id", "?")
        name = rec.get("Name", "?")
        empties = []
        for f in CONTACT_FIELDS:
            if f in ("Id", "Name"):
                continue
            if available_fields and f not in available_fields:
                continue
            if _is_empty(rec.get(f)):
                empty_counts[f] += 1
                empties.append(f)
            else:
                filled_counts[f] += 1
        used_by = opp_contact_map.get(cid, [])
        per_record_status.append((cid, name, empties, used_by))

    print(f"\n{'Mező':<42} {'Populált':>10} {'Üres':>8} {'Üres %':>8}")
    print("-" * 72)
    for f in CONTACT_FIELDS:
        if f in ("Id", "Name"):
            continue
        if available_fields and f not in available_fields:
            print(f"  {f:<40} {'—':>10} {'NEM LÉTEZIK':>8}")
            continue
        filled = filled_counts.get(f, 0)
        empty = empty_counts.get(f, 0)
        total = filled + empty
        pct = f"{(empty/total*100):.0f}%" if total else "—"
        flag = " ⚠" if (total > 0 and (empty == total or empty > total/2)) else ""
        print(f"  {f:<40} {filled:>10} {empty:>8} {pct:>8}{flag}")

    print("\nÜres mezők Contact-onként:")
    for cid, name, empties, used_by in per_record_status:
        print(f"\n  • {name} ({cid})")
        print(f"      használat: {', '.join(used_by)}")
        if empties:
            print(f"      ÜRES mezők ({len(empties)}):")
            for e in empties:
                print(f"        – {e}")
        else:
            print(f"      minden mező populált ✓")

    return records


def audit_properties(sf, opp_records, available_fields):
    print("\n" + "=" * 70)
    print("PROPERTY__c MEZŐ AUDIT")
    print("=" * 70)

    check_fields_exist("Property__c", PROPERTY_FIELDS, available_fields)

    opp_ids = [r["Id"] for r in opp_records if r.get("Id")]
    if not opp_ids:
        print("\n  ⚠ Nincsenek Opportunity-k.")
        return []

    id_list = ", ".join(f"'{oid}'" for oid in opp_ids)
    role_query = (
        f"SELECT Property__c, Ingatlan_szerepe__c, Opportunity__c "
        f"FROM Opportunity_Property_Role__c WHERE Opportunity__c IN ({id_list})"
    )
    try:
        role_result = sf.query(role_query)
    except Exception as e:
        print(f"\n  ❌ SOQL hiba (Opportunity_Property_Role__c): {e}")
        return []
    role_records = role_result.get("records", [])
    print(f"\nOpportunity_Property_Role__c rekordok: {len(role_records)}")

    if not role_records:
        print("  ⚠ Egyetlen Opportunity-hez sincs Property kapcsolva!")
        return []

    prop_ids = list({r["Property__c"] for r in role_records if r.get("Property__c")})
    print(f"Hivatkozott Property__c ID-k: {len(prop_ids)}")

    if not prop_ids:
        print("  ⚠ A kapcsolótáblában nincs kitöltve a Property__c lookup!")
        return []

    queryable = [f for f in PROPERTY_FIELDS if available_fields is None or f in available_fields]
    fields_str = ", ".join(queryable)
    prop_id_list = ", ".join(f"'{pid}'" for pid in prop_ids)
    prop_query = f"SELECT {fields_str} FROM Property__c WHERE Id IN ({prop_id_list})"
    try:
        prop_result = sf.query(prop_query)
    except Exception as e:
        print(f"\n  ❌ SOQL hiba (Property__c): {e}")
        return []
    records = prop_result.get("records", [])
    print(f"Lekérdezett Property__c rekordok: {len(records)}")

    if not records:
        print("  ⚠ Egyik hivatkozott Property sem található!")
        return []

    empty_counts = defaultdict(int)
    filled_counts = defaultdict(int)
    per_record_status = []

    prop_role_map = defaultdict(list)
    for r in role_records:
        pid = r.get("Property__c")
        role = r.get("Ingatlan_szerepe__c", "?")
        if pid:
            prop_role_map[pid].append(role)

    for rec in records:
        pid = rec.get("Id", "?")
        name = rec.get("Name", "?")
        empties = []
        for f in PROPERTY_FIELDS:
            if f in ("Id", "Name"):
                continue
            if available_fields and f not in available_fields:
                continue
            if _is_empty(rec.get(f)):
                empty_counts[f] += 1
                empties.append(f)
            else:
                filled_counts[f] += 1
        roles = prop_role_map.get(pid, [])
        per_record_status.append((pid, name, empties, roles))

    print(f"\n{'Mező':<38} {'Populált':>10} {'Üres':>8} {'Üres %':>8}")
    print("-" * 68)
    for f in PROPERTY_FIELDS:
        if f in ("Id", "Name"):
            continue
        if available_fields and f not in available_fields:
            print(f"  {f:<36} {'—':>10} {'NEM LÉTEZIK':>8}")
            continue
        filled = filled_counts.get(f, 0)
        empty = empty_counts.get(f, 0)
        total = filled + empty
        pct = f"{(empty/total*100):.0f}%" if total else "—"
        flag = " ⚠" if (total > 0 and (empty == total or empty > total/2)) else ""
        print(f"  {f:<36} {filled:>10} {empty:>8} {pct:>8}{flag}")

    print("\nÜres mezők Property-nként:")
    for pid, name, empties, roles in per_record_status:
        print(f"\n  • {name} ({pid})")
        print(f"      szerep: {', '.join(str(r) for r in roles) if roles else '?'}")
        if empties:
            print(f"      ÜRES mezők ({len(empties)}):")
            for e in empties:
                print(f"        – {e}")
        else:
            print(f"      minden mező populált ✓")

    return records


def main():
    username = os.getenv("SF_USERNAME", "")
    password = os.getenv("SF_PASSWORD", "")
    security_token = os.getenv("SF_SECURITY_TOKEN", "")
    domain = os.getenv("SF_DOMAIN", "test")

    if not username or not password:
        print("HIBA: hiányzó SF_USERNAME / SF_PASSWORD!")
        sys.exit(1)

    print(f"Salesforce csatlakozás: {username} @ {domain} ...")
    try:
        from simple_salesforce import Salesforce
        sf = Salesforce(
            username=username,
            password=password,
            security_token=security_token,
            domain=domain,
        )
    except Exception as e:
        print(f"\n❌ Salesforce csatlakozási hiba: {e}")
        sys.exit(1)

    print("✓ Csatlakozva.")

    # Identity
    try:
        identity = sf.identity()
        print(f"  Felhasználó: {identity.get('username', '?')}")
        print(f"  Org ID: {identity.get('organization_id', '?')}")
    except Exception:
        pass

    # Describe minden objektumon (egyszer)
    print("\nObjektum-séma lekérése (describe) ...")
    opp_fields = describe_object(sf, "Opportunity")
    contact_fields = describe_object(sf, "Contact")
    property_fields = describe_object(sf, "Property__c")

    opp_records = audit_opportunities(sf, opp_fields)
    audit_contacts(sf, opp_records, contact_fields)
    audit_properties(sf, opp_records, property_fields)

    print("\n" + "=" * 70)
    print("AUDIT BEFEJEZVE")
    print("=" * 70)


if __name__ == "__main__":
    main()

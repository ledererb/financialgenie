#!/usr/bin/env python3
"""
Salesforce LEAD objektum audit.

Megvizsgálja:
  1. Létezik-e a sandboxban Lead objektum és vannak-e Lead rekordok.
  2. A mapping JSON-ekben `Lead.*` canonical névtérként használt mezők
     (Ingatlan_* mezők) léteznek-e a Lead objektumon.
  3. Ha vannak Lead rekordok, ezek a mezők populáltak-e rajtuk.

Read-only — csak SOQL SELECT + describe.
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


# A mapping JSON-ekben Lead.* canonical névtérként használt mezők
LEAD_CANONICAL_FIELDS = [
    "Ingatlan_irsz__c",
    "Ingatlan_telepules__c",
    "Ingatlan_kozterulet_neve__c",
    "Ingatlan_hazszam__c",       # a mappingben 'Ingtalan_hazszam' (typo!) is szerepel
    "Ingatlan_megjegyzes__c",
    "Ingatlan_alapterulet__c",
    "Ingatlan_jellege__c",
    "Ingatlan_emelet__c",
    "Ingatlan_energetika__c",
    "Ingatlan_terhek__c",
    "Ingatlan_szerepe__c",
    "Estimated__c",
    "Purchase_price__c",
    "Tervezett_onero__c",
    "Tervezett_CSOK_Plusz__c",
    "Tervezett_Otthon_Start__c",
    "Term_in_year__c",
]

# Standard Lead mezők is, hogy lássuk egyáltalán van-e adat
LEAD_STANDARD_FIELDS = [
    "Id", "FirstName", "LastName", "Company", "Email", "Phone",
    "Street", "City", "PostalCode", "Country", "Status", "ConvertedDate",
]


def _is_empty(val):
    return val is None or val == "" or val == 0


def main():
    username = os.getenv("SF_USERNAME", "")
    password = os.getenv("SF_PASSWORD", "")
    security_token = os.getenv("SF_SECURITY_TOKEN", "")
    domain = os.getenv("SF_DOMAIN", "test")

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

    # --- 1. Lead describe ---
    print("\n" + "=" * 70)
    print("LEAD OBJEKTUM – SÉMA")
    print("=" * 70)
    try:
        desc = sf.Lead.describe()
        lead_field_names = {f["name"] for f in desc["fields"]}
        print(f"Lead objektum létezik. Összes mező: {len(lead_field_names)}")
    except Exception as e:
        print(f"❌ Lead describe sikertelen: {e}")
        return

    # --- 2. Hivatkozott Ingatlan_* mezők léteznek-e a Lead-en? ---
    print("\nA mapping JSON-ek `Lead.*` névterében hivatkozott mezők:")
    print(f"  {'Mező':<35} {'Létezik a Lead-en?':>20}")
    print("  " + "-" * 55)
    existing_custom = []
    missing_custom = []
    for f in LEAD_CANONICAL_FIELDS:
        exists = f in lead_field_names
        print(f"  {f:<35} {'✓ IGEN' if exists else '✗ NEM':>20}")
        (existing_custom if exists else missing_custom).append(f)

    # --- 3. Lead rekordok száma ---
    print("\n" + "=" * 70)
    print("LEAD REKORDOK")
    print("=" * 70)
    try:
        count_result = sf.query("SELECT COUNT() FROM Lead")
        total_leads = count_result.get("totalSize", 0)
        print(f"Összes Lead a sandboxban: {total_leads}")
    except Exception as e:
        print(f"❌ COUNT hiba: {e}")
        return

    if total_leads == 0:
        print("\n  ⚠ NINCS egyetlen Lead rekord sem a sandboxban!")
        print("  → A Lead mezők populáltsága nem vizsgálható (nincs adathordozó).")
        print("  → Ez lehet teljesen rendben van, ha az üzleti folyamat nem használ Lead-et.")
        # Akkor is megmutatjuk, mi hiányzik
        if missing_custom:
            print(f"\n  Megjegyzés: {len(missing_custom)} hivatkozott mező nem létezik a Lead-en")
            print("  (lásd fent).")
        return

    # --- 4. Mezők populáltsága a Lead rekordokon ---
    queryable = [f for f in (LEAD_STANDARD_FIELDS + LEAD_CANONICAL_FIELDS) if f in lead_field_names]
    fields_str = ", ".join(queryable)
    query = f"SELECT {fields_str} FROM Lead LIMIT 50"
    result = sf.query(query)
    records = result.get("records", [])
    print(f"Lekérdezett Lead rekordok: {len(records)}")

    empty_counts = defaultdict(int)
    filled_counts = defaultdict(int)

    for rec in records:
        for f in queryable:
            if f == "Id":
                continue
            if _is_empty(rec.get(f)):
                empty_counts[f] += 1
            else:
                filled_counts[f] += 1

    print(f"\n{'Mező':<35} {'Populált':>10} {'Üres':>8} {'Üres %':>8}")
    print("-" * 65)
    for f in (LEAD_STANDARD_FIELDS + LEAD_CANONICAL_FIELDS):
        if f == "Id":
            continue
        if f not in lead_field_names:
            print(f"  {f:<33} {'—':>10} {'NEM LÉTEZIK':>8}")
            continue
        filled = filled_counts.get(f, 0)
        empty = empty_counts.get(f, 0)
        total = filled + empty
        pct = f"{(empty/total*100):.0f}%" if total else "—"
        flag = " ⚠" if (total > 0 and (empty == total or empty > total/2)) else ""
        print(f"  {f:<33} {filled:>10} {empty:>8} {pct:>8}{flag}")

    # Rekordonként
    print("\nÜres mezők Lead-ekenként:")
    for rec in records:
        lid = rec.get("Id", "?")
        name_parts = [rec.get("FirstName", ""), rec.get("LastName", ""), rec.get("Company", "")]
        label = " ".join(p for p in name_parts if p).strip() or lid
        empties = [f for f in queryable if f != "Id" and _is_empty(rec.get(f))]
        if empties:
            print(f"\n  • {label} ({lid}): {len(empties)} üres mező")
            for e in empties:
                print(f"      – {e}")
        else:
            print(f"\n  • {label} ({lid}): minden mező populált ✓")

    print("\n" + "=" * 70)
    print("AUDIT BEFEJEZVE")
    print("=" * 70)


if __name__ == "__main__":
    main()

"""
Kiegészíti az OTP v5 mapping JSON-t a `-társ` (adóstárs) változatokkal.

Logika:
  - Végigmegy a mapping összes field-jén.
  - Ha a `pdf_field_name` NEM tartalmaz `-társ`-t,
    ÉS a `canonical_field` `participant.X` formátumú,
  - Akkor biztosítja, hogy létezik egy `{pdf_field_name}-társ` entry,
    melynek canonical_field-je `participant[1].X` lesz.

  - Ha már létezik `{pdf_field_name}-társ` entry `participant.X` (régi hibás)
    target-tel, akkor javítja azt `participant[1].X`-re.

Idempotens: többszöri futtatás esetén is helyes eredményt ad.
"""

import json
import re
from pathlib import Path

MAPPING_PATH = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "mapping"
    / "OTP_Igenylesi_Dokumentumok_v5_mapping.json"
)

PARTICIPANT_RE = re.compile(r"^participant\.(.+)$")
TARS_SUFFIX = "-társ"


def to_indexed(canonical: str) -> str:
    """participant.X.Y -> participant[1].X.Y"""
    m = PARTICIPANT_RE.match(canonical)
    if not m:
        return canonical
    return f"participant[1].{m.group(1)}"


def main() -> None:
    if not MAPPING_PATH.exists():
        raise SystemExit(f"Mapping file not found: {MAPPING_PATH}")

    with MAPPING_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)

    fields = data.get("fields", [])
    print(f"[start] total fields: {len(fields)}")

    # Index existing entries by pdf_field_name (első előfordulás nyer).
    by_name: dict[str, dict] = {}
    for f in fields:
        name = f.get("pdf_field_name")
        if name and name not in by_name:
            by_name[name] = f

    created = 0
    updated = 0
    skipped = 0

    # Feldolgozás: eredeti, nem -társ participant.X mezők alapján.
    base_entries = [
        f for f in fields
        if (f.get("pdf_field_name") and TARS_SUFFIX not in f["pdf_field_name"])
        and PARTICIPANT_RE.match(f.get("canonical_field") or "")
    ]

    for base in base_entries:
        base_name = base["pdf_field_name"]
        base_canon = base["canonical_field"]
        tars_name = f"{base_name}{TARS_SUFFIX}"
        tars_canon = to_indexed(base_canon)

        existing = by_name.get(tars_name)
        if existing is None:
            new_entry = dict(base)
            new_entry["pdf_field_name"] = tars_name
            new_entry["canonical_field"] = tars_canon
            if new_entry.get("label"):
                new_entry["label"] = f"{new_entry['label']} (társ)"
            fields.append(new_entry)
            by_name[tars_name] = new_entry
            created += 1
        else:
            cur = existing.get("canonical_field")
            if cur == tars_canon:
                skipped += 1
            else:
                existing["canonical_field"] = tars_canon
                updated += 1

    data["fields"] = fields

    # Mentás előtt biztonsági mentés.
    backup = MAPPING_PATH.with_suffix(".json.bak")
    if not backup.exists():
        backup.write_text(MAPPING_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"[backup] saved -> {backup}")

    with MAPPING_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"[done] created={created} updated={updated} skipped={skipped}")
    print(f"[done] final total fields: {len(fields)}")

    # Statisztika.
    parts0 = sum(
        1 for f in fields
        if PARTICIPANT_RE.match(f.get("canonical_field") or "")
    )
    parts1 = sum(
        1 for f in fields
        if (f.get("canonical_field") or "").startswith("participant[1].")
    )
    mapped = sum(1 for f in fields if f.get("canonical_field"))
    print(f"[stats] participant. (adós): {parts0}")
    print(f"[stats] participant[1]. (adóstárs): {parts1}")
    print(f"[stats] mapped fields total: {mapped} / {len(fields)}")


if __name__ == "__main__":
    main()

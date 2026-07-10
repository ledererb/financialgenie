"""
Checkbox-csoport → Point konfiguráció generátor (PLAN_CHECKBOX_GROUPS.md §5.2).

Híd az AI felismerés (mapping.fields[].checkbox_group) és a kitöltőmotor
(src/engine/fill_rules, rule_type 3) között. A mapping checkbox-csoport
adataiból automatikusan felépíti a `points[]` konfigurációt, hogy a
kölcsönösen kizáró checkbox-csoportok (pl. legmagasabb iskolai végzettség)
helyesen pipázódjanak ki a Contact tényleges értéke alapján.

Blokk-struktúra (PLAN §4.3.1, opció (i)): egy opció = egy blokk.
    blocks: [{block_id: option_value, members: [pdf_field_name, ...]}, ...]
A rule_type 3 `active_block` params értékét a pipeline tölti ki runtime
(src/main.py §5.3) a Contact canonical értéke alapján.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Optional

logger = logging.getLogger(__name__)


def _field_meta(f: dict) -> Optional[dict]:
    """checkbox_group dict kinyerése egy mapping field-ből (raw dict)."""
    g = f.get("checkbox_group")
    if not g or not g.get("group_id"):
        return None
    return g


def generate_points_from_groups(
    fields: list[dict],
    sf_field_types: dict[str, str] | None = None,
) -> list[dict]:
    """Mapping fields[] → points[] konfiguráció checkbox-csoportokból.

    Minden checkbox_group.group_id-ből egy Point dict-et épít:
      {
        "point_id": f"AUTO_{group_id}",
        "framework": "*",
        "label": group_label,
        "page_number": <első tag oldala>,
        "blocks": [
          {"block_id": option_value, "members": [pdf_field_name, ...]},
          ...
        ],
        "rule_type": 3,        # picklist → kölcsönösen kizáró (PLAN §4.3.2)
        "params": {"active_block": None},   # runtime tölti ki (main.py §5.3)
        "_source": "auto_group",            # editor-only metadata
      }

    sf_field_types: a backend mapping_service.SF_FIELD_TYPES dict-je, hogy
      eldöntse picklist vs. multipicklist (PLAN §4.3.2 heurisztika).
      - picklist  → rule_type 3 (kölcsönösen kizáró, csak egy aktív blokk)
      - multipicklist → rule_type 1 (több opció is aktív lehet)
      - ismeretlen / string → rule_type 3 (biztonságosabb: csak egyet pipál)

    Backward compat: a checkbox_group == None / hiányzó group_id mezőket
    egyszerűen kihagyja — a régi mapping-ek (ahol mindenhol null) üres
    points[]-t kapnak, így továbbra is betöltődnek.
    """
    sf_field_types = sf_field_types or {}

    # group_id → OrderedDict[option_value → list[pdf_field_name]]
    # OrderedDict hogy a blokkok sorrendje stabil legyen (első előfordulás).
    groups: "OrderedDict[str, OrderedDict[str, list[str]]]" = OrderedDict()
    group_meta: dict[str, dict] = {}  # group_id → {label, page, canonical}

    for f in fields:
        g = _field_meta(f)
        if g is None:
            continue
        gid = g["group_id"]
        # option_value: preferált; match_value = régi alias (backward compat)
        ov = g.get("option_value")
        if ov is None:
            ov = g.get("match_value")
        # Ha nincs option_value, a pdf_field_name lesz a block_id (fallback)
        block_id = ov if ov else f.get("pdf_field_name", "")
        if not block_id:
            continue

        pdf_name = f.get("pdf_field_name", "")
        if gid not in groups:
            groups[gid] = OrderedDict()
            group_meta[gid] = {
                "label": g.get("group_label") or gid,
                "page": f.get("page_number", 1),
                "canonical": f.get("canonical_field"),
            }
        groups[gid].setdefault(block_id, []).append(pdf_name)

    points: list[dict] = []
    for gid, options in groups.items():
        meta = group_meta[gid]
        canonical = meta["canonical"]

        # rule_type heurisztika a SF mező típusa alapján (PLAN §4.3.2)
        sf_type = sf_field_types.get(canonical, "") if canonical else ""
        if sf_type == "multipicklist":
            rule_type = 1  # több opció is kipipálható
        else:
            # picklist / string / ismeretlen → kölcsönösen kizáró (biztonságos)
            rule_type = 3

        blocks = [
            {"block_id": block_id, "members": list(members)}
            for block_id, members in options.items()
        ]
        points.append({
            "point_id": f"AUTO_{gid}",
            "framework": "*",
            "label": meta["label"],
            "page_number": meta["page"],
            "blocks": blocks,
            "rule_type": rule_type,
            "params": {"active_block": None},
            "_source": "auto_group",
        })

    logger.info(
        "points_generator: %d csoportból %d pont generálva",
        len(groups), len(points),
    )
    return points

"""Deterministically build the runtime Salesforce-to-canonical-source map.

Legacy translation knowledge is used only in this offline builder.  Production
loads the generated JSON and never imports ``canonical_migration_map``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from scripts.canonical_migration_map import (
    definition_for_translation,
    explicit_legacy_fields,
    translate_legacy_path,
)
from src.canonical.registry import canonical_registry


ROOT = Path(__file__).resolve().parents[1]
FIELD_MAP_PATH = ROOT / "src" / "integrations" / "salesforce_field_map.json"


def _dynamic_rule(owner: str, source: str, path: str | None) -> dict[str, str]:
    namespace, key = source.split(".", 1)
    if namespace not in {"extra", "consents"}:
        raise ValueError(f"Unsupported dynamic source: {source!r}")
    rule = {"target": f"{owner}.{namespace}", "key": key}
    if path is not None:
        rule["path"] = path
    return rule


def _rule_from_path(path: str) -> dict[str, str]:
    source = canonical_registry.get(path).source
    if source.startswith("extra."):
        owner = path.partition(".")[0]
        return _dynamic_rule(owner, source, path)
    if source.startswith("consents."):
        return _dynamic_rule("participant", source, path)
    return {"target": source, "path": path}


def _rule_for_legacy(legacy_path: str) -> dict[str, str] | None:
    object_name = legacy_path.partition(".")[0]
    translation = translate_legacy_path(legacy_path)
    definition = definition_for_translation(legacy_path, translation)
    registered_source = canonical_registry.get(translation.path).source
    if definition["source"] != registered_source:
        raise ValueError(
            f"Registry/builder source mismatch for {legacy_path!r}: "
            f"{definition['source']!r} != {registered_source!r}"
        )
    source = definition["source"]
    namespace = source.partition(".")[0]

    # Contact is a participant source, not a second requested-loan or Property
    # route.  Account.Id is likewise obtained from Opportunity.AccountId.
    owner = translation.path.partition(".")[0]
    if object_name == "Contact" and (
        owner not in {"participant", "account"}
        or namespace not in {"participant", "account", "extra", "consents"}
    ):
        return None
    if object_name == "Account" and namespace != "account":
        return None

    if namespace == "extra":
        owner = translation.path.partition(".")[0]
        return _dynamic_rule(owner, source, translation.path)
    if namespace == "consents":
        return _dynamic_rule("participant", source, translation.path)
    return {"target": source, "path": translation.path}


def _direct_rule(target: str, path: str | None = None) -> dict[str, str]:
    rule = {"target": target}
    if path is not None:
        rule["path"] = path
    return rule


def _extra_rule(owner: str, key: str) -> dict[str, str]:
    return {"target": f"{owner}.extra", "key": key}


def _supplemental_rules() -> dict[str, dict[str, dict[str, str]]]:
    opportunity_paths = {
        "Id": "deal.deal_id",
        "Down_Payment__c": "deal.down_payment",
        "Monthly_Payment__c": "deal.monthly_payment",
        "Purchase_Price__c": "deal.purchase_price",
        "CSOK_Amount__c": "deal.csok_amount",
        "AFA_Support__c": "deal.afa_support",
        "Housing_Savings__c": "deal.housing_savings",
        "Interest_Period__c": "deal.interest_period",
        "Loan_Purpose__c": "deal.loan_purpose",
        "Product_Name__c": "deal.product_name",
        "Product_Type__c": "deal.product_type",
            "Term_in_year__c": "deal.loan_term_years",
            "Loan_Term__c": "deal.loan_term_months",
            "CreatedDate": "deal.created_at",
        }

    property_paths = {
        "Property_Type__c": "property.property_type",
        "Ingatlan_hrsz__c": "property.parcel_number",
        "Ingatlan_alapterulet__c": "property.area_sqm",
        "Property_value__c": "property.estimated_value",
        "Purchase_price__c": "property.purchase_price",
        "Ingatlan_irsz__c": "property.address.zip_code",
        "Ingatlan_telepules__c": "property.address.city",
        "Ingatlan_kozterulet_neve__c": "property.address.street",
        "Ingatlan_Kozterulet_jellege__c": "property.street_type",
        "Ingatlan_hazszam__c": "property.address.house_number",
        "Ingatlan_emelet__c": "property.address.floor",
        "Ingatlan_energetika__c": "property.energetika",
        "Ingatlan_terhek__c": "property.terhek",
        "Ingatlan_szerepe__c": "property.ingatlan_szerepe",
        "Ingatlan_jellege__c": "property.form_type",
        "Ingatlan_allapot__c": "property.condition",
        "Ingatlan_futes__c": "property.heating",
        "Ingatlan_epites_eve__c": "property.year_built",
        "Ingatlan_lift__c": "property.lift",
        "Ingatlan_biztositas__c": "property.insurance",
        "Ingatlan_megye__c": "property.region",
        "Account__c": "deal.account_id",
    }

    income_paths = {
        "Sorszam__c": "participant.borrower.income[1].sequence",
        "Jovedelem_tipusa__c": "participant.borrower.income[1].income_type",
        "Osszeg__c": "participant.borrower.income[1].amount",
        "Penznem__c": "participant.borrower.income[1].currency",
        "Erkezes_helye__c": "participant.borrower.income[1].destination",
        "Bank__c": "participant.borrower.income[1].bank_name",
        "Szamlaszam__c": "participant.borrower.income[1].account_number",
    }
    existing_loan_paths = {
        "Sorszam__c": "participant.borrower.existing_loan[1].sequence",
        "Hiteltipus__c": "participant.borrower.existing_loan[1].loan_type",
        "Bank__c": "participant.borrower.existing_loan[1].bank_name",
        "Szamlaszam__c": "participant.borrower.existing_loan[1].account_number",
        "Eredeti_osszeg__c": "participant.borrower.existing_loan[1].original_amount",
        "Fennallo_tartozas__c": (
            "participant.borrower.existing_loan[1].outstanding_amount"
        ),
        "Havi_torleszto__c": "participant.borrower.existing_loan[1].monthly_payment",
        "Penznem__c": "participant.borrower.existing_loan[1].currency",
        "Kivaltando__c": "participant.borrower.existing_loan[1].refinance",
    }

    rules: dict[str, dict[str, dict[str, str]]] = {
        "Contact": {"Id": _rule_from_path("participant.borrower.contact_id")},
        "Opportunity": {
            **{
                api_name: _rule_from_path(path)
                for api_name, path in opportunity_paths.items()
            },
        },
        "Account": {},
        "Property__c": {
            api_name: _rule_from_path(path)
            for api_name, path in property_paths.items()
        },
        "Jovedelem__c": {
            "Contact__c": _direct_rule("participant.contact_id"),
            **{
                api_name: _rule_from_path(path)
                for api_name, path in income_paths.items()
            },
        },
        "Meglevo_hitel__c": {
            "Contact__c": _direct_rule("participant.contact_id"),
            **{
                api_name: _rule_from_path(path)
                for api_name, path in existing_loan_paths.items()
            },
        },
    }
    rules["Property__c"].update(
        {
            "Id": _rule_from_path("property.property_id"),
            "Name": _extra_rule("property", "name"),
            "Ingatlan_megjegyzes__c": _extra_rule(
                "property", "ingatlan_megjegyzes"
            ),
            "Ingatlan_osztatlan__c": _extra_rule("property", "ingatlan_osztatlan"),
            "Ingatlan_neve__c": _extra_rule("property", "ingatlan_neve"),
            # Occupancy-permit metadata is not silently converted to year_built.
            "Ingatlan_hasznalatbaveteli__c": _extra_rule(
                "property", "ingatlan_hasznalatbaveteli"
            ),
            # Adapter concepts carry registry-validated paths so the audit can
            # prove their targets exist in the core registry.
            "Ingatlan_szobak_szama__c": _rule_from_path("property.number_of_rooms"),
            "Ingatlan_reszletes_tipus__c": _rule_from_path(
                "property.property_type_detail"
            ),
        }
    )
    return rules


def _add_rule(
    field_map: dict[str, dict[str, dict[str, str]]],
    object_name: str,
    api_name: str,
    rule: dict[str, str],
) -> None:
    rules = field_map.setdefault(object_name, {})
    previous = rules.get(api_name)
    if previous is not None and previous != rule:
        raise ValueError(
            f"Conflicting Salesforce map rules for {object_name}.{api_name}: "
            f"{previous!r} != {rule!r}"
        )
    rules[api_name] = rule


def build_field_map() -> dict[str, dict[str, dict[str, str]]]:
    """Return a complete, sorted, registry-validated runtime field map."""

    field_map = _supplemental_rules()
    for legacy_path in sorted(explicit_legacy_fields()):
        object_name, separator, api_name = legacy_path.partition(".")
        if not separator or object_name not in {"Contact", "Opportunity", "Account"}:
            continue
        rule = _rule_for_legacy(legacy_path)
        if rule is not None:
            _add_rule(field_map, object_name, api_name, rule)

    return {
        object_name: {
            api_name: field_map[object_name][api_name]
            for api_name in sorted(field_map[object_name])
        }
        for object_name in sorted(field_map)
    }


def formatted_field_map() -> str:
    return json.dumps(build_field_map(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_field_map(path: str | Path | None = None) -> bool:
    """Write changed bytes only; return whether the destination changed."""

    destination = Path(path) if path is not None else FIELD_MAP_PATH
    content = formatted_field_map()
    try:
        if destination.read_text(encoding="utf-8") == content:
            return False
    except FileNotFoundError:
        pass
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")
    return True


def _audit(field_map: dict[str, Any]) -> None:
    for object_name, rules in field_map.items():
        for api_name, rule in rules.items():
            if not api_name or not rule.get("target"):
                raise ValueError(f"Empty map entry: {object_name}.{api_name}")
            path = rule.get("path")
            if path is not None and not canonical_registry.validate(path):
                raise ValueError(f"Unregistered canonical path: {path}")


if __name__ == "__main__":
    generated = build_field_map()
    _audit(generated)
    changed = write_field_map()
    print(f"salesforce_objects={len(generated)}")
    print(f"salesforce_fields={sum(len(rules) for rules in generated.values())}")
    print(f"changed={str(changed).lower()}")

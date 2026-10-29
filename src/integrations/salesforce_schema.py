"""Strict, metadata-only Salesforce describe snapshots and capability checks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any


FIELD_MAP_PATH = Path(__file__).with_name("salesforce_field_map.json")
_TOP_LEVEL_KEYS = frozenset({"org_id", "generated_at", "objects"})
_OBJECT_KEYS = frozenset({"label", "fields"})
_FIELD_KEYS = frozenset({"type", "referenceTo", "picklistValues"})
_PICKLIST_KEYS = frozenset({"value", "label", "active", "defaultValue"})


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be a JSON object")
    return value


def _exact_keys(
    value: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    description: str,
) -> None:
    keys = set(value)
    unknown = keys - allowed
    missing = required - keys
    if unknown:
        raise ValueError(f"Unknown {description} keys: {sorted(unknown)!r}")
    if missing:
        raise ValueError(f"Missing {description} keys: {sorted(missing)!r}")


def _name(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{description} must be a non-empty, untrimmed string")
    return value


def _timestamp(value: Any) -> str:
    value = _name(value, "Snapshot generated_at")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
    except ValueError as error:
        raise ValueError("Snapshot generated_at must be ISO 8601") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Snapshot generated_at must include a timezone")
    return value


def _string_list(value: Any, description: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{description} must be a list")
    return [_name(item, f"{description} entry") for item in value]


def _picklist_values(value: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{description} must be a list")
    normalized = []
    for index, item in enumerate(value):
        item_description = f"{description}[{index}]"
        item = _mapping(item, item_description)
        _exact_keys(
            item,
            allowed=_PICKLIST_KEYS,
            required=frozenset({"value"}),
            description=item_description,
        )
        picklist_value = item["value"]
        if not isinstance(picklist_value, str):
            raise ValueError(f"{item_description}.value must be a string")
        label = item.get("label", picklist_value)
        if not isinstance(label, str):
            raise ValueError(f"{item_description}.label must be a string")
        active = item.get("active", True)
        default = item.get("defaultValue", False)
        if type(active) is not bool or type(default) is not bool:
            raise ValueError(f"{item_description} active/defaultValue must be boolean")
        normalized.append(
            {
                "value": picklist_value,
                "label": label,
                "active": active,
                "defaultValue": default,
            }
        )
    return normalized


def _field(value: Any, description: str) -> dict[str, Any]:
    value = _mapping(value, description)
    _exact_keys(
        value,
        allowed=_FIELD_KEYS,
        required=frozenset({"type"}),
        description=description,
    )
    return {
        "type": _name(value["type"], f"{description}.type"),
        "referenceTo": _string_list(
            value.get("referenceTo", []), f"{description}.referenceTo"
        ),
        "picklistValues": _picklist_values(
            value.get("picklistValues", []), f"{description}.picklistValues"
        ),
    }


def _object(value: Any, description: str) -> dict[str, Any]:
    value = _mapping(value, description)
    _exact_keys(
        value,
        allowed=_OBJECT_KEYS,
        required=frozenset({"fields"}),
        description=description,
    )
    normalized: dict[str, Any] = {}
    if "label" in value:
        normalized["label"] = _name(value["label"], f"{description}.label")
    fields = _mapping(value["fields"], f"{description}.fields")
    normalized_fields = {}
    for api_name, field_value in fields.items():
        api_name = _name(api_name, f"{description} field API name")
        normalized_fields[api_name] = _field(
            field_value, f"{description}.fields.{api_name}"
        )
    normalized["fields"] = normalized_fields
    return normalized


class SalesforceSchema:
    """An immutable-by-interface, sanitized Salesforce metadata snapshot."""

    def __init__(self, document: Mapping[str, Any]):
        self._document = deepcopy(document)
        self.org_id = document["org_id"]
        self.generated_at = document["generated_at"]

    @classmethod
    def from_dict(cls, data: dict) -> "SalesforceSchema":
        """Parse a sanitized snapshot, rejecting unknown or record-shaped data."""

        data = _mapping(data, "Salesforce snapshot")
        _exact_keys(
            data,
            allowed=_TOP_LEVEL_KEYS,
            required=_TOP_LEVEL_KEYS,
            description="Salesforce snapshot",
        )
        objects = _mapping(data["objects"], "Salesforce snapshot objects")
        normalized_objects = {}
        for object_name, object_value in objects.items():
            object_name = _name(object_name, "Salesforce object API name")
            normalized_objects[object_name] = _object(
                object_value, f"Salesforce object {object_name}"
            )
        return cls(
            {
                "org_id": _name(data["org_id"], "Snapshot org_id"),
                "generated_at": _timestamp(data["generated_at"]),
                "objects": normalized_objects,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a defensive copy suitable for deterministic JSON serialization."""

        return deepcopy(self._document)

    def has_object(self, name: str) -> bool:
        return isinstance(name, str) and name in self._document["objects"]

    def has_field(self, object_name: str, field_name: str) -> bool:
        if not self.has_object(object_name) or not isinstance(field_name, str):
            return False
        return field_name in self._document["objects"][object_name]["fields"]

    def available_fields(
        self, object_name: str, candidates: Iterable[str]
    ) -> list[str]:
        """Filter candidates exactly, preserving input order and duplicates."""

        return [
            candidate
            for candidate in candidates
            if self.has_field(object_name, candidate)
        ]

    def missing_objects(self, candidates: Iterable[str]) -> list[str]:
        return [name for name in candidates if not self.has_object(name)]


def load_field_map() -> dict[str, dict[str, dict[str, str]]]:
    """Load the generated runtime map without importing migration tooling."""

    return json.loads(FIELD_MAP_PATH.read_text(encoding="utf-8"))

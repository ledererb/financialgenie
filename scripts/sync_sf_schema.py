#!/usr/bin/env python3
"""Read-only Salesforce describe sync; never queries customer records."""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any

from simple_salesforce import Salesforce

from src.integrations.salesforce_schema import SalesforceSchema, load_field_map


# Backend tests intentionally alias backend/config.py as ``config``.  Load the
# existing project settings by path under a private name so import order cannot
# substitute the backend module for config/settings.py.
_SETTINGS_SPEC = importlib.util.spec_from_file_location(
    "_financialgenie_salesforce_project_settings",
    Path(__file__).resolve().parents[1] / "config" / "settings.py",
)
if _SETTINGS_SPEC is None or _SETTINGS_SPEC.loader is None:
    raise ImportError("Unable to load config/settings.py")
settings = importlib.util.module_from_spec(_SETTINGS_SPEC)
_SETTINGS_SPEC.loader.exec_module(settings)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_timestamp(value: datetime | str | None) -> str:
    if value is None:
        value = _utcnow()
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(
                value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value
            )
        except ValueError as error:
            raise ValueError("generated_at must be ISO 8601") from error
    if not isinstance(value, datetime):
        raise ValueError("generated_at must be a datetime or ISO 8601 string")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _raw_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be an object")
    return value


def _describe_object(sf: Any, object_name: str) -> dict[str, Any]:
    # This is the only Salesforce API operation performed by this module.
    description = _raw_mapping(
        getattr(sf, object_name).describe(), f"Describe result for {object_name}"
    )
    raw_fields = description.get("fields")
    if not isinstance(raw_fields, list):
        raise ValueError(f"Describe fields for {object_name} must be a list")

    fields = {}
    for raw_field in raw_fields:
        raw_field = _raw_mapping(raw_field, f"Describe field on {object_name}")
        api_name = raw_field.get("name")
        field_type = raw_field.get("type")
        if not isinstance(api_name, str) or not api_name:
            raise ValueError(f"Describe field on {object_name} has no API name")
        if api_name in fields:
            raise ValueError(f"Duplicate describe field: {object_name}.{api_name}")
        if not isinstance(field_type, str) or not field_type:
            raise ValueError(f"Describe field {object_name}.{api_name} has no type")

        picklist_values = []
        for raw_value in raw_field.get("picklistValues") or []:
            raw_value = _raw_mapping(
                raw_value, f"Picklist value on {object_name}.{api_name}"
            )
            picklist_values.append(
                {
                    "value": raw_value.get("value", ""),
                    "label": raw_value.get("label", raw_value.get("value", "")),
                    "active": raw_value.get("active", True),
                    "defaultValue": raw_value.get("defaultValue", False),
                }
            )
        fields[api_name] = {
            "type": field_type,
            "referenceTo": list(raw_field.get("referenceTo") or []),
            "picklistValues": picklist_values,
        }

    return {
        "label": description.get("label") or object_name,
        "fields": {api_name: fields[api_name] for api_name in sorted(fields)},
    }


def snapshot_from_describe(
    sf: Any,
    *,
    org_id: str,
    generated_at: datetime | str | None = None,
    object_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a strict metadata-only document using per-object describe only.

    Every requested object is described individually. An object the org does
    not expose (missing or inaccessible) is recorded as absent by omitting it
    from the snapshot — no fields are fabricated and one absent object never
    fails the whole snapshot. If not a single requested object can be
    described, the sync fails closed instead of writing a misleading empty
    snapshot (the failure text never includes exception details).
    """

    names = sorted(dict.fromkeys(load_field_map() if object_names is None else object_names))
    objects: dict[str, Any] = {}
    absent: list[str] = []
    for name in names:
        try:
            objects[name] = _describe_object(sf, name)
        except Exception:
            absent.append(name)
    if names and not objects:
        raise ValueError(
            "None of the requested Salesforce objects could be described "
            f"({', '.join(names)}); no snapshot was written."
        )
    schema = SalesforceSchema.from_dict(
        {
            "org_id": org_id,
            "generated_at": _iso_timestamp(generated_at),
            "objects": objects,
        }
    )
    return schema.to_dict()


def _write_atomic(destination: Path, document: Mapping[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".sf-schema-", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def sync_schema(
    sf: Any,
    output: str | Path,
    *,
    org_id: str,
    generated_at: datetime | str | None = None,
    object_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Describe configured objects and atomically write the sanitized snapshot."""

    document = snapshot_from_describe(
        sf,
        org_id=org_id,
        generated_at=generated_at,
        object_names=object_names,
    )
    _write_atomic(Path(output), document)
    return document


def default_snapshot_path(
    org_id: str, generated_at: datetime | str | None = None
) -> Path:
    safe_org_id = re.sub(r"[^A-Za-z0-9_-]", "_", org_id)
    date = _iso_timestamp(generated_at)[:10]
    return settings.PROJECT_ROOT / "config" / f"sf_schema_{safe_org_id}_{date}.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync Salesforce describe metadata only; no record queries."
    )
    parser.add_argument("--output", type=Path, help="Snapshot JSON destination")
    parser.add_argument(
        "--org-id",
        default=os.getenv("SF_ORG_ID", ""),
        help="Salesforce org ID (or SF_ORG_ID); identity calls are not used",
    )
    parser.add_argument(
        "--objects",
        default="",
        help=(
            "Comma-separated Salesforce object API names to describe; "
            "defaults to every object in the generated field map"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    missing = [
        name
        for name, value in (
            ("SF_USERNAME", settings.SF_USERNAME),
            ("SF_PASSWORD", settings.SF_PASSWORD),
        )
        if not value
    ]
    if missing:
        print(f"Missing Salesforce settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not args.org_id:
        print(
            "Set SF_ORG_ID or --org-id; identity and record queries are disabled.",
            file=sys.stderr,
        )
        return 2
    requested_names = [
        name.strip() for name in args.objects.split(",") if name.strip()
    ]
    if args.objects and not requested_names:
        print("--objects must list at least one object API name", file=sys.stderr)
        return 2

    generated_at = _utcnow()
    destination = args.output or default_snapshot_path(args.org_id, generated_at)
    try:
        salesforce = Salesforce(
            username=settings.SF_USERNAME,
            password=settings.SF_PASSWORD,
            security_token=settings.SF_SECURITY_TOKEN,
            domain=settings.SF_DOMAIN,
        )
        document = sync_schema(
            salesforce,
            destination,
            org_id=args.org_id,
            generated_at=generated_at,
            object_names=requested_names or None,
        )
    except Exception as error:
        # Exception text can contain request headers or credentials; do not print it.
        print(
            f"Salesforce describe sync failed ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1

    # Absent objects are recorded by their omission; report them without any
    # exception detail (which could contain request headers or credentials).
    absent_names = [
        name
        for name in sorted(requested_names)
        if name not in document["objects"]
    ] if requested_names else sorted(
        set(load_field_map()) - set(document["objects"])
    )
    for name in absent_names:
        print(
            f"Salesforce object {name} is missing or inaccessible; "
            "recorded as absent in the snapshot.",
            file=sys.stderr,
        )

    field_count = sum(
        len(object_data["fields"]) for object_data in document["objects"].values()
    )
    summary = (
        f"snapshot={destination} objects={len(document['objects'])} "
        f"fields={field_count}"
    )
    if absent_names:
        summary += f" absent={len(absent_names)}"
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

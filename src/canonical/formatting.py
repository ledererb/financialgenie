"""Central text formatting for canonical scalar values."""

from datetime import date, datetime
from enum import Enum
import re
from typing import Any


def _format_money(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError, OverflowError):
        return str(value)


def _format_date(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y.%m.%d")
    if isinstance(value, str):
        text = value.strip()
        match = re.fullmatch(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
        if match:
            try:
                parsed = date(*(int(part) for part in match.groups()))
            except ValueError:
                return text
            return parsed.strftime("%Y.%m.%d")
        return text
    return str(value)


def format_value(value: Any, field_type: str) -> str:
    """Format a registry-typed scalar without inventing missing values."""

    if value is None:
        return ""
    if isinstance(value, Enum):
        value = value.value
    if field_type == "money":
        return _format_money(value)
    if field_type == "date":
        return _format_date(value)
    if field_type == "boolean" and isinstance(value, bool):
        return "Igen" if value else "Nem"
    return str(value)


def apply_fragment(value: str, fragment: str | None) -> str:
    """Extract a date component or one-based character from formatted text."""

    if not fragment:
        return value
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = str(fragment).strip().lower()

    if normalized in {"year", "month", "day"}:
        parts = re.split(r"[.\-/]", text)
        if len(parts) == 1 and parts[0].isdigit():
            return parts[0] if normalized == "year" else ""
        if len(parts) != 3 or not all(part.isdigit() for part in parts):
            return ""
        year, month, day = parts
        return {
            "year": year,
            "month": month.zfill(2),
            "day": day.zfill(2),
        }[normalized]

    if normalized.startswith("digit:"):
        try:
            index = int(normalized.removeprefix("digit:")) - 1
        except ValueError:
            return ""
        return text[index] if 0 <= index < len(text) else ""

    raise ValueError(f"Unknown canonical fragment: {fragment!r}")

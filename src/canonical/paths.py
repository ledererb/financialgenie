"""Strict parser for Salesforce-independent Genie canonical paths."""

from dataclasses import dataclass
import re


MAX_PARTICIPANT_INDEX = 3
MAX_PROPERTY_INDEX = 9
MAX_RELATED_INDEX = 12

#: Cutover Task 4 review fix 3 — the participant role list has ONE source of
#: truth: this tuple. The registry exposes it (``CanonicalRegistry.roles()``)
#: and every consumer (recognizer role expansion, UI grouping) reads it from
#: there instead of duplicating a literal.
PARTICIPANT_ROLES: tuple[str, ...] = (
    "borrower",
    "co_borrower",
    "guarantor",
    "mortgagor",
    "usufructuary",
)
_ROLES = "|".join(PARTICIPANT_ROLES)
_INDEX = r"[1-9][0-9]*"
_IDENTIFIER = r"[a-z][a-z0-9_]*"
_ADDRESS_FIELD = r"zip_code|city|street|house_number|floor|door|district"
_PARTICIPANT_FIELD = (
    rf"(?:{_IDENTIFIER}|(?:address|mailing_address)\.(?:{_ADDRESS_FIELD}))"
)
_PROPERTY_FIELD = rf"(?:{_IDENTIFIER}|address\.(?:{_ADDRESS_FIELD}))"

_DEAL_OR_ACCOUNT_RE = re.compile(
    rf"(?P<namespace>deal|account)\.(?P<field>{_IDENTIFIER})"
)
_PROPERTY_RE = re.compile(
    rf"property(?:\[(?P<property_index>{_INDEX})\])?\."
    rf"(?P<field>{_PROPERTY_FIELD})"
)
_PARTICIPANT_RE = re.compile(
    rf"participant\.(?P<role>{_ROLES})"
    rf"(?:\[(?P<participant_index>{_INDEX})\])?\."
    rf"(?P<field>{_PARTICIPANT_FIELD})"
)
_RELATED_RE = re.compile(
    rf"participant\.(?P<role>{_ROLES})"
    rf"(?:\[(?P<participant_index>{_INDEX})\])?\."
    rf"(?P<collection>income|existing_loan)\[(?P<collection_index>{_INDEX})\]\."
    rf"(?P<field>{_IDENTIFIER})"
)


@dataclass(frozen=True)
class CanonicalPath:
    """The parsed components of one concrete canonical path."""

    namespace: str
    field: str
    role: str | None = None
    participant_index: int | None = None
    collection: str | None = None
    collection_index: int | None = None
    property_index: int | None = None


def _bounded_index(raw: str | None, maximum: int, label: str, path: str) -> int | None:
    if raw is None:
        return None
    value = int(raw)
    if value > maximum:
        raise ValueError(f"{label} index outside 1..{maximum} in {path!r}")
    return value


def parse_path(path: str) -> CanonicalPath:
    """Parse an approved concrete path without aliases or normalization.

    The parser validates grammar and index bounds. Catalog membership is handled
    separately by :class:`src.canonical.registry.CanonicalRegistry`.
    """

    if not isinstance(path, str) or not path:
        raise ValueError("Canonical path must be a non-empty string")

    match = _DEAL_OR_ACCOUNT_RE.fullmatch(path)
    if match:
        return CanonicalPath(namespace=match["namespace"], field=match["field"])

    match = _PROPERTY_RE.fullmatch(path)
    if match:
        return CanonicalPath(
            namespace="property",
            field=match["field"],
            property_index=_bounded_index(
                match["property_index"], MAX_PROPERTY_INDEX, "Property", path
            ),
        )

    match = _RELATED_RE.fullmatch(path)
    if match:
        return CanonicalPath(
            namespace="participant",
            field=match["field"],
            role=match["role"],
            participant_index=_bounded_index(
                match["participant_index"], MAX_PARTICIPANT_INDEX, "Participant", path
            ),
            collection=match["collection"],
            collection_index=_bounded_index(
                match["collection_index"], MAX_RELATED_INDEX, "Related", path
            ),
        )

    match = _PARTICIPANT_RE.fullmatch(path)
    if match:
        return CanonicalPath(
            namespace="participant",
            field=match["field"],
            role=match["role"],
            participant_index=_bounded_index(
                match["participant_index"], MAX_PARTICIPANT_INDEX, "Participant", path
            ),
        )

    raise ValueError(f"Invalid canonical path: {path!r}")

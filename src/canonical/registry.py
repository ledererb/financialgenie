"""Versioned Genie canonical field registry and template expansion."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import inspect
import json
from pathlib import Path
import re
from typing import Any

from src.models.canonical_model import (
    Address,
    DealData,
    ExistingLoan,
    IncomeEntry,
    LoanDetails,
    Participant,
    Property,
)

from .paths import (
    MAX_PARTICIPANT_INDEX,
    MAX_PROPERTY_INDEX,
    MAX_RELATED_INDEX,
    PARTICIPANT_ROLES,
    parse_path,
)


_DEFAULT_REGISTRY_PATH = Path(__file__).with_name("fields.json")
_SUPPORTED_TYPES = frozenset({"string", "integer", "number", "boolean", "date", "money"})
_SUPPORTED_REPEATS = frozenset({"none", "participant", "property", "income", "existing_loan"})
# Review fix 3: the role list comes from src.canonical.paths (single source
# of truth); the registry re-exposes it through CanonicalRegistry.roles().
_LIMIT_KEYS = ("participant", "property", "related")
_LIMIT_MAXIMA = {
    "participant": MAX_PARTICIPANT_INDEX,
    "property": MAX_PROPERTY_INDEX,
    "related": MAX_RELATED_INDEX,
}
_FIELD_KEYS = frozenset({"path", "label", "type", "source", "repeat"})

_IDENTIFIER = r"[a-z][a-z0-9_]*"
_ADDRESS_SOURCE = r"(?:zip_code|city|street|house_number|floor|door|district|full_address)"
_SOURCE_RE = re.compile(
    rf"(?:"
    rf"participant\.(?:{_IDENTIFIER}|(?:address|mailing_address)\.(?:{_ADDRESS_SOURCE}))"
    rf"|property\.(?:{_IDENTIFIER}|address\.(?:{_ADDRESS_SOURCE}))"
    rf"|(?:loan|account|deal)\.{_IDENTIFIER}"
    rf"|extra\.{_IDENTIFIER}"
    rf"|consents\.{_IDENTIFIER}"
    rf"|(?:income|existing_loan)\.{_IDENTIFIER}"
    rf")"
)
_TYPED_SOURCE_MODELS = {
    "participant": Participant,
    "property": Property,
    "loan": LoanDetails,
    "deal": DealData,
    "income": IncomeEntry,
    "existing_loan": ExistingLoan,
}
_NESTED_SOURCE_MODELS = {
    ("participant", "address"): Address,
    ("participant", "mailing_address"): Address,
    ("property", "address"): Address,
}


def _model_has_attribute(model: type, attribute: str) -> bool:
    if attribute in model.model_fields:
        return True
    return isinstance(inspect.getattr_static(model, attribute, None), property)


def _source_has_valid_structure(source: str) -> bool:
    namespace, _, remainder = source.partition(".")
    model = _TYPED_SOURCE_MODELS.get(namespace)
    if model is None:
        # Account, extra, and consent dictionaries intentionally have dynamic keys.
        return True
    parts = remainder.split(".")
    if len(parts) == 1:
        return _model_has_attribute(model, parts[0])
    if len(parts) == 2:
        nested = _NESTED_SOURCE_MODELS.get((namespace, parts[0]))
        return nested is not None and _model_has_attribute(nested, parts[1])
    return False


_SCALAR_SOURCE_OWNERS = {
    "deal": frozenset({"deal", "loan", "extra"}),
    "account": frozenset({"account"}),
    "participant": frozenset({"participant", "extra", "consents"}),
    "property": frozenset({"property", "extra"}),
}


def _validate_source_ownership(path: str, source: str, repeat: str) -> None:
    if repeat in {"income", "existing_loan"}:
        allowed = frozenset({repeat})
    elif repeat == "participant":
        allowed = _SCALAR_SOURCE_OWNERS["participant"]
    elif repeat == "property":
        allowed = _SCALAR_SOURCE_OWNERS["property"]
    else:
        parsed = parse_path(path)
        if parsed.collection is not None:
            allowed = frozenset({parsed.collection})
        else:
            allowed = _SCALAR_SOURCE_OWNERS[parsed.namespace]

    source_namespace = source.partition(".")[0]
    if source_namespace not in allowed:
        raise ValueError(f"Canonical source {source!r} does not belong to {path!r}")


@dataclass(frozen=True)
class CanonicalField:
    """One concrete field definition produced from a registry template."""

    path: str
    label: str
    type: str
    source: str
    repeat: str


class CanonicalRegistry:
    """Load, validate, and expand a canonical registry document.

    ``document`` may be a decoded mapping or a JSON file path. When omitted,
    the package's curated ``fields.json`` is loaded.
    """

    def __init__(self, document: Mapping[str, Any] | str | Path | None = None):
        if document is None:
            document = _DEFAULT_REGISTRY_PATH
        if isinstance(document, (str, Path)):
            document = json.loads(Path(document).read_text(encoding="utf-8"))
        if not isinstance(document, Mapping):
            raise ValueError("Canonical registry must be a JSON object")

        self._version = self._validate_version(document)
        self._limits = self._validate_limits(document)
        definitions = document.get("fields")
        if not isinstance(definitions, list):
            raise ValueError("Canonical registry 'fields' must be a list")

        self._fields: dict[str, CanonicalField] = {}
        # Template (pre-expansion) definitions kept in document order so
        # :meth:`definitions` can expose the shared vocabulary (Cutover Task 4).
        self._template_paths: list[str] = []
        self._template_fields: list[CanonicalField] = []
        for definition in definitions:
            self._add_definition(definition)
        # Review fix 4: immutable-after-construction caches (built lazily on
        # first use, then shared by every caller).
        self._definitions_cache: list[dict[str, Any]] | None = None
        self._concrete_fields_cache: list[dict] | None = None

    @staticmethod
    def _validate_version(document: Mapping[str, Any]) -> int:
        version = document.get("version")
        if type(version) is not int or version < 1:
            raise ValueError("Canonical registry 'version' must be a positive integer")
        return version

    @staticmethod
    def _validate_limits(document: Mapping[str, Any]) -> dict[str, int]:
        limits = document.get("limits")
        if not isinstance(limits, Mapping) or set(limits) != set(_LIMIT_KEYS):
            raise ValueError("Canonical registry limits must contain participant, property, related")
        validated: dict[str, int] = {}
        for key in _LIMIT_KEYS:
            value = limits[key]
            maximum = _LIMIT_MAXIMA[key]
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"Canonical registry limit {key!r} must be within 1..{maximum}")
            validated[key] = value
        return validated

    def _add_definition(self, definition: Any) -> None:
        if not isinstance(definition, Mapping) or set(definition) != _FIELD_KEYS:
            raise ValueError("Each canonical field requires exactly path, label, type, source, repeat")

        path = definition["path"]
        label = definition["label"]
        field_type = definition["type"]
        source = definition["source"]
        repeat = definition["repeat"]

        if not isinstance(path, str) or not path:
            raise ValueError("Canonical field path must be a non-empty string")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"Canonical field {path!r} requires a Hungarian label")
        if not isinstance(field_type, str) or field_type not in _SUPPORTED_TYPES:
            raise ValueError(f"Unknown canonical field type {field_type!r} for {path!r}")
        if (
            not isinstance(source, str)
            or not _SOURCE_RE.fullmatch(source)
            or not _source_has_valid_structure(source)
        ):
            raise ValueError(f"Unknown canonical field source {source!r} for {path!r}")
        if not isinstance(repeat, str) or repeat not in _SUPPORTED_REPEATS:
            raise ValueError(f"Unknown canonical repeat mode {repeat!r} for {path!r}")
        _validate_source_ownership(path, source, repeat)

        self._template_paths.append(path)
        self._template_fields.append(
            CanonicalField(
                path=path,
                label=label,
                type=field_type,
                source=source,
                repeat=repeat,
            )
        )
        for concrete_path in self._expand(path, repeat):
            parse_path(concrete_path)
            if concrete_path in self._fields:
                raise ValueError(f"Duplicate canonical path: {concrete_path!r}")
            self._fields[concrete_path] = CanonicalField(
                path=concrete_path,
                label=label,
                type=field_type,
                source=source,
                repeat=repeat,
            )

    def _expand(self, path: str, repeat: str) -> list[str]:
        if repeat == "none":
            return [path]
        if repeat == "participant":
            return self._expand_participant(path)
        if repeat == "property":
            return self._expand_property(path)
        return self._expand_related(path, repeat)

    def _expand_participant(self, template: str) -> list[str]:
        prefix = "participant.{role}."
        if not template.startswith(prefix) or template.count("{role}") != 1:
            raise ValueError(f"Invalid participant registry template: {template!r}")

        expanded: list[str] = []
        for role in PARTICIPANT_ROLES:
            base = template.replace("{role}", role)
            parsed = parse_path(base)
            if (
                parsed.namespace != "participant"
                or parsed.role != role
                or parsed.participant_index is not None
                or parsed.collection is not None
            ):
                raise ValueError(f"Invalid participant registry template: {template!r}")
            expanded.append(base)
            for index in range(1, self._limits["participant"] + 1):
                expanded.append(
                    base.replace(
                        f"participant.{role}", f"participant.{role}[{index}]", 1
                    )
                )
        return expanded

    def _expand_property(self, template: str) -> list[str]:
        if not template.startswith("property."):
            raise ValueError(f"Invalid property registry template: {template!r}")
        parsed = parse_path(template)
        if parsed.namespace != "property" or parsed.property_index is not None:
            raise ValueError(f"Invalid property registry template: {template!r}")

        suffix = template.removeprefix("property.")
        expanded = [template]
        expanded.extend(
            f"property[{index}].{suffix}"
            for index in range(1, self._limits["property"] + 1)
        )
        return expanded

    def _expand_related(self, template: str, repeat: str) -> list[str]:
        prefix = f"participant.{{role}}.{repeat}."
        if not template.startswith(prefix) or template.count("{role}") != 1:
            raise ValueError(f"Invalid {repeat} registry template: {template!r}")
        field = template.removeprefix(prefix)
        if not field:
            raise ValueError(f"Invalid {repeat} registry template: {template!r}")

        expanded: list[str] = []
        for role in PARTICIPANT_ROLES:
            participant_prefixes = [f"participant.{role}"]
            participant_prefixes.extend(
                f"participant.{role}[{index}]"
                for index in range(1, self._limits["participant"] + 1)
            )
            for participant_prefix in participant_prefixes:
                for index in range(1, self._limits["related"] + 1):
                    concrete = f"{participant_prefix}.{repeat}[{index}].{field}"
                    parsed = parse_path(concrete)
                    if parsed.collection != repeat or parsed.field != field:
                        raise ValueError(f"Invalid {repeat} registry template: {template!r}")
                    expanded.append(concrete)
        return expanded

    def validate(self, path: str) -> bool:
        """Return whether ``path`` is grammatical and present in the registry."""

        try:
            parse_path(path)
        except ValueError:
            return False
        return path in self._fields

    def get(self, path: str) -> CanonicalField:
        """Return a registered field; malformed paths raise ``ValueError``."""

        parse_path(path)
        try:
            return self._fields[path]
        except KeyError:
            raise KeyError(f"Unknown canonical path: {path!r}") from None

    def roles(self) -> tuple[str, ...]:
        """The canonical participant roles (single source: paths module).

        Cutover Task 4 review fix 3: consumers (recognizer role expansion,
        Studio grouping) read the role list from the registry instead of
        duplicating a literal. The tuple is immutable and shared — treat it
        as read-only.
        """
        return PARTICIPANT_ROLES

    def canonical_fields(self) -> list[dict]:
        """Return concrete field definitions in deterministic registry order.

        Review fix 4: the expansion is immutable after construction, so the
        list is built ONCE and the very same object is returned on every
        call — callers must treat it (and its dicts) as read-only.
        """
        if self._concrete_fields_cache is None:
            self._concrete_fields_cache = [asdict(field) for field in self._fields.values()]
        return self._concrete_fields_cache

    def definitions(self) -> list[dict[str, Any]]:
        """Return the raw template definitions in deterministic registry order.

        Cutover Task 4: production consumers (recognizer prompts/heuristics,
        mapping editor catalog) share the registry as the single canonical
        vocabulary. Templates carry the pre-expansion ``{role}`` form plus
        ``repeat`` metadata so callers can derive their own representative
        paths; every template remains loadable through :meth:`get` after the
        caller substitutes a concrete role/index. The list is immutable after
        construction — the same object is returned on every call (review fix 4).
        """
        if self._definitions_cache is None:
            self._definitions_cache = [
                {
                    "path": path,
                    "label": field.label,
                    "type": field.type,
                    "source": field.source,
                    "repeat": field.repeat,
                }
                for path, field in zip(
                    self._template_paths, self._template_fields, strict=True
                )
            ]
        return self._definitions_cache


canonical_registry = CanonicalRegistry()

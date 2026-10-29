"""Resolve registry-approved scalar paths from the canonical deal model."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.models.canonical_model import DealData, Participant, ParticipantRole, Property

from .formatting import apply_fragment, format_value
from .paths import CanonicalPath, parse_path
from .registry import (
    CanonicalField,
    CanonicalRegistry,
    _validate_source_ownership,
    canonical_registry,
)


@dataclass(frozen=True)
class ResolutionContext:
    """The current one-based document instances used by base paths."""

    participant: tuple[str, int] | None = None
    property_index: int = 1


_ROLE_MAP = {
    "borrower": ParticipantRole.BORROWER,
    "co_borrower": ParticipantRole.CO_BORROWER,
    "guarantor": ParticipantRole.GUARANTOR,
    "mortgagor": ParticipantRole.MORTGAGOR,
    "usufructuary": ParticipantRole.BENEFICIARY,
}


class CanonicalResolver:
    """Read scalar values through canonical registry source metadata."""

    def __init__(self, registry: CanonicalRegistry | None = None):
        self._registry = registry if registry is not None else canonical_registry

    def _field(self, path: str) -> CanonicalField:
        try:
            return self._registry.get(path)
        except KeyError:
            raise ValueError(f"Unknown canonical path: {path!r}") from None

    @staticmethod
    def _at(items: list[Any], index: Any) -> Any | None:
        if not isinstance(index, int) or isinstance(index, bool) or index < 1:
            return None
        return items[index - 1] if index <= len(items) else None

    @staticmethod
    def _navigate(root: Any, source: str) -> Any | None:
        value = root
        for part in source.split("."):
            if value is None:
                return None
            if isinstance(value, Mapping):
                value = value.get(part)
            else:
                value = getattr(value, part, None)
        return value

    @classmethod
    def _owned_value(cls, owner: Any, source: str, namespace: str) -> Any | None:
        prefix = f"{namespace}."
        if source.startswith(prefix):
            return cls._navigate(owner, source.removeprefix(prefix))
        if source.startswith("extra."):
            extra = cls._navigate(owner, "extra")
            return cls._navigate(extra, source.removeprefix("extra."))
        if source.startswith("consents."):
            consents = cls._navigate(owner, "consents")
            return cls._navigate(consents, source.removeprefix("consents."))
        raise ValueError(
            f"Canonical source {source!r} does not belong to {namespace!r}"
        )

    @classmethod
    def _deal_value(cls, deal: DealData, source: str) -> Any | None:
        roots = {
            "deal": deal,
            "loan": deal.loan,
            "account": deal.account,
            "extra": deal.extra,
        }
        namespace, separator, field = source.partition(".")
        if not separator or namespace not in roots:
            raise ValueError(f"Canonical source {source!r} is not a deal source")
        return cls._navigate(roots[namespace], field)

    def _participant(
        self,
        deal: DealData,
        parsed: CanonicalPath,
        context: ResolutionContext,
    ) -> Participant | None:
        participants = [
            participant
            for participant in deal.active_participants
            if participant.role == _ROLE_MAP[parsed.role]
        ]
        index = parsed.participant_index
        if (
            index is None
            and context.participant is not None
            and context.participant[0] == parsed.role
        ):
            index = context.participant[1]
        return self._at(participants, 1 if index is None else index)

    @staticmethod
    def _income_is_excluded(participant: Participant) -> bool:
        """Exclude income rows only for an explicit non-income-bearing participant."""
        return participant.income_bearing is False

    @classmethod
    def _related_value(
        cls,
        participant: Participant,
        parsed: CanonicalPath,
        field: CanonicalField,
    ) -> Any | None:
        if parsed.collection == "income":
            if cls._income_is_excluded(participant):
                return None
            rows = participant.incomes
        elif parsed.collection == "existing_loan":
            rows = participant.existing_loans
        else:
            raise ValueError(f"Unsupported canonical collection: {parsed.collection!r}")

        row = cls._at(rows, parsed.collection_index)
        if row is None:
            return None
        return cls._owned_value(row, field.source, parsed.collection)

    def resolve(
        self,
        deal: DealData,
        path: str,
        context: ResolutionContext | None = None,
    ) -> Any | None:
        """Resolve one registered scalar path, or ``None`` when it is absent."""

        field = self._field(path)
        parsed = parse_path(path)
        _validate_source_ownership(path, field.source, field.repeat)
        current = context if context is not None else ResolutionContext()

        if parsed.namespace == "participant":
            participant = self._participant(deal, parsed, current)
            if participant is None:
                return None
            if parsed.collection is not None:
                return self._related_value(participant, parsed, field)
            return self._owned_value(participant, field.source, "participant")

        if parsed.namespace == "property":
            index = (
                parsed.property_index
                if parsed.property_index is not None
                else current.property_index
            )
            prop: Property | None = self._at(deal.properties, index)
            if prop is None:
                return None
            return self._owned_value(prop, field.source, "property")

        return self._deal_value(deal, field.source)

    def resolve_text(
        self,
        deal: DealData,
        path: str,
        context: ResolutionContext | None = None,
        fragment: str | None = None,
    ) -> str:
        """Resolve and centrally format a scalar, then apply an optional fragment."""

        field = self._field(path)
        value = self.resolve(deal, path, context)
        return apply_fragment(format_value(value, field.type), fragment)

"""
FinancialGenie – Teljességi ellenőrző (Completeness Checker)

Validálja, hogy egy DealData objektum tartalmazza-e az összes szükséges
adatot egy nyomtatvány kitöltéséhez.

Ellenőrzési típusok:
- MISSING: Kötelező mező teljesen hiányzik (None).
- EMPTY: A mező létezik, de üres string.
- SUSPICIOUS: A mező tartalmaz értéket, de gyanús (pl. túl rövid név,
  érvénytelen formátumú adóazonosító).

A checker használható:
1. Kitöltés előtti validációhoz (van-e elég adat a PDF generáláshoz).
2. Minőségellenőrzéshez (figyelmeztetések a felhasználónak).

Konfiguráció:
A required_fields lista kanonikus mező útvonalakat tartalmaz, pl.:
  - "loan.loan_amount"
  - "participant.*.name"  (* = minden aktív szereplő)
  - "property.0.parcel_number"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.models.canonical_model import DealData, Participant, Property

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Eredmény struktúrák
# ---------------------------------------------------------------------------

class CompletenessStatus(str, Enum):
    """Az ügylet teljességi státusza."""
    COMPLETE = "complete"
    """Minden kötelező mező ki van töltve, nincs figyelmeztetés."""

    INCOMPLETE = "incomplete"
    """Egy vagy több kötelező mező hiányzik."""

    WARNING = "warning"
    """Minden kötelező mező megvan, de vannak figyelmeztetések."""


class IssueType(str, Enum):
    """Mező-probléma típusa."""
    MISSING = "missing"
    """A mező teljesen hiányzik (None)."""

    EMPTY = "empty"
    """A mező üres string."""

    SUSPICIOUS = "suspicious"
    """A mező értéke gyanús (formátum, hossz stb.)."""

    STRUCTURAL = "structural"
    """Strukturális probléma (pl. nincs szereplő, nincs ingatlan)."""


@dataclass
class FieldIssue:
    """
    Egy konkrét mezővel kapcsolatos probléma.

    Attributes:
        field_path: A mező kanonikus útvonala (pl. "participant.0.name").
        issue_type: A probléma típusa.
        message: Emberi olvasásra szánt hibaüzenet (magyar).
        severity: Súlyosság 1-3 (1=kritikus, 3=enyhe).
    """
    field_path: str
    issue_type: IssueType
    message: str
    severity: int = 1

    @property
    def is_blocking(self) -> bool:
        """Blokkoló-e a hiba (megakadályozza a kitöltést)."""
        return self.issue_type in (IssueType.MISSING, IssueType.EMPTY)


@dataclass
class CompletenessReport:
    """
    Teljességi ellenőrzés eredménye.

    Attributes:
        status: Összesített státusz.
        issues: A talált problémák listája.
        checked_fields: Az ellenőrzött mezők száma.
        deal_id: Az ügylet azonosítója.
    """
    status: CompletenessStatus = CompletenessStatus.COMPLETE
    issues: list[FieldIssue] = field(default_factory=list)
    checked_fields: int = 0
    deal_id: str = ""

    @property
    def blocking_issues(self) -> list[FieldIssue]:
        """Blokkoló (kitöltést megakadályozó) problémák."""
        return [i for i in self.issues if i.is_blocking]

    @property
    def warnings(self) -> list[FieldIssue]:
        """Nem-blokkoló figyelmeztetések."""
        return [i for i in self.issues if not i.is_blocking]

    @property
    def is_fillable(self) -> bool:
        """Kitölthető-e az ügylet (nincs blokkoló hiba)."""
        return len(self.blocking_issues) == 0

    @property
    def summary(self) -> str:
        """Emberi olvasásra szánt összegzés."""
        return (
            f"Teljességi ellenőrzés [{self.deal_id}]: {self.status.value} | "
            f"ellenőrzött: {self.checked_fields} mező | "
            f"blokkoló hibák: {len(self.blocking_issues)}, "
            f"figyelmeztetések: {len(self.warnings)}"
        )


# ---------------------------------------------------------------------------
# Ellenőrző
# ---------------------------------------------------------------------------

# Gyanús érték validátorok
_SUSPICIOUS_VALIDATORS: dict[str, dict[str, Any]] = {
    "name": {
        "min_length": 3,
        "message": "A név túl rövid (min. 3 karakter).",
    },
    "mother_name": {
        "min_length": 3,
        "message": "Az anyja neve túl rövid (min. 3 karakter).",
    },
    "tax_id": {
        "pattern": r"^\d{10}$",
        "message": "Az adóazonosító jel 10 számjegyből kell álljon.",
    },
    "personal_id": {
        "min_length": 6,
        "message": "A személyi igazolvány szám túl rövid.",
    },
    "phone": {
        "pattern": r"^[\d\s\+\-\(\)]{7,}$",
        "message": "A telefonszám formátuma gyanús.",
    },
    "email": {
        "pattern": r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        "message": "Az e-mail cím formátuma érvénytelen.",
    },
    "zip_code": {
        "pattern": r"^\d{4}$",
        "message": "Az irányítószám 4 számjegyből kell álljon.",
    },
    "parcel_number": {
        "min_length": 2,
        "message": "A helyrajzi szám túl rövid.",
    },
}


class CompletenessChecker:
    """
    Ügyletadatok teljességi ellenőrzője.

    Bejárja a DealData-t a required_fields lista alapján, és jelentést
    készít a hiányzó, üres vagy gyanús értékekről.

    Mezőútvonal formátumok:
    - "loan.loan_amount"          → LoanDetails.loan_amount
    - "participant.*.name"        → Minden aktív szereplő neve
    - "participant.0.birth_date"  → Első szereplő születési dátuma
    - "property.*.parcel_number"  → Minden ingatlan helyrajzi száma
    - "property.0.address.city"   → Első ingatlan címének települése

    A * wildcard minden aktív szereplőt / ingatlant jelent.

    Használat:
        checker = CompletenessChecker()
        report = checker.check(deal, ["loan.loan_amount", "participant.*.name"])
        print(report.summary)
    """

    def __init__(self, run_suspicious_checks: bool = True):
        """
        Args:
            run_suspicious_checks: Futtatja-e a gyanús-érték ellenőrzéseket.
                                   Ha False, csak hiányzó/üres mezőket keres.
        """
        self.run_suspicious_checks = run_suspicious_checks

    def check(
        self,
        deal: DealData,
        required_fields: list[str],
    ) -> CompletenessReport:
        """
        Teljesség ellenőrzése.

        Args:
            deal: A kanonikus ügyletadatok.
            required_fields: Kötelező mező útvonalak listája.

        Returns:
            CompletenessReport az eredményekkel.
        """
        report = CompletenessReport(deal_id=deal.deal_id)

        # --- Strukturális ellenőrzések ---
        self._check_structural(deal, report)

        # --- Mezőnkénti ellenőrzés ---
        for field_path in required_fields:
            self._check_field(deal, field_path, report)

        # --- Státusz meghatározása ---
        report.status = self._determine_status(report)

        logger.info(report.summary)
        for issue in report.blocking_issues:
            logger.warning("Blokkoló hiba: %s – %s", issue.field_path, issue.message)

        return report

    def _check_structural(self, deal: DealData, report: CompletenessReport) -> None:
        """Strukturális validációk (pl. létezik-e szereplő, ingatlan)."""
        if not deal.participants:
            report.issues.append(FieldIssue(
                field_path="participants",
                issue_type=IssueType.STRUCTURAL,
                message="Az ügyletnek nincsenek szereplői.",
                severity=1,
            ))

        active = deal.active_participants
        if deal.participants and not active:
            report.issues.append(FieldIssue(
                field_path="participants",
                issue_type=IssueType.STRUCTURAL,
                message=(
                    "Az ügyletnek vannak szereplői, de egyik sem aktív "
                    '(mind "kuka" státuszú).'
                ),
                severity=1,
            ))

        borrowers = deal.borrowers
        if active and not borrowers:
            report.issues.append(FieldIssue(
                field_path="participants.borrower",
                issue_type=IssueType.STRUCTURAL,
                message="Az ügyletnek nincs adósa (BORROWER).",
                severity=1,
            ))

        if not deal.properties:
            report.issues.append(FieldIssue(
                field_path="properties",
                issue_type=IssueType.STRUCTURAL,
                message="Az ügyletnek nincsenek fedezeti ingatlanjai.",
                severity=2,
            ))

    def _check_field(
        self,
        deal: DealData,
        field_path: str,
        report: CompletenessReport,
    ) -> None:
        """
        Egyetlen mező útvonal ellenőrzése.

        A wildcard (*) kibontása és a tényleges érték ellenőrzése.
        """
        parts = field_path.split(".")

        if not parts:
            return

        root = parts[0]

        if root == "loan":
            self._check_loan_field(deal, parts[1:], field_path, report)

        elif root == "participant":
            self._check_participant_fields(deal, parts[1:], field_path, report)

        elif root == "property":
            self._check_property_fields(deal, parts[1:], field_path, report)

        elif root == "deal_id":
            self._check_value(deal.deal_id, field_path, "deal_id", report)

        else:
            # Egyéb gyökér mezők
            value = getattr(deal, root, None)
            self._check_value(value, field_path, root, report)

    def _check_loan_field(
        self,
        deal: DealData,
        remaining: list[str],
        full_path: str,
        report: CompletenessReport,
    ) -> None:
        """Hitel-mező ellenőrzése."""
        if not remaining:
            return

        report.checked_fields += 1
        attr_name = remaining[0]
        value = getattr(deal.loan, attr_name, None)
        self._check_value(value, full_path, attr_name, report)

    def _check_participant_fields(
        self,
        deal: DealData,
        remaining: list[str],
        full_path: str,
        report: CompletenessReport,
    ) -> None:
        """
        Szereplő-mező ellenőrzése.

        Ha az index '*', minden aktív szereplőre lefut.
        """
        if len(remaining) < 2:
            return

        index_part = remaining[0]
        attr_parts = remaining[1:]  # Lehetnek beágyazott mezők (pl. address.city)

        participants = deal.active_participants

        if index_part == "*":
            # Wildcard: minden aktív szereplő
            if not participants:
                report.checked_fields += 1
                report.issues.append(FieldIssue(
                    field_path=full_path,
                    issue_type=IssueType.MISSING,
                    message="Nincs aktív szereplő az ellenőrzéshez.",
                    severity=1,
                ))
                return

            for idx, participant in enumerate(participants):
                concrete_path = full_path.replace("*", str(idx))
                self._check_participant_attr(
                    participant, attr_parts, concrete_path, report
                )
        else:
            # Konkrét index
            try:
                idx = int(index_part)
            except ValueError:
                logger.warning("Érvénytelen szereplő index: %s", index_part)
                return

            if idx >= len(participants):
                report.checked_fields += 1
                report.issues.append(FieldIssue(
                    field_path=full_path,
                    issue_type=IssueType.MISSING,
                    message=f"Nincs {idx}. indexű aktív szereplő.",
                    severity=1,
                ))
                return

            self._check_participant_attr(
                participants[idx], attr_parts, full_path, report
            )

    def _check_participant_attr(
        self,
        participant: Participant,
        attr_parts: list[str],
        full_path: str,
        report: CompletenessReport,
    ) -> None:
        """Egy konkrét szereplő attribútumának ellenőrzése."""
        report.checked_fields += 1
        value = self._resolve_nested_attr(participant, attr_parts)
        leaf_name = attr_parts[-1] if attr_parts else ""
        self._check_value(value, full_path, leaf_name, report)

    def _check_property_fields(
        self,
        deal: DealData,
        remaining: list[str],
        full_path: str,
        report: CompletenessReport,
    ) -> None:
        """
        Ingatlan-mező ellenőrzése.

        Ha az index '*', minden ingatlanra lefut.
        """
        if len(remaining) < 2:
            return

        index_part = remaining[0]
        attr_parts = remaining[1:]

        properties = deal.properties

        if index_part == "*":
            if not properties:
                report.checked_fields += 1
                report.issues.append(FieldIssue(
                    field_path=full_path,
                    issue_type=IssueType.MISSING,
                    message="Nincsenek ingatlanok az ellenőrzéshez.",
                    severity=1,
                ))
                return

            for idx, prop in enumerate(properties):
                concrete_path = full_path.replace("*", str(idx))
                self._check_property_attr(prop, attr_parts, concrete_path, report)
        else:
            try:
                idx = int(index_part)
            except ValueError:
                logger.warning("Érvénytelen ingatlan index: %s", index_part)
                return

            if idx >= len(properties):
                report.checked_fields += 1
                report.issues.append(FieldIssue(
                    field_path=full_path,
                    issue_type=IssueType.MISSING,
                    message=f"Nincs {idx}. indexű ingatlan.",
                    severity=1,
                ))
                return

            self._check_property_attr(properties[idx], attr_parts, full_path, report)

    def _check_property_attr(
        self,
        prop: Property,
        attr_parts: list[str],
        full_path: str,
        report: CompletenessReport,
    ) -> None:
        """Egy konkrét ingatlan attribútumának ellenőrzése."""
        report.checked_fields += 1
        value = self._resolve_nested_attr(prop, attr_parts)
        leaf_name = attr_parts[-1] if attr_parts else ""
        self._check_value(value, full_path, leaf_name, report)

    def _check_value(
        self,
        value: Any,
        field_path: str,
        field_name: str,
        report: CompletenessReport,
    ) -> None:
        """
        Egy konkrét érték ellenőrzése.

        Ellenőrzi: None → MISSING, üres string → EMPTY,
        és opcionálisan gyanús értékeket is.
        """
        if value is None:
            report.issues.append(FieldIssue(
                field_path=field_path,
                issue_type=IssueType.MISSING,
                message=f"A(z) '{field_path}' mező hiányzik.",
                severity=1,
            ))
            return

        # Üres string ellenőrzés
        if isinstance(value, str) and value.strip() == "":
            report.issues.append(FieldIssue(
                field_path=field_path,
                issue_type=IssueType.EMPTY,
                message=f"A(z) '{field_path}' mező üres.",
                severity=1,
            ))
            return

        # Gyanús érték ellenőrzés
        if self.run_suspicious_checks and isinstance(value, str):
            self._check_suspicious(value, field_path, field_name, report)

    def _check_suspicious(
        self,
        value: str,
        field_path: str,
        field_name: str,
        report: CompletenessReport,
    ) -> None:
        """Gyanús értékek ellenőrzése (formátum, hossz)."""
        validator = _SUSPICIOUS_VALIDATORS.get(field_name)
        if validator is None:
            return

        # Min. hossz ellenőrzés
        min_len = validator.get("min_length")
        if min_len is not None and len(value.strip()) < min_len:
            report.issues.append(FieldIssue(
                field_path=field_path,
                issue_type=IssueType.SUSPICIOUS,
                message=validator["message"],
                severity=3,
            ))
            return

        # Regex minta ellenőrzés
        pattern = validator.get("pattern")
        if pattern is not None and not re.match(pattern, value.strip()):
            report.issues.append(FieldIssue(
                field_path=field_path,
                issue_type=IssueType.SUSPICIOUS,
                message=validator["message"],
                severity=3,
            ))

    @staticmethod
    def _resolve_nested_attr(obj: Any, attr_parts: list[str]) -> Any:
        """
        Beágyazott attribútum feloldása pont-szeparált útvonal alapján.

        Pl. ["address", "city"] → obj.address.city
        """
        current = obj
        for part in attr_parts:
            if current is None:
                return None
            current = getattr(current, part, None)
        return current

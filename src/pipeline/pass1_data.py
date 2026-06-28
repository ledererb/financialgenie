"""
Pass 1 – Adatgyűjtés és normalizálás.

Ez a pass egy Salesforce ügylet azonosítóból kanonikus DealData-t
gyárt: lekéri a nyers adatokat a `SalesforceClient`-től (mock vagy
éles), majd a `DataNormalizer`-rel normalizálja.

Lefedettségi metrika: hány kötelező canonical mező került kitöltésre
a teljesség-ellenőrzés során. A `coverage` 0.0-1.0 közötti érték;
0.8 felett az orchestrátor kihagyhatja az opcionális AI sub-pass-okat.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.engine.completeness_checker import CompletenessChecker, CompletenessStatus
from src.integrations.salesforce_client import SalesforceClient
from src.models.canonical_model import DealData
from src.normalizer.data_normalizer import DataNormalizer
from src.pipeline.types import PassResult

logger = logging.getLogger(__name__)


# A "canonical" kötelező mezők, amelyekből a pass1 lefedettségét mérjük.
# Ugyanaz a lista, mint amit a FormFillerPipeline._check_completeness használ
# (src/main.py), hogy a pass1 konzisztens maradjon a meglévő viselkedéssel.
DEFAULT_REQUIRED_FIELDS: list[str] = [
    "loan.loan_amount",
    "loan.loan_term_months",
    "participant.*.name",
    "participant.*.birth_name",
    "participant.*.mother_name",
    "participant.*.birth_date",
    "participant.*.birth_place",
    "participant.*.tax_id",
    "participant.*.phone",
    "property.*.parcel_number",
]


def run_data_pass(
    sf_client: SalesforceClient,
    normalizer: DataNormalizer,
    deal_id: str,
    required_fields: Optional[list[str]] = None,
) -> tuple[Optional[DealData], PassResult]:
    """
    Lefuttatja a 1. pass-t: lekéri + normalizálja az ügyletet.

    Returns:
        (DealData | None, PassResult). A DealData None, ha a lekérés
        vagy normalizálás meghiúsult; ekkor `success=False`.
    """
    issues: list[str] = []
    metrics: dict = {"total_fields": 0, "missing_fields": [], "coverage": 0.0}

    # 1. Adatlekérés
    logger.info("📥 Pass 1 (data): ügylet lekérése: %s", deal_id)
    try:
        raw = sf_client.get_deal(deal_id)
    except Exception as exc:
        msg = f"Salesforce lekérési hiba: {exc}"
        logger.error("   ✗ %s", msg)
        issues.append(msg)
        return None, PassResult(name="data", success=False, metrics=metrics, issues=issues)

    if raw is None:
        msg = f"Ügylet nem található: {deal_id}"
        issues.append(msg)
        logger.warning("   ⚠️ %s", msg)
        return None, PassResult(name="data", success=False, metrics=metrics, issues=issues)

    # 2. Normalizálás
    logger.info("🔄 Pass 1 (data): normalizálás")
    try:
        deal = normalizer.normalize_deal(raw)
    except Exception as exc:
        msg = f"Normalizálási hiba: {exc}"
        issues.append(msg)
        logger.error("   ✗ %s", msg)
        return None, PassResult(name="data", success=False, metrics=metrics, issues=issues)

    logger.info(
        "   ✓ Ügylet: %s | szereplők: %d | ingatlanok: %d",
        deal.deal_id,
        len(deal.active_participants),
        len(deal.properties),
    )

    # 3. Teljesség / lefedettség mérés
    required = required_fields if required_fields is not None else DEFAULT_REQUIRED_FIELDS
    checker = CompletenessChecker(run_suspicious_checks=True)
    report = checker.check(deal, required)

    missing_paths = [i.field_path for i in report.blocking_issues]
    total = max(report.checked_fields, len(required), 1)
    filled = max(total - len(missing_paths), 0)
    coverage = filled / total if total else 0.0

    metrics.update(
        total_fields=total,
        filled_fields=filled,
        missing_fields=missing_paths,
        coverage=round(coverage, 4),
        completeness_status=report.status.value,
        participants=len(deal.active_participants),
        properties=len(deal.properties),
        warnings=[f"{i.field_path}: {i.message}" for i in report.warnings],
    )

    # Nem-blokkoló figyelmeztetéseket issue-ként is feljegyezzük.
    for w in report.warnings:
        issues.append(f"Figyelmeztetés: {w.field_path} – {w.message}")

    success = report.status != CompletenessStatus.INCOMPLETE or report.is_fillable
    # A pass-t "sikeresnek" tekintjük, ha van DealData — a blokkoló hiányok
    # nem akadályozzák meg a későbbi pass-ok futását (a fill csak kevesebb
    # mezőt tölt ki). Az orchestrátor a coverage alapján dönt.
    return deal, PassResult(
        name="data",
        success=True,
        metrics=metrics,
        issues=issues,
    )

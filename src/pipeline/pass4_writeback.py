"""
Pass 4 – Salesforce visszaírás.

A kitöltött PDF-et csatolja az ügylethez, és frissíti az Opportunity
stage-t a Salesforce-ban (vagy a mock store-ban).

Metrika: success (bool), stage, attachment filename.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.pipeline.types import PassResult

if TYPE_CHECKING:
    from src.integrations.salesforce_client import SalesforceClient
    from src.models.canonical_model import DealData

logger = logging.getLogger(__name__)


def run_writeback_pass(
    sf_client: "SalesforceClient",
    deal: "DealData",
    output_path: Path,
    filename_prefix: str = "OTP_nyomtatvany",
) -> PassResult:
    """
    Lefuttatja a 4. pass-t: PDF csatolás + stage frissítés.

    Returns:
        PassResult. A `metrics` tartalmazza a `success`, `stage`,
        `attachment_filename` mezőket.
    """
    issues: list[str] = []
    metrics: dict = {
        "success": False,
        "stage": "",
        "attachment_filename": "",
    }

    logger.info("📤 Pass 4 (writeback): PDF csatolás + stage frissítés")

    try:
        filename = (
            f"{filename_prefix}_{deal.deal_id}_"
            f"{datetime.now():%Y%m%d_%H%M}.pdf"
        )
        sf_client.attach_pdf(deal.deal_id, Path(output_path), filename)
        sf_client.update_stage(deal.deal_id, "Dokumentáció kész – review")
        metrics["attachment_filename"] = filename
        metrics["stage"] = "Dokumentáció kész – review"
        metrics["success"] = True
        logger.info("   ✓ PDF csatolva, stage frissítve")
        return PassResult(
            name="writeback",
            success=True,
            metrics=metrics,
            issues=issues,
        )
    except Exception as exc:
        msg = f"Visszaírási hiba: {exc}"
        issues.append(msg)
        logger.warning("   ⚠️ %s", msg)
        return PassResult(
            name="writeback",
            success=False,
            metrics=metrics,
            issues=issues,
        )

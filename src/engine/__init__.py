"""
FinancialGenie – Engine csomag

Determinisztikus PDF kitöltő motor: mezőkitöltés, szerepkör-logika,
teljességi ellenőrzés.
"""

from src.engine.pdf_filler import (
    AcroFormFiller,
    BaseFiller,
    FillingResult,
    OverlayFiller,
    TextPlacement,
)
from src.engine.role_instance_logic import (
    FormStructure,
    GenerationPlan,
    PageConfig,
    PageScope,
    PageSpec,
    RoleInstancePlanner,
)
from src.engine.completeness_checker import (
    CompletenessChecker,
    CompletenessReport,
    CompletenessStatus,
    FieldIssue,
    IssueType,
)

__all__ = [
    # PDF kitöltő
    "BaseFiller",
    "AcroFormFiller",
    "OverlayFiller",
    "FillingResult",
    "TextPlacement",
    # Szerepkör / példányszám
    "RoleInstancePlanner",
    "GenerationPlan",
    "PageSpec",
    "PageConfig",
    "PageScope",
    "FormStructure",
    # Teljességi ellenőrzés
    "CompletenessChecker",
    "CompletenessReport",
    "CompletenessStatus",
    "FieldIssue",
    "IssueType",
]

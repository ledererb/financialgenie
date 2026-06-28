"""
Pass 2 – Mező-leképezés (Field mapping).

A kanonikus `DealData`-ból és egy `MappingConfig`-ból előállítja a
`tényleges PDF-mező → érték` párokat (`field_data`).

4 sub-pass-t implementál (a DOCUMENT_PROCESSOR_LEARNINGS.md alapján):

  1. Exact / canonical match (0 AI cost). Ez a meglévő
     `_prepare_field_data` logika: canonical_field alapján a
     participant/loan/property adatok kitöltése.
  2. Salesforce label matching (0 AI cost) — opcionális, jelenleg
     stub (a canonical mapping már tartalmazza a label-alapú
     feloldást, ezért nincs külön lépés).
  3. AI classification (DeepSeek) — opcionális, csak ha a
     lefedettség küszöb alatt van. Default off.
  4. Legal checkbox defaults — a leképezetlen checkboxokra a
     LegalClassifier kategória→érték értékeit készíti elő (a PDF-be
     írás a Pass 3-ban történik).

Metrika: hány mapping-mező kapott értéket az összes elérhetőhöz képest.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.ai.legal_classifier import LegalClassifier, extract_unmapped_checkboxes
from src.pipeline.types import PassResult

if TYPE_CHECKING:
    from src.ai.field_recognizer import MappingConfig
    from src.main import FormFillerPipeline
    from src.models.canonical_model import DealData

logger = logging.getLogger(__name__)


def run_mapping_pass(
    pipeline: "FormFillerPipeline",
    deal: "DealData",
    mapping: "MappingConfig",
    coverage_threshold: float = 0.8,
    run_ai_subpass: bool = False,
) -> tuple[dict, PassResult, dict]:
    """
    Lefuttatja a 2. pass-t.

    Args:
        pipeline: Egy FormFillerPipeline példány (a `_prepare_field_data`
            és `_resolve_legal` helper-eket használjuk, hogy a
            meglévő kitöltési logika változatlan maradjon).
        deal: Normalizált DealData (pass 1 kimenete).
        mapping: MappingConfig a template-hez.
        coverage_threshold: Ha a pass 1 lefedettség e felett van, az AI
            sub-pass-t kihagyjuk (jelenleg default off is).
        run_ai_subpass: Explicit kapcsoló az AI sub-pass-ra.

    Returns:
        (field_data: dict[str,str], PassResult, legal_values: dict[str,bool]).
        A `legal_values` dict a Pass 3 számára készül (PDF checkbox-ok
        kategória-alapú default értékei).
    """
    issues: list[str] = []
    metrics: dict = {
        "mapped": 0,
        "unmapped": 0,
        "total_mapping_fields": 0,
        "coverage": 0.0,
        "subpasses_run": [],
        "legal_values_count": 0,
        "ai_subpass": False,
    }

    # --- Sub-pass 1: Exact / canonical match -----------------------------
    logger.info("📋 Pass 2 (mapping): sub-pass 1 – canonical leképezés")
    field_data: dict[str, str] = pipeline._prepare_field_data(deal, mapping)
    metrics["subpasses_run"].append("1_exact_match")

    # --- Sub-pass 2: Salesforce label matching ---------------------------
    # (A canonical mapping már tartalmazza; külön lépés most nincs.)
    metrics["subpasses_run"].append("2_sf_label_match_skipped")

    # --- Sub-pass 3: AI classification (opcionális) ----------------------
    if run_ai_subpass:
        logger.info("🤖 Pass 2 (mapping): sub-pass 3 – AI klasszifikáció (engedélyezve)")
        metrics["ai_subpass"] = True
        metrics["subpasses_run"].append("3_ai_classification")
        # Megjegyzés: a futásidejű AI mező-klasszifikáció jelenleg nincs
        # implementálva (a FieldRecognizer mapping-generálásra szolgál,
        # nem runtime értékfeloldásra). A hook a jövőbeli bővítéshez van itt.
        issues.append("AI sub-pass hook meghívva, de nincs runtime implementáció.")
    else:
        metrics["subpasses_run"].append("3_ai_classification_skipped")

    # --- Sub-pass 4: Legal checkbox defaults -----------------------------
    # Kiszámoljuk a leképezetlen checkboxokra a kategória→értékeket;
    # a PDF-be írást a Pass 3 végzi (fill_legal_declarations_on_pdf).
    legal_values: dict[str, bool] = {}
    try:
        unmapped = extract_unmapped_checkboxes(mapping)
        if unmapped:
            classifier = LegalClassifier()
            classified = classifier.classify_batch(unmapped)
            legal_values = classifier.apply_defaults(classified, deal) or {}
            metrics["legal_unmapped_count"] = len(unmapped)
            metrics["legal_classified_count"] = len(classified or {})
            metrics["legal_values_count"] = len(legal_values)
            logger.info(
                "   ⚖️ Pass 2 (mapping): sub-pass 4 – legal defaults: "
                "%d érték %d leképezetlen checkboxhoz",
                len(legal_values),
                len(unmapped),
            )
        metrics["subpasses_run"].append("4_legal_defaults")
    except Exception as exc:
        msg = f"Legal defaults hiba: {exc}"
        issues.append(msg)
        logger.warning("   ⚠️ %s", msg)
        metrics["subpasses_run"].append("4_legal_defaults_failed")

    # --- Metrika: mapped / unmapped / coverage ---------------------------
    # Csak azokat a mapping mezőket számoljuk, amelyek canonical_field-lel
    # rendelkeznek (a tiszta checkbox/üres mezőket nem — azokat a legal
    # sub-pass kezeli).
    canonical_fields = [f for f in mapping.fields if f.canonical_field]
    total = len(canonical_fields)
    mapped = sum(1 for f in canonical_fields if field_data.get(f.pdf_field_name))
    unmapped = total - mapped
    coverage = (mapped / total) if total else 0.0

    metrics.update(
        mapped=mapped,
        unmapped=unmapped,
        total_mapping_fields=total,
        coverage=round(coverage, 4),
        field_data_count=len(field_data),
    )

    logger.info(
        "   ✓ Pass 2 (mapping): %d/%d mező kitöltve (coverage: %.1f%%)",
        mapped,
        total,
        coverage * 100,
    )

    return (
        field_data,
        PassResult(name="mapping", success=True, metrics=metrics, issues=issues),
        legal_values,
    )

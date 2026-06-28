"""
Pass 3 – PDF kitöltés.

Ez a pass egy template PDF + `field_data` + `MappingConfig` (és a Pass 2
által előkészített `legal_values`) alapján legenerálja a kitöltött PDF-et.

Lépések:
  1. Master PDF detektálás: ha a template egy master (≥ MASTER_PDF_PAGE_COUNT
     oldal), a `DocumentAssembler`-rel daraboljuk a termékek/szereplők/
     ingatlanok alapján (dokumentum-agnosztikus, nem magic-number).
  2. Kitöltés: `AcroFormFiller` vagy `OverlayFiller` a mapping
     `form_type`-ja alapján (a meglévő `_fill_pdf` logika).
  3. Jogi nyilatkozatok: a Pass 2 által előkészített `legal_values`
     dict beírása a PDF AcroForm checkbox-aiba.

Metrika: hány mező íródott be sikeresen a `field_data`-ból + jogi
checkbox-ok száma.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.ai.legal_classifier import fill_legal_declarations_on_pdf
from src.engine.document_assembler import DocumentAssembler, ProductType
from src.pipeline.types import PassResult

if TYPE_CHECKING:
    from src.ai.field_recognizer import MappingConfig
    from src.main import FormFillerPipeline
    from src.models.canonical_model import DealData

logger = logging.getLogger(__name__)


def run_fill_pass(
    pipeline: "FormFillerPipeline",
    deal: "DealData",
    template_pdf: Path,
    field_data: dict,
    mapping: "MappingConfig",
    legal_values: Optional[dict] = None,
    output_dir: Optional[Path] = None,
) -> PassResult:
    """
    Lefuttatja a 3. pass-t: PDF generálás + jogi checkbox-ok.

    Returns:
        PassResult. Az `output_path` a `metrics["output_path"]`-ben van.
    """
    issues: list[str] = []
    metrics: dict = {
        "filled": 0,
        "not_filled": 0,
        "output_path": None,
        "issues": [],
        "master_pdf_assembled": False,
        "legal_modified": 0,
    }

    logger.info("📝 Pass 3 (fill): PDF kitöltés indul")

    actual_template = template_pdf
    try:
        assembler = DocumentAssembler()
        if assembler.is_master_pdf(template_pdf):
            logger.info(
                "   📂 Master PDF észlelve (≥%d oldal) → Document Assembly...",
                assembler.MASTER_PDF_PAGE_COUNT,
            )
            out_dir = output_dir or pipeline.output_dir
            temp_assembled = out_dir / f"assembled_{deal.deal_id}.pdf"

            products_enum = []
            for p in deal.products:
                try:
                    products_enum.append(ProductType(p))
                except ValueError:
                    pass
            if not products_enum:
                products_enum = [ProductType.PIACI_HITEL]

            assembler.assemble(
                master_pdf=template_pdf,
                products=products_enum,
                num_participants=len(deal.active_participants),
                num_properties=len(deal.properties),
                output_path=temp_assembled,
            )
            actual_template = temp_assembled
            metrics["master_pdf_assembled"] = True
            metrics["assembled_path"] = str(temp_assembled)

        output_path = pipeline._fill_pdf(actual_template, deal, field_data, mapping)
        metrics["output_path"] = str(output_path)
        logger.info("   ✓ Kitöltött PDF: %s", output_path)

        # Kitöltött mezők számítása a field_data alapján.
        filled = sum(1 for v in field_data.values() if v)
        metrics["filled"] = filled
        metrics["not_filled"] = max(
            len([f for f in mapping.fields if f.canonical_field]) - filled, 0
        )

        # Jogi checkbox-ok beírása a Pass 2 által előkészített értékekkel.
        legal_modified = 0
        if legal_values:
            try:
                legal_modified = fill_legal_declarations_on_pdf(
                    Path(output_path), legal_values
                )
                if legal_modified:
                    logger.info(
                        "   ⚖️ Jogi nyilatkozat: %d checkbox módosítva",
                        legal_modified,
                    )
            except Exception as exc:
                msg = f"Jogi nyilatkozat hiba: {exc}"
                issues.append(msg)
                logger.warning("   ⚠️ %s", msg)
        metrics["legal_modified"] = legal_modified

        return PassResult(
            name="fill",
            success=True,
            metrics=metrics,
            issues=issues,
        )

    except Exception as exc:
        msg = f"PDF kitöltési hiba: {exc}"
        issues.append(msg)
        logger.error("   ✗ %s", msg)
        metrics["issues"] = [msg]
        return PassResult(
            name="fill",
            success=False,
            metrics=metrics,
            issues=issues,
        )

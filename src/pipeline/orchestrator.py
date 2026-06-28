"""
Pipeline orchestrator – a 4 pass-t összefogó koordinátor.

A `PipelineOrchestrator.run()` láncba kapcsolja a 4 pass-t:

  1. data       – Salesforce lekérés + normalizálás (Pass 1)
  2. mapping    – canonical → PDF mezőértékek + legal defaults (Pass 2)
  3. fill       – PDF generálás AcroForm/overlay + document assembly (Pass 3)
  4. writeback  – Salesforce csatolás + stage (Pass 4)

Lefedettség-alapú AI kapcsolás: ha a Pass 1 `coverage` > küszöb
(default 0.8), a Pass 2 opcionális AI sub-pass-t kihagyja.

Az orchestrátor nem másolja le a `FormFillerPipeline` logikáját –
azt a helper-metódusokon keresztül hívja (`_prepare_field_data`,
`_fill_pdf`), így a meglévő viselkedés garantáltan változatlan.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.integrations.salesforce_client import SalesforceClient
from src.normalizer.data_normalizer import DataNormalizer
from src.pipeline.pass1_data import run_data_pass
from src.pipeline.pass2_mapping import run_mapping_pass
from src.pipeline.pass3_fill import run_fill_pass
from src.pipeline.pass4_writeback import run_writeback_pass
from src.pipeline.types import PassResult, PipelineResult, merge_issues

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """
    A 4-pass pipeline koordinátora.

    Példányosítás:

        orch = PipelineOrchestrator(
            sf_client=sf, normalizer=norm, pipeline=pipeline,
        )
        result = orch.run(
            deal_id="006WB...", template_pdf=Path("..."), mapping=mapping,
        )
    """

    def __init__(
        self,
        sf_client: SalesforceClient,
        normalizer: DataNormalizer,
        pipeline,
        coverage_threshold: float = 0.8,
        force_ai_subpass: bool = False,
    ):
        """
        Args:
            sf_client: Salesforce (vagy mock) kliens.
            normalizer: DataNormalizer példány.
            pipeline: FormFillerPipeline példány (a kitöltő helper-ek
                újrafelhasználásához).
            coverage_threshold: Pass 1 lefedettség felett az AI sub-pass
                kihagyható.
            force_ai_subpass: Ha True, AI sub-pass mindig fut (teszteléshez).
        """
        self.sf_client = sf_client
        self.normalizer = normalizer
        self.pipeline = pipeline
        self.coverage_threshold = coverage_threshold
        self.force_ai_subpass = force_ai_subpass

    def run(
        self,
        deal_id: str,
        template_pdf: Path,
        mapping,
        skip_writeback: bool = False,
    ) -> PipelineResult:
        """
        Lefuttatja a teljes 4-pass pipeline-t egy ügyletre.

        Args:
            deal_id: Salesforce ügylet azonosító.
            template_pdf: Kitöltendő template PDF útvonala.
            mapping: MappingConfig.
            skip_writeback: Ha True, a Pass 4-et átugorja (teszteléshez).

        Returns:
            PipelineResult (tartalmazza mind a 4 pass eredményét).
        """
        timestamp = datetime.now().isoformat()
        passes: dict[str, PassResult] = {}
        all_issues: list[str] = []

        logger.info("=" * 60)
        logger.info("🚀 Pipeline orchestrator: deal=%s, template=%s", deal_id, template_pdf.name)
        logger.info("=" * 60)

        # --- Pass 1: data -------------------------------------------------
        deal, data_result = run_data_pass(self.sf_client, self.normalizer, deal_id)
        passes["data"] = data_result
        all_issues.extend(data_result.issues)
        if deal is None:
            return self._finalize(
                deal_id, passes, all_issues, None, 0.0, timestamp
            )

        # --- Pass 2: mapping ----------------------------------------------
        # AI sub-pass csak ha a coverage alacsony VAGY force.
        run_ai = self.force_ai_subpass or data_result.coverage < self.coverage_threshold
        if data_result.coverage >= self.coverage_threshold:
            logger.info(
                "📊 Pass 1 coverage %.1f%% ≥ küszöb %.1f%% → AI sub-pass kihagyása",
                data_result.coverage * 100,
                self.coverage_threshold * 100,
            )

        field_data, mapping_result, legal_values = run_mapping_pass(
            self.pipeline, deal, mapping,
            coverage_threshold=self.coverage_threshold,
            run_ai_subpass=run_ai,
        )
        passes["mapping"] = mapping_result
        all_issues.extend(mapping_result.issues)

        # --- Pass 3: fill -------------------------------------------------
        fill_result = run_fill_pass(
            self.pipeline,
            deal,
            template_pdf,
            field_data,
            mapping,
            legal_values=legal_values,
            output_dir=self.pipeline.output_dir,
        )
        passes["fill"] = fill_result
        all_issues.extend(fill_result.issues)

        output_path = None
        if fill_result.success and fill_result.metrics.get("output_path"):
            output_path = Path(fill_result.metrics["output_path"])

        # --- Pass 4: writeback --------------------------------------------
        if skip_writeback or output_path is None:
            if output_path is None:
                logger.warning("⚠️ Pass 4 (writeback) átugorva: nincs output PDF")
            wb_result = PassResult(
                name="writeback",
                success=bool(skip_writeback),
                metrics={"success": bool(skip_writeback), "stage": "", "skipped": True},
                issues=[] if skip_writeback else ["Nincs output PDF"],
            )
            passes["writeback"] = wb_result
            all_issues.extend(wb_result.issues)
        else:
            wb_result = run_writeback_pass(self.sf_client, deal, output_path)
            passes["writeback"] = wb_result
            all_issues.extend(wb_result.issues)

        # --- Összegzés ----------------------------------------------------
        overall_coverage = mapping_result.coverage
        success = all(
            p.success for k, p in passes.items() if k != "writeback"
        ) and (passes.get("writeback") and passes["writeback"].success or skip_writeback)

        return self._finalize(
            deal_id, passes, all_issues, output_path, overall_coverage, timestamp
        )

    @staticmethod
    def _finalize(
        deal_id: str,
        passes: dict[str, PassResult],
        issues: list[str],
        output_path: Optional[Path],
        coverage: float,
        timestamp: str,
    ) -> PipelineResult:
        success = (
            passes.get("data") is not None
            and passes["data"].success
            and passes.get("mapping") is not None
            and passes["mapping"].success
            and passes.get("fill") is not None
            and passes["fill"].success
            and (
                (passes.get("writeback") is not None and passes["writeback"].success)
                or passes.get("writeback", PassResult("x", True)).metrics.get("skipped")
            )
        )
        merged = list(issues) + merge_issues(passes)
        return PipelineResult(
            deal_id=deal_id,
            success=success,
            output_path=output_path,
            passes=passes,
            issues=merged,
            overall_coverage=coverage,
            timestamp=timestamp,
        )

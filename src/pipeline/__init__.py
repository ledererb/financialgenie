"""
FinancialGenie – modular 4-pass pipeline.

Ez a csomag a korábbi monolitikus `FormFillerPipeline.run_for_deal()`
metódus modularizált változatát tartalmazza. A 4 pass:

  - pass1_data      : Salesforce lekérés + normalizálás
  - pass2_mapping   : canonical → PDF mezőértékek (4 sub-pass)
  - pass3_fill      : PDF generálás (AcroForm / overlay / master assembly)
  - pass4_writeback : Salesforce csatolás + stage

A `PipelineOrchestrator` fűzi össze a 4 pass-t; a `run()` metódus
egy `PipelineResult`-ot ad vissza, amely tartalmazza az összes pass
eredményét és az általános lefedettséget.
"""

from src.pipeline.orchestrator import PipelineOrchestrator
from src.pipeline.pass1_data import run_data_pass
from src.pipeline.pass2_mapping import run_mapping_pass
from src.pipeline.pass3_fill import run_fill_pass
from src.pipeline.pass4_writeback import run_writeback_pass
from src.pipeline.types import PassResult, PipelineResult, merge_issues

__all__ = [
    "PipelineOrchestrator",
    "PipelineResult",
    "PassResult",
    "merge_issues",
    "run_data_pass",
    "run_mapping_pass",
    "run_fill_pass",
    "run_writeback_pass",
]

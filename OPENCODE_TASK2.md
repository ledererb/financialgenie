# OpenCode Task: Document Processor Modularization + Coverage Tracking

## Context
FinancialGenie PoC — OTP bank form automation. The current pipeline works (7/7 PDFs, 0 errors), but `main.py` is monolithic (~1200 lines). Per `DOCUMENT_PROCESSOR_LEARNINGS.md`, we need to:

1. Refactor the pipeline into modular passes (4-pass design)
2. Add per-pass coverage tracking with metadata
3. Create `scripts/analyze_pdf.py` — generic PDF analyzer
4. Update `ARCHITECTURE.md`

## Codebase Structure
```
/workspace/financialgenie/
├── src/
│   ├── main.py              # ~1200 line monolithic pipeline (REFACTOR THIS)
│   ├── models/
│   │   └── canonical_model.py  # DealData, Participant, Property, LoanDetails
│   ├── normalizer/
│   │   └── data_normalizer.py
│   ├── engine/
│   │   ├── pdf_filler.py       # AcroFormFiller, OverlayFiller
│   │   ├── document_assembler.py
│   │   ├── completeness_checker.py
│   │   └── role_instance_logic.py
│   ├── integrations/
│   │   └── salesforce_client.py
│   ├── ai/
│   │   ├── field_recognizer.py  # DeepSeek FieldRecognizer
│   │   └── legal_classifier.py  # Legal checkbox classifier
│   └── mapping/                 # JSON mapping files
├── scripts/
│   ├── analyze_flat_pdf.py      # Existing overlay analyzer
│   ├── map_ertekbecsles_from_salesforce.py
│   └── recognize_ertekbecsles.py
├── DOCUMENT_PROCESSOR_LEARNINGS.md
└── config/
    └── .env (SF_USERNAME, SF_PASSWORD, SF_SECURITY_TOKEN, SF_DOMAIN=test, DEEPSEEK_API_KEY)
```

## Current Architecture (monolithic)
`main.py` has class `FormFillerPipeline` with method `run_for_deal()` that does:
1. Data fetch (Salesforce)
2. Normalization
3. Completeness check
4. Field data preparation
5. PDF filling (AcroForm or overlay)
6. Legal declarations checkbox fill
7. Writeback to Salesforce

## Task 1: Create modular pipeline (`src/pipeline/`)

Create `src/pipeline/` directory with separate pass modules:

**`src/pipeline/__init__.py`** — exports

**`src/pipeline/pass1_data.py`** — Data fetch + normalization pass
- Takes `deal_id`, returns normalized `DealData`
- Reports: `{total_fields: N, missing_fields: [...], coverage: 0.0-1.0}`
- Metric: how many canonical fields got populated (vs canonical model total)

**`src/pipeline/pass2_mapping.py`** — Field mapping pass
- Takes `DealData` + `MappingConfig`, returns `field_data: dict[str, str]`
- Implements 4 sub-passes (from DOCUMENT_PROCESSOR_LEARNINGS.md):
  1. Exact match (0 AI cost)
  2. Salesforce label matching (0 AI cost) — optional
  3. AI classification — optional, if below threshold
  4. Legal checkbox defaults — optional
- Reports: `{mapped: N, unmapped: N, coverage: 0.0-1.0}`

**`src/pipeline/pass3_fill.py`** — PDF filling pass
- Takes template PDF + `field_data` + `MappingConfig`
- Calls `AcroFormFiller` or `OverlayFiller` as appropriate
- Handles Document Assembly for master PDFs
- Reports: `{filled: N, not_filled: N, output_path: str, issues: [str]}`

**`src/pipeline/pass4_writeback.py`** — Salesforce writeback
- Attaches filled PDF to Salesforce
- Updates stage
- Reports: `{success: bool, stage: str}`

**`src/pipeline/orchestrator.py`** — Pipeline orchestrator
- `class PipelineOrchestrator` that chains the 4 passes
- Coverage threshold logic: if pass1 coverage > 80%, skip AI sub-passes in pass2
- `run(deal_id, template_pdf, mapping_config) -> dict` returns unified result
- Logs coverage at each stage

**Results format** (unified, used throughout):
```python
@dataclass
class PipelineResult:
    deal_id: str
    success: bool
    output_path: Optional[Path]
    passes: dict[str, PassResult]  # "data" | "mapping" | "fill" | "writeback"
    issues: list[str]
    overall_coverage: float  # 0.0-1.0
    timestamp: str

@dataclass
class PassResult:
    name: str
    success: bool
    metrics: dict  # pass-specific metrics
    issues: list[str]
```

## Task 2: Refactor `main.py`

After creating `src/pipeline/`:
- `main.py` becomes thin CLI wrapper
- CLI stays the same (`--all`, `--salesforce`, `--deal`, `--scenario`, `--recognize`)
- `main()` calls `PipelineOrchestrator.run()` instead of `FormFillerPipeline.run_for_deal()`
- Keep backward compatibility: `_run_all_mappings()` still works but uses orchestrator
- Keep `_load_mapping_config()`, `_overlay_to_mapping_config()`, `_resolve_mapping_for_template()` in main.py or move to a shared utils module
- Remove old `FormFillerPipeline` class or keep as thin delegator

## Task 3: Create `scripts/analyze_pdf.py`

Generic PDF analysis script (from DOCUMENT_PROCESSOR_LEARNINGS.md):

```
Usage: python scripts/analyze_pdf.py <path/to/pdf>
```

Outputs structured JSON report:
```json
{
  "filename": "elozetes_ertekbecsles.pdf",
  "page_count": 18,
  "has_acroform": true,
  "acroform_fields": 1029,
  "field_names": ["Check Box 1", "Text Field 1", ...],
  "field_types": {"Check Box 1": "checkbox", "Text Field 1": "text"},
  "name_quality": "generic" | "descriptive" | "mixed",
  "flat_placeholders": {
    "underlines": 0,
    "checkbox_boxes": 0,
    "dots": 0
  },
  "sample_labels_per_page": [
    {"page": 1, "labels": ["Ingatlan címe", "Helyrajzi szám", ...]},
    ...
  ]
}
```

Use PyMuPDF (fitz) for:
- AcroForm field detection (widget annotations)
- Field name + type extraction
- Page-by-page text block extraction (for labels near fields)
- Flat PDF placeholder detection (borrow from `scripts/analyze_flat_pdf.py`)

## Task 4: Update `ARCHITECTURE.md` or create if missing

Document the 4-pass pipeline architecture. Structure:

```markdown
# Architecture — FinancialGenie Document Processor

## Overview
(Purpose: bank-agnostic document filling)

## Pipeline Architecture (4-pass)

### Pass 1: Data Acquisition & Normalization
- Input: deal_id + Salesforce connection
- Output: canonical DealData
- Coverage metric: % of canonical model populated

### Pass 2: Field Mapping (4 sub-passes)
1. Exact name match — 0 AI cost
2. Salesforce label matching — 0 AI cost
3. AI classification (DeepSeek) — optional
4. Legal checkbox defaults — last resort
- Coverage metric: % of mapping fields resolved

### Pass 3: PDF Filling
- Input: field_data + template + mapping
- Output: filled PDF
- Supports: AcroForm, overlay, document assembly

### Pass 4: Salesforce Writeback
- Attaches filled PDF
- Updates opportunity stage

## Coverage Tracking
- Each pass reports {mapped, total, coverage}
- Orchestrator stops AI passes if coverage > 80%

## Key Design Decisions
- AI is NOT the primary mapping engine — it fills the gap
- Salesforce is the single source of truth
- Determinisztikus before AI (pass 1-2 before 3-4)
- Document-agnostic naming convention
```

## Implementation Rules

1. **ALL existing functionality must be preserved.** The `--all` batch mode, `--salesforce`, `--recognize`, overlay support, legal classifier — everything must still work exactly as before.
2. **Backward compatibility:** old `FormFillerPipeline` class should still work if someone imports it (keep it as thin delegator to new orchestrator).
3. **Test after each file change:** run `python -m src.main --all --salesforce --deal 006WB00000LwTDgYAN` to verify nothing broke.
4. **Don't change the existing output filenames.** The `output/filled_*.pdf` naming convention stays.
5. **Don't remove any existing files.** Add new files, modify existing ones carefully.
6. **Git commit after finishing each task** (or after each file if changing more than 3 files).
7. **Use DeepSeek V4 Flash** for any AI calls — model string: `deepseek-v4-flash`
8. **`response_format: {"type": "json_object"}`** for JSON-only responses
9. **`"thinking": {"type": "disabled"}`** in request body for non-reasoning tasks

## Before Starting

Read these files first to understand the current code:
- `/workspace/financialgenie/src/main.py`
- `/workspace/financialgenie/src/engine/pdf_filler.py`
- `/workspace/financialgenie/src/engine/completeness_checker.py`
- `/workspace/financialgenie/src/models/canonical_model.py`
- `/workspace/financialgenie/DOCUMENT_PROCESSOR_LEARNINGS.md`

## Git

Work on the `main` branch. Commit after each meaningful milestone with a descriptive Hungarian commit message.

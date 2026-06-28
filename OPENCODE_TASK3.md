# OpenCode Task: Finish pipeline modularization (remaining work)

## Context
FinancialGenie PoC. The `src/pipeline/` module was already committed (`8b364bd`). Three things remain:

## 1. Create `scripts/analyze_pdf.py`

A generic PDF analyzer (from DOCUMENT_PROCESSOR_LEARNINGS.md).

```bash
Usage: python scripts/analyze_pdf.py <path/to/pdf>
```

Outputs structured JSON to stdout:
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
  ],
  "recommended_mode": "acroform" | "overlay" | "analyze_first",
  "guess": "A PDF valószínűleg egy OTP-s nyomtatvány, 18 oldal, 1029 generikus AcroForm mezővel."
}
```

Use PyMuPDF (fitz) for:
- AcroForm field detection (widget annotations) — `doc[page].widgets()`
- Field name + type extraction
- Page-by-page text block extraction (`doc[page].get_text("blocks")`)
- Flat PDF placeholder detection (underlines, □ boxes, dots)

Import logic from `scripts/analyze_flat_pdf.py` for flat PDF detection.

## 2. Update `ARCHITECTURE.md`

It currently describes the old monolithic pipeline. Update to describe the new `src/pipeline/` 4-pass architecture.

**Full replacement content:**

```markdown
# FinancialGenie Architecture

## Overview
Automated bank form filling system. End-to-end: Salesforce sandbox → normalized data → PDF fill → upload back.

## Pipeline Architecture (4-pass)

### Pass 1: Data Acquisition & Normalization (`src/pipeline/pass1_data.py`)
- Input: deal_id + Salesforce connection
- Output: canonical DealData
- Coverage metric: % of canonical model populated

### Pass 2: Field Mapping (`src/pipeline/pass2_mapping.py`)
- 4 sub-passes:
  1. Exact name match — 0 AI cost
  2. Salesforce label matching — 0 AI cost (built into canonical model)
  3. AI classification (DeepSeek) — optional, threshold-gated
  4. Legal checkbox defaults — last resort
- Coverage metric: % of mapping fields resolved

### Pass 3: PDF Filling (`src/pipeline/pass3_fill.py`)
- Input: field_data + template + mapping
- Output: filled PDF
- Supports: AcroForm, overlay, document assembly, legal checkbox fill

### Pass 4: Salesforce Writeback (`src/pipeline/pass4_writeback.py`)
- Attaches filled PDF to Opportunity
- Updates stage to "Dokumentáció kész – review"

## Coverage Tracking
- Each pass reports {mapped, total, coverage}
- Orchestrator (`src/pipeline/orchestrator.py`) stops AI sub-passes if coverage > 80%

## Key Design Decisions
- **AI is NOT the primary mapping engine** — it fills the gap (deterministic before AI)
- **Salesforce is the single source of truth**
- **Document-agnostic**: works on any PDF with AcroForm or overlay support
- **Separate AI and deterministic layers**

## Components

### Data Layer (`src/integrations/`)
- `salesforce_client.py` — Salesforce sandbox API (simple-salesforce)

### Normalization (`src/normalizer/`)
- `data_normalizer.py` — maps Salesforce field names to canonical model

### AI Layer (`src/ai/`)
- `field_recognizer.py` — DeepSeek V4 Flash PDF field recognition
- `legal_classifier.py` — Legal declarations classification (hybrid AI + config)

### Canonical Model (`src/models/`)
- `canonical_model.py` — Pydantic models (DealData, LoanDetails, Participant, Property, Address)

### Engine (`src/engine/`)
- `pdf_filler.py` — AcroForm + overlay PDF filling
- `document_assembler.py` — multi-page document assembly
- `completeness_checker.py` — field-level validation
- `role_instance_logic.py` — role-based field repetition

### Pipeline (`src/pipeline/`)
- `orchestrator.py` — PipelineOrchestrator (chains 4 passes)
- `pass1_data.py` — Data + normalization
- `pass2_mapping.py` — Field mapping (4 sub-passes)
- `pass3_fill.py` — PDF generation
- `pass4_writeback.py` — Salesforce upload

### Scripts (`scripts/`)
- `analyze_pdf.py` — Generic PDF analyzer (field structure, placeholder detection)
- `analyze_flat_pdf.py` — Overlay coordinate detection for flat PDFs
- `map_ertekbecsles_from_salesforce.py` — Salesforce label matching for generic fields
- `recognize_ertekbecsles.py` — DeepSeek page-by-page classification

## AI Models
All AI: **DeepSeek V4 Flash** (`deepseek-v4-flash`)
- Endpoint: `https://api.deepseek.com/v1/chat/completions`
- Temperature: 0.0 (deterministic)
- JSON mode: `response_format: {"type": "json_object"}`
- Thinking mode disabled: `"thinking": {"type": "disabled"}`
```

## 3. Fix `src/main.py` import

The import on line 55 is unused:
```python
from src.pipeline import PipelineOrchestrator, PipelineResult
```
Keep the import (it's fine to have it), but add a TODO comment above it. No other changes needed to main.py since the old FormFillerPipeline still works as the main entry point. The pipeline module is available for programmatic use starting from imports.

## Implementation Rules

1. Test `scripts/analyze_pdf.py` on one of the actual PDFs in the project root:
   ```bash
   cd /workspace/financialgenie
   python scripts/analyze_pdf.py elozetes_ertekbecsles.pdf
   python scripts/analyze_pdf.py partner_nyilatkozat.pdf
   ```
2. Verify both versions produce valid JSON output.
3. Commit and push after finishing all items.
4. Don't change any other files.

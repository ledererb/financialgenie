# FinancialGenie Architecture

## Overview
Automated, bank-agnostic form filling system. End-to-end: Salesforce (or mock
data) → normalized canonical data → Genie canonical resolution → PDF fill.
Upload back to Salesforce only on explicit request.

## Components

### Canonical Core (src/canonical/) — added by the 2026-10-29 Genie cutover
- `fields.json` — curated, versioned canonical field catalog (253 base fields,
  5 roles: borrower, co_borrower, guarantor, mortgagor, usufructuary)
- `registry.py` — catalog load/validation (the single source shared by Studio,
  backend validation, AI prompts and the pipeline)
- `paths.py` — canonical path parser (`deal.*`, `account.*`,
  `participant.<role>[N].*`, `property[N].*`, `income[N].*`, `existing_loan[N].*`)
- `resolver.py` — value resolution from `DealData` + explicit `(role, index)`
  document-instance context
- Active mappings (`src/mapping/*.json`) contain **Genie paths only**; legacy
  `Contact.*`/`Lead.*`/`Opportunity.*`/`Account.*` paths are rejected
  (422 on save, runtime error on use).

### Salesforce Integration (src/integrations/)
- `salesforce_client.py` — mock-mode (samples/dummy_data) or live
  simple-salesforce client; describe-filtered SOQL; role resolution from
  `OpportunityContactRole` (+ lookup/Account-pool fallback), `Property__c`
  properties, `Jovedelem__c`/`Meglevo_hitel__c` related rows
  (`ORDER BY Sorszam__c, CreatedDate, Id`, batched by Contact ID)
- `salesforce_schema.py` — strict metadata snapshot parser; live SOQL field
  lists are filtered through the snapshot
- `salesforce_field_map.json` — generated Salesforce API name → canonical
  target map (Account/Contact/Opportunity/Property__c/Jovedelem__c/Meglevo_hitel__c)
- Live mode **fails closed**: no schema snapshot (`SF_SCHEMA_PATH` or
  `config/sf_schema_snapshot.json`) → RuntimeError; missing related objects →
  one actionable warning + empty rows, never fabricated data. Snapshots come
  from the describe-only `scripts/sync_sf_schema.py` (never queries records).

### Normalization (src/normalizer/)
- `data_normalizer.py` — populates the canonical model via the field map;
  preserves roles, contact/property IDs, income/existing-loan rows,
  `None` vs explicit `False`, and separate payment semantics.

### AI Layer (src/ai/)
- `field_recognizer.py` — PDF field recognition via Anthropic Claude
  (`claude-sonnet-4-6`, `ANTHROPIC_API_KEY`); AcroForm + visual/flat mode;
  deterministic offline post-processing; only Genie registry paths are emitted
- `legal_classifier.py` — legal declaration **categorization only** (DeepSeek
  is optional, for category labels); it never fills values. Filling is limited
  to reviewed role/signature points and explicit CRM consent canonical paths;
  everything else is listed as a manual decision (`manual_declarations`).

### Canonical Model (src/models/)
- `canonical_model.py` — Pydantic models (DealData, LoanDetails, Participant
  with consents/incomes/existing_loans, Property, Address)

### Engine (src/engine/)
- `pdf_filler.py` — AcroForm (pikepdf/PyMuPDF) + overlay (ReportLab) filling,
  Hungarian glyph appearances, checkbox/radio `/V`↔`/AS` sync, comb dropdown guard
- `document_assembler.py` — master PDF splitting / multi-page assembly
- `fill_rules.py` — fill rules incl. rule-2 points with indexed role addressing

### Pipeline (src/main.py — FormFillerPipeline)
1. Data fetch (live Salesforce or mock; legacy canonical paths raise)
2. Normalization
3. Completeness check (explicit Genie mandatory paths)
4. Field data assembly — every consumer resolves through `CanonicalResolver`
5. PDF fill (+ master-PDF assembly, runtime digit-box/date-fragment handling)
6. Legal declarations: safe-defaults boundary; unresolved → manual list
7. Upload back to Salesforce only when explicitly requested (never in mock)

## Key Design Decisions
- **Document-agnostic**: recognition works on any PDF with AcroForm or flat layout
- **Genie canonical keys, not Salesforce names**: bank forms map to stable
  canonical paths; the Salesforce field map lives in the integration layer
- **Separate AI and deterministic layers**: AI only recognizes/categorizes;
  filling is deterministic and evidence-based
- **Fail-closed live mode**: schema snapshot + explicit org ID required;
  no silent mock fallback, no dummy data (backend answers 503 when unconfigured)
- **Legal safety by default**: no guessed consent/declaration values; manual
  decisions are surfaced, not silently ticked

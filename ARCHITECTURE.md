# FinancialGenie Architecture

## Overview
Automated bank form filling system. End-to-end: Salesforce sandbox → normalized data → PDF fill → upload back.

## Components

### Data Layer (src/integrations/)
- `salesforce_client.py` — Salesforce sandbox API (simple-salesforce)
- SOQL queries for Opportunity, Contact, Property__c

### Normalization (src/normalizer/)
- `data_normalizer.py` — maps Salesforce field names → canonical model

### AI Layer (src/ai/)
- `field_recognizer.py` — PDF field recognition via DeepSeek V4 Flash
  - Processes AcroForm field labels → matches to canonical fields
  - Batch mode: 50 fields/call, JSON output
  - Fallback: persistence of previous mapping
- `legal_classifier.py` — Legal declarations classification
  - Hybrid: AI (DeepSeek V4 Flash) + config (legal_defaults.json)
  - Batch mode: 40 fields/call, JSON output
  - Categories: consent, data_sharing, bank_info, decline, property_state, product_choice, conditional, unknown
  - Fallback: rule-based keyword matching
  - Config: category → default value mapping in legal_defaults.json

### Canonical Model (src/models/)
- `canonical_model.py` — Pydantic models (DealData, LoanDetails, Participant, Property, Address)

### Engine (src/engine/)
- `pdf_filler.py` — AcroForm + overlay PDF filling
- `document_assembler.py` — multi-page document assembly
- `role_instance_logic.py` — role-based field repetition

### Pipeline (src/main.py)
1. Data fetch (Salesforce or mock)
2. Normalization
3. Completeness check
4. Field data assembly
5. PDF fill (canonical mapping)
6. Legal declarations fill (AI + config hybrid)
7. Upload back to Salesforce (mock)

## AI Models
All AI: **DeepSeek V4 Flash** (`deepseek-v4-flash`)
- Endpoint: `https://api.deepseek.com/v1/chat/completions`
- Auth: `DEEPSEEK_API_KEY` env var
- Temperature: 0.0 (deterministic)
- JSON mode enabled for structured output

## Key Design Decisions
- **Document-agnostic**: field recognition works on any PDF with AcroForm
- **Separate AI and deterministic layers**: Claude/DeepSeek only for field recognition, not filling
- **Legal declarations hybrid**: AI categorizes → config decides → rule-based fallback

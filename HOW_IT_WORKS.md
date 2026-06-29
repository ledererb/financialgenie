# FinancialGenie — How It Works

> Definitive technical reference for the FinancialGenie codebase.
>
> FinancialGenie is a **Proof of Concept** built by *Think AI Kft.* that
automates the filling of OTP Bank mortgage/form documents from Salesforce deal
data. It is a *deterministic* filler (no AI in the hot path) paired with an
*AI-assisted onboarding* layer (Claude) used only to recognize the fields of a
brand-new, empty form and produce a mapping configuration.

---

## 1. High-level purpose

Hungarian banks (here: OTP Bank Nyrt.) require customers and advisors to fill
out large, repetitive PDF document packages for mortgage products — application
forms, partner declarations, valuation orders, income-verification consent
forms, etc. These are 1–97-page PDFs containing hundreds to thousands of fields
that today are filled in by hand.

FinancialGenie solves this by:

1. **Reading deal data** from Salesforce (Opportunity + Contacts + Properties).
2. **Normalizing** it into a single bank-agnostic *canonical model*.
3. **Mapping** the canonical model onto the concrete PDF form fields of each
   OTP document via a JSON mapping config.
4. **Deterministically filling** the PDF (AcroForm value-writing *or*
   coordinate-based overlay text).
5. **Checking completeness** (missing/empty/suspicious fields).
6. **Writing back** the produced PDF into Salesforce as a file attachment and
   advancing the Opportunity stage ("Dokumentáció kész – review").

AI (Anthropic Claude) is used **only on the onboarding side**: when a new empty
PDF is introduced, the AI recognizes its fillable fields and proposes a mapping
to the canonical model. That mapping, once a human approves it, is reused by the
deterministic engine. This keeps the data path auditable, cheap, and
GDPR-friendly — no customer PII ever touches the model.

---

## 2. Architecture overview

```
financialgenie/
├── README.md
├── HOW_IT_WORKS.md                              # this file
├── CLIENT_OVERVIEW_HU.md                        # client-facing doc (Hungarian)
├── requirements.txt
├── config/
│   ├── __init__.py
│   ├── settings.py                              # env loading, paths, API keys
│   └── .env.example
├── otp/                                         # real OTP PDF source documents
│   ├── Piaci hitel/                             # market-rate mortgage package
│   ├── Otthon Start/                            # Otthon Start product package
│   ├── Szabadfelhasználású hitel/               # free-purpose loan package
│   └── Előzetes értékbecslé megrendelés/        # preliminary valuation order
├── samples/
│   ├── acroform_sample.pdf
│   ├── flat_sample.pdf
│   └── dummy_data/*.json
├── scripts/
│   ├── generate_sample_pdfs.py
│   ├── generate_dummy_data.py
│   └── test_otp_fill.py
├── src/
│   ├── main.py                                  # FormFillerPipeline + CLI
│   ├── models/
│   │   └── canonical_model.py                   # Pydantic canonical schema
│   ├── normalizer/
│   │   └── data_normalizer.py                   # SF/raw → canonical
│   ├── integrations/
│   │   └── salesforce_client.py                 # mock + real Salesforce
│   ├── engine/
│   │   ├── pdf_filler.py                        # AcroFormFiller + OverlayFiller
│   │   ├── document_assembler.py                # master-PDF slicer
│   │   ├── completeness_checker.py              # validation/suspicious checks
│   │   └── role_instance_logic.py               # per-role page planning
│   ├── ai/
│   │   └── field_recognizer.py                  # Claude field recognition + CANONICAL_FIELDS
│   └── mapping/                                 # JSON mapping configs (16 documents)
├── backend/
│   ├── server.py                                # FastAPI mapping editor API
│   ├── mapping_service.py                       # mapping CRUD logic
│   └── config.py                                # backend configuration
├── frontend/                                    # React + TypeScript + Vite
│   └── src/
│       ├── App.tsx
│       ├── store.ts                             # Zustand state management
│       ├── api/client.ts                        # API client
│       ├── components/
│       │   ├── PageEditor.tsx                   # page-level field editor
│       │   └── ReviewDashboard.tsx              # mapping review/approval
│       └── types/index.ts
└── tests/
    ├── test_canonical_model.py
    ├── test_normalizer.py
    ├── test_document_assembler.py
    └── test_integration.py
```

### Component roles

| Component | File | Responsibility |
|---|---|---|
| **Pipeline orchestrator** | `src/main.py` | `FormFillerPipeline.run_for_deal()` — wires SF → normalizer → checker → field-data prep → assembly → fill → write-back. |
| **Canonical model** | `src/models/canonical_model.py` | Bank-independent Pydantic v2 schema: `DealData`, `Participant`, `Property`, `LoanDetails`, `Address`. |
| **Normalizer** | `src/normalizer/data_normalizer.py` | `DataNormalizer.normalize_deal()` — accepts both canonical JSON (dummy data) and Salesforce-shaped dicts. |
| **Salesforce client** | `src/integrations/salesforce_client.py` | `SalesforceClient` — dual-mode: mock (JSON files) or real (simple-salesforce). |
| **PDF filler engine** | `src/engine/pdf_filler.py` | `AcroFormFiller` (pikepdf) + `OverlayFiller` (reportlab + PyMuPDF). |
| **Document assembler** | `src/engine/document_assembler.py` | `DocumentAssembler` slices the 97-page OTP master PDF by product/participant/property. |
| **Completeness checker** | `src/engine/completeness_checker.py` | Validates `DealData` against required SF field paths. Reports MISSING/EMPTY/SUSPICIOUS. |
| **Role/instance planner** | `src/engine/role_instance_logic.py` | `RoleInstancePlanner.plan()` builds `GenerationPlan` of `PageSpec`s by `PageScope`. |
| **AI field recognizer** | `src/ai/field_recognizer.py` | `FieldRecognizer` + `MappingConfig` + `CANONICAL_FIELDS` (236 SF fields). |
| **Mapping configs** | `src/mapping/*.json` | 16 JSON mapping configs, one per document. |
| **Mapping Editor API** | `backend/server.py` | FastAPI server with 25+ endpoints for the visual mapping editor. |
| **Mapping Editor UI** | `frontend/` | React/TypeScript/Vite SPA for visual PDF field mapping. |

---

## 3. Data flow

```
┌────────────┐   1. get_deal(deal_id)        ┌──────────────────┐
│ Salesforce  │ ────────────────────────────▶ │ SalesforceClient │
│ (or mock)   │                               │  (mock or real)  │
└─────────────┘                               └────────┬─────────┘
                                                       │ raw dict
                                                       ▼
                                ┌──────────────────────────────┐
                                │ 2. DataNormalizer.normalize   │
                                │     SF role/type maps         │
                                │     date / int parsing        │
                                └──────────────┬───────────────┘
                                               │ DealData (canonical)
                                               ▼
                                ┌──────────────────────────────┐
                                │ 3. CompletenessChecker.check  │
                                │     (SF field-based required) │
                                └──────────────┬───────────────┘
                                               │ CompletenessReport
                                               ▼
                                ┌──────────────────────────────┐
                                │ 4. _prepare_field_data        │
                                │    Contact.* ← participant    │
                                │    Opportunity.* ← loan       │
                                │    Lead.* ← property          │
                                │    → mapping → field_data     │
                                └──────────────┬───────────────┘
                                               │ dict[pdf_field → value]
                                               ▼
                                ┌──────────────────────────────┐
                                │ 5. DocumentAssembler          │
                                │   if is_master_pdf (≥97 pp):  │
                                │   slice → assembled.pdf       │
                                └──────────────┬───────────────┘
                                               ▼
                                ┌──────────────────────────────┐
                                │ 6. AcroFormFiller OR          │
                                │    OverlayFiller              │
                                │    → filled_<deal>_<ts>.pdf   │
                                └──────────────┬───────────────┘
                                               ▼
                                ┌──────────────────────────────┐
                                │ 7. SalesforceClient.attach    │
                                │    _pdf + update_stage        │
                                └──────────────────────────────┘
```

---

## 4. Salesforce-driven canonical field model

> **Salesforce is the single source of truth.**

The canonical field model is derived **directly** from the live Salesforce sandbox schema. Every canonical field name uses the `Object.FieldName` format — there is no intermediate translation layer.

### 4.1 SF Object coverage

| SF Object | Field Count | Purpose | Example Fields |
|---|---:|---|---|
| **Contact** | 142 | Person data (borrower/co-borrower) | `Contact.Name`, `Contact.Tax_ID__c`, `Contact.Birthdate`, `Contact.Mother_s_Name__c` |
| **Lead** | 38 | Property data, CSOK/subsidy info | `Lead.Ingatlan_telepules__c`, `Lead.Ingatlan_irsz__c`, `Lead.Ingatlan_alapterulet__c` |
| **Opportunity** | 56 | Loan/deal data | `Opportunity.Hitel_sszeg__c`, `Opportunity.Hitelc_l__c`, `Opportunity.Term_k__c` |
| **Total** | **236** | | |

### 4.2 How CANONICAL_FIELDS is built

`CANONICAL_FIELDS` is defined in `src/ai/field_recognizer.py` as a Python dict mapping `Object.FieldName → label`. It was generated from the **live SF sandbox schema** using `simple_salesforce`'s `describe()` API:

```python
CANONICAL_FIELDS = {
    # Contact fields (person data)
    "Contact.Name": "Full Name",
    "Contact.Szuletesi_nev__c": "Születési név",
    "Contact.Mother_s_Name__c": "Anyja neve",
    "Contact.Place_of_Birth__c": "Születési hely",
    "Contact.Birthdate": "Birthdate",
    "Contact.Tax_ID__c": "Adóazonosító",
    "Contact.ID_Card_Number__c": "Személyi ig. szám",
    "Contact.MobilePhone": "Mobile Phone",
    "Contact.Email": "Email",
    "Contact.ZIP__c": "Irányítószám",
    "Contact.MailingCity": "Mailing City",
    "Contact.MailingStreet": "Mailing Street",
    "Contact.Loan_amount__c": "Igényelt hitelösszeg",
    "Contact.Loan_period__c": "Hitel futamidő",
    "Contact.Interest_Period__c": "Kamatperiódus",
    "Contact.Income_type__c": "Jövedelem típusa",
    "Contact.Average_monthly_net_income__c": "Átlagos havi nettó jövedelem",
    "Contact.Name_of_employer__c": "Munkáltató neve",
    # ... (142 Contact fields total)

    # Lead fields (property/subsidy)
    "Lead.Ingatlan_telepules__c": "Ingatlan település",
    "Lead.Ingatlan_irsz__c": "Ingatlan irányítószám",
    "Lead.Ingatlan_alapterulet__c": "Ingatlan alapterület",
    "Lead.Ingatlan_jellege__c": "Ingatlan jellege",
    "Lead.Estimated__c": "Becsült érték",
    "Lead.Purchase_price__c": "Vételár",
    "Lead.Tervezett_CSOK_Plusz__c": "Tervezett CSOK Plusz",
    # ... (38 Lead fields total)

    # Opportunity fields (loan/deal)
    "Opportunity.Hitel_sszeg__c": "Hitelösszeg",
    "Opportunity.Hitelc_l__c": "Hitelcél",
    "Opportunity.Term_k__c": "Termék",
    "Opportunity.StageName": "Stage",
    # ... (56 Opportunity fields total)
}
```

### 4.3 Field exclusions

The following SF field types were excluded during generation:
- System/metadata fields: `Id`, `CreatedDate`, `LastModifiedDate`, `SystemModstamp`, etc.
- URL fields, Geolocation fields
- Formula fields (read-only in SF)
- `IsDeleted`, `MasterRecordId`, etc.

### 4.4 Field data preparation (`_prepare_field_data`)

The fill pipeline translates DealData (Pydantic model) into SF-keyed dicts:

```python
# Person data → Contact.* keys
borrower_data = {
    "Contact.Name": participant.name,
    "Contact.Szuletesi_nev__c": participant.birth_name,
    "Contact.Mother_s_Name__c": participant.mother_name,
    "Contact.Tax_ID__c": participant.tax_id,
    "Contact.MobilePhone": participant.phone,
    "Contact.Email": participant.email,
    "Contact.Loan_amount__c": "20 000 000",    # from deal.loan
    "Contact.Loan_period__c": "240",            # from deal.loan
    ...
}

# Loan data → Opportunity.* keys
loan_data = {
    "Opportunity.Hitel_sszeg__c": "20 000 000",
    "Opportunity.Hitelc_l__c": "Lakásvásárlás",
    "Opportunity.Term_k__c": "Piaci hitel",
}

# Property data → Lead.* keys
prop_data = {
    "Lead.Ingatlan_irsz__c": "1111",
    "Lead.Ingatlan_telepules__c": "Budapest",
    "Lead.Ingatlan_kozterulet_neve__c": "Bartók Béla út 42",
    "Lead.Ingatlan_alapterulet__c": "65.0",
    "Lead.Estimated__c": "35 000 000",
}
```

### 4.5 Routing logic

For each field in the mapping, the pipeline routes by SF object prefix:

```python
if canonical.startswith("Contact."):
    # Check for -társ suffix → co-borrower
    source = co_borrower_data if is_co_borrower else borrower_data
    field_data[pdf_name] = source[canonical]

elif canonical.startswith("Lead."):
    field_data[pdf_name] = prop_data[canonical]

elif canonical.startswith("Opportunity."):
    field_data[pdf_name] = loan_data[canonical]
```

### 4.6 Co-borrower routing (OTP-specific)

OTP PDFs use naming conventions to distinguish borrower vs. co-borrower:
- PDF fields containing `-társ`, `társigénylő`, or `tarsigenylő` → route to `co_borrower_data`
- All other `Contact.*` fields → route to `borrower_data`

Both map to the same Contact.* canonical keys; the routing selects the data source.

---

## 5. Pydantic data model

Defined in `src/models/canonical_model.py` (Pydantic v2). This is the **internal** representation — it does NOT use SF field names. The SF field names are used only in the mapping/fill layer.

### Enums

```python
class ParticipantRole(str, Enum):
    BORROWER     = "adós"
    CO_BORROWER  = "adóstárs"
    GUARANTOR    = "kezes"
    BENEFICIARY  = "haszonélvező"

class PropertyType(str, Enum):
    APARTMENT  = "lakás"
    HOUSE      = "ház"
    LAND       = "telek"
    COMMERCIAL = "üzlethelyiség"
```

### Entities

**`Address`** — shared by participants and properties.

| Field | Type | SF Mapping |
|---|---|---|
| `zip_code` | str | `Contact.ZIP__c` / `Lead.Ingatlan_irsz__c` |
| `city` | str | `Contact.OtherCity` / `Lead.Ingatlan_telepules__c` |
| `street` | str | `Contact.OtherStreet` / `Lead.Ingatlan_kozterulet_neve__c` |
| `house_number` | str | `Lead.Ingtalan_hazszam__c` |
| `floor` | Optional[str] | — |
| `door` | Optional[str] | — |

**`Participant`**

| Field | Type | SF Mapping |
|---|---|---|
| `role` | ParticipantRole | `Contact.Relation__c` |
| `name` | str | `Contact.Name` |
| `birth_name` | Optional[str] | `Contact.Szuletesi_nev__c` |
| `mother_name` | Optional[str] | `Contact.Mother_s_Name__c` |
| `birth_place` | Optional[str] | `Contact.Place_of_Birth__c` |
| `birth_date` | Optional[date] | `Contact.Birthdate` |
| `personal_id` | Optional[str] | `Contact.ID_Card_Number__c` |
| `tax_id` | Optional[str] | `Contact.Tax_ID__c` |
| `phone` | Optional[str] | `Contact.MobilePhone` |
| `email` | Optional[str] | `Contact.Email` |
| `employer` | Optional[str] | `Contact.Name_of_employer__c` |
| `monthly_income` | Optional[int] | `Contact.Average_monthly_net_income__c` |

**`LoanDetails`**

| Field | Type | SF Mapping |
|---|---|---|
| `loan_amount` | int | `Contact.Loan_amount__c` / `Opportunity.Hitel_sszeg__c` |
| `loan_term_months` | int | `Contact.Loan_period__c` |
| `interest_period` | Optional[str] | `Contact.Interest_Period__c` |
| `loan_purpose` | Optional[str] | `Contact.Loan_Purpose__c` / `Opportunity.Hitelc_l__c` |
| `product_name` | Optional[str] | `Opportunity.Term_k__c` |
| `purchase_price` | Optional[int] | `Lead.Purchase_price__c` |

**`Property`**

| Field | Type | SF Mapping |
|---|---|---|
| `address` | Address | `Lead.Ingatlan_*` fields |
| `parcel_number` | str | `Lead.Ingatlan_megjegyzes__c` |
| `area_sqm` | Optional[float] | `Lead.Ingatlan_alapterulet__c` |
| `property_type` | PropertyType | `Lead.Ingatlan_jellege__c` |
| `estimated_value` | Optional[int] | `Lead.Estimated__c` |

**`DealData`** (root)

| Field | Type |
|---|---|
| `deal_id` | str (Salesforce Opportunity ID) |
| `salesforce_stage` | Optional[str] |
| `loan` | LoanDetails |
| `participants` | List[Participant] |
| `properties` | List[Property] |
| `products` | List[str] |

---

## 6. Mapping system

Each document has one **mapping config** stored as `src/mapping/<form>_mapping.json`.

### Per-field shape (`RecognizedField`)

```jsonc
{
  "pdf_field_name": "SZA_IG_név",            // PDF AcroForm /T name
  "label": "Név",                            // human label
  "field_type": "text",                      // text|checkbox|date|number|dropdown
  "canonical_field": "Contact.Name",         // SF Object.FieldName or null
  "confidence": "high",                      // high|medium|low|manual
  "page_number": 1,
  "coordinates": null,                       // {x, y, width, height} for flat/overlay
  "notes": null
}
```

### Canonical field naming

All canonical fields now use the **Salesforce `Object.FieldName` format**:

- `Contact.<field>` — personal data (name, birth, address, income, employment)
- `Lead.<field>` — property data (`Lead.Ingatlan_*`), CSOK/subsidy info
- `Opportunity.<field>` — loan/deal data (amount, purpose, product)

### Transformations at fill time

In `FormFillerPipeline._prepare_field_data`:

- **Money** fields are formatted as `"35 000 000"` (space-separated thousands).
- **Dates** are formatted `%Y.%m.%d` (Hungarian dotted).
- **Address "street"** concatenates street + house number.
- **Co-borrower routing**: PDF fields containing `-társ` or `társigénylő` route to the co-borrower's Contact data.

### Current mapping inventory

| File | Fields | Mapped to SF |
|---|---:|---:|
| `otp_piaci_hitel_mapping.json` (97-page master) | 1,744 | 382 |
| `otp_otthon_start_2szem_1ing_mapping.json` | 1,723 | 73 |
| `igenylesi_dokumentum_elozetes_ertekbecsleshez_mapping.json` | 1,030 | 204 |
| `v_szamu_fuggelek_nyilatkozat_mapping.json` | 90 | 1 |
| `acroform_sample_mapping.json` | 74 | 69 |
| `otp_acroform_mapping.json` | 50 | 50 |
| `zold_lakashitel_fiataloknak_nyilatkozat_mapping.json` | 34 | 17 |
| `flat_sample_mapping.json` | 27 | 27 |
| `hozzajarulo_nyilatkozat_munkaviszony_mapping.json` | 24 | 6 |
| `otp_jovedelem_ellenorzes_mapping.json` | 24 | 0 |
| `otp_zold_lakashitel_mapping.json` | 23 | 13 |
| `igazolas_csok_afa_kamattamogatasi_mapping.json` | 13 | 8 |
| `acroform_ai_mapping.json` | 74 | 14 |
| `otp_csok_afa_igazolas_mapping.json` | 7 | 4 |
| `otp_partner_nyilatkozat_mapping.json` | 6 | 1 |
| **Total** | **4,943** | **869** |

---

## 7. PDF filling

`src/engine/pdf_filler.py` defines `BaseFiller.fill(...)` returning `FillingResult`.

### 7.1 AcroForm filling — `AcroFormFiller`

For PDFs with interactive `/AcroForm/Fields`:

- Opens the template with **pikepdf**, recursively descends `/Kids`.
- For each field whose `/T` name is in `mapping`, resolves `canonical → value` from `field_data`.
- Writes type-sensitively:
  - `/Btn` (checkbox) → `pikepdf.Name("/Yes")` if truthy (`yes/igen/true/1/x/y/i/on`)
  - `/Tx`, `/Ch` (text, dropdown) → `pikepdf.String(str(value))`
- Deletes `/AP` so viewers re-render.
- Optional `flatten=True` → sets ReadOnly `/Ff` bit.

### 7.2 Overlay filling — `OverlayFiller`

For flat/scanned PDFs (no `/AcroForm`). Uses coordinates:

- `_create_overlay` builds a transparent PDF with **reportlab** Canvas.
- `_merge_overlay` merges via **pikepdf** `add_overlay`.
- Vertical correction: `y = coords.y + height - 3`.

---

## 8. Document assembly (master PDF slicing)

`src/engine/document_assembler.py` — `DocumentAssembler` slices the 97-page
OTP master PDF into a product/participant/property-specific sub-PDF.

### Page groups (ProductType → page ranges)

| Section | Pages | Scope |
|---|---|---|
| Cover page | 1 | global |
| Személyi adatlap | 2–11 | per_participant |
| Ingatlan adatlap | 12–17 | per_property |
| Hitelkérelem | 18–27 | global |
| CSOK/támogatás | varies | global |
| Partner nyilatkozat | varies | per_participant |
| Értékbecslés megrendelés | varies | per_property |

`DocumentAssembler.assemble(deal)` selects the relevant page ranges based on
`deal.products`, replicates per-participant/per-property sections, and produces
a single assembled PDF.

---

## 9. AI integration

`src/ai/field_recognizer.py` is the **onboarding layer**. It never touches
customer PII — it only sees *empty* PDFs.

### Model

```python
AI_MODEL = "claude-sonnet-4-20250514"
```

### Recognition modes

- **AcroForm** (`recognize_acroform`):
  1. `_extract_acroform_fields` (pikepdf) gathers `{name, type, value, page}`.
  2. `_heuristic_map_fields` produces baseline mapping using `OTP_EXACT_MAP` + `KEYWORD_MAP`.
  3. If `ANTHROPIC_API_KEY` is set:
     - `≤ 200` fields → single `_ai_map_fields` call.
     - `> 200` fields → `_ai_batch_recognize` — groups by page, sends batches of ≤60 fields with page images at 150 DPI.
  4. `_merge_mappings` fuses heuristic + AI results.

- **Flat/overlay** (`recognize_flat`):
  1. `_extract_text_anchors` detects `……` (ellipsis fill lines) via PyMuPDF.
  2. `_ai_map_flat_labels` sends labels to Claude.
  3. Builds `RecognizedField`s with coordinates.

### AI prompt strategy

The AI prompt tells Claude to map PDF fields to **Salesforce API field names**:

```
You are mapping PDF form field names to Salesforce API fields
for a Hungarian mortgage broker company.

SALESFORCE CANONICAL FIELDS (use exactly these keys):
  - Contact.Name: Full Name
  - Contact.Tax_ID__c: Adóazonosító
  - Lead.Ingatlan_telepules__c: Ingatlan település
  - Opportunity.Hitel_sszeg__c: Hitelösszeg
  ...

EXAMPLES:
  {"f": "SZA_IG_név", "c": "Contact.Name", "t": "text"}
  {"f": "IA_település", "c": "Lead.Ingatlan_telepules__c", "t": "text"}
  {"f": "LAHI_összeg", "c": "Opportunity.Hitel_sszeg__c", "t": "number"}
```

### Recognition accuracy

Tested on OTP 97-page master PDF (pages 1–3, 67 fields):
- **63/67 mapped (94% success rate)** using Claude with SF canonical fields.

### Canonical catalog shown to the model

`CANONICAL_FIELDS` — 236 SF fields with Hungarian labels. The full list is
embedded into the AI prompt to constrain Claude's output vocabulary.

---

## 10. Salesforce integration

`src/integrations/salesforce_client.py` provides `SalesforceClient`.

### Modes

- **Mock mode** (`mock_mode=True`): loads JSON from `samples/dummy_data/`.
- **Live mode** (`mock_mode=False`): uses `simple-salesforce` with parameterized SOQL.

### Live data model

`get_deal(deal_id)` queries:

1. **Opportunity** — `Hitel_sszeg__c`, `Hitelc_l__c`, `Term_k__c`, `StageName`, plus
   Contact lookups: `Opportunity_Contact_Name__c`, `First_Co_debtor__c` (×3),
   `Mortgagor__c`, `Usufructuary__c`.

2. **Contact** (one SOQL `WHERE Id IN :contact_ids`) — `Name`, `Szuletesi_nev__c`,
   `Mother_s_Name__c`, `Place_of_Birth__c`, `Date_of_birth__c`, `ID_Card_Number__c`,
   `Tax_ID__c`, `Permanent_address__c`, `Phone`, `Email`, `Name_of_employer__c`,
   `Average_monthly_net_income__c`, `ZIP__c`, etc.

3. **Opportunity_Property_Role__c** (junction table) — links Opportunity ↔ Property.

4. **Property__c** — `Property_Type__c`, `Ingatlan_hrsz__c`, `Ingatlan_alapterulet__c`,
   `Property_value__c`, `Ingatlan_irsz__c`, `Ingatlan_telepules__c`,
   `Ingatlan_kozterulet_neve__c`, `Ingatlan_hazszam__c`, etc.

### Write-back

- `attach_pdf` → creates SF ContentVersion with base64-encoded PDF.
- `update_stage` → updates Opportunity StageName to `"Dokumentáció kész – review"`.

---

## 11. Mapping Editor (Web UI)

A visual editor for creating and reviewing field mappings.

### Backend — `backend/server.py` (FastAPI, port 8765)

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/pdfs` | List all PDFs |
| GET | `/api/pdf/info` | PDF metadata |
| GET | `/api/pdf/page/{n}/image` | Render page PNG |
| GET | `/api/pdf/fields` | Extract AcroForm fields |
| GET | `/api/pdf/preview` | Multi-page preview |
| GET | `/api/mapping` | Load mapping JSON |
| PUT | `/api/mapping` | Save full mapping |
| PUT | `/api/mapping/field` | Update single field |
| POST | `/api/mapping/field` | Add new field |
| DELETE | `/api/mapping/field` | Delete field |
| GET | `/api/mapping/canonical-fields` | **Serve 236 SF canonical fields** |
| POST | `/api/mapping/recognize` | Start AI recognition |
| GET | `/api/recognize/{id}/status` | Poll recognition status |
| GET | `/api/recognize/{id}/result` | Get recognition result |
| POST | `/api/pdf/upload` | Upload PDF + auto-fill |
| GET | `/api/pdf/download` | Download filled PDF |
| POST/PUT/DELETE | `/api/mapping/group` | Character group CRUD |
| POST | `/api/mapping/suggest-groups` | Auto-suggest groups |
| GET/POST | `/api/mapping/export`, `/import` | JSON export/import |

### Frontend — React + TypeScript + Vite

| Component | Purpose |
|---|---|
| `PageEditor.tsx` | Page-level view with field overlays + canonical field dropdown (grouped by Contact/Lead/Opportunity) |
| `ReviewDashboard.tsx` | Mapping statistics, per-page heatmap, duplicate detection, approval workflow |
| `store.ts` | Zustand state management |
| `api/client.ts` | Typed API client |

The canonical field dropdown is grouped by SF object using `<optgroup>`:

```
── Contact ──────────────────
   Name — Full Name
   Tax_ID__c — Adóazonosító
   Birthdate — Birthdate
   ...
── Lead ─────────────────────
   Ingatlan_telepules__c — Ingatlan település
   Ingatlan_irsz__c — Ingatlan irányítószám
   ...
── Opportunity ──────────────
   Hitel_sszeg__c — Hitelösszeg
   Term_k__c — Termék
   ...
```

---

## 12. Configuration

### Environment variables (`config/.env`)

| Var | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API (onboarding only) | `""` |
| `SF_USERNAME` | Salesforce sandbox username | `""` |
| `SF_PASSWORD` | Salesforce sandbox password | `""` |
| `SF_SECURITY_TOKEN` | Salesforce security token | `""` |
| `SF_DOMAIN` | Salesforce domain | `test` |
| `LOG_LEVEL` | Python logging level | `INFO` |

### CLI (`python -m src.main`)

```
--scenario, -s       dummy scenario (simple|multi_borrower|multi_property|complex|missing_data)
--recognize, -r PATH run AI field recognition on this PDF first
--template, -t PATH  PDF template to fill
--mapping, -m PATH   mapping JSON
--salesforce, -sf    use real Salesforce sandbox
--deal, -d ID        explicit Salesforce Opportunity ID
```

### Dependencies

`pikepdf>=8.0.0`, `reportlab>=4.0`, `PyMuPDF>=1.23.0`, `pydantic>=2.0`,
`anthropic>=0.30.0`, `simple-salesforce>=1.12.0`, `python-dotenv>=1.0.0`,
`pytest>=8.0`, `pytest-cov>=5.0`.

---

## 13. Test coverage

| File | Covers |
|---|---|
| `test_canonical_model.py` | Address, Participant, DealData, LoanDetails validation, JSON round-trip |
| `test_normalizer.py` | Canonical JSON + SF normalization, date parsing, role mapping |
| `test_document_assembler.py` | Page-plan math for all ProductType combos |
| `test_integration.py` | End-to-end fill, mapping I/O, AI heuristic modes |

### Test gaps

- No isolated tests for `pdf_filler.py` (AcroFormFiller/OverlayFiller).
- No tests for `completeness_checker.py`.
- No tests for `role_instance_logic.py`.
- No live Salesforce tests (expected; sandbox-only).
- No AI tests (Claude not called in CI).

---

## 14. Quick start

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 1. Generate samples + dummy deals
python scripts/generate_sample_pdfs.py
python scripts/generate_dummy_data.py

# 2. Run the default pipeline (mock Salesforce)
python -m src.main

# 3. Start the Mapping Editor
cd backend && uvicorn server:app --port 8765
cd frontend && npm run dev

# 4. AI field recognition (needs ANTHROPIC_API_KEY)
python -m src.ai.field_recognizer samples/acroform_sample.pdf

# 5. Tests
pytest tests/ -v --cov=src
```

---

*End of `HOW_IT_WORKS.md`.*

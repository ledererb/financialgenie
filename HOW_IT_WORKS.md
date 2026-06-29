# FinancialGenie — How It Works

> Definitive technical reference for the FinancialGenie codebase
> (commit `c20292e`).
>
 FinancialGenie is a **Proof of Concept** built by *Think AI Kft.* that
automates the filling of OTP Bank mortgage/form documents from Salesforce deal
data. It is a *deterministic* filler (no AI in the hot path) paired with an
*AI-assisted onboarding* layer (Claude) used only to recognize the fields of a
brand-new, empty form and produce a mapping configuration. The repository is
written in Hungarian; this document is in English.

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

The formal product description, scope and architecture intent live in
`financialgenie_tech_spec_0616.pdf` (Think AI technical specification).

---

## 2. Architecture overview

```
financialgenie/
├── README.md
├── requirements.txt
├── financialgenie_tech_spec_0616.pdf        # Think AI tech spec (PDF)
├── config/
│   ├── __init__.py
│   ├── settings.py                          # env loading, paths, API keys
│   └── .env.example
├── otp/                                     # real OTP PDF source documents
│   ├── Piaci hitel/                         # market-rate mortgage package
│   ├── Otthon Start/                        # Otthon Start product package
│   ├── Szabadfelhasználású hitel/           # free-purpose loan package
│   └── Előzetes értékbecslé megrendelés/    # preliminary valuation order
├── samples/
│   ├── acroform_sample.pdf                  # generated 3-page AcroForm sample
│   ├── flat_sample.pdf                      # generated 3-page flat sample
│   └── dummy_data/*.json                    # generated canonical deals
├── scripts/
│   ├── generate_sample_pdfs.py              # reportlab AcroForm/flat samples
│   ├── generate_dummy_data.py               # realistic Hungarian dummy deals
│   └── test_otp_fill.py                     # real OTP master PDF fill demo
├── src/
│   ├── main.py                              # FormFillerPipeline + CLI
│   ├── models/
│   │   └── canonical_model.py               # Pydantic canonical schema
│   ├── normalizer/
│   │   └── data_normalizer.py               # SF/raw → canonical
│   ├── integrations/
│   │   └── salesforce_client.py             # mock + real Salesforce
│   ├── engine/
│   │   ├── pdf_filler.py                    # AcroFormFiller + OverlayFiller
│   │   ├── document_assembler.py            # master-PDF slicer
│   │   ├── completeness_checker.py          # validation/suspicious checks
│   │   └── role_instance_logic.py           # per-role page planning
│   ├── ai/
│   │   └── field_recognizer.py              # Claude field recognition
│   └── mapping/                             # JSON mapping configs
│       ├── otp_piaci_hitel_mapping.json
│       ├── otp_acroform_mapping.json
│       ├── acroform_sample_mapping.json
│       ├── acroform_ai_mapping.json
│       ├── otp_partner_nyilatkozat_mapping.json
│       ├── otp_csok_afa_igazolas_mapping.json
│       ├── otp_jovedelem_ellenorzes_mapping.json
│       ├── otp_zold_lakashitel_mapping.json
│       ├── igazolas_csok_afa_kamattamogatasi_kerelem_atvetele_mapping.json
│       ├── hozzajarulo_nyilatkozat_munkunkaviszony_es_jovedel_mapping.json
│       └── zold_lakashitel_fiataloknak_nyilatkozat_elso_lakas_mapping.json
└── tests/
    ├── test_canonical_model.py
    ├── test_normalizer.py
    ├── test_document_assembler.py
    └── test_integration.py
```

### Component roles

| Component | File | Responsibility |
|---|---|---|
| **Pipeline orchestrator** | `src/main.py` | `FormFillerPipeline.run_for_deal()` — wires SF → normalizer → checker → field-data prep → assembly → fill → write-back. Also the CLI entry. |
| **Canonical model** | `src/models/canonical_model.py` | Bank-independent Pydantic v2 schema: `DealData`, `Participant`, `Property`, `LoanDetails`, `Address`. |
| **Normalizer** | `src/normalizer/data_normalizer.py` | `DataNormalizer.normalize_deal()` — accepts both canonical JSON (dummy data) and Salesforce-shaped dicts; converts role strings, property types, dates. |
| **Salesforce client** | `src/integrations/salesforce_client.py` | `SalesforceClient` — dual-mode: `mock_mode=True` reads dummy JSON; otherwise `simple-salesforce` with username/password/security token, parameterized SOQL. |
| **PDF filler engine** | `src/engine/pdf_filler.py` | `BaseFiller`, `AcroFormFiller` (pikepdf), `OverlayFiller` (reportlab + PyMuPDF + pikepdf merge), `FillingResult`, `TextPlacement`. |
| **Document assembler** | `src/engine/document_assembler.py` | `DocumentAssembler` slices the 97-page OTP master PDF into a product/participant/property-specific sub-PDF before filling. |
| **Completeness checker** | `src/engine/completeness_checker.py` | Walks `required_fields` against a `DealData`, reports MISSING/EMPTY/SUSPICIOUS/STRUCTURAL issues and a `CompletenessStatus`. |
| **Role/instance planner** | `src/engine/role_instance_logic.py` | `RoleInstancePlanner.plan()` builds a `GenerationPlan` of `PageSpec`s based on `PageScope` (GLOBAL / PER_PARTICIPANT / PER_PROPERTY / PER_PARTICIPANT_PROPERTY). |
| **AI field recognizer** | `src/ai/field_recognizer.py` | `FieldRecognizer` + `MappingConfig` — heuristic + Claude mapping of empty-PDF fields to the canonical model. |
| **Mapping configs** | `src/mapping/*.json` | One `MappingConfig` per document, serialized by `MappingConfig.save/load`. |
| **Settings** | `config/settings.py` | Loads `config/.env`, exposes API keys, paths, dirs. |

---

## 3. Data flow

The canonical end-to-end flow (matches `FormFillerPipeline.run_for_deal`):

```
┌────────────┐   1. get_deal(deal_id)        ┌──────────────────┐
│ Salesforce │ ─────────────────────────────▶ │ SalesforceClient │
│ (or mock)  │                                │  (mock or real)  │
└────────────┘                                └────────┬─────────┘
                                                       │ raw dict
                                                       ▼
                                ┌──────────────────────────────┐
                                │ 2. DataNormalizer.normalize  │
                                │     SF role/type maps        │
                                │     date / int parsing       │
                                └──────────────┬───────────────┘
                                               │ DealData (canonical)
                                               ▼
                                ┌──────────────────────────────┐
                                │ 3. CompletenessChecker.check │
                                │     (required_fields list)   │
                                └──────────────┬───────────────┘
                                               │ CompletenessReport
                                               ▼
                                ┌──────────────────────────────┐
                                │ 4. _prepare_field_data       │
                                │    borrower/co_borrower/loan │
                                │    /property canonical dicts │
                                │    → mapping → field_data    │
                                └──────────────┬───────────────┘
                                               │ dict[pdf_field → value]
                                               ▼
                                ┌──────────────────────────────┐
                                │ 5. DocumentAssembler         │
                                │   if is_master_pdf (≥97 pp): │
                                │   slice → assembled.pdf      │
                                └──────────────┬───────────────┘
                                               ▼
                                ┌──────────────────────────────┐
                                │ 6. AcroFormFiller OR          │
                                │    OverlayFiller             │
                                │    → filled_<deal>_<ts>.pdf  │
                                └──────────────┬───────────────┘
                                               ▼
                                ┌──────────────────────────────┐
                                │ 7. SalesforceClient.attach   │
                                │    _pdf + update_stage       │
                                └──────────────────────────────┘
```

Step-by-step inside `run_for_deal` (`src/main.py:66`):

1. **Adatlekérés** — `sf_client.get_deal(deal_id)` returns a raw dict. In mock mode it reads `samples/dummy_data/<deal_id>.json`; in live mode it issues parameterized SOQL against `Opportunity`, `Contact`, `Opportunity_Property_Role__c`, and `Property__c` (see §10).
2. **Normalizálás** — `normalizer.normalize_deal(raw)`. If the raw dict already has `loan` + `participants` keys (canonical dummy JSON), it validates via Pydantic; otherwise it runs `_normalize_salesforce`, mapping SF `Role__c` strings to `ParticipantRole` and `Property_Type__c` strings to `PropertyType`.
3. **Teljességellenőrzés** — `CompletenessChecker` walks the canonical model with a hardcoded `required_fields` list (loan amount/term, per-participant name/birth/mother/birth date/place/tax/phone, per-property parcel) plus structural checks (≥1 borrower, ≥1 property).
4. **Mezőadatok összeállítása** — `_prepare_field_data(deal, mapping)` flattens the canonical model into three dicts (`borrower_data`, `co_borrower_data`, `loan_data`, `prop_data`) keyed by dotted canonical paths (`participant.name`, `loan.loan_amount`, …). It then iterates `mapping.fields`, routing each canonical field to the right participant namespace using OTP's `-társ` co-borrower convention, and emits `field_data[pdf_field_name] = value`. Money is formatted as `"NNN NNN NNN"` (space-separated thousands).
5. **PDF kitöltés** — `DocumentAssembler.is_master_pdf` checks if the template is the full 97-page OTP master. If so, the master is sliced into an `assembled_<deal>.pdf` using `ProductType` values derived from `deal.products` plus participant/property counts. Then `_fill_pdf` dispatches on `mapping.form_type`:
   - `"acroform"` → `AcroFormFiller.fill` writes `/V` values into PDF `/AcroForm/Fields` via pikepdf.
   - anything else (`"flat"`) → `OverlayFiller.fill` renders an invisible overlay PDF with reportlab and merges it onto the template with pikepdf, using the per-field `coordinates` block from the mapping.
6. **Visszaírás** — `sf_client.attach_pdf(deal_id, output_path, filename)` (creates a Salesforce `ContentVersion` in live mode) and `sf_client.update_stage(deal_id, "Dokumentáció kész – review")`.

The pipeline result dict is `{"deal_id", "success", "output_path", "issues", "timestamp"}`.

---

## 4. Canonical data model

Defined in `src/models/canonical_model.py` (Pydantic v2).

### Enums

```python
class ParticipantRole(str, Enum):
    BORROWER     = "adós"        # primary borrower
    CO_BORROWER  = "adóstárs"    # co-borrower
    GUARANTOR    = "kezes"       # guarantor
    BENEFICIARY  = "haszonélvező" # usufructuary / beneficiary

class PropertyType(str, Enum):
    APARTMENT  = "lakás"
    HOUSE      = "ház"
    LAND       = "telek"
    COMMERCIAL = "üzlethelyiség"
```

### Entities

**`Address`** — shared by participants and properties.

| Field | Type | Note |
|---|---|---|
| `zip_code` | str | required |
| `city` | str | required |
| `street` | str | required |
| `house_number` | str | required |
| `floor` | Optional[str] | |
| `door` | Optional[str] | |

Computed: `full_address` → `"1052, Budapest, Váci utca 10, 3. em., 12. ajtó"`.

**`Participant`**

| Field | Type | Note |
|---|---|---|
| `role` | ParticipantRole | required |
| `name` | str | required |
| `birth_name` | Optional[str] | |
| `mother_name` | Optional[str] | |
| `birth_place` | Optional[str] | |
| `birth_date` | Optional[date] | |
| `personal_id` | Optional[str] | személyi igazolvány |
| `tax_id` | Optional[str] | adóazonosító jel |
| `id_card_number` | Optional[str] | személyi szám |
| `address` | Optional[Address] | állandó lakcím |
| `mailing_address` | Optional[Address] | levelezési cím |
| `phone` | Optional[str] | |
| `email` | Optional[str] | |
| `employer` | Optional[str] | |
| `monthly_income` | Optional[int] | Ft |
| `is_active` | bool | default True; `False` = "kuka" (skipped) |

Helpers: `is_borrower`, `is_co_borrower`.

> Note: the AI recognizer and `_prepare_field_data` reference *more* canonical
> participant keys than the Pydantic model declares — e.g. `participant.gender`,
> `participant.citizenship`, `participant.marital_status`,
> `participant.id_document_type`, `participant.education`,
> `participant.employment_type`, `participant.dependents`,
> `participant.employee_count`, `participant.nav_declaration`,
> `participant.mailing_address_same`, `participant.residence_since`,
> `participant.business_name`, `participant.business_tax_id`,
> `participant.employer_tax_id`, `participant.kata_status`,
> `participant.address.country`. These are emitted as flat strings during field
> preparation (see `main._participant_to_dict`) but not declared on the model —
> a deliberate loosening so checkbox-style PDF fields can still be ticked when
> richer SF data is available.

**`Property`**

| Field | Type | Note |
|---|---|---|
| `address` | Address | required |
| `parcel_number` | str | required (helyrajzi szám) |
| `area_sqm` | Optional[float] | |
| `property_type` | PropertyType | default `APARTMENT` |
| `estimated_value` | Optional[int] | Ft |
| `year_built` | Optional[int] | |
| `number_of_rooms` | Optional[int] | |

**`LoanDetails`**

| Field | Type | Note |
|---|---|---|
| `loan_amount` | int | required (Ft) |
| `loan_term_months` | int | required |
| `interest_period` | Optional[str] | e.g. "5 év fix" |
| `loan_purpose` | Optional[str] | |
| `product_name` | Optional[str] | |
| `product_type` | Optional[str] | `piaci_hitel`, `csok_plusz`, … |
| `down_payment` | Optional[int] | önerő |
| `monthly_payment` | Optional[int] | törlesztő |
| `purchase_price` | Optional[int] | vételár |
| `csok_amount` | Optional[int] | CSÖK támogatás |
| `afa_support` | Optional[int] | ÁFA támogatás |
| `housing_savings` | Optional[int] | lakástakarék |
| `refinance_account` | Optional[str] | kiváltandó hitel számlaszáma |

**`DealData`** (root)

| Field | Type | Note |
|---|---|---|
| `deal_id` | str | Salesforce Opportunity ID |
| `salesforce_stage` | Optional[str] | |
| `loan` | LoanDetails | required |
| `participants` | List[Participant] | |
| `properties` | List[Property] | |
| `products` | List[str] | default `["piaci_hitel"]` |
| `created_at` | Optional[date] | |
| `notes` | Optional[str] | |

Computed: `active_participants`, `borrowers`, `co_borrowers`, `guarantors`,
`get_participants_by_role(role)`.

### Participant-type catalog

The role taxonomy maps onto the Hungarian SF labels and OTP "participant types"
(`ADOS`, `ADOSTARS`, `KEZES`, `HASZONELVEZO`). In the code the SF role strings
are normalized in `data_normalizer.SF_ROLE_MAP`:

| Canonical | Hungarian values accepted |
|---|---|
| `BORROWER` | `adós`, `ados`, `borrower`, `főadós`, `foadós`, `primary_borrower` |
| `CO_BORROWER` | `adóstárs`, `adostars`, `co_borrower`, `társadós` |
| `GUARANTOR` | `kezes`, `guarantor` |
| `BENEFICIARY` | `haszonélvező`, `haszonelvezo`, `beneficiary` |

### Property-type catalog

`data_normalizer.SF_PROPERTY_TYPE_MAP` accepts many Hungarian variants — e.g.
`lakás`, `társasházi lakás`, `használt társasházi lakás`, `újépítésű lakás`,
`családi ház`, `használt lakóház`, `nyaraló, üdülő, hétvégi ház`, `telek`,
`üzlethelyiség`, `iroda` — collapsing them into one of the four canonical
`PropertyType` values.

---

## 5. Mapping system

Each document has one **mapping config** (a serialized `MappingConfig`) stored
under `src/mapping/<form>_mapping.json`. It is the bridge between the canonical
model and the concrete PDF.

### Top-level shape

```jsonc
{
  "bank_name": "OTP Bank",
  "form_name": "Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5",
  "form_type": "acroform",           // "acroform" or "flat"
  "approved": false,                 // human sign-off flag
  "approved_by": null,
  "notes": "Összefésült mapping: heurisztikus + AI (374 leképezve).",
  "page_structure": {},              // optional {"1": "global", "2": "per_participant", ...}
  "fields": [ /* RecognizedField[] */ ]
}
```

### Per-field shape (`RecognizedField`)

```jsonc
{
  "pdf_field_name": "SZA_IG_név",           // PDF AcroForm /T or generated id
  "label": "Név",                           // human label from the form
  "field_type": "text",                     // text|checkbox|date|number|dropdown
  "canonical_field": "participant.name",    // null if unmapped
  "confidence": "high",                     // high|medium|low
  "page_number": 1,
  "coordinates": null,                      // {x, y, width, height} for flat/overlay
  "notes": null                             // free text / AI note
}
```

### How mappings are consumed

- `MappingConfig.mapping_dict` → `{pdf_field_name: canonical_field}` for all
  fields with a non-null canonical. `AcroFormFiller` consumes this directly.
- `MappingConfig.fields[].coordinates` is read by the pipeline's overlay branch
  (`main._fill_pdf`) to build `TextPlacement`s for `OverlayFiller`.
- `page_structure` is descriptive and feeds the AI reviewer; it is not used by
  the deterministic filler.

### Canonical field naming conventions

- `loan.<attr>` — single-valued hitel block.
- `participant.<attr>` and `participant.address.<attr>` /
  `participant.mailing_address.<attr>` — personal data, duplicated per
  participant; OTP distinguishes igénylő (no suffix) vs társigénylő (PDF field
  ends with `-társ`).
- `property.<attr>` and `property.address.<attr>` — real-estate data.
- "Special" canonicals like `participant.role`, `participant.gender`,
  `participant.citizenship`, … correspond to **checkbox groups** on the OTP
  master form and are *not* text-filled (the pipeline `continue`s on
  `participant.role`; others would tick truthy values via `AcroFormFiller`'s
  `/Btn` handling).

### Transformations applied at fill time

In `FormFillerPipeline._prepare_field_data`:

- **Money** fields (`loan_amount`, `monthly_income`, `estimated_value`, …) are
  rendered as `f"{n:,}".replace(",", " ")` → `"35 000 000"`.
- **Dates** are formatted `%Y.%m.%d` (Hungarian dotted).
- **Address "street" canonical** is the concatenation of street + house number.
- **Co-borrower routing**: a PDF field whose name contains `-társ`, `társigénylő`
  or `SZA_IG_…-társ` is routed to the first co-borrower's data; otherwise to the
  first borrower. This makes the same canonical path (`participant.name`) fill
  both the igénylő and társigénylő blocks.

### Source of mappings

Mappings are produced in three ways:

1. **Heuristic** (`FieldRecognizer._heuristic_map_fields`) — always runs, uses
   two tables: `OTP_EXACT_MAP` (real OTP field names like `SZA_IG_név`,
   `LAHI_KTKA_Igényelt_kölcsön`, `IA_Bérletidíj`) and a generic `KEYWORD_MAP`.
   Keys are normalized through `_normalize_key` (NFD-decompose + drop combining
   marks + lowercase) so `"SZA_IG_név"`, `"SZA_IG_nev"`, `"sza_ig_név"` all
   resolve identically.
2. **AI** (Claude Sonnet, see §9) — refines heuristics in single-call or
   batch mode; merged with `_merge_mappings` (AI wins when it produces a
   canonical).
3. **Hand-authored** — e.g. `otp_acroform_mapping.json` (approved=true) and
   `zold_lakashitel_fiataloknak_nyilatkozat_elso_lakas_mapping.json`.

### Current mapping inventory

| File | form_type | fields | mapped |
|---|---|---:|---:|
| `otp_piaci_hitel_mapping.json` (97-page master, AcroForm) | acroform | 1745 | 374 |
| `acroform_sample_mapping.json` (3-page sample) | acroform | 74 | 69 |
| `otp_acroform_mapping.json` (approved sample) | acroform | 50 | 50 |
| `acroform_ai_mapping.json` | acroform | 74 | 14 |
| `hozzajarulo_nyilatkozat_munkunkaviszony_es_jovedel_mapping.json` | flat | 23 | 8 |
| `igazolas_csok_afa_kamattamogatasi_kerelem_atvetele_mapping.json` | flat | 13 | 8 |
| `otp_csok_afa_igazolas_mapping.json` | flat | 7 | 4 |
| `otp_jovedelem_ellenorzes_mapping.json` | flat | 7 | 3 |
| `otp_partner_nyilatkozat_mapping.json` | flat | 5 | 1 |
| `otp_zold_lakashitel_mapping.json` | flat | 23 | 13 |
| `zold_lakashitel_fiataloknak_nyilatkozat_elso_lakas_mapping.json` | flat | 34 | 17 |

---

## 6. PDF filling

`src/engine/pdf_filler.py` defines a common `BaseFiller.fill(...)` returning a
`FillingResult` (`success`, `filled_fields`, `skipped_fields`, `errors`,
`output_path`, `summary` property).

### 6.1 AcroForm filling — `AcroFormFiller`

For PDFs with interactive `/AcroForm/Fields`.

- Opens the template with **pikepdf**, locates `/AcroForm/Fields`, recursively
  descends `/Kids`.
- For each field whose `/T` name is in `mapping`, resolves
  `canonical → value` from `field_data`.
- Writes the value type-sensitively:
  - `/Btn` (checkbox) → `pikepdf.Name("/Yes")` if `_is_truthy(value)` else
    `pikepdf.Name("/Off")`. Truthy set: `yes/igen/true/1/x/y/i/on`.
  - `/Tx`, `/Ch` (text, dropdown, list) → `pikepdf.String(str(value))`.
- Deletes `/AP` so viewers re-render.
- Optional `flatten=True` → `_make_readonly` sets `/NeedAppearances` and the
  ReadOnly `/Ff` bit (pikepdf cannot do real flattening; use Ghostscript/pdftk
  for that).
- `AcroFormFiller.list_fields(pdf)` is a debug helper that dumps name/type/value.

### 6.2 Overlay filling — `OverlayFiller`

For "flat"/scanned PDFs (no `/AcroForm`). Works on **coordinates**.

- `_get_page_dimensions` reads each page's `(width, height)` with **PyMuPDF**
  (fitz).
- `_create_overlay` builds a transparent PDF with **reportlab**'s
  `canvas.Canvas`: for each `page_index`, draws every
  `TextPlacement(canonical_name) = (x, y, font, size, page)` whose value is
  non-empty via `c.drawString(x, y, value)`.
- `_merge_overlay` opens both PDFs with **pikepdf** and calls
  `original_page.add_overlay(overlay_page)` per page, then saves.
- The pipeline's `_fill_pdf` constructs `TextPlacement`s from the mapping's
  `coordinates` blocks, applying a vertical correction
  `y = coords.y + height - 3` (commit `292d0a1`: "shift overlay text 3 pixels
  upwards to sit neatly above the line"). `font_size` defaults to 10.
- Only fields with both a `coordinates` block and a non-empty canonical value
  are emitted.

### Key engine behavior

- Both fillers accept `field_data` keyed either by canonical name (overlay) or
  pdf field name (acroform). The pipeline adapts accordingly.
- `FillingResult` is logged but never raised — partial fills still produce a
  PDF; issues flow into the pipeline `result["issues"]`.

---

## 7. Document assembly (master PDF slicing)

`src/engine/document_assembler.py` replicates what OTP's internal system does:
build the final document package out of the **97-page master PDF** by selecting
the right page ranges for the requested products, number of participants and
number of properties.

### Master detection

```python
DocumentAssembler.MASTER_PDF_PAGE_COUNT = 97
def is_master_pdf(pdf_path) -> bool:
    return pikepdf page count >= 97
```

This is a *document-agnostic* heuristic: any PDF with ≥97 pages is treated as
the OTP master and sliced; smaller PDFs are assumed pre-assembled and filled
directly. The pipeline (`main.run_for_deal`) calls `is_master_pdf` before
filling and runs assembly automatically.

### Section map (1-indexed, inclusive)

```python
BASE_SECTIONS = {
    "fedlap":                 (1, 1),
    "sza_ig_igenylő":         (2, 5),    # personal data — borrower
    "sza_ig_tarsigenylő":     (6, 9),    # personal data — co-borrower
    "sza_esz":                (10, 10),  # eszköz nyilatkozat
    "meghatalmazas":          (11, 12),
    "ingatlan_adatlap":       (13, 15),
    "hitelfeltetelek":        (16, 21),  # KTKA
    "altalanos_nyilatkozatok": (22, 30),
}
TARSADOS_SECTION = ("tarsados_adatlap", 31, 36)   # ≥2 participants only

PRODUCT_SECTIONS = {
    ProductType.PIACI_HITEL:      [],
    ProductType.CSOK:             [("csok_nyilatkozat", 37, 48)],
    ProductType.AFA_VISSZATERITES:[("afa_nyilatkozat", 49, 56)],
    ProductType.CSOK_PLUSZ:       [("csok_plusz", 57, 63), ("csok_plusz_kiegesz", 64, 68)],
    ProductType.OTTHON_START:     [("otthon_start", 69, 82)],
    ProductType.HITELKARTYA:      [("hitelkartya", 83, 85)],
    ProductType.VIDEKI_FELUJITAS: [("videki_felujitas", 86, 93), ("videki_kiegesz", 94, 97)],
}
```

### Page-plan logic (`_build_page_plan`)

1. Always include the cover (`fedlap`).
2. Always include the igénylő SZA block (pages 2–5).
3. If `num_participants >= 2`, include the társigénylő block (6–9); for every
   extra participant beyond 2, duplicate that block (up to `num_participants`
   total co-borrowers).
4. Include `sza_esz`, `meghatalmazas`.
5. Duplicate `ingatlan_adatlap` (13–15) `num_properties` times.
6. Include `hitelfeltetelek` and `altalanos_nyilatkozatok`.
7. If `num_participants >= 2`, include the társadós section (31–36).
8. For each requested product (de-duplicated), include its `PRODUCT_SECTIONS`
   range(s).

Pages are then copied from the source PDF by index via pikepdf into a new
`Pdf.new()`, copying `/AcroForm` if present. Output filename:
`OTP_<products>_<n>szem_<m>ing.pdf`. `get_assembly_summary` returns a preview
without writing a PDF (used by tests).

### Verified assembly math (from `tests/test_document_assembler.py`)

- Piaci hitel, 2 szem, 1 ingatlan → **36** pages (30 base + 6 társadós).
- 1 szem → **26** pages (no társigénylő, no társadós).
- 3 szem → **40** pages (+4 for 2nd extra co-borrower).
- 2 ingatlan → **39** pages (+3 for 2nd property block).
- CSOK Plusz → 48, Otthon Start → 50, CSOK+ÁFA → 56, combo CSOK Plusz + Otthon
  Start → 62, complex (CSOK Plusz + Otthon Start, 3 szem, 2 ing) → 69.

---

## 8. Document type catalog (OTP products)

The repo ships two document tiers: (a) the **97-page master application
package** per product, and (b) **standalone auxiliary forms** that accompany
the master.

### Master application packages (in `otp/`)

Each contains `Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf`
(the 97-page master, AcroForm-enabled, ~1745 fields), a
`Partner_nyilatkozat_hiteligeny_leadasakor.pdf`, and a
`V_szamu_fuggelek_…_ugyfel_ceg_nyilatkozat_20250601.pdf`. Extras vary per
product.

| Directory | Product | Extra forms | Mapping used |
|---|---|---|---|
| `otp/Piaci hitel/` | **Piaci hitel** (market-rate mortgage) | partner nyilatkozat + V. sz. függelék | `otp_piaci_hitel_mapping.json` |
| `otp/Otthon Start/` | **Otthon Start** (state-subsidized) | + `Igazolas_CSOK_AFA_kamattamogatasi_kerelem_atvetelerol_20250901.pdf`, + `Zold_Lakashitel_Fiataloknak_Nyilatkozat_elso_lakastulajdonrol_20251201.pdf` | `igazolas_csok_afa…`, `zold_lakashitel_fiataloknak…`, `otp_csok_afa_igazolas_mapping.json` |
| `otp/Szabadfelhasználású hitel/` | **Szabadfelhasználású hitel** (free-purpose loan) | + `Hozzajarulo_nyilatkozat_munkunkaviszony_es_jovedelemadatok_ellenorzesehez.pdf` | `hozzajarulo_nyilatkozat…`, `otp_jovedelem_ellenorzes_mapping.json` |
| `otp/Előzetes értékbecslé megrendelés/` | **Előzetes értékbecslés** (preliminary valuation order) | `Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104.pdf` + partner nyilatkozat | `otp_partner_nyilatkozat_mapping.json` |

### Standalone OTP forms (with their own mapping)

- **Partner nyilatkozat hiteligény leadásakor** — `otp_partner_nyilatkozat_mapping.json`
  (5 fields, only 1 mapped — `flat_1_3` → `participant.name`). Flat/overlay.
- **Igazolás CSÖK/ÁFA/kamattámogatási kérelem átvétele** — two variants:
  `igazolas_csok_afa_kamattamogatasi_kerelem_atvetele_mapping.json` (hand-crafted,
  13 fields, 8 mapped) and `otp_csok_afa_igazolas_mapping.json` (text-anchor
  AI, 7 fields, 4 mapped).
- **Hozzájáruló nyilatkozat munkaviszony és jövedelemadatok
  visszaellenőrzéséhez** — two variants:
  `hozzajarulo_nyilatkozat_munkunkaviszony_es_jovedel_mapping.json`
  (hand-authored, 23 fields, 8 mapped) and `otp_jovedelem_ellenorzes_mapping.json`
  (text-anchor AI, 7 fields, 3 mapped).
- **Zöld Lakáshitel Fiataloknak — Nyilatkozat első lakástulajdonról** — two
  variants: `zold_lakashitel_fiataloknak_nyilatkozat_elso_lakas_mapping.json`
  (hand-authored, 34 fields, 17 mapped, `per_participant` page structure) and
  `otp_zold_lakashitel_mapping.json` (text-anchor AI, 23 fields, 13 mapped).

### Special handling notes

- The **97-page master** is the only AcroForm-enabled real OTP document. Its
  field names follow OTP conventions: `SZA_IG_*` (igénylő), `SZA_IG_*-társ`
  (társigénylő), `TA_*` (társadós), `IA_*` (ingatlan), `LAHI_KTKA_*`
  (hitel/költség), `A_NY_*` (általános nyilatkozatok), `MA_*` (meghatalmazás),
  `CSOK*`, `AFA*`. The pipeline routes these through the `-társ` suffix rule.
- `participant.role`, `participant.gender`, `participant.citizenship`,
  `participant.marital_status`, `participant.id_document_type`,
  `participant.employment_type`, `participant.address.country`,
  `participant.mailing_address_same`, etc. are **checkbox groups** — values are
  not text-filled; they would be ticked via the AcroFormFiller `/Btn` path when
  the canonical value is truthy. The pipeline currently skips
  `participant.role` entirely (`continue` on that canonical).
- Auxiliary flat forms rely on **text-anchor recognition**: the recognizer
  finds dotted lines `……` in the PDF text spans, computes precise `(x, y,
  width, height)` coordinates, and Claude maps each label to a canonical field.
  The `-3 px` vertical correction in `_fill_pdf` is what makes overlay text sit
  *above* the dotted line.

---

## 9. Document-agnostic design

There is **no `DOCUMENT_AGNOSTIC.md` file in this repo** at commit `c20292e`.
The repository's only markdown is `README.md`. The "document-agnostic"
philosophy the task references is instead encoded **directly in the code and
commits** (notably `cb0df8a` "integrate DocumentAssembler directly into
FormFillerPipeline for automatic Master PDF slicing" and
`c20292e` "critical + warning fixes from code review"):

1. **Master detection by behavior, not filename** —
   `DocumentAssembler.is_master_pdf` tests the page count (`>= 97`), so the
   pipeline doesn't care *which* document it received; it auto-slices any large
   OTP-style master. The previous magic-number `page_count == 97` check was
   replaced by the threshold check (see the comment at `main.py:130`).

2. **Role-based routing, not positional indexing** — instead of "the first
   participant is the borrower", the pipeline uses `deal.borrowers` /
   `deal.co_borrowers` and OTP's `-társ` field-name convention. Any active
   participant whose first BORROWER role is automatically the igénylő; the rest
   become CO_BORROWER. This is documented in `main._prepare_field_data`
   comments as "dokumentum-agnosztikus marad a logika".

3. **Mapping-driven, not form-driven** — every PDF is filled purely from its
   `MappingConfig`. New document types don't require code changes; only a new
   JSON mapping (hand-authored or AI-generated). The same engine fills a
   3-page sample, a 97-page master, a flat partner declaration, etc.

4. **Unsupported documents fall through gracefully** — if a mapping has no
   fields (`MappingConfig(_empty_mapping)`), the fill still runs but produces an
   unfilled PDF; issues are logged via `FillingResult.skipped_fields` /
   `errors` and surfaced in `result["issues"]`. If a PDF is below the master
   threshold and has no AcroForm, `AcroFormFiller` returns an error result and
   the pipeline records `"PDF kitöltési hiba: ..."` without crashing.

5. **Heuristic normalization of field names** — `_normalize_key` (NFD + strip
   combining marks + lowercase) makes Hungarian accented field names
   (`név`, `állandó_lakcím`) match regardless of the PDF generator's encoding
   or case. This was added in commit `c20292e` as fix 2b.

So "document-agnostic" here means: **the same pipeline handles any OTP PDF
(AcroForm or flat, master or standalone, known or new) by (a) detecting the
master by page count, (b) slicing via product/participant/property counts,
(c) routing fields by role and `-társ` suffix, and (d) filling via a
per-document mapping that can be regenerated by the AI onboarding layer.**

---

## 10. AI integration

`src/ai/field_recognizer.py` is the **onboarding layer**. It never touches
customer PII — it only sees *empty* PDFs.

### Model

```python
AI_MODEL = "claude-sonnet-4-20250514"
```

A single module-level constant (commit `c20292e` fix 2a consolidated four
hard-coded `"claude-sonnet-4-6"` strings into one). The README still mentions
"Claude Sonnet 4.6"; the tech spec PDF says "Claude Sonnet 4.6". The
`anthropic` SDK is initialized lazily inside `FieldRecognizer.__init__` only if
`ANTHROPIC_API_KEY` is set; otherwise the recognizer runs heuristics only.

> Note on "DeepSeek": the task description mentions "DeepSeek usage" and a
> "legal classifier" mentioned in commits. **Neither exists in the code at
> commit `c20292e`.** The full `git log --oneline` shows six commits
> (`1abe78f`, `f2550fb`, `2717233`, `292d0a1`, `cb0df8a`, `c20292e`); none of
> them reference DeepSeek or a legal classifier, and no such code, import, or
> commit message is present. All AI is Anthropic Claude. (The first commit
> message does say "AI (Claude Sonnet 4.6) kizárólag új nyomtatványok
> mezőfelismerésére szolgál" — i.e. field recognition only.)

### Recognition modes

`FieldRecognizer.recognize(pdf_path, mode)` with `mode ∈ {auto, acroform,
overlay}`:

- **auto** — peeks for `/AcroForm/Fields`; if present uses `recognize_acroform`,
  otherwise `recognize_flat`.
- **acroform** — `_extract_acroform_fields` (pikepdf) gathers `{name, type,
  value, page}` for every field; then:
  1. `_heuristic_map_fields` always produces a baseline mapping using
     `OTP_EXACT_MAP` (normalized keys) + `KEYWORD_MAP`.
  2. If `ANTHROPIC_API_KEY` is set:
     - `<= 200` fields → single `_ai_map_fields` call (sends up to 20 page
       images at 150 DPI + all field names + a system prompt that lists the
       canonical catalog and demands strict JSON).
     - `> 200` fields → `_ai_batch_recognize` groups fields by OTP prefix
       (`SZA_IG_`, `CSOK_`, `TA_`, `AFA`, `IA_`, `KTKA/LAHI/LAKHK`,
       `A_NY_/ANY_`, `MA_`, `videk/otthon`), then sends up to 80 fields per
       sub-batch with at most 5 relevant page images, 1 s sleep between calls.
       Compact response schema `{"f","c","t","p"}` to save tokens.
  3. `_merge_mappings` fuses heuristic + AI (AI overrides when it produces a
     canonical).
- **overlay / flat** — `recognize_flat`:
  1. `_extract_text_anchors` walks every text span via PyMuPDF and detects
     `……` (≥2 ellipses). For each it computes pixel-precise `fill_x` (start of
     dots), `fill_y`, `width`, `font_size`, and a 5-span surrounding context.
     Label is chosen from a following italic span (e.g. "(Hiteligénylő neve)"),
     the pre-text (e.g. "Név:"), or the post-text.
  2. `_ai_map_flat_labels` sends all labels + context to Claude as plain text
     (no images) and asks for `{"field_id": "canonical.field", ...}`.
  3. Builds `RecognizedField`s with confidence `HIGH` when mapped, `LOW` when
     not. If no anchors were found and Claude is unavailable, falls back to
     `_mock_flat_recognition` (searches for known Hungarian labels and places
     coordinates to the right of the label rect).

### Output

A `MappingConfig` serialized with `MappingConfig.save(path)` into
`src/mapping/<pdf_stem>_mapping.json`. The CLI also prints a color-coded
summary via `print_mapping_summary`.

### Canonical catalog shown to the model

`CANONICAL_FIELDS` is a fixed dict of ~37 canonical paths with Hungarian
descriptions (e.g. `"participant.tax_id": "Adóazonosító jel"`), embedded into
the system/user prompts. This keeps Claude's output vocabulary constrained to
known canonical fields.

### JSON extraction

`_extract_json` handles ```` ```json ... ``` ````, bare ``` ``` ````,
balanced-brace scanning, and a last-resort first-`{`/last-`}` fallback.

---

## 11. Salesforce integration

`src/integrations/salesforce_client.py` provides `SalesforceClient`, a
dual-mode client.

### Modes

- **Mock mode** (`mock_mode=True`, default): loads every JSON in
  `samples/dummy_data/` into an in-memory `MockSalesforceState.deals` dict.
  `get_deal`, `list_deals`, `update_stage`, `attach_pdf`, `get_attachments` all
  operate on this in-memory state. This is what the tests and the default
  `python -m src.main` run use.
- **Live mode** (`mock_mode=False`): uses `simple-salesforce`
  (`Salesforce(username=…, password=…, security_token=…, domain="test")`).
  Invoked when you pass `--salesforce` to `main.py`.

### Live data model (Salesforce objects/fields)

`get_deal(deal_id)` issues **parameterized SOQL** (`:variable` binding via
`self._sf.query(query_str, **kwargs)` — the driver escapes values, preventing
SOQL injection; commit `f2550fb` added this):

1. **Opportunity** (`self._sf.Opportunity.get(deal_id)`). Relevant fields:
   - `Opportunity_Contact_Name__c` → adós
   - `First_Co_debtor__c`, `Second_Co_debtor__c`, `Third_Co_debtor__c` → adóstárs 1/2/3
   - `Mortgagor__c` → kezes
   - `Usufructuary__c` → haszonélvező
   - `Hitel_sszeg__c` / `Amount` → loan amount
   - `Hitelc_l__c` → loan purpose
   - `Term_k__c` → product name
   - `StageName`, `CreatedDate`, `Description`, `remark__c`

2. **Contact** (one SOQL `WHERE Id IN :contact_ids` for all 6 lookup IDs).
   Fields: `Name, FirstName, LastName, Szuletesi_nev__c, Mother_s_Name__c,
   Place_of_Birth__c, Date_of_birth__c, ID_Card_Number__c, Tax_ID__c,
   Address_Card_Number__c, Permanent_address__c, Phone, Email,
   Name_of_employer__c, Average_monthly_net_income__c, Term_in_year_c__c,
   Highest_Educational_Qualification__c, Marital_Status__c, Dependents_count__c,
   Current_employment_started__c, ZIP__c`. The free-text `Permanent_address__c`
   is parsed into structured components by `_parse_address_string` (regex for
   4-digit zip, city vs street split by comma/space, house number
   `\d+[\w\-/]*`, optional floor `N. em`/`emelet` and door `N. aj`/`ajto`/`ajtó`).

3. **Opportunity_Property_Role__c** (junction table) —
   `SELECT Property__c, Ingatlan_szerepe__c FROM Opportunity_Property_Role__c
   WHERE Opportunity__c = :deal_id`.

4. **Property__c** (`WHERE Id IN :prop_ids`). Fields: `Name, Property_Type__c,
   Ingatlan_hrsz__c, Ingatlan_alapterulet__c, Property_value__c,
   Purchase_price__c, Ingatlan_irsz__c, Ingatlan_telepules__c,
   Ingatlan_kozterulet_neve__c, Ingatlan_Kozterulet_jellege__c,
   Ingatlan_hazszam__c, Ingatlan_emelet__c`. Street is composed by joining
   `Ingatlan_kozterulet_neve__c` + `Ingatlan_Kozterulet_jellege__c`.

5. **Loan term** is derived from the first contact's `Term_in_year_c__c`
   (years × 12), defaulting to 240 months.

The assembled normalized dict has the same shape the `DataNormalizer`
expects (keys like `Amount__c`, `Loan_Term__c`, `Participants__r.records`,
`Properties__r.records`).

### Write-back

- `attach_pdf` → creates a Salesforce **ContentVersion** with base64-encoded
  PDF bytes and `FirstPublishLocationId = deal_id` (Salesforce Files).
- `update_stage` → `Opportunity.update(deal_id, {"StageName": new_stage})`.
  The pipeline uses `"Dokumentáció kész – review"`.

### Trigger model

Per the tech spec, the flow is triggered either by an Opportunity stage change
or a button on the Opportunity (orchestration is expected to live in **n8n** in
production; this PoC exposes a CLI).

---

## 12. Configuration

### Environment variables (`config/.env`, see `.env.example`)

| Var | Purpose | Default |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude API for field recognition (onboarding only) | `""` |
| `SF_USERNAME` | Salesforce sandbox username | `""` |
| `SF_PASSWORD` | Salesforce sandbox password | `""` |
| `SF_SECURITY_TOKEN` | Salesforce security token | `""` |
| `SF_DOMAIN` | Salesforce domain (`test` = sandbox) | `test` |
| `LOG_LEVEL` | Python logging level | `INFO` |
| `OUTPUT_DIR` | (documented in `.env.example`; not actually read by `settings.py`) | — |

### `config/settings.py`

- Loads `config/.env` via `python-dotenv`.
- Exposes `ANTHROPIC_API_KEY`, `SF_USERNAME/PASSWORD/SECURITY_TOKEN/DOMAIN`,
  `LOG_LEVEL`.
- Defines path constants `PROJECT_ROOT`, `CONFIG_DIR`, `OUTPUT_DIR`,
  `SAMPLES_DIR`, `MAPPINGS_DIR` and ensures `OUTPUT_DIR` / `SAMPLES_DIR`
  exist.

### CLI (`python -m src.main`)

```
--scenario, -s      dummy scenario (simple|multi_borrower|multi_property|complex|missing_data)
--recognize, -r PATH    run AI field recognition on this PDF first
--template, -t PATH     PDF template to fill (else auto-pick samples/acroform_sample.pdf)
--mapping, -m PATH      mapping JSON (else first src/mapping/*_mapping.json)
--output-dir, -o PATH
--salesforce, -sf       use real Salesforce sandbox (requires SF_* env)
--deal, -d ID           explicit Salesforce Opportunity ID
```

If dummy data is missing it auto-runs `generate_dummy_data.generate_all_scenarios`.
If no PDF template exists it runs a normalizer-only demo printing the deal.

### Dependencies (`requirements.txt`)

`pikepdf>=8.0.0`, `reportlab>=4.0`, `PyMuPDF>=1.23.0`, `pydantic>=2.0`,
`anthropic>=0.30.0`, `simple-salesforce>=1.12.0`, `python-dotenv>=1.0.0`,
`pytest>=8.0`, `pytest-cov>=5.0`.

---

## 13. Test coverage

Tests live in `tests/`; run with `pytest tests/ -v --cov=src` (per README).

| File | Covers | Status |
|---|---|---|
| `test_canonical_model.py` | `Address.full_address`, `Participant.is_borrower`, role enum values, `DealData.active_participants`/`borrowers`/inactive filtering, `LoanDetails` required/all fields, `PropertyType` values, JSON round-trip (`model_dump(mode="json")` ↔ `model_validate`) | ~15 tests, all unit |
| `test_normalizer.py` | Canonical-JSON normalization, participant/property normalization, inactive filtering, date parsing (`%Y-%m-%d`, `%Y.%m.%d`, None, date obj, invalid), `_safe_int`/`_safe_float` | ~13 tests |
| `test_document_assembler.py` | Page-plan math for all `ProductType` combos, participant/property multiplicity, section de-duplication, `ProductType` enum coverage | ~13 tests |
| `test_integration.py` | End-to-end `run_for_deal` with mock SF + acroform sample + OTP mapping; mock `list_deals`/`update_stage`; `FieldRecognizer` heuristic AcroForm/auto/flat modes; `MappingConfig` load/dict/save-load round-trip/high-confidence filter | ~9 tests (some skip if samples missing) |

### What is covered

- The canonical model and its serialization.
- The normalizer's two input formats and edge cases.
- The document assembler's deterministic page math (the most rigorously tested
  component).
- A happy-path end-to-end fill on generated samples.
- Mapping config I/O and AI recognizer heuristic mode (no API key needed).

### What is NOT covered (gaps)

- **No tests for `pdf_filler.py`** (`AcroFormFiller`, `OverlayFiller`,
  `FillingResult`, `_make_readonly`, `list_fields`). The integration test
  exercises them transitively but there are no isolated assertions on
  `filled_fields`/`skipped_fields`/`errors`/checkbox handling/overlay merge.
- **No tests for `completeness_checker.py`** — `FormFillerPipeline._check_completeness`
  is wired in but the `CompletenessChecker` itself (MISSING/EMPTY/SUSPICIOUS,
  structural checks, wildcard expansion) has zero unit tests. The
  `missing_data` dummy scenario exists but is never asserted on.
- **No tests for `role_instance_logic.py`** — `RoleInstancePlanner`,
  `PageScope`, `PageConfig`, `FormStructure`, `GenerationPlan` are fully
  implemented but never used by `main.py` (which uses `DocumentAssembler`
  instead) and have no tests.
- **No live Salesforce tests** (expected; sandbox-only).
- **No AI tests** (Claude not called in CI).
- **No tests for the flat/overlay path on real OTP auxiliary forms** — the
  overlay correction `y = coords.y + height - 3` is untested.
- **`scripts/test_otp_fill.py`** is a manual demo, not a pytest; it depends on
  the real 97-page OTP PDF being present.

---

## 14. Quick start (developer cheat-sheet)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 1. Generate the sample PDFs (AcroForm + flat) and dummy deals
python scripts/generate_sample_pdfs.py
python scripts/generate_dummy_data.py

# 2. Run the default pipeline (mock Salesforce, acroform_sample.pdf)
python -m src.main

# 3. Try other scenarios
python -m src.main --scenario complex
python -m src.main --scenario missing_data      # observe completeness warnings

# 4. AI field recognition on a fresh empty PDF (needs ANTHROPIC_API_KEY)
python -m src.ai.field_recognizer samples/acroform_sample.pdf

# 5. Fill the real 97-page OTP master (manual demo)
python scripts/test_otp_fill.py

# 6. Tests
pytest tests/ -v --cov=src
```

---

## 15. Commit history (for context)

```
c20292e fix: critical + warning fixes from code review
        → consolidated Claude model constant (2a), NFD-based field-key
          normalization (2b), removed magic-number master detection.
cb0df8a feat: integrate DocumentAssembler directly into FormFillerPipeline
             for automatic Master PDF slicing
292d0a1 style: shift overlay text 3 pixels upwards to sit neatly above the line
2717233 fix: resolve overlay filler bug where it looked up fields using
             canonical_field instead of pdf_field_name
f2550fb feat: complete Salesforce Sandbox real integration, custom schema
             mapping, and address parser
1abe78f initial commit: FinancialGenie Banki nyomtatvanykitolto PoC
```

This history explains the layering: a PoC scaffold (`1abe78f`) → real SF +
address parsing (`f2550fb`) → overlay bug fixes (`2717233`, `292d0a1`) →
auto-slicing integration (`cb0df8a`) → review hardening (`c20292e`).

---

*End of `HOW_IT_WORKS.md`.*

# Technical Plan — Project-Based Document Upload

**Project:** FinancialGenie Mapping Studio
**Author:** OpenCode (Architecture Analysis)
**Date:** 2026-07-10
**Status:** Draft for client review (rev 3 — master split is now fully automatic per client decision)

---

## 1. Executive Summary

The FinancialGenie Mapping Studio currently uses a **flat document upload model**: a single PDF is dropped into `samples/`, the AI resolves its field mapping, and the pipeline fills it. There is no notion of *which bank* or *which product* a document belongs to.

Balázs needs a **project-based hierarchy** reflecting the real OTP Google Drive layout:

```
Bank (OTP)
└── Bank Product
    ├── Előzetes értékbecslés megrendelés   (Preliminary property valuation)
    ├── Szabadfelhasználású hitel            (Free-use mortgage)
    ├── Otthon Start                          (Green home loan for youth)
    └── Piaci hitel                           (Market-rate mortgage)
        └── PDF documents (standalone forms + split pages of the master)
```

A document can belong to **multiple products** (M:N). For example `Partner_nyilatkozat_hiteligeny_leadasakor.pdf` appears in all 4 product folders; `V_szamu_fuggelek_…` appears in 3. The large MASTER PDF (`Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf`, ~2 MB) is **duplicated across 3 folders** but must **never be uploaded as-is** — it must be split into individual pages/sections with base sections shared across products and product-specific sections scoped.

Two new requirements from Balázs drive this revision:
1. **Multi-applicant (adóstárs) handling** — some forms are filled once *per applicant*. The catalog must tag each document with `per_applicant`, and the fill pipeline must fetch N copies and fill N times when there are co-applicants.
2. **Section-aware master split (fully automatic)** — the master PDF has base sections (shared by all products) and product-specific sections; uploading the master triggers an automatic, asynchronous split that assigns pages to the right products from the section map, with no manual dialog.

This plan introduces a **document catalog** (a lightweight JSON manifest) that models the Bank → Product → Document hierarchy with many-to-many relationships and per-applicant metadata, while keeping the existing file-based architecture intact. No database migration is required for the PoC; the design is forward-compatible with SQLite if needed later.

---

## 2. Current State Analysis

### 2.1 Storage Model (no database)

The codebase is **entirely file-based** — there is no database:

| Artifact | Location | Notes |
|---|---|---|
| PDF templates | `otp/`, `samples/` | Walked recursively by `config.list_pdfs()` |
| Field mappings | `src/mapping/*.json` | One JSON per form; resolved by `mapping_path_for()` |
| Filled output | `output/` | Generated PDFs from the fill pipeline |
| Master PDF | repo root (`OTP_Igenylesi_Dokumentumok_v5.pdf`) | 97 pages, split by `DocumentAssembler` |

`backend/config.py:34` defines:
```python
PDF_ROOTS = [PROJECT_ROOT / "otp", PROJECT_ROOT / "samples"]
```
`list_pdfs()` (config.py:233) already walks these recursively and returns a flat list of `{pdf_id, name, size_bytes, parent}`. The `parent` field exposes the subdirectory, but the frontend ignores it — `UploadStep.tsx` renders a flat list.

### 2.2 The Hierarchy Is Already Half-Built

The backend **already anticipates** the OTP product hierarchy, even though the real folder layout differs from the enum:

- `mapping_path_for()` (config.py:65) has **cross-directory alias logic** (step 4, line 204): if the same filename appears in a sibling OTP product folder, it reuses that sibling's mapping. This implies the authors expected `otp/Piaci hitel/` and sibling folders with shared forms.
- `DocumentAssembler` (`src/engine/document_assembler.py`) defines an `OTP ProductType` enum and section maps referencing master-PDF page ranges per product — but the enum lists `CSOK`, `CSOK Plusz`, `Otthon Start`, etc., which **does not match the real Drive** (no CSOK folders; instead `Szabadfelhasználású hitel` and `Előzetes értékbecslés megrendelés`). This enum must be reconciled with reality (§7).
- The **`otp/` directory does not exist yet** — it was removed in commit `95eb087`.

So the backend supports hierarchy in principle; the **frontend, upload flow, and the product enum are the gaps**.

### 2.3 Upload Flow (current)

`POST /api/pdf/upload` (server.py:677) does the following in one synchronous call:

1. Sanitize filename (NFKD transliteration, collision-safe suffix).
2. Save to `samples/<safe_name>.pdf` (flat — no subdirectory).
3. Auto-run `FormFillerPipeline.run_for_deal()` which resolves/creates a mapping via AI and fills the PDF.
4. Return `filled_pdf_url`.

The frontend `UploadStep.tsx` is a drag-and-drop zone + flat list of existing PDFs. There is no way to choose a destination product or bank, and **no concept of multiple applicants**.

### 2.4 Frontend Architecture

```
App.tsx
└── MappingStudio.tsx          ← wizard orchestrator (5 steps)
    ├── UploadStep.tsx         ← flat upload + flat PDF list   ← MAIN CHANGE TARGET
    ├── AnalysisStep.tsx       ← AI recognition polling
    ├── ReviewDashboard.tsx    ← mapping review heatmap
    │   ├── PageEditor.tsx     ← per-page field mapping
    │   └── PointsEditor.tsx   ← checkbox group / points editor
    ├── LockStep.tsx           ← approval gate
    └── FillPreviewStep.tsx    ← fill preview with deal data   ← AFFECTED BY per_applicant
```

**State management:** Zustand store (`store.ts`) holds `pdfs[]`, `activePdfId`, `mapping`, `pdfFields`, etc. There is **no router**; navigation is purely `useState`-driven within `MappingStudio`.

### 2.5 The Multi-Project Constraint (real Drive evidence)

The real OTP Drive proves the M:N requirement concretely:

| Document | Értékbecslés | Szab.hitel | Otthon Start | Piaci hitel |
|---|:---:|:---:|:---:|:---:|
| `Partner_nyilatkozat_hiteligeny_leadasakor.pdf` | ✓ | ✓ | ✓ | ✓ |
| `V_szamu_fuggelek_Penzugyi_szolgaltatas_…_nyilatkozat_20250601.pdf` | — | ✓ | ✓ | ✓ |
| `Igenylesi_dokumentumok_OTP_…_v5.pdf` (MASTER) | — | ✓ | ✓ | ✓ |
| `Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104.pdf` | ✓ | — | — | — |
| `Hozzajarulo_nyilatkozat_munkaviszony_es_jovedelemadatok_ellenorzesehez.pdf` | — | ✓ | — | — |
| `Zold_Lakashitel_Fiataloknak_Nyilatkozat_elso_lakastulajdonrol_20251201.pdf` | — | — | ✓ | — |
| `Igazolas_CSOK_AFA_kamattamogatasi_kerelem_atvetelerol_20250901.pdf` | — | — | ✓ | — |

A naive folder hierarchy would **duplicate files and mappings** — exactly what the existing cross-directory alias code tried to avoid. The MASTER PDF being duplicated in 3 folders is a clear anti-pattern the catalog must eliminate: store once, reference many.

---

## 3. Proposed Architecture

### 3.1 Design Principle: Catalog Overlay on File Hierarchy

We keep PDFs on disk (no DB required) but introduce a **document catalog** — a single JSON manifest that owns the logical hierarchy, the many-to-many product↔document associations, and the per-applicant metadata. The physical files live in a content-organized store.

```
┌─────────────────────────────────────────────────┐
│            Document Catalog (JSON)              │  ← single source of truth
│  banks → products → document associations       │
│  + per_applicant flags + master-split provenance│
└──────────────────────┬──────────────────────────┘
                       │ references
┌──────────────────────▼──────────────────────────┐
│         Physical Document Store                  │
│  documents/<bank_slug>/<doc_id>.pdf              │  ← deduplicated by content
│  (mappings resolve via existing mapping_path_for)│
└─────────────────────────────────────────────────┘
```

**Why JSON catalog over SQLite?** Consistent with the existing architecture (everything is JSON + files); zero-migration cost for the PoC; human-readable and git-friendly; forward-compatible (can port to SQLite later without changing the API contract).

**Why not pure folders?** Multi-project documents cannot be expressed with folders alone — a file can only live in one directory. The catalog decouples **logical grouping** from **physical storage**.

### 3.2 Physical Directory Structure

```
financialgenie_review/
├── documents/                      ← NEW: physical document store (downloaded from Drive)
│   └── otp/                        ← per-bank
│       ├── base/                   ← shared/base forms (multi-product) + master base sections
│       │   ├── partner_nyilatkozat_hiteligeny_leadasakor.pdf
│       │   ├── v_szamu_fuggelek_penzugyi_szolgaltatas_nyilatkozat.pdf
│       │   └── _master_sections/base/   ← split base sections of the master
│       ├── elozetes_ertekbecsles/        ← "Előzetes értékbecslés megrendelés"
│       │   └── igenylesi_dokumentum_elozetes_ertekbecsleshez.pdf
│       ├── szabadfelhasznalasu_hitel/    ← "Szabadfelhasználású hitel"
│       │   ├── hozzajarulo_nyilatkozat_munkaviszony_jovedelem_ellenorzes.pdf
│       │   └── _master_sections/         ← product-specific split sections
│       ├── otthon_start/
│       │   ├── zold_lakashitel_fiataloknak_elso_lakastulajdon_nyilatkozat.pdf
│       │   ├── igazolas_csok_afa_kamattamogatasi_kerelem_atvetel.pdf
│       │   └── _master_sections/
│       └── piaci_hitel/
│           └── _master_sections/
├── _master/                        ← the source master PDF (git-ignored, not a template)
│   └── Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf
├── catalog/
│   └── document_catalog.json       ← NEW: hierarchy + associations + per_applicant
├── src/mapping/                    ← existing mappings (unchanged)
└── samples/                        ← legacy uploads (kept for backward compat)
```

### 3.3 Seeding From Google Drive

The real Drive folder **is the seed data**. The initial catalog must be populated by downloading the Drive contents into `documents/`:

1. **Download** each product folder from Drive preserving structure, OR flatten into `documents/otp/<product_slug>/`.
2. **Deduplicate** identical files by SHA-256: the MASTER PDF appears 3× — store one copy in `_master/`, reference it. `Partner_nyilatkozat_…` appears 4× — store once in `base/`, associate to all 4 products.
3. **Run `scripts/seed_catalog.py`** to scan `documents/otp/`, hash files, and emit the catalog JSON with correct `product_ids` associations inferred from the folder each unique file was found in.
4. **Tag `per_applicant`** during seeding based on a curated override table (§4.2) — the default heuristic is: any document whose title contains "személyi adatlap", "nyilatkozat" + applicant-identity keywords, or income/employment consent → `per_applicant: true`.

> **Note on the MASTER PDF:** it is downloaded **once** into `_master/` (git-ignored). It is **never** a catalog document itself. The split endpoint reads from it to produce base/product section PDFs that *do* become catalog documents.

The existing `otp/` and `samples/` roots remain valid (`config.PDF_ROOTS`). The new `documents/` root is added. `mapping_path_for()` continues to work because it resolves by signature/word-match, not by directory.

---

## 4. Data Model — Document Catalog Schema

The catalog is a single JSON file: `catalog/document_catalog.json`.

```jsonc
{
  "version": 2,
  "banks": [
    {
      "id": "otp",
      "name": "OTP Bank",
      "products": [
        { "id": "elozetes_ertekbecsles",  "name": "Előzetes értékbecslés megrendelés",
          "document_ids": ["doc_partner_nyilatkozat", "doc_ertekbecsles_igenyles"] },
        { "id": "szabadfelhasznalasu",    "name": "Szabadfelhasználású hitel",
          "document_ids": ["doc_partner_nyilatkozat", "doc_v_szamu_fuggelek",
                           "doc_hozzajarulo_munkaviszony", "doc_master_base_*"] },
        { "id": "otthon_start",           "name": "Otthon Start",
          "document_ids": ["doc_partner_nyilatkozat", "doc_v_szamu_fuggelek",
                           "doc_zold_lakashitel_elso_lakas", "doc_csok_afa_igazolas",
                           "doc_master_base_*", "doc_master_otthon_start_*"] },
        { "id": "piaci_hitel",            "name": "Piaci hitel",
          "document_ids": ["doc_partner_nyilatkozat", "doc_v_szamu_fuggelek",
                           "doc_master_base_*", "doc_master_piaci_*"] }
      ]
    }
  ],
  "documents": [
    {
      "id": "doc_partner_nyilatkozat",
      "title": "Partner nyilatkozat hiteligény leadásakor",
      "file_path": "documents/otp/base/partner_nyilatkozat_hiteligeny_leadasakor.pdf",
      "source": "drive:OTP/Előzetes értékbecslés megrendelés",
      "product_ids": ["elozetes_ertekbecsles", "szabadfelhasznalasu", "otthon_start", "piaci_hitel"],
      "page_count": 1,
      "per_applicant": true,
      "tags": ["base", "declaration"],
      "uploaded_at": "2026-07-10T12:00:00"
    },
    {
      "id": "doc_hozzajarulo_munkaviszony",
      "title": "Hozzájáruló nyilatkozat – munkaviszony és jövedelemadatok ellenőrzése",
      "file_path": "documents/otp/szabadfelhasznalasu_hitel/hozzajarulo_nyilatkozat_munkaviszony_jovedelem.pdf",
      "source": "drive:OTP/Szabadfelhasználású hitel",
      "product_ids": ["szabadfelhasznalasu"],
      "page_count": 1,
      "per_applicant": true,
      "tags": ["consent", "income"]
    },
    {
      "id": "doc_master_base_p02",
      "title": "Master – Személyi adatlap (base section, page 2)",
      "file_path": "documents/otp/base/_master_sections/base/szemelyi_adatlap_p02.pdf",
      "source": "split:Igenylesi_dokumentumok_OTP_…_v5.pdf:page 2",
      "product_ids": ["szabadfelhasznalasu", "otthon_start", "piaci_hitel"],
      "page_count": 1,
      "split_from_master": true,
      "master_section": "base",
      "master_page_number": 2,
      "per_applicant": true,
      "tags": ["base", "personal_data"]
    },
    {
      "id": "doc_master_otthon_start_p69",
      "title": "Master – Otthon Start nyilatkozat (product section, page 69)",
      "file_path": "documents/otp/otthon_start/_master_sections/otthon_start_p69.pdf",
      "source": "split:Igenylesi_dokumentumok_OTP_…_v5.pdf:page 69",
      "product_ids": ["otthon_start"],
      "page_count": 1,
      "split_from_master": true,
      "master_section": "otthon_start",
      "master_page_number": 69,
      "per_applicant": false,
      "tags": ["product_specific"]
    }
  ]
}
```

### 4.1 Key Fields

| Field | Purpose |
|---|---|
| `documents[].id` | Stable unique ID (slug-based). |
| `documents[].file_path` | Relative path to the physical PDF. |
| `documents[].source` | Provenance — `drive:<folder>` \| `upload:<name>` \| `split:<master>:page N`. |
| `documents[].product_ids` | **M:N association** — which products reference this doc. Drives the tree. |
| `documents[].per_applicant` | **NEW.** `true` = must be filled once per applicant (igénylő + each adóstárs); `false` = once per application. Drives fill fan-out (§5.5). |
| `documents[].split_from_master` / `master_page_number` / `master_section` | Marks split-page documents; `master_section` is `"base"` or a product slug. Enables section-aware re-assembly. |
| `banks[].products[].document_ids` | Redundant inverse index for efficient tree rendering. |

The M:N relationship is modeled in **both directions** (document → products, product → documents). The catalog service keeps them in sync on writes.

### 4.2 Per-Applicant Tagging Guide

`per_applicant` is set during seeding via a curated override table and editable in the UI. Defaults for the known OTP documents:

| Document | `per_applicant` | Rationale |
|---|:---:|---|
| `Partner_nyilatkozat_hiteligeny_leadasakor.pdf` | `true` | A personal declaration per applicant |
| `Hozzajarulo_nyilatkozat_munkaviszony_…` | `true` | Income/employment consent is individual |
| Master base: Személyi adatlap pages | `true` | Personal data sheet — one per applicant |
| `V_szamu_fuggelek_Penzugyi_szolgaltatas_…` | `false` | Intermediary declaration — once per application |
| `Zold_Lakashitel_…_elso_lakastulajdon_nyilatkozat.pdf` | `true` | First-home-owner declaration — per applicant |
| `Igazolas_CSOK_AFA_…_atvetel.pdf` | `false` | Subsidy handover confirmation — per application |
| Master: Fedlap / cover page | `false` | One cover per application |
| `Igenylesi_dokumentum_elozetes_ertekbecsleshez.pdf` | `false` | Valuation order — per application/property |

The heuristic for unknown uploads: title contains "személyi adatlap", "jövedelem", "munkaviszony", or "nyilatkozat" + a person-identity keyword → `true`; otherwise `false`. Always user-editable.

---

## 5. Backend API Changes

### 5.1 New Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/catalog` | Return full catalog (banks → products → documents). Drives the tree. |
| `GET` | `/api/catalog/banks` | List banks. |
| `POST` | `/api/catalog/banks` | Create a new bank. |
| `POST` | `/api/catalog/banks/{bank_id}/products` | Create a new product folder. |
| `POST` | `/api/documents/upload` | **Upload a document into product(s)** (replaces `/api/pdf/upload` for new flow). |
| `PUT` | `/api/documents/{doc_id}/products` | Associate a document with product(s) (M:N). |
| `PUT` | `/api/documents/{doc_id}` | Update document metadata — incl. toggling `per_applicant`. |
| `DELETE` | `/api/documents/{doc_id}/products/{product_id}` | Remove association (keeps file if referenced elsewhere). |
| `POST` | `/api/documents/split-master` | **Automatic master PDF split** into base + product sections (§5.3). Async, returns `task_id`. |
| `DELETE` | `/api/documents/{doc_id}` | Delete document (file + catalog entry + mapping). |

### 5.2 Upload Endpoint (new signature)

`POST /api/documents/upload` (multipart):

```
file:              <PDF binary>
bank_id:           otp
product_ids:       szabadfelhasznalasu,piaci_hitel     (comma-separated; ≥1)
title:             "Partner nyilatkozat"                (optional)
per_applicant:     true                                  (optional; default inferred by heuristic)
auto_fill:         false                                 (skip immediate AI fill)
auto_split_master: true                                  (default; if a master PDF is detected, trigger the automatic async split — §5.3)
```

> **Master auto-detection at upload:** when `auto_split_master` is `true` (the default), the upload endpoint inspects the file **before** creating a normal catalog entry. If it is detected as a master (filename matches the pattern `Igenylesi_dokumentumok_OTP_*` **OR** page count exceeds the configured threshold, e.g. > 50 pages), the upload stores the file under `_master/` and returns a `task_id` for the asynchronous split instead of a regular document. The frontend shows a progress indicator (§6.1) and the resulting split documents appear in the catalog tree when the task completes.

### 5.3 Master PDF Split Endpoint — Fully Automatic & Asynchronous

> **Client decision (Balázs):** master splitting is **fully automatic**. There is no manual dialog. The user uploads the master PDF and the system handles detection, section mapping, splitting, and product assignment. The source of truth for the page-to-product mapping is the reconciled `DocumentAssembler.PRODUCT_SECTIONS` and `BASE_SECTIONS` (§7.2).

The master PDF is **not homogeneous**. It contains **base sections** (shared by all 3 products that use the master: Szabadfelhasználású, Otthon Start, Piaci hitel) and **product-specific sections** (scoped to one product). The split endpoint understands this — but it derives the mapping **from the section map**, not from user input.

#### 5.3.1 Detection

Master detection happens at **upload time** (§5.2) when `auto_split_master: true`. A file is treated as a master if it satisfies either:

- **Filename pattern** — matches `Igenylesi_dokumentumok_OTP_*` (case-insensitive, accent-insensitive), **or**
- **Page-count heuristic** — page count > `MASTER_PAGE_THRESHOLD` (default `50`) **and** the filename matches the naming pattern.

Both conditions guard against false positives (a 90-page unrelated form should not trigger a split).

#### 5.3.2 The Split Job (Asynchronous)

`POST /api/documents/split-master`:

```jsonc
// Request — minimal: the section map is NOT supplied by the client; it is read from DocumentAssembler.
{
  "master_pdf_path": "_master/Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf",
  "bank_id": "otp"
  // section_map is derived server-side from DocumentAssembler.PRODUCT_SECTIONS / BASE_SECTIONS (§7.2)
}
```

```jsonc
// Response (immediate) — 202 Accepted
{
  "task_id": "split_a1b2c3",
  "status": "running",
  "total_pages": 97,
  "processed_pages": 0
}
```

The endpoint returns a `task_id` immediately and runs the split **in the background** (mirroring the existing AI-recognition polling pattern). Progress is polled via `GET /api/documents/split-master/{task_id}`:

```jsonc
// Poll response
{
  "task_id": "split_a1b2c3",
  "status": "running",            // running | completed | failed
  "total_pages": 97,
  "processed_pages": 27,          // → "Master PDF feldolgozása: 27/97 oldal…"
  "created_doc_ids": [],          // populated as pages are written
  "error": null
}
```

A 97-page PDF can take 5–10 seconds (PyMuPDF ~50 ms/page plus catalog writes), which is why the job **must** be asynchronous — never blocking the upload request.

#### 5.3.3 What the Split Job Does

1. Opens the master PDF with PyMuPDF.
2. Reads the reconciled `DocumentAssembler.PRODUCT_SECTIONS` (product-specific page ranges) and `BASE_SECTIONS` (shared page ranges) as the **single source of truth** for page → section → product assignment.
3. For each page, determines its `master_section` (`"base"` or a product slug) and the resulting `product_ids`:
   - **Base section** pages → `product_ids` = **all** master-using products (Szabadfelhasználású, Otthon Start, Piaci hitel); stored in `documents/otp/base/_master_sections/base/`.
   - **Product-specific section** pages → `product_ids` = that product only; stored in `documents/otp/<product>/_master_sections/`.
4. Extracts each page into a standalone single-page PDF.
5. Creates catalog entries with `source: "split:<master>:page N"`, `master_section`, `master_page_number`, and `per_applicant` (base personal-data pages → `true`; cover/property pages → `false`, per the table in §4.2).
6. Emits `processed_pages` increments as it writes pages, so the frontend progress indicator advances per page.
7. On completion, the poll response includes the full `created_doc_ids[]` list and `status: "completed"`.

> **Fallback if the section map is wrong:** the split still runs and produces page documents, but a page may land in the wrong section. The user can **manually re-assign pages after the fact** via the existing multi-project association UI (`PUT /api/documents/{doc_id}/products`) — they are never locked into an incorrect auto-assignment. See §7.3.

> **Note:** `Előzetes értékbecslés megrendelés` does **not** use the master PDF, so it receives no split pages — only its 2 standalone forms.

### 5.4 Decoupling Upload from Fill

The current `/api/pdf/upload` runs the **entire fill pipeline synchronously**. The new upload endpoint **decouples** these: Upload → save file + catalog entry + (optionally) auto-resolve mapping; Fill → happens later from the Fill step with a selected deal. `auto_fill` defaults to `false`.

### 5.5 Per-Applicant Fill Pipeline (NEW)

When `per_applicant: true`, the fill pipeline must **fan out** across all applicants on the deal. Concretely:

1. **Applicant discovery:** `FormFillerPipeline` queries Salesforce for the deal's applicant list — the primary igénylő plus every adóstárs. Let N = number of applicants.
2. **Per document:** if `document.per_applicant == true`, the pipeline fills the template **N times**, once per applicant, using that applicant's personal/income fields. Each filled copy is named with the applicant's role+name (e.g. `szemelyi_adatlap__igenylo_Kovacs_Janos.pdf`, `szemelyi_adatlap__adostars_Szabo_Maria.pdf`).
3. **Per application:** if `document.per_applicant == false`, fill **once** using deal/property-level fields (same as today).
4. **Output grouping:** filled PDFs are grouped in the output so the user sees, per template, either 1 file or N files (one row per applicant) with a clear "per applicant" label.

This requires:
- `FormFillerPipeline.run_for_deal()` to accept the applicant list and loop per-applicant documents.
- The catalog `per_applicant` flag to be passed into the pipeline (the pipeline reads the catalog to decide fan-out).
- **FillPreviewStep** changes (§6.5): it must show the applicant count, let the user pick which applicant to preview, and clearly mark which documents will be multiplied.

For PoC, N is typically 1–2 (igénylő + one adóstárs); the design supports arbitrary N.

### 5.6 Catalog Service

A new `backend/catalog_service.py` mirrors `mapping_service.py`:

```python
class CatalogService:
    def load(self) -> dict: ...
    def save(self, catalog: dict): ...      # atomic write (.tmp + rename)
    def list_banks(self) -> list[dict]: ...
    def list_products(self, bank_id: str) -> list[dict]: ...
    def list_documents(self, product_id: str) -> list[dict]: ...
    def add_document(self, ..., per_applicant: bool) -> dict: ...
    def set_per_applicant(self, doc_id: str, value: bool) -> dict: ...
    def associate(self, doc_id: str, product_ids: list[str]): ...
    def dissociate(self, doc_id: str, product_id: str): ...
    def delete_document(self, doc_id: str): ...
```

Uses the same `threading.Lock` + atomic-write pattern as `mapping_service.py`.

---

## 6. Frontend Component Tree & Data Flow

### 6.1 New Component Hierarchy

```
App.tsx
└── MappingStudio.tsx  (wizard orchestrator — extended)
    ├── ProjectBrowser.tsx          ← NEW: Bank → Product → Document tree
    │   ├── BankSelector.tsx
    │   ├── ProductList.tsx
    │   └── DocumentList.tsx        ← M:N badge + per-applicant badge
    ├── UploadStep.tsx              ← REFACTORED: context-aware upload + master auto-split
    │   ├── ProductAssociationPicker.tsx
    │   └── SplitProgressIndicator.tsx   ← NEW: async master-split progress (polls task_id)
    ├── AnalysisStep.tsx            (unchanged)
    ├── ReviewDashboard.tsx         (unchanged)
    ├── LockStep.tsx                (unchanged)
    └── FillPreviewStep.tsx         ← EXTENDED: applicant selector + per-applicant fan-out
```

### 6.2 The ProjectBrowser — Primary Navigation

Replaces the flat "Meglévő PDF-ek" list. Tree rendered from `GET /api/catalog`:

```
▼ OTP Bank
  ▼ Előzetes értékbecslés megrendelés        [2 docs]
    ├─ Partner nyilatkozat …                 ⊕ shared 4×  👤 per-applicant
    └─ Igénylési dokumentum értékbecsléshez
  ▼ Szabadfelhasználású hitel                [master + 3 standalone]
    ├─ Partner nyilatkozat …                 ⊕ shared 4×  👤 per-applicant
    ├─ V. számú függelék (pénzügyi szolg.)   ⊕ shared 3×
    ├─ Hozzájáruló nyilatkozat (jövedelem)                👤 per-applicant
    └─ [master base sections…] + [master szab.hitel sections…]
  ▼ Otthon Start                              […]
  ▼ Piaci hitel                               […]
```

Badges: `⊕ shared` = belongs to multiple products (tooltip lists them); `👤 per-applicant` = filled once per applicant. **Selecting a document** opens it in Review; **selecting a product folder** contextualizes Upload.

### 6.3 Store Changes (`store.ts`)

New Zustand state slices:

```typescript
catalog: Catalog | null;
catalogLoading: boolean;
selectedBankId: string | null;
selectedProductId: string | null;
selectedDocumentId: string | null;
applicants: Applicant[];           // igénylő + adóstársak for the active deal
selectedApplicantId: string | null; // which applicant to preview in Fill

loadCatalog: () => Promise<void>;
uploadToProduct: (file, productIds, opts?: { per_applicant?: boolean; auto_split_master?: boolean }) => Promise<void>;
splitMaster: (masterPath, bankId) => Promise<string>;   // returns task_id; map derived server-side from DocumentAssembler
pollSplitTask: (taskId) => Promise<SplitTaskStatus>;    // for SplitProgressIndicator
associateDocument: (docId, productIds) => Promise<void>;
setPerApplicant: (docId, value) => Promise<void>;
loadApplicants: (dealId) => Promise<void>;
```

### 6.4 TypeScript Types (`types/index.ts`)

```typescript
export interface CatalogDocument {
  id: string;
  title: string;
  file_path: string;            // → becomes pdf_id for existing endpoints
  source: string;
  product_ids: string[];        // M:N
  page_count: number;
  per_applicant: boolean;       // NEW — fill fan-out
  tags?: string[];
  split_from_master?: boolean;
  master_page_number?: number;
  master_section?: "base" | string;  // base | product slug
}

export interface Applicant {
  id: string;
  role: "igenylo" | "adostars";
  name: string;
}
```

The `file_path` maps to the existing `pdf_id`, so all downstream endpoints (`/api/pdf/fields`, `/api/mapping`, …) work unchanged.

### 6.5 FillPreviewStep — Applicant-Aware (CHANGED)

The Fill step is no longer unchanged. It must:

1. Load the applicant list for the deal (`store.applicants`).
2. For each document in the selected product set, show whether it is `👤 per-applicant` or per-application.
3. Provide an **applicant selector** (tabs: "Igénylő", "Adóstárs 1", …) so the user previews the filled output for a specific applicant.
4. When triggering fill, pass the full applicant list; the backend returns grouped results (per-applicant docs appear N times, per-application docs appear once).
5. Show a summary: *"3 documents × 2 applicants = 6 per-applicant fills + 4 per-application fills = 10 PDFs."*

This is the single biggest fill-pipeline change in this plan and is the reason `FillPreviewStep` moves from "unchanged" to "extended".

---

## 7. Master PDF Split — Section Awareness (Fully Automatic)

Requirement: the master document must be **split into individual pages and uploaded separately**, but the split must respect **base sections** (shared) vs **product-specific sections** (scoped). Per the client decision, this split is **fully automatic** — the user only uploads the master PDF; there is no manual dialog. The section map in `DocumentAssembler` (§7.2) is the source of truth for auto-assignment.

### 7.1 Section Model

The master PDF logically divides into:

- **Base sections** — personal data sheets, declarations, property sheets. Shared by **all 3 master-using products** (Szabadfelhasználású hitel, Otthon Start, Piaci hitel). Each base page becomes one catalog document with `product_ids` = all 3, stored under `base/`.
- **Product-specific sections** — e.g. Otthon Start declaration pages, Piaci hitel annex pages. Scoped to one product. Stored under that product's folder.

`Élőzetes értékbecslés megrendelés` is excluded entirely (it has no master pages).

### 7.2 Reconciling `DocumentAssembler.PRODUCT_SECTIONS`

The existing enum lists `CSOK`, `CSOK Plusz`, etc. — **wrong for the real Drive**. It must be updated to:

```python
PRODUCT_SECTIONS = {
    ProductType.SZABADFELHASZNALASU: [("szab_hitel", 31, 36)],
    ProductType.OTTHON_START:        [("otthon_start", 69, 82)],
    ProductType.PIACI_HITEL:         [("piaci_hitel", 83, 90)],
}
BASE_SECTIONS = {"fedlap": (1,1), "sza_ig_igenylo": (2,5), ...}  # shared by all 3
```

(Page ranges above are illustrative — exact ranges come from inspecting the v5 master during Phase 1.) 

This map is the **single source of truth** for the automatic split: base pages → all 3 products, product pages → that product only. There is no user confirmation step before the split runs — but because the map is editable by the developer in Phase 1 and reconciled against the real products, the auto-assignment is correct by construction. If a page ever lands in the wrong section, the user can re-assign it afterward (§7.3).

### 7.3 Automatic Split Flow

There is no manual dialog. The flow is:

1. **User uploads the master PDF** like any other document in the Upload step (drag-and-drop). No "Split master PDF" button, no mode selection.
2. **Backend detects the master** at upload time (§5.3.1): filename pattern `Igenylesi_dokumentumok_OTP_*` OR page count > threshold. The file is stored under `_master/` (git-ignored), **not** as a normal catalog document.
3. **Backend starts the asynchronous split job** (`POST /api/documents/split-master`) and returns a `task_id`. The job reads the reconciled `DocumentAssembler.PRODUCT_SECTIONS` / `BASE_SECTIONS` (§7.2) to determine each page's `master_section` and `product_ids`, then extracts pages into standalone PDFs via PyMuPDF and writes catalog entries.
4. **Frontend shows a progress indicator** — `SplitProgressIndicator.tsx` polls `GET /api/documents/split-master/{task_id}` and renders: *"Master PDF feldolgozása: 27/97 oldal…"* (progress from `processed_pages` / `total_pages`).
5. **On completion**, the newly created split documents appear in the catalog tree: base sections under each of the 3 master-using products (`⊕ shared` badge), product-specific sections under their product. No user action required.
6. **Edge case — wrong section map:** if a page was auto-assigned to the wrong product/section, the user can **manually re-assign it after the split** via the existing multi-project association UI (`PUT /api/documents/{doc_id}/products`), exactly as for any other M:N document. They are never locked into an incorrect auto-assignment.

> **Why automatic:** Balázs confirmed the master PDF structure is stable (one canonical v5 layout per bank), so a curated section map is more reliable than asking the user to set page ranges every time. The map is validated once in Phase 1 and reused on every upload.

### 7.4 Split Pages vs. Master Reassembly

Each split page is a **real, standalone PDF** — independently mappable and fillable. When the fill pipeline needs the **assembled** multi-page document, the existing `DocumentAssembler` still reads from `_master/` directly. Both coexist: split documents are for **mapping/editing**, assembly is for **final output**. (See Open Question #4.)

---

## 8. Migration & Seeding Strategy

### 8.1 Seed From the Real Drive

1. **Create catalog** with one bank (`otp`) and 4 products: `elozetes_ertekbecsles`, `szabadfelhasznalasu`, `otthon_start`, `piaci_hitel`.
2. **Download Drive folders** into `documents/otp/<product_slug>/` (or `base/` for shared files). See §3.3.
3. **Run `scripts/seed_catalog.py`**: hash each file, deduplicate, infer `product_ids` from the folders each unique file appeared in, apply the `per_applicant` override table (§4.2).
4. **Place the master PDF** once in `_master/` (git-ignored); do not seed it as a catalog document.
5. **Mappings** stay in `src/mapping/`; `mapping_path_for()` resolves them unchanged.

### 8.2 Backward Compatibility

- `GET /api/pdfs` continues to return the flat list (old `UploadStep` still works if `ProjectBrowser` isn't rendered).
- `POST /api/pdf/upload` continues to work (saves to `samples/`, runs fill). Marked **deprecated**, not removed.
- `config.PDF_ROOTS` gains `documents/` as a third root.

### 8.3 Master PDF at Repo Root

The 7 MB binary should **not** be committed long-term. Move to `_master/` (git-ignored), referenced by path in the split endpoint, never as a `pdf_id`.

---

## 9. Edge Cases

- **M:N documents** — stored once; `product_ids[]` lists owners; tree shows under each product with `⊕ shared`; deleting from one product only removes the association; deleting the last association prompts "delete file too?" Optional: content-hash dedup on upload offers to associate instead of copy.
- **Per-applicant with 0 adóstárs** — N=1; behaves like today (single fill). No special handling.
- **Per-applicant field keying** — the mapping must reference the *active applicant's* fields; the pipeline swaps the applicant context per fill iteration. Mappings don't need duplication — they reference applicant-relative keys (e.g. `applicant.given_name`) resolved per iteration.
- **Split-page re-upload** — content-hash dedup detects an already-split page; offers association.
- **Orphaned documents** — last association removed → marked `orphaned: true`; shown under an "Unassigned" virtual folder.
- **Filename collisions** — physical files under `documents/otp/<product_slug>/` avoid collisions; shared/base under `base/`; `sanitize_filename()` handles Hungarian accents.
- **Large master split performance** — PyMuPDF ~50 ms/page; 90 pages ≈ 5 s. The split runs **asynchronously** (§5.3.2) with `task_id` polling, so the upload request never blocks; the `SplitProgressIndicator` shows per-page progress. For 200+ pages the same async path scales without change.
- **Auto-detection false positive** — a non-master PDF that happens to match the filename pattern or exceed the page threshold would be split. Mitigation: both conditions (pattern **and** page count > threshold) must hold; and the user can delete the erroneous split docs + re-upload with `auto_split_master: false`.
- **Concurrent catalog edits** — `CatalogService.save()` uses atomic-write + mtime conflict detection (`FileConflictError` → HTTP 409); frontend reloads on conflict.

---

## 10. Implementation Phases

### Phase 1 — Catalog Backend & Drive Seeding (foundation)
- Create `backend/catalog_service.py` (load/save/CRUD, incl. `per_applicant`).
- Create `catalog/document_catalog.json` seeded from the **real Drive** (4 products, real documents, deduped).
- Download Drive contents into `documents/otp/…` and `_master/`.
- Write `scripts/seed_catalog.py` (hash, dedup, infer associations, apply per_applicant table).
- Update `DocumentAssembler.PRODUCT_SECTIONS` enum to real products.
- Add `GET /api/catalog`; add `documents/` to `PDF_ROOTS`.
- **Deliverable:** Catalog API returns the real OTP hierarchy with per_applicant flags.

### Phase 2 — ProjectBrowser Frontend (read-only tree)
- Add `ProjectBrowser`, `BankSelector`, `ProductList`, `DocumentList` (with `⊕ shared` + `👤 per-applicant` badges).
- Add catalog state to `store.ts`; replace flat list in `UploadStep.tsx`.
- **Deliverable:** User browses Bank → Product → Document and opens existing docs.

### Phase 3 — Context-Aware Upload + M:N Association
- Add `POST /api/documents/upload` (with `product_ids`, `per_applicant`).
- Refactor `UploadStep`; add `ProductAssociationPicker`.
- Add `PUT /api/documents/{id}/products`, `PUT /api/documents/{id}` (toggle per_applicant).
- **Deliverable:** User uploads into a product and shares across products; can toggle per-applicant.

### Phase 4 — Automatic Master Split (async, no dialog)
- Add `POST /api/documents/split-master` + `GET /api/documents/split-master/{task_id}`: **automatic** section-aware split driven by `DocumentAssembler.PRODUCT_SECTIONS` / `BASE_SECTIONS` (no client-supplied `section_map`).
- Add master auto-detection in the upload endpoint (filename pattern `Igenylesi_dokumentumok_OTP_*` OR page count > `MASTER_PAGE_THRESHOLD`), gated by `auto_split_master` (default `true`).
- Split job: PyMuPDF page extraction → base sections → all master-using products; product sections → that product; catalog entries with `master_section` + `master_page_number` + `per_applicant`.
- Add `SplitProgressIndicator.tsx` in `UploadStep` (polls `task_id`, renders *"Master PDF feldolgozása: N/M oldal…"*).
- Manual re-assignment after split reuses the existing `PUT /api/documents/{id}/products` association UI.
- **Deliverable:** User uploads the master PDF; it is detected, split, and catalogued automatically; results appear in the tree. No manual dialog.

### Phase 5 — Per-Applicant Fill Pipeline + FillPreviewStep
- Extend `FormFillerPipeline.run_for_deal()` to accept applicant list and fan out per-applicant docs.
- Extend `FillPreviewStep` with applicant selector, per-applicant/per-application labels, fill-count summary.
- **Deliverable:** Co-applicants are handled — per-applicant docs filled N times.

### Phase 6 — Polish & Migration
- Content-hash dedup; orphan detection + "Unassigned" folder; deprecate `/api/pdf/upload`; move master to `_master/`; tests (catalog CRUD, split, M:N, per-applicant fan-out).

---

## 11. Files Affected

| File | Change | Phase |
|---|---|---|
| `backend/catalog_service.py` | **NEW** — catalog CRUD + per_applicant | 1 |
| `backend/server.py` | Add `/api/catalog*`, `/api/documents*`, split-master endpoints | 1–5 |
| `backend/config.py` | Add `documents/` to `PDF_ROOTS`; `CATALOG_DIR` | 1 |
| `catalog/document_catalog.json` | **NEW** — seeded from real Drive | 1 |
| `scripts/seed_catalog.py` | **NEW** — Drive→catalog seeding + dedup | 1 |
| `src/engine/document_assembler.py` | Update `PRODUCT_SECTIONS`/enum to real products | 1 |
| `src/engine/form_filler_pipeline.py` | Per-applicant fan-out | 5 |
| `frontend/src/types/index.ts` | Add `Bank`, `Product`, `CatalogDocument` (+per_applicant), `Applicant` | 2 |
| `frontend/src/store.ts` | Catalog state + applicant state + actions | 2–5 |
| `frontend/src/components/ProjectBrowser.tsx` | **NEW** — tree nav | 2 |
| `frontend/src/components/UploadStep.tsx` | Refactor: context-aware upload | 2–3 |
| `frontend/src/components/ProductAssociationPicker.tsx` | **NEW** — M:N picker | 3 |
| `frontend/src/components/SplitProgressIndicator.tsx` | **NEW** — async master-split progress (polls `task_id`) | 4 |
| `frontend/src/components/FillPreviewStep.tsx` | **EXTENDED** — applicant selector + fan-out | 5 |

**Unchanged:** `src/mapping/*.json`, `src/ai/*`, `AnalysisStep`, `ReviewDashboard`, `PageEditor`, `PointsEditor`, `LockStep`.

---

## 12. Risks & Open Questions

| # | Risk / Question | Mitigation / Recommendation |
|---|---|---|
| 1 | **JSON catalog scaling** | <200 docs is fine; port to SQLite (same API) later. |
| 2 | **Master PDF in git** | Move to git-ignored `_master/` in Phase 6. |
| 3 | **`per_applicant` field keying** — do mappings reference applicant-relative keys? | **Verify** existing mapping JSON uses applicant-agnostic keys that the pipeline can rebind per applicant. If keys are hard-coded to one applicant, a key-namespacing pass is needed. |
| 4 | **Split docs vs. master assembly** — should fill use split docs or reassemble from master? | **Open for Balázs.** Recommend: keep assembly from `_master/` (proven); split docs for mapping/editing. |
| 5 | **Exact master page ranges** — base vs product sections need validation against v5. | Inspect the v5 master during Phase 1 to confirm page ranges and bake them into `PRODUCT_SECTIONS` / `BASE_SECTIONS`; the auto-split reads from there. If a page is mis-assigned, the user re-assigns it afterward via the association UI (§7.3). |
| 6 | **Salesforce applicant data shape** — does the deal object expose N applicants cleanly? | **Confirm** the Salesforce query returns igénylő + all adóstárs with the fields each per-applicant form needs. |
| 7 | **Bank expansion** (K&H, Erste) | Catalog supports it from Phase 1 (`banks[]`); per-bank section maps needed when a 2nd bank arrives. |
| 8 | **Decoupling upload from fill** — OK that uploads no longer auto-fill? | **Open for Balázs.** `auto_fill` defaults `false`; set `true` to preserve old behavior. |

---

## 13. Summary

This plan transforms the flat upload model into a **project-based hierarchy** aligned with the **real OTP Google Drive structure** (4 products, M:N shared documents, deduplicated master PDF), and adds two first-class concepts:

- **Per-applicant documents** (`per_applicant` flag) — the fill pipeline fans out per applicant so adóstárs forms are filled correctly.
- **Section-aware master split (fully automatic)** — uploading the master PDF triggers an asynchronous split that assigns base sections to all products and product-specific sections to their product, driven entirely by the `DocumentAssembler` section map — no manual dialog. The frontend shows a progress indicator and the results land in the catalog tree; mis-assigned pages are correctable afterward via the association UI.

It does so with **no database** (JSON catalog), **no mapping-format changes**, and keeps assembly from the master for proven output. The work splits into **6 phases**, each independently deliverable; Phases 1–4 cover catalog + tree + upload + split, Phase 5 adds the per-applicant fill pipeline and FillPreviewStep, Phase 6 is polish.

---

*Prepared by OpenCode · 2026-07-10 · rev 3 — master split fully automatic (no manual dialog) · For client review*

# Technical Plan — Project-Based Document Upload

**Project:** FinancialGenie Mapping Studio
**Author:** OpenCode (Architecture Analysis)
**Date:** 2026-07-10
**Status:** Draft for client review

---

## 1. Executive Summary

The FinancialGenie Mapping Studio currently uses a **flat document upload model**: a single PDF is dropped into `samples/`, the AI resolves its field mapping, and the pipeline fills it. There is no notion of *which bank* or *which product* a document belongs to.

Balázs needs a **project-based hierarchy**:

```
Bank (OTP)
└── Bank Product (Piaci hitel, CSOK Plusz, Otthon Start, ...)
    └── PDF documents (split pages of the master + standalone forms)
```

A document can belong to **multiple products** (e.g. the personal data form is shared). The 97-page OTP master PDF must be **split into individual pages** and uploaded as separate documents — never as a single file.

This plan introduces a **document catalog** (a lightweight JSON manifest) that models the Bank → Product → Document hierarchy with many-to-many relationships, while keeping the existing file-based architecture intact. No database migration is required for the PoC; the design is forward-compatible with SQLite if needed later.

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

The backend **already anticipates** the OTP product hierarchy, even though it is unused:

- `mapping_path_for()` (config.py:65) has **cross-directory alias logic** (step 4, line 204): if the same filename appears in a sibling OTP product folder, it reuses that sibling's mapping. This implies the authors expected `otp/Piaci hitel/` and `otp/CSOK/` sibling folders with shared forms.
- `DocumentAssembler` (`src/engine/document_assembler.py`) defines an `OTP ProductType` enum (Piaci hitel, CSOK, CSOK Plusz, Otthon Start, …) and section maps referencing master-PDF page ranges per product.
- The **`otp/` directory does not exist yet** — it was removed in commit `95eb087` ("remove 90-page OTP v5 PDF and its stale mapping").

So the backend supports hierarchy; the **frontend and upload flow are the gap**.

### 2.3 Upload Flow (current)

`POST /api/pdf/upload` (server.py:677) does the following in one synchronous call:

1. Sanitize filename (NFKD transliteration, collision-safe suffix).
2. Save to `samples/<safe_name>.pdf` (flat — no subdirectory).
3. Auto-run `FormFillerPipeline.run_for_deal()` which resolves/creates a mapping via AI and fills the PDF.
4. Return `filled_pdf_url`.

The frontend `UploadStep.tsx` is a drag-and-drop zone + flat list of existing PDFs. There is no way to choose a destination product or bank.

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
    └── FillPreviewStep.tsx    ← fill preview with deal data
```

**State management:** Zustand store (`store.ts`) holds `pdfs[]`, `activePdfId`, `mapping`, `pdfFields`, etc. There is **no router** (`react-router` is not in `package.json`); navigation is purely `useState`-driven within `MappingStudio`.

**Styling:** CSS custom properties + Tailwind utility classes; inline styles are used heavily.

### 2.5 The Multi-Project Constraint

Requirement: *"A document can belong to multiple projects."*

In practice, OTP's base forms (pages 2–30: personal data sheet, declarations, property sheet) are **shared across all products**. The `DocumentAssembler._build_page_plan()` always includes `BASE_SECTIONS` regardless of the chosen `ProductType`. Only product-specific sections (CSOK 37–48, Otthon Start 69–82, etc.) differ.

This means a naive folder hierarchy (`otp/Piaci hitel/form.pdf` + duplicate in `otp/CSOK/form.pdf`) would **duplicate files and mappings** — exactly the problem the existing cross-directory alias code tried to avoid.

---

## 3. Proposed Architecture

### 3.1 Design Principle: Catalog Overlay on File Hierarchy

We keep PDFs on disk (no DB required) but introduce a **document catalog** — a single JSON manifest that owns the logical hierarchy and the many-to-many product↔document associations. The physical files live in a content-organized store.

```
┌─────────────────────────────────────────────────┐
│            Document Catalog (JSON)              │  ← single source of truth
│  banks → products → document associations       │
└──────────────────────┬──────────────────────────┘
                       │ references
┌──────────────────────▼──────────────────────────┐
│         Physical Document Store                  │
│  documents/<bank_slug>/<doc_id>.pdf              │  ← deduplicated by content
│  (mappings resolve via existing mapping_path_for)│
└─────────────────────────────────────────────────┘
```

**Why JSON catalog over SQLite/database?**
- Consistent with the existing architecture (everything is JSON + files).
- Zero-migration cost for the PoC.
- Human-readable, diffable, git-friendly.
- Forward-compatible: if the catalog grows, it can be ported to SQLite later without changing the API contract.

**Why not pure folders (like the old `otp/<product>/` layout)?**
- Multi-project documents (requirement #6) cannot be expressed with folders alone — a file can only live in one directory.
- The catalog decouples **logical grouping** (product membership) from **physical storage** (deduplicated file location).

### 3.2 Physical Directory Structure

```
financialgenie_review/
├── documents/                      ← NEW: physical document store
│   └── otp/                        ← per-bank
│       ├── base/                   ← shared/base forms (multi-product)
│       │   ├── szemelyi_adatlap_igenylo.pdf
│       │   ├── szemelyi_adatlap_tarsigenylo.pdf
│       │   └── ...
│       ├── piaci_hitel/
│       │   └── piaci_hitel_nyilatkozat_p01.pdf   ← split master pages
│       ├── csok_plusz/
│       │   └── csok_plusz_p57.pdf
│       └── otthon_start/
│           └── otthon_start_p69.pdf
├── catalog/
│   └── document_catalog.json       ← NEW: hierarchy + associations
├── src/mapping/                    ← existing mappings (unchanged)
└── samples/                        ← legacy uploads (kept for backward compat)
```

The existing `otp/` and `samples/` roots remain valid (`config.PDF_ROOTS`). The new `documents/` root is added. `mapping_path_for()` continues to work because it resolves by signature/word-match, not by directory.

---

## 4. Data Model — Document Catalog Schema

The catalog is a single JSON file: `catalog/document_catalog.json`.

```jsonc
{
  "version": 1,
  "banks": [
    {
      "id": "otp",
      "name": "OTP Bank",
      "products": [
        {
          "id": "piaci_hitel",
          "name": "Piaci hitel",
          "document_ids": ["doc_szemelyi_adatlap_igenylo", "doc_piaci_p01", "…"]
        },
        {
          "id": "csok_plusz",
          "name": "CSOK Plusz",
          "document_ids": ["doc_szemelyi_adatlap_igenylo", "doc_csok_plusz_p57", "…"]
        }
      ]
    }
  ],
  "documents": [
    {
      "id": "doc_szemelyi_adatlap_igenylo",
      "title": "Személyi adatlap – Igénylő",
      "file_path": "documents/otp/base/szemelyi_adatlap_igenylo.pdf",
      "source": "split:OTP_Igenylesi_Dokumentumok_v5.pdf:pages 2-5",
      "product_ids": ["piaci_hitel", "csok_plusz", "otthon_start", "afa_visszaterites"],
      "page_count": 4,
      "tags": ["base", "personal_data"],
      "uploaded_at": "2026-07-10T12:00:00",
      "updated_at": "2026-07-10T12:00:00"
    },
    {
      "id": "doc_piaci_p01",
      "title": "OTP master – Page 1 (Fedlap)",
      "file_path": "documents/otp/piaci_hitel/piaci_hitel_p01.pdf",
      "source": "split:OTP_Igenylesi_Dokumentumok_v5.pdf:page 1",
      "product_ids": ["piaci_hitel"],
      "page_count": 1,
      "split_from_master": true,
      "master_page_number": 1,
      "uploaded_at": "2026-07-10T12:00:00"
    }
  ]
}
```

**Key fields:**

| Field | Purpose |
|---|---|
| `documents[].id` | Stable unique ID (slug-based). |
| `documents[].file_path` | Relative path to the physical PDF. |
| `documents[].source` | Provenance — `upload:<original_name>` or `split:<master>:page N` |
| `documents[].product_ids` | **M:N association** — which products reference this doc. Drives the tree. |
| `documents[].split_from_master` / `master_page_number` | Marks split-page documents; enables re-assembly display. |
| `banks[].products[].document_ids` | Redundant inverse index for efficient tree rendering. |

The M:N relationship is modeled in **both directions** (document → products, product → documents). The catalog service keeps them in sync on writes.

---

## 5. Backend API Changes

### 5.1 New Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/catalog` | Return full catalog (banks → products → documents). Drives the tree view. |
| `GET` | `/api/catalog/banks` | List banks (for top-level nav). |
| `POST` | `/api/catalog/banks` | Create a new bank. |
| `GET` | `/api/catalog/banks/{bank_id}/products` | List products under a bank. |
| `POST` | `/api/catalog/banks/{bank_id}/products` | Create a new product folder. |
| `GET` | `/api/catalog/products/{product_id}/documents` | List documents in a product. |
| `POST` | `/api/documents/upload` | **Upload a document into a product** (replaces `/api/pdf/upload` for the new flow). |
| `PUT` | `/api/documents/{doc_id}/products` | **Associate a document with one or more products** (multi-project). |
| `DELETE` | `/api/documents/{doc_id}/products/{product_id}` | Remove a document from a product (keeps the file if referenced elsewhere). |
| `POST` | `/api/documents/split-master` | **Split a master PDF** into individual page documents. |
| `GET` | `/api/documents/{doc_id}` | Get document metadata. |
| `DELETE` | `/api/documents/{doc_id}` | Delete a document (file + catalog entry + mapping). |

### 5.2 Upload Endpoint (new signature)

`POST /api/documents/upload` (multipart):

```
file:           <PDF binary>
bank_id:        otp
product_ids:    piaci_hitel,csok_plusz     (comma-separated; ≥1)
title:          "Személyi adatlap – Igénylő"   (optional; defaults to filename)
auto_fill:      false                      (skip immediate AI fill — see §5.4)
```

**Response:**
```json
{
  "document_id": "doc_szemelyi_adatlap_igenylo",
  "file_path": "documents/otp/base/szemelyi_adatlap_igenylo.pdf",
  "product_ids": ["piaci_hitel", "csok_plusz"],
  "mapping_resolved": true,
  "fill_triggered": false
}
```

### 5.3 Master PDF Split Endpoint

`POST /api/documents/split-master`:

```json
{
  "master_pdf_id": "samples/OTP_Igenylesi_Dokumentumok_v5.pdf",
  "bank_id": "otp",
  "page_assignments": [
    {"page": 1,  "product_ids": ["piaci_hitel"], "title": "Fedlap"},
    {"page": 2,  "product_ids": ["piaci_hitel", "csok_plusz"], "title": "Személyi adatlap – Igénylő p1"},
    {"pages": "2-5", "product_ids": ["piaci_hitel"], "title": "Személyi adatlap – Igénylő (full)"}
  ]
}
```

This endpoint:
1. Opens the master PDF with PyMuPDF.
2. For each assignment, extracts the page(s) into a new standalone PDF.
3. Creates catalog entries with `source: "split:<master>:page N"`.
4. Associates each to the specified product(s).

The frontend can offer **two split modes**:
- **Per-page**: every page becomes its own document (meets requirement #5).
- **Per-range**: a contiguous page range becomes one document (for multi-page forms like the 4-page personal data sheet).

> **Note:** The existing `DocumentAssembler` already splits the master PDF at runtime during filling. The new split endpoint produces **standalone template documents** that the editor can map individually. This is additive, not a replacement.

### 5.4 Decoupling Upload from Fill

The current `/api/pdf/upload` runs the **entire fill pipeline synchronously** (AI recognition + Salesforce fill). This is inappropriate for the project flow, where the user uploads many templates and maps them *before* filling.

The new upload endpoint **decouples** these:
- Upload → save file + create catalog entry + (optionally) auto-resolve mapping.
- Fill → happens later, when the user explicitly triggers it from the Fill step with a selected deal.

The `auto_fill` parameter (default `false`) preserves the old behavior for backward compatibility. A new `auto_resolve_mapping` parameter (default `true`) controls whether AI recognition runs on upload.

### 5.5 Catalog Service

A new `backend/catalog_service.py` mirrors the existing `mapping_service.py` pattern:

```python
class CatalogService:
    def load(self) -> dict: ...           # read catalog/document_catalog.json
    def save(self, catalog: dict): ...    # atomic write (.tmp + rename)
    def list_banks(self) -> list[dict]: ...
    def list_products(self, bank_id: str) -> list[dict]: ...
    def list_documents(self, product_id: str) -> list[dict]: ...
    def add_document(self, ...) -> dict: ...
    def associate(self, doc_id: str, product_ids: list[str]): ...
    def dissociate(self, doc_id: str, product_id: str): ...
    def delete_document(self, doc_id: str): ...

catalog_service = CatalogService()
```

It uses the same `threading.Lock` + atomic-write pattern as `mapping_service.py`.

---

## 6. Frontend Component Tree & Data Flow

### 6.1 New Component Hierarchy

```
App.tsx
└── MappingStudio.tsx  (wizard orchestrator — extended)
    ├── ProjectBrowser.tsx          ← NEW: Bank → Product → Document tree
    │   ├── BankSelector.tsx        ← top-level bank tabs (OTP, …)
    │   ├── ProductList.tsx         ← product folders under selected bank
    │   └── DocumentList.tsx        ← documents in selected product (with M:N badge)
    ├── UploadStep.tsx              ← REFACTORED: context-aware upload (knows selected product)
    │   └── ProductAssociationPicker.tsx  ← NEW: assign doc to multiple products
    ├── MasterSplitDialog.tsx       ← NEW: split master PDF into pages
    ├── AnalysisStep.tsx            (unchanged)
    ├── ReviewDashboard.tsx         (unchanged)
    │   ├── PageEditor.tsx          (unchanged)
    │   └── PointsEditor.tsx        (unchanged)
    ├── LockStep.tsx                (unchanged)
    └── FillPreviewStep.tsx         (unchanged)
```

### 6.2 The ProjectBrowser — Primary Navigation

This replaces the flat "Meglévő PDF-ek" list in `UploadStep.tsx`. It is a **tree view** rendered from `GET /api/catalog`:

```
▼ OTP Bank
  ▼ Piaci hitel                    [4 docs]
    ├─ Fedlap                      [page 1]
    ├─ Személyi adatlap – Igénylő  [4 pages]  ⊕ shared
    ├─ Hitelfeltételek             [6 pages]  ⊕ shared
    └─ Piaci hitel nyilatkozat     [2 pages]
  ▼ CSOK Plusz                     [3 docs]
    ├─ Személyi adatlap – Igénylő  [4 pages]  ⊕ shared   ← same doc, shown again
    └─ CSOK Plusz nyilatkozat      [7 pages]
```

The `⊕ shared` badge indicates a document that belongs to multiple products (requirement #6). Clicking it opens a tooltip listing the products.

**Selecting a document** in the tree navigates to the Review step (same as the current `onOpenExisting`), skipping Upload. **Selecting a product folder** contextualizes the Upload step so new uploads land in that product.

### 6.3 Store Changes (`store.ts`)

New Zustand state slices:

```typescript
// Catalog state
catalog: Catalog | null;
catalogLoading: boolean;
selectedBankId: string | null;      // e.g. "otp"
selectedProductId: string | null;   // e.g. "piaci_hitel"
selectedDocumentId: string | null;  // e.g. "doc_szemelyi_adatlap_igenylo"

// Actions
loadCatalog: () => Promise<void>;
selectBank: (bankId: string) => void;
selectProduct: (productId: string | null) => void;
uploadToProduct: (file: File, productIds: string[], opts?: {...}) => Promise<void>;
splitMaster: (masterPdfId: string, assignments: PageAssignment[]) => Promise<void>;
associateDocument: (docId: string, productIds: string[]) => Promise<void>;
```

The existing `uploadPdfFile(file)` action is preserved for backward compatibility but internally calls `uploadToProduct` with the currently-selected product.

### 6.4 TypeScript Types (`types/index.ts`)

```typescript
export interface Bank {
  id: string;
  name: string;
  products: Product[];
}

export interface Product {
  id: string;
  name: string;
  bank_id: string;
  document_ids: string[];
}

export interface CatalogDocument {
  id: string;
  title: string;
  file_path: string;          // → becomes pdf_id for existing mapping/field endpoints
  source: string;             // "upload:…" | "split:…:page N"
  product_ids: string[];      // M:N — drives the tree + shared badge
  page_count: number;
  tags?: string[];
  split_from_master?: boolean;
  master_page_number?: number;
}

export interface Catalog {
  version: number;
  banks: Bank[];
  documents: CatalogDocument[];
}
```

The `file_path` field maps directly to the existing `pdf_id` concept, so all downstream endpoints (`/api/pdf/fields`, `/api/mapping`, `/api/mapping/recognize`) work unchanged.

---

## 7. Master PDF Split Handling

Requirement #5 is explicit: the master document (`Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf`) must be **split into individual pages and uploaded separately** — never uploaded as a single file.

### 7.1 Split Flow

1. User selects **"Split master PDF"** in the Upload step.
2. `MasterSplitDialog` shows the master PDF (detected via `DocumentAssembler.is_master_pdf()` — ≥97 pages).
3. A page-grid preview renders each page thumbnail (reuse `GET /api/pdf/page/{n}/image`).
4. For each page (or selected range), the user assigns:
   - A title.
   - One or more product memberships (defaults inferred from `DocumentAssembler.PRODUCT_SECTIONS`).
5. On confirm, `POST /api/documents/split-master` extracts each page into a standalone PDF and creates catalog entries.

### 7.2 Default Product Assignment (auto-suggest)

The `DocumentAssembler` already knows which master-PDF pages belong to which product:

```python
PRODUCT_SECTIONS = {
    ProductType.CSOK: [("csok_nyilatkozat", 37, 48)],
    ProductType.OTTHON_START: [("otthon_start", 69, 82)],
    ...
}
BASE_SECTIONS = {"fedlap": (1,1), "sza_ig_igenylő": (2,5), ...}  # shared by all
```

The split dialog can **auto-populate** product assignments from this map: pages 1–30 → all products (base), pages 37–48 → CSOK only, pages 69–82 → Otthon Start only, etc. The user confirms or adjusts before the split runs.

### 7.3 Standalone Page PDFs vs. Master Reassembly

Each split page is a **real, standalone PDF** (single page extracted via PyMuPDF `Document.insert_pdf` with a 1-page range). This means:
- Each can be mapped independently in the PageEditor.
- Each has its own mapping JSON (resolved by the existing `mapping_path_for`).
- They can be filled individually.

When the fill pipeline needs the **assembled** document (multiple pages in order), the existing `DocumentAssembler` still works — it reads from the master PDF directly. The split documents are for the **editing/mapping workflow**, not for replacing assembly. Both coexist.

---

## 8. Migration Strategy

### 8.1 Existing Documents in `samples/`

The 8 PDFs currently in `samples/` are a mix of test forms and early OTP splits. Migration approach:

1. **Seed the catalog** with one bank (`OTP`) and 4 products (`piaci_hitel`, `csok_plusz`, `otthon_start`, `afa_visszaterites`) — matching the OTP product structure.
2. **Assign existing sample PDFs** to products via a one-time migration script (`scripts/seed_catalog.py`):
   - `Igenylesi_dokumentumok_elso_4_oldal.pdf` → `piaci_hitel` (base, pages 1–4).
   - `01_SZA_IG_Szemelyi_adatlap_Igenylo.pdf` → all products (base form).
   - `Partner_nyilatkozat_hiteligeny_leadasakor.pdf` → all products.
   - etc.
3. **Files stay where they are** — `samples/` remains a valid PDF root. The catalog `file_path` simply points to `samples/<name>.pdf`. No file moves required.
4. **Mappings stay where they are** — `src/mapping/*.json` is untouched; `mapping_path_for()` resolves them the same way.

### 8.2 Backward Compatibility

- `GET /api/pdfs` continues to return the flat list (the old `UploadStep` flat list still works if `ProjectBrowser` is not rendered).
- `POST /api/pdf/upload` continues to work (saves to `samples/`, runs fill). Marked as **deprecated** in the API docs but not removed.
- The new `POST /api/documents/upload` is the primary path going forward.
- `config.PDF_ROOTS` gains `documents/` as a third root. `list_pdfs()` walks all three.

### 8.3 The Master PDF at Repo Root

`OTP_Igenylesi_Dokumentumok_v5.pdf` (7 MB) sits at the repo root. It should **not** be committed long-term (it's a binary, and `.gitignore` already excludes `output/`). Recommendation:

- Move to `documents/otp/_master/` (or a `.gitignore`d path).
- Reference it by path in the split endpoint, not by `pdf_id` (it's not a mappable template).
- Add a `.gitignore` entry for `documents/otp/_master/`.

---

## 9. Edge Cases

### 9.1 Multi-Project Documents (M:N)

**Scenario:** The personal data form belongs to Piaci hitel, CSOK Plusz, AND Otthon Start.

**Handling:**
- The document is stored **once** (physical deduplication by `file_path`).
- The catalog `product_ids: [...]` array lists all owning products.
- The tree shows the document under each product with a `⊕ shared` badge.
- Deleting it from one product (`DELETE /api/documents/{id}/products/{product_id}`) only removes the association — the file and mapping survive as long as ≥1 product references it.
- Deleting the last association triggers a confirmation: *"This document is no longer referenced by any product. Delete the file too?"*

**Deduplication on upload:** If a user uploads a file with identical content (SHA-256) to an existing document, the backend detects the duplicate and offers to **associate** the existing document with the new product instead of creating a copy. This is an optional enhancement (Phase 3).

### 9.2 Split PDF Re-upload

**Scenario:** A user manually uploads a single page that was already extracted by the master splitter.

**Handling:** Content-hash deduplication (§9.1) detects it. The upload endpoint returns the existing `document_id` and asks whether to add the new product association.

### 9.3 Orphaned Documents

**Scenario:** A document's only product association is removed, leaving it unreferenced.

**Handling:** The catalog marks it with `orphaned: true`. The `ProjectBrowser` shows an "Unassigned" virtual folder containing orphans. The user can re-assign or delete them.

### 9.4 Filename Collisions Across Products

**Scenario:** Two products each have a file named `nyilatkozat.pdf`.

**Handling:** Physical files are stored under `documents/otp/<product_slug>/` so paths don't collide. Shared/base documents go under `documents/otp/base/`. The `sanitize_filename()` function (server.py:192) already handles Hungarian-accent collisions.

### 9.5 Large Master PDF Performance

**Scenario:** Splitting a 97-page, 7 MB PDF into 97 individual files.

**Handling:** The split endpoint runs **synchronously** but fast (PyMuPDF page extraction is ~50 ms/page; total ~5 s). For larger documents (200+ pages), it should return a `task_id` and poll like the existing recognition endpoint (`/api/recognize/{task_id}/status`). For the OTP 97-page case, synchronous is acceptable.

### 9.6 Catalog Corruption / Concurrent Edits

**Scenario:** Two browser tabs edit the catalog simultaneously.

**Handling:** The `CatalogService.save()` uses the same atomic-write + mtime conflict detection pattern as `mapping_service.py` (`FileConflictError` → HTTP 409). The frontend reloads on conflict.

### 9.7 Empty Products / Empty Banks

**Scenario:** A user creates a product folder but hasn't uploaded any documents yet.

**Handling:** The tree shows the folder with a "0 documents" count and an inline upload affordance. No special backend handling needed.

---

## 10. Implementation Phases

### Phase 1 — Catalog Backend & Seeding (foundation)
- Create `backend/catalog_service.py` (load/save/CRUD).
- Create `catalog/document_catalog.json` with OTP bank + 4 products seeded.
- Add `GET /api/catalog` endpoint.
- Add `documents/` to `config.PDF_ROOTS`.
- Write `scripts/seed_catalog.py` to assign existing `samples/` PDFs.
- **Deliverable:** Catalog API returns the OTP hierarchy. No frontend changes yet.

### Phase 2 — ProjectBrowser Frontend (read-only tree)
- Add `ProjectBrowser`, `BankSelector`, `ProductList`, `DocumentList` components.
- Add catalog state to `store.ts`.
- Replace the flat PDF list in `UploadStep.tsx` with the tree.
- Clicking a document opens it in Review (existing flow).
- **Deliverable:** User can browse Bank → Product → Document and open existing docs.

### Phase 3 — Context-Aware Upload + Multi-Product Association
- Add `POST /api/documents/upload` endpoint (with `product_ids`).
- Refactor `UploadStep` to upload into the selected product.
- Add `ProductAssociationPicker` for assigning a document to multiple products.
- Add `PUT /api/documents/{id}/products` and `DELETE …/products/{pid}`.
- **Deliverable:** User uploads a PDF into a specific product and can share it across products.

### Phase 4 — Master PDF Splitter
- Add `POST /api/documents/split-master` endpoint.
- Add `MasterSplitDialog` with page-grid preview + product assignment.
- Auto-suggest product assignments from `DocumentAssembler.PRODUCT_SECTIONS`.
- **Deliverable:** User splits the 97-page master into individual page documents.

### Phase 5 — Polish & Migration
- Content-hash deduplication on upload.
- Orphan detection + "Unassigned" virtual folder.
- Deprecate `/api/pdf/upload` (keep for backward compat).
- Move master PDF to `documents/otp/_master/`.
- Tests: catalog CRUD, split, M:N association, dedup.

---

## 11. Files Affected

| File | Change | Phase |
|---|---|---|
| `backend/catalog_service.py` | **NEW** — catalog CRUD service | 1 |
| `backend/server.py` | Add `/api/catalog*`, `/api/documents*`, `/api/documents/split-master` endpoints | 1–4 |
| `backend/config.py` | Add `documents/` to `PDF_ROOTS`; add `CATALOG_DIR` | 1 |
| `catalog/document_catalog.json` | **NEW** — seeded OTP hierarchy | 1 |
| `scripts/seed_catalog.py` | **NEW** — one-time migration | 1 |
| `frontend/src/api/client.ts` | Add catalog + document API functions | 2–4 |
| `frontend/src/types/index.ts` | Add `Bank`, `Product`, `CatalogDocument`, `Catalog` types | 2 |
| `frontend/src/store.ts` | Add catalog state + actions | 2–3 |
| `frontend/src/components/ProjectBrowser.tsx` | **NEW** — tree navigation | 2 |
| `frontend/src/components/UploadStep.tsx` | Refactor: context-aware upload, integrate `ProjectBrowser` | 2–3 |
| `frontend/src/components/ProductAssociationPicker.tsx` | **NEW** — M:N product picker | 3 |
| `frontend/src/components/MasterSplitDialog.tsx` | **NEW** — master PDF splitter UI | 4 |

**Unchanged (no modifications needed):**
- `src/mapping/*.json` — mappings resolve via existing `mapping_path_for()`.
- `src/engine/*` — fill pipeline untouched.
- `src/ai/*` — field recognizer untouched.
- `AnalysisStep`, `ReviewDashboard`, `PageEditor`, `PointsEditor`, `LockStep`, `FillPreviewStep` — all unchanged.

---

## 12. Risks & Open Questions

| # | Risk / Question | Mitigation / Recommendation |
|---|---|---|
| 1 | **JSON catalog scaling** — if hundreds of documents are added, the single JSON file could get large. | For PoC (<200 docs) this is fine. The catalog service can be ported to SQLite (same API) without frontend changes. |
| 2 | **Master PDF in git** — the 7 MB binary is currently committed. | Move to git-ignored path in Phase 5. |
| 3 | **Mapping reuse across split pages** — split pages share field names with the master; does `mapping_path_for` resolve correctly? | Yes — the signature-match + word-overlap logic (config.py:108–198) already handles this. Verify with a test in Phase 4. |
| 4 | **Should the fill pipeline use split documents or the master?** | **Open question for Balázs.** Currently the pipeline assembles from the master. If split documents should drive assembly, `DocumentAssembler` needs a new mode. Recommend: keep assembly from master (it's proven), use split documents only for mapping/editing. |
| 5 | **Bank expansion** — when will the second bank (e.g. K&H, Erste) be added? | The catalog supports it from Phase 1 (`banks[]` array). The `DocumentAssembler` and `ProductType` enum are OTP-specific and will need per-bank section maps when a second bank arrives. |
| 6 | **Decoupling upload from fill** — the current upload triggers fill automatically. Is the client OK with uploads no longer auto-filling? | **Open question for Balázs.** The new `auto_fill` parameter defaults to `false`. If the client wants the old behavior, set it to `true` in the upload call. |

---

## 13. Summary

This plan transforms the flat upload model into a **project-based hierarchy** with minimal disruption:

- **No database** — a JSON catalog models the Bank → Product → Document tree and M:N associations, consistent with the existing file-based architecture.
- **No mapping changes** — the existing `mapping_path_for()` resolution works unchanged because it matches by signature and word overlap, not by directory.
- **No fill-pipeline changes** — assembly and filling continue to read from the master PDF; split documents are for the mapping/editing workflow.
- **Multi-project documents** are a first-class concept (catalog `product_ids[]` array + `⊕ shared` UI badge).
- **Master PDF splitting** produces standalone single-page PDFs via a dedicated endpoint, with auto-suggested product assignments from the existing `DocumentAssembler` section map.

The work is split into **5 phases**, each independently deliverable. Phase 1–2 (catalog + tree browser) can ship first; Phase 3–4 (context-aware upload + master split) follow. The existing wizard steps (Analysis → Review → Lock → Fill) require **zero changes**.

---

*Prepared by OpenCode · 2026-07-10 · For client review*

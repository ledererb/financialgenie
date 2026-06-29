# FinancialGenie Mapping Editor — Technical Specification

> Web-based visual mapping editor for OTP PDF form field mappings.
> Spec version 1.0 · 2026-06-29

---

## 1. Product Overview

### 1.1 Problem

A FinancialGenie jelenlegi mapping workflow-ja: AI (Claude) generál egy `src/mapping/*_mapping.json`-t → ember átnézi szövegesen → commit. Ez 374+ mezős mappingeknél átláthatatlan. Nincs vizuális visszajelzés arról, hogy melyik PDF mezőbe milyen érték került, és nincs egyszerű mód a hibás AI hozzárendelés javítására.

### 1.2 Solution

Egy böngészőben futó, hárompaneles webalkalmazás:

- **Bal oldal:** mezőlista szűréssel, kereséssel
- **Középen:** PDF vizuális megjelenítés, mezők kiemelésével
- **Jobb oldal:** kiválasztott mező szerkesztőpanel

A felhasználó rákattint egy mezőre a PDF-en → látja a mapping részleteit → felülírhatja a kanonikus hozzárendelést, vagy új mezőt adhat hozzá (különösen flat PDF-eknél, ahol kattintással helyez el koordinátákat).

### 1.3 Key Constraints

- **AcroForm és flat PDF támogatás** egyaránt
- **Karakter-mezők kezelése**: ahol egy érték (adóazonosító, személyi szám) több különálló PDF mezőbe van szétosztva
- **Mezőtípus-tudatosság**: text, checkbox, number, date, dropdown → más-más UI viselkedés
- **Minden a böngészőben fut**, kivéve a PDF renderelést és mentést (backend API)
- **Nem része a pipeline-nak** — ez egy standalone tool a mapping szerkesztésére

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────┐
│                    Browser (React)                    │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ Field    │  │ PDF Viewer   │  │ Field Editor  │  │
│  │ List     │  │ (react-pdf)  │  │ Panel         │  │
│  │ +Filter  │  │ +Overlay     │  │               │  │
│  └──────────┘  └──────────────┘  └───────────────┘  │
│         │              │                  │          │
│         └──────────────┴──────────────────┘          │
│                        │                             │
│                 Zustand Store                        │
│           (mapping state + selection)                │
└────────────────────────┬────────────────────────────┘
                         │ REST API
┌────────────────────────┴────────────────────────────┐
│                  Backend (FastAPI)                    │
│                                                      │
│  /api/pdf/*         PDF page images + field data     │
│  /api/mapping/*     CRUD mapping operations          │
│  /api/recognize/*   Trigger AI field recognition     │
│                                                      │
│  Uses: pikepdf, PyMuPDF, reportlab                   │
└─────────────────────────────────────────────────────┘
```

### 2.1 Technology Stack

| Layer | Technology | Rationale |
|---|---|---|
| Frontend framework | React 19 + TypeScript + Vite | Modern, typed, fast dev |
| UI | Tailwind CSS + shadcn/ui | Clean, customizable components |
| State management | Zustand | Lightweight, no boilerplate |
| PDF rendering | react-pdf (pdf.js) | Canvas-based, supports custom overlay |
| Backend | FastAPI (Python 3.12+) | Native to existing stack, async |
| PDF processing | pikepdf + PyMuPDF | Already in requirements.txt |
| Field recognition | Existing FieldRecognizer | Reuse AI onboarding code |

---

## 3. Backend API Design

### 3.1 PDF Service

```
GET  /api/pdfs                          → list all PDFs in otp/ and samples/
GET  /api/pdf/{pdf_id}/info             → page count, has_acroform, file size
GET  /api/pdf/{pdf_id}/page/{n}/image   → PNG render of page n (150 DPI)
GET  /api/pdf/{pdf_id}/fields           → all form fields with type, rect, page
GET  /api/pdf/{pdf_id}/preview          → quick filled preview (first 3 fields)
```

**PDF ID format**: URL-encoded path relative to project root, e.g. `otp%2FPiaci+hitel%2FIgenylesi...pdf`

**Field response** (`GET /fields`):

```json
{
  "pdf_id": "otp/Piaci hitel/...",
  "total_pages": 97,
  "has_acroform": true,
  "fields": [
    {
      "pdf_field_name": "SZA_IG_név",
      "field_type": "text",
      "page_number": 2,
      "rect": {"x": 120, "y": 450, "width": 280, "height": 18},
      "flags": {"readonly": false, "required": false, "multiline": false},
      "options": null
    }
  ]
}
```

**For AcroForm PDFs**: `rect` comes from the field widget annotation's `/Rect`.
**For flat PDFs**: `rect` is derived from `coordinates` in the mapping JSON, or estimated from text anchor positions.

### 3.2 Mapping Service

```
GET  /api/mapping/{pdf_id}              → load mapping JSON (or empty)
PUT  /api/mapping/{pdf_id}/field/{name} → update single field mapping
POST /api/mapping/{pdf_id}/field        → add new field mapping
DELETE /api/mapping/{pdf_id}/field/{name} → remove field mapping
POST /api/mapping/{pdf_id}/field/group  → create character group
DELETE /api/mapping/{pdf_id}/field/group/{group_id} → delete char group
PUT  /api/mapping/{pdf_id}/field/group/{group_id}   → update char group
POST /api/mapping/{pdf_id}/recognize    → trigger AI recognition
GET  /api/mapping/{pdf_id}/canonical-fields → list available canonical paths
```

**Update field request**:

```json
{
  "canonical_field": "participant.name",
  "field_type": "text",
  "confidence": "manual",
  "notes": "Javítva: az AI rosszul mapelte",
  "coordinates": null
}
```

**Character group request**:

```json
{
  "group_name": "adoazonosito_jel",
  "field_type": "character_split",
  "canonical_field": "participant.tax_id",
  "member_fields": [
    "A_NY_11_társigénylő_1",
    "A_NY_11_társigénylő_2",
    "A_NY_11_társigénylő_3",
    "A_NY_12_társigénylő_1",
    "A_NY_12_társigénylő_2",
    "A_NY_12_társigénylő_3",
    "A_NY_15_társigénylő_1",
    "A_NY_15_társigénylő_2",
    "A_NY_15_társigénylő_3",
    "A_NY_17társigénylő_1"
  ],
  "direction": "left_to_right",
  "separator": ""
}
```

### 3.3 Recognition Service

```
POST /api/mapping/{pdf_id}/recognize
  Body: { "mode": "auto" | "acroform" | "flat" }
  Response: { "status": "running" | "done" | "error", "task_id": "..." }

GET  /api/recognize/{task_id}/status   → poll recognition progress
GET  /api/recognize/{task_id}/result   → get merged mapping
```

AI recognition runs asynchronously (can take 30-120s for large PDFs). The frontend polls the status endpoint.

---

## 4. Frontend Architecture

### 4.1 Component Tree

```
<App>
  <Header />                          ← PDF selector, save button, recognition trigger
  <MainLayout>
    <FieldListPanel />                ← left sidebar
    <PdfViewerPanel />                ← center, takes most space
    <FieldEditorPanel />              ← right sidebar
  </MainLayout>
  <StatusBar />                       ← save status, mapping stats
```

### 4.2 Component Details

#### Header

- **PDF Selector**: dropdown of available PDFs → loads mapping + renders pages
- **Mapping stats**: "374 leképezve / 1743 összesen"
- **Save button**: PUT entire mapping back
- **AI Recognize button**: triggers `/recognize` → merges result
- **Export/Import**: download mapping JSON, upload modified version

#### FieldListPanel (Left Sidebar)

- **Search**: filter by field name, label, canonical path
- **Filter tabs**: All | Mapped | Unmapped | Character groups | By page
- **Field list**: scrollable list, each item shows:
  - Color dot (green=mapped, yellow=unmapped, blue=char-group, red=error)
  - PDF field name (truncated)
  - Canonical path (if mapped)
  - Field type icon
- **Click**: selects field, scrolls PDF to page, highlights rect
- **Multi-select**: Ctrl/Cmd+click for batch operations (create char group)
- **Drag & drop reorder**: for character groups

#### PdfViewerPanel (Center)

- **Page navigation**: prev/next + page number input
- **PDF canvas**: rendered by `react-pdf`, zoomable (50%-200%)
- **Field overlay**: semi-transparent colored rectangles over each field
  - Green: mapped, high confidence
  - Yellow: mapped, low/medium confidence
  - Orange: unmapped
  - Blue: currently selected
  - Purple: character group
  - Gray: not relevant (decorative/static fields)
- **Hover**: tooltip with field name + canonical
- **Click**: selects field → updates FieldEditorPanel
- **For flat PDFs**: "Place Field" mode — click+drag to define coordinates for a new field
- **Preview mode**: toggle to show filled values instead of field names

#### FieldEditorPanel (Right Sidebar)

Shows when a field is selected. All fields editable.

**For a single field:**

```
┌─────────────────────────────┐
│ Field: SZA_IG_név           │
│ Type: text                  │
│ Page: 2                     │
│                             │
│ Canonical field:            │
│ ┌─────────────────────────┐ │
│ │ participant.name    [▼] │ │  ← autocomplete dropdown
│ └─────────────────────────┘ │
│                             │
│ Field type override:        │
│ [text ▼]                    │
│                             │
│ Confidence:                 │
│ ○ high  ● medium  ○ low    │
│ ○ manual                    │
│                             │
│ Notes:                      │
│ ┌─────────────────────────┐ │
│ │                         │ │
│ └─────────────────────────┘ │
│                             │
│ [Unmap]  [Delete Field]     │
└─────────────────────────────┘
```

**For a character group:**

```
┌─────────────────────────────┐
│ Group: adoazonosito_jel     │
│ Type: character_split       │
│                             │
│ Canonical field:            │
│ [participant.tax_id    [▼]] │
│                             │
│ Direction: [left_to_right ▼]│
│ Separator: [________]       │
│                             │
│ Member fields (10):         │
│ ┌─────────────────────────┐ │
│ │ 1. A_NY_11_társigénylő_1│ │
│ │ 2. A_NY_11_társigénylő_2│ │
│ │ 3. A_NY_11_társigénylő_3│ │
│ │ ...           [reorder] │ │
│ └─────────────────────────┘ │
│                             │
│ [+ Add field to group]      │
│ [Ungroup] [Delete Group]    │
└─────────────────────────────┘
```

**For a flat PDF field (extra coordinate editor):**

```
│ Coordinates:                │
│ x: [310]  y: [132]         │
│ w: [230]  h: [18]          │
│                             │
│ [Recalculate from click]    │
```

### 4.3 State Management (Zustand)

```typescript
interface EditorState {
  // PDF
  pdfId: string | null;
  pdfInfo: PdfInfo | null;
  currentPage: number;
  zoom: number;

  // Fields
  pdfFields: PdfField[];           // from GET /fields
  mapping: MappingConfig | null;   // from GET /mapping

  // Selection
  selectedFieldName: string | null;
  selectedCharGroupId: string | null;
  multiSelected: string[];

  // UI modes
  viewMode: 'fields' | 'preview';
  editMode: 'normal' | 'place_field';  // for flat PDF field placement
  placingField: { page: number; x: number; y: number } | null;

  // Char groups
  charGroups: CharGroup[];

  // Dirty tracking
  isDirty: boolean;
  lastSaved: Date | null;
}
```

### 4.4 Field Type System

| Type | Icon | UI Behavior | Fill Behavior |
|---|---|---|---|
| `text` | 📝 | Text input in preview | Single value → string |
| `number` | 🔢 | Number input | Formatted number |
| `date` | 📅 | Date input (YYYY.MM.DD) | Hungarian dotted date |
| `checkbox` | ☑️ | Boolean toggle | Checked/unchecked in PDF |
| `dropdown` | 📋 | Select from options list | Set choice value |
| `character_split` | 🔤 | Group of checkbox/number fields | Split value across fields |
| `money` | 💰 | Number with formatting | Space-separated thousands |

Additional metadata per field:
- `options`: string[] — for dropdown fields, list of valid choices
- `character_split_config`: { direction, separator, member_fields[] }
- `validation`: { min, max, pattern } — optional

---

## 5. PDF Viewer Integration

### 5.1 Rendering Pipeline

```
Backend:                                 Frontend:
PyMuPDF renders page → PNG bytes    →   react-pdf displays <Page>
pikepdf extracts field rects   →   Overlay <canvas> draws colored rects
metadata (type, name, value)    →   Tooltip on hover
```

### 5.2 Overlay Implementation

The overlay is a transparent `<canvas>` positioned exactly on top of the react-pdf `<Page>` component. On every render cycle:

1. Get the page's rendered dimensions from react-pdf
2. Scale field rectangles proportionally
3. Draw semi-transparent colored rectangles for each field
4. Draw field name labels inside rectangles (if zoom > 100%)

### 5.3 Field Highlight States

```
Normal state:
┌────────────────────────┐   ← green 30% opacity fill, 1px green border
│  SZA_IG_név            │
└────────────────────────┘

Selected state:
┌════════════════════════┐   ← blue 50% opacity fill, 2px blue border, dashed
║  SZA_IG_név            ║     with label above: "SZA_IG_név → participant.name"
└════════════════════════┘

Hover state:
┌────────────────────────┐   ← brighter fill, tooltip appears
│  SZA_IG_név            │
└────────────────────────┘
```

### 5.4 Flat PDF "Place Field" Mode

When the user clicks "Add Field" for a flat PDF:

1. Mode switches to `place_field`
2. User clicks on PDF → first click sets top-left corner
3. User drags → rectangle preview follows mouse
4. User releases → second click sets bottom-right corner
5. A dialog appears: "New field at page N, (x, y, w, h)" → enter field name, select canonical, set type
6. Field is added to the mapping with the coordinates

---

## 6. Character Groups — Deep Dive

### 6.1 Problem

OTP forms often split a single value across multiple PDF fields. Example:

- Adóazonosító jel (`8123456789` = 10 digits) mapped to 10 separate number fields
- Személyi igazolvány szám split across characters
- Bank account number split across groups of 4 digits

The mapping editor needs to:
1. **Detect** these patterns (consecutive similarly-named fields)
2. **Group** them visually
3. **Map** the group to a single canonical field
4. **Fill** by splitting the canonical value across member fields

### 6.2 Auto-Detection Heuristic

The backend should suggest groups by:
- Fields with the same prefix and sequential numeric suffixes (e.g., `A_NY_11_X_1`, `A_NY_11_X_2`, `A_NY_11_X_3`)
- Fields that are physically adjacent on the same page (similar Y coordinate, sequential X)
- Fields of the same type (all number, all checkbox)

Suggested groups appear as "hint" indicators in the FieldListPanel.

### 6.3 Manual Grouping

The user can also:
- Multi-select fields (Ctrl+click) → "Group as character fields"
- Drag & drop to reorder within a group
- Set direction (left-to-right, top-to-bottom)
- Set separator (empty for continuous, or "-" for grouped digits)

### 6.4 Fill Logic

When a character group is filled:
- Value is split into individual characters
- Each character is written to its corresponding member field
- If the value has fewer characters than fields, remaining fields are left empty
- If the value has more characters, it's truncated

---

## 7. User Workflows

### 7.1 Primary Workflow: Review & Correct AI Mapping

```
1. Open PDF → auto-loads existing mapping
2. Scroll through pages, visually inspect colored fields
3. Spot a wrong mapping (e.g., "TA_név" mapped to "property.parcel_number")
4. Click the field → FieldEditorPanel shows current mapping
5. Clear or change canonical field → backspace + new value from autocomplete
6. Field color changes immediately (visual feedback)
7. Continue reviewing...
8. Click Save → PUT /api/mapping/{pdf_id}
```

### 7.2 Workflow: Map an Unmapped Field

```
1. Filter list to "Unmapped" → see orange-highlighted fields
2. Click an unmapped field
3. Select canonical field from dropdown → auto-complete with Hungarian descriptions
4. Set confidence to "manual"
5. Field turns green → mapped
```

### 7.3 Workflow: Create Character Group

```
1. Notice multiple adjacent fields (e.g., tax ID digits)
2. Ctrl+click to multi-select them from the list OR from the PDF
3. Right-click → "Group as character fields"
4. In FieldEditorPanel: name the group, select canonical field
5. Fields merge into a single purple-highlighted group block on PDF
6. Adjust order if needed
```

### 7.4 Workflow: Add Field to Flat PDF

```
1. Open a flat PDF (e.g., hozzajarulo_nyilatkozat)
2. Click "Add Field" button → enters Place Field mode
3. Find a dotted line on the PDF
4. Click-drag to define rectangle over the dotted line area
5. Dialog: enter field name, select canonical path, set type
6. Field appears as new overlay rectangle
7. Coordinates are precise (pixel-level from the click)
```

### 7.5 Workflow: Trigger AI Re-recognition

```
1. Click "AI Recognize" in header
2. Select mode (auto/acroform/flat)
3. Backend runs FieldRecognizer
4. Progress bar appears
5. On completion: mapping is merged (existing manual changes preserved, AI only fills unmapped)
6. Visual refresh → new fields get AI assignments, confidence displayed
```

---

## 8. Implementation Plan

### Phase 1: Backend Foundation (2-3 hours)

**Files to create:**
- `backend/` directory
- `backend/server.py` — FastAPI app with CORS
- `backend/pdf_service.py` — PDF page rendering, field extraction
- `backend/mapping_service.py` — CRUD operations on mapping JSONs
- `backend/recognize_service.py` — thin wrapper around existing FieldRecognizer
- `backend/requirements.txt`

**Key deliverables:**
- [x] `GET /api/pdfs` — list available PDFs
- [x] `GET /api/pdf/{id}/info` — page count, has_acroform
- [x] `GET /api/pdf/{id}/page/{n}/image` — PNG page render
- [x] `GET /api/pdf/{id}/fields` — AcroForm field list with rects
- [x] `GET /api/mapping/{id}` — load mapping
- [x] `PUT /api/mapping/{id}/field/{name}` — update field
- [x] `POST /api/mapping/{id}/field` — add field
- [x] `DELETE /api/mapping/{id}/field/{name}` — remove field
- [x] `POST /api/mapping/{id}/field/group` — create char group
- [x] `GET /api/mapping/{id}/canonical-fields` — list canonical paths

### Phase 2: Frontend Foundation (3-4 hours)

**Files to create:**
- `frontend/` directory (Vite + React + TypeScript)
- `frontend/src/App.tsx` — main layout
- `frontend/src/components/Header.tsx`
- `frontend/src/components/FieldListPanel.tsx`
- `frontend/src/components/PdfViewerPanel.tsx`
- `frontend/src/components/FieldEditorPanel.tsx`
- `frontend/src/store/editorStore.ts` — Zustand store
- `frontend/src/types/index.ts` — TypeScript interfaces
- `frontend/src/api/client.ts` — API client
- `frontend/src/components/FieldOverlay.tsx` — canvas overlay

**Key deliverables:**
- [x] Three-panel layout with resizable panels
- [x] PDF viewer with page navigation and zoom
- [x] Field overlay with color-coded rectangles
- [x] Field list with search and filter
- [x] Field editor panel with canonical autocomplete
- [x] Save functionality (PUT mapping)
- [x] Loading and error states

### Phase 3: Character Groups (1-2 hours)

- [x] Multi-select in field list and PDF viewer
- [x] "Group as character fields" action
- [x] CharGroup editor UI (member list, direction, separator)
- [x] Purple overlay for grouped fields
- [x] Auto-detection heuristic in backend

### Phase 4: Flat PDF Support (1-2 hours)

- [x] "Place Field" mode toggle
- [x] Click-drag coordinate selection on PDF canvas
- [x] Coordinate editor in FieldEditorPanel
- [x] New field creation with coordinates
- [x] Backend: flat PDF field extraction (text anchor detection)

### Phase 5: Advanced Features (2-3 hours)

- [x] Preview mode — show filled values in fields
- [x] AI recognition integration with progress
- [x] Auto-save drafts (localStorage)
- [x] Undo/redo
- [x] Keyboard shortcuts (Tab between fields, Delete to unmap, G to group)
- [x] Dark mode
- [x] PDF page thumbnail strip (bottom of viewer)

### Phase 6: Polish (1-2 hours)

- [x] Responsive layout (minimally usable on tablet)
- [x] Field rect adjustment handles (drag to resize overlay field)
- [x] Batch operations (remap all fields matching pattern)
- [x] Diff view: show changes since last save
- [x] Export/import mapping JSON

---

## 9. Key Technical Decisions

### 9.1 Why PDF rendering on backend, not client-side?

- PyMuPDF gives exact field rectangles that match the fill coordinates
- react-pdf (pdf.js) can't extract AcroForm field positions reliably
- Server-side rendering ensures consistent 150 DPI output
- Pages are cached after first render

### 9.2 Why not embed existing pipeline?

The mapping editor is a **separate tool** from the filling pipeline. It:
- Runs as its own process (`python backend/server.py`)
- Has its own dependencies (FastAPI + uvicorn, not in requirements.txt)
- Serves the React frontend from a separate dev server or built static files
- Does NOT import `src/main.py` FormFillerPipeline
- DOES reuse `src/ai/field_recognizer.py` MappingConfig

### 9.3 Character groups in mapping JSON

Character groups are stored as an extension to the existing mapping format:

```json
{
  "bank_name": "OTP Bank",
  "form_name": "...",
  "form_type": "acroform",
  "fields": [ ... ],
  "character_groups": [
    {
      "group_id": "adoazonosito_jel_igenylo",
      "field_type": "character_split",
      "canonical_field": "participant.tax_id",
      "member_fields": ["A_NY_11_1", "A_NY_11_2", ...],
      "direction": "left_to_right",
      "separator": ""
    }
  ]
}
```

The `MappingConfig` class is extended to support `character_groups`. During filling, the pipeline checks if a field belongs to a character group and routes accordingly.

### 9.4 Field name conflicts (AcroForm vs flat)

Flat PDF field names are generated by the mapping creator (e.g., `"birth_name"`, `"employer_name"`). AcroForm field names come from the PDF (`"SZA_IG_név"`). The backend field listing endpoint adds a `source` field: `"acroform"` or `"overlay"` to disambiguate.

---

## 10. Data Flow Example

### Loading a PDF in the editor:

```
1. User selects "otp/Piaci hitel/Igenylesi_dokumentumok_...pdf" from dropdown
2. Frontend: GET /api/pdf/otp%2FPiaci+hitel%2F.../info  → { pages: 97, has_acroform: true }
3. Frontend: GET /api/pdf/otp%2FPiaci+hitel%2F.../fields → 1743 fields with rects
4. Frontend: GET /api/mapping/otp%2FPiaci+hitel%2F... → 374 mapped fields + char groups
5. Frontend: GET /api/pdf/otp%2FPiaci+hitel%2F.../page/1/image → PNG for page 1
6. Store updates, PDF viewer renders page 1 with field overlay
7. User can now interact
```

### Saving changes:

```
1. User edits 5 field mappings + creates 1 char group
2. Click Save
3. Frontend: PUT /api/mapping/otp%2FPiaci+hitel%2F...  ← full mapping JSON
4. Backend: writes to src/mapping/otp_piaci_hitel_mapping.json
5. Backend: returns success
6. Frontend: shows "Saved at 09:15" in status bar, clears dirty flag
```

---

## 11. Error Handling

| Scenario | Handling |
|---|---|
| PDF file not found | 404 + message "PDF not found in repository" |
| Mapping file not found | Return empty mapping template (all fields unmapped) |
| AI recognition fails | Show error in UI, keep existing mapping intact |
| Save conflict (file modified externally) | Detect via mtime, show "Reload first" dialog |
| Very large PDF (>200 pages) | Progressive page loading, virtual scroll for field list |
| Field rectangle out of page bounds | Clamp to page, show warning icon |
| Character group with no members | Validation error, don't save |
| Duplicate field name | Backend validation, reject with error |

---

## 12. Non-Goals (for v1)

- Multi-user collaboration / real-time sync
- Version history / git integration
- Direct PDF annotation (only mapping editing, not form redesign)
- Mobile support (desktop-only for v1)
- Authentication / user management (local tool only)
- Integration into the filling pipeline (standalone tool)
- Template creation from scratch (only editing existing mappings)
- Batch fill and compare (one mapping at a time)

---

*End of spec. Coverage: product overview, architecture, backend API (full endpoint catalog), frontend component tree with detailed specs, PDF viewer overlay design, character group system, all user workflows, 6-phase implementation plan, technical decisions with rationale, and error handling.*

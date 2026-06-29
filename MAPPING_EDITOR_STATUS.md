# Mapping Editor — Jelenlegi Állapot

**Dátum:** 2026-06-29  
**Branch:** `feature/mapping-editor` (commit `625fbf9`)  
**Baseline:** `c20292e` (Claude-era, 2026-06-28)

---

## 1. Cél

A FinancialGenie PDF kitöltési pipeline-hoz egy **standalone mapping editor** (FastAPI backend + React frontend), amivel:
- Láthatóvá válnak a PDF űrlapmezők a renderelt oldalakra overlayelve
- Kézi finomhangolás lehetséges az AI-generált field→canonical mappingon
- Field tulajdonságok szerkeszthetők (confidence, canonical field, notes, character groups)

**Fontos:** Az editor **standalone tool** — nem módosítja a meglévő kitöltési pipeline kódját. Saját `backend/` és `frontend/` könyvtárakban van.

---

## 2. Architektúra

```
┌─────────────┐     proxy (/api)     ┌──────────────┐
│  Frontend   │ ──────────────────→  │   Backend    │
│  React 19   │ ←──────────────────  │   FastAPI    │
│  Vite :5173 │     JSON responses   │   :8765      │
└─────────────┘                      └──────┬───────┘
                                            │
                                    ┌───────┴───────┐
                                    │  PyMuPDF      │
                                    │  pikepdf      │
                                    │  pdfs/ dir    │
                                    └───────────────┘
```

- **Frontend:** React 19 + TypeScript + Vite + Tailwind CSS + Zustand
- **Backend:** FastAPI (Python), PyMuPDF + pikepdf
- **Portok:** Backend `8765`, Frontend `5173`
- **Vite proxy:** `/api/*` → `localhost:8765`
- **Backend bind:** `0.0.0.0:8765` (externally accessible)
- **Process persistence:** tmux session-ök (`fg-be` = backend, `fg-fe` = frontend)

---

## 3. Backend (FastAPI, port 8765)

### Fájlok

| Fájl | Sorok | Leírás |
|------|-------|--------|
| `backend/server.py` | ~446 | API endpoint-ok, CORS, startup |
| `backend/pdf_service.py` | ~388 | PDF olvasás, field extraction, page rendering |
| `backend/mapping_service.py` | ~285 | Mapping CRUD, mentés/betöltés |
| `backend/recognize_service.py` | ~96 | AI field felismerés (mock) |
| `backend/config.py` | ~183 | Konfiguráció, mapping path resolution |

### API Endpoint-ok

| Method | Path | Leírás |
|--------|------|--------|
| `GET` | `/api/pdfs` | Elérhető PDF-ek listája (16 db) |
| `GET` | `/api/pdfs/{pdf_id}/fields` | PDF mezői (név, típus, oldal, koordináták) |
| `GET` | `/api/pdfs/{pdf_id}/page/{page}` | Oldal renderelése PNG-ként (150 DPI) |
| `GET` | `/api/pdfs/{pdf_id}/mapping` | Jelenlegi mapping betöltése |
| `PUT` | `/api/pdfs/{pdf_id}/mapping` | Mapping mentése |
| `PATCH` | `/api/pdfs/{pdf_id}/mapping/field/{field_name}` | Egy field mapping frissítése |
| `GET` | `/api/canonical-fields` | Kanonikus mezőlista (36 mező) |
| `POST` | `/api/pdfs/{pdf_id}/recognize` | AI felismerés indítása |

### PDF kezelés

- **16 PDF** támogatott: AcroForm sample, flat/overlay PDF-ek, OTP master PDF-ek (Otthon Start, Piaci hitel, Szabadfelhasználású stb.)
- **Master OTP PDF:** 97 oldal, 2171 mező
- **Támogatott mezőtípusok:** text (235), checkbox (653), number (55), date (18), character_split
- **Oldal renderelés:** PyMuPDF, 150 DPI, PNG output

---

## 4. Frontend (React + Vite, port 5173)

### Fájlok

| Fájl | Sorok | Leírás |
|------|-------|--------|
| `src/main.tsx` | ~10 | Entry point |
| `src/App.tsx` | ~52 | Layout wrapper, PDF selector |
| `src/store.ts` | ~191 | Zustand globális state |
| `src/api/client.ts` | ~236 | API kliens, összes endpoint wrapper |
| `src/types/index.ts` | ~111 | TypeScript típusdefiníciók |
| `src/components/PdfSelector.tsx` | ~62 | PDF választó dropdown |
| `src/components/PdfViewerPanel.tsx` | ~207 | PDF oldal renderer + overlay |
| `src/components/FieldListPanel.tsx` | ~164 | Mezőlista bal panel |
| `src/components/FieldEditorPanel.tsx` | ~296 | Mezőszerkesztő jobb panel |

### Three-Panel Layout

```
┌──────────┬──────────────────────┬──────────┐
│ Field    │   PDF Viewer         │ Field    │
│ List     │   (oldal kép +       │ Editor   │
│          │    overlayek)        │          │
│          │                      │          │
│ - név    │   ◀ pagination ▶    │ - can.   │
│ - típus  │   🔍 zoom           │ - conf   │
│ - oldal  │                      │ - notes  │
│ - státusz│                      │ - groups │
└──────────┴──────────────────────┴──────────┘
```

### Jellemzők
- **Color-coded overlayek:** zöld=mapped, sárga=unmapped, piros=selected, kék=character-group
- **Pagination:** oldalak között léptetés
- **Zoom:** ± gombok a nagyításhoz/kicsinyítéshez
- **Place-field mode:** flat PDF-ekhez, field pozícionálás (UI kész, backend nem implementált)
- **Natural image rendering:** `maxWidth: "none"` a scale probléma elkerülésére

---

## 5. Javított Bugok

### Bug 1: Oldalszám hiba
- **Probléma:** Mind a 2171 mező az 1. oldalon jelent meg
- **Ok:** `page_index` (pikepdf `page.obj.objgen[0]`) nem működött master OTP PDF-re
- **Fix:** `_patch_page_numbers_from_mupdf()` — PyMuPDF `page.widgets()` alapján épített `{field_name → page_number}` mapping
- **Fix helye:** `backend/pdf_service.py`

### Bug 2: Image natural-size rendering
- **Probléma:** A renderelt PDF oldal kép kisebb volt mint a természetes mérete
- **Ok:** Tailwind `max-w-full` CSS property lekicsinyítette a böngészőben
- **Fix:** `max-w-full` eltávolítva, `maxWidth: "none"` inline style
- **Fix helye:** `frontend/src/components/PdfViewerPanel.tsx`

### Bug 3: Y-koordináta dupla flip (root cause)
- **Probléma:** Overlayek szisztematikusan el voltak csúszva függőlegesen. A lap tetején lévő mezőknél a legnagyobb (~1051px), alul csökkenő offset.
- **Ok:** A pikepdf `/Rect` értékei ennél a PDF-nél már eleve top-left origóban vannak (néhány PDF producer így ír), de `_widget_rect_px` megint megfordította az Y-t: `(page_h - y1) * RENDER_SCALE` — dupla flip
- **Fix:** `_patch_page_numbers_from_mupdf` kiterjesztve — PyMuPDF `widget.rect`-ből (mindig standard PDF user space, bottom-left origó) számoljuk a koordinátákat
- **Fix helye:** `backend/pdf_service.py`

### Bug 4: Mapping path resolution
- **Probléma:** Bizonyos PDF-ekhez nem találta a mapping fájlt
- **Ok:** A parent könyvtár slug-ja és a mapping fájl neve nem egyezett
- **Fix:** `mapping_path_for()` — word-level overlap + cross-directory alias fallback
- **Fix helye:** `backend/config.py`

---

## 6. Verifikáció

| PDF | Mezők | Státusz |
|-----|-------|---------|
| acroform_sample | 74 | ✅ Minden overlay pontos |
| OTP Otthon Start | 2171 | ✅ 97 oldalra elosztva, koordináták pontosak |
| OTP Piaci hitel | 2203 | ✅ |
| OTP Szabadfelhasználású | 2171 | ✅ |
| Összes többi PDF | változó | ✅ Page distribution helyes |

---

## 7. Deployment

### Indítás

```bash
# Backend
cd financialgenie/backend
tmux new-session -d -s fg-be 'python server.py'
# → port 8765

# Frontend
cd financialgenie/frontend
tmux new-session -d -s fg-fe 'npx vite --host'
# → port 5173, proxy /api → localhost:8765
```

Vagy a `START.sh` script-tel:
```bash
./START.sh
```

### Elérhetőség
- **Publikus URL:** `http://165.232.112.125:5173`
- **Backend API:** `http://165.232.112.125:8765/api/`

### Tmux session-ök
- `tmux attach -t fg-be` — backend logok
- `tmux attach -t fg-fe` — frontend logok

---

## 8. Konfigurációs Kulcsértékek

| Kulcs | Érték | Hely |
|-------|-------|------|
| `RENDER_DPI` | 150 | `backend/config.py` |
| `RENDER_SCALE` | 150/72 ≈ 2.083 | számolt |
| `PDF_DIR` | `../pdfs/` | `backend/config.py` |
| `MAPPINGS_DIR` | `../mappings/` | `backend/config.py` |
| `CANONICAL_FIELDS` | 36 mező | `backend/config.py` |

---

## 9. Függőségek

### Backend (`requirements.txt`)
- fastapi, uvicorn
- PyMuPDF (fitz)
- pikepdf
- Pillow
- pydantic

### Frontend (`package.json`)
- react, react-dom (v19)
- zustand (state management)
- vite (bundler)
- typescript
- tailwind css (v4)
- @vitejs/plugin-react

---

## 10. Ami Még Hiányzik / TODO

### Nem implementált
- **Place-field mode backend:** Flat/overlay PDF-ekhez új mező pozícionálás — frontend UI kész, backend nem
- **AI recognize valódi implementáció:** Jelenleg mock (`recognize_service.py`), valódi AI integráció nincs
- **Character-group editor:** Karakter-csoportok (pl. adóazonosító 10 mezőre bontva) részletes szerkesztése
- **Multi-field batch edit:** Több mező egyszerre szerkesztése
- **Undo/redo:** Mapping változások visszavonása

### Ismert limitációk
- Csak **AcroForm** PDF-ek mezői jelennek meg overlay-ként. Flat/overlay PDF-eknél a "place-field" mode backend nélkül nem működik
- Az AI recognize endpoint mock — mindig ugyanazt a teszt eredményt adja vissza
- Nincs autentikáció — publikus porton fut
- Mapping fájlok csak lokálisan, a `mappings/` könyvtárban tárolódnak (nincs adatbázis)

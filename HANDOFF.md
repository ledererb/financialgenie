# FinancialGenie — Handoff (2026-10-06)

Teljes projekt-csomag áttelepítéshez. **A ZIP tartalmazza a `config/.env` fájlt is
(SF + Anthropic hitelesítésekkel) — kezeld ennek megfelelően!**

---

## 1. Mi ez a projekt

Automatizált OTP Bank nyomtatvány-kitöltő: a banki AcroForm PDF-ek mezőit a
Salesforce-ból (sandbox) származó ügyletadatokkal tölti ki, egy mapping-rétegen
(pdf mező → kanonikus SF mező) keresztül. Két fő rész:

- **Backend** (FastAPI, `backend/server.py`, port 8765): pipeline (`src/main.py`),
  SF-klient (`src/integrations/salesforce_client.py`), mapping-szolgáltatás
  (`backend/mapping_service.py`), AI mezőfelismerő (`src/ai/field_recognizer.py`)
- **Frontend** (React+Vite, `frontend/`): Dokumentumtár (bank→termék→dokumentum),
  Mapping Studio (PageEditor: oldal-nézet overlay-ekkel, checkbox-csoportosítás,
  digit-sor csoportok, fill rule-ök), Generálás nézet (deal-választás, PDF generálás,
  SF feltöltés, notice-ok megjelenítése)

## 2. Gyors indulás (új gépen)

```bash
# Backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env: config/.env (már a csomagban) — SF_DOMAIN=test (sandbox)
uvicorn backend.server:app --port 8765

# Frontend
cd frontend && npm install && npm run dev   # vite dev server
```

Docker-alternatíva: `docker-compose.yml` (backend + nginx frontend).

## 3. Éles szerver (ahol most fut)

### 3a. Távoli elérés — hogyan éred el futtató gépről

| Mit | Hogyan |
|---|---|
| **Web UI (Mapping Studio, Generálás)** | böngészőben: **http://209.38.249.101:3000** |
| **Backend API** | ugyanazon a porton, nginx proxy-z: **http://209.38.249.101:3000/api/...** (pl. `/api/mapping/canonical-fields`, `/api/pdf/validate`) |
| **Backend közvetlen (8765)** | NINCS publikálva a host felé — csak SSH-n: `docker exec financialgenie-backend python -c "..."` vagy SSH-tunnel |
| **SSH (terminál)** | `ssh -i ~/.ssh/financialgenie_do root@209.38.249.101` |

⚠️ A 8765-ös port szándékosan nincs kipublikálva; a UI-n keresztül (nginx
`/api/` proxy) minden elérhető. Külső gépről aUI + API tehát egyetlen címen
fut: `http://209.38.249.101:3000`.

⚠️ **A privát SSH-kulcs (`~/.ssh/financialgenie_do`) NINCS a ZIP-ben** — azt
külön, biztonságosan kell átmásolni (pl. AirDrop/pendrive, `chmod 600`).

### 3b. Szerver-topológía és deploy

- Projekt a szerveren: `/opt/financialgenie`
- Konténerek: `financialgenie-backend` (8765, nem publikált — `docker exec`-tel
  hívandó), `financialgenie-frontend` (0.0.0.0:3000 → nginx 80)
- **Deploy-folyamatok**:
  - mapping JSON: `scp` → `/opt/financialgenie/src/mapping/` (mountolt, restart nem kell)
  - backend kód: `scp` → `/tmp/` → `docker cp` → `/app/src/...` → `docker restart financialgenie-backend`
  - frontend: `npm run build` → `tar czf dist.tgz -C dist .` → `docker cp` → kicsomagolás `/usr/share/nginx/html`
- A kód a konténer image-be van sütve — a szerveren NINCS git pull!

## 4. ⚠️ Ismert blokkoló hiba (2026-10-06 óta)

**A SF sandbox integrációs user jelszava LEJÁRT**:
`INVALID_OPERATION_WITH_EXPIRED_PASSWORD — Your password has expired.`

Minden SF-lekérdezés (validate/generate/field-values) 500-zal elhasal. Teendő:
1. SF sandbox admin: jelszó reset az integrációs usernek
2. új jelszó + security token beírása a `config/.env`-be (szerveren is!)
3. `docker restart financialgenie-backend`
A mapping-editor UI ettől függetlenül működik (a katalógus statikus).

## 5. Frissen deployed állapot (mit tud a rendszer most)

- **Konstrukció-gate**: `#piaci` / `#tamogatott` gate-elt canonical kulcsok —
  a 3.1/3.2 szekció csak a megfelelő hiteltípusnál töltődik; argmax
  checkbox-illesztés (`_classify_construction`, `_enrich_derived_values` a `main.py`-ben)
- **Nem-kitöltés**: Salutation-fordítás (`_VALUE_TRANSLATIONS`) + magyar
  keresztnév→nem becslés (`src/utils/gender.py`), mindig notice-szal
- **Dátum-fragmentek**: `<canonical>#ev` / `#ho` („mióta lakik" dobozok)
- **Digit-sor csoportok**: `src/mapping/digit_row_grouper.py` (geometriai CLI-eszköz),
  41 character_group az Igénylési dokumentum mappingjében
- **Account-névtér**: `Opportunity.AccountId` → Account rekord lekérés, kontakt-pool
  (`SELECT ... WHERE AccountId`), duplikát-enrichment (üres mezők pótlása, sosem
  felülír), `Account.*` canonical routing + 17 szabványos Account mező a katalógusban
- **Notice-ok**: egységesen a `_prepare_field_data` choke-pointból, a frontend
  GenerateView amber paneljén jelennek meg
- **UI**: objektum-szűrő chipsek a canonical keresőben (Account/Contact/Lead/
  Opportunity/loan), vizuális checkbox pick-mode a vásznon, „Mindig pipál" toggle
  a mezőkártyán

## 6. Következő lépések (csütörtöki egyeztetés tárgya)

1. **Új SF mezők** (István viszi fel): ezekután a
   `src/ai/field_recognizer.py` → `CANONICAL_FIELDS` + `backend/mapping_service.py`
   → `SF_FIELD_TYPES` katalógust bővíteni, majd AI összerendelés újrafuttatása
2. Párosítási irányelv a modelljük szerint: személyes adat → `Contact.*`,
   hiteladat → `Opportunity.*`, ügyfél-szintű alapadat → `Account.*`
   (a régi szintetikus `Contact.Loan_*` kulcsok visszamenőleg működnek)
3. Backlog: picklist-fordítás (Salutation úr/hölgy), „mióta lakik" routing
   további dokumentumokra, 3.1-es szekció szabály finomítása, egyéb
   dokumentumok recognition-újrafuttatása

## 7. Fontos fájlok

| Fájl | Mi |
|---|---|
| `src/main.py` | FormFillerPipeline: field_data építés, resolution, notice-ok |
| `src/integrations/salesforce_client.py` | SF SOQL, OCR-szerep feloldás, Account pool |
| `src/models/canonical_model.py` | DealData/Participant/LoanDetails |
| `src/engine/pdf_filler.py` | AcroForm-író, karaktercsoport-expand |
| `backend/mapping_service.py` | mapping CRUD, canonical katalógus, validáció |
| `backend/server.py` | FastAPI endpointok |
| `src/mapping/*.json` | a mapping-ek (Igénylési dokumentum = legfrissebb) |
| `config/.env` | ⚠️ hitelesítések (SF, Anthropic) |
| `PLAN_CHECKBOX_GROUPS.md`, `DOCUMENT_AGNOSTIC.md`, `FEJLESZTESI_BRIEF.md` | spec/tervek |

## 8. Adatvédelmi megjegyzés

A `samples/`, `otp/` (űrlapok), `documents/` és a korábbi `output/` PDF-ek
valós személyes adatokat tartalmazó tesztadatokat hordoznak (név, cím,
okmányszám). A ZIP-ben az `output/` nincs benne (szerveren újragenerálható),
a többi a működéshez kell — a csomagot bizalmasan kezelendő.

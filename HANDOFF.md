# FinancialGenie — Handoff (2026-10-28)

Teljes projekt-dokumentáció és üzemeltetési átadás (Handoff). **A csomag és a szerver a `config/.env` fájlban élő Salesforce és Anthropic hitelesítő adatokat tartalmaz — kezeld bizalmasan!**

---

## 1. Mi ez a projekt?

Automatizált, bank-agnosztikus (OTP Bank, CIB Bank és további bankok) nyomtatvány-kitöltő platform: a banki AcroForm és lapos (overlay) PDF nyomtatványok mezőit az éles Salesforce-ból származó ügylet-, szereplő- és ingatlanadatokkal tölti ki egy AI-támogatott mapping rétegen (`PDF mező → Genie kanonikus mező`) keresztül.

> **2026-10-29 — Genie canonical cutover:** a mapping réteg, a kitöltő pipeline, a Mapping Studio és az AI-felismerő teljesen átállt a Salesforce-név-független **Genie kanonikus útvonalakra** (`src/canonical/` registry + resolver: pl. `participant.borrower.birth_date`, `property[2].parcel_number`). Az aktív mappingekben (`src/mapping/*.json`) többé nincs `Contact.*` / `Lead.*` / `Opportunity.*` / `Account.*` canonical út. A végső offline igazolás: `docs/superpowers/reports/2026-10-29-genie-cutover-verification.md`; Salesforce-oldali előkészítés: `docs/SALESFORCE_SETUP_RUNBOOK.md`.

- **Frontend (Vercel — `https://financialgenie.vercel.app`)**: React + Vite + Tailwind alkalmazás (`frontend/`).
  - **Belépés és jogosultságkezelés**: Adminisztrátor (`admin`) és Ügyintéző/Tag (`member`) szerepkörök.
  - **Dokumentumtár**: Bank → Termék → Dokumentum hierarchia, PDF feltöltés, Master PDF darabolás, státuszkezelés.
  - **Mapping Studio (`PageEditor`)**: Vizuális PDF-oldal nézet interaktív mező-overlayekkel, AI mezőfelismerés indítása, kanonikus mező kereső, checkbox-csoportok, karakterdoboz-csoportok (digit-sorok) és kitöltési szabályok (`fill_rule`) szerkesztése.
  - **Generálás (`GenerateView`)**: Kereshető Salesforce ügyletválasztó (combobox név és Deal ID szerinti szűréssel), adós/adóstárs szerep-feloldás, hiányzó adatok ellenőrzése (`CompletenessChecker`), egyedi PDF vagy teljes csomag (ZIP) generálása és letöltése.
- **Backend (Hetzner VPS Docker — `2.29.113.104:3000`)**: FastAPI (`backend/server.py`, belső port `8765`, Nginx reverse proxy mögött a `3000`-es porton).
  - **Kitöltő pipeline (`src/main.py`)**: `FormFillerPipeline` — Salesforce adatlekérés, normalizálás, kanonikus routing, futásidejű karakterdoboz- és dátum-fragment szétbontás, checkbox/rádiógomb feloldás. A cutover után minden mező-/szabályfogyasztó (közvetlen mező, checkbox-csoport, fill rule, points, karaktercsoport, dátumfragment) a **`CanonicalResolver`**-en (`src/canonical/`) keresztül old fel Genie útvonalakat; a PDF-mezőnév sosem választ szereplőt — a dokumentumpéldányok explicit `(role, index)` kontextust kapnak. Legacy canonical út aktív mappingben futásidőn/elmentéskor hibát dob (422 a mentési API-n).
  - **Kanonikus registry (`src/canonical/`)**: kurált, verziózott mezőkatalógus (253 alapmező, 5 szerep: `borrower/co_borrower/guarantor/mortgagor/usufructuary`; a `/api/mapping/canonical-fields` végpont 6487 validált Genie utat szolgál ki). Ezt használja a Studio keresője, a backend validáció és az AI-felismerő promptja is.
  - **PDF kitöltőmotor (`src/engine/pdf_filler.py`)**: `AcroFormFiller` (pikepdf + PyMuPDF + egyedi magyar `Ő/Ű/ő/ű` glyph appearance generálás) és `OverlayFiller` (ReportLab).
  - **AI mezőfelismerő (`src/ai/field_recognizer.py`)**: Claude Sonnet alapú vizuális + AcroForm batch mezőfelismerés automatikus utófeldolgozással (`_postprocess_recognized_fields`).

---

## 2. Éles elérés és belépési adatok

### 2a. Publikus elérés (Ügyfél / Felhasználói felület)
- **Webes felület (HTTPS)**: **https://financialgenie.vercel.app**
- A Vercel proxyzza az összes `/api/*` kérést (a `vercel.json` rewrite szabályon keresztül) a Hetzner backend szerverre (`http://2.29.113.104:3000/api/*`), így a böngésző felé minden kommunikáció titkosított HTTPS-en zajlik.

### 2b. Beállított felhasználói fiókok

| Szerepkör | Felhasználónév | Jelszó | Jogosultság |
|---|---|---|---|
| **Adminisztrátor (`admin`)** | `admin` | `Admin123!` | Teljes hozzáférés: Dokumentumtár szerkesztése, új sablon feltöltése, AI felismerés, mező-leképezés módosítása, dokumentumgenerálás, felhasználók kezelése |
| **Ügyintéző / Tag (`member`)** | `user` | `Tag123!` | Olvasási jog a Dokumentumtárhoz + teljes Dokumentumgenerálás, validáció és PDF/ZIP letöltés (sablonokat és mappinget nem módosíthat) |

> Új felhasználókat bármikor létrehozhatsz vagy törölhetsz az Adminisztrátor fiókkal belépve a felső menüsor **Felhasználók** gombjára kattintva. A felhasználók az SQLite adatbázisban (`catalog/catalog.db`, `users` tábla) tárolódnak PBKDF2-SHA256 jelszó-hash-sel.

---

## 3. Szerver-infrastruktúra és Deploy folyamat

### 3a. Topológia

| Komponens | Hely | Elérés |
|---|---|---|
| **GitHub repo** | `https://github.com/ledererb/financialgenie` (`main` branch) | `git push origin main` (automatikusan triggereli a Vercel frontend deployt) |
| **Frontend (Vercel)** | `financialgenie.vercel.app` | Konfiguráció: `vercel.json` (gyökérben) + `frontend/vercel.json` |
| **Backend VPS (Hetzner)** | `2.29.113.104` (`/opt/financialgenie`) | `ssh -i ~/.ssh/financialgenie_do root@2.29.113.104` |
| **Nginx / Frontend konténer** | `financialgenie-frontend` (`0.0.0.0:3000 -> 80/tcp`) | Proxyzza az `/api/` hívásokat a `financialgenie-backend:8765` konténernek |
| **FastAPI Backend konténer** | `financialgenie-backend` (`8765/tcp`, belső Docker hálózaton) | `docker exec -it financialgenie-backend ...` |

### 3b. Kódfrissítés és Deploy lépések

1. **Frontend / Vercel frissítés**:
   ```bash
   git add .
   git commit -m "feat/fix: ..."
   git push origin main
   ```
   A `git push origin main` után a Vercel automatikusan újraépíti és élesíti a frontendet.

2. **Backend kód frissítése a Hetzner szerveren**:
   A `src/` vagy `backend/` módosításait másold fel `scp`-vel a `/opt/financialgenie` alá, majd építsd újra a backend konténert:
   ```bash
   scp -i ~/.ssh/financialgenie_do src/main.py root@2.29.113.104:/opt/financialgenie/src/main.py
   ssh -i ~/.ssh/financialgenie_do root@2.29.113.104 "cd /opt/financialgenie && docker compose up -d --build backend"
   ```
   *(Megjegyzés: a `/opt/financialgenie/src/mapping`, `/opt/financialgenie/catalog`, `/opt/financialgenie/samples`, `/opt/financialgenie/otp` és `/opt/financialgenie/output` könyvtárak Docker volume-ként fel vannak csatolva, így a mapping JSON-ok és feltöltött PDF-ek konténer-újraépítéskor is megmaradnak.)*

---

## 4. Salesforce integráció (`config/.env`)

Az éles Salesforce (`login` domain) kapcsolat a `config/.env` fájlban van beállítva (helyben és a szerveren a `/opt/financialgenie/config/.env` alatt):

- `SF_USERNAME=agnes.fischer@gmail.com`
- `SF_DOMAIN=login`
- `SF_MOCK_MODE=false`

> ⚠️ **Séma-snapshot előfeltétel (2026-10-29 cutover utáni élő módhoz):** az élő `SalesforceClient` induláskor **kötelezően** metaadat-snapshotból (`SF_SCHEMA_PATH` vagy `config/sf_schema_snapshot.json`) olvassa az objektum-/mezősémát, és **fail-closed**: snapshot nélkül/olvashatatlan snapshot esetén `RuntimeError`-ral indul újra, describe- vagy mock-fallback NINCS. A snapshot a **describe-only** `scripts/sync_sf_schema.py`-val készül (rekordot soha nem kérdez), explicit `SF_ORG_ID` megadásával, emberi jóváhagyás után — folyamat: `docs/SALESFORCE_SETUP_RUNBOOK.md` §5. **A live describe-snapshot ebben a környezetben MÉG NEM készült el** (nem volt `SF_ORG_ID`); az éllesítés előfeltétele. Backend oldalon: ha az élő hitelesítő adatok hiányoznak és `SF_MOCK_MODE` nem `true`, az API **503**-at ad, dummy adat nélkül.

**Működési jellemzők (`src/integrations/salesforce_client.py` + `src/integrations/salesforce_schema.py` + generált `src/integrations/salesforce_field_map.json`):**
- **Ügyletlista (`list_deals`)**: Lekérdezi a legutóbbi 500 Opportunity rekordot (`ORDER BY CreatedDate DESC LIMIT 500`), amelyet a frontend kereshető combobox-ban jelenít meg.
- **Szereplők feloldása**: Elsődlegesen az `OpportunityContactRole` táblából olvassa ki az `Adós`, `Adóstárs`, `Kezes`, `Haszonélvező` szerepeket (Contact-ID szerinti deduplikálással, `adós/adóstárs_N/kezes_N/zálogkötelezett_N/haszonélvező_N` indexeléssel; a zálogkötelezett külön szerep a kezestől), fallbackként pedig az Opportunity lookup mezőit és az `Account` kontakt-poolját használja.
- **Account kontakt-pool enrichment (`_enrich_contacts_from_pool`)**: Ha egy szereplőnek több (duplikált) Contact rekordja van az Account alatt, az üresen maradt mezőket (születési adatok, lakcím, irányítószám, adóazonosító, okmányszámok, munkáltató) automatikusan pótolja a teljesebb donor rekordból (csak explicit leképezett skalár mezőkre).
- **Ingatlanok (`Property__c`)**: Elsődlegesen az `Opportunity_Property_Role__c` kapcsolótáblán, fallbackként `Account__c` szerint; lekéri az ügylethez/Accounthoz tartozó ingatlanokat (`Célingatlan` előre rendezve), elkerüli a közterület-jelleg duplikálását (pl. `Csók István utca` + `utca` → `Csók István utca`), és a helyrajzi számot mind az `Ingatlan_hrsz__c`, mind az `Ingatlan_megjegyzes__c` (valamint fallbackként a `Contact.Town_or_possibly_the_exact_address__c`) mezőkből feloldja.
- **Kapcsolódó sorok (`Jovedelem__c`, `Meglevo_hitel__c`)**: szereplőnként (`Contact__c` lookup, `ORDER BY Sorszam__c, CreatedDate, Id`, `IN`-batch) jövedelem- és meglévő-hitelsorok; a lekérdezés csak akkor fut, ha a séma-snapshot megerősíti az objektumot és a lookupot — különben 1 figyelmeztetés + üres lista, kitalált sor sosem készül. (Ezek az objektumok a Setup-runbookben javasolt sémával org-oldali létrehozást igényelnek.)
- **SOQL mezőlisták describe-szűrése**: az élő lekérdezések mezőlistája a snapshot `available_fields` szűrésén megy keresztül; a hiányzó mezőkről összegzett warning készül.

---

## 5. Automatikus dokumentumfeldolgozás és PDF-kitöltés (OTP + CIB + új sablonok)

A rendszer úgy lett felépítve, hogy új PDF nyomtatványok feltöltésekor és kitöltésekor **ne kelljen dokumentumonként egyedi javításokat végezni**:

1. **Automatikus karakterdoboz (digit-box / irányítószám) felismerés és szétbontás**:
   - **Felismeréskor (`src/ai/field_recognizer.py` — `_detect_character_groups`, `_postprocess_recognized_fields`)**: Geometriailag felismeri az egy sorban lévő karakterdobozokat, létrehozza a `character_groups` definíciókat, és megtartja a tagmezőkön is a `canonical_field` értéket (hogy a UI-ban is látszódjon).
   - **Futásidőben (`src/main.py` — `_infer_runtime_character_groups`)**: Akkor is automatikusan felismeri és karakterenként (`9 | 7 | 0 | 0`) tölti ki az egymást követő számozott mezőket (`postalCodePart0..3`, `irsz_1..4`, `(1. jegy)..(4. jegy)`), ha a mapping JSON-ból hiányzik a `character_groups`, vagy ha a felhasználó a UI-ban utólag módosította a mezők leképezését.

2. **Rádiógombok és Checkbox-csoportok automatikus kezelése**:
   - **PDF állapot-normalizálás (`src/engine/pdf_filler.py` — `_norm_pdf_state_name`, `_match_pdf_state`)**: Automatikusan kezeli a `/0`, `/1`, `/TRUE`, `/FALSE`, `/1,2` és a karakterkódolási hibás (mojibake / `#XX` hex-escaped, pl. `VÃ¡lasztÃ¡s2` ↔ `Választás2`) PDF `/AP/N` export-állapotokat, és rekurzívan szinkronizálja a `/V` és `/AS` értékeket.
   - **Szinonima- és logikai következtetés (`src/main.py` — `_opt_matches`, `_prepare_field_data`)**:
     - Összepárosítja az eltérő megfogalmazású Salesforce és banki opciókat (pl. SF `Használt lakóház` → `Lakóingatlan` / `Lakó-üdülő ingatlan telekkel`; SF `Használt lakóingatlan vásárlás` → `Lakásvásárlás` / `Lakás/Lakóház vásárlás`).
     - Ha egy `Igen`/`Nem` rádiógomb-csoport összeg- vagy szerepmezőre van kötve (pl. `Lead.Tervezett_CSOK_Plusz__c` vagy `Lead.Ingatlan_szerepe__c`), automatikusan levezeti belőle az `Igen` / `Nem` választ.
     - Megakadályozza, hogy a duplikált `fill_rule` (`match_value`) felülírja a már feloldott `checkbox_group` értékét, vagy hogy önálló checkboxokba nem-logikai szöveg (pl. `"Aktív"`) kerüljön.

3. **Zárt legördülő listák (`/Ch`) és szétbontott dátummezők (`év` / `hónap` / `nap`)**:
   - **Zárt ComboBox védelem (`src/engine/pdf_filler.py`)**: Nem ír be szabad szöveget olyan PDF dropdown mezőbe, amelynek `/Opt` listájában az adott érték nem szerepel (és a PyMuPDF fallback sem írja felül a szándékosan kihagyott mezőket).
   - **Automatikus dátum-fragment (`src/main.py` — `_infer_date_fragment`)**: Ha egy dátummező külön `év`, `hónap`, `nap` mezőkre van bontva a PDF-ben, külön fragment-konfiguráció nélkül is automatikusan csak a megfelelő dátumrészt írja be.

4. **Jogi nyilatkozatok — biztonsági politika (Cutover Task 5, 2026-10-29)**:
   - Az `altalanos_nyilatkozatok` mapping a valódi AcroForm widgetekből lett újraépítve (207 checkbox + 5 szövegmező), a `src/mapping/legal_defaults.json` minden kategóriája `default_value: null` és `manual`/`crm_consent` módú — **semmilyen jogi választás nem kerül automatikusan bepipálásra találgatásból**.
   - Csak a 4 ember által review-olt, tisztán tudomásulvételi pont (1., 2., 9., 14. pont) töltődik a points/rule-2 indexelt szerepcímzéssel; minden más (KHR, NAV, pénznem, cselekvőképesség, felhatalmazások, hozzájárulások) a generálás eredményében **`manual_declarations` kézi döntési listaként** jelenik meg, és nem számít automatikusan kitöltött mezőnek. Hozzájárulás csak explicit CRM consent canonical út (`participant.{role}.aml_consent` / `data_processing_consent` / `khr_consent` / `nav_consent`) ember által jóváhagyott mappinggel pipálható — jelenleg egy sincs bedrótozva (szándékos). Részletek: `docs/SALESFORCE_SETUP_RUNBOOK.md` §6.

---

## 6. Fontos fájlok jegyzéke

| Fájl | Szerep |
|---|---|
| `src/main.py` | `FormFillerPipeline`: kanonikus adat-összeállítás, futásidejű digit-box és dátum-fragment felismerés, checkbox-csoport és `fill_rule` motor |
| `src/engine/pdf_filler.py` | `AcroFormFiller` és `OverlayFiller`: PDF mezők kitöltése, `/AP/N` rádiógomb/checkbox szinkronizáció, magyar ékezetes szöveg-appearance generálás |
| `src/ai/field_recognizer.py` | `FieldRecognizer`: AcroForm és lapos PDF AI mezőfelismerés, karakterdoboz-csoport detektálás, automatikus utófeldolgozás |
| `src/integrations/salesforce_client.py` | `SalesforceClient`: élő Salesforce SOQL lekérdezések, OCR szerepfeloldás, Account kontakt-pool enrichment |
| `backend/server.py` | FastAPI végpontok (`/api/auth/*`, `/api/catalog/*`, `/api/pdf/*`, `/api/mapping/*`, `/api/sf/deals`, `/api/catalog/generate-package`) |
| `backend/auth_service.py` | Felhasználó- és tokenkezelés (`admin` / `member` szerepkörök, SQLite `users` tábla) |
| `backend/mapping_service.py` | Mapping JSON betöltés/mentés, kanonikus mezőkatalógus |
| `frontend/src/components/GenerateView.tsx` | Dokumentumgenerálás nézet kereshető ügyletválasztóval (combobox) |
| `src/mapping/*.json` | A sablonokhoz tartozó mapping konfigurációk (OTP és CIB nyomtatványok) — kizárólag Genie kanonikus útvonalakkal (`approved: false`, éles használat előtt jóváhagyandók) |
| `src/canonical/` + `src/integrations/salesforce_field_map.json` | Genie kanonikus registry/parser/resolver; generált Salesforce API-név → kanonikus cél mezőtérkép |
| `src/integrations/salesforce_schema.py` | `SalesforceSchema`: strict séma-snapshot értelmezés + describe-alapú SOQL mezőszűrés az élő módhoz |
| `scripts/sync_sf_schema.py` | Describe-only, rekordot soha nem kérdező séma-snapshot sync (`SF_ORG_ID` kötelező; lsd. `docs/SALESFORCE_SETUP_RUNBOOK.md`) |
| `scripts/migrate_mappings_to_genie.py` + `scripts/rebuild_altalanos_nyilatkozatok_mapping.py` | Determinista, idempotens mapping-migrátor (audit/rollback: runbook §7) és a nyilatkozat-mapping PDF-widgetekből történő újraépítője (`--check` mód) |
| `docs/superpowers/reports/2026-10-29-genie-cutover-verification.md` | A cutover offline igazolása (migrátor-audit, OTP csomag- és CIB smoke) |
| `docs/superpowers/reports/2026-10-29-live-e2e-verification.md` | Valós Salesforce-adatú GUI + API E2E: 11/11 PDF, valós mezőértékek és hitelesített ZIP letöltés |
| `docs/SALESFORCE_SETUP_RUNBOOK.md` | Salesforce Setup-runbook: Lead→Opportunity kockázatok, `Property__c` bővítés, `Jovedelem__c`/`Meglevo_hitel__c` séma, consent/audit mezők, snapshot-folyamat, deployment-óvintézkedések |
| `vercel.json` | Vercel frontend build és `/api/*` → Hetzner backend reverse proxy konfiguráció |
| `config/.env` | ⚠️ Hitelesítő adatok (`SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `ANTHROPIC_API_KEY`) |

---

## 7. Tesztelés

A teljes automata tesztcsomag helyben egyetlen paranccsal futtatható:

```bash
python3 -m pytest -q
```

Jelenlegi állapot (2026-10-29, Genie cutover után): **886 passed, 4 skipped, 8 deselected**.
A 4 skip a repositoryban nem jelen lévő `acroform_sample.pdf` miatt van, a 8
deselect a `pytest.ini`-ben beállított `-m "not live_api"` (élő AI/Salesforce
hitelesítő adatot igénylő) tesztek kizárása. Frontend oldalon:

```bash
npm --prefix frontend run typecheck   # tsc --noEmit (app + node config) — jelenleg hibátlan
npm --prefix frontend run build       # vite production build — jelenleg zöld
```

A cutover elfogadási bizonyítékai (migrátor-audit, OTP-csomag- és CIB-smoke,
ismert korlátok): `docs/superpowers/reports/2026-10-29-genie-cutover-verification.md`.

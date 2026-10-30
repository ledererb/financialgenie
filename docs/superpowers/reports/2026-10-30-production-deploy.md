# Éles deploy és valós E2E ellenőrzés — 2026-10-30

## Deployált állapot

- `main`: `03778ae feat: migrate document filling to Genie canonical model`
- E2E fix: `5997d4b fix: resolve legacy OTP paths and product fallback`
- Audit fix: `03778ae fix: correct PDF appearance and income mapping`
- Infra fix: `453c8f3 chore: mount Salesforce schema snapshot in backend`
- Frontend: Vercel, `https://financialgenie.vercel.app`
- Backend: Hetzner Docker, `financialgenie-backend` healthy.

## Deploy lépések

1. Friss teljes verifikáció: **926 backendteszt**, frontend typecheck és production build zöld.
2. `feat/genie-canonical-migration` push `main`re, Vercel automatikus frontend deploy.
3. Backend `src/`, `backend/`, `scripts/` és describe-only sémasnapshot feltöltése.
4. Backend konténer újraépítése és egészségellenőrzés.
5. A snapshot bind mount javítása: `config/sf_schema_snapshot.json` read-only a konténerben. Az első mount egy üres könyvtár volt; a tényleges JSON fájl pótlása és force-recreate után a backend ismét healthy.

## Éles API ellenőrzés

- `GET /api/health`: `200 {"ok":true}`.
- Admin login: `200`, admin szerepkör.
- `GET /api/sf/deals`: `200`, **261 ügylet**.
- Valós csomaggenerálás `upload_to_sf=false` mellett: **9/9 dokumentum sikeres, 0 hiba, notices=[]**.
- Hitelesített ZIP letöltés: `200 application/zip`, **9 PDF**.

A letöltött PDF-eken a korábbi kritikus ellenőrzések mindegyike zöld:

- standard Magyar állampolgárság;
- külföldi munkabér a helyes 7. sorban, nem-OTP érkezéssel;
- GYED sor üres;
- nincsenek idegen nullák a 2–4. jövedelemsorokban;
- adóstárs OTP-s érkezés kiválasztva;
- fedlap: SZA IG=2, MA IG=1, IA=1, SZA ESZ=0;
- nincs indokolatlan SZA ESZ lap a csomagban.

## Éles GUI ellenőrzés

- Admin bejelentkezés működik.
- OTP Bank → Piaci hitel → valós Salesforce-ügylet kiválasztva.
- Salesforce-feltöltés checkbox kikapcsolva (`checked: false`).
- **9/9 dokumentum sikeresen generálva**.
- GUI ZIP letöltés esemény megtörtént; backend `GET /api/pdf/download ... 200 OK`.

Vizuális bizonyítékok helyben:

- `gui-test-screenshots/prod-t1-login.png`
- `gui-test-screenshots/prod-t2-all-success.png`

Részletes, ignore-olt bizonyítékok:

- `.superpowers/sdd/live-prod-response.json`
- `.superpowers/sdd/e2e-real/live-prod-package.zip`
- `.superpowers/sdd/e2e-real/live-prod-checks.json`

## Következtetés

A Genie canonical migráció és az audit javítások **éles környezetben, valós Salesforce-adatokkal, GUI-n és API-n is működnek**. A backend healthy, a frontend elérhető, a csomag letölthető, és a tartalom a korábbi auditokkal konzisztens.

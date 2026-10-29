# Valós-adatú E2E verifikáció — 2026-10-29

## Hatókör és adatvédelem

A `feat/genie-canonical-migration` branch `1d2ee66` commitja után helyi React/Vite frontend és FastAPI backend mellett, valós Salesforce-org metaadatai és egy valós Opportunity felhasználásával GUI- és API-E2E ellenőrzés történt.

A valós ügylet 2 szereplőt és 1 ingatlant tartalmazott. A Salesforce-olvasás engedélyezett volt, de ContentVersion-feltöltés és orgmódosítás nem történt (`upload_to_sf=false`). A jelentés nem tartalmazza az ügylet nevét, Salesforce ID-ját vagy személyes mezőértékeket. A részletes bizonyítékok csak helyi, ignore-olt fájlokban maradtak.

## Környezet előkészítése

- Read-only `Organization` metadata lekérdezéssel org-ID beszerzése (a telepített `simple-salesforce` nem tartalmaz `identity()` metódust).
- Describe-only sémasnapshot: 4 objektum, 383 mező; `Jovedelem__c` és `Meglevo_hitel__c` nem érhető el az orgban, explicit hiányként rögzítve.
- Helyi backend: `SF_SCHEMA_PATH=config/sf_schema_snapshot.json`, `SF_MOCK_MODE=false`.
- Helyi frontend: Vite `127.0.0.1:5180`; backend `127.0.0.1:8765`.
- GUI: admin bejelentkezés. A `user` fiók handoffbeli jelszavát a helyi auth-adatbázis elutasította; ezt külön megjegyeztem, az adatbázist nem módosítottam.

## Első valós futtatás és talált hibák

A teljes GUI-folyamat (bank, termék, valós ügylet kiválasztása, csomaggenerálás, ZIP letöltés) végigment. Az első eredmény 9 sikeres és 2 sikertelen dokumentum volt.

A két hiba kezdetben „PDF fájl nem található” üzenetet adott, de a fájlok léteztek. A valós ok: a katalógus `OTP/` nagybetűs legacy útvonalat használ, a P0 containment-ellenőrzés pedig csak az `otp/` kisbetűs gyökeret engedte. A macOS fájlrendszeren a két név ugyanazt a könyvtárat jelöli, de a lexikális `Path.is_relative_to` elutasította.

Egy külön figyelmeztetés is kiderült: hiányzó termékmezők esetén a `Product_Name__c` az Opportunity megnevezését kapta fallbackként, ami téves product-gate figyelmeztetést okozott.

## Javítások, RED → GREEN

1. `resolve_pdf` containment mostantól a lexikális ellenőrzés mellett a valós fájlrendszerbeli ős-/gyökérkönyvtárakat (`samefile`) is összehasonlítja. Ez engedi a case-variant legacy gyökeret, de a path-traversal és sibling-prefix támadások továbbra is elutasítottak.
2. `Product_Name__c` többé nem kap Opportunity `Name` fallbacket; hiányzó termékmezők esetén `None` marad. Az explicit terméklista (`piaci_hitel`) továbbra is feloldódik.

Új regressziós tesztek:

- `test_resolve_pdf_preserves_uppercase_legacy_otp_root` — előbb `PermissionError`, javítás után a valós PDF feloldása sikeres.
- `test_missing_product_fields_do_not_use_opportunity_name_as_product` — előbb az Opportunity neve terméknévként jelent meg, javítás után `None`, az explicit `piaci_hitel` megmarad.
- A path-traversal és normál root-path tesztek továbbra is zöldek.

Teljes backend-svít a javítások után: **886 passed, 4 skipped, 8 deselected**.

## Végső valós E2E eredmény

### GUI

- OTP Bank → Piaci hitel → valós Opportunity kiválasztva.
- Salesforce-feltöltés checkbox kikapcsolva (`checked: false`).
- **11/11 dokumentum sikeresen generálva, 0 hiba.**
- A korábbi két sikertelen dokumentum is elkészült.
- Nincs product-gate figyelmeztetés (`notices=[]`).
- GUI ZIP letöltés esemény megtörtént; backend `GET /api/pdf/download ... 200 OK`.

Vizuális bizonyíték: `gui-test-screenshots/t6_real_generation_all_success.png` (helyi, ignore-olt).

### ZIP és PDF-tartalom

- Hitelesített ZIP letöltés: HTTP 200, `application/zip`.
- **11 PDF** a csomagban, köztük mindkét korábban hibás dokumentum.
- A valós Salesforce-ból normalizált értékekkel összevetve:
  - adós neve jelen van a megfelelő PDF-ben;
  - adóstárs neve jelen van az adóstárs PDF-ben;
  - ingatlan települése jelen van;
  - hitelösszeg számjegyekre normalizálva egyezik;
  - helyrajzi szám az orgban üres, ezért a mező üressége várható;
  - általános nyilatkozat: pontosan **8 reviewed pipa**, nincs automatikus jogi találgatás.

Minden tartalomellenőrzés `true`. A részletes, ignore-olt bizonyítékok:

- `.superpowers/sdd/e2e-real/live-package-fixed.zip`
- `.superpowers/sdd/e2e-real/final-content-checks.json`
- `gui-test-screenshots/live-generate-response-fixed.json`

## Maradó korlátok

- Az orgban nincs `Jovedelem__c` és `Meglevo_hitel__c`; a kapcsolt soros képesség továbbra is fail-closed figyelmeztetést igényel, ha ezeket az objektumokat később létrehozzák.
- A valós ügyletben a futamidő és a két szereplő adóazonosítója hiányzik; ezek valós adathiányok, nem motorhibák.
- A helyi `user` fiók jelszava külön auth-konfigurációs kérdés; az admin főfolyamatot nem érinti.

## Konklúzió

A javított commit utáni valós GUI-E2E, a hitelesített ZIP letöltés, a 11 PDF és a valós mezőértékek ellenőrzése **sikeres**. A két E2E által feltárt regresszió külön fix commitban került rögzítésre.

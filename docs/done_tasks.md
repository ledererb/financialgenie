## 1. Checkbox groupok (Jelölőnégyzet csoportok) bevezetése
* **Hol történt:** `src/main.py` (`_prepare_field_data`), frontend típusok (`frontend/src/types/index.ts`).
* **Technikai háttér:** A PDF-ekben gyakoriak a kizárólagos checkbox csoportok (pl. "Hitelcél: Lakásvásárlás / Építés / Bővítés"). Ezeket mostantól a `checkbox_group` objektum/tulajdonság fogja össze.
* **Működés:** A backend kikeresi a csoporthoz tartozó kanonikus SF mezőt (pl. `Opportunity.Hitelc_l__c`), majd annak értékét összeveti a csoport egyes elemeinek `match_value` értékével (pl. `"Lakásvásárlás"`). Amelyik egyezik, az kap `"igen"`/`"/Yes"` értéket, a többi `"nem"`/`"/Off"`. A logika támogatja az `adóstárs` (co-borrower) mezők speciális prefix-alapú ("coborrower") feloldását is.

## 2. Kitöltési szabályok (Fill Rules Engine)
* **Hol történt:** `src/main.py` (`_eval_fill_rule` metódus).
* **Technikai háttér:** Bizonyos PDF mezők nem egy az egyben kanonikus SF mezők, hanem logikai szabályok alapján dőlnek el (pl. "Van-e adóstárs?", "Kér-e hírlevelet?"). Erre bevezettük a `fill_rule` JSON attribútumot a mappingekben.
* **Támogatott típusok:**
  * `"static"`: Mindig egy fix értéket ad (pl. "igen").
  * `"per_participant"`: "igen", ha van aktív résztvevő az ügyletben.
  * `"conditional"`: Egy adott SF mező (pl. `sf_field`) értékét vizsgálja egy elvárt értékkel (`match`). Ha egyezik, visszaadja a `value`-t.
  * `"role_based"`: Akkor ad értéket, ha az ügyletben a résztvevők valamelyike a megadott szerepkörök (`roles`) egyikével rendelkezik (pl. "adós").

## 3. Flat PDF szerkesztő UI (Kattintásos mapping)
* **Hol történt:** `frontend/src/components/PageEditor.tsx` (~600 sornyi bővítés).
* **Technikai háttér:** Nem minden PDF rendelkezik interaktív `/AcroForm` mezőkkel (pl. szkennelt vagy "lapos" PDF-ek). A frontendben mostantól a felhasználó a kurzorral rákattinthat a renderelt oldalra, és vizuálisan helyezhet el overlay dobozokat (koordinátákkal: `x, y, width, height`), amiket a rendszer JSON-ként lement a mappingbe, így lehetővé téve a szöveg rávetítését (OverlayFiller).

## 4. Mapping felülírási probléma javítása
* **Hol történt:** `backend/mapping_service.py`.
* **Technikai háttér:** Amikor egy PDF-et a rendszer újra feldolgozott (kivonatolt), hajlamos volt felülírni a korábban már manuálisan beállított `canonical_field` vagy `fill_rule` értékeket.
* **Fix:** Bevezetésre került egy merge logika. Az új kivonatolás során a rendszer összeveti a meglévő mapping fájllal a mezőket, és ha egy mező már rendelkezik szabályokkal, azt megőrzi. Továbbá azokat a mezőket is megtartja, amiket az új extrakció esetleg nem találna meg.

## 5. 1788 soros OTP Master Igénylési mapping
* **Hol történt:** `src/mapping/otp_master_igenylesi_mapping.json`.
* **Technikai háttér:** Létrehoztuk a projekt eddigi legnagyobb mapping konfigurációját, amely az OTP 97 oldalas master igénylési dokumentumához rendel hozzá SF kanonikus mezőket és komplex `fill_rule`-okat (beleértve a checkbox csoportokat is).

## 6. Dokumentáció és projektstruktúra tisztítása
* **Hol történt:** `docs/` mappa és gyökérkönyvtár.
* **Technikai háttér:** A gyökérből a `DEPLOY.md`, `HOW_IT_WORKS.md` és `README.md` fájlok bekerültek a `docs/` alá. Ezen felül hozzáadásra kerültek az ügyfél (Think AI Kft.) által biztosított originál specifikációk is (`financialgenie_brief.pdf` és `kanonikus_mezok_utmutato.pdf`).

## 7. Új minták a teszteléshez
* **Hol történt:** `samples/` és `src/mapping/`.
* **Technikai háttér:** Hogy az új funkciók (fill rules, flat PDF) éleshez hasonló környezetben is tesztelhetők legyenek, bekerültek új PDF részletek (`Igenylesi_dokumentumok_elso_4_oldal.pdf`, `OTP_Jelzaloghitel_2oldal.pdf`) és az azokhoz tartozó lokális mapping fájlok. A `FormFillerPipeline` automatikusan támogatja ezeket.

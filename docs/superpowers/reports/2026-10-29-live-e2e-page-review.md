# Oldalankénti valós E2E és vizuális PDF-audit — 2026-10-29

## Eredmény

A 11 dokumentumos valós csomag mind a **38 oldalát** képként vizsgáltam, és mind a **927 widgetet** mezőszinten összevetettem a mappinggel és a valós Salesforce-adatokkal. **Nem elfogadható véglegesen:** 1 blokkoló és 5 fontos probléma maradt.

- Vizuális oldalak: **28 PASS / 10 FAIL**

- Mezőérték- és szerepellenőrzés: nincs bizonyított rossz érték, rossz szereplőhöz rendelt mező vagy várt nemüres mező, amely üresen maradt volna.

- Független mezőaudit: **109** nemüres érték/állapot egyezik, **819** várható üres mező igazolt, **51** külön ellenőrzés sikeres.

- A vizuális és a mezőaudit eltérő dolgot mér: a tárolt mezőérték lehet jó miközben a PDF-ben levágott, rossz rubrikába kerül vagy a csomaglogika hibás.

## Blokkoló és fontos problémák

| Oldal | Probléma | Osztály |
|---|---|---|
| `munkaltato_adatlap.pdf` p1 | A hosszú munkáltatónév a mező szélén levágódik; a teljes érték megvan, de nem látható. | **Blokkoló — generálási appearance** |
| `V szamu fuggelek...pdf` p4 | Cégnév levágva; a „Születési helye és ideje” csak helyet tartalmaz. | **Fontos — generálás + mapping** |
| `fedlap.pdf` p1 | A csomagban használt SZA IG/MA IG/IA/SZA ESZ darabszámok nincsenek kitöltve. | **Fontos — csomagmanifeszt/workflow** |
| `sza_esz (adóstárs 1).pdf` p1 | Telefonszám utolsó számjegye a comb kapacitása miatt nem látszik; több személy/cím mező forrásadat-hiányos. | **Fontos — sablon + forrásadat** |
| `sza_esz.pdf` p1 | A „más személy, mint az igénylő” lap az igénylő adataival készül, non-applicant collateral szereplő nélkül. | **Fontos — ügylet-workflow** |
| `sza_ig_tarsigenylő...pdf` p2 | Adóstárs ország checkbox üres; a forrásadat hiányzik. Foglalkozás szöveg/checkbox inkonzisztencia. | **Fontos — forrásadat + alias** |

## Egyéb megállapítások

- `altalanos_nyilatkozatok.pdf` p3: `3. oldal/8` a 9 oldalas formon — **eredeti banki sablonhiba**, nem generálási regresszió.

- `sza_ig_igenylő.pdf` p2 és `sza_ig_tarsigenylő...pdf` p2: az „Egyéb” foglalkozás-szöveg ki van töltve, de a hozzá tartozó checkbox nem; alias/fill_rule javítás szükséges.

- `sza_ig_igenylő.pdf` p3 és `sza_ig_tarsigenylő...pdf` p3 ékezetvesztés csak a teszt PNG-ben jelenik meg; a PDF szövegréteg és forrásstream hibátlan, tehát **nem produktumhiba**.

- A `V szamu...pdf` 22 checkboxának `/Off` appearance hiányzik, de ez az eredeti sablonból örökölt technikai anomália; az On/Off állapotok helyesek.

## Mind a 38 oldal

| Doc ID | Dokumentum | Oldal | Ítélet | Megjegyzés |
|---|---|---:|---|---|
| d01 | Partner nyilatkozat hiteligeny leadasakor.pdf | 1 | **PASS** | — |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 1 | **PASS** | — |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 2 | **PASS** | — |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 3 | **PASS** | — |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 4 | **FAIL** | FONTOS: hosszú cégnév levágva (generálási); a kombinált születési hely+idő mező csak helyet tartalmaz (mapping). |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 5 | **PASS** | — |
| d02 | V szamu fuggelek Penzugyi szolgaltatas kozvetiteseben valo kozremukodesre vonatkozo ugyfel ceg nyilatkozat 20250601.pdf | 6 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 1 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 2 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 3 | **FAIL** | MINOR: 3. oldal/8 a 9 oldalas dokumentumban — eredeti banki sablonhiba. |
| d03 | altalanos_nyilatkozatok.pdf | 4 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 5 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 6 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 7 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 8 | **PASS** | — |
| d03 | altalanos_nyilatkozatok.pdf | 9 | **PASS** | — |
| d04 | fedlap.pdf | 1 | **FAIL** | FONTOS: SZA IG/MA IG/IA/SZA ESZ darabszámok üresek; a fedlap nem a tényleges csomagmanifesztből számol. |
| d05 | hitelfeltetelek.pdf | 1 | **PASS** | — |
| d05 | hitelfeltetelek.pdf | 2 | **PASS** | — |
| d05 | hitelfeltetelek.pdf | 3 | **PASS** | — |
| d05 | hitelfeltetelek.pdf | 4 | **PASS** | — |
| d05 | hitelfeltetelek.pdf | 5 | **PASS** | — |
| d05 | hitelfeltetelek.pdf | 6 | **PASS** | — |
| d06 | ingatlan_adatlap.pdf | 1 | **PASS** | — |
| d06 | ingatlan_adatlap.pdf | 2 | **PASS** | — |
| d06 | ingatlan_adatlap.pdf | 3 | **PASS** | — |
| d07 | munkaltato_adatlap.pdf | 1 | **FAIL** | BLOKKOLÓ: munkáltató teljes neve a mező szélén levágva (generálási appearance). |
| d07 | munkaltato_adatlap.pdf | 2 | **PASS** | — |
| d08 | sza_esz (adóstárs 1).pdf | 1 | **FAIL** | FONTOS: telefonszám utolsó számjegye a comb kapacitása miatt nem látszik (sablon+validáció); további személy/cím adathiányok a forrásban. |
| d09 | sza_esz.pdf | 1 | **FAIL** | FONTOS: „más személy, mint az igénylő” űrlap az igénylő adataival, pedig nincs non-applicant collateral szereplő — workflow hiba. |
| d10 | sza_ig_igenylő.pdf | 1 | **PASS** | — |
| d10 | sza_ig_igenylő.pdf | 2 | **FAIL** | MINOR: foglalkozás „Egyéb” szöveg kitöltve, de minden releváns checkbox üres — alias/fill_rule hiba. |
| d10 | sza_ig_igenylő.pdf | 3 | **FAIL** | RENDER ARTIFACT: a PNG-ben elvész egy ékezet; a PDF szövegrétege és forrásstream hibátlan. |
| d10 | sza_ig_igenylő.pdf | 4 | **PASS** | — |
| d11 | sza_ig_tarsigenylő (adóstárs 1).pdf | 1 | **PASS** | — |
| d11 | sza_ig_tarsigenylő (adóstárs 1).pdf | 2 | **FAIL** | FONTOS: adóstárs ország nincs kipipálva — forrásadat-hiány; MINOR: foglalkozás checkbox/szöveg inkonzisztencia. |
| d11 | sza_ig_tarsigenylő (adóstárs 1).pdf | 3 | **FAIL** | RENDER ARTIFACT: ugyanaz a vizuális renderelési ékezetvesztés; a PDF hibátlan. |
| d11 | sza_ig_tarsigenylő (adóstárs 1).pdf | 4 | **PASS** | — |

## Bizonyítékok

- Renderelt oldalak: `.superpowers/sdd/e2e-real/rendered/` (38 PNG, 144 DPI).
- Vizuális verdictek: `visual-d01-d04.jsonl`, `visual-d05-d08.jsonl`, `visual-d09-d11.jsonl`.
- Részletes mezőaudit: `.superpowers/sdd/e2e-real/field-audit.json`.
- Ezek ignore-olt helyi fájlok, a jelentés szándékosan nem tartalmaz személyes mezőértékeket.

## Javasolt javítási sorrend (még nincs implementálva)

1. Hosszú szövegek appearance-fitting: munkáltató- és cégnév levágás megszüntetése (wrap/shrink/multiline).
2. Csomagmanifeszt-vezérelt fedlap és SZA ESZ csak akkor, ha valódi non-applicant collateral szereplő létezik.
3. Kombinált születési hely+idő mező és foglalkozás checkbox aliasok javítása.
4. Telefon-comb kapacitás/validáció és a hiányzó forrásadatok kezelése.
5. Ezek után a teljes 38 oldalas vizuális és mezőaudit megismétlése.

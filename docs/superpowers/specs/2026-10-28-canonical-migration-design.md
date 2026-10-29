# FinancialGenie kanonikus migráció és stabilizálás — design

Dátum: 2026-10-28
Állapot: jóváhagyott design

## Cél és hatókör

A Mapping Studio, az AI-felismerő, a mapping JSON-ok és a kitöltőpipeline teljes átállása Salesforce-független Genie-kanonikus kulcsokra. A végső aktív mappingekben nem maradnak `Contact.*`, `Lead.*`, `Opportunity.*` vagy `-társ` canonical aliasok.

Az éles Salesforce org nem módosul; csak read-only describe engedélyezett. A member felhasználók továbbra is az összes ügyletet láthatják a generáláshoz, minimális API-válaszokon keresztül. Ismeretlen jogi nyilatkozatokat a rendszer nem tölt ki automatikusan.

A NAS-on említett `docs/mapping_studio_atvilagitas.md` nem található a munkafában. A szemantikai mappingjavításokat a PDF-címkék, mezőnevek és az átadott auditpéldák alapján kell reprodukálni és explicit migrációs táblában rögzíteni.

## Architektúra

### Genie-katalógus és resolver

Egy `src/canonical/` csomag tartalmazza:

- `fields.json`: verziózott, kurált mezőkatalógus magyar címkével, típussal és metaadatokkal;
- `registry.py`: betöltés és katalógusvalidáció;
- `paths.py`: canonical path parser;
- `resolver.py`: értékfeloldás `DealData` és dokumentumpéldány-kontextus alapján.

Kulcsformák:

- `deal.<mező>`;
- `account.<mező>`;
- `participant.<role>.<mező>` és `participant.<role>[N].<mező>`;
- `property.<mező>` és `property[N].<mező>`;
- `participant.<role>.income[N].<mező>`;
- `participant.<role>.existing_loan[N].<mező>`.

Szerepek: `borrower`, `co_borrower`, `guarantor`, `mortgagor`, `usufructuary`. A zálogkötelezett nem azonos a kezessel. A dátum- és karakterbontás fragment/metaadat, nem canonical suffix.

A Salesforce describe külön séma-snapshotot ad az adapter validálásához. A Studio canonical listája kizárólag a kurált Genie-registry.

### Adatmodell

A `DealData` kibővül stabil Contact/Property azonosítókkal, új szereplőmezőkkel, `IncomeEntry` és `ExistingLoan` listákkal, valamint a szükséges Property-attribútumokkal. A régi mock JSON-ok olvashatók maradnak, de a registry és az új mappingek csak Genie-kulcsokat használnak.

Az ingatlanforrás továbbra is a meglévő `Property__c`; nem jön létre vakon párhuzamos ingatlanobjektum. A jövedelem- és meglévőhitel-rekordok szereplőhöz kötöttek, stabil sorrendűek.

### Salesforce-adapter

A `SalesforceClient` összefésüli az OCR- és custom lookup-szerepeket Contact ID szerinti deduplikálással. A zálogkötelezett, kezes és haszonélvező külön szerep, mindegyikből több példány lehet. A lookup ID-k helyett a kapcsolt Contact-adatok kerülnek a modellbe.

A SOQL mezőlisták describe alapján szűrhetők. Hiányzó objektum vagy mező explicit figyelmeztetés; éles módban nincs csendes mock fallback. A normalizer megőrzi a listákat, szerepeket, azonosítókat és részletes Property-adatokat.

### Kitöltőpipeline

Minden fogyasztó — közvetlen mező, checkbox group, fill rule, points, character group, dátumfragment — ugyanazt a resolvert használja. A PDF-mező neve nem választ szereplőt. A dokumentumpéldányok explicit `(role, index)` kontextust kapnak; kezes vagy haszonélvező nem okoz automatikusan adóstársoldalt.

A generálás, validálás és field-values előnézet azonos adatburkot és resolverviselkedést használ. Különböző szemantikájú mezők — hrsz/megjegyzés, háztartás/eltartottak/gyermekek, nem/megszólítás, jövedelemérkezés/jóváírásvállalás, okmánytípusok — nem helyettesítik egymást.

## Mappingmigráció

Egy idempotens `scripts/migrate_canonical_mappings.py` explicit migrációs táblával írja át valamennyi mapping `fields`, `character_groups`, `points` és szabályhivatkozását. Tilos a repository-szerte végzett stringcsere.

A migrátor:

1. jelenti az ismeretlen vagy többértelmű tételeket;
2. a régi ingatlan-aliasokat valódi jelentésük szerint `property.*` kulcsokra írja;
3. a hiteladatokat `deal.*` kulcsokra írja;
4. kijavítja a `#nap` dupla fragmentálást és a `#count` szemantikáját;
5. megőrzi a csoportokat és szabályokat;
6. második futásra nem módosít semmit.

A stúdió, AI promptok, validátor, completeness és dokumentáció ugyanazt a registryt használja. Minden teljes mentés, import és group-módosítás validált. Jóváhagyáskor `approved_by` és időbélyeg keletkezik. Automatikus jóváhagyás nincs; admin review és sikeres smoke teszt szükséges.

## Nyilatkozatok

A valós AcroForm alapján helyreállandók a 212 mező típusai és a 46 választási csoport. Szerep-/aláíráscheckboxok a `points`/`rule_type=2` mechanizmust használják `role#index` kiegészítéssel. Csak explicit CRM-hozzájárulás tölthető automatikusan.

A `legal_defaults.json` nem adhat implicit true/false jogi választ. A points által kezelt mezőket a `LegalClassifier` nem dolgozhatja felül. A validáció listázza a manuális döntést igénylő mezőket.

## Biztonság és megerősített hibák

- A `testclient` User-Agent nem kerülheti meg az authot.
- A health válasz nem tartalmaz belső útvonalat.
- PDF/ZIP letöltés, kitöltött preview és mapping export hitelesített.
- A frontend hitelesített fetch + blob helperrel tölt le és kér le védett képeket.
- A deals válasz csak a szükséges mezőket adja.
- Javítandó a hiányzó `re` import, a `filled_set`, az objektumos fragment és a zárt PDF utáni oldalszámlekérés.

## Salesforce Setup-runbook

Külön dokumentum írja le a Lead → Opportunity mezőpárokat, a meglévő `Property__c` bővítését, a `Jovedelem__c` és `Meglevo_hitel__c` sémáját, az okmánytípus/gyermekszám/díjkedvezmény/kiváltandó számlaszám mezőket és a describe-sync elvárt API-neveit. Ezek nem képezik az éles orgon végrehajtott munka részét.

## Hibakezelés

- Éles SF-konfiguráció hiányában egyértelmű 503, nem dummy adat.
- Ismeretlen canonical vagy migrációs tétel validációs hiba és jelentés.
- Hiányzó kapcsolt Salesforce-objektum warning/notice, nem kitalált érték.
- Manuális nyilatkozat nem számít automatikusan sikeresen kitöltött mezőnek.
- Mappingírásnál megmarad az atomikus mentés és az mtime-konfliktusdetektálás.

## Tesztelés és elfogadás

TDD-vel készülnek:

- registry/path/resolver tesztek;
- canonical lefedettségi és mapping contract tesztek;
- idempotens migrációs tesztek;
- fake Salesforce OCR/lookup/describe/normalizer tesztek;
- szerep-, ingatlan-, jövedelem- és hitelsor routing tesztek;
- szemantikai mapping regressziók;
- nyilatkozatbiztonsági tesztek;
- valódi tokenes auth és minimális API-válasz tesztek;
- PDF fallback/preview regressziók;
- frontend typecheck/build és authos E2E-alapok.

Elfogadáshoz teljes pytest, frontend typecheck/build, mappingaudit és reprezentatív OTP + CIB generálási smoke teszt szükséges. A skipelt vagy korábban hibás teszteket külön kell jelenteni.

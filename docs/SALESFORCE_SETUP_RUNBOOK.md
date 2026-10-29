# Salesforce Setup Runbook — Genie cutover utáni éles előkészítés

**Dátum:** 2026-10-29 (Cutover Task 6 artifact)
**Állapot:** tervezet — a SalesForce orgon **semmilyen munka nem lett végrehajtva**.
Ez a dokumentum a várt objektum-/mezőneveket, a describe-snapshot folyamatot, a
hibakezelést és az üzemeltetési óvintézkedéseket írja le. A nevek nagy része
**konfiguráció-elvárás** a `src/integrations/salesforce_field_map.json`-ban
(generált mezőtérkép), nem leképezés a pillanatnyi org-állapotról.

Kapcsolódó végső bizonyíték: `docs/superpowers/reports/2026-10-29-genie-cutover-verification.md`.

---

## 1. Lead → Opportunity átállás kockázatai

A legacy mapping-rendszer a nyomtatványokat közvetlenül Salesforce API-nevekre
képezte le, és több adatot **Lead**-ről olvasott (a
`scripts/canonical_migration_map.py` ~46 `Lead.*` bejegyzést tartalmaz:
cím, ingatlan-adatok — `Lead.Ingatlan_hrsz__c`, `Lead.Ingatlan_telepules__c`,
`Lead.Ingatlan_megjegyzes__c` (helyrajzi szám!), `Lead.Form_Type__c`,
`Lead.Estimated__c` stb. —, továbbá jövedelem-/hitelinformációk). A Genie
cutover után a **futásidejű pipeline egyáltalán nem kérdez Lead-et**: minden
adat az `Opportunity`, `Contact`, `Account`, `Property__c` és a kapcsolódó
`Jovedelem__c`/`Meglevo_hitel__c` objektumokból jön.

Kockázatok, amiket a Lead→Opportunity konverziónál kezelni kell:

| Kockázat | Leírás | Kezelés |
|---|---|---|
| **Konverzió adatvesztés** | A Lead-re felvett cím/ingatlan/jövedelem mezők konverziókor nem másolódnak automatikusan az Opportunity/Contact/Property__c mezőkre → a PDF-ek üresen maradnak. | A konverziós mapping (Lead field mapping / flow) vigye át a mezőket; a hiányzó adat a CompletenessChecker `Hiányzó mező` issues-ként jelenik meg, nem találgatás. |
| **Ingatlan-adatok a rossz objektumon** | A Lead-en tartott ingatlanadatok helyesen a `Property__c` rekordokra kerüljenek (Account-hoz/Opportunity-hez kötve), ne maradjanak Lead-szövegmezőben. | Lsd. §2; a pipeline a `Property__c`-ből olvas, Lead-ről soha. |
| **Kétértelmű futamidő** | A legacy `Lead.Term_in_year__c` fogyasztója kétértelmű volt; az éles adapter szándékosan **üresen hagyja** ezt az utat, a futamidő csak `Opportunity.Loan_Term__c` (hónap) / `Opportunity.Term_in_year__c` (év) alapján él. | Ne töltsünk futamidőt Lead mezőből; hónap/év külön kanonikus út (`deal.loan_term_months` / `deal.loan_term_years`), nincs automatikus átszámítás. |
| **OCR vs. lookup szerepek** | A régi Lead-alapú szereplőkezelés helyett a szerepek elsődlegesen `OpportunityContactRole`-ból jönnek (`Adós`, `Adóstárs`, `Kezes`, `Haszonélvező`), ahol a Contact-ID szerinti deduplikáció és az `adós/adóstárs_N/kezes_N/zálogkötelezett_N/haszonélvező_N` indexelés a szabvány. | A szerepelnevezést tartsuk konzisztenssen az OpportunityContactRole-ban. |

**Éles-előkészületi ellenőrzőlista:** egy konvertált (Lead→Opportunity)
ügyleten futtassunk completeness-checket; minden, ami a Lead-en megvolt és itt
hiányzik, konverziós mapping-munka.

---

## 2. Ingatlanok: a MEGLEVŐ `Property__c` bővítése — nem új `Ingatlan__c`

A pipeline ingatlanforrása továbbra is a meglévő **`Property__c`** objektum.
**Nem jön létre párhuzamos `Ingatlan__c` objektum** — az elvárás a meglévő
`Property__c` kiterjesztése az alábbi custom mezőkkel (API-nevek a field map
szerint; aminek nincs `__c` utótagja vagy eltérő a neve, azt Setupban kell
létrehozni/átnevezni — a describe-snapshot mutatja meg, mi hiányzik):

| Várt API-név (`Property__c`-n) | Kanonikus cél | Megjegyzés |
|---|---|---|
| `Account__c` | `deal.account_id` | Lookup (Account) — kötelező a lekérdezéshez |
| `Ingatlan_kozterulet_neve__c` | `property.address.street` | utca neve |
| `Ingatlan_Kozterulet_jellege__c` | `property.street_type` | közterület jellege (utca/tér…) |
| `Ingatlan_hazszam__c` | `property.address.house_number` | |
| `Ingatlan_emelet__c` | `property.address.floor` | emelet/ajtó |
| `Ingatlan_irsz__c` | `property.address.zip_code` | |
| `Ingatlan_telepules__c` | `property.address.city` | |
| `Ingatlan_megye__c` | `property.region` | |
| `Ingatlan_hrsz__c` | `property.parcel_number` | helyrajzi szám (a `Ingatlan_megjegyzes__c` fallback) |
| `Ingatlan_alapterulet__c` | `property.area_sqm` | m² |
| `Ingatlan_jellege__c` | `property.form_type` | építmény típusa |
| `Ingatlan_reszletes_tipus__c` | `property.property_type_detail` | **részletes** típus — NEM redukálható a legacy enumra |
| `Property_Type__c` | `property.property_type` | |
| `Property_value__c` | `property.estimated_value` | becsült érték |
| `Purchase_price__c` | `property.purchase_price` | vételár (külön van a becsült értéktől) |
| `Ingatlan_allapot__c` | `property.condition` | állapot |
| `Ingatlan_futes__c` | `property.heating` | fűtés |
| `Ingatlan_epites_eve__c` | `property.year_built` | építés éve (nem használatbavétel!) |
| `Ingatlan_lift__c` | `property.lift` | |
| `Ingatlan_biztositas__c` | `property.insurance` | |
| `Ingatlan_energetika__c` | `property.energetika` | energetikai besorolás |
| `Ingatlan_szobak_szama__c` | `property.number_of_rooms` | |
| `Ingatlan_szerepe__c` | `property.ingatlan_szerepe` | pl. `Célingatlan` (a rendezéshez kell) |
| `Ingatlan_terhek__c` | `property.terhek` | |
| `Ingatlan_hasznalatbaveteli__c` | `property.extra["ingatlan_hasznalatbaveteli"]` | használatbavétel — **külön** marad az építés évétől |
| `Ingatlan_megjegyzes__c` | `property.extra["ingatlan_megjegyzes"]` + hrsz-fallback | megjegyzés ≠ helyrajzi szám |
| `Ingatlan_neve__c`, `Ingatlan_osztatlan__c` | `property.extra[...]` | |

Kapcsolódás: elsődlegesen az **`Opportunity_Property_Role__c`** kapcsolótábla
(`Property__c` + `Ingatlan_szerepe__c` mezőkkel); ha az nem elérhető, fallback
`Property__c WHERE Account__c = <account_id>`. Ingatlan-szerep (`Célingatlan`)
szerint előre rendezve. **Figyelem:** a közterület-jelleg duplikálást
(pl. `Csók István utca` + `utca`) a normalizer szűri, de az org-oldali
adatminőség ezt nem teheti szükségessé.

---

## 3. Javasolt kapcsolódó objektumok: `Jovedelem__c` és `Meglevo_hitel__c`

Két, szereplőhöz (Contact) kötött kapcsolódó objektum — **javasolt** sémával.
Ha az org-ban még nem léteznek, a pipeline NEM hibázik el: a related-lekérdezés
 csak akkor fut, ha a describe megerősíti az objektumot és a `Contact__c`
lookupot; ellenkező esetben **egy actionable warning + üres lista** készül
(soha nem kitalált sorok).

### 3.1 `Jovedelem__c` (jövedelemsorok)

| Mező | Kanonikus cél | Típus (javaslat) |
|---|---|---|
| `Contact__c` | (lookup → Contact, a tulajdonos) | MasterDetail/Lookup — **kötelező** |
| `Sorszam__c` | `income.sequence` | Number — stabil sorrend |
| `Jovedelem_tipusa__c` | `income.income_type` | Text/Picklist |
| `Osszeg__c` | `income.amount` | Currency |
| `Penznem__c` | `income.currency` | Text (pl. HUF/EUR) |
| `Bank__c` | `income.bank_name` | Text |
| `Szamlaszam__c` | `income.account_number` | Text — **kiváltandó számlaszám-jelleg**, csak itt |
| `Erkezes_helye__c` | `income.destination` | Text — jövedelem érkezési helye (≠ jóváírásvállalás!) |

### 3.2 `Meglevo_hitel__c` (meglévő hitelek)

| Mező | Kanonikus cél | Típus (javaslat) |
|---|---|---|
| `Contact__c` | (lookup → Contact, a tulajdonos) | MasterDetail/Lookup — **kötelező** |
| `Sorszam__c` | `existing_loan.sequence` | Number — stabil sorrend |
| `Hiteltipus__c` | `existing_loan.loan_type` | Text/Picklist |
| `Bank__c` | `existing_loan.bank_name` | Text |
| `Szamlaszam__c` | `existing_loan.account_number` | Text |
| `Eredeti_osszeg__c` | `existing_loan.original_amount` | Currency |
| `Fennallo_tartozas__c` | `existing_loan.outstanding_amount` | Currency |
| `Havi_torleszto__c` | `existing_loan.monthly_payment` | Currency |
| `Kivaltando__c` | `existing_loan.refinance` | Checkbox |
| `Penznem__c` | `existing_loan.currency` | Text |

### 3.3 Sorrend és szétválasztás (kritikus)

* Lekérdezési sorrend: **`ORDER BY Sorszam__c, CreatedDate, Id`** — a PDF-ek
  sorszámozott jövedelem-/hitelsorai ehhez a stabil sorrendhez rögzültek.
* **Nincs** N+1 lekérdezés: a sorok `WHERE Contact__c IN (...)` batch-ben jönnek,
  15/18 karakteres, ellenőrzött Salesforce ID-kkel (malformed ID → warning, nem
  kerül a SOQL-ba).
* Három külön pénzügyi fogalom sosem esik össze: igényelt hitel
  törlesztője (`deal.monthly_payment`), meglévő hitel törlesztője
  (`existing_loan.monthly_payment`), elvállalható törlesztő
  (`participant.*.affordable_monthly_installments`).
* `None` ≠ explicit `False`; tizenkét jövedelemsor sosem generálódik
  automatikusan.

---

## 4. Contact: audit mezők és hozzájárulások (consents)

A kitöltés személyes adatai `Contact`-ról jönnek. Két kategória:

**a) Hozzájárulás-mezők (consents) — kizárólag explicit bizonyíték:**

| Mező | Kanonikus | Szemantika |
|---|---|---|
| `AML_Consent__c` | `participant.{role}.aml_consent` → `consents["aml"]` | Csak explicit `True` számít bizonyítéknak |
| `Data_Processing_Consent__c` | `participant.{role}.data_processing_consent` → `consents["data_processing"]` | Ismeretlen/hiányzó → `None`, sosem `False`-re találtatás |
| `KHR_Consent__c` | `participant.{role}.khr_consent` → `consents["khr"]` | |
| `NAV_Consent__c` | `participant.{role}.nav_consent` → `consents["nav"]` | |

A nyilatkozat-kitöltő réteg ezekre húzható ONLY explicit, ember által
jóváhagyott canonical mappinggel (lsd. §6). Szabad szövegből / mező-jelenlétből
**soha** nem következtet hozzájárulást.

**b) Audit / nyilvántartás mezők** (a field map `participant.extra`-ba teszi):
`OwnerId` (`owner_id`), `ReportsToId` (`reports_to_id`),
`IsPriorityRecord` (`is_priority_record`), `IsEmailBounced` /
`EmailBouncedDate` / `EmailBouncedReason`, `HasOptedOutOfEmail`
(`email_opt_out`), `IndividualId`, `Department`, `AssistantName`,
`AssistantPhone`, `Last_4_nr_of_ID__c` (`personal_id_last4`),
`Display_Name__c`. Ezek kitöltése/vezetése org-oldali folyamat; a pipeline
csak olvassa őket, és csak leképezett canonical úton jelenítik meg. A
nyilatkozatokhoz kapcsolódó `Date_of_signature__c` (`participant.date_of_signature`,
aláírás kelte) szintén Contact-mező — a nyilatkozat-mappingek kizárólag explicit,
review-olt mappinggel használhatják.

**Okmány- és családi mezők, amiket a nyomtatványok várnak** (nem helyettesítik
egymást): `Document_Type__c`/`ID_Document_Type__c` (okmánytípus),
`ID_Card_Number__c` (okmányszám) vs `Personal_ID__c`/`Personal_Number__c`
(személyi azonosító), `Children_Count__c` (gyermekek száma) vs
`Numer_and_Age_of_Children__c` (gyermekek száma ÉS kora) vs
`Dependents_count__c` (eltartottak), `Highest_Educational_Qualification__c`.

**Szerepfeloldás:** elsődleges `OpportunityContactRole`, fallback
Opportunity lookup-ok (`First/Second/Third_Co_debtor__c`) és Account
kontakt-pool (csak explicit leképezett skalár mezőkre végzett enrichment,
sosem sor-összemosás más Contact-ID-vel). Egy Contact tölthet két különböző
nem-adós szerepet is; a zálogkötelezett (mortgagor) nem keveredik a kezessel
(guarantor).

---

## 5. Describe-only séma-snapshot folyamat (`SF_ORG_ID`, `SF_SCHEMA_PATH`)

Az élő mód **csak** jóváhagyott, metaadat-only séma-snapshot mellett indul.

```bash
# 1) Describe-only sync (NEM kérdez rekordokat, nem módosít semmit):
SF_ORG_ID=<18 karakteres org ID> python3 scripts/sync_sf_schema.py \
    --org-id "<ugyanaz az org ID>" \
    --output config/sf_schema_<orgid>_<YYYY-MM-DD>.json

# 2) Jóváhagyás: ember nézi át a snapshotot (nincs benne ügyfélrekord,
#    csak objektum-/mezőmetaadat: típus, referenceTo, picklistValues).

# 3) Bekapcsolás az élő kliensnek:
export SF_SCHEMA_PATH=config/sf_schema_<orgid>_<YYYY-MM-DD>.json
```

Szabályok és garanciák:

* A `sync_sf_schema.py` **kizárólag** `describe()` hívásokat végez; Opportunity/
  Contact/Lead **rekordokat soha** nem kérdez; a kimenet atomikusan íródik;
  hibaüzenetből sosem szivárog ki credential/kérésfejléc (exception-szöveg
  nem kerül a kimenetre).
* Az `SF_ORG_ID` **kötelező** és explicit — a script nem használ identity
  hívást a orgazonosításhoz; enélkül a sync nem indul (exit 2).
* Hiányzó/inaccessible objektum (pl. még nem létező `Jovedelem__c`) a
  snapshotból **kimarad** — nem fabrikált mezők; a sync stderr-en listázza.
* Ha EGYETLEN kért objektum sem describe-olható, a sync **fail-closed**:
  nem ír félrevezető üres snapshotot.
* Snapshot frissítése minden org-sémaváltozás (új mező/objektum) után
  kötelező, majd újra jóváhagyás — az élő SOQL mezőlisták ebből szűrődnek
  (`available_fields`), így a snapshot nélkülözhetetlen a leképezéshez.

## 5b. Élés mód fail-closed viselkedése (összefoglaló)

| Helyzet | Viselkedés |
|---|---|
| Élő mód, de nincs snapshot sehol (`SF_SCHEMA_PATH` / `config/sf_schema_snapshot.json`) | `RuntimeError` induláskor — **nincs** live describe fallback, nincs mock fallback |
| Snapshot olvashatatlan/érvénytelen | `RuntimeError` (ugyanígy fail-closed) |
| Backend: élő hitelesítő adatok hiányoznak és `SF_MOCK_MODE` nem `true` | HTTP **503** „Live Salesforce is not configured…", dummy adat NINCS |
| Objektum mező hiányzik a describe-ból | a mező kikerül a SOQL-ból + összegzett warning; nincs kitalált érték |
| Kapcsolódó objektum/lookup hiányzik (pl. `Jovedelem__c`) | 1 actionable warning + üres sorlista; nincs fabricált sor |
| Tiltott/legacy canonical út a mappingben | 422-es validációs hiba a mentésnél; futásidőn `LegacyCanonicalMappingError` |

Mock fejlesztői mód: `SF_MOCK_MODE=true` → `samples/dummy_data` JSON-ok,
hálózati hívás nélkül.

---

## 6. Manuális nyilatkozati politika (deployment-kritikus)

* A jogi nyilatkozatmezőket a rendszer **csak** két feltétellel tölti:
  1. **review-olt role/signature pontok** (`points`, rule 2, indexelt
     szerepcímzés — jelenleg 4 tisztán tudomásulvételi pont az
     `altalanos_nyilatkozatok` formon), amelyeket a `LegalClassifier` nem
     írhat felül;
  2. **explicit CRM consent canonical utak**, ember által jóváhagyott mappinggel
     (jelenleg EGY sincs bedrótozva — szándékos).
* Minden más (KHR, NAV, pénznem, cselekvőképesség, felhatalmazások,
  hozzájárulások, rezidensi/építésügyi/adósságrendezési nyilatkozatok)
  **manuális döntésként listázódik** a generálás eredményében
  (`manual_declarations`), és NEM számít automatikusan kitöltött mezőnek.
* A `legal_defaults.json`-ban `default_value: null` és
  `apply_when ∈ {manual, crm_consent}`; az örökölt `always`/`conditional`
  auto-értékeket a kód is visszautasítja (log + skip).
* Termékfüggő automatikus hozzájárulás-pipálás (CSOK/zöld/ÁFA…) **nincs** —
  a product-gate-elt szekciók üresen maradnak, ha az ügylet terméke nem
  illeszkedik a review-olt kulcsszó-metadatára (explicit notice jelzi).
* Új termék- vagy nyilatkozat-szótár bővítése kizárólag explicit metaadat- és
  mapping-módosítással, emberi jóváhagyással (`approved`/`approved_by`).

---

## 7. Migráció-audit és rollback parancsok

```bash
# Idempotencia-audit (soha nem ír): elvárt entries_changed=0, unresolved=0
python3 scripts/migrate_mappings_to_genie.py --dry-run \
    --report .superpowers/sdd/genie-migration-final-audit.json

# Nyilatkozat-mapping újraépítés ellenőrzése (determinista, idempotens):
python3 scripts/rebuild_altalanos_nyilatkozatok_mapping.py --check

# Tiltott legacy canonical utak tesztjei:
python3 -m pytest -q tests/test_no_legacy_canonical.py \
    tests/test_canonical_migration_map.py tests/test_mapping_genie_migrator.py

# Teljes offline ellenőrzés:
python3 -m pytest -q && npm --prefix frontend run typecheck && npm --prefix frontend run build
```

**Rollback (git-alapú, committing nélkül is elvégezhető):**

* A teljes cutover a `2ec9ccf` felett, commitolatlan munkafában él.
* Mapping-állapot visszaállítása:
  `git checkout -- src/mapping` (minden mapping JSON visszaáll a HEAD-re);
  ellenőrzött, SHA256-ös pre-activation backup:
  `.superpowers/sdd/mapping-preactivation/` (+ `SHA256SUMS.txt`).
* Teljes visszaállás: `git reset --hard 2ec9ccf` + nem-követett új fájlok
  (pl. `src/canonical/`) törlése — ez a teljes cutovert visszaveszi.
* A migrátor maga visszafelé nem fut (egyszerű, csak előre fordító eszköz);
  a rollback forrásbackupból/gitből történik, nem újrafordítással.

---

## 8. Deployment-óvintézkedések

1. **Ne deployoljunk éles Salesforce-hoz snapshot nélkül.** Először describe-sync
   (§5), jóváhagyás, `SF_SCHEMA_PATH` beállítás, és csak utána
   `SF_MOCK_MODE=false`.
2. **`config/.env` bizalmas** — valós SF/Anthropic hitelesítő adatokat
   tartalmaz; sosem kerül repo-ba/logba/snapshotba.
3. **Először mock/preview:** generálás előtt FillPreview (field-values) és
   completeness-check; a csomag- és CIB-generálás offline útja a
   `2026-10-29-genie-cutover-verification.md` §2-ben dokumentált (11 dokumentumos
   OTP csomag + CIB értékbecslő megrendelőlap, mind offline, SF-upload nélkül).
4. **Upload to Salesforce explicit:** az éles feltöltés (`attach_pdf`,
   ContentVersion) csak explicit `upload_to_sf: true` kérésre fut. A kihagyott
   mező sosem tölt fel — a backend alapértelmezése `false`
   (`/api/catalog/generate-package`), és a frontend csomag-generáló
   párbeszédablakban a jelölőnégyzet alapból nincs bepipálva. Mock módban soha
   nem tölt fel.
5. **Mapping-jóváhagyás:** minden aktív mapping `approved: false` — éles
   használat előtt admin review + approve a Mapping Studioban.
6. **Séma-változás → új snapshot → tesztek:** minden org-oldali mező/objektum
   változás után új describe-sync, majd `python3 -m pytest -q` (a szerződés-
   tesztek a field maphoz képest is validálnak).
7. **Frontend/backend verziókapcsolás:** a backend új `/api/mapping/canonical-fields`
   (6487 Genie út) és a 422-es legacy-elutasítás miatt a frontend és backend
   együtt deployolandó (régi frontend + új backend kombó nem támogatott).
8. **Naplózás:** a generálás eredményében `notices` (pl. termék-gate), `issues`
   (hiányzó mezők) és `manual_declarations` mindig felülvizsgálandó — a
   manuális nyilatkozatok nem tekinthetők kitöltöttnek.

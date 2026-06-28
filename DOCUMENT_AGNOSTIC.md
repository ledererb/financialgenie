# Dokumentum-agnosztikusság vizsgálata

## Összefoglaló

A FinancialGenie PoC **alapvetően dokumentum-agnosztikus architektúrára épül**, de vannak
OTP-specifikus rétegek. A rendszer három jól elkülöníthető rétegből áll:

```
+---------------------------+
|   OTP-specifikus réteg    |  ← mapping JSON-ok, OTP_EXACT_MAP, DocumentAssembler
+---------------------------+
|   Általános mag           |  ← FieldRecognizer prompt, kanonikus modell, AcroFormFiller
+---------------------------+
|   CRM-specifikus réteg    |  ← SalesforceClient, DataNormalizer (SF mezőnevek)
+---------------------------
```

## 1. Komponensenként

### ✅ FieldRecognizer (`src/ai/field_recognizer.py`)

**Agnosztikus rész:** A Claude-nak küldött prompt általános — kanonikus mezőket sorol fel,
és kéri a PDF mezőnevek leképezését rájuk. Nincs benne OTP-specifikus utasítás.
Ez azt jelenti, hogy bármilyen banki (vagy nem-banki) nyomtatvány esetén működik:
feltöltesz egy üres PDF-et, Claude felismeri a mezőket és hozzárendeli a kanonikus
nevekhez. A prompt támogatja mind az AcroForm (interaktív), mind a flat (lapos/scannelt)
PDF-eket (külön-külön prompt szekcióval).

**OTP-specifikus rész:**
- `OTP_EXACT_MAP` (~100 sor): hardcoded OTP mezőnév → kanonikus leképezés
  (pl. `SZA_IG_név` → `participant.name`). Ez egy gyorsító réteg: ha a mezőnév
  pontosan egyezik egy OTP-specifikus prefix-szel, azonnal HIGH confidence mapping-et ad,
  anélkül hogy Claude-ot kelljen hívni.
- `KEYWORD_MAP` (~20 sor): OTP-re jellemző kulcsszó-lista (pl. `SZA_IG_` prefixek).
- A mapping outputban a `bank_name` alapértelmezése `"OTP Bank"`.

**Hatás:** Ha másik bank nyomtatványát töltöd fel, az `OTP_EXACT_MAP` egyszerűen nem fog
illeszkedni (más mezőnevek), így a rendszer a nyelvi prompt alapú (Claude) mapping-re
hagyatkozik, ami továbbra is működik. **Agnosztikus, OTP-map csak optimalizáció.**

### ✅ Kanonikus adatmodell (`src/models/canonical_model.py`)

**Agnosztikus:** A mezőnevek általánosak:
- `participant.name`, `loan.loan_amount`, `property.address` — bármely pénzügyi
  termékhez használhatók. Nincs bennük OTP-specifikus terminológia.

**Megjegyzés:** A `products: List[str]` mező és a `ProductType` enum (`PIACI_HITEL`,
`CSOK`, `CSOK_PLUSZ`, `OTTHON_START` stb.) OTP-specifikus termékneveket tartalmaz.
Új bank esetén ezt az enumot bővíteni kell, vagy string-alapúvá kell tenni.

### ⚠️ Mapping JSON-ek (`src/mapping/*.json`)

**OTP-specifikus:** Ezek a fájlok tartalmazzák a tényleges OTP nyomtatványok mezőinek
és a kanonikus mezőknek a leképezését. Minden JSON egy-egy OTP űrlapot ír le.

**Ez a várt viselkedés:** új bank = új mapping JSON, a meglévő motor változatlan marad.

### ✅ AcroFormFiller / OverlayFiller (`src/engine/pdf_filler.py`)

**Teljesen agnosztikus:** Mindkét filler általános PDF műveleteket végez:
- `AcroFormFiller`: bejárja az AcroForm mezőfát, és a mapping alapján tölti ki
  az értékeket. Nincs benne se OTP, se bank-specifikus logika.
- `OverlayFiller`: koordináta-alapú szövegréteget rak lapos PDF-re.

### ✅ CompletenessChecker (`src/engine/completeness_checker.py`)

**Teljesen agnosztikus:** Csak a kapott mapping és kanonikus mezők alapján ellenőrzi
a hiányzó adatokat. Nincs benne bank-specifikus logika.

### ⚠️ DocumentAssembler (`src/engine/document_assembler.py`)

**OTP-specifikus:** A 97 oldalas master PDF darabolása OTP-specifikus tudás.
A `BASE_SECTIONS`, `PRODUCT_SECTIONS`, `TARSADOS_SECTION` mind OTP oldal-indexeket
tartalmaz. Más bankok master dokumentumaihoz más szekció-térkép kell.

Ugyanakkor a darabolási algoritmus (`_build_page_plan`, `assemble`) általános:
a szekció-térképet kell cserélni, a logikát nem.

### ⚠️ DataNormalizer (`src/normalizer/data_normalizer.py`)

**CRM-specifikus:** A Salesforce mezőneveket (pl. `Loan_Term__c`, `Amount__c`) képezi le
a kanonikus modellre. Más CRM-hez új normalizáló réteg kell, de az interfész ugyanaz.

### ⚠️ RoleInstancePlanner (`src/engine/role_instance_logic.py`)

**Agnosztikus:** A szereplő- és példányszám logika általános (page scope-ok: global,
per_participant, per_property). Nincs benne OTP-kötődés. A `FormStructure` osztály
viszont OTP-konfigurációt vár — ezt kellene kívülről paraméterezni.

## 2. Mit kell tennie egy új banknak / nyomtatványnak

Egy új bank integrálásához a következő inputok kellenek:

1. **Üres nyomtatványcsomag** (AcroForm PDF vagy lapos PDF)
2. **New bank mapping**: a rendszer lefuttatja a `FieldRecognizer`-t → kap egy
   mapping JSON-t → emberi jóváhagyás → kész
3. **FormStructure** (opcionális): ha a nyomtatvány oldalanként ismétlődő struktúrájú
   (szereplőnként/ingatlanonként), akkor egy `FormStructure` objektum kell a
   `RoleInstancePlanner`-hez
4. **CRM normalizáló** (opcionális): ha nem Salesforce, kell egy új normalizáló osztály
5. **DocumentAssembly** (opcionális): ha a banknak van master/slice struktúrája,
   kell egy új assembler konfiguráció

## 3. Mit KELLENE változtatni a teljesen általános használathoz

1. **`OTP_EXACT_MAP` áthelyezése** konfigurációba (pl. `otp_exact_map.json`),
   ne legyen a kódban. Így új bank esetén a saját exact map-ja is opcionális JSON-ként
   tölthető be.
2. **`DocumentAssembler` szekció-térkép** kívülről paraméterezhető legyen
   (jelenleg konstansokban van a kódban).
3. **`ProductType` enum** → string-alapú vagy kívülről bővíthető.
4. **`bank_name`** ne legyen hardcoded "OTP Bank" a mapping outputokban.
5. **`KEYWORD_MAP`** OTP prefixei konfigurálhatóvá tétele.

## 4. Következtetés

**Igen, a mechanizmus dokumentum-agnosztikus.** A mezőfelismerő és a kitöltőmotor
nem tartalmaz bank-specifikus logikát. Az OTP-specifikus részek (OTP_EXACT_MAP,
DocumentAssembler szekciók, mapping JSON-ek) jól elkülönített rétegekben vannak,
amiket új bank esetén ki kell cserélni, nem módosítani.

A specifikációban leírt skálázhatósági ígéret — "új bank = új konfiguráció, nem fejlesztés" —
teljesül, az 5 kisebb változtatással fent maradéktalanul is.

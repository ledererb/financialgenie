# FinancialGenie — Rendszer Áttekintés

**Készítette:** Think AI Kft.  
**Ügyfél:** Rufus Pénzügyi Bróker  
**Dátum:** 2026. június 29.  
**Verzió:** 1.0 — Proof of Concept (PoC)

---

## 1. Összefoglaló

A **FinancialGenie** egy automatizált dokumentumkitöltő rendszer, amelyet a Think AI Kft. fejlesztett a Rufus Pénzügyi Bróker számára. A rendszer közvetlenül a Salesforce-ból kéri le az ügyféladatokat, és azokat automatikusan beilleszti a banki PDF-űrlapokba — kiküszöbölve a kézi kitöltés szükségességét.

### Kulcs előnyök

| Előny | Részletek |
|:------|:----------|
| ⏱️ **Időmegtakarítás** | Egy 97 oldalas hitelkérelem kitöltése percek helyett másodpercek alatt történik |
| ✅ **Pontosság** | A gépelési hibák, elírások és inkonzisztenciák megszűnnek |
| 🔄 **Konzisztencia** | Minden dokumentumban azonos adatok jelennek meg, azonos formátumban |
| 🔒 **Adatbiztonság** | Az ügyféladatok soha nem hagyják el a zárt Salesforce ↔ FinancialGenie rendszert |

> [!IMPORTANT]
> A FinancialGenie jelenleg **Proof of Concept (PoC)** fázisban van. A rendszer működőképes és használható, de a teljes produkciós üzembe helyezéshez további lépések szükségesek (lásd: [8. Következő lépések](#8-következő-lépések)).

---

## 2. Hogyan működik?

A rendszer öt lépésben dolgozik. Az alábbi folyamatábra szemlélteti a teljes működést:

```
┌─────────────────────────────────────────────────────────────────────┐
│                     FinancialGenie — Működési folyamat              │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│   ① Salesforce-ból adatlekérés                                      │
│   │  Opportunity, Contact, Property__c adatok lekérése              │
│   ▼                                                                 │
│   ② Adatok normalizálása                                            │
│   │  Egységes formátumba konvertálás (dátumok, címek, összegek)     │
│   ▼                                                                 │
│   ③ PDF mezők leképezése                                            │
│   │  A Salesforce mezők hozzárendelése a PDF mezőkhöz               │
│   ▼                                                                 │
│   ④ Automatikus kitöltés                                            │
│   │  Determinisztikus, szabály-alapú PDF kitöltés                   │
│   ▼                                                                 │
│   ⑤ Visszaírás Salesforce-ba                                       │
│      A kitöltött dokumentum feltöltése az Opportunity-hoz           │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### Az egyes lépések részletezése

| Lépés | Leírás | Technológia |
|:-----:|:-------|:------------|
| **①** | A rendszer lekéri az aktuális ügylet (Opportunity) összes kapcsolódó adatát a Salesforce-ból: ügyféladatok, ingatlanadatok, hitelparaméterek. | Salesforce API |
| **②** | Az adatok egységes formátumba kerülnek: dátumformázás, név-sorrend, cím-összeállítás, összegek kerekítése. | Python + Pydantic |
| **③** | A mapping konfiguráció alapján a rendszer tudja, melyik Salesforce mező melyik PDF mezőbe kerüljön. | JSON konfiguráció |
| **④** | A PDF-eket a rendszer **determinisztikusan** tölti ki — szabályok alapján, nem mesterséges intelligenciával. | pikepdf + PyMuPDF |
| **⑤** | A kitöltött PDF visszakerül a Salesforce-ba, az adott Opportunity-hoz csatolva. | Salesforce API |

> [!NOTE]
> **Fontos megkülönböztetés: az AI szerepe a rendszerben**
>
> A mesterséges intelligencia (Claude Sonnet 4) **kizárólag** az új PDF-sablonok feldolgozásánál segít — azaz a mezők felismerésénél és a leképezési javaslatok készítésénél. Az ügyféladatok kitöltése **soha nem AI-alapú**, hanem determinisztikus, szabály-alapú folyamat. Ez azt jelenti, hogy az ügyféladatok **soha nem kerülnek AI modellhez**.

---

## 3. Salesforce integráció

A FinancialGenie közvetlenül a Salesforce-ból dolgozik. A rendszer az alábbi Salesforce objektumokat használja:

### Használt Salesforce objektumok

| Objektum | Szerepe | Példa mezők |
|:---------|:--------|:------------|
| **Contact** | Ügyfél személyes adatai | Név, adószám, születési dátum, anyja neve, telefon, e-mail |
| **Lead** | Ingatlan- és előminősítési adatok | Ingatlan település, irányítószám, alapterület, becsült érték |
| **Opportunity** | Ügylet és hitelparaméterek | Hitelösszeg, hitelcél, termék, státusz |
| **Property__c** | Ingatlan részletes adatai | Cím, típus, helyrajzi szám, tulajdoni lap |
| **Opportunity_Property_Role__c** | Ingatlan–ügylet kapcsolat | Ingatlan szerepe az ügyletben |

### Mezőstatisztika

A rendszer jelenleg **236 Salesforce mezőt** támogat, az alábbi megoszlásban:

```
Contact mezők ········· 142 db   ██████████████████████████████░░░░░░  60%
Opportunity mezők ······ 56 db   ██████████████░░░░░░░░░░░░░░░░░░░░░  24%
Lead mezők ············· 38 db   ██████████░░░░░░░░░░░░░░░░░░░░░░░░░  16%
                        ────────
Összesen               236 db
```

### Példa mezőkre objektumonként

**Contact (142 mező)** — személyes és jövedelmi adatok:

| Salesforce API név | Tartalom |
|:-------------------|:---------|
| `Contact.Name` | Ügyfél teljes neve |
| `Contact.Tax_ID__c` | Adóazonosító jel |
| `Contact.Birthdate` | Születési dátum |
| `Contact.Mother_s_Name__c` | Anyja születési neve |
| `Contact.MobilePhone` | Mobiltelefonszám |
| `Contact.Email` | E-mail cím |
| `Contact.ZIP__c` | Irányítószám |
| `Contact.Income_type__c` | Jövedelem típusa |
| `Contact.Loan_amount__c` | Kért hitelösszeg |
| `Contact.Loan_period__c` | Hitel futamidő |

**Lead (38 mező)** — ingatlanadatok és előminősítés:

| Salesforce API név | Tartalom |
|:-------------------|:---------|
| `Lead.Ingatlan_telepules__c` | Ingatlan települése |
| `Lead.Ingatlan_irsz__c` | Ingatlan irányítószáma |
| `Lead.Ingatlan_alapterulet__c` | Ingatlan alapterülete (m²) |
| `Lead.Ingatlan_jellege__c` | Ingatlan jellege (lakás, ház, stb.) |
| `Lead.Estimated__c` | Becsült ingatlanérték |
| `Lead.Purchase_price__c` | Vételár |

**Opportunity (56 mező)** — ügylet és hitelparaméterek:

| Salesforce API név | Tartalom |
|:-------------------|:---------|
| `Opportunity.Hitel_sszeg__c` | Hitelösszeg |
| `Opportunity.Hitelc_l__c` | Hitelcél |
| `Opportunity.Term_k__c` | Termék megnevezése |
| `Opportunity.StageName` | Ügylet státusza |

> [!TIP]
> A Salesforce **mindig az egyetlen igazságforrás** (single source of truth). A FinancialGenie nem tárol ügyféladatokat — minden adat közvetlenül a Salesforce-ból érkezik a kitöltés pillanatában.

---

## 4. Támogatott dokumentumok

A rendszer jelenleg **16 mapping konfigurációt** tartalmaz az alábbi OTP Bank dokumentumokhoz:

### Fő banki dokumentumok

| # | Dokumentum neve | PDF mezők | Leképezve | Lefedettség |
|:-:|:----------------|----------:|----------:|:-----------:|
| 1 | **OTP Piaci Hitel** (mester PDF, 97 oldal) | 1 744 | 382 | 22% |
| 2 | **OTP Otthon Start** (2 személyhez) | 1 723 | 73 | 4% |
| 3 | **Igénylési Dokumentum Előzetes Értékbecsléshez** | 1 030 | 204 | 20% |
| 4 | **V. számú Függelék** — Ügyfél/Cég Nyilatkozat | 90 | 1 | 1% |
| 5 | **Zöld Lakáshitel Fiataloknak** — Nyilatkozat | 34 | 17 | 50% |
| 6 | **OTP Jövedelem Ellenőrzés** | 24 | — | — |
| 7 | **Hozzájáruló Nyilatkozat Munkaviszonyról** | 24 | 6 | 25% |
| 8 | **OTP Zöld Lakáshitel** | 23 | 13 | 57% |
| 9 | **Igazolás CSOK ÁFA Kamattámogatási Kérelem** | 13 | 8 | 62% |
| 10 | **OTP CSOK ÁFA Igazolás** | 7 | 4 | 57% |
| 11 | **OTP Partner Nyilatkozat** | 6 | 1 | 17% |

### Teszt és minta dokumentumok

| # | Dokumentum neve | Megjegyzés |
|:-:|:----------------|:-----------|
| 12–16 | Minta PDF-ek és tesztsablonok | Fejlesztési és validációs célra |

> [!NOTE]
> A fenti lefedettségi százalékok a PoC aktuális állapotát tükrözik. A cél a produkciós változatban **90%+ lefedettség** elérése az összes fő dokumentumtípusnál.

---

## 5. Mapping Editor (Szerkesztő Felület)

A FinancialGenie tartalmaz egy **webes szerkesztő felületet**, amelyen keresztül a mezőleképezések kezelhetők, szerkeszthetők és új dokumentumok dolgozhatók fel.

### Technológia

| Komponens | Technológia |
|:----------|:------------|
| Frontend | React + TypeScript + Vite |
| Backend | Python + FastAPI |
| API végpontok | 25+ REST endpoint |

### Fő funkciók

```
┌──────────────────────────────────────────────────────────────┐
│                   Mapping Editor — Funkciók                   │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  📄 PDF oldalnézet                                           │
│     Oldalankénti képmegjelenítés a PDF-ből                    │
│                                                              │
│  🤖 AI-alapú mezőfelismerés                                  │
│     Claude Sonnet 4 javasolja a leképezéseket                │
│     (csak üres sablonokat lát, ügyféladatokat SOHA)          │
│                                                              │
│  ✏️ Kézi szerkesztés                                         │
│     Minden leképezés manuálisan felülbírálható               │
│                                                              │
│  📊 Review Dashboard                                         │
│     Statisztikák, oldalankénti állapot, konfliktusok         │
│                                                              │
│  🗂️ Salesforce mezőcsoportok                                │
│     Contact / Lead / Opportunity szerinti szűrés              │
│                                                              │
│  📥 Export / Import                                          │
│     JSON konfiguráció exportálása és importálása              │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### A szerkesztő felület előnyei

- **Átláthatóság:** Minden leképezés vizuálisan ellenőrizhető, oldalról oldalra
- **Kontroll:** A bróker munkatársai bármikor módosíthatják a leképezéseket
- **Skálázhatóság:** Új PDF dokumentumok könnyedén feldolgozhatók AI segítségével
- **Auditálhatóság:** A Review Dashboard megmutatja a hiányzó, ellentmondásos vagy problémás leképezéseket

---

## 6. Technológiai stack

| Komponens | Technológia | Szerep |
|:----------|:------------|:-------|
| **Backend** | Python 3.12 + FastAPI | API szerver, üzleti logika, PDF feldolgozás |
| **Frontend** | React + TypeScript + Vite | Mapping Editor webes felülete |
| **PDF kezelés** | pikepdf (AcroForm) + PyMuPDF + reportlab | PDF mezők olvasása, kitöltése, overlay generálás |
| **AI** | Anthropic Claude Sonnet 4 | Mezőfelismerés és leképezési javaslatok (csak sablonokhoz) |
| **CRM** | Salesforce (simple-salesforce) | Ügyféladatok lekérése és dokumentumok visszaírása |
| **Adatmodell** | Pydantic v2 | Adatvalidáció és típusbiztonság |

---

## 7. Biztonsági szempontok

Az adatbiztonság és a GDPR-megfelelőség a FinancialGenie tervezésének alapvető szempontjai.

### Alapelvek

| Szempont | Megvalósítás |
|:---------|:-------------|
| 🔒 **AI és ügyféladatok** | Az AI **soha nem lát ügyféladatot**. Kizárólag üres PDF-sablonokat elemez a mezőfelismerés során. |
| ⚙️ **Kitöltési módszer** | A dokumentumkitöltés **determinisztikus** (szabály-alapú). Nem AI dönt arról, mit ír a mezőbe — a leképezési konfiguráció határozza meg. |
| 📍 **Adatforrás** | A Salesforce az adatok **egyetlen, hiteles forrása**. A rendszer nem tárol, nem cachel ügyféladatot. |
| 🇪🇺 **GDPR** | Személyes adatok nem hagyják el a **Salesforce ↔ FinancialGenie zárt rendszert**. Külső AI szolgáltatáshoz ügyféladat nem kerül. |

### Adatfolyam biztonsági szempontból

```
┌─────────────┐         ┌──────────────────┐         ┌─────────────┐
│             │  Zárt   │                  │  Zárt   │             │
│  Salesforce │◄───────►│  FinancialGenie  │◄───────►│  Kitöltött  │
│  (adatok)   │ csatorna│  (feldolgozás)   │ csatorna│  PDF-ek     │
│             │         │                  │         │             │
└─────────────┘         └──────────────────┘         └─────────────┘
                                 ▲
                                 │ Csak üres sablonok
                                 ▼
                        ┌──────────────────┐
                        │   Claude AI      │
                        │ (mezőfelismerés)  │
                        │ Ügyféladat: ❌    │
                        └──────────────────┘
```

> [!CAUTION]
> Az AI modell (Claude Sonnet 4) kizárólag a mapping editor részeként, üres PDF-sablonok elemzéséhez használatos. Ügyféladatok — nevek, adószámok, címek, jövedelmek — **soha, semmilyen körülmények között** nem jutnak el az AI modellhez.

---

## 8. Következő lépések

A PoC sikeres befejezése után az alábbi lépések szükségesek a produkciós üzembe helyezéshez:

### Rövid táv (1–3 hónap)

| Prioritás | Feladat | Jelenlegi állapot | Cél |
|:---------:|:--------|:-------------------|:----|
| 🔴 | **Mapping lefedettség növelése** | ~20% átlagos lefedettség | 90%+ az összes fő dokumentumnál |
| 🔴 | **Produkciós telepítés** | Fejlesztői környezet | n8n trigger, Salesforce stage-váltásra indulás |
| 🟡 | **Minőségbiztosítási folyamat** | Manuális ellenőrzés | Automatikus validáció + emberi jóváhagyás |

### Közép táv (3–6 hónap)

| Prioritás | Feladat | Leírás |
|:---------:|:--------|:-------|
| 🟡 | **További bank dokumentumok** | K&H Bank, Erste Bank, UniCredit dokumentumok integrálása |
| 🟢 | **Tömeges kitöltés** | Egy ügylethez tartozó összes dokumentum egyszerre történő kitöltése |
| 🟢 | **Felhasználói jogosultságkezelés** | Bróker-szintű hozzáférés-szabályozás |

### A fejlesztés ütemterve vizuálisan

```
     PoC (most)          Rövid táv              Közép táv
    ─────────────────────────────────────────────────────────►
    │                    │                       │
    ▼                    ▼                       ▼
    ✅ Alaprendszer      🔨 90%+ lefedettség     🏦 Több bank
    ✅ 16 dokumentum     🔨 Produkció            📦 Tömeges kitöltés
    ✅ Mapping Editor    🔨 QA folyamat          🔐 Jogosultságok
    ✅ Salesforce API    🔨 n8n integráció
```

---

## Kapcsolat

| | |
|:--|:--|
| **Fejlesztő** | Think AI Kft. |
| **Projekt** | FinancialGenie — Banki dokumentumkitöltő rendszer |
| **Ügyfél** | Rufus Pénzügyi Bróker |

---

*Ez a dokumentum a FinancialGenie rendszer Proof of Concept változatának állapotát tükrözi. A rendszer folyamatos fejlesztés alatt áll.*

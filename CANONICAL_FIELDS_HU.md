# Kanonikus Mezők Útmutatója (FinancialGenie)

Ez a dokumentum bemutatja a FinancialGenie rendszerében használt összes **kanonikus mezőt** (Canonical Field), azok pontos Salesforce API nevét, és azt, hogy a teszt Salesforce sandbox-ban mely mezőkhöz tartozik adat és melyekhez nem.

---

## 1. Mi az a Kanonikus Mező?

A FinancialGenie a PDF mezőket közvetlenül **Salesforce mezőkre** képezi le. Ez azt jelenti, hogy minden PDF mező egy Salesforce objektum és mező pároshoz van hozzárendelve, a következő formátumban:

```
SalesforceObjektum.api_mezo_nev
```

Például:
- `Contact.Name` → az adós teljes neve a Salesforce Contact objektumból
- `Opportunity.Hitel_sszeg__c` → a hitelösszeg a Salesforce Opportunity objektumból

**Miért ez a megközelítés?**

Ahelyett, hogy minden PDF-hez egyedi logikát írnánk, a Mapping Stúdióban egyszer összekötjük a PDF mezőit a Salesforce mezőkkel. Utána bármely ügyletnél a rendszer automatikusan lekéri a Salesforce-ból a megfelelő adatot, és beírja a PDF megfelelő helyére.

---

## 2. A Három Salesforce Objektum

A rendszer jelenleg három Salesforce objektumból dolgozik:

| Objektum | Mit tárol? | Prefix a mezőnevekben |
|---|---|---|
| **Contact** | Az ügyletben részt vevő személyek adatai (adós, adóstárs, kezes) | `Contact.` |
| **Opportunity** | Az ügylet és a hitel paraméterei | `Opportunity.` |
| **Lead** | A fedezeti ingatlan adatai | `Lead.` |

---

## 3. Az Összes Jelenleg Használt Canonical Field

> [!NOTE]
> Az alábbi táblázatokban az **Adat a SF-ben** oszlop a „FinancialGenie Test Opportunity" sandbox ügyletre vonatkozik.
> - ✅ = van adat a Salesforce-ban
> - ❌ = üres (null / nincs kitöltve)
> - ⚠️ = a mező nem létezik az objektumon (API hiba)

### Contact mezők – Személyes adatok

#### Azonosítás és alapadatok

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Name` | Teljes név | szöveg | ✅ `Teszt_Ados János` |
| `Contact.Szuletesi_nev__c` | Születési név | szöveg | ✅ `Teszt János` |
| `Contact.Mother_s_Name__c` | Anyja születési neve | szöveg | ✅ `Minta Mária` |
| `Contact.Place_of_Birth__c` | Születési hely | szöveg | ✅ `Budapest` |
| `Contact.Date_of_birth__c` | Születési dátum | dátum | ✅ `1985-05-12` |
| `Contact.Birthdate` | Születési dátum (standard SF mező) | dátum | ❌ |
| `Contact.Year_of_birthdate__c` | Születési év (csak az év) | szöveg | ❌ |
| `Contact.Salutation` | Neme (Mr./Ms.) | jelölőnégyzet | ❌ |

#### Okmányok

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.ID_Card_Number__c` | Személyazonosító igazolvány száma | szöveg / jelölőnégyzet | ✅ `123456AB` |
| `Contact.Tax_ID__c` | Adóazonosító jel | szöveg | ✅ `8401021234` |

#### Lakcím és levelezési cím

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Permanent_address__c` | Állandó lakcím (teljes, összetett) | szöveg | ✅ `1123 Budapest, Alkotás utca 12...` |
| `Contact.ZIP__c` | Állandó lakcím – irányítószám | szöveg | ❌ |
| `Contact.OtherCity` | Állandó lakcím – település | szöveg | ❌ |
| `Contact.OtherStreet` | Állandó lakcím – utca, házszám | szöveg | ❌ |
| `Contact.OtherCountry` | Állandó lakcím – ország (szöveges, külföldinél) | szöveg | ❌ |
| `Contact.MailingPostalCode` | Levelezési cím – irányítószám | szöveg | ❌ |
| `Contact.MailingCity` | Levelezési cím – település | szöveg | ❌ |
| `Contact.MailingStreet` | Levelezési cím típusa (azonos az állandóval / eltérő) | jelölőnégyzet | ❌ |
| `Contact.Legal_Title_of_permanent_residence__c` | Lakóhely jogcíme (saját tulajdon / albérlet / stb.) | jelölőnégyzet | ❌ |
| `Contact.Date_of_notification_for_residence__c` | Bejelentkezés dátuma az állandó lakcímre | dátum | ❌ |

#### Kapcsolat

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.MobilePhone` | Mobiltelefon | szöveg | ❌ |
| `Contact.Email` | E-mail cím | szöveg | ✅ `janos.teszt@example.com` |

> [!NOTE]
> A `Contact.Phone` mező tartalmazza a telefonszámot (`+36301234567`), de a mapping-ekben `Contact.MobilePhone` van használva, ami üres. A backend a `Phone`→`MobilePhone` konverziót automatikusan végzi.

#### Személyi adatok

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Marital_Status__c` | Családi állapot | jelölőnégyzet | ❌ |
| `Contact.Citizenship__c` | Állampolgárság | szöveg / jelölőnégyzet | ❌ |
| `Contact.Dependents_count__c` | Háztartás létszáma / eltartottak száma | szám | ❌ |
| `Contact.Relation__c` | Szerepkör az ügyletben (adós / adóstárs / zálogkötelezett) | jelölőnégyzet | ❌ |

#### Foglalkoztatás és jövedelem

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Employment_Type_c__c` | Foglalkoztatás típusa (alkalmazott / vállalkozó / stb.) | jelölőnégyzet | ❌ |
| `Contact.Foglalkozas_tipusa__c` | Foglalkoztatás – kiegészítő opció (pl. KATA) | jelölőnégyzet | ❌ |
| `Contact.Occupation__c` | Foglalkoztatás – szöveges (egyéb esetén) | szöveg | ❌ |
| `Contact.Name_of_employer__c` | Munkáltató neve | szöveg | ✅ `Fiktív Kft.` |
| `Contact.Self_employment_details__c` | KATA / önfoglalkoztatói adóalap | szöveg | ❌ |
| `Contact.Current_employment_started__c` | Jelenlegi munkaviszony kezdete | dátum | ❌ |
| `Contact.Highest_Educational_Qualification__c` | Legmagasabb iskolai végzettség | jelölőnégyzet | ❌ |

#### Jövedelem adatok

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Average_monthly_net_income__c` | Havi nettó jövedelem (Ft) | szám | ✅ `450 000` |
| `Contact.Income_type__c` | Jövedelem típusa (munkabér / vállalkozói / stb.) | jelölőnégyzet | ❌ |
| `Contact.Other_income__c` | Egyéb jövedelem megnevezése | szöveg | ❌ |
| `Contact.Other_monthly_income__c` | Egyéb jövedelem összege (Ft) | szám | ❌ |
| `Contact.Jovairas_vallalasa__c` | Jövedelem érkezési módja (számlára / készpénz) | jelölőnégyzet | ❌ |
| `Contact.Current_debt_Credit_limit__c` | Meglévő hitelkeret / törlesztési kötelezettség | szám | ❌ |

#### Hitelhez kapcsolódó Contact mezők

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Loan_Purpose__c` | Hitel célja (pl. lakásvásárlás) | szöveg | ❌ |
| `Contact.Loan_period__c` | Futamidő (hónapokban) | szám | ❌ |
| `Contact.Interest_Period__c` | Kamatperiódus | szöveg | ❌ |
| `Contact.Monthly_installment_payment__c` | Becsült havi törlesztő (Ft) | szám | ⚠️ mező nem létezik |
| `Contact.Own_resources__c` | Önerő összege (Ft) | szám | ⚠️ mező nem létezik |

#### Egyéb

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Contact.Description` | NAV nyilatkozat (adótartozás-mentesség jelölő) | jelölőnégyzet | ❌ |
| `Contact.Date_of_signature__c` | Aláírás dátuma | dátum | ❌ |

---

### Opportunity mezők – Ügylet és hitelparaméterek

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Opportunity.Term_k__c` | Hitel típusa / Termék neve | szöveg | ✅ `Lakáshitel` |
| `Opportunity.Hitel_sszeg__c` | Igényelt hitelösszeg (Ft) | szám | ✅ `25 000 000` |

---

### Lead mezők – Fedezeti ingatlan adatai

> [!NOTE]
> Az ingatlan adatok a Salesforce-ban `Property__c` custom objektumban vannak, de a mapping-ekben `Lead.` prefix-szel hivatkozunk rájuk.

| Canonical Field | Magyar megnevezés | Típus | Adat a SF-ben |
|---|---|---|---|
| `Lead.Estimated__c` | Becsült forgalmi érték (Ft) | szám | ✅ `45 000 000` |
| `Lead.Ingatlan_alapterulet__c` | Alapterület (m²) | szám | ✅ `64,5` |
| `Lead.Ingatlan_irsz__c` | Ingatlan irányítószáma | szöveg | ✅ `1123` |
| `Lead.Ingatlan_telepules__c` | Ingatlan települése | szöveg | ✅ `Budapest` |
| `Lead.Ingatlan_kozterulet_neve__c` | Ingatlan utcája és házszáma | szöveg | ✅ `Alkotás` |
| `Lead.Ingatlan_jellege__c` | Ingatlan típusa (lakás / ház / telek) | jelölőnégyzet | ✅ `Használt társasházi lakás` |
| `Lead.Ingatlan_megjegyzes__c` | Helyrajzi szám | szöveg | ✅ `12345/6/A` |

---

## 4. Különleges Esetek

### Jelölőnégyzet csoportok (Checkbox Groups)
Számos mező típusa `jelölőnégyzet`, mert a banki nyomtatványokon a Salesforce-ból érkező **szöveges értéket** (pl. `"lakás"`) egy konkrét checkbox bepipálásává kell alakítani.

A Mapping Stúdióban ilyenkor a jelölőnégyzeteket **csoportosítani** kell:
- `group_id`: a csoport neve (pl. `property_type`)
- `match_value`: az az érték, amelyiknél ez a checkbox bepipálódik (pl. `lakás`)

Ha a Salesforce-ból `"lakás"` érkezik, csak a `lakás` értékű checkbox lesz `igen`, a többiek `nem` maradnak.

### Cím mezők
A Salesforce-ban a cím több mezőre bontva van tárolva (irányítószám, település, utca+házszám külön). Ezeket a rendszer **egyenként** rendeli hozzá a PDF megfelelő cím-mezőihez — nem szükséges manuálisan összefűzni vagy szétszedni.

> [!IMPORTANT]
> A teszt sandbox-ban a `Contact.Permanent_address__c` mezőben az összes címadat egy szövegként van tárolva (`1123 Budapest, Alkotás utca 12...`), de a bontott mezők (`ZIP__c`, `OtherCity`, `OtherStreet`) üresek. A rendszer a `SalesforceClient`-ben automatikusan feldarabolja az összetett címet a megfelelő részekre.

---

## 5. Összefoglalás

| | Kitöltve | Üres | ⚠️ Nem létezik | Összesen |
|---|---|---|---|---|
| **Contact** | 12 | 31 | 2 | 45 |
| **Opportunity** | 2 | 0 | 0 | 2 |
| **Lead** | 7 | 0 | 0 | 7 |
| **Összesen** | **21** | **31** | **2** | **54** |

> [!WARNING]
> A teszt sandbox Contact rekordjain a mezők **nagy része üres** (31/45). Ezek a mezők léteznek a Salesforce sémában, de a teszt adatokkal nincsenek kitöltve. Éles használatban az ügyintéző tölti ki ezeket a mezőket az ügyfél adataival.

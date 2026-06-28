# FinancialGenie — Teljes rendszer kiterjesztése

## Kontextus
Repo: `/home/openclaw/.openclaw/workspace/financialgenie`
Salesforce sandbox: skyrocketgroup--uat.sandbox.my.salesforce.com
Teszt deal: `006WB00000LwTDgYAN` (FinancialGenie Test Opportunity, adós: Teszt_Ados János 003WB00000rsjcAYAQ, adóstárs: Teszt_Adostars Mária 003WB00000rsaJPYAY, 1 property)

## Mit kell csinálni

### 1. V. számú függelék mapping (96 AcroForm mező)

Fájl: `V_fuggelek.pdf` a working dir-ben (165KB, 6 oldal)
96 mező, ebből:
- ~14 névvel: `SZA_IG_dátum`, `SZA_IG_dátum.2`, `SZA_IG_hely`, `SZA_IG_hely.2`, `Sec_SZA_IG_dátum`, `Sec_SZA_IG_dátum.4`, `Sec_SZA_IG_hely`, `Sec_SZA_IG_hely.4`
- ~82 generikus: `Jelölőnégyzet NNN`, `Szövegmező NNN`
- 2 db: `2`, `4` (névtelen mezők)

Futtasd a FieldRecognizer-t ezen a PDF-en:
```bash
python3 -m src.main --template V_fuggelek.pdf --recognize --scenario simple
```
Ez generál egy `src/mapping/V_fuggelek_mapping.json`-t (vagy hasonló nevet).

Ha a recognizer nem tudja a generikus mezőket, próbáld a --context mode-ot. Ha az sem elég, írj egy helper scriptet ami a PDF-ből kinyeri a mezőneveket (`pikepdf`-fel `pdf.Root['/AcroForm']['/Fields']` iteráció) és a környező szöveggel együtt elküldi DeepSeek-nek.

### 2. Előzetes értékbecslés mapping (1029 mező)

Fájl: `elozetes_ertekbecsles.pdf` (554KB, 18 oldal)
1029 AcroForm mező, **MIND** generikus: `Check Box NNN`, `Text Field NNN`

Itt nem elég a név alapú AI recognition — mert a mezőnevek teljesen értelmetlenek. Kell egy **context-based field-to-field mapping**:

a) Extraháld a mezők pozícióját (page, x, y, width, height) PyMuPDF-ből
b) Extraháld a környező szöveget minden mezőhöz (oldal szintű szöveg)
c) Küldd el DeepSeek-nek batch-ben: "Given this form field at position (page X, around text Y) in an OTP property valuation request form, what data does it contain?"

Javasolt prompt struktúra:
```
You are analyzing an OTP Bank "Előzetes értékbecslés" (preliminary property valuation) PDF form.
Each field has a position on a page and surrounding text context.
Map each to a canonical field if possible.

Available canonical fields for reference:
- participant.name, participant.birth_name, participant.mother_name, participant.birth_city, participant.birth_date, participant.id_card_number
- participant.residence_address, participant.phone, participant.email, participant.tax_id
- participant.marital_status, participant.nationality, participant.education
- participant.employer_name, participant.employment_type, participant.monthly_income
- property.street, property.city, property.zip, property.property_number, property.floor, property.area_sqm
- property.property_type, property.energy_certificate, property.valuation
- deal.loan_amount, deal.loan_purpose, deal.term_years, deal.interest_rate
- legal.accepts_terms, legal.consent_mortgage, legal.consent_salary_transfer (és más jogi checkboxok)
- {add more as needed}
```

Célszerű page-by-page feldolgozás (18 oldal × ~55 mező/oldal).

### 3. Flat PDF overlay (4 dokumentum, 0 AcroForm mező)

Ezek a PDF-ek nem tartalmaznak kitölthető űrlapmezőket — a szöveget **közvetlenül a PDF-re kell overlay-elni**:

- `partner_nyilatkozat.pdf` — 1 oldal, Partner nyilatkozat hiteligénylés leadásakor
- `hozzajarulo_nyilatkozat.pdf` — 1 oldal, Hozzájáruló nyilatkozat munkaviszony/jövedelem ellenőrzéshez
- `zold_lakashitel_nyilatkozat.pdf` — 2 oldal, Zöld Lakáshitel nyilatkozat
- `CSOK_afa_igazolas.pdf` — 1 oldal, CSOK/ÁFA kamattámogatási kérelem átvételi igazolás

**Megközelítés:**
Használd a `src/engine/pdf_filler.py`-ban lévő `OverlayFiller` osztályt (ha létezik), vagy implementálj egy új overlay mechanizmust PyMuPDF segítségével:

```python
# Alap overlay stratégia:
# 1. PDF oldal renderelése
# 2. Szöveg pozíciók detektálása (keresd a kitöltendő üres helyeket)
# 3. Szöveg beillesztése adott koordinátákra
# 4. Checkboxok jelölése (X vagy pipa karakter)
```

Mivel ezek a PDF-ek szabad formátumú nyilatkozatok, valószínűleg a következő mezőket kell kitölteni:
- **Partner nyilatkozat**: név, dátum, aláírás
- **Hozzájáruló nyilatkozat**: név, munkáltató, jövedelem adatok, dátum
- **Zöld Lakáshitel**: név, lakcím, ingatlan adatok, dátum, checkboxok (első lakás tulajdon)
- **CSOK/ÁFA igazolás**: név, gyermek adatok, támogatás típusa, dátum

### 4. Salesforce lekérdezés kiterjesztése

Jelenleg a `salesforce_client.py` a következő mezőket kérdezi le:
- Contact: ~25 mező (személyes adatok, foglalkozás, jövedelem)
- Opportunity: egy pár mező
- Property__c: néhány mező

**Ki kell terjeszteni** a hiányzó mezőkkel. A teljes Salesforce schema:

Contact custom fields (check `config/sf_schema.txt`):
```
Mother_s_Name__c, Szuletesi_nev__c, Place_of_Birth__c, Date_of_birth__c
ID_Card_Number__c, Address_Card_Number__c, Tax_ID__c, Citizenship__c
Marital_Status__c, Highest_Educational_Qualification__c
Name_of_employer__c, Occupation__c, Employment_Type_c__c
Employer_s_company_type__c, Ownership_in_employer__c
Contract_start_date__c, Contract_end_date__c
Current_employment_started__c
Average_monthly_net_income__c, Other_monthly_income__c
Other_income__c (textarea), Monthly_Payment_details__c
Income_type__c, Foglalkozas_tipusa__c
Affordable_monthly_installments__c, Loan_amount__c, Loan_period__c
Permanent_address__c, Residence_Status__c
Date_of_notification_for_residence__c
Dependents_count__c, Numer_and_Age_of_Children__c
Property_Type__c, Property_value__c, Property_encumbrances__c
Estimated_market_value__c
Loan_Purpose__c, Purpose_real_estate_address__c
Property_50pct_ownership_details__c
Bank__c, Preferalt_bank__c, Nem_preferalt_bank__c
Continuous_TB_2_years__c, State_Support__c
```

Property__c custom fields:
```
Ingatlan_neve__c, Property_Type__c (ingatlan típusa)
Ingatlan_irsz__c, Ingatlan_telepules__c, Ingatlan_kozterulet_neve__c
Ingatlan_Kozterulet_jellege__c, Ingatlan_hazszam__c
Ingatlan_emelet__c (épület, lépcsőház, emelet, ajtó)
Ingatlan_hrsz__c (helyrajzi szám), Ingatlan_jellege__c
Ingatlan_hasznalatbaveteli__c, Ingatlan_osztatlan__c
Ingatlan_energetika__c, Ingatlan_alapterulet__c
Ingatlan_megjegyzes__c
Purchase_price__c, Property_value__c, Ingatlan_terhek__c
Ingatlan_szerepe__c
```

### 5. Multi-PDF pipeline mód

Csinálj egy új `--all` flag-et a `main.py`-ban, ami:
1. Betölti az összes mapping-et a `src/mapping/`-ből
2. Kiválasztja a megfelelő PDF template-ot a mapping alapján
3. Futtatja a pipeline-t minden PDF-re
4. Összesíti az eredményt

Vagy alternatíva: készíts egy scriptet ami batch-ben futtatja:
```bash
for pdf in *.pdf; do
    python3 -m src.main --template "$pdf" --mapping "src/mapping/$(basename $pdf .pdf)_mapping.json" --salesforce --deal 006WB00000LwTDgYAN
done
```

### 6. Legal declarations beállítás (ha vannak új checkboxok)

A V függelékben ~23 `Jelölőnégyzet NNN` checkbox van. Ha a legal classifier már lefutott a v5 PDF-re, ezeket is hozzá kell adni.

## Tesztelés

Minden lépés után futtasd:
```bash
python3 -m src.main --template <pdf> --mapping <mapping> --salesforce --deal 006WB00000LwTDgYAN 2>&1 | tail -20
```

A pipeline output-nak ezt kell mutatnia:
```
Kitöltés sikeres | kitöltött: <N>, kihagyott: <M>, hibák: 0
```

ahol N az adott PDF-ben reálisan kitölthető mezők száma.

## Commit

Minden kész lépés után:
```bash
git add -A && git commit -m "<leírás>"
```

Ne commitolj nagy bináris PDF fájlokat — `.gitignore`-ban kivéve vannak?

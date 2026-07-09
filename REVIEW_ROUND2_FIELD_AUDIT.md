# Round 2 — Canonical Field Audit

Cross-reference of the **54 canonical fields** defined in `docs/kanonikus_mezok_utmutato.pdf` / `docs/CANONICAL_FIELDS_HU.md` against the **actual code**: the pipeline value-source (`_participant_to_dict`, `_address_to_dict`, `_prepare_field_data` in `src/main.py`), the live SF query (`salesforce_client.py`), and the production OTP v5 mapping (`src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json`).

## Legend

- **In Catalog?** — listed in the 54-field catalog (`CANONICAL_FIELDS_HU.md`).
- **Produced by pipeline?** — does `main.py` ever write a real, non-empty value for this key into `field_data`?
  - `YES` = a real value is produced.
  - `GHOST` = the key is emitted but **always `""`** (backing `Participant` attribute does not exist — see Round-1 finding 4.1).
  - `NO` = the key is never written → any PDF field mapped to it stays **empty**.
- **In SF live query?** — read by the SOQL `contact_fields`/`prop_fields` SELECT in `salesforce_client.py` (live mode).
- **OTP v5 mapped?** — referenced by `igenylesi_dokumentumok_elso_6_oldal_mapping.json` (count of PDF fields).
- **Status** — bold = mismatch / will break or stay empty.

---

## Contact — Identification & basics

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Contact.Name` | ✅ | YES (`p.name`) | ✅ | 2× | ✅ OK |
| `Contact.Szuletesi_nev__c` | ✅ | YES (`p.birth_name`) | ✅ | 2× | ✅ OK |
| `Contact.Mother_s_Name__c` | ✅ | YES (`p.mother_name`) | ✅ | 2× | ✅ OK |
| `Contact.Place_of_Birth__c` | ✅ | YES (`p.birth_place`) | ✅ | 2× | ✅ OK |
| **`Contact.Date_of_birth__c`** | ✅ (has SF data) | **NO** (pipeline emits `Birthdate` + `_year/_month/_day`) | ✅ read | — (heuristic OTP_EXACT_MAP routes year/month/day boxes here → **empty**) | **🔴 BROKEN — Round-1 §1.1** |
| `Contact.Birthdate` | ✅ | YES (`%Y.%m.%d`) | ❌ not read (SF gives `Date_of_birth__c`) | — | ⚠️ value-source mismatch (normalizer bridges it) |
| **`Contact.Year_of_birthdate__c`** | ✅ | **NO** (pipeline emits `Birthdate_year`, different key) | ❌ | — | **🟠 catalog↔code key drift** |
| **`Contact.Salutation`** (gender) | ✅ | **NO** (no gender field on `Participant`) | ❌ | 2× | **🟠 OTP v5 expects it; never filled** |

## Contact — Documents

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Contact.ID_Card_Number__c` | ✅ | YES (`p.personal_id`) | ✅ | 8× | ✅ OK |
| `Contact.Tax_ID__c` | ✅ | YES (`p.tax_id`) | ✅ | 2× | ✅ OK |

## Contact — Address (permanent & mailing)

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| **`Contact.Permanent_address__c`** | ✅ (has SF data, composite) | **NO** (Round-1 B9) | ❌ parsed into parts | 2× | **🟠 OTP v5 maps 2 fields here → empty** |
| `Contact.ZIP__c` | ✅ | YES — but **collision**: both `_address_to_dict("address")` and `("mailing_address")` write `ZIP__c` (`main.py:575,581`) → last writer wins | ❌ (`ZIP__c` read separately) | 1× | **🟠 ZIP overwrite bug (NEW)** |
| `Contact.OtherCity` | ✅ | YES (permanent only) | ❌ | 1× | ✅ OK |
| `Contact.OtherStreet` | ✅ | YES (permanent only) | ❌ | 1× | ✅ OK |
| **`Contact.OtherCountry`** | ✅ | **NO** | ❌ | 1× | **🟠 never filled** |
| **`Contact.MailingPostalCode`** | ✅ | **NO** (mailing path writes `ZIP__c`, not `MailingPostalCode`) | ❌ | 1× | **🟠 never filled + ZIP collision** |
| `Contact.MailingCity` | ✅ | YES (mailing only) | ❌ | 1× | ✅ OK |
| `Contact.MailingStreet` | ✅ | YES (mailing only) | ❌ | 3× | ✅ OK |
| **`Contact.Legal_Title_of_permanent_residence__c`** | ✅ | **NO** | ❌ | 4× | **🟠 OTP v5 maps 4 checkbox fields here → empty** |
| **`Contact.Date_of_notification_for_residence__c`** | ✅ ("mióta lakik") | **NO** | ❌ | 3× | **🟠 Round-1 §1.2 — 3 boxes always empty** |

## Contact — Contact data

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Contact.MobilePhone` | ✅ | YES (phone normalized, `+`/spaces stripped) | ❌ (reads `Phone`; catalog notes auto `Phone→MobilePhone`) | 1× | ✅ OK (note: SF source is `Phone`, not `MobilePhone`) |
| `Contact.Email` | ✅ | YES | ✅ | 1× | ✅ OK |

## Contact — Personal data (the "ghost five")

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| **`Contact.Marital_Status__c`** | ✅ | **GHOST** (`getattr(p,"marital_status","")` → always `""`; no model attr) | ✅ read | 5× | **🟠 Round-1 §4.1 — 5 boxes always empty** |
| **`Contact.Citizenship__c`** | ✅ | **GHOST** | ✅ read | 3× | **🟠 always empty** |
| **`Contact.Dependents_count__c`** | ✅ | **GHOST** (`p.dependents_count` → always `None`) | ✅ read | 2× | **🟠 always empty** |
| `Contact.Relation__c` | ✅ | YES (`p.role.value`) — but **explicitly skipped** at `main.py:373` | ❌ | 3× (checkbox_group target) | ⚠️ only filled via checkbox-group resolver, not direct |
| **`Contact.Salutation`** | (see above) | **NO** | ❌ | 2× | **🟠 gender never filled** |

## Contact — Employment & income

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| **`Contact.Employment_Type_c__c`** | ✅ | **NO** | ❌ | — (heuristic routes `SZA_IG_foglalkoztatás` here) | **🟠 always empty** |
| **`Contact.Foglalkozas_tipusa__c`** | ✅ | **NO** | ❌ | 13× | **🟠 OTP v5 maps 13 checkboxes here → empty** |
| **`Contact.Occupation__c`** | ✅ | **NO** | ❌ | 1× | **🟠 empty** |
| `Contact.Name_of_employer__c` | ✅ | YES | ✅ | — | ✅ OK |
| **`Contact.Self_employment_details__c`** | ✅ | **NO** | ❌ | 1× (+ heuristic `kata`/`létszám`) | **🟠 empty** |
| **`Contact.Current_employment_started__c`** | ✅ | **NO** | ✅ read | — | **🟠 read but never produced** |
| **`Contact.Highest_Educational_Qualification__c`** | ✅ | **GHOST** (`getattr(p,"education","")`) | ✅ read | 4× | **🟠 always empty** |
| `Contact.Average_monthly_net_income__c` | ✅ (has SF data) | YES (money-formatted) | ✅ | 6× | ✅ OK (subject to `0→""` Round-1 §5) |
| **`Contact.Income_type__c`** | ✅ | **GHOST** | ✅ read | 7× | **🟠 always empty** |
| **`Contact.Other_income__c`** | ✅ | **NO** | ❌ | — (heuristic `IA_Bérletidíj`) | **🟠 empty** |
| **`Contact.Other_monthly_income__c`** | ✅ | **NO** | ❌ | 4× | **🟠 empty** |
| **`Contact.Jovairas_vallalasa__c`** | ✅ | **NO** | ❌ | — | **🟠 empty** |
| **`Contact.Current_debt_Credit_limit__c`** | ✅ | **NO** | ❌ | — | **🟠 empty** |

## Contact — Loan-related

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Contact.Loan_Purpose__c` | ✅ | YES (`loan.loan_purpose`) | ❌ (lives on Opportunity `Hitelc_l__c`) | — | ✅ OK |
| `Contact.Loan_period__c` | ✅ | YES (`loan_term_months`) | ❌ | — | ✅ OK |
| `Contact.Interest_Period__c` | ✅ | YES (`loan.interest_period`) | ❌ | — | ✅ OK |
| `Contact.Loan_amount__c` | *(not in 54)* | YES | ❌ | — | ✅ produced (catalog omits it) |
| **`Contact.Monthly_installment_payment__c`** | ✅ | **NO** | ❌ | — (`otp_acroform_mapping.json` maps `loan_monthly_payment` here) | **🔴 ⚠️ FIELD DOES NOT EXIST ON SF — Round-2 critical** |
| **`Contact.Own_resources__c`** | ✅ | **NO** | ❌ | — (`otp_acroform_mapping.json` maps `loan_down_payment` here) | **🔴 ⚠️ FIELD DOES NOT EXIST ON SF — Round-2 critical** |

## Contact — Other

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| **`Contact.Description`** | ✅ (NAV checkbox) | **NO** | ❌ (`Description` read on Opp, not Contact) | 1× | **🟠 empty** |
| **`Contact.Date_of_signature__c`** | ✅ | **NO** | ❌ | — | **🟠 empty** |

## Opportunity (2)

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Opportunity.Term_k__c` | ✅ | YES (`loan.product_name`) | ✅ (`Term_k__c`) | — | ✅ OK |
| `Opportunity.Hitel_sszeg__c` | ✅ | YES (money-formatted) | ✅ (`Hitel_sszeg__c`) | — | ✅ OK |

## Lead / Property (7)

| Canonical Field | In Catalog? | Produced by pipeline? | In SF live query? | OTP v5 mapped? | Status |
|---|---|---|---|---|---|
| `Lead.Estimated__c` | ✅ | YES | ✅ | — | ✅ OK (Round-1 §4.3 collision in `otp_acroform` fixture) |
| `Lead.Ingatlan_alapterulet__c` | ✅ | YES | ✅ | — | ✅ OK |
| `Lead.Ingatlan_irsz__c` | ✅ | YES | ✅ | — | ✅ OK (Round-1 §3.2 — `property2_*` gets prop 1 data) |
| `Lead.Ingatlan_telepules__c` | ✅ | YES | ✅ | — | ✅ OK (§3.2) |
| `Lead.Ingatlan_kozterulet_neve__c` | ✅ | YES | ✅ | — | ✅ OK |
| `Lead.Ingatlan_jellege__c` | ✅ | YES (`property_type.value`) | ✅ | — | ✅ OK (Round-1 §4.4 KEYWORD_MAP wrong target) |
| `Lead.Ingatlan_megjegyzes__c` | ✅ | YES (`parcel_number`) | ✅ (`Ingatlan_hrsz__c`) | — | ✅ OK (Round-1 §4.3 collision in fixture) |

---

## Non-existent canonicals emitted by the heuristic mapper

These are produced by `OTP_EXACT_MAP` / `KEYWORD_MAP` in `field_recognizer.py` but **are not real SF fields** and are **never produced by `main.py`** — they are silently unroutable (Round-1 §4.2):

| Heuristic canonical | PDF source | In 54 catalog? | Status |
|---|---|---|---|
| `loan.housing_savings` | `LAHI_KTKA_Lakástakarék` | ❌ | 🟠 always empty |
| `loan.refinance_account` | `LAHI_KTKA_Kiváltandó_kölcsön_szlaszám` | ❌ | 🟠 always empty |
| `loan.product_type` | `LAHI_KTKA_Kölcsön_tipusa_OTP` | ❌ | 🟠 always empty |
| `participant.business_name` | `MA_IG_vállalkozás_neve` | ❌ | 🟠 always empty |
| `participant.employer_tax_id` | `MA_IG_munáltató_adósz` | ❌ | 🟠 always empty |
| `participant.business_tax_id` | `MA_IG_vállalkozás_adószám` | ❌ | 🟠 always empty |

> Note: the `Participant` model *does* carry the underlying data (`LoanDetails.housing_savings`, `refinance_account`, `product_type`) — it's just never surfaced to a canonical key. See Gap Analysis §2.

---

## OTP v5 production mapping — rule-engine usage

`src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json` (199 fields, 71 of them `checkbox`):

| Mechanism | Count in OTP v5 mapping |
|---|---|
| `checkbox_group` defined | **0** |
| `fill_rule` defined | **0** |
| `fragment` defined | **0** |
| `character_groups` defined | **0** |
| `canonical_field == null` (unmapped) | **60** |

**Implication:** the production OTP v5 form relies *entirely* on direct `canonical_field → value` routing. None of the 71 checkbox fields carry any tick logic (no group, no rule, no fragment). The 7-rule checkbox system (`docs/hiteligenyles_kitoltesi_szabalyok.md`) is **not represented in the production mapping at all** — it is an unimplemented spec. See Gap Analysis Gap 1 and the Fill Engine Design.

---

## Summary roll-up

| Category | Count | Share of 54 |
|---|---|---|
| ✅ Produced with real data | **~21** | 39% |
| 🟠 Produced but always empty (GHOST) | **5** | 9% |
| 🟠 Never produced (catalog says it should be) | **~26** | 48% |
| 🔴 Non-existent on SF (API error if touched) | **2** | 4% |
| **Total catalog fields** | **54** | 100% |

**Bottom line:** fewer than 4 in 10 catalog fields are actually filled with real data today. Nearly half are mapped in forms but never sourced. The two `⚠️` fields (`Monthly_installment_payment__c`, `Own_resources__c`) are in shipped mapping fixtures and will hard-error the moment a live SF query touches them.

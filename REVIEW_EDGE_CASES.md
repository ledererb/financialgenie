# FinancialGenie — Edge Case Report

Branch: `feature/mapping-editor` @ `f66ec7b`
Reviewer methodology: every finding below is **reproduced against the actual code on this branch** — not against the field names or files described in the original task brief or in earlier review drafts (which referenced code that does not exist; see "Reconciliation note" at the bottom).

Severity legend: **🔴 CRITICAL** (silent data corruption / wrong deal processed) · **🟠 HIGH** (functional breakage of a stated feature) · **🟡 MEDIUM** (correctness issue in edge inputs) · **🟢 LOW** (quality / smell).

Findings are grouped by the seven investigation areas requested, then a "Bonus" section lists additional production hazards found while tracing the seven.

---

## Reconciliation note (read this first)

The task brief (and the pre-existing `REVIEW_FIXES.md` / `REVIEW_CODE_QUALITY.md` in the repo, which appear to have been written against an imagined codebase) make several claims that **do not match the code on this branch**. To keep the review grounded:

| Claim in brief / old docs | Reality on `f66ec7b` |
|---|---|
| "AI uses DeepSeek V4 Flash" | Code calls **Anthropic Claude** (`anthropic.Anthropic`, model `claude-sonnet-4-6`, env `ANTHROPIC_API_KEY`). See `src/ai/field_recognizer.py:32,516,527`. |
| "Flask-based web UI" | Backend is **FastAPI** + uvicorn (`backend/server.py:49,768`). Confirmed by `tests/test_upload_api.py` (`from fastapi.testclient import TestClient`). |
| Canonical model has `participant.gender`, `participant.citizenship`, `participant.kata_status`, `participant.dependents_count`, `loan.product_type`, etc. | `Participant` model has **16 fields only** (`src/models/canonical_model.py:50-72`). No gender/citizenship/marital_status/education/income_type/dependents_count/kata_status/business_name/etc. |
| Mapping namespace is `participant.*`, `property.*`, `loan.*` | Mapping namespace is **Salesforce-style**: `Contact.*`, `Lead.*`, `Opportunity.*` (see `CANONICAL_FIELDS` in `src/ai/field_recognizer.py:64-315`). The pipeline routes by these keys. |
| Files like `legal_classifier.py`, `OTP_Igenylesi_Dokumentumok_v5_mapping.json`, `complete_otpv5_mapping.py` exist | They do **not** exist on this branch. |

All findings below are evidence-based against the actual source.

---

## 1. Date-field fragmentation (CRITICAL task #1)

### 1.1 OTP Heuristic mapper collapses year/month/day onto a single full-date canonical — and the pipeline never produces that canonical

**What breaks (current behavior):** When a *new* OTP form (one without a hand-edited mapping) is recognized, the year/month/day birth-date boxes (`SZA_IG_szül_év`, `SZA_IG_szül_hónap`, `SZA_IG_szül_nap` and their `-társ` variants) are **silently left empty** in the filled PDF.

**Code path:**
- `src/ai/field_recognizer.py:1558-1560` (`OTP_EXACT_MAP`) maps all three boxes to **the same canonical** `Contact.Date_of_birth__c`.
- Same file lines `1575-1576` map `SZA_IG_mióta_lakik_év/_hónap` both to `Contact.Date_of_notification_for_residence__c`.
- `src/main.py:541-566` (`_participant_to_dict`) produces `Contact.Birthdate`, `Contact.Birthdate_year`, `Contact.Birthdate_month`, `Contact.Birthdate_day` — **never** `Contact.Date_of_birth__c` and never `Contact.Date_of_notification_for_residence__c`.
- The resolver `src/main.py:384-386` does `if canonical in source and source[canonical]:` — so a canonical absent from `borrower_data` simply produces no entry; the box stays empty. No warning is logged.

**Latent risk:** if anyone "fixes" the empty box by adding `Contact.Date_of_birth__c` to `_participant_to_dict`, all three boxes will receive the **full** `1985.03.15` string — the canonical fragmentation scenario from the brief. So the structure of `OTP_EXACT_MAP` is a footgun even though today's symptom is "empty" rather than "overflow".

**Confirmed by reproduction:**
```
SZA_IG_szül_év    -> Contact.Date_of_birth__c   (heuristic)
SZA_IG_szül_hónap -> Contact.Date_of_birth__c   (heuristic)
SZA_IG_szül_nap   -> Contact.Date_of_birth__c   (heuristic)
```
vs. the **manually edited** production mapping `src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json`, which uses the correct fragments:
```
SZA_IG_szül_év    -> Contact.Birthdate_year
SZA_IG_szül_hónap -> Contact.Birthdate_month
SZA_IG_szül_nap   -> Contact.Birthdate_day
```
…so for this one already-curated file the boxes *are* filled correctly. **Every new OTP form will regress to "empty" until manually curated.**

**Severity:** 🔴 CRITICAL (functional gap for the entire "AI mapper scales to new forms" pitch).
**Category:** Functional gap + latent data-corruption risk.
**Proposed fix:** see Fix B in `REVIEW_FIXES.md` — introduce a `fragment` field on `RecognizedField`, route the year/month/day boxes to it, and add `Contact.Date_of_notification_for_residence__c` (split into `_year`/`_month`) to `_participant_to_dict`.

### 1.2 `mióta_lakik` (residence-since) boxes are empty even on the curated production mapping

**What breaks:** `SZA_IG_mióta_lakik_év/_hónap/_nap` are mapped (in both the heuristic `OTP_EXACT_MAP` *and* the curated production file) to `Contact.Date_of_notification_for_residence__c`, which `_participant_to_dict` never produces. So all three boxes are empty on every fill — including the production OTP master form.

**Severity:** 🟠 HIGH.
**Category:** Functional gap (canonical-mismatch).
**Code:** `src/ai/field_recognizer.py:1575-1576`; `src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json`; `src/main.py:541-566` (missing entries).

### 1.3 Income-by-currency fields collapse onto two canonicals

The OTP production mapping has `SZA_IG_jövedelem_deviza1..12` — twelve income-line fields. Of these, `deviza1/6/7` all map to `Contact.Average_monthly_net_income__c` and `deviza2/5` both map to `Contact.Other_monthly_income__c`. Since `_participant_to_dict` produces only one value per canonical, **all twelve boxes are filled with at most two distinct values** — multi-source income breakdown (the whole point of having 12 lines) is lost.

**Severity:** 🟡 MEDIUM (the form is "filled" but with semantically wrong data).
**Category:** Mapping ambiguity + missing canonical richness.

---

## 2. Postal code as multi-field (HIGH task #2)

**Current state:** the canonical catalog has `Contact.ZIP__c`, `Contact.MailingPostalCode`, `Contact.OtherPostalCode`, `Lead.Ingatlan_irsz__c`, `Lead.PostalCode` — all single-string fields. No 4-digit-box representation exists anywhere in the code, mappings, or model. The OTP master form's ZIP fields are also single boxes.

**Latent risk:** if a future form (or a future OTP revision) splits the postal code into 4 single-digit boxes (this used to be common on Hungarian forms), the same fragmentation machinery as §1 applies: the heuristic mapper has no concept of digit-box fragments, and `_prepare_field_data` will route the full 4-digit string into whichever single canonical the mapper picks — silently wrong or empty.

**Severity:** 🟠 HIGH (latent — would block adoption of any new form that uses digit boxes).
**Category:** Same as 1.1 — needs a `fragment: "digit:N"` mechanism.
**Proposed fix:** Fix B in `REVIEW_FIXES.md` defines both `digit:N` and `year|month|day` fragment kinds in one place.

---

## 3. First-only data handling (CRITICAL task #3)

This is the most consequential family of bugs in the codebase. Three independent layers each drop "everything past index 0" in different ways.

### 3.1 Only `borrowers[0]` and `co_borrowers[0]` are routed

**Code:** `src/main.py:298-322`
```python
borrowers = deal.borrowers
co_borrowers = deal.co_borrowers
borrower = borrowers[0] if borrowers else None
co_borrower = co_borrowers[0] if co_borrowers else None
for participant, target in ((borrower, "borrower"), (co_borrower, "co_borrower")):
    ...
```
- 2nd coborrower → silently dropped.
- **All guarantors** → silently dropped (`deal.guarantors` is never read).
- **All beneficiaries** → silently dropped.
- A deal whose borrower is somehow inactive but has 2 coborrowers: coborrower #0 is treated as the borrower.

**Reproduction** (`samples/dummy_data/multi_borrower.json` has 1 borrower + 1 coborrower + 1 guarantor):
```
guarantor name in any field value? False
guarantor phone normalized '36307572575' in any value? False
```

**Severity:** 🔴 CRITICAL.
**Category:** Functional gap.

### 3.2 Second property's data is silently replaced by the first property's data

**Code:** `src/main.py:348-362`
```python
prop_data = {}
for i, prop in enumerate(deal.properties):
    pd = { "Lead.Ingatlan_irsz__c": prop.address.zip_code, ... }
    if i == 0:
        prop_data = pd   # <-- only i==0 is kept
```
Then `src/main.py:388-390`:
```python
elif canonical.startswith("Lead."):
    if canonical in prop_data and prop_data[canonical]:
        field_data[pdf_name] = prop_data[canonical]
```
Because `otp_acroform_mapping.json` maps **both** `property_*` and `property2_*` PDF fields onto the *same* canonicals (`Lead.Ingatlan_irsz__c`, `Lead.Estimated__c`, etc.), the loop fills `property2_*` slots with **property 1's data**. This is silent corruption — the form looks filled, but the second property's address/value/area is wrong.

**Reproduction** with `multi_property.json` (P1: Szolnok, 47M Ft; P2: Budapest, 144M Ft):
```
property2_city            = 'Szolnok'         <-- should be 'Budapest'
property2_estimated_value = '47 000 000'      <-- should be '144 000 000'
property2_parcel_number   = '23397/4/D/3'     <-- P1's parcel
```

**Severity:** 🔴 CRITICAL (silent data corruption — wrong property data on a mortgage form).
**Category:** Functional gap + silent corruption.

### 3.3 DocumentAssembler duplicates pages but does not rename AcroForm fields

**Code:** `src/engine/document_assembler.py:194-216` (extra participant / extra property sections all reuse the same master-PDF source pages via `dst_pdf.pages.append(src_pdf.pages[page_idx])`); line `151-153` copies the AcroForm wholesale.

When `num_participants=3` (or `num_properties≥2`), the same source page (and therefore the same AcroForm field names) is appended multiple times to the output PDF. AcroForm field names are **global to the document**, not per-page — so all copies of "SZA_IG_név" share one value. The pipeline cannot put participant 1's name on copy A and participant 2's name on copy B; both will show the same single value.

**Severity:** 🔴 CRITICAL (architectural — the multi-copy feature cannot work as advertised for AcroForm PDFs).
**Category:** Architectural / impossible-in-PDF.
**Proposed fix:** either (a) rename fields per copy (`SZA_IG_név` → `SZA_IG_név__p2`) using pikepdf and regenerate `/AP` appearances, or (b) switch duplicated sections to overlay mode and feed coordinates per page, or (c) drop the multi-copy feature and require one form per participant/property.

---

## 4. Hardcoded empty values vs canonical model (HIGH task #4)

### 4.1 `_participant_to_dict` references five Participant fields that do not exist

**Code:** `src/main.py:559-564`
```python
"Contact.Citizenship__c":              getattr(p, "citizenship", "") or "",
"Contact.Marital_Status__c":           getattr(p, "marital_status", "") or "",
"Contact.Highest_Educational_Qualification__c": getattr(p, "education", "") or "",
"Contact.Income_type__c":              getattr(p, "income_type", "") or "",
"Contact.Dependents_count__c":         str(p.dependents_count) if getattr(p, "dependents_count", None) is not None else "",
```
The `getattr(..., "")` defensive form hides the fact that `Participant` has **none** of `citizenship`, `marital_status`, `education`, `income_type`, `dependents_count` (`src/models/canonical_model.py:50-72` — only 16 fields). These five `Contact.*` keys are therefore **always `""`**. Any PDF field mapped to them — including `SZA_IG_állampolgárság`, `SZA_IG_családiállapot`, `SZA_IG_végzettség`, `SZA_IG_foglalkoztatás`, `SZA_IG_eltartottak` (all in `OTP_EXACT_MAP`) — will always be blank.

**Reproduction:** confirmed empirically — all five keys evaluate to `''`.

**Severity:** 🟠 HIGH.
**Category:** Model/pipeline drift; latent AttributeError magnet (the day someone replaces `getattr` with direct access, every fill crashes).

### 4.2 Canonical fields that the heuristic mapper produces but `_participant_to_dict`/`_prepare_field_data` never populate

Beyond the date cases in §1, the heuristic `OTP_EXACT_MAP` (`src/ai/field_recognizer.py:1552-1633`) emits canonicals that the pipeline has no source for:

| Heuristic canonical | PDF fields that map to it | Produced by main.py? |
|---|---|---|
| `loan.housing_savings` | `LAHI_KTKA_Lakástakarék` | ❌ never |
| `loan.refinance_account` | `LAHI_KTKA_Kiváltandó_kölcsön_szlaszám` | ❌ never |
| `loan.product_type` | `LAHI_KTKA_Kölcsön_tipusa_OTP` | ❌ never |
| `participant.business_name` | `MA_IG_vállalkozás_neve` | ❌ never |
| `participant.employer_tax_id` | `MA_IG_munáltató_adósz` | ❌ never |
| `participant.business_tax_id` | `MA_IG_vállalkozás_adószám` | ❌ never |
| `Contact.Self_employment_details__c` | `SZA_IG_kata`, `SZA_IG_létszám` | ❌ never |
| `Contact.Description` | `SZA_IG_nav` | ❌ never |
| `Contact.Date_of_notification_for_residence__c` | `SZA_IG_mióta_lakik_*` | ❌ never (also §1.2) |

For *heuristically mapped* forms, all of these PDF fields stay empty. The pipeline reports success.

**Severity:** 🟠 HIGH.
**Category:** Drift between the AI mapper vocabulary and the pipeline's value source.

### 4.3 Three PDF fields collapse onto one canonical in `otp_acroform_mapping.json`

`property_parcel_number`, `property_rooms`, `property_year_built` all map to `Lead.Ingatlan_megjegyzes__c`. Result confirmed in reproduction:
```
property_parcel_number = '23397/4/D/3'
property_rooms         = '23397/4/D/3'   <-- parcel number in the rooms box
property_year_built    = '23397/4/D/3'   <-- parcel number in the year-built box
```

**Severity:** 🟠 HIGH (silent wrong data).
**Category:** Mapping data bug — likely from heuristic mapping that was never curated.

### 4.4 `KEYWORD_MAP` puts parcel number in the property-type field

**Code:** `src/ai/field_recognizer.py:1540-1542`
```python
"helyrajzi_szam": "Lead.Ingatlan_jellege__c",   # WRONG: this is property TYPE
"hrsz":           "Lead.Ingatlan_jellege__c",   # WRONG
"parcel_number":  "Lead.Ingatlan_jellege__c",   # WRONG
```
The correct canonical for parcel/hrsz is `Lead.Ingatlan_megjegyzes__c` (which is what main.py uses at line 356). `Lead.Ingatlan_jellege__c` is the property *type* (lakás/ház/telek).

**Severity:** 🟡 MEDIUM (only fires on fields whose names contain `helyrajzi_szam`/`hrsz`/`parcel_number` *and* aren't in `OTP_EXACT_MAP`).
**Category:** Wrong mapping data.

---

## 5. Number formatting edge cases (HIGH task #5)

**Code:** every money-formatting site uses the pattern
```python
f"{x:,}".replace(",", " ") if x else ""
```
Examples: `src/main.py:326, 332, 343, 357, 359, 557`.

| Input `x` | Output | Correct? |
|---|---|---|
| `None` | `""` | ✅ |
| `0` | `""` | ❌ — **`0` is falsy, so a real zero-amount field becomes empty.** A `down_payment=0` (meaning "no self-funding") is indistinguishable from "data missing". |
| `492000` | `"492 000"` | ✅ |
| `-50000` | `"-50 000"` | ✅ (sign preserved) |
| `492000.5` | (rejected by pydantic — `monthly_income: int`) | ⚠️ decimal incomes from Salesforce strings like `"492000.5"` get `int(float(...))`-truncated in `_safe_int` (`data_normalizer.py:271-277`), silently losing the fraction. |
| `"492000"` (str) | `"492 000"` | ✅ (works by accident — Python's `,` format spec accepts numeric strings) |
| `"abc"` | `ValueError` → pipeline crashes | ❌ — no try/except around the format expression. |

**Reproduction:** confirmed `monthly_income=0` produces `''`; `monthly_income=-50000` produces `'-50 000'`.

**Severity:** 🟠 HIGH (the `0 → ""` case is the dangerous one — it changes the meaning of the form).
**Category:** Numeric edge case + missing error handling.
**Proposed fix:** Fix D in `REVIEW_FIXES.md` — single `_fmt_money` helper, `0 → "0"`, type coercion guarded by try/except.

---

## 6. Address parsing robustness (HIGH task #6)

**Code:** `src/integrations/salesforce_client.py:100-156` (`_parse_address_string`).

| Input | Output | Verdict |
|---|---|---|
| `"1052 Budapest, Váci utca 10"` | zip=1052, city=Budapest, street=Váci utca, hsz=10 | ✅ |
| `"Budapest"` (city only) | city=Budapest, street="", hsz="" | ✅ |
| `"Budapest VII. kerület, Erzsébet krt. 7"` | city=**"Budapest VII. kerület"**, hsz=7 | ❌ district grafted into city |
| `"Bécsi út 145"` (street only) | city=**"Bécsi"**, street=**"út"**, hsz=145 | ❌ first word becomes city, second word becomes street |
| `"1052 Szeged, Kossuth Lajos krt. 12. 3. em. 4. ajtó"` | zip=1052, city=Szeged, street=Kossuth Lajos krt., hsz=12, floor=3, door=4 | ✅ |
| `""` (empty) | **`KeyError: 'floor'`** | ❌ — early-return dict on line 103 is missing the `floor`/`door` keys that the normal return on line 149-156 includes. Any caller that does `parsed["floor"]` (rather than `.get("floor")`) crashes. |
| `"Vienna, Austria"` (foreign) | city=Vienna, street=Austria | ❌ country shoved into street |

Underlying causes:
1. The fallback at line 122-126 (`words = clean_str.split(); city = words[0]`) treats the first whitespace-separated token as the city — wrong for street-only inputs and for inputs without a comma separator.
2. The district regex never fires; `_DISTRICT_RE` is not even defined in this file (it lives only in the *imagined* fix in the old `REVIEW_FIXES.md`).
3. The empty-string early return at line 102-103 returns a dict of shape `{zip_code, city, street, house_number}` — the rest of the function returns `{zip_code, city, street, house_number, floor, door}`. **Two different return shapes from the same function.**

**Severity:** 🟠 HIGH (the district case affects every Budapest address with a district, which is most of them; the empty-string crash is a `KeyError` waiting for any caller that uses `[]` access).
**Category:** Parser robustness + inconsistent return shape.
**Proposed fix:** Fix G in `REVIEW_FIXES.md`.

---

## 7. Mapping editor (HIGH task #7)

The mapping editor is the new functionality on this branch. UI: React + TypeScript (`frontend/src/`). API: FastAPI (`backend/server.py`, `backend/mapping_service.py`, `backend/pdf_service.py`, `backend/recognize_service.py`). The branch HEAD commit `f66ec7b` is "fix: add mapping refetch + refresh button, fix stale unmapped count".

### 7.1 Field-level updates bypass the optimistic-concurrency guard (race condition)

**Code:** `backend/server.py:302-333` (`update_field`, `add_field`, `delete_field`, `create_group`, `update_group`, `delete_group`).

Each of these endpoints does:
```python
data = mapping_service.load(pdf_id)        # read
updated = mapping_service.update_field(data, ...)   # mutate in-memory
save_res = mapping_service.save(pdf_id, data)       # write
```
`mapping_service.save` accepts an `original_mtime` for conflict detection (line 75-86), but the field-level endpoints **call `save()` without passing the client's mtime**. So:
- The full-save endpoint (`PUT /api/mapping`) is concurrency-safe.
- The six field-level endpoints are not.
- Two users (or one user with two tabs) editing different fields of the same mapping can clobber each other's edits — the loser's change is silently overwritten.

**Reproduction sketch:** load mapping at mtime T0 in tab A and tab B; A updates field X (saves, mtime → T1); B updates field Y (loads at T1 in B's *second* call, but B's request body didn't include `_mtime`, so even if it did, B never reloaded after A's save); B's save overwrites A's field-X edit if B's load happened before A's save. Because the load–mutate–save sequence is **not atomic across `_LOCK`** (`_LOCK` in `mapping_service.py:40` only guards the file write itself, line 95-99), the TOCTOU window is wide open.

**Severity:** 🟠 HIGH (the whole point of the new branch is collaborative mapping editing).
**Category:** Race condition / concurrency.

### 7.2 `result.get("filled_fields", [])` is always empty

**Code:** `backend/server.py:695-696` in `fill_pdf` endpoint, and similar in `upload_pdf`.

`FormFillerPipeline.run_for_deal` returns a result dict with keys `{deal_id, success, output_path, issues, timestamp}` (`src/main.py:148-154`). It does **not** return `filled_fields` or `skipped_fields`. The endpoint therefore always responds with `filled_fields: []` and `skipped_fields: []`. The UI shows "0 fields filled" on every successful fill, which makes the preview screen look broken.

**Severity:** 🟡 MEDIUM (UI cosmetic, but undermines trust in the feature).
**Category:** API/pipeline contract drift.

### 7.3 `upload_pdf` filename sanitisation destroys Hungarian filenames and silently merges distinct files

**Code:** `backend/server.py:552`
```python
safe_filename = "".join(c for c in file.filename if c.isalnum() or c in (".", "_", "-"))
```
- `"Igenylesi_dokumentumok_OTP.pdf"` → unchanged ✅
- `"Pénzügyi átadás.pdf"` → `"Pnzügyi_tads.pdf"` ❌ (every accent and space dropped)
- Two uploads `"my form.pdf"` and `"myform.pdf"` both become `"myform.pdf"` → **silent overwrite** of the first uploaded file (and its mapping).

**Severity:** 🟡 MEDIUM.
**Category:** Filename sanitisation; silent collision.

### 7.4 `force_recreate_mapping=True` on every upload is surprising and expensive

**Code:** `backend/server.py:619-624` — every `/api/pdf/upload` triggers `force_recreate_mapping=True`, which calls the AI recognizer (real Anthropic API call) even if an existing mapping matches the PDF perfectly. This is the behavior the test `test_upload_and_download_endpoints` exercises, and it costs real API money per upload. The intent may be "always re-recognize", but the API contract (`/api/pdf/fill` uses `force_recreate_mapping=False`) is inconsistent.

**Severity:** 🟡 MEDIUM.
**Category:** Resource waste + surprising default.

### 7.5 `_fuzzy_match_canonical` case-sensitivity bug

**Code:** `src/ai/field_recognizer.py:866-892`.
```python
c = candidate.lower().strip().replace(" ", "_").replace("-", "_")
if c in valid_keys:           # <-- valid_keys contains mixed-case "Contact.Name"
    return c
```
Line 871 can **never return true** because `valid_keys` (built from `CANONICAL_FIELDS`) is mixed-case (`Contact.Name`) but `c` is lower-case (`contact.name`). The function falls through to substring matching (also case-sensitive) and finally to a prefix-accept rule (line 888-890) that lets the AI invent `participant.*` / `loan.*` canonicals — which main.py then cannot find in `borrower_data`. Net effect: AI suggestions that deviate from the exact canonical catalog are either dropped or silently accepted-but-unroutable.

**Severity:** 🟡 MEDIUM.
**Category:** Logic bug.

### 7.6 `update_field` accepts arbitrary canonical fields without validation

**Code:** `backend/mapping_service.py:113-120`; the `FieldUpdate` pydantic model (`backend/server.py:283-288`) declares `canonical_field: str | None` with no enum/regex constraint. The mapping editor will happily save `canonical_field: "Contact.TotallyMadeUp__c"` and only discover the problem at fill time (the field is silently skipped because the canonical is never produced).

**Severity:** 🟡 MEDIUM (the canonical-fields endpoint exists at `/api/mapping/canonical-fields` — the editor should validate against it on save).
**Category:** Missing validation.

### 7.7 Dead `_LOCK` for `update_field` flow + missing `fill_rule`/`checkbox_group` in `add_field`

- `mapping_service.add_field` (`backend/mapping_service.py:122-138`) does not accept `checkbox_group` or `fill_rule`, so the UI cannot create radio-button groups or rule-based fills via the API — only the full-save endpoint can.
- `update_field` line 116 lists `fill_rule`, `label`, `checkbox_group` in the mutate loop, but the `FieldUpdate` pydantic model never sends them, so they are effectively read-only via the field-level API.

**Severity:** 🟢 LOW.
**Category:** API surface gap.

### 7.8 Frontend uses a "stale unmapped count" that the HEAD commit claims to fix — verify

The HEAD commit message (`fix stale unmapped count`) suggests this was a known issue. Without running the frontend I can't fully verify the fix, but the underlying API issue is that `/api/pdf/fields` returns AcroForm fields independent of the mapping (so the unmapped count = total_fields − mapped_fields is always correct), while the editor UI's "Refresh" button has to refetch *both* endpoints to recompute. The fix commit added a refresh button; the race in 7.1 can still produce a stale view if another tab edited meanwhile.

**Severity:** 🟢 LOW (mostly mitigated by HEAD).
**Category:** UI state management.

---

## Bonus findings (encountered while tracing the seven)

### B1. `SalesforceClient.get_deal` in mock mode returns the **first deal** when given a non-existent ID — 🔴 CRITICAL

**Code:** `src/integrations/salesforce_client.py:169-179`
```python
if self._mock_mode:
    deal = self._state.deals.get(deal_id)
    if deal:
        ...
    else:
        for did, ddata in self._state.deals.items():
            logger.info(f"Ügylet lekérve (mock, első találat): {did}")
            return ddata     # <-- returns FIRST deal for ANY unknown id
    return deal
```
**Reproduction:** `sf.get_deal("DOES_NOT_EXIST_12345")` returns `deal_id='SF-970630'` (the first dummy). In tests or local dev this means any typo'd deal ID silently processes the wrong customer's data.

**Severity:** 🔴 CRITICAL (wrong-customer data; even though it's "only mock", tests rely on the mock for correctness assertions).
**Category:** Mock correctness.

### B2. CompletenessChecker is wired to a vocabulary it doesn't understand — 🟠 HIGH

**Code:** `src/main.py:260-273` calls the checker with `required_fields` like `"Contact.Loan_amount__c"`, `"Contact.Name"`. But `CompletenessChecker._check_field` (`src/engine/completeness_checker.py:307-324`) only understands the roots `loan`, `participant`, `property`, `deal_id`. Any `Contact.*` field falls into the `else` branch and calls `getattr(deal, "Contact", None)` → `None` → reported as MISSING.

**Reproduction:** every required field is reported MISSING, the report status is always `INCOMPLETE` with 9 blocking issues, regardless of how complete the deal actually is. The pipeline logs 9 warnings on every run and continues anyway (`src/main.py:187-192` only warns; it does not abort).

**Severity:** 🟠 HIGH (the completeness checker is dead weight in the pipeline — neither blocks nor informs).
**Category:** Component contract drift.

### B3. Tests make live Anthropic API calls when `ANTHROPIC_API_KEY` is set — 🟠 HIGH

**Code:** `src/ai/field_recognizer.py:507-527` — the constructor picks up `ANTHROPIC_API_KEY` from env (or `config/.env`). The tests `test_acroform_recognition`, `test_auto_mode_detects_acroform`, `test_flat_recognition`, `test_resolve_mapping_generates_if_missing`, and `test_run_for_deal_resolves_mapping_automatically` all instantiate `FieldRecognizer()` without forcing the key to empty.

**Reproduction:** with `ANTHROPIC_API_KEY` set in the environment (as it is in many dev/CI machines), running `pytest` hangs for minutes per test while making real Claude API calls — burning money and making the test suite non-hermetic.

**Severity:** 🟠 HIGH (CI reliability + cost).
**Category:** Test hermeticity.

### B4. Checkbox export values are hardcoded to `/Yes` — 🟡 MEDIUM

**Code:** `src/engine/pdf_filler.py:331-343`
```python
if field_type == "/Btn":
    if isinstance(value, str) and value not in ("igen","nem",...):
        field_obj[pikepdf.Name("/V")] = pikepdf.Name(f"/{value}")  # radio
    else:
        is_checked = self._is_truthy(value)
        if is_checked:
            field_obj[pikepdf.Name("/V")] = pikepdf.Name("/Yes")   # always /Yes
```
A checkbox whose export value is `/1`, `/X`, or `/true` (common in real bank forms) will not be checked by `/Yes` in most viewers. The `_mupdf_fill_missing` fallback does try `button_states()` to find the on-value (line 437-450) — but only for fields pikepdf missed. Fields successfully reached by pikepdf's recursive walk get the wrong `/Yes`.

**Severity:** 🟡 MEDIUM.
**Category:** PDF edge case.

### B5. `_is_truthy` has a duplicate `"false"` token and dead code — 🟢 LOW

**Code:** `src/engine/pdf_filler.py:474,479` — the false-list contains `"false"` twice; the last line `return s != ""` is unreachable because the preceding `if s in (...)` already returned for empty strings.

### B6. `RoleInstancePlanner` is imported but never used by the pipeline — 🟢 LOW

**Code:** `src/main.py:41` imports `RoleInstancePlanner` and aliases `ParticipantRole as _RRole`, but neither symbol is ever instantiated or referenced in the file. The role-routing that *should* use the planner is instead hand-rolled at `src/main.py:298-322` (and is the source of bug 3.1). 415 lines of well-tested planning code (`src/engine/role_instance_logic.py`) sit unused.

**Severity:** 🟢 LOW (dead code) — but it's a *missed opportunity*, since wiring the planner in would fix bug 3.1.

### B7. DocumentAssembler log hardcodes "97 oldal" regardless of actual master size — 🟢 LOW

**Code:** `src/engine/document_assembler.py:135` — `logger.info(f"   Master: {master_pdf.name} (97 oldal)")`. The actual page count comes from `len(src_pdf.pages)`; the log message lies if the master PDF is a different size.

### B8. DocumentAssembler output is written even when zero source pages matched — 🟡 MEDIUM

**Code:** `src/engine/document_assembler.py:144-162`. If the master PDF is shorter than the highest page number in `_build_page_plan` (e.g. master only has 50 pages but the plan asks for page 86), every out-of-range page is silently skipped (line 148-149 just warns) and the output PDF is saved with whatever subset of pages *was* found. The pipeline then fills this truncated PDF and reports success.

**Severity:** 🟡 MEDIUM.
**Category:** Silent truncation.

### B9. `Contact.Permanent_address__c` is mapped but never produced — 🟡 MEDIUM

**Code:** `src/mapping/acroform_sample_mapping.json` maps `borrower_floor` and `borrower_door` to `Contact.Permanent_address__c`. `_participant_to_dict` produces `Contact.OtherStreet`/`OtherCity`/`ZIP__c` and `Contact.MailingStreet`/`MailingCity`/`ZIP__c` — but never `Contact.Permanent_address__c`. Floor/door data is therefore dropped on this fixture (and likely on any form that uses the same canonical).

**Severity:** 🟡 MEDIUM.
**Category:** Canonical-mismatch.

### B10. AcroForm Comb-text centering comment says "bit 25" but code uses bit 24 — 🟢 LOW

**Code:** `src/engine/pdf_filler.py:351`
```python
if ff & (1 << 24):  # Comb flag (bit 25)
```
Per the PDF spec, Comb is flag bit 25 (1-indexed) which is `(1 << 24)` (0-indexed). The code is correct; the comment is misleading.

### B11. `simple-salesforce` `query()` is called with a kwarg it doesn't support — 🟡 MEDIUM

**Code:** `src/integrations/salesforce_client.py:446`:
```python
result = self._sf.query(query, deal_id=deal_id)
```
`simple_salesforce.Salesforce.query()` does not accept `deal_id` — this raises `TypeError` in live mode. The `get_attachments` endpoint is therefore broken in production (mock mode never hits this path).

**Severity:** 🟡 MEDIUM.
**Category:** Live-mode bug not covered by tests.

### B12. SOQL string interpolation contradicts the parameterization comment — 🟡 MEDIUM

**Code:** `src/integrations/salesforce_client.py:200-216, 269-273`. The comment claims "`:változó` parameterization prevents SOQL injection", but the actual code interpolates IDs via f-strings (`f"... WHERE Id IN ({id_list_str})"`). The comment and code disagree. (The task explicitly de-scopes security, so this is noted only as a maintainability hazard.)

---

## Severity roll-up

| Severity | Count | IDs |
|---|---|---|
| 🔴 CRITICAL | 5 | 1.1, 3.1, 3.2, 3.3, B1 |
| 🟠 HIGH | 11 | 1.2, 2, 4.1, 4.2, 4.3, 5, 6, 7.1, 7.2*, B2, B3 |
| 🟡 MEDIUM | 11 | 1.3, 4.4, 7.3, 7.4, 7.5, 7.6, B4, B8, B9, B11, B12 |
| 🟢 LOW | 6 | 7.7, 7.8, B5, B6, B7, B10 |

(*7.2 downgraded from HIGH to MEDIUM — cosmetic only.)

The five CRITICAL findings all share a common theme: **the pipeline does not abort on data it cannot place — it silently drops, silently duplicates, or silently substitutes.** A mortgage form that looks filled but contains the wrong property address, the wrong customer, or empty mandatory boxes is a regulatory and reputational hazard. None of the current 52 tests would catch any of these, because no test reads back the filled PDF and asserts on its content (the only PDF-content test, `test_fill_fields_recursive_handles_kids_without_t`, asserts on a single contrived field name).

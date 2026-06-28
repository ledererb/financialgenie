# OpenCode Task: Fix PDF Fill Issues

## Background

We have a FinancialGenie pipeline that fills OTP bank forms. The main AcroForm PDF (`OTP_Igenylesi_Dokumentumok_v5.pdf`) has 2203 form widgets. The mapping (`OTP_Igenylesi_Dokumentumok_v5_mapping.json`) has 1729 entries, but only 89 have a `canonical_field`. The pipeline fills only 6 fields out of 2203 in the filled output, and those have obvious errors.

## Root cause analysis

The core issue is in `src/main.py`'s `_prepare_field_data()`. When I trace the data flow:

1. `_prepare_field_data()` produces 47 entries (all correct data)
2. These 47 pdf_field_names → canonical values are converted to `canonical_field_data` with 39 entries  
3. The filler uses these 39 canonical values, but only finds matches for fields in the mapping that reference those 39 canonical names

**Key problems:**

### Problem 1: Date splitting
`_participant_to_dict()` produces `participant.birth_date = "1978.12.28"`. The mapping has:
- `SZA_IG_szül_év` → `participant.birth_date` (should get "1978")
- `SZA_IG_szül_hónap` → `participant.birth_date` (should get "12")  
- `SZA_IG_szül_nap` → `participant.birth_date` (should get "28")

All three get "1978.12.28" because the code doesn't split the date.

Same for co-borrower (`-társ` suffix) and `residence_since` (which maps to year/month).

### Problem 2: Checkbox fields get text values
- `participant.citizenship = "magyar"` → placed in a checkbox field (type=number/checkbox)
- `participant.gender = ""` → placed in checkbox but empty → nothing
- `participant.marital_status = ""` → empty
- `participant.education = ""` → empty
- `participant.employment_type = ""` → empty
- `participant.id_document_type = ""` → empty

Checkboxes need "Yes" / "Off" values, and we need to infer which checkbox option to check from available data.

### Problem 3: Income fields unmapped
98 income fields like `SZA_IG_jövedelem1`, `SZA_IG_jövedelem_összeg1` all have `canonical_field: None` in the mapping. Even though `participant.monthly_income = 1158000` is available.

The income fields in the PDF are digit boxes (comb fields) where each digit box gets one character. The full formatted income "1 158 000" needs to be split character-by-character into individual digit box fields.

### Problem 4: Unpopulated canonical fields
14 canonical fields referenced by the mapping have no data source:
- `loan.afa_support`, `loan.csok_amount`, `loan.down_payment` (loan data exists in raw_data)
- `loan.housing_savings`, `loan.product_type`, `loan.purchase_price`
- `participant.mailing_address.city/street/zip_code` (mailing_address might be null)
- `property.contact_name`, `property.contact_phone`, `property.rental_fee`, `property.rental_fee_eur`, `property.usage_type`

## Required Fixes

### Fix 1: Date splitting in `_prepare_field_data()`

In `src/main.py`, in the `_prepare_field_data()` method, after building the standard field_data, add a **date splitting pass**.

Logic: For each mapping entry where the pdf_field_name contains `év`/`hónap`/`nap` (or English equivalents `year`/`month`/`day`) and the canonical field is a date:
- Parse the date from the canonical data source
- For `év`/`year`: place just the year portion
- For `hónap`/`month`: place just the month portion  
- For `nap`/`day`: place just the day portion

Also handle `résidence_since` (mióta_lakik_év, mióta_lakik_hónap).

### Fix 2: Checkbox canonical values

Add more canonical values to `_participant_to_dict()`:
- `participant.gender` → infer from name or personal_id prefix? For now, keep empty but the checkbox handler should leave it unchecked
- `participant.citizenship` → already "magyar" but needs to work as checkbox "Yes"/"Off"
- `participant.marital_status` → unknown
- `participant.education` → unknown  
- `participant.employment_type` → infer from employer existence? If employer exists → "alkalmazott"
- `participant.id_document_type` → unknown

Also add checkbox-specific logic: when a field_type is "checkbox" (or type=2 in PDF) and the value is "magyar"/"Magyar"/"hungarian": set "Yes"/"Off" on the checkbox.

### Fix 3: Income field mapping

Add income field handling:
- Detect fields named `jövedelem_összeg` or containing `jövedelem` + `összeg` or `__összeg` 
- Map them to `participant.monthly_income`
- Handle digit box fields: if the PDF field type has a character limit of 1 (comb field), split the income value character by character
- Handle `deviza` (currency) fields → "HUF" or "Ft"
- Handle `érk` (arrival) checkbox fields → income type (bank account, cash, etc.)

### Fix 4: Extended canonical data

In `_prepare_field_data()`, add support for:
- `loan.*` fields: read from `raw_data["loan"]` via `deal` object. The raw data has `loan_amount`, `loan_purpose`, `product_name`, `down_payment`, etc.
- `property.*` fields: iterate properties
- `participant.mailing_address.*`: fallback to permanent address if no separate mailing address
- `participant.employer_tax_id`, `participant.business_name`, etc.

### Fix 5: Address field handling

Currently `_address_to_dict()` produces:
- `participant.address.street` = "Rákóczi út 104" (street + house_number combined)

The PDF has separate fields for street and house number. The mapping might reference either combined or separate. Handle gracefully: if street contains a numeric suffix, try to detect house number splitting.

## Files to modify

**Primary:**
- `src/main.py` — `_prepare_field_data()`, `_participant_to_dict()`, `_address_to_dict()`
- `src/engine/pdf_filler.py` — AcroFormFiller checkbox handling for text→Yes/Off conversion

**Mapping (may need regeneration):**
- `src/mapping/OTP_Igenylesi_Dokumentumok_v5_mapping.json` — extend canonical mappings for income fields and checkboxes

## Validation

After fixes, run:
```
python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN
```

Check:
1. Filled PDF should show Mészáros Ferenc' name in SZA_IG_név field
2. Birth date should show "1978" in év, "12" in hónap, "28" in nap fields (not "1978.12.28")
3. Checkboxes for citizenship should be checked (magyar)
4. Income should appear (even partially) 
5. Check overall field count: at least 50+ fields should be filled (up from 6)

## Approach Principles

1. **Backward compatible**: Don't break existing tests (47 passing tests)
2. **Deterministic**: No AI in the fill path, only in field recognition/classification
3. **Path-based**: Fix by modifying `_prepare_field_data()` to handle special cases, not by regenerating all mappings
4. **Testable**: Each fix should produce measurable improvement in filled field count

# OpenCode Task: Fix Mapping & Verify Documents

## Background

FinancialGenie pipeline fills OTP "Előzetes értékbecslés" PDF. The mapping has 1029 fields, 248 mapped, 781 unmapped. After the character-distribution fix (commit 379f227), income and phone digit boxes work correctly (verified by analysis).

**But key mappings are wrong**, causing visible errors Balázs screenshotted. The pipeline needs the mapping fixed so documents are correct without manual review.

## What's Wrong

### Issue 1: TF 357-362 — Digit boxes mapped as address fields (CRITICAL)

Page 3, y=247.5. Six narrow (15.1pt) digit boxes. Current mapping:
```
Text Field 357 → participant.address.city         (WRONG — gets "Budapest")
Text Field 358 → participant.address.street       (WRONG — gets "Alkotás utca 12")  
Text Field 359 → participant.address.house_number  (WRONG — gets "12")
Text Field 360 → participant.address.zip_code      (WRONG — gets "1123")
Text Field 361 → participant.address.full_address  (WRONG — gets "1123, Budapest...")
Text Field 362 → participant.education             (WRONG — gets "felsőfokú")
```

These labels contain keywords: "Mióta lakik", "év", "hónap", "jelenlegi tartózkodási". 
**These are digit boxes for residence years and months**, NOT address fields.

**Fix**: Change canonical_field for TF 357-362 to `participant.residence_since`.
- The character-distribution logic (`_distribute_char_fields()` in main.py) already handles digit boxes
- `residence_since` is a date (e.g., `2020-03-15`). Need to extract year digits (4) and month digits (2) across 6 boxes
- **But**: The character-distribution logic needs to handle this. Currently it does:
  - Groups by (canonical, y_row) and narrow width
  - Explodes the value string char-by-char  
  - For dates: extracts only digits
- The 6 boxes at y=247.5 need the YEAR (4 digits) placed in TF 357-360, MONTH (2 digits) in TF 361-362
- But with current logic, all 6 get the SAME value character by character, which won't work
- **Solution**: Remove canonical_field from 5 of 6, set TF 357-360 → participant.residence_since (year), TF 361-362 → participant.residence_since (month). OR better: use different canonical fields for year and month parts.

**Simpler approach**: 
- Set canonical_field to None for all 6 (so they don't get wrong data)
- Add explicit logic in `_prepare_field_data()`: if a field name contains "év" or "year" in its label and residence_since is set → fill with year digits. Same for "hónap"/"month".
- OR: map them to new pseudo-canonicals like `participant.residence_since_year` and handle in field_data prep

**Best approach**: Since the labels are garbage (OCR mixed keywords), use COORDINATE-based detection: any 15pt-wide digit box at y=247.5 on page 3 that shares canonical participant.residence_since → distribute year (first 4 chars of YYYYMMDD) into first 4 boxes, month (next 2) into last 2.

### Issue 2: No email field mapping

The participant model has `email: Optional[str]` and main.py line 773 generates:
```python
"participant.email": p.email or "",
```

But NO text field in the mapping references `participant.email`. The email entry field exists in the PDF's overlay (not as an AcroForm field), so this can't be filled via AcroForm. This is a non-issue for the current pipeline — the "phone in email field" from the old screenshot was caused by the old bug (identity mapping collapse), now fixed.

### Issue 3: Unmapped data fields

781 fields are unmapped. Key ones with available data:

| PDF Field | Label | Y | Width | Available Data |
|-----------|-------|---|-------|----------------|
| Text Field 730 | Egyéb foglalkozás | 91.2 | 215.5 | participant.employment_type |
| Text Field 731 | Egyéb munkaviszony típus | 304.8 | 215.5 | participant.employment_type |
| Text Field 732 | Egyéb munkáltató cégforma | 468.5 | 222.9 | participant.employer |
| Text Field 5012 | Nyilatkozat dátuma | 695.9 | 167.2 | legal.signature_date |
| Text Field 5016 | Nyilatkozat dátuma | 695.9 | 167.2 | legal.signature_date |
| Text Field 1249 | Dokumentumok átvételének dátuma | 149.2 | 254.6 | legal.signature_date |

Plus page 16-17 legal checkboxes (Check Box 195-209) — some get filled by legal classifier, others are Off.

### Issue 4: Unmapped text digit boxes (page 4)

Page 4 has dozens of unmapped digit boxes for:
- "Más banknál lévő hitelek száma" (TF 365-366)
- "Nem OTP Banknak fizetett havi törlesztőrészletek" (TF 367-375, rest on page 7)
- "Hazai vagy uniós költségvetési forrású visszatérítendő támogatás" etc.

These need data from Salesforce (existing loans/payments) that's not in the mock data. These should be skipped unless we have the data.

### Issue 5: Filled field count vs expectation

Current run: 183 fields filled (up from 12). But 795 unmapped. Many are legitimate (no data in mock) but many could be mapped.

## Required Changes

### 1. Fix TF 357-362 in mapping JSON

In `src/mapping/elozetes_ertekbecsles_mapping.json`:

1. Find Text Field 357-362 entries
2. Set `canonical_field` to `participant.residence_since` for ALL 6
3. The character-distribution logic handles the rest (groups by y_row, explodes value)

**But**: The character-distribution logic needs an update too. When `residence_since` is a date (e.g., "2020-03-15") and the target is 6 digit boxes:
- Extract only year+month digits: "202003"
- 4 year digits go to first 4 boxes, 2 month digits to last 2
- Add this logic to `_distribute_char_fields()` in `main.py`

### 2. Add remaining text field mappings

In the mapping JSON, add canonical_field for:
- Text Field 730 → `participant.employment_type`
- Text Field 731 → `participant.employment_type` 
- Text Field 732 → `participant.employer` (already has TF 363, 364)
- Text Field 5012 → `legal.signature_date`
- Text Field 5016 → `legal.signature_date`

Also update main.py's `_prepare_field_data` to ensure employment_type and signature_date are populated in the field_data dict.

### 3. Verify and re-run

After changes:
```bash
python3 -m pytest tests/ -v  # Check tests still pass
python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN
```

### 4. Upload to Drive + Salesforce

Upload all 7 filled PDFs to Drive folder "Kitöltött nyomtatványok" (ID: `1tvaLZ3EWfr0AEMv40ufMj9ei80DmwMdb`).

Use the existing Drive upload script pattern:
```python
# Upload with direct Google API
UPLOAD_SCRIPT = '/tmp/drive_upload_v2.py'
```

The script already exists at `/tmp/drive_upload_v2.py` — copy the logic and run it for all output PDFs.

## Files to Modify

### Primary:
- `src/mapping/elozetes_ertekbecsles_mapping.json` — fix canonical_field for TF 357-362 + add new mappings
- `src/main.py` — update `_distribute_char_fields()` for residence_since date splitting + add missing canonical data sources

### Secondary:
None — the legal classifier already handles consent checkboxes

## Mapping JSON Format

Each field entry looks like:
```json
{
  "pdf_field_name": "Text Field 357",
  "canonical_field": null, 
  "coordinates": {"x": 26.3, "y": 247.5, "width": 15.1, "height": 19.3},
  "page_number": 3,
  "label": "Mióta lakik Mi az a Tulajdonos...",
  "field_type": "text",
  "canonical_value": null
}
```

## Pipeline Key Functions in main.py

- `_prepare_field_data(deal, mapping)` — line ~760 — builds field_data dict
- `_distribute_char_fields()` — ~line 800 — character-level box distribution  
- `_fill_pdf(output_path, field_data, mapping, mapping_path)` — ~line 910 — fills PDF

## Validation Criteria

After fix:
1. `python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN` returns 0
2. 7/7 PDFs in output/ have no errors
3. The ertekbecsles PDF has >185 fields filled (183 current + ~6 from residence fix + ~5 from new mappings = ~194)
4. TF 357 should get "2" (first digit of year), TF 358 "0", TF 359 "2", TF 360 "0" (4 year digits), TF 361 "0" (first month digit), TF 362 "3" (second month digit) — example with residence_since=2020-03-15
5. All tests pass
6. All 7 PDFs uploaded to Drive

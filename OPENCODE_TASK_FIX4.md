# OpenCode Task: Fix Mapping Issues & Digit Box Overflow

## Background

FinancialGenie pipeline fills OTP "Előzetes értékbecslés" PDF. After character-distribution fix (commit 379f227), income/phone digit boxes work correctly, but NEW bugs were introduced. Balázs sent 4 screenshot showing visible errors.

## Root Cause Analysis

### Problem 1: TF 357-362 mapped to WRONG canonicals (RESIDENCE TIME = ADDRESS)
Current mapping (all at y=247.5, 15.1pt width digit boxes):
```
TF 357 → participant.address.city          gets "Budapest" ✅ (wrong data)
TF 358 → participant.address.street        gets "Alkotás utca 12" ✅ (wrong data)
TF 359 → participant.address.house_number  gets "12" ✅ (wrong data)
TF 360 → participant.address.zip_code      gets "1123" ✅ (wrong data)
TF 361 → participant.address.full_address  gets full address ✅ (wrong data)
TF 362 → participant.education             gets empty (no data)
```

These labels contain "Mióta lakik", "év", "hónap" — these are **residence years/months** digit boxes, not address fields. The 6 narrow (15.1pt) boxes are on page 3, Section "Egyéb személyes adatok", below the housing tenure checkboxes and next to "Mióta lakik a jelenlegi tartózkodási helyén?" label.

**Fix**: Change ALL 6 canonical_field to `participant.residence_since`.

The character-distribution logic in `_distribute_char_fields()` already groups fields by (canonical, y_row) with width < 20pt. All 6 share residence_since → they get grouped → value "2020-03-15" (or whatever) → extracted digits "20200315" (8 chars) → distributed across 6 boxes:
- TF 357="2", TF 358="0", TF 359="2", TF 360="0" (year)
- TF 361="0", TF 362="3" (month)
- Remaining 2 chars ("15" / day) are lost because only 6 boxes

**But wait**: The character-distribution also processes OTHER residence_since fields. Check if any other fields have canonical=participant.residence_since. If so, they'd be grouped by y_row separately (each y_row is its own group).

### Problem 2: Single digit boxes with FULL multi-digit value (OVERFLOW)
Fields like TF 1179, TF 1221, TF 1237 (all 15.1pt width, canonical=loan.loan_amount) get the ENTIRE value "25 000 000" (10 chars with spaces) crammed into a single tiny box.

These are at unique y positions (148.9, 682.4, 224.8), so they're alone on their y_row. The character-distribution logic groups by (canonical, y_row) — a single field on a row gets NO distribution.

Same issue: TF 330 at y=562.3 (15.1pt, canonical=participant.address.full_address) gets "1123, Budapest, Alkotás utca 12..." — full address in a digit-sized box.

**Fix**: In `_distribute_char_fields()`, after standard distribution, do a second pass:
- Find all field_data entries where width < 20pt AND value length > 5
- Check if this field is the ONLY one with this canonical on this approximate y_row
- If so: skip it — set its value to empty string "" (better empty than overflowing garbage)

### Problem 3: Address lines in digit boxes (TF 334, 335, 340, 341)
These are 15.1pt wide text boxes at y=681.4 and y=757.7. They contain:
- TF 334="Budapest" (15.1pt — barely fits, 8 chars)
- TF 335="Alkotás utca 12" (15.1pt — OVERFLOWS, 18 chars)
- Same for TF 340, 341

These are single fields on their y_rows with valid address data in tiny boxes. The data is CORRECT but the box is too small for "Alkotás utca 12".

**Fix**: Apply the same overflow protection — if width < 20pt and value > ~8 chars for a SINGLE field on its row, skip it (empty string).

### Problem 4: Residence time character distribution logic update
The character-distribution logic in `_distribute_char_fields()` currently:
1. Groups fields by (canonical, y_row)
2. For each group with >1 field and width < 20pt:
   - Extracts digits from value (numeric/date canonicals)
   - Distributes char-by-char to each field

BUT: The y_row matching uses exact float comparison. Fields at y=247.5 (TF 357-362) should group together. And birth_date fields at y=260.0 (TF 307-314) are separate.

The current grouping uses `abs(y - row_y) < 5` or similar? Let me check the actual implementation.

## Required Code Changes

### File 1: `src/mapping/elozetes_ertekbecsles_mapping.json`

Change TF 357-362 canonical fields:

```json
// BEFORE:
{"pdf_field_name": "Text Field 357", "canonical_field": "participant.address.city", ...}
{"pdf_field_name": "Text Field 358", "canonical_field": "participant.address.street", ...}
{"pdf_field_name": "Text Field 359", "canonical_field": "participant.address.house_number", ...}
{"pdf_field_name": "Text Field 360", "canonical_field": "participant.address.zip_code", ...}
{"pdf_field_name": "Text Field 361", "canonical_field": "participant.address.full_address", ...}
{"pdf_field_name": "Text Field 362", "canonical_field": "participant.education", ...}

// AFTER:
{"pdf_field_name": "Text Field 357", "canonical_field": "participant.residence_since", ...}
{"pdf_field_name": "Text Field 358", "canonical_field": "participant.residence_since", ...}
{"pdf_field_name": "Text Field 359", "canonical_field": "participant.residence_since", ...}
{"pdf_field_name": "Text Field 360", "canonical_field": "participant.residence_since", ...}
{"pdf_field_name": "Text Field 361", "canonical_field": "participant.residence_since", ...}
{"pdf_field_name": "Text Field 362", "canonical_field": "participant.residence_since", ...}
```

### File 2: `src/main.py` — `_distribute_char_fields()` method

Add these changes:

#### Change A: Handle spaces in numeric values during digit extraction
When extracting digits for numeric/date canonicals, first remove ALL spaces from the value before extracting digits. "25 000 000" → remove spaces → "25000000" → digits only → "25000000" (same, no change needed for this case, but ensure it works).

#### Change B: Overflow protection for single-field groups
After the main character-distribution loop, add a second pass over ALL field_data entries:
```python
# Overflow protection: skip single-field entries with tiny boxes
for key, entry in field_data.items():
    w = entry.get('width', 100)
    val = entry.get('value', '')
    if w < 20 and len(str(val)) > 5 and key not in distributed_keys:
        # This is a single field on its row with overflow — skip it
        field_data[key] = ''
```

Where `distributed_keys` tracks which fields were already handled by the main distribution logic.

### File 3: `src/main.py` — `_prepare_field_data()` method

Ensure `participant.residence_since` is populated in the field_data dict. Check:
```python
"participant.residence_since": str(p.residence_since) if p.residence_since else "",
```

Look in the `_participant_to_dict()` or `_prepare_field_data()` method and verify residence_since is included.

## Verification

### Step 1: Check if residence_since is in field_data
```bash
grep -n "residence_since" src/main.py
```

### Step 2: Run tests
```bash
python3 -m pytest tests/ -v
```

### Step 3: Run pipeline
```bash
python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN
```

### Step 4: Verify filled fields
```python
# Check:
# - TF 357-362 should have single digits (not address text)
# - TF 1179, 1221, 1237 should be EMPTY (not overflowing)
# - TF 330, 334, 335, 340, 341 should be EMPTY (not overflowing)
```

### Step 5: Upload to Drive
Upload all 7 filled PDFs to Drive folder `1tvaLZ3EWfr0AEMv40ufMj9ei80DmwMdb`.

Use the existing upload script pattern at `/tmp/drive_upload_v2.py`.

## Files to Modify

1. `src/mapping/elozetes_ertekbecsles_mapping.json` — TF 357-362 canonical change
2. `src/main.py` — `_distribute_char_fields()` — overflow protection + digit extraction fix

## Mark Complete And Report

After finishing, write a summary to me in Slack channel "#coding-work":
1. What was fixed
2. Pipeline run result (errors? filled counts?)
3. Drive upload result
4. Any remaining issues

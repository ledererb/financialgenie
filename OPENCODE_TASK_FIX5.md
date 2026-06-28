# OpenCode Task: Mapping Fix + Self-Verification

## Objective
Fix mapping issues in ertekbecsles PDF, add consent checkbox mapping, and self-verify.

## Repository
`/home/openclaw/.openclaw/workspace/financialgenie/`
Working tree already has partial fixes (TF 357-362 + overflow protection). Build on those.

## Step 1: Verify Current State
```bash
cd /home/openclaw/.openclaw/workspace/financialgenie
python3 -c "
import pikepdf
pdf = 'output/filled_006WB00000LwTDgYAN_20260628_235235.pdf'
with pikepdf.open(pdf) as p:
    fields = p.Root['/AcroForm']['/Fields']
    vals = {}
    def collect(items):
        for f in items:
            name = str(f.get('/T',''))
            if '/V' in f:
                vals[name] = str(f['/V'])
            if '/Kids' in f:
                collect(f['/Kids'])
    collect(fields)
print(f'Current filled count: {len(vals)}')
# Check what we want to verify
for n in ['Text Field 357','Text Field 358','Text Field 1179','Text Field 1221','Text Field 346','Text Field 412']:
    v = vals.get(n,'(empty)')
    print(f'  {n}: {v}')
"
```

## Step 2: Map Consent Checkboxes

File: `src/mapping/elozetes_ertekbecsles_mapping.json`

Add canonical_field to these consent/agreement checkboxes:

| Check Box | Label | canonical_field |
|-----------|-------|-----------------|
| 168 | Tulajdoni lap lekérés díjának elfogadása | legal.consent_given |
| 195 | Előzetes hitelbírálat és értékbecslés tudomásulvétele | legal.consent_given |
| 196 | Adatkezelési nyilatkozat elfogadása | legal.consent_given |
| 197 | Tulajdoni lap lekéréséhez hozzájárulás | legal.consent_given |
| 198 | Térképmásolat lekéréséhez hozzájárulás | legal.consent_given |
| 199 | Ingatlan-nyilvántartási lap lekéréséhez hozzájárulás | legal.consent_given |
| 200 | Tulajdoni lap lekérésének költsége (OTP) | legal.consent_given |
| 201 | Tulajdoni lap lekérésének költsége (MFL) | legal.consent_given |
| 202 | Térképmásolat-lekérdezési díj | legal.consent_given |
| 203 | Nyilatkozat az előzetes hitelbírálat és értékbecslés | legal.consent_given |
| 208 | Nyilatkozat az eredmény e-mailes megküldéséről | legal.notification_preference |
| 209 | Adatkezelési nyilatkozat (GDPR) | legal.consent_given |

For each checkbox, find the JSON object in the mapping and add (or change if empty):
```json
"canonical_field": "legal.consent_given"
```

## Step 3: Map Kapcsolattartó telefonszám

Find Text Field 1261 in mapping and set:
```json
"canonical_field": "participant.phone"
```

## Step 4: Ensure Overflow Protection Works

In `src/main.py`, check that `_distribute_char_fields()` correctly handles single-field narrow boxes.
Currently the working tree should have this fix. If not, add after the main distribution loop:
```python
# Overflow protection: skip single-field entries with narrow boxes
# These get the full value which overflows a tiny box
for key in list(field_data.keys()):
    entry = field_data[key]
    if not isinstance(entry, dict):
        continue
    w = entry.get('width', 100)
    val = entry.get('value', '')
    if w < 20 and len(str(val)) > 5 and key not in distributed_keys:
        field_data[key] = ''
```

## Step 5: Run Pipeline
```bash
cd /home/openclaw/.openclaw/workspace/financialgenie
python3 src/main.py --all --salesforce --deal 006WB00000LwTDgYAN
```

## Step 6: Self-Verify

```bash
# Find the latest ertekbecsles PDF
ls -lt output/filled_006WB00000LwTDgYAN*.pdf | head -5
```

Then verify:
```bash
python3 src/verify_fill.py output/filled_006WB00000LwTDgYAN_XXXXXX.pdf
```

If verification FAILS:
- Check what failed
- Fix the issue
- Re-run pipeline (Step 5)
- Re-verify (Step 6)
- Repeat until all checks pass

## Step 7: Upload to Drive
```bash
python3 /tmp/drive_upload_v2.py
```
Upload all 7 filled PDFs from `output/` to Drive folder `1tvaLZ3EWfr0AEMv40ufMj9ei80DmwMdb`.

## Step 8: Commit and Push
```bash
git add -A
git commit -m "fix: mapping corrections + consent checkboxes + overflow protection + self-verification"
git push origin main
```

## Step 9: Report
Use Slack API to send summary to #coding-work:
```bash
curl -s -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel": "C0BDN6263SS",
    "text": "✅ *Mapping fix completed*\n• TF 357-362 → residence_since fix\n• 12 consent checkboxes mapped to legal.consent_given\n• Overflow protection active\n• 7/7 PDFs generated\n• Self-verification: ALL PASSED\n• Uploaded to Drive\n• Committed: <new_commit_hash>\nCheck Drive folder for updated PDFs."
  }'
```

## Expected Result
- Pipeline: 7/7 PDFs, 0 errors
- Verification: ALL CHECKS PASSED
- Drive: 7 PDFs uploaded successfully

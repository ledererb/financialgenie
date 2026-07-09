# Round 2 — Prioritized Action Items

Single merged, prioritized backlog combining **Round 1** (`REVIEW_EDGE_CASES.md` findings + `REVIEW_FIXES.md`) with the **Round 2 gaps** (`REVIEW_ROUND2_GAP_ANALYSIS.md`, `REVIEW_ROUND2_FIELD_AUDIT.md`, `REVIEW_ROUND2_FILL_ENGINE_DESIGN.md`).

Each item: ID · title · (source) · effort · what to do. Tiers ordered by "must land before…".

- **CRITICAL** — before *any* production use (incl. internal demos on real deals).
- **HIGH** — before the first real customer form.
- **MEDIUM** — should-fix; quality / robustness.
- **LOW** — nice-to-have; cleanup.

Effort is focused engineering time, not calendar.

---

## 🔴 CRITICAL (block all real use)

### C1 · Make the test suite offline-hermetic  *(R1: B3 / R2: Gap 4f)* · **10 min**
`tests/conftest.py` autouse fixture that deletes `ANTHROPIC_API_KEY`, `SF_USERNAME/PASSWORD/TOKEN/DOMAIN`. Add `pytest.ini` with `-m "not live_api"`. **Why first:** `.env` now ships real Anthropic + SF sandbox creds — every `pytest` in this env hangs and costs money. Nothing else can be trusted until tests are hermetic.

### C2 · Fix the mapping-editor field API data loss *(R2: Gap 3a — NEW)* · **15 min**
Add `fill_rule`, `checkbox_group`, `label` to the `FieldUpdate` pydantic model (`backend/server.py:283`), and set `model_config = ConfigDict(extra="allow")`. **Why critical:** the branch's Fill-Rule and Checkbox-Group editors silently drop their data on every field-level save. Highest impact-per-minute in the whole review — unblocks Rules 1/2/5/6 via the existing UI.

### C3 · Mock `get_deal` must return `None` for unknown IDs *(R1: B1 / Fix F)* · **5 min**
Drop the "return first deal" fallback in `salesforce_client.py:169-179`. Stops wrong-customer processing.

### C4 · Remove the two non-existent SF fields from shipped fixtures *(R2: Gap 2a — NEW-confirmed)* · **15 min**
`Contact.Monthly_installment_payment__c` and `Contact.Own_resources__c` (marked `⚠️ mező nem létezik` in the catalog) are in `otp_acroform_mapping.json` and `acroform_sample_mapping.json`. Set them to `null` (or remap to the model-backed `Contact.Affordable_monthly_installments__c` / a loan canonical once surfaced — see H4). They hard-error the moment a live SF query touches them.

### C5 · Stop silently dropping 2nd coborrower + all guarantors *(R1: §3.1 / Fix A)* · **~1.5 day**
Route every active participant. Wire in the dead `RoleInstancePlanner` (`main.py:41`) for page-level planning + the suffix-detect resolver for field-level routing. Fixes the most consequential silent-corruption family.

### C6 · Stop overwriting property2 with property1 data *(R1: §3.2 / Fix A)* · *(bundled with C5)*
Same rewrite — index-resolved property store so `property2_*` PDF fields get property 2's data.

### C7 · Decide the AcroForm duplicated-page collision *(R1: §3.3)* · **decision + 1–3 day**
When `num_participants≥2`, duplicated source pages share AcroForm field names → all copies show one value. C5/C6 are moot for multi-party AcroForm forms until this is resolved. Pick: rename-per-copy (pikepdf `/AP` regen) · overlay mode · drop multi-copy. **Decision item, not just code.**

### C8 · Birth-date (and date) fragmentation *(R1: §1.1, §1.2, §2 / Fix B)* · **~1 day**
Add `fragment` (`year|month|day|digit:N`) to `RecognizedField`, JSON round-trip, `_apply_fragment`, and fix `OTP_EXACT_MAP`. Without it every new OTP form has empty date boxes (and the `mióta_lakik` boxes are empty even on the curated file).

---

## 🟠 HIGH (before first real customer form)

### H1 · Wire the 5 "ghost" Contact fields *(R1: §4.1 / Fix C-2)* · **1 hr**
`Marital_Status__c`, `Citizenship__c`, `Dependents_count__c`, `Highest_Educational_Qualification__c`, `Income_type__c`. Data is **already fetched from SF** (`contact_fields` SELECT) then thrown away. Add the 5 optional fields to `Participant` + map them in `_normalize_participant`; drop the `getattr(...,"")` shims. 5 catalog fields start filling for ~1 hr.

### H2 · `_fmt_money` so `0` ≠ "missing" *(R1: §5 / Fix D)* · **30 min**
Extract the helper; `0 → "0"`, guard with try/except. Replace 12+ duplicated sites. A `0` down-payment must not read as "no data" on a mortgage form.

### H3 · Address parser robustness + return-shape *(R1: §6 / Fix G)* · **1 hr**
Fix the district grafting, street-only fallback, and the `KeyError: 'floor'` on empty input. Affects every Budapest address.

### H4 · Surface the `LoanDetails` model fields as canonicals *(R2: Gap 2e)* · **2 hr**
`monthly_payment`, `down_payment`, `product_type`, `housing_savings`, `refinance_account`, `purchase_price`, `csok_amount`, `afa_support` are on the model and normalised but never emitted. Add them to the loan output dict; this also retires the unreachable `loan.*` heuristic canonicals (R1 §4.2).

### H5 · ZIP overwrite collision *(R2: Gap 2g — NEW)* · **30 min**
`_address_to_dict` writes `Contact.ZIP__c` for both permanent and mailing paths. Use `MailingPostalCode` for the mailing path so two-ZIP deals keep both.

### H6 · Concurrency on field-level mapping endpoints *(R1: §7.1 / Fix 7.1)* · **2 hr**
Per-pdf RLock across load→mutate→save for the 6 field-level + group endpoints. The whole branch premise is collaborative editing; today two tabs clobber each other.

### H7 · Build the 7-rule checkbox engine, phases 0–2 *(R2: Gap 1 / Fill Engine Design)* · **3–5 day**
- Phase 0 = C2 (already above).
- Phase 1: `src/engine/fill_rules.py` registry + Rules 1,2,5,6 + tests.
- Phase 2: Rules 3,4,7 + `Point`/`Block` model + editor UI.
- Phase 3: curate OTP v5's 71 checkboxes into `points`.
**Why high, not critical:** the production OTP v5 mapping currently has *zero* checkbox rules, so the engine is greenfield — but it is the product's core pitch and must exist before the form is trusted.

### H8 · CompletenessChecker vocabulary *(R1: B2 / Fix B2)* · **15 min**
Translate `Contact.*` required fields into the `loan.*/participant.*` vocabulary the checker understands, or the checker always reports INCOMPLETE and is dead weight.

### H9 · `get_attachments` is broken in live SF mode *(R1: B11 / Fix B11)* · **5 min**
`self._sf.query(query, deal_id=deal_id)` → `TypeError`. simple-salesforce `query()` takes no kwargs. Mock mode hides it.

---

## 🟡 MEDIUM (should-fix)

### M1 · Validate `canonical_field` against the catalog on save *(R1: §7.6 / R2: Gap 3d)* · **1 hr**
Reject unknown canonicals at the field API and validate the `conditional` fill-rule's `sf_field`. The endpoint `/api/mapping/canonical-fields` already exists — use it.

### M2 · Curate the `Lead.Ingatlan_megjegyzes__c` triple-collision *(R1: §4.3 / Fix 4.3)* · **15 min**
`property_parcel_number`, `property_rooms`, `property_year_built` all → one canonical in `otp_acroform_mapping.json`. Set rooms/year-built to `null` until canonicals exist.

### M3 · Fix `KEYWORD_MAP` parcel→property-type bug *(R1: §4.4 / Fix 4.4)* · **5 min**
`helyrajzi_szam`/`hrsz`/`parcel_number` must map to `Lead.Ingatlan_megjegyzes__c`, not `Ingatlan_jellege__c`.

### M4 · AcroForm checkbox export values ≠ `/Yes` *(R1: B4 / Fix E)* · **1 hr**
Detect the field's `/On` state from `/AP /N`; fall back to `/Yes`. Real bank forms use `/1`, `/X`, etc.

### M5 · Wire `character_groups` into the pipeline + add UI *(R2: Gap 3b)* · **1.5 day**
`character_groups` exist in backend + types but the pipeline ignores them and the editor has no UI. Needed for digit-box postal codes and comb text.

### M6 · Stop returning ghost `filled_fields` from the API *(R1: §7.2 / Fix 7.2)* · **30 min**
Either return the real list from `run_for_deal` or drop the keys. UI shows "0 filled" on every success.

### M7 · Filename sanitisation for Hungarian names *(R1: §7.3)* · **30 min**
`Pénzügyi átadás.pdf` → `Pnzughtyi_tads.pdf` and silent collisions. Use unicodedata + collision-safe suffix.

### M8 · Make `_fuzzy_match_canonical` actually case-insensitive *(R1: §7.5 / Fix 7.5)* · **15 min**
Lowercase index; stop auto-accepting invented `participant.*/loan.*` canonicals that can never be routed.

### M9 · DocumentAssembler must not save a truncated PDF silently *(R1: B8)* · **1 hr**
Out-of-range master pages → warn-and-skip today; either abort or flag the output.

### M10 · `Permanent_address__c` never produced *(R1: B9)* · **30 min**
Many mappings expect the composite address; today floor/door are dropped. Populate it from `Address.full_address`.

### M11 · Income-by-currency collapse *(R1: §1.3)* · **0.5 day**
12 `deviza` lines map to 2 canonicals → max 2 distinct values. Needs richer income canonicals or indexed income lines.

### M12 · SOQL string-interpolation vs comment *(R1: B12)* · **30 min**
Comment claims `:változó` parameterisation; code f-string-interpolates. Reconcile (security is out of review scope, but the drift is a trap).

---

## 🟢 LOW (nice-to-have / cleanup)

- **L1** Dead `RoleInstancePlanner` import *(R1: 1.3)* — resolved by C5.
- **L2** `add_field` can't create checkbox groups / rules *(R1: §7.7)* — resolved by C2 for update; mirror for create.
- **L3** `_is_truthy` duplicate `"false"` + dead `return` *(R1: B5)* — 2 min.
- **L4** DocumentAssembler log hardcodes "97 oldal" *(R1: B7)* — 2 min.
- **L5** Comb-flag comment "bit 25" vs `(1<<24)` *(R1: B10)* — comment fix.
- **L6** `temp_check.py` scratch file in `backend/` *(R1 §9)* — delete.
- **L7** `print()` in CLI → `logging.getLogger("cli")` *(R1 §5.2)* — 20 min.
- **L8** Layering inversion: `src/` imports `backend/pdf_service` *(R1 §1.1)* — extract `extract_acroform_fields` into `src/`.
- **L9** `force_recreate_mapping=True` on every upload *(R1: §7.4 / R2: Gap 4i)* — now costs real Claude money; make opt-in.
- **L10** Curate the canonical-fields endpoint to the **54-field** catalog *(R2: Gap 2f)* — offer only blessed fields in the editor autocomplete.
- **L11** `pyproject.toml` + ruff + mypy *(R1 §4.4, §7.1)* — reproducible install + drift surfacing.
- **L12** `suggest-groups` for checkbox groups, not just char-split *(R2: Gap 3e)*.
- **L13** Auto-generate `CANONICAL_FIELDS` from SF schema *(R1 §6.1)* — the "auto-generated" comment is currently a lie.

---

## Recommended landing order (first 2–3 days)

1. **C1** (10m) — safe tests.
2. **C2** (15m) — editor rule UI works.
3. **C3** (5m) + **C4** (15m) — no wrong-deal, no SF hard-error.
4. **H1** (1h) + **H2** (30m) + **H5** (30m) — 5 fields + money + ZIP, all cheap.
5. **C8** (1d) — date/digit fragments.
6. **C5+C6** (1.5d) — multi-instance routing.
7. **C7 decision** in parallel.
8. **H7 Phase 1** (1–2d) — the checkbox engine for Rules 1/2/5/6.

After step 8 the system: never processes the wrong deal, never silently drops a party/property, fills dates/digits correctly, treats `0` as `0`, keeps both ZIPs, and can express 4 of the 7 checkbox rules through a working editor. That is the realistic "first real customer form" gate. Rules 3/4/7 (H7 Phase 2) and the OTP v5 checkbox curation (H7 Phase 3) follow.

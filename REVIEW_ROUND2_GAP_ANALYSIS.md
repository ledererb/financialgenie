# Round 2 — Gap Analysis (main deliverable)

This is a **gap analysis**, not a re-review. Round 1 (`REVIEW_EDGE_CASES.md` / `REVIEW_FIXES.md` / `REVIEW_CODE_QUALITY.md` / `REVIEW_TESTING_STRATEGY.md`) is the baseline for the *code defects*. Round 2 measures the **delta between three new spec documents** and what the code on `feature/mapping-editor` actually implements:

1. `docs/hiteligenyles_kitoltesi_szabalyok.md` — the 7-rule checkbox fill system.
2. `docs/kanonikus_mezok_utmutato.pdf` (+ `docs/CANONICAL_FIELDS_HU.md`) — the 54-field canonical catalog.
3. `.env` — real Anthropic + Salesforce sandbox credentials are present (raises the stakes of Round-1 B3: tests hang / cost money here).

Each gap states **what the doc says**, **what the code does**, **the delta**, **severity / effort**, and a **concrete approach**. New Round-2-only findings are prefixed **[NEW]**.

Severity: 🔴 CRITICAL · 🟠 HIGH · 🟡 MEDIUM · 🟢 LOW.

---

## Gap 1 — Fill Engine vs. Kitöltési Szabályrendszer (the 7 rules)

**Doc says** (`hiteligenyles_kitoltesi_szabalyok.md`): checkbox filling is governed by 7 generalisable rule types, **framework-independent** (ALAP / CSOK Plusz / Otthon Start differ only in *which point uses which rule*, not in rule semantics). The doc explicitly defines a *point* (sorszámozott kérdés) and *block* (almezőcsoport) as first-class concepts, and gives a decision tree + a JSON contract:

```json
{ "point_id": "ALAP_8", "rule_type": 5, "condition_field": "SF.Termék", "condition_value": "évnyerő" }
```

**Code does:** `FormFillerPipeline._eval_fill_rule` (`src/main.py:488-539`) implements **four** flat, per-PDF-field rule types: `static`, `per_participant`, `conditional`, `role_based`. There is **no concept of a "point" or "block"** anywhere in the pipeline; rules attach to individual `RecognizedField`s, not to logical form sections.

**Rule-by-rule coverage:**

| Rule | Spec | Code coverage | Verdict |
|---|---|---|---|
| **1** Default, check-all unconditionally | every party ticked | `static` / `per_participant` returns one value | ✅ covered (but per-field, not per-point) |
| **2** Role-based multi-check (Adós **also** gets "Fedezettulajdonos") | one party → multiple ticks | `role_based` returns **a single value** if *any* participant has the role; it cannot express "role X ⇒ tick boxes A **and** B" | 🟠 partial — needs a role→tick-list map, not a boolean |
| **3** Partial block (only block N active, rest empty) | pick one sub-block | **none** — no block selector | 🔴 missing |
| **4** Two-step compound (default value in every block + closing role-tick) | step1 + step2 | **none** — engine is single-pass per field; no multi-step | 🔴 missing |
| **5** Product-conditional | tick iff `termék == X` | `conditional` works **if** `sf_field` is in `all_data` — `Opportunity.Term_k__c` IS there | 🟡 mechanically possible, but no UI/wiring and no "tick ALL parties" semantics |
| **6** Loan-purpose-conditional | tick iff `hitelcél == X` | `conditional` against `Contact.Loan_Purpose__c` (present) | 🟡 same as Rule 5 |
| **7** Multi-block, block-by-block different pattern | per-block independent rule sets | **none** — no block grouping | 🔴 missing |

**Delta:** 3 of 7 rules (3, 4, 7) are **structurally impossible** in the current per-field engine because they need a *block/point* abstraction. Rule 2 only half-works (single tick vs multi-tick). Rules 5/6 are *expressible* but unused.

**Is the architecture framework-independent?** The `_eval_fill_rule` evaluator itself is framework-agnostic (good), but the **rule attachment model is wrong**: rules hang off PDF fields, so reusing the same rule across ALAP/CSOK requires re-entering it on every field of every form. The spec's `point_id → rule_type` model — where a point is an addressable, reusable entity — does not exist.

**[NEW] Critical reality check — the production OTP v5 mapping uses NONE of this.** `igenylesi_dokumentumok_elso_6_oldal_mapping.json` has **199 fields, 71 of them checkboxes, and 0 `fill_rule`, 0 `checkbox_group`, 0 `fragment`** (see Field Audit). So even Rule 1 is not encoded on the real form today — the 71 checkbox boxes are mapped only by `canonical_field`, and most of those canonicals (e.g. `Foglalkozas_tipusa__c` ×13, `Legal_Title…` ×4, `Income_type__c` ×7) are **never produced** by the pipeline. The checkboxes are, in practice, all empty.

**Severity:** 🔴 CRITICAL — the checkbox engine is the core of the product pitch and it is unbuilt on the production form.
**Effort:** 3–5 days (see `REVIEW_ROUND2_FILL_ENGINE_DESIGN.md`).
**Approach:** introduce a `Point`/`Block` model and a rule registry that the per-field evaluator delegates to; see the dedicated design doc.

---

## Gap 2 — Canonical Fields vs. Code Usage

Full row-by-row detail is in `REVIEW_ROUND2_FIELD_AUDIT.md`. Headline deltas:

**2a. 🔴 Two non-existent SF fields are shipped in mapping fixtures [NEW-confirmed].**
`Contact.Monthly_installment_payment__c` and `Contact.Own_resources__c` are marked `⚠️ mező nem létezik` in the catalog and are present in `src/mapping/otp_acroform_mapping.json` (`loan_monthly_payment`, `loan_down_payment`) and `src/mapping/acroform_sample_mapping.json`. They are **never produced by `main.py`** (so the pipeline silently leaves them empty today), but any future SF read keyed on them will hard-error. The model *has* the data (`LoanDetails.monthly_payment`, `down_payment`) — it is just not surfaced.

**2b. 🟠 ~26 of 54 catalog fields are never produced.** (~48%.) The biggest clusters: address composition (`Permanent_address__c`, `OtherCountry`, `MailingPostalCode`, `Legal_Title…`, `Date_of_notification_for_residence__c`), employment (`Employment_Type_c__c`, `Foglalkozas_tipusa__c`, `Occupation__c`, `Self_employment_details__c`, `Current_employment_started__c`), income detail (`Other_income__c`, `Other_monthly_income__c`, `Jovairas_vallalasa__c`, `Current_debt_Credit_limit__c`), gender (`Salutation`), and `Description` / `Date_of_signature__c`. Every OTP v5 PDF field mapped to one of these is silently empty.

**2c. 🟠 The "ghost five" (Round-1 §4.1) confirmed against catalog.** `Marital_Status__c`, `Citizenship__c`, `Dependents_count__c`, `Highest_Educational_Qualification__c`, `Income_type__c` are real SF fields (read by the live SOQL!) but `_participant_to_dict` sources them from non-existent `Participant` attributes via `getattr(...,"")`, so they are always empty. The data is *fetched from SF and then thrown away* — the normalizer never maps these SF fields onto the model. This is the single highest-leverage fix: Option C-2 from Round-1 (`REVIEW_FIXES.md` Fix C) wires 5 fields for ~1 hour.

**2d. 🟡 Key-naming drift between catalog and pipeline.**
- Catalog: `Contact.Date_of_birth__c` (has data). Pipeline emits `Contact.Birthdate` + `Birthdate_year/_month/_day`. → heuristic birth boxes (`OTP_EXACT_MAP`) route to `Date_of_birth__c` and stay empty (Round-1 §1.1).
- Catalog: `Contact.Year_of_birthdate__c`. Pipeline emits `Birthdate_year` (no `__c`). Different key.
- These are why the curated OTP v5 file had to hand-edit 6 date rows.

**2e. 🟢 LoanDetail model fields exist but are unsurfaced.** `housing_savings`, `refinance_account`, `product_type`, `monthly_payment`, `down_payment`, `purchase_price`, `csok_amount`, `afa_support` are on `LoanDetails` (`canonical_model.py:113-120`) and the normalizer populates them — but `_prepare_field_data` emits **none** of them as canonical keys. The `loan.*` heuristic canonicals (Round-1 §4.2) are unreachable.

**2f. 🟡 Catalog vs. editor vocab mismatch.** The catalog curates **54** fields. But `/api/mapping/canonical-fields` returns **all ~200** entries of `CANONICAL_FIELDS` (156 Contact + Lead + Opportunity). The editor's autocomplete therefore offers fields the catalog explicitly did not bless, which encourages curators to map PDF fields onto canonicals the pipeline will never fill.

**2g. 🟠 [NEW] `ZIP__c` overwrite collision.** `_address_to_dict` writes `Contact.ZIP__c` for **both** the permanent (`address`) and the mailing path (`main.py:575,581`). When both addresses are present, the mailing ZIP silently overwrites the permanent ZIP (or vice-versa, depending on call order), and `MailingPostalCode` is never produced. A two-ZIP deal loses one ZIP.

**Severity:** 🔴 (2a) / 🟠 (2b, 2c, 2g) overall.
**Effort:** 2a+2c ≈ 2 hrs (data-wiring); 2b is incremental (a day per cluster); 2g ≈ 30 min.
**Approach:** apply Round-1 Fix C-2; add the 8 `LoanDetails` keys to the loan output dict; add a `pipeline_output_keys` generator test (Round-1 §6.2) to freeze the contract; fix `_address_to_dict` to use `MailingPostalCode` for the mailing path.

---

## Gap 3 — Mapping Editor vs. Field Catalog

The `feature/mapping-editor` branch's reason to exist. UI: `frontend/src/components/PageEditor.tsx` (1188 LOC). API: `backend/server.py`, `backend/mapping_service.py`.

**3a. 🔴 [NEW] The Fill-Rule and Checkbox-Group editors silently lose data through the field-level API.** This is the most important new finding for the branch.

`PageEditor.tsx` renders a full **Fill Rule** editor (type / value / sf_field / match / roles — lines 964-1047) and a **Checkbox Group** editor (group_id / match_value — lines 1057-1107). Both call `handleFieldUpdate` → `updateField(pdfId, field, patch)` → `PUT /api/mapping/field`.

But the backend `FieldUpdate` pydantic model (`backend/server.py:283-288`) declares **only** `canonical_field, field_type, confidence, notes, coordinates` — **not** `fill_rule`, **not** `checkbox_group`, **not** `label`. Pydantic v2 default `extra="ignore"` silently drops them. `mapping_service.update_field` *does* know how to write `fill_rule`/`checkbox_group`/`label` (`mapping_service.py:116`) — the server model just never forwards them.

Net effect: the operator sets a fill rule or checkbox group, the React state updates optimistically so **the UI looks correct**, but the next `GET /api/mapping` returns the field without the rule — it was never persisted. Only the full-save (`PUT /api/mapping` with the whole body) would persist them, and the editor never calls that path for field edits.

**Severity:** 🔴 CRITICAL for the branch — the two editors that implement Rules 1–7 are non-functional in normal use.
**Effort:** 15 min. Add the three fields to `FieldUpdate` (and `model_config = ConfigDict(extra="allow")` as a belt-and-braces).

**3b. 🟠 The editor cannot express the multi-field fragmentation the forms actually use.** There is **no `fragment` concept** anywhere: not in `RecognizedField`, not in `MappingField`, not in the UI. Year/month/day birth boxes and 4-digit postal boxes can only be mapped to whole canonicals → Round-1 §1.1/§2 reproduce in the editor. The `character_groups` mechanism (digit-box splitting) **does** exist in the backend (`mapping_service.create_group`) and in `MappingField` types, **and there is no UI for it** — `PageEditor` offers a `field_type: "character_split"` dropdown option but never lets you build a group (member_fields / direction). Worse: the **pipeline never reads `character_groups` at all** (`_prepare_field_data` only looks at `checkbox_group` and `fill_rule`), so even a correctly-defined char group is ignored at fill time.

**Severity:** 🟠 HIGH.
**Effort:** fragment: 1 day (Round-1 Fix B); char-group UI + pipeline wiring: 1.5 days.

**3c. 🟠 The editor cannot express per-instance routing.** A deal may have borrower + 2 coborrowers + guarantor + 2 properties. The only per-instance signal the pipeline honours is the OTP `-társ` suffix and a checkbox-group `all_co` heuristic (`main.py:378-382, 427-431`). There is **no UI field** to say "this PDF field belongs to `coborrower2`" or "to `property2`". Result: Round-1 §3.1/§3.2 (2nd coborrower dropped, 2nd property overwritten) cannot be corrected by the editor — the operator has no lever.

**Severity:** 🟠 HIGH (blocks any multi-party / multi-property form).
**Effort:** ~1 day UI + the Round-1 Fix A backend.

**3d. 🟡 No validation of `canonical_field` against the catalog on save.** `FieldUpdate.canonical_field` is a free `str`. The editor's `<select>` is sourced from `/api/mapping/canonical-fields` so direct picks are safe, but the `conditional` fill-rule's `sf_field` is a free-text input, and import / API calls can store anything (e.g. the non-existent `Own_resources__c`). Round-1 §7.6 stands.

**Severity:** 🟡 MEDIUM.
**Effort:** 1 hr (validate against the canonical-fields endpoint).

**3e. 🟢 `suggest-groups` heuristic is character-split only.** It detects sequential numeric-suffix fields (`suggest_groups`, `mapping_service.py:189-240`) — useful for digit boxes, but there is **no suggestion for checkbox groups** (grouping same-named `_role`/`_igen` checkboxes into a radio-like group). Curators must hand-build every checkbox group.

**Severity:** 🟢 LOW.
**Effort:** 0.5 day.

---

## Gap 4 — Missing Features for Production

Combining all sources, what is missing before a real customer form can be filled end-to-end correctly:

**4a. 🔴 The 7-rule checkbox engine** (Gap 1). Without it, the 71 checkboxes on the OTP v5 master form are either empty or unconditionally ticked. *Design in the dedicated doc.*

**4b. 🔴 Multi-instance (party × property) routing.** Round-1 §3.1/§3.2 are CRITICAL and unaddressed. The well-built `RoleInstancePlanner` (`role_instance_logic.py`) is imported-but-dead (`main.py:41`); wiring it in is the cleanest fix and also resolves Gap 3c's backend half. Note: `RoleInstancePlanner` is **page-level** (which pages, how many copies); field-level routing still needs the suffix-detect resolver from Round-1 Fix A. The two compose: planner decides pages, resolver fills each page's fields with the right instance.

**4c. 🔴 AcroForm field-name collision on duplicated pages.** Round-1 §3.3 — when `num_participants≥2`, the assembler appends the same source page (same AcroForm field names) and **all copies share one value**. This makes 4a/4b *moot* for AcroForm multi-party forms: even with correct routing, the PDF can't display two different names on two copies of the same field. Architectural decision required (rename-per-copy / overlay / drop feature).

**4d. 🟠 Multi-field value splitting (dates, postal digits).** Round-1 §1.1/§2 + Gap 3b. Needs the `fragment` field plumbed through model → JSON → UI → pipeline, plus `character_groups` consumed by `_prepare_field_data`.

**4e. 🟠 The 5 ghost fields + 2 non-existent fields.** Gap 2a/2c. The non-existent ones must be removed from shipped fixtures immediately (they will hard-error on live SF); the ghost five must be wired (Fix C-2) or the mappings curated to `null`.

**4f. 🟠 Test hermeticity in THIS environment.** `.env` now contains a **real** `ANTHROPIC_API_KEY` and **real** SF sandbox credentials. Round-1 B3 ("5 tests hang / cost money") is therefore **active, not theoretical** here: any `pytest` run without an offline fixture makes live Claude + Salesforce calls. The `tests/conftest.py` autouse fixture from Round-1 Fix B3 must be the very first thing landed. (I did **not** execute anything that touches these keys.)

**4g. 🟡 Money/number `0` → `""`.** Round-1 §5. A `0` down-payment reads as "missing"; on a mortgage form that is a misrepresentation.

**4h. 🟡 Editor↔pipeline contract drift.** `character_groups`, `fill_rule`, `checkbox_group`, `fragment` are each understood by *some* layers but not all (see Gap 3 table below). Until the four layers (model / JSON / backend API / frontend / pipeline) agree on the same field set, every new feature leaks somewhere.

**4i. 🟢 `force_recreate_mapping=True` on every upload** (Round-1 §7.4) costs a real Claude call per upload. With real credentials in `.env`, this is now real money per click.

---

## Cross-layer feature matrix (the connective-tissue view)

Which layer understands which advanced mapping feature. ✅ = implemented, ⚠️ = partial, ❌ = absent.

| Feature | `RecognizedField` model | Mapping JSON round-trip | Backend field API | Backend full-save | Frontend UI | **Pipeline consumes** |
|---|---|---|---|---|---|---|
| `canonical_field` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `checkbox_group` (group_id+match_value) | ✅ | ✅ | ❌ dropped (3a) | ✅ | ⚠️ checkbox-type only | ✅ |
| `fill_rule` (4 types) | ✅ | ✅ | ❌ dropped (3a) | ✅ | ✅ | ✅ |
| `fragment` (year/month/day, digit:N) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `character_groups` (digit split) | n/a | ✅ | ✅ CRUD | ✅ | ❌ no UI | ❌ ignored |
| per-instance routing (coborrower2/property2) | ❌ | ❌ | ❌ | ❌ | ❌ | ⚠️ `-társ` only |
| **7-rule point/block engine** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Read the matrix top-to-bottom for any feature: the lowest ❌ is where it stops working. For `fill_rule` and `checkbox_group` that is the **backend field API** (Gap 3a) — a 15-minute fix that unblocks the entire Rule 1/2/5/6 path. For `fragment` and the 7-rule engine, every layer needs work.

---

## Effort-to-unblock map

| Land this | Unblocks | Effort |
|---|---|---|
| Round-1 Fix B3 (offline test fixture) | safe `pytest` in this env | 10 min |
| Gap 3a (add 3 fields to `FieldUpdate`) | Rules 1/2/5/6 via editor | 15 min |
| Round-1 Fix F (mock `get_deal`) + 2a (remove non-existent fields) | trustworthy tests + no SF hard-error | 30 min |
| Round-1 Fix C-2 (wire ghost five) | 5 catalog fields filled from already-fetched SF data | 1 hr |
| Round-1 Fix D (`_fmt_money`) + 2g (ZIP fix) | correct money + ZIP | 1 hr |
| Round-1 Fix B (fragments) + char-group pipeline wiring | date/digit boxes | ~1.5 day |
| Round-1 Fix A (multi-instance) + planner wiring | multi-party / multi-property | ~1.5 day |
| 7-rule point/block engine (Gap 1) | the checkbox product | 3–5 day |
| Round-1 §3.3 architectural decision | multi-copy AcroForm | product decision |

The ordering matters: 3a is 15 minutes and is the difference between "the editor's rule UI works" and "it doesn't".

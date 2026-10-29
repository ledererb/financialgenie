# Genie Pipeline Cutover — Final Verification Report (2026-10-29)

**Task:** Cutover Task 6 — full cutover verification and handoff artifacts.
**Scope of this report:** offline verification only. No live Salesforce calls,
no org mutation, no AI calls, no commits. HEAD remained `2ec9ccf` throughout.

---

## 1. Approved state verified

| Check | Expected (approved state) | Measured | Verdict |
|---|---|---|---|
| Full pytest | 881 passed / 4 skipped / 8 deselected | **881 passed, 4 skipped, 8 deselected, 1 warning in 11.95s** | PASS |
| Frontend typecheck | green | `tsc --noEmit && tsc --noEmit -p tsconfig.node.json` — no errors | PASS |
| Frontend production build | green | `vite build` — 58 modules, `dist/` produced in 734 ms | PASS |
| Migrator final dry-run audit | `changed_count=0`, `unresolved=0` | `mode=dry-run mappings=22 entries_changed=0 unresolved=0 semantic_corrections=0 fragment_changes=0 group_changes=0 product_gates=0 nested_changes=0` (exit 0) | PASS |
| Canonical occurrence baseline | 1214 | `mapping_occurrences()` == **1214** (golden count in `tests/test_canonical_migration_map.py`) | PASS |
| Forbidden canonical paths in active mappings | 0 | **0** across all 1214 `canonical_field` values (regex scan for `Contact./Lead./Opportunity./Account.` prefixes, `#ev/#ho/#nap/#count/#piaci/#tamogatott` suffixes, `loan.*` paths, `-társ` endings) | PASS |

### 1.1 Exact commands and outputs

```bash
$ python3 -m pytest -q
881 passed, 4 skipped, 8 deselected, 1 warning in 11.95s

$ npm --prefix frontend run typecheck
> tsc --noEmit && tsc --noEmit -p tsconfig.node.json
(no output — clean)

$ npm --prefix frontend run build
> tsc -b && vite build
vite v6.4.3 building for production...
✓ 58 modules transformed.
dist/index.html                   0.46 kB │ gzip:   0.30 kB
dist/assets/index-D2Wxi8Yx.css    8.17 kB │ gzip:   2.36 kB
dist/assets/index-BGuQgJ-F.js   384.01 kB │ gzip: 103.62 kB
✓ built in 734ms

$ python3 scripts/migrate_mappings_to_genie.py --dry-run --report .superpowers/sdd/genie-migration-final-audit.json
mode=dry-run mappings=22 entries_changed=0 unresolved=0 semantic_corrections=0
fragment_changes=0 group_changes=0 product_gates=0 nested_changes=0
```

### 1.2 Skips and deselects (unchanged, all pre-existing)

* **4 skipped** — the sample `acroform_sample.pdf` is not present in the repo:
  * `tests/test_fixes_src.py:138` — acroform_sample.pdf missing
  * `tests/test_instance_renaming_c6.py:424` — acroform_sample.pdf not present
  * `tests/test_integration.py:56`, `tests/test_integration.py:87` — AcroForm minta PDF nem elérhető
* **8 deselected** — `pytest.ini` `addopts = -m "not live_api"` (tests requiring live
  Anthropic/Salesforce credentials): 3 in `tests/test_ai_smoke.py`, 4 in
  `tests/test_integration.py`, 1 in `tests/test_upload_api.py`.
* 1 warning — `PytestConfigWarning: Unknown config option: timeout` (pytest.ini
  carries a `timeout` option but `pytest-timeout` is not installed; cosmetic).

### 1.3 Migration audit trail (cumulative)

| Report (`.superpowers/sdd/`) | mode | mappings | entries_changed | unresolved | semantic_corrections | fragment_changes | group_changes | product_gates |
|---|---|---|---|---|---|---|---|---|
| `genie-migration-dry-run.json` (Task 1) | dry-run | 22 | 1222 | 0 | 198 | 53 | 42 | 21 |
| `genie-migration-apply.json` (Task 3) | apply | 22 | 1222 | 0 | 198 | 53 | 42 | 21 |
| `genie-migration-apply-second.json` (Task 3) | apply | 22 | 0 | 0 | 0 | 0 | 0 | 0 |
| `genie-migration-post-apply-audit.json` | dry-run | 22 | 0 | 0 | 0 | 0 | 0 | 0 |
| **`genie-migration-final-audit.json` (this task)** | dry-run | 22 | **0** | **0** | 0 | 0 | 0 | 0 |

Idempotence holds end-to-end: the Task 1 dry-run predicted 1222 changes; the
Task 3 apply performed exactly those 1222; every subsequent run (second apply,
post-apply audit, this final audit) changed nothing and resolved nothing as
unresolved. Pre-activation backups of all mappings with SHA256SUMS are under
`.superpowers/sdd/mapping-preactivation/`.

### 1.4 Migration diff summary (working tree vs HEAD `2ec9ccf`)

The cumulative diff of the cutover (Tasks 1–5, uncommitted by design):

* `src/mapping/*.json` — 22 files, +2157/−1786 lines (canonical path migration,
  Task 5 declaration rebuild included).
* Whole tree — 58 tracked files, +7499/−4477 lines (pipeline resolver, SF
  adapter, backend/frontend registry unification, legal safety) plus the
  untracked new modules (`src/canonical/`, `src/integrations/salesforce_schema.py`,
  `src/integrations/salesforce_field_map.json`, `scripts/` tooling, 21 new test
  files). Per-task review diffs are in `scratch/reviews/`.

Canonical model surface at cutover: curated registry of **253 base field
definitions**, **5 roles** (`borrower, co_borrower, guarantor, mortgagor,
usufructuary`); the `/api/mapping/canonical-fields` endpoint serves **6487
validated Genie paths** (no legacy paths).

---

## 2. Offline generation smoke (mock Salesforce, no live calls)

Script: `.superpowers/sdd/task6_smoke.py` (working-tree artifact, git-ignored).
Data source: `samples/dummy_data/multi_borrower.json` (deal **SF-577531**:
adós Németh Ferenc, adóstárs Papp Éva, kezes Rácz Attila, 1 ingatlan).
`DEEPSEEK_API_KEY`/`ANTHROPIC_API_KEY` were removed from the environment; every
mapping resolved from the existing `src/mapping/*.json` (exact-stem match, no
recognition fallback); `upload_to_sf=False`; the mock client performs no network
I/O. Results artifact: `.superpowers/sdd/task6-smoke-results.json`.

### 2.1 OTP package (`otp_bank` / `piaci_hitel`)

Output: **`output/package_otp_bank_piaci_hitel_SF-577531_task6smoke_20261029_142002.zip`**
— 11 entries, 2 736 039 bytes. Per-applicant copies behaved as specified:
`sza_ig_tarsigenylo` only for the co-borrower, `sza_esz` for borrower + co-borrower.

| Document | success | filled | skipped | manual decl. | widget census (text/checkbox of total) |
|---|---|---|---|---|---|
| fedlap | yes | 0 | 13 | 0 | 0/0 of 13 |
| sza_ig_igenylő | yes | 19 | 92 | 11 | 14/5 of 153 |
| sza_ig_tarsigenylő (adóstárs 1) | yes | 20 | 91 | 5 | 17/1 of 153 |
| sza_esz | yes | 13 | 16 | 3 | 13/0 of 37 |
| sza_esz (adóstárs 1) | yes | 16 | 13 | 3 | 16/0 of 37 |
| munkaltato_adatlap | yes | 2 | 17 | 2 | 2/0 of 41 |
| ingatlan_adatlap | yes | 4 | 25 | 42 | 4/0 of 69 |
| hitelfeltetelek | yes | 6 | 52 | 10 | 3/9 of 122 |
| altalanos_nyilatkozatok | yes | 22 | 139 | **185** | 0/8 of 212 |
| Partner nyilatkozat hiteligeny leadasakor | yes | 1 | 0 | 0 | 0/0 of 0 (flat form) |
| V számú függelék ügyfél/cég nyilatkozat | yes | 27 | 63 | 9 | 27/0 of 90 |
| **Total** | **11/11** | **130** | **521** | **270** | — |

Independent PyMuPDF census of the produced PDFs confirms the filler's own
counts (e.g. correct per-participant routing: `SZA_IG_név='Németh Ferenc'` on
the igénylő copy vs `SZA_IG_név-társ='Papp Éva'` on the co-borrower copy; date
fragments split correctly: `SZA_IG_szül_év='1981'`, `SZA_IG_szül_hónap='10'`).

**Legal/declaration safety observed on the real `altalanos_nyilatkozatok`
form:** exactly **8 checkboxes ticked** — the reviewed acknowledgement points'
columns of the two applicants (adós + adóstárs_1) — and **185 manual
declarations listed** (KHR, NAV, pénznem, cselekvőképesség, all
felhatalmazás/hozzájárulás points). No consent, KHR or currency checkbox was
ticked automatically. This matches the Task 5 golden behavior.

### 2.2 CIB value-assessment order form

Template: `samples/ertekbecslo_megrendelolap_240920_2.pdf`
(mapping `src/mapping/ertekbecslo_megrendelolap_240920_2_mapping.json`, 83 fields).

Output: **`output/filled_SF-577531_ertekbecslo_megrendelolap_240920_2_20261029_142002_474790.pdf`**

* success: **true**; filled fields **20**; skipped **34**; issues: none.
* Widget census: 15 text + 8 checkbox of 83 total. Sample values:
  `c.debtor.fullName='Németh Ferenc'`, `c.debtor.motherFullName='Balogh Ágnes'`,
  `c.debtor.birthDate='1981.10.17'`, `c.debtor.address='3000, Miskolc, Thököly út 120'`,
  `Megrendelő telefonszáma='+36 20 822 5424'`.
* **15 manual declarations** listed (ingatlan choice families `Ing_al_1___*`,
  `Ing_al_2___*`, etc.) — none auto-ticked from a guess.

### 2.3 Validation / completeness and notices

* `run_for_deal` completeness ran for every document; **no blocking issues**
  were recorded (`issues: []` on all 12 runs) — the mock deal satisfies the
  explicit Genie mandatory paths.
* One recurring informational notice on every document (expected, reviewed
  behavior): the deal's product data (`'OTP Lakáshitel Plus'`) matches none of
  the reviewed `_PRODUCT_GATE_KEYWORDS`, so product-gated sections stay empty
  instead of being force-filled — a deliberate fail-safe, not a defect.
* `fedlap` filled 0/13 because its mapping carries only `deal.csok_amount` /
  `deal.otthon_start_amount`, and this piaci hitel mock deal has neither —
  data-driven, not a pipeline failure.
* No Salesforce upload occurred: `upload_to_sf=False`; mock attach touches only
  the in-memory `MockSalesforceState`.

---

## 3. Known operational prerequisites (post-cutover)

1. **Schema snapshot is mandatory for live mode.** `SalesforceClient` live mode
   fails closed without `config/sf_schema_snapshot.json` (or `SF_SCHEMA_PATH`).
   Generate it with the describe-only sync — see
   `docs/SALESFORCE_SETUP_RUNBOOK.md` §3. **The live describe has NOT been run**
   (no `SF_ORG_ID` was provided in this environment; `config/` contains no
   snapshot). All SF work in this cutover used fakes/mocks.
2. **Proposed org objects/fields are configuration, not org state.**
   `Jovedelem__c` / `Meglevo_hitel__c` and the extended `Property__c` fields are
   *expected API names* in `src/integrations/salesforce_field_map.json`; if the
   org lacks them, related-record queries degrade to a warning + empty rows
   (never fabricated data). Creating them in the org is a Setup decision — see
   the runbook.
3. **Mapping approval.** All active mappings remain `approved: false`
   (including the Task 5 declaration rebuild). Production use requires the
   usual human approval pass in Mapping Studio (`approved_by` + timestamp).
4. **Legal declarations stay manual** except the 4 reviewed acknowledgement
   points. Consents can only ever be ticked via explicit CRM consent canonical
   paths (`participant.{role}.aml_consent` / `data_processing_consent` /
   `khr_consent` / `nav_consent`) wired as reviewed mappings.
5. **AI keys are optional offline.** Recognition/legal AI are only used when
   explicitly triggered and keyed (`ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY`);
   the deterministic pipeline path used in the smoke needs neither.
6. **Rollback** is git-based: the entire cutover is uncommitted on top of
   `2ec9ccf`; `git checkout -- src/mapping` (or full `git reset --hard 2ec9ccf`)
   restores pre-cutover mappings; per-file SHA256-verified pre-activation
   backups exist under `.superpowers/sdd/mapping-preactivation/`.

---

## 4. Verdict

The cutover state is **verified**: full suite green at the approved counts,
frontend typecheck/build clean, migration fully idempotent with zero unresolved
entries, zero forbidden canonical paths at the 1214-occurrence baseline, and
representative OTP (11-document package ZIP) + CIB (value-assessment order
form) generation works end-to-end offline with correct participant routing and
fail-safe legal behavior. Handoff documents updated: `HANDOFF.md`,
`docs/SALESFORCE_SETUP_RUNBOOK.md`, `ARCHITECTURE.md`,
`docs/CANONICAL_FIELDS_HU.md` (staleness banner).

---

## 5. Post-verification correction (final review findings B1 + B2, 2026-10-29)

Two inaccuracies were found by the final review pass and fixed; they are
recorded here so this report reflects the verified state:

1. **Mapping approval (§3.3).** "All active mappings remain `approved: false`"
   was not true at verification time: `src/mapping/otp_acroform_mapping.json`
   (a PoC sample mapping that was also part of the audited migration set)
   still had `approved: true` / `approved_by: "PoC auto-generated"`. Fixed to
   `approved: false` / `approved_by: null`. A new audit test
   (`tests/test_mapping_approval_audit.py`) now enforces that every
   `src/mapping/*.json` (except `legal_defaults.json`) stays unapproved —
   verified red on the old flags, green after the fix. §3.3 is now accurate.

2. **Upload-to-Salesforce explicitness (§8 of the runbook).** The runbook
   claimed live ContentVersion uploads happen only on explicit request, but
   `/api/catalog/generate-package` defaulted `upload_to_sf` to `True` and the
   frontend dialog pre-checked the upload checkbox — an omitted flag uploaded.
   Fixed: backend default is now `False`
   (`backend/server.py`, `body.get("upload_to_sf", False)`), the
   `PackageGenerationDialog` checkbox starts unchecked, and new tests
   (`tests/test_fixes_backend.py::TestGeneratePackageUploadToSfIsOptIn`,
   fully faked boundaries, no live calls) prove: omitted flag → no upload;
   explicit `true` → upload proceeds. The runbook wording now also states the
   omitted-field default explicitly.

Evidence: `python3 -m pytest tests/test_mapping_approval_audit.py
tests/test_fixes_backend.py::TestGeneratePackageUploadToSfIsOptIn -q` →
`3 passed` (RED before the fix: the omitted-flag test failed with an
unexpected `sf_uploads` entry; the audit test failed on `otp_acroform`).

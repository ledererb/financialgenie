# Pipeline and Studio Genie Cutover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the active filling pipeline, Mapping Studio, recognizer and persisted mappings exclusively to the approved Genie canonical paths.

**Architecture:** The mapping migrator is built and audited before activation. The pipeline then consumes `CanonicalResolver` for every field/rule consumer, persisted mappings are migrated in one audited step, and recognizer/backend/frontend share the same registry. Legacy Salesforce-named canonical routing is deleted rather than aliased.

**Tech Stack:** Python 3.12, FastAPI, React/TypeScript, PyMuPDF, pytest, Playwright where useful.

## Global Constraints

- Final active `src/mapping/*.json` files contain no `Contact.*`, `Lead.*`, `Opportunity.*`, `Account.*` Salesforce-named canonical paths, `-társ` suffixes, `#ev/#ho/#nap/#count/#piaci/#tamogatott` canonical suffixes, or `loan.*` legacy paths.
- Preserve PDF field names, coordinates, character-group membership, checkbox groups, points, labels and approval metadata unless a semantic correction explicitly requires otherwise.
- No legal declaration is filled from a guess/default; only explicit canonical evidence and reviewed role/signature rules may fill.
- No live Salesforce calls, org mutation, commits, or customer-record processing.
- Full offline pytest, frontend typecheck and production build must pass at completion.

---

### Task 1: Deterministic mapping migrator and dry-run audit

**Files:**
- Create: `scripts/migrate_mappings_to_genie.py`
- Create: `tests/test_mapping_genie_migrator.py`

**Interfaces:**
- `migrate_mapping(data: dict, *, pdf_path: str | None = None) -> MigrationResult`
- `MigrationResult` exposes `mapping`, `changed_count`, `unresolved`, `semantic_corrections`, `fragment_changes`, `group_changes`, `product_gates`.
- CLI supports `--dry-run` (default), `--apply`, `--report`, `--only`, and refuses to run twice with changes.

- [ ] **Step 1: Build failing migrator tests from real files**

Load every `src/mapping/*.json`; test representative semantic corrections (`#nap`, `#count`, `-társ`, property index, company declaration, requested-vs-participant payments), preservation of coordinates/groups/points, and reports for all 22+ mappings. Include idempotence and no-write dry run.

- [ ] **Step 2: Implement migration only, not activation**

Use `Translation` from `scripts/canonical_migration_map.py`; label/member-field context must drive count semantics. Date suffixes become `fragment`; product suffixes become explicit product-gate metadata consumed by existing rule logic; participant index and property index become explicit path indexes. Unknown entries abort the whole apply with a report.

- [ ] **Step 3: Verify and record baseline**

Run migrator dry-run over all mappings. Save JSON report under `.superpowers/sdd/genie-migration-dry-run.json`; no active mapping is modified yet. Focused tests and full suite pass.

---

### Task 2: Resolver-backed pipeline for every field/rule consumer

**Files:**
- Modify: `src/main.py`
- Modify: `src/engine/document_assembler.py` only where participant context requires it
- Modify: `src/engine/fill_rules.py` only if indexed roles need a small extension
- Create: `tests/test_pipeline_genie_resolver.py`

**Interfaces:**
- `_prepare_field_data(..., context: ResolutionContext | None = None)` resolves mapped canonical paths through `CanonicalResolver`.
- `participant_override` is replaced or wrapped by explicit `(role, index)` context; callers pass context rather than PDF-name heuristics.
- Direct fields, checkbox groups, fill rules, points, character groups, date/digit fragments and role instances all use the resolver/context.

- [ ] **Step 1: Write synthetic Genie-mapping tests before activation**

Use synthetic mappings containing direct paths for borrower/co-borrower/guarantor/mortgagor, indexed properties, income rows, existing loans, account fields, date/digit fragments and all seven rule types. Assert exact values and that PDF names containing `társ` do not select participants. Add document-instance tests proving guarantor/mortgagor copies do not generate co-borrower pages.

- [ ] **Step 2: Implement resolver-backed consumers**

Keep old mappings working only until Task 3 via a narrowly isolated temporary compatibility branch; do not add a public legacy alias API. Remove the borrower/co-borrower dict duplication for new paths. Apply income exclusion through resolver policy. Preserve notices and completeness diagnostics.

- [ ] **Step 3: Run focused and existing pipeline regressions**

Run new resolver pipeline tests plus fill rules, fragments, instance renaming and integration suites. Expected all green before activation.

---

### Task 3: Activate mapping migration and delete legacy canonical routing

**Files:**
- Modify: all active `src/mapping/*.json` via Task 1 script
- Modify: `src/main.py`
- Modify: affected tests, especially mapping-quality/golden expectations
- Create: `tests/test_no_legacy_canonical.py`

**Interfaces:**
- Active mappings and runtime route accept Genie paths only.
- The temporary compatibility branch from Task 2 is deleted.

- [ ] **Step 1: Add final-state audit tests**

Audit every mapping field, character group, point and rule parameter for forbidden legacy forms. Audit runtime `_prepare_field_data` with a legacy path: it must raise/report rather than silently resolve. Verify migrated mapping counts against the dry-run report.

- [ ] **Step 2: Apply migration and remove compatibility**

Run `scripts/migrate_mappings_to_genie.py --apply --report ...`; verify idempotent second run changes nothing. Delete legacy branch and old producer/routing dictionaries for `Lead.*`, `Contact.*`, `Opportunity.*`, `Account.*` and `-társ`.

- [ ] **Step 3: Repair semantic regressions test-first**

Run full pytest. For every failure, decide whether the old expectation was a bug (update test with rationale) or migration defect (fix migrator/pipeline and re-run). No failure may be skipped or deselected to hide migration breakage.

---

### Task 4: Recognizer, backend catalog and Studio on one registry

**Files:**
- Modify: `src/ai/field_recognizer.py`
- Modify: `backend/mapping_service.py`
- Modify: `backend/server.py`
- Modify: `frontend/src/components/PageEditor.tsx` and related canonical search/grouping UI
- Modify: relevant backend/frontend tests

**Interfaces:**
- `/api/mapping/canonical-fields` returns registry entries with type/label/path metadata and no legacy paths.
- Recognition prompts/heuristics and manual autocomplete use only Genie paths.
- Backend full-mapping save/import and group CRUD validate registry paths and fragments consistently with the parser.

- [ ] **Step 1: Add failing API/recognition tests**

Test canonical endpoint data, suffix-free validation, invalid legacy rejection, recognizer postprocess output and deterministic grouping. Add a frontend typecheck-level test or focused component test for grouped/searched Genie paths.

- [ ] **Step 2: Implement shared registry use**

Remove static `CANONICAL_FIELDS`/`SF_FIELD_TYPES` duplication. Keep AI behavior deterministic offline. Update PageEditor grouping labels for `deal`, `account`, `participant.<role>`, `property`, `income`, `existing_loan`; do not render 6k options in one unvirtualized list if the current UI cannot handle it.

- [ ] **Step 3: Verify Studio flows offline**

Run backend tests, frontend typecheck/build and existing Playwright smoke where available. No live AI calls.

---

### Task 5: Safe declaration rebuild and legal-rule table

**Files:**
- Modify: `src/mapping/altalanos_nyilatkozatok_mapping.json` via a deterministic script
- Create: `scripts/rebuild_altalanos_nyilatkozatok_mapping.py`
- Modify: `src/ai/legal_classifier.py`
- Modify: `src/mapping/legal_defaults.json`
- Modify: `src/main.py` legal application boundary
- Create: `tests/test_legal_declaration_safety.py`

**Interfaces:**
- Actual AcroForm widget types/groups drive the mapping; 207 checkbox widgets and 5 text fields are represented truthfully.
- Role/signature checkbox rules use `points`/rule 2 with indexed role addressing.
- Non-role legal choices stay canonical-less/manual; unknown categories never receive a value.

- [ ] **Step 1: Write safety tests first**

Assert actual PDF type counts/groups, no automatic true/false for unknown KHR/NAV/legal choices, explicit consent paths fill only from CRM booleans, indexed roles tick only their column, and LegalClassifier cannot overwrite points-managed fields.

- [ ] **Step 2: Rebuild mapping and rules deterministically**

Derive widget types/groups from the real PDF; preserve existing labels/pages/coordinates. Add reviewed role/signature rule entries only where labels clearly identify role/signature semantics. Set legal defaults to manual/null and remove product-condition automatic consent ticks.

- [ ] **Step 3: Run legal and end-to-end regressions**

Run legal safety tests, fill-rule tests and full suite. Generate at least one offline mock declaration-package preview and assert unresolved declarations are listed as manual, not silently ticked.

---

### Task 6: Full cutover verification and handoff artifacts

**Files:**
- Create: `docs/superpowers/reports/2026-10-29-genie-cutover-verification.md`
- Modify: `HANDOFF.md` only after verification
- Create/modify runbook under `docs/`

**Interfaces:**
- Final evidence includes dry-run/apply reports, migration diff summary, test/build counts, and known operational prerequisites.

- [ ] **Step 1: Run complete verification**

Run:

```bash
python3 -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
python3 scripts/migrate_mappings_to_genie.py --dry-run --report .superpowers/sdd/genie-migration-final-audit.json
```

Expected: idempotence (`changed_count=0`), all tests/builds green, and no forbidden canonical paths.

- [ ] **Step 2: Smoke representative OTP and CIB generation offline**

Use mock Salesforce data and existing representative PDFs for one OTP package and the CIB value-assessment form. Record output paths, filled-field counts, manual declarations and validation results. Do not upload to Salesforce.

- [ ] **Step 3: Write handoff/runbook updates**

Document the schema snapshot prerequisite (`SF_ORG_ID`, describe-only sync, approval), role/related-object expectations, manual declaration policy, migration audit commands and rollback through git (without committing in this session).

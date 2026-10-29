# Salesforce Canonical Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate the approved Genie canonical model from real Salesforce-shaped data, with describe-safe queries and correct participant/related-record semantics.

**Architecture:** Salesforce metadata and field mapping live in the integration layer, separate from the Genie registry. `SalesforceClient` resolves records and roles; `DataNormalizer` populates canonical model attributes, canonical account/deal/property stores and related rows. All tests use fake Salesforce boundaries; live org access is limited to an optional final describe.

**Tech Stack:** Python 3.12, simple-salesforce, Pydantic 2, pytest, JSON describe snapshots.

## Global Constraints

- No Salesforce schema/data mutation. Tests never use live credentials/network.
- No runtime legacy canonical aliases or `Lead.*` property routing.
- No automatic legal values or consent inference from free text/presence.
- Preserve `None` versus explicit `False`; never synthesize twelve income rows.
- Keep requested-loan payments, existing-loan payments and affordable installments separate.
- No git commits; preserve P0/canonical-core work and user files.

---

### Task 1: Describe snapshot, capability checks and generated SF field map

**Files:**
- Create: `src/integrations/salesforce_schema.py`
- Create: `src/integrations/salesforce_field_map.json`
- Create: `scripts/sync_sf_schema.py`
- Create: `scripts/build_salesforce_field_map.py`
- Create: `tests/fixtures/sf_schema.json`
- Create: `tests/test_salesforce_schema.py`

**Interfaces:**
- `SalesforceSchema.from_dict(data: dict)`, `has_object(name)`, `has_field(object_name, field_name)`, `available_fields(object_name, candidates)`.
- Snapshot contains `org_id`, `generated_at`, `objects` with field type/reference/picklist metadata; no customer records.
- Generated field map translates Salesforce API fields to canonical model sources; production never imports legacy migration code.

- [ ] **Step 1: Write failing schema/capability tests**

Use fixture objects for Contact, Opportunity, Account, Property__c, Jovedelem__c and Meglevo_hitel__c. Assert exact available-field filtering, unknown object/field behavior, lookup/picklist metadata retention and no record payload in snapshots. Test the generated map against canonical registry structural validation:

```python
def test_field_map_targets_valid_model_sources():
    field_map = load_field_map()
    for api_name, rule in field_map["Contact"].items():
        assert api_name
        assert rule["target"].startswith(("participant.", "account."))
        assert_model_source(rule["target"])


def test_missing_related_object_is_explicit(schema):
    assert not schema.has_object("Jovedelem__c")
    assert schema.missing_objects(["Jovedelem__c"]) == ["Jovedelem__c"]
```

- [ ] **Step 2: Verify RED, then implement**

Run `python3 -m pytest -q tests/test_salesforce_schema.py`; expected missing module. Implement strict snapshot parsing and deterministic field-map generation from the offline canonical translation/registry artifacts. Include account/company fields, deal extras, participant audit fields/consents and Property fields. API names for proposed new objects are configuration in this map, not hard-coded live assumptions.

- [ ] **Step 3: Add read-only sync script**

`sync_sf_schema.py` uses existing settings, calls only `describe()`, writes a dated/org-identified JSON snapshot atomically, never prints secrets and supports `--output`. It must not query Opportunity/Contact/Lead records. Offline tests use a fake `Salesforce` describe object.

- [ ] **Step 4: Run GREEN**

Run schema tests plus canonical registry tests. Expected: all pass, deterministic map/snapshot.

---

### Task 2: Participant role merge and canonical scalar/account/property normalization

**Files:**
- Modify: `src/integrations/salesforce_client.py`
- Modify: `src/normalizer/data_normalizer.py`
- Modify: `src/models/canonical_model.py` only if a missing canonical convenience property is required
- Create: `tests/test_salesforce_roles.py`
- Create: `tests/test_salesforce_normalizer_live_shape.py`

**Interfaces:**
- Role keys support `adós`, `adóstárs_N`, `kezes_N`, `zálogkötelezett_N`, `haszonélvező_N`.
- Raw participants carry `contact_id`, role/index, incomes/existing-loans placeholders and canonical field inputs.
- Normalized account keys include `name`, `phone`, `billing_street` and the remaining registry account concepts.

- [ ] **Step 1: Write failing fake-SF role tests**

Cover OCR-only, lookup-only and mixed records. OCR must not hide First/Second/Third_Co_debtor lookup participants. Deduplicate repeated Contact IDs within a role and borrower/co-borrower conflicts; preserve a Contact legitimately holding two distinct non-borrower roles. Separate `kezes` from `zálogkötelezett`, retain multiple instances and income flags. Never return a lookup Salesforce ID as a person's display name.

- [ ] **Step 2: Verify RED and implement ordered role merge**

Run role tests against fake `_sf.query`; expected current co-borrower loss and mortgagor/guarantor conflation. Implement one ordered merge with explicit role/index, no silent OCR-first discard. Update normalizer role mapping to `MORTGAGOR` versus `GUARANTOR` and propagate `contact_id`.

- [ ] **Step 3: Add failing live-shape normalization tests**

Use raw `_opportunity`, `_account`, Contact and Property dictionaries rather than already-canonical dummy JSON. Assert canonical nested addresses, account company fields, participant IDs/audit scalars/extra, explicit consents, property ID/details and deal extras resolve through `CanonicalResolver`. Prove `Contact.Monthly_Payment_details__c`, requested-loan monthly payment, existing-loan payment and affordable installment remain four independent values.

- [ ] **Step 4: Implement normalization and describe-filtered SOQL**

Populate canonical stores using the generated field map and existing explicit type/date normalizers. Filter static Contact/Property candidate fields through `SalesforceSchema.available_fields` in live mode; aggregate missing-field warnings without dummy data. Remove affordable-installment fallback into existing-loan payment. Consents parse only explicit boolean/picklist evidence and preserve unknown as absent/`None`.

- [ ] **Step 5: Run GREEN and existing regressions**

Run new role/normalizer tests, existing normalizer/model tests and canonical resolver tests. Expected: all pass.

---

### Task 3: Related incomes, existing loans and property enrichment

**Files:**
- Modify: `src/integrations/salesforce_client.py`
- Modify: `src/normalizer/data_normalizer.py`
- Modify: `src/integrations/salesforce_field_map.json`
- Create: `tests/test_salesforce_related_records.py`

**Interfaces:**
- `SalesforceClient` attaches `_incomes` and `_existing_loans` lists to the correct raw participant by Contact ID.
- Related queries execute only when describe confirms the object and required lookup/fields; otherwise one actionable warning per missing capability.

- [ ] **Step 1: Write failing related-record tests**

Fake two participants with interleaved `Jovedelem__c` and `Meglevo_hitel__c` records, including twelve incomes for one participant. Assert Contact-ID ownership, `Sorszam__c/CreatedDate/Id` stable ordering, all approved fields, currency/boolean/None preservation and normalizer → resolver row paths. Test missing object/lookup produces warning and empty list, never fabricated rows or a query with invalid fields.

- [ ] **Step 2: Verify RED and implement related queries**

Use the field map's proposed API names and `Contact__c` lookup contract. Batch by participant Contact IDs rather than N+1 queries where the fake/API supports `IN`; sanitize IDs. Extend Account-pool enrichment only to explicitly mapped scalar fields, not related-row merging across different Contact IDs.

- [ ] **Step 3: Complete Property support**

Add describe-safe candidates and normalization for rooms, condition, heating, year built, lift, insurance, detailed type and existing extra fields. Preserve `property_id`; do not reduce detailed type to the legacy enum. Keep parcel number distinct from comments and remove any year/loan-term semantic conflation.

- [ ] **Step 4: Run GREEN**

Run related tests plus existing integration/normalizer/resolver suites. Expected: exact twelve rows, correct ownership and no live calls.

---

### Task 4: End-to-end fake Salesforce contract and optional read-only describe

**Files:**
- Create: `tests/test_salesforce_canonical_contract.py`
- Modify: `docs/superpowers/specs/2026-10-28-canonical-migration-design.md` only to record actual adapter interfaces if needed
- Optional generated output: `config/sf_schema_snapshot.json` (metadata only)

**Interfaces:**
- One fake raw Opportunity package flows `SalesforceClient.get_deal()` → `DataNormalizer` → `CanonicalResolver` for deal/account/all participant roles/property/income/existing-loan namespaces.

- [ ] **Step 1: Add the failing end-to-end contract**

Assert non-null sentinel resolution for every source namespace, exact company account fields, separate guarantor/mortgagor, no affordable/existing-payment conflation, and no legal consent without explicit evidence. Add a capability-missing variant with actionable warnings. Verify old mock JSON still deserializes without becoming a runtime legacy-alias path.

- [ ] **Step 2: Implement only contract gaps and run full offline verification**

Run:

```bash
python3 -m pytest -q tests/test_salesforce_schema.py tests/test_salesforce_roles.py tests/test_salesforce_normalizer_live_shape.py tests/test_salesforce_related_records.py tests/test_salesforce_canonical_contract.py
python3 -m pytest -q
```

Expected: all offline tests pass; report exact counts/skips.

- [ ] **Step 3: Optional live metadata-only describe**

Only after offline green, if existing credentials are available, run `python3 scripts/sync_sf_schema.py --output config/sf_schema_snapshot.json`. This performs describe only. Do not create/modify org objects, do not query customer records and do not include credentials in output. If credentials/network are unavailable, record that the live describe was not run; offline completion remains valid.

- [ ] **Step 4: Review adapter readiness for pipeline cutover**

Confirm all canonical stores required by the final core review are populated, no runtime legacy alias exists, missing new org objects are warnings, and monthly-payment semantics are available for occurrence-aware mapping migration. Record any live-schema differences for the Setup-runbook rather than changing the org.

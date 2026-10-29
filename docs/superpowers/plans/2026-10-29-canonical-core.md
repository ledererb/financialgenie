# Genie Canonical Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tested, Salesforce-independent canonical field registry, path parser and resolver without yet changing the active filling pipeline.

**Architecture:** A versioned JSON registry describes Genie fields and model sources. A strict parser handles repeatable participant/property/related-record paths; one resolver reads `DealData` plus an explicit document-instance context. The legacy translation table is migration tooling only, never a runtime alias.

**Tech Stack:** Python 3.12, Pydantic 2, pytest, JSON.

## Global Constraints

- Do not change active mapping JSONs or pipeline routing in this plan.
- No Salesforce network/mutation; model and resolver tests are entirely offline.
- No automatic legal declaration values.
- No git commits unless explicitly requested.
- Preserve the P0 changes and the user's existing files.
- Final runtime canonical paths contain no `Contact.*`, `Lead.*`, `Opportunity.*` or `-társ` suffixes.

---

### Task 1: Registry schema and strict path parser

**Files:**
- Create: `src/canonical/__init__.py`
- Create: `src/canonical/paths.py`
- Create: `src/canonical/registry.py`
- Create: `src/canonical/fields.json`
- Create: `tests/test_canonical_registry.py`

**Interfaces:**
- `parse_path(path: str) -> CanonicalPath`
- `CanonicalPath` exposes `namespace`, `role`, `participant_index`, `collection`, `collection_index`, `property_index`, `field`.
- `CanonicalRegistry.validate(path: str) -> bool`, `get(path: str) -> CanonicalField`, `canonical_fields() -> list[dict]`.
- Module singleton: `canonical_registry`.

- [ ] **Step 1: Write parser/registry tests**

Cover all approved forms and rejection of legacy/arbitrary paths:

```python
@pytest.mark.parametrize("path,role,index", [
    ("participant.co_borrower.name", "co_borrower", None),
    ("participant.co_borrower[2].name", "co_borrower", 2),
    ("participant.mortgagor[3].income[12].amount", "mortgagor", 3),
])
def test_parse_participants(path, role, index):
    parsed = parse_path(path)
    assert parsed.role == role
    assert parsed.participant_index == index


def test_parse_related_and_property():
    related = parse_path("participant.borrower.existing_loan[2].bank_name")
    assert (related.collection, related.collection_index) == ("existing_loan", 2)
    assert parse_path("property[3].address.city").property_index == 3


@pytest.mark.parametrize("path", [
    "Contact.Name", "Lead.Ingatlan_irsz__c", "Opportunity.Amount",
    "participant.co_borrower.name-társ", "participant.unknown.name",
    "participant.co_borrower[0].name", "property[10].parcel_number",
    "participant.borrower.income[13].amount", "deal..amount",
])
def test_reject_invalid_paths(path):
    with pytest.raises(ValueError):
        parse_path(path)
```

Add registry tests for duplicate paths, unknown types/sources, Hungarian labels, and concrete expansion of participant/property/related templates.

- [ ] **Step 2: Run tests to verify RED**

Run `python3 -m pytest -q tests/test_canonical_registry.py`. Expected: import failure because `canonical` does not exist.

- [ ] **Step 3: Implement the parser and registry**

Use explicit regular expressions, positive one-based indexes, roles `borrower/co_borrower/guarantor/mortgagor/usufructuary`, maximum participant index 3, property index 9, related index 12. Do not accept arbitrary attributes or silent normalization.

`fields.json` starts with version, limits and field definitions. A field definition has:

```json
{
  "path": "participant.{role}.name",
  "label": "Teljes név",
  "type": "string",
  "source": "participant.name",
  "repeat": "participant"
}
```

Supported repeat modes are `none`, `participant`, `property`, `income`, `existing_loan`. Registry expansion emits base first-instance paths and explicit indexes required by the approved grammar; it validates all expanded paths with `parse_path`.

- [ ] **Step 4: Run GREEN and regression**

Run `python3 -m pytest -q tests/test_canonical_registry.py tests/test_canonical_model.py`. Expected: new tests pass and existing model tests remain green.

---

### Task 2: Canonical model extension and scalar resolver

**Files:**
- Modify: `src/models/canonical_model.py`
- Create: `src/canonical/formatting.py`
- Create: `src/canonical/resolver.py`
- Create: `tests/test_canonical_resolver.py`

**Interfaces:**
- `ResolutionContext(participant: tuple[str, int] | None = None, property_index: int = 1)`
- `CanonicalResolver.resolve(deal: DealData, path: str, context: ResolutionContext | None = None) -> Any | None`
- `CanonicalResolver.resolve_text(..., fragment: str | None = None) -> str`
- Resolver never accepts legacy canonical aliases.

- [ ] **Step 1: Write failing scalar/context tests**

Construct two co-borrowers, a mortgagor, two properties, deal loan and canonical account values. Assert distinct name/contact ID resolution, explicit `[2]`, base-path context override, property address/parcel number, money/date formatting and missing-value blank text. Include:

```python
def test_context_selects_current_participant_without_pdf_name_heuristics():
    deal = make_deal_with_two_co_borrowers()
    resolver = CanonicalResolver()
    context = ResolutionContext(participant=("co_borrower", 2))
    assert resolver.resolve_text(deal, "participant.co_borrower.name", context) == "Második Társ"
    assert resolver.resolve_text(deal, "participant.co_borrower[1].name", context) == "Első Társ"


def test_legacy_path_is_not_resolved(deal):
    with pytest.raises(ValueError):
        CanonicalResolver().resolve(deal, "Contact.Name")
```

Add semantic tests proving `gender`, `household_size`, `dependents_count`, `children_count`, `id_document_type`, `employer_tax_number` and `credit_commitment` are separate fields.

- [ ] **Step 2: Verify RED**

Run `python3 -m pytest -q tests/test_canonical_resolver.py`. Expected: missing model attributes/resolver.

- [ ] **Step 3: Extend the model**

Add `ParticipantRole.MORTGAGOR`; `Participant.contact_id`, explicit audit scalar fields, `consents: dict[str, bool]`, `extra: dict[str, Any]`; `Property.property_id`, detailed type/condition/heating/lift/insurance and `extra`; `DealData.extra`. Preserve existing defaults and old mock JSON deserialization. Add `IncomeEntry` and `ExistingLoan` models now for Task 3, and `Participant.incomes`/`existing_loans` default-empty lists.

Do not put Salesforce API names in the resolver or registry. Registry sources are model paths such as `participant.name`, `participant.address.city`, `property.parcel_number`, `loan.loan_amount`, `extra.<canonical_name>`.

- [ ] **Step 4: Implement scalar resolver/formatting**

Resolve only registry-validated paths. Base participant/property selects the first active instance unless a matching context override exists; explicit index always wins. Inactive participants are skipped consistently with `DealData.active_participants`. `resolve_text` returns `""` for `None`, applies money/date/boolean formatting centrally, and supports `year/month/day/digit:N` fragments without mutating the canonical path.

- [ ] **Step 5: Run GREEN and existing model/normalizer tests**

Run `python3 -m pytest -q tests/test_canonical_registry.py tests/test_canonical_resolver.py tests/test_canonical_model.py tests/test_normalizer.py`. Expected: all pass.

---

### Task 3: Income and existing-loan rows

**Files:**
- Modify: `src/canonical/fields.json`
- Modify: `src/canonical/resolver.py`
- Modify: `tests/test_canonical_resolver.py`

**Interfaces:**
- `participant.<role>.income[N].<field>` reads `Participant.incomes[N-1]`.
- `participant.<role>.existing_loan[N].<field>` reads `Participant.existing_loans[N-1]`.
- Supported income fields: `income_type`, `amount`, `currency`, `destination`, `bank_name`, `account_number`, `sequence`.
- Supported existing-loan fields: `loan_type`, `bank_name`, `account_number`, `original_amount`, `outstanding_amount`, `monthly_payment`, `currency`, `refinance`, `sequence`.

- [ ] **Step 1: Add failing collection tests**

Use a borrower with twelve incomes and a co-borrower with two existing loans. Assert exact row boundaries, stable order, currency/money formatting, `None` for rows outside the available list, and no fallback from one participant's rows to another. Verify `income_bearing=False` causes income paths to resolve blank through a dedicated resolver policy while non-income fields remain populated.

- [ ] **Step 2: Verify RED, then implement**

Run the focused tests and confirm missing registry paths. Add registry definitions and collection navigation. Do not synthesize twelve rows or reuse the requested-loan `LoanDetails` as an existing loan. Income exclusion applies only to income values, not identity or existing-loan data.

- [ ] **Step 3: Run GREEN**

Run `python3 -m pytest -q tests/test_canonical_registry.py tests/test_canonical_resolver.py`. Expected: all pass, including twelve-row coverage.

---

### Task 4: Exhaustive migration translation table and registry coverage

**Files:**
- Create: `scripts/canonical_migration_map.py`
- Create: `scripts/build_canonical_registry.py`
- Modify: `src/canonical/fields.json`
- Create: `tests/test_canonical_migration_map.py`

**Interfaces:**
- `Translation` is a frozen dataclass with `path: str`, `fragment: str | None = None`, `product_gate: str | None = None`.
- `translate_legacy_path(path: str, *, label: str = "", pdf_field_name: str = "") -> Translation` raises `ValueError` on unresolved entries.
- `build_registry() -> dict` returns the complete versioned registry document.
- This module is used only by offline migration/tests, never by production runtime.

- [ ] **Step 1: Inventory and write failing translation tests**

Read the current static `CANONICAL_FIELDS` and every non-null canonical in `src/mapping/*.json`, including character groups and points parameters. Write tests that every unique legacy entry has an explicit translation, every translated path parses and exists in the registry, duplicates do not create conflicting definitions, and a second registry build is byte-identical.

Include exact semantic cases:

```python
assert translate_legacy_path("Lead.Ingatlan_megjegyzes__c").path == "property.parcel_number"
assert translate_legacy_path("Lead.Tervezett_onero__c").path == "deal.down_payment"
assert translate_legacy_path("Contact.Nem__c").path == "participant.borrower.gender"
assert translate_legacy_path("Contact.Name-társ").path == "participant.co_borrower.name"
assert translate_legacy_path("Contact.Date_of_notification_for_residence__c#nap") == Translation(
    path="participant.borrower.residence_notification_date", fragment="day"
)
assert translate_legacy_path("Lead.Ingatlan_irsz__c_2").path == "property[2].address.zip_code"
```

For `#nap`, assert migration metadata uses `fragment="day"` separately in the later mapping migration; the canonical result must not retain a suffix. `#count` must map according to the actual PDF label to `children_count` or `dependents_count`, never to household size by coincidence.

- [ ] **Step 2: Verify RED and build the explicit map**

Run `python3 -m pytest -q tests/test_canonical_migration_map.py`. Expected: translation module missing. Implement explicit semantic mappings for Contact/Lead/Opportunity/Account/loan namespaces, role suffixes, property indexes and product/date gates. Do not use global string replacement or blindly turn every Lead field into an Opportunity field.

Add the user's new org-audit concepts even if absent from the old catalog: gender, personal number, document type, household/children counts, KHR/NAV/AML/consents, employer tax number/company form, property condition/heating/year/lift/insurance, income rows, existing loans, discount action and refinance accounts.

- [ ] **Step 3: Generate and validate the complete registry**

`build_canonical_registry.py` merges curated Genie definitions and translated legacy definitions, rejects unresolved/conflicting entries, and writes formatted `fields.json` only when content differs. Run it locally; no Salesforce access. Ensure the recognizer is not imported by production registry code.

- [ ] **Step 4: Run core and full regressions**

Run:

```bash
python3 -m pytest -q tests/test_canonical_registry.py tests/test_canonical_resolver.py tests/test_canonical_migration_map.py
python3 -m pytest -q
```

Expected: core coverage proves zero unresolved legacy paths and full existing suite remains green. Report counts and any skipped tests exactly.

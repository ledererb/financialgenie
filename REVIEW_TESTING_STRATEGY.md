# FinancialGenie — Testing Strategy

Companion to `REVIEW_EDGE_CASES.md`. Goal: a test pyramid that catches the silent-corruption families (§3, B1) and the canonical-drift families (§1, §4) before they reach a real customer form. Every test pattern below is keyed to a specific finding ID from the edge-case report and is designed to run **without Salesforce and without the Anthropic API**.

---

## 0. Current state (baseline)

`pytest tests/ -v` on `f66ec7b`:

| Env | Result |
|---|---|
| `ANTHROPIC_API_KEY` **unset** (recommended CI state) | **52 passed, 0 skipped** in ~3 s |
| `ANTHROPIC_API_KEY` **set** (typical dev machine) | 5 tests **hang** making real Claude API calls |

Coverage gaps:
- **No test reads back a filled PDF and asserts on field values.** The one content test (`test_fill_fields_recursive_handles_kids_without_t`) asserts on a single contrived field name. Silent-corruption bugs (3.2, 4.3) are invisible to the suite.
- **No test for `CompletenessChecker`.** Bug B2 went unnoticed.
- **No test for `_prepare_field_data` with multi-borrower / multi-property deals.** Bugs 3.1 and 3.2 went unnoticed.
- **No test for the address parser.** Bug 6 went unnoticed.
- **No test that `get_deal` with an unknown ID returns `None`.** Bug B1 went unnoticed.
- **No test for money formatting with `0`, `None`, negatives.** Bug 5 went unnoticed.
- **No test for `_participant_to_dict` ↔ `Participant.model_fields` parity.** Bug 4.1 went unnoticed.

The 52 passing tests verify happy-path mechanics only. They are necessary but nowhere near sufficient.

---

## 1. Test hermeticity (do this first — fixes B3)

### 1.1 Force the recognizer into heuristic mode in every test

Add to `tests/conftest.py`:
```python
import os
import pytest

@pytest.fixture(autouse=True)
def _no_anthropic_key(monkeypatch):
    """Every test runs in heuristic mode — no live API calls, ever."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
```
This single fixture converts the 5 currently-hanging tests into fast heuristic-mode tests and guarantees CI hermeticity. Add a CI guard:
```yaml
# .github/workflows/ci.yml
- name: Assert no API keys in CI env
  run: |
    [ -z "$ANTHROPIC_API_KEY" ] || (echo "ANTHROPIC_API_KEY must not be set in CI" && exit 1)
```

### 1.2 Mark any test that *intentionally* exercises the live API
```python
@pytest.mark.live_api
def test_recognize_with_real_claude(): ...
```
And in `pytest.ini`:
```ini
[pytest]
addopts = -m "not live_api"
```

---

## 2. Unit tests — pure-Python, no PDF, no I/O

### 2.1 `_fmt_money` parametric tests (validates Fix D / finding 5)

`tests/test_money_format.py`:
```python
import pytest
from src.main import _fmt_money  # after extracting to module level

@pytest.mark.parametrize("value, expected", [
    (None,              ""),
    (0,                 "0"),          # THE bug
    (492000,            "492 000"),
    (-50000,            "-50 000"),    # sign-preserving
    (492000.49,         "492 000"),    # float rounds
    ("492000",          "492 000"),    # numeric string
    ("abc",             ""),           # never raises
    ([],                ""),           # never raises
])
def test_fmt_money(value, expected):
    assert _fmt_money(value) == expected
```

### 2.2 `_participant_to_dict` ↔ model parity (validates Fix C / finding 4.1)

```python
from src.models.canonical_model import Participant
from src.main import FormFillerPipeline

def test_participant_dict_only_references_model_fields():
    """Every Contact.* key in _participant_to_dict must map to a real
    Participant field — no getattr-with-default that hides drift."""
    pipe = FormFillerPipeline()
    p = Participant(role="adós", name="X")
    d = pipe._participant_to_dict(p)
    # These keys are explicitly allowed to be derived (date formatting etc.):
    derived = {"Contact.Birthdate", "Contact.Birthdate_year",
               "Contact.Birthdate_month", "Contact.Birthdate_day",
               "Contact.Relation__c", "Contact.Average_monthly_net_income__c",
               "Contact.MobilePhone"}
    model_attrs = set(Participant.model_fields.keys())
    for key in d:
        if key in derived:
            continue
        # Map Contact.X__c -> x; check the source field exists
        attr = key.split(".", 1)[1].rstrip("_c").rstrip("_")
        # crude but catches the 5 ghost fields:
        assert _resolve_to_model_attr(key) in model_attrs, \
            f"{key} in participant dict has no backing model field"
```
A simpler version: assert that `Contact.Citizenship__c` / `Marital_Status__c` / `Highest_Educational_Qualification__c` / `Income_type__c` / `Dependents_count__c` are **removed** (or that the model gains those fields).

### 2.3 Address parser property tests (validates Fix G / finding 6)

```python
@pytest.mark.parametrize("raw, expected", [
    ("1052 Budapest, Váci utca 10",
        dict(zip_code="1052", city="Budapest", street="Váci utca", house_number="10")),
    ("Budapest",
        dict(zip_code="",     city="Budapest", street="",          house_number="")),
    ("Budapest VII. kerület, Erzsébet krt. 7",
        dict(zip_code="",     city="Budapest", street="Erzsébet krt.", house_number="7")),
    ("Bécsi út 145",                                      # street-only
        dict(zip_code="",     city="",        street="Bécsi út",   house_number="145")),
    ("",                                                  # empty
        dict(zip_code="",     city="",        street="",          house_number="")),
    ("Vienna, Austria",                                   # foreign
        dict(zip_code="",     city="Vienna",  street="",          house_number="")),
])
def test_parse_address(raw, expected):
    sf = SalesforceClient(mock_mode=True, mock_data_dir=Path("samples/dummy_data"))
    out = sf._parse_address_string(raw)
    for k, v in expected.items():
        assert out.get(k) == v, f"{k}: got {out.get(k)!r}, want {v!r}"
    # Also assert the shape is consistent (no KeyError on floor/door):
    assert "floor" in out and "door" in out
```

### 2.4 `get_deal` mock correctness (validates Fix F / finding B1)

```python
def test_get_deal_unknown_id_returns_none():
    sf = SalesforceClient(mock_mode=True, mock_data_dir=Path("samples/dummy_data"))
    assert sf.get_deal("DOES_NOT_EXIST") is None

def test_get_deal_empty_id_returns_none():
    sf = SalesforceClient(mock_mode=True, mock_data_dir=Path("samples/dummy_data"))
    assert sf.get_deal("") is None

def test_get_deal_known_id_returns_that_deal():
    sf = SalesforceClient(mock_mode=True, mock_data_dir=Path("samples/dummy_data"))
    deal = sf.get_deal("SF-970630")
    assert deal is not None
    assert deal["deal_id"] == "SF-970630"
```

### 2.5 `CompletenessChecker` round-trip (validates Fix / finding B2)

```python
from src.engine.completeness_checker import CompletenessChecker, CompletenessStatus

def test_completeness_checker_understands_its_required_fields():
    """required_fields must use a vocabulary the checker understands."""
    deal = _build_minimal_complete_deal()
    checker = CompletenessChecker()
    # Use the canonical vocabulary the checker actually supports:
    report = checker.check(deal, ["loan.loan_amount",
                                  "participant.*.name",
                                  "property.*.parcel_number"])
    assert report.status == CompletenessStatus.COMPLETE
    assert report.blocking_issues == []

def test_pipeline_completeness_does_not_always_block():
    """main.py _check_completeness should not report INCOMPLETE on a complete deal."""
    pipe = FormFillerPipeline(...)
    deal = _build_minimal_complete_deal()
    mapping = MappingConfig(bank_name="OTP", form_name="t", form_type="acroform")
    report = pipe._check_completeness(deal, mapping)
    assert report.status != CompletenessStatus.INCOMPLETE
```

### 2.6 Fragment transform unit tests (validates Fix B / findings 1.1, 2)

```python
@pytest.mark.parametrize("fragment, value, expected", [
    ("year",     "1985.03.15", "1985"),
    ("month",    "1985.03.15", "03"),
    ("day",      "1985.03.15", "15"),
    ("year",     "1985-03-15", "1985"),       # alt separator
    ("year",     "not-a-date", ""),           # never raises
    ("year",     "1985.03",     ""),          # wrong arity
    ("digit:0",  "1052",       "1"),
    ("digit:3",  "1052",       "2"),
    ("digit:9",  "1052",       ""),           # out of range
    (None,       "anything",   "anything"),
])
def test_apply_fragment(fragment, value, expected):
    from src.main import _apply_fragment
    assert _apply_fragment(value, fragment) == expected
```

---

## 3. Field-mapping tests — exercises `_prepare_field_data` end-to-end

### 3.1 Read-back fixture: real AcroForm + real data + assertion on filled values

This is the single most valuable test class to add — it would have caught findings 1.1, 3.1, 3.2, 4.1, 4.3, 5 in one shot. Pattern:

```python
import pikepdf

def _read_back_filled(pdf_path):
    """Return {pdf_field_name: value} for every /Tx field in a filled PDF."""
    out = {}
    with pikepdf.open(pdf_path) as pdf:
        if "/AcroForm" not in pdf.Root:
            return out
        def walk(fields, parent=""):
            for f in fields:
                name = str(f.get("/T", ""))
                full = f"{parent}.{name}" if parent and name else (name or parent)
                if "/Kids" in f:
                    kids = f["/Kids"]
                    nested = [k for k in kids if "/T" in k]
                    if nested:
                        walk(nested, full)
                        continue
                if str(f.get("/FT", "")) == "/Tx":
                    out[full] = str(f.get("/V", ""))
        walk(pdf.Root["/AcroForm"]["/Fields"])
    return out

@pytest.fixture
def filled_pdf(pipeline, acroform_pdf, otp_mapping):
    deal_id = pipeline.sf_client.list_deals()[0]["deal_id"]
    result = pipeline.run_for_deal(deal_id, acroform_pdf, otp_mapping)
    assert result["success"]
    return Path(result["output_path"])
```

### 3.2 Read-back assertions (one per bug)

```python
class TestFilledContent:
    def test_borrower_name_is_filled(self, filled_pdf):
        v = _read_back_filled(filled_pdf)
        assert v.get("borrower_name") not in ("", None)

    def test_birth_date_fragments_are_split(self, filled_pdf):
        """Findings 1.1 — must be split, not the full date."""
        v = _read_back_filled(filled_pdf)
        # If the form has year/month/day boxes, they must each contain
        # ONLY the year/month/day, not the full date.
        for name, validator in [("borrower_birth_year",   lambda s: len(s)==4 and s.isdigit()),
                                ("borrower_birth_month",  lambda s: len(s)==2 and s.isdigit()),
                                ("borrower_birth_day",    lambda s: len(s)==2 and s.isdigit())]:
            if name in v:
                assert validator(v[name]), f"{name}={v[name]!r} is not a fragment"
                assert "." not in v[name]

    def test_no_field_contains_other_participants_data(self, filled_pdf):
        """Finding 3.2 — property2_* must NOT contain property1's address."""
        v = _read_back_filled(filled_pdf)
        if "property2_city" in v and "property_city" in v:
            assert v["property2_city"] != v["property_city"] or v["property2_city"] == ""
```

### 3.3 Multi-scenario parametrized test (catches 3.1, 3.2, B1)

```python
@pytest.mark.parametrize("scenario, expected_fields", [
    ("simple",        ["borrower_name"]),
    ("complex",       ["borrower_name", "coborrower_name"]),
    ("multi_borrower", ["borrower_name", "coborrower_name"]),  # + guarantor?
    ("multi_property", ["property_city"]),                     # + property2_city different
])
def test_pipeline_runs_for_every_scenario(pipeline, acroform_pdf, otp_mapping, scenario, expected_fields):
    sf = SalesforceClient(mock_mode=True, mock_data_dir=Path("samples/dummy_data"))
    raw = json.loads((Path("samples/dummy_data")/f"{scenario}.json").read_text())
    deal = DataNormalizer().normalize_deal(raw)
    fd = pipeline._prepare_field_data(deal, otp_mapping)
    for f in expected_fields:
        assert f in fd and fd[f], f"{f} not filled for scenario {scenario}"
```

### 3.4 Mapping-heuristic drift tests

```python
def test_otp_exact_map_does_not_collapse_distinct_fields():
    """Finding 1.1 — no two OTP field names that differ in _év/_hónap/_nap
    suffix should map to the same canonical."""
    from src.ai.field_recognizer import FieldRecognizer
    r = FieldRecognizer()
    # Extract OTP_EXACT_MAP via the same path the heuristic uses:
    fields_in = [{"name": "SZA_IG_szül_év", "type": "/Tx", "page": 1},
                 {"name": "SZA_IG_szül_hónap", "type": "/Tx", "page": 1},
                 {"name": "SZA_IG_szül_nap", "type": "/Tx", "page": 1}]
    mapping = r._heuristic_map_fields(fields_in, Path("x.pdf"), "acroform")
    canonicals = {f.canonical_field for f in mapping.fields if f.canonical_field}
    # Either we have 3 distinct canonicals, OR fragments attached:
    assert len(canonicals) > 1 or all(f.fragment for f in mapping.fields), \
        "year/month/day boxes must not collapse to a single full-date canonical"
```

---

## 4. PDF-filling tests — AcroForm edge cases (validates Fix E / finding B4)

### 4.1 Checkbox with non-`/Yes` export value

Build a fixture PDF with a checkbox whose on-state is `/1`:
```python
def _build_checkbox_pdf(on_state="/1", path="cb.pdf"):
    import pikepdf
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()
    # ... build /AP/N with both /Off and /1 appearance streams ...
    pdf.save(path)

def test_checkbox_with_non_yes_export_value(tmp_path):
    pdf_path = tmp_path / "cb.pdf"
    _build_checkbox_pdf("/1", pdf_path)
    out = tmp_path / "out.pdf"
    filler = AcroFormFiller()
    result = filler.fill(pdf_path, out,
                         field_data={"CB": "igen"},
                         mapping={"CB": "canonical"})
    with pikepdf.open(out) as pdf:
        v = str(pdf.Root.AcroForm.Fields[0].V)
    assert v == "/1", f"expected /1, got {v}"
```

### 4.2 Multi-page field hierarchy (`/Kids` without `/T`)

Already covered by `test_fill_fields_recursive_handles_kids_without_t` — keep it.

### 4.3 Comb-text centering flag

```python
def test_comb_text_gets_centered(tmp_path):
    # Build a /Tx field with /Ff bit 25 set, fill it, assert /Q == 1.
    ...
```

---

## 5. Normalization tests — fill the gaps

Existing `tests/test_normalizer.py` covers happy-path canonical JSON and basic dates. Add:

```python
def test_normalize_property_missing_address_keeps_property():  # not None
    """Verify behaviour: address becomes empty Address, property stays."""
    ...

def test_normalize_participant_role_case_insensitive():
    for r in ["Adós", "ADÓS", "adós", "Ados"]:
        p = DataNormalizer()._normalize_participant({"role": r, "name": "X"})
        assert p is not None
        assert p.role == ParticipantRole.BORROWER

def test_normalize_participant_unknown_role_drops():
    p = DataNormalizer()._normalize_participant({"role": "wizard", "name": "X"})
    assert p is None

def test_normalize_participant_no_name_drops():
    p = DataNormalizer()._normalize_participant({"role": "adós", "name": ""})
    assert p is None

@pytest.mark.parametrize("raw, expected", [
    ("2026-03-15",            date(2026, 3, 15)),
    ("2026.03.15",            date(2026, 3, 15)),
    ("15.03.2026",            date(2026, 3, 15)),
    ("2026-03-15T10:00:00",   date(2026, 3, 15)),
    ("2026-03-15T10:00:00Z",  date(2026, 3, 15)),
    ("not-a-date",            None),
    ("",                       None),
    (None,                     None),
])
def test_parse_date(raw, expected):
    assert DataNormalizer()._parse_date(raw) == expected
```

---

## 6. CompletenessChecker tests (currently zero coverage)

```python
class TestCompleteness:
    def _deal(self, **overrides):
        # build a minimal but complete DealData
        ...

    def test_complete_deal_passs(self):
        report = CompletenessChecker().check(self._deal(),
            ["loan.loan_amount", "participant.*.name", "property.*.address.city"])
        assert report.status == CompletenessStatus.COMPLETE

    def test_missing_required_blocks(self):
        deal = self._deal()
        deal.loan.loan_amount = None  # but it's required on the model — use a different field
        # ... actually use a field that can be None, like participant.*.tax_id

    def test_structural_no_borrower(self):
        deal = self._deal()
        deal.participants[0].role = ParticipantRole.GUARANTOR
        report = CompletenessChecker().check(deal, [])
        assert any("adós" in i.message for i in report.warnings)

    def test_suspicious_short_name(self):
        deal = self._deal()
        deal.participants[0].name = "A"
        report = CompletenessChecker(run_suspicious_checks=True).check(deal, [])
        assert any(i.issue_type.value == "suspicious" for i in report.warnings)
```

---

## 7. DocumentAssembler tests — fill the gaps

Existing tests cover `_build_page_plan` math. Add:

```python
def test_assemble_warns_on_truncated_master(tmp_path):
    """Finding B8 — out-of-range pages must not silently shrink the output."""
    short_master = tmp_path / "short.pdf"
    _build_pdf_with_n_pages(short_master, 5)
    assembler = DocumentAssembler()
    out = tmp_path / "out.pdf"
    with pytest.warns(UserWarning, match="nincs a master-ben"):
        assembler.assemble(short_master, [ProductType.PIACI_HITEL],
                           num_participants=2, num_properties=1, output_path=out)
    # Either the output should not be saved, or its page count should be flagged.
    # At minimum: assert that the result is not silently a 5-page "success".

def test_assemble_duplicated_pages_have_unique_field_names(tmp_path):
    """Finding 3.3 — when 3 participants are requested, the duplicated
    SZA_IG pages must have field names that allow per-participant filling."""
    master = _build_master_with_n_pages(tmp_path, 97)
    out = tmp_path / "out.pdf"
    DocumentAssembler().assemble(master, [ProductType.PIACI_HITEL],
                                 num_participants=3, num_properties=1,
                                 output_path=out)
    with pikepdf.open(out) as pdf:
        field_names = []
        # collect all /T names recursively
        ...
    # Expect either suffixed names or a documented limitation:
    assert len(set(field_names)) == len(field_names), \
        "Duplicated pages have colliding AcroForm field names — per-participant fill is impossible"
```
(This test will **fail today** — it documents bug 3.3 as a regression target.)

---

## 8. Mapping-editor API tests

### 8.1 Concurrency test (validates Fix / finding 7.1)

```python
import threading
from fastapi.testclient import TestClient
from server import app
client = TestClient(app)

def test_concurrent_field_updates_do_not_clobber(tmp_path):
    """Two threads update different fields; both changes must survive."""
    # Setup: upload a PDF with known mapping
    pdf_id = _upload_known_pdf(client, tmp_path)

    # Load current mtime
    r = client.get(f"/api/mapping?pdf_id={pdf_id}")
    mtime = r.json()["_mtime"]

    barrier = threading.Barrier(2)
    results = {}
    def update(field, value):
        barrier.wait()
        results[field] = client.put(
            "/api/mapping/field",
            params={"pdf_id": pdf_id, "field": field},
            json={"canonical_field": value})

    t1 = threading.Thread(target=update, args=("field_a", "Contact.Name"))
    t2 = threading.Thread(target=update, args=("field_b", "Contact.Email"))
    t1.start(); t2.start(); t1.join(); t2.join()

    # Reload and assert both edits survive
    final = client.get(f"/api/mapping?pdf_id={pdf_id}").json()
    fields = {f["pdf_field_name"]: f.get("canonical_field") for f in final["fields"]}
    assert fields.get("field_a") == "Contact.Name"
    assert fields.get("field_b") == "Contact.Email"
```
This test will **fail today** — it documents bug 7.1.

### 8.2 Round-trip tests

```python
def test_save_and_reload_preserves_fill_rule(tmp_path):
    pdf_id = _upload_known_pdf(client, tmp_path)
    # Set a fill_rule via full save:
    mapping = client.get(f"/api/mapping?pdf_id={pdf_id}").json()
    mapping["fields"][0]["fill_rule"] = {"type": "static", "value": "igen"}
    r = client.put(f"/api/mapping?pdf_id={pdf_id}", json=mapping)
    assert r.status_code == 200
    # Reload and verify:
    reloaded = client.get(f"/api/mapping?pdf_id={pdf_id}").json()
    assert reloaded["fields"][0]["fill_rule"] == {"type": "static", "value": "igen"}

def test_canonical_field_validation_rejects_unknown():
    """Finding 7.6."""
    valid = client.get("/api/mapping/canonical-fields").json()["fields"]
    valid_paths = {f["path"] for f in valid}
    # Try to save an unknown canonical via field update:
    pdf_id = _upload_known_pdf(client, tmp_path)
    r = client.put("/api/mapping/field",
                   params={"pdf_id": pdf_id, "field": "field_a"},
                   json={"canonical_field": "Contact.TotallyMadeUp__c"})
    assert r.status_code in (400, 422), "Unknown canonical must be rejected"
```

### 8.3 Filename sanitisation (finding 7.3)

```python
def test_upload_preserves_unicode_filename(tmp_path):
    """Upload a Hungarian-named PDF; the saved filename must remain readable."""
    ...
def test_upload_distinct_filenames_do_not_collide(tmp_path):
    """my form.pdf and myform.pdf must not overwrite each other."""
    ...
```

---

## 9. Integration testing — full pipeline with read-back

```python
class TestPipelineReadBack:
    """One test per scenario in samples/dummy_data/, all asserting on
    what actually ended up in the filled PDF."""

    @pytest.fixture params:
        ("simple",        1, 1, []),
        ("complex",       2, 1, []),
        ("multi_borrower", 2, 1, ["guarantor_name_absent"]),  # document 3.1
        ("multi_property", 1, 2, []),
        ("missing_data",  1, 1, ["expected_missing"]),
    ])
    def scenario(request):
        return request.param

    def test_read_back(self, pipeline, acroform_pdf, otp_mapping, scenario):
        name, n_p, n_prop, flags = scenario
        ...
        v = _read_back_filled(out)
        # Assert expected filled fields per scenario
        # Assert no cross-contamination (property2 != property1 etc.)
```

---

## 10. CI configuration without API keys

`pytest.ini`:
```ini
[pytest]
testpaths = tests
addopts = -ra -m "not live_api" --strict-markers
markers =
    live_api: tests that call the real Anthropic API (deselected by default)
```

`conftest.py`:
```python
import pytest

@pytest.fixture(autouse=True)
def _force_heuristic_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SF_USERNAME", raising=False)
    monkeypatch.delenv("SF_PASSWORD", raising=False)
```

`.github/workflows/ci.yml` (sketch):
```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    env:
      ANTHROPIC_API_KEY: ""        # force heuristic
      SF_USERNAME: ""
      SF_PASSWORD: ""
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt -r backend/requirements.txt
      - run: python scripts/generate_sample_pdfs.py    # so PDF tests don't skip
      - run: python scripts/generate_dummy_data.py
      - run: pytest --cov=src --cov-report=xml -m "not live_api"
      - uses: codecov/codecov-action@v4
```

The two `python scripts/...` steps are critical — without them, 7 tests skip and the integration coverage is zero.

---

## 11. Property-based tests (optional but high-value)

Use Hypothesis for the parsers:
```python
from hypothesis import given, strategies as st

@given(st.text(min_size=0, max_size=80))
def test_address_parser_never_raises(s):
    sf._parse_address_string(s)  # must not raise for any string

@given(st.from_regex(r"\d{4} \w+, [\w ]+ \d+", fullmatch=True))
def test_address_parser_well_formed(s):
    out = sf._parse_address_string(s)
    assert out["zip_code"] != ""
    assert out["city"] != ""
    assert out["house_number"] != ""
```

---

## 12. Recommended rollout order

1. **Step 1** (1 hour): add the autouse `_no_anthropic_key` fixture, the `pytest.ini` markers, the `generate_*` scripts to CI. — Hermetic suite, fixes B3.
2. **Step 2** (2 hours): add `_read_back_filled` helper + 3.2 / 3.3 / 9 test classes. — Catches 1.1, 3.1, 3.2, 4.1, 4.3, 5 regressions.
3. **Step 3** (1 hour): add 2.1 / 2.3 / 2.4 / 2.5 unit tests. — Catches 5, 6, B1, B2.
4. **Step 4** (1 hour): add 8.1 concurrency test. — Catches 7.1.
5. **Step 5** (ongoing): parametric fill-rule / fragment / suspicious-value tests as each Fix lands.

Total estimated effort to reach a meaningful safety net: **~5 hours of test writing**, all of which can run in CI without secrets. The current suite's 52 tests should stay green throughout (except where intentionally changed by a Fix).

---

## 13. What still needs real-world fixtures

The current `samples/*.pdf` are synthetic (74 fields, friendly names). To catch mapping bugs that only manifest on real OTP forms, the test suite needs at least:
- One **real** OTP SZA_IG page (with `SZA_IG_szül_év/hónap/nap` boxes) as a fixture, with a hand-curated golden mapping.
- One real form with non-`/Yes` checkbox export values (for B4).
- One real form with a 4-digit-box postal code (for §2) — when one is encountered.

Without these, the unit-level tests above catch the structural bugs but cannot verify real-form coverage.

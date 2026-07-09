# FinancialGenie — Code Quality Notes

Companion to `REVIEW_EDGE_CASES.md` and `REVIEW_FIXES.md`. Scope: maintainability, layering, anti-patterns, type safety, error handling, logging, and documentation — *not* the functional bugs (which live in the edge-case report).

> The previous `REVIEW_CODE_QUALITY.md` in this repo was written against an imagined codebase and has been replaced. Ground truth is the code on `f66ec7b`.

Branch telemetry (cloc):
```
src/         ~5,800 LOC Python (8 modules)
backend/     ~1,500 LOC Python (5 modules)
frontend/    ~3,000 LOC TypeScript (10 files)
tests/         ~750 LOC Python (5 files; 52 tests, 0 for completeness-checker / address parser / multi-instance)
```

---

## 1. Architecture & layering

### 1.1 `[med]` Layering inversion: `src/` reaches into `backend/`

**Code:** `src/ai/field_recognizer.py:1196-1231`
```python
def _extract_acroform_fields(self, pdf_path):
    backend_path = Path(__file__).resolve().parent.parent.parent / "backend"
    if str(backend_path) not in sys.path:
        sys.path.insert(0, str(backend_path))
    from pdf_service import pdf_service  # <-- src/ imports from backend/
    ...
```
The stated architecture is "`backend/` is a consumer that reuses `src/`" (`backend/config.py:1-9`). In reality the dependency points the other way at this call site. Consequences:
- `src/ai/field_recognizer.py` cannot be imported in any environment where `backend/` is absent (e.g. a slim Docker image that ships only the pipeline).
- Tests that import `FieldRecognizer` drag in the FastAPI stack transitively.
- Any change to `backend/pdf_service.py` can break the recognizer without a test catching it.

**Recommendation:** extract `extract_acroform_fields` into a neutral helper module under `src/` (e.g. `src/engine/pdf_field_extractor.py`) and have both `backend/pdf_service.py` and `src/ai/field_recognizer.py` import it from there.

### 1.2 `[low]` `backend/server.py` does `from main import FormFillerPipeline` inside request handlers

**Code:** `backend/server.py:483, 575, 668`. The import works only because `backend/config.py:25-28` puts `src/` on `sys.path`. If anyone renames `src/main.py` or moves the pipeline class, every fill/preview endpoint breaks at request time (not at startup), because the import is deferred inside the handler. Either:
- Import at module top with a clear error if it fails, or
- Move the pipeline-binding endpoints into a separate `backend/fill_service.py` that explicitly depends on `src/main.py`.

### 1.3 `[low]` Dead `RoleInstancePlanner`

**Code:** `src/main.py:41` imports `RoleInstancePlanner` and aliases `ParticipantRole as _RRole`, but neither symbol is used anywhere in `main.py`. Meanwhile `src/engine/role_instance_logic.py` (415 LOC, well-structured) implements exactly the role-and-instance routing that `_prepare_field_data` hand-rolls incorrectly (finding 3.1). Wiring the planner in would both delete dead-code imports and fix the bug.

### 1.4 `[low]` `_LOCK` in `mapping_service.py` is too narrow

**Code:** `backend/mapping_service.py:40,95-99`. The lock guards only the file-write inside `save()`. The `load → mutate → save` sequence that every field-level endpoint performs (`backend/server.py:302-333`) is not atomic, so the lock provides false confidence. See finding 7.1 / Fix 7.1 for the per-pdf RLock proposal.

### 1.5 `[low]` Two parallel address-building paths with different output

**Code:** `src/models/canonical_model.py:39-47` (`Address.full_address` property) vs `src/main.py:569-582` (`_address_to_dict`). The two build the address string differently — the property joins `zip city street house_number` and optionally floor/door, while `_address_to_dict` puts street+house_number into `Contact.OtherStreet` and never uses floor/door at all. The result is that `Contact.Permanent_address__c` (which many mappings expect to be the full address) is never populated with the floor/door (finding B9).

---

## 2. Error handling

### 2.1 `[med]` `force_recreate_mapping` swallows mapping-merge exceptions

**Code:** `src/main.py:122-123`
```python
except Exception as e:
    logger.warning(f"⚠️ Hiba a meglévő mapping összefésülésekor: {e}")
```
A failure to merge old+new mapping silently falls through to `mapping.save(mapping_path)` (line 125) which writes whatever partial state `mapping` was left in. The user is told "mapping saved" but the file may be a half-merged mess. Either skip the save on merge failure or surface the error to the caller.

### 2.2 `[med]` `_ai_map_fields` falls back to heuristic without warning the caller

**Code:** `src/ai/field_recognizer.py:1318-1321`
```python
except Exception as e:
    logger.error(f"AI API hiba: {e}")
    logger.info("Visszaesés heurisztikus leképezésre")
    return self._heuristic_map_fields(pdf_fields, pdf_path, form_type)
```
A common case (network blip, rate limit, expired key) produces a mapping that is structurally identical to a "successful AI call" — same return type, same `MappingConfig` shape. The caller (`_resolve_mapping`, `run_for_deal`) cannot tell that the AI was skipped. The saved file looks identical to an AI-mapped file. Consider setting `MappingConfig.notes` to `"FALLBACK: heuristic after AI error: {e}"` and lowering `confidence` of every field by one level.

### 2.3 `[low]` `_extract_json` raises `ValueError` if no JSON found — caller doesn't catch

**Code:** `src/ai/field_recognizer.py:1391-1443`. The function raises `ValueError("Nem található JSON...")` as its terminal branch. The two callers (`_ai_map_fields:1312-1314`, `_ai_recognize_flat_pdf:1362-1364`) call it without a try/except, so a single non-JSON response from Claude propagates up to `_ai_map_fields`'s outer `except Exception` (line 1318), triggering the silent heuristic fallback described in 2.2. A more informative error would distinguish "AI returned non-JSON" from "AI raised an exception".

### 2.4 `[low]` `DocumentAssembler.assemble` saves a truncated PDF without raising

**Code:** `src/engine/document_assembler.py:144-162`. If `_build_page_plan` requests a page index beyond the master PDF, line 148-149 logs a warning and the page is silently skipped. The output PDF is then saved with whatever pages *were* available. The pipeline reports success. See finding B8.

### 2.5 `[low]` `_mupdf_fill_missing` saves incrementally — failure modes are subtle

**Code:** `src/engine/pdf_filler.py:459-466`. `doc.save(output_path, incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)` requires the file to exist and not be open elsewhere. If the prior pikepdf save left the file in a half-flushed state, or if a virus scanner / sync client has a handle on it (Windows especially), the incremental save fails and is logged as a warning — but the result still looks "successful" because `result.success` was already set to `True` at line 682 of `OverlayFiller` / line 225 of `AcroFormFiller`. The fields the fallback was supposed to fill stay empty.

### 2.6 `[med]` `unhandled` exception handler in `server.py` will double-handle `HTTPException`

**Code:** `backend/server.py:757-762`
```python
@app.exception_handler(Exception)
def unhandled(request, exc):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    ...
```
FastAPI already has a default `HTTPException` handler that respects the `headers` attribute of `HTTPException` (e.g. for 405, 415, RFC-compliant Retry-After). Registering a catch-all `Exception` handler that re-implements the HTTPException branch loses the headers and any subclass behaviour. Either remove this catch-all (and accept FastAPI's default 500 for unhandled errors), or use `super()` properly. As written, a 304/429/etc. response from a library that raises `HTTPException` with headers will be silently stripped.

---

## 3. Anti-patterns

### 3.1 `[med]` Magic numbers everywhere

- `src/engine/document_assembler.py:86` — `MASTER_PDF_PAGE_COUNT = 97`. If the OTP master PDF grows to 100 pages, `is_master_pdf` returns True but the section page-ranges in `BASE_SECTIONS` still point to hardcoded page numbers that will be wrong.
- `src/engine/document_assembler.py:37-69` — every section's start/end page is hardcoded; the section map cannot adapt to a re-ordered or re-paginated master.
- `src/integrations/salesforce_client.py:310` — `loan_term_months = 240 # alapértelmezett 20 év` (this one was already refactored into `DEFAULT_LOAN_TERM_MONTHS` in `data_normalizer.py:85`, but the live-mode SF client still has the literal `240`).
- `src/ai/field_recognizer.py:557,614,1255` — `200`, `MAX_FIELDS_PER_BATCH = 60`, `max_images = 20`. The first two have names; the third is inline. All three interact (a 250-field PDF triggers the batch path with batches of 60, sending up to 20 images per call).
- `src/engine/pdf_filler.py:351` — `(1 << 24)` for the Comb flag. The comment says "bit 25" which is correct in PDF-spec 1-indexed terms but confusing alongside the 0-indexed shift.

**Recommendation:** extract to named module-level constants and add a single test that asserts the master-PDF page count matches the highest page in `BASE_SECTIONS`/`PRODUCT_SECTIONS`.

### 3.2 `[med]` Mixing Hungarian and English in identifiers and log messages

The codebase mixes Hungarian (`feldolgozo`, `szereplo`, `Ügylet lekérve`) and English (`FormFillerPipeline`, `DealData`) freely. Neither is wrong, but the same file often uses both — e.g. `src/main.py:298-322` has `borrowers`/`co_borrowers` (English) producing `borrower_data`/`co_borrower_data` which are then routed by the Hungarian suffix `-társ`. Pick one for identifiers (English is strongly recommended for code) and keep Hungarian for user-facing strings only.

### 3.3 `[low]` Repeated `if x else ""` money-formatting pattern (12+ sites)

Already covered as Fix D. The pattern `f"{x:,}".replace(",", " ") if x else ""` is duplicated across `_prepare_field_data` and `_participant_to_dict` — a textbook DRY violation that also hides the `0 → ""` bug.

### 3.4 `[low]` `getattr(p, "field_that_does_not_exist", "")` defensive-programming that hides drift

**Code:** `src/main.py:559-564`. Five lines use `getattr(p, "citizenship"/"marital_status"/"education"/"income_type"/"dependents_count", "")`. The `getattr` default is hiding the fact that these attributes do not exist on the model. If the model ever adds them, no test fires; if it doesn't, the keys are always empty. Direct attribute access would have made finding 4.1 a loud `AttributeError` on the first run. See Fix C.

### 3.5 `[low]` Use of `type: str | None` annotations requires `from __future__ import annotations` on Python <3.10

The codebase targets Python 3.x without specifying a minimum. `src/engine/pdf_filler.py:21` correctly adds `from __future__ import annotations`. `src/main.py` does not — but uses `str | None` only in type comments inside `_eval_fill_rule` (line 488: `def _eval_fill_rule(...) -> str | None:`). On Python 3.9 this would raise `TypeError: unsupported operand type(s) for |`. Pin a minimum Python version (the test environment ran 3.12.3) in `pyproject.toml` / `requirements.txt`.

### 3.6 `[low]` Comment claims parameterisation; code does string interpolation

**Code:** `src/integrations/salesforce_client.py:200-216, 269-273`. The comment says "a simple-salesforce támogatja a `:változó` formát" but the actual code uses f-strings (`f"... WHERE Id IN ({id_list_str})"`). The comment is misleading. (Security is explicitly out of scope for this review, but the maintainability hazard — a future reader trusting the comment — is real.)

---

## 4. Type safety

### 4.1 `[med]` `OverlayFiller.fill` `mapping` parameter has an unsound type

**Code:** `src/engine/pdf_filler.py:626-632`
```python
def fill(self, ..., mapping: dict[str, str] | dict[str, TextPlacement]) -> FillingResult:
```
`dict` is invariant in its key and value types, so `dict[str, str] | dict[str, TextPlacement]` is not equivalent to `dict[str, str | TextPlacement]` — and at runtime Python doesn't enforce either. Inside the function, line 656 does `isinstance(val, TextPlacement)` to discriminate, which is correct, but the type annotation suggests the caller must construct one of two distinct dict types. Cleanest:
```python
mapping: dict[str, TextPlacement | str]
```
plus a runtime check.

### 4.2 `[med]` `MappingConfig.mapping_dict` returns a non-`None`-filtered dict

**Code:** `src/ai/field_recognizer.py:443-450`
```python
@property
def mapping_dict(self):
    return {
        f.pdf_field_name: f.canonical_field
        for f in self.fields
        if f.canonical_field is not None
    }
```
The return type is `dict[str, str]` but the docstring (line 444) and the consumer (`AcroFormFiller.fill` line 605-611) treat it as `dict[str, str]`. Fine — but the property name `mapping_dict` is ambiguous (it's actually "pdf→canonical with Nones stripped"). Rename to `pdf_to_canonical` for clarity.

### 4.3 `[low]` `_heuristic_map_fields` accepts `pdf_field["type"]` as `/Btn` raw PDF token

**Code:** `src/ai/field_recognizer.py:1677`. The `type` field of each `pdf_field` dict is sometimes a raw PDF token (`"/Btn"`, `"/Tx"`) and sometimes the FieldType enum string (`"text"`, `"checkbox"`). Two callers construct these dicts differently (`pdf_service.extract_acroform_fields` returns `/Btn`; `_mock_flat_recognition` produces no `type` key at all). The function silently falls through to `FieldType.TEXT` for unknown values. Either normalise at the boundary or document the expected input.

### 4.4 `[low]` No `pyproject.toml`; mypy/ruff config absent

There's a `requirements.txt` (runtime) and `backend/requirements.txt` (editor), but no `pyproject.toml`, no `[tool.mypy]` / `[tool.ruff]` config, no lint step in the (sketched) CI. The codebase has many `# type: ignore` comments and `Optional[str]` next to `str | None` in the same module — a one-time `mypy --strict` run would surface dozens of drift points (start with the canonical-model-vs-pipeline drift in finding 4.1).

---

## 5. Logging

### 5.1 `[med]` Information is logged at the wrong severity throughout

Examples:
- `src/main.py:88-91` logs "Mapping kényszerített újragenerálása" (a potentially 30-second, money-costing AI call) at `INFO`. Should be `WARNING`.
- `src/main.py:162` logs a mapping-resolution failure at `ERROR` and returns. Good. But `src/main.py:234-237` logs a PDF-fill failure at `ERROR` *and continues* (it returns `result` with `success=False`, but stage 6 — Salesforce writeback — has already happened in the previous `try` block? Actually no, the writeback is in the next try block at line 241 — but the surrounding flow is confusing).
- `src/ai/field_recognizer.py:1318-1321` logs an AI API error at `ERROR`, then logs "Visszaesés heurisztikus leképezésre" at `INFO`. The fallback is significant — it should be `WARNING` with the exception traceback (`logger.warning(..., exc_info=True)`).
- `src/engine/document_assembler.py:149` logs a missing-master-page at `WARNING` (good), but does not abort — see 2.4.

### 5.2 `[low]` `print()` instead of `logger` in CLI entrypoints

**Code:** `src/main.py:785-904` (the `main()` function) and `src/ai/field_recognizer.py:1787-1816` (`print_mapping_summary`). Mixing `print` and `logger.info` in the same module makes log-level filtering useless. Either route CLI output through a separate logger (`logging.getLogger("cli")`) or accept that CLI demos use `print` and mark those functions clearly.

### 5.3 `[low]` Logging uses f-strings (no lazy formatting)

Everywhere: `logger.info(f"📋 ...")`. The Python logging docs recommend `logger.info("📋 ...", arg)` so the format string is not evaluated when the level is filtered out. With ~150 f-string logging calls across the codebase, this is a minor perf concern at DEBUG level, but more importantly it defeats log-aggregator tools that introspect `record.msg` to derive structure. Cosmetics, but worth a single ruff rule (`logging-fstring-interpolation`).

### 5.4 `[low]` Sensitive-looking context in logs

`src/integrations/salesforce_client.py:421-422` logs `"PDF csatolva (SF): {filename} → {deal_id}"` — `deal_id` is an SF Opportunity ID (not sensitive) and `filename` is operator-supplied (mildly sensitive if it contains a customer name). Not a security issue per the review scope, but worth knowing.

---

## 6. Documentation gaps

### 6.1 `[med]` `CANONICAL_FIELDS` is labelled "auto-generated from Salesforce org schema" but is hand-edited

**Code:** `src/ai/field_recognizer.py:57-62`
```python
# Auto-generated from Salesforce org schema (2026-06-29).
# Keys are SF API names in Object.Field format.
```
The comment claims auto-generation but there's no generator script in `scripts/` — the dict is hand-maintained. New SF fields added after 2026-06-29 are silently invisible to the AI prompt. Either commit a `scripts/regenerate_canonical_fields.py` that runs against the SF sandbox and regenerates this dict, or change the comment to "Last manually reviewed: 2026-06-29".

### 6.2 `[med]` No documentation of the canonical-field vocabulary for mapping authors

A mapping author (human curator) needs to know: "what `Contact.*` / `Lead.*` / `Opportunity.*` keys will the pipeline actually produce values for?" There is no such doc. The closest thing is `docs/CANONICAL_FIELDS_HU.md`, which I did not inspect, but the code-level truth (the keys produced by `_participant_to_dict`, `_address_to_dict`, and `_prepare_field_data`) is not surfaced anywhere. Result: mappings get curated against the AI prompt's vocabulary (`Contact.Citizenship__c`) which doesn't match what the pipeline produces — root cause of findings 1.1, 4.1, 4.2, B9.

**Recommendation:** add a `docs/PIPELINE_OUTPUT_KEYS.md` auto-generated from `_participant_to_dict` / `_address_to_dict` / `_prepare_field_data` — every key the pipeline can produce, with its source field and format. Mapping authors curate against this doc, not against the AI prompt.

### 6.3 `[low]` Docstrings reference non-existent parameters

**Code:** `src/engine/completeness_checker.py:20-21` — the module docstring says `participant.*.name` and `property.0.parcel_number` are valid paths, but `main.py:260-270` calls the checker with `Contact.*` paths that don't work. The docstring is correct; the caller is wrong. Either fix the caller (Fix B2) or add a clear error when an unknown root is passed (currently silently mis-routed via the `else` branch at line 321).

### 6.4 `[low]` `docs/MAPPING_EDITOR_SPEC.md` and `docs/MAPPING_EDITOR_STATUS.md` exist but are not cross-referenced from the code

The mapping-editor branch's primary deliverables are these docs, but no code comment in `backend/` points at them. New contributors will read `backend/server.py`'s module docstring (which is good and detailed) and never know the spec doc exists.

### 6.5 `[low]` Two READMEs in `docs/`

`docs/README.md` and the top-level repo (no top-level README at all, actually). A reader entering at the repo root has no signposting. Add a one-page top-level `README.md` pointing at `docs/README.md`, `FEJLESZTESI_BRIEF.md`, and the four `REVIEW_*.md` files.

---

## 7. Configuration & deployment

### 7.1 `[med]` No `pyproject.toml`; two `requirements.txt` files diverge

`requirements.txt` (pipeline) and `backend/requirements.txt` (editor) share several deps (`pydantic`, `pikepdf`, `PyMuPDF`) but with potentially different version pins. There's no lockfile (no `pip-compile` output, no `poetry.lock`, no `uv.lock`). A reproducible install is impossible today. Add a `pyproject.toml` with both pipeline and editor as optional extras:
```toml
[project]
name = "financialgenie"
dependencies = ["pydantic>=2.0", "pikepdf>=8.0.0", "PyMuPDF>=1.23.0", "reportlab>=4.0"]

[project.optional-dependencies]
pipeline = ["simple-salesforce>=1.12.0", "python-dotenv>=1.0.0", "json_repair>=0.28.0"]
editor = ["fastapi>=0.110", "uvicorn[standard]>=0.27", "python-multipart>=0.0.9", "anthropic>=0.30.0"]
dev = ["pytest>=8.0", "pytest-cov>=5.0", "ruff", "mypy"]
```

### 7.2 `[low]` Docker setup exists but `Dockerfile`/`docker-compose.yml` were not inspected

The repo contains `Dockerfile`, `docker-compose.yml`, `docker/nginx.conf`, `.dockerignore`. I did not review these (out of time budget). Worth a separate look — particularly whether the Dockerfile ships both `src/` and `backend/` together (the layering inversion in 1.1 suggests they should be separate images).

### 7.3 `[low]` `START.sh` exists but is not documented

There's a top-level `START.sh` (not read). If it's the canonical "run the editor locally" entrypoint, it should be referenced from a README.

---

## 8. Frontend (TypeScript / React) — quick scan

I did not do a deep frontend review (the task focuses on Python functionality). A skim of `frontend/src/` reveals:

### 8.1 `[med]` API client uses string-concatenated URLs with pdf_id

`frontend/src/api/client.ts` (268 LOC) builds query-string URLs by hand. With pdf_ids that contain slashes and non-ASCII (`otp/Piaci hitel/Igenylesi_....pdf`), every URL construction site needs `encodeURIComponent`. The backend notes (`backend/server.py:7-13`) explicitly call out this fragility. Verify the client encodes consistently; consider a single `buildUrl(endpoint, params)` helper.

### 8.2 `[low]` `PageEditor.tsx` is 1,188 LOC

A single React component file over 1k lines is a maintainability smell. Likely holds the field-rendering, group-rendering, drag-and-drop, and zoom logic in one place. Worth splitting into smaller components.

### 8.3 `[low]` `store.ts` exists but wasn't reviewed

The frontend has a `store.ts` (presumably Zustand/Redux/Jotai). The HEAD commit's "stale unmapped count" fix suggests state-management bugs are a known category — a deeper review of the store's selectors and the relationship between PDF-fields state and mapping state would be valuable.

---

## 9. Quick wins (high value, low effort)

In approximate order of impact-per-hour:

1. **Add `tests/conftest.py` with the autouse offline fixture** (Fix B3, 10 min) — turns 5 hanging tests into passing tests, makes CI hermetic.
2. **Apply Fix F** (5 min) — one-line fix to `get_deal`, prevents wrong-customer-data scenarios in mock mode.
3. **Extract `_fmt_money` and replace 12+ duplication sites** (Fix D, 30 min) — fixes the `0 → ""` bug.
4. **Add `test_no_two_distinct_pdf_fields_share_canonical`** (15 min) — guards against mapping drift in the curated fixtures.
5. **Move `extract_acroform_fields` from `backend/pdf_service.py` into `src/`** (1 hr) — fixes the layering inversion and makes the recognizer independently testable.
6. **Generate sample PDFs and dummy data in CI** (10 min in workflow yaml) — turns 7 skipped tests into 7 passing tests.
7. **Replace `print` calls in `main()` / `print_mapping_summary` with `logging.getLogger("cli")`** (20 min) — clean log filtering.
8. **Add `ruff` and `mypy --strict` to CI** (30 min) — surfaces a pile of drift on first run; addresses several findings in §4 at once.
9. **Delete `temp_check.py`** (`backend/temp_check.py`, 11 LOC) — clearly a scratch file that should not be in `backend/`.

---

## 10. Strengths (to be preserved)

The review is critical because the brief asked for brutality, but the codebase has real strengths worth keeping in mind:

- **Clean separation** of AcroForm vs Overlay fillers behind a `BaseFiller` ABC — easy to add a third filler type.
- **`MappingConfig` round-trips cleanly** to/from JSON with optional fields added backward-compatibly (`checkbox_group`, `fill_rule`).
- **Optimistic concurrency on the full-save endpoint** (`mapping_service.save` with `original_mtime`) is the right pattern — it just needs to be extended to the field-level endpoints (Fix 7.1).
- **`DocumentAssembler._build_page_plan` is fully tested** (10 tests covering 8 scenarios) — one of the few modules with thorough coverage.
- **`FieldRecognizer` degrades gracefully** from AI → batch-AI → heuristic → mock — the failure cascade is well-structured even if the silent fallback (2.2) is a hazard.
- **Per-page-image annotation with numbered red boxes** (`_ai_batch_recognize` line 642-670) is a clever way to ground Claude's field-name recognition in visual context.
- **The role-instance planning module** (`src/engine/role_instance_logic.py`) is well-designed and well-tested in isolation — it's a shame the pipeline doesn't use it (1.3).
- **Hungarian documentation is thorough** — `FEJLESZTESI_BRIEF.md`, `docs/*.md`. The team has clearly invested in spec writing.

The bones are good. The connective tissue between well-designed modules is where the bugs live.

---

## 11. Suggested next-review focus areas

If a follow-up review is planned, the highest-yield areas to look at next are:

1. **The frontend in depth** (`frontend/src/components/PageEditor.tsx`, `MappingStudio.tsx`, `store.ts`) — likely holds the other half of the "stale state" bugs the HEAD commit started fixing.
2. **The real OTP master PDF** (`docs/Igenylesi_dokumentumok_OTP_Jelzaloghitelek_es_tamogatasok_20260330_v5.pdf`) — does the actual production mapping match what `_participant_to_dict` produces? Run the read-back test from §3 of `REVIEW_TESTING_STRATEGY.md` against this PDF.
3. **The Docker deployment** (`Dockerfile`, `docker-compose.yml`) — does the shipped image preserve the `src/ ↔ backend/` sys.path manipulations, or does it silently break the recognizer?
4. **`scripts/generate_dummy_data.py` and `scripts/test_otp_fill.py`** — these were not reviewed but appear to encode assumptions about the data shape; worth verifying they don't paper over the bugs in §3 and §4.

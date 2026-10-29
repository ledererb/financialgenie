# P0 Security and PDF Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the confirmed authentication/data-exposure issues and repair the confirmed PDF/fragment defects without changing the Salesforce org.

**Architecture:** Keep the existing FastAPI middleware and React API client, but make authentication mandatory by default, use authenticated blob downloads, and add focused regression tests before each fix. This plan deliberately excludes the canonical migration.

**Tech Stack:** FastAPI, pytest, PyMuPDF, React/TypeScript, Vite.

## Global Constraints

- No Salesforce mutation; only local code and tests.
- No automatic legal declaration values.
- No git commits unless the user explicitly requests them.
- Tests must use temporary SQLite databases and must not modify `catalog/catalog.db`.
- Existing workspace modifications and untracked PDFs must not be overwritten.

---

### Task 1: Mandatory authentication and minimal health response

**Files:**
- Modify: `backend/server.py:41-50,99-165,325-358,877-884`
- Test: `tests/test_fixes_backend.py`

**Interfaces:**
- Produces: public endpoints are only `/api/health` and `/api/auth/login`; health returns `{"ok": true}`.
- Produces: `_validate_canonical_field()` handles suffix validation without `NameError`.

- [ ] **Step 1: Write failing security tests**

Add tests using a temporary `AuthService` and real tokens:

```python
def test_health_is_minimal(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_testclient_user_agent_does_not_bypass_auth(client):
    response = client.get(
        "/api/catalog",
        headers={"User-Agent": "testclient"},
    )
    assert response.status_code == 401


def test_canonical_suffix_validation_does_not_raise_name_error():
    from server import _validate_canonical_field
    _validate_canonical_field("Contact.Birthdate_2")
```

Add a fixture which replaces `server.auth_service` with `AuthService(db_path=tmp_path / "users.db")`; convert the existing role-middleware test to use it instead of the production database.

- [ ] **Step 2: Run the tests and verify the failures**

Run:

```bash
python3 -m pytest -q tests/test_fixes_backend.py -k 'health_is_minimal or testclient_user_agent or canonical_suffix'
```

Expected: health exposes paths, testclient bypass returns a non-401 response, and suffix validation raises `NameError` before the fix.

- [ ] **Step 3: Implement the minimal backend fix**

Add `import re`; reduce `_PUBLIC_PATHS` to:

```python
_PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/login",
}
```

Delete the `User-Agent: testclient` bypass. Keep Bearer/query-token extraction temporarily for compatibility until Task 2 replaces browser download links. Change health to:

```python
@app.get("/api/health")
def health():
    return {"ok": True}
```

Keep Docker healthcheck unchanged because it only requires HTTP success.

- [ ] **Step 4: Run backend auth tests**

Run:

```bash
python3 -m pytest -q tests/test_fixes_backend.py
```

Expected: all tests pass and no test creates `teszt_member_tmp` in the real catalog DB.

---

### Task 2: Authenticated PDF/ZIP download and preview

**Files:**
- Modify: `backend/server.py` download/package route middleware and `/api/pdf/preview`
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/components/GenerateView.tsx`
- Modify: `frontend/src/components/FillPreviewStep.tsx`
- Modify: `frontend/src/components/PackageGenerationDialog.tsx`
- Modify mapping export caller found under `frontend/src/components/`
- Test: `tests/test_fixes_backend.py`

**Interfaces:**
- Produces: `downloadFile(path: string, filename: string): Promise<void>` in `frontend/src/api/client.ts`.
- Member GET access covers `/api/pdf/download` and `/api/package/download/<name>`; mapping export remains admin-only.

- [ ] **Step 1: Write failing endpoint tests**

Use a temporary output root and real admin/member tokens:

```python
def test_pdf_download_requires_auth(client, tmp_path, monkeypatch):
    import server
    output = tmp_path / "output"
    output.mkdir()
    (output / "private.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    response = client.get(
        "/api/pdf/download",
        params={"path": str(output / "private.pdf")},
    )
    assert response.status_code == 401


def test_mapping_export_requires_admin(client, member_headers):
    response = client.get(
        "/api/mapping/export",
        params={"pdf_id": "fedlap.pdf"},
        headers=member_headers,
    )
    assert response.status_code == 403
```

Add equivalent member-success and package-unauthorized tests using temporary files.

- [ ] **Step 2: Verify the new tests fail**

Run the focused tests. Expected: downloads currently succeed without a token and member package/download authorization is not consistently enforced.

- [ ] **Step 3: Protect backend routes**

Remove download/package/public-preview exceptions. Add a small predicate instead of duplicating prefix checks:

```python
def _member_allowed(method: str, path: str) -> bool:
    if (method, path) in _MEMBER_ALLOWED:
        return True
    if method == "GET" and path == "/api/pdf/download":
        return True
    if method == "GET" and path.startswith("/api/package/download/"):
        return True
    return False
```

Use it in the role middleware. Add `("GET", "/api/pdf/download")` only through the predicate, not public paths. Mapping export is absent from the member allowlist and therefore admin-only.

- [ ] **Step 4: Add authenticated browser downloads**

Implement in `client.ts`:

```typescript
export async function downloadFile(path: string, filename: string): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
```

Make all three download handlers `async`, await `downloadFile(...)`, and surface errors in the existing error state. Replace direct mapping-export links with the same helper. Do not put tokens in URLs.

- [ ] **Step 5: Verify backend and frontend**

Run:

```bash
python3 -m pytest -q tests/test_fixes_backend.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: all pass; generated frontend contains no direct authenticated download-anchor flow.

---

### Task 3: PDF fallback, fragment and page-preview regressions

**Files:**
- Modify: `src/engine/pdf_filler.py:844-932`
- Modify: `src/ai/field_recognizer.py` fragment postprocessor
- Modify: `src/mapping/ertekbecslo_megrendelolap_240920_2_mapping.json`
- Modify: `backend/server.py:1624-1642`
- Test: `tests/test_fragment_c8.py`
- Create: `tests/test_pdf_p0_regressions.py`

**Interfaces:**
- Fragment values are only `"year"`, `"month"`, `"day"`, `"digit:N"` or `None`.
- `/api/pdf/fill/pages` returns `total_pages` after closing the document safely.

- [ ] **Step 1: Add failing unit/regression tests**

Test fragment postprocessing without AI/network:

```python
def test_recognizer_emits_string_date_fragments():
    fields = [_date_field("ev", "év"), _date_field("ho", "hónap"), _date_field("nap", "nap")]
    result = _postprocess_recognized_fields(fields)
    assert [f["fragment"] for f in result] == ["year", "month", "day"]
```

Test all three persisted mapping fragments are strings. Test the closed-document preview with a temporary PDF and an authenticated member. Test `_mupdf_fill_missing` with a real one-field temporary AcroForm PDF, asserting no `NameError` and the field is filled.

- [ ] **Step 2: Run and verify failures**

Run:

```bash
python3 -m pytest -q tests/test_fragment_c8.py tests/test_pdf_p0_regressions.py
```

Expected: object fragments or preview `ValueError`/fallback failure reproduce.

- [ ] **Step 3: Implement minimal fixes**

Replace the undefined `filled_set.add(wname)` with `handled_set.add(wname)` in both branches. In the recognizer, emit the exact fragment strings expected by `_infer_date_fragment`; migrate the three JSON objects to their corresponding string values. In `fill_pdf_pages`, capture `total_pages = len(doc)` before `doc.close()` and return the captured value. Use `try/finally` to close the document on rendering errors.

- [ ] **Step 4: Verify PDF regressions**

Run the same test command and then the complete existing fragment/filler tests:

```bash
python3 -m pytest -q tests/test_fragment_c8.py tests/test_pdf_p0_regressions.py tests/test_integration.py
```

Expected: all selected tests pass; any pre-existing missing-fixture skips are reported explicitly.

---

### Task 4: Explicit Salesforce source and minimal deals DTO

**Files:**
- Modify: `backend/server.py` SF-client factory, `/api/sf/deals`, `/api/pdf/field-values`
- Test: `tests/test_fixes_backend.py`

**Interfaces:**
- Produces: `_make_sf_client()` returns live Salesforce when configured, explicit mock only when `SF_MOCK_MODE=true`, otherwise raises HTTP 503.
- `/api/sf/deals` returns only `Id`, `Name`, `StageName`, `CloseDate`, `AccountName` per deal.

- [ ] **Step 1: Add failing source/DTO tests**

```python
def test_missing_live_salesforce_config_returns_503(client, admin_headers, monkeypatch):
    import server
    monkeypatch.setattr(server, "_get_sf_creds", lambda: None)
    monkeypatch.setenv("SF_MOCK_MODE", "false")
    response = client.get("/api/sf/deals", headers=admin_headers)
    assert response.status_code == 503


def test_deals_response_is_minimal(client, admin_headers, monkeypatch):
    class FakeSF:
        def list_deals(self):
            return [{
                "Id": "0061",
                "Name": "Demo",
                "StageName": "Open",
                "CloseDate": "2026-10-28",
                "AccountName": "Demo Account",
                "AnnualRevenue": 123,
            }]
    monkeypatch.setattr(server, "_make_sf_client", lambda: FakeSF())
    response = client.get("/api/sf/deals", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["deals"] == [{
        "Id": "0061",
        "Name": "Demo",
        "StageName": "Open",
        "CloseDate": "2026-10-28",
        "AccountName": "Demo Account",
    }]
```

Add an explicit mock-success test with `SF_MOCK_MODE=true` and a fake client factory, without live credentials.

- [ ] **Step 2: Verify failures and implement the factory**

Run the tests; expected current behavior silently constructs mock clients and returns raw deal dictionaries. Implement one `_make_sf_client()` used by deals, field-values, generation and validation paths. Do not instantiate live clients in tests. Preserve explicit local mock development with `SF_MOCK_MODE=true`.

- [ ] **Step 3: Pass raw Opportunity consistently to field-values**

Extract the existing raw Opportunity expression used by generation/validation into `_raw_opportunity(deal_raw)` and pass it to `_prepare_field_data(..., raw_opportunity=...)` in `/api/pdf/field-values`. Add a test asserting the same fake raw value appears in preview and generation preparation.

- [ ] **Step 4: Run P0 and full verification**

Run:

```bash
python3 -m pytest -q tests/test_fixes_backend.py tests/test_fragment_c8.py tests/test_pdf_p0_regressions.py
python3 -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Expected: P0 tests pass. Report complete-suite failures/skips with exact output; do not claim the full suite is green unless it is.

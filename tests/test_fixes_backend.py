"""Evidence tests for backend-only fixes (M7 sanitization, L2 add_field).

These tests import ONLY backend modules (server / mapping_service), following
the same sys.path pattern as tests/test_upload_api.py. Mixing backend and src
imports in one module collides on the ``config`` name (backend/config.py vs the
project ``config/`` package), so backend-facing tests live here in isolation.

NB: under pytest the project ``config/`` package would shadow ``backend/config.py``
(both are importable as ``config`` because PROJECT_ROOT is on sys.path via the
``tests/__init__.py`` ancestor). We preload ``backend/config.py`` and register it
as ``config`` in ``sys.modules`` so the backend modules resolve their flat
``from config import ...`` imports. This is test-only plumbing — production code
is untouched.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Make backend/config.py the canonical ``config`` module for this test process.
_BACKEND = PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_spec = importlib.util.spec_from_file_location("config", _BACKEND / "config.py")
_backend_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_config)
sys.modules["config"] = _backend_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from auth_service import AuthService
    monkeypatch.setenv("AUTH_SECRET", "task3-backend-fixture-secret-0123456789abcdef")
    import server

    auth = AuthService(db_path=tmp_path / "users.db")
    monkeypatch.setattr(server, "auth_service", auth)
    with TestClient(server.app) as test_client:
        yield test_client


@pytest.fixture
def admin_headers(client):
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "Admin123!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
def member_headers(client, admin_headers):
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "download_member",
            "password": "MemberPass1!",
            "display_name": "Download Member",
            "role": "member",
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/auth/login",
        json={"username": "download_member", "password": "MemberPass1!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


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


def test_pdf_download_requires_auth(client, tmp_path, monkeypatch):
    import server

    output = tmp_path / "output"
    output.mkdir()
    private_pdf = output / "private.pdf"
    private_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get("/api/pdf/download", params={"path": str(private_pdf)})

    assert response.status_code == 401


def test_pdf_download_allows_member(client, member_headers, tmp_path, monkeypatch):
    import server

    output = tmp_path / "output"
    output.mkdir()
    private_pdf = output / "private.pdf"
    payload = b"%PDF-1.4\n%%EOF"
    private_pdf.write_bytes(payload)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/download",
        params={"path": str(private_pdf)},
        headers=member_headers,
    )

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize("headers_fixture", ["admin_headers", "member_headers"])
def test_pdf_download_rejects_output_sibling(
    client,
    request,
    headers_fixture,
    tmp_path,
    monkeypatch,
):
    import server

    output_private = tmp_path / "output-private"
    output_private.mkdir()
    secret_pdf = output_private / "secret.pdf"
    secret_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/download",
        params={"path": str(secret_pdf)},
        headers=request.getfixturevalue(headers_fixture),
    )

    assert response.status_code == 403


def test_pdf_download_rejects_directory(client, admin_headers, tmp_path, monkeypatch):
    import server

    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/download",
        params={"path": str(output)},
        headers=admin_headers,
    )

    assert response.status_code == 404


def test_zip_download_allows_member(client, member_headers, tmp_path, monkeypatch):
    import server

    output = tmp_path / "output"
    output.mkdir()
    private_zip = output / "private.zip"
    payload = b"PK\x05\x06" + b"\x00" * 18
    private_zip.write_bytes(payload)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/download",
        params={"path": str(private_zip)},
        headers=member_headers,
    )

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/zip"


def test_package_download_requires_auth(client):
    response = client.get("/api/package/download/private.zip")

    assert response.status_code == 401


def test_package_download_member_passes_authorization(client, member_headers):
    response = client.get(
        "/api/package/download/private.zip",
        headers=member_headers,
    )

    # Package ZIPs currently use /api/pdf/download; this legacy namespace has
    # no route, so 404 proves the request passed member authorization.
    assert response.status_code == 404


def test_pdf_preview_requires_auth(client):
    response = client.get("/api/pdf/preview", params={"pdf_id": "private.pdf"})

    assert response.status_code == 401


def test_pdf_preview_requires_admin(client, member_headers):
    response = client.get(
        "/api/pdf/preview",
        params={"pdf_id": "private.pdf"},
        headers=member_headers,
    )

    assert response.status_code == 403


def test_pdf_preview_allows_admin(client, admin_headers, tmp_path, monkeypatch):
    import server

    preview_pdf = tmp_path / "preview.pdf"
    preview_pdf.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(server, "_get_pdf", lambda pdf_id: preview_pdf)
    monkeypatch.setattr(
        server.pdf_service,
        "render_first_pages_preview",
        lambda path, count: [b"png"],
    )

    response = client.get(
        "/api/pdf/preview",
        params={"pdf_id": "preview.pdf"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"pages": ["cG5n"]}


def test_mapping_export_requires_admin(client, member_headers):
    response = client.get(
        "/api/mapping/export",
        params={"pdf_id": "fedlap.pdf"},
        headers=member_headers,
    )

    assert response.status_code == 403


def test_canonical_suffix_validation_does_not_raise_name_error():
    """Cutover Task 4: a validáció suffix-MENTES — a régi „Contact.Birthdate_2”
    forma (multi-property _2 utótag levágással elfogadott volt) ma már
    elavult legacy canonical: 422-t kell dobni, nem NameError-t."""
    from fastapi import HTTPException
    from server import _validate_canonical_field

    with pytest.raises(HTTPException) as excinfo:
        _validate_canonical_field("Contact.Birthdate_2")
    assert excinfo.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# Cutover Task 4 — canonical catalog + validation on the shared Genie registry
# ═══════════════════════════════════════════════════════════════════════════

GENIE_REGISTRY_TYPES = {"string", "integer", "number", "boolean", "date", "money"}


def _registry():
    from src.canonical.registry import canonical_registry

    return canonical_registry


@pytest.fixture
def mapping_store(tmp_path, monkeypatch):
    """Redirect mapping persistence into a tmp dir for full round-trips."""
    import config as backend_config
    import mapping_service as mapping_service_module
    import server

    mapping_dir = tmp_path / "mapping"
    mapping_dir.mkdir()  # tests may write mapping files directly to disk
    monkeypatch.setattr(backend_config, "MAPPING_DIR", mapping_dir)
    monkeypatch.setattr(
        backend_config,
        "mapping_path_for",
        lambda pdf_id: mapping_dir / f"{Path(pdf_id).stem}_mapping.json",
    )
    monkeypatch.setattr(mapping_service_module, "MAPPING_DIR", mapping_dir)
    monkeypatch.setattr(
        server, "_get_pdf", lambda pdf_id: tmp_path / "sample.pdf"
    )
    return mapping_dir


class TestCanonicalCatalogEndpoint:
    def test_canonical_fields_endpoint_returns_registry_entries(self, client, admin_headers):
        response = client.get(
            "/api/mapping/canonical-fields", headers=admin_headers
        )
        assert response.status_code == 200
        fields = response.json()["fields"]
        assert len(fields) > 1000  # the full registry expansion (thousands)

        registry = _registry()
        invalid = [
            f for f in fields if not registry.validate(f["path"])
        ]
        assert invalid == []

        for field in fields:
            assert {"path", "label", "type"}.issubset(field)
            assert field["type"] in GENIE_REGISTRY_TYPES
            assert str(field["label"]).strip()

    def test_canonical_fields_endpoint_has_no_legacy_namespaces(self, client, admin_headers):
        response = client.get(
            "/api/mapping/canonical-fields", headers=admin_headers
        )
        paths = [f["path"] for f in response.json()["fields"]]
        legacy = [
            p for p in paths
            if p.startswith(("Contact.", "Lead.", "Opportunity.", "Account.", "loan."))
            or "-társ" in p or "#" in p or "sf_type" in p
        ]
        assert legacy == []
        # The old Salesforce-typed catalog key is gone; registry types remain.
        entry = next(f for f in response.json()["fields"] if f["path"] == "deal.loan_amount")
        assert entry["type"] == "money"
        assert "sf_type" not in entry

    def test_catalog_includes_expanded_indexed_paths(self, client, admin_headers):
        response = client.get(
            "/api/mapping/canonical-fields", headers=admin_headers
        )
        paths = {f["path"] for f in response.json()["fields"]}
        assert "participant.co_borrower[2].name" in paths
        assert "property[9].address.city" in paths
        assert "participant.borrower.income[12].amount" in paths


class TestSuffixFreeCanonicalValidation:
    @pytest.mark.parametrize(
        "canonical",
        [
            "Contact.Name",
            "Contact.Name-társ",
            "deal.loan_amount#piaci",
            "Contact.Birthdate_2",
            "loan.housing_savings",
            "participant.borrower.not_in_registry",
            "document.signature_date",
        ],
    )
    def test_legacy_or_suffixed_values_are_rejected(self, canonical):
        from fastapi import HTTPException
        from server import _validate_canonical_field

        with pytest.raises(HTTPException) as excinfo:
            _validate_canonical_field(canonical)
        assert excinfo.value.status_code == 422
        assert excinfo.value.detail["error"] == "invalid_canonical_field"
        # Operator-facing: the payload must not embed thousands of valid paths.
        assert "valid_values" not in excinfo.value.detail

    @pytest.mark.parametrize(
        "canonical",
        [
            "deal.loan_amount",
            "participant.borrower.name",
            "participant.co_borrower[1].name",
            "property[2].address.zip_code",
            "participant.borrower.income[1].amount",
            "account.name",
        ],
    )
    def test_registry_paths_pass_untouched(self, canonical):
        from server import _validate_canonical_field

        _validate_canonical_field(canonical)  # must not raise
        _validate_canonical_field(None)  # unmapped is always allowed
        _validate_canonical_field("")


def _mapping_document(canonical="participant.borrower.name", fragment=None):
    return {
        "bank_name": "OTP",
        "form_name": "sample",
        "form_type": "acroform",
        "approved": False,
        "fields": [
            {
                "pdf_field_name": "nev",
                "label": "Név",
                "field_type": "text",
                "canonical_field": canonical,
                "confidence": "manual",
                "page_number": 1,
                "fragment": fragment,
            }
        ],
        "character_groups": [],
        "points": [],
    }


class TestFullSaveAndImportRejectLegacyMappings:
    def test_full_save_rejects_legacy_field_canonical(self, client, admin_headers, mapping_store):
        response = client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(canonical="Contact.Name"),
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "invalid_canonical_field"
        assert "Contact.Name" in detail["message"]

    def test_full_save_rejects_legacy_character_group_canonical(self, client, admin_headers, mapping_store):
        document = _mapping_document()
        document["character_groups"] = [{
            "group_id": "g_zip",
            "group_name": "Irányítószám",
            "field_type": "character_split",
            "canonical_field": "Contact.ZIP__c",
            "member_fields": ["z1", "z2", "z3", "z4"],
            "direction": "left_to_right",
            "separator": "",
        }]
        response = client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=document,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"

    def test_full_save_rejects_legacy_nested_sf_field(self, client, admin_headers, mapping_store):
        document = _mapping_document()
        document["fields"][0]["fill_rule"] = {
            "type": "conditional",
            "sf_field": "Contact.Loan_Purpose__c",
        }
        response = client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=document,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"

    @pytest.mark.parametrize("fragment", ["év", "honap", "digit", "digit:0", "digit:x", 5])
    def test_full_save_rejects_invalid_fragments(self, client, admin_headers, mapping_store, fragment):
        response = client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(fragment=fragment),
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_fragment"

    @pytest.mark.parametrize("fragment", ["year", "month", "day", "digit:1", "digit:12"])
    def test_full_save_accepts_registry_paths_and_valid_fragments(
        self, client, admin_headers, mapping_store, fragment
    ):
        response = client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(fragment=fragment),
        )
        assert response.status_code == 200
        assert (mapping_store / "sample_mapping.json").exists()

    def test_import_rejects_legacy_mapping(self, client, admin_headers, mapping_store):
        document = _mapping_document(canonical="Opportunity.Hitel_sszeg__c")
        response = client.post(
            "/api/mapping/import",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            files={
                "file": (
                    "sample_mapping.json",
                    json.dumps(document).encode("utf-8"),
                    "application/json",
                )
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"


class TestGroupCrudRejectsInvalidCanonicals:
    def test_group_create_rejects_legacy_canonical(self, client, admin_headers, mapping_store):
        response = client.post(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json={
                "group_id": "g_zip",
                "canonical_field": "Contact.ZIP__c",
                "member_fields": ["z1", "z2", "z3", "z4"],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"

    def test_group_create_accepts_registry_canonical(self, client, admin_headers, mapping_store):
        client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(),
        )
        response = client.post(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json={
                "canonical_field": "participant.borrower.address.zip_code",
                "member_fields": ["z1", "z2", "z3", "z4"],
            },
        )
        assert response.status_code == 200
        assert response.json()["canonical_field"] == (
            "participant.borrower.address.zip_code"
        )

    def test_group_update_rejects_legacy_canonical(self, client, admin_headers, mapping_store):
        client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(),
        )
        client.post(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json={
                "group_id": "g_zip",
                "canonical_field": "participant.borrower.address.zip_code",
                "member_fields": ["z1", "z2", "z3", "z4"],
            },
        )
        response = client.put(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf", "group_id": "g_zip"},
            headers=admin_headers,
            json={"canonical_field": "Lead.Ingatlan_irsz__c"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"

    def test_field_update_endpoint_rejects_legacy_canonical(
        self, client, admin_headers, mapping_store
    ):
        client.put(
            "/api/mapping",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json=_mapping_document(),
        )
        response = client.put(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf", "field": "nev"},
            headers=admin_headers,
            json={"canonical_field": "Contact.Name"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"


def test_live_salesforce_config_uses_live_client(monkeypatch):
    import server
    import integrations.salesforce_client as salesforce_module

    credentials = {
        "username": "configured-user",
        "password": "configured-password",
        "security_token": "configured-token",
        "domain": "test",
        "mock_mode": False,
    }
    fake_sf = object()
    calls = []

    def fake_client_factory(**kwargs):
        calls.append(kwargs)
        return fake_sf

    monkeypatch.setattr(server, "_get_sf_creds", lambda: credentials)
    monkeypatch.setenv("SF_MOCK_MODE", "false")
    monkeypatch.setattr(salesforce_module, "SalesforceClient", fake_client_factory)

    factory = getattr(server, "_make_sf_client", None)
    assert callable(factory), "Salesforce construction must use an explicit factory"
    assert factory() is fake_sf
    assert calls == [credentials]


def test_first_call_dotenv_only_mock_mode_is_honored(tmp_path, monkeypatch):
    import server
    import integrations.salesforce_client as salesforce_module

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (tmp_path / ".env").write_text("SF_MOCK_MODE=true\n", encoding="utf-8")
    (config_dir / "settings.py").write_text(
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n"
        "load_dotenv(Path(__file__).parent.parent / '.env', override=True)\n",
        encoding="utf-8",
    )

    fake_sf = object()
    calls = []

    def fake_client_factory(**kwargs):
        calls.append(kwargs)
        return fake_sf

    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("SF_MOCK_MODE", raising=False)
    monkeypatch.setattr(salesforce_module, "SalesforceClient", fake_client_factory)

    try:
        sf_client = server._make_sf_client()
    except Exception as exc:
        pytest.fail(f"First-call .env-only mock selection failed: {type(exc).__name__}: {exc}")

    assert sf_client is fake_sf
    assert calls == [{
        "mock_mode": True,
        "mock_data_dir": tmp_path / "samples" / "dummy_data",
    }]


def test_missing_live_salesforce_config_returns_503(client, admin_headers, monkeypatch):
    import server
    import integrations.salesforce_client as salesforce_module

    class FakeSF:
        def list_deals(self):
            return []

    monkeypatch.setattr(server, "_get_sf_creds", lambda: None)
    monkeypatch.setenv("SF_MOCK_MODE", "false")
    monkeypatch.setattr(salesforce_module, "SalesforceClient", lambda **kwargs: FakeSF())

    response = client.get("/api/sf/deals", headers=admin_headers)

    assert response.status_code == 503


def test_deals_response_is_minimal(client, admin_headers, monkeypatch):
    import server
    import integrations.salesforce_client as salesforce_module

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

    # The fallback patches keep the RED run on the old code path isolated from
    # live credentials too; the implemented endpoint uses the factory seam.
    monkeypatch.setattr(server, "_make_sf_client", lambda: FakeSF(), raising=False)
    monkeypatch.setattr(server, "_get_sf_creds", lambda: None)
    monkeypatch.setattr(salesforce_module, "SalesforceClient", lambda **kwargs: FakeSF())

    response = client.get("/api/sf/deals", headers=admin_headers)

    assert response.status_code == 200
    assert response.json()["deals"] == [{
        "Id": "0061",
        "Name": "Demo",
        "StageName": "Open",
        "CloseDate": "2026-10-28",
        "AccountName": "Demo Account",
    }]


def test_explicit_mock_salesforce_mode_returns_mock_deals(
    client,
    admin_headers,
    monkeypatch,
):
    import server
    import integrations.salesforce_client as salesforce_module

    class FakeSF:
        def list_deals(self):
            return [{"deal_id": "mock-1", "stage": "Open", "AnnualRevenue": 123}]

    calls = []

    def fake_client_factory(**kwargs):
        calls.append(kwargs)
        return FakeSF()

    monkeypatch.setattr(server, "_get_sf_creds", lambda: None)
    monkeypatch.setenv("SF_MOCK_MODE", "true")
    monkeypatch.setattr(salesforce_module, "SalesforceClient", fake_client_factory)

    response = client.get("/api/sf/deals", headers=admin_headers)

    assert response.status_code == 200
    assert calls == [{
        "mock_mode": True,
        "mock_data_dir": server.PROJECT_ROOT / "samples" / "dummy_data",
    }]
    assert response.json()["deals"] == [{
        "Id": "mock-1",
        "Name": None,
        "StageName": "Open",
        "CloseDate": None,
        "AccountName": None,
    }]


@pytest.mark.parametrize(
    ("method", "url", "body", "expected_detail"),
    [
        (
            "POST",
            "/api/pdf/page/1/preview",
            {"pdf_id": "secret.pdf", "deal_id": "0061"},
            "Page preview error",
        ),
        (
            "POST",
            "/api/catalog/generate-package",
            {"bank_id": "bank", "product_id": "product", "deal_id": "0061"},
            "Csomag generálási hiba",
        ),
        (
            "POST",
            "/api/pdf/fill",
            {"pdf_id": "secret.pdf", "deal_id": "0061"},
            "Fill error",
        ),
        (
            "POST",
            "/api/pdf/validate",
            {"pdf_id": "secret.pdf", "deal_id": "0061"},
            "Validation error",
        ),
        (
            "GET",
            "/api/pdf/field-values?pdf_id=secret.pdf",
            None,
            "Field values error",
        ),
        ("GET", "/api/sf/deals", None, "SF error"),
    ],
    ids=["page-preview", "generate-package", "fill", "validate", "field-values", "deals"],
)
def test_sf_outer_errors_use_fixed_public_messages(
    client,
    admin_headers,
    tmp_path,
    monkeypatch,
    method,
    url,
    body,
    expected_detail,
):
    from types import ModuleType

    import server

    secret = "SF_SECRET_SENTINEL_DO_NOT_RETURN"
    fake_main = ModuleType("main")
    fake_main.FormFillerPipeline = object

    def fail_sf_client():
        raise RuntimeError(secret)

    pdf_path = tmp_path / "secret.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setitem(sys.modules, "main", fake_main)
    monkeypatch.setattr(server, "_make_sf_client", fail_sf_client)
    monkeypatch.setattr(server, "_get_pdf", lambda pdf_id: pdf_path)

    response = client.request(method, url, json=body, headers=admin_headers)

    assert response.status_code == 500
    assert secret not in response.text
    assert response.json() == {"detail": expected_detail}


def test_field_values_and_generation_receive_same_raw_opportunity(
    client,
    admin_headers,
    tmp_path,
    monkeypatch,
):
    from types import SimpleNamespace

    import server
    import main as generation_main

    GenerationPipeline = generation_main.FormFillerPipeline
    raw_opportunity = {"Name": "Raw Opportunity", "Amount": 123}
    deal_raw = {"deal_id": "0061", "_opportunity": raw_opportunity}

    class FakeSF:
        _mock_mode = False

        def list_deals(self):
            return [{"Id": "0061"}]

        def get_deal(self, deal_id):
            assert deal_id == "0061"
            return deal_raw

        def attach_pdf(self, *args):
            raise AssertionError("Salesforce mutation is forbidden in this test")

        def update_stage(self, *args):
            raise AssertionError("Salesforce mutation is forbidden in this test")

    class FakeNormalizer:
        def normalize_deal(self, raw):
            assert raw is deal_raw
            return SimpleNamespace(
                deal_id="0061",
                active_participants=[],
                properties=[],
                borrowers=[],
                co_borrowers=[],
            )

    class FakePreviewPipeline:
        def __init__(self):
            self.sf_client = FakeSF()
            self.normalizer = FakeNormalizer()
            self.prepared_raw_opportunities = []

        def _prepare_field_data(
            self,
            deal,
            mapping,
            raw_opportunity=None,
            notices=None,
        ):
            self.prepared_raw_opportunities.append(raw_opportunity)
            return {"Opportunity.Name": (raw_opportunity or {}).get("Name", "")}

    preview_pipeline = FakePreviewPipeline()
    monkeypatch.setattr(
        generation_main,
        "FormFillerPipeline",
        lambda sf_client, output_dir: preview_pipeline,
    )
    monkeypatch.setattr(server, "_make_sf_client", lambda: preview_pipeline.sf_client)
    monkeypatch.setattr(server.mapping_service, "load", lambda pdf_id: {
        "bank_name": "Test",
        "form_name": "Test",
        "form_type": "acroform",
        "fields": [],
    })

    preview_response = client.get(
        "/api/pdf/field-values",
        params={"pdf_id": "raw-opportunity.pdf"},
        headers=admin_headers,
    )

    mapping = object()
    generation_pipeline = GenerationPipeline.__new__(GenerationPipeline)
    generation_pipeline.sf_client = FakeSF()
    generation_pipeline.normalizer = FakeNormalizer()
    generation_pipeline.output_dir = tmp_path
    generation_raw_opportunities = []

    def generation_prepare(
        deal,
        received_mapping,
        participant_override=None,
        raw_opportunity=None,
        notices=None,
        context=None,
    ):
        # Cutover Task 3 (additional requirement 5): run_for_deal now passes
        # the ``context`` kwarg unconditionally — the stub accepts it.
        assert received_mapping is mapping
        generation_raw_opportunities.append(raw_opportunity)
        return {"Opportunity.Name": (raw_opportunity or {}).get("Name", "")}

    complete = SimpleNamespace(
        status=generation_main.CompletenessStatus.COMPLETE,
        blocking_issues=[],
        warnings=[],
    )
    monkeypatch.setattr(generation_pipeline, "_prepare_field_data", generation_prepare)
    monkeypatch.setattr(
        generation_pipeline,
        "_check_completeness",
        lambda deal, received_mapping: complete,
    )

    class FakeDocumentAssembler:
        def is_master_pdf(self, path):
            return False

    monkeypatch.setattr(generation_main, "DocumentAssembler", FakeDocumentAssembler)
    output_path = tmp_path / "generated.pdf"
    output_path.write_bytes(b"%PDF-1.4\n%%EOF")

    def fake_fill_pdf(
        template,
        deal,
        field_data,
        received_mapping,
        instance_fields=None,
        participant_override=None,
    ):
        assert field_data == {"Opportunity.Name": "Raw Opportunity"}
        return output_path, list(field_data), []

    monkeypatch.setattr(generation_pipeline, "_fill_pdf", fake_fill_pdf)
    monkeypatch.setattr(
        generation_pipeline,
        "_fill_legal_declarations",
        lambda path, received_mapping, deal: 0,
    )

    generation_result = generation_pipeline.run_for_deal(
        deal_id="0061",
        template_pdf=tmp_path / "template.pdf",
        mapping_config=mapping,
        upload_to_sf=False,
    )

    assert preview_response.status_code == 200
    assert generation_result["success"] is True, generation_result
    assert preview_pipeline.prepared_raw_opportunities == generation_raw_opportunities == [raw_opportunity]
    assert preview_response.json()["values"] == {"Opportunity.Name": "Raw Opportunity"}


# ── M7: filename sanitization (Hungarian accents + collision) ──────────────
class TestM7SanitizeFilename:
    def test_hungarian_accents_transliterated_not_stripped(self):
        from server import sanitize_filename
        # The bug report: "Pénzügyi átadás.pdf" → "Pnzughtyi_tads.pdf" (stripped).
        # After NFKD: accents fold to ASCII base letters (case preserved).
        out = sanitize_filename("Pénzügyi átadás.pdf")
        assert out == "Penzugyi_atadas.pdf", out

    def test_special_chars_underscored(self):
        from server import sanitize_filename
        out = sanitize_filename("Piaci hitel (2026).pdf")
        assert out == "Piaci_hitel_2026.pdf", out

    def test_collision_safe_suffix(self):
        from server import sanitize_filename
        existing = {"demo.pdf"}
        out = sanitize_filename("demo.pdf", existing=existing)
        assert out == "demo_2.pdf", out

    def test_collision_increments(self):
        from server import sanitize_filename
        existing = {"demo.pdf", "demo_2.pdf", "demo_3.pdf"}
        out = sanitize_filename("demo.pdf", existing=existing)
        assert out == "demo_4.pdf", out

    def test_empty_filename_gets_random_name(self):
        from server import sanitize_filename
        out = sanitize_filename("")
        assert out.endswith(".pdf")
        assert out.startswith("uploaded_")

    def test_double_accent_letters_o_u(self):
        from server import sanitize_filename
        # ő / ű must fold to "o"/"u" (not be dropped) under NFKD.
        out = sanitize_filename("Nagy űzlet ősz.pdf")
        assert "uzlet" in out and "osz" in out, out
        assert "ű" not in out and "ő" not in out


# ── L2: add_field persists fill_rule / checkbox_group ──────────────────────
class TestL2AddFieldPersistsExtras:
    def test_add_field_keeps_fill_rule_and_checkbox_group(self):
        from mapping_service import MappingService

        svc = MappingService()
        mapping = {"fields": [], "character_groups": []}
        record = svc.add_field(mapping, {
            "pdf_field_name": "cb_yes",
            "field_type": "checkbox",
            "canonical_field": None,
            "fill_rule": {"type": "static", "value": "igen"},
            "checkbox_group": {"group_id": "g1", "match_value": "Igen"},
        })
        assert record["fill_rule"] == {"type": "static", "value": "igen"}
        assert record["checkbox_group"] == {"group_id": "g1", "match_value": "Igen"}
        # And the stored mapping carries them too.
        stored = mapping["fields"][0]
        assert stored["fill_rule"] is not None
        assert stored["checkbox_group"] is not None

    def test_add_field_defaults_extras_to_none(self):
        from mapping_service import MappingService

        svc = MappingService()
        mapping = {"fields": []}
        record = svc.add_field(mapping, {"pdf_field_name": "plain"})
        # Fields without rules get None (not missing keys).
        assert record["fill_rule"] is None
        assert record["checkbox_group"] is None


# ── Auth & Admin/Member Role Tests ─────────────────────────────────────────
class TestAuthAndUserRoles:
    def test_auth_service_crud_and_tokens(self, tmp_path):
        from auth_service import AuthService

        db_file = tmp_path / "test_users.db"
        auth = AuthService(db_path=db_file)

        # Default admin seeded
        users = auth.list_users()
        assert len(users) == 1
        assert users[0]["username"] == "admin"
        assert users[0]["role"] == "admin"

        # Authenticate default admin
        admin_user = auth.authenticate("admin", "Admin123!")
        assert admin_user is not None
        assert auth.authenticate("admin", "wrong") is None

        # Create token & verify
        token = auth.create_token(admin_user)
        verified = auth.verify_token(token)
        assert verified is not None
        assert verified["id"] == admin_user["id"]

        # Create member user
        member = auth.create_user("kovacs.anna", "Titok123", "Kovács Anna", "member")
        assert member["role"] == "member"
        assert auth.authenticate("kovacs.anna", "Titok123") is not None

        # Cannot demote or delete the only admin
        with pytest.raises(ValueError):
            auth.update_user(admin_user["id"], role="member")
        with pytest.raises(ValueError):
            auth.delete_user(admin_user["id"], acting_user_id=admin_user["id"])

        # Can delete member
        assert auth.delete_user(member["id"], acting_user_id=admin_user["id"]) is True
        assert len(auth.list_users()) == 1

    def test_server_role_middleware(self, client):
        # Unauthenticated request -> 401
        r_unauth = client.get("/api/catalog")
        assert r_unauth.status_code == 401

        # Admin login
        r_login = client.post("/api/auth/login", json={"username": "admin", "password": "Admin123!"})
        assert r_login.status_code == 200
        admin_token = r_login.json()["token"]
        admin_hdr = {"Authorization": f"Bearer {admin_token}"}

        # Admin can list users and create a member
        r_users = client.get("/api/users", headers=admin_hdr)
        assert r_users.status_code == 200

        r_create = client.post(
            "/api/users",
            headers=admin_hdr,
            json={
                "username": "teszt_member_tmp",
                "password": "MemberPass1!",
                "display_name": "Teszt Member",
                "role": "member",
            },
        )
        assert r_create.status_code == 200
        member_id = r_create.json()["user"]["id"]

        try:
            # Login as member
            r_mlogin = client.post(
                "/api/auth/login",
                json={"username": "teszt_member_tmp", "password": "MemberPass1!"},
            )
            assert r_mlogin.status_code == 200
            member_token = r_mlogin.json()["token"]
            member_hdr = {"Authorization": f"Bearer {member_token}"}

            # Member can read catalog (/api/catalog) and /api/auth/me
            assert client.get("/api/auth/me", headers=member_hdr).status_code == 200
            assert client.get("/api/catalog", headers=member_hdr).status_code == 200

            # Member CANNOT access /api/users or create banks or read/edit mappings -> 403
            assert client.get("/api/users", headers=member_hdr).status_code == 403
            assert client.post("/api/catalog/banks", headers=member_hdr, json={"name": "Tiltott"}).status_code == 403
            assert client.get("/api/mapping?pdf_id=fedlap.pdf", headers=member_hdr).status_code == 403
        finally:
            client.delete(f"/api/users/{member_id}", headers=admin_hdr)


# ── P0: PDF containment and authenticated response regressions ──────────────
def _configure_pdf_roots(tmp_path, monkeypatch):
    import config as backend_config

    roots = [tmp_path / name for name in ("otp", "samples", "documents")]
    for root in roots:
        root.mkdir()
    monkeypatch.setattr(backend_config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(backend_config, "PDF_ROOTS", roots)
    return roots


def _escape_pdf_ids(tmp_path: Path) -> dict[str, str]:
    targets = {
        "absolute": tmp_path / "outside" / "secret.pdf",
        "traversal": tmp_path / "secret.pdf",
        "sibling-prefix": tmp_path / "samples-private" / "secret.pdf",
    }
    for target in targets.values():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"%PDF-1.4\n%%EOF")
    return {
        "absolute": str(targets["absolute"]),
        "traversal": "samples/../secret.pdf",
        "sibling-prefix": str(targets["sibling-prefix"]),
    }


@pytest.mark.parametrize("escape", ["absolute", "traversal", "sibling-prefix"])
def test_resolve_pdf_rejects_outside_approved_roots(tmp_path, monkeypatch, escape):
    import config as backend_config

    _configure_pdf_roots(tmp_path, monkeypatch)
    pdf_id = _escape_pdf_ids(tmp_path)[escape]

    with pytest.raises(PermissionError):
        backend_config.resolve_pdf(pdf_id)


@pytest.mark.parametrize("root_name", ["otp", "samples", "documents"])
@pytest.mark.parametrize("style", ["relative", "absolute", "legacy-absolute", "root-relative"])
def test_resolve_pdf_preserves_valid_root_paths(tmp_path, monkeypatch, root_name, style):
    import config as backend_config

    _configure_pdf_roots(tmp_path, monkeypatch)
    pdf_path = tmp_path / root_name / f"{root_name}-valid.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    pdf_ids = {
        "relative": f"{root_name}/{pdf_path.name}",
        "absolute": str(pdf_path),
        "legacy-absolute": f"/legacy/location/{root_name}/{pdf_path.name}",
        "root-relative": pdf_path.name,
    }

    assert backend_config.resolve_pdf(pdf_ids[style]) == pdf_path.resolve()


@pytest.mark.parametrize("pdf_id", ["samples/missing.pdf", "samples"])
def test_resolve_pdf_missing_and_directory_are_not_found(tmp_path, monkeypatch, pdf_id):
    import config as backend_config

    _configure_pdf_roots(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        backend_config.resolve_pdf(pdf_id)


@pytest.mark.parametrize("endpoint", ["/api/pdf/fill", "/api/pdf/validate"])
@pytest.mark.parametrize("escape", ["absolute", "traversal", "sibling-prefix"])
def test_member_pdf_endpoints_reject_outside_paths_before_salesforce(
    client,
    member_headers,
    tmp_path,
    monkeypatch,
    endpoint,
    escape,
):
    import server

    _configure_pdf_roots(tmp_path, monkeypatch)
    pdf_id = _escape_pdf_ids(tmp_path)[escape]

    def forbidden_salesforce():
        raise AssertionError("Salesforce must not be called for an unsafe PDF path")

    monkeypatch.setattr(server, "_make_sf_client", forbidden_salesforce)
    response = client.post(
        endpoint,
        json={"pdf_id": pdf_id, "deal_id": "006-safe-test", "ai_review": False},
        headers=member_headers,
    )

    assert response.status_code == 403


@pytest.mark.parametrize("endpoint", ["/api/pdf/fill", "/api/pdf/validate"])
@pytest.mark.parametrize("pdf_id", ["samples/missing.pdf", "samples"])
def test_member_pdf_endpoints_return_404_for_missing_or_directory(
    client,
    member_headers,
    tmp_path,
    monkeypatch,
    endpoint,
    pdf_id,
):
    import server

    _configure_pdf_roots(tmp_path, monkeypatch)

    def forbidden_salesforce():
        raise AssertionError("Salesforce must not be called for an unresolved PDF path")

    monkeypatch.setattr(server, "_make_sf_client", forbidden_salesforce)
    response = client.post(
        endpoint,
        json={"pdf_id": pdf_id, "deal_id": "006-safe-test", "ai_review": False},
        headers=member_headers,
    )

    assert response.status_code == 404


def test_query_string_token_does_not_authenticate(client, admin_headers):
    token = admin_headers["Authorization"].split(" ", 1)[1]

    response = client.get("/api/catalog", params={"token": token})

    assert response.status_code == 401


def test_page_image_cache_is_private_and_not_stored(client, admin_headers, tmp_path, monkeypatch):
    import server

    pdf_path = tmp_path / "page.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")
    monkeypatch.setattr(server, "_get_pdf", lambda pdf_id: pdf_path)
    monkeypatch.setattr(
        server.pdf_service,
        "render_page_png",
        lambda path, page_number: b"authenticated-png",
    )

    response = client.get(
        "/api/pdf/page/1/image",
        params={"pdf_id": "samples/page.pdf"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.content == b"authenticated-png"
    assert response.headers["cache-control"] == "private, no-store"


# ──────────────────────────────────────────────────────────────────────────
# Cutover Task 3 review fix (defect 2) — AI-review sf_context from explicit
# canonical paths, with per-path isolation.
# ──────────────────────────────────────────────────────────────────────────


class TestBorrowerSfContext:
    @staticmethod
    def _borrower_deal():
        from datetime import date

        from src.models.canonical_model import (
            Address,
            DealData,
            LoanDetails,
            Participant,
            ParticipantRole,
            Property,
            PropertyType,
        )

        return DealData(
            deal_id="SF-CTX-1",
            loan=LoanDetails(
                loan_amount=30_000_000,
                loan_purpose="lakásvásárlás",
            ),
            participants=[
                Participant(
                    role=ParticipantRole.BORROWER,
                    name="Kovács Anna",
                    tax_id="8123456789",
                    phone="+36301234567",
                    email="kovacs.anna@example.hu",
                    birth_date=date(1985, 5, 12),
                    address=Address(
                        zip_code="1123",
                        city="Budapest",
                        street="Kossuth",
                        house_number="9",
                    ),
                )
            ],
            properties=[
                Property(
                    address=Address(
                        zip_code="1123",
                        city="Budapest",
                        street="Kossuth",
                        house_number="9",
                    ),
                    property_type=PropertyType.APARTMENT,
                )
            ],
        )

    def test_populated_sf_context_for_borrower_deal(self):
        import server

        context = server._borrower_sf_context(self._borrower_deal())

        # Review defect 2: the earlier implementation resolved one
        # UNREGISTERED path (participant.borrower.address.full_address) —
        # resolve_text raised and the blanket except wiped everything, so
        # sf_context was always None. It must now be a populated dict of
        # registry-approved paths.
        assert context is not None
        assert context["participant.borrower.name"] == "Kovács Anna"
        assert context["participant.borrower.tax_id"] == "8123456789"
        assert context["participant.borrower.email"] == (
            "kovacs.anna@example.hu"
        )
        assert context["deal.loan_amount"] == "30 000 000"
        assert context["deal.loan_purpose"] == "lakásvásárlás"
        # The registered composite-address path (source: address.full_address)
        # resolves instead of the unregistered address.full_address path.
        assert "participant.borrower.address.full_address" not in context
        assert "participant.borrower.permanent_address" in context
        assert "1123" in context["participant.borrower.permanent_address"]

    def test_one_invalid_path_cannot_wipe_the_context(self, monkeypatch):
        """Per-path isolation: an unresolvable path is skipped with a warning;
        the remaining values survive (no blanket except)."""
        import server

        monkeypatch.setattr(
            server,
            "_BORROWER_SF_CONTEXT_PATHS",
            (
                "participant.borrower.name",
                "participant.borrower.nope_not_registered",
                "deal.loan_amount",
            ),
        )
        context = server._borrower_sf_context(self._borrower_deal())

        assert context is not None
        assert context["participant.borrower.name"] == "Kovács Anna"
        assert context["deal.loan_amount"] == "30 000 000"
        assert "participant.borrower.nope_not_registered" not in context

    def test_deal_without_borrowers_returns_none(self):
        import server

        deal = self._borrower_deal()
        deal.participants = []
        assert server._borrower_sf_context(deal) is None


class TestLegacyMapping422OnFillAndValidate:
    """Cutover Task 4 additional requirement 6 — a /api/pdf/fill és a
    /api/pdf/validate elavult (legacy Salesforce-neves) mapping esetén
    operátor-barát 422-t ad, nem generikus 500-at."""

    def _install_legacy_pipeline(self, monkeypatch, message):
        import main as main_module
        from main import LegacyCanonicalMappingError

        class _LegacyPipeline:
            def __init__(self, **kwargs):
                pass

            def run_for_deal(self, **kwargs):
                raise LegacyCanonicalMappingError(message)

        monkeypatch.setattr(main_module, "FormFillerPipeline", _LegacyPipeline)
        import server

        monkeypatch.setattr(server, "_get_pdf", lambda pdf_id: Path("x.pdf"))
        monkeypatch.setattr(server, "_make_sf_client", lambda: object())
        return _LegacyPipeline

    def test_fill_returns_422_with_operator_message(self, client, admin_headers, monkeypatch):
        message = (
            "Legacy (Salesforce-neves) canonical mapping — a pipeline csak "
            "Genie canonical path-okat fogad."
        )
        self._install_legacy_pipeline(monkeypatch, message)

        response = client.post(
            "/api/pdf/fill",
            headers=admin_headers,
            json={"pdf_id": "sample.pdf", "deal_id": "DEAL-1"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_mapping"
        assert "migrate_mappings_to_genie" in detail["message"]

    def test_validate_returns_422_with_operator_message(self, client, admin_headers, monkeypatch):
        message = "Legacy (Salesforce-neves) canonical mapping."
        self._install_legacy_pipeline(monkeypatch, message)

        response = client.post(
            "/api/pdf/validate",
            headers=admin_headers,
            json={"pdf_id": "sample.pdf", "deal_id": "DEAL-1"},
        )
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["error"] == "legacy_mapping"
        assert "migrate_mappings_to_genie" in detail["message"]

    def test_other_pipeline_failures_stay_500(self, client, admin_headers, monkeypatch):
        import main as main_module

        class _BoomPipeline:
            def __init__(self, **kwargs):
                pass

            def run_for_deal(self, **kwargs):
                raise RuntimeError("unexpected")

        monkeypatch.setattr(main_module, "FormFillerPipeline", _BoomPipeline)
        import server

        monkeypatch.setattr(server, "_get_pdf", lambda pdf_id: Path("x.pdf"))
        monkeypatch.setattr(server, "_make_sf_client", lambda: object())

        response = client.post(
            "/api/pdf/fill",
            headers=admin_headers,
            json={"pdf_id": "sample.pdf", "deal_id": "DEAL-1"},
        )
        assert response.status_code == 500


class TestFieldAndGroupCrudOnStaleMapping:
    """Task 4 review fix 1 — a STALE (legacy) mapping already on disk must be
    reported through the same structured 422 on every field/group CRUD route
    (each route re-saves the whole document), not as a raw 500; and the file
    must not change (no partial write)."""

    @staticmethod
    def _write_stale_mapping(mapping_dir, canonical="Contact.Name",
                             extra_field=None, fragment=None):
        document = {
            "bank_name": "OTP",
            "form_name": "sample",
            "form_type": "acroform",
            "approved": False,
            "fields": [
                {
                    "pdf_field_name": "nev",
                    "label": "Név",
                    "field_type": "text",
                    "canonical_field": canonical,
                    "confidence": "manual",
                    "page_number": 1,
                },
                {
                    "pdf_field_name": "megjegyzes",
                    "label": "Megjegyzés",
                    "field_type": "text",
                    "canonical_field": "Lead.Ingatlan_megjegyzes__c",
                    "confidence": "manual",
                    "page_number": 1,
                },
            ],
            "character_groups": [],
            "points": [],
        }
        if extra_field:
            document["fields"].append(extra_field)
        if fragment is not None:
            document["fields"][0]["fragment"] = fragment
        path = mapping_dir / "sample_mapping.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2), "utf-8")
        return path

    def test_update_field_returns_422_on_stale_mapping(
        self, client, admin_headers, mapping_store
    ):
        path = self._write_stale_mapping(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.put(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf", "field": "nev"},
            headers=admin_headers,
            json={"notes": "review"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert "Contact.Name" in response.json()["detail"]["message"]
        assert path.read_text(encoding="utf-8") == before

    def test_add_field_returns_422_on_stale_mapping(
        self, client, admin_headers, mapping_store
    ):
        path = self._write_stale_mapping(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.post(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json={"pdf_field_name": "uj", "canonical_field": "deal.loan_amount"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert path.read_text(encoding="utf-8") == before

    def test_delete_field_returns_422_on_stale_mapping_without_partial_write(
        self, client, admin_headers, mapping_store
    ):
        """Trivial DELETE: even field deletion re-saves the stale remainder —
        structured 422, file untouched."""
        path = self._write_stale_mapping(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.delete(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf", "field": "megjegyzes"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert "Contact.Name" in response.json()["detail"]["message"]
        assert path.read_text(encoding="utf-8") == before

    def test_delete_field_can_repair_the_last_stale_field(
        self, client, admin_headers, mapping_store
    ):
        """Deleting the ONLY offending field legitimately cleans the document:
        the save then succeeds (200) — the route is not blanket-blocking."""
        document = {
            "bank_name": "OTP",
            "form_name": "sample",
            "form_type": "acroform",
            "approved": False,
            "fields": [
                {
                    "pdf_field_name": "nev",
                    "label": "Név",
                    "field_type": "text",
                    "canonical_field": "Contact.Name",
                    "confidence": "manual",
                    "page_number": 1,
                }
            ],
            "character_groups": [],
            "points": [],
        }
        path = mapping_store / "sample_mapping.json"
        path.write_text(json.dumps(document, ensure_ascii=False), "utf-8")

        response = client.delete(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf", "field": "nev"},
            headers=admin_headers,
        )
        assert response.status_code == 200
        stored = json.loads(path.read_text(encoding="utf-8"))
        assert stored["fields"] == []

    def test_delete_field_returns_422_for_stale_fragment(
        self, client, admin_headers, mapping_store
    ):
        path = self._write_stale_mapping(
            mapping_dir=mapping_store,
            canonical="participant.borrower.birth_date",
            fragment="év",  # invalid fragment role
        )
        before = path.read_text(encoding="utf-8")

        response = client.delete(
            "/api/mapping/field",
            params={"pdf_id": "sample.pdf", "field": "megjegyzes"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_fragment"
        assert path.read_text(encoding="utf-8") == before

    def test_create_group_returns_422_on_stale_mapping(
        self, client, admin_headers, mapping_store
    ):
        path = self._write_stale_mapping(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.post(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf"},
            headers=admin_headers,
            json={
                "canonical_field": "participant.borrower.address.zip_code",
                "member_fields": ["z1", "z2", "z3", "z4"],
            },
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert path.read_text(encoding="utf-8") == before

    @staticmethod
    def _with_group(mapping_dir):
        """Stale document that also CONTAINS the group being edited, so the
        route reaches the save (group found) instead of a 404."""
        path = TestFieldAndGroupCrudOnStaleMapping._write_stale_mapping(mapping_dir)
        document = json.loads(path.read_text(encoding="utf-8"))
        document["character_groups"] = [{
            "group_id": "g",
            "group_name": "Irányítószám",
            "field_type": "character_split",
            "canonical_field": "participant.borrower.address.zip_code",
            "member_fields": ["z1", "z2", "z3", "z4"],
            "direction": "left_to_right",
            "separator": "",
        }]
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), "utf-8"
        )
        return path

    def test_update_group_returns_422_on_stale_mapping(
        self, client, admin_headers, mapping_store
    ):
        path = self._with_group(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.put(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf", "group_id": "g"},
            headers=admin_headers,
            json={"canonical_field": "property.parcel_number"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert path.read_text(encoding="utf-8") == before

    def test_delete_group_returns_422_on_stale_mapping(
        self, client, admin_headers, mapping_store
    ):
        path = self._with_group(mapping_store)
        before = path.read_text(encoding="utf-8")

        response = client.delete(
            "/api/mapping/group",
            params={"pdf_id": "sample.pdf", "group_id": "g"},
            headers=admin_headers,
        )
        assert response.status_code == 422
        assert response.json()["detail"]["error"] == "invalid_canonical_field"
        assert path.read_text(encoding="utf-8") == before


class TestCanonicalCatalogIsCached:
    """Task 4 review fix 4 — a /api/mapping/canonical-fields válaszként
    szolgáló 6487 elemű expansion egyetlen, immutable cache-ből jön."""

    def test_canonical_fields_expansion_is_cached_not_rebuilt(self):
        from mapping_service import mapping_service

        first = mapping_service.canonical_fields()
        second = mapping_service.canonical_fields()

        assert first is second
        assert len(first) > 1000
        assert first[0]["path"] == second[0]["path"]


class TestGeneratePackageUploadToSfIsOptIn:
    """Final-review finding B2 — POST /api/catalog/generate-package must treat
    ``upload_to_sf`` as explicit-only (opt-in), matching the runbook claim that
    live ContentVersion uploads happen solely on an explicit request. An
    omitted flag must never upload; only an explicit ``true`` may.

    Every boundary is faked (SF client, pipeline, catalog, PDF store): no
    network, no Salesforce org, no real upload can occur from these tests."""

    @staticmethod
    def _install_generate_package_fakes(tmp_path, monkeypatch):
        """Wire fake SF client / pipeline / catalog into the endpoint.

        The fake SF client reports ``_mock_mode = False`` (i.e. simulates the
        live configuration in which the upload branch is reachable), while its
        ``attach_pdf`` only records calls — nothing leaves the process."""
        from types import SimpleNamespace

        import main as main_module
        import server
        from catalog_service import catalog_service

        template = tmp_path / "template.pdf"
        template.write_bytes(b"%PDF-1.4 fake template")
        filled = tmp_path / "Nyilatkozat.pdf"
        filled.write_bytes(b"%PDF-1.4 filled")

        class FakeSF:
            _mock_mode = False

            def __init__(self):
                self.attach_calls = []

            def get_deal(self, deal_id):
                return {"Id": deal_id, "Name": "Demo deal"}

            def attach_pdf(self, deal_id, pdf_path, filename):
                self.attach_calls.append({"deal_id": deal_id, "filename": filename})
                return True

        class FakePipeline:
            def __init__(self, **kwargs):
                pass

            normalizer = SimpleNamespace(
                normalize_deal=lambda raw: SimpleNamespace(borrowers=[], co_borrowers=[])
            )

            def run_for_deal(self, **kwargs):
                return {
                    "success": True,
                    "output_path": str(filled),
                    "filled_fields": ["field1"],
                    "skipped_fields": [],
                    "notices": [],
                    "issues": [],
                }

        sf = FakeSF()
        monkeypatch.setattr(main_module, "FormFillerPipeline", FakePipeline)
        monkeypatch.setattr(server, "_make_sf_client", lambda: sf)
        monkeypatch.setattr(server, "_get_pdf", lambda file_path: template)
        monkeypatch.setattr(
            catalog_service,
            "load_catalog",
            lambda: {
                "documents": [
                    {
                        "id": "nyilatkozat",
                        "title": "Nyilatkozat",
                        "product_ids": ["prod-1"],
                        "file_path": "nyilatkozat.pdf",
                        "per_applicant": False,
                    }
                ]
            },
        )
        # ZIP output lands in the test sandbox, not the repo's output/ dir.
        monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
        return sf

    def test_omitted_upload_to_sf_flag_does_not_upload(
        self, client, admin_headers, tmp_path, monkeypatch
    ):
        sf = self._install_generate_package_fakes(tmp_path, monkeypatch)

        response = client.post(
            "/api/catalog/generate-package",
            headers=admin_headers,
            json={"bank_id": "OTP", "product_id": "prod-1", "deal_id": "0061"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["sf_uploads"] == []
        assert sf.attach_calls == []

    def test_explicit_upload_to_sf_true_uploads_each_filled_pdf(
        self, client, admin_headers, tmp_path, monkeypatch
    ):
        sf = self._install_generate_package_fakes(tmp_path, monkeypatch)

        response = client.post(
            "/api/catalog/generate-package",
            headers=admin_headers,
            json={
                "bank_id": "OTP",
                "product_id": "prod-1",
                "deal_id": "0061",
                "upload_to_sf": True,
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert sf.attach_calls == [{"deal_id": "0061", "filename": "Nyilatkozat.pdf"}]
        assert body["sf_uploads"] == [{"file": "Nyilatkozat.pdf", "success": True}]


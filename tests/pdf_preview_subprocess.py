"""Preview endpoint tests executed only by test_pdf_p0_regressions subprocesses."""
import base64
import importlib.util
import json
import os
import sys
from pathlib import Path

import fitz
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Backend modules use flat imports. Preload their config explicitly to avoid
# colliding with the project's src/config package during the test run.
_BACKEND = PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_spec = importlib.util.spec_from_file_location("config", _BACKEND / "config.py")
_backend_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_config)
sys.modules["config"] = _backend_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    required_env = ("CATALOG_DB_PATH", "AUTH_DB_PATH", "AUTH_SECRET")
    missing = [name for name in required_env if not os.environ.get(name)]
    assert not missing, f"Preview subprocess requires isolated env: {missing}"

    # These imports happen only after the parent process has supplied temporary
    # import-time database paths and an in-memory signing secret.
    from fastapi.testclient import TestClient
    from auth_service import AuthService
    import server

    auth = AuthService(db_path=tmp_path / "users.db")
    monkeypatch.setattr(server, "auth_service", auth)
    with TestClient(server.app, raise_server_exceptions=False) as test_client:
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
            "username": "preview_member",
            "password": "MemberPass1!",
            "display_name": "Preview Member",
            "role": "member",
        },
    )
    assert response.status_code == 200
    response = client.post(
        "/api/auth/login",
        json={"username": "preview_member", "password": "MemberPass1!"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _write_pdf(path: Path, page_count: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(page_count):
        doc.new_page()
    doc.save(path)
    doc.close()


def test_import_time_state_is_session_isolated():
    """Probe conftest isolation before importing server's singleton graph."""
    probe_output = os.environ.get("FG_TEST_ISOLATION_PROBE")
    assert probe_output, "Isolation probe output path is required"
    required_env = ("CATALOG_DB_PATH", "AUTH_DB_PATH", "AUTH_SECRET")
    missing = [name for name in required_env if not os.environ.get(name)]
    assert not missing, f"conftest did not establish isolated import state: {missing}"

    catalog_db = Path(os.environ["CATALOG_DB_PATH"]).resolve()
    auth_db = Path(os.environ["AUTH_DB_PATH"]).resolve()
    real_db = (PROJECT_ROOT / "catalog" / "catalog.db").resolve()
    assert catalog_db != real_db
    assert auth_db != real_db
    assert catalog_db != auth_db
    assert catalog_db.parent == auth_db.parent
    assert not catalog_db.parent.is_relative_to(PROJECT_ROOT)

    import auth_service
    import catalog_service
    import server

    assert catalog_service.DB_PATH.resolve() == catalog_db
    assert auth_service.DB_PATH.resolve() == auth_db
    assert server.auth_service._db_path.resolve() == auth_db
    assert server.auth_service._secret == os.environ["AUTH_SECRET"].encode("utf-8")
    assert catalog_db.is_file(), "Import-time catalog singleton did not use its isolated DB"
    assert auth_db.is_file(), "Import-time auth singleton did not use its isolated DB"

    Path(probe_output).write_text(
        json.dumps(
            {
                "state_dir": str(catalog_db.parent),
                "catalog_db": str(catalog_db),
                "auth_db": str(auth_db),
            }
        ),
        encoding="utf-8",
    )


def test_fill_pages_returns_total_pages_to_authenticated_member(
    client, member_headers, tmp_path, monkeypatch
):
    import server

    pdf_path = tmp_path / "output" / "filled.pdf"
    _write_pdf(pdf_path, page_count=2)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/fill/pages",
        params={"path": str(pdf_path), "count": 1},
        headers=member_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_pages"] == 2
    assert body["path"] == str(pdf_path)
    assert len(body["pages"]) == 1
    assert base64.b64decode(body["pages"][0]).startswith(b"\x89PNG")


def test_fill_pages_closes_document_when_rendering_fails(
    client, member_headers, tmp_path, monkeypatch
):
    import server

    pdf_path = tmp_path / "output" / "filled.pdf"
    _write_pdf(pdf_path)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    original_open = fitz.open
    opened = {}

    class FailingPage:
        def get_pixmap(self, matrix):
            raise RuntimeError("render failed")

    class TrackingDocument:
        def __init__(self, document):
            self.document = document
            self.closed = False

        def __len__(self):
            return len(self.document)

        def __getitem__(self, index):
            return FailingPage()

        def close(self):
            self.closed = True
            self.document.close()

    def tracking_open(path):
        document = TrackingDocument(original_open(path))
        opened["document"] = document
        return document

    monkeypatch.setattr(fitz, "open", tracking_open)

    response = client.get(
        "/api/pdf/fill/pages",
        params={"path": str(pdf_path)},
        headers=member_headers,
    )

    assert response.status_code == 500
    assert opened["document"].closed is True


def test_fill_pages_rejects_output_sibling(
    client, member_headers, tmp_path, monkeypatch
):
    import server

    sibling_pdf = tmp_path / "output-private" / "secret.pdf"
    _write_pdf(sibling_pdf)
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/fill/pages",
        params={"path": str(sibling_pdf)},
        headers=member_headers,
    )

    assert response.status_code == 403


def test_fill_pages_rejects_directory(
    client, member_headers, tmp_path, monkeypatch
):
    import server

    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = client.get(
        "/api/pdf/fill/pages",
        params={"path": str(output)},
        headers=member_headers,
    )

    assert response.status_code == 404

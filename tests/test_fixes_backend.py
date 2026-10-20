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

    def test_server_role_middleware(self):
        from fastapi.testclient import TestClient
        import server

        client = TestClient(server.app)

        # Unauthenticated request with X-Enforce-Auth -> 401
        r_unauth = client.get("/api/catalog", headers={"X-Enforce-Auth": "1"})
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


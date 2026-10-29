"""
Authentication and User Management Service (SQLite + PBKDF2-SHA256 + HMAC tokens).

Roles:
  - "admin": full access (catalog CRUD, PDF upload/split, mapping editor, AI recognition,
             package generation, user management)
  - "member": read-only access to the catalog ("Dokumentumtár") + full package generation
              ("Generálás"), validation, and ZIP/PDF download. Cannot modify banks, products,
              documents, mappings, or users.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DB_PATH: Path = Path(
    os.getenv("AUTH_DB_PATH", str(PROJECT_ROOT / "catalog" / "catalog.db"))
)
SECRET_PATH: Path = PROJECT_ROOT / "catalog" / ".auth_secret"

log = logging.getLogger("auth_service")

PBKDF2_ITERATIONS = 120_000
VALID_ROLES = ("admin", "member")


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("ascii"),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iter_str, salt, hash_hex = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("ascii"),
            iterations,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("ascii"))


class AuthService:
    """Thread-safe SQLite user & token service."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or DB_PATH
        self._local = threading.local()
        self._secret = self._load_or_create_secret()
        self._ensure_db()

    def _load_or_create_secret(self) -> bytes:
        env_secret = os.getenv("AUTH_SECRET", "").strip()
        if env_secret:
            return env_secret.encode("utf-8")
        try:
            SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
            if SECRET_PATH.exists():
                val = SECRET_PATH.read_text(encoding="utf-8").strip()
                if len(val) >= 32:
                    return val.encode("utf-8")
            generated = secrets.token_hex(32)
            SECRET_PATH.write_text(generated, encoding="utf-8")
            return generated.encode("utf-8")
        except Exception:
            return b"financialgenie-fallback-hmac-secret-key-2026"

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _ensure_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'member',
                created_at TEXT DEFAULT (datetime('now'))
            );
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            admin_user = os.getenv("ADMIN_USERNAME", "admin").strip() or "admin"
            admin_pass = os.getenv("ADMIN_PASSWORD", "Admin123!").strip() or "Admin123!"
            admin_name = os.getenv("ADMIN_DISPLAY_NAME", "Adminisztrátor").strip() or "Adminisztrátor"
            conn.execute(
                "INSERT INTO users (id, username, display_name, password_hash, role) VALUES (?, ?, ?, ?, 'admin')",
                ("usr_admin", admin_user, admin_name, _hash_password(admin_pass)),
            )
            log.info("Seeded default admin user '%s'", admin_user)
        conn.commit()
        conn.close()

    @staticmethod
    def _row_to_user(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "username": row["username"],
            "display_name": row["display_name"],
            "role": row["role"],
            "created_at": row["created_at"],
        }

    # ------------------------------------------------------------------
    # Authentication & Tokens
    # ------------------------------------------------------------------
    def authenticate(self, username: str, password: str) -> dict | None:
        uname = (username or "").strip()
        if not uname or not password:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (uname,),
        ).fetchone()
        if not row:
            return None
        if not _verify_password(password, row["password_hash"]):
            return None
        return self._row_to_user(row)

    def create_token(self, user: dict, ttl_seconds: int = 7 * 86400) -> str:
        payload = {
            "sub": user["id"],
            "username": user["username"],
            "role": user["role"],
            "exp": int(time.time()) + ttl_seconds,
        }
        payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(self._secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
        return f"{payload_b64}.{_b64url_encode(sig)}"

    def verify_token(self, token: str) -> dict | None:
        if not token or "." not in token:
            return None
        try:
            payload_b64, sig_b64 = token.split(".", 1)
            expected_sig = hmac.new(self._secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
            actual_sig = _b64url_decode(sig_b64)
            if not hmac.compare_digest(expected_sig, actual_sig):
                return None
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            user_id = payload.get("sub")
            if not user_id:
                return None
            return self.get_user(user_id)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # User CRUD (Admin)
    # ------------------------------------------------------------------
    def list_users(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, username, display_name, role, created_at FROM users ORDER BY CASE WHEN role='admin' THEN 0 ELSE 1 END, username COLLATE NOCASE"
        ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def get_user(self, user_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT id, username, display_name, role, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        return self._row_to_user(row) if row else None

    def create_user(
        self,
        username: str,
        password: str,
        display_name: str = "",
        role: str = "member",
    ) -> dict:
        uname = (username or "").strip()
        if not uname:
            raise ValueError("A felhasználónév megadása kötelező.")
        if len(uname) < 2:
            raise ValueError("A felhasználónév túl rövid (min. 2 karakter).")
        if not password or len(password) < 4:
            raise ValueError("A jelszó legalább 4 karakter hosszú kell legyen.")
        if role not in VALID_ROLES:
            raise ValueError(f"Érvénytelen szerepkör: {role}")

        dname = (display_name or "").strip() or uname
        user_id = f"usr_{secrets.token_hex(6)}"
        conn = self._get_conn()
        existing = conn.execute(
            "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
            (uname,),
        ).fetchone()
        if existing:
            raise ValueError(f"A(z) '{uname}' felhasználónév már foglalt.")

        conn.execute(
            "INSERT INTO users (id, username, display_name, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (user_id, uname, dname, _hash_password(password), role),
        )
        conn.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def update_user(
        self,
        user_id: str,
        display_name: str | None = None,
        role: str | None = None,
        password: str | None = None,
    ) -> dict:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise KeyError("A felhasználó nem található.")

        if role is not None:
            if role not in VALID_ROLES:
                raise ValueError(f"Érvénytelen szerepkör: {role}")
            if row["role"] == "admin" and role != "admin":
                admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
                if admin_count <= 1:
                    raise ValueError("Az utolsó adminisztrátor jogosultsága nem vehető el.")
            conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))

        if display_name is not None:
            dname = display_name.strip() or row["username"]
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (dname, user_id))

        if password is not None and password.strip():
            if len(password.strip()) < 4:
                raise ValueError("A jelszó legalább 4 karakter hosszú kell legyen.")
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (_hash_password(password.strip()), user_id),
            )

        conn.commit()
        return self.get_user(user_id)  # type: ignore[return-value]

    def delete_user(self, user_id: str, acting_user_id: str | None = None) -> bool:
        if acting_user_id and user_id == acting_user_id:
            raise ValueError("Saját magadat nem törölheted.")
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return False
        if row["role"] == "admin":
            admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0]
            if admin_count <= 1:
                raise ValueError("Az utolsó adminisztrátor nem törölhető.")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return True

"""
Catalog service: thread-safe SQLite CRUD for the document catalog.

Uses Python's built-in sqlite3 module (no ORM, no external server).
The database file (catalog/catalog.db) is created automatically on first
access with the correct schema.

Thread safety: each thread gets its own sqlite3.Connection via
threading.local(). WAL mode enables concurrent reads. Foreign keys
are enforced (PRAGMA foreign_keys=ON).

The catalog models the Bank -> Product -> Document hierarchy with M:N
document-product associations and per-applicant metadata.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import unicodedata
from pathlib import Path

#: Absolute path to the project root (parent of backend/).
#: Computed from __file__ to avoid shadowing by the project-level config/
#: directory when imported standalone.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

log = logging.getLogger("catalog_service")

#: Path to the SQLite catalog database.
DB_PATH: Path = PROJECT_ROOT / "catalog" / "catalog.db"


def _slugify(name: str) -> str:
    """Derive an ASCII slug from a (possibly accented) name.

    NFKD-normalises, strips combining marks, lowercases, and replaces
    spaces/hyphens with underscores so Hungarian names like
    "OTP Bank" -> "otp_bank", "Piaci hitel" -> "piaci_hitel".
    """
    nfd = unicodedata.normalize("NFKD", name.lower())
    ascii_str = nfd.encode("ascii", "ignore").decode()
    return ascii_str.replace(" ", "_").replace("-", "_")


class CatalogService:
    """Thread-safe SQLite-based catalog for banks, products, and documents."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._ensure_db()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(DB_PATH))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _ensure_db(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS banks (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                bank_id TEXT NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                file_path TEXT NOT NULL,
                source TEXT DEFAULT '',
                page_count INTEGER DEFAULT 0,
                per_applicant INTEGER DEFAULT 0,
                split_from_master INTEGER DEFAULT 0,
                master_page_number INTEGER,
                master_section TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS document_products (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                PRIMARY KEY (document_id, product_id)
            );
            CREATE TABLE IF NOT EXISTS document_tags (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                tag TEXT NOT NULL,
                PRIMARY KEY (document_id, tag)
            );
            """
        )
        conn.commit()
        conn.close()
        self._migrate()

    def _migrate(self) -> None:
        """Add columns introduced after the initial schema (idempotent)."""
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        cols = [row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()]
        if "sha256" not in cols:
            conn.execute("ALTER TABLE documents ADD COLUMN sha256 TEXT")
            conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Bank CRUD
    # ------------------------------------------------------------------
    def list_banks(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM banks ORDER BY created_at").fetchall()
        banks = []
        for row in rows:
            bank = dict(row)
            prods = conn.execute(
                "SELECT * FROM products WHERE bank_id = ? ORDER BY created_at",
                (bank["id"],),
            ).fetchall()
            bank["products"] = []
            for p in prods:
                prod = dict(p)
                doc_rows = conn.execute(
                    "SELECT document_id FROM document_products WHERE product_id = ?",
                    (prod["id"],),
                ).fetchall()
                prod["document_ids"] = [d["document_id"] for d in doc_rows]
                bank["products"].append(prod)
            banks.append(bank)
        return banks

    def add_bank(self, name: str) -> dict:
        slug = _slugify(name)
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO banks (id, name, slug) VALUES (?, ?, ?)",
            (slug, name, slug),
        )
        conn.commit()
        return {"id": slug, "name": name, "slug": slug, "products": []}

    def get_bank(self, bank_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM banks WHERE id = ?", (bank_id,)
        ).fetchone()
        if not row:
            return None
        bank = dict(row)
        prods = conn.execute(
            "SELECT * FROM products WHERE bank_id = ? ORDER BY created_at",
            (bank_id,),
        ).fetchall()
        bank["products"] = [dict(p) for p in prods]
        return bank

    # ------------------------------------------------------------------
    # Product CRUD
    # ------------------------------------------------------------------
    def add_product(self, bank_id: str, name: str) -> dict:
        slug = _slugify(name)
        conn = self._get_conn()
        bank = conn.execute(
            "SELECT id FROM banks WHERE id = ?", (bank_id,)
        ).fetchone()
        if not bank:
            raise ValueError(f"Bank '{bank_id}' not found")
        conn.execute(
            "INSERT INTO products (id, name, slug, bank_id) VALUES (?, ?, ?, ?)",
            (slug, name, slug, bank_id),
        )
        conn.commit()
        return {
            "id": slug,
            "name": name,
            "slug": slug,
            "bank_id": bank_id,
            "document_ids": [],
        }

    def get_product(self, product_id: str, bank_id: str = None) -> dict | None:
        conn = self._get_conn()
        query = "SELECT * FROM products WHERE id = ?"
        params: list = [product_id]
        if bank_id:
            query += " AND bank_id = ?"
            params.append(bank_id)
        row = conn.execute(query, params).fetchone()
        if not row:
            return None
        prod = dict(row)
        doc_rows = conn.execute(
            "SELECT document_id FROM document_products WHERE product_id = ?",
            (product_id,),
        ).fetchall()
        prod["document_ids"] = [d["document_id"] for d in doc_rows]
        return prod

    # ------------------------------------------------------------------
    # Document CRUD
    # ------------------------------------------------------------------
    def add_document(self, document: dict) -> dict:
        """Insert a document, or update it if the id already exists (upsert).

        This makes the split idempotent: re-splitting the same master PDF for
        the same bank overwrites the existing ``split_<bank>_<section>``
        documents instead of raising ``UNIQUE constraint failed``.
        """
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO documents (id, title, file_path, source, page_count,
                                   per_applicant, split_from_master,
                                   master_page_number, master_section, sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                file_path = excluded.file_path,
                source = excluded.source,
                page_count = excluded.page_count,
                per_applicant = excluded.per_applicant,
                split_from_master = excluded.split_from_master,
                master_page_number = excluded.master_page_number,
                master_section = excluded.master_section,
                sha256 = excluded.sha256
            """,
            (
                document["id"],
                document["title"],
                document["file_path"],
                document.get("source", ""),
                document.get("page_count", 0),
                1 if document.get("per_applicant") else 0,
                1 if document.get("split_from_master") else 0,
                document.get("master_page_number"),
                document.get("master_section"),
                document.get("sha256"),
            ),
        )
        for pid in document.get("product_ids", []):
            conn.execute(
                "INSERT OR IGNORE INTO document_products (document_id, product_id) VALUES (?, ?)",
                (document["id"], pid),
            )
        for tag in document.get("tags", []):
            conn.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag) VALUES (?, ?)",
                (document["id"], tag),
            )
        conn.commit()
        return document

    def get_document(self, doc_id: str) -> dict | None:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        doc["per_applicant"] = bool(doc["per_applicant"])
        doc["split_from_master"] = bool(doc["split_from_master"])
        prod_rows = conn.execute(
            "SELECT product_id FROM document_products WHERE document_id = ?",
            (doc_id,),
        ).fetchall()
        doc["product_ids"] = [p["product_id"] for p in prod_rows]
        tag_rows = conn.execute(
            "SELECT tag FROM document_tags WHERE document_id = ?", (doc_id,)
        ).fetchall()
        doc["tags"] = [t["tag"] for t in tag_rows]
        return doc

    def find_by_hash(self, sha256_hash: str) -> dict | None:
        """Check if a document with this SHA-256 hash already exists."""
        if not sha256_hash:
            return None
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM documents WHERE sha256 = ?", (sha256_hash,)
        ).fetchone()
        if not row:
            return None
        doc = dict(row)
        doc["per_applicant"] = bool(doc["per_applicant"])
        doc["split_from_master"] = bool(doc["split_from_master"])
        prod_rows = conn.execute(
            "SELECT product_id FROM document_products WHERE document_id = ?",
            (doc["id"],),
        ).fetchall()
        doc["product_ids"] = [p["product_id"] for p in prod_rows]
        tag_rows = conn.execute(
            "SELECT tag FROM document_tags WHERE document_id = ?", (doc["id"],)
        ).fetchall()
        doc["tags"] = [t["tag"] for t in tag_rows]
        return doc

    def list_documents(self) -> list[dict]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY created_at"
        ).fetchall()
        docs = []
        for row in rows:
            doc = dict(row)
            doc["per_applicant"] = bool(doc["per_applicant"])
            doc["split_from_master"] = bool(doc["split_from_master"])
            prod_rows = conn.execute(
                "SELECT product_id FROM document_products WHERE document_id = ?",
                (doc["id"],),
            ).fetchall()
            doc["product_ids"] = [p["product_id"] for p in prod_rows]
            tag_rows = conn.execute(
                "SELECT tag FROM document_tags WHERE document_id = ?",
                (doc["id"],),
            ).fetchall()
            doc["tags"] = [t["tag"] for t in tag_rows]
            docs.append(doc)
        return docs

    def update_document_products(self, doc_id: str, product_ids: list[str]) -> None:
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM document_products WHERE document_id = ?", (doc_id,)
        )
        for pid in product_ids:
            conn.execute(
                "INSERT OR IGNORE INTO document_products (document_id, product_id) VALUES (?, ?)",
                (doc_id, pid),
            )
        conn.commit()

    def set_per_applicant(self, doc_id: str, value: bool) -> None:
        conn = self._get_conn()
        conn.execute(
            "UPDATE documents SET per_applicant = ? WHERE id = ?",
            (1 if value else 0, doc_id),
        )
        conn.commit()

    def delete_bank(self, bank_id: str) -> None:
        """Delete a bank, its products, exclusive documents, and on-disk files.

        SQLite foreign keys cascade products → banks and
        document_products → products/documents.  However, the
        ``documents`` table has no direct FK to ``banks`` (the link is
        M:N via ``document_products``), so documents that are
        *exclusively* associated with this bank's products must be
        removed explicitly before the bank row is deleted.

        Shared documents (associated with products from other banks)
        are kept; only the association is removed via CASCADE.

        The ``documents/<bank_id>/`` directory on disk is removed as well.
        """
        conn = self._get_conn()
        # Collect product ids that belong to this bank.
        prod_rows = conn.execute(
            "SELECT id FROM products WHERE bank_id = ?", (bank_id,)
        ).fetchall()
        product_ids = [r["id"] for r in prod_rows]

        if product_ids:
            placeholders = ",".join("?" * len(product_ids))
            # Find documents associated with ANY of this bank's products.
            doc_rows = conn.execute(
                f"SELECT DISTINCT document_id FROM document_products WHERE product_id IN ({placeholders})",
                product_ids,
            ).fetchall()
            for row in doc_rows:
                doc_id = row["document_id"]
                # Check if this document is also associated with a product
                # that does NOT belong to this bank.
                shared = conn.execute(
                    f"SELECT 1 FROM document_products dp JOIN products p ON dp.product_id = p.id WHERE dp.document_id = ? AND p.bank_id != ? LIMIT 1",
                    (doc_id, bank_id),
                ).fetchone()
                if not shared:
                    # Exclusive to this bank — delete the document row and file.
                    self._delete_document_file(conn, doc_id)
                    conn.execute(
                        "DELETE FROM documents WHERE id = ?", (doc_id,)
                    )
        # Finally, delete the bank. CASCADE removes its products and any
        # remaining document_products / document_tags entries.
        conn.execute("DELETE FROM banks WHERE id = ?", (bank_id,))
        conn.commit()

        # Remove the bank's on-disk directory (master + sections + splits).
        import shutil
        bank_dir = PROJECT_ROOT / "documents" / bank_id
        if bank_dir.exists():
            shutil.rmtree(str(bank_dir), ignore_errors=True)

    def delete_product(self, product_id: str) -> dict:
        """Delete a product. Documents are NOT deleted (M:N — a document
        may belong to multiple products). The ``document_products`` rows
        cascade via FK.

        Returns ``{"deleted": true, "orphaned_documents": [...]}`` — the
        document IDs that now have zero product associations after this
        deletion, so the frontend can warn the user.
        """
        conn = self._get_conn()
        # Find documents that will be orphaned (this is their only product).
        orphan_rows = conn.execute(
            """
            SELECT dp.document_id
            FROM document_products dp
            WHERE dp.product_id = ?
              AND dp.document_id IN (
                SELECT document_id FROM document_products
                GROUP BY document_id HAVING COUNT(*) = 1
              )
            """,
            (product_id,),
        ).fetchall()
        orphaned = [r["document_id"] for r in orphan_rows]

        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        conn.commit()
        return {"deleted": True, "orphaned_documents": orphaned}

    def _delete_document_file(self, conn, doc_id: str) -> None:
        """Delete the on-disk file for *doc_id* if it exists and no other
        document references the same file_path. This protects against
        accidental deletion of shared files.
        """
        row = conn.execute(
            "SELECT file_path FROM documents WHERE id = ?", (doc_id,)
        ).fetchone()
        if not row or not row["file_path"]:
            return
        file_path_str = row["file_path"]
        # Check if any OTHER document points to the same file_path.
        dup = conn.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE file_path = ? AND id != ?",
            (file_path_str, doc_id),
        ).fetchone()
        if dup["n"] > 0:
            return  # Shared file — don't delete from disk.
        file_path = PROJECT_ROOT / file_path_str
        if file_path.is_file():
            try:
                file_path.unlink()
                log.info("Deleted file: %s", file_path)
            except OSError as e:
                log.warning("Could not delete file %s: %s", file_path, e)

    def delete_document(self, doc_id: str) -> None:
        """Delete a document from the catalog AND its file on disk.

        Product associations and tags are removed via CASCADE. The
        on-disk PDF is deleted only if no other catalog document
        references the same ``file_path`` (dedup protection).
        """
        conn = self._get_conn()
        self._delete_document_file(conn, doc_id)
        conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        conn.commit()

    # ------------------------------------------------------------------
    # Full catalog export (for the GET /api/catalog endpoint)
    # ------------------------------------------------------------------
    def load_catalog(self) -> dict:
        return {
            "version": 2,
            "banks": self.list_banks(),
            "documents": self.list_documents(),
        }


#: Module-level singleton (mirrors mapping_service / pdf_service pattern).
catalog_service = CatalogService()

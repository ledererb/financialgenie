"""
Catalog service: thread-safe CRUD for the document catalog JSON.

Mirrors mapping_service.py's concurrency pattern:
  - threading.Lock guards the atomic write
  - .tmp -> os.replace for crash-safe writes
  - mtime tracking for optimistic-concurrency conflict detection

The catalog models the Bank -> Product -> Document hierarchy with M:N
document-product associations and per-applicant metadata.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import unicodedata
from pathlib import Path

#: Absolute path to the project root (parent of backend/).
#: Computed from __file__ to avoid shadowing by the project-level config/
#: directory when imported standalone.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

log = logging.getLogger("catalog_service")

#: Path to the single catalog manifest.
CATALOG_PATH: Path = PROJECT_ROOT / "catalog" / "document_catalog.json"


def _slugify(name: str) -> str:
    """Derive an ASCII slug from a (possibly accented) name.

    NFKD-normalises, strips combining marks, lowercases, and replaces
    spaces/hyphens with underscores so Hungarian names like
    "OTP Bank" -> "otp_bank", "Piaci hitel" -> "piaci_hitel".
    """
    nfd = unicodedata.normalize("NFKD", name.lower())
    ascii_str = "".join(ch for ch in nfd if not unicodedata.combining(ch))
    return ascii_str.replace(" ", "_").replace("-", "_")


class CatalogService:
    """Thread-safe CRUD over the document catalog JSON."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float | None = None

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------
    def _ensure_exists(self) -> None:
        """Create an empty catalog file on disk if it doesn't exist yet."""
        if not CATALOG_PATH.exists():
            CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.save({"version": 1, "banks": [], "documents": []})

    def load(self) -> dict:
        """Load and return the full catalog dict. Creates it if missing."""
        self._ensure_exists()
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._mtime = os.path.getmtime(CATALOG_PATH)
        return data

    def save(self, catalog: dict) -> None:
        """Atomically persist the full catalog (tmp + rename).

        Raises FileConflictError if the on-disk mtime changed since the
        last load() — a concurrent writer beat us.
        """
        with self._lock:
            if self._mtime is not None and CATALOG_PATH.exists():
                current_mtime = os.path.getmtime(CATALOG_PATH)
                if current_mtime != self._mtime:
                    raise FileConflictError(
                        "Catalog modified by another process"
                    )
            tmp_path = CATALOG_PATH.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(catalog, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, CATALOG_PATH)
            self._mtime = os.path.getmtime(CATALOG_PATH)

    # ------------------------------------------------------------------
    # Banks
    # ------------------------------------------------------------------
    def list_banks(self) -> list[dict]:
        return self.load()["banks"]

    def add_bank(self, name: str) -> dict:
        """Create a bank entry and return it."""
        slug = _slugify(name)
        bank: dict = {"id": slug, "name": name, "products": []}
        catalog = self.load()
        catalog["banks"].append(bank)
        self.save(catalog)
        return bank

    def get_bank(self, bank_id: str) -> dict | None:
        catalog = self.load()
        for bank in catalog["banks"]:
            if bank["id"] == bank_id:
                return bank
        return None

    # ------------------------------------------------------------------
    # Products
    # ------------------------------------------------------------------
    def add_product(self, bank_id: str, name: str) -> dict:
        """Create a product under a bank. Returns the product dict."""
        slug = _slugify(name)
        product: dict = {"id": slug, "name": name, "document_ids": []}
        catalog = self.load()
        for bank in catalog["banks"]:
            if bank["id"] == bank_id:
                bank["products"].append(product)
                self.save(catalog)
                return product
        raise ValueError(f"Bank '{bank_id}' not found")

    def get_product(self, product_id: str, bank_id: str | None = None) -> dict | None:
        catalog = self.load()
        for bank in catalog["banks"]:
            if bank_id and bank["id"] != bank_id:
                continue
            for product in bank["products"]:
                if product["id"] == product_id:
                    return product
        return None

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def add_document(self, document: dict) -> dict:
        """Add a document to the catalog and update each referenced
        product's ``document_ids`` (M:N inverse index)."""
        catalog = self.load()
        catalog["documents"].append(document)
        for product_id in document.get("product_ids", []):
            for bank in catalog["banks"]:
                for product in bank["products"]:
                    if product["id"] == product_id:
                        if document["id"] not in product["document_ids"]:
                            product["document_ids"].append(document["id"])
        self.save(catalog)
        return document


class FileConflictError(Exception):
    """Raised when the catalog was modified externally (mtime mismatch)."""


#: Module-level singleton (mirrors mapping_service / pdf_service pattern).
catalog_service = CatalogService()

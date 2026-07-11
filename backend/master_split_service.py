"""
Master PDF split service: async, fully-automatic section-aware splitting.

Splits a large master PDF (e.g. the ~90-page OTP application document pack)
into per-section PDFs using the curated section map in DocumentAssembler
(BASE_SECTIONS + PRODUCT_SECTIONS). Each split document is registered in the
catalog with appropriate M:N product associations.

The split runs in a background thread; progress is tracked via a thread-safe
dict keyed by split_id. The frontend polls GET /api/catalog/split/{split_id}
to render a progress bar.
"""
from __future__ import annotations

import hashlib
import logging
import sys
import threading
import unicodedata
import uuid
from pathlib import Path

import fitz  # PyMuPDF

#: Ensure project root is on sys.path so src.engine imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.engine.document_assembler import (  # noqa: E402
    BASE_SECTIONS,
    PRODUCT_SECTIONS,
    ProductType,
)

log = logging.getLogger("master_split_service")

# Thread-local split progress tracking.
# {split_id: {status, progress, total_pages, done_pages, error, ...}}
_split_progress: dict[str, dict] = {}
_split_lock = threading.Lock()


def _slugify_section(name: str) -> str:
    """NFKD-normalise a section name into an ASCII-safe filename stem."""
    nfd = unicodedata.normalize("NFKD", name.lower())
    ascii_str = nfd.encode("ascii", "ignore").decode()
    safe = ascii_str.replace(" ", "_").replace("/", "_")
    # collapse repeated underscores
    while "__" in safe:
        safe = safe.replace("__", "_")
    return safe.strip("_")


def _compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_page_range(
    master_pdf_path: Path | str,
    start_page: int,
    end_page: int,
    output_path: Path | str,
) -> int:
    """Extract a contiguous page range from *master_pdf_path* into a new PDF.

    Page numbers are 1-indexed inclusive (matching ``BASE_SECTIONS`` and user
    expectations). The new PDF is saved to *output_path*.

    Uses **pikepdf** (not PyMuPDF) to preserve AcroForm widget annotations.
    PyMuPDF's ``insert_pdf`` silently drops form fields, which breaks all
    downstream mapping/filling. pikepdf's ``add_pages_from`` copies pages
    together with their widget annotations and the global AcroForm dictionary.

    Returns the number of pages written.
    """
    import pikepdf

    master_pdf_path = Path(master_pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    src = pikepdf.open(str(master_pdf_path))
    total_pages = len(src.pages)

    # Clamp to the real page count (1-indexed, inclusive).
    start_1 = max(1, min(start_page, total_pages))
    end_1 = max(start_1, min(end_page, total_pages))

    out = pikepdf.Pdf.new()
    # add_pages_from preserves form widget annotations correctly (unlike
    # manual page append + copy_foreign, which can orphan widgets). It also
    # copies the relevant AcroForm fields automatically (forms='preserve').
    out.add_pages_from(src, pages=slice(start_1 - 1, end_1))

    out.save(str(output_path))
    out.close()
    src.close()

    return end_1 - start_1 + 1


def _master_using_product_ids(catalog_service, bank_id: str) -> list[str]:
    """Return product IDs under *bank_id* that use the master PDF.

    The BASE_SECTIONS (fedlap, személyi adatlap, ingatlan adatlap, etc.) are
    shared by ALL credit products — every loan application needs them. So
    every product under the bank receives the base-section split documents,
    except products that are clearly standalone (like "Előzetes értékbecslés"
    which has its own dedicated form, not part of the master application pack).
    """
    bank = catalog_service.get_bank(bank_id)
    if not bank:
        return []
    result = []
    for prod in bank.get("products", []):
        slug = prod.get("slug") or prod.get("id") or ""
        # Skip products that are standalone (not part of the master pack).
        standalone_slugs = {"elozetes_ertekbecsles_megrendeles"}
        if slug in standalone_slugs:
            continue
        result.append(prod["id"])
    return result


class MasterSplitService:
    """Splits a master PDF into per-section documents using the
    DocumentAssembler section map."""

    @staticmethod
    def start_split(master_pdf_path: str, output_dir: str, bank_id: str) -> str:
        """Start an async split. Returns a split_id for polling."""
        split_id = uuid.uuid4().hex[:8]

        with _split_lock:
            _split_progress[split_id] = {
                "split_id": split_id,
                "status": "pending",
                "progress": 0,
                "total_pages": 0,
                "done_pages": 0,
                "error": None,
                "bank_id": bank_id,
                "output_dir": output_dir,
                "output_files": [],
            }

        thread = threading.Thread(
            target=MasterSplitService._do_split,
            args=(split_id, master_pdf_path, output_dir, bank_id),
            daemon=True,
        )
        thread.start()
        return split_id

    @staticmethod
    def get_progress(split_id: str) -> dict | None:
        with _split_lock:
            entry = _split_progress.get(split_id)
            return dict(entry) if entry else None

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------
    @staticmethod
    def _do_split(
        split_id: str,
        master_pdf_path: str,
        output_dir: str,
        bank_id: str,
    ) -> None:
        try:
            from catalog_service import catalog_service

            master_path = Path(master_pdf_path)
            if not master_path.exists():
                raise FileNotFoundError(
                    f"Master PDF not found: {master_pdf_path}"
                )

            doc = fitz.open(str(master_path))
            total_pages = len(doc)

            with _split_lock:
                _split_progress[split_id]["status"] = "processing"
                _split_progress[split_id]["total_pages"] = total_pages

            # Determine which products are "master-using" (non-empty sections).
            master_products = _master_using_product_ids(
                catalog_service, bank_id
            )
            log.info(
                "Master split %s: %d pages, bank=%s, master_products=%s",
                split_id,
                total_pages,
                bank_id,
                master_products,
            )

            # Build a flat list of sections to extract.
            # Page numbers in BASE_SECTIONS / PRODUCT_SECTIONS are 1-based
            # inclusive; PyMuPDF is 0-based.
            sections: list[dict] = []

            for section_name, (start_page, end_page) in BASE_SECTIONS.items():
                sections.append(
                    {
                        "name": section_name,
                        "start_1": start_page,
                        "end_1": end_page,
                        "is_base": True,
                        "product_ids": master_products,
                    }
                )

            for product_type, page_ranges in PRODUCT_SECTIONS.items():
                for section_name, start_page, end_page in page_ranges:
                    sections.append(
                        {
                            "name": section_name,
                            "start_1": start_page,
                            "end_1": end_page,
                            "is_base": False,
                            "product_ids": _match_product_id(
                                catalog_service, bank_id, product_type
                            ),
                        }
                    )

            # Sort by start page for logical ordering.
            sections.sort(key=lambda s: s["start_1"])

            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            done_pages = 0
            output_files: list[dict] = []

            for section in sections:
                start_1 = section["start_1"]    # 1-based inclusive
                end_1 = section["end_1"]        # 1-based inclusive

                # Skip sections entirely beyond the PDF.
                if start_1 > total_pages:
                    continue

                filename = _slugify_section(section["name"]) + ".pdf"
                output_file = output_path / filename

                # Create a new PDF with just these pages (reuses the shared
                # extract_page_range helper, also used by the manual editor).
                page_count = extract_page_range(
                    master_path, start_1, end_1, output_file
                )

                actual_start_1 = max(1, start_1)
                actual_end_1 = min(end_1, total_pages)

                sha256 = _compute_sha256(output_file)

                output_files.append(
                    {
                        "filename": filename,
                        "path": str(output_file),
                        "section": section["name"],
                        "page_start": actual_start_1,
                        "page_end": actual_end_1,
                        "page_count": page_count,
                        "is_base": section["is_base"],
                        "product_ids": section["product_ids"],
                        "sha256": sha256,
                    }
                )

                done_pages += page_count
                with _split_lock:
                    _split_progress[split_id]["progress"] = int(
                        (done_pages / total_pages) * 100
                    )
                    _split_progress[split_id]["done_pages"] = done_pages

            doc.close()

            # Register split documents in the catalog.
            # Per-applicant sections are filled once per applicant (co-applicant
            # data sheet, additional party declaration).
            per_applicant_sections = {"sza_ig_tarsigenylő", "sza_esz"}
            for f in output_files:
                doc_id = f"split_{bank_id}_{Path(f['filename']).stem}"
                catalog_service.add_document(
                    {
                        "id": doc_id,
                        "title": f["section"],
                        "file_path": f["path"],
                        "source": f"split:{master_path.name}:page {f['page_start']}"
                        if f["page_start"] == f["page_end"]
                        else f"split:{master_path.name}:pages {f['page_start']}-{f['page_end']}",
                        "page_count": f["page_count"],
                        "product_ids": f["product_ids"],
                        "sha256": f["sha256"],
                        "split_from_master": True,
                        "master_page_number": f["page_start"],
                        "master_section": "base" if f["is_base"] else f["section"],
                        "per_applicant": f["section"] in per_applicant_sections,
                    }
                )

            with _split_lock:
                _split_progress[split_id]["status"] = "done"
                _split_progress[split_id]["progress"] = 100
                _split_progress[split_id]["output_files"] = output_files

            log.info(
                "Master split %s complete: %d sections written",
                split_id,
                len(output_files),
            )

        except Exception as e:
            log.exception("Master split %s failed", split_id)
            with _split_lock:
                _split_progress[split_id]["status"] = "error"
                _split_progress[split_id]["error"] = str(e)


def _match_product_id(
    catalog_service, bank_id: str, product_type: ProductType
) -> list[str]:
    """Return the product ID list under *bank_id* matching *product_type*."""
    bank = catalog_service.get_bank(bank_id)
    if not bank:
        return []
    for prod in bank.get("products", []):
        if prod["slug"] == product_type.value:
            return [prod["id"]]
    return []


#: Module-level singleton.
master_split_service = MasterSplitService()

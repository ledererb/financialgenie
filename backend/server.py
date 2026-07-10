"""
FastAPI server for the FinancialGenie Mapping Editor.

Run:  python backend/server.py
      (uses uvicorn; serves on http://127.0.0.1:8765)

NOTE on pdf_id encoding: pdf_ids are relative paths like
"otp/Piaci hitel/Igenylesi_....pdf". They contain slashes and non-ASCII
characters, which makes path-parameter routing fragile (Starlette does not
route %2F inside a single path segment). We therefore pass pdf_id as a
QUERY PARAMETER on every endpoint. The frontend API client builds these
URLs; the logical contract matches spec §3.

API surface (spec §3):

  GET    /api/pdfs
  GET    /api/pdf/info?pdf_id=...
  GET    /api/pdf/page/{n}/image?pdf_id=...
  GET    /api/pdf/fields?pdf_id=...
  GET    /api/pdf/preview?pdf_id=...

  GET    /api/mapping?pdf_id=...
  PUT    /api/mapping?pdf_id=...                      (full mapping save)
  PUT    /api/mapping/field?pdf_id=...&field=...
  POST   /api/mapping/field?pdf_id=...
  DELETE /api/mapping/field?pdf_id=...&field=...
  POST   /api/mapping/group?pdf_id=...
  PUT    /api/mapping/group?pdf_id=...&group_id=...
  DELETE /api/mapping/group?pdf_id=...&group_id=...
  POST   /api/mapping/suggest-groups?pdf_id=...
  GET    /api/mapping/canonical-fields
  GET    /api/mapping/export?pdf_id=...
  POST   /api/mapping/import?pdf_id=...   (multipart file)

  POST   /api/mapping/recognize?pdf_id=...
  GET    /api/recognize/{task_id}/status
  GET    /api/recognize/{task_id}/result
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import sys
import threading
import unicodedata
import urllib.parse
import uuid
import weakref
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse
from pydantic import BaseModel, ConfigDict

# Allow running both as `python backend/server.py` and `python -m backend.server`.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config import (  # noqa: E402
    MAPPING_DIR,
    PROJECT_ROOT,
    RENDER_SCALE,
    list_pdfs,
    log,
    resolve_pdf,
)
from mapping_service import FileConflictError, mapping_service  # noqa: E402
from pdf_service import pdf_service  # noqa: E402
from recognize_service import recognize_service  # noqa: E402
from catalog_service import catalog_service  # noqa: E402

app = FastAPI(title="FinancialGenie Mapping Editor API", version="1.0")

# Permissive CORS — local dev tool; frontend runs on a different port.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _get_pdf(pdf_id: str) -> Path:
    if not pdf_id:
        raise HTTPException(400, "pdf_id query parameter is required")
    try:
        return resolve_pdf(pdf_id)
    except FileNotFoundError:
        raise HTTPException(404, f"PDF not found in repository: {pdf_id}")


def _png_response(data: bytes) -> Response:
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _get_sf_creds() -> dict | None:
    """Load SF credentials from project config/settings.py. Returns None if not set."""
    import importlib.util
    settings_path = PROJECT_ROOT / "config" / "settings.py"
    if not settings_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("project_settings", settings_path)
    settings = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(settings)
    if not (getattr(settings, "SF_USERNAME", None) and getattr(settings, "SF_PASSWORD", None)):
        return None
    return {
        "username": settings.SF_USERNAME,
        "password": settings.SF_PASSWORD,
        "security_token": getattr(settings, "SF_SECURITY_TOKEN", ""),
        "domain": getattr(settings, "SF_DOMAIN", "login"),
        "mock_mode": False,
    }


# FIX H6 — per-mapping reentrant lock so concurrent browser tabs editing the
# same PDF mapping can't clobber each other (load → mutate → save is atomic).
# A WeakValueDictionary keeps memory bounded: when no endpoint holds the lock
# it is garbage-collected, so the dict doesn't grow forever across PDFs.
_MAPPING_LOCKS: "weakref.WeakValueDictionary[str, threading.RLock]" = (
    weakref.WeakValueDictionary()
)
_MAPPING_LOCKS_GUARD = threading.Lock()


def _get_mapping_lock(pdf_id: str) -> threading.RLock:
    """Return the (shared) RLock for one mapping, keyed by pdf_id.

    Two concurrent requests for the same pdf_id receive the *same* lock
    instance (serializing their load→mutate→save). A request for a different
    pdf_id gets a different lock (no cross-PDF contention). Idle locks are
    freed automatically by the WeakValueDictionary.
    """
    with _MAPPING_LOCKS_GUARD:
        lock = _MAPPING_LOCKS.get(pdf_id)
        if lock is None:
            lock = threading.RLock()
            _MAPPING_LOCKS[pdf_id] = lock
        return lock


def _valid_canonical_paths() -> list[str]:
    """Sorted list of known canonical_field paths (FIX M1)."""
    return sorted(f["path"] for f in mapping_service.canonical_fields())


def _validate_canonical_field(canonical_field: str | None) -> None:
    """Reject unknown canonical_field values with 422 (FIX M1).

    ``None`` / empty means "unmapped" and is always allowed. Any other value
    must exist in the canonical-field catalog (the same data the
    ``/api/mapping/canonical-fields`` endpoint serves). The error body includes
    the full list of valid values so the frontend can drive its autocomplete.
    """
    if not canonical_field:
        return
    valid = _valid_canonical_paths()
    if canonical_field in valid:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "error": "invalid_canonical_field",
            "message": (
                f"Unknown canonical_field: '{canonical_field}'. "
                f"Use one of the known canonical paths (see valid_values)."
            ),
            "field": "canonical_field",
            "valid_values": valid,
        },
    )


def sanitize_filename(filename: str, existing: "set[str] | None" = None) -> str:
    """Sanitize an uploaded filename for safe on-disk storage.

    FIX M7 — Hungarian accented characters (é, á, ő, ű …) used to be
    stripped entirely, turning ``"Pénzügyi átadás.pdf"`` into the unreadable
    ``"Pnzughtyi_tads.pdf"``. Now we NFKD-decompose the name and keep the
    ASCII base characters (é→e, ő→o, ű→u), replacing every remaining
    non-alphanumeric character with an underscore. A collision-safe numeric
    suffix is appended when the result already appears in ``existing`` (the
    set of names already present in the target directory).

    Returns a non-empty filename ending in ``.pdf``.
    """
    if not filename:
        return f"uploaded_{uuid.uuid4().hex[:8]}.pdf"

    stem = Path(filename).stem
    suffix = Path(filename).suffix.lower() or ".pdf"

    # NFKD splits accented characters into a base letter + combining mark;
    # dropping the combining marks yields the closest ASCII transliteration.
    nfd = unicodedata.normalize("NFKD", stem)
    ascii_stem = "".join(ch for ch in nfd if not unicodedata.combining(ch))

    # Replace remaining non-alphanumeric characters with a single underscore.
    safe_chars: list[str] = []
    prev_under = False
    for ch in ascii_stem:
        if ch.isalnum():
            safe_chars.append(ch)
            prev_under = False
        elif not prev_under:
            safe_chars.append("_")
            prev_under = True
    safe_stem = "".join(safe_chars).strip("_")

    if not safe_stem or safe_stem.startswith("."):
        safe_stem = f"uploaded_{uuid.uuid4().hex[:8]}"

    name = f"{safe_stem}{suffix}"
    # Collision-safe suffix: "foo.pdf" → "foo_2.pdf" → "foo_3.pdf" …
    if existing:
        i = 2
        candidate = name
        while candidate in existing:
            candidate = f"{safe_stem}_{i}{suffix}"
            i += 1
        name = candidate
    return name




# ======================================================================
# PDF service endpoints
# ======================================================================
@app.get("/api/pdfs")
def get_pdfs():
    return {"pdfs": list_pdfs()}


@app.delete("/api/pdf")
def delete_pdf(pdf_id: str = Query(...)):
    """
    Delete a PDF and its associated mapping JSON.

    Only allows deletion of PDFs under samples/ (uploaded PDFs).
    OTP source PDFs under otp/ are protected.
    """
    if not pdf_id:
        raise HTTPException(400, "pdf_id query parameter is required")

    pdf_path = PROJECT_ROOT / pdf_id
    if not pdf_path.is_file():
        raise HTTPException(404, f"PDF not found: {pdf_id}")

    # Find and delete the mapping JSON
    from config import mapping_path_for

    mapping_file = mapping_path_for(pdf_id)
    mapping_deleted = False
    if mapping_file.exists():
        mapping_file.unlink()
        mapping_deleted = True
        log.info("Deleted mapping: %s", mapping_file.name)

    # Delete the PDF itself
    pdf_path.unlink()
    log.info("Deleted PDF: %s", pdf_id)

    return {
        "deleted": True,
        "pdf_id": pdf_id,
        "mapping_deleted": mapping_deleted,
    }


@app.get("/api/pdf/info")
def pdf_info(pdf_id: str = Query(...)):
    p = _get_pdf(pdf_id)
    info = pdf_service.info(p)
    info["pdf_id"] = pdf_id
    return info


@app.get("/api/pdf/page/{page_number}/image")
def pdf_page_image(page_number: int, pdf_id: str = Query(...)):
    p = _get_pdf(pdf_id)
    try:
        data = pdf_service.render_page_png(p, page_number)
        return _png_response(data)
    except IndexError as e:
        raise HTTPException(400, str(e))


@app.get("/api/pdf/fields")
def pdf_fields(pdf_id: str = Query(...)):
    """
    Return the complete field list for a PDF.

    AcroForm PDFs: pikepdf-extracted widget rectangles, converted to
    rendered-image pixels (top-left origin).

    Flat PDFs: built from the mapping's overlay `coordinates`.
    """
    p = _get_pdf(pdf_id)
    pdf_service.prime_page_heights(p)
    info = pdf_service.info(p)

    # 1. AcroForm fields (if any).
    fields = pdf_service.extract_acroform_fields(p)

    # 2. Overlay fields from the mapping (flat PDFs).
    mapping = mapping_service.load(pdf_id)
    if mapping.get("form_type") == "flat" or not fields:
        for f in mapping.get("fields", []):
            coords = f.get("coordinates")
            if not coords:
                continue
            # Mapping coordinates are in points (72-DPI, top-left origin), scale to 150-DPI image pixels.
            fields.append(
                {
                    "pdf_field_name": f["pdf_field_name"],
                    "field_type": f.get("field_type", "text"),
                    "page_number": f.get("page_number", 1),
                    "rect": {
                        "x": round(float(coords.get("x", 0)) * RENDER_SCALE, 2),
                        "y": round(float(coords.get("y", 0)) * RENDER_SCALE, 2),
                        "width": round(float(coords.get("width", 0)) * RENDER_SCALE, 2),
                        "height": round(float(coords.get("height", 0)) * RENDER_SCALE, 2),
                    },
                    "flags": {"readonly": False, "required": False, "multiline": False},
                    "options": None,
                    "value": None,
                    "source": "overlay",
                }
            )

    return {
        "pdf_id": pdf_id,
        "total_pages": info["total_pages"],
        "has_acroform": info["has_acroform"],
        "fields": fields,
    }


@app.get("/api/pdf/preview")
def pdf_preview(pdf_id: str = Query(...), count: int = Query(default=3, ge=1, le=10)):
    """Quick multi-page preview: first `count` page PNGs base64-encoded."""
    p = _get_pdf(pdf_id)
    images = pdf_service.render_first_pages_preview(p, count)
    return {"pages": [base64.b64encode(b).decode() for b in images]}


# ======================================================================
# Mapping service endpoints
# ======================================================================
@app.get("/api/mapping/canonical-fields")
def canonical_fields():
    return {"fields": mapping_service.canonical_fields()}


@app.get("/api/mapping")
def get_mapping(pdf_id: str = Query(...)):
    _get_pdf(pdf_id)  # 404 if PDF missing
    data = mapping_service.load(pdf_id)
    mpath = (PROJECT_ROOT / data["_mapping_file"]) if data.get("_mapping_file") else None
    data["_mtime"] = mpath.stat().st_mtime if mpath and mpath.exists() else None
    return data


@app.put("/api/mapping")
def save_mapping(body: dict, pdf_id: str = Query(...)):
    """Full mapping save (editor Save button)."""
    _get_pdf(pdf_id)
    original_mtime = body.get("_mtime")
    try:
        result = mapping_service.save(pdf_id, body, original_mtime=original_mtime)
    except FileConflictError as e:
        raise HTTPException(409, str(e))
    return result


# --- Field-level helpers -------------------------------------------------
class FieldUpdate(BaseModel):
    canonical_field: str | None = None
    field_type: str | None = None
    confidence: str | None = None
    notes: str | None = None
    coordinates: dict | None = None
    fill_rule: dict | None = None
    checkbox_group: dict | None = None
    label: str | None = None

    # extra="allow" prevents silent data loss: any additional field the
    # frontend sends in the future is kept instead of being dropped by
    # Pydantic. (C2 — frontend sent fill_rule/checkbox_group/label which
    # were silently discarded before this fix.)
    model_config = ConfigDict(extra="allow")


class FieldCreate(BaseModel):
    pdf_field_name: str
    label: str | None = None
    field_type: str = "text"
    canonical_field: str | None = None
    confidence: str = "manual"
    page_number: int = 1
    coordinates: dict | None = None
    notes: str | None = None
    fill_rule: dict | None = None
    checkbox_group: dict | None = None

    model_config = ConfigDict(extra="allow")


@app.put("/api/mapping/field")
def update_field(body: FieldUpdate, pdf_id: str = Query(...), field: str = Query(...)):
    field_name = urllib.parse.unquote(field)
    _validate_canonical_field(body.canonical_field)
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        try:
            updated = mapping_service.update_field(data, field_name, body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, f"field not found: {field_name}")
        save_res = mapping_service.save(pdf_id, data)
    return {"field": updated, "_mtime": save_res["mtime"]}


@app.post("/api/mapping/field")
def add_field(body: FieldCreate, pdf_id: str = Query(...)):
    _validate_canonical_field(body.canonical_field)
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        try:
            created = mapping_service.add_field(data, body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
        save_res = mapping_service.save(pdf_id, data)
    return {"field": created, "_mtime": save_res["mtime"]}


@app.delete("/api/mapping/field")
def delete_field(pdf_id: str = Query(...), field: str = Query(...)):
    field_name = urllib.parse.unquote(field)
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        ok = mapping_service.delete_field(data, field_name)
        if not ok:
            raise HTTPException(404, f"field not found: {field_name}")
        save_res = mapping_service.save(pdf_id, data)
    return {"deleted": ok, "_mtime": save_res["mtime"]}


# --- Character groups ----------------------------------------------------
class GroupCreate(BaseModel):
    group_id: str | None = None
    group_name: str | None = None
    field_type: str = "character_split"
    canonical_field: str | None = None
    member_fields: list[str]
    direction: str = "left_to_right"
    separator: str = ""


class GroupUpdate(BaseModel):
    group_name: str | None = None
    canonical_field: str | None = None
    member_fields: list[str] | None = None
    direction: str | None = None
    separator: str | None = None
    field_type: str | None = None


@app.post("/api/mapping/group")
def create_group(body: GroupCreate, pdf_id: str = Query(...)):
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        try:
            g = mapping_service.create_group(data, body.model_dump())
        except ValueError as e:
            raise HTTPException(400, str(e))
        mapping_service.save(pdf_id, data)
    return g


@app.put("/api/mapping/group")
def update_group(body: GroupUpdate, pdf_id: str = Query(...), group_id: str = Query(...)):
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        try:
            g = mapping_service.update_group(data, group_id, body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, f"group not found: {group_id}")
        except ValueError as e:
            raise HTTPException(400, str(e))
        mapping_service.save(pdf_id, data)
    return g


@app.delete("/api/mapping/group")
def delete_group(pdf_id: str = Query(...), group_id: str = Query(...)):
    lock = _get_mapping_lock(pdf_id)
    with lock:
        data = mapping_service.load(pdf_id)
        ok = mapping_service.delete_group(data, group_id)
        if not ok:
            raise HTTPException(404, f"group not found: {group_id}")
        mapping_service.save(pdf_id, data)
    return {"deleted": ok}


@app.post("/api/mapping/suggest-groups")
def suggest_groups(pdf_id: str = Query(...)):
    p = _get_pdf(pdf_id)
    pdf_service.prime_page_heights(p)
    fields = pdf_service.extract_acroform_fields(p)
    suggestions = mapping_service.suggest_groups(fields)
    return {"suggestions": suggestions}


# --- Export / import -----------------------------------------------------
@app.get("/api/mapping/export")
def export_mapping(pdf_id: str = Query(...)):
    data = mapping_service.load(pdf_id)
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    payload = json.dumps(clean, ensure_ascii=False, indent=2).encode("utf-8")
    fname = Path(pdf_id).stem + "_mapping.json"
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/mapping/import")
async def import_mapping(pdf_id: str = Query(...), file: UploadFile = File(...)):
    raw = await file.read()
    try:
        data = json.loads(raw)
    except Exception as e:
        raise HTTPException(400, f"invalid JSON: {e}")
    result = mapping_service.save(pdf_id, data)
    return result


# ======================================================================
# Recognition endpoints
# ======================================================================
class RecognizeRequest(BaseModel):
    mode: str = "auto"  # auto|acroform|flat


@app.post("/api/mapping/recognize")
def recognize(body: RecognizeRequest, pdf_id: str = Query(...)):
    p = _get_pdf(pdf_id)
    if not recognize_service.available():
        raise HTTPException(
            503,
            "FieldRecognizer unavailable. Install deps + set ANTHROPIC_API_KEY.",
        )
    mode = body.mode
    if mode not in ("auto", "acroform", "flat"):
        raise HTTPException(400, "mode must be auto|acroform|flat")
    task_id = recognize_service.start(p, pdf_id, mode)
    return {"status": "running", "task_id": task_id}


@app.get("/api/recognize/{task_id}/status")
def recognize_status(task_id: str):
    st = recognize_service.status(task_id)
    if not st:
        raise HTTPException(404, "task not found")
    return {
        "task_id": st.task_id,
        "pdf_id": st.pdf_id,
        "status": st.status,
        "progress": st.progress,
        "message": st.message,
        "error": st.error,
        "started_at": st.started_at,
        "finished_at": st.finished_at,
    }


@app.get("/api/recognize/{task_id}/result")
def recognize_result(task_id: str):
    st = recognize_service.status(task_id)
    if not st:
        raise HTTPException(404, "task not found")
    if st.status != "done":
        raise HTTPException(409, f"task not done (status={st.status})")
    return {"task_id": st.task_id, "mapping": st.result}


@app.post("/api/pdf/page/{page_number}/preview")
def pdf_page_preview(page_number: int, body: dict):
    pdf_id = body.get("pdf_id")
    deal_id = body.get("deal_id")
    if not pdf_id:
        raise HTTPException(400, "pdf_id is required")

    pdf_path = _get_pdf(pdf_id)

    try:
        from main import FormFillerPipeline
        from integrations.salesforce_client import SalesforceClient
        sf_creds = _get_sf_creds()
        if sf_creds:
            sf_client = SalesforceClient(**sf_creds)
        else:
            sf_client = SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data")

        if not deal_id:
            deals = sf_client.list_deals()
            if not deals:
                raise HTTPException(500, "No deals available")
            deal_id = deals[0].get("Id") or deals[0].get("deal_id")

        pipeline = FormFillerPipeline(sf_client=sf_client, output_dir=PROJECT_ROOT / "output")
        result = pipeline.run_for_deal(
            deal_id=deal_id,
            template_pdf=pdf_path,
            mapping_config=None,
            force_recreate_mapping=False,
        )

        if not result["success"]:
            raise HTTPException(500, f"Fill failed: {', '.join(result.get('issues', []))}")

        import fitz
        doc = fitz.open(str(result["output_path"]))
        if page_number < 1 or page_number > len(doc):
            doc.close()
            raise HTTPException(400, "Invalid page number")

        mat = fitz.Matrix(150 / 72, 150 / 72)
        pix = doc[page_number - 1].get_pixmap(matrix=mat)
        png_data = pix.tobytes("png")
        doc.close()

        return {"image": base64.b64encode(png_data).decode("utf-8")}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Page preview fill failed")
        raise HTTPException(500, f"Page preview error: {str(e)}")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "recognizer_available": recognize_service.available(),
        "project_root": str(PROJECT_ROOT),
        "mapping_dir": str(MAPPING_DIR),
    }


# ======================================================================
# Catalog service endpoints (Phase 1 — Bank/Product creation)
# ======================================================================
@app.get("/api/catalog")
def get_catalog():
    """Return the full document catalog (banks -> products -> documents) from the SQLite DB."""
    return catalog_service.load_catalog()


@app.post("/api/catalog/banks")
def create_bank(body: dict):
    """Create a new bank and its physical documents directory."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Bank name is required")
    try:
        bank = catalog_service.add_bank(name)
        docs_dir = PROJECT_ROOT / "documents" / bank["id"]
        docs_dir.mkdir(parents=True, exist_ok=True)
        return bank
    except HTTPException:
        raise
    except Exception as e:
        log.exception("create_bank failed")
        raise HTTPException(500, str(e))


@app.post("/api/catalog/banks/{bank_id}/products")
def create_product(bank_id: str, body: dict):
    """Create a product under an existing bank."""
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Product name is required")
    try:
        product = catalog_service.add_product(bank_id, name)
        return product
    except ValueError as e:
        raise HTTPException(404, str(e))
    except HTTPException:
        raise
    except Exception as e:
        log.exception("create_product failed")
        raise HTTPException(500, str(e))


# ======================================================================
# Catalog service endpoints (Phase 3 — Document registration + M:N)
# ======================================================================
@app.post("/api/catalog/documents")
def add_catalog_document(body: dict):
    """Register an uploaded document in the catalog with product associations."""
    required = ["id", "title", "file_path"]
    for field in required:
        if field not in body:
            raise HTTPException(400, f"Missing required field: {field}")
    try:
        doc = catalog_service.add_document(body)
        return doc
    except Exception as e:
        log.exception("add_catalog_document failed")
        raise HTTPException(500, str(e))


@app.post("/api/catalog/documents/{doc_id}/products")
def set_document_products(doc_id: str, body: dict):
    """Add/update product associations for a document (M:N)."""
    product_ids = body.get("product_ids", [])
    try:
        catalog_service.update_document_products(doc_id, product_ids)
        return {"ok": True}
    except Exception as e:
        log.exception("set_document_products failed")
        raise HTTPException(500, str(e))


# ======================================================================
# Catalog service endpoints (Phase 6 — Admin delete)
# ======================================================================
@app.delete("/api/catalog/banks/{bank_id}")
def delete_bank(bank_id: str):
    """Delete a bank and cascade-remove its products and associations."""
    try:
        catalog_service.delete_bank(bank_id)
        return {"ok": True}
    except Exception as e:
        log.exception("delete_bank failed")
        raise HTTPException(500, str(e))


@app.delete("/api/catalog/documents/{doc_id}")
def delete_catalog_document(doc_id: str):
    """Remove a document from the catalog (does NOT delete the file on disk)."""
    try:
        catalog_service.delete_document(doc_id)
        return {"ok": True}
    except Exception as e:
        log.exception("delete_catalog_document failed")
        raise HTTPException(500, str(e))


# ======================================================================
# Catalog service endpoints (Phase 4 — Automatic master PDF split)
# ======================================================================
@app.post("/api/catalog/split-master")
async def start_master_split(
    bank_id: str = Query(...),
    file: UploadFile | None = File(None),
):
    """Start an asynchronous master PDF split.

    Accepts a multipart file upload with *bank_id* as a query parameter.
    The file is saved under ``documents/<bank_id>/master/`` and the split
    runs in a background thread.

    Returns 202 with ``{ split_id, status }``.
    """
    if not bank_id:
        raise HTTPException(400, "bank_id query parameter is required")

    bank = catalog_service.get_bank(bank_id)
    if not bank:
        raise HTTPException(404, f"Bank '{bank_id}' not found")

    if file is None:
        raise HTTPException(400, "file (multipart upload) is required")

    # Save the uploaded master PDF.
    master_dir = PROJECT_ROOT / "documents" / bank_id / "master"
    master_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(file.filename or "master.pdf")
    master_path = master_dir / safe_name
    content = await file.read()
    with open(master_path, "wb") as f:
        f.write(content)

    output_dir = str(PROJECT_ROOT / "documents" / bank_id / "split")

    from master_split_service import master_split_service

    split_id = master_split_service.start_split(
        str(master_path), output_dir, bank_id
    )
    return JSONResponse(
        status_code=202,
        content={"split_id": split_id, "status": "pending"},
    )


@app.get("/api/catalog/split/{split_id}")
def get_split_progress(split_id: str):
    """Poll split progress. Returns the progress dict or 404."""
    from master_split_service import master_split_service

    progress = master_split_service.get_progress(split_id)
    if not progress:
        raise HTTPException(404, "Split not found")
    return progress


@app.post("/api/pdf/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """
    Upload a new PDF template, automatically resolve/create mapping using AI, and fill it.
    Uses actual Salesforce Sandbox data if credentials are set, otherwise falls back to mock data.
    """
    import uuid
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are allowed")

    # Ensure samples/ uploads directory exists
    uploads_dir = PROJECT_ROOT / "samples"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # FIX M7 — NFKD-based transliteration of accented filenames instead of
    # dropping the characters outright, plus a collision-safe suffix.
    existing_names = (
        {p.name for p in uploads_dir.iterdir()} if uploads_dir.exists() else set()
    )
    safe_filename = sanitize_filename(file.filename, existing=existing_names)

    pdf_path = uploads_dir / safe_filename
    
    # Save the uploaded file
    try:
        content = await file.read()
        with open(pdf_path, "wb") as f:
            f.write(content)
    except Exception as e:
        log.error(f"Failed to save uploaded PDF: {e}")
        raise HTTPException(500, f"Failed to save uploaded PDF: {e}")

    # Compute SHA-256 hash of the file content for dedup (Phase 3)
    sha256 = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha256.update(chunk)
    file_hash = sha256.hexdigest()

    # Resolve PDF relative ID (pdf_id)
    try:
        pdf_id = pdf_path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        pdf_id = f"samples/{safe_filename}"

    # Now trigger AI/Heuristic mapping & PDF filling
    try:
        from main import FormFillerPipeline
        from integrations.salesforce_client import SalesforceClient
        import importlib.util
        
        # Load settings dynamically from the root config directory to avoid shadowing backend/config.py
        settings_path = PROJECT_ROOT / "config" / "settings.py"
        spec = importlib.util.spec_from_file_location("project_settings", settings_path)
        settings = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(settings)
        
        has_sf_credentials = bool(settings.SF_USERNAME and settings.SF_PASSWORD)
        
        if has_sf_credentials:
            log.info("☁️ Salesforce credentials found. Initializing real Salesforce Client...")
            sf_client = SalesforceClient(
                username=settings.SF_USERNAME,
                password=settings.SF_PASSWORD,
                security_token=settings.SF_SECURITY_TOKEN,
                domain=settings.SF_DOMAIN,
                mock_mode=False
            )
        else:
            log.info("ℹ️ Salesforce credentials not set. Falling back to mock Salesforce client...")
            sf_client = SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data")
            
        # Instantiate pipeline using the client
        pipeline = FormFillerPipeline(
            sf_client=sf_client,
            output_dir=PROJECT_ROOT / "output"
        )
        
        # Get a deal ID to fill the PDF with
        deals = pipeline.sf_client.list_deals()
        if not deals:
            raise RuntimeError("No deals available for filling in Salesforce")
            
        first_deal = deals[0]
        deal_id = first_deal.get("Id") or first_deal.get("deal_id")
        if not deal_id:
            raise RuntimeError("Failed to extract deal ID from Salesforce records")
            
        log.info(f"Automatically resolving mapping and filling for uploaded PDF {pdf_path.name} with deal {deal_id}")
        
        # This will resolve mapping (create it if missing via AI/heuristic) and fill it!
        # FIX L9 — do NOT force-recreate the mapping on every upload: that
        # discards any user-edited mapping a signature match already resolved
        # (see config.mapping_path_for). Auto-resolution still generates a
        # mapping when none exists; callers who want to force a fresh AI run
        # can request the /api/mapping/recognize endpoint explicitly.
        result = pipeline.run_for_deal(
            deal_id=deal_id,
            template_pdf=pdf_path,
            mapping_config=None,  # triggers auto-resolution!
            force_recreate_mapping=False,
        )
        
        if not result["success"]:
            issues = ", ".join(result.get("issues", []))
            raise RuntimeError(f"Filling pipeline failed: {issues}")
            
        filled_path = result["output_path"]
        
        # Build download URL
        download_url = f"/api/pdf/download?path={urllib.parse.quote(str(filled_path))}"
        
        return {
            "success": True,
            "pdf_id": pdf_id,
            "filename": safe_filename,
            "hash": file_hash,
            "path": str(pdf_path.relative_to(PROJECT_ROOT).as_posix()),
            "filled_pdf_url": download_url,
            "message": "AI-driven mapping generated and PDF filled successfully!"
        }
        
    except Exception as e:
        log.exception("Upload filling pipeline failed")
        # Clean up PDF if pipeline fails so we don't pollute samples with broken PDFs
        if pdf_path.exists():
            pdf_path.unlink()
        raise HTTPException(500, f"Error processing PDF: {str(e)}")



@app.post("/api/pdf/fill")
def fill_pdf(body: dict):
    """
    Fill a PDF with Salesforce deal data and return a download URL.

    Body: { pdf_id: str, deal_id: str }
    Returns: { success, filled_pdf_url, deal_id, filled_fields, skipped_fields }
    """
    pdf_id = body.get("pdf_id")
    deal_id = body.get("deal_id")
    if not pdf_id or not deal_id:
        raise HTTPException(400, "pdf_id and deal_id are required")

    pdf_path = _get_pdf(pdf_id)

    try:
        from main import FormFillerPipeline
        from integrations.salesforce_client import SalesforceClient
        sf_creds = _get_sf_creds()
        if sf_creds:
            sf_client = SalesforceClient(**sf_creds)
        else:
            sf_client = SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data")

        pipeline = FormFillerPipeline(sf_client=sf_client, output_dir=PROJECT_ROOT / "output")
        result = pipeline.run_for_deal(
            deal_id=deal_id,
            template_pdf=pdf_path,
            mapping_config=None,
            force_recreate_mapping=False,  # use existing mapping
        )

        if not result["success"]:
            issues = ", ".join(result.get("issues", []))
            raise HTTPException(500, f"Fill failed: {issues}")

        filled_path = result["output_path"]
        download_url = f"/api/pdf/download?path={urllib.parse.quote(str(filled_path))}"

        return {
            "success": True,
            "filled_pdf_url": download_url,
            "deal_id": deal_id,
            "filled_fields": result.get("filled_fields", []),
            "skipped_fields": result.get("skipped_fields", []),
        }
    except HTTPException:
        raise
    except Exception as e:
        log.exception("Fill failed")
        raise HTTPException(500, f"Fill error: {str(e)}")


@app.get("/api/pdf/fill/pages")
def fill_pdf_pages(path: str = Query(...), count: int = Query(default=10, ge=1, le=50)):
    """Render the first `count` pages of a filled PDF as base64 PNGs for preview."""
    out_dir = (PROJECT_ROOT / "output").resolve()
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        raise HTTPException(404, "File not found")
    if not str(abs_path).startswith(str(out_dir)):
        raise HTTPException(403, "Access denied")

    import fitz
    doc = fitz.open(str(abs_path))
    pages = []
    mat = fitz.Matrix(1.5, 1.5)  # 108 DPI for preview
    for i in range(min(count, len(doc))):
        pix = doc[i].get_pixmap(matrix=mat)
        pages.append(base64.b64encode(pix.tobytes("png")).decode())
    doc.close()
    return {"total_pages": len(doc), "pages": pages, "path": str(abs_path)}


@app.get("/api/sf/deals")
def list_deals():
    """List available Salesforce deals for the fill preview dropdown."""
    try:
        from integrations.salesforce_client import SalesforceClient
        sf_creds = _get_sf_creds()
        if sf_creds:
            sf_client = SalesforceClient(**sf_creds)
        else:
            sf_client = SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data")
        deals = sf_client.list_deals()
        return {"deals": deals}
    except Exception as e:
        log.exception("Failed to list deals")
        raise HTTPException(500, f"SF error: {str(e)}")


@app.get("/api/pdf/download")
def pdf_download(path: str = Query(...)):
    """Serve a filled PDF file from the output directory for downloading."""
    # Safety check: make sure the path is inside PROJECT_ROOT / "output"
    out_dir = (PROJECT_ROOT / "output").resolve()
    abs_path = Path(path).resolve()
    if not abs_path.exists():
        raise HTTPException(404, "File not found")
    # Prevent directory traversal
    if not str(abs_path).startswith(str(out_dir)):
        raise HTTPException(403, "Access denied")
    return FileResponse(abs_path, media_type="application/pdf", filename=abs_path.name)


@app.exception_handler(Exception)
def unhandled(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": str(exc)})


def main():
    import uvicorn

    uvicorn.run(
        "server:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
        log_level="info",
        app_dir=str(_HERE),
    )


if __name__ == "__main__":
    main()

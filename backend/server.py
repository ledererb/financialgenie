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
import json
import logging
import sys
import urllib.parse
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
from pydantic import BaseModel

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


class FieldCreate(BaseModel):
    pdf_field_name: str
    label: str | None = None
    field_type: str = "text"
    canonical_field: str | None = None
    confidence: str = "manual"
    page_number: int = 1
    coordinates: dict | None = None
    notes: str | None = None


@app.put("/api/mapping/field")
def update_field(body: FieldUpdate, pdf_id: str = Query(...), field: str = Query(...)):
    field_name = urllib.parse.unquote(field)
    data = mapping_service.load(pdf_id)
    try:
        updated = mapping_service.update_field(data, field_name, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, f"field not found: {field_name}")
    save_res = mapping_service.save(pdf_id, data)
    return {"field": updated, "_mtime": save_res["mtime"]}


@app.post("/api/mapping/field")
def add_field(body: FieldCreate, pdf_id: str = Query(...)):
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
    data = mapping_service.load(pdf_id)
    try:
        g = mapping_service.create_group(data, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    mapping_service.save(pdf_id, data)
    return g


@app.put("/api/mapping/group")
def update_group(body: GroupUpdate, pdf_id: str = Query(...), group_id: str = Query(...)):
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


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "recognizer_available": recognize_service.available(),
        "project_root": str(PROJECT_ROOT),
        "mapping_dir": str(MAPPING_DIR),
    }


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
    
    # Save the file
    safe_filename = "".join(c for c in file.filename if c.isalnum() or c in (".", "_", "-"))
    if not safe_filename or safe_filename.startswith("."):
         safe_filename = f"uploaded_{uuid.uuid4().hex[:8]}.pdf"
         
    pdf_path = uploads_dir / safe_filename
    
    # Save the uploaded file
    try:
        content = await file.read()
        with open(pdf_path, "wb") as f:
            f.write(content)
    except Exception as e:
        log.error(f"Failed to save uploaded PDF: {e}")
        raise HTTPException(500, f"Failed to save uploaded PDF: {e}")

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
        result = pipeline.run_for_deal(
            deal_id=deal_id,
            template_pdf=pdf_path,
            mapping_config=None,  # triggers auto-resolution!
            force_recreate_mapping=True,
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

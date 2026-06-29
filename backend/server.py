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
from fastapi.responses import JSONResponse, Response
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


# ======================================================================
# PDF service endpoints
# ======================================================================
@app.get("/api/pdfs")
def get_pdfs():
    return {"pdfs": list_pdfs()}


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
    mapping_service.save(pdf_id, data)
    return updated


@app.post("/api/mapping/field")
def add_field(body: FieldCreate, pdf_id: str = Query(...)):
    data = mapping_service.load(pdf_id)
    try:
        created = mapping_service.add_field(data, body.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    mapping_service.save(pdf_id, data)
    return created


@app.delete("/api/mapping/field")
def delete_field(pdf_id: str = Query(...), field: str = Query(...)):
    field_name = urllib.parse.unquote(field)
    data = mapping_service.load(pdf_id)
    ok = mapping_service.delete_field(data, field_name)
    if not ok:
        raise HTTPException(404, f"field not found: {field_name}")
    mapping_service.save(pdf_id, data)
    return {"deleted": ok}


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

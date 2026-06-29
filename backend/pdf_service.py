"""
PDF service for the Mapping Editor backend.

Responsibilities:
  * Render a page to PNG (PyMuPDF) for the frontend overlay.
  * Extract AcroForm field widgets with their on-page rectangles (pikepdf).
  * Build the combined field list (AcroForm fields + mapping-defined overlay
    fields) in a single coordinate system: top-left origin, scaled to the
    rendered image pixels at RENDER_DPI.

All coordinates returned to the frontend are in RENDERED IMAGE PIXELS at
RENDER_DPI (default 150). This is the same coordinate system the frontend
canvas uses for drawing overlays, so 1:1 scaling works.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pikepdf

from config import RENDER_DPI, RENDER_SCALE, log

#: Extra padding applied to AcroForm widget rectangles that are suspiciously
#: thin/zero-size (some OTP widgets report a 0-height /Rect).
_MIN_RECT_HEIGHT_PTS = 12.0


@dataclass
class PageInfo:
    page_size_pt: tuple[float, float]
    page_size_px: tuple[int, int]


class PdfService:
    """Stateless helpers operating on a single PDF file."""

    # ------------------------------------------------------------------
    # Info / rendering
    # ------------------------------------------------------------------
    def info(self, pdf_path: Path) -> dict:
        with fitz.open(str(pdf_path)) as doc:
            has_acroform = doc.is_form_pdf if hasattr(doc, "is_form_pdf") else bool(doc.need_appear_names)
            # More reliable AcroForm detection via catalog.
            try:
                cat = doc.pdf_catalog()
                acro = doc.xref_get_key(cat, "AcroForm")
                has_acroform = acro is not None and acro[0] != "null"
            except Exception:
                pass

            page0 = doc.load_page(0)
            w_pt, h_pt = page0.rect.width, page0.rect.height
            return {
                "pdf_id": str(pdf_path),
                "total_pages": doc.page_count,
                "has_acroform": bool(has_acroform),
                "file_size": pdf_path.stat().st_size,
                "page_size_pt": [float(w_pt), float(h_pt)],
            }

    def page_sizes(self, pdf_path: Path) -> list[PageInfo]:
        sizes: list[PageInfo] = []
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                r = page.rect
                sizes.append(
                    PageInfo(
                        page_size_pt=(r.width, r.height),
                        page_size_px=(int(r.width * RENDER_SCALE), int(r.height * RENDER_SCALE)),
                    )
                )
        return sizes

    def render_page_png(self, pdf_path: Path, page_number: int) -> bytes:
        """Render 1-indexed page_number to PNG bytes at RENDER_DPI."""
        with fitz.open(str(pdf_path)) as doc:
            if page_number < 1 or page_number > doc.page_count:
                raise IndexError(f"page {page_number} out of range (1..{doc.page_count})")
            page = doc.load_page(page_number - 1)
            mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            return pix.tobytes("png")

    def render_first_pages_preview(self, pdf_path: Path, count: int = 3) -> list[bytes]:
        out = []
        with fitz.open(str(pdf_path)) as doc:
            for i in range(min(count, doc.page_count)):
                page = doc.load_page(i)
                mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                out.append(pix.tobytes("png"))
        return out

    # ------------------------------------------------------------------
    # AcroForm field extraction
    # ------------------------------------------------------------------
    def extract_acroform_fields(self, pdf_path: Path) -> list[dict]:
        """
        Extract every AcroForm field with its widget rectangle, page, type,
        options and flags. Coordinates are converted to rendered-image pixels
        with a TOP-LEFT origin (so they line up directly with the PNG render).
        """
        fields: list[dict] = []
        try:
            pdf = pikepdf.open(str(pdf_path))
        except Exception as e:
            log.error("pikepdf open failed for %s: %s", pdf_path, e)
            return fields

        try:
            if "/AcroForm" not in pdf.Root:
                return fields
            acroform = pdf.Root["/AcroForm"]
            if "/Fields" not in acroform:
                return fields

            # Walk the AcroForm field tree. Kids can be fields themselves
            # (radio groups) whose widget annotations live deeper.
            self._walk_fields(pdf, acroform["/Fields"], fields)
        finally:
            pdf.close()

        # Patch page numbers from PyMuPDF — pikepdf /P references are unreliable
        # across PDF producers and often resolve to object ids that don't match.
        self._patch_page_numbers_from_mupdf(pdf_path, fields)

        return fields

    def _walk_fields(
        self,
        pdf,
        kids,
        out: list[dict],
        parent_name: str = "",
    ):
        for raw in kids:
            try:
                f = raw
                name = str(f.get("/T", ""))
                full_name = f"{parent_name}.{name}" if parent_name and name else (name or parent_name)
                ft = str(f.get("/FT", ""))  # /Tx /Btn /Ch
                fv = f.get("/V")
                value = self._pdf_value_to_str(fv)

                # Type mapping.
                field_type, options = self._type_and_options(f, ft)

                # Look for widget annotation(s). A field may have /Kids that are
                # widget annotations (/Subtype /Widget) OR nested fields.
                widgets: list = []
                nested: list = []
                for kid in f.get("/Kids", []) or []:
                    try:
                        sub = str(kid.get("/Subtype", ""))
                    except Exception:
                        sub = ""
                    if sub == "/Widget":
                        widgets.append(kid)
                    else:
                        nested.append(kid)

                # A field with no /Kids widgets may itself carry /Rect + /P.
                if not widgets and "/Rect" in f:
                    widgets = [f]

                flags = self._read_flags(f)

                if widgets:
                    for w in widgets:
                        rect = self._widget_rect_px(w)
                        if rect is None:
                            continue
                        out.append(
                            {
                                "pdf_field_name": full_name,
                                "field_type": field_type,
                                "page_number": rect["page_number"],
                                "rect": {
                                    "x": rect["x"],
                                    "y": rect["y"],
                                    "width": rect["width"],
                                    "height": rect["height"],
                                },
                                "flags": flags,
                                "options": options,
                                "value": value,
                                "source": "acroform",
                            }
                        )
                elif not nested:
                    # Field with no widget and no nested fields: still list it
                    # (page unknown) so the editor can map it.
                    out.append(
                        {
                            "pdf_field_name": full_name,
                            "field_type": field_type,
                            "page_number": 1,
                            "rect": {"x": 0, "y": 0, "width": 0, "height": 0},
                            "flags": flags,
                            "options": options,
                            "value": value,
                            "source": "acroform",
                            "no_widget": True,
                        }
                    )

                if nested:
                    self._walk_fields(pdf, nested, out, full_name)
            except Exception as e:
                log.debug("field walk error: %s", e)
                continue

    # ------------------------------------------------------------------
    # Coordinate conversion
    # ------------------------------------------------------------------
    def _widget_rect_px(self, widget) -> dict | None:
        """Return {page_number,x,y,width,height} in image px (top-left origin)."""
        try:
            raw_rect = widget["/Rect"]
            # /Rect is [x0, y0, x1, y1] in PDF user space (bottom-left origin).
            x0, y0, x1, y1 = [float(v) for v in raw_rect]
        except Exception:
            return None

        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0

        # Page number assigned later by _patch_page_numbers_from_mupdf.
        page_number = 1

        # We need the page height to flip the y-axis.
        page_h_pt = self._page_height_pt.get(page_number)
        if page_h_pt is None:
            # Fall back: assume A4 portrait; rect will be approximate.
            page_h_pt = 842.0

        # Convert to top-left origin image pixels.
        x_px = x0 * RENDER_SCALE
        # bottom edge (y1) maps to top in top-left system:
        y_top_px = (page_h_pt - y1) * RENDER_SCALE
        width_px = (x1 - x0) * RENDER_SCALE
        height_px = (y1 - y0) * RENDER_SCALE

        if height_px < 2 and width_px > 0:
            height_px = _MIN_RECT_HEIGHT_PTS * RENDER_SCALE

        return {
            "page_number": 1,
            "x": round(x_px, 2),
            "y": round(y_top_px, 2),
            "width": round(width_px, 2),
            "height": round(height_px, 2),
        }

    # Cache page heights so we don't reopen the PDF for every widget.
    _page_height_pt: dict[int, float] = {}

    # ------------------------------------------------------------------
    # Coordinate & page patching via PyMuPDF
    # ------------------------------------------------------------------
    def _patch_page_numbers_from_mupdf(self, pdf_path: Path, fields: list[dict]):
        """Use PyMuPDF widgets() to fix page numbers AND rect coordinates.

        pikepdf /Rect values are sometimes pre-flipped (already in top-left
        origin), sometimes not — it depends on the PDF producer. PyMuPDF's
        widget.rect is always in standard PDF user space (bottom-left origin),
        so we convert to rendered-image top-left pixels consistently.
        """
        try:
            doc = fitz.open(str(pdf_path))
            # Build {field_name: list_of_geometries} to handle multiple widgets sharing the same field name
            name_to_geos: dict[str, list[dict]] = {}
            for i, page in enumerate(doc):
                for w in page.widgets():
                    if w.field_name:
                        r = w.rect
                        geo = {
                            "page": i + 1,
                            "x": round(r.x0 * RENDER_SCALE, 2),
                            "y": round(r.y0 * RENDER_SCALE, 2),
                            "width": round(r.width * RENDER_SCALE, 2),
                            "height": round(r.height * RENDER_SCALE, 2),
                        }
                        name_to_geos.setdefault(w.field_name, []).append(geo)
            doc.close()

            patched_page = 0
            patched_rect = 0
            name_counts: dict[str, int] = {}
            for f in fields:
                name = f.get("pdf_field_name", "")
                geos = name_to_geos.get(name)
                if not geos:
                    continue

                # Match by occurrence index
                idx = name_counts.get(name, 0)
                if idx < len(geos):
                    geo = geos[idx]
                    name_counts[name] = idx + 1
                else:
                    geo = geos[-1]

                f["page_number"] = geo["page"]
                patched_page += 1
                if f.get("rect"):
                    f["rect"] = {
                        "x": geo["x"],
                        "y": geo["y"],
                        "width": geo["width"],
                        "height": geo["height"],
                    }
                    patched_rect += 1
            if patched_page:
                log.info(
                    "PyMuPDF patched %d fields (page numbers) / %d (rects)",
                    patched_page,
                    patched_rect,
                )
        except Exception as e:
            log.warning("PyMuPDF coordinate patch failed: %s", e)

    def prime_page_heights(self, pdf_path: Path):
        """Populate the page-height cache from PyMuPDF."""
        self._page_height_pt = {}
        try:
            with fitz.open(str(pdf_path)) as doc:
                for i, page in enumerate(doc):
                    self._page_height_pt[i + 1] = float(page.rect.height)
        except Exception as e:
            log.warning("could not prime page heights: %s", e)

    # ------------------------------------------------------------------
    # Type / options / flags helpers
    # ------------------------------------------------------------------
    def _type_and_options(self, f, ft: str) -> tuple[str, list[str] | None]:
        options = None
        if ft == "/Tx":
            ftype = "text"
        elif ft == "/Btn":
            # radio vs checkbox: if /Kids have /AP states look like export values.
            try:
                ftype = "checkbox"
                # Radio button: parent has multiple widget kids with /AP /N states.
                kids = f.get("/Kids", []) or []
                export_states = set()
                for k in kids:
                    ap = k.get("/AP")
                    if ap and "/N" in ap:
                        for st in ap["/N"].keys():
                            s = str(st)
                            if s not in ("/Off",):
                                export_states.add(s.lstrip("/"))
                if len(export_states) > 1:
                    ftype = "dropdown"  # treat radio as dropdown
                    options = sorted(export_states)
            except Exception:
                ftype = "checkbox"
        elif ft == "/Ch":
            ftype = "dropdown"
            try:
                opts = f.get("/Opt") or []
                options = [str(o) if not isinstance(o, pikepdf.Array) else str(o[1]) for o in opts]
            except Exception:
                options = None
        else:
            ftype = "text"
        return ftype, options

    def _read_flags(self, f) -> dict:
        flags = {"readonly": False, "required": False, "multiline": False}
        try:
            ff = int(f.get("/Ff", 0) or 0)
            flags["readonly"] = bool(ff & 1)
            flags["required"] = bool(ff & 2)
            flags["multiline"] = bool(ff & (1 << 12))
        except Exception:
            pass
        return flags

    def _pdf_value_to_str(self, v) -> str | None:
        try:
            if v is None or str(v) == "":
                return None
            if isinstance(v, pikepdf.Array):
                return " ".join(str(x) for x in v)
            return str(v)
        except Exception:
            return None


pdf_service = PdfService()

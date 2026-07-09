"""AcroForm field extraction (FIX L8).

Canonical home for the logic that walks a PDF's AcroForm widgets and returns
their on-page rectangles, page numbers, types, options and flags. Previously
this lived only in ``backend/pdf_service.py``, which forced the ``src/``
package (``src/ai/field_recognizer.py``) to import from ``backend/`` — a
layering inversion. Extracting it into ``src/engine/`` removes that inverted
dependency: both ``backend/pdf_service`` and ``src/ai/field_recognizer`` now
import from here.

Coordinates are returned in RENDERED IMAGE PIXELS with a TOP-LEFT origin at
``render_scale`` (default 150/72 = RENDER_DPI/72), matching the PNG renders.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pikepdf

log = logging.getLogger(__name__)

#: Extra padding applied to AcroForm widget rectangles that are suspiciously
#: thin/zero-size (some OTP widgets report a 0-height /Rect).
_MIN_RECT_HEIGHT_PTS = 12.0

#: Standard A4 portrait height in PDF points, used when the real page height
#: cannot be determined (fallback only — PyMuPDF patching corrects it later).
_DEFAULT_PAGE_HEIGHT_PT = 842.0


def extract_acroform_fields(
    pdf_path: Path,
    render_scale: float = 150.0 / 72.0,
) -> list[dict]:
    """
    Extract every AcroForm field with its widget rectangle, page, type,
    options and flags. Coordinates are converted to rendered-image pixels
    with a TOP-LEFT origin (so they line up directly with the PNG render).

    Args:
        pdf_path: PDF file to inspect.
        render_scale: multiplier from PDF user-space points (72 dpi) to the
            rendered image pixels (default ``150/72``).
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

        page_heights = _prime_page_heights(pdf_path)
        _walk_fields(pdf, acroform["/Fields"], fields, render_scale, page_heights)
    finally:
        pdf.close()

    # Patch page numbers from PyMuPDF — pikepdf /P references are unreliable.
    _patch_page_numbers_from_mupdf(pdf_path, fields, render_scale)

    # Fallback: pick up widgets PyMuPDF sees but pikepdf missed (deeply
    # nested AcroForm hierarchies, radio widgets stored outside /Fields).
    _add_missing_mupdf_widgets(pdf_path, fields, render_scale)

    return fields


def _prime_page_heights(pdf_path: Path) -> dict[int, float]:
    """Return ``{1-based page number: height in PDF points}`` via PyMuPDF."""
    heights: dict[int, float] = {}
    try:
        with fitz.open(str(pdf_path)) as doc:
            for i, page in enumerate(doc):
                heights[i + 1] = float(page.rect.height)
    except Exception as e:
        log.warning("could not prime page heights: %s", e)
    return heights


def _walk_fields(
    pdf,
    kids,
    out: list[dict],
    render_scale: float,
    page_heights: dict[int, float],
    parent_name: str = "",
):
    for raw in kids:
        try:
            f = raw
            name = str(f.get("/T", ""))
            full_name = f"{parent_name}.{name}" if parent_name and name else (name or parent_name)
            ft = str(f.get("/FT", ""))  # /Tx /Btn /Ch
            fv = f.get("/V")
            value = _pdf_value_to_str(fv)

            field_type, options = _type_and_options(f, ft)

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

            if not widgets and "/Rect" in f:
                widgets = [f]

            flags = _read_flags(f)

            if widgets:
                for w in widgets:
                    rect = _widget_rect_px(w, render_scale, page_heights)
                    if rect is None:
                        continue

                    actual_name = full_name
                    actual_type = field_type

                    if field_type == "dropdown" and len(widgets) > 1:
                        export_val = None
                        try:
                            ap = w.get("/AP")
                            if ap and "/N" in ap:
                                for st in ap["/N"].keys():
                                    s = str(st)
                                    if s not in ("/Off",):
                                        export_val = s.lstrip("/")
                                        break
                        except Exception:
                            pass

                        if export_val:
                            actual_name = f"{full_name}___{export_val}"
                            actual_type = "checkbox"

                    out.append(
                        {
                            "pdf_field_name": actual_name,
                            "field_type": actual_type,
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
                _walk_fields(pdf, nested, out, render_scale, page_heights, full_name)
        except Exception as e:
            log.debug("field walk error: %s", e)
            continue


def _widget_rect_px(
    widget,
    render_scale: float,
    page_heights: dict[int, float],
) -> dict | None:
    """Return ``{page_number, x, y, width, height}`` in image px (top-left)."""
    try:
        raw_rect = widget["/Rect"]
        x0, y0, x1, y1 = [float(v) for v in raw_rect]
    except Exception:
        return None

    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0

    page_number = 1
    page_h_pt = page_heights.get(page_number, _DEFAULT_PAGE_HEIGHT_PT)

    x_px = x0 * render_scale
    y_top_px = (page_h_pt - y1) * render_scale
    width_px = (x1 - x0) * render_scale
    height_px = (y1 - y0) * render_scale

    if height_px < 2 and width_px > 0:
        height_px = _MIN_RECT_HEIGHT_PTS * render_scale

    return {
        "page_number": 1,
        "x": round(x_px, 2),
        "y": round(y_top_px, 2),
        "width": round(width_px, 2),
        "height": round(height_px, 2),
    }


def _patch_page_numbers_from_mupdf(
    pdf_path: Path,
    fields: list[dict],
    render_scale: float,
):
    """Use PyMuPDF widgets() to fix page numbers AND rect coordinates.

    pikepdf /Rect values are sometimes pre-flipped; PyMuPDF's widget.rect is
    always standard PDF user space (bottom-left), so we convert consistently.
    """
    try:
        doc = fitz.open(str(pdf_path))
        name_to_geos: dict[str, list[dict]] = {}
        for i, page in enumerate(doc):
            for w in page.widgets():
                if w.field_name:
                    r = w.rect
                    geo = {
                        "page": i + 1,
                        "x": round(r.x0 * render_scale, 2),
                        "y": round(r.y0 * render_scale, 2),
                        "width": round(r.width * render_scale, 2),
                        "height": round(r.height * render_scale, 2),
                    }
                    name_to_geos.setdefault(w.field_name, []).append(geo)
        doc.close()

        patched_page = 0
        patched_rect = 0
        matched_indices: dict[str, set[int]] = {}
        for f in fields:
            name = f.get("pdf_field_name", "")
            base_name = name.split("___")[0]
            geos = name_to_geos.get(base_name)
            if not geos:
                continue

            rect = f.get("rect")
            if not rect:
                continue

            best_geo_idx = -1
            best_diff = float("inf")
            already_matched = matched_indices.setdefault(base_name, set())

            for idx, geo in enumerate(geos):
                if idx in already_matched:
                    continue
                diff_x = abs(rect["x"] - geo["x"])
                diff_w = abs(rect["width"] - geo["width"])
                if diff_x < 5.0 and diff_w < 5.0:
                    total_diff = diff_x + diff_w
                    if total_diff < best_diff:
                        best_diff = total_diff
                        best_geo_idx = idx

            if best_geo_idx != -1:
                geo = geos[best_geo_idx]
                already_matched.add(best_geo_idx)
            else:
                fallback_idx = next(
                    (i for i in range(len(geos)) if i not in already_matched),
                    len(geos) - 1,
                )
                geo = geos[fallback_idx]
                already_matched.add(fallback_idx)

            f["page_number"] = geo["page"]
            patched_page += 1
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


def _add_missing_mupdf_widgets(
    pdf_path: Path,
    fields: list[dict],
    render_scale: float,
):
    """Add widgets PyMuPDF finds but pikepdf missed (deeply nested / radio)."""
    existing_names = {f["pdf_field_name"] for f in fields}
    existing_bases = {n.split("___")[0] for n in existing_names}

    try:
        doc = fitz.open(str(pdf_path))
        widgets_by_name: dict[str, list] = {}
        for page in doc:
            for w in page.widgets():
                if w.field_name:
                    widgets_by_name.setdefault(w.field_name, []).append(
                        (page.number, w)
                    )

        added = 0
        for name, widget_list in widgets_by_name.items():
            if name in existing_bases:
                continue

            if len(widget_list) == 1:
                page_num, w = widget_list[0]
                r = w.rect
                ft = {1: "checkbox", 2: "text", 5: "checkbox", 7: "text"}.get(
                    w.field_type, "text"
                )
                fields.append({
                    "pdf_field_name": name,
                    "field_type": ft,
                    "page_number": page_num + 1,
                    "rect": {
                        "x": round(r.x0 * render_scale, 2),
                        "y": round(r.y0 * render_scale, 2),
                        "width": round(r.width * render_scale, 2),
                        "height": round(r.height * render_scale, 2),
                    },
                    "flags": "",
                    "options": [],
                    "value": w.field_value or "",
                    "source": "acroform",
                })
                added += 1
            else:
                for idx, (page_num, w) in enumerate(widget_list):
                    r = w.rect
                    export_val = None
                    try:
                        states = w.button_states()
                        if states and "normal" in states:
                            for st in states["normal"]:
                                if st != "Off":
                                    export_val = st
                                    break
                    except Exception:
                        pass

                    suffix = export_val or str(idx)
                    unique_name = f"{name}___{suffix}"
                    if unique_name in existing_names:
                        continue

                    fields.append({
                        "pdf_field_name": unique_name,
                        "field_type": "checkbox",
                        "page_number": page_num + 1,
                        "rect": {
                            "x": round(r.x0 * render_scale, 2),
                            "y": round(r.y0 * render_scale, 2),
                            "width": round(r.width * render_scale, 2),
                            "height": round(r.height * render_scale, 2),
                        },
                        "flags": "",
                        "options": [],
                        "value": w.field_value or "",
                        "source": "acroform",
                    })
                    added += 1

        doc.close()
        if added:
            log.info("PyMuPDF fallback added %d missing widgets", added)
    except Exception as e:
        log.warning("PyMuPDF fallback widget extraction failed: %s", e)


def _type_and_options(f, ft: str) -> tuple[str, list[str] | None]:
    options = None
    if ft == "/Tx":
        ftype = "text"
    elif ft == "/Btn":
        try:
            ftype = "checkbox"
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
                ftype = "dropdown"
                options = sorted(export_states)
        except Exception:
            ftype = "checkbox"
    elif ft == "/Ch":
        ftype = "dropdown"
        try:
            opts = f.get("/Opt") or []
            options = [
                str(o) if not isinstance(o, pikepdf.Array) else str(o[1])
                for o in opts
            ]
        except Exception:
            options = None
    else:
        ftype = "text"
    return ftype, options


def _read_flags(f) -> dict:
    flags = {"readonly": False, "required": False, "multiline": False}
    try:
        ff = int(f.get("/Ff", 0) or 0)
        flags["readonly"] = bool(ff & 1)
        flags["required"] = bool(ff & 2)
        flags["multiline"] = bool(ff & (1 << 12))
    except Exception:
        pass
    return flags


def _pdf_value_to_str(v) -> str | None:
    try:
        if v is None or str(v) == "":
            return None
        if isinstance(v, pikepdf.Array):
            return " ".join(str(x) for x in v)
        return str(v)
    except Exception:
        return None

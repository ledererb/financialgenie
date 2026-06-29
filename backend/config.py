"""
Backend for the FinancialGenie Mapping Editor.

Standalone FastAPI service. It does NOT import the filling pipeline
(src/main.py FormFillerPipeline). It only reuses the MappingConfig /
FieldRecognizer classes from src/ai/field_recognizer.py.

Run:  python backend/server.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("mapping_editor")

#: Absolute path to the project root (parent of backend/).
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

#: Add src/ to sys.path so we can reuse MappingConfig / FieldRecognizer.
_SRC = PROJECT_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: Directories that hold PDFs we can open in the editor.
PDF_ROOTS: list[Path] = [
    PROJECT_ROOT / "otp",
    PROJECT_ROOT / "samples",
]

#: Where mapping JSON files live.
MAPPING_DIR: Path = PROJECT_ROOT / "src" / "mapping"

#: Render DPI used when rasterising pages to PNG for the frontend overlay.
RENDER_DPI: int = 150

#: Scale factor between PDF user-space points (72 dpi) and the rendered image.
RENDER_SCALE: float = RENDER_DPI / 72.0


def resolve_pdf(pdf_id: str) -> Path:
    """
    Resolve a URL-decoded pdf_id (path relative to project root) to an absolute
    Path. Raises FileNotFoundError if the file does not exist.
    """
    p = PROJECT_ROOT / pdf_id
    if p.is_file():
        return p
    # Some callers may pass the path relative to otp/ or samples/.
    for root in PDF_ROOTS:
        cand = root / pdf_id
        if cand.is_file():
            return cand
    raise FileNotFoundError(f"PDF not found in repository: {pdf_id}")


def mapping_path_for(pdf_id: str, _depth: int = 0) -> Path:
    """
    Return the mapping JSON path for a given pdf_id.

    Convention mirrors the existing src/mapping files:
        otp/Piaci hitel/Igenylesi_...pdf  ->  otp_piaci_hitel_mapping.json
        samples/flat_sample.pdf            ->  flat_sample_mapping.json
    We also try a couple of fallbacks so an unknown PDF still resolves.
    """
    import unicodedata

    def slug(s: str) -> str:
        nfd = unicodedata.normalize("NFD", s)
        no_accents = "".join(c for c in nfd if not unicodedata.combining(c))
        out = []
        prev_under = False
        for ch in no_accents.lower():
            if ch.isalnum():
                out.append(ch)
                prev_under = False
            elif not prev_under:
                out.append("_")
                prev_under = True
        return "".join(out).strip("_")

    stem = Path(pdf_id).stem
    parent_dir = Path(pdf_id).parent.name if Path(pdf_id).parent.name else ""

    stem_slug = slug(stem)
    stem_words = set(w for w in stem_slug.split("_") if len(w) >= 3)

    # 1. Exact stem match
    candidate = MAPPING_DIR / f"{stem}_mapping.json"
    if candidate.exists():
        return candidate

    # 2. Word-level overlap: best mapping = most overlapping non-trivial words.
    #    Needs >=2 overlapping words AND >=25% of stem words matched.
    #    e.g. "Partner_nyilatkozat_..." ↔ "otp_partner_nyilatkozat" = 2 words.
    if len(stem_words) >= 2:
        best: "tuple[int, Path] | None" = None
        for existing in sorted(MAPPING_DIR.glob("*_mapping.json")):
            exist_slug = slug(existing.stem.replace("_mapping", ""))
            exist_words = set(exist_slug.split("_"))
            overlap = stem_words & exist_words
            score = len(overlap)
            if score >= 2 and score / len(stem_words) >= 0.25:
                if best is None or score > best[0]:
                    best = (score, existing)
        if best is not None:
            return best[1]

    # 3. Parent directory slug match (only for master files, e.g. "Igenylesi_dokumentumok")
    if parent_dir and "igenylesi_dokumentumok" in stem_slug:
        parent_slug = slug(parent_dir)
        parent_words = set(parent_slug.split("_"))
        best2: "tuple[int, Path] | None" = None
        for existing in sorted(MAPPING_DIR.glob("*_mapping.json")):
            exist_slug = slug(existing.stem.replace("_mapping", ""))
            exist_words = set(exist_slug.split("_"))
            overlap = parent_words & exist_words
            score = len(overlap)
            if score >= 1 and score / max(len(parent_words), 1) >= 0.3:
                if best2 is None or score > best2[0]:
                    best2 = (score, existing)
        if best2 is not None:
            return best2[1]

    # 4. Cross-directory alias: same filename appears in another OTP product
    #    folder where it already found a mapping.  e.g. the master
    #    "Igenylesi_dokumentumok_..." PDF in "Otthon Start" should reuse the
    #    mapping from the "Piaci hitel" copy.
    if parent_dir and _depth < 1:
        for root in PDF_ROOTS:
            if not root.is_dir():
                continue
            for sibling_dir in root.iterdir():
                if not sibling_dir.is_dir() or sibling_dir.name == parent_dir:
                    continue
                sibling_pdf = sibling_dir / f"{stem}.pdf"
                if sibling_pdf.is_file():
                    sibling_id = sibling_pdf.relative_to(PROJECT_ROOT).as_posix()
                    # Recurse once to resolve that sibling; cap depth at 1.
                    sibling_mapping = mapping_path_for(sibling_id, _depth + 1)
                    if sibling_mapping.exists():
                        sibling_slug = slug(sibling_mapping.stem.replace("_mapping", ""))
                        # Only reuse if the mapping looks like a real one
                        # (not a default auto-generated name).
                        if sibling_slug != slug(stem):
                            return sibling_mapping

    # 5. Exact stem slug match (fallback)
    if stem_slug:
        for existing in MAPPING_DIR.glob("*_mapping.json"):
            if slug(existing.stem.replace("_mapping", "")) == stem_slug:
                return existing

    # 6. Default new-file location.
    return MAPPING_DIR / f"{stem_slug or stem}_mapping.json"


def list_pdfs() -> list[dict]:
    """List every PDF under otp/ and samples/."""
    out: list[dict] = []
    seen: set[Path] = set()
    for root in PDF_ROOTS:
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.pdf")):
            if p in seen:
                continue
            seen.add(p)
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            out.append(
                {
                    "pdf_id": rel,
                    "name": p.stem,
                    "size_bytes": p.stat().st_size,
                    "parent": p.parent.relative_to(PROJECT_ROOT).as_posix(),
                }
            )
    return out

"""P0 regressions for PDF fallback filling and filled-page previews."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import fitz
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PREVIEW_TESTS = PROJECT_ROOT / "tests" / "pdf_preview_subprocess.py"


def _run_preview_test_in_subprocess(node_id: str, tmp_path: Path) -> None:
    """Run backend preview tests using the child pytest session's isolation."""
    env = os.environ.copy()
    for name in ("CATALOG_DB_PATH", "AUTH_DB_PATH", "AUTH_SECRET"):
        env.pop(name, None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(tmp_path / "child-pytest"),
            f"{_PREVIEW_TESTS}::{node_id}",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    details = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert result.returncode == 0, details


def _real_state_fingerprints() -> dict[str, dict[str, int | str]]:
    fingerprints = {}
    for relative in ("catalog/catalog.db", "catalog/.auth_secret"):
        path = PROJECT_ROOT / relative
        stat = path.stat()
        fingerprints[relative] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    return fingerprints


def test_pytest_isolates_import_time_state_before_test_module_imports(tmp_path):
    """Collection-time server imports must never touch real catalog state."""
    real_before = _real_state_fingerprints()
    probe_output = tmp_path / "isolation-probe.json"
    env = os.environ.copy()
    for name in ("CATALOG_DB_PATH", "AUTH_DB_PATH", "AUTH_SECRET"):
        env.pop(name, None)
    env["FG_TEST_ISOLATION_PROBE"] = str(probe_output)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--basetemp",
            str(tmp_path / "child-pytest"),
            f"{_PREVIEW_TESTS}::test_import_time_state_is_session_isolated",
        ],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    details = f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    assert _real_state_fingerprints() == real_before
    assert result.returncode == 0, details
    probe = json.loads(probe_output.read_text(encoding="utf-8"))
    state_dir = Path(probe["state_dir"])
    assert not state_dir.is_relative_to(PROJECT_ROOT)
    assert not state_dir.exists(), "Session-temporary import state was not cleaned up"


def test_persisted_mapping_uses_string_date_fragments():
    mapping_path = (
        PROJECT_ROOT
        / "src"
        / "mapping"
        / "ertekbecslo_megrendelolap_240920_2_mapping.json"
    )
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    fields = {
        field["pdf_field_name"]: field
        for field in data["fields"]
        if field.get("fragment") is not None
    }

    assert set(fields) == {
        "Kelt_ing_adatlap_év",
        "Kelt_ing_adatlap_hónap",
        "Kelt_ing_adatlap_nap",
    }
    assert all(isinstance(field["fragment"], str) for field in fields.values())
    assert {
        name: field["fragment"] for name, field in fields.items()
    } == {
        "Kelt_ing_adatlap_év": "year",
        "Kelt_ing_adatlap_hónap": "month",
        "Kelt_ing_adatlap_nap": "day",
    }


@pytest.mark.parametrize(
    "field_type,value,expected",
    [
        (fitz.PDF_WIDGET_TYPE_TEXT, "Fallback Value", "Fallback Value"),
        (fitz.PDF_WIDGET_TYPE_CHECKBOX, "igen", "Yes"),
    ],
    ids=["text", "checkbox"],
)
def test_mupdf_fill_missing_fills_unhandled_widget(
    tmp_path, field_type, value, expected
):
    from src.engine.pdf_filler import AcroFormFiller, FillingResult

    pdf_path = tmp_path / "fallback.pdf"
    doc = fitz.open()
    page = doc.new_page()
    widget = fitz.Widget()
    widget.field_name = "fallback_field"
    widget.field_type = field_type
    widget.rect = fitz.Rect(50, 50, 250, 80)
    page.add_widget(widget)
    doc.save(pdf_path)
    doc.close()

    result = FillingResult()
    filler = AcroFormFiller()
    filler._mupdf_fill_missing(
        pdf_path,
        {"fallback_field": "Contact.Name"},
        {"Contact.Name": value},
        result,
    )

    assert result.filled_fields == ["fallback_field"]
    with fitz.open(pdf_path) as filled_doc:
        filled_page = filled_doc[0]
        filled_widget = next(filled_page.widgets())
        assert filled_widget.field_value == expected


def test_fill_pages_returns_total_pages_to_authenticated_member(tmp_path):
    _run_preview_test_in_subprocess(
        "test_fill_pages_returns_total_pages_to_authenticated_member", tmp_path
    )


def test_fill_pages_closes_document_when_rendering_fails(tmp_path):
    _run_preview_test_in_subprocess(
        "test_fill_pages_closes_document_when_rendering_fails", tmp_path
    )


def test_fill_pages_rejects_output_sibling(tmp_path):
    _run_preview_test_in_subprocess("test_fill_pages_rejects_output_sibling", tmp_path)


def test_fill_pages_rejects_directory(tmp_path):
    _run_preview_test_in_subprocess("test_fill_pages_rejects_directory", tmp_path)

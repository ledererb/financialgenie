"""Rendered-appearance regressions using the real OTP source PDFs."""
import logging
from pathlib import Path

import fitz
import pytest

from src.engine.pdf_filler import AcroFormFiller


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LONG_EMPLOYER = (
    "European Union Agency for the Operational Management of Large-Scale IT "
    "Systems in the Area of Freedom, Security and Justice (eu-LISA)"
)
V_FORM = (
    "OTP/Piaci hitel/"
    "V_szamu_fuggelek_Penzugyi_szolgaltatas_kozvetiteseben_valo_"
    "kozremukodesre_vonatkozo_ugyfel_ceg_nyilatkozat_20250601.pdf"
)


def _normalized(text: str) -> str:
    """Ignore layout whitespace while retaining every visible glyph."""
    return "".join(text.split())


def _field_glyphs(page: fitz.Page, rect: fitz.Rect) -> list:
    """Return complete trace spans touching the field, including overflow."""
    glyphs = []
    for span in page.get_texttrace():
        if fitz.Rect(span["bbox"]).intersects(rect):
            glyphs.extend(span["chars"])
    return glyphs


def _fill_field(
    tmp_path: Path,
    template_relative_path: str,
    field_name: str,
    value: str,
) -> Path:
    output_path = tmp_path / "filled.pdf"
    result = AcroFormFiller().fill(
        PROJECT_ROOT / template_relative_path,
        output_path,
        {field_name: value},
        {field_name: field_name},
    )
    assert result.success, result.errors
    return output_path


def _assert_complete_value_is_rendered(
    tmp_path: Path,
    template_relative_path: str,
    field_name: str,
    value: str,
    *,
    expected_page: int,
    expected_visible: str | None = None,
    expected_cells: int | None = None,
) -> None:
    output_path = _fill_field(
        tmp_path, template_relative_path, field_name, value
    )
    expected_visible = expected_visible or value
    with fitz.open(output_path) as document:
        matches = [
            (page.number + 1, page, widget)
            for page in document
            for widget in (page.widgets() or [])
            if widget.field_name == field_name
        ]
        assert len(matches) == 1
        page_number, page, widget = matches[0]
        assert page_number == expected_page
        assert widget.field_value == value

        rendered_text = page.get_text(clip=widget.rect)
        assert _normalized(expected_visible) in _normalized(rendered_text), (
            f"Complete value is not visible in {field_name}: "
            f"expected {expected_visible!r}, rendered {rendered_text!r}"
        )

        glyphs = _field_glyphs(page, widget.rect)
        glyph_text = "".join(chr(glyph[0]) for glyph in glyphs)
        assert _normalized(glyph_text) == _normalized(expected_visible)
        for glyph in glyphs:
            glyph_bbox = fitz.Rect(glyph[3])
            assert widget.rect.contains(glyph_bbox), (
                f"Glyph {chr(glyph[0])!r} escapes {field_name}: "
                f"bbox={glyph_bbox}, widget={widget.rect}"
            )

        if expected_cells is not None:
            assert widget.text_maxlen == expected_cells
            visible_glyphs = [
                glyph for glyph in glyphs if not chr(glyph[0]).isspace()
            ]
            assert len(visible_glyphs) == expected_cells
            cell_width = widget.rect.width / expected_cells
            for index, glyph in enumerate(visible_glyphs):
                cell = fitz.Rect(
                    widget.rect.x0 + index * cell_width,
                    widget.rect.y0,
                    widget.rect.x0 + (index + 1) * cell_width,
                    widget.rect.y1,
                )
                assert cell.contains(fitz.Rect(glyph[3])), (
                    f"Glyph {index} is outside its original comb cell: "
                    f"bbox={fitz.Rect(glyph[3])}, cell={cell}"
                )


def test_d07_long_employer_is_fully_visible_inside_widget(tmp_path):
    _assert_complete_value_is_rendered(
        tmp_path,
        "documents/otp_bank/split/munkaltato_adatlap.pdf",
        "MA_IG_munáltató_neve",
        LONG_EMPLOYER,
        expected_page=1,
    )


def test_d02_original_page_4_long_employer_is_fully_visible_inside_widget(tmp_path):
    _assert_complete_value_is_rendered(
        tmp_path,
        V_FORM,
        "Szövegmező 38",
        LONG_EMPLOYER,
        expected_page=4,
    )


def test_d08_original_phone_normalizes_plus_and_preserves_11_comb_cells(tmp_path):
    _assert_complete_value_is_rendered(
        tmp_path,
        "documents/otp_bank/split/sza_esz.pdf",
        "SZA_ESZ_mobil",
        "+36308379070",
        expected_page=1,
        expected_visible="36308379070",
        expected_cells=11,
    )


def test_unfittable_comb_warns_and_preserves_layout(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    output_path = _fill_field(
        tmp_path,
        "documents/otp_bank/split/sza_esz.pdf",
        "SZA_ESZ_mobil",
        "+363083790700",
    )

    with fitz.open(output_path) as document:
        widget = next(
            widget
            for widget in (document[0].widgets() or [])
            if widget.field_name == "SZA_ESZ_mobil"
        )
        assert widget.field_value == "+363083790700"
        assert widget.text_maxlen == 11
        assert widget.text_fontsize == 10
        assert widget.rect == fitz.Rect(
            25.76180076599121, 700.3740234375, 248.17300415039062, 719.717041015625
        )
    assert "SZA_ESZ_mobil" in caplog.text
    assert "Comb" in caplog.text


def _create_text_widget(path: Path, *, multiline: bool) -> None:
    document = fitz.open()
    page = document.new_page()
    widget = fitz.Widget()
    widget.field_name = "appearance_settings"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.field_flags = (1 << 12) if multiline else 0
    widget.text_font = "Helv"
    widget.text_fontsize = 12
    widget.rect = fitz.Rect(50, 50, 250, 74)
    page.add_widget(widget)
    document.save(path)
    document.close()


@pytest.mark.parametrize(
    "multiline,value",
    [
        (True, LONG_EMPLOYER),
        (False, "First line\rSecond line that is too wide for the widget"),
    ],
    ids=["pdf-multiline-flag", "carriage-return"],
)
def test_fit_excludes_multiline_and_carriage_return_without_changing_settings(
    tmp_path, multiline, value
):
    template_path = tmp_path / "source.pdf"
    _create_text_widget(template_path, multiline=multiline)
    with fitz.open(template_path) as document:
        original = next(document[0].widgets())
        original_settings = (
            original.field_flags,
            original.text_fontsize,
            original.text_maxlen,
            original.rect,
        )

    result = AcroFormFiller().fill(
        template_path,
        tmp_path / "filled.pdf",
        {"appearance_settings": value},
        {"appearance_settings": "appearance_settings"},
    )

    assert result.success, result.errors
    with fitz.open(tmp_path / "filled.pdf") as document:
        fitted = next(document[0].widgets())
        assert (
            fitted.field_flags,
            fitted.text_fontsize,
            fitted.text_maxlen,
            fitted.rect,
        ) == original_settings
        assert bool(fitted.field_flags & (1 << 12)) is multiline

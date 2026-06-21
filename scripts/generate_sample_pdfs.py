"""
FinancialGenie – Minta PDF generátor

Két típusú mintanyomtatványt generál OTP Bank jelzáloghitel-kérelem stílusban:

1. acroform_sample.pdf  – Kitölthető PDF AcroForm mezőkkel
2. flat_sample.pdf      – Vizuálisan azonos, de mező nélküli PDF (overlay teszteléshez)

Mindkét PDF 3 oldalas:
  - 1. oldal: Hiteladatok + adós személyes adatai
  - 2. oldal: Adóstárs / kezes személyes adatai
  - 3. oldal: Fedezeti ingatlan adatai

Használat:
    source venv/bin/activate
    python scripts/generate_sample_pdfs.py
"""

import os
import sys
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import (
    HexColor, white, black, Color, lightgrey, darkgrey
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT, TA_CENTER

# ── Colours ───────────────────────────────────────────────────────────
OTP_GREEN = HexColor("#00843D")
OTP_DARK_GREEN = HexColor("#006633")
OTP_LIGHT_GREEN = HexColor("#E8F5E9")
SECTION_BG = HexColor("#F5F5F5")
FIELD_BORDER = HexColor("#CCCCCC")
HEADER_TEXT = white
LABEL_COLOR = HexColor("#333333")
FIELD_BG = HexColor("#FFFFFF")

# ── Page dimensions ──────────────────────────────────────────────────
PAGE_W, PAGE_H = A4  # 595.27, 841.89
MARGIN_LEFT = 20 * mm
MARGIN_RIGHT = 20 * mm
MARGIN_TOP = 25 * mm
MARGIN_BOTTOM = 20 * mm
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT


# ─────────────────────────────────────────────────────────────────────
#  Drawing helpers
# ─────────────────────────────────────────────────────────────────────

def draw_otp_header(c: canvas.Canvas, page_num: int, total_pages: int):
    """Rajzolja az OTP Bank stílusú fejlécet az oldal tetejére."""
    # Green banner
    banner_h = 22 * mm
    y_top = PAGE_H - 10 * mm
    c.setFillColor(OTP_GREEN)
    c.rect(0, y_top - banner_h, PAGE_W, banner_h, fill=1, stroke=0)

    # Bank name
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(MARGIN_LEFT, y_top - 9 * mm, "OTP Bank Nyrt.")

    # Sub-title
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN_LEFT, y_top - 16 * mm, "Jelzáloghitel-kérelem / Lakáshitel igénylőlap")

    # Page number right-aligned
    c.setFont("Helvetica", 9)
    page_text = f"{page_num}. oldal / {total_pages}"
    c.drawRightString(PAGE_W - MARGIN_RIGHT, y_top - 9 * mm, page_text)

    # Thin dark line under banner
    c.setStrokeColor(OTP_DARK_GREEN)
    c.setLineWidth(1.5)
    c.line(0, y_top - banner_h, PAGE_W, y_top - banner_h)

    return y_top - banner_h - 8 * mm  # usable Y below header


def draw_section_header(c: canvas.Canvas, y: float, title: str) -> float:
    """Rajzol egy szekció fejlécet (sötétzöld sáv fehér szöveggel)."""
    h = 8 * mm
    c.setFillColor(OTP_DARK_GREEN)
    c.rect(MARGIN_LEFT, y - h, CONTENT_W, h, fill=1, stroke=0)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(MARGIN_LEFT + 3 * mm, y - 6 * mm, title)
    return y - h - 4 * mm


def draw_field_label(c: canvas.Canvas, x: float, y: float, label: str):
    """Mezőcímkét rajzol (szürke, kis betű)."""
    c.setFillColor(LABEL_COLOR)
    c.setFont("Helvetica", 7.5)
    c.drawString(x, y + 1 * mm, label)


def draw_field_box(c: canvas.Canvas, x: float, y: float, w: float, h: float = 7 * mm):
    """Üres meződobozt rajzol (fehér háttér, szürke keret)."""
    c.setStrokeColor(FIELD_BORDER)
    c.setFillColor(FIELD_BG)
    c.setLineWidth(0.5)
    c.rect(x, y, w, h, fill=1, stroke=1)


def draw_checkbox_box(c: canvas.Canvas, x: float, y: float, size: float = 4 * mm):
    """Jelölőnégyzetet rajzol."""
    c.setStrokeColor(FIELD_BORDER)
    c.setFillColor(FIELD_BG)
    c.setLineWidth(0.5)
    c.rect(x, y, size, size, fill=1, stroke=1)


def draw_field_row(
    c: canvas.Canvas,
    y: float,
    fields: list[tuple[str, str, float]],
    field_height: float = 7 * mm,
    is_acroform: bool = False,
) -> float:
    """
    Egy sort rajzol mezőkkel.

    fields: [(label, field_name, width_ratio), ...]
        width_ratio = mekkora hányada az elérhető szélességnek (0-1)
    Returns: az új y pozíció.
    """
    label_h = 4 * mm
    row_h = label_h + field_height + 2 * mm
    x = MARGIN_LEFT
    gap = 3 * mm

    total_ratio = sum(f[2] for f in fields)
    total_gap = gap * (len(fields) - 1)
    available_w = CONTENT_W - total_gap

    for label, field_name, ratio in fields:
        w = available_w * (ratio / total_ratio)

        # Label
        draw_field_label(c, x, y - label_h, label)

        # Box
        box_y = y - label_h - field_height
        draw_field_box(c, x, box_y, w, field_height)

        # AcroForm field
        if is_acroform and field_name:
            c.acroForm.textfield(
                name=field_name,
                tooltip=label,
                x=x + 1,
                y=box_y + 1,
                width=w - 2,
                height=field_height - 2,
                borderWidth=0,
                fillColor=FIELD_BG,
                textColor=black,
                fontSize=9,
                fieldFlags="",
            )

        x += w + gap

    return y - row_h


def draw_checkbox_row(
    c: canvas.Canvas,
    y: float,
    options: list[tuple[str, str]],
    group_label: str = "",
    is_acroform: bool = False,
) -> float:
    """
    Jelölőnégyzeteket rajzol egy sorban.

    options: [(label_text, field_name), ...]
    """
    if group_label:
        c.setFillColor(LABEL_COLOR)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(MARGIN_LEFT, y, group_label)
        y -= 5 * mm

    x = MARGIN_LEFT
    cb_size = 4 * mm
    for label_text, field_name in options:
        draw_checkbox_box(c, x, y - cb_size, cb_size)
        if is_acroform and field_name:
            c.acroForm.checkbox(
                name=field_name,
                tooltip=label_text,
                x=x,
                y=y - cb_size,
                size=cb_size,
                borderWidth=0.5,
                fillColor=FIELD_BG,
                buttonStyle="check",
            )
        c.setFillColor(LABEL_COLOR)
        c.setFont("Helvetica", 7.5)
        c.drawString(x + cb_size + 2 * mm, y - cb_size + 1 * mm, label_text)
        x += cb_size + 2 * mm + c.stringWidth(label_text, "Helvetica", 7.5) + 6 * mm

    return y - cb_size - 5 * mm


def draw_footer(c: canvas.Canvas):
    """Lábléc finom szürke szöveggel."""
    c.setFillColor(darkgrey)
    c.setFont("Helvetica", 6.5)
    c.drawString(
        MARGIN_LEFT,
        MARGIN_BOTTOM - 5 * mm,
        "OTP Bank Nyrt. | Székhely: 1051 Budapest, Nádor utca 16. | Cégjegyzékszám: Cg. 01-10-041585 | Nyomtatvány kód: OTP-JHK-2026/01",
    )
    c.drawRightString(
        PAGE_W - MARGIN_RIGHT,
        MARGIN_BOTTOM - 5 * mm,
        "FinancialGenie PoC – Minta nyomtatvány",
    )


# ─────────────────────────────────────────────────────────────────────
#  Page 1: Hiteladatok + Adós személyes adatai
# ─────────────────────────────────────────────────────────────────────

def draw_page1(c: canvas.Canvas, is_acroform: bool):
    """1. oldal: Hiteladatok + adós (fő hiteligénylő) adatai."""
    y = draw_otp_header(c, 1, 3)

    # ── I. HITELADATOK ────────────────────────────────────────────
    y = draw_section_header(c, y, "I. HITELADATOK")

    y = draw_field_row(c, y, [
        ("Hitel típusa / Termék neve", "loan_product_name", 2),
        ("Hitel célja", "loan_purpose", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Igényelt hitelösszeg (Ft)", "loan_amount", 1),
        ("Futamidő (hónap)", "loan_term_months", 0.6),
        ("Kamatperiódus", "loan_interest_period", 0.8),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Önerő összege (Ft)", "loan_down_payment", 1),
        ("Becsült havi törlesztő (Ft)", "loan_monthly_payment", 1),
    ], is_acroform=is_acroform)

    y -= 2 * mm

    # ── II. ADÓS (FŐ HITELIGÉNYLŐ) SZEMÉLYES ADATAI ─────────────
    y = draw_section_header(c, y, "II. ADÓS (FŐ HITELIGÉNYLŐ) SZEMÉLYES ADATAI")

    y = draw_field_row(c, y, [
        ("Név", "borrower_name", 1),
        ("Születési név", "borrower_birth_name", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Anyja neve", "borrower_mother_name", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Születési hely", "borrower_birth_place", 1),
        ("Születési idő (éééé.hh.nn)", "borrower_birth_date", 0.8),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Személyi ig. szám", "borrower_personal_id", 1),
        ("Adóazonosító jel", "borrower_tax_id", 1),
        ("Személyi szám", "borrower_id_card_number", 1),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    # Állandó lakcím sub-header
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Állandó lakcím")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Irányítószám", "borrower_zip", 0.5),
        ("Település", "borrower_city", 1),
        ("Utca, házszám", "borrower_street", 1.5),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Emelet", "borrower_floor", 0.4),
        ("Ajtó", "borrower_door", 0.4),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    # Elérhetőségek sub-header
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Elérhetőségek")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Telefonszám", "borrower_phone", 1),
        ("E-mail cím", "borrower_email", 1.5),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    # Jövedelmi adatok sub-header
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Jövedelmi adatok")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Munkáltató neve", "borrower_employer", 1.5),
        ("Havi nettó jövedelem (Ft)", "borrower_monthly_income", 1),
    ], is_acroform=is_acroform)

    y -= 3 * mm

    # ── Nyilatkozat ──────────────────────────────────────────────
    c.setFillColor(LABEL_COLOR)
    c.setFont("Helvetica", 7)
    decl_text = (
        "Alulírott kijelentem, hogy a fenti adatok a valóságnak megfelelnek. "
        "Tudomásul veszem, hogy a valótlan adatok közlése az igénylés elutasítását, "
        "illetve a szerződés azonnali felmondását vonhatja maga után."
    )
    # Wrap roughly
    text_obj = c.beginText(MARGIN_LEFT, y)
    text_obj.setFont("Helvetica", 7)
    text_obj.setFillColor(LABEL_COLOR)
    max_w = CONTENT_W
    words = decl_text.split()
    line = ""
    for w in words:
        test_line = f"{line} {w}".strip()
        if c.stringWidth(test_line, "Helvetica", 7) > max_w:
            text_obj.textLine(line)
            line = w
        else:
            line = test_line
    if line:
        text_obj.textLine(line)
    c.drawText(text_obj)

    # Signature line
    y -= 30 * mm
    sig_w = 60 * mm
    c.setStrokeColor(FIELD_BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN_LEFT, y, MARGIN_LEFT + sig_w, y)
    c.setFont("Helvetica", 7)
    c.setFillColor(LABEL_COLOR)
    c.drawString(MARGIN_LEFT, y - 4 * mm, "Adós aláírása")

    c.line(PAGE_W - MARGIN_RIGHT - sig_w, y, PAGE_W - MARGIN_RIGHT, y)
    c.drawString(PAGE_W - MARGIN_RIGHT - sig_w, y - 4 * mm, "Dátum")

    draw_footer(c)


# ─────────────────────────────────────────────────────────────────────
#  Page 2: Adóstárs / Kezes adatai
# ─────────────────────────────────────────────────────────────────────

def draw_page2(c: canvas.Canvas, is_acroform: bool):
    """2. oldal: Adóstárs / kezes személyes adatai."""
    y = draw_otp_header(c, 2, 3)

    # ── Szerepkör kiválasztása (checkbox) ────────────────────────
    y = draw_section_header(c, y, "III. ADÓSTÁRS / KEZES ADATAI")

    y = draw_checkbox_row(c, y, [
        ("Adóstárs", "coborrower_role_coborrower"),
        ("Kezes", "coborrower_role_guarantor"),
        ("Haszonélvező", "coborrower_role_beneficiary"),
    ], group_label="Szerepkör:", is_acroform=is_acroform)

    y -= 2 * mm

    # ── Személyes adatok ─────────────────────────────────────────
    y = draw_field_row(c, y, [
        ("Név", "coborrower_name", 1),
        ("Születési név", "coborrower_birth_name", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Anyja neve", "coborrower_mother_name", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Születési hely", "coborrower_birth_place", 1),
        ("Születési idő (éééé.hh.nn)", "coborrower_birth_date", 0.8),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Személyi ig. szám", "coborrower_personal_id", 1),
        ("Adóazonosító jel", "coborrower_tax_id", 1),
        ("Személyi szám", "coborrower_id_card_number", 1),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Állandó lakcím")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Irányítószám", "coborrower_zip", 0.5),
        ("Település", "coborrower_city", 1),
        ("Utca, házszám", "coborrower_street", 1.5),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Emelet", "coborrower_floor", 0.4),
        ("Ajtó", "coborrower_door", 0.4),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Elérhetőségek")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Telefonszám", "coborrower_phone", 1),
        ("E-mail cím", "coborrower_email", 1.5),
    ], is_acroform=is_acroform)

    y -= 1 * mm
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Jövedelmi adatok")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Munkáltató neve", "coborrower_employer", 1.5),
        ("Havi nettó jövedelem (Ft)", "coborrower_monthly_income", 1),
    ], is_acroform=is_acroform)

    y -= 3 * mm

    # ── Hozzájáruló nyilatkozat ──────────────────────────────────
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Nyilatkozatok")
    y -= 5 * mm

    y = draw_checkbox_row(c, y, [
        ("Hozzájárulok személyes adataim kezeléséhez", "coborrower_consent_data"),
    ], is_acroform=is_acroform)

    y = draw_checkbox_row(c, y, [
        ("Hozzájárulok a KHR lekérdezéshez", "coborrower_consent_khr"),
    ], is_acroform=is_acroform)

    # Signature
    y -= 15 * mm
    sig_w = 60 * mm
    c.setStrokeColor(FIELD_BORDER)
    c.setLineWidth(0.5)
    c.line(MARGIN_LEFT, y, MARGIN_LEFT + sig_w, y)
    c.setFont("Helvetica", 7)
    c.setFillColor(LABEL_COLOR)
    c.drawString(MARGIN_LEFT, y - 4 * mm, "Adóstárs / Kezes aláírása")

    c.line(PAGE_W - MARGIN_RIGHT - sig_w, y, PAGE_W - MARGIN_RIGHT, y)
    c.drawString(PAGE_W - MARGIN_RIGHT - sig_w, y - 4 * mm, "Dátum")

    draw_footer(c)


# ─────────────────────────────────────────────────────────────────────
#  Page 3: Fedezeti ingatlan adatai
# ─────────────────────────────────────────────────────────────────────

def draw_page3(c: canvas.Canvas, is_acroform: bool):
    """3. oldal: Fedezeti ingatlan adatai."""
    y = draw_otp_header(c, 3, 3)

    # ── IV. FEDEZETI INGATLAN ────────────────────────────────────
    y = draw_section_header(c, y, "IV. FEDEZETI INGATLAN ADATAI")

    # Típus checkboxes
    y = draw_checkbox_row(c, y, [
        ("Lakás", "property_type_apartment"),
        ("Családi ház", "property_type_house"),
        ("Telek", "property_type_land"),
        ("Üzlethelyiség", "property_type_commercial"),
    ], group_label="Ingatlan típusa:", is_acroform=is_acroform)

    y -= 2 * mm

    # Cím
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Ingatlan címe")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Irányítószám", "property_zip", 0.5),
        ("Település", "property_city", 1),
        ("Utca, házszám", "property_street", 1.5),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Emelet", "property_floor", 0.4),
        ("Ajtó", "property_door", 0.4),
    ], is_acroform=is_acroform)

    y -= 2 * mm

    # Ingatlan részletei
    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Ingatlan részletes adatai")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Helyrajzi szám", "property_parcel_number", 1),
        ("Terület (m²)", "property_area_sqm", 0.7),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Szobák száma", "property_rooms", 0.5),
        ("Építés éve", "property_year_built", 0.5),
        ("Becsült forgalmi érték (Ft)", "property_estimated_value", 1.2),
    ], is_acroform=is_acroform)

    y -= 3 * mm

    # ── V. MÁSODIK FEDEZETI INGATLAN (opcionális) ────────────────
    y = draw_section_header(c, y, "V. MÁSODIK FEDEZETI INGATLAN (ha van)")

    y = draw_checkbox_row(c, y, [
        ("Lakás", "property2_type_apartment"),
        ("Családi ház", "property2_type_house"),
        ("Telek", "property2_type_land"),
        ("Üzlethelyiség", "property2_type_commercial"),
    ], group_label="Ingatlan típusa:", is_acroform=is_acroform)

    y -= 2 * mm

    c.setFillColor(OTP_GREEN)
    c.setFont("Helvetica-Bold", 8)
    c.drawString(MARGIN_LEFT, y, "Ingatlan címe")
    y -= 4 * mm

    y = draw_field_row(c, y, [
        ("Irányítószám", "property2_zip", 0.5),
        ("Település", "property2_city", 1),
        ("Utca, házszám", "property2_street", 1.5),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Helyrajzi szám", "property2_parcel_number", 1),
        ("Terület (m²)", "property2_area_sqm", 0.7),
        ("Becsült forgalmi érték (Ft)", "property2_estimated_value", 1),
    ], is_acroform=is_acroform)

    y -= 5 * mm

    # ── VI. ÉRTÉKBECSLŐ ADATAI ───────────────────────────────────
    y = draw_section_header(c, y, "VI. ÉRTÉKBECSLŐ ADATAI (bank tölti ki)")

    y = draw_field_row(c, y, [
        ("Értékbecslő neve", "appraiser_name", 1),
        ("Értékbecslő igazolvány sz.", "appraiser_id", 1),
    ], is_acroform=is_acroform)

    y = draw_field_row(c, y, [
        ("Értékbecslés dátuma", "appraisal_date", 0.8),
        ("Megállapított forgalmi érték (Ft)", "appraisal_value", 1.2),
    ], is_acroform=is_acroform)

    # ── Aláírások ────────────────────────────────────────────────
    y -= 15 * mm
    sig_w = 55 * mm
    c.setStrokeColor(FIELD_BORDER)
    c.setLineWidth(0.5)

    # Three signature lines
    positions = [
        (MARGIN_LEFT, "Adós aláírása"),
        (MARGIN_LEFT + sig_w + 15 * mm, "Adóstárs aláírása"),
        (PAGE_W - MARGIN_RIGHT - sig_w, "Ügyintéző aláírása / Bélyegző"),
    ]
    for x_pos, label in positions:
        c.line(x_pos, y, x_pos + sig_w, y)
        c.setFont("Helvetica", 7)
        c.setFillColor(LABEL_COLOR)
        c.drawString(x_pos, y - 4 * mm, label)

    draw_footer(c)


# ─────────────────────────────────────────────────────────────────────
#  Main generators
# ─────────────────────────────────────────────────────────────────────

def generate_acroform_pdf(output_path: Path):
    """AcroForm (kitölthető) PDF generálása."""
    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle("OTP Bank – Jelzáloghitel-kérelem (AcroForm)")
    c.setAuthor("FinancialGenie PoC")
    c.setSubject("Minta kitölthető nyomtatvány")

    draw_page1(c, is_acroform=True)
    c.showPage()
    draw_page2(c, is_acroform=True)
    c.showPage()
    draw_page3(c, is_acroform=True)
    c.showPage()

    c.save()
    print(f"  ✓ AcroForm PDF: {output_path}")
    print(f"    Méret: {output_path.stat().st_size / 1024:.1f} KB")


def generate_flat_pdf(output_path: Path):
    """Flat (nem kitölthető) PDF generálása – vizuálisan azonos, mező nélkül."""
    c = canvas.Canvas(str(output_path), pagesize=A4)
    c.setTitle("OTP Bank – Jelzáloghitel-kérelem (Flat)")
    c.setAuthor("FinancialGenie PoC")
    c.setSubject("Minta nyomtatvány – overlay teszteléshez")

    draw_page1(c, is_acroform=False)
    c.showPage()
    draw_page2(c, is_acroform=False)
    c.showPage()
    draw_page3(c, is_acroform=False)
    c.showPage()

    c.save()
    print(f"  ✓ Flat PDF:     {output_path}")
    print(f"    Méret: {output_path.stat().st_size / 1024:.1f} KB")


def list_acroform_fields(pdf_path: Path):
    """Kilistázza az AcroForm mezőket a generált PDF-ből (ellenőrzés)."""
    try:
        import pikepdf
        pdf = pikepdf.open(str(pdf_path))
        if "/AcroForm" not in pdf.Root:
            print("  ⚠ Nincs AcroForm a PDF-ben!")
            return

        fields = pdf.Root.AcroForm.Fields
        print(f"\n  AcroForm mezők ({len(fields)} db):")
        for i, field in enumerate(fields):
            field_obj = field.resolve() if hasattr(field, 'resolve') else field
            name = str(field_obj.get("/T", "???"))
            field_type = str(field_obj.get("/FT", "???"))
            print(f"    {i+1:2d}. {name:40s}  [{field_type}]")
        pdf.close()
    except ImportError:
        print("  ℹ pikepdf nincs telepítve – mező-ellenőrzés kihagyva")
    except Exception as e:
        print(f"  ⚠ Hiba a mezők olvasásakor: {e}")


# ─────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────

def main():
    samples_dir = PROJECT_ROOT / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    acroform_path = samples_dir / "acroform_sample.pdf"
    flat_path = samples_dir / "flat_sample.pdf"

    print("=" * 60)
    print("FinancialGenie – Minta PDF generálás")
    print("=" * 60)
    print()

    print("📄 AcroForm (kitölthető) PDF generálása...")
    generate_acroform_pdf(acroform_path)

    print()
    print("📄 Flat (nem kitölthető) PDF generálása...")
    generate_flat_pdf(flat_path)

    # Mezőellenőrzés
    print()
    print("🔍 AcroForm mezők ellenőrzése...")
    list_acroform_fields(acroform_path)

    print()
    print("=" * 60)
    print("✅ Kész! Fájlok:")
    print(f"   {acroform_path}")
    print(f"   {flat_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

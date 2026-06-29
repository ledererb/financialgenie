"""
FinancialGenie – PDF kitöltő motor (Determinisztikus)

Két üzemmód:
1. AcroFormFiller: AcroForm mezőket tölt ki pikepdf segítségével (név → érték).
   Használat: olyan PDF-ek, amelyekben vannak interaktív űrlapmezők.

2. OverlayFiller: reportlab + PyMuPDF segítségével szöveget helyez el
   megadott koordinátákon „lapos" (nem interaktív) PDF-ekre.
   Használat: beszkennelt vagy flatten-elt nyomtatványok.

Mindkét filler közös interfészt valósít meg (BaseFiller.fill), és
FillingResult-ot ad vissza az eredményekről.

Függőségek:
  - pikepdf >=8.0.0
  - reportlab >=4.0
  - PyMuPDF (fitz) >=1.23.0
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result adatstruktúra
# ---------------------------------------------------------------------------

@dataclass
class FillingResult:
    """
    A kitöltés eredménye.

    Attributes:
        success: Sikeresen befejeződött-e a kitöltés (részleges siker is True).
        filled_fields: Ténylegesen kitöltött mezők listája.
        skipped_fields: Kihagyott mezők (pl. nincs adat hozzá a mapping-ben).
        errors: Hibák listája (mező → hibaüzenet).
        output_path: A generált PDF elérési útja.
    """
    success: bool = False
    filled_fields: list[str] = field(default_factory=list)
    skipped_fields: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    output_path: str | None = None

    @property
    def summary(self) -> str:
        """Emberi olvasásra szánt összegzés."""
        return (
            f"Kitöltés {'sikeres' if self.success else 'sikertelen'} | "
            f"kitöltött: {len(self.filled_fields)}, "
            f"kihagyott: {len(self.skipped_fields)}, "
            f"hibák: {len(self.errors)}"
        )


# ---------------------------------------------------------------------------
# Alap interfész
# ---------------------------------------------------------------------------

class BaseFiller(ABC):
    """
    Közös PDF-kitöltő interfész.

    Minden filler implementálja a fill() metódust, amely:
    - template_path: a bemeneti PDF sablon elérési útja
    - output_path: a kimeneti (kitöltött) PDF elérési útja
    - field_data: kanonikus mező → érték szótár (már feloldott adatok)
    - mapping: PDF-mezőnév → kanonikus mezőnév szótár
      (pl. {"txt_nev_1": "participant.0.name"})

    A fill() visszaad egy FillingResult-ot.
    """

    @abstractmethod
    def fill(
        self,
        template_path: str | Path,
        output_path: str | Path,
        field_data: dict[str, str],
        mapping: dict[str, str],
    ) -> FillingResult:
        """
        Kitölti a PDF sablont a megadott adatokkal.

        Args:
            template_path: Bemeneti PDF sablon útvonala.
            output_path: Kimeneti (kitöltött) PDF útvonala.
            field_data: Kanonikus mezőnév → érték szótár.
                        Pl. {"participant.0.name": "Kovács János"}
            mapping: PDF mezőnév → kanonikus mezőnév szótár.
                     Pl. {"txt_adós_neve": "participant.0.name"}

        Returns:
            FillingResult az eredményekkel.
        """
        ...

    def _resolve_field_value(
        self,
        pdf_field_name: str,
        mapping: dict[str, str],
        field_data: dict[str, str],
    ) -> tuple[str | None, str | None]:
        """
        Feloldja egy PDF-mező értékét a mapping és az adatok alapján.

        Returns:
            (canonical_name, value) tuple, vagy (None, None) ha nincs mapping
            vagy nincs adat.
        """
        canonical_name = mapping.get(pdf_field_name)
        value = None
        if canonical_name is not None:
            value = field_data.get(canonical_name)
        
        # Fallback: ha kanonikus névvel nem találtuk meg, megpróbáljuk közvetlenül
        # a PDF mezőnévvel kikeresni (mert a pipeline _prepare_field_data
        # PDF-mezőnév kulcsokkal adja vissza az adatokat).
        if value is None:
            value = field_data.get(pdf_field_name)

        return canonical_name, value


# ---------------------------------------------------------------------------
# AcroForm kitöltő (pikepdf)
# ---------------------------------------------------------------------------

class AcroFormFiller(BaseFiller):
    """
    AcroForm-alapú PDF kitöltő pikepdf segítségével.

    Működés:
    1. Megnyitja a PDF-et pikepdf-fel.
    2. Bejárja az AcroForm mezőket.
    3. A mapping alapján feloldja a kanonikus mezőnevet.
    4. Beírja az értéket a mezőbe.
    5. Opcionálisan flatten-eli a PDF-et (read-only).

    Használat:
        filler = AcroFormFiller(flatten=True)
        result = filler.fill("sablon.pdf", "kitöltött.pdf", adatok, mapping)
    """

    def __init__(self, flatten: bool = False, font_name: str | None = None):
        """
        Args:
            flatten: Ha True, a kitöltés után flatten-eli a mezőket
                     (nem szerkeszthető PDF lesz).
            font_name: Opcionális betűtípus neve az AcroForm megjelenítéshez.
        """
        self.flatten = flatten
        self.font_name = font_name

    def fill(
        self,
        template_path: str | Path,
        output_path: str | Path,
        field_data: dict[str, str],
        mapping: dict[str, str],
    ) -> FillingResult:
        """AcroForm mezők kitöltése pikepdf segítségével."""
        import pikepdf

        template_path = Path(template_path)
        output_path = Path(output_path)
        result = FillingResult(output_path=str(output_path))

        # --- Validáció ---
        if not template_path.exists():
            result.errors.append({
                "field": "_template",
                "error": f"Sablon nem található: {template_path}",
            })
            return result

        try:
            pdf = pikepdf.open(template_path)
        except Exception as exc:
            result.errors.append({
                "field": "_template",
                "error": f"Nem sikerült megnyitni a PDF-et: {exc}",
            })
            return result

        # --- AcroForm ellenőrzés ---
        if "/AcroForm" not in pdf.Root:
            result.errors.append({
                "field": "_acroform",
                "error": "A PDF nem tartalmaz AcroForm mezőket.",
            })
            pdf.close()
            return result

        acroform = pdf.Root["/AcroForm"]
        if "/Fields" not in acroform:
            result.errors.append({
                "field": "_acroform",
                "error": "Az AcroForm nem tartalmaz mezőket (/Fields hiányzik).",
            })
            pdf.close()
            return result

        # --- Mezők kitöltése ---
        fields = acroform["/Fields"]
        self._fill_fields_recursive(fields, mapping, field_data, result)

        # --- Flatten / ReadOnly (opcionális) ---
        if self.flatten:
            self._make_readonly(pdf)

        # --- Mentés ---
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            pdf.save(str(output_path))
            result.success = True
            logger.info(
                "AcroForm kitöltés kész: %s → %s (%s)",
                template_path.name,
                output_path.name,
                result.summary,
            )
        except Exception as exc:
            result.errors.append({
                "field": "_save",
                "error": f"Nem sikerült menteni: {exc}",
            })
        finally:
            pdf.close()

        return result

    def _fill_fields_recursive(
        self,
        fields: Any,
        mapping: dict[str, str],
        field_data: dict[str, str],
        result: FillingResult,
        parent_name: str = "",
    ) -> None:
        """
        Rekurzívan bejárja az AcroForm mező-fát és kitölti az értékeket.

        Az AcroForm mezők hierarchikusak lehetnek (/Kids al-mezők).
        Típus-specifikus értékbeírás:
        - /Tx (szövegmező): pikepdf.String
        - /Btn (checkbox): pikepdf.Name("/Yes") ha a kitöltendő érték
          truthy (a NoToggleToOff / 1-es /Ff bit mellett ez a konvenció)
        - /Ch (dropdown/list): pikepdf.String
        """
        import pikepdf

        for field_ref in fields:
            try:
                field_obj = field_ref

                # Különválasztjuk a widget annotációkat és a beágyazott al-mezőket a /Kids alatt.
                # Ha egy gyereknek van /T tulajdonsága, akkor az beágyazott al-mező.
                widgets = []
                nested = []
                if "/Kids" in field_obj:
                    for kid in field_obj["/Kids"]:
                        try:
                            sub = str(kid.get("/Subtype", ""))
                        except Exception:
                            sub = ""
                        if sub == "/Widget" or "/T" not in kid:
                            widgets.append(kid)
                        else:
                            nested.append(kid)

                # Mező neve
                name = str(field_obj.get("/T", ""))
                pdf_field_name = f"{parent_name}.{name}" if parent_name and name else (name or parent_name)

                if not pdf_field_name:
                    continue

                # Ha vannak beágyazott mezők, rekurzívan bejárjuk őket
                if nested:
                    self._fill_fields_recursive(
                        nested, mapping, field_data, result, pdf_field_name
                    )

                # Ha nincsenek al-mezők, akkor ez egy kitölthető mező (akár van /Kids widgetje, akár nincs)
                if not nested:
                    # Érték feloldása
                    canonical_name, value = self._resolve_field_value(
                        pdf_field_name, mapping, field_data
                    )

                    if canonical_name is None:
                        result.skipped_fields.append(pdf_field_name)
                        logger.debug("Nincs mapping ehhez a mezőhöz: %s", pdf_field_name)
                        continue

                    if value is None or value == "":
                        result.skipped_fields.append(pdf_field_name)
                        logger.debug(
                            "Nincs adat ehhez a mezőhöz: %s → %s",
                            pdf_field_name,
                            canonical_name,
                        )
                        continue

                    # Típus-specifikus értékbeírás
                    field_type = str(field_obj.get("/FT", ""))
                    # Ha nincs /FT a szülőn, megpróbáljuk kinyerni a widgetekből (öröklődés fallback)
                    if not field_type and widgets:
                        for w in widgets:
                            if "/FT" in w:
                                field_type = str(w.get("/FT", ""))
                                break

                    if field_type == "/Btn":
                        # Checkbox: az érték truthy-e? A PDF /Yes névvel jelzi
                        # a bepipált állapotot (NoToggleToOff = /Ff 1-es bit).
                        is_checked = self._is_truthy(value)
                        if is_checked:
                            field_obj[pikepdf.Name("/V")] = pikepdf.Name("/Yes")
                        else:
                            # Kikapcsolt checkbox: /Off név
                            field_obj[pikepdf.Name("/V")] = pikepdf.Name("/Off")
                    else:
                        # Szöveg / dropdown / lista: String érték
                        field_obj[pikepdf.Name("/V")] = pikepdf.String(str(value))

                    # Megjelenítés frissítése – töröljük az /AP-t a szülőből és az összes widgetből,
                    # hogy a PDF-olvasó újra renderelje a mezőket
                    if "/AP" in field_obj:
                        del field_obj["/AP"]
                    for w in widgets:
                        if "/AP" in w:
                            del w["/AP"]

                    result.filled_fields.append(pdf_field_name)
                    logger.debug(
                        "Mező kitöltve: %s = %s (← %s)",
                        pdf_field_name,
                        value[:50] if len(str(value)) > 50 else value,
                        canonical_name,
                    )

            except Exception as exc:
                pdf_name = str(field_ref.get("/T", "ismeretlen"))
                result.errors.append({
                    "field": pdf_name,
                    "error": f"Hiba a mező kitöltésekor: {exc}",
                })
                logger.warning("Mező kitöltési hiba: %s – %s", pdf_name, exc)

    @staticmethod
    def _is_truthy(value: Any) -> bool:
        """Egy mezőérték truthy értelmezése checkbox kitöltéshez."""
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("", "0", "false", "no", "nem", "off", "false", "x-", "-"):
            return False
        if s in ("yes", "igen", "true", "1", "x", "y", "i", "on"):
            return True
        # Bármi más nem-empty → truthy
        return s != ""

    @staticmethod
    def _make_readonly(pdf: Any) -> None:
        """
        Csak-olvashatóvá teszi az AcroForm mezőket.

        Megjegyzés: a pikepdf nem támogatja a valódi flatten-t (a megjelenő
        réteg widget-ekké alakítását). Ez a metódus a /NeedAppearances flag
        bekapcsolásával és minden mező ReadOnly bitjének (/Ff bit 1) beállításával
        éri el, hogy a nézők ne tudják módosítani a kitöltött mezőket.

        Ha valódi flatten-re van szükség (mezők tartalom-réteggé alakítása),
        külső könyvtár (pl. Ghostscript vagy pdftk) szükséges.
        """
        import pikepdf

        if "/AcroForm" not in pdf.Root:
            return

        acroform = pdf.Root["/AcroForm"]
        # NeedAppearances = True, hogy a viewer újra renderelj
        acroform[pikepdf.Name("/NeedAppearances")] = True

        if "/Fields" in acroform:
            for field_ref in acroform["/Fields"]:
                try:
                    # ReadOnly bit (bit 1) bekapcsolása
                    current_ff = int(field_ref.get("/Ff", 0))
                    field_ref[pikepdf.Name("/Ff")] = current_ff | 1
                except Exception:
                    pass

    @staticmethod
    def list_fields(pdf_path: str | Path) -> list[dict[str, str]]:
        """
        Listázza egy PDF AcroForm mezőit (debug/mapping segédlet).

        Returns:
            Lista dict-ekből: {"name": "...", "type": "...", "value": "..."}
        """
        import pikepdf

        pdf_path = Path(pdf_path)
        result: list[dict[str, str]] = []

        with pikepdf.open(pdf_path) as pdf:
            if "/AcroForm" not in pdf.Root:
                return result
            acroform = pdf.Root["/AcroForm"]
            if "/Fields" not in acroform:
                return result

            def _collect(fields: Any, parent_name: str = "") -> None:
                for f in fields:
                    # Különválasztjuk a widgeteket és a beágyazott al-mezőket a /Kids alatt
                    widgets = []
                    nested = []
                    if "/Kids" in f:
                        for kid in f["/Kids"]:
                            try:
                                sub = str(kid.get("/Subtype", ""))
                            except Exception:
                                sub = ""
                            if sub == "/Widget" or "/T" not in kid:
                                widgets.append(kid)
                            else:
                                nested.append(kid)

                    name = str(f.get("/T", ""))
                    full_name = f"{parent_name}.{name}" if parent_name and name else (name or parent_name)

                    if nested:
                        _collect(nested, full_name)

                    if not nested and full_name:
                        field_type = str(f.get("/FT", ""))
                        if not field_type and widgets:
                            for w in widgets:
                                if "/FT" in w:
                                    field_type = str(w.get("/FT", ""))
                                    break
                        value = str(f.get("/V", ""))
                        result.append({
                            "name": full_name,
                            "type": field_type,
                            "value": value,
                        })

            _collect(acroform["/Fields"])

        return result


# ---------------------------------------------------------------------------
# Overlay kitöltő (reportlab + PyMuPDF)
# ---------------------------------------------------------------------------

@dataclass
class TextPlacement:
    """
    Egy szöveg elhelyezési specifikációja az overlay módhoz.

    Attributes:
        x: Bal oldali pozíció pontokban (1 pont = 1/72 inch).
        y: Alsó pozíció pontokban (PDF koordináta-rendszer: bal-alsó sarok = 0,0).
        font_name: Betűtípus neve (reportlab-kompatibilis).
        font_size: Betűméret pontokban.
        page_index: A céloldal indexe (0-alapú).
    """
    x: float
    y: float
    font_name: str = "Helvetica"
    font_size: float = 10.0
    page_index: int = 0


class OverlayFiller(BaseFiller):
    """
    Overlay-alapú PDF kitöltő reportlab + PyMuPDF segítségével.

    Működés:
    1. PyMuPDF-fel kiolvassa az eredeti PDF oldalméretet.
    2. reportlab-bal egy átlátszó overlay PDF-et generál a szövegekkel.
    3. pikepdf-fel összeolvasztja (merge) az eredeti és az overlay rétegeket.

    A mapping ebben az üzemmódban nem PDF-mezőnév → kanonikus név,
    hanem kanonikus mezőnév → TextPlacement (koordináta-alapú elhelyezés).

    Használat:
        placements = {
            "participant.0.name": TextPlacement(x=150, y=700, page_index=0),
            "loan.amount": TextPlacement(x=300, y=650, page_index=0, font_size=12),
        }
        filler = OverlayFiller()
        result = filler.fill("sablon.pdf", "kitöltött.pdf", adatok, placements)
    """

    def __init__(self, default_font: str = "Helvetica", default_font_size: float = 10.0):
        """
        Args:
            default_font: Alapértelmezett betűtípus az overlay szövegekhez.
            default_font_size: Alapértelmezett betűméret pontokban.
        """
        self.default_font = default_font
        self.default_font_size = default_font_size

    def fill(
        self,
        template_path: str | Path,
        output_path: str | Path,
        field_data: dict[str, str],
        mapping: dict[str, str] | dict[str, TextPlacement],
    ) -> FillingResult:
        """
        Overlay szövegek elhelyezése a PDF-en.

        A mapping kétféle formában érkezhet:
        1. dict[str, TextPlacement] – közvetlen elhelyezés
        2. dict[str, str] – egyszerű mapping (ilyenkor koordináta-konfig kell)

        Az overlay mód a TextPlacement formátumot preferálja.
        """
        template_path = Path(template_path)
        output_path = Path(output_path)
        result = FillingResult(output_path=str(output_path))

        if not template_path.exists():
            result.errors.append({
                "field": "_template",
                "error": f"Sablon nem található: {template_path}",
            })
            return result

        # --- Placement-ek feloldása ---
        placements: dict[str, TextPlacement] = {}
        for key, val in mapping.items():
            if isinstance(val, TextPlacement):
                placements[key] = val
            elif isinstance(val, str):
                # Egyszerű mapping mód: a key a PDF-mező, val a kanonikus név
                # Ilyenkor nincs koordináta → skipped
                result.skipped_fields.append(key)
                logger.warning(
                    "Overlay módban TextPlacement szükséges, de egyszerű "
                    "mapping érkezett: %s → %s. Használj TextPlacement-et.",
                    key,
                    val,
                )

        if not placements:
            result.errors.append({
                "field": "_mapping",
                "error": "Nincsenek érvényes TextPlacement-ek a mapping-ben.",
            })
            return result

        try:
            page_dimensions = self._get_page_dimensions(template_path)
            overlay_pdf_bytes = self._create_overlay(
                placements, field_data, page_dimensions, result
            )
            self._merge_overlay(template_path, output_path, overlay_pdf_bytes)
            result.success = True
            logger.info(
                "Overlay kitöltés kész: %s → %s (%s)",
                template_path.name,
                output_path.name,
                result.summary,
            )
        except Exception as exc:
            result.errors.append({
                "field": "_overlay",
                "error": f"Overlay hiba: {exc}",
            })
            logger.exception("Overlay kitöltési hiba: %s", exc)

        return result

    @staticmethod
    def _get_page_dimensions(pdf_path: Path) -> list[tuple[float, float]]:
        """
        Kiolvassa az oldalméretet PyMuPDF (fitz) segítségével.

        Returns:
            Lista (width, height) tuple-ökkel oldalanként, pontokban.
        """
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        dimensions = []
        for page in doc:
            rect = page.rect
            dimensions.append((rect.width, rect.height))
        doc.close()
        return dimensions

    def _create_overlay(
        self,
        placements: dict[str, TextPlacement],
        field_data: dict[str, str],
        page_dimensions: list[tuple[float, float]],
        result: FillingResult,
    ) -> bytes:
        """
        Létrehozza az overlay PDF-et reportlab segítségével.

        Az overlay egy átlátszó PDF, amelyen csak a kitöltendő szövegek
        vannak elhelyezve a megadott koordinátákon.

        Returns:
            Az overlay PDF tartalma bytes-ként.
        """
        from io import BytesIO

        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buffer = BytesIO()

        # Oldalszám meghatározása: a legnagyobb page_index + 1
        max_page_index = max(
            (p.page_index for p in placements.values()),
            default=0,
        )
        num_pages = max(max_page_index + 1, len(page_dimensions))

        # Canvas létrehozása – az első oldal méretével
        first_page_size = page_dimensions[0] if page_dimensions else A4
        c = canvas.Canvas(buffer, pagesize=first_page_size)

        for page_idx in range(num_pages):
            # Oldal méret beállítása
            if page_idx < len(page_dimensions):
                w, h = page_dimensions[page_idx]
            else:
                w, h = A4
            c.setPageSize((w, h))

            # Ezen az oldalon elhelyezendő szövegek
            page_placements = {
                canonical_name: placement
                for canonical_name, placement in placements.items()
                if placement.page_index == page_idx
            }

            for canonical_name, placement in page_placements.items():
                value = field_data.get(canonical_name)
                if value is None or str(value).strip() == "":
                    result.skipped_fields.append(canonical_name)
                    continue

                value_str = str(value)
                font = placement.font_name or self.default_font
                size = placement.font_size or self.default_font_size

                try:
                    c.setFont(font, size)
                    c.drawString(placement.x, placement.y, value_str)
                    result.filled_fields.append(canonical_name)
                    logger.debug(
                        "Overlay szöveg: %s = '%s' @ (%s, %s) oldal %d",
                        canonical_name,
                        value_str[:40],
                        placement.x,
                        placement.y,
                        page_idx,
                    )
                except Exception as exc:
                    result.errors.append({
                        "field": canonical_name,
                        "error": f"Overlay rajzolási hiba: {exc}",
                    })

            c.showPage()

        c.save()
        return buffer.getvalue()

    @staticmethod
    def _merge_overlay(
        template_path: Path,
        output_path: Path,
        overlay_bytes: bytes,
    ) -> None:
        """
        Összefésüli az eredeti PDF-et az overlay réteggel pikepdf segítségével.

        Az overlay PDF oldalai az eredeti PDF oldalaira kerülnek rá
        mint extra tartalmi réteg.
        """
        import pikepdf

        output_path.parent.mkdir(parents=True, exist_ok=True)

        original = pikepdf.open(template_path)
        from io import BytesIO
        overlay = pikepdf.open(BytesIO(overlay_bytes))

        for page_idx, original_page in enumerate(original.pages):
            if page_idx < len(overlay.pages):
                overlay_page = overlay.pages[page_idx]
                # Az overlay oldal tartalmát ráhelyezzük az eredetire
                original_page.add_overlay(overlay_page)

        original.save(str(output_path))
        original.close()
        overlay.close()

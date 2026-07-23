"""
Kitöltött PDF dokumentumok validálása.

Két réteg:
1. Mechanikai ellenőrzés — a kitöltött mezők értékei egyeznek-e az elvárt értékekkel
2. AI szöveges ellenőrzés — a dokumentum logikai koherenciája (Claude API)
"""
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class FieldValidation:
    """Egy mező ellenőrzésének eredménye."""
    pdf_field_name: str
    expected: str
    actual_pikepdf: str
    actual_mupdf: str
    status: str  # "ok" | "mismatch" | "missing" | "empty"
    detail: str = ""


@dataclass
class ValidationReport:
    """Teljes validálási riport."""
    total_fields: int = 0
    ok_fields: int = 0
    mismatch_fields: int = 0
    missing_fields: int = 0
    empty_fields: int = 0
    field_results: list[FieldValidation] = field(default_factory=list)
    ai_issues: list[dict] = field(default_factory=list)
    ai_summary: str = ""

    @property
    def score(self) -> float:
        if self.total_fields == 0:
            return 0.0
        return self.ok_fields / self.total_fields * 100


def validate_filled_pdf(
    pdf_path: Path,
    field_data: dict[str, str],
    filled_fields: list[str],
    skipped_fields: list[str],
    run_ai_review: bool = True,
    sf_context: dict | None = None,
) -> ValidationReport:
    """
    A kitöltött PDF validálása.

    Args:
        pdf_path: A kitöltött PDF útvonala
        field_data: Elvárt értékek (pdf_field_name → value)
        filled_fields: A kitöltés során sikeresen kitöltött mezők
        skipped_fields: A kitöltés során kihagyott mezők
        run_ai_review: Futtassa-e az AI szöveges ellenőrzést
        sf_context: Salesforce referencia adatok az AI review-hoz
    """
    report = ValidationReport()

    # 1. Mechanikai ellenőrzés
    _mechanical_validation(report, pdf_path, field_data, filled_fields, skipped_fields)

    # 2. AI szöveges ellenőrzés
    if run_ai_review:
        _ai_review(report, pdf_path, field_data, sf_context)

    return report


def _read_pikepdf_values(pdf_path: Path) -> dict[str, str]:
    """Visszaolvassa a /V értékeket pikepdf-ből."""
    import pikepdf

    values: dict[str, str] = {}
    try:
        pdf = pikepdf.open(str(pdf_path))
        acroform = pdf.Root.get("/AcroForm", {})
        for field in acroform.get("/Fields", []):
            name = str(field.get("/T", ""))
            v = field.get("/V")
            if v is not None:
                values[name] = str(v)
        pdf.close()
    except Exception as e:
        logger.warning("pikepdf read failed: %s", e)
    return values


def _read_mupdf_widget_values(pdf_path: Path) -> dict[str, str]:
    """Visszaolvassa a widget field_value-kat PyMuPDF-ből."""
    import fitz

    values: dict[str, str] = {}
    try:
        doc = fitz.open(str(pdf_path))
        for page in doc:
            for w in page.widgets():
                if w.field_name:
                    values[w.field_name] = w.field_value or ""
        doc.close()
    except Exception as e:
        logger.warning("PyMuPDF read failed: %s", e)
    return values


def _mechanical_validation(
    report: ValidationReport,
    pdf_path: Path,
    field_data: dict[str, str],
    filled_fields: list[str],
    skipped_fields: list[str],
) -> None:
    """Mechanikai ellenőrzés: elvárt vs tényleges értékek."""

    pikepdf_values = _read_pikepdf_values(pdf_path)
    mupdf_values = _read_mupdf_widget_values(pdf_path)

    filled_set = set(filled_fields)
    skipped_set = set(skipped_fields)

    for pdf_name, expected in field_data.items():
        expected_str = str(expected).strip()
        if not expected_str:
            report.empty_fields += 1
            continue

        actual_pk = pikepdf_values.get(pdf_name, "")
        actual_mu = mupdf_values.get(pdf_name, "")

        # Checkbox/radio mezőknél az elvárt érték "igen" = be van pipálva
        is_checkbox_expected = expected_str.lower() in ("igen", "yes", "true", "1")

        if pdf_name in skipped_set:
            # Elvárt érték nem üres, de a kitöltés kihagyta → mismatch
            report.field_results.append(FieldValidation(
                pdf_field_name=pdf_name,
                expected=expected_str,
                actual_pikepdf=actual_pk,
                actual_mupdf=actual_mu,
                status="missing",
                detail="Kitöltés kihagyta (skipped)",
            ))
            report.mismatch_fields += 1
            continue

        # For checkbox options (___suffix), also check the base name
        base_name = pdf_name.split("___")[0] if "___" in pdf_name else None
        base_pk = pikepdf_values.get(base_name, "") if base_name else ""
        base_mu = mupdf_values.get(base_name, "") if base_name else ""

        if pdf_name in filled_set or actual_pk or actual_mu or base_pk or base_mu:
            # Van érték a PDF-ben — ellenőrizzük az egyezést
            if is_checkbox_expected:
                # Checkbox: "igen" elvárás → /V nem /Off, /AS nem /Off
                # A suffix mező saját értéke, VAGY a base name értéke
                pk_ok = (
                    (actual_pk and actual_pk not in ("/Off", "Off", "", "/")) or
                    (base_pk and base_pk not in ("/Off", "Off", "", "/"))
                )
                mu_ok = (
                    (actual_mu and actual_mu not in ("Off", "", "/Off")) or
                    (base_mu and base_mu not in ("Off", "", "/Off"))
                )
                if pk_ok or mu_ok:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk or base_pk,
                        actual_mupdf=actual_mu or base_mu,
                        status="ok",
                    ))
                    report.ok_fields += 1
                else:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk or base_pk,
                        actual_mupdf=actual_mu or base_mu,
                        status="mismatch",
                        detail=f"Elvárt: bepipálva, tényleges: nem pipálva",
                    ))
                    report.mismatch_fields += 1
            elif expected_str.lower() in ("nem", "no", "false", "0"):
                # Checkbox "nem" elvárás: jó ha a MEZŐ nincs bepipálva (Off).
                # A suffix mezőnek (___suffix) kell Off-nak lennie, nem a base
                # name-nek — a base name lehet hogy értékkel van (ha egy másik
                # opció van bepipálva).
                pk_ok = not actual_pk or actual_pk in ("/Off", "Off", "", "/")
                mu_ok = not actual_mu or actual_mu in ("Off", "", "/Off")
                if pk_ok and mu_ok:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk or base_pk,
                        actual_mupdf=actual_mu or base_mu,
                        status="ok",
                    ))
                    report.ok_fields += 1
                else:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk or base_pk,
                        actual_mupdf=actual_mu or base_mu,
                        status="mismatch",
                        detail=f"Elvárt: nem pipálva, de be van pipálva",
                    ))
                    report.mismatch_fields += 1
            else:
                # Szövegmező: string egyezés
                if actual_pk == expected_str or actual_mu == expected_str:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk,
                        actual_mupdf=actual_mu,
                        status="ok",
                    ))
                    report.ok_fields += 1
                elif actual_pk or actual_mu:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk,
                        actual_mupdf=actual_mu,
                        status="mismatch",
                        detail=f"Elvárt: '{expected_str}', pikepdf: '{actual_pk}', mupdf: '{actual_mu}'",
                    ))
                    report.mismatch_fields += 1
                else:
                    report.field_results.append(FieldValidation(
                        pdf_field_name=pdf_name,
                        expected=expected_str,
                        actual_pikepdf=actual_pk,
                        actual_mupdf=actual_mu,
                        status="missing",
                        detail="Elvárt érték, de üres a PDF-ben",
                    ))
                    report.mismatch_fields += 1
        else:
            # Se nem kitöltve, se nem skippeve → missing
            report.field_results.append(FieldValidation(
                pdf_field_name=pdf_name,
                expected=expected_str,
                actual_pikepdf=actual_pk,
                actual_mupdf=actual_mu,
                status="missing",
                detail="Nincs a PDF-ben és nem lett kitöltve",
            ))
            report.mismatch_fields += 1

    report.total_fields = report.ok_fields + report.mismatch_fields + report.empty_fields + report.missing_fields


def _ai_review(
    report: ValidationReport,
    pdf_path: Path,
    field_data: dict[str, str],
    sf_context: dict | None,
) -> None:
    """AI szöveges ellenőrzés: a dokumentum logikai koherenciája."""
    try:
        import anthropic
        import fitz
        from config import settings

        # Szöveg kinyerése a PDF-ből
        doc = fitz.open(str(pdf_path))
        pages_text = []
        for i, page in enumerate(doc):
            text = page.get_text()
            pages_text.append(f"=== {i+1}. oldal ===\n{text[:2000]}")
        doc.close()

        # Field data összegzése
        field_summary = "\n".join(
            f"- {k}: {v}" for k, v in sorted(field_data.items()) if v and str(v).strip()
        )

        sf_text = ""
        if sf_context:
            sf_lines = [f"- {k}: {v}" for k, v in sorted(sf_context.items()) if v]
            sf_text = "\n\nSALESFORCE REFERENCIA ADATOK:\n" + "\n".join(sf_lines[:30])

        prompt = (
            "Elemeld meg az alábbi banki hiteligénylő dokumentum kitöltött verzióját. "
            "Keresd meg a logikai hibákat, ellentmondásokat, értelmetlenségeket. "
            "Ne a mapping technikai hibáira koncentrálj, hanem arra ami a dokumentum olvasója "
            "számára nyilvánvalóan rossz, össze-vissza, vagy logikailag ellentmondó. "
            "A kitöltött értékek és a Salesforce referencia adatok is meg vannak adva. "
            "Rendezd a hibákat súlyosság szerint: KRITIKUS, FIGYELMEZTETÉS, INFORMÁCIÓ.\n\n"
            "Válaszolj JSON formátumban: {\"issues\": [{\"severity\": \"critical|warning|info\", \"field\": \"...\", \"problem\": \"...\", \"suggestion\": \"...\"}], \"summary\": \"...\"}\n\n"
            "KITÖLTÖTT MEZŐK:\n" + field_summary + "\n\n"
            "SZÖVEG A PDF-BŐL:\n" + "\n".join(pages_text) + sf_text
        )

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )

        result_text = response.content[0].text

        # JSON kinyerése
        try:
            # Ha markdown code block-ban van, szedjük ki
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]
            parsed = json.loads(result_text.strip())
            report.ai_issues = parsed.get("issues", [])
            report.ai_summary = parsed.get("summary", "")
        except (json.JSONDecodeError, IndexError) as e:
            logger.warning("AI review JSON parse failed: %s", e)
            report.ai_summary = result_text[:500]

    except Exception as e:
        logger.warning("AI review failed: %s", e)

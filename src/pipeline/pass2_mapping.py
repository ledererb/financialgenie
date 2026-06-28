"""
Pass 2 – Mező-leképezés (Field mapping).

A kanonikus `DealData`-ból és egy `MappingConfig`-ból előállítja a
`tényleges PDF-mező → érték` párokat (`field_data`).

4 sub-pass-t implementál (a DOCUMENT_PROCESSOR_LEARNINGS.md alapján):

  1. Exact / canonical match (0 AI cost). Ez a meglévő
     `_prepare_field_data` logika: canonical_field alapján a
     participant/loan/property adatok kitöltése.
  2. Salesforce label matching (0 AI cost) — opcionális, jelenleg
     stub (a canonical mapping már tartalmazza a label-alapú
     feloldást, ezért nincs külön lépés).
  3. AI classification (DeepSeek) — opcionális, csak ha a
     lefedettség küszöb alatt van. Default off.
  4. Legal checkbox defaults — a leképezetlen checkboxokra a
     LegalClassifier kategória→érték értékeit készíti elő (a PDF-be
     írás a Pass 3-ban történik).

Metrika: hány mapping-mező kapott értéket az összes elérhetőhöz képest.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from src.ai.legal_classifier import LegalClassifier, extract_unmapped_checkboxes
from src.pipeline.types import PassResult

if TYPE_CHECKING:
    from src.ai.field_recognizer import MappingConfig
    from src.main import FormFillerPipeline
    from src.models.canonical_model import DealData

logger = logging.getLogger(__name__)


def run_mapping_pass(
    pipeline: "FormFillerPipeline",
    deal: "DealData",
    mapping: "MappingConfig",
    coverage_threshold: float = 0.8,
    run_ai_subpass: bool = False,
    template_pdf: Optional[Path] = None,
    has_static_mapping: bool = True,
) -> tuple[dict, PassResult, dict]:
    """
    Lefuttatja a 2. pass-t.

    Args:
        pipeline: Egy FormFillerPipeline példány (a `_prepare_field_data`
            és `_resolve_legal` helper-eket használjuk, hogy a
            meglévő kitöltési logika változatlan maradjon).
        deal: Normalizált DealData (pass 1 kimenete).
        mapping: MappingConfig a template-hez.
        coverage_threshold: Ha a pass 1 lefedettség e felett van, az AI
            sub-pass-t kihagyjuk (jelenleg default off is).
        run_ai_subpass: Explicit kapcsoló az AI sub-pass-ra.
        template_pdf: A kitöltendő template PDF útvonala – az AI sub-pass
            ebből nyeri ki a leképezetlen AcroForm mezőket. Ha None,
            az AI sub-pass nem tud futni.
        has_static_mapping: Ha False, nincs előre elkészített mapping
            (dinamikus mód) – ilyenkor az AI sub-pass kötelező.

    Returns:
        (field_data: dict[str,str], PassResult, legal_values: dict[str,bool]).
        A `legal_values` dict a Pass 3 számára készül (PDF checkbox-ok
        kategória-alapú default értékei).
    """
    issues: list[str] = []
    metrics: dict = {
        "mapped": 0,
        "unmapped": 0,
        "total_mapping_fields": 0,
        "coverage": 0.0,
        "subpasses_run": [],
        "legal_values_count": 0,
        "ai_subpass": False,
    }

    # --- Sub-pass 1: Exact / canonical match -----------------------------
    logger.info("📋 Pass 2 (mapping): sub-pass 1 – canonical leképezés")
    field_data: dict[str, str] = pipeline._prepare_field_data(deal, mapping)
    metrics["subpasses_run"].append("1_exact_match")

    # --- Sub-pass 2: Salesforce label matching ---------------------------
    # (A canonical mapping már tartalmazza; külön lépés most nincs.)
    metrics["subpasses_run"].append("2_sf_label_match_skipped")

    # --- Sub-pass 3: AI classification (dynamic V4 Pro) ------------------
    # Feltételek:
    #   - Explicit `run_ai_subpass` kapcsoló (force / --dynamic-mapping)
    #   - VAGY nincs statikus mapping (has_static_mapping=False)
    #   - VAGY a coverage 80% alatt van (az első sub-pass után mérve)
    canonical_fields = [f for f in mapping.fields if f.canonical_field]
    total = len(canonical_fields)
    pre_ai_mapped = sum(1 for f in canonical_fields if field_data.get(f.pdf_field_name))
    pre_ai_coverage = (pre_ai_mapped / total) if total else 0.0

    should_run_ai = (
        run_ai_subpass
        or (not has_static_mapping)
        or (total > 0 and pre_ai_coverage < coverage_threshold)
    )

    if should_run_ai and template_pdf is not None:
        logger.info(
            "🤖 Pass 2 (mapping): sub-pass 3 – AI klasszifikáció (V4 Pro)"
        )
        ai_added = _run_dynamic_ai_subpass(
            pipeline=pipeline,
            deal=deal,
            mapping=mapping,
            field_data=field_data,
            template_pdf=template_pdf,
            metrics=metrics,
            issues=issues,
        )
        metrics["ai_subpass"] = True
        metrics["ai_fields_added"] = ai_added
        metrics["subpasses_run"].append("3_ai_classification")
    elif should_run_ai and template_pdf is None:
        logger.warning(
            "⚠️ Pass 2 (mapping): AI sub-pass kért, de template_pdf hiányzik – kihagyva"
        )
        issues.append("AI sub-pass kihagyva: template_pdf hiányzik.")
        metrics["subpasses_run"].append("3_ai_classification_skipped_no_template")
    else:
        metrics["subpasses_run"].append("3_ai_classification_skipped")

    # --- Sub-pass 4: Legal checkbox defaults -----------------------------
    # Kiszámoljuk a leképezetlen checkboxokra a kategória→értékeket;
    # a PDF-be írást a Pass 3 végzi (fill_legal_declarations_on_pdf).
    legal_values: dict[str, bool] = {}
    try:
        unmapped = extract_unmapped_checkboxes(mapping)
        if unmapped:
            classifier = LegalClassifier()
            classified = classifier.classify_batch(unmapped)
            legal_values = classifier.apply_defaults(classified, deal) or {}
            metrics["legal_unmapped_count"] = len(unmapped)
            metrics["legal_classified_count"] = len(classified or {})
            metrics["legal_values_count"] = len(legal_values)
            logger.info(
                "   ⚖️ Pass 2 (mapping): sub-pass 4 – legal defaults: "
                "%d érték %d leképezetlen checkboxhoz",
                len(legal_values),
                len(unmapped),
            )
        metrics["subpasses_run"].append("4_legal_defaults")
    except Exception as exc:
        msg = f"Legal defaults hiba: {exc}"
        issues.append(msg)
        logger.warning("   ⚠️ %s", msg)
        metrics["subpasses_run"].append("4_legal_defaults_failed")

    # --- Metrika: mapped / unmapped / coverage ---------------------------
    # Újra mérjük, mert az AI sub-pass új mezőket adhatott a mapping-hez.
    canonical_fields = [f for f in mapping.fields if f.canonical_field]
    total = len(canonical_fields)
    mapped = sum(1 for f in canonical_fields if field_data.get(f.pdf_field_name))
    unmapped = total - mapped
    coverage = (mapped / total) if total else 0.0

    metrics.update(
        mapped=mapped,
        unmapped=unmapped,
        total_mapping_fields=total,
        coverage=round(coverage, 4),
        field_data_count=len(field_data),
    )

    logger.info(
        "   ✓ Pass 2 (mapping): %d/%d mező kitöltve (coverage: %.1f%%)",
        mapped,
        total,
        coverage * 100,
    )

    return (
        field_data,
        PassResult(name="mapping", success=True, metrics=metrics, issues=issues),
        legal_values,
    )


def _run_dynamic_ai_subpass(
    pipeline: "FormFillerPipeline",
    deal: "DealData",
    mapping: "MappingConfig",
    field_data: dict,
    template_pdf: Path,
    metrics: dict,
    issues: list,
) -> int:
    """
    Valódi AI sub-pass 3 – DeepSeek V4 Pro dinamikus klasszifikáció.

    Lépések:
      1. AcroForm mezők kinyerése a template PDF-ből (scripts/analyze_pdf).
      2. "Unmapped" szűrés: olyan PDF mezők, amik még nincsenek a mapping
         canonical mezői között, VAGY ott vannak de nem kaptak értéket.
      3. FieldRecognizer.dynamic_classify_fields hívás (data-first:
         csak azokat a canonical mezőket kínáljuk fel, amikhez van deal adat).
      4. Új RecognizedField-ek hozzáadása a mapping-hez.
      5. _prepare_field_data újrafuttatása, hogy az új mezők is kapjanak
         értéket a deal-ből. Az eredményt beolvassuk a field_data-ba.

    Returns:
        Hány új mező kapott canonical leképezést (és értéket).
    """
    from src.ai.field_recognizer import (
        FieldRecognizer,
        RecognizedField,
        FieldType,
        MappingConfidence,
        CANONICAL_FIELDS,
        _get_available_deal_fields,
    )

    # 1. PDF mezők kinyerése
    try:
        import sys
        project_root = Path(__file__).resolve().parent.parent.parent
        scripts_dir = project_root / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from analyze_pdf import analyze_pdf  # type: ignore

        analysis = analyze_pdf(Path(template_pdf))
    except Exception as exc:
        msg = f"AI sub-pass: PDF analízis hiba ({template_pdf.name}): {exc}"
        logger.warning("   ⚠️ %s", msg)
        issues.append(msg)
        metrics["subpasses_run"].append("3_ai_classification_pdf_failed")
        return 0

    pdf_field_names: list[str] = analysis.get("field_names") or []
    field_types: dict = analysis.get("field_types") or {}
    if not pdf_field_names:
        logger.info("   ℹ️ Nincsenek AcroForm mezők a PDF-ben – AI sub-pass ugrása")
        return 0

    # 2. Unmapped szűrés
    mapped_canonical = {f.pdf_field_name for f in mapping.fields if f.canonical_field}
    already_filled = set(field_data.keys())
    unmapped_names = [
        n for n in pdf_field_names
        if n not in mapped_canonical and n not in already_filled
    ]
    logger.info(
        "   🔍 %d PDF mező, %d canonical-mapped, %d unmapped → AI-nak",
        len(pdf_field_names), len(mapped_canonical), len(unmapped_names),
    )
    metrics["ai_pdf_field_count"] = len(pdf_field_names)
    metrics["ai_unmapped_count"] = len(unmapped_names)

    if not unmapped_names:
        return 0

    # RecognizedField-ek összeállítása az unmapped mezőkhöz.
    # A label-t a mezőnév mellett a PDF-ben látható, a widget-hez közeli
    # szövegből is kinyerjük – kritikus, mert sok PDF generikus neveket
    # használ ("Text Field 304"), és anélkül az AI nem tud klasszifikálni.
    type_map = {"/Tx": "text", "/Btn": "checkbox", "/Ch": "dropdown"}
    widget_labels = _extract_widget_labels(Path(template_pdf))
    unmapped_fields: list[RecognizedField] = []
    for name in unmapped_names:
        raw_type = field_types.get(name, "")
        try:
            ftype = FieldType(type_map.get(raw_type, "text"))
        except Exception:
            ftype = FieldType.TEXT
        # Label: előnyben részesítjük a widget melletti látható szöveget;
        # ha nincs, visszaesünk a mezőnévre.
        label = widget_labels.get(name) or name.replace("_", " ").strip()
        unmapped_fields.append(
            RecognizedField(
                pdf_field_name=name,
                label=label,
                field_type=ftype,
                canonical_field=None,
                confidence=MappingConfidence.LOW,
                page_number=1,
            )
        )

    # 3. Data-first: csak az elérhető canonical mezőket kínáljuk fel.
    available = _get_available_deal_fields(deal)
    if not available:
        logger.info("   ℹ️ A deal-ben nincs feldolgozható adat – AI ugrása")
        return 0

    recognizer = FieldRecognizer()
    if not recognizer._client:
        issues.append("AI sub-pass: DEEPSEEK_API_KEY hiányzik.")
        logger.warning("   ⚠️ DEEPSEEK_API_KEY hiányzik – AI sub-pass ugrása")
        return 0

    classified = recognizer.dynamic_classify_fields(
        fields=unmapped_fields,
        canonical_model_fields=sorted(available),
        salesforce_field_descriptions=CANONICAL_FIELDS,
        pdf_name=getattr(mapping, "form_name", Path(template_pdf).name),
    )

    # 4. Új RecognizedField-ek hozzáadása a mapping-hez (form_type-on belül).
    newly_added: list[RecognizedField] = []
    for f in classified:
        if f.canonical_field and f.pdf_field_name not in mapped_canonical:
            mapping.fields.append(f)
            mapped_canonical.add(f.pdf_field_name)
            newly_added.append(f)

    if not newly_added:
        logger.info("   ℹ️ AI nem talált új leképezést")
        return 0

    # 5. Értékek kinyerése az új mezőkre a _prepare_field_data-val.
    # Egy átlátható "shadow" mapping-gel hívjuk, ami CSAK az új mezőket
    # tartalmazza – így az eredeti field_data nem íródik felül váratlanul.
    from src.ai.field_recognizer import MappingConfig as _MC
    shadow = _MC(
        bank_name=getattr(mapping, "bank_name", ""),
        form_name=getattr(mapping, "form_name", ""),
        form_type=getattr(mapping, "form_type", "acroform"),
        fields=newly_added,
    )
    try:
        new_values = pipeline._prepare_field_data(deal, shadow)
    except Exception as exc:
        msg = f"AI sub-pass: értékfeloldás hiba: {exc}"
        logger.warning("   ⚠️ %s", msg)
        issues.append(msg)
        return 0

    added = 0
    for k, v in new_values.items():
        if v:
            field_data[k] = v
            added += 1

    logger.info(
        "   ✅ AI sub-pass: %d új mező leképezve, %d kapott értéket",
        len(newly_added), added,
    )
    return added


def _extract_widget_labels(pdf_path: Path) -> dict[str, str]:
    """
    Minden AcroForm widget-hez kinyeri a hozzá legközelebbi látható
    szöveges label-t a PDF oldalon.

    A generikus nevű PDF-eknél (pl. "Text Field 304") ez az egyetlen
    módja annak, hogy az AI klasszifikálni tudja a mezőket. A widget
    téglalapja (rect) feletti és bal oldali szövegblokkokat keressük,
    mert a banki nyomtatványoknál a label általában felette/balra van.

    Returns:
        {field_name: label_str} dict. Csak azokat a mezőket tartalmazza,
        ahol sikerült értelmes (≥2 karakteres) label-t találni.
    """
    import fitz

    labels: dict[str, str] = {}
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as exc:
        logger.warning("widget label kinyerés hiba: %s", str(exc)[:120])
        return labels

    try:
        for page in doc:
            try:
                widgets = list(page.widgets() or [])
            except Exception:
                widgets = []
            # Oldal szövegblokkjai (x0, y0, x1, y1, text, ...)
            try:
                blocks = page.get_text("blocks") or []
            except Exception:
                blocks = []

            for w in widgets:
                try:
                    name = w.field_name or ""
                except Exception:
                    name = ""
                if not name:
                    continue
                rect = w.rect  # fitz.Rect
                # A widget feletti (y1 < rect.y0) és vele átfedésben lévő
                # (x-tengelyen közel) blokkokat keressük. Távolság: y0 - b_y1.
                candidates: list[tuple[float, str]] = []
                for b in blocks:
                    if len(b) < 5:
                        continue
                    bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
                    btext = b[4] if isinstance(b[4], str) else ""
                    btext = btext.strip()
                    if len(btext) < 2:
                        continue
                    # x-átfedés: a blokk és a widget vízszintesen érintkezzen
                    x_overlap = min(bx1, rect.x1) - max(bx0, rect.x0)
                    if x_overlap < -20:  # max 20pt vízszintes eltérés
                        continue
                    # Függőleges távolság: blokk a widget FELETT (by1 <= rect.y0)
                    if by1 <= rect.y0 + 2:
                        dist = rect.y0 - by1
                        if 0 <= dist <= 40:
                            candidates.append((dist, btext))
                    # VAGY bal oldalról: blokk a widgettől BALRA
                    elif bx1 <= rect.x0 + 2 and abs((by0 + by1) / 2 - (rect.y0 + rect.y1) / 2) < 15:
                        dist = rect.x0 - bx1
                        if 0 <= dist <= 40:
                            candidates.append((dist + 50, btext))  # kis büntetés
                if candidates:
                    candidates.sort(key=lambda c: c[0])
                    # Legközelebbi blokk első nem-üres sora
                    best_text = candidates[0][1]
                    first_line = ""
                    for line in best_text.splitlines():
                        line = line.strip()
                        if line:
                            first_line = line
                            break
                    if len(first_line) >= 2:
                        labels[name] = first_line[:100]
    finally:
        doc.close()

    return labels

    # --- Sub-pass 4: Legal checkbox defaults -----------------------------
    # Kiszámoljuk a leképezetlen checkboxokra a kategória→értékeket;
    # a PDF-be írást a Pass 3 végzi (fill_legal_declarations_on_pdf).
    legal_values: dict[str, bool] = {}
    try:
        unmapped = extract_unmapped_checkboxes(mapping)
        if unmapped:
            classifier = LegalClassifier()
            classified = classifier.classify_batch(unmapped)
            legal_values = classifier.apply_defaults(classified, deal) or {}
            metrics["legal_unmapped_count"] = len(unmapped)
            metrics["legal_classified_count"] = len(classified or {})
            metrics["legal_values_count"] = len(legal_values)
            logger.info(
                "   ⚖️ Pass 2 (mapping): sub-pass 4 – legal defaults: "
                "%d érték %d leképezetlen checkboxhoz",
                len(legal_values),
                len(unmapped),
            )
        metrics["subpasses_run"].append("4_legal_defaults")
    except Exception as exc:
        msg = f"Legal defaults hiba: {exc}"
        issues.append(msg)
        logger.warning("   ⚠️ %s", msg)
        metrics["subpasses_run"].append("4_legal_defaults_failed")

    # --- Metrika: mapped / unmapped / coverage ---------------------------
    # Csak azokat a mapping mezőket számoljuk, amelyek canonical_field-lel
    # rendelkeznek (a tiszta checkbox/üres mezőket nem — azokat a legal
    # sub-pass kezeli).
    canonical_fields = [f for f in mapping.fields if f.canonical_field]
    total = len(canonical_fields)
    mapped = sum(1 for f in canonical_fields if field_data.get(f.pdf_field_name))
    unmapped = total - mapped
    coverage = (mapped / total) if total else 0.0

    metrics.update(
        mapped=mapped,
        unmapped=unmapped,
        total_mapping_fields=total,
        coverage=round(coverage, 4),
        field_data_count=len(field_data),
    )

    logger.info(
        "   ✓ Pass 2 (mapping): %d/%d mező kitöltve (coverage: %.1f%%)",
        mapped,
        total,
        coverage * 100,
    )

    return (
        field_data,
        PassResult(name="mapping", success=True, metrics=metrics, issues=issues),
        legal_values,
    )

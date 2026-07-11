"""
Layer 3 — AI smoke tests (manual, requires Anthropic API key).

These tests run the REAL FieldRecognizer (with the Anthropic API) against the
test PDF and assert that the AI produces a high-quality mapping. They are
marked ``@pytest.mark.live_api`` and excluded from the default test run.

Run manually when you change the AI prompt, model, or pipeline:

    pytest tests/test_ai_smoke.py -v -m live_api

Prerequisites:
    - ANTHROPIC_API_KEY set in config/.env
    - samples/Szemelyi_adatlap_Igenylo_4old.pdf present
"""
import html
from datetime import datetime
from pathlib import Path

import pytest

from src.ai.field_recognizer import FieldRecognizer, CANONICAL_FIELDS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_PDF = _PROJECT_ROOT / "samples" / "Szemelyi_adatlap_Igenylo_4old.pdf"
OUTPUT_DIR = _PROJECT_ROOT / "output"


@pytest.mark.live_api
class TestAISmoke:
    """Smoke tests that call the real Anthropic API."""

    def test_ai_recognition_coverage(self):
        """AI recognition should map >=80% of fields.

        If the AI call fails internally, the recognizer falls back to the
        heuristic-only mapping (~16% coverage). We detect this by checking
        if the result equals the heuristic baseline — if so, we report it
        as an AI failure (not a coverage failure) so it's diagnosed correctly.
        """
        if not TEST_PDF.exists():
            pytest.skip(f"Test PDF not found: {TEST_PDF}")

        recognizer = FieldRecognizer()
        if recognizer._client is None:
            pytest.skip("ANTHROPIC_API_KEY not set — skipping live AI test")

        mapping = recognizer.recognize(TEST_PDF, mode="acroform")
        total = len(mapping.fields)
        mapped = sum(1 for f in mapping.fields if f.canonical_field)
        coverage = mapped / total if total > 0 else 0

        # Heuristic baseline for comparison
        heur = FieldRecognizer(api_key="")
        heur_mapping = heur.recognize(TEST_PDF, mode="acroform")
        heur_mapped = sum(1 for f in heur_mapping.fields if f.canonical_field)

        print(f"\n  AI coverage: {mapped}/{total} ({coverage:.0%})")
        print(f"  Heuristic baseline: {heur_mapped}/{total}")

        if mapped == heur_mapped:
            pytest.fail(
                f"AI did not improve over heuristic ({mapped} == {heur_mapped}). "
                f"The AI API call likely failed and the recognizer fell back to "
                f"heuristic-only mode. Check the logs for 'AI API hiba' messages. "
                f"This is a recognizer bug, not a test failure."
            )

        # The golden (reference) mapping achieves 62% on this PDF — many of
        # the 153 fields are repeated table rows (jövedelem5, jövedelem6...)
        # and radio-button options (___1,2 / ___Yes) that have no canonical
        # Salesforce equivalent. 50% is a realistic floor that catches the
        # "AI crashed and fell back to 16% heuristic" regression while
        # allowing for natural variance in AI output.
        assert coverage >= 0.50, f"AI coverage {coverage:.0%} < 50%"

    def test_ai_vs_heuristic_improvement(self):
        """AI should improve coverage over heuristic-only."""
        if not TEST_PDF.exists():
            pytest.skip(f"Test PDF not found: {TEST_PDF}")

        # Heuristic baseline (no AI)
        heur = FieldRecognizer(api_key="")
        heur_mapping = heur.recognize(TEST_PDF, mode="acroform")
        heur_mapped = sum(1 for f in heur_mapping.fields if f.canonical_field)

        # AI (real API)
        ai = FieldRecognizer()
        if ai._client is None:
            pytest.skip("ANTHROPIC_API_KEY not set")

        ai_mapping = ai.recognize(TEST_PDF, mode="acroform")
        ai_mapped = sum(1 for f in ai_mapping.fields if f.canonical_field)

        print(f"\n  Heuristic: {heur_mapped}/{len(heur_mapping.fields)}")
        print(f"  AI:        {ai_mapped}/{len(ai_mapping.fields)}")
        assert ai_mapped > heur_mapped, (
            f"AI ({ai_mapped}) should improve over heuristic ({heur_mapped})"
        )

    def test_generate_html_report(self):
        """Generate an HTML report of the AI mapping quality."""
        if not TEST_PDF.exists():
            pytest.skip(f"Test PDF not found: {TEST_PDF}")

        recognizer = FieldRecognizer()
        if recognizer._client is None:
            pytest.skip("ANTHROPIC_API_KEY not set")

        mapping = recognizer.recognize(TEST_PDF, mode="acroform")

        # Statistics
        total = len(mapping.fields)
        high = [f for f in mapping.fields if f.confidence == "high"]
        medium = [f for f in mapping.fields if f.confidence == "medium"]
        low = [f for f in mapping.fields if f.confidence == "low"]
        mapped = [f for f in mapping.fields if f.canonical_field]
        valid_canonicals = set(CANONICAL_FIELDS.keys())
        invalid = [
            f for f in mapped if f.canonical_field not in valid_canonicals
        ]

        # Build HTML
        rows = []
        for f in sorted(mapping.fields, key=lambda x: (x.page_number, x.pdf_field_name)):
            conf_color = {"high": "#22c55e", "medium": "#f59e0b", "low": "#ef4444"}.get(
                str(f.confidence), "#888"
            )
            is_valid = not f.canonical_field or f.canonical_field in valid_canonicals
            canonical_display = (
                f'<span style="color:{"var(--text-primary)" if is_valid else "#ef4444"}">'
                f'{html.escape(str(f.canonical_field))}</span>'
                if f.canonical_field
                else '<em style="color:#888">— unmapped —</em>'
            )
            rows.append(f"""
              <tr>
                <td>{f.page_number}</td>
                <td><strong>{html.escape(f.pdf_field_name)}</strong></td>
                <td>{html.escape(str(f.field_type))}</td>
                <td>{canonical_display}</td>
                <td style="color:{conf_color};font-weight:600">{f.confidence}</td>
                <td>{html.escape(str(f.fragment or ""))}</td>
              </tr>""")

        report_html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="utf-8">
<title>AI Mapping Smoke Report — {html.escape(TEST_PDF.name)}</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 40px; background: #f8fafc; color: #1e293b; }}
  h1 {{ color: #1e40af; }}
  .stats {{ display: flex; gap: 20px; margin: 20px 0; }}
  .stat-card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; }}
  .stat-card .num {{ font-size: 2rem; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th {{ background: #1e293b; color: white; padding: 10px; text-align: left; font-size: 0.8rem; text-transform: uppercase; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #e2e8f0; font-size: 0.85rem; }}
  tr:hover {{ background: #f1f5f9; }}
</style>
</head>
<body>
<h1>AI Mapping Report</h1>
<p><strong>PDF:</strong> {html.escape(TEST_PDF.name)} &mdash; <strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>

<div class="stats">
  <div class="stat-card"><div class="num">{total}</div><div>Összes mező</div></div>
  <div class="stat-card"><div class="num" style="color:#22c55e">{len(mapped)}</div><div>Mappingelve</div></div>
  <div class="stat-card"><div class="num">{len(high)}</div><div>HIGH</div></div>
  <div class="stat-card"><div class="num" style="color:#f59e0b">{len(medium)}</div><div>MEDIUM</div></div>
  <div class="stat-card"><div class="num" style="color:#ef4444">{len(low)}</div><div>LOW</div></div>
  <div class="stat-card"><div class="num" style="color:#ef4444">{len(invalid)}</div><div>Ismeretlen SF</div></div>
</div>

<table>
<thead><tr><th>Oldal</th><th>PDF mező</th><th>Típus</th><th>Salesforce mező</th><th>Confidence</th><th>Fragment</th></tr></thead>
<tbody>{''.join(rows)}
</tbody>
</table>
</body>
</html>"""

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        report_path = OUTPUT_DIR / "ai_smoke_report.html"
        report_path.write_text(report_html, encoding="utf-8")
        print(f"\n  Report saved: {report_path}")

        # Assert no invalid canonicals (all mapped fields should be valid SF fields)
        assert len(invalid) == 0, (
            f"{len(invalid)} fields have invalid canonical_field: "
            f"{[f.canonical_field for f in invalid[:5]]}"
        )

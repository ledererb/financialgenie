"""
Shared pytest fixtures for the FinancialGenie test suite.

No env-vars are stripped here. Tests that trigger real Anthropic
(Claude) API calls must opt-in via the ``@pytest.mark.live_api`` marker,
which is excluded from the default run via ``pytest.ini``.
"""
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path so src.* imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ── Paths ──────────────────────────────────────────────────────────────────

#: The 4-page OTP "Személyi adatlap – Igénylő" test PDF (153 AcroForm fields).
TEST_PDF_PATH = _PROJECT_ROOT / "samples" / "Szemelyi_adatlap_Igenylo_4old.pdf"

#: Golden mapping JSON produced by the AI for the test PDF.
GOLDEN_MAPPING_PATH = (
    _PROJECT_ROOT / "src" / "mapping" / "szemelyi_adatlap_igenylo_4old_mapping.json"
)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def heuristic_recognizer():
    """A FieldRecognizer guaranteed to run in heuristic-only mode (no AI).

    Always passes ``api_key=""`` so the Anthropic client is never created,
    regardless of whether ``ANTHROPIC_API_KEY`` is set in the environment.
    This makes the mapping output fully deterministic.
    """
    from src.ai.field_recognizer import FieldRecognizer

    recognizer = FieldRecognizer(api_key="")
    assert recognizer._client is None, "Recognizer should be in heuristic-only mode"
    return recognizer


@pytest.fixture(scope="session")
def test_pdf():
    """The 4-page OTP test PDF. Skips the test if the file is absent."""
    if not TEST_PDF_PATH.exists():
        pytest.skip(f"Test PDF not found: {TEST_PDF_PATH}")
    return TEST_PDF_PATH


@pytest.fixture(scope="session")
def golden_mapping():
    """The AI-generated golden mapping for the test PDF.

    Serves as a reference for "what a good mapping looks like". Skips if absent.
    """
    if not GOLDEN_MAPPING_PATH.exists():
        pytest.skip(f"Golden mapping not found: {GOLDEN_MAPPING_PATH}")
    from src.ai.field_recognizer import MappingConfig

    return MappingConfig.load(GOLDEN_MAPPING_PATH)

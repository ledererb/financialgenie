"""
Shared pytest fixtures for the FinancialGenie test suite.

Database paths and the signing secret are redirected before test module
collection, including modules that import backend singletons at module scope.
Tests that trigger real Anthropic (Claude) API calls must opt-in via the
``@pytest.mark.live_api`` marker, which is excluded by ``pytest.ini``.
"""
import atexit
import os
import sys
import tempfile
from pathlib import Path

import pytest

# This executes while pytest loads the initial conftest, before importing any
# test module. Override even inherited values so an exported production path or
# secret can never be reused by the test suite.
_TEST_STATE = tempfile.TemporaryDirectory(prefix="financialgenie-pytest-")
_TEST_STATE_PATH = Path(_TEST_STATE.name)
os.environ["CATALOG_DB_PATH"] = str(_TEST_STATE_PATH / "catalog.db")
os.environ["AUTH_DB_PATH"] = str(_TEST_STATE_PATH / "auth.db")
os.environ["AUTH_SECRET"] = "financialgenie-pytest-session-secret-0123456789abcdef"
atexit.register(_TEST_STATE.cleanup)


def pytest_sessionfinish(session, exitstatus):
    """Remove import-time databases and sidecar files at the session boundary."""
    _TEST_STATE.cleanup()


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

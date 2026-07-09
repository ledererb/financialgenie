"""Evidence tests for backend-only fixes (M7 sanitization, L2 add_field).

These tests import ONLY backend modules (server / mapping_service), following
the same sys.path pattern as tests/test_upload_api.py. Mixing backend and src
imports in one module collides on the ``config`` name (backend/config.py vs the
project ``config/`` package), so backend-facing tests live here in isolation.

NB: under pytest the project ``config/`` package would shadow ``backend/config.py``
(both are importable as ``config`` because PROJECT_ROOT is on sys.path via the
``tests/__init__.py`` ancestor). We preload ``backend/config.py`` and register it
as ``config`` in ``sys.modules`` so the backend modules resolve their flat
``from config import ...`` imports. This is test-only plumbing — production code
is untouched.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Make backend/config.py the canonical ``config`` module for this test process.
_BACKEND = PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_spec = importlib.util.spec_from_file_location("config", _BACKEND / "config.py")
_backend_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_config)
sys.modules["config"] = _backend_config


# ── M7: filename sanitization (Hungarian accents + collision) ──────────────
class TestM7SanitizeFilename:
    def test_hungarian_accents_transliterated_not_stripped(self):
        from server import sanitize_filename
        # The bug report: "Pénzügyi átadás.pdf" → "Pnzughtyi_tads.pdf" (stripped).
        # After NFKD: accents fold to ASCII base letters (case preserved).
        out = sanitize_filename("Pénzügyi átadás.pdf")
        assert out == "Penzugyi_atadas.pdf", out

    def test_special_chars_underscored(self):
        from server import sanitize_filename
        out = sanitize_filename("Piaci hitel (2026).pdf")
        assert out == "Piaci_hitel_2026.pdf", out

    def test_collision_safe_suffix(self):
        from server import sanitize_filename
        existing = {"demo.pdf"}
        out = sanitize_filename("demo.pdf", existing=existing)
        assert out == "demo_2.pdf", out

    def test_collision_increments(self):
        from server import sanitize_filename
        existing = {"demo.pdf", "demo_2.pdf", "demo_3.pdf"}
        out = sanitize_filename("demo.pdf", existing=existing)
        assert out == "demo_4.pdf", out

    def test_empty_filename_gets_random_name(self):
        from server import sanitize_filename
        out = sanitize_filename("")
        assert out.endswith(".pdf")
        assert out.startswith("uploaded_")

    def test_double_accent_letters_o_u(self):
        from server import sanitize_filename
        # ő / ű must fold to "o"/"u" (not be dropped) under NFKD.
        out = sanitize_filename("Nagy űzlet ősz.pdf")
        assert "uzlet" in out and "osz" in out, out
        assert "ű" not in out and "ő" not in out


# ── L2: add_field persists fill_rule / checkbox_group ──────────────────────
class TestL2AddFieldPersistsExtras:
    def test_add_field_keeps_fill_rule_and_checkbox_group(self):
        from mapping_service import MappingService

        svc = MappingService()
        mapping = {"fields": [], "character_groups": []}
        record = svc.add_field(mapping, {
            "pdf_field_name": "cb_yes",
            "field_type": "checkbox",
            "canonical_field": None,
            "fill_rule": {"type": "static", "value": "igen"},
            "checkbox_group": {"group_id": "g1", "match_value": "Igen"},
        })
        assert record["fill_rule"] == {"type": "static", "value": "igen"}
        assert record["checkbox_group"] == {"group_id": "g1", "match_value": "Igen"}
        # And the stored mapping carries them too.
        stored = mapping["fields"][0]
        assert stored["fill_rule"] is not None
        assert stored["checkbox_group"] is not None

    def test_add_field_defaults_extras_to_none(self):
        from mapping_service import MappingService

        svc = MappingService()
        mapping = {"fields": []}
        record = svc.add_field(mapping, {"pdf_field_name": "plain"})
        # Fields without rules get None (not missing keys).
        assert record["fill_rule"] is None
        assert record["checkbox_group"] is None

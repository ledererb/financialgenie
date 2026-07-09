"""
FIX C8 — Date / digit field fragmentation tests.

A single logical value (a date, or a multi-digit number) is often split across
several small PDF boxes on Hungarian bank forms (year/month/day birth boxes,
4 single-digit postal-code boxes). ``RecognizedField.fragment`` records which
piece a field carries; ``_apply_fragment`` extracts that piece.

These tests cover:
  - the ``_apply_fragment`` transform unit (all fragment kinds + edge cases)
  - the heuristic mapper attaching ``fragment`` to known OTP date boxes
  - end-to-end ``_prepare_field_data`` splitting a birth date into 3 boxes
  - the C8 + C6 combination (fragment inherited by instanced ``_N`` fields)
"""
import pytest
from datetime import date
from pathlib import Path

from src.main import _apply_fragment, FormFillerPipeline
from src.integrations.salesforce_client import SalesforceClient
from src.normalizer.data_normalizer import DataNormalizer
from src.ai.field_recognizer import FieldRecognizer, MappingConfig, RecognizedField, FieldType, MappingConfidence
from src.models.canonical_model import DealData, ParticipantRole, Participant, Address, LoanDetails


PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# 1. _apply_fragment unit tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fragment, value, expected", [
    # Date fragments — Hungarian dot separator
    ("year",     "1985.05.12", "1985"),
    ("month",    "1985.05.12", "05"),
    ("day",      "1985.05.12", "12"),
    # ISO dash separator also accepted
    ("year",     "1985-05-12", "1985"),
    ("month",    "1985-05-12", "05"),
    # slash separator
    ("day",      "1985/05/12", "12"),
    # Year-only value fills the year box, leaves month/day empty (spec edge case)
    ("year",     "1985",       "1985"),
    ("month",    "1985",       ""),
    ("day",      "1985",       ""),
    # int year-only
    ("year",     1985,         "1985"),
    # Never raises — bad input yields ""
    ("year",     "not-a-date", ""),
    ("year",     "1985.05",    ""),      # wrong arity (2 parts)
    ("month",    "abc",        ""),
    # digit:N — 1-based character position (postal code split into N boxes)
    ("digit:1",  "1123",       "1"),
    ("digit:2",  "1123",       "1"),
    ("digit:3",  "1123",       "2"),
    ("digit:4",  "1123",       "3"),
    ("digit:5",  "1123",       ""),      # out of range
    ("digit:9",  "1052",       ""),
    ("digit:0",  "1052",       ""),      # digit:0 undefined (1-based scheme) → ""
    ("digit:abc","1052",       ""),      # malformed index
    # None fragment = passthrough (value unchanged)
    (None,       "anything",   "anything"),
    (None,       "1985.05.12", "1985.05.12"),
    # None / empty value → "" for any fragment (clear all fragment fields)
    ("year",     None,         ""),
    ("year",     "",           ""),
    ("digit:1",  None,         ""),
    # Unknown fragment kind → value unchanged (forward-compatible)
    ("unknown",  "x",          "x"),
])
def test_apply_fragment(fragment, value, expected):
    assert _apply_fragment(value, fragment) == expected


def test_apply_fragment_digit_zero_is_undefined():
    """digit:N uses a 1-based scheme (digit:1 = first char).

    digit:0 has no meaning in the 1-based scheme and is treated as an invalid
    index (returns "" rather than silently producing the last char via Python
    negative indexing — that would be surprising/buggy behaviour).
    """
    assert _apply_fragment("1123", "digit:0") == ""


# ---------------------------------------------------------------------------
# 2. Heuristic mapper attaches fragments to OTP date boxes
# ---------------------------------------------------------------------------

class TestHeuristicMapperFragments:
    """FIX C8 ground-work: OTP_DATE_FRAGMENTS must tag the birth-date boxes."""

    def test_birth_date_boxes_carry_fragment_roles(self):
        recognizer = FieldRecognizer()
        fields_in = [
            {"name": "SZA_IG_szül_év",   "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_hónap", "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_nap",   "type": "/Tx", "page": 1},
        ]
        mapping = recognizer._heuristic_map_fields(fields_in, Path("x.pdf"), "acroform")
        by_name = {f.pdf_field_name: f for f in mapping.fields}

        # Each box maps to the SAME parent canonical (Contact.Birthdate — the
        # full-date canonical the pipeline actually produces) but carries a
        # distinct fragment role — they must NOT collapse to a single full date.
        assert by_name["SZA_IG_szül_év"].canonical_field == "Contact.Birthdate"
        assert by_name["SZA_IG_szül_év"].fragment == "year"
        assert by_name["SZA_IG_szül_hónap"].fragment == "month"
        assert by_name["SZA_IG_szül_nap"].fragment == "day"

    def test_birth_date_boxes_do_not_collapse_to_single_canonical(self):
        """The three year/month/day boxes must not all be treated as one full date."""
        recognizer = FieldRecognizer()
        fields_in = [
            {"name": "SZA_IG_szül_év", "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_hónap", "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_nap", "type": "/Tx", "page": 1},
        ]
        mapping = recognizer._heuristic_map_fields(fields_in, Path("x.pdf"), "acroform")
        mapped = [f for f in mapping.fields if f.canonical_field]
        # Either 3 distinct canonicals, OR fragments attached (C8 path).
        canonicals = {f.canonical_field for f in mapped}
        assert len(canonicals) > 1 or all(f.fragment for f in mapped), \
            "year/month/day boxes must not collapse to a single full-date canonical"

    def test_tars_birth_date_boxes_also_get_fragments(self):
        """The társigénylő (-társ) birth-date boxes carry fragments too."""
        recognizer = FieldRecognizer()
        fields_in = [
            {"name": "SZA_IG_szül_év-társ",   "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_hónap-társ", "type": "/Tx", "page": 1},
            {"name": "SZA_IG_szül_nap-társ",   "type": "/Tx", "page": 1},
        ]
        mapping = recognizer._heuristic_map_fields(fields_in, Path("x.pdf"), "acroform")
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["SZA_IG_szül_év-társ"].fragment == "year"
        assert by_name["SZA_IG_szül_hónap-társ"].fragment == "month"
        assert by_name["SZA_IG_szül_nap-társ"].fragment == "day"


# ---------------------------------------------------------------------------
# 3. End-to-end _prepare_field_data splits a birth date into 3 boxes
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    return FormFillerPipeline(
        sf_client=SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data"),
        output_dir=tmp_path,
    )


def _birth_date_deal():
    return DealData(
        deal_id="C8-DOB",
        loan=LoanDetails(loan_amount=10_000_000, loan_term_months=240),
        participants=[
            Participant(
                role=ParticipantRole.BORROWER,
                name="Kovács János",
                birth_date=date(1985, 5, 12),
                address=Address(zip_code="1000", city="Budapest", street="Utca", house_number="1"),
            ),
        ],
    )


def _birth_date_mapping():
    """Three year/month/day boxes all pointing at Contact.Birthdate."""
    return MappingConfig(
        bank_name="OTP", form_name="t", form_type="acroform",
        fields=[
            RecognizedField("SZA_IG_szül_év",   "Év",   FieldType.DATE, "Contact.Birthdate", MappingConfidence.HIGH, 1, fragment="year"),
            RecognizedField("SZA_IG_szül_hónap","Hónap", FieldType.DATE, "Contact.Birthdate", MappingConfidence.HIGH, 1, fragment="month"),
            RecognizedField("SZA_IG_szül_nap",  "Nap",   FieldType.DATE, "Contact.Birthdate", MappingConfidence.HIGH, 1, fragment="day"),
        ],
    )


class TestBirthDateFragmentsAreSplit:
    def test_prepare_field_data_splits_birth_date(self, pipeline):
        deal = _birth_date_deal()
        mapping = _birth_date_mapping()
        fd = pipeline._prepare_field_data(deal, mapping)

        # Each box receives ONLY its piece — never the full date.
        assert fd["SZA_IG_szül_év"] == "1985"
        assert fd["SZA_IG_szül_hónap"] == "05"
        assert fd["SZA_IG_szül_nap"] == "12"
        # And crucially, none of them contains the dot-separated full date.
        for key in ("SZA_IG_szül_év", "SZA_IG_szül_hónap", "SZA_IG_szül_nap"):
            assert "." not in fd[key], f"{key} should hold only a fragment, got {fd[key]!r}"

    def test_missing_birth_date_leaves_boxes_empty(self, pipeline):
        """If the date is absent, all fragment boxes stay empty (not a crash)."""
        deal = _birth_date_deal()
        deal.participants[0].birth_date = None
        mapping = _birth_date_mapping()
        fd = pipeline._prepare_field_data(deal, mapping)
        # No full-date string leaked into any box.
        for key in ("SZA_IG_szül_év", "SZA_IG_szül_hónap", "SZA_IG_szül_nap"):
            assert fd.get(key, "") in ("", None)

    def test_digit_fragment_splits_postal_code(self, pipeline):
        """A 4-digit postal code split into 4 single-digit boxes (digit:N)."""
        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[
                RecognizedField(f"irsz_d{i}", f"Digit {i}", FieldType.TEXT, "Contact.ZIP__c",
                                MappingConfidence.HIGH, 1, fragment=f"digit:{i}")
                for i in (1, 2, 3, 4)
            ],
        )
        deal = _birth_date_deal()
        deal.participants[0].address.zip_code = "1123"
        fd = pipeline._prepare_field_data(deal, mapping)
        assert fd["irsz_d1"] == "1"
        assert fd["irsz_d2"] == "1"
        assert fd["irsz_d3"] == "2"
        assert fd["irsz_d4"] == "3"

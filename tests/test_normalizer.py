"""
FinancialGenie – Unit tesztek: Adatnormalizáló
"""

import pytest
from datetime import date

from src.normalizer.data_normalizer import DataNormalizer
from src.models.canonical_model import ParticipantRole, PropertyType


@pytest.fixture
def normalizer():
    return DataNormalizer()


@pytest.fixture
def canonical_json():
    """Dummy adat kanonikus JSON formátumban."""
    return {
        "deal_id": "SF-TEST-001",
        "salesforce_stage": "Dokumentáció készítés",
        "loan": {
            "loan_amount": 30000000,
            "loan_term_months": 240,
            "interest_period": "5 év fix",
            "loan_purpose": "Lakásvásárlás",
            "product_name": "OTP Lakáshitel",
        },
        "participants": [
            {
                "role": "adós",
                "name": "Nagy István",
                "birth_name": "Nagy István",
                "mother_name": "Kiss Mária",
                "birth_place": "Budapest",
                "birth_date": "1985-03-15",
                "personal_id": "1234567AB",
                "tax_id": "8123456789",
                "address": {
                    "zip_code": "1052",
                    "city": "Budapest",
                    "street": "Váci utca",
                    "house_number": "10",
                },
                "phone": "+36 30 123 4567",
                "email": "nagy.istvan@gmail.com",
                "is_active": True,
            }
        ],
        "properties": [
            {
                "address": {
                    "zip_code": "1052",
                    "city": "Budapest",
                    "street": "Váci utca",
                    "house_number": "10",
                },
                "parcel_number": "12345/1/A/2",
                "area_sqm": 65.0,
                "property_type": "lakás",
                "estimated_value": 45000000,
            }
        ],
    }


class TestCanonicalJsonNormalization:
    def test_basic_normalization(self, normalizer, canonical_json):
        deal = normalizer.normalize_deal(canonical_json)
        assert deal.deal_id == "SF-TEST-001"
        assert deal.loan.loan_amount == 30_000_000
        assert deal.loan.loan_term_months == 240

    def test_participant_normalization(self, normalizer, canonical_json):
        deal = normalizer.normalize_deal(canonical_json)
        assert len(deal.participants) == 1
        p = deal.participants[0]
        assert p.name == "Nagy István"
        assert p.role == ParticipantRole.BORROWER
        assert p.birth_date == date(1985, 3, 15)

    def test_property_normalization(self, normalizer, canonical_json):
        deal = normalizer.normalize_deal(canonical_json)
        assert len(deal.properties) == 1
        prop = deal.properties[0]
        assert prop.parcel_number == "12345/1/A/2"
        assert prop.property_type == PropertyType.APARTMENT

    def test_inactive_participant(self, normalizer, canonical_json):
        canonical_json["participants"].append({
            "role": "adóstárs",
            "name": "Törölt Személy",
            "is_active": False,
        })
        deal = normalizer.normalize_deal(canonical_json)
        assert len(deal.participants) == 2
        assert len(deal.active_participants) == 1


class TestDateParsing:
    def test_iso_format(self, normalizer):
        result = normalizer._parse_date("1985-03-15")
        assert result == date(1985, 3, 15)

    def test_dot_format(self, normalizer):
        result = normalizer._parse_date("1985.03.15")
        assert result == date(1985, 3, 15)

    def test_none_value(self, normalizer):
        assert normalizer._parse_date(None) is None

    def test_date_object(self, normalizer):
        d = date(1985, 3, 15)
        assert normalizer._parse_date(d) == d

    def test_invalid_date(self, normalizer):
        assert normalizer._parse_date("not_a_date") is None


class TestSafeConversions:
    def test_safe_int(self):
        assert DataNormalizer._safe_int(42) == 42
        assert DataNormalizer._safe_int("42") == 42
        assert DataNormalizer._safe_int(42.7) == 42
        assert DataNormalizer._safe_int(None) is None
        assert DataNormalizer._safe_int("abc") is None

    def test_safe_float(self):
        assert DataNormalizer._safe_float(3.14) == 3.14
        assert DataNormalizer._safe_float("3.14") == 3.14
        assert DataNormalizer._safe_float(None) is None
        assert DataNormalizer._safe_float("abc") is None

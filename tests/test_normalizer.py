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


class TestAddressParsing:
    """FIX H3: a cím-parser (salesforce_client._parse_address_string) robusztussága."""

    @pytest.fixture
    def parser(self):
        # Csak a statikus metódusra van szükségünk – nem hívunk fel SF-et.
        from src.integrations.salesforce_client import SalesforceClient
        sc = SalesforceClient.__new__(SalesforceClient)
        return sc._parse_address_string

    def test_budapest_district_extracted(self, parser):
        """A kerület (római szám) kerüljön a district mezőbe, ne az utcába."""
        result = parser("1052 Budapest, II. kerület, Váci utca 10")
        assert result["district"] == "II"
        assert "kerület" not in result["street"]
        assert result["street"] == "Váci utca"
        assert result["house_number"] == "10"
        assert result["city"] == "Budapest"

    def test_district_no_house_number(self, parser):
        result = parser("1011 Budapest, I. kerület, Fortuna utca")
        assert result["district"] == "I"
        assert result["street"] == "Fortuna utca"
        assert result["house_number"] == ""

    def test_high_district_number(self, parser):
        result = parser("1132 Budapest, XIII. kerület, Szent István körút 1")
        assert result["district"] == "XIII"
        assert result["house_number"] == "1"

    def test_street_only_no_crash(self, parser):
        """Cím házszám nélkül ne omoljon össze."""
        result = parser("1052 Budapest, Váci utca")
        assert result["street"] == "Váci utca"
        assert result["house_number"] == ""
        assert result["floor"] is None

    def test_floor_missing_key(self, parser):
        """A visszaadott dict mindig tartalmazza a floor kulcsot (None default)."""
        result = parser("2000 Szentendre, Kossuth tér 5")
        assert "floor" in result
        assert result["floor"] is None
        assert "door" in result
        assert result["door"] is None

    def test_floor_and_door_parsed(self, parser):
        result = parser("1052 Budapest, Váci utca 10, 3. em. 12. ajtó")
        assert result["floor"] == "3"
        assert result["door"] == "12"

    def test_empty_address(self, parser):
        result = parser("")
        assert result["zip_code"] == ""
        assert result["district"] is None
        assert result["floor"] is None

    def test_non_budapest_no_false_district(self, parser):
        """Budapesten kívül ne próbáljon kerületet keresni."""
        result = parser("5000 Szolnok, Tószegi út 5")
        assert result["district"] is None
        assert result["city"] == "Szolnok"

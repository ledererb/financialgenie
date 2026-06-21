"""
FinancialGenie – Unit tesztek: Kanonikus adatmodell
"""

import pytest
from datetime import date

from src.models.canonical_model import (
    DealData, LoanDetails, Participant, ParticipantRole,
    Property, PropertyType, Address
)


@pytest.fixture
def sample_address():
    return Address(
        zip_code="1052",
        city="Budapest",
        street="Váci utca",
        house_number="10",
        floor="3",
        door="12",
    )


@pytest.fixture
def sample_borrower(sample_address):
    return Participant(
        role=ParticipantRole.BORROWER,
        name="Nagy István",
        birth_name="Nagy István",
        mother_name="Kiss Mária",
        birth_place="Budapest",
        birth_date=date(1985, 3, 15),
        personal_id="1234567AB",
        tax_id="8123456789",
        address=sample_address,
        phone="+36 30 123 4567",
        email="nagy.istvan@gmail.com",
    )


@pytest.fixture
def sample_property(sample_address):
    return Property(
        address=sample_address,
        parcel_number="12345/1/A/2",
        area_sqm=65.0,
        property_type=PropertyType.APARTMENT,
        estimated_value=45_000_000,
    )


@pytest.fixture
def sample_deal(sample_borrower, sample_property):
    return DealData(
        deal_id="SF-123456",
        salesforce_stage="Dokumentáció készítés",
        loan=LoanDetails(
            loan_amount=30_000_000,
            loan_term_months=240,
            interest_period="5 év fix",
            loan_purpose="Lakásvásárlás",
            product_name="OTP Lakáshitel",
        ),
        participants=[sample_borrower],
        properties=[sample_property],
    )


class TestAddress:
    def test_full_address(self, sample_address):
        addr = sample_address.full_address
        assert "1052" in addr
        assert "Budapest" in addr
        assert "Váci utca" in addr

    def test_full_address_without_floor(self):
        addr = Address(
            zip_code="1011", city="Budapest",
            street="Fő utca", house_number="5",
        )
        assert "em." not in addr.full_address
        assert "ajtó" not in addr.full_address


class TestParticipant:
    def test_is_borrower(self, sample_borrower):
        assert sample_borrower.is_borrower is True
        assert sample_borrower.is_co_borrower is False

    def test_role_values(self):
        assert ParticipantRole.BORROWER.value == "adós"
        assert ParticipantRole.CO_BORROWER.value == "adóstárs"
        assert ParticipantRole.GUARANTOR.value == "kezes"
        assert ParticipantRole.BENEFICIARY.value == "haszonélvező"


class TestDealData:
    def test_active_participants(self, sample_deal):
        assert len(sample_deal.active_participants) == 1

    def test_inactive_participant_filtered(self, sample_deal):
        inactive = Participant(
            role=ParticipantRole.CO_BORROWER,
            name="Törölt Személy",
            is_active=False,
        )
        sample_deal.participants.append(inactive)
        assert len(sample_deal.active_participants) == 1
        assert len(sample_deal.participants) == 2

    def test_borrowers(self, sample_deal):
        assert len(sample_deal.borrowers) == 1
        assert sample_deal.borrowers[0].name == "Nagy István"

    def test_co_borrowers_empty(self, sample_deal):
        assert len(sample_deal.co_borrowers) == 0

    def test_get_participants_by_role(self, sample_deal):
        borrowers = sample_deal.get_participants_by_role(ParticipantRole.BORROWER)
        assert len(borrowers) == 1
        guarantors = sample_deal.get_participants_by_role(ParticipantRole.GUARANTOR)
        assert len(guarantors) == 0


class TestLoanDetails:
    def test_required_fields(self):
        loan = LoanDetails(loan_amount=30_000_000, loan_term_months=240)
        assert loan.loan_amount == 30_000_000
        assert loan.loan_term_months == 240
        assert loan.interest_period is None

    def test_all_fields(self):
        loan = LoanDetails(
            loan_amount=50_000_000,
            loan_term_months=360,
            interest_period="végig fix",
            loan_purpose="Lakásvásárlás",
            product_name="OTP Zöld Lakáshitel",
            down_payment=15_000_000,
            monthly_payment=200_000,
        )
        assert loan.down_payment == 15_000_000


class TestProperty:
    def test_property_types(self):
        assert PropertyType.APARTMENT.value == "lakás"
        assert PropertyType.HOUSE.value == "ház"
        assert PropertyType.LAND.value == "telek"

    def test_property_creation(self, sample_property):
        assert sample_property.area_sqm == 65.0
        assert sample_property.property_type == PropertyType.APARTMENT


class TestSerialization:
    def test_deal_json_roundtrip(self, sample_deal):
        """JSON szerializáció és deszerialiáció tesztelése."""
        json_data = sample_deal.model_dump(mode="json")
        reconstructed = DealData.model_validate(json_data)
        assert reconstructed.deal_id == sample_deal.deal_id
        assert reconstructed.loan.loan_amount == sample_deal.loan.loan_amount
        assert len(reconstructed.participants) == len(sample_deal.participants)
        assert len(reconstructed.properties) == len(sample_deal.properties)

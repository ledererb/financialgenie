"""Production-mapping SZA ESZ role and option-group regression tests.

No synthetic mapping metadata or name-only assertions: these tests load the real
sza_esz_mapping.json and exercise direct preparation plus real master assembly
and instance rebinding with temporary local PDFs only.
"""
from pathlib import Path

import pytest

from src.ai.field_recognizer import MappingConfig
from src.canonical.resolver import ResolutionContext
from src.engine.document_assembler import DocumentAssembler, ProductType
from src.integrations.salesforce_client import SalesforceClient
from src.main import FormFillerPipeline
from src.models.canonical_model import ParticipantRole
from src.normalizer.data_normalizer import DataNormalizer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MASTER_PDF = PROJECT_ROOT / "documents/otp_bank/master/OTP_Igenylesi_Dokumentumok_v5.pdf"
MAILING_SAME = "same"
MAILING_DIFFERENT = "different"


@pytest.fixture
def production_mapping():
    return MappingConfig.load(PROJECT_ROOT / "src/mapping/sza_esz_mapping.json")


@pytest.fixture
def pipeline(tmp_path):
    return FormFillerPipeline(
        sf_client=SalesforceClient(
            mock_mode=True,
            mock_data_dir=PROJECT_ROOT / "samples/dummy_data",
        ),
        output_dir=tmp_path,
    )


def _participant(
    role: ParticipantRole,
    name: str,
    *,
    gender: str | None = "nő",
    citizenship: str | None = "magyar",
    country: str | None = "Magyarország",
    mailing_option: str | None = MAILING_SAME,
    include_permanent_address: bool = True,
) -> dict:
    """Production-shaped OpportunityContactRole + Contact raw record."""
    contact = {
        "Id": f"003-{role.value}-{name}",
        "Name": name,
        "Nem__c": gender,
        "Citizenship__c": citizenship,
        "OtherCountry": country,
    }
    if include_permanent_address:
        contact.update({
            "OtherPostalCode": "1123",
            "OtherCity": "Budapest",
            "OtherStreet": "Fő utca 1.",
        })
    if mailing_option == MAILING_SAME:
        contact.update({
            "MailingPostalCode": "1123",
            "MailingCity": "Budapest",
            "MailingStreet": "Fő utca 1.",
        })
    elif mailing_option == MAILING_DIFFERENT:
        contact.update({
            "MailingPostalCode": "2000",
            "MailingCity": "Szentendre",
            "MailingStreet": "Duna korzó 3.",
        })
    elif mailing_option == "partial":
        contact.update({
            "MailingCity": "Budapest",
            "MailingStreet": "Fő utca 1.",
        })
    return {"role": role.value, "contact_id": contact["Id"], "_contact": contact}


def _borrower() -> dict:
    return _participant(ParticipantRole.BORROWER, "Adós Teljes")


def _deal(*participants: dict):
    raw = {
        "Id": "SZA-ESZ-OPTIONS",
        "_opportunity": {"Hitel_sszeg__c": "10000000"},
        "Participants__r": {"records": [_borrower(), *participants]},
    }
    return DataNormalizer().normalize_deal(raw)


def _direct_data(pipeline, mapping, deal, role, index=1):
    return pipeline._prepare_field_data(
        deal,
        mapping,
        context=ResolutionContext(participant=(role, index)),
    )


def _master_data(pipeline, mapping, deal, tmp_path, collateral_participants):
    # Deliberately start with complete borrower values. Rebinding must replace
    # or clear every one of them on the first master copy as appropriate.
    initial = pipeline._prepare_field_data(deal, mapping)
    assembly = DocumentAssembler().assemble(
        MASTER_PDF,
        [ProductType.PIACI_HITEL],
        num_participants=1,
        num_properties=1,
        output_path=tmp_path / "assembled.pdf",
        collateral_participants=collateral_participants,
    )
    return pipeline._apply_instance_data(
        initial,
        deal,
        mapping,
        assembly.instance_fields,
    )


def _assert_options(
    data,
    *,
    suffix="",
    role,
    gender,
    citizenship,
    permanent_address,
    mailing_address,
):
    assert data[f"SZA_ESZ_szerep{suffix}"] == role
    assert data[f"SZA_ESZ_neme{suffix}"] == gender
    assert data[f"SZA_ESZ_állampolgárság{suffix}"] == citizenship
    assert data[f"SZA_ESZ_állandó_cím{suffix}"] == permanent_address
    assert data[f"SZA_ESZ_levelezés_cím{suffix}"] == mailing_address


def _assert_missing_options(data, *, suffix=""):
    for base in (
        "SZA_ESZ_neme",
        "SZA_ESZ_állampolgárság",
        "SZA_ESZ_állandó_cím",
        "SZA_ESZ_levelezés_cím",
        "SZA_ESZ_állandó_cím_isz",
        "SZA_ESZ_levelezés_cím_isz",
    ):
        assert f"{base}{suffix}" not in data


def test_direct_mortgagor_only_production_options(pipeline, production_mapping):
    data = _direct_data(
        pipeline,
        production_mapping,
        _deal(_participant(ParticipantRole.MORTGAGOR, "Zálog Egy")),
        "mortgagor",
    )

    assert data["SZA_ESZ_név"] == "Zálog Egy"
    _assert_options(
        data,
        role="Igen",
        gender="Igen",
        citizenship="Igen",
        permanent_address="Igen",
        mailing_address="Igen",
    )
    assert data["SZA_ESZ_szerep___Igen"] == "igen"
    assert "SZA_ESZ_szerep___no" not in data


def test_direct_usufructuary_only_production_options(pipeline, production_mapping):
    usufructuary = _participant(
        ParticipantRole.BENEFICIARY,
        "Haszon Egy",
        gender="férfi",
        citizenship="Egyéb",
        country="Németország",
        mailing_option=MAILING_DIFFERENT,
    )
    data = _direct_data(
        pipeline,
        production_mapping,
        _deal(usufructuary),
        "usufructuary",
    )

    assert data["SZA_ESZ_név"] == "Haszon Egy"
    _assert_options(
        data,
        role="no",
        gender="no",
        citizenship="no",
        permanent_address="no",
        mailing_address="no",
    )
    assert data["SZA_ESZ_szerep___no"] == "igen"
    assert "SZA_ESZ_szerep___Igen" not in data


def test_direct_both_role_types_follow_effective_context(pipeline, production_mapping):
    deal = _deal(
        _participant(ParticipantRole.MORTGAGOR, "Zálog Egy"),
        _participant(ParticipantRole.BENEFICIARY, "Haszon Egy"),
    )

    mortgagor = _direct_data(pipeline, production_mapping, deal, "mortgagor")
    usufructuary = _direct_data(pipeline, production_mapping, deal, "usufructuary")

    assert mortgagor["SZA_ESZ_szerep"] == "Igen"
    assert "SZA_ESZ_szerep___no" not in mortgagor
    assert usufructuary["SZA_ESZ_szerep"] == "no"
    assert "SZA_ESZ_szerep___Igen" not in usufructuary


def test_direct_missing_values_never_fall_back_to_borrower(
    pipeline, production_mapping
):
    missing = _participant(
        ParticipantRole.MORTGAGOR,
        "Hiányos Zálog",
        gender=None,
        citizenship=None,
        country=None,
        mailing_option=None,
        include_permanent_address=False,
    )
    data = _direct_data(
        pipeline,
        production_mapping,
        _deal(missing),
        "mortgagor",
    )

    assert data["SZA_ESZ_szerep"] == "Igen"
    assert data["SZA_ESZ_név"] == "Hiányos Zálog"
    _assert_missing_options(data)


def test_co_borrower_missing_country_and_partial_mailing_address_stay_blank(
    pipeline, production_mapping
):
    co_borrower = _participant(
        ParticipantRole.CO_BORROWER,
        "Hiányos Adóstárs",
        country=None,
        mailing_option="partial",
    )
    data = _direct_data(
        pipeline,
        production_mapping,
        _deal(co_borrower),
        "co_borrower",
    )

    assert "SZA_ESZ_állandó_cím" not in data
    assert "SZA_ESZ_levelezés_cím" not in data


@pytest.mark.parametrize(
    ("role", "canonical_role", "expected_export"),
    [
        (ParticipantRole.MORTGAGOR, "mortgagor", "Igen"),
        (ParticipantRole.BENEFICIARY, "usufructuary", "no"),
    ],
)
def test_master_single_role_rebinds_first_copy_and_virtual_options(
    pipeline,
    production_mapping,
    tmp_path,
    role,
    canonical_role,
    expected_export,
):
    participant = _participant(
        role,
        f"{role.value} Egy",
        gender="nő" if expected_export == "Igen" else "férfi",
        citizenship="magyar" if expected_export == "Igen" else "Egyéb",
        country="Magyarország" if expected_export == "Igen" else "Németország",
        mailing_option=MAILING_SAME if expected_export == "Igen" else MAILING_DIFFERENT,
    )
    data = _master_data(
        pipeline,
        production_mapping,
        _deal(participant),
        tmp_path,
        [(canonical_role, 1)],
    )

    assert data["SZA_ESZ_név"] == participant["_contact"]["Name"]
    _assert_options(
        data,
        role=expected_export,
        gender=expected_export,
        citizenship=expected_export,
        permanent_address=expected_export,
        mailing_address=expected_export,
    )
    selected_virtual = "___Igen" if expected_export == "Igen" else "___no"
    cleared_virtual = "___no" if expected_export == "Igen" else "___Igen"
    for base in (
        "SZA_ESZ_szerep",
        "SZA_ESZ_neme",
        "SZA_ESZ_állampolgárság",
        "SZA_ESZ_állandó_cím",
        "SZA_ESZ_levelezés_cím",
    ):
        assert data[f"{base}{selected_virtual}"] == "igen"
        assert f"{base}{cleared_virtual}" not in data


def test_master_both_roles_rebind_complete_option_groups_on_copies_1_and_2(
    pipeline, production_mapping, tmp_path
):
    deal = _deal(
        _participant(ParticipantRole.MORTGAGOR, "Zálog Egy"),
        _participant(
            ParticipantRole.BENEFICIARY,
            "Haszon Egy",
            gender="férfi",
            citizenship="Egyéb",
            country="Németország",
            mailing_option=MAILING_DIFFERENT,
        ),
    )
    data = _master_data(
        pipeline,
        production_mapping,
        deal,
        tmp_path,
        [("mortgagor", 1), ("usufructuary", 1)],
    )

    assert data["SZA_ESZ_név"] == "Zálog Egy"
    assert data["SZA_ESZ_név_2"] == "Haszon Egy"
    _assert_options(
        data,
        role="Igen",
        gender="Igen",
        citizenship="Igen",
        permanent_address="Igen",
        mailing_address="Igen",
    )
    _assert_options(
        data,
        suffix="_2",
        role="no",
        gender="no",
        citizenship="no",
        permanent_address="no",
        mailing_address="no",
    )
    assert data["SZA_ESZ_szerep___Igen"] == "igen"
    assert "SZA_ESZ_szerep___no" not in data


def test_master_multiple_same_role_participants_keep_independent_options(
    pipeline, production_mapping, tmp_path
):
    deal = _deal(
        _participant(ParticipantRole.MORTGAGOR, "Zálog Egy"),
        _participant(
            ParticipantRole.MORTGAGOR,
            "Zálog Kettő",
            gender="férfi",
            citizenship="Egyéb",
            country="Németország",
            mailing_option=MAILING_DIFFERENT,
        ),
    )
    data = _master_data(
        pipeline,
        production_mapping,
        deal,
        tmp_path,
        [("mortgagor", 1), ("mortgagor", 2)],
    )

    assert data["SZA_ESZ_név"] == "Zálog Egy"
    assert data["SZA_ESZ_név_2"] == "Zálog Kettő"
    _assert_options(
        data,
        role="Igen",
        gender="Igen",
        citizenship="Igen",
        permanent_address="Igen",
        mailing_address="Igen",
    )
    _assert_options(
        data,
        suffix="_2",
        role="Igen",
        gender="no",
        citizenship="no",
        permanent_address="no",
        mailing_address="no",
    )


def test_master_missing_values_clear_borrower_options_on_both_copies(
    pipeline, production_mapping, tmp_path
):
    deal = _deal(
        _participant(
            ParticipantRole.MORTGAGOR,
            "Hiányos Zálog",
            gender=None,
            citizenship=None,
            country=None,
            mailing_option=None,
        include_permanent_address=False,
        ),
        _participant(
            ParticipantRole.BENEFICIARY,
            "Hiányos Haszonélvező",
            gender=None,
            citizenship=None,
            country=None,
            mailing_option=None,
        include_permanent_address=False,
        ),
    )
    data = _master_data(
        pipeline,
        production_mapping,
        deal,
        tmp_path,
        [("mortgagor", 1), ("usufructuary", 1)],
    )

    assert data["SZA_ESZ_szerep"] == "Igen"
    assert data["SZA_ESZ_szerep_2"] == "no"
    assert data["SZA_ESZ_név"] == "Hiányos Zálog"
    assert data["SZA_ESZ_név_2"] == "Hiányos Haszonélvező"
    _assert_missing_options(data)
    _assert_missing_options(data, suffix="_2")
    for virtual in (
        "SZA_ESZ_neme___Igen",
        "SZA_ESZ_állampolgárság___Igen",
        "SZA_ESZ_állandó_cím___Igen",
        "SZA_ESZ_levelezés_cím___Igen",
    ):
        assert virtual not in data

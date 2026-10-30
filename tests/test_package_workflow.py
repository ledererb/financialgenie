"""Regression tests for package-manifest counts and SZA ESZ role routing.

All external boundaries are fake: no Salesforce calls, mapping recognition,
network access, or writes outside pytest temporary directories.
"""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Match the backend test import setup: backend/config.py must win over the
# project-level config package before server/main are imported.
_BACKEND = PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_spec = importlib.util.spec_from_file_location("config", _BACKEND / "config.py")
_backend_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_backend_config)
sys.modules["config"] = _backend_config

from src.canonical.resolver import CanonicalResolver, ResolutionContext
from src.main import _resolve_canonical_text
from src.models.canonical_model import (
    DealData,
    LoanDetails,
    Participant,
    ParticipantRole,
)


FEDLAP_COUNT_FIELDS = {
    "sza_ig": "Fedlap LAHI személyi igénylő",
    "ma_ig": "Fedlap LAHI Munkavállalói_adatlap",
    "ia": "Fedlap LAHI ingatlan adatlap",
    "sza_esz": "Fedlap LAHI személyi adatlap egyéb",
}


def _deal(*roles: ParticipantRole) -> DealData:
    participants = [
        Participant(role=ParticipantRole.BORROWER, name="Adós Anna")
    ]
    for index, role in enumerate(roles, start=1):
        participants.append(
            Participant(role=role, name=f"{role.value} Teszt {index}")
        )
    return DealData(
        deal_id="DEAL-PACKAGE-TEST",
        loan=LoanDetails(loan_amount=10_000_000),
        participants=participants,
    )


def _mapping_for(stem: str) -> dict:
    common = {
        "bank_name": "OTP Bank",
        "form_type": "acroform",
        "fields": [],
        "page_structure": {},
    }
    metadata = {
        "fedlap": {
            "form_name": "fedlap",
            "package_value_fields": FEDLAP_COUNT_FIELDS,
        },
        "sza_ig_igenylo": {
            "form_name": "sza_ig_igenylo",
            "participant_roles": ["borrower"],
            "package_count_key": "sza_ig",
        },
        "sza_ig_tarsigenylo": {
            "form_name": "sza_ig_tarsigenylo",
            "participant_roles": ["co_borrower"],
            "package_count_key": "sza_ig",
        },
        "munkaltato_adatlap": {
            "form_name": "munkaltato_adatlap",
            "package_count_key": "ma_ig",
        },
        "ingatlan_adatlap": {
            "form_name": "ingatlan_adatlap",
            "package_count_key": "ia",
        },
        "sza_esz": {
            "form_name": "sza_esz",
            "participant_roles": ["mortgagor", "usufructuary"],
            "package_count_key": "sza_esz",
        },
    }
    return {**common, **metadata.get(stem, {"form_name": stem})}


def _document(stem: str, *, per_applicant: bool = False) -> dict:
    return {
        "id": f"split_otp_bank_{stem}",
        "title": stem,
        "product_ids": ["prod-1"],
        "file_path": f"documents/otp_bank/split/{stem}.pdf",
        "per_applicant": per_applicant,
    }


def _run_package(
    tmp_path,
    monkeypatch,
    *,
    deal: DealData,
    documents: list[dict],
    fail_stems: set[str] | None = None,
):
    import main as main_module
    import server
    from catalog_service import catalog_service

    fail_stems = fail_stems or set()
    calls = []
    templates = {}
    for document in documents:
        stem = Path(document["file_path"]).stem
        template = tmp_path / f"{stem}.pdf"
        template.write_bytes(b"%PDF-1.4 fake template")
        templates[stem] = template
    filled = tmp_path / "filled.pdf"
    filled.write_bytes(b"%PDF-1.4 filled")

    class FakeSF:
        _mock_mode = True

        def get_deal(self, deal_id):
            return {"Id": deal_id}

    class FakePipeline:
        def __init__(self, **kwargs):
            self.normalizer = SimpleNamespace(normalize_deal=lambda raw: deal)

        def run_for_deal(self, **kwargs):
            stem = Path(kwargs["template_pdf"]).stem
            calls.append(kwargs)
            if stem in fail_stems:
                return {
                    "success": False,
                    "issues": ["szándékos teszthiba"],
                    "notices": [],
                }
            return {
                "success": True,
                "output_path": str(filled),
                "filled_fields": [],
                "skipped_fields": [],
                "notices": [],
                "issues": [],
            }

    monkeypatch.setattr(main_module, "FormFillerPipeline", FakePipeline)
    monkeypatch.setattr(server, "_make_sf_client", lambda: FakeSF())
    monkeypatch.setattr(
        server,
        "_get_pdf",
        lambda file_path: templates[Path(file_path).stem],
    )
    monkeypatch.setattr(
        server.mapping_service,
        "load",
        lambda pdf_id: _mapping_for(Path(pdf_id).stem),
    )
    monkeypatch.setattr(
        catalog_service,
        "load_catalog",
        lambda: {"documents": documents},
    )
    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)

    response = server.generate_package(
        {"bank_id": "otp_bank", "product_id": "prod-1", "deal_id": deal.deal_id}
    )
    return response, calls


def test_no_collateral_role_omits_sza_esz_and_writes_zero_cover_count(
    tmp_path, monkeypatch
):
    response, calls = _run_package(
        tmp_path,
        monkeypatch,
        deal=_deal(ParticipantRole.CO_BORROWER),
        # Deliberately put fedlap first: generation must reorder it last.
        documents=[_document("fedlap"), _document("sza_esz", per_applicant=True)],
    )

    assert [Path(call["template_pdf"]).stem for call in calls] == ["fedlap"]
    assert [item["title"] for item in response["documents"]] == ["fedlap"]
    assert calls[0]["package_values"][FEDLAP_COUNT_FIELDS["sza_esz"]] == "0"


@pytest.mark.parametrize(
    ("role", "canonical_role"),
    [
        (ParticipantRole.MORTGAGOR, "mortgagor"),
        (ParticipantRole.BENEFICIARY, "usufructuary"),
    ],
)
def test_sza_esz_is_generated_for_the_actual_collateral_participant(
    tmp_path, monkeypatch, role, canonical_role
):
    deal = _deal(ParticipantRole.CO_BORROWER, role)
    response, calls = _run_package(
        tmp_path,
        monkeypatch,
        deal=deal,
        documents=[_document("sza_esz", per_applicant=True)],
    )

    assert response["total_documents"] == 1
    assert len(calls) == 1
    context = calls[0]["context"]
    assert isinstance(context, ResolutionContext)
    assert context.participant == (canonical_role, 1)
    expected_name = deal.get_participants_by_role(role)[0].name
    assert _resolve_canonical_text(
        CanonicalResolver(),
        deal,
        "participant.borrower.name",  # actual SZA ESZ mapping path
        context,
    ) == expected_name
    assert response["documents"][0]["success"] is True


def test_fedlap_counts_successful_generated_manifest_not_placeholders(
    tmp_path, monkeypatch
):
    response, calls = _run_package(
        tmp_path,
        monkeypatch,
        deal=_deal(ParticipantRole.CO_BORROWER, ParticipantRole.MORTGAGOR),
        documents=[
            _document("fedlap"),
            _document("sza_ig_igenylo"),
            _document("sza_ig_tarsigenylo", per_applicant=True),
            _document("munkaltato_adatlap"),
            _document("ingatlan_adatlap"),
            _document("sza_esz", per_applicant=True),
        ],
        fail_stems={"munkaltato_adatlap"},
    )

    assert Path(calls[-1]["template_pdf"]).stem == "fedlap"
    assert calls[-1]["package_values"] == {
        FEDLAP_COUNT_FIELDS["sza_ig"]: "2",
        FEDLAP_COUNT_FIELDS["ma_ig"]: "0",
        FEDLAP_COUNT_FIELDS["ia"]: "1",
        FEDLAP_COUNT_FIELDS["sza_esz"]: "1",
    }
    assert response["total_documents"] == 5
    assert any(
        item["title"] == "munkaltato_adatlap" and not item["success"]
        for item in response["documents"]
    )

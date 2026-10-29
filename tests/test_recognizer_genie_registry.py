"""Cutover Task 4 — the recognizer speaks the shared Genie registry only.

Requirement 1: recognition prompts/heuristics/postprocess and auto-created
mappings must emit ONLY registry-valid Genie paths — no ``Contact.``/
``Lead.``/``Opportunity.``/``Account.``/``loan.`` names, no ``-társ``
suffixes, no invented namespaces.

These tests import only ``src.*`` modules (same isolation pattern as
tests/test_fixes_src.py).
"""
from pathlib import Path

import pytest

from src.ai import field_recognizer as fr
from src.ai.field_recognizer import (
    FieldRecognizer,
    FieldType,
    MappingConfidence,
    RecognizedField,
    _postprocess_recognized_fields,
    _stem_to_canonical,
)
from src.canonical.registry import canonical_registry
from src.canonical.paths import parse_path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_PREFIXES = ("Contact.", "Lead.", "Opportunity.", "Account.", "loan.")


def _mapped_canonicals(fields):
    return [f.canonical_field for f in fields if f.canonical_field]


# ── shared registry template access ─────────────────────────────────────────

class TestRegistryTemplateAccess:
    def test_registry_exposes_template_definitions(self):
        definitions = canonical_registry.definitions()
        assert isinstance(definitions, list)
        assert definitions, "registry template list must not be empty"
        for definition in definitions:
            assert set(definition) == {
                "path", "label", "type", "source", "repeat",
            }
            assert canonical_registry.get(
                _representative_path(definition)
            ), definition["path"]

    def test_template_definitions_cover_every_repeat_mode(self):
        repeats = {d["repeat"] for d in canonical_registry.definitions()}
        assert repeats == {"none", "participant", "property", "income", "existing_loan"}


def _representative_path(definition):
    repeat = definition["repeat"]
    if repeat == "participant":
        return definition["path"].replace("{role}", "borrower")
    if repeat in {"income", "existing_loan"}:
        field = definition["path"].rsplit(".", 1)[1]
        return f"participant.borrower.{repeat}[1].{field}"
    return definition["path"]


# ── recognizer catalog derives from the registry ────────────────────────────

class TestRecognizerCatalogIsRegistryBacked:
    def test_every_catalog_key_is_a_registry_valid_genie_path(self):
        assert fr.CANONICAL_FIELDS, "catalog must not be empty"
        invalid = [
            path for path in fr.CANONICAL_FIELDS
            if not canonical_registry.validate(path)
        ]
        assert invalid == []

    def test_catalog_has_no_legacy_namespaces(self):
        legacy = [
            path for path in fr.CANONICAL_FIELDS
            if path.startswith(LEGACY_PREFIXES) or "-társ" in path or "#" in path
        ]
        assert legacy == []

    def test_catalog_expands_every_participant_role(self):
        for role in ("borrower", "co_borrower", "guarantor", "mortgagor", "usufructuary"):
            assert f"participant.{role}.name" in fr.CANONICAL_FIELDS

    def test_catalog_includes_deal_property_account_and_related_representatives(self):
        for path in (
            "deal.loan_amount",
            "account.name",
            "property.parcel_number",
            "participant.borrower.income[1].amount",
            "participant.co_borrower.existing_loan[1].monthly_payment",
        ):
            assert path in fr.CANONICAL_FIELDS, path

    def test_catalog_labels_are_non_empty(self):
        empty = [
            path for path, label in fr.CANONICAL_FIELDS.items()
            if not str(label).strip()
        ]
        assert empty == []


# ── heuristics emit Genie paths ─────────────────────────────────────────────

class TestHeuristicsEmitGeniePaths:
    def _heuristic(self, names):
        recognizer = FieldRecognizer(api_key="")
        pdf_fields = [{"name": n, "type": "/Tx", "page": 1} for n in names]
        return recognizer._heuristic_map_fields(pdf_fields, Path("x.pdf"), "acroform")

    def test_all_heuristic_canonicals_are_registry_valid(self):
        mapping = self._heuristic([
            "SZA_IG_név", "SZA_IG_szül_név", "SZA_IG_anyja_neve",
            "SZA_IG_szül_hely", "SZA_IG_szül_év", "SZA_IG_szül_hónap",
            "SZA_IG_szül_nap", "SZA_IG_adóazonosító", "SZA_IG_mobil",
            "SZA_IG_email_cím", "SZA_IG_név-társ", "SZA_IG_adóazonosító-társ",
            "IA_cím_isz", "IA_helyrazi_szam", "IA_hasznos_terület",
            "LAHI_KTKA_Igényelt_kölcsön", "LAHI_KTKA_Önerő_kp",
            "LAHI_KTKA_Lakástakarék", "LAHI_KTKA_Kölcsön_tipusa_OTP",
            "MA_IG_adós_neve", "MA_IG_vállalkozás_adószám",
            "hitelosszeg", "iranyitoszam", "helyrajzi_szam", "futamido",
        ])
        mapped = _mapped_canonicals(mapping.fields)
        assert mapped, "heuristic should map a representative set"
        invalid = [c for c in mapped if not canonical_registry.validate(c)]
        assert invalid == []

    def test_borrower_fields_map_to_borrower_scoped_paths(self):
        mapping = self._heuristic([
            "SZA_IG_név", "SZA_IG_szül_név", "SZA_IG_adóazonosító",
            "SZA_IG_szül_év",
        ])
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["SZA_IG_név"].canonical_field == "participant.borrower.name"
        assert by_name["SZA_IG_szül_név"].canonical_field == "participant.borrower.birth_name"
        assert by_name["SZA_IG_adóazonosító"].canonical_field == "participant.borrower.tax_id"
        # The split birth-date boxes share one canonical + fragment roles.
        assert by_name["SZA_IG_szül_év"].canonical_field == "participant.borrower.birth_date"
        assert by_name["SZA_IG_szül_év"].fragment == "year"

    def test_co_borrower_pdf_names_map_to_co_borrower_paths(self):
        mapping = self._heuristic([
            "SZA_IG_név-társ", "SZA_IG_adóazonosító-társ",
            "SZA_IG_szül_év-társ",
        ])
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["SZA_IG_név-társ"].canonical_field == "participant.co_borrower.name"
        assert by_name["SZA_IG_adóazonosító-társ"].canonical_field == "participant.co_borrower.tax_id"
        assert by_name["SZA_IG_szül_év-társ"].canonical_field == "participant.co_borrower.birth_date"
        assert by_name["SZA_IG_szül_év-társ"].fragment == "year"

    def test_property_and_deal_scoped_keywords_map_to_genie_namespaces(self):
        mapping = self._heuristic([
            "IA_cím_isz", "IA_helyrazi_szam", "LAHI_KTKA_Igényelt_kölcsön",
            "LAHI_KTKA_Lakástakarék", "LAHI_KTKA_Kölcsön_tipusa_OTP",
        ])
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["IA_cím_isz"].canonical_field == "property.address.zip_code"
        assert by_name["IA_helyrazi_szam"].canonical_field == "property.parcel_number"
        assert by_name["LAHI_KTKA_Igényelt_kölcsön"].canonical_field == "deal.loan_amount"
        assert by_name["LAHI_KTKA_Lakástakarék"].canonical_field == "deal.housing_savings"
        assert by_name["LAHI_KTKA_Kölcsön_tipusa_OTP"].canonical_field == "deal.product_type"

    def test_keyword_map_still_matches_after_prefix_and_suffix_stripping(self):
        mapping = self._heuristic(["hitelosszeg", "iranyitoszam", "futamido"])
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["hitelosszeg"].canonical_field == "deal.loan_amount"
        assert by_name["iranyitoszam"].canonical_field == "participant.borrower.address.zip_code"
        assert by_name["futamido"].canonical_field == "deal.loan_term_years"


# ── real-PDF heuristic postcondition ────────────────────────────────────────

class TestHeuristicOutputOnTestPdf:
    def test_full_heuristic_output_is_registry_valid(self, heuristic_recognizer, test_pdf):
        mapping = heuristic_recognizer.recognize(test_pdf, mode="acroform")
        mapped = _mapped_canonicals(mapping.fields)
        assert mapped, "heuristic maps fields on the OTP test PDF"
        invalid = [c for c in mapped if not canonical_registry.validate(c)]
        assert invalid == []

    def test_char_stem_rules_resolve_to_registry_paths_only(self):
        for keyword, canonical in fr._CHAR_STEM_CANONICAL_RULES:
            assert canonical_registry.validate(canonical), (keyword, canonical)

    def test_stem_to_canonical_returns_registry_path_or_none(self):
        assert canonical_registry.validate(_stem_to_canonical("adóazonosító jel"))
        assert _stem_to_canonical("teljesen ismeretlen felirat") is None


# ── postprocess ─────────────────────────────────────────────────────────────

class TestPostprocessEmitsRegistryPathsOnly:
    def test_hrsz_field_maps_to_property_parcel_number(self):
        fields = [
            RecognizedField("hrsz_1", "Helyrajzi szám", FieldType.TEXT,
                            None, MappingConfidence.LOW, 1),
        ]
        _postprocess_recognized_fields(fields)
        assert fields[0].canonical_field == "property.parcel_number"
        assert canonical_registry.validate(fields[0].canonical_field)

    def test_date_component_fragment_inference_survives_genie_paths(self):
        fields = [
            RecognizedField("szerzodes_ev", "Szerződés éve", FieldType.DATE,
                            "deal.contract_signed_date", MappingConfidence.MEDIUM, 1),
        ]
        _postprocess_recognized_fields(fields)
        assert fields[0].fragment == "year"

    def test_char_group_member_backfill_uses_group_canonical(self):
        fields = [
            RecognizedField("tax_d1", "Adóazonosító (1. jegy)", FieldType.TEXT,
                            None, MappingConfidence.LOW, 1),
        ]
        groups = [{
            "group_id": "g1",
            "canonical_field": "participant.borrower.tax_id",
            "member_fields": ["tax_d1"],
        }]
        _postprocess_recognized_fields(fields, groups)
        assert fields[0].canonical_field == "participant.borrower.tax_id"

    def test_postprocess_output_never_contains_legacy_names(self):
        fields = [
            RecognizedField("hrsz_2", "hrsZ", FieldType.TEXT,
                            None, MappingConfidence.LOW, 1),
            RecognizedField("datum_nap", "Kelt nap", FieldType.DATE,
                            "deal.contract_signed_date", MappingConfidence.MEDIUM, 1),
        ]
        _postprocess_recognized_fields(fields)
        for canonical in _mapped_canonicals(fields):
            assert canonical_registry.validate(canonical), canonical
            assert not canonical.startswith(LEGACY_PREFIXES)


# ── AI-response sanitizing / fuzzy matching ─────────────────────────────────

class TestAIReturnsAreSanitizedToRegistryPaths:
    def test_sanitize_keeps_registry_paths_and_drops_everything_else(self):
        assert fr._sanitize_canonical("deal.loan_amount") == "deal.loan_amount"
        assert fr._sanitize_canonical("participant.co_borrower.name") == "participant.co_borrower.name"
        assert fr._sanitize_canonical("Contact.Name") is None
        assert fr._sanitize_canonical("loan.housing_savings") is None
        assert fr._sanitize_canonical("deal.loan_amount#piaci") is None
        assert fr._sanitize_canonical("Contact.Name-társ") is None
        assert fr._sanitize_canonical("") is None
        assert fr._sanitize_canonical(None) is None

    def test_fuzzy_match_snaps_case_variants_to_catalog_keys(self):
        valid = {"participant.borrower.name", "deal.loan_amount"}
        assert FieldRecognizer._fuzzy_match_canonical(
            "Participant.Borrower.Name", valid
        ) == "participant.borrower.name"
        assert FieldRecognizer._fuzzy_match_canonical(
            "DEAL.LOAN_AMOUNT", valid
        ) == "deal.loan_amount"

    def test_fuzzy_match_no_longer_accepts_invented_prefixes(self):
        valid = set(fr.CANONICAL_FIELDS.keys())
        # Legacy recognizer-only audit names are NOT registry fields.
        assert FieldRecognizer._fuzzy_match_canonical("participant.business_name", valid) is None
        assert FieldRecognizer._fuzzy_match_canonical("csok.amount", valid) is None
        assert FieldRecognizer._fuzzy_match_canonical("document.signature_date", valid) is None
        assert FieldRecognizer._fuzzy_match_canonical("declaration.consent", valid) is None

    def test_parse_ai_response_drops_non_registry_canonicals(self):
        recognizer = FieldRecognizer(api_key="")
        ai_result = {
            "form_name": "T",
            "fields": [
                {"pdf_field_name": "nev", "label": "Név",
                 "field_type": "text", "canonical_field": "Contact.Name",
                 "confidence": "high", "page_number": 1},
                {"pdf_field_name": "osszeg", "label": "Hitelösszeg",
                 "field_type": "number", "canonical_field": "deal.loan_amount",
                 "confidence": "high", "page_number": 1},
            ],
        }
        mapping = recognizer._parse_ai_response(ai_result, Path("t.pdf"), "acroform")
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["nev"].canonical_field is None
        assert by_name["osszeg"].canonical_field == "deal.loan_amount"

    def test_parse_ai_response_drops_non_registry_fill_rule_sf_fields(self):
        """A fill_rule-be ágyazott canonical hivatkozás (sf_field) sem lehet
        legacy — az auto-létrehozott mapping mentése (mapping_service.save)
        egyébként 422-t adna."""
        recognizer = FieldRecognizer(api_key="")
        ai_result = {
            "form_name": "T",
            "fields": [
                {
                    "pdf_field_name": "cel",
                    "label": "Hitelcél",
                    "field_type": "text",
                    "canonical_field": "deal.loan_purpose",
                    "confidence": "high",
                    "page_number": 1,
                    "fill_rule": {
                        "type": "conditional",
                        "sf_field": "Contact.Loan_Purpose__c",
                        "value": "x",
                    },
                },
                {
                    "pdf_field_name": "cel2",
                    "label": "Hitelcél 2",
                    "field_type": "text",
                    "canonical_field": "deal.loan_purpose",
                    "confidence": "high",
                    "page_number": 1,
                    "fill_rule": {
                        "type": "conditional",
                        "sf_field": "deal.product_type",
                        "value": "y",
                    },
                },
            ],
        }
        mapping = recognizer._parse_ai_response(ai_result, Path("t.pdf"), "acroform")
        by_name = {f.pdf_field_name: f for f in mapping.fields}
        assert by_name["cel"].fill_rule["sf_field"] is None
        assert by_name["cel2"].fill_rule["sf_field"] == "deal.product_type"

    def test_ai_flat_label_map_drops_non_registry_values(self):
        recognizer = FieldRecognizer(api_key="")
        raw = {
            "flat_1_1": "participant.borrower.name",
            "flat_1_2": "Contact.Name",
            "flat_1_3": "deal.loan_amount",
        }
        sanitized = recognizer._sanitize_label_map(raw)
        assert sanitized == {
            "flat_1_1": "participant.borrower.name",
            "flat_1_3": "deal.loan_amount",
        }


# ── prompts advertise the Genie vocabulary only ─────────────────────────────

class TestPromptsAdvertiseGenieVocabulary:
    def test_system_prompt_has_no_legacy_examples(self):
        prompt = FieldRecognizer.SYSTEM_PROMPT
        assert "Contact.Name" not in prompt
        assert "Lead.Ingatlan" not in prompt
        assert "-társ" not in prompt
        assert "{canonical_fields}" in prompt

    def test_batch_prompt_builder_uses_registry_catalog(self):
        canonical_desc = fr._canonical_catalog_description()
        assert "deal.loan_amount" in canonical_desc
        assert "participant.borrower.name" in canonical_desc
        assert "Contact." not in canonical_desc
        assert "Lead." not in canonical_desc


# ── auto-created mappings are accepted by the activated pipeline ────────────

class TestAutoCreatedMappingPassesPipelineGate:
    def test_heuristic_mapping_is_not_rejected_as_legacy(self):
        from src.main import _mapping_has_legacy_canonical, _mapping_uses_canonical_paths

        recognizer = FieldRecognizer(api_key="")
        pdf_fields = [
            {"name": n, "type": "/Tx", "page": 1}
            for n in (
                "SZA_IG_név", "SZA_IG_név-társ", "IA_helyrazi_szam",
                "LAHI_KTKA_Igényelt_kölcsön",
            )
        ]
        mapping = recognizer._heuristic_map_fields(
            pdf_fields, Path("x.pdf"), "acroform"
        )
        assert _mapping_uses_canonical_paths(mapping) is True
        assert _mapping_has_legacy_canonical(mapping) is False


class TestRoleListComesFromTheRegistry:
    """Task 4 review fix 3 — a szereplő-lista (borrower, co_borrower, …) egyetlen
    forrásból, a canonical registry-ből érkezik; a recognizer nem másolja."""

    def test_registry_exposes_the_canonical_role_list(self):
        roles = canonical_registry.roles()
        assert roles == (
            "borrower", "co_borrower", "guarantor", "mortgagor", "usufructuary",
        )

    def test_recognizer_uses_the_registry_tuple_verbatim(self):
        import src.ai.field_recognizer as fr

        assert fr._PARTICIPANT_ROLES is canonical_registry.roles()

    def test_paths_role_grammar_and_registry_list_agree(self):
        from src.canonical import paths as canonical_paths

        assert canonical_paths.PARTICIPANT_ROLES == canonical_registry.roles()
        # The parser grammar accepts exactly these roles (and nothing else).
        for role in canonical_registry.roles():
            parsed = parse_path(f"participant.{role}.name")
            assert parsed.role == role
        with pytest.raises(ValueError):
            parse_path("participant.not_a_role.name")

    def test_catalog_role_coverage_matches_registry_roles(self):
        for role in canonical_registry.roles():
            assert f"participant.{role}.name" in fr.CANONICAL_FIELDS


class TestCanonicalCatalogCaching:
    """Task 4 review fix 4 — a registry-expansion immutable: az expandált
    katalógus nem épül újra kérés/használatonként."""

    def test_registry_concrete_expansion_is_cached(self):
        assert (
            canonical_registry.canonical_fields()
            is canonical_registry.canonical_fields()
        )

    def test_registry_definitions_are_cached(self):
        assert canonical_registry.definitions() is canonical_registry.definitions()

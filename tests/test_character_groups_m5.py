"""Tests for FIX M5 — character-split groups (digit boxes / comb text)."""
from src.engine.pdf_filler import expand_character_groups
from src.ai.field_recognizer import MappingConfig


def _group(canonical, members, separator=""):
    return {
        "group_id": "g1",
        "group_type": "character_split",
        "field_type": "character_split",
        "canonical_field": canonical,
        "member_fields": members,
        "direction": "left_to_right",
        "separator": separator,
    }


class TestExpandCharacterGroups:
    def test_postal_code_split(self):
        src = {"Contact.ZIP__c": "1123"}
        out = expand_character_groups(src, [_group("Contact.ZIP__c", ["zip1", "zip2", "zip3", "zip4"])])
        assert out == {"zip1": "1", "zip2": "1", "zip3": "2", "zip4": "3"}

    def test_missing_value_skips_group(self):
        # No canonical value → all boxes stay empty (group skipped entirely).
        out = expand_character_groups({}, [_group("Contact.ZIP__c", ["z1", "z2"])])
        assert out == {}

    def test_short_value_pads_empty(self):
        out = expand_character_groups(
            {"Contact.ZIP__c": "12"},
            [_group("Contact.ZIP__c", ["z1", "z2", "z3", "z4"])],
        )
        assert out == {"z1": "1", "z2": "2", "z3": "", "z4": ""}

    def test_long_value_truncated_to_boxes(self):
        out = expand_character_groups(
            {"Contact.ZIP__c": "1123AB"},
            [_group("Contact.ZIP__c", ["z1", "z2"])],
        )
        assert out == {"z1": "1", "z2": "1"}

    def test_separator_stripped_before_split(self):
        # Separator chars are removed before the per-box split.
        out = expand_character_groups(
            {"x": "1-2-3"},
            [_group("x", ["a", "b", "c"], separator="-")],
        )
        assert out == {"a": "1", "b": "2", "c": "3"}

    def test_empty_groups_returns_empty(self):
        assert expand_character_groups({"x": "1"}, []) == {}

    def test_group_without_members_skipped(self):
        out = expand_character_groups(
            {"x": "1"},
            [{"canonical_field": "x", "member_fields": []}],
        )
        assert out == {}


class TestMappingConfigRoundTrip:
    def test_character_groups_survive_to_dict_from_dict(self):
        mc = MappingConfig(
            bank_name="OTP",
            form_name="demo",
            form_type="acroform",
            character_groups=[_group("Contact.ZIP__c", ["z1", "z2"])],
        )
        data = mc.to_dict()
        assert data["character_groups"][0]["member_fields"] == ["z1", "z2"]

        rebuilt = MappingConfig.from_dict(data)
        assert rebuilt.character_groups == mc.character_groups

    def test_old_mapping_without_character_groups_loads_empty(self):
        data = {"bank_name": "OTP", "form_name": "d", "form_type": "acroform", "fields": []}
        mc = MappingConfig.from_dict(data)
        assert mc.character_groups == []


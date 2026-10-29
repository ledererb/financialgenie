"""Offline Salesforce describe snapshots, capabilities, and field-map generation."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.build_salesforce_field_map import (
    FIELD_MAP_PATH,
    build_field_map,
    formatted_field_map,
    write_field_map,
)
from scripts.sync_sf_schema import default_snapshot_path, main as sync_main, sync_schema
from src.canonical.registry import (
    _SOURCE_RE,
    _source_has_valid_structure,
    canonical_registry,
)
from src.integrations.salesforce_schema import SalesforceSchema, load_field_map


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "sf_schema.json"
OBJECT_NAMES = {
    "Contact",
    "Opportunity",
    "Account",
    "Property__c",
    "Jovedelem__c",
    "Meglevo_hitel__c",
}
FIXED_TIME = datetime(2026, 10, 29, 1, 2, 3, tzinfo=timezone.utc)


@pytest.fixture
def snapshot_data():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def schema(snapshot_data):
    return SalesforceSchema.from_dict(snapshot_data)


def assert_model_source(source):
    assert _SOURCE_RE.fullmatch(source), source
    assert _source_has_valid_structure(source), source


def test_schema_loads_exact_fixture_objects(schema):
    assert schema.org_id == "00D000000000001AAA"
    assert schema.generated_at == "2026-10-29T01:02:03Z"
    assert set(schema.to_dict()["objects"]) == OBJECT_NAMES
    assert schema.has_object("Contact")
    assert not schema.has_object("Lead")


def test_available_fields_preserves_candidate_order_and_duplicates(schema):
    candidates = ["Email", "Missing__c", "Name", "AccountId", "Email"]

    assert schema.available_fields("Contact", candidates) == [
        "Email",
        "Name",
        "AccountId",
        "Email",
    ]
    assert schema.available_fields("Contact", []) == []
    assert schema.available_fields("Unknown__c", ["Name"]) == []


def test_unknown_object_and_field_behavior_is_explicit(schema):
    assert not schema.has_object("Unknown__c")
    assert not schema.has_field("Contact", "Unknown__c")
    assert not schema.has_field("Unknown__c", "Name")
    assert schema.has_field("Contact", "Name")
    assert schema.missing_objects(["Contact", "Unknown__c", "Lead"]) == [
        "Unknown__c",
        "Lead",
    ]


def test_missing_related_object_is_explicit(schema, snapshot_data):
    without_income = deepcopy(snapshot_data)
    del without_income["objects"]["Jovedelem__c"]
    incomplete = SalesforceSchema.from_dict(without_income)

    assert not incomplete.has_object("Jovedelem__c")
    assert incomplete.missing_objects(["Jovedelem__c"]) == ["Jovedelem__c"]
    assert incomplete.available_fields("Jovedelem__c", ["Osszeg__c"]) == []


def test_lookup_and_picklist_metadata_round_trip(schema, snapshot_data):
    document = schema.to_dict()
    account_lookup = document["objects"]["Contact"]["fields"]["AccountId"]
    marital_status = document["objects"]["Contact"]["fields"]["Marital_Status__c"]

    assert account_lookup["type"] == "reference"
    assert account_lookup["referenceTo"] == ["Account"]
    assert marital_status["picklistValues"] == [
        {
            "value": "single",
            "label": "Egyedülálló",
            "active": True,
            "defaultValue": False,
        },
        {
            "value": "married",
            "label": "Házas",
            "active": True,
            "defaultValue": False,
        },
    ]
    assert document == snapshot_data


def test_snapshot_contains_no_record_payload(schema):
    encoded = json.dumps(schema.to_dict(), ensure_ascii=False)

    assert '"records"' not in encoded
    assert '"totalSize"' not in encoded
    assert '"attributes"' not in encoded
    assert '"query"' not in encoded


def test_from_dict_does_not_mutate_or_alias_input(snapshot_data):
    original = deepcopy(snapshot_data)
    parsed = SalesforceSchema.from_dict(snapshot_data)
    parsed.to_dict()["objects"]["Contact"]["fields"].clear()

    assert snapshot_data == original
    assert parsed.has_field("Contact", "Name")


@pytest.mark.parametrize("key", ["org_id", "generated_at", "objects"])
def test_snapshot_requires_top_level_sections(snapshot_data, key):
    del snapshot_data[key]

    with pytest.raises(ValueError):
        SalesforceSchema.from_dict(snapshot_data)


@pytest.mark.parametrize("generated_at", ["2026-10-29T01:02:03", "not-a-date", ""])
def test_snapshot_requires_timezone_aware_iso_timestamp(snapshot_data, generated_at):
    snapshot_data["generated_at"] = generated_at

    with pytest.raises(ValueError):
        SalesforceSchema.from_dict(snapshot_data)


@pytest.mark.parametrize(
    "location",
    ["top", "object", "field"],
)
def test_snapshot_rejects_record_payload_at_every_level(snapshot_data, location):
    if location == "top":
        snapshot_data["records"] = [{"Id": "customer"}]
    elif location == "object":
        snapshot_data["objects"]["Contact"]["records"] = [{"Id": "customer"}]
    else:
        snapshot_data["objects"]["Contact"]["fields"]["Name"]["records"] = [
            {"value": "customer"}
        ]

    with pytest.raises(ValueError):
        SalesforceSchema.from_dict(snapshot_data)


def test_snapshot_rejects_malformed_field_metadata(snapshot_data):
    snapshot_data["objects"]["Contact"]["fields"]["AccountId"]["referenceTo"] = "Account"

    with pytest.raises(ValueError):
        SalesforceSchema.from_dict(snapshot_data)


def test_snapshot_rejects_unknown_metadata_keys(snapshot_data):
    snapshot_data["objects"]["Contact"]["fields"]["Name"]["customerValue"] = "secret"

    with pytest.raises(ValueError):
        SalesforceSchema.from_dict(snapshot_data)


def test_field_map_targets_valid_model_sources():
    field_map = load_field_map()

    for api_name, rule in field_map["Contact"].items():
        assert api_name
        assert rule["target"].startswith(("participant.", "account."))
        assert_model_source(rule["target"])

    for object_name, rules in field_map.items():
        assert object_name in OBJECT_NAMES
        for api_name, rule in rules.items():
            assert api_name
            assert_model_source(rule["target"])
            if "path" in rule:
                assert canonical_registry.validate(rule["path"]), rule["path"]
            if rule["target"] in {
                "participant.extra",
                "participant.consents",
                "deal.extra",
                "property.extra",
            }:
                assert rule["key"]


def test_field_map_covers_account_deal_participant_and_property_concepts():
    field_map = load_field_map()

    assert field_map["Account"]["Name"]["target"] == "account.name"
    assert field_map["Account"]["BillingStreet"]["target"] == "account.billing_street"
    assert field_map["Account"]["AccountNumber"]["target"] == "account.account_number"
    assert field_map["Opportunity"]["Discount_Action__c"]["target"] == (
        "loan.discount_action"
    )
    assert field_map["Opportunity"]["Refinance_Account__c"]["target"] == (
        "loan.refinance_account"
    )
    assert field_map["Opportunity"]["Monthly_Payment__c"]["target"] == (
        "loan.monthly_payment"
    )
    assert field_map["Contact"]["Id"]["target"] == "participant.contact_id"
    assert field_map["Contact"]["Nem__c"]["target"] == "participant.gender"
    assert field_map["Contact"]["Personal_Number__c"]["target"] == (
        "participant.personal_number"
    )
    assert field_map["Contact"]["ID_Document_Type__c"]["target"] == (
        "participant.id_document_type"
    )
    assert field_map["Contact"]["Household_Size__c"]["target"] == (
        "participant.household_size"
    )
    assert field_map["Contact"]["Children_Count__c"]["target"] == (
        "participant.children_count"
    )
    assert field_map["Contact"]["Employer_Tax_Number__c"]["target"] == (
        "participant.employer_tax_number"
    )
    assert field_map["Contact"]["OwnerId"]["target"] == "participant.extra"
    assert field_map["Contact"]["OwnerId"]["key"] == "owner_id"


def test_field_map_has_explicit_consents_without_legal_inference():
    contact = load_field_map()["Contact"]

    assert contact["KHR_Consent__c"] == {
        "target": "participant.consents",
        "key": "khr",
        "path": "participant.borrower.khr_consent",
    }
    assert contact["NAV_Consent__c"]["key"] == "nav"
    assert contact["AML_Consent__c"]["key"] == "aml"
    assert contact["Data_Processing_Consent__c"]["key"] == "data_processing"


def test_field_map_keeps_property_details_and_parcel_comments_distinct():
    properties = load_field_map()["Property__c"]

    assert properties["Id"]["target"] == "property.property_id"
    assert properties["Ingatlan_hrsz__c"]["target"] == "property.parcel_number"
    assert properties["Ingatlan_megjegyzes__c"]["target"] == "property.extra"
    assert properties["Ingatlan_megjegyzes__c"]["key"] == "ingatlan_megjegyzes"
    assert properties["Ingatlan_allapot__c"]["target"] == "property.condition"
    assert properties["Ingatlan_futes__c"]["target"] == "property.heating"
    assert properties["Ingatlan_epites_eve__c"]["target"] == "property.year_built"
    assert properties["Ingatlan_lift__c"]["target"] == "property.lift"
    assert properties["Ingatlan_biztositas__c"]["target"] == "property.insurance"
    assert properties["Ingatlan_szobak_szama__c"]["target"] == (
        "property.number_of_rooms"
    )
    assert properties["Ingatlan_reszletes_tipus__c"]["target"] == (
        "property.property_type_detail"
    )
    assert properties["Ingatlan_hasznalatbaveteli__c"]["target"] == "property.extra"


def test_field_map_rules_carry_registry_validated_paths_for_adapter_concepts():
    field_map = load_field_map()

    assert field_map["Property__c"]["Id"] == {
        "target": "property.property_id",
        "path": "property.property_id",
    }
    assert field_map["Property__c"]["Ingatlan_szobak_szama__c"] == {
        "target": "property.number_of_rooms",
        "path": "property.number_of_rooms",
    }
    assert field_map["Property__c"]["Ingatlan_reszletes_tipus__c"] == {
        "target": "property.property_type_detail",
        "path": "property.property_type_detail",
    }
    assert field_map["Opportunity"]["CreatedDate"] == {
        "target": "deal.created_at",
        "path": "deal.created_at",
    }
    for rules in field_map.values():
        for rule in rules.values():
            if "path" in rule:
                assert canonical_registry.validate(rule["path"]), rule["path"]


def test_related_object_api_names_and_targets_are_map_configuration():
    field_map = load_field_map()
    income = field_map["Jovedelem__c"]
    loan = field_map["Meglevo_hitel__c"]

    assert income["Contact__c"]["target"] == "participant.contact_id"
    assert income["Sorszam__c"]["target"] == "income.sequence"
    assert income["Jovedelem_tipusa__c"]["target"] == "income.income_type"
    assert income["Osszeg__c"]["target"] == "income.amount"
    assert income["Penznem__c"]["target"] == "income.currency"
    assert income["Erkezes_helye__c"]["target"] == "income.destination"
    assert income["Bank__c"]["target"] == "income.bank_name"
    assert income["Szamlaszam__c"]["target"] == "income.account_number"
    assert loan["Contact__c"]["target"] == "participant.contact_id"
    assert loan["Sorszam__c"]["target"] == "existing_loan.sequence"
    assert loan["Hiteltipus__c"]["target"] == "existing_loan.loan_type"
    assert loan["Eredeti_osszeg__c"]["target"] == "existing_loan.original_amount"
    assert loan["Fennallo_tartozas__c"]["target"] == "existing_loan.outstanding_amount"
    assert loan["Havi_torleszto__c"]["target"] == "existing_loan.monthly_payment"
    assert loan["Kivaltando__c"]["target"] == "existing_loan.refinance"


def test_field_map_is_deterministic_and_checked_in_bytes_match():
    assert build_field_map() == build_field_map()
    assert json.loads(formatted_field_map()) == build_field_map()
    assert FIELD_MAP_PATH.read_text(encoding="utf-8") == formatted_field_map()
    assert load_field_map() == build_field_map()


def test_field_map_writer_is_idempotent_and_preserves_unchanged_mtime(tmp_path):
    destination = tmp_path / "salesforce_field_map.json"

    assert write_field_map(destination) is True
    first_bytes = destination.read_bytes()
    first_mtime = destination.stat().st_mtime_ns
    assert write_field_map(destination) is False
    assert destination.read_bytes() == first_bytes
    assert destination.stat().st_mtime_ns == first_mtime


def test_runtime_schema_loader_does_not_import_legacy_migration_code():
    source = (
        ROOT / "src" / "integrations" / "salesforce_schema.py"
    ).read_text(encoding="utf-8")
    field_map = load_field_map()

    assert "canonical_migration_map" not in source
    assert "Lead" not in field_map
    for rules in field_map.values():
        for rule in rules.values():
            assert not rule["target"].startswith(("Contact.", "Lead.", "Opportunity."))


def test_sync_import_survives_backend_config_module_alias():
    program = """
import importlib.util
import sys
from pathlib import Path
root = Path.cwd()
spec = importlib.util.spec_from_file_location('config', root / 'backend' / 'config.py')
backend_config = importlib.util.module_from_spec(spec)
sys.modules['config'] = backend_config
spec.loader.exec_module(backend_config)
import scripts.sync_sf_schema as sync_module
assert sync_module.settings.PROJECT_ROOT == root
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


class FakeDescribable:
    def __init__(self, salesforce, object_name):
        self.salesforce = salesforce
        self.object_name = object_name

    def describe(self):
        self.salesforce.describe_calls.append(self.object_name)
        return deepcopy(self.salesforce.raw_objects[self.object_name])


class AbsentDescribable:
    """Simulates an object the org does not expose (describe fails)."""

    def __init__(self, salesforce, object_name):
        self.salesforce = salesforce
        self.object_name = object_name

    def describe(self):
        self.salesforce.describe_calls.append(self.object_name)
        raise RuntimeError(f"ENTITY_IS_NOT_DELETED: {self.object_name} absent")


class FakeSalesforce:
    """Describe-only fake; every data/identity operation is a hard failure."""

    def __init__(self, raw_objects, absent_objects=()):
        self.raw_objects = raw_objects
        self.absent_objects = set(absent_objects)
        self.describe_calls = []

    def __getattr__(self, name):
        if name in self.absent_objects:
            return AbsentDescribable(self, name)
        if name in self.raw_objects:
            return FakeDescribable(self, name)
        raise AttributeError(name)

    def query(self, _query):
        raise AssertionError("Customer record query is forbidden")

    def identity(self):
        raise AssertionError("Identity call is forbidden in describe-only sync")


def raw_describe_objects(snapshot_data):
    raw_objects = {}
    for object_name, object_data in snapshot_data["objects"].items():
        fields = []
        for api_name, metadata in object_data["fields"].items():
            fields.append(
                {
                    "name": api_name,
                    "label": f"{api_name} label",
                    **deepcopy(metadata),
                    "createable": True,
                    "updateable": True,
                    "customerValue": "must not be copied",
                }
            )
        raw_objects[object_name] = {
            "name": object_name,
            "label": object_data["label"],
            "fields": fields,
            "records": [{"Id": "customer-record-must-not-be-copied"}],
            "urls": {"rowTemplate": "must-not-be-copied"},
        }
    return raw_objects


def test_sync_schema_uses_only_describe_and_writes_sanitized_snapshot(
    tmp_path, snapshot_data
):
    fake = FakeSalesforce(raw_describe_objects(snapshot_data))
    destination = tmp_path / "custom-snapshot.json"

    document = sync_schema(
        fake,
        destination,
        org_id=snapshot_data["org_id"],
        generated_at=FIXED_TIME,
    )

    assert fake.describe_calls == sorted(OBJECT_NAMES)
    assert document == snapshot_data
    assert json.loads(destination.read_text(encoding="utf-8")) == snapshot_data
    assert SalesforceSchema.from_dict(document).to_dict() == snapshot_data
    assert "customer-record" not in destination.read_text(encoding="utf-8")
    assert "customerValue" not in destination.read_text(encoding="utf-8")
    assert "rowTemplate" not in destination.read_text(encoding="utf-8")
    assert list(tmp_path.glob(".sf-schema-*")) == []


def test_sync_snapshot_is_byte_deterministic_with_fixed_clock(tmp_path, snapshot_data):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    fake = FakeSalesforce(raw_describe_objects(snapshot_data))

    sync_schema(fake, first, org_id=snapshot_data["org_id"], generated_at=FIXED_TIME)
    sync_schema(fake, second, org_id=snapshot_data["org_id"], generated_at=FIXED_TIME)

    assert first.read_bytes() == second.read_bytes()


def test_default_snapshot_name_is_dated_and_org_identified():
    destination = default_snapshot_path("00D000000000001AAA", FIXED_TIME)

    assert destination.name == "sf_schema_00D000000000001AAA_2026-10-29.json"


def test_sync_schema_records_one_missing_object_as_absent(
    tmp_path, snapshot_data
):
    """Task 2: an absent object must not fail the whole snapshot."""
    raw_objects = raw_describe_objects(snapshot_data)
    fake = FakeSalesforce(raw_objects, absent_objects={"Jovedelem__c"})
    destination = tmp_path / "partial-snapshot.json"

    document = sync_schema(
        fake,
        destination,
        org_id=snapshot_data["org_id"],
        generated_at=FIXED_TIME,
    )

    # Every configured object is still described individually, in order.
    assert fake.describe_calls == sorted(OBJECT_NAMES)
    expected = deepcopy(snapshot_data)
    del expected["objects"]["Jovedelem__c"]
    assert document == expected
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert written == expected
    # The absent object is recorded through its absence, never fabricated.
    schema = SalesforceSchema.from_dict(written)
    assert not schema.has_object("Jovedelem__c")
    assert schema.missing_objects(["Jovedelem__c"]) == ["Jovedelem__c"]
    assert schema.has_object("Contact")
    assert list(tmp_path.glob(".sf-schema-*")) == []


def test_sync_schema_fails_closed_when_no_requested_object_is_describable(
    tmp_path, snapshot_data
):
    """An entirely empty snapshot (e.g. auth failure) must not be written."""
    fake = FakeSalesforce(
        raw_describe_objects(snapshot_data), absent_objects=set(OBJECT_NAMES)
    )
    destination = tmp_path / "empty-snapshot.json"

    with pytest.raises(ValueError, match="Jovedelem__c"):
        sync_schema(
            fake,
            destination,
            org_id=snapshot_data["org_id"],
            generated_at=FIXED_TIME,
        )

    assert not destination.exists()
    assert list(tmp_path.glob(".sf-schema-*")) == []


def test_sync_cli_objects_flag_narrows_describe_request(
    tmp_path, snapshot_data, monkeypatch, capsys
):
    import scripts.sync_sf_schema as sync_module

    fake = FakeSalesforce(raw_describe_objects(snapshot_data))
    monkeypatch.setattr(sync_module.settings, "SF_USERNAME", "offline-user@example.invalid")
    monkeypatch.setattr(sync_module.settings, "SF_PASSWORD", "offline-password")
    monkeypatch.setattr(sync_module.settings, "SF_SECURITY_TOKEN", "offline-token")
    monkeypatch.setattr(sync_module.settings, "SF_DOMAIN", "test")
    monkeypatch.setattr(sync_module, "Salesforce", lambda **kwargs: fake)
    monkeypatch.setattr(sync_module, "_utcnow", lambda: FIXED_TIME)
    monkeypatch.setenv("SF_ORG_ID", snapshot_data["org_id"])
    destination = tmp_path / "filtered.json"

    result = sync_main(["--objects", "Contact, Account", "--output", str(destination)])

    assert result == 0
    assert fake.describe_calls == ["Account", "Contact"]
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert set(written["objects"]) == {"Contact", "Account"}
    assert written == {
        "org_id": snapshot_data["org_id"],
        "generated_at": "2026-10-29T01:02:03Z",
        "objects": {
            name: snapshot_data["objects"][name] for name in ("Contact", "Account")
        },
    }


def test_sync_cli_reports_absent_objects_without_secrets(
    tmp_path, snapshot_data, monkeypatch, capsys
):
    import scripts.sync_sf_schema as sync_module

    fake = FakeSalesforce(
        raw_describe_objects(snapshot_data),
        absent_objects={"Jovedelem__c", "Meglevo_hitel__c"},
    )
    monkeypatch.setattr(sync_module.settings, "SF_USERNAME", "offline-user@example.invalid")
    monkeypatch.setattr(sync_module.settings, "SF_PASSWORD", "offline-password-never-print")
    monkeypatch.setattr(sync_module.settings, "SF_SECURITY_TOKEN", "offline-token")
    monkeypatch.setattr(sync_module.settings, "SF_DOMAIN", "test")
    monkeypatch.setattr(sync_module, "Salesforce", lambda **kwargs: fake)
    monkeypatch.setattr(sync_module, "_utcnow", lambda: FIXED_TIME)
    monkeypatch.setenv("SF_ORG_ID", snapshot_data["org_id"])
    destination = tmp_path / "absent.json"

    result = sync_main(["--output", str(destination)])

    assert result == 0
    captured = capsys.readouterr()
    output = captured.out + captured.err
    for name in ("Jovedelem__c", "Meglevo_hitel__c"):
        assert name in output
        assert "absent" in output
    assert "offline-password-never-print" not in output
    assert "ENTITY_IS_NOT_DELETED" not in output
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert set(written["objects"]) == OBJECT_NAMES - {"Jovedelem__c", "Meglevo_hitel__c"}


def test_sync_cli_honors_output_and_existing_settings_without_printing_secrets(
    tmp_path, snapshot_data, monkeypatch, capsys
):
    import scripts.sync_sf_schema as sync_module

    fake = FakeSalesforce(raw_describe_objects(snapshot_data))
    constructor_arguments = {}

    def fake_salesforce(**kwargs):
        constructor_arguments.update(kwargs)
        return fake

    secrets = {
        "username": "offline-user@example.invalid",
        "password": "offline-password-never-print",
        "security_token": "offline-token-never-print",
    }
    monkeypatch.setattr(sync_module.settings, "SF_USERNAME", secrets["username"])
    monkeypatch.setattr(sync_module.settings, "SF_PASSWORD", secrets["password"])
    monkeypatch.setattr(
        sync_module.settings, "SF_SECURITY_TOKEN", secrets["security_token"]
    )
    monkeypatch.setattr(sync_module.settings, "SF_DOMAIN", "test")
    monkeypatch.setattr(sync_module, "Salesforce", fake_salesforce)
    monkeypatch.setattr(sync_module, "_utcnow", lambda: FIXED_TIME)
    monkeypatch.setenv("SF_ORG_ID", snapshot_data["org_id"])
    destination = tmp_path / "selected-output.json"

    result = sync_main(["--output", str(destination)])

    assert result == 0
    assert constructor_arguments == {
        "username": secrets["username"],
        "password": secrets["password"],
        "security_token": secrets["security_token"],
        "domain": "test",
    }
    output = capsys.readouterr().out + capsys.readouterr().err
    assert str(destination) in output
    for secret in secrets.values():
        assert secret not in output
    assert json.loads(destination.read_text(encoding="utf-8")) == snapshot_data
    assert fake.describe_calls == sorted(OBJECT_NAMES)

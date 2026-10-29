"""Deterministic mapping migrator: dry-run audit over the real mapping files."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.migrate_mappings_to_genie import (
    MigrationResult,
    migrate_mapping,
    main,
    mapping_files,
)


ROOT = Path(__file__).resolve().parents[1]
MAPPING_DIR = ROOT / "src" / "mapping"
# Cutover Task 3: az aktív mappingek már MIGRÁLT (Genie canonical) állapotban
# élnek. A migrátor legacy-input regressziói az aktiválás ELŐTTI audited
# snapshotból futnak (ugyanaz az állapot, amit a Task 1 dry-run audit és a
# --apply írt). Az "active mapping" inventor-t továbbra is a élő
# MAPPING_DIR adja.
LEGACY_DIR = ROOT / ".superpowers" / "sdd" / "mapping-preactivation"
DEFAULT_REPORT = ROOT / ".superpowers" / "sdd" / "genie-migration-dry-run.json"

# The two `.bak` files and legal_defaults.json are not active mappings:
# legal_defaults.json carries no ``fields`` document at all.
EXPECTED_MAPPINGS = sorted(
    path.name
    for path in LEGACY_DIR.glob("*.json")
    if isinstance(json.loads(path.read_text(encoding="utf-8")).get("fields"), list)
)
# The snapshot must mirror exactly the active mapping inventory.
assert EXPECTED_MAPPINGS == sorted(
    path.name
    for path in MAPPING_DIR.glob("*.json")
    if isinstance(json.loads(path.read_text(encoding="utf-8")).get("fields"), list)
), "pre-activation snapshot out of sync with src/mapping inventory"

COMPANY_MAPPING = (
    "v_szamu_fuggelek_penzugyi_szolgaltatas_kozvetiteseben_valo_"
    "kozremukodesre_vonatkozo_ugyfel_ceg_nyilatkozat_20250601_mapping.json"
)


def load_mapping(filename: str) -> dict:
    return json.loads((LEGACY_DIR / filename).read_text(encoding="utf-8"))


def find_field(data: dict, pdf_field_name: str) -> dict:
    for field in data["fields"]:
        if field.get("pdf_field_name") == pdf_field_name:
            return field
    raise AssertionError(f"Field {pdf_field_name!r} not found")


def migrated_field(result: MigrationResult, pdf_field_name: str) -> dict:
    return find_field(result.mapping, pdf_field_name)


def dir_hashes(directory: Path) -> dict:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.glob("*.json"))
    }


# ──────────────────────────────────────────────────────────────────────────
# Coverage: every active mapping file is loaded and audited
# ──────────────────────────────────────────────────────────────────────────


def test_every_active_mapping_file_is_audited():
    files = mapping_files(MAPPING_DIR)

    assert [path.name for path in files] == EXPECTED_MAPPINGS
    assert len(EXPECTED_MAPPINGS) >= 22


def test_migrate_mapping_returns_result_for_every_file():
    for name in EXPECTED_MAPPINGS:
        result = migrate_mapping(load_mapping(name))

        assert isinstance(result, MigrationResult)
        assert isinstance(result.mapping, dict)
        assert isinstance(result.changed_count, int)


# ──────────────────────────────────────────────────────────────────────────
# Representative semantic corrections from real files
# ──────────────────────────────────────────────────────────────────────────


def test_date_suffix_nap_becomes_fragment_metadata():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    result = migrate_mapping(data)

    field = migrated_field(result, "SZA_IG_mióta_lakik_nap")
    assert field["canonical_field"] == "participant.borrower.residence_notification_date"
    assert field["fragment"] == "day"

    change = next(
        item
        for item in result.fragment_changes
        if item["pdf_field_name"] == "SZA_IG_mióta_lakik_nap"
    )
    assert change["legacy"] == "Contact.Date_of_notification_for_residence__c#nap"
    assert change["fragment"] == "day"


def test_count_suffix_uses_label_context_for_children():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    result = migrate_mapping(data)

    field = migrated_field(result, "SZA_IG_eltartottak")
    # Label: "Ebből gyermekek száma (fő)" → children count, not the free-text default.
    assert field["canonical_field"] == "participant.borrower.children_count"

    correction = next(
        item
        for item in result.semantic_corrections
        if item["pdf_field_name"] == "SZA_IG_eltartottak"
    )
    assert correction["legacy"] == "Contact.Numer_and_Age_of_Children__c#count"
    assert correction["canonical"] == "participant.borrower.children_count"


def test_household_label_redirects_dependents_legacy_field():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    result = migrate_mapping(data)

    field = migrated_field(result, "SZA_IG_létszám")
    # Label: "Mekkora a háztartás létszáma? (fő)" although the legacy field
    # claims Dependents_count.
    assert field["canonical_field"] == "participant.borrower.household_size"

    assert any(
        item["pdf_field_name"] == "SZA_IG_létszám"
        for item in result.semantic_corrections
    )


def test_character_group_count_uses_member_field_labels():
    data = load_mapping(
        "Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json"
    )
    result = migrate_mapping(data)

    groups = {
        group["group_id"]: group for group in result.mapping["character_groups"]
    }
    household = groups["chargroup_contact_dependents_count_c_p6_12"]
    # Member labels say "Háztartás létszáma (1. számjegy)", so the legacy
    # Dependents field migrates to household size for the co-borrower.
    assert household["canonical_field"] == "participant.co_borrower.household_size"
    assert household["member_fields"] == ["Text Field 718", "Text Field 719"]
    assert household["direction"] == "left_to_right"
    assert household["separator"] == ""

    dependents = groups["chargroup_contact_dependents_count_c_p6_13"]
    assert dependents["canonical_field"] == "participant.co_borrower.dependents_count"

    children = groups["chargroup_contact_numer_and_age_of_children_c_p6_14"]
    assert children["canonical_field"] == "participant.co_borrower.children_count"

    group_ids = {item["group_id"] for item in result.group_changes}
    assert {
        "chargroup_contact_dependents_count_c_p6_12",
        "chargroup_contact_dependents_count_c_p6_13",
        "chargroup_contact_numer_and_age_of_children_c_p6_14",
    } <= group_ids


def test_co_borrower_suffix_becomes_explicit_role_path():
    data = load_mapping(
        "Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json"
    )
    result = migrate_mapping(data)

    field = migrated_field(result, "Check Box 76___Yes")
    assert field["canonical_field"] == "participant.co_borrower.citizenship"

    correction = next(
        item
        for item in result.semantic_corrections
        if item["pdf_field_name"] == "Check Box 76___Yes"
    )
    assert correction["legacy"] == "Contact.Citizenship__c-társ"


def test_gender_label_redirects_salutation():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    result = migrate_mapping(data)

    field = migrated_field(result, "SZA_IG_neme___Yes")
    assert field["canonical_field"] == "participant.borrower.gender"


def test_property_index_becomes_explicit_path_index():
    data = load_mapping("ingatlan_adatlap_mapping.json")
    extra = copy.deepcopy(find_field(data, "IA_cím_isz"))
    extra["pdf_field_name"] = "IA_cím_isz_2"
    extra["label"] = "Ingatlan irányítószáma (második ingatlan)"
    extra["canonical_field"] = "Lead.Ingatlan_irsz__c_2"
    data = copy.deepcopy(data)
    data["fields"].append(extra)

    result = migrate_mapping(data)

    field = migrated_field(result, "IA_cím_isz_2")
    assert field["canonical_field"] == "property[2].address.zip_code"
    assert any(
        item["pdf_field_name"] == "IA_cím_isz_2"
        for item in result.semantic_corrections
    )
    # The un-indexed original stays first property.
    assert migrated_field(result, "IA_cím_isz")["canonical_field"] == (
        "property.address.zip_code"
    )


def test_company_declaration_redirects_to_account_namespace():
    data = load_mapping(COMPANY_MAPPING)
    result = migrate_mapping(data)

    company_name = migrated_field(result, "Szövegmező 2")
    assert company_name["canonical_field"] == "account.name"

    company_phone = migrated_field(result, "Szövegmező 12")
    assert company_phone["canonical_field"] == "account.phone"

    company_seat = migrated_field(result, "Szövegmező 4")
    assert company_seat["canonical_field"] == "account.billing_street"

    # The natural-person name in the same document stays participant-scoped.
    person_name = migrated_field(result, "Szövegmező 1")
    assert person_name["canonical_field"] == "participant.borrower.name"

    assert {
        item["pdf_field_name"] for item in result.semantic_corrections
    } >= {"Szövegmező 2", "Szövegmező 12", "Szövegmező 4"}


def test_requested_payment_moves_to_deal_and_participant_stays_loan():
    data = load_mapping("acroform_sample_mapping.json")
    result = migrate_mapping(data)

    requested = migrated_field(result, "loan_monthly_payment")
    # "Becsült havi törlesztő" is the requested deal payment.
    assert requested["canonical_field"] == "deal.monthly_payment"
    assert any(
        item["pdf_field_name"] == "loan_monthly_payment"
        for item in result.semantic_corrections
    )

    participant = copy.deepcopy(find_field(data, "loan_monthly_payment"))
    participant["pdf_field_name"] = "borrower_existing_loan_payment"
    participant["label"] = "Meglévő hitel havi törlesztője"
    data = copy.deepcopy(data)
    data["fields"].append(participant)

    result = migrate_mapping(data)
    field = migrated_field(result, "borrower_existing_loan_payment")
    # Without the "becsült" context the same legacy field describes the
    # participant's own existing loan installment.
    assert field["canonical_field"] == (
        "participant.borrower.existing_loan[1].monthly_payment"
    )


def test_product_suffixes_become_explicit_gate_metadata():
    data = load_mapping(
        "Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json"
    )
    result = migrate_mapping(data)

    market = migrated_field(result, "Check Box 186___Yes")
    assert market["canonical_field"] == "deal.insurance"
    assert market["product_gate"] == "piaci"
    # The fill rule is preserved untouched for the existing rule logic.
    assert market["fill_rule"] == find_field(
        load_mapping(
            "Igenylesi_dokumentum_elozetes_ertekbecsleshez_20241104_mapping.json"
        ),
        "Check Box 186___Yes",
    )["fill_rule"]

    supported = migrated_field(result, "Check Box 177___Yes")
    assert supported["canonical_field"] == "deal.insurance"
    assert supported["product_gate"] == "tamogatott"

    gates = {item["product_gate"] for item in result.product_gates}
    assert gates == {"piaci", "tamogatott"}
    # Every gated path is one of the approved product-gate targets and the
    # gate value is exactly the decoded legacy suffix.
    gate_targets = {
        "deal.loan_amount",
        "deal.loan_purpose",
        "deal.loan_term_years",
        "deal.insurance",
        "property.insurance",
    }
    for item in result.product_gates:
        assert item["canonical"] in gate_targets
        assert item["legacy"].endswith("#" + item["product_gate"])


def test_participant_index_becomes_explicit_path_index():
    data = load_mapping("fedlap_mapping.json")
    co_debtor = {
        "pdf_field_name": "FEDLAP_adostars_id",
        "label": "Első adóstárs Contact azonosítója",
        "field_type": "text",
        "canonical_field": "Contact.First_Co_debtor__c",
        "confidence": "high",
        "page_number": 1,
        "coordinates": None,
        "notes": None,
        "checkbox_group": None,
        "fill_rule": None,
        "fragment": None,
    }
    data = copy.deepcopy(data)
    data["fields"].append(co_debtor)

    result = migrate_mapping(data)

    field = migrated_field(result, "FEDLAP_adostars_id")
    assert field["canonical_field"] == "participant.co_borrower[1].contact_id"


# ──────────────────────────────────────────────────────────────────────────
# Preservation of PDF-level metadata
# ──────────────────────────────────────────────────────────────────────────


MUTABLE_FIELD_KEYS = {"canonical_field", "fragment", "product_gate"}


@pytest.mark.parametrize("filename", EXPECTED_MAPPINGS)
def test_migration_preserves_all_pdf_metadata(filename):
    original = load_mapping(filename)
    result = migrate_mapping(original)

    assert len(result.mapping["fields"]) == len(original["fields"])
    for before, after in zip(original["fields"], result.mapping["fields"]):
        kept_before = {k: v for k, v in before.items() if k not in MUTABLE_FIELD_KEYS}
        kept_after = {k: v for k, v in after.items() if k not in MUTABLE_FIELD_KEYS}
        assert kept_after == kept_before
        assert after["pdf_field_name"] == before["pdf_field_name"]
        assert after.get("coordinates") == before.get("coordinates")
        assert after.get("checkbox_group") == before.get("checkbox_group")
        assert after.get("fill_rule") == before.get("fill_rule")

    original_groups = original.get("character_groups") or []
    migrated_groups = result.mapping.get("character_groups") or []
    assert len(migrated_groups) == len(original_groups)
    for before, after in zip(original_groups, migrated_groups):
        kept_before = {k: v for k, v in before.items() if k not in MUTABLE_FIELD_KEYS}
        kept_after = {k: v for k, v in after.items() if k not in MUTABLE_FIELD_KEYS}
        assert kept_after == kept_before
        assert after["member_fields"] == before["member_fields"]

    for key, value in original.items():
        if key in ("fields", "character_groups"):
            continue
        assert result.mapping[key] == value
    assert result.mapping.get("points") == original.get("points")
    assert result.mapping.get("rule_sets") == original.get("rule_sets")


def test_unmapped_fields_stay_null_and_are_not_unresolved():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    result = migrate_mapping(data)

    field = migrated_field(result, "SZA_IG_kegészítő_adatok")
    assert field["canonical_field"] is None
    assert not any(
        item.get("pdf_field_name") == "SZA_IG_kegészítő_adatok"
        for item in result.unresolved
    )


def test_migrate_mapping_does_not_mutate_input():
    data = load_mapping("sza_ig_igenylo_mapping.json")
    snapshot = copy.deepcopy(data)

    result = migrate_mapping(data)

    assert data == snapshot
    assert result.mapping is not data
    assert result.mapping != data


# ──────────────────────────────────────────────────────────────────────────
# Idempotence and unresolved handling
# ──────────────────────────────────────────────────────────────────────────


def test_second_pass_is_idempotent_for_every_mapping():
    for name in EXPECTED_MAPPINGS:
        first = migrate_mapping(load_mapping(name))
        second = migrate_mapping(first.mapping)

        assert second.mapping == first.mapping, name
        assert second.changed_count == 0, name
        assert second.unresolved == [], name
        assert second.semantic_corrections == [], name
        assert second.fragment_changes == [], name
        assert second.group_changes == [], name
        assert second.product_gates == [], name


def test_unknown_legacy_entry_is_reported_not_raised():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    data["fields"].append(
        {
            "pdf_field_name": "FEDLAP_bogus",
            "label": "Ismeretlen mező",
            "field_type": "text",
            "canonical_field": "Contact.Bogus__c",
            "confidence": "low",
            "page_number": 1,
            "coordinates": None,
            "notes": None,
            "checkbox_group": None,
            "fill_rule": None,
            "fragment": None,
        }
    )

    result = migrate_mapping(data)

    assert len(result.unresolved) == 1
    unresolved = result.unresolved[0]
    assert unresolved["legacy"] == "Contact.Bogus__c"
    assert unresolved["pdf_field_name"] == "FEDLAP_bogus"
    assert unresolved["reason"]
    # The field itself is left untouched.
    assert migrated_field(result, "FEDLAP_bogus")["canonical_field"] == "Contact.Bogus__c"


# ──────────────────────────────────────────────────────────────────────────
# CLI: dry-run default, report, --only, apply guardrails
# ──────────────────────────────────────────────────────────────────────────


def test_cli_dry_run_is_default_and_writes_nothing(tmp_path):
    before = dir_hashes(MAPPING_DIR)
    report_path = tmp_path / "dry-run.json"

    exit_code = main(["--report", str(report_path)])

    assert exit_code == 0
    assert dir_hashes(MAPPING_DIR) == before
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "dry-run"
    assert [entry["file"] for entry in report["mappings"]] == EXPECTED_MAPPINGS
    totals = report["totals"]
    assert totals["mappings"] == len(EXPECTED_MAPPINGS)
    for key in (
        "entries_changed",
        "unresolved",
        "semantic_corrections",
        "fragment_changes",
        "group_changes",
        "product_gates",
    ):
        assert isinstance(totals[key], int)
        assert totals[key] >= 0
    # Per-mapping dry-run numbers match the functional API. Cutover Task 3:
    # a CLI az ÉLŐ mapping-könyvtárat auditalja (már migrált → 0 változás).
    for entry in report["mappings"]:
        live_document = json.loads(
            (MAPPING_DIR / entry["file"]).read_text(encoding="utf-8")
        )
        result = migrate_mapping(live_document)
        assert entry["changed_count"] == result.changed_count
        assert entry["unresolved"] == result.unresolved


def test_cli_dry_run_subprocess_leaves_mappings_untouched(tmp_path):
    before = dir_hashes(MAPPING_DIR)
    report_path = tmp_path / "subprocess-report.json"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "migrate_mappings_to_genie.py"),
            "--report",
            str(report_path),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )

    assert completed.returncode == 0, completed.stderr
    assert dir_hashes(MAPPING_DIR) == before
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["mappings"]) >= 22


def test_cli_only_restricts_selection(tmp_path):
    report_path = tmp_path / "only.json"

    exit_code = main(["--only", "sza_ig_igenylo", "--report", str(report_path)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [entry["file"] for entry in report["mappings"]] == [
        "sza_ig_igenylo_mapping.json"
    ]


def test_cli_apply_writes_migrated_content_then_refuses_second_run(tmp_path):
    work_dir = tmp_path / "mappings"
    work_dir.mkdir()
    # Cutover Task 3: az apply-path teszt legacy (pre-activation) bemeneteken
    # fut — az élő src/mapping már migrált.
    for path in LEGACY_DIR.glob("*.json"):
        (work_dir / path.name).write_bytes(path.read_bytes())

    report_path = tmp_path / "apply.json"
    exit_code = main(
        [
            "--apply",
            "--mapping-dir",
            str(work_dir),
            "--only",
            "fedlap",
            "--report",
            str(report_path),
        ]
    )
    assert exit_code == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["mode"] == "apply"
    applied = json.loads(
        (work_dir / "fedlap_mapping.json").read_text(encoding="utf-8")
    )
    expected = migrate_mapping(load_mapping("fedlap_mapping.json"))
    assert applied == expected.mapping
    # Unselected files stay untouched.
    assert (work_dir / "sza_ig_igenylo_mapping.json").read_bytes() == (
        LEGACY_DIR / "sza_ig_igenylo_mapping.json"
    ).read_bytes()

    # Running apply again refuses: nothing left to migrate.
    before = dir_hashes(work_dir)
    exit_code = main(
        [
            "--apply",
            "--mapping-dir",
            str(work_dir),
            "--only",
            "fedlap",
            "--report",
            str(tmp_path / "second.json"),
        ]
    )
    assert exit_code == 3
    assert dir_hashes(work_dir) == before


def test_cli_apply_aborts_entirely_on_unresolved_entries(tmp_path):
    work_dir = tmp_path / "mappings"
    work_dir.mkdir()
    # Cutover Task 3: legacy (pre-activation) bemenetek az apply-pathhoz.
    for path in LEGACY_DIR.glob("*.json"):
        (work_dir / path.name).write_bytes(path.read_bytes())
    poisoned = json.loads(
        (work_dir / "fedlap_mapping.json").read_text(encoding="utf-8")
    )
    poisoned["fields"][0]["canonical_field"] = "Contact.Bogus__c"
    (work_dir / "fedlap_mapping.json").write_text(
        json.dumps(poisoned, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    before = dir_hashes(work_dir)

    report_path = tmp_path / "aborted.json"
    exit_code = main(
        [
            "--apply",
            "--mapping-dir",
            str(work_dir),
            "--report",
            str(report_path),
        ]
    )

    # The whole apply aborts: no file was rewritten.
    assert exit_code == 2
    assert dir_hashes(work_dir) == before
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["totals"]["unresolved"] >= 1
    assert any(
        item["legacy"] == "Contact.Bogus__c"
        for entry in report["mappings"]
        for item in entry["unresolved"]
    )


def test_cli_apply_and_dry_run_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main(["--dry-run", "--apply"])


# ──────────────────────────────────────────────────────────────────────────
# Cutover Task 3 — nested references: conditional fill_rule.sf_field,
# point rule params and checkbox/rule metadata (additional requirement 1)
# ──────────────────────────────────────────────────────────────────────────


def _synthetic_conditional_entry(pdf_name: str, label: str) -> dict:
    return {
        "pdf_field_name": pdf_name,
        "label": label,
        "field_type": "text",
        "canonical_field": "Contact.Monthly_Payment_details__c",
        "confidence": "high",
        "page_number": 1,
        "coordinates": None,
        "notes": None,
        "checkbox_group": None,
        "fill_rule": {
            "type": "conditional",
            "sf_field": "Contact.Loan_Purpose__c",
            "match": "Lakásvásárlás",
            "value": "igen",
        },
        "fragment": None,
    }


def test_conditional_fill_rule_sf_field_is_translated():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    data["fields"].append(
        _synthetic_conditional_entry("FEDLAP_felteteles", "Havi törlesztő")
    )

    result = migrate_mapping(data)

    field = migrated_field(result, "FEDLAP_felteteles")
    assert field["fill_rule"]["sf_field"] == "deal.loan_purpose"
    # Every other rule key is preserved byte-for-byte.
    assert field["fill_rule"]["type"] == "conditional"
    assert field["fill_rule"]["match"] == "Lakásvásárlás"
    assert field["fill_rule"]["value"] == "igen"

    change = next(
        item
        for item in result.nested_changes
        if item["pdf_field_name"] == "FEDLAP_felteteles"
    )
    assert change["legacy"] == "Contact.Loan_Purpose__c"
    assert change["canonical"] == "deal.loan_purpose"
    assert change["location"] == "fields[13].fill_rule.sf_field" or change[
        "location"
    ].endswith(".fill_rule.sf_field")


def test_conditional_sf_field_translation_uses_entry_label_context():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    # Label "Becsült havi törlesztő" redirects the requested payment entry
    # itself; the gender label redirects Salutation sf_field references.
    data["fields"].append(
        _synthetic_conditional_entry("FEDLAP_nem_feltetel", "Neme")
    )
    data["fields"][-1]["fill_rule"]["sf_field"] = "Contact.Salutation"

    result = migrate_mapping(data)

    field = migrated_field(result, "FEDLAP_nem_feltetel")
    # "Neme" label context: Salutation migrates to the explicit gender path.
    assert field["fill_rule"]["sf_field"] == "participant.borrower.gender"


def test_already_canonical_sf_field_is_left_unchanged():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    entry = _synthetic_conditional_entry("FEDLAP_ready", "Havi törlesztő")
    entry["fill_rule"]["sf_field"] = "deal.loan_purpose"
    data["fields"].append(entry)

    result = migrate_mapping(data)

    field = migrated_field(result, "FEDLAP_ready")
    assert field["fill_rule"]["sf_field"] == "deal.loan_purpose"
    assert not any(
        item.get("pdf_field_name") == "FEDLAP_ready"
        for item in result.nested_changes
    )


def test_unknown_sf_field_is_reported_as_unresolved():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    entry = _synthetic_conditional_entry("FEDLAP_bogus_rule", "Havi törlesztő")
    entry["fill_rule"]["sf_field"] = "Contact.Bogus__c"
    data["fields"].append(entry)

    result = migrate_mapping(data)

    unresolved = next(
        item
        for item in result.unresolved
        if item.get("pdf_field_name") == "FEDLAP_bogus_rule"
    )
    assert unresolved["legacy"] == "Contact.Bogus__c"
    assert ".fill_rule.sf_field" in unresolved["location"]
    # Left untouched in the mapping document.
    field = migrated_field(result, "FEDLAP_bogus_rule")
    assert field["fill_rule"]["sf_field"] == "Contact.Bogus__c"


def test_point_rule_references_are_translated():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    data["points"] = [
        {
            "point_id": "P1",
            "framework": "*",
            "label": "Legmagasabb iskolai végzettség",
            "page_number": 1,
            "blocks": [{"block_id": "Egyetem", "members": ["CB_1"]}],
            "rule_type": 3,
            "params": {
                "active_block": None,
                "canonical_field": "Contact.Highest_Educational_Qualification__c",
                "sf_field": "Contact.Marital_Status__c",
            },
        }
    ]

    result = migrate_mapping(data)

    point = result.mapping["points"][0]
    assert point["params"]["canonical_field"] == (
        "participant.borrower.education"
    )
    assert point["params"]["sf_field"] == "participant.borrower.marital_status"
    # Everything else about the point is preserved.
    assert point["point_id"] == "P1"
    assert point["rule_type"] == 3
    assert point["blocks"] == [{"block_id": "Egyetem", "members": ["CB_1"]}]
    assert point["params"]["active_block"] is None

    locations = {item["location"] for item in result.nested_changes}
    assert any(
        loc.startswith("points[0]") and loc.endswith(".canonical_field")
        for loc in locations
    )
    assert any(
        loc.startswith("points[0]") and loc.endswith(".sf_field")
        for loc in locations
    )


def test_point_rule_block_metadata_reference_is_translated():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    data["points"] = [
        {
            "point_id": "P2",
            "framework": "*",
            "label": "Állampolgárság",
            "page_number": 1,
            "blocks": [
                {
                    "block_id": "Magyar",
                    "members": ["CB_2"],
                    "canonical_field": "Contact.Citizenship__c",
                }
            ],
            "rule_type": 1,
            "params": {},
        }
    ]

    result = migrate_mapping(data)

    assert result.mapping["points"][0]["blocks"][0]["canonical_field"] == (
        "participant.borrower.citizenship"
    )


def test_checkbox_group_metadata_reference_is_translated():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    entry = _synthetic_conditional_entry("FEDLAP_cbg_meta", "Állampolgárság")
    entry["fill_rule"] = None
    entry["canonical_field"] = None
    entry["checkbox_group"] = {
        "group_id": "allampolgarsag_meta",
        "group_label": "Állampolgársága",
        "option_value": "Magyar",
        "canonical_field": "Contact.Citizenship__c",
    }
    data["fields"].append(entry)

    result = migrate_mapping(data)

    cbg = migrated_field(result, "FEDLAP_cbg_meta")["checkbox_group"]
    assert cbg["canonical_field"] == "participant.borrower.citizenship"
    assert cbg["option_value"] == "Magyar"
    assert cbg["group_id"] == "allampolgarsag_meta"


def test_nested_changes_count_in_changed_count_and_stay_idempotent():
    data = load_mapping("fedlap_mapping.json")
    data = copy.deepcopy(data)
    data["fields"].append(
        _synthetic_conditional_entry("FEDLAP_felteteles", "Havi törlesztő")
    )
    baseline = migrate_mapping(load_mapping("fedlap_mapping.json"))

    first = migrate_mapping(data)
    # The synthetic entry contributes TWO changes: its own canonical_field
    # translation plus the nested sf_field translation.
    assert first.changed_count == baseline.changed_count + 2
    assert len(first.nested_changes) == 1

    second = migrate_mapping(first.mapping)
    assert second.mapping == first.mapping
    assert second.changed_count == 0
    assert second.nested_changes == []
    assert second.unresolved == []


def test_real_mappings_have_no_unresolved_nested_references():
    for name in EXPECTED_MAPPINGS:
        result = migrate_mapping(load_mapping(name))

        nested_unresolved = [
            item
            for item in result.unresolved
            if ".fill_rule." in item["location"]
            or item["location"].startswith("points[")
            or ".checkbox_group." in item["location"]
        ]
        assert nested_unresolved == [], name


def test_cli_report_includes_nested_change_totals(tmp_path):
    report_path = tmp_path / "nested.json"
    exit_code = main(["--report", str(report_path)])

    assert exit_code == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "nested_changes" in report["totals"]
    for entry in report["mappings"]:
        assert "nested_changes" in entry
        assert isinstance(entry["nested_changes"], list)

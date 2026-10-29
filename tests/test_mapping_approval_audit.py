"""Audit: every active mapping must stay unapproved (approved: false, approved_by: null).

Final-review finding B1: ``otp_acroform_mapping.json`` still carried
``approved: true`` / ``approved_by: "PoC auto-generated"`` after the Genie
cutover, while HANDOFF.md, the cutover Task 6 report and the cutover
verification report all state that no mapping is approved yet. This test pins
the documented state: no ``src/mapping/*.json`` may claim approval until the
human approval pass in Mapping Studio actually happens.

``legal_defaults.json`` is excluded: it is shared legal boilerplate config,
not a mapping document (it carries no ``approved`` flag).
"""
import json
from pathlib import Path

MAPPING_DIR = Path(__file__).resolve().parent.parent / "src" / "mapping"

#: Not a mapping document — no approved flag, shared legal boilerplate.
EXCLUDED_FILES = {"legal_defaults.json"}


def test_every_active_mapping_is_unapproved():
    mapping_files = sorted(
        p for p in MAPPING_DIR.glob("*.json") if p.name not in EXCLUDED_FILES
    )
    assert mapping_files, "src/mapping should contain mapping JSON documents"

    offenders = []
    for path in mapping_files:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), f"{path.name} should be a mapping object"
        approved = document.get("approved")
        approved_by = document.get("approved_by")
        if approved is not False or approved_by is not None:
            offenders.append(
                f"{path.name}: approved={approved!r}, approved_by={approved_by!r}"
            )

    assert not offenders, (
        "Active mappings must stay unapproved (approved: false, approved_by: "
        "null) until the human approval pass in Mapping Studio. "
        "Violations: " + "; ".join(offenders)
    )

"""
FIX C6 — DocumentAssembler AcroForm field renaming tests.

When a master-PDF page template is duplicated for multiple instances (several
co-borrowers, multiple properties), every copy used to share ONE AcroForm field
object — so all instances displayed the SAME value. FIX C6 renames the fields
on each DUPLICATE page with an ``_N`` suffix so they become independent.

These tests cover:
  - assembler returns distinct renamed fields (``borrower_name_2``/``_3`` …)
  - the renamed fields are independently fillable (different values)
  - the instance plan describes roles for the pipeline
  - the pipeline routes the Nth participant's / property's data to ``_N`` fields
  - end-to-end: a filled multi-instance PDF holds distinct values per instance

A synthetic master PDF is built in-memory (no 97-page OTP master needed).
"""
import pikepdf
import pytest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from src.engine.document_assembler import (
    DocumentAssembler, AssemblyResult, InstanceFieldMap, ProductType,
    INSTANCE_ROLE_CO_BORROWER, INSTANCE_ROLE_PROPERTY,
    _classify_instance_role,
)
from src.main import FormFillerPipeline
from src.integrations.salesforce_client import SalesforceClient
from src.ai.field_recognizer import MappingConfig, RecognizedField, FieldType, MappingConfidence
from src.models.canonical_model import (
    DealData, ParticipantRole, Participant, Address, LoanDetails,
    Property, PropertyType,
)


PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers — build a synthetic master PDF with an AcroForm template page
# ---------------------------------------------------------------------------

def build_master_with_fields(field_names: list[str], num_pages: int = 1) -> Path:
    """Build a small master PDF; page (num_pages) carries the AcroForm fields.

    Each field is a text field (``/Tx``) with a single widget annotation on the
    last page. Returns the path to the saved master.
    """
    import tempfile
    pdf = pikepdf.Pdf.new()
    for _ in range(num_pages):
        pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name("/Page"), MediaBox=pikepdf.Array([0, 0, 200, 200]),
        )))

    fields = []
    for name in field_names:
        widget = pdf.make_indirect(pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Widget"), Type=pikepdf.Name("/Annot"),
            Rect=pikepdf.Array([10, 10, 100, 30]),
        ))
        field = pdf.make_indirect(pikepdf.Dictionary(
            T=pikepdf.String(name), FT=pikepdf.Name("/Tx"), V=pikepdf.String(""),
            Ff=pikepdf.Integer(0), Kids=pikepdf.Array([widget]),
        ))
        widget.Parent = field
        fields.append(field)

    template_page = pdf.pages[num_pages - 1].obj
    template_page.Annots = pdf.make_indirect(pikepdf.Array([f.Kids[0] for f in fields]))
    pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary(
        Fields=pikepdf.Array(fields), NeedAppearances=True,
    ))

    out = Path(tempfile.mkdtemp()) / "master.pdf"
    pdf.save(out)
    pdf.close()
    return out


def duplicate_page_plan(template_page: int, n_instances: int, section_prefix="sza_ig_tarsigenylő_"):
    """A page plan that uses ``template_page`` n_instances times."""
    return [
        {"page": template_page, "section": f"{section_prefix}{i}", "note": f"inst{i}"}
        for i in range(1, n_instances + 1)
    ]


def read_field_values(pdf_path: Path) -> dict[str, str]:
    """Read back {field_name: value} from a PDF's AcroForm /Fields."""
    out = {}
    with pikepdf.open(pdf_path) as pdf:
        if "/AcroForm" not in pdf.Root:
            return out
        for f in pdf.Root.AcroForm.Fields:
            out[str(f.get("/T", ""))] = str(f.get("/V", ""))
    return out


def fill_fields(pdf_path: Path, values: dict[str, str]) -> None:
    with pikepdf.open(pdf_path, allow_overwriting_input=True) as pdf:
        for f in pdf.Root.AcroForm.Fields:
            t = str(f.get("/T", ""))
            if t in values:
                f.V = pikepdf.String(values[t])
        pdf.save(pdf_path)


# ---------------------------------------------------------------------------
# 1. DocumentAssembler field renaming
# ---------------------------------------------------------------------------

class TestAssemblerFieldRenaming:
    def test_three_instances_produce_distinct_renamed_fields(self, tmp_path):
        """3 copies of a template page → borrower_name, _2, _3 (instance 1 original)."""
        master = build_master_with_fields(["borrower_name", "borrower_city"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 3)):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 3, 1,
                                  output_path=tmp_path / "out.pdf")

        assert isinstance(result, AssemblyResult)
        names = set(read_field_values(result.output_path).keys())
        # Original (instance 1) + two renamed copies (instances 2 and 3).
        assert names == {
            "borrower_name", "borrower_name_2", "borrower_name_3",
            "borrower_city", "borrower_city_2", "borrower_city_3",
        }

    def test_renamed_fields_are_independently_fillable(self, tmp_path):
        """Filling borrower_name / _2 / _3 must set three DIFFERENT values."""
        master = build_master_with_fields(["borrower_name"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 3)):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 3, 1,
                                  output_path=tmp_path / "out.pdf")

        fill_fields(result.output_path, {
            "borrower_name": "PRIMARY",
            "borrower_name_2": "COB1",
            "borrower_name_3": "COB2",
        })
        vals = read_field_values(result.output_path)
        assert vals["borrower_name"] == "PRIMARY"
        assert vals["borrower_name_2"] == "COB1"
        assert vals["borrower_name_3"] == "COB2"

    def test_single_instance_keeps_original_field_names(self, tmp_path):
        """No duplication → no renaming; original names are preserved exactly."""
        master = build_master_with_fields(["borrower_name", "borrower_city"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 1)):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 1, 1,
                                  output_path=tmp_path / "out.pdf")
        assert set(read_field_values(result.output_path).keys()) == {"borrower_name", "borrower_city"}
        assert result.instance_fields == []

    def test_instance_plan_describes_roles_and_base_fields(self, tmp_path):
        master = build_master_with_fields(["borrower_name", "borrower_city"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 3)):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 3, 1,
                                  output_path=tmp_path / "out.pdf")
        # Two duplicate instances (2 and 3); instance 1 is the original.
        assert len(result.instance_fields) == 2
        by_inst = {im.instance: im for im in result.instance_fields}
        assert set(by_inst) == {2, 3}
        for im in result.instance_fields:
            assert im.role == INSTANCE_ROLE_CO_BORROWER
            assert set(im.base_fields) == {"borrower_name", "borrower_city"}

    def test_property_sections_classified_as_property_role(self, tmp_path):
        master = build_master_with_fields(["property_city"])
        asm = DocumentAssembler()
        plan = duplicate_page_plan(1, 2, section_prefix="ingatlan_adatlap_")
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: plan):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 1, 2,
                                  output_path=tmp_path / "out.pdf")
        assert len(result.instance_fields) == 1
        assert result.instance_fields[0].role == INSTANCE_ROLE_PROPERTY
        assert "property_city_2" in read_field_values(result.output_path)

    def test_suffix_accumulates_correctly_across_many_instances(self, tmp_path):
        """5 instances → _2, _3, _4, _5 (no _2_3 cascade from copy_foreign dedup)."""
        master = build_master_with_fields(["borrower_name"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 5)):
            result = asm.assemble(master, [ProductType.PIACI_HITEL], 5, 1,
                                  output_path=tmp_path / "out.pdf")
        names = set(read_field_values(result.output_path).keys())
        assert names == {"borrower_name", "borrower_name_2", "borrower_name_3",
                         "borrower_name_4", "borrower_name_5"}

    def test_original_master_pdf_not_mutated(self, tmp_path):
        """The source master must remain unchanged (field names intact)."""
        master = build_master_with_fields(["borrower_name"])
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 3)):
            asm.assemble(master, [ProductType.PIACI_HITEL], 3, 1,
                         output_path=tmp_path / "out.pdf")
        # Master still has exactly one borrower_name field.
        assert set(read_field_values(master).keys()) == {"borrower_name"}


# ---------------------------------------------------------------------------
# 2. Role classification helper
# ---------------------------------------------------------------------------

class TestRoleClassification:
    @pytest.mark.parametrize("section, expected", [
        ("sza_ig_tarsigenylő_2", INSTANCE_ROLE_CO_BORROWER),
        ("tarsados_adatlap", INSTANCE_ROLE_CO_BORROWER),
        ("ingatlan_adatlap_2", INSTANCE_ROLE_PROPERTY),
        ("kezes_adatlap_2", "guarantor"),
        ("haszonelvező_adatlap_2", "beneficiary"),
        ("unknown_section_2", INSTANCE_ROLE_CO_BORROWER),  # default
    ])
    def test_classify_instance_role(self, section, expected):
        assert _classify_instance_role(section) == expected


# ---------------------------------------------------------------------------
# 3. Pipeline instance-data routing (_apply_instance_data)
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline(tmp_path):
    return FormFillerPipeline(
        sf_client=SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data"),
        output_dir=tmp_path,
    )


def _deal_with_coborrowers(cities):
    participants = [
        Participant(
            role=ParticipantRole.BORROWER, name="Adós János",
            birth_date=date(1980, 1, 1),
            address=Address(zip_code="1000", city="Budapest", street="Utca", house_number="1"),
        )
    ]
    for i, city in enumerate(cities, start=1):
        participants.append(Participant(
            role=ParticipantRole.CO_BORROWER, name=f"Társ {i}",
            birth_date=date(1981, 1, 1),
            address=Address(zip_code="2000", city=city, street="Utca", house_number=str(i)),
        ))
    return DealData(
        deal_id="C6-TEST",
        loan=LoanDetails(loan_amount=10_000_000, loan_term_months=240),
        participants=participants,
    )


def _tars_mapping():
    """'-társ' field names → Contact.* (co-borrower routing for instance 1)."""
    return MappingConfig(
        bank_name="OTP", form_name="t", form_type="acroform",
        fields=[
            RecognizedField("SZA_IG_név-társ", "Név", FieldType.TEXT,
                            "Contact.Name", MappingConfidence.HIGH, 1),
            RecognizedField("SZA_IG_település-társ", "Település", FieldType.TEXT,
                            "Contact.MailingCity", MappingConfidence.HIGH, 1),
        ],
    )


class TestApplyInstanceData:
    def test_suffixed_keys_get_nth_coborrower_data(self, pipeline):
        deal = _deal_with_coborrowers(["Miskolc", "Debrecen", "Pécs"])
        mapping = _tars_mapping()
        # instance 1 → co_borrowers[0], instance 2 → co_borrowers[1], ...
        instance_fields = [
            InstanceFieldMap(section="sza_ig_tarsigenylő_2", instance=2,
                             role=INSTANCE_ROLE_CO_BORROWER,
                             base_fields=["SZA_IG_név-társ", "SZA_IG_település-társ"]),
            InstanceFieldMap(section="sza_ig_tarsigenylő_3", instance=3,
                             role=INSTANCE_ROLE_CO_BORROWER,
                             base_fields=["SZA_IG_név-társ", "SZA_IG_település-társ"]),
        ]
        fd = pipeline._prepare_field_data(deal, mapping)
        fd = pipeline._apply_instance_data(fd, deal, mapping, instance_fields)

        # instance 1 (base) = co_borrowers[0]
        assert fd["SZA_IG_név-társ"] == "Társ 1"
        assert fd["SZA_IG_település-társ"] == "Miskolc"
        # instance 2 = co_borrowers[1]
        assert fd["SZA_IG_név-társ_2"] == "Társ 2"
        assert fd["SZA_IG_település-társ_2"] == "Debrecen"
        # instance 3 = co_borrowers[2]
        assert fd["SZA_IG_név-társ_3"] == "Társ 3"
        assert fd["SZA_IG_település-társ_3"] == "Pécs"

    def test_property_instances_get_nth_property_data(self, pipeline):
        props = [
            Property(address=Address(zip_code="3000", city="Eger", street="U", house_number="1"),
                     parcel_number="1", property_type=PropertyType.APARTMENT),
            Property(address=Address(zip_code="4000", city="Győr", street="U", house_number="2"),
                     parcel_number="2", property_type=PropertyType.HOUSE),
        ]
        deal = DealData(
            deal_id="C6-PROP",
            loan=LoanDetails(loan_amount=1, loan_term_months=12),
            participants=[Participant(
                role=ParticipantRole.BORROWER, name="B",
                address=Address(zip_code="1", city="c", street="s", house_number="1"))],
            properties=props,
        )
        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[RecognizedField("IA_település", "Település", FieldType.TEXT,
                                    "Lead.Ingatlan_telepules__c", MappingConfidence.HIGH, 1)],
        )
        instance_fields = [
            InstanceFieldMap(section="ingatlan_adatlap_2", instance=2,
                             role=INSTANCE_ROLE_PROPERTY, base_fields=["IA_település"]),
        ]
        fd = pipeline._prepare_field_data(deal, mapping)
        fd = pipeline._apply_instance_data(fd, deal, mapping, instance_fields)
        # instance 1 = properties[0], instance 2 = properties[1]
        assert fd["IA_település"] == "Eger"
        assert fd["IA_település_2"] == "Győr"

    def test_instance_beyond_available_data_is_skipped(self, pipeline):
        """Instance 4 when only 3 co-borrowers exist → no _4 key (no crash)."""
        deal = _deal_with_coborrowers(["A", "B"])  # only 2 co-borrowers
        mapping = _tars_mapping()
        instance_fields = [
            InstanceFieldMap(section="x_4", instance=4, role=INSTANCE_ROLE_CO_BORROWER,
                             base_fields=["SZA_IG_név-társ"]),
        ]
        fd = pipeline._prepare_field_data(deal, mapping)
        before = dict(fd)
        fd = pipeline._apply_instance_data(fd, deal, mapping, instance_fields)
        # instance 4 → co_borrowers[3] does not exist → key absent
        assert "SZA_IG_név-társ_4" not in fd
        assert fd == before  # base data untouched

    def test_fragment_inherited_by_instanced_field(self, pipeline):
        """C8 fragment on a base field applies to its _N instance copy too."""
        deal = _deal_with_coborrowers(["X"])
        deal.co_borrowers[0].birth_date = date(1990, 7, 8)
        mapping = MappingConfig(
            bank_name="OTP", form_name="t", form_type="acroform",
            fields=[
                RecognizedField("SZA_IG_szül_év-társ", "Év", FieldType.DATE,
                                "Contact.Birthdate", MappingConfidence.HIGH, 1, fragment="year"),
            ],
        )
        instance_fields = [
            InstanceFieldMap(section="sza_ig_tarsigenylő_2", instance=2,
                             role=INSTANCE_ROLE_CO_BORROWER,
                             base_fields=["SZA_IG_szül_év-társ"]),
        ]
        fd = pipeline._prepare_field_data(deal, mapping)
        fd = pipeline._apply_instance_data(fd, deal, mapping, instance_fields)
        # No co_borrowers[1] → _2 empty, but the base (instance 1) shows the year fragment.
        assert fd["SZA_IG_szül_év-társ"] == "1990"
        assert "SZA_IG_szül_év-társ_2" not in fd  # no 2nd co-borrower


# ---------------------------------------------------------------------------
# 4. End-to-end: assemble → fill → distinct values per instance
# ---------------------------------------------------------------------------

class TestEndToEndMultiInstance:
    def test_three_coborrowers_fill_distinctly(self, tmp_path):
        """Full flow: assemble master with 3 template copies, fill, read back.

        Verifies the C6 requirement end-to-end: 3 co-borrowers → 3 copies of the
        template page → distinct borrower_name / _2 / _3 fields filled with
        DIFFERENT data.
        """
        master = build_master_with_fields(["SZA_IG_név-társ", "SZA_IG_település-társ"])
        deal = _deal_with_coborrowers(["Miskolc", "Debrecen", "Pécs"])
        mapping = _tars_mapping()
        pipeline = FormFillerPipeline(
            sf_client=SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data"),
            output_dir=tmp_path,
        )
        asm = DocumentAssembler()
        with patch.object(DocumentAssembler, "_build_page_plan",
                          lambda self, p, np, npr: duplicate_page_plan(1, 3)):
            assembly = asm.assemble(master, [ProductType.PIACI_HITEL], 3, 1,
                                    output_path=tmp_path / "assembled.pdf")

        fd = pipeline._prepare_field_data(deal, mapping)
        fd = pipeline._apply_instance_data(fd, deal, mapping, assembly.instance_fields)
        out, _filled, _skipped = pipeline._fill_pdf(assembly.output_path, deal, fd, mapping, instance_fields=None)

        vals = read_field_values(out)
        # Each instance holds a DIFFERENT co-borrower — the core C6 guarantee.
        assert vals["SZA_IG_név-társ"] == "Társ 1"
        assert vals["SZA_IG_név-társ_2"] == "Társ 2"
        assert vals["SZA_IG_név-társ_3"] == "Társ 3"
        assert vals["SZA_IG_település-társ"] == "Miskolc"
        assert vals["SZA_IG_település-társ_2"] == "Debrecen"
        assert vals["SZA_IG_település-társ_3"] == "Pécs"
        # No two name fields share a value.
        names = [vals["SZA_IG_név-társ"], vals["SZA_IG_név-társ_2"], vals["SZA_IG_név-társ_3"]]
        assert len(set(names)) == 3

    def test_no_instance_data_when_not_master(self, tmp_path):
        """A non-master (e.g. sample) PDF is filled directly, no assembly/instances."""
        # The 3-page sample PDF has no duplicated pages → no instance fields.
        sample = PROJECT_ROOT / "samples" / "acroform_sample.pdf"
        if not sample.exists():
            pytest.skip("acroform_sample.pdf not present")
        pipeline = FormFillerPipeline(
            sf_client=SalesforceClient(mock_mode=True, mock_data_dir=PROJECT_ROOT / "samples" / "dummy_data"),
            output_dir=tmp_path,
        )
        # instance_fields=None (default) → _fill_pdf does not touch instances.
        # Just ensure it runs without the assembler path.
        from src.ai.field_recognizer import MappingConfig
        mapping = MappingConfig.load(PROJECT_ROOT / "src" / "mapping" / "otp_acroform_mapping.json")
        deal = _deal_with_coborrowers(["Miskolc"])
        fd = pipeline._prepare_field_data(deal, mapping)
        out, _filled, _skipped = pipeline._fill_pdf(sample, deal, fd, mapping, instance_fields=None)
        assert Path(out).exists()

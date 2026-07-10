"""
Tesztek a DocumentAssembler-hez.

Updated for the reconciled ProductType enum (PLAN_project_upload.md §7.2):
CSOK / CSOK_PLUSZ / AFA_VISSZATERITES / HITELKARTYA / VIDEKI_FELUJITAS
were replaced by SZABADFELHASZNALASU and ERTEKBECSLES. PIACI_HITEL now has
its own product sections (pages 83-90).
"""
import pytest
from pathlib import Path
from src.engine.document_assembler import (
    DocumentAssembler, ProductType,
    BASE_SECTIONS, PRODUCT_SECTIONS, TARSADOS_SECTION,
)


class TestPagePlan:
    """Oldaltervrajz tesztek (PDF nélkül, csak logika)."""

    def setup_method(self):
        self.assembler = DocumentAssembler()

    def test_piaci_hitel_base_pages(self):
        """Piaci hitel alap → 44 oldal (30 alap + 6 társadós + 8 piaci_hitel)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 44

    def test_single_participant_no_tarsados(self):
        """1 szereplő → nincs társigénylő, nincs társadós szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=1, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" not in summary["sections"]
        assert "tarsados_adatlap" not in summary["sections"]
        assert "fedlap" in summary["sections"]
        assert "sza_ig_igenylő" in summary["sections"]
        # 26 alap + 8 piaci_hitel
        assert summary["total_pages"] == 34

    def test_three_participants_extra_pages(self):
        """3 szereplő → extra társigénylő szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=3, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" in summary["sections"]
        assert "sza_ig_tarsigenylő_2" in summary["sections"]
        # 40 (base 3 szem) + 8 piaci_hitel
        assert summary["total_pages"] == 48

    def test_two_properties_extra_pages(self):
        """2 ingatlan → dupla ingatlan adatlap."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=2
        )
        assert "ingatlan_adatlap_1" in summary["sections"]
        assert "ingatlan_adatlap_2" in summary["sections"]
        # 39 (base 2 ing) + 8 piaci_hitel
        assert summary["total_pages"] == 47

    def test_szabadfelhasznalasu_adds_sections(self):
        """Szabadfelhasználású hitel → alap + szab_hitel (6 oldal)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.SZABADFELHASZNALASU], num_participants=2, num_properties=1
        )
        assert "szab_hitel" in summary["sections"]
        assert summary["total_pages"] == 42  # 36 + 6

    def test_otthon_start_adds_sections(self):
        """Otthon Start → alap + otthon_start (14 oldal)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.OTTHON_START], num_participants=2, num_properties=1
        )
        assert "otthon_start" in summary["sections"]
        assert summary["total_pages"] == 50  # 36 + 14

    def test_combo_szabad_otthon_start(self):
        """Szabadfelhasználású + Otthon Start kombó."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.SZABADFELHASZNALASU, ProductType.OTTHON_START],
            num_participants=2, num_properties=1,
        )
        assert "szab_hitel" in summary["sections"]
        assert "otthon_start" in summary["sections"]
        assert summary["total_pages"] == 56  # 36 + 6 + 14

    def test_combo_piaci_szabad(self):
        """Piaci hitel + Szabadfelhasználású kombó."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL, ProductType.SZABADFELHASZNALASU],
            num_participants=2, num_properties=1,
        )
        assert "piaci_hitel" in summary["sections"]
        assert "szab_hitel" in summary["sections"]
        assert summary["total_pages"] == 50  # 36 + 8 + 6

    def test_ertekbecsles_no_extra_sections(self):
        """Előzetes értékbecslés → nincs master szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.ERTEKBECSLES], num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 36  # csak alap + társadós

    def test_complex_scenario(self):
        """Komplex: Piaci + Szabad + Otthon Start, 3 szereplő, 2 ingatlan."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL, ProductType.SZABADFELHASZNALASU, ProductType.OTTHON_START],
            num_participants=3, num_properties=2
        )
        # Alap(3 szem, 2 ing): 30 + 6(társ) + 4(extra társ) + 3(extra ing) = 43
        # + Piaci: 8 + Szabad: 6 + Otthon: 14 = 28
        # Total = 43 + 28 = 71
        assert summary["total_pages"] == 71

    def test_no_duplicate_sections(self):
        """Ugyanaz a termék kétszer → nem duplikálja a szekciót."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.OTTHON_START, ProductType.OTTHON_START],
            num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 50  # 36 + 14, not doubled


class TestProductType:
    """ProductType enum tesztek."""

    def test_all_products_have_sections(self):
        """Minden terméknek van szekció definíciója."""
        for product in ProductType:
            assert product in PRODUCT_SECTIONS

    def test_piaci_hitel_has_sections(self):
        """Piaci hitel = piaci_hitel szekció (83-90)."""
        assert PRODUCT_SECTIONS[ProductType.PIACI_HITEL] == [("piaci_hitel", 83, 90)]

    def test_ertekbecsles_empty_sections(self):
        """Előzetes értékbecslés = üres szekció lista (no master pages)."""
        assert PRODUCT_SECTIONS[ProductType.ERTEKBECSLES] == []

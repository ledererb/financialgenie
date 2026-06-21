"""
Tesztek a DocumentAssembler-hez.
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
        """Piaci hitel alap → 36 oldal (30 alap + 6 társadós)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 36

    def test_single_participant_no_tarsados(self):
        """1 szereplő → nincs társigénylő, nincs társadós szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=1, num_properties=1
        )
        # Nincs sza_ig_tarsigenylő és tarsados_adatlap
        assert "sza_ig_tarsigenylő_1" not in summary["sections"]
        assert "tarsados_adatlap" not in summary["sections"]
        # Alap szekciók megmaradnak
        assert "fedlap" in summary["sections"]
        assert "sza_ig_igenylő" in summary["sections"]
        assert summary["total_pages"] == 26  # 30 - 4 (társ) = 26

    def test_three_participants_extra_pages(self):
        """3 szereplő → extra társigénylő szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=3, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" in summary["sections"]
        assert "sza_ig_tarsigenylő_2" in summary["sections"]
        # +4 oldal a 2. társigénylőtől
        assert summary["total_pages"] == 40

    def test_two_properties_extra_pages(self):
        """2 ingatlan → dupla ingatlan adatlap."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=2
        )
        assert "ingatlan_adatlap_1" in summary["sections"]
        assert "ingatlan_adatlap_2" in summary["sections"]
        # +3 oldal a 2. ingatlantól
        assert summary["total_pages"] == 39

    def test_csok_plusz_adds_sections(self):
        """CSOK Plusz → alap + csok_plusz + csok_plusz_kiegészítő."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.CSOK_PLUSZ], num_participants=2, num_properties=1
        )
        assert "csok_plusz" in summary["sections"]
        assert "csok_plusz_kiegesz" in summary["sections"]
        assert summary["total_pages"] == 48  # 36 + 7 + 5

    def test_otthon_start_adds_sections(self):
        """Otthon Start → alap + otthon_start (14 oldal)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.OTTHON_START], num_participants=2, num_properties=1
        )
        assert "otthon_start" in summary["sections"]
        assert summary["total_pages"] == 50  # 36 + 14

    def test_combo_csok_plusz_otthon_start(self):
        """CSOK Plusz + Otthon Start kombó."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.CSOK_PLUSZ, ProductType.OTTHON_START],
            num_participants=2, num_properties=1
        )
        assert "csok_plusz" in summary["sections"]
        assert "otthon_start" in summary["sections"]
        assert summary["total_pages"] == 62  # 36 + 12 + 14

    def test_csok_plus_afa(self):
        """CSOK + ÁFA kombó."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.CSOK, ProductType.AFA_VISSZATERITES],
            num_participants=2, num_properties=1
        )
        assert "csok_nyilatkozat" in summary["sections"]
        assert "afa_nyilatkozat" in summary["sections"]
        assert summary["total_pages"] == 56  # 36 + 12 + 8

    def test_complex_scenario(self):
        """Komplex: CSOK Plusz + Otthon Start, 3 szereplő, 2 ingatlan."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.CSOK_PLUSZ, ProductType.OTTHON_START],
            num_participants=3, num_properties=2
        )
        # Alap: 30 + társ szekció: 6 + extra társigénylő: 4 + extra ingatlan: 3
        # + CSOK Plusz: 7+5 + Otthon Start: 14
        assert summary["total_pages"] == 69

    def test_no_duplicate_sections(self):
        """Ugyanaz a termék kétszer → nem duplikálja a szekciót."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.CSOK_PLUSZ, ProductType.CSOK_PLUSZ],
            num_participants=2, num_properties=1
        )
        # Nem dupla
        assert summary["total_pages"] == 48


class TestProductType:
    """ProductType enum tesztek."""

    def test_all_products_have_sections(self):
        """Minden terméknek van szekció definíciója."""
        for product in ProductType:
            assert product in PRODUCT_SECTIONS

    def test_piaci_hitel_has_no_extra_sections(self):
        """Piaci hitel = üres szekció lista."""
        assert PRODUCT_SECTIONS[ProductType.PIACI_HITEL] == []

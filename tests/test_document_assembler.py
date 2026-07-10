"""
Tesztek a DocumentAssembler-hez.

Updated section map a v5 master PDF valós tartalma alapján:
- BASE_SECTIONS: meghatalmazas → munkaltato_adatlap (11-12), komment javítások
- PRODUCT_SECTIONS: PIACI_HITEL és SZABADFELHASZNALASU [] (base KTKA-t használnak),
  csak OTTHON_START-nak van saját oldala (69-82, 14 oldal)
- TARSADOS_SECTION eltávolítva (fiktív volt — a 31-36 valójában CSOK TKA)

Base oldalak (1 participant, 1 property):
  fedlap(1) + sza_ig_igenylo(4) + sza_esz(1) + munkaltato_adatlap(2) +
  ingatlan_adatlap(3) + hitelfeltetelek(6) + altalanos_nyilatkozatok(9) = 26
  (sza_ig_tarsigenylo csak 2+ participantnál)
"""
import pytest
from pathlib import Path
from src.engine.document_assembler import (
    DocumentAssembler, ProductType,
    BASE_SECTIONS, PRODUCT_SECTIONS,
)


class TestPagePlan:
    """Oldaltervrajz tesztek (PDF nélkül, csak logika)."""

    def setup_method(self):
        self.assembler = DocumentAssembler()

    def test_single_participant_base_pages(self):
        """1 szereplő, 1 ingatlan, Piaci hitel → 26 alap oldal (nincs társigénylő)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=1, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" not in summary["sections"]
        assert "fedlap" in summary["sections"]
        assert "sza_ig_igenylő" in summary["sections"]
        assert "munkaltato_adatlap" in summary["sections"]
        # Piaci hitelnek nincs saját oldala (base KTKA-t használ)
        assert summary["total_pages"] == 26

    def test_two_participants_adds_tarsigenylo(self):
        """2 szereplő → társigénylő szekció hozzáadódik (4 oldal)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" in summary["sections"]
        # 26 base + 4 társigénylő
        assert summary["total_pages"] == 30

    def test_three_participants_extra_pages(self):
        """3 szereplő → extra társigénylő szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=3, num_properties=1
        )
        assert "sza_ig_tarsigenylő_1" in summary["sections"]
        assert "sza_ig_tarsigenylő_2" in summary["sections"]
        # 26 base + 4 + 4 (két extra társigénylő)
        assert summary["total_pages"] == 34

    def test_two_properties_extra_pages(self):
        """2 ingatlan → dupla ingatlan adatlap."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=2
        )
        assert "ingatlan_adatlap_1" in summary["sections"]
        assert "ingatlan_adatlap_2" in summary["sections"]
        # 30 (2 participant, 1 property) + 3 (extra property)
        assert summary["total_pages"] == 33

    def test_piaci_hitel_no_product_sections(self):
        """Piaci hitel → nincs saját product szekció (base KTKA-t használ)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL], num_participants=2, num_properties=1
        )
        assert "piaci_hitel" not in summary["sections"]
        assert summary["total_pages"] == 30  # csak base

    def test_szabadfelhasznalasu_no_product_sections(self):
        """Szabadfelhasználású hitel → nincs saját product szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.SZABADFELHASZNALASU], num_participants=2, num_properties=1
        )
        assert "szab_hitel" not in summary["sections"]
        assert summary["total_pages"] == 30  # csak base

    def test_otthon_start_adds_sections(self):
        """Otthon Start → alap + otthon_start (14 oldal)."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.OTTHON_START], num_participants=2, num_properties=1
        )
        assert "otthon_start" in summary["sections"]
        # 30 base + 14 otthon_start
        assert summary["total_pages"] == 44

    def test_combo_piaci_otthon_start(self):
        """Piaci hitel + Otthon Start kombó."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL, ProductType.OTTHON_START],
            num_participants=2, num_properties=1,
        )
        # Piaci hitel nem ad saját oldalt
        assert "piaci_hitel" not in summary["sections"]
        assert "otthon_start" in summary["sections"]
        # 30 base + 14 otthon_start
        assert summary["total_pages"] == 44

    def test_ertekbecsles_no_extra_sections(self):
        """Előzetes értékbecslés → nincs master szekció."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.ERTEKBECSLES], num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 30  # csak base + társigénylő

    def test_complex_scenario(self):
        """Komplex: Piaci + Otthon Start, 3 szereplő, 2 ingatlan."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.PIACI_HITEL, ProductType.OTTHON_START],
            num_participants=3, num_properties=2
        )
        # Base(3 szem, 2 ing): 26 + 4(társ) + 4(extra társ) + 3(extra ing) = 37
        # + Otthon Start: 14
        # Total = 37 + 14 = 51
        assert summary["total_pages"] == 51

    def test_no_duplicate_sections(self):
        """Ugyanaz a termék kétszer → nem duplikálja a szekciót."""
        summary = self.assembler.get_assembly_summary(
            [ProductType.OTTHON_START, ProductType.OTTHON_START],
            num_participants=2, num_properties=1
        )
        assert summary["total_pages"] == 44  # 30 + 14, not doubled


class TestProductType:
    """ProductType enum tesztek."""

    def test_all_products_have_sections(self):
        """Minden terméknek van szekció definíciója."""
        for product in ProductType:
            assert product in PRODUCT_SECTIONS

    def test_piaci_hitel_empty_sections(self):
        """Piaci hitel → üres szekció lista (base KTKA-t használ)."""
        assert PRODUCT_SECTIONS[ProductType.PIACI_HITEL] == []

    def test_szabadfelhasznalasu_empty_sections(self):
        """Szabadfelhasználású hitel → üres szekció lista."""
        assert PRODUCT_SECTIONS[ProductType.SZABADFELHASZNALASU] == []

    def test_ertekbecsles_empty_sections(self):
        """Előzetes értékbecslés = üres szekció lista (no master pages)."""
        assert PRODUCT_SECTIONS[ProductType.ERTEKBECSLES] == []

    def test_otthon_start_has_sections(self):
        """Otthon Start = otthon_start szekció (69-82)."""
        assert PRODUCT_SECTIONS[ProductType.OTTHON_START] == [("otthon_start", 69, 82)]

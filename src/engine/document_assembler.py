"""
FinancialGenie – OTP Master Dokumentum Darabolás (Document Assembly)

A 97 oldalas master PDF-ből a terméktípus, szereplőszám és ingatlaszám
alapján összeállítja a végleges dokumentumot.

Az OTP belső rendszere ezt automatikusan végzi – mi replikáljuk.
"""

import logging
from enum import Enum
from pathlib import Path
from typing import Optional

import pikepdf

logger = logging.getLogger(__name__)


class ProductType(str, Enum):
    """OTP hiteltermék típusok."""
    PIACI_HITEL = "piaci_hitel"
    CSOK = "csok"
    CSOK_PLUSZ = "csok_plusz"
    OTTHON_START = "otthon_start"
    AFA_VISSZATERITES = "afa_visszaterites"
    VIDEKI_FELUJITAS = "videki_felujitas"
    HITELKARTYA = "hitelkartya"


# ============================================================
# Szekció-térkép: oldaltartományok (1-indexed, inclusive)
# ============================================================

# Alap szekciók – minden ügylethez szükségesek
BASE_SECTIONS = {
    "fedlap": (1, 1),
    "sza_ig_igenylő": (2, 5),        # Személyi adatlap – igénylő
    "sza_ig_tarsigenylő": (6, 9),     # Személyi adatlap – társigénylő
    "sza_esz": (10, 10),              # Eszköz nyilatkozat
    "meghatalmazas": (11, 12),        # Meghatalmazás
    "ingatlan_adatlap": (13, 15),     # Ingatlan adatlap
    "hitelfeltetelek": (16, 21),      # KTKA / hitelfeltételek
    "altalanos_nyilatkozatok": (22, 30),  # Általános nyilatkozatok
}

# Termékspecifikus szekciók
PRODUCT_SECTIONS = {
    ProductType.PIACI_HITEL: [],  # Csak az alap
    ProductType.CSOK: [
        ("csok_nyilatkozat", 37, 48),
    ],
    ProductType.AFA_VISSZATERITES: [
        ("afa_nyilatkozat", 49, 56),
    ],
    ProductType.CSOK_PLUSZ: [
        ("csok_plusz", 57, 63),
        ("csok_plusz_kiegesz", 64, 68),
    ],
    ProductType.OTTHON_START: [
        ("otthon_start", 69, 82),
    ],
    ProductType.HITELKARTYA: [
        ("hitelkartya", 83, 85),
    ],
    ProductType.VIDEKI_FELUJITAS: [
        ("videki_felujitas", 86, 93),
        ("videki_kiegesz", 94, 97),
    ],
}

# Társadós bővített adatlap – ha ≥2 szereplő
TARSADOS_SECTION = ("tarsados_adatlap", 31, 36)


class DocumentAssembler:
    """
    OTP master dokumentum automatikus darabolása.
    
    A 97 oldalas PDF-ből a terméktípus, szereplőszám és
    ingatlaszám alapján összeállítja a végleges dokumentumot.
    """

    def assemble(
        self,
        master_pdf: Path,
        products: list[ProductType],
        num_participants: int = 2,
        num_properties: int = 1,
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        Összeállítja a végleges PDF-et a master dokumentumból.

        Args:
            master_pdf: A 97 oldalas master PDF elérési útja
            products: Igényelt termékek listája
            num_participants: Szereplők száma (adós + adóstárs + további)
            num_properties: Ingatlanok száma
            output_path: Kimeneti PDF elérési útja (opcionális)

        Returns:
            A kész PDF elérési útja
        """
        master_pdf = Path(master_pdf)
        if not master_pdf.exists():
            raise FileNotFoundError(f"Master PDF nem található: {master_pdf}")

        # 1. Összeállítandó oldalak meghatározása
        page_plan = self._build_page_plan(products, num_participants, num_properties)

        logger.info(f"📋 Document Assembly:")
        logger.info(f"   Master: {master_pdf.name} (97 oldal)")
        logger.info(f"   Termékek: {[p.value for p in products]}")
        logger.info(f"   Szereplők: {num_participants}, Ingatlanok: {num_properties}")
        logger.info(f"   Tervezett oldalak: {len(page_plan)}")

        # 2. PDF összeállítás
        with pikepdf.open(master_pdf) as src_pdf:
            dst_pdf = pikepdf.Pdf.new()

            for entry in page_plan:
                page_idx = entry["page"] - 1  # 0-indexed
                if 0 <= page_idx < len(src_pdf.pages):
                    dst_pdf.pages.append(src_pdf.pages[page_idx])
                else:
                    logger.warning(f"   ⚠️ {entry['section']}: oldal {entry['page']} nincs a master-ben")

            # AcroForm másolása ha van
            if "/AcroForm" in src_pdf.Root:
                dst_pdf.Root["/AcroForm"] = dst_pdf.copy_foreign(src_pdf.Root["/AcroForm"])

            # Kimenet
            if output_path is None:
                product_str = "_".join(p.value for p in products)
                output_path = master_pdf.parent.parent / "output" / f"OTP_{product_str}_{num_participants}szem_{num_properties}ing.pdf"

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            dst_pdf.save(output_path)

        logger.info(f"   ✅ Kész: {output_path.name} ({len(page_plan)} oldal, {output_path.stat().st_size / 1024:.0f} KB)")

        return output_path

    def _build_page_plan(
        self,
        products: list[ProductType],
        num_participants: int,
        num_properties: int,
    ) -> list[dict]:
        """
        Oldaltervrajz összeállítása.
        
        Returns:
            Lista dict-ekből: {"page": int, "section": str, "note": str}
        """
        plan = []

        # === 1. ALAP SZEKCIÓK ===

        # Fedlap
        self._add_section(plan, "fedlap", *BASE_SECTIONS["fedlap"])

        # Személyi adatlap – igénylő (mindig)
        self._add_section(plan, "sza_ig_igenylő", *BASE_SECTIONS["sza_ig_igenylő"])

        # Személyi adatlap – társigénylő (ha ≥2 szereplő)
        if num_participants >= 2:
            self._add_section(plan, "sza_ig_tarsigenylő_1", *BASE_SECTIONS["sza_ig_tarsigenylő"])

        # Extra társigénylők – SZA_IG-társ oldalak sokszorosítva
        for extra_idx in range(2, num_participants):
            self._add_section(
                plan,
                f"sza_ig_tarsigenylő_{extra_idx}",
                *BASE_SECTIONS["sza_ig_tarsigenylő"],
                note=f"Extra társigénylő #{extra_idx}",
            )

        # Eszköz nyilatkozat
        self._add_section(plan, "sza_esz", *BASE_SECTIONS["sza_esz"])

        # Meghatalmazás
        self._add_section(plan, "meghatalmazas", *BASE_SECTIONS["meghatalmazas"])

        # Ingatlan adatlap – sokszorosítva
        for prop_idx in range(num_properties):
            self._add_section(
                plan,
                f"ingatlan_adatlap_{prop_idx + 1}",
                *BASE_SECTIONS["ingatlan_adatlap"],
                note=f"Ingatlan #{prop_idx + 1}" if num_properties > 1 else None,
            )

        # Hitelfeltételek
        self._add_section(plan, "hitelfeltetelek", *BASE_SECTIONS["hitelfeltetelek"])

        # Általános nyilatkozatok
        self._add_section(plan, "altalanos_nyilatkozatok", *BASE_SECTIONS["altalanos_nyilatkozatok"])

        # === 2. TÁRSADÓS SZEKCIÓ (ha ≥2 szereplő) ===
        if num_participants >= 2:
            self._add_section(plan, TARSADOS_SECTION[0], TARSADOS_SECTION[1], TARSADOS_SECTION[2])

        # === 3. TERMÉKSPECIFIKUS SZEKCIÓK ===
        added_sections = set()
        for product in products:
            sections = PRODUCT_SECTIONS.get(product, [])
            for section_name, start, end in sections:
                if section_name not in added_sections:
                    self._add_section(plan, section_name, start, end)
                    added_sections.add(section_name)

        return plan

    def _add_section(
        self,
        plan: list[dict],
        section_name: str,
        start_page: int,
        end_page: int,
        note: Optional[str] = None,
    ):
        """Egy szekció oldalait hozzáadja a tervhez."""
        for page_num in range(start_page, end_page + 1):
            plan.append({
                "page": page_num,
                "section": section_name,
                "note": note or section_name,
            })

    def get_assembly_summary(
        self,
        products: list[ProductType],
        num_participants: int = 2,
        num_properties: int = 1,
    ) -> dict:
        """Összesítő a tervezett darabolásról (preview, PDF létrehozása nélkül)."""
        plan = self._build_page_plan(products, num_participants, num_properties)

        # Szekciók csoportosítása
        sections = {}
        for entry in plan:
            sec = entry["section"]
            if sec not in sections:
                sections[sec] = {"pages": [], "note": entry["note"]}
            sections[sec]["pages"].append(entry["page"])

        return {
            "total_pages": len(plan),
            "products": [p.value for p in products],
            "num_participants": num_participants,
            "num_properties": num_properties,
            "sections": {
                name: {
                    "pages": f"{min(data['pages'])}-{max(data['pages'])}",
                    "count": len(data["pages"]),
                    "note": data["note"],
                }
                for name, data in sections.items()
            },
        }

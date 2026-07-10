"""
FinancialGenie – OTP Master Dokumentum Darabolás (Document Assembly)

A 97 oldalas master PDF-ből a terméktípus, szereplőszám és ingatlaszám
alapján összeállítja a végleges dokumentumot.

Az OTP belső rendszere ezt automatikusan végzi – mi replikáljuk.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import pikepdf

logger = logging.getLogger(__name__)


class ProductType(str, Enum):
    """OTP hiteltermék típusok (reconciled with real Drive layout)."""
    PIACI_HITEL = "piaci_hitel"
    SZABADFELHASZNALASU = "szabadfelhasznalasu_hitel"
    OTTHON_START = "otthon_start"
    ERTEKBECSLES = "elozetes_ertekbecsles_megrendeles"

    # --- Legacy values (may be needed later, kept for reference) ---
    # CSOK = "csok"
    # CSOK_PLUSZ = "csok_plusz"
    # AFA_VISSZATERITES = "afa_visszaterites"
    # VIDEKI_FELUJITAS = "videki_felujitas"
    # HITELKARTYA = "hitelkartya"


# ============================================================
# Szekció-térkép: oldaltartományok (1-indexed, inclusive)
# ============================================================

# Alap szekciók – minden ügylethez szükségesek
# Oldaltartományok a v5 master PDF valós tartalma alapján ellenőrizve.
BASE_SECTIONS = {
    "fedlap": (1, 1),
    "sza_ig_igenylő": (2, 5),        # Személyi adatlap – igénylő
    "sza_ig_tarsigenylő": (6, 9),     # Személyi adatlap – társigénylő
    "sza_esz": (10, 10),              # Személyi adatlap – egyéb szereplő (fedezet tulajdonosa/haszonélvezője)
    "munkaltato_adatlap": (11, 12),   # Munkáltatói és vállalkozói adatlap (MA IG)
    "ingatlan_adatlap": (13, 15),     # Ingatlan adatlap
    "hitelfeltetelek": (16, 21),      # KTKA Kölcsönkérelmi adatlap (16-19) + FA Finanszírozás (20-21)
    "altalanos_nyilatkozatok": (22, 30),  # Általános nyilatkozat
}

# Termékspecifikus szekciók.
# A v5 master PDF-ben a 31-68. oldalak (CSOK/CSOK Plusz/ÁFA) és a 83-97.
# oldalak (Hitelkártya/Vidéki Felújítás) más termékekhez tartoznak, amelyek
# nincsenek a jelenlegi 4 ProductType enumban. A Piaci hitel és a
# Szabadfelhasználású hitel a base KTKA-t használják (16-21), így nincs
# saját oldaltartományuk. Ezeket az oldalakat a manuális section editorral
# lehet termékekhez rendelni.
PRODUCT_SECTIONS = {
    ProductType.PIACI_HITEL: [],          # base KTKA-t használ, nincs saját oldala
    ProductType.SZABADFELHASZNALASU: [],  # base KTKA-t használ, nincs saját oldala
    ProductType.OTTHON_START: [("otthon_start", 69, 82)],
    ProductType.ERTEKBECSLES: [],  # no master pages
}


# ============================================================
# C6 — AcroForm field renaming for duplicated pages
# ============================================================
# When a page template (e.g. the társigénylő or ingatlan-adatlap pages) is
# duplicated for multiple instances (several co-borrowers / properties), the
# copied AcroForm widget annotations would otherwise all share ONE field
# object — so every instance would display the SAME value. FIX C6 renames the
# fields on each DUPLICATE page with an ``_N`` suffix (instance 2 → ``_2``,
# instance 3 → ``_3`` …) so they become independent, fillable fields.
#
# Role classification of an instanced page group, used by the pipeline to map
# an instance number to the right data (co-borrowers[1], properties[1], …).
INSTANCE_ROLE_CO_BORROWER = "co_borrower"
INSTANCE_ROLE_PROPERTY = "property"
INSTANCE_ROLE_GUARANTOR = "guarantor"
INSTANCE_ROLE_BENEFICIARY = "beneficiary"


def _classify_instance_role(section: str) -> str:
    """Best-effort role for a duplicated section based on its section name."""
    s = section.lower()
    if "ingatlan" in s:
        return INSTANCE_ROLE_PROPERTY
    if "kezes" in s or "guarantor" in s:
        return INSTANCE_ROLE_GUARANTOR
    if "haszonelvez" in s or "beneficiary" in s:
        return INSTANCE_ROLE_BENEFICIARY
    # társigénylő / társadós / anything participant-like → co-borrower
    return INSTANCE_ROLE_CO_BORROWER


@dataclass
class InstanceFieldMap:
    """Describes the AcroForm fields created for ONE duplicated page instance.

    Attributes:
        section: The page-plan section label (e.g. ``sza_ig_tarsigenylő_2``).
        instance: Instance number (≥2; instance 1 keeps the original names).
        role: Data role used to resolve instance data
            (``co_borrower`` | ``property`` | ``guarantor`` | ``beneficiary``).
        base_fields: Original (unsuffixed) PDF field names defined on this
            template page; the renamed copies are ``f"{name}_{instance}"``.
    """
    section: str
    instance: int
    role: str
    base_fields: list[str] = field(default_factory=list)


@dataclass
class AssemblyResult:
    """Result of ``DocumentAssembler.assemble``.

    Attributes:
        output_path: Path to the assembled PDF.
        instance_fields: One entry per DUPLICATED instance (instance ≥ 2),
            describing the renamed ``_N`` fields so the pipeline can route the
            Nth participant's / property's data to them.
    """
    output_path: Path
    instance_fields: list[InstanceFieldMap] = field(default_factory=list)


class DocumentAssembler:
    """
    OTP master dokumentum automatikus darbolása.
    
    A 97 oldalas PDF-ből a terméktípus, szereplőszám és
    ingatlaszám alapján összeállítja a végleges dokumentumot.
    """

    #: A teljes (darabolatlan) OTP master PDF oldalainak száma.
    #: Erre a magic-number-re épül a `is_master_pdf` detektálás.
    MASTER_PDF_PAGE_COUNT: int = 97

    def is_master_pdf(self, pdf_path: Path) -> bool:
        """
        Eldönti, hogy egy PDF a master (darabolandó) dokumentum-e.

        A master PDF jellemzője, hogy oldalszáma eléri vagy meghaladja a
        `MASTER_PDF_PAGE_COUNT`-ot (a teljes OTP nyomtatvány-csomag).
        Ha ennél kevesebb oldal van, már összeállított / egyedi nyomtatvány.
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            return False
        import pikepdf
        try:
            with pikepdf.open(pdf_path) as pdf:
                return len(pdf.pages) >= self.MASTER_PDF_PAGE_COUNT
        except Exception:
            return False

    def assemble(
        self,
        master_pdf: Path,
        products: list[ProductType],
        num_participants: int = 2,
        num_properties: int = 1,
        output_path: Optional[Path] = None,
    ) -> AssemblyResult:
        """
        Összeállítja a végleges PDF-et a master dokumentumból.

        Args:
            master_pdf: A 97 oldalas master PDF elérési útja
            products: Igényelt termékek listája
            num_participants: Szereplők száma (adós + adóstárs + további)
            num_properties: Ingatlanok száma
            output_path: Kimeneti PDF elérési útja (opcionális)

        Returns:
            ``AssemblyResult`` — a kész PDF útvonala, plusz a C6
            ``instance_fields`` leírás, amely a duplikált oldalakon
            átnevezett ``_N`` AcroForm mezőket sorolja fel, hogy a pipeline a
            megfelelő (N-edik) példány adatait tudja hozzájuk rendelni.
        """
        master_pdf = Path(master_pdf)
        if not master_pdf.exists():
            raise FileNotFoundError(f"Master PDF nem található: {master_pdf}")

        # 1. Összeállítandó oldalak meghatározása
        page_plan = self._build_page_plan(products, num_participants, num_properties)

        logger.info("📋 Document Assembly:")
        logger.info("   Master: %s (%d oldal)", master_pdf.name, self.MASTER_PDF_PAGE_COUNT)
        logger.info("   Termékek: %s", [p.value for p in products])
        logger.info("   Szereplők: %d, Ingatlanok: %d", num_participants, num_properties)
        logger.info("   Tervezett oldalak: %d", len(page_plan))

        # 2. PDF összeállítás
        with pikepdf.open(master_pdf) as src_pdf:
            # FIX M9 — abort early (instead of silently truncating) when the
            # page plan references master pages that do not exist. A truncated
            # assembly would otherwise drop whole sections (e.g. a product's
            # declaration pages) while the caller believes the output is
            # complete. Validate against the real source page count.
            src_page_count = len(src_pdf.pages)
            out_of_range = [
                e for e in page_plan if not (1 <= e["page"] <= src_page_count)
            ]
            if out_of_range:
                offenders = ", ".join(
                    f"{e['section']}={e['page']}" for e in out_of_range
                )
                raise ValueError(
                    f"DocumentAssembly oldal tartomány hiba: a(z) '{master_pdf.name}' "
                    f"csak {src_page_count} oldalt tartalmaz, de a terv "
                    f"{len(out_of_range)} oldalt ezen kívül hivatkoz "
                    f"({offenders}). A master PDF nem kompatibilis a kért "
                    f"termék/szereplő kombinációval."
                )

            dst_pdf = pikepdf.Pdf.new()
            has_acroform = "/AcroForm" in src_pdf.Root

            # C6: hányadik alkalommal használunk egy adott forrás-oldalt.
            # Az 1. használat = eredeti mezőnevek, a 2+, 3+ … használatok =
            # átnevezett ``_N`` mezők (független AcroForm mező-objektumok).
            page_instance_count: dict[int, int] = {}
            # Duplikált példányokon létrehozott, átnevezett mező-objektumok,
            # amelyeket a végén az AcroForm /Fields tömbhöz kell adni.
            renamed_field_objs: list = []
            # A pipeline számára: példányonként a szerep + az eredeti mezőnevek.
            instance_fields: list[InstanceFieldMap] = []

            for entry in page_plan:
                page_idx = entry["page"] - 1  # 0-indexed
                # FIX M9 — never silently skip an out-of-range page. The
                # whole plan was validated up front, so this is a defensive
                # guard; if we ever reach it we abort loudly instead of
                # producing a truncated PDF the caller would mistake for
                # complete.
                if not (0 <= page_idx < len(src_pdf.pages)):
                    raise ValueError(
                        f"{entry['section']}: oldal {entry['page']} nincs a "
                        f"master-ben ({len(src_pdf.pages)} oldal). "
                        f"A DocumentAssembly nem folytatható."
                    )

                page_instance_count[page_idx] = page_instance_count.get(page_idx, 0) + 1
                instance = page_instance_count[page_idx]

                if instance == 1:
                    # Első használat — eredeti mezőnevek maradnak.
                    dst_pdf.pages.append(src_pdf.pages[page_idx])
                else:
                    # C6: duplikált oldal — másoljuk az oldalt (független
                    # widget+mező objektumokkal), és nevezzük át a mezőket.
                    new_page = dst_pdf.copy_foreign(src_pdf.pages[page_idx].obj)
                    dst_pdf.pages.append(pikepdf.Page(new_page))
                    if has_acroform:
                        base_names = self._rename_page_fields(
                            dst_pdf,
                            new_page,
                            instance,
                            src_pdf,
                            page_idx,
                            renamed_field_objs,
                        )
                        if base_names:
                            instance_fields.append(InstanceFieldMap(
                                section=entry["section"],
                                instance=instance,
                                role=_classify_instance_role(entry["section"]),
                                base_fields=base_names,
                            ))

            # AcroForm másolása ha van (globális beállítások: NeedAppearances,
            # DR/DA betűtípusok stb.), majd a duplikált példányok mezőinek
            # hozzáadása a /Fields tömbhöz.
            if has_acroform:
                dst_pdf.Root["/AcroForm"] = dst_pdf.copy_foreign(src_pdf.Root["/AcroForm"])
                acroform = dst_pdf.Root["/AcroForm"]
                if "/Fields" not in acroform:
                    acroform[pikepdf.Name("/Fields")] = pikepdf.Array()
                for nf in renamed_field_objs:
                    acroform["/Fields"].append(nf)

            # Kimenet
            if output_path is None:
                product_str = "_".join(p.value for p in products)
                output_path = master_pdf.parent.parent / "output" / f"OTP_{product_str}_{num_participants}szem_{num_properties}ing.pdf"

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            dst_pdf.save(output_path)

        renamed_total = sum(len(im.base_fields) for im in instance_fields)
        logger.info(
            f"   ✅ Kész: {output_path.name} ({len(page_plan)} oldal, "
            f"{output_path.stat().st_size / 1024:.0f} KB)"
            + (f", {renamed_total} átnevezett _N mező ({len(instance_fields)} példány)" if instance_fields else "")
        )

        return AssemblyResult(output_path=output_path, instance_fields=instance_fields)

    @staticmethod
    def _source_page_field_names(src_pdf, page_idx: int) -> list[str]:
        """A forrás-oldal widget annotációinak mezőnevei, pozíció-sorrendben.

        A ``src_pdf`` sosem módosul a másolás során, így ezeket az eredeti
        neveket biztonságosan használhatjuk a ``_N`` suffix építéséhez —
        ellentétben a már másolt (és eseten átnevezett) cél-objektumokkal,
        amelyeket a pikepdf ``copy_foreign`` deduplikálhat.
        """
        names: list[str] = []
        page = src_pdf.pages[page_idx].obj
        annots = page.get("/Annots")
        if not annots:
            return names
        for w in annots:
            try:
                if str(w.get("/Subtype", "")) != "/Widget":
                    names.append("")
                    continue
                parent = w.get("/Parent")
                field_obj = parent if parent is not None else w
                names.append(str(field_obj.get("/T", "")))
            except Exception:
                names.append("")
        return names

    @staticmethod
    def _rename_page_fields(
        dst_pdf,
        page,
        instance: int,
        src_pdf,
        src_page_idx: int,
        renamed_field_objs: list,
    ) -> list[str]:
        """Átnevezi egy duplikált cél-oldal AcroForm mezőit ``_N`` suffix-szel.

        Minden widget-hez egy FRISSS, független mező-objektumot hoz létre
        (nem a pikepdf ``copy_foreign`` által deduplikáltat használjuk, mert az
        ugyanazon forrás-oldal többszöri másolásakor alias-olná a már
        átnevezett mezőket). A mező lényeges attribútumait (``/FT``, ``/Ff``,
        ``/DA``, ``/MaxLen``, ``/Q`` …) az eredetiből másolja, a ``/V``
        értéket törli, a ``/T`` nevet ``<eredeti>_<instance>``-re állítja,
        majd a widget ``/Parent``-jét az új mezőre irányítja.

        Args:
            dst_pdf: Cél PDF (már tartalmazza a másolt ``page``-et).
            page: A duplikált cél-oldal objektum (már ``dst_pdf``-ben).
            instance: Példányszám (≥2).
            src_pdf: Nyitott forrás-PDF (eredeti nevek olvasásához).
            src_page_idx: Forrás-oldal 0-indexelt sorszáma.
            renamed_field_objs: Gyűjtő lista az új mező-objektumoknak.

        Returns:
            A sikeresen átnevezett EREDETI mezőnevek listája.
        """
        # Eredeti nevek a módosítatlan forrásból, pozíció szerint.
        orig_names = DocumentAssembler._source_page_field_names(src_pdf, src_page_idx)

        annots = page.get("/Annots")
        if not annots:
            return []
        renamed: list[str] = []
        widgets = list(annots)
        for pos, w in enumerate(widgets):
            try:
                if str(w.get("/Subtype", "")) != "/Widget":
                    continue
                base = orig_names[pos] if pos < len(orig_names) else ""
                if not base:
                    continue
                parent = w.get("/Parent")
                orig_field = parent if parent is not None else w
                new_name = f"{base}_{instance}"

                # Új, független mező-objektum a lényeges attribútumokkal.
                field_attrs = {"/T": pikepdf.String(new_name)}
                for key in ("/FT", "/Ff", "/DA", "/DV", "/DS", "/MaxLen", "/Q", "/Tu"):
                    if key in orig_field:
                        field_attrs[key] = orig_field[key]
                field_attrs["/Kids"] = pikepdf.Array([w])
                new_field = dst_pdf.make_indirect(pikepdf.Dictionary(field_attrs))

                # Widget az új mezőre mutasson; /AP törlése, hogy a viewer
                # újra renderelje.
                w[pikepdf.Name("/Parent")] = new_field
                if "/AP" in w:
                    del w["/AP"]

                renamed_field_objs.append(new_field)
                renamed.append(base)
            except Exception as exc:
                logger.warning("   ⚠️ mező-átnevezés sikertelen (instance %d): %s", instance, exc)
        return renamed

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

        # Egyéb szereplő (fedezet tulajdonosa/haszonélvezője)
        self._add_section(plan, "sza_esz", *BASE_SECTIONS["sza_esz"])

        # Munkáltatói és vállalkozói adatlap
        self._add_section(plan, "munkaltato_adatlap", *BASE_SECTIONS["munkaltato_adatlap"])

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

        # === TERMÉKSPECIFIKUS SZEKCIÓK ===
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

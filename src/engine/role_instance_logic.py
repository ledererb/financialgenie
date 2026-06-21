"""
FinancialGenie – Szerepkör és példányszám logika

Ez a modul felelős annak meghatározásáért, hogy egy adott banknyomtatvány
kitöltésekor mely oldalakat kell generálni, hány példányban, és melyik
szereplőhöz / ingatlanhoz tartoznak.

Banki nyomtatványok jellemzői:
- Egyes oldalak GLOBÁLISAK (pl. hitel összefoglaló) – egyszer kell kitölteni.
- Egyes oldalak SZEREPLŐNKÉNT ismétlődnek (pl. személyes adatok blokk).
- Egyes oldalak INGATLANONKÉNT ismétlődnek (pl. fedezeti ingatlan adatlap).
- Egyes oldalak SZEREPLŐ + INGATLAN kombinációnként ismétlődnek.

A RoleInstancePlanner a DealData és a FormStructure alapján összeállítja
a GenerationPlan-t, amely megmondja, pontosan milyen oldalakat kell
generálni a végleges PDF-ben.

Szűrés:
- Inaktív (is_active=False, „kuka") szereplők kiszűrésre kerülnek.
- Szerepkör szerinti szűrés is lehetséges (pl. egy oldal csak adósra vonatkozik).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from src.models.canonical_model import DealData, Participant, ParticipantRole, Property

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Oldal típusok (scope)
# ---------------------------------------------------------------------------

class PageScope(str, Enum):
    """Meghatározza, hogy egy oldal milyen hatókörrel ismétlődik."""
    GLOBAL = "global"
    """Globális oldal – egyszer kell kitölteni (pl. hitel összefoglaló)."""

    PER_PARTICIPANT = "per_participant"
    """Szereplőnként ismétlődik (pl. személyes adatok oldal)."""

    PER_PROPERTY = "per_property"
    """Ingatlanonként ismétlődik (pl. fedezeti adatlap)."""

    PER_PARTICIPANT_PROPERTY = "per_participant_property"
    """Szereplő × ingatlan kombinációnként ismétlődik."""


# ---------------------------------------------------------------------------
# Konfigurációs struktúrák
# ---------------------------------------------------------------------------

@dataclass
class PageConfig:
    """
    Egy sablon-oldal konfigurációja.

    Attributes:
        template_page_index: Az oldal indexe a sablon PDF-ben (0-alapú).
        scope: Az oldal hatóköre (global / per_participant / per_property).
        role_filter: Ha megadva, csak az adott szerepű szereplőkhöz generálódik.
                     Pl. [ParticipantRole.BORROWER] → csak adósokhoz.
        copies: Fix példányszám (override a dinamikus kalkuláció felett).
                Ha None, a scope alapján kalkulálódik.
        label: Emberi olvasásra szánt név (debug/log célra).
    """
    template_page_index: int
    scope: PageScope = PageScope.GLOBAL
    role_filter: list[ParticipantRole] | None = None
    copies: int | None = None
    label: str = ""


@dataclass
class FormStructure:
    """
    Egy nyomtatvány struktúra-definíciója.

    Meghatározza, hogy a sablon PDF egyes oldalai milyen hatókörrel
    és szerep-szűrővel ismétlődnek.

    Attributes:
        form_id: Nyomtatvány azonosító (pl. "otp_hitelkérelem_v3").
        pages: Oldal-konfigurációk listája.
        total_template_pages: A sablon PDF összes oldalszáma.
    """
    form_id: str
    pages: list[PageConfig] = field(default_factory=list)
    total_template_pages: int = 0


# ---------------------------------------------------------------------------
# Generálási terv (output)
# ---------------------------------------------------------------------------

@dataclass
class PageSpec:
    """
    Egy konkrét oldal a generált PDF-ben.

    Attributes:
        template_page_index: A forrásoldal indexe a sablon PDF-ben (0-alapú).
        participant_index: A szereplő indexe a DealData.active_participants-ben
                          (None ha globális oldal).
        participant: A hozzárendelt szereplő objektum (None ha globális).
        property_index: Az ingatlan indexe a DealData.properties-ben
                       (None ha nem ingatlan-specifikus oldal).
        property_ref: A hozzárendelt ingatlan objektum (None ha nem releváns).
        copies: Ennyi másolatot kell generálni ebből az oldalból.
        label: Emberi olvasásra szánt leírás.
    """
    template_page_index: int
    participant_index: int | None = None
    participant: Participant | None = None
    property_index: int | None = None
    property_ref: Property | None = None
    copies: int = 1
    label: str = ""

    @property
    def context_label(self) -> str:
        """Kontextus leírás a debug loghoz."""
        parts = [f"oldal={self.template_page_index}"]
        if self.participant is not None:
            parts.append(
                f"szereplő={self.participant.name} ({self.participant.role.value})"
            )
        if self.property_ref is not None:
            parts.append(f"ingatlan={self.property_ref.parcel_number}")
        if self.copies > 1:
            parts.append(f"×{self.copies}")
        return " | ".join(parts)


@dataclass
class GenerationPlan:
    """
    A teljes generálási terv – a végleges PDF oldalsorrendje.

    Attributes:
        form_id: A nyomtatvány azonosítója.
        deal_id: Az ügylet azonosítója.
        page_specs: A generálandó oldalak sorrendje.
        warnings: Figyelmeztetések (pl. nincs adós, üres ingatlanlista).
    """
    form_id: str
    deal_id: str
    page_specs: list[PageSpec] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        """Összes generálandó oldal (copies-szal együtt)."""
        return sum(spec.copies for spec in self.page_specs)

    @property
    def summary(self) -> str:
        """Emberi olvasásra szánt összegzés."""
        return (
            f"Generálási terv [{self.form_id}] – ügylet: {self.deal_id} | "
            f"oldalak: {len(self.page_specs)} specifikáció, "
            f"összesen {self.total_pages} oldal | "
            f"figyelmeztetések: {len(self.warnings)}"
        )


# ---------------------------------------------------------------------------
# Tervező (Planner)
# ---------------------------------------------------------------------------

class RoleInstancePlanner:
    """
    Szerepkör- és példányszám tervező.

    A FormStructure és a DealData alapján összeállítja a GenerationPlan-t,
    amely megmondja, milyen oldalakat kell generálni.

    Szabályok:
    1. GLOBAL oldalak: egyszer generálódnak.
    2. PER_PARTICIPANT oldalak: minden aktív (nem kuka) szereplőhöz
       egyszer, a role_filter szerint szűrve.
    3. PER_PROPERTY oldalak: minden ingatlanhoz egyszer.
    4. PER_PARTICIPANT_PROPERTY oldalak: minden (aktív szereplő × ingatlan)
       kombinációhoz egyszer, role_filter szűréssel.

    Használat:
        planner = RoleInstancePlanner()
        plan = planner.plan(deal, form_structure)
        for spec in plan.page_specs:
            print(spec.context_label)
    """

    def plan(self, deal: DealData, form_structure: FormStructure) -> GenerationPlan:
        """
        Generálási terv összeállítása.

        Args:
            deal: A kanonikus ügyletadatok.
            form_structure: A nyomtatvány struktúra-definíciója.

        Returns:
            GenerationPlan a generálandó oldalak sorrendjével.
        """
        plan = GenerationPlan(
            form_id=form_structure.form_id,
            deal_id=deal.deal_id,
        )

        # Aktív szereplők (kuka kiszűrve)
        active_participants = deal.active_participants
        properties = deal.properties

        # Figyelmeztetések
        if not active_participants:
            plan.warnings.append(
                "Nincsenek aktív szereplők az ügyletben – "
                "szereplőnkénti oldalak nem generálódnak."
            )
        if not properties:
            plan.warnings.append(
                "Nincsenek ingatlanok az ügyletben – "
                "ingatlanonkénti oldalak nem generálódnak."
            )

        borrowers = deal.borrowers
        if not borrowers:
            plan.warnings.append(
                "Nincs adós (BORROWER) az ügyletben – "
                "ez valószínűleg hibás adat."
            )

        # Oldalak generálása a konfigurációk alapján
        for page_config in form_structure.pages:
            specs = self._generate_specs_for_page(
                page_config, active_participants, properties, plan
            )
            plan.page_specs.extend(specs)

        logger.info(plan.summary)
        for warning in plan.warnings:
            logger.warning("Generálási figyelmeztetés: %s", warning)

        return plan

    def _generate_specs_for_page(
        self,
        page_config: PageConfig,
        active_participants: list[Participant],
        properties: list[Property],
        plan: GenerationPlan,
    ) -> list[PageSpec]:
        """Egy adott oldal-konfigurációból PageSpec-eket generál."""
        specs: list[PageSpec] = []

        if page_config.scope == PageScope.GLOBAL:
            specs.append(self._make_global_spec(page_config))

        elif page_config.scope == PageScope.PER_PARTICIPANT:
            specs.extend(
                self._make_per_participant_specs(
                    page_config, active_participants, plan
                )
            )

        elif page_config.scope == PageScope.PER_PROPERTY:
            specs.extend(
                self._make_per_property_specs(page_config, properties, plan)
            )

        elif page_config.scope == PageScope.PER_PARTICIPANT_PROPERTY:
            specs.extend(
                self._make_per_participant_property_specs(
                    page_config, active_participants, properties, plan
                )
            )

        return specs

    @staticmethod
    def _make_global_spec(page_config: PageConfig) -> PageSpec:
        """Globális (egyszer generálandó) oldal specifikáció."""
        return PageSpec(
            template_page_index=page_config.template_page_index,
            copies=page_config.copies or 1,
            label=page_config.label or f"Globális oldal {page_config.template_page_index}",
        )

    def _make_per_participant_specs(
        self,
        page_config: PageConfig,
        active_participants: list[Participant],
        plan: GenerationPlan,
    ) -> list[PageSpec]:
        """Szereplőnként ismétlődő oldalak generálása."""
        specs: list[PageSpec] = []
        filtered = self._filter_by_role(
            active_participants, page_config.role_filter
        )

        if not filtered and page_config.role_filter:
            roles_str = ", ".join(r.value for r in page_config.role_filter)
            plan.warnings.append(
                f"Oldal {page_config.template_page_index} "
                f"({page_config.label}): nincs szereplő a szűrt "
                f"szerepkörökben ({roles_str})."
            )

        for idx, participant in enumerate(filtered):
            # Az index az active_participants-ben keresendő
            participant_idx = active_participants.index(participant)
            specs.append(PageSpec(
                template_page_index=page_config.template_page_index,
                participant_index=participant_idx,
                participant=participant,
                copies=page_config.copies or 1,
                label=(
                    page_config.label
                    or f"Személyes oldal – {participant.name}"
                ),
            ))

        return specs

    def _make_per_property_specs(
        self,
        page_config: PageConfig,
        properties: list[Property],
        plan: GenerationPlan,
    ) -> list[PageSpec]:
        """Ingatlanonként ismétlődő oldalak generálása."""
        specs: list[PageSpec] = []

        if not properties:
            plan.warnings.append(
                f"Oldal {page_config.template_page_index} "
                f"({page_config.label}): nincs ingatlan az ügyletben."
            )

        for idx, prop in enumerate(properties):
            specs.append(PageSpec(
                template_page_index=page_config.template_page_index,
                property_index=idx,
                property_ref=prop,
                copies=page_config.copies or 1,
                label=(
                    page_config.label
                    or f"Ingatlan oldal – {prop.parcel_number}"
                ),
            ))

        return specs

    def _make_per_participant_property_specs(
        self,
        page_config: PageConfig,
        active_participants: list[Participant],
        properties: list[Property],
        plan: GenerationPlan,
    ) -> list[PageSpec]:
        """Szereplő × ingatlan kombinációkhoz generálandó oldalak."""
        specs: list[PageSpec] = []
        filtered = self._filter_by_role(
            active_participants, page_config.role_filter
        )

        if not filtered:
            plan.warnings.append(
                f"Oldal {page_config.template_page_index} "
                f"({page_config.label}): nincs releváns szereplő."
            )
            return specs

        if not properties:
            plan.warnings.append(
                f"Oldal {page_config.template_page_index} "
                f"({page_config.label}): nincs ingatlan az ügyletben."
            )
            return specs

        for participant in filtered:
            participant_idx = active_participants.index(participant)
            for prop_idx, prop in enumerate(properties):
                specs.append(PageSpec(
                    template_page_index=page_config.template_page_index,
                    participant_index=participant_idx,
                    participant=participant,
                    property_index=prop_idx,
                    property_ref=prop,
                    copies=page_config.copies or 1,
                    label=(
                        page_config.label
                        or f"{participant.name} × {prop.parcel_number}"
                    ),
                ))

        return specs

    @staticmethod
    def _filter_by_role(
        participants: list[Participant],
        role_filter: list[ParticipantRole] | None,
    ) -> list[Participant]:
        """
        Szereplők szűrése szerepkör szerint.

        Ha role_filter None, minden szereplő visszaadódik.
        """
        if role_filter is None:
            return participants
        return [p for p in participants if p.role in role_filter]

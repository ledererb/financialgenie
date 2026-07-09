"""
7-szabályos checkbox-kitöltő motor (Round-2 design).

A motor KERETFÜGGETLEN: sosem hivatkozik konkrét keretprogramra.
Az egyetlen elágazás a ``rule_type ∈ 1..7`` értéken történik. A
keretspecifikus döntés (melyik pont melyik szabályt használja) a
mapping ``points`` konfigurációjában van, nem a kódban.

Ez a fájl tartalmazza a spec döntési fájának egyetlen forrását.

Lásd:
- docs/hiteligenyles_kitoltesi_szabalyok.md  (szabályrendszer)
- REVIEW_ROUND2_FILL_ENGINE_DESIGN.md        (design)

Szabályok:
  1 — feltétel nélküli, teljes körű pipázás
  2 — szerepkörös, többszörös pipázás
  3 — részleges blokk-pipázás (csak egy alblokk)
  4 — kétlépéses / összetett pipázás (kiinduló érték + záró szerepkör-pipa)
  5 — termékfüggő feltételes pipázás
  6 — hitelcél-függő feltételes pipázás
  7 — többblokkos, blokkonként eltérő pipázási minta
"""
from dataclasses import dataclass
from typing import Callable, Optional


# ──────────────────────────────────────────────────────────────────────────
# Adatmodell
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class RuleContext:
    """Minden, amire egy szabálynak szüksége van az ügyletből — kívülről kapja."""

    active_participants: list           # Participant objektumok
    products: list                      # deal.products (str lista)
    loan_purpose: Optional[str]         # loan.loan_purpose
    product_name: Optional[str]         # loan.product_name
    canonical_values: dict              # {canonical_field: value}

    def role(self, p) -> str:
        """Szerepkör értéke: enum -> .value, egyébként str()."""
        return p.role.value if hasattr(p.role, "value") else str(p.role)


@dataclass
class Point:
    """Egy sorszámozott nyomtatványpont (checkbox-csoport)."""

    point_id: str
    framework: str
    blocks: list                         # [{"block_id", "members": [pdf_field...]}]
    rule_type: int                       # 1..7
    params: dict


@dataclass
class PointResult:
    """Egy pont kitöltésének eredménye."""

    point_id: str
    ticks: dict                          # {pdf_field_name: "igen" | "nem"}


# (Point, RuleContext) -> PointResult
RuleFn = Callable[["Point", RuleContext], PointResult]

# Egyetlen dispatch tábla: rule_type -> függvény
RULE_REGISTRY: dict = {}


def rule(rule_type: int):
    """Dekorátor: regisztrálja a függvényt az adott szabálytípushoz."""

    def deco(fn: RuleFn) -> RuleFn:
        RULE_REGISTRY[rule_type] = fn
        return fn

    return deco


# ──────────────────────────────────────────────────────────────────────────
# Segítők
# ──────────────────────────────────────────────────────────────────────────


def _all_members(point: "Point") -> list:
    """A pont minden blokkjának minden mezője, sorrendben."""
    members = []
    for blk in point.blocks:
        for m in blk.get("members", []):
            members.append(m)
    return members


# ──────────────────────────────────────────────────────────────────────────
# A hét szabály
# ──────────────────────────────────────────────────────────────────────────


@rule(1)  # default, feltétel nélküli teljes körű pipázás
def _r1_check_all(point: "Point", ctx: RuleContext) -> PointResult:
    value = "igen" if ctx.active_participants else "nem"
    ticks = {m: value for m in _all_members(point)}
    return PointResult(point.point_id, ticks)


@rule(2)  # szerepkörös, többszörös pipázás
def _r2_role_multicheck(point: "Point", ctx: RuleContext) -> PointResult:
    role_to_ticks = point.params.get("role_to_ticks", {})  # {role_value: [pdf_field...]}
    ticks = {m: "nem" for m in _all_members(point)}
    for p in ctx.active_participants:
        for member in role_to_ticks.get(ctx.role(p), []):
            ticks[member] = "igen"
    return PointResult(point.point_id, ticks)


@rule(3)  # részleges blokk-pipázás — csak egy alblokk aktív
def _r3_partial_block(point: "Point", ctx: RuleContext) -> PointResult:
    active = point.params.get("active_block")              # block_id
    value = "igen" if ctx.active_participants else "nem"
    ticks = {m: "nem" for m in _all_members(point)}
    for blk in point.blocks:
        if blk.get("block_id") == active:
            for m in blk.get("members", []):
                ticks[m] = value
    return PointResult(point.point_id, ticks)


@rule(4)  # kétlépéses / összetett: kiinduló érték minden blokkban + záró szerepkör-pipa
def _r4_two_step(point: "Point", ctx: RuleContext) -> PointResult:
    step1_value = point.params.get("step1_value", "nem vagyok")
    closing_field = point.params.get("closing_field")     # pdf_field a záró pipához
    role_field = point.params.get("role_field")           # pdf_field a szerepkör-pipához
    closing_value = point.params.get("closing_value", "igen")

    reserved = {closing_field, role_field}
    ticks = {}
    for blk in point.blocks:
        for m in blk.get("members", []):
            if m in reserved:
                continue
            ticks[m] = step1_value
    # záró pipa: a role_field minden aktív félre pipa
    ticks[role_field] = "igen" if ctx.active_participants else "nem"
    ticks[closing_field] = closing_value
    return PointResult(point.point_id, ticks)


def _conditional(point: "Point", ctx: RuleContext, getter: Callable) -> PointResult:
    """Közös mag az 5-ös (termék) és 6-os (hitelcél) szabályokhoz."""
    cond_value = point.params.get("condition_value", "")
    current = str(getter(ctx) or "").strip().lower()
    target = str(cond_value).strip().lower()
    matches = current == target
    value = "igen" if matches and ctx.active_participants else "nem"
    ticks = {m: value for m in _all_members(point)}
    return PointResult(point.point_id, ticks)


@rule(5)  # termékfüggő feltételes pipázás
def _r5_product(point: "Point", ctx: RuleContext) -> PointResult:
    return _conditional(point, ctx, lambda c: c.product_name)


@rule(6)  # hitelcél-függő feltételes pipázás
def _r6_loan_purpose(point: "Point", ctx: RuleContext) -> PointResult:
    return _conditional(point, ctx, lambda c: c.loan_purpose)


@rule(7)  # többblokkos, blokkonként eltérő pipázási minta
def _r7_multi_block(point: "Point", ctx: RuleContext) -> PointResult:
    block_rules = point.params.get("block_rules", {})     # {block_id: {value}}
    active = ctx.active_participants
    ticks = {}
    for blk in point.blocks:
        sub = block_rules.get(blk.get("block_id"), {})
        val = sub.get("value", "igen")
        for m in blk.get("members", []):
            ticks[m] = val if active else "nem"
    return PointResult(point.point_id, ticks)


# ──────────────────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────────────────


def evaluate_point(point: "Point", ctx: RuleContext) -> PointResult:
    """A pont rule_type-ja szerint kikeresi és végrehajtja a szabályt."""
    fn = RULE_REGISTRY.get(point.rule_type)
    if fn is None:
        return PointResult(point.point_id, {})
    return fn(point, ctx)

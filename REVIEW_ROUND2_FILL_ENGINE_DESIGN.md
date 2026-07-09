# Round 2 — 7-Rule Checkbox Fill Engine: Design Proposal

A concrete design for implementing the rule system in `docs/hiteligenyles_kitoltesi_szabalyok.md`, integrated into the existing `_prepare_field_data` pipeline, **framework-independent** per the spec (ALAP / CSOK Plusz / Otthon Start differ only in *which point uses which rule*, not in rule semantics).

The goal: make `point_id → rule_type + params` an addressable, reusable, per-framework configuration, so the engine code never branches on framework name.

---

## 1. Why the current engine cannot do this

`_eval_fill_rule` (`src/main.py:488-539`) evaluates **one rule per PDF field** and returns **one scalar value** (`"igen"`/`"nem"`/`None`). It has no notion of:

- a **point** (a numbered question spanning many checkboxes),
- a **block** (a sub-group of checkboxes within a point),
- **multi-tick from a single party** (Rule 2: Adós → tick *two* boxes),
- **ordered steps** (Rule 4: step1 default + step2 role-tick),
- **per-block different logic** (Rule 7).

So Rules 3, 4, 7 are *structurally* impossible, Rule 2 only half-works, and Rules 5/6 are expressible but carry no "tick every party" semantics. We need a layer **above** the per-field evaluator.

---

## 2. Data model

### 2.1 New first-class entities (stored in the mapping JSON)

Add two optional top-level arrays to `MappingConfig` (additive — old mappings keep loading):

```jsonc
{
  "bank_name": "OTP",
  "form_name": "...",
  "form_type": "acroform",
  "fields": [ ... ],            // existing per-PDF-field records
  "character_groups": [ ... ],  // existing
  // ── NEW ──
  "points":   [ ... ],          // logical form points (the 7-rule targets)
  "rule_sets": [ ... ]          // reusable rule definitions, framework-tagged
}
```

#### `Point` — a numbered question on the form

```jsonc
{
  "point_id": "ALAP_8",                 // stable handle (the spec's point_id)
  "framework": "ALAP",                  // ALAP | CSOK_Plusz | Otthon_Start | "*"
  "label": "Adós szerepkörök",
  "page_number": 3,
  "blocks": [                           // ≥1 sub-blocks
    {
      "block_id": "ALAP_8.main",
      "members": [                      // PDF field names that belong to this block
        "SZA_IG_role_ados",
        "SZA_IG_role_fedezettulaj_1",
        "SZA_IG_role_adostars",
        "SZA_IG_role_fedezettulaj_2"
      ]
    }
  ],
  "rule_set": "rs_role_multicheck",     // → rule_sets[].rule_set_id  (the reusable logic)
  "params": {                           // per-point parameters (rule-specific)
    "role_to_ticks": {
      "adós":      ["SZA_IG_role_ados", "SZA_IG_role_fedezettulaj_1"],
      "adóstárs":  ["SZA_IG_role_adostars", "SZA_IG_role_fedezettulaj_2"]
    }
  }
}
```

#### `RuleSet` — the reusable, framework-agnostic logic

```jsonc
{
  "rule_set_id": "rs_role_multicheck",
  "rule_type": 2,                       // 1..7  ← the spec's rule_type
  "description": "Role-based multi-check",
  // rule_type-specific params live here too (defaults a point may override)
}
```

The **7 `rule_type`s are the only branching the engine ever does** — exactly the spec's decision tree. Frameworks never appear in `if` statements.

### 2.2 Python side (new module: `src/engine/fill_rules.py`)

Framework-independent. No imports from `main.py`, `salesforce_client`, or FastAPI.

```python
# src/engine/fill_rules.py
from dataclasses import dataclass, field
from typing import Callable, Optional

@dataclass
class RuleContext:
    """Everything a rule needs from the deal — passed in, never imported."""
    active_participants: list          # Participant objects
    products: list[str]                # deal.products
    loan_purpose: Optional[str]        # loan.loan_purpose
    product_name: Optional[str]        # loan.product_name
    canonical_values: dict             # {canonical_field: value} (borrower_data etc.)
    def role(self, p):                 # helper
        return p.role.value

@dataclass
class Point:
    point_id: str
    framework: str
    blocks: list[dict]
    rule_type: int
    params: dict

@dataclass
class PointResult:
    point_id: str
    ticks: dict[str, str]   # {pdf_field_name: "igen" | "nem"}

# each rule is a pure function: (Point, RuleContext) -> PointResult
RuleFn = Callable[[Point, RuleContext], PointResult]

RULE_REGISTRY: dict[int, RuleFn] = {}

def rule(rule_type: int):
    def deco(fn): RULE_REGISTRY[rule_type] = fn; return fn
    return deco
```

The seven implementations register themselves. This is the single source of truth and the only place the spec's decision tree lives.

---

## 3. The seven rule implementations (sketches)

```python
@rule(1)  # default, unconditional check-all
def _r1_check_all(point: Point, ctx: RuleContext) -> PointResult:
    ticks = {}
    for blk in point.blocks:
        for member in blk["members"]:
            ticks[member] = "igen" if ctx.active_participants else "nem"
    return PointResult(point.point_id, ticks)

@rule(2)  # role-based multi-check
def _r2_role_multicheck(point: Point, ctx: RuleContext) -> PointResult:
    role_to_ticks = point.params["role_to_ticks"]   # {role_value: [pdf_field...]}
    ticks = {}
    all_members = {m for blk in point.blocks for m in blk["members"]}
    ticks = {m: "nem" for m in all_members}
    for p in ctx.active_participants:
        for member in role_to_ticks.get(ctx.role(p), []):
            ticks[member] = "igen"
    return PointResult(point.point_id, ticks)

@rule(3)  # partial block — only block N
def _r3_partial_block(point: Point, ctx: RuleContext) -> PointResult:
    active = point.params["active_block"]            # block_id
    ticks = {m: "nem" for blk in point.blocks for m in blk["members"]}
    for blk in point.blocks:
        if blk["block_id"] == active:
            for m in blk["members"]:
                ticks[m] = "igen" if ctx.active_participants else "nem"
    return PointResult(point.point_id, ticks)

@rule(4)  # two-step compound: step1 default per block + step2 closing role-tick
def _r4_two_step(point: Point, ctx: RuleContext) -> PointResult:
    step1_value = point.params.get("step1_value", "nem vagyok")
    closing_field = point.params["closing_field"]    # pdf_field for the role-tick
    role_field = point.params["role_field"]          # pdf_field that carries each party's role
    ticks = {}
    for blk in point.blocks:
        for m in blk["members"]:
            if m in (closing_field, role_field):
                continue
            ticks[m] = step1_value
    # closing tick: tick role_field for every active party (one value; multi-copy handles N)
    ticks[role_field] = "igen" if ctx.active_participants else "nem"
    ticks[closing_field] = point.params.get("closing_value", "igen")
    return PointResult(point.point_id, ticks)

def _conditional(point: Point, ctx: RuleContext, getter) -> PointResult:
    # shared by rules 5 & 6 — only the condition source differs
    cond_value = point.params["condition_value"]
    matches = str(getter(ctx) or "").strip().lower() == str(cond_value).strip().lower()
    ticks = {m: ("igen" if matches and ctx.active_participants else "nem")
             for blk in point.blocks for m in blk["members"]}
    return PointResult(point.point_id, ticks)

@rule(5)  # product-conditional
def _r5_product(point: Point, ctx: RuleContext) -> PointResult:
    return _conditional(point, ctx, lambda c: c.product_name)

@rule(6)  # loan-purpose-conditional
def _r6_loan_purpose(point: Point, ctx: RuleContext) -> PointResult:
    return _conditional(point, ctx, lambda c: c.loan_purpose)

@rule(7)  # multi-block, per-block different pattern
def _r7_multi_block(point: Point, ctx: RuleContext) -> PointResult:
    # each block carries its own sub-rule in params["block_rules"]
    block_rules = point.params["block_rules"]        # {block_id: {type, value/...}}
    ticks = {}
    for blk in point.blocks:
        sub = block_rules.get(blk["block_id"], {})
        val = sub.get("value", "igen")
        for m in blk["members"]:
            ticks[m] = val if ctx.active_participants else "nem"
    return PointResult(point.point_id, ticks)
```

Rules 5 and 6 collapse to one parameterised "conditional" — exactly what the spec's open question #6 hints at ("értelmes lehet egy közös, paraméterezhető típusként kezelni").

---

## 4. Integration with `_prepare_field_data`

The engine plugs in **after** the existing per-field resolution and **before** the fill_rule loop. It is purely additive — a mapping with no `points` behaves exactly as today.

```python
# inside _prepare_field_data, after the existing groups/fill_rule sections:
points = getattr(mapping, "points", None) or []
if points:
    from src.engine.fill_rules import RuleContext, RULE_REGISTRY
    ctx = RuleContext(
        active_participants=deal.active_participants,
        products=deal.products,
        loan_purpose=loan.loan_purpose,
        product_name=loan.product_name,
        canonical_values={**borrower_data, **co_borrower_data, **loan_data, **prop_data},
    )
    for p_def in points:
        rt = p_def.get("rule_type") or self._lookup_rule_type(p_def.get("rule_set"))
        fn = RULE_REGISTRY.get(rt)
        if not fn:
            logger.warning("unknown rule_type %s on point %s", rt, p_def.get("point_id"))
            continue
        point = Point(point_id=p_def["point_id"], framework=p_def.get("framework","*"),
                      blocks=p_def["blocks"], rule_type=rt, params=p_def.get("params",{}))
        result = fn(point, ctx)
        field_data.update(result.ticks)   # point ticks win; logged if they override a canonical
```

**Key invariants:**
- The engine only writes `{pdf_field_name: "igen"|"nem"}` — the same shape `_prepare_field_data` already emits. No downstream change in `pdf_filler`.
- A point's ticks are logged when they overwrite a value the canonical path already set — surfaces mapping conflicts (e.g. a field that is both `canonical_field`-mapped and a block member).
- `RoleContext` is constructed from data already in scope; **no new SF coupling**.

**Framework filtering** happens at *config load*, not in the engine: pick the `Point`s whose `framework` matches `deal.products` (or `"*"`). The engine itself never reads `framework`.

---

## 5. How it stays framework-independent (the spec's core requirement)

1. **The engine (`fill_rules.py`) has zero framework references.** It branches only on `rule_type ∈ 1..7`. A grep for "ALAP"/"CSOK" in that file must always return nothing — make it a CI assertion.
2. **Frameworks differ only in the `points` config** — which `point_id` uses which `rule_type`. Adding Otthon Start means authoring a new `points` array, not editing Python.
3. **`points` are reusable across forms** via `rule_set` indirection: two forms that share "ALAP 8" point to the same `rule_set_id`.
4. **The decision tree is data, not code.** The spec's decision diagram (condition → multi-block → partial → two-step → role → default) maps 1:1 to `rule_type`; the engine is just a dispatch table.

This satisfies the spec's closing requirement: *"a fill engine-ben egy (pont azonosító → szabálytípus + paraméterek) hozzárendelésként reprezentálható"*.

---

## 6. Worked example — Rule 5 (product-conditional), end to end

**Scenario:** CSOK Plusz form, point "CSOK_3" should be ticked for every party **only if** the product is `CSOK Plusz Babaváró`.

### 6.1 Mapping JSON

```jsonc
{
  "points": [{
    "point_id": "CSOK_3",
    "framework": "CSOK_Plusz",
    "page_number": 2,
    "blocks": [{ "block_id": "CSOK_3.main",
                 "members": ["CSOK3_ados_igen", "CSOK3_adostars_igen"] }],
    "rule_type": 5,
    "params": { "condition_value": "CSOK Plusz Babaváró" }
  }]
}
```

### 6.2 Deal data

```python
deal.products == ["csok_plusz"]
deal.loan.product_name == "CSOK Plusz Babaváró"   # surfaced via Opportunity.Term_k__c
deal.active_participants == [borrower, coborrower]
```

### 6.3 Engine execution

1. `_prepare_field_data` builds `ctx`. `ctx.product_name = "CSOK Plusz Babaváró"`.
2. For point `CSOK_3`: `rule_type=5` → `_r5_product`.
3. `_conditional`: `"csok plusz babaváró" == "csok plusz babaváró"` → `matches=True`; parties exist → tick.
4. `PointResult.ticks = {"CSOK3_ados_igen": "igen", "CSOK3_adostars_igen": "igen"}`.
5. `field_data.update(ticks)` → both checkboxes set to `"igen"`.

### 6.4 Negative case

A deal with `product_name == "Piaci lakáshitel"`: `matches=False` → both members `"nem"` → checkboxes left unchecked. No code change between the two deals — only the deal data differs.

### 6.5 Cross-framework reuse

ALAP's equivalent point (`ALAP_9`) that should tick unconditionally reuses `rule_type=1`; a future "Évnyerő" point reuses `rule_type=5` with `condition_value="Évnyerő"`. Same seven functions, different config rows.

---

## 7. Phased rollout

| Phase | Scope | Deliverable | Depends on |
|---|---|---|---|
| **0** | Round-2 Gap 3a: make `fill_rule`/`checkbox_group` survive the field API | editor Rule 1/2/5/6 work today | nothing (15 min) |
| **1** | `src/engine/fill_rules.py` + registry + Rules 1,2,5,6 + unit tests | the common cases work end-to-end | Phase 0 |
| **2** | Rules 3,4,7 + `Point`/`Block` UI in the editor | full 7-rule coverage | Phase 1 |
| **3** | Convert OTP v5's 71 checkboxes into `points` (curation) | production form actually ticks | Phase 2 + Round-1 §3.3 decision |

Phase 0 alone (the 15-minute `FieldUpdate` fix) means Rules 1, 2, 5, 6 become usable *today* through the existing per-field `fill_rule` UI — without any new engine — because `_eval_fill_rule` already implements those four. The dedicated engine in Phase 1–2 is what adds Rules 3/4/7 and the reusable `point` model. **Recommend landing Phase 0 immediately** so the existing UI stops silently losing rules.

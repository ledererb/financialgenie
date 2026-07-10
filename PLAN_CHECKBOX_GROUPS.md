# PLAN — Checkbox-csoportok strukturált kezelése a FinancialGenie-ben

> **Státusz:** Tervezeti dokumentum (spec). NEM implementáció.
> **Dátum:** 2026-07-10
> **Érintett repo:** `/home/openclaw/.openclaw/workspace/financialgenie_review`
> **Hatás:** 7 fájl (1 Python AI, 1 Python backend, 1 Python engine/pipeline, 1 Python dataclass, 1 TS types, 2 React komponens)

---

## 1. Probléma újrafogalmazása

A `01_SZA_IG_Szemelyi_adatlap_Igenylo_2.pdf` tartalmaz **69 checkboxot 14 csoportban** (pl. a "Mi a legmagasabb iskolai végzettsége?" kérdés alatt 4 opció: Felsőfokú / Szakképesítés / Érettségi / 8 általános). Az AI felismerés (`src/ai/field_recognizer.py`) és a mapping JSON már tartalmazza a `checkbox_group` mezőt (`{group_id, match_value}` alakban), de **a valós mapping-ekben mindenhol `null`** — az AI nem tölti ki, mert a vizuális prompt nem kötelezi rá strukturáltan. Ennek következtében:

- A `field_recognizer.py:874-877` felismeri a `g`/`mv` kulcsokat az AI válaszából, de az AI ritkán adja meg őket.
- A `src/main.py:645-687` pont-motor (`fill_rules.py` rule_type 3 = kölcsönösen kizáró csoport) **sosem fut** ezen a nyomtatványon, mert `points: []` (a mapping-ben 0 pont van).
- Az opció→enum-érték leképezés (melyik checkbox = "Felsőfokú") **csak a `label`/`notes` szövegében van benne** ("Legmagasabb iskolai végzettség – Felsőfokú"), struktúrátlanul — a pipeline nem tudja automatikusan Point-tá alakítani.

**Cél:** strukturáltan rögzíteni csoportonként a kérdés szövegét (`group_label`), az opció emberi nevét (`option_label`) és a hozzá tartozó Salesforce picklist értéket (`option_value`), majd ebből automatikusan generálni a `fill_rules.Point` konfigurációt (rule_type 3), hogy a kitöltőmotor helyesen pipázza ki az adós tényleges végzettségéhez tartozó checkboxot.

---

## 2. Adatmodell változások

### 2.1 Meglévő állapot (referencia)

A `RecognizedField` dataclass (`src/ai/field_recognizer.py:353-371`) már tartalmaz:
```python
checkbox_group: Optional[dict] = None   # {"group_id": str, "match_value": str}
```
Ez round-trippel a `MappingConfig.to_dict()` / `from_dict()`-en (`field_recognizer.py:425, 453`), és a backend `mapping_service.py:117` is perzisztálja (`update_field` whitelistben a `checkbox_group` kulcs). A TS típus `MappingField.checkbox_group` (`frontend/src/types/index.ts:69`) szintén létezik.

**Probléma:** a `match_value` elnevezés kétértelmű (opció érték vs. egyezési feltétel), és hiányzik a kérdés szövege (`group_label`) valamint az opció magyar neve (`option_label`).

### 2.2 Döntés: a `checkbox_group` dict kiterjesztése

**NEM** új top-level mezőket adunk a `MappingField`-hez. Ehelyett a meglévő `checkbox_group` dict-et bővítjük 4 új kulccsal, és a `match_value`-t alias-ként megtartjuk (`option_value` preferált). Indoklás: egyetlen határeset a validáció, kevesebb séma-migráció, a backend whitelist már tartalmazza a `checkbox_group`-ot.

#### Új `checkbox_group` alak (teljes):

```json
{
  "group_id": "highest_education",
  "group_label": "Legmagasabb iskolai végzettség",
  "option_value": "Felsőfokú",
  "option_label": "Felsőfokú"
}
```

| Kulcs | Típus | Kötelező | Jelentés |
|---|---|---|---|
| `group_id` | `string` | **igen** | Stabil azonosító, közös a csoport minden tagján (pl. `highest_education`). Snake_case, ASCII. |
| `group_label` | `string \| null` | nem | A kérdés emberi felirata (pl. "Legmagasabb iskolai végzettség"). UI fejléc. |
| `option_value` | `string \| null` | nem | A Salesforce picklist érték, ami ezt az opciót kiválasztja (pl. `"Felsőfokú"`). Ez megy a `Contact.Highest_Educational_Qualification__c` mezőbe. |
| `option_label` | `string \| null` | nem | Az opció magyar neve a PDF-en (általában = `option_value`, de lehet hosszabb pl. "8 általános"). |
| `match_value` | `string \| null` | **deprecated alias** | Régi név. Olvasáskor `option_value := option_value ?? match_value`. Íráskor már nem generáljuk. |

#### Példa — teljes csoport a mapping JSON-ben (4 checkbox):

```json
{ "pdf_field_name": "SZA_IG_végzettség___1,2", "canonical_field": "Contact.Highest_Educational_Qualification__c",
  "field_type": "checkbox",
  "checkbox_group": { "group_id": "highest_education", "group_label": "Legmagasabb iskolai végzettség", "option_value": "Felsőfokú", "option_label": "Felsőfokú" } },
{ "pdf_field_name": "SZA_IG_végzettség___2,3", "canonical_field": "Contact.Highest_Educational_Qualification__c",
  "field_type": "checkbox",
  "checkbox_group": { "group_id": "highest_education", "group_label": "Legmagasabb iskolai végzettség", "option_value": "Szakképesítés", "option_label": "Szakképesítés" } },
{ "pdf_field_name": "SZA_IG_végzettség___3,4", "canonical_field": "Contact.Highest_Educational_Qualification__c",
  "field_type": "checkbox",
  "checkbox_group": { "group_id": "highest_education", "group_label": "Legmagasabb iskolai végzettség", "option_value": "Érettségi", "option_label": "Érettségi" } },
{ "pdf_field_name": "SZA_IG_végzettség___4,5", "canonical_field": "Contact.Highest_Educational_Qualification__c",
  "field_type": "checkbox",
  "checkbox_group": { "group_id": "highest_education", "group_label": "Legmagasabb iskolai végzettség", "option_value": "8 általános", "option_label": "8 általános" } }
```

### 2.3 Fájlonkénti változtatások (adatmodell)

| Fájl | Változás |
|---|---|
| `src/ai/field_recognizer.py` | `RecognizedField.checkbox_group` kommentjét bővíteni (a dict shape már nyitott, nem kell új dataclass-mező). `MappingConfig.to_dict()` / `from_dict()` már körbe-körbe másolja az egész dict-et — **nincs kódváltozás**, csak a dict bővül. |
| `frontend/src/types/index.ts:69` | `MappingField.checkbox_group` típusát kibővíteni: `{ group_id: string; group_label?: string \| null; option_value?: string \| null; option_label?: string \| null; match_value?: string \| null } \| null`. |
| `backend/mapping_service.py` | **Nincs változás** — `update_field` (`:117`) már a teljes `checkbox_group` dict-et perzisztálja. |

---

## 3. AI recognition módosítások

### 3.1 Érintett kód

- `src/ai/field_recognizer.py` — `SYSTEM_PROMPT` (`:515-552`), `_ai_batch_recognize` system string (`:727-784`), és az AI válasz feldolgozó (`:867-879`).
- A `_ai_batch_recognize` jelenleg a `g` és `mv` rövidített kulcsokat várja (`:874-877`), és `{"group_id": item["g"], "match_value": item.get("mv", "")}`-t épít.

### 3.2 A system prompt bővítése

Az `_ai_batch_recognize`-ban lévő `system` string `CHECKBOX GROUP DETECTION` blokkját (`:766-771`) le kell cserélni egy részletesebbre:

**Meglévő (tömör):**
```
CHECKBOX GROUP DETECTION:
If you see multiple checkboxes next to options like "☐ lakás ☐ ház ☐ telek" ...
Example: {"f": ..., "g": "property_type", "mv": "lakás"}
```

**Tervezett (strukturált):**
```
CHECKBOX GROUP DETECTION (CRITICAL — do this for EVERY mutually-exclusive option group):
When you see a question followed by multiple checkboxes, each next to a different option
(e.g. "Mi a legmagasabb iskolai végzettsége?  ☐ Felsőfokú  ☐ Szakképesítés  ☐ Érettségi  ☐ 8 általános"),
this is a RADIO/EXCLUSIVE group. For EACH checkbox in the group, add ALL of:
  "g"   — group_id: stable snake_case id shared by all members (e.g. "highest_education")
  "gl"  — group_label: the QUESTION text (e.g. "Legmagasabb iskolai végzettség")
  "ov"  — option_value: the Salesforce picklist value this option represents (e.g. "Felsőfokú")
  "ol"  — option_label: the option text printed next to this checkbox (e.g. "Felsőfokú")

Rules:
- Every member of an exclusive group MUST share the SAME "g" and "gl", but have DIFFERENT "ov"/"ol".
- "ov" must be the value stored in Salesforce (Contact.Highest_Educational_Qualification__c = "Felsőfokú").
- If a checkbox is a standalone yes/no (not part of a picklist group), do NOT set "g" — leave it as a plain checkbox.
- Look at the RED BOXES: options belonging to one question are visually clustered (same row or stacked column).

Example (full group, 4 members):
  {"f": "végzettség___1,2", "c": "Contact.Highest_Educational_Qualification__c", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Felsőfokú", "ol": "Felsőfokú"}
  {"f": "végzettség___2,3", "c": "Contact.Highest_Educational_Qualification__c", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Szakképesítés", "ol": "Szakképesítés"}
  {"f": "végzettség___3,4", "c": "Contact.Highest_Educational_Qualification__c", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "Érettségi", "ol": "Érettségi"}
  {"f": "végzettség___4,5", "c": "Contact.Highest_Educational_Qualification__c", "t": "checkbox", "g": "highest_education", "gl": "Legmagasabb iskolai végzettség", "ov": "8 általános", "ol": "8 általános"}
```

Az output formátum sorát (`:781-782`) bővíteni:
```
For checkbox groups add: "g": "group_id", "gl": "group_label", "ov": "option_value", "ol": "option_label"
```

A `SYSTEM_PROMPT` klasszikus (`:515-552`) ugyanezt a blokkot kapja a `checkbox_group` mező leírásában, a `fill_rule` mellé.

### 3.3 AI válasz-feldolgozó módosítása (`_ai_batch_recognize`)

A `field_recognizer.py:867-879` blokkban az `RecognizedField(...)` konstrukció `checkbox_group=...` argumentumát kibővíteni:

```python
checkbox_group=(
    {
        "group_id":   item["g"],
        "group_label": item.get("gl"),
        "option_value": item.get("ov") or item.get("mv"),  # mv = régi alias
        "option_label": item.get("ol"),
    }
    if item.get("g") else None
),
```

**Backward compat:** ha az AI csak régi `mv`-t ad, az `option_value` megkapja. Ha csak `g`-t, a többi `None` marad.

### 3.4 Enum/picklist értékek biztosítása az AI-nak

A `CANONICAL_FIELDS` dict jelenleg csak `{path: label}` párokat tartalmaz (`field_recognizer.py:64-333`), **enum-értékek nélkül**. Az AI ezért találgatja a `option_value`-t. Két opció (a spec-ben **opció B javasolt**, de nem kötelező):

- **Opció A (könnyű):** csak a prompt-példákban mutatunk minta-enumokat (fentebb már szerepel). Az AI a PDF felirataiból következtet.
- **Opció B (robusztus):** új modul-szintű dict `CANONICAL_PICKLIST_VALUES` a `field_recognizer.py`-ban (a `CANONICAL_FIELDS` mellett), amely a 13 érintett picklist mezőhöz a valid értékeket sorolja fel. Ez a dict bemegy a prompt `{canonical_fields}` helyébe vagy egy új `{picklist_values}` placeholderre. Forrása: a `backend/mapping_service.py:253-294` `SF_FIELD_TYPES` mintájára, SF schema-ból generálva.

Példa a `CANONICAL_PICKLIST_VALUES`-re (opció B):
```python
CANONICAL_PICKLIST_VALUES = {
    "Contact.Highest_Educational_Qualification__c": ["Felsőfokú", "Szakképesítés", "Érettségi", "8 általános"],
    "Contact.Marital_Status__c": ["Házas", "Egyedülálló", "Elvált", "Özvegy", "Élettársi"],
    "Contact.Salutation": ["Úr", "Hölgy"],
    # … a többi 10 mező …
}
```

### 3.5 Validáció az AI outputon (új helper)

Új függvény a `field_recognizer.py`-ba (a `_merge_mappings` közelébe):

```python
def _validate_checkbox_groups(fields: list[RecognizedField]) -> list[str]:
    """Visszatér a hibák listájával (üres = OK).
    Ellenőrzések:
    - Egy group_id-n belül minden tagnak azonos canonical_fieldje van-e.
    - Egy group_id-n belül minden option_value egyedi-e (nincs duplikáció).
    - group_label konzisztens (ugyanaz minden tagon) — ha meg van adva.
    """
```

Ez fut az `_ai_batch_recognize` végén és/vagy a `_merge_mappings` után; találat esetén `logger.warning` (nem fail-hard), hogy a review során látszódjon.

---

## 4. Frontend változások

### 4.1 `frontend/src/types/index.ts` (sor 69)

A `MappingField.checkbox_group` típus cseréje:
```ts
checkbox_group?: {
  group_id: string;
  group_label?: string | null;
  option_value?: string | null;
  option_label?: string | null;
  match_value?: string | null;   // deprecated alias
} | null;
```

### 4.2 `frontend/src/components/PageEditor.tsx` — sidebar csoport-megjelenítés

**Jelenleg:** minden checkbox mező egy önálló kártya (`:881-1248`), a `checkbox_group` editor egy egyszerű 2-input doboz (`:1175-1225`: group_id + match_value).

**Tervezett változások:**

**(a) Csoportosított renderelés a sidebar listában (`:881` körüli `pageFields.map(...)`)**

A `pageFields.map` helyett egy csoportosító pre-step: a checkbox mezőket `checkbox_group?.group_id` szerint bucketeljük; a nem-checkbox / csoport nélküli checkbox mezők önálló kártyák maradnak. A csoportba tartozó mezőket egy közös `<fieldset>`-szerű konténerbe tesszük:

```
┌─────────────────────────────────────┐
│ ▼ Legmagasabb iskolai végzettség    │  ← group_label fejléc (klikkre nyithat)
│   Contact.Highest_Educational_...   │
│   ┌───────────────────────────────┐ │
│   │ ☐ SZA_IG_végzettség___1,2     │ │  ← option_label: "Felsőfokú"
│   │   → option_value: Felsőfokú   │ │
│   ├───────────────────────────────┤ │
│   │ ☐ SZA_IG_végzettség___2,3     │ │  ← "Szakképesítés"
│   └───────────────────────────────┘ │
└─────────────────────────────────────┘
```

Új segédfüggvény a komponens tetején:
```ts
function groupPageFields(fields: MappingField[]): Array<
  | { kind: "single"; field: MappingField }
  | { kind: "group"; groupId: string; groupLabel: string; members: MappingField[] }
> { … }
```
Logika: checkbox + nem-null `checkbox_group.group_id` → group bucket; minden más → single. A csoportokon belül a tagokat az eredeti `pageFields` sorrendjében tartjuk.

**(b) A `checkbox_group` editor blokk bővítése (`:1175-1225`)**

A jelenlegi 2 input (group_id, match_value) helyett 4 input + 1 megjegyzés:
- `group_id` (datalist a meglévő csoportokkal — már megvan `:1188-1198`)
- `group_label` (új input — közös kérdés szövege)
- `option_value` (új input — SF picklist érték)
- `option_label` (új input — opció magyar neve)
- `match_value`-t eltávolítani az UI-ból (a backend olvasáskor alias-olja).

A `handleFieldUpdate` hívásokban a `checkbox_group` dict-et mind a 4 kulccsal felépíteni. Ha `group_id` üres → `null` (nem csoport tagja).

**(c) Új "csoport alkalmazása minden tagra" gomb** (opcionális, nice-to-have)

Amikor a user egy csoporttag `group_label`-jét szerkeszti, egy gomb: " groupName + group_label szinkronizálása a csoport összes tagjára". Ez batch `updateField` hívásokat indít. Csak ha az első implementáció után is szükség van rá.

### 4.3 `frontend/src/components/PointsEditor.tsx` — auto-populálás a mapping-ből

**Jelenleg:** a `PointsEditor` (`frontend/src/components/PointsEditor.tsx`) teljesen manuális — a user "+ Új pont" gombbal hoz létre Pointokat (`:879-890`), blokkokat és mezőket kézzel válogat össze.

**Tervezett új funkció: "Auto-generálás mapping csoportokból" gomb** a header-ben (`:953` mellé):

```ts
function handleAutoGenerateFromGroups() {
  // 1. mapping.fields-ből kinyeri a checkbox_group.group_id szerinti csoportokat
  // 2. Minden csoportból egy PointData-t épít:
  //    - point_id: `AUTO_${groupId}`
  //    - label: group_label ?? groupId
  //    - framework: "*"
  //    - page_number: a csoport első tagjának page_number-e
  //    - blocks: [{ block_id: member.pdf_field_name, members: [member.pdf_field_name] }]
  //      VAGY egyetlen blokk az összes taggal (l. 4.3.1 dilemma)
  //    - rule_type: 3 (részleges blokk = kölcsönösen kizáró)
  //    - params: { active_block: "" }  ← runtime dönti el a canonical_value alapján
  // 3. Nem felülírja a már létező AUTO_* pointokat, de felajánlja a frissítést
}
```

#### 4.3.1 Blokk-struktúra dilemma (döntés szükséges)

A rule_type 3 (`_r3_partial_block`, `fill_rules.py:121-129`) **blokkonként** pipázik: az `active_block` block_id-hoz tartozó mezők kapnak "igen"-t, a többi "nem"-et. Két értelmezés:

- **(i) Egy tag = egy blokk:** `blocks: [{block_id: "Felsőfokú", members: ["SZA_IG_végzettség___1,2"]}, {block_id: "Szakképesítés", members: ["...2,3"]}, …]`. A `params.active_block` runtimeban a `option_value` lesz. **Ez a terv szerinti.** A block_id = `option_value` (emberi olvasható), members = az adott opció checkboxa(i).
- **(ii) Egy csoport = egy blokk:** minden tag egy blokkban, és rule_type 3 nem alkalmazható (nincs al-blokk). Ez nem működik a kölcsönös kizárásra.

**Döntés a spec-ben: opció (i).** A blokkok az opciók, `block_id = option_value`. A rule_type 3 `active_block` params értékét a pipeline tölti ki (l. 5.3).

#### 4.3.2 Rule_type választás heurisztika

Nem minden csoport kölcsönösen kizáró! A `Contact.Income_type__c` (7 opció) lehet multi-select. A `Contact.Jovairas_vallalasa__c` (18 opció) szintén. Heurisztika az auto-generáláshoz:

| SF mező típusa (a `mapping_service.SF_FIELD_TYPES` alapján) | Szereplő típus | Javasolt rule_type |
|---|---|---|
| `picklist` (egyértékű) | exkluzív csoport | **3** (részleges blokk) |
| `multipicklist` (többértékű) | több opció is kipipálható | **1** (mind pipa) vagy **2** (szerepkör) |
| ismeretlen / `string` | fallback | **3** (biztonságosabb: csak egyet pipál) |

Ez a heurisztika az auto-generálás default-ja; a user a PointsEditor-ban utólag módosíthatja.

---

## 5. Backend / fill engine változások

### 5.1 `backend/mapping_service.py` — validáció kiegészítése

A `update_field` metódus (`:114-121`) whitelist-je (`:117`) már tartalmazza a `checkbox_group`-ot — **nincs változás**. Opcionális: új `validate_checkbox_groups(self, mapping)` metódus, amit a `save()` végén meghívunk, és konzisztencia-hibák esetén `logger.warning`-ot jelez (nem blokkolja a mentést). Ugyanazok a szabályok mint a 3.5-ös AI-validációnál.

### 5.2 Új modul: `src/engine/points_generator.py` (javasolt)

A mapping `fields[]` checkbox_group adataiból `points[]` konfigurációt generáló függvény. Ez híd az AI felismerés és a fill engine között.

```python
# src/engine/points_generator.py (ÚJ FÁJL)
from collections import defaultdict
from typing import Optional

def generate_points_from_groups(
    fields: list[dict],
    sf_field_types: dict[str, str] | None = None,
) -> list[dict]:
    """
    Mapping fields[] → points[] konfiguráció.

    Minden checkbox_group.group_id-ből egy Point dict-et épít:
      {
        "point_id": f"AUTO_{group_id}",
        "framework": "*",
        "label": group_label,
        "page_number": <első tag oldala>,
        "blocks": [
          {"block_id": option_value, "members": [pdf_field_name, ...]},
          ...
        ],
        "rule_type": 3,   # picklist → kölcsönösen kizáró
        "params": {"active_block": None},   # runtime tölti ki
        "_source": "auto_group",   # editor-only metadata
      }

    sf_field_types: a backend mapping_service.SF_FIELD_TYPES dict-je,
      hogy eldöntse picklist vs. multipicklist (l. 4.3.2).
    """
```

Ezt hívja a PointsEditor "auto-generálás" gombja (a backend egy új endpointon keresztül, vagy a frontendeben inline — l. 5.3), ÉS opcionálisan a pipeline indulásakor (`src/main.py`), ha a mapping `points: []` de vannak csoportok.

### 5.3 `src/main.py:645-687` — runtime `active_block` feloldás

**A probléma:** a rule_type 3 (`fill_rules.py:121-129`) a `params.active_block` fix block_id-t vár, de ez only runtime dől el: a Contact tényleges `Highest_Educational_Qualification__c` értéke ("Felsőfokú") határozza meg, melyik blokk aktív.

**Tervezett megoldás:** a `src/main.py:661-678` pont-feldolgozó ciklusban, **mielőtt** a `Point(...)` objektumot átadnánk a szabálynak, egy runtime patch lépés:

```python
for p_def in points:
    # ── NEW: runtime active_block feloldás auto-generált pontoknál ──
    if p_def.get("_source") == "auto_group" and p_def.get("rule_type") == 3:
        canonical_field = _infer_canonical_from_blocks(p_def, mapping)
        actual_value = ctx.canonical_values.get(canonical_field)
        # a block_id-k = option_value-k; keressük az egyezőt
        matched_block = next(
          (b["block_id"] for b in p_def["blocks"]
           if _values_match(b["block_id"], actual_value)),
          None,
        )
        p_def = {**p_def, "params": {**p_def.get("params", {}), "active_block": matched_block}}
    point = Point(...)
    result = fn(point, ctx)
    …
```

Két új segédfüggvény a `main.py`-ba (vagy egy helper modulba):

- `_infer_canonical_from_blocks(p_def, mapping)`: a blokkok members PDF-mezőinek `canonical_field`-jéből (a mapping-ből kikeresve) megmondja, melyik canonical mezőhöz tartozik a csoport. Mindegyik blokk ugyanoda mutat, elég az első member.
- `_values_match(block_id, actual_value)`: kisbetűs, ékezet-normálázott (`_normalize_key`, már létezik a `field_recognizer.py:35-54`) összehasonlítás, hogy a `"felsőfokú"` ~= `"Felsőfokú"` ~= `"Felsofoku"`.

**Ha nincs egyezés** (`matched_block is None`): minden blokk "nem" kap (a rule_type 3 alapértelmezése), és egy `logger.info` jelzi, hogy a Contact értéke nem illik egyetlen opcióra sem (pl. hiányzó adat).

### 5.4 `src/engine/fill_rules.py` — **nincs kódváltozás**

A rule_type 3 (`_r3_partial_block`) már helyesen implementálja a kölcsönös kizárást: az `active_block` block_id-hoz tartozó mezők "igen", a többi "nem". A `Point` dataclass és `RuleContext` is megfelelő. Csak a hívó (`main.py`) kell, hogy feltöltse a `params.active_block`-ot runtime (5.3).

---

## 6. Implementációs sorrend (függőségek)

```
                  ┌──────────────────────────┐
   1. Adatmodell  │ types/index.ts (TS típus) │  ← nincs függőség
   (spec-2)       │ field_recognizer.py komment│
                  └────────────┬─────────────┘
                               │
                  ┌────────────▼─────────────┐
   2. AI felismerés│ SYSTEM_PROMPT bővítés     │  ← függ: 1 (dict shape)
   (spec-3)       │ _ai_batch_recognize parse │
                  │ _validate_checkbox_groups │
                  └────────────┬─────────────┘
                               │
                  ┌────────────▼─────────────┐
   3. Backend     │ mapping_service validáció │  ← függ: 1
   (spec-5.1,5.2) │ points_generator.py (ÚJ)  │  ← függ: 1, 2
                  └────────────┬─────────────┘
                               │
         ┌─────────────────────┼──────────────────────┐
         ▼                                           ▼
   ┌──────────────────┐                    ┌───────────────────┐
   │ 4. PageEditor UI │                    │ 5. Pipeline runtime│
   │  (spec-4.2)      │                    │  main.py patch    │
   │  csoportosítás   │                    │  (spec-5.3)       │
   └────────┬─────────┘                    └─────────┬─────────┘
            │                                        │
            ▼                                        ▼
   ┌──────────────────┐                    ┌───────────────────┐
   │ 6. PointsEditor  │                    │ 7. Tesztelés      │
   │ auto-generálás   │                    │ regresszió +      │
   │  (spec-4.3)      │                    │ új smoke tesztek  │
   └──────────────────┘                    └───────────────────┘
```

### Lépések (részletesen):

| # | Lépés | Fájlok | Ellenőrzés (checkpoint) |
|---|---|---|---|
| 1 | TS típus bővítése + Python kommentek | `types/index.ts`, `field_recognizer.py` | `tsc --noEmit` pass; mapping JSON betöltése nem törik |
| 2 | AI prompt bővítése + parse + validáció | `field_recognizer.py` | Újra-futtatni a felismerést a SZA PDF-en; ellenőrizni, hogy a 14 csoport mindegyike megkapja a `group_id`/`group_label`/`option_value`/`option_label`-t |
| 3 | `points_generator.py` + backend validáció | `points_generator.py` (új), `mapping_service.py` | Unit teszt: a 4-tagú `highest_education` csoportból 1 Point, 4 blokk, rule_type 3 |
| 4 | PageEditor UI csoportosítás + 4-input editor | `PageEditor.tsx` | Kézi teszt: sidebar-ban a csoportok keretben jelennek meg; option_value szerkeszthető és perzisztálódik |
| 5 | `main.py` runtime active_block feloldás | `main.py` | Smoke: adós `education="Felsőfokú"` → csak a `végzettség___1,2` checkbox pipa, a többi nem |
| 6 | PointsEditor auto-generálás gomb | `PointsEditor.tsx` | Kézi: gombra kattintva 14 Point jön létre; mentés után a `points[]` nem üres |
| 7 | Regresszió + edge case tesztek | `tests/` | Létező mapping-ek (amik `checkbox_group: null`) továbbra is betöltődnek; fill engine nem tör |

**Kritikus útvonal:** 1 → 2 → 5 (ez már önmagában működőképes end-to-end, az AI felismer és a pipeline pipáz). A 3, 4, 6 a UX/dev-ui része, párhuzamosan fejleszthető az 5-tel.

---

## 7. Kockázatok és edge case-ek

### 7.1 Backward compatibility (kritikus)

- **Meglévő mapping-ek `checkbox_group: null`-lal:** minden eddigi fájl (`01_sza_ig_...`, `otp_acroform_mapping.json`, stb.) mind a 69+ checkboxánál `null` az érték. **Ezek továbbra is működnek** — a `points_generator.py` egyszerűen kihagyja a `null` csoportú mezőket, a `main.py` point-ciklusa pedig üres `points[]` esetén lépést nem tesz (jelenlegi viselkedés).
- **`match_value` deprecated alias:** az olvasó kód mindenhol `option_value := option_value ?? match_value` logikát használ, így a régi (AI vagy ember által `match_value`-t tartalmazó) mapping-ek nem törnek.
- **`MappingConfig.from_dict` (`field_recognizer.py:441-473`)** már az egész `checkbox_group` dict-et átmásolja (`f.get("checkbox_group")`) — új kulcsok automatikusan round-trippelnek.

### 7.2 Csoport tagjai különböző oldalakon

**Eset:** egy checkbox csoport (azonos `group_id`) tagjai két PDF oldalon oszlanak el (pl. egy hosszú kérdéssor átnyúlik a lapra). 

**Kezelés:**
- A `group_id` oldalfüggetlen, így a `points_generator.py` gyűjtse össze az összes oldalon (ne csak `pageFields`-re szűrjön). A generált Point `page_number`-e legyen a csoport **első** tagjának oldala (tájékoztató jellegű).
- A PageEditor sidebar oldalonként renderel — ott a csoport "csonkoltan" jelenik meg (csak az adott oldal tagjaival). Ez acceptálható; a fejléc mutatja a group_label-t. Opcionális javítás: ha egy csoport több oldalon van, a sidebar egy "(N. oldalon folytatódik)" megjegyzést jelez.
- A fill engine (`fill_rules.py`) oldalfüggetlen — csak PDF-mezőnevekkel dolgozik, nem érdekli az elhelyezés.

### 7.3 Optikailag egy csoport, de a PDF-mezőnevek nem utalnak rá

**Eset:** a PDF-en vizuálag együtt vannak a checkboxok ("☐ Felsőfokú ☐ Szakképesítés"), de a PDF-mezőnevek generikusak (`Check_Box_12`, `Check_Box_13`), és az AI nem ismeri fel a csoportot.

**Kezelés:** ezért van a vizuális prompt (piros számozott dobozok, `field_recognizer.py:692-724`). A bővített prompt (3.2) explicit utasítja az AI-t, hogy a vizuális klaszterezés alapján csoportosítson. **Ha az AI mégsem csoportosít**, a PageEditor UI-ban a user manuálisan megadhatja a `group_id`-t (a 4.2(b) editorban) — a `datalist` (`:1188-1198`) már felajánlja a meglévő csoportokat. Tehát a folyamat: AI felismer → emberi review/javítás a UI-ban.

### 7.4 Ugyanarra a canonical_field-re több csoport

**Eset:** két külön kérdés ugyanahhoz a SF mezőhöz tartozik (pl. két különböző oldalon rákérdeznek a családi állapotra). Ekkor két különböző `group_id` (`marital_status_p2`, `marital_status_p5`) mutat ugyanarra a `Contact.Marital_Status__c`-re.

**Kezelés:** a `points_generator.py` külön Pointot generál mindkettőre. A `main.py:680-687` már naplózza, ha egy PDF-mezőt két point felülír (`logger.debug`) — ez elfogadható, de ha konfliktus van (két csoport más opciót pipálna), az utolsó nyer. **Opcionális hardening:** a `main.py`-ban ellenőrizni, hogy ugyanarra canonical_field-re mutató csoportok ne pipáljanak egymásnak ellentmondóan; találat esetén `logger.warning`.

### 7.5 `option_value` nem egyezik egyetlen blokk `block_id`-vel sem

**Eset:** a Contact `Highest_Educational_Qualification__c = "Egyetemi diploma"`, de a mapping-ben a block_id-k `["Felsőfokú", "Szakképesítés", "Érettségi", "8 általános"]` — nincs "Egyetemi diploma". 

**Kezelés:** a `_values_match` (5.3) nem talál egyezést → `matched_block = None` → minden checkbox "nem" kap. `logger.info` jelzi az eltérést. Ez a biztonságos viselkedés (inkább üres, mint rossz). A `.ai-instructions.md` Evidence Gate értelmében ez elfogadható, mert a hiba látható és nem okoz csendes hibás kitöltést.

### 7.6 Multi-select csoportok (nem kölcsönösen kizáró)

**Eset:** `Contact.Income_type__c` (7 opció) — lehet, hogy több jövedelemtípus is fennáll egyszerre (fizetés + vállalkozás). Ilyenkor a rule_type 3 (csak egy aktív blokk) **hibás** lenne.

**Kezelés:** a `points_generator.py` a `sf_field_types` (5.2) alapján dönt — ha a mező `multipicklist`, rule_type 1 (mind aktív) vagy egy új rule_type 8 (multi-match). **A spec jelenleg nem vezet be új rule_type-ot**; a multi-select csoportoknál az auto-generálás `rule_type: 1`-et ad, és a user a PointsEditor-ban finomhangolhatja. Ez egy ismert korlát, dokumentálandó a kódban.

### 7.7 Token-költség növekedés a prompt bővítése miatt

A bővített `CHECKBOX GROUP DETECTION` blokk (~30 sor) minden batch API hívásnal bekerül a system stringbe (`field_recognizer.py:727`). Ennek mérete elhanyagolható a `CANONICAL_FIELDS` listához (~249 sor) képest, de ha a 3.4 Opció B (`CANONICAL_PICKLIST_VALUES`) is bekerül, az további ~30 sort jelent. **Kockázat: alacsony.** A `max_tokens=8192` (`:814`) bőven elég.

### 7.8 AI inkonzisztens `group_label`-t ad a csoport tagjainak

**Eset:** az AI a 4 checkbox közül 2-nek `"gl": "Legmagasabb iskolai végzettség"`, 2-nek `"gl": "Végzettség"` értéket ad. 

**Kezelés:** a `_validate_checkbox_groups` (3.5) detektálja és `logger.warning`-ot ad. A `points_generator.py` a `group_label`-t a csoport **első** tagjából veszi (többségi szavazás alternatíva, de túlbonyolított). A PageEditor UI-ban a user javíthatja.

---

## 8. Összefoglaló tábla — érintett fájlok

| Fájl | Sor (~) | Változás típusa | Kockázat |
|---|---|---|---|
| `src/ai/field_recognizer.py` | `:515-552`, `:727-784`, `:867-879` | prompt + parse + validáció | 🟡 közepes |
| `frontend/src/types/index.ts` | `:69` | típusbővítés | 🟢 alacsony |
| `frontend/src/components/PageEditor.tsx` | `:881-1248`, `:1175-1225` | csoportosított render + editor | 🟡 közepes |
| `frontend/src/components/PointsEditor.tsx` | `:879-890`, `:953` körül | auto-generálás gomb + logika | 🟡 közepes |
| `src/engine/points_generator.py` | új fájl (~80 sor) | új modul | 🟢 alacsony (tiszta lappal) |
| `src/main.py` | `:661-678` | runtime active_block feloldás | 🔴 magas (pipeline core) |
| `backend/mapping_service.py` | `:114-121` (validáció) | opcionális validáció | 🟢 alacsony |

**Nem érintett:** `src/engine/fill_rules.py` (a rule_type 3 már kész), `src/models/canonical_model.py` (a Participant már tartalmazza a `marital_status`/`education`/`income_type` mezőket).

---

*A terv kész. Implementáció csak a user jóváhagyása után, a fenti sorrendben, lépésenkénti Evidence Gate-tel (.ai-instructions.md szerint).*

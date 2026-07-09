# FinancialGenie — Bug Fix Proposals

Concrete, reviewable diffs for the CRITICAL and HIGH findings in `REVIEW_EDGE_CASES.md`. Every fix lists:
- the **finding ID** it addresses,
- the **test from `REVIEW_TESTING_STRATEGY.md`** that must pass before merge,
- the **risk** of the change (what else it could break),
- the **files touched**.

The fixes are ordered so each builds on the previous; the rollout order is summarised at the end. **All diffs are against `f66ec7b`.** Line numbers are accurate to that commit.

> NOTE: the previous `REVIEW_FIXES.md` in this repo was written against an imagined codebase (different canonical model, non-existent files). It has been replaced wholesale. Do not trust line numbers or function names from any earlier draft.

---

## Fix F — `get_deal` must not return the wrong deal  (CRITICAL · finding B1)

**Why first:** every test that asserts on filled output assumes `get_deal(deal_id)` returns *that* deal. Until this is fixed, every downstream test is suspect.

**File:** `src/integrations/salesforce_client.py:169-179`

```diff
@@ get_deal @@
         if self._mock_mode:
             deal = self._state.deals.get(deal_id)
-            if deal:
-                logger.info(f"Ügylet lekérve (mock): {deal_id}")
-            else:
-                # Ha nem pontos ID, próbáljuk az első találatot
-                for did, ddata in self._state.deals.items():
-                    logger.info(f"Ügylet lekérve (mock, első találat): {did}")
-                    return ddata
-                logger.warning(f"Ügylet nem található (mock): {deal_id}")
+            if deal is None:
+                logger.warning(f"Ügylet nem található (mock): {deal_id!r}")
+            else:
+                logger.info(f"Ügylet lekérve (mock): {deal_id!r}")
             return deal
         else:
             try:
```

**Test that must pass:** `test_get_deal_unknown_id_returns_none`, `test_get_deal_known_id_returns_that_deal` (§2.4 of strategy).
**Risk:** very low. Mock-only change; live mode is unaffected.
**Files:** `src/integrations/salesforce_client.py`.

---

## Fix D — Money formatting handles `0`, `None`, negatives, garbage strings  (HIGH · finding 5)

**Files:** `src/main.py` (12+ sites); add a module-level helper.

```diff
@@ src/main.py  (add after the imports, before class FormFillerPipeline @)
+def _fmt_money(value) -> str:
+    """Format a HUF amount with space-separated thousands.
+
+    None   -> ""           (missing data — left blank)
+    0      -> "0"          (a real, meaningful zero)
+    <0     -> "-N NNN"     (sign-preserving)
+    float  -> rounded to int
+    str    -> parsed if numeric, else ""
+    other  -> ""           (never raises)
+    """
+    if value is None or value == "":
+        return ""
+    try:
+        n = int(round(float(value)))
+    except (TypeError, ValueError):
+        return ""
+    sign = "-" if n < 0 else ""
+    return f"{sign}{abs(n):,}".replace(",", " ")
+
+
+def _str_or_empty(value) -> str:
+    """str(x) if x is not None else "" — never raises."""
+    return "" if value is None else str(value)
```

Then replace every `f"{x:,}".replace(",", " ") if x else ""` site in `src/main.py`:

```diff
@@ _prepare_field_data, line 326 @@
-        loan_amount_fmt = f"{loan.loan_amount:,}".replace(",", " ") if loan.loan_amount else ""
+        loan_amount_fmt = _fmt_money(loan.loan_amount)

@@ line 357 @@
-                "Lead.Ingatlan_alapterulet__c": str(prop.area_sqm) if prop.area_sqm else "",
+                "Lead.Ingatlan_alapterulet__c": _str_or_empty(prop.area_sqm),

@@ line 359 @@
-                "Lead.Estimated__c": f"{prop.estimated_value:,}".replace(",", " ") if prop.estimated_value else "",
+                "Lead.Estimated__c": _fmt_money(prop.estimated_value),

@@ _participant_to_dict, line 557 @@
-            "Contact.Average_monthly_net_income__c": f"{p.monthly_income:,}".replace(",", " ") if p.monthly_income else "",
+            "Contact.Average_monthly_net_income__c": _fmt_money(p.monthly_income),
```

**Test that must pass:** `test_fmt_money` parametric test (§2.1); `test_zero_down_payment_is_written` (new — build a deal with `down_payment=0`, assert the field is `"0"`, not empty).
**Risk:** low — changes filled output only for `0`/negative/garbage inputs, which were already wrong.
**Files:** `src/main.py`.

---

## Fix C — Stop referencing model fields that don't exist  (HIGH · finding 4.1)

`_participant_to_dict` (`src/main.py:541-566`) currently produces five `Contact.*` keys whose backing attributes do not exist on `Participant`. They silently always equal `""`.

There are two valid resolutions; pick one based on product intent:

### Option C-1 (recommended): remove the ghost entries

```diff
@@ src/main.py  (_participant_to_dict, lines 559-564) @@
             "Contact.Relation__c": p.role.value,
-            # SF-ből töltjük — ha nincs adat, üresen hagyjuk
-            "Contact.Citizenship__c": getattr(p, "citizenship", "") or "",
-            "Contact.Marital_Status__c": getattr(p, "marital_status", "") or "",
-            "Contact.Highest_Educational_Qualification__c": getattr(p, "education", "") or "",
-            "Contact.Income_type__c": getattr(p, "income_type", "") or "",
-            "Contact.Dependents_count__c": str(p.dependents_count) if getattr(p, "dependents_count", None) is not None else "",
         }
         return d
```
…combined with mapping-curating the PDF fields `SZA_IG_állampolgárság` etc. to `null` (unmapped) until the SF integration actually returns those fields.

### Option C-2 (alternative): add the fields to the model

```diff
@@ src/models/canonical_model.py  (class Participant, after line 71) @@
+    citizenship: Optional[str] = Field(None, description="Állampolgárság")
+    marital_status: Optional[str] = Field(None, description="Családi állapot")
+    education: Optional[str] = Field(None, description="Legmagasabb iskolai végzettség")
+    income_type: Optional[str] = Field(None, description="Jövedelem típusa")
+    dependents_count: Optional[int] = Field(None, description="Eltartottak száma")
```
…and update `_normalize_participant` in `data_normalizer.py` to populate them from the SF contact record (`Contact.Citizenship__c`, etc. — these already exist on the SF side, see `CANONICAL_FIELDS`).

Then drop the `getattr` defensive calls:
```diff
-            "Contact.Citizenship__c": getattr(p, "citizenship", "") or "",
+            "Contact.Citizenship__c": p.citizenship or "",
```

**Recommended:** Option C-2 — these fields exist in the SF schema (`CANONICAL_FIELDS` lines 84, 142, 115, 119, 97), so the data is already in Salesforce; we're just dropping it on the floor. Option C-1 is the right choice if these fields are deliberately out-of-scope for the PoC.

**Test that must pass:** `test_participant_dict_only_references_model_fields` (§2.2).
**Risk:** C-1 — some mapped PDF fields will go from "always empty" to "unmapped"; needs the affected mappings curated. C-2 — model gains optional fields; no breaking change but `_normalize_participant` must map the SF fields.
**Files:** `src/main.py`; `src/models/canonical_model.py` and `src/normalizer/data_normalizer.py` if Option C-2.

---

## Fix A — Route every active participant and every property  (CRITICAL · findings 3.1, 3.2)

**Why this is the big one:** the current `_prepare_field_data` only materialises `borrowers[0]`, `co_borrowers[0]`, and `properties[0]`. Beyond index 0 the data is dropped or, worse, silently replaced by index 0's data.

**File:** `src/main.py:275-486` (`_prepare_field_data`) — substantial rewrite.

### Strategy: index-resolved store + per-instance PDF-field suffix

1. Build a value store keyed by both `Contact.X` (instance 0) **and** `Contact[i].X` for every active participant. Same for `Lead.X` / `Lead[i].X`.
2. When resolving a mapping field, recognise a per-instance suffix in the PDF field name (`borrower_`, `coborrower_`, `coborrower2_`, `property_`, `property2_`, etc.) and route to the corresponding participant/property.

```diff
@@ src/main.py  (replace the body of _prepare_field_data from line 285 to 394) @@

     def _prepare_field_data(self, deal: DealData, mapping: MappingConfig) -> dict:
-        """..."""
-        field_data = {}
-        # ... existing 100+ lines that only handle index 0 ...
+        """
+        Materialise every active participant and every property into an
+        index-resolved value store, then resolve each mapping field with
+        instance-aware routing.
+
+        Instance suffixes recognised in PDF field names:
+          borrower_, borrower1_, borrower[0]_    -> participants[0] (BORROWER)
+          coborrower_, coborrower1_              -> first CO_BORROWER
+          coborrower2_, coborrower3_, ...        -> 2nd, 3rd, ... CO_BORROWER
+          guarantor_, guarantor1_, guarantor2_   -> GUARANTOR instances
+          beneficiary_, ...                       -> BENEFICIARY instances
+          property_, property1_, property[0]_    -> properties[0]
+          property2_, property3_, ...            -> properties[1], [2], ...
+
+        The existing "-társ" suffix on OTP forms is preserved as an alias
+        for the first coborrower.
+        """
+        field_data = {}
+
+        # ---- 1. Build the value store, indexed ----
+        participants = deal.active_participants
+        properties = deal.properties
+
+        # Group participants by role for role-suffix routing.
+        by_role: dict[str, list[Participant]] = {}
+        for p in participants:
+            by_role.setdefault(p.role.value, []).append(p)
+
+        def _add_participant(p: Participant, idx: int, role_key: str):
+            """Put participant's data under every key the resolver might look up."""
+            pdata = self._participant_to_dict(p)
+            if p.address:
+                pdata.update(self._address_to_dict(p.address, "address"))
+            if p.mailing_address:
+                pdata.update(self._address_to_dict(p.mailing_address, "mailing_address"))
+            elif p.address:
+                pdata.update(self._address_to_dict(p.address, "mailing_address"))
+            # Instance-0 of BORROWER is also exposed unprefixed (Contact.X),
+            # preserving backward compatibility with existing mappings.
+            if role_key == "adós" and idx == 0:
+                for k, v in pdata.items():
+                    field_data.setdefault(k, v)  # don't overwrite (setdefault)
+                    # also store under explicit "borrower_" prefix for clarity
+            # Always store under the indexed key for per-instance routing:
+            for k, v in pdata.items():
+                field_data[f"__store__.participant[{idx}].{k}"] = v
+                field_data[f"__store__.role[{role_key}][{idx}].{k}"] = v
+
+        for idx, p in enumerate(participants):
+            _add_participant(p, idx, p.role.value)
+
+        for idx, prop in enumerate(properties):
+            pdata = {
+                "Lead.Ingatlan_irsz__c":                prop.address.zip_code,
+                "Lead.Ingatlan_telepules__c":           prop.address.city,
+                "Lead.Ingatlan_kozterulet_neve__c":     f"{prop.address.street} {prop.address.house_number}".strip(),
+                "Lead.Ingtalan_hazszam__c":             prop.address.house_number,
+                "Lead.Ingatlan_megjegyzes__c":          prop.parcel_number,
+                "Lead.Ingatlan_alapterulet__c":         _str_or_empty(prop.area_sqm),
+                "Lead.Ingatlan_jellege__c":             prop.property_type.value,
+                "Lead.Estimated__c":                    _fmt_money(prop.estimated_value),
+            }
+            if idx == 0:
+                for k, v in pdata.items():
+                    field_data.setdefault(k, v)
+            for k, v in pdata.items():
+                field_data[f"__store__.property[{idx}].{k}"] = v
+
+        # Loan data (single instance — global)
+        loan = deal.loan
+        contact_loan_data = {
+            "Contact.Loan_amount__c":    _fmt_money(loan.loan_amount),
+            "Contact.Loan_period__c":    _str_or_empty(loan.loan_term_months),
+            "Contact.Interest_Period__c": loan.interest_period or "",
+            "Contact.Loan_Purpose__c":   loan.loan_purpose or "",
+        }
+        opportunity_loan_data = {
+            "Opportunity.Hitel_sszeg__c": _fmt_money(loan.loan_amount),
+            "Opportunity.Hitelc_l__c":    loan.loan_purpose or "",
+            "Opportunity.Term_k__c":      loan.product_name or "",
+        }
+        for k, v in {**contact_loan_data, **opportunity_loan_data}.items():
+            field_data[k] = v
+            field_data[f"__store__.loan.{k}"] = v
+
+        # ---- 2. Resolve each mapping field with instance routing ----
+        for f in mapping.fields:
+            if not f.canonical_field:
+                continue
+            pdf_name = f.pdf_field_name
+            canonical = f.canonical_field
+
+            if canonical == "Contact.Relation__c":
+                continue
+
+            instance_idx, instance_role = self._detect_instance(pdf_name)
+            value = self._resolve_value(canonical, instance_idx, instance_role, pdf_name, field_data)
+
+            if value:
+                # Apply fragment transform (Fix B)
+                fragment = getattr(f, "fragment", None)
+                value = _apply_fragment(value, fragment)
+                if value:
+                    field_data[pdf_name] = value
+
+        # ---- 3. Checkbox group resolution (existing logic preserved) ----
+        # ... (the existing groups/fill_rule loops, adapted to use the store) ...
+
+        return field_data
+
+    # New helpers:
+
+    # PDF-name suffixes that identify which participant/property a field belongs to.
+    # Order matters: longer prefixes first (coborrower2_ before coborrower_).
+    _INSTANCE_PATTERNS = [
+        # (regex, kind, role_or_None, index_from_suffix_or_None)
+        (r"^borrower(\d+)?_",   "participant", "adós",      lambda m: (int(m) - 1) if m else 0),
+        (r"^coborrower(\d+)?_", "participant", "adóstárs",  lambda m: (int(m) - 1) if m else 0),
+        (r"^guarantor(\d+)?_",  "participant", "kezes",     lambda m: (int(m) - 1) if m else 0),
+        (r"^beneficiary(\d+)?_","participant", "haszonélvező", lambda m: (int(m) - 1) if m else 0),
+        (r"^property(\d+)?_",   "property",    None,        lambda m: (int(m) - 1) if m else 0),
+    ]
+    # Also: OTP convention - "-társ" suffix on any field => first coborrower
+
+    def _detect_instance(self, pdf_name: str) -> tuple[Optional[int], Optional[str]]:
+        """Return (instance_index, role) inferred from the PDF field name.
+        Returns (None, None) when no per-instance marker is present."""
+        import re
+        lower = pdf_name.lower()
+        # OTP "-társ" suffix -> first coborrower
+        if "-társ" in pdf_name or "társigénylő" in lower or "tarsigenylő" in lower:
+            return 0, "adóstárs"
+        for pattern, kind, role, idx_fn in self._INSTANCE_PATTERNS:
+            m = re.match(pattern, lower)
+            if m:
+                idx = idx_fn(m.group(1))
+                return idx, role
+        return None, None
+
+    def _resolve_value(self, canonical, idx, role, pdf_name, store) -> str:
+        """Look up a canonical value, honouring the instance detected by _detect_instance."""
+        if idx is None and role is None:
+            # Default: instance 0 (existing behaviour)
+            return store.get(canonical, "")
+        if role is not None:
+            # Role-routed: find the idx-th participant with that role
+            role_key = f"__store__.role[{role}][{idx}].{canonical}"
+            return store.get(role_key, "")
+        # Property-routed
+        prop_key = f"__store__.property[{idx}].{canonical}"
+        return store.get(prop_key, "")
```

**Test that must pass:** `test_no_cross_contamination_between_properties` (§3.2), `test_guarantor_data_reaches_pdf` (§3.3 with `multi_borrower`), `test_coborrower2_data_reaches_pdf` (new — requires a 3-coborrower scenario).
**Risk:** high. This is the biggest change in the review. Mitigations:
- Add a "compatibility mode" boolean to keep the old code path alive for one release.
- Run the read-back test class (§3 of strategy) on **every** existing mapping file as a smoke test before merge.
- The old `-társ` heuristic is preserved as a first-class pattern in `_detect_instance`.

**Files:** `src/main.py`.

---

## Fix B — Fragment-aware field filling  (CRITICAL · findings 1.1, 1.2, 2)

**Files:** `src/ai/field_recognizer.py` (extend `RecognizedField` + `MappingConfig` (de)serialisation), `src/main.py` (apply the fragment), and curated edits to existing mapping JSONs.

### B-1: Extend the dataclass

```diff
@@ src/ai/field_recognizer.py  (RecognizedField, lines 335-347) @@
 @dataclass
 class RecognizedField:
     """Egy felismert PDF mező az AI által."""
     pdf_field_name: str
     label: str
     field_type: FieldType
     canonical_field: Optional[str]
     confidence: MappingConfidence
     page_number: int
     coordinates: Optional[dict] = None
     notes: Optional[str] = None
     checkbox_group: Optional[dict] = None
     fill_rule: Optional[dict] = None
+    # How to derive this field's value from its canonical_field:
+    #   None              -> use the canonical value verbatim
+    #   "year"/"month"/"day" -> date fragment of a date-typed canonical
+    #   "digit:N"          -> the (N+1)-th character of a string canonical
+    fragment: Optional[str] = None
```

### B-2: Round-trip the new field in JSON

```diff
@@ MappingConfig.to_dict, lines 375-389 @@
                 {
                     "pdf_field_name": f.pdf_field_name,
                     "label": f.label,
                     "field_type": f.field_type.value,
                     "canonical_field": f.canonical_field,
                     "confidence": f.confidence.value,
                     "page_number": f.page_number,
                     "coordinates": f.coordinates,
                     "notes": f.notes,
                     "checkbox_group": f.checkbox_group,
                     "fill_rule": f.fill_rule,
+                    "fragment": f.fragment,
                 }

@@ MappingConfig.from_dict, lines 396-407 @@
                 page_number=f["page_number"],
                 coordinates=f.get("coordinates"),
                 notes=f.get("notes"),
                 checkbox_group=f.get("checkbox_group"),
                 fill_rule=f.get("fill_rule"),
+                fragment=f.get("fragment"),
             )
```

### B-3: Apply the fragment in main.py (used by Fix A's resolver)

```diff
@@ src/main.py  (module-level helper, after _fmt_money) @@
+import re as _re
+
+
+def _apply_fragment(value: str, fragment: Optional[str]) -> str:
+    """Apply a per-field fragment transformation.
+
+    fragment values:
+      None              -> str(value) verbatim
+      "year"/"month"/"day" -> split a date like '1985.03.15' on [./-] and
+                              return the requested component, or "" on failure
+      "digit:N"          -> the (N+1)-th character of value, or "" if out of range
+    """
+    if value is None:
+        return ""
+    if not fragment:
+        return str(value)
+    frag = fragment.strip().lower()
+    if frag in ("year", "month", "day"):
+        parts = _re.split(r"[.\-/]", str(value).strip())
+        if len(parts) != 3:
+            logger.warning("Cannot extract %s from %r (expected 3 date parts)", frag, value)
+            return ""
+        y, mo, d = parts
+        return {"year": y, "month": mo, "day": d}[frag]
+    if frag.startswith("digit:"):
+        try:
+            idx = int(frag.split(":", 1)[1])
+        except ValueError:
+            return ""
+        s = str(value)
+        return s[idx] if 0 <= idx < len(s) else ""
+    logger.warning("Unknown fragment %r", fragment)
+    return str(value)
```

### B-4: Update the heuristic mapper so it emits fragments for known fragmented forms

```diff
@@ src/ai/field_recognizer.py  (OTP_EXACT_MAP, replace lines 1558-1560) @@
-            "SZA_IG_szül_év": "Contact.Date_of_birth__c",
-            "SZA_IG_szül_hónap": "Contact.Date_of_birth__c",
-            "SZA_IG_szül_nap": "Contact.Date_of_birth__c",
+            "SZA_IG_szül_év":    "Contact.Birthdate",         # +fragment=year
+            "SZA_IG_szül_hónap": "Contact.Birthdate",         # +fragment=month
+            "SZA_IG_szül_nap":   "Contact.Birthdate",         # +fragment=day
...
@@ and -társ variants, lines 1592-1594 @@
-            "SZA_IG_szül_év-társ": "Contact.Date_of_birth__c",
-            "SZA_IG_szül_hónap-társ": "Contact.Date_of_birth__c",
-            "SZA_IG_szül_nap-társ": "Contact.Date_of_birth__c",
+            "SZA_IG_szül_év-társ":    "Contact.Birthdate",     # +fragment=year
+            "SZA_IG_szül_hónap-társ": "Contact.Birthdate",     # +fragment=month
+            "SZA_IG_szül_nap-társ":   "Contact.Birthdate",     # +fragment=day
```

Then teach `_heuristic_map_fields` to attach the fragment. The cleanest way: make `OTP_EXACT_MAP` value a tuple `(canonical, fragment_or_None)`:

```diff
@@ src/ai/field_recognizer.py  (_heuristic_map_fields, around line 1640) @@
-        otp_exact_map_normalized = {
-            _normalize_key(k): v for k, v in OTP_EXACT_MAP.items()
-        }
-        for pdf_field in pdf_fields:
-            field_name = pdf_field["name"]
-            canonical = None
-            confidence = MappingConfidence.LOW
-
-            normalized = _normalize_key(field_name)
-            if normalized in otp_exact_map_normalized:
-                canonical = otp_exact_map_normalized[normalized]
-                confidence = MappingConfidence.HIGH
+        otp_exact_map_normalized = {
+            _normalize_key(k): (v if isinstance(v, tuple) else (v, None))
+            for k, v in OTP_EXACT_MAP.items()
+        }
+        for pdf_field in pdf_fields:
+            field_name = pdf_field["name"]
+            canonical = None
+            fragment = None
+            confidence = MappingConfidence.LOW
+
+            normalized = _normalize_key(field_name)
+            if normalized in otp_exact_map_normalized:
+                canonical, fragment = otp_exact_map_normalized[normalized]
+                confidence = MappingConfidence.HIGH
             else:
                 # ... (existing keyword-map fallback unchanged) ...

             fields.append(RecognizedField(
                 pdf_field_name=field_name,
                 ...
                 canonical_field=canonical,
                 confidence=confidence,
                 page_number=pdf_field.get("page", 1),
+                fragment=fragment,
             ))
```

### B-5: Add the missing canonicals to `_participant_to_dict`

```diff
@@ src/main.py  (_participant_to_dict, after line 551) @@
             "Contact.Birthdate_day": p.birth_date.strftime("%d") if p.birth_date else "",
+            # Residence-since date — needed by SZA_IG_mióta_lakik_* boxes
+            # (currently NOT a Participant field — see Fix C-2 if you want
+            # to source it from SF).
+            # For now: leave empty until Participant gains the field.
```

(Properly addressing finding 1.2 requires adding `residence_since` to `Participant` per Fix C-2.)

### B-6: Curate the existing production mapping

Edit `src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json` — find the six date-box rows and add `"fragment": "year|month|day"`. For example:
```json
{ "pdf_field_name": "SZA_IG_szül_év", "canonical_field": "Contact.Birthdate_year",
  "fragment": null, ... }
```
(Or change canonical to `Contact.Birthdate` + fragment — either works since `_participant_to_dict` already produces `_year/_month/_day`.)

**Test that must pass:** `test_apply_fragment` (§2.6), `test_birth_date_fragments_are_split` (§3.2).
**Risk:** medium. The new `fragment` field is optional everywhere (defaults to None) so existing mapping JSONs keep loading unchanged. The risk is in the OTP_EXACT_MAP value-type change (str → tuple) — every consumer of `OTP_EXACT_MAP` must be checked; there is currently only one consumer (`_heuristic_map_fields`).
**Files:** `src/ai/field_recognizer.py`, `src/main.py`, `src/mapping/igenylesi_dokumentumok_elso_6_oldal_mapping.json` (and any other affected OTP mappings).

---

## Fix E — AcroForm checkbox export values  (MEDIUM · finding B4)

**File:** `src/engine/pdf_filler.py:331-343, 469-479`.

```diff
@@ src/engine/pdf_filler.py  (add a static method on AcroFormFiller, near _is_truthy) @@
+    @staticmethod
+    def _checkbox_on_state(field_obj) -> "Any":
+        """Return the /On appearance-state name for a /Btn field by inspecting
+        /AP /N. Falls back to /Yes (reportlab's default) when /AP is absent.
+        """
+        import pikepdf
+        try:
+            ap = field_obj.get("/AP")
+            if ap and "/N" in ap:
+                states = [str(k) for k in ap["/N"].keys() if str(k) != "/Off"]
+                if states:
+                    return pikepdf.Name(states[0])
+        except Exception:
+            pass
+        return pikepdf.Name("/Yes")
+
     @staticmethod
     def _is_truthy(value: Any) -> bool:
         if isinstance(value, bool):
             return value
         s = str(value).strip().lower()
-        if s in ("", "0", "false", "no", "nem", "off", "false", "x-", "-"):
+        if s in ("", "0", "false", "no", "nem", "off", "x-", "-"):
             return False
         if s in ("yes", "igen", "true", "1", "x", "y", "i", "on"):
             return True
-        return s != ""
+        return True  # any other non-empty token counts as truthy


@@ _fill_fields_recursive, lines 331-343 @@
                     if field_type == "/Btn":
                         if isinstance(value, str) and value not in ("igen", "nem", "true", "false", "True", "False", "1", "0", ""):
                             field_obj[pikepdf.Name("/V")] = pikepdf.Name(f"/{value}")
                         else:
                             is_checked = self._is_truthy(value)
+                            on_state = self._checkbox_on_state(field_obj)
                             if is_checked:
-                                field_obj[pikepdf.Name("/V")] = pikepdf.Name("/Yes")
+                                field_obj[pikepdf.Name("/V")] = on_state
                             else:
                                 field_obj[pikepdf.Name("/V")] = pikepdf.Name("/Off")
```

**Test that must pass:** `test_checkbox_with_non_yes_export_value` (§4.1).
**Risk:** low — the fallback is `/Yes`, which is current behaviour.
**Files:** `src/engine/pdf_filler.py`.

---

## Fix G — Address parser robustness  (HIGH · finding 6)

**File:** `src/integrations/salesforce_client.py:100-156` (rewrite).

```diff
@@ src/integrations/salesforce_client.py  (replace _parse_address_string, lines 100-156) @@
+import re as _re
+
+_ADDR_DISTRICT_RE = _re.compile(
+    r"^\s*((?:[IVX]+\d*)\s*\.?\s*ker(?:ület)?\.?)\s*,?\s*",
+    _re.IGNORECASE,
+)
+_ADDR_ZIP_RE = _re.compile(r"\b(\d{4})\b")
+_ADDR_HOUSE_RE = _re.compile(r"(?:^|\s+)(\d+[A-Za-z]?(?:[/-]\d+[A-Za-z]?)?)\b")
+_ADDR_FLOOR_RE = _re.compile(r"(\d+)\.?\s*(?:em|emelet)\.?", _re.IGNORECASE)
+_ADDR_DOOR_RE  = _re.compile(r"(\d+)\.?\s*(?:aj|ajto|ajtó)\.?", _re.IGNORECASE)
+_ADDR_COUNTRIES = {"austria", "germany", "slovakia", "romania", "serbia",
+                   "uk", "usa", "united states", "united kingdom"}

     def _parse_address_string(self, addr_str: str, zip_code: str = None) -> dict:
-        """Parses a Hungarian address string into a structured dictionary."""
-        if not addr_str:
-            return {"zip_code": zip_code or "", "city": "", "street": "", "house_number": ""}
-
-        import re
-        # ... (the existing implementation that we replace wholesale) ...
+        """Parse a Hungarian address string into a structured dictionary.
+
+        Always returns the same dict shape: {zip_code, city, street,
+        house_number, floor, door}. Empty/None inputs are safe.
+        """
+        empty = {"zip_code": zip_code or "", "city": "", "street": "",
+                 "house_number": "", "floor": None, "door": None}
+        if not addr_str or not addr_str.strip():
+            return empty
+
+        s = addr_str.strip()
+
+        # 1. Strip a leading district token ("Budapest VII. kerület, ...")
+        district = None
+        m = _ADDR_DISTRICT_RE.match(s)
+        if m:
+            district = m.group(1)
+            s = s[m.end():].strip(" ,")
+
+        # 2. Extract 4-digit zip
+        zm = _ADDR_ZIP_RE.search(s)
+        detected_zip = zm.group(1) if zm else (zip_code or "")
+        if zm:
+            s = (s[:zm.start()] + s[zm.end():]).strip(" ,")
+
+        # 3. Split remaining on commas -> [city, street+num, ...]
+        parts = [p.strip() for p in s.split(",") if p.strip()]
+        city = ""
+        street_and_num = ""
+        if parts:
+            # Pop trailing country if present
+            if parts[-1].lower() in _ADDR_COUNTRIES:
+                parts.pop()
+            if parts:
+                city = parts[0]
+                street_and_num = ", ".join(parts[1:]) if len(parts) > 1 else ""
+                # If only one part and it looks like a street (has a house number),
+                # treat the whole thing as the street.
+                if len(parts) == 1 and _ADDR_HOUSE_RE.search(parts[0]):
+                    city = ""
+                    street_and_num = parts[0]
+        # Reattach district info to the city for round-tripping
+        if district and city.lower().startswith("budapest"):
+            city = f"Budapest, {district}"
+
+        # 4. House number, floor, door
+        house_number = ""
+        street = street_and_num
+        floor = None
+        door = None
+        hm = _ADDR_HOUSE_RE.search(street_and_num)
+        if hm:
+            house_number = hm.group(1)
+            street = (street_and_num[:hm.start()] + street_and_num[hm.end():]).strip(" ,")
+        fm = _ADDR_FLOOR_RE.search(street_and_num)
+        if fm:
+            floor = fm.group(1)
+        dm = _ADDR_DOOR_RE.search(street_and_num)
+        if dm:
+            door = dm.group(1)
+
+        return {
+            "zip_code": str(detected_zip),
+            "city": city,
+            "street": street,
+            "house_number": house_number,
+            "floor": floor,
+            "door": door,
+        }
```

**Test that must pass:** `test_parse_address` parametric suite (§2.3).
**Risk:** low — only used by `_normalize_participant` in live SF mode and is currently broken for the same edge cases.
**Files:** `src/integrations/salesforce_client.py`.

---

## Fix B2 — Wire the CompletenessChecker to the right vocabulary  (HIGH · finding B2)

**File:** `src/main.py:253-273`.

The simplest fix is to translate the SF-style required fields into the canonical-model paths the checker actually understands:

```diff
@@ src/main.py  (_check_completeness) @@
     def _check_completeness(self, deal: DealData, mapping: MappingConfig):
-        """
-        Teljességellenőrzés a professional CompletenessChecker-rel.
-        ...
-        """
-        required_fields = [
-            "Contact.Loan_amount__c",
-            "Contact.Loan_period__c",
-            "Contact.Name",
-            "Contact.Szuletesi_nev__c",
-            "Contact.Mother_s_Name__c",
-            "Contact.Birthdate",
-            "Contact.Place_of_Birth__c",
-            "Contact.Tax_ID__c",
-            "Contact.MobilePhone",
-        ]
+        """Teljességellenőrzés — required fields expressed in the canonical
+        model vocabulary that CompletenessChecker actually understands
+        (loan.* / participant.* / property.* / deal_id), not in SF API names.
+        """
+        required_fields = [
+            "loan.loan_amount",
+            "loan.loan_term_months",
+            "participant.*.name",
+            "participant.*.birth_name",
+            "participant.*.mother_name",
+            "participant.*.birth_date",
+            "participant.*.birth_place",
+            "participant.*.tax_id",
+            "participant.*.phone",
+        ]
         checker = CompletenessChecker(run_suspicious_checks=True)
         report = checker.check(deal, required_fields)
         return report
```

**Test that must pass:** `test_completeness_checker_understands_its_required_fields`, `test_pipeline_completeness_does_not_always_block` (§2.5).
**Risk:** low. The pipeline currently doesn't act on the checker's output (it just logs), so this is purely about making the warnings truthful.
**Files:** `src/main.py`.

---

## Fix 7.1 — Optimistic concurrency for field-level updates  (HIGH · finding 7.1)

**Files:** `backend/server.py:302-387`, `backend/mapping_service.py`.

Two-part fix:

### 7.1a: Take-and-hold a per-pdf lock across load–mutate–save

```diff
@@ backend/mapping_service.py  (add a per-pdf-id lock dict, near line 40) @@
+_LOCKS: dict[str, threading.RLock] = {}
+_LOCKS_GUARD = threading.Lock()
+
+def _lock_for(pdf_id: str) -> threading.RLock:
+    with _LOCKS_GUARD:
+        if pdf_id not in _LOCKS:
+            _LOCKS[pdf_id] = threading.RLock()
+        return _LOCKS[pdf_id]


@@ backend/server.py  (update_field, lines 302-311) @@
 @app.put("/api/mapping/field")
 def update_field(body: FieldUpdate, pdf_id: str = Query(...), field: str = Query(...)):
     field_name = urllib.parse.unquote(field)
-    data = mapping_service.load(pdf_id)
-    try:
-        updated = mapping_service.update_field(data, field_name, body.model_dump(exclude_none=True))
-    except KeyError:
-        raise HTTPException(404, f"field not found: {field_name}")
-    save_res = mapping_service.save(pdf_id, data)
-    return {"field": updated, "_mtime": save_res["mtime"]}
+    from mapping_service import _lock_for
+    client_mtime = body._mtime if hasattr(body, "_mtime") else None
+    with _lock_for(pdf_id):
+        data = mapping_service.load(pdf_id)
+        # Optional: enforce optimistic concurrency at field level too
+        # if client_mtime and abs(data.get("_mtime", 0) - client_mtime) > 0.001:
+        #     raise HTTPException(409, "mapping modified externally — reload")
+        try:
+            updated = mapping_service.update_field(data, field_name, body.model_dump(exclude_none=True))
+        except KeyError:
+            raise HTTPException(404, f"field not found: {field_name}")
+        save_res = mapping_service.save(pdf_id, data)
+        return {"field": updated, "_mtime": save_res["mtime"]}
```

Apply the same `with _lock_for(pdf_id):` wrapper to `add_field`, `delete_field`, `create_group`, `update_group`, `delete_group`, and `save_mapping`.

### 7.1b: Make the full-save endpoint's `_mtime` check actually work end-to-end

The current `save_mapping` reads `body.get("_mtime")` from the request body (line 274). The frontend must send this on every save; verify `frontend/src/api/client.ts` does so. (The HEAD commit's "refresh button" suggests this is partially in place.)

**Test that must pass:** `test_concurrent_field_updates_do_not_clobber` (§8.1).
**Risk:** low — RLock is reentrant and the load/save sequence is fast.
**Files:** `backend/server.py`, `backend/mapping_service.py`.

---

## Fix 4.4 — Correct the parcel-number mapping in KEYWORD_MAP  (MEDIUM · finding 4.4)

```diff
@@ src/ai/field_recognizer.py  (KEYWORD_MAP, lines 1540-1542) @@
-            "helyrajzi_szam": "Lead.Ingatlan_jellege__c",
-            "hrsz": "Lead.Ingatlan_jellege__c",
-            "parcel_number": "Lead.Ingatlan_jellege__c",
+            "helyrajzi_szam": "Lead.Ingatlan_megjegyzes__c",
+            "hrsz":           "Lead.Ingatlan_megjegyzes__c",
+            "parcel_number":  "Lead.Ingatlan_megjegyzes__c",
```

**Test that must pass:** new unit test on `_heuristic_map_fields` that asserts each of the three keywords maps to `Lead.Ingatlan_megjegyzes__c`.
**Risk:** very low.
**Files:** `src/ai/field_recognizer.py`.

---

## Fix 4.3 — Resolve the `Lead.Ingatlan_megjegyzes__c` collision in `otp_acroform_mapping.json`  (HIGH · finding 4.3)

The sample fixture maps three conceptually-distinct fields (`property_parcel_number`, `property_rooms`, `property_year_built`) onto one canonical. The fix is a data edit, not a code edit:

```diff
@@ src/mapping/otp_acroform_mapping.json  (the three affected field records) @@
 { "pdf_field_name": "property_parcel_number", "canonical_field": "Lead.Ingatlan_megjegyzes__c", ... },
-{ "pdf_field_name": "property_rooms",         "canonical_field": "Lead.Ingatlan_megjegyzes__c", ... },
-{ "pdf_field_name": "property_year_built",    "canonical_field": "Lead.Ingatlan_megjegyzes__c", ... },
+{ "pdf_field_name": "property_rooms",         "canonical_field": null, ... },
+{ "pdf_field_name": "property_year_built",    "canonical_field": null, ... },
```
Set them to `null` (unmapped) until the canonical catalog grows fields for room count and year built (or remap to existing fields if appropriate, e.g. rooms → there is no canonical for room count today; year_built → no canonical either).

**Test that must pass:** `test_no_two_distinct_pdf_fields_share_canonical` — a guard test:
```python
def test_no_two_distinct_pdf_fields_share_canonical():
    m = json.load(open("src/mapping/otp_acroform_mapping.json"))
    canon_to_pdf = {}
    for f in m["fields"]:
        c = f.get("canonical_field")
        if not c:
            continue
        # Allow the same canonical for borrower_X / coborrower_X pairs (different instances)
        # but not for unrelated field names.
        canon_to_pdf.setdefault(c, []).append(f["pdf_field_name"])
    for c, names in canon_to_pdf.items():
        if len(names) > 1:
            # OK if names differ only by borrower_/coborrower_ prefix
            stems = {n.replace("coborrower_", "").replace("borrower_", "")
                      .replace("property2_", "").replace("property_", "")
                      for n in names}
            assert len(stems) == 1, \
                f"canonical {c} mapped to unrelated fields: {names}"
```

**Risk:** low — only the sample fixture changes.
**Files:** `src/mapping/otp_acroform_mapping.json`.

---

## Fix B3 — Tests must not call live APIs  (HIGH · finding B3)

```diff
@@ tests/conftest.py  (create the file if missing) @@
+import pytest
+
+
+@pytest.fixture(autouse=True)
+def _force_offline(monkeypatch):
+    """Force every test into offline mode — no live Anthropic or SF calls."""
+    for var in ("ANTHROPIC_API_KEY", "SF_USERNAME", "SF_PASSWORD",
+                "SF_SECURITY_TOKEN", "SF_DOMAIN"):
+        monkeypatch.delenv(var, raising=False)
+

@@ pytest.ini  (create or update) @@
+[pytest]
+testpaths = tests
+addopts = -ra -m "not live_api" --strict-markers
+markers =
+    live_api: tests that exercise the real Anthropic API (deselected by default)
```

**Test that must pass:** the suite runs in <10s with no network calls. Verify with `pytest --cov=src -m "not live_api"` and a packet capture / `ANTHROPIC_API_KEY=nonempty pytest` should still run heuristic-only.
**Risk:** none.
**Files:** `tests/conftest.py`, `pytest.ini`.

---

## Fix 7.2 — Stop returning ghost `filled_fields` / `skipped_fields` from the API  (MEDIUM · finding 7.2)

Two options:

### 7.2a (preferred): make `run_for_deal` actually return the lists

```diff
@@ src/main.py  (run_for_deal, around lines 199-237) @@
         # 5. PDF kitöltés
         logger.info("📝 5. PDF kitöltés")
         try:
             assembler = DocumentAssembler()
             actual_template = template_pdf
             ...
             output_path = self._fill_pdf(actual_template, deal, field_data, mapping_config)
             result["output_path"] = str(output_path)
+            result["filled_fields"] = list(field_data.keys())
+            result["filled_field_count"] = len(field_data)
+            result["skipped_field_count"] = max(
+                0, len(mapping_config.fields) - len(field_data))
             logger.info(f"   ✓ Kitöltött PDF: {output_path}")
         except Exception as e:
             result["issues"].append(f"PDF kitöltési hiba: {e}")
```

### 7.2b (alternative): drop the fields from the API response

```diff
@@ backend/server.py  (fill_pdf endpoint, lines 691-697) @@
         return {
             "success": True,
             "filled_pdf_url": download_url,
             "deal_id": deal_id,
-            "filled_fields": result.get("filled_fields", []),
-            "skipped_fields": result.get("skipped_fields", []),
+            "filled_field_count": len(field_data) if 'field_data' in dir() else None,
         }
```

**Recommended:** 7.2a — gives useful telemetry everywhere.
**Test that must pass:** `test_fill_endpoint_returns_real_field_count`.
**Risk:** none.
**Files:** `src/main.py` (7.2a) or `backend/server.py` (7.2b).

---

## Fix 7.5 — Make `_fuzzy_match_canonical` actually case-insensitive  (MEDIUM · finding 7.5)

```diff
@@ src/ai/field_recognizer.py  (_fuzzy_match_canonical, lines 866-892) @@
     @staticmethod
     def _fuzzy_match_canonical(candidate: str, valid_keys: set[str]) -> str | None:
         """Try to match an AI-returned canonical key to a valid one."""
         c = candidate.lower().strip().replace(" ", "_").replace("-", "_")
-        if c in valid_keys:
+        # Build a lowercase index ONCE per call (small set, acceptable cost):
+        valid_lower = {k.lower(): k for k in valid_keys}
+        if c in valid_lower:
-            return c
+            return valid_lower[c]

         for key in valid_keys:
-            if c in key or key in c:
+            if c in key.lower() or key.lower() in c:
                 return key
             c_parts = c.split(".")
             k_parts = key.split(".")
             if len(c_parts) >= 2 and len(k_parts) >= 2 and c_parts[0] == k_parts[0].lower() and c_parts[-1] == k_parts[-1].lower():
                 return key

         # NOTE: do NOT auto-accept invented participant.*/loan.* canonicals —
-        for prefix in ("participant.", "property.", "loan.", "csok.", "document.", "declaration.", "signature.", "vat.", "appraisal."):
-            if c.startswith(prefix):
-                return c
+        # main.py has no source for them, so they'd silently never be filled.
+        # If you want to accept them, also wire them into _participant_to_dict.

         return None
```

**Test that must pass:** new unit test on `_fuzzy_match_canonical("contact.name", {"Contact.Name"})` returns `"Contact.Name"` (currently returns None).
**Risk:** low.
**Files:** `src/ai/field_recognizer.py`.

---

## Fix B11 — `get_attachments` is broken in live mode  (MEDIUM · finding B11)

```diff
@@ src/integrations/salesforce_client.py  (get_attachments, lines 440-446) @@
         else:
+            # simple-salesforce query() does NOT support kwargs for bind
+            # variables; interpolate the (18-char SF ID, no injection risk)
+            # directly with single quotes.
+            safe_id = deal_id.replace("'", "")  # defensive
             query = (
                 "SELECT ContentDocument.Title, ContentDocument.CreatedDate "
                 "FROM ContentDocumentLink "
-                "WHERE LinkedEntityId = :deal_id"
+                f"WHERE LinkedEntityId = '{safe_id}'"
             )
-            result = self._sf.query(query, deal_id=deal_id)
+            result = self._sf.query(query)
             return result.get("records", [])
```

**Test that must pass:** with a `simple_salesforce` mock; assert `query` is called with a string (no kwargs).
**Risk:** none — the current code raises `TypeError` in live mode; any change is strictly better.
**Files:** `src/integrations/salesforce_client.py`.

---

## 3.3 is documented as "no easy fix" — architectural

Bug 3.3 (duplicated AcroForm pages share field names) has no clean drop-in fix. The options are:

1. **Field renaming per copy** — when the assembler appends the second copy of `SZA_IG_név`, rename it to `SZA_IG_név__p2`. Requires walking every duplicated field, regenerating `/AP` appearances, and updating the mapping to reference the new names. Non-trivial in pikepdf but doable.
2. **Switch to overlay mode for duplicated sections** — render participant data via coordinates rather than AcroForm fields. Loses the AcroForm editability for those sections.
3. **Drop the multi-copy feature** — require the operator to upload separate per-participant forms. Simplest; loses the "auto-stapler" pitch.

The choice is a product decision. Code-side, the read-back test `test_assemble_duplicated_pages_have_unique_field_names` (§7 of strategy) will document the current breakage and prevent it being silently relied on.

---

## Rollout order

Order is chosen to unblock trustworthy testing first, then stop silent corruption, then improve correctness.

| # | Fix | Finding | Effort | Unblocks |
|---|---|---|---|---|
| 1 | **Fix F** (mock correctness) | B1 | 5 min | every integration test |
| 2 | **Fix B3** (test hermeticity) | B3 | 10 min | CI reliability |
| 3 | **Fix D** (`_fmt_money`) | 5 | 30 min | money formatting tests |
| 4 | **Fix B2** (completeness vocabulary) | B2 | 15 min | honest completeness warnings |
| 5 | **Fix C** (ghost model fields) | 4.1 | 1 hr | read-back tests on those keys |
| 6 | **Fix G** (address parser) | 6 | 1 hr | address tests |
| 7 | **Fix 4.4 / 4.3** (mapping data) | 4.4, 4.3 | 30 min | correct sample fixture |
| 8 | **Fix B** (fragments) | 1.1, 1.2, 2 | 3 hr | date / digit-box fields |
| 9 | **Fix A** (multi-instance routing) | 3.1, 3.2 | 1 day | the big one |
| 10 | **Fix 7.1** (concurrency) | 7.1 | 2 hr | mapping editor safety |
| 11 | **Fix E** (checkbox states) | B4 | 1 hr | non-`/Yes` checkboxes |
| 12 | **Fixes 7.5, B11, 7.2** | 7.5, B11, 7.2 | 1 hr | cleanup |

After each fix, run the full suite plus the new tests added for that fix. The 52 currently-passing tests should remain green throughout (Fix A's read-back tests will *replace* any existing test that asserts on the buggy behaviour, if such tests existed — they don't today).

**Estimated total: 2-3 days of focused work** to land fixes 1-11, after which the system will:
- never silently process the wrong deal,
- never silently drop participant/property data,
- never silently put the wrong value in year/month/day boxes,
- never silently collapse a `0` amount to empty,
- have a hermetic CI suite that runs in seconds.

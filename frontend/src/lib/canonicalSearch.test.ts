// Cutover Task 4 — canonical search/grouping over the shared Genie registry.
//
// The registry expands to thousands of concrete paths (participant roles ×
// indexes, property[1..9], income/existing_loan[1..12]); the Studio picker
// must group (deal / account / participant.<role> / property / income /
// existing_loan), search, and CAP rendering instead of mounting one huge
// list. This is a pure-logic test module: `node src/lib/canonicalSearch.test.ts`
// runs it (Node 24 type stripping), `tsc --noEmit` typechecks it.
import assert from "node:assert/strict";

import {
  GROUP_LABELS,
  GROUP_ORDER,
  buildGroupChips,
  canonicalGroupOf,
  capGroups,
  filterCanonicals,
  groupCanonicals,
  type CanonicalFieldLite,
} from "./canonicalSearch.ts";

function field(path: string, label = "label", type = "string"): CanonicalFieldLite {
  return { path, label, type };
}

// ── grouping ────────────────────────────────────────────────────────────────

assert.equal(canonicalGroupOf("deal.loan_amount"), "deal");
assert.equal(canonicalGroupOf("account.name"), "account");
assert.equal(canonicalGroupOf("property.address.city"), "property");
assert.equal(canonicalGroupOf("property[9].address.city"), "property");
assert.equal(canonicalGroupOf("participant.borrower.name"), "participant.borrower");
assert.equal(canonicalGroupOf("participant.co_borrower[2].tax_id"), "participant.co_borrower");
assert.equal(canonicalGroupOf("participant.guarantor.phone"), "participant.guarantor");
assert.equal(canonicalGroupOf("participant.mortgagor.name"), "participant.mortgagor");
assert.equal(canonicalGroupOf("participant.usufructuary.name"), "participant.usufructuary");
assert.equal(canonicalGroupOf("participant.borrower.income[1].amount"), "income");
assert.equal(canonicalGroupOf("participant.co_borrower.existing_loan[12].monthly_payment"), "existing_loan");

// Every expected group has a reviewed Hungarian label and a deterministic order.
for (const key of [
  "deal",
  "account",
  "participant.borrower",
  "participant.co_borrower",
  "participant.guarantor",
  "participant.mortgagor",
  "participant.usufructuary",
  "property",
  "income",
  "existing_loan",
]) {
  assert.ok(GROUP_ORDER.includes(key), `GROUP_ORDER missing ${key}`);
  assert.ok(GROUP_LABELS[key], `GROUP_LABELS missing ${key}`);
}

// ── grouping + ordering over a mixed catalog ───────────────────────────────

const catalog: CanonicalFieldLite[] = [
  field("deal.loan_amount", "Hitelösszeg", "money"),
  field("participant.borrower.name", "Teljes név"),
  field("account.name", "Ügyfél neve"),
  field("existing_loan placeholder", "x"), // unknown group sorts last, deterministically
  field("participant.borrower.income[1].amount", "Jövedelem összege", "money"),
  field("property.address.city", "Ingatlan település"),
  field("participant.co_borrower.name", "Adóstárs neve"),
];

const grouped = groupCanonicals(catalog);
const keys = grouped.map((g) => g.key);
assert.deepEqual(
  keys.slice(0, 7),
  [
    "deal",
    "account",
    "participant.borrower",
    "participant.co_borrower",
    "property",
    "income",
    "existing_loan placeholder", // unknown groups appended in catalog order
  ],
  `unexpected group order: ${JSON.stringify(keys)}`
);
assert.equal(grouped[0].label, GROUP_LABELS["deal"]);
assert.deepEqual(
  grouped[0].fields.map((f) => f.path),
  ["deal.loan_amount"]
);

// ── search: path, label, accent-insensitive ────────────────────────────────

const searchable: CanonicalFieldLite[] = [
  field("deal.loan_amount", "Hitelösszeg", "money"),
  field("participant.borrower.tax_id", "Adóazonosító jel"),
  field("property.parcel_number", "Helyrajzi szám"),
];

assert.deepEqual(
  filterCanonicals(searchable, "tax_id").map((f) => f.path),
  ["participant.borrower.tax_id"]
);
assert.deepEqual(
  filterCanonicals(searchable, "helyrajzi").map((f) => f.path),
  ["property.parcel_number"]
);
// Accent-insensitive: "hitelosszeg" finds "Hitelösszeg".
assert.deepEqual(
  filterCanonicals(searchable, "hitelosszeg").map((f) => f.path),
  ["deal.loan_amount"]
);
// Empty query returns everything unchanged.
assert.equal(filterCanonicals(searchable, "").length, 3);
assert.equal(filterCanonicals(searchable, "   ").length, 3);

// ── capping: thousands of expanded paths never render as one huge list ─────

const big: CanonicalFieldLite[] = [];
for (const role of ["borrower", "co_borrower", "guarantor", "mortgagor", "usufructuary"]) {
  for (let i = 0; i < 131; i++) {
    big.push(field(`participant.${role}.field_${i}`, `Mező ${i}`));
  }
}
for (let i = 1; i <= 9; i++) big.push(field(`property[${i}].address.city`, "Település"));
for (let i = 1; i <= 12; i++)
  big.push(field(`participant.borrower.income[${i}].amount`, "Jövedelem", "money"));
// 5*131 + 9 + 12 = 676 entries — representative of the expanded registry shape.
assert.equal(big.length, 676);

const PER_GROUP = 25;
const MAX_TOTAL = 120;
const capped = capGroups(groupCanonicals(big), { perGroup: PER_GROUP, maxTotal: MAX_TOTAL });
const rendered = capped.groups.reduce((sum, g) => sum + g.fields.length, 0);
assert.ok(rendered <= MAX_TOTAL, `rendered ${rendered} rows exceeds cap`);
assert.ok(capped.hiddenTotal > 0, "hidden count must be positive when truncating");
const borrowerGroup = capped.groups.find((g) => g.key === "participant.borrower")!;
assert.ok(borrowerGroup.fields.length <= PER_GROUP);
assert.equal(
  borrowerGroup.hidden,
  131 - borrowerGroup.fields.length,
  "hidden count = group size - rendered"
);
// Every group reports its hidden remainder, so the UI can hint precisely.
for (const g of capped.groups) {
  assert.ok(g.hidden >= 0);
  assert.ok(g.fields.length + g.hidden > 0);
}

// Small catalogs are never truncated unnecessarily.
const small = capGroups(groupCanonicals(searchable), {
  perGroup: PER_GROUP,
  maxTotal: MAX_TOTAL,
});
assert.equal(small.hiddenTotal, 0);
assert.equal(small.groups.reduce((s, g) => s + g.fields.length, 0), searchable.length);

// ── chips: group counts over the whole catalog (not the filtered subset) ──

const chips = buildGroupChips(big);
const chipMap = new Map(chips.map((c) => [c.key, c.count]));
assert.equal(chipMap.get("participant.borrower"), 131);
assert.equal(chipMap.get("property"), 9);
assert.equal(chipMap.get("income"), 12);
// Chip order follows GROUP_ORDER restricted to the groups present.
assert.deepEqual(
  chips.map((c) => c.key),
  [
    "participant.borrower",
    "participant.co_borrower",
    "participant.guarantor",
    "participant.mortgagor",
    "participant.usufructuary",
    "property",
    "income",
  ]
);


// ── review fix 4: precomputed (cached) folded search index ─────────────────

import {
  buildFoldedIndex,
  filterFoldedIndex,
  foldText,
} from "./canonicalSearch.ts";

assert.equal(foldText("Hitelösszeg"), "hitelosszeg");
assert.equal(foldText("  Jövedelem  "), "jovedelem");

const foldCatalog: CanonicalFieldLite[] = [
  field("deal.loan_amount", "Hitelösszeg", "money"),
  field("participant.borrower.tax_id", "Adóazonosító jel"),
  field("property.parcel_number", "Helyrajzi szám"),
  field("account.name", "Ügyfél neve"),
];

const foldedIndex = buildFoldedIndex(foldCatalog);
assert.equal(foldedIndex.length, foldCatalog.length);
// The folded forms are precomputed once, not per keystroke.
assert.equal(foldedIndex[0].foldedPath, "deal.loan_amount");
assert.equal(foldedIndex[0].foldedLabel, "hitelosszeg");
assert.equal(foldedIndex[1].foldedLabel, "adoazonosito jel");

// Filtering through the cached index is equivalent to filterCanonicals.
for (const query of ["", "tax_id", "hitelosszeg", "helyrajzi", "UGYFEL", "név"]) {
  const viaIndex = filterFoldedIndex(foldedIndex, query).map((f) => f.path);
  const direct = filterCanonicals(foldCatalog, query).map((f) => f.path);
  assert.deepEqual(viaIndex, direct, `query=${query}`);
}
assert.deepEqual(
  filterFoldedIndex(foldedIndex, "hitelosszeg").map((f) => f.path),
  ["deal.loan_amount"]
);
assert.deepEqual(
  filterFoldedIndex(foldedIndex, "ugyfel").map((f) => f.path),
  ["account.name"]
);

// ── review fix 2: keyboard active-option navigation helper ─────────────────

import { moveActive } from "./canonicalSearch.ts";

// Empty option lists stay inert.
assert.equal(moveActive(null, 1, 0), null);
assert.equal(moveActive(0, 1, 0), null);
// First ArrowDown from nothing selects the first option; ArrowUp from
// nothing selects the last (standard combobox wrap behaviour).
assert.equal(moveActive(null, 1, 5), 0);
assert.equal(moveActive(null, -1, 5), 4);
// Moves step and wrap around in both directions.
assert.equal(moveActive(0, -1, 5), 4);
assert.equal(moveActive(4, 1, 5), 0);
assert.equal(moveActive(2, 1, 5), 3);
assert.equal(moveActive(2, -1, 5), 1);
// Single option list is always 0 once active.
assert.equal(moveActive(null, 1, 1), 0);
assert.equal(moveActive(0, 1, 1), 0);
assert.equal(moveActive(0, -1, 1), 0);
// Out-of-range current index is tolerated (clamps into range).
assert.equal(moveActive(9, 1, 5), 0);
assert.equal(moveActive(-3, -1, 5), 4);

console.log("canonicalSearch review-fix tests: all assertions passed");

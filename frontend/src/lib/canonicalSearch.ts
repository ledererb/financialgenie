// ---------------------------------------------------------------------------
// canonicalSearch — search/group/cap helpers over the shared Genie registry
// (Cutover Task 4).
//
// The registry's concrete expansion is thousands of paths (5 participant
// roles × fields × indexes 1..3, property[1..9], income/existing_loan[1..12]).
// The Studio pickers group them (deal / account / participant.<role> /
// property / income / existing_loan), search accent-insensitively, and CAP
// the rendered rows — never one huge unvirtualized list.
// ---------------------------------------------------------------------------

/** The subset of the canonical field shape these helpers need. */
export interface CanonicalFieldLite {
  path: string;
  label?: string;
  type?: string;
}

/** Reviewed, deterministic group order (known groups first, unknown last). */
export const GROUP_ORDER: readonly string[] = [
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
];

/** Reviewed Hungarian display labels for the canonical groups. */
export const GROUP_LABELS: Readonly<Record<string, string>> = {
  deal: "Ügylet (deal)",
  account: "Ügyfél (account)",
  "participant.borrower": "Adós (participant.borrower)",
  "participant.co_borrower": "Adóstárs (participant.co_borrower)",
  "participant.guarantor": "Kezes (participant.guarantor)",
  "participant.mortgagor": "Zálogkötelezett (participant.mortgagor)",
  "participant.usufructuary": "Haszonélvező (participant.usufructuary)",
  property: "Ingatlan (property)",
  income: "Jövedelem (income)",
  existing_loan: "Fennálló hitel (existing_loan)",
};

const PARTICIPANT_ROLE = /^participant\.(borrower|co_borrower|guarantor|mortgagor|usufructuary)(\[|\.)?/;

/** The canonical group a concrete Genie path belongs to. */
export function canonicalGroupOf(path: string): string {
  if (!path) return "other";
  if (path.startsWith("deal.")) return "deal";
  if (path.startsWith("account.")) return "account";
  if (path.startsWith("property.") || /^property\[\d+\]\./.test(path)) {
    return "property";
  }
  const participant = PARTICIPANT_ROLE.exec(path);
  if (participant) {
    const role = participant[1];
    if (path.includes(".income[")) return "income";
    if (path.includes(".existing_loan[")) return "existing_loan";
    return `participant.${role}`;
  }
  return path.split(".")[0] || "other";
}

/** Accent-insensitive, lowercase fold for Hungarian search matching. */
export function foldText(value: string): string {
  return (value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

const fold = foldText;

/** A catalog entry with its folded path/label precomputed (review fix 4). */
export interface FoldedCanonicalField {
  field: CanonicalFieldLite;
  foldedPath: string;
  foldedLabel: string;
}

/**
 * Precompute the folded search forms ONCE per catalog (review fix 4).
 *
 * The registry expansion is immutable for a session; folding 6.5k paths on
 * every keystroke is wasted work. Build this index once (component
 * `useMemo`) and filter through :func:`filterFoldedIndex`.
 */
export function buildFoldedIndex(
  fields: CanonicalFieldLite[]
): FoldedCanonicalField[] {
  return fields.map((field) => ({
    field,
    foldedPath: foldText(field.path),
    foldedLabel: foldText(field.label ?? ""),
  }));
}

/** Case/accent-insensitive search over a precomputed folded index. */
export function filterFoldedIndex(
  index: FoldedCanonicalField[],
  query: string
): CanonicalFieldLite[] {
  const q = foldText(query);
  if (!q) return index.map((entry) => entry.field);
  return index
    .filter((e) => e.foldedPath.includes(q) || e.foldedLabel.includes(q))
    .map((e) => e.field);
}

/** Case/accent-insensitive path+label search over the catalog (convenience
 * wrapper: folds on the fly — prefer the cached index in hot paths). */
export function filterCanonicals(
  fields: CanonicalFieldLite[],
  query: string
): CanonicalFieldLite[] {
  return filterFoldedIndex(buildFoldedIndex(fields), query);
}

/**
 * Move the active option index for combobox keyboard navigation
 * (review fix 2). ``delta`` is +1 (ArrowDown) or −1 (ArrowUp); navigation
 * wraps around; ``null`` (nothing active) starts at the first (down) or
 * last (up) option. Returns ``null`` when there is nothing to navigate.
 */
export function moveActive(
  current: number | null,
  delta: number,
  optionCount: number
): number | null {
  if (optionCount <= 0) return null;
  if (current === null || current < 0 || current >= optionCount) {
    return delta >= 0 ? 0 : optionCount - 1;
  }
  return (current + delta + optionCount) % optionCount;
}

export interface CanonicalGroup {
  key: string;
  label: string;
  fields: CanonicalFieldLite[];
}

/**
 * Group fields by canonical group in the reviewed GROUP_ORDER; unknown groups
 * are appended deterministically after the known ones (in first-seen order).
 */
export function groupCanonicals(fields: CanonicalFieldLite[]): CanonicalGroup[] {
  const byGroup = new Map<string, CanonicalFieldLite[]>();
  for (const f of fields) {
    const key = canonicalGroupOf(f.path);
    const bucket = byGroup.get(key);
    if (bucket) bucket.push(f);
    else byGroup.set(key, [f]);
  }

  const ordered: CanonicalGroup[] = [];
  const seen = new Set<string>();
  for (const key of GROUP_ORDER) {
    const bucket = byGroup.get(key);
    if (bucket) {
      ordered.push({
        key,
        label: GROUP_LABELS[key] ?? key,
        fields: bucket,
      });
      seen.add(key);
    }
  }
  for (const [key, bucket] of byGroup) {
    if (!seen.has(key)) {
      ordered.push({ key, label: key, fields: bucket });
    }
  }
  return ordered;
}

export interface CappedGroup extends CanonicalGroup {
  /** How many fields of this group are hidden by the cap (>= 0). */
  hidden: number;
}

export interface CappedGroups {
  groups: CappedGroup[];
  /** Total hidden fields across all groups (for the global hint). */
  hiddenTotal: number;
}

/**
 * Cap the rendered rows: at most `perGroup` per group and `maxTotal` overall.
 *
 * Groups keep their order; the per-group cap is applied first, the global cap
 * truncates trailing groups. Every group reports its hidden remainder so the
 * UI can show a precise "… refine your search" hint instead of a huge list.
 */
export function capGroups(
  groups: CanonicalGroup[],
  limits: { perGroup: number; maxTotal: number }
): CappedGroups {
  const capped: CappedGroup[] = [];
  let rendered = 0;
  let hiddenTotal = 0;

  for (const group of groups) {
    if (rendered >= limits.maxTotal) {
      if (group.fields.length > 0) {
        capped.push({ ...group, fields: [], hidden: group.fields.length });
        hiddenTotal += group.fields.length;
      }
      continue;
    }
    const perGroupTake = Math.min(
      group.fields.length,
      limits.perGroup,
      Math.max(0, limits.maxTotal - rendered)
    );
    const hidden = group.fields.length - perGroupTake;
    hiddenTotal += hidden;
    capped.push({
      ...group,
      fields: group.fields.slice(0, perGroupTake),
      hidden,
    });
    rendered += perGroupTake;
  }

  return { groups: capped, hiddenTotal };
}

export interface GroupChip {
  key: string;
  label: string;
  count: number;
}

/** Group chips with counts over the WHOLE catalog (not the filtered subset). */
export function buildGroupChips(fields: CanonicalFieldLite[]): GroupChip[] {
  return groupCanonicals(fields).map((g) => ({
    key: g.key,
    label: g.label,
    count: g.fields.length,
  }));
}

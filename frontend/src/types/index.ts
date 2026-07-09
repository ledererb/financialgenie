// ---------------------------------------------------------------------------
// Shared types for the FinancialGenie Mapping Editor frontend.
// Shapes match the backend API at http://localhost:8765 (see backend/server.py).
// ---------------------------------------------------------------------------

export interface PdfSummary {
  pdf_id: string;
  name: string;
  size_bytes: number;
  parent: string;
}

export interface PdfInfo {
  pdf_id: string;
  total_pages: number;
  has_acroform: boolean;
  file_size: number;
  page_size_pt: [number, number];
}

export interface Rect {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A field as returned by GET /api/pdf/fields. Rect is in 150-DPI image px. */
export interface PdfField {
  pdf_field_name: string;
  field_type: string;
  page_number: number;
  rect: Rect;
  flags: { readonly: boolean; required: boolean; multiline: boolean };
  options: string[] | null;
  value: string | null;
  source: "acroform" | "overlay";
}

export interface PdfFieldsResponse {
  pdf_id: string;
  total_pages: number;
  has_acroform: boolean;
  fields: PdfField[];
}

export type Confidence = "high" | "medium" | "low" | "manual" | null;

/** Fill rule object per the development brief spec. */
export interface FillRule {
  type: "static" | "per_participant" | "conditional" | "role_based";
  value: string;
  sf_field?: string;   // only for "conditional"
  match?: string;      // only for "conditional"
  roles?: string[];    // only for "role_based"
}

/** A field entry inside the mapping JSON. */
export interface MappingField {
  pdf_field_name: string;
  label?: string | null;
  field_type: string;
  canonical_field: string | null;
  confidence: Confidence;
  page_number: number;
  coordinates: Rect | null;
  notes?: string | null;
  options?: string[] | null;
  checkbox_group?: { group_id: string; match_value: string } | null;
  fill_rule?: FillRule | null;
}

export interface CharacterGroup {
  group_id: string;
  group_name?: string | null;
  field_type: string; // "character_split"
  canonical_field: string | null;
  member_fields: string[];
  direction: "left_to_right" | "top_to_bottom";
  separator: string;
}

export type CharacterGroupUpdate = Partial<Omit<CharacterGroup, "group_id">>;

export type CharacterGroupCreate = {
  group_id?: string;
  group_name?: string;
  field_type?: string;
  canonical_field?: string | null;
  member_fields: string[];
  direction?: string;
  separator?: string;
};

/** A block of checkboxes inside a numbered point on a bank form. */
export interface PointBlock {
  block_id: string;
  members: string[];
}

/**
 * A numbered question on a bank form containing one or more blocks of
 * checkboxes. rule_type (1-7) selects which checkbox engine rule applies;
 * params are rule-specific (see src/engine/fill_rules.py).
 */
export interface PointData {
  point_id: string;
  framework: string; // ALAP | CSOK_Plusz | Otthon_Start | "*" (universal)
  label: string;
  page_number: number;
  blocks: PointBlock[];
  rule_type: number; // 1..7
  params: Record<string, any>;
}

export interface MappingConfig {
  bank_name?: string;
  form_name?: string;
  form_type?: "acroform" | "flat" | string;
  approved?: boolean;
  approved_by?: string;
  notes?: string;
  page_structure?: unknown;
  fields: MappingField[];
  character_groups: CharacterGroup[];
  points?: PointData[];
  // Internal metadata echoed by backend
  _mapping_file?: string;
  _mtime?: number;
}

export interface CanonicalField {
  path: string;
  label?: string;
  description?: string;
  sf_type?: string;
}

export type FieldColorKey =
  | "mapped"
  | "lowconf"
  | "unmapped"
  | "selected"
  | "group"
  | "static";

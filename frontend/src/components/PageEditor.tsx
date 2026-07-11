// ---------------------------------------------------------------------------
// PageEditor – focused page-level editor with image + field overlays + sidebar.
// Enhanced with: fill_rule editor, auto-scroll sync, label/notes/field_type
// editing, SF type display, field add/delete, scroll sync.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CanonicalField,
  CharacterGroup,
  Confidence,
  FillRule,
  MappingConfig,
  MappingField,
  PdfField,
  PdfFieldsResponse,
} from "@/types";
import {
  addField,
  createGroup,
  deleteField,
  deleteGroup,
  getCanonicalFields,
  getFieldValues,
  getMapping,
  getPdfFields,
  pageImageUrl,
  previewPage,
  updateField,
  updateGroup,
} from "@/api/client";
import CheckboxGroupDialog from "./CheckboxGroupDialog";

interface PageEditorProps {
  pdfId: string;
  pageNumber: number;
  onBack: () => void;
  onPageChange?: (page: number) => void;
}

// ── helpers ────────────────────────────────────────────────────────────────

function overlayClass(
  field: MappingField,
  isSelected: boolean,
  validCanonicalPaths: Set<string>,
): string {
  if (isSelected) return "field-overlay selected";
  if (!field.canonical_field) return "field-overlay unmapped";

  // Validate that the assigned canonical_field actually exists in the
  // server's canonical fields registry. AI recognition sometimes assigns
  // internal domain model paths (e.g. "participant.role") that don't
  // correspond to real Salesforce fields.
  const isValid = validCanonicalPaths.size === 0 || validCanonicalPaths.has(field.canonical_field);

  if (field.confidence === "high" && isValid) return "field-overlay mapped-high";
  if (field.confidence === "high" && !isValid) return "field-overlay mapped-low";
  if (field.confidence === "medium" && isValid) return "field-overlay mapped-medium";
  return "field-overlay mapped-low";
}

function confidenceBadgeClass(c: Confidence): string {
  if (c === "high" || c === "manual") return "badge badge-green";
  if (c === "medium") return "badge badge-amber";
  return "badge badge-red";
}

// ── CanonicalFieldSearch: searchable combobox for canonical field selection ──
// Replaces the native <select> which was impractical for 249+ SF fields.
function CanonicalFieldSearch({
  autoFocus,
  value,
  fields,
  validPaths,
  onSelect,
  onClear,
}: {
  autoFocus?: boolean;
  value: string;
  fields: CanonicalField[];
  validPaths: Set<string>;
  onSelect: (path: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (autoFocus) {
      setOpen(true);
      inputRef.current?.focus();
    }
  }, [autoFocus]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        onClear();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClear]);

  const filtered = useMemo(() => {
    if (!query.trim()) return fields;
    const q = query.toLowerCase();
    return fields.filter(
      (c) => c.path.toLowerCase().includes(q) || (c.label ?? "").toLowerCase().includes(q),
    );
  }, [fields, query]);

  // Group by SF object prefix
  const groups = useMemo(() => {
    const g = new Map<string, CanonicalField[]>();
    for (const c of filtered) {
      const dot = c.path.indexOf(".");
      const obj = dot > 0 ? c.path.slice(0, dot) : "Other";
      const arr = g.get(obj);
      if (arr) arr.push(c);
      else g.set(obj, [c]);
    }
    return g;
  }, [filtered]);

  const isValid = !value || validPaths.has(value);

  return (
    <div ref={containerRef} style={{ flex: 1, position: "relative" }} onClick={(e) => e.stopPropagation()}>
      <div style={{ display: "flex", gap: "4px", alignItems: "center" }}>
        <input
          ref={inputRef}
          type="text"
          value={open ? query : value}
          placeholder={value || "— keresés —"}
          onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
          onFocus={() => { setOpen(true); setQuery(""); }}
          onKeyDown={(e) => {
            if (e.key === "Escape") { setOpen(false); onClear(); }
            if (e.key === "Enter" && filtered.length > 0) {
              onSelect(filtered[0].path);
              setOpen(false);
            }
          }}
          style={{
            flex: 1,
            background: "var(--bg-tertiary)",
            color: "var(--text-primary)",
            border: `1px solid ${isValid ? "var(--border-strong)" : "var(--accent-red)"}`,
            borderRadius: "var(--radius-sm)",
            padding: "4px 8px",
            fontSize: "0.75rem",
            outline: "none",
          }}
        />
        {value && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={(e) => { e.stopPropagation(); onSelect(""); }}
            title="Törlés"
            style={{ padding: "2px 6px", fontSize: "0.7rem", flexShrink: 0 }}
          >
            ✕
          </button>
        )}
      </div>

      {open && (
        <div style={{
          position: "absolute",
          top: "100%",
          left: 0,
          right: 0,
          marginTop: "2px",
          maxHeight: "280px",
          overflow: "auto",
          background: "var(--bg-secondary)",
          border: "1px solid var(--border-default)",
          borderRadius: "var(--radius-sm)",
          boxShadow: "var(--shadow-lg)",
          zIndex: 50,
        }}>
          {filtered.length === 0 ? (
            <div style={{ padding: "8px 12px", fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
              Nincs találat
            </div>
          ) : (
            Array.from(groups.entries()).map(([obj, gFields]) => (
              <div key={obj}>
                <div style={{
                  padding: "3px 10px",
                  fontSize: "0.65rem",
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--text-tertiary)",
                  background: "var(--bg-tertiary)",
                }}>
                  {obj} ({gFields.length})
                </div>
                {gFields.map((c) => (
                  <div
                    key={c.path}
                    onClick={() => { onSelect(c.path); setOpen(false); }}
                    style={{
                      padding: "4px 12px",
                      fontSize: "0.73rem",
                      cursor: "pointer",
                      color: "var(--text-primary)",
                      background: c.path === value ? "var(--accent-blue-glow)" : "transparent",
                    }}
                    onMouseEnter={(e) => { if (c.path !== value) e.currentTarget.style.background = "var(--bg-hover)"; }}
                    onMouseLeave={(e) => { if (c.path !== value) e.currentTarget.style.background = "transparent"; }}
                  >
                    <strong>{c.path.split(".").pop()}</strong>
                    <span style={{ color: "var(--text-tertiary)" }}> — {c.label}</span>
                    {c.sf_type && <span style={{ color: "var(--text-tertiary)", fontSize: "0.65rem" }}> [{c.sf_type}]</span>}
                  </div>
                ))}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

// PLAN_CHECKBOX_GROUPS.md §4.2(a) — bucket checkbox fields that share a
// checkbox_group.group_id into a group; everything else stays "single".
// Group members keep their original order within the group.
type GroupedField =
  | { kind: "single"; field: MappingField }
  | { kind: "group"; groupId: string; groupLabel: string; members: MappingField[] };

function groupPageFields(fields: MappingField[]): GroupedField[] {
  const result: GroupedField[] = [];
  const groupIndex = new Map<string, number>(); // groupId → index in result
  for (const f of fields) {
    const gid = f.checkbox_group?.group_id;
    if (f.field_type === "checkbox" && gid) {
      const existing = groupIndex.get(gid);
      if (existing !== undefined) {
        (result[existing] as Extract<GroupedField, { kind: "group" }>).members.push(f);
      } else {
        groupIndex.set(gid, result.length);
        result.push({
          kind: "group",
          groupId: gid,
          groupLabel: f.checkbox_group?.group_label ?? gid,
          members: [f],
        });
      }
    } else {
      result.push({ kind: "single", field: f });
    }
  }
  return result;
}

// ── component ──────────────────────────────────────────────────────────────

export default function PageEditor({
  pdfId,
  pageNumber,
  onBack,
  onPageChange,
}: PageEditorProps) {
  // ── state ──────────────────────────────────────────────────────────────

  const [mapping, setMapping] = useState<MappingConfig | null>(null);
  const [fieldsRes, setFieldsRes] = useState<PdfFieldsResponse | null>(null);
  const [canonicals, setCanonicals] = useState<CanonicalField[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedField, setSelectedField] = useState<string | null>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [loadedPage, setLoadedPage] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [showGroupDialog, setShowGroupDialog] = useState(false);
  const [showOverlayLabels, setShowOverlayLabels] = useState(true);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [showData, setShowData] = useState(true);
  const [fieldValues, setFieldValues] = useState<Record<string, string>>({});

  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // ── draw mode (flat PDF field creation) ────────────────────────────
  const [drawMode, setDrawMode] = useState(false);
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const [drawEnd, setDrawEnd] = useState<{ x: number; y: number } | null>(null);
  const [pendingRect, setPendingRect] = useState<{ x: number; y: number; width: number; height: number } | null>(null);
  const [newFieldName, setNewFieldName] = useState("");
  const [newFieldType, setNewFieldType] = useState("text");

  // M5 Step 3 — character-split groups UI state.
  const [showCharGroups, setShowCharGroups] = useState(false);
  const [newCharGroupMembers, setNewCharGroupMembers] = useState("");
  const [newCharGroupCanonical, setNewCharGroupCanonical] = useState("");

  const imageContainerRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLDivElement>(null);
  const fieldCardRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [imageNaturalSize, setImageNaturalSize] = useState<{
    w: number;
    h: number;
  } | null>(null);
  const [imageDisplaySize, setImageDisplaySize] = useState<{
    w: number;
    h: number;
  } | null>(null);

  // ── data fetching ──────────────────────────────────────────────────────

  const refetchData = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([getMapping(pdfId), getPdfFields(pdfId), getCanonicalFields()])
      .then(([m, f, c]) => {
        setMapping(m);
        setFieldsRes(f);
        setCanonicals(c);
      })
      .catch((e) => {
        setError((e as Error).message);
      })
      .finally(() => {
        setLoading(false);
      });
  }, [pdfId]);

  useEffect(() => {
    refetchData();
  }, [refetchData]);

  // Load field values (mock/SF deal data) once for overlay display
  useEffect(() => {
    getFieldValues(pdfId)
      .then((res) => setFieldValues(res.values ?? {}))
      .catch(() => { /* silent — values are optional */ });
  }, [pdfId]);

  // Reset selection when page changes
  useEffect(() => {
    setSelectedField(null);
    setEditingField(null);
    setLoadedPage(null);
  }, [pageNumber]);

  // Keyboard navigation: ←/→ to switch pages (when not typing in an input)
  useEffect(() => {
    if (!onPageChange || !fieldsRes) return;
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === "ArrowLeft" && pageNumber > 1) {
        e.preventDefault();
        onPageChange(pageNumber - 1);
      } else if (e.key === "ArrowRight" && pageNumber < fieldsRes.total_pages) {
        e.preventDefault();
        onPageChange(pageNumber + 1);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onPageChange, fieldsRes, pageNumber]);

  // ── derived: fields for this page ─────────────────────────────────────

  const pageFields = useMemo(() => {
    if (!mapping) return [];
    return mapping.fields.filter((f) => f.page_number === pageNumber);
  }, [mapping, pageNumber]);

  // PLAN §4.2(a) — checkbox fields bucketed by checkbox_group.group_id.
  const groupedPageFields = useMemo(() => groupPageFields(pageFields), [pageFields]);

  const pdfFieldsByName = useMemo(() => {
    if (!fieldsRes) return new Map<string, PdfField>();
    const m = new Map<string, PdfField>();
    for (const f of fieldsRes.fields) m.set(f.pdf_field_name, f);
    return m;
  }, [fieldsRes]);

  // Unmapped PDF fields (for "add field" feature)
  const unmappedPdfFields = useMemo(() => {
    if (!fieldsRes || !mapping) return [];
    const mappedNames = new Set(mapping.fields.map((f) => f.pdf_field_name));
    return fieldsRes.fields
      .filter((f) => f.page_number === pageNumber && !mappedNames.has(f.pdf_field_name))
      .map((f) => f.pdf_field_name);
  }, [fieldsRes, mapping, pageNumber]);

  // Valid canonical field paths from the server registry. Used to validate
  // that a mapping's canonical_field actually corresponds to a real SF field.
  const validCanonicalPaths = useMemo(() => {
    return new Set(canonicals.map((c) => c.path));
  }, [canonicals]);

  // ── image load handler ────────────────────────────────────────────────

  const handleImageLoad = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      const img = e.currentTarget;
      setImageNaturalSize({ w: img.naturalWidth, h: img.naturalHeight });
      setImageDisplaySize({ w: img.clientWidth, h: img.clientHeight });
      setLoadedPage(pageNumber);
    },
    [pageNumber],
  );

  // Keep display size in sync on resize
  useEffect(() => {
    if (!imageContainerRef.current) return;
    const observer = new ResizeObserver(() => {
      const img =
        imageContainerRef.current?.querySelector<HTMLImageElement>("img");
      if (img) {
        setImageDisplaySize({ w: img.clientWidth, h: img.clientHeight });
      }
    });
    observer.observe(imageContainerRef.current);
    return () => observer.disconnect();
  }, []);

  // ── auto-scroll sync (Task 3.1 + 3.6) ────────────────────────────────

  useEffect(() => {
    if (!selectedField) return;
    // Scroll sidebar card into view
    const card = fieldCardRefs.current.get(selectedField);
    if (card && sidebarRef.current) {
      card.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    // Scroll PDF overlay into view
    if (imageContainerRef.current) {
      const overlay = imageContainerRef.current.querySelector<HTMLElement>(
        `[data-field-name="${CSS.escape(selectedField)}"]`
      );
      if (overlay) {
        overlay.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
  }, [selectedField]);

  // ── scale factor for overlays ─────────────────────────────────────────

  const scale = useMemo(() => {
    if (!imageNaturalSize || !imageDisplaySize) return 1;
    return imageDisplaySize.w / imageNaturalSize.w;
  }, [imageNaturalSize, imageDisplaySize]);

  // ── field update handler (generic) ────────────────────────────────────

  const handleFieldUpdate = useCallback(
    async (fieldName: string, patch: Partial<MappingField>) => {
      if (!mapping) return;
      setSaving(true);
      try {
        await updateField(pdfId, fieldName, patch);
        setMapping((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            fields: prev.fields.map((f) =>
              f.pdf_field_name === fieldName ? { ...f, ...patch } : f,
            ),
          };
        });
      } catch {
        // silently keep previous state
      } finally {
        setSaving(false);
      }
    },
    [mapping, pdfId],
  );

  // Bulk checkbox-group assignment (from the CheckboxGroupDialog)
  const handleBulkCheckboxGroup = useCallback(
    async (updates: { fieldName: string; checkboxGroup: NonNullable<MappingField["checkbox_group"]> }[]) => {
      for (const u of updates) {
        await handleFieldUpdate(u.fieldName, { checkbox_group: u.checkboxGroup });
      }
    },
    [handleFieldUpdate],
  );

  const handleCanonicalChange = useCallback(
    async (fieldName: string, canonicalPath: string | null) => {
      const patch: Partial<MappingField> = {
        canonical_field: canonicalPath,
        confidence: canonicalPath ? "manual" : null,
      };
      await handleFieldUpdate(fieldName, patch);
      setEditingField(null);
    },
    [handleFieldUpdate],
  );

  // ── add field (Task 3.4) ──────────────────────────────────────────────

  const handleAddField = useCallback(
    async (pdfFieldName: string) => {
      if (!mapping) return;
      setSaving(true);
      try {
        const pf = pdfFieldsByName.get(pdfFieldName);
        await addField(pdfId, {
          pdf_field_name: pdfFieldName,
          field_type: pf?.field_type ?? "text",
          page_number: pageNumber,
          coordinates: pf?.rect ?? null,
          confidence: "manual",
        });
        // Refetch
        const m = await getMapping(pdfId);
        setMapping(m);
      } catch {
        // ignore
      } finally {
        setSaving(false);
      }
    },
    [mapping, pdfId, pageNumber, pdfFieldsByName],
  );

  // ── delete field (Task 3.5) ───────────────────────────────────────────

  const handleDeleteField = useCallback(
    async (fieldName: string) => {
      if (!mapping || !confirm(`Biztosan törlöd: ${fieldName}?`)) return;
      setSaving(true);
      try {
        await deleteField(pdfId, fieldName);
        setMapping((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            fields: prev.fields.filter((f) => f.pdf_field_name !== fieldName),
          };
        });
        if (selectedField === fieldName) setSelectedField(null);
      } catch {
        // ignore
      } finally {
        setSaving(false);
      }
    },
    [mapping, pdfId, selectedField],
  );

  // ── character-group handlers (M5 Step 3) ──────────────────────────────
  // A character group splits one canonical value (e.g. a postal code) across
  // several one-character PDF boxes. Users define the member field names as a
  // comma-separated list. We persist via the dedicated /api/mapping/group
  // endpoints and refetch the whole mapping afterwards (same pattern as the
  // add-field handler above).

  const refetchMapping = useCallback(async () => {
    const m = await getMapping(pdfId);
    setMapping(m);
  }, [pdfId]);

  const handleCreateCharGroup = useCallback(
    async (memberFieldsCsv: string, canonicalField: string) => {
      const members = memberFieldsCsv
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (members.length === 0) return;
      setSaving(true);
      try {
        await createGroup(pdfId, {
          field_type: "character_split",
          direction: "left_to_right",
          separator: "",
          canonical_field: canonicalField || null,
          member_fields: members,
        });
        await refetchMapping();
      } catch {
        // keep previous state
      } finally {
        setSaving(false);
      }
    },
    [pdfId, refetchMapping],
  );

  const handleUpdateCharGroup = useCallback(
    async (groupId: string, patch: Partial<CharacterGroup>) => {
      setSaving(true);
      try {
        await updateGroup(pdfId, groupId, patch);
        await refetchMapping();
      } catch {
        // keep previous state
      } finally {
        setSaving(false);
      }
    },
    [pdfId, refetchMapping],
  );

  const handleDeleteCharGroup = useCallback(
    async (groupId: string) => {
      if (!confirm(`Biztosan törlöd a karaktercsoportot: ${groupId}?`)) return;
      setSaving(true);
      try {
        await deleteGroup(pdfId, groupId);
        setMapping((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            character_groups: prev.character_groups.filter(
              (g) => g.group_id !== groupId,
            ),
          };
        });
      } catch {
        // keep previous state
      } finally {
        setSaving(false);
      }
    },
    [pdfId],
  );

  // ── draw mode handlers (flat PDF area selection) ─────────────────────

  const isFlat = mapping?.form_type === "flat";

  const handleDrawMouseDown = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!drawMode || !imageContainerRef.current) return;
      const img = imageContainerRef.current.querySelector<HTMLImageElement>("img");
      if (!img) return;
      const imgRect = img.getBoundingClientRect();
      const x = e.clientX - imgRect.left;
      const y = e.clientY - imgRect.top;
      setDrawStart({ x, y });
      setDrawEnd({ x, y });
      e.preventDefault();
    },
    [drawMode],
  );

  const handleDrawMouseMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (!drawMode || !drawStart || !imageContainerRef.current) return;
      const img = imageContainerRef.current.querySelector<HTMLImageElement>("img");
      if (!img) return;
      const imgRect = img.getBoundingClientRect();
      setDrawEnd({
        x: Math.max(0, Math.min(e.clientX - imgRect.left, imgRect.width)),
        y: Math.max(0, Math.min(e.clientY - imgRect.top, imgRect.height)),
      });
    },
    [drawMode, drawStart],
  );

  const handleDrawMouseUp = useCallback(() => {
    if (!drawMode || !drawStart || !drawEnd || !scale) return;
    const x = Math.min(drawStart.x, drawEnd.x);
    const y = Math.min(drawStart.y, drawEnd.y);
    const w = Math.abs(drawEnd.x - drawStart.x);
    const h = Math.abs(drawEnd.y - drawStart.y);
    // Minimum 10px drag
    if (w < 10 || h < 10) {
      setDrawStart(null);
      setDrawEnd(null);
      return;
    }
    // Convert display coords to PDF coords
    setPendingRect({
      x: Math.round(x / scale),
      y: Math.round(y / scale),
      width: Math.round(w / scale),
      height: Math.round(h / scale),
    });
    setNewFieldName(`flat_field_${Date.now() % 10000}`);
    setNewFieldType("text");
    setDrawStart(null);
    setDrawEnd(null);
  }, [drawMode, drawStart, drawEnd, scale]);

  const handleCreateFlatField = useCallback(async () => {
    if (!mapping || !pendingRect || !newFieldName.trim()) return;
    setSaving(true);
    try {
      await addField(pdfId, {
        pdf_field_name: newFieldName.trim(),
        field_type: newFieldType,
        page_number: pageNumber,
        coordinates: pendingRect,
        confidence: "manual",
      });
      const m = await getMapping(pdfId);
      setMapping(m);
      setPendingRect(null);
      setNewFieldName("");
      setDrawMode(false);
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  }, [mapping, pdfId, pageNumber, pendingRect, newFieldName, newFieldType]);

  // ── preview handler ──────────────────────────────────────────────────

  const handlePreviewPage = useCallback(async () => {
    setPreviewLoading(true);
    try {
      const data = await previewPage(pdfId, pageNumber);
      setPreviewImage(data.image);
    } catch (err) {
      alert("Hiba az előnézet generálásakor: " + (err as Error).message);
    } finally {
      setPreviewLoading(false);
    }
  }, [pdfId, pageNumber]);

  // ── render ─────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          color: "var(--text-secondary)",
        }}
      >
        Oldal betöltése…
      </div>
    );
  }

  if (error) {
    return (
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
          gap: "var(--space-md)",
        }}
      >
        <span className="badge badge-red">Hiba</span>
        <p style={{ color: "var(--text-secondary)" }}>{error}</p>
        <button className="btn btn-ghost" onClick={onBack}>
          Vissza az irányítópultra
        </button>
      </div>
    );
  }

  return (
    <div
      className="animate-fade-in"
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* ── header ───────────────────────────────────────────────────── */}
      <header
        className="glass"
        style={{
          padding: "16px 28px",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          Vissza az irányítópultra
        </button>

        {/* Page navigation */}
        {onPageChange && fieldsRes && (
          <div style={{ display: "flex", alignItems: "center", gap: "var(--space-xs)" }}>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => onPageChange(Math.max(1, pageNumber - 1))}
              disabled={pageNumber <= 1}
              title="Előző oldal (←)"
            >
              ←
            </button>
            <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-primary)" }}>
              {pageNumber} / {fieldsRes.total_pages}
            </span>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => onPageChange(Math.min(fieldsRes.total_pages, pageNumber + 1))}
              disabled={pageNumber >= fieldsRes.total_pages}
              title="Következő oldal (→)"
            >
              →
            </button>
          </div>
        )}
        {!onPageChange && (
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>
            {pageNumber}. oldal
          </h2>
        )}
        <span
          style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}
        >
          {pageFields.length} mező
        </span>
        {isFlat && (
          <button
            className={`btn btn-sm ${drawMode ? "btn-accent" : "btn-ghost"}`}
            onClick={() => { setDrawMode(!drawMode); setPendingRect(null); setDrawStart(null); }}
            style={{
              marginLeft: "auto",
              background: drawMode ? "var(--accent-green)" : undefined,
              color: drawMode ? "#fff" : undefined,
              transition: "all 0.2s",
            }}
          >
            {drawMode ? "✏️ Rajzolás aktív" : "✏️ Mező rajzolása"}
          </button>
        )}
        <button
          className="btn btn-ghost btn-sm"
          onClick={handlePreviewPage}
          disabled={previewLoading}
          style={{ marginLeft: isFlat ? undefined : "auto" }}
        >
          {previewLoading ? "Generálás..." : "👀 Oldal előnézete"}
        </button>

        {/* Overlay labels toggle */}
        <button
          className={`btn btn-sm ${showOverlayLabels ? "btn-accent" : "btn-ghost"}`}
          onClick={() => setShowOverlayLabels(!showOverlayLabels)}
          title="Mezőnevek mutatása a PDF képen"
          style={{
            background: showOverlayLabels ? "var(--accent-blue)" : undefined,
            color: showOverlayLabels ? "#fff" : undefined,
            fontSize: "0.75rem",
          }}
        >
          {showOverlayLabels ? "🏷 Címkék ON" : "🏷 Címkék"}
        </button>

        {/* Data overlay toggle */}
        <button
          className={`btn btn-sm ${showData ? "btn-accent" : "btn-ghost"}`}
          onClick={() => setShowData(!showData)}
          title="Mezőértékek mutatása a PDF képen (mock adatok)"
          style={{
            background: showData ? "var(--accent-green)" : undefined,
            color: showData ? "#fff" : undefined,
            fontSize: "0.75rem",
          }}
        >
          {showData ? "📊 Adatok ON" : "📊 Adatok"}
        </button>

        {/* Checkbox grouping — only if there are checkbox fields on this page */}
        {pageFields.some((f) => f.field_type === "checkbox") && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setShowGroupDialog(true)}
            title="Checkbox mezők csoportosítása"
            style={{ fontSize: "0.75rem" }}
          >
            ⊕ Csoportosítás
          </button>
        )}

        {saving && (
          <span
            className="badge badge-blue"
          >
            Mentés…
          </span>
        )}
      </header>

      {/* ── body: image + sidebar ────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          display: "flex",
          overflow: "hidden",
        }}
      >
        {/* ─── image panel ─────────────────────────────────────────── */}
        <div
          ref={imageContainerRef}
          style={{
            flex: 1,
            overflow: "auto",
            padding: "var(--space-md)",
            display: "flex",
            justifyContent: "center",
            alignItems: "flex-start",
            background: "var(--bg-primary)",
          }}
        >
          <div
            style={{ position: "relative", display: "inline-block", cursor: drawMode ? "crosshair" : undefined }}
            onMouseDown={handleDrawMouseDown}
            onMouseMove={handleDrawMouseMove}
            onMouseUp={handleDrawMouseUp}
          >
            <img
              src={pageImageUrl(pdfId, pageNumber)}
              alt={`${pageNumber}. oldal`}
              onLoad={handleImageLoad}
              style={{
                maxWidth: "100%",
                height: "auto",
                display: "block",
                borderRadius: "var(--radius-md)",
                boxShadow: "var(--shadow-lg)",
                pointerEvents: drawMode ? "none" : undefined,
              }}
            />

            {/* Draw selection rectangle preview */}
            {drawMode && drawStart && drawEnd && (
              <div
                style={{
                  position: "absolute",
                  left: Math.min(drawStart.x, drawEnd.x),
                  top: Math.min(drawStart.y, drawEnd.y),
                  width: Math.abs(drawEnd.x - drawStart.x),
                  height: Math.abs(drawEnd.y - drawStart.y),
                  border: "2px dashed var(--accent-green)",
                  background: "rgba(34,197,94,0.15)",
                  borderRadius: "var(--radius-sm)",
                  pointerEvents: "none",
                  zIndex: 1000,
                }}
              />
            )}

            {/* Pending rect (after drag, before naming) */}
            {pendingRect && (
              <div
                style={{
                  position: "absolute",
                  left: pendingRect.x * scale,
                  top: pendingRect.y * scale,
                  width: pendingRect.width * scale,
                  height: pendingRect.height * scale,
                  border: "2px solid var(--accent-green)",
                  background: "rgba(34,197,94,0.25)",
                  borderRadius: "var(--radius-sm)",
                  zIndex: 1001,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <div
                  onMouseDown={(e) => e.stopPropagation()}
                  onMouseMove={(e) => e.stopPropagation()}
                  onMouseUp={(e) => e.stopPropagation()}
                  onClick={(e) => e.stopPropagation()}
                  style={{
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border-strong)",
                    borderRadius: "var(--radius-md)",
                    padding: "var(--space-sm) var(--space-md)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                    minWidth: 200,
                    boxShadow: "var(--shadow-lg)",
                  }}
                >
                  <span style={{ fontSize: "0.7rem", fontWeight: 700, textTransform: "uppercase", color: "var(--text-tertiary)" }}>
                    Új mező létrehozása
                  </span>
                  <input
                    autoFocus
                    placeholder="Mező neve"
                    value={newFieldName}
                    onChange={(e) => setNewFieldName(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") handleCreateFlatField(); }}
                    style={inputStyle}
                  />
                  <select
                    value={newFieldType}
                    onChange={(e) => setNewFieldType(e.target.value)}
                    style={{ ...inputStyle, appearance: "auto" }}
                  >
                    <option value="text" style={{ background: "#1a1a2e", color: "#fff" }}>Text</option>
                    <option value="checkbox" style={{ background: "#1a1a2e", color: "#fff" }}>Checkbox</option>
                    <option value="date" style={{ background: "#1a1a2e", color: "#fff" }}>Date</option>
                    <option value="number" style={{ background: "#1a1a2e", color: "#fff" }}>Number</option>
                  </select>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button
                      onClick={handleCreateFlatField}
                      style={{
                        flex: 1,
                        background: "var(--accent-green)",
                        color: "#fff",
                        border: "none",
                        borderRadius: "var(--radius-sm)",
                        padding: "4px 8px",
                        fontSize: "0.72rem",
                        cursor: "pointer",
                      }}
                    >
                      ✓ Létrehozás
                    </button>
                    <button
                      onClick={() => setPendingRect(null)}
                      style={{
                        flex: 1,
                        background: "var(--bg-tertiary)",
                        color: "var(--text-secondary)",
                        border: "1px solid var(--border-subtle)",
                        borderRadius: "var(--radius-sm)",
                        padding: "4px 8px",
                        fontSize: "0.72rem",
                        cursor: "pointer",
                      }}
                    >
                      ✕ Mégse
                    </button>
                  </div>
                </div>
              </div>
            )}

            {/* field overlays – only when the right page is loaded */}
            {loadedPage === pageNumber &&
              pageFields.map((mf) => {
                const pf = pdfFieldsByName.get(mf.pdf_field_name);
                const rect = pf?.rect ?? mf.coordinates;
                if (!rect) return null;

                const labelText = mf.canonical_field ?? mf.label ?? mf.pdf_field_name;
                const isAlwaysChecked = mf.fill_rule?.type === "static";

                return (
                  <div
                    key={mf.pdf_field_name}
                    data-field-name={mf.pdf_field_name}
                    className={overlayClass(
                      mf,
                      selectedField === mf.pdf_field_name,
                      validCanonicalPaths,
                    )}
                    onClick={() => {
                      setSelectedField(mf.pdf_field_name);
                      setEditingField(null);
                    }}
                    style={{
                      left: rect.x * scale,
                      top: rect.y * scale,
                      width: rect.width * scale,
                      height: rect.height * scale,
                      borderRadius: "var(--radius-sm)",
                    }}
                    title={`${mf.pdf_field_name}${mf.canonical_field ? ` → ${mf.canonical_field}` : ""}`}
                  >
                    {/* Field label overlay (toggleable) */}
                    {showOverlayLabels && rect.width * scale > 30 && (
                      <span
                        style={{
                          position: "absolute",
                          top: -14,
                          left: 0,
                          fontSize: "7px",
                          fontWeight: 600,
                          lineHeight: "12px",
                          padding: "0 3px",
                          borderRadius: "2px",
                          background: mf.canonical_field
                            ? "rgba(59,130,246,0.85)"
                            : "rgba(239,68,68,0.85)",
                          color: "white",
                          whiteSpace: "nowrap",
                          maxWidth: 120,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          pointerEvents: "none",
                          zIndex: 5,
                        }}
                      >
                        {isAlwaysChecked && "📌 "}{labelText}
                      </span>
                    )}

                    {/* Field value overlay (toggleable — shows deal data) */}
                    {showData && fieldValues[mf.pdf_field_name] && (
                      <span
                        style={{
                          position: "absolute",
                          bottom: 0,
                          left: 0,
                          right: 0,
                          fontSize: "6px",
                          fontWeight: 600,
                          lineHeight: "9px",
                          padding: "0 2px",
                          background: "rgba(34,197,94,0.9)",
                          color: "white",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          pointerEvents: "none",
                          zIndex: 4,
                          textAlign: "center",
                        }}
                      >
                        {fieldValues[mf.pdf_field_name]}
                      </span>
                    )}
                  </div>
                );
              })}
          </div>
        </div>

        {/* ─── sidebar ─────────────────────────────────────────────── */}
        <aside
          className="glass-strong animate-slide-in"
          style={{
            width: 340,
            flexShrink: 0,
            overflowY: "auto",
            borderLeft: "1px solid var(--border-subtle)",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div
            style={{
              padding: "var(--space-md)",
              fontSize: "0.8rem",
              fontWeight: 600,
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              borderBottom: "1px solid var(--border-subtle)",
              flexShrink: 0,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <span>Mezők a(z) {pageNumber}. oldalon</span>
          </div>

          {/* ─── Add field dropdown (Task 3.4) ─────────────────────── */}
          <div style={{ padding: "var(--space-sm) var(--space-md)", borderBottom: "1px solid var(--border-subtle)", display: "flex", gap: 4, alignItems: "center" }}>
            {unmappedPdfFields.length > 0 ? (
              <select
                value=""
                onChange={(e) => {
                  if (e.target.value) handleAddField(e.target.value);
                }}
                style={{
                  flex: 1,
                  background: "var(--bg-tertiary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--accent-green)",
                  borderRadius: "var(--radius-sm)",
                  padding: "6px 8px",
                  fontSize: "0.72rem",
                }}
              >
                <option value="">+ Mező hozzáadása ({unmappedPdfFields.length} elérhető)</option>
                {unmappedPdfFields.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            ) : (
              <span style={{ flex: 1, fontSize: "0.72rem", color: "var(--text-tertiary)" }}>
                Minden mezo mappelve
              </span>
            )}
            <button
              onClick={refetchData}
              title="Mapping újratöltése"
              style={{
                background: "none",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-secondary)",
                cursor: "pointer",
                padding: "4px 8px",
                fontSize: "0.72rem",
                flexShrink: 0,
              }}
            >
              ↻
            </button>
          </div>

          <div
            ref={sidebarRef}
            style={{
              flex: 1,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
            }}
          >
            {pageFields.length === 0 && (
              <div
                style={{
                  padding: "var(--space-lg)",
                  textAlign: "center",
                  color: "var(--text-muted)",
                  fontSize: "0.85rem",
                }}
              >
                Nincs mező ezen az oldalon.
              </div>
            )}

            {groupedPageFields.map((entry) => {
              const renderMember = (mf: MappingField) => {
                const isSelected = selectedField === mf.pdf_field_name;
                const isEditing = editingField === mf.pdf_field_name;

                return (
                <div
                  key={mf.pdf_field_name}
                  ref={(el) => {
                    if (el) fieldCardRefs.current.set(mf.pdf_field_name, el);
                  }}
                  className="mapping-row"
                  onClick={() => {
                    setSelectedField(mf.pdf_field_name);
                    if (!isEditing) setEditingField(null);
                  }}
                  style={{
                    flexDirection: "column",
                    alignItems: "stretch",
                    gap: "var(--space-xs)",
                    padding: "var(--space-sm) var(--space-md)",
                    cursor: "pointer",
                    borderLeft: isSelected
                      ? "3px solid var(--accent-blue)"
                      : "3px solid transparent",
                    background: isSelected
                      ? "var(--accent-blue-glow)"
                      : undefined,
                  }}
                >
                  {/* row top: name + confidence */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "space-between",
                      gap: "var(--space-sm)",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "0.8rem",
                        fontWeight: 600,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                        flex: 1,
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                      }}
                      title={mf.pdf_field_name}
                    >
                      {mf.checkbox_group?.option_value && (
                        <span
                          className="badge badge-blue"
                          style={{ fontSize: "0.65rem", padding: "1px 6px", flexShrink: 0 }}
                        >
                          {mf.checkbox_group.option_label || mf.checkbox_group.option_value}
                        </span>
                      )}
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {mf.checkbox_group?.option_value
                          ? mf.pdf_field_name.replace(/_{2,}.*$/, "")
                          : mf.pdf_field_name}
                      </span>
                    </span>
                    <span className={confidenceBadgeClass(mf.confidence)}>
                      {mf.confidence ?? "nincs"}
                    </span>
                  </div>

                  {/* row bottom: canonical mapping */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                    }}
                  >
                    <span
                      style={{
                        fontSize: "0.7rem",
                        color: "var(--text-tertiary)",
                        marginRight: "auto",
                      }}
                    >
                      →
                    </span>

                    {isEditing || isSelected ? (
                      <CanonicalFieldSearch
                        autoFocus={isEditing}
                        value={mf.canonical_field ?? ""}
                        fields={canonicals}
                        validPaths={validCanonicalPaths}
                        onSelect={(val) => {
                          handleCanonicalChange(mf.pdf_field_name, val || null);
                        }}
                        onClear={() => setEditingField(null)}
                      />
                    ) : (
                      <span
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingField(mf.pdf_field_name);
                          setSelectedField(mf.pdf_field_name);
                        }}
                        style={{
                          flex: 1,
                          fontSize: "0.75rem",
                          color: !mf.canonical_field
                            ? "var(--accent-red)"
                            : !validCanonicalPaths.has(mf.canonical_field)
                              ? "var(--accent-amber)"
                              : "var(--text-primary)",
                          fontStyle: mf.canonical_field ? "normal" : "italic",
                          cursor: "pointer",
                          padding: "2px 4px",
                          borderRadius: "var(--radius-sm)",
                          transition: "background var(--transition-fast)",
                        }}
                        title="Kattints a mapping szerkesztéséhez"
                      >
                        {!mf.canonical_field
                          ? "nem mappelt"
                          : !validCanonicalPaths.has(mf.canonical_field)
                            ? `⚠ ${mf.canonical_field}`
                            : mf.canonical_field}
                      </span>
                    )}
                  </div>

                  {/* ── expanded details when selected ──────────────── */}
                  {isSelected && (
                    <div
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        marginTop: "var(--space-xs)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 6,
                        borderTop: "1px solid var(--border-subtle)",
                        paddingTop: "var(--space-sm)",
                      }}
                    >
                      <span style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-tertiary)" }}>
                        Mező részletek
                      </span>

                      {/* "Always check" quick toggle for checkbox fields */}
                      {mf.field_type === "checkbox" && (
                        <label
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "var(--space-xs) var(--space-sm)",
                            background: mf.fill_rule?.type === "static" ? "rgba(34,197,94,0.12)" : "var(--bg-tertiary)",
                            borderRadius: "var(--radius-sm)",
                            border: mf.fill_rule?.type === "static" ? "1px solid var(--accent-green)" : "1px solid var(--border-subtle)",
                            cursor: "pointer",
                            fontSize: "0.75rem",
                            color: "var(--text-primary)",
                          }}
                        >
                          <span>📌 Mindig pipál</span>
                          <input
                            type="checkbox"
                            checked={mf.fill_rule?.type === "static"}
                            onChange={(e) => {
                              if (e.target.checked) {
                                handleFieldUpdate(mf.pdf_field_name, {
                                  fill_rule: { type: "static", value: "igen" },
                                });
                              } else {
                                handleFieldUpdate(mf.pdf_field_name, { fill_rule: null });
                              }
                            }}
                          />
                        </label>
                      )}

                      {/* Haladó panel: Label, Field type, Fill Rule, Notes */}
                      <button
                        onClick={() => setShowAdvanced(!showAdvanced)}
                        style={{
                          background: "none",
                          border: "1px dashed var(--border-default)",
                          borderRadius: "var(--radius-sm)",
                          padding: "4px 8px",
                          fontSize: "0.7rem",
                          color: "var(--text-tertiary)",
                          cursor: "pointer",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          gap: 4,
                        }}
                      >
                        {showAdvanced ? "▲ Haladó elrejtése" : "▼ Haladó beállítások"}
                      </button>

                      {showAdvanced && (
                        <>
                          {/* Label */}
                          <input
                            placeholder="Címke"
                            value={mf.label ?? ""}
                            onChange={(e) => handleFieldUpdate(mf.pdf_field_name, { label: e.target.value || null })}
                            style={inputStyle}
                          />

                          {/* Field type (character_split elrejtve) */}
                          <select
                            value={mf.field_type === "character_split" ? "text" : mf.field_type}
                            onChange={(e) => handleFieldUpdate(mf.pdf_field_name, { field_type: e.target.value })}
                            style={inputStyle}
                          >
                            <option value="text">Text</option>
                            <option value="number">Number</option>
                            <option value="date">Date</option>
                            <option value="checkbox">Checkbox</option>
                            <option value="dropdown">Dropdown</option>
                            <option value="money">Money</option>
                          </select>

                          {/* Fill rule editor */}
                          <div style={{
                            padding: "var(--space-xs) var(--space-sm)",
                            background: mf.fill_rule ? "rgba(34,197,94,0.08)" : "var(--bg-tertiary)",
                            borderRadius: "var(--radius-sm)",
                            border: mf.fill_rule ? "1px solid var(--accent-green)" : "1px solid var(--border-subtle)",
                            display: "flex",
                            flexDirection: "column",
                            gap: 4,
                          }}>
                            <span style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-tertiary)" }}>
                              Fill Rule
                            </span>
                            <select
                              value={mf.fill_rule?.type ?? ""}
                          onChange={(e) => {
                            const t = e.target.value as FillRule["type"] | "";
                            if (!t) {
                              handleFieldUpdate(mf.pdf_field_name, { fill_rule: null });
                            } else {
                              handleFieldUpdate(mf.pdf_field_name, {
                                fill_rule: {
                                  type: t,
                                  value: mf.fill_rule?.value ?? "igen",
                                  ...(t === "conditional" ? { sf_field: mf.fill_rule?.sf_field ?? "", match: mf.fill_rule?.match ?? "" } : {}),
                                  ...(t === "role_based" ? { roles: mf.fill_rule?.roles ?? ["adós"] } : {}),
                                },
                              });
                            }
                          }}
                          style={inputStyle}
                        >
                          <option value="">— nincs szabály —</option>
                          <option value="static">static (fix érték)</option>
                          <option value="per_participant">per_participant (minden szereplőre)</option>
                          <option value="conditional">conditional (SF mező alapján)</option>
                          <option value="role_based">role_based (szerepkör alapján)</option>
                        </select>

                        {mf.fill_rule && (
                          <>
                            <input
                              placeholder="Érték (pl. igen)"
                              value={mf.fill_rule.value ?? ""}
                              onChange={(e) => handleFieldUpdate(mf.pdf_field_name, {
                                fill_rule: { ...mf.fill_rule!, value: e.target.value },
                              })}
                              style={{ ...inputStyle, fontFamily: "monospace" }}
                            />

                            {mf.fill_rule.type === "conditional" && (
                              <>
                                <input
                                  placeholder="SF mező (pl. Opportunity.Hitelc_l__c)"
                                  value={mf.fill_rule.sf_field ?? ""}
                                  onChange={(e) => handleFieldUpdate(mf.pdf_field_name, {
                                    fill_rule: { ...mf.fill_rule!, sf_field: e.target.value },
                                  })}
                                  style={{ ...inputStyle, fontFamily: "monospace" }}
                                />
                                <input
                                  placeholder="Egyezés (pl. Új ingatlan vásárlás)"
                                  value={mf.fill_rule.match ?? ""}
                                  onChange={(e) => handleFieldUpdate(mf.pdf_field_name, {
                                    fill_rule: { ...mf.fill_rule!, match: e.target.value },
                                  })}
                                  style={inputStyle}
                                />
                              </>
                            )}

                            {mf.fill_rule.type === "role_based" && (
                              <input
                                placeholder="Szerepek (vesszővel: adós,adóstárs)"
                                value={(mf.fill_rule.roles ?? []).join(",")}
                                onChange={(e) => handleFieldUpdate(mf.pdf_field_name, {
                                  fill_rule: { ...mf.fill_rule!, roles: e.target.value.split(",").map(s => s.trim()).filter(Boolean) },
                                })}
                                style={inputStyle}
                              />
                            )}
                          </>
                        )}
                      </div>

                      {/* Notes (Task 3.2) */}
                      <input
                        placeholder="Megjegyzés"
                        value={mf.notes ?? ""}
                        onChange={(e) => handleFieldUpdate(mf.pdf_field_name, { notes: e.target.value || null })}
                        style={inputStyle}
                      />
                        </>
                      )}

                      {/* Delete button (Task 3.5) */}
                      <button
                        onClick={() => handleDeleteField(mf.pdf_field_name)}
                        style={{
                          background: "var(--accent-red)",
                          color: "#fff",
                          border: "none",
                          borderRadius: "var(--radius-sm)",
                          padding: "4px 8px",
                          fontSize: "0.72rem",
                          cursor: "pointer",
                          alignSelf: "flex-start",
                          marginTop: 4,
                        }}
                      >
                        🗑 Mező törlése
                      </button>
                    </div>
                  )}
                </div>
                );
              };

              if (entry.kind === "single") return renderMember(entry.field);

              // PLAN §4.2(a) — checkbox-csoport konténer (fieldset-szerű)
              return (
                <div
                  key={`group-${entry.groupId}`}
                  className="mapping-row"
                  style={{
                    flexDirection: "column",
                    alignItems: "stretch",
                    gap: 0,
                    padding: 0,
                    borderLeft: "3px solid var(--accent-blue)",
                    background: "var(--bg-tertiary)",
                  }}
                >
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      padding: "var(--space-xs) var(--space-sm)",
                      borderBottom: "1px solid var(--border-subtle)",
                    }}
                  >
                    <span style={{ fontSize: "0.7rem" }}>▼</span>
                    <span
                      title={entry.groupLabel}
                      style={{
                        fontSize: "0.8rem",
                        fontWeight: 700,
                        flex: 1,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {entry.groupLabel}
                    </span>
                    <span style={{ fontSize: "0.7rem", color: "var(--text-tertiary)" }}>
                      {entry.members.length} opció
                    </span>
                  </div>
                  {entry.members.map((mf) => renderMember(mf))}
                </div>
              );
            })}
          </div>

          {/* ─── Character-split groups (M5 Step 3) ─────────────────────── */}
          {/* Lets the user declare digit-box / comb-text groups: one canonical
              value split character-by-character across several PDF fields. */}
          <div
            style={{
              borderTop: "1px solid var(--border-subtle)",
              flexShrink: 0,
            }}
          >
            <button
              onClick={() => setShowCharGroups((v) => !v)}
              style={{
                width: "100%",
                background: "none",
                border: "none",
                color: "var(--text-secondary)",
                fontSize: "0.7rem",
                fontWeight: 700,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                padding: "var(--space-sm) var(--space-md)",
                cursor: "pointer",
                textAlign: "left",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>
                Karaktercsoportok
                {mapping && mapping.character_groups.length > 0 && (
                  <span
                    style={{
                      marginLeft: 6,
                      background: "var(--accent-blue-glow)",
                      color: "var(--accent-blue)",
                      borderRadius: 999,
                      padding: "0 6px",
                      fontSize: "0.6rem",
                    }}
                  >
                    {mapping.character_groups.length}
                  </span>
                )}
              </span>
              <span>{showCharGroups ? "▾" : "▸"}</span>
            </button>

            {showCharGroups && (
              <div
                style={{
                  padding: "var(--space-xs) var(--space-md) var(--space-sm)",
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                }}
              >
                {mapping && mapping.character_groups.length === 0 && (
                  <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>
                    Egy karaktercsoport sem. Egy értéket (pl. irányítószám)
                    darabol fel dobozonkénti egy-karakteres mezőkre.
                  </span>
                )}

                {mapping?.character_groups.map((g) => (
                  <div
                    key={g.group_id}
                    style={{
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      padding: "var(--space-xs) var(--space-sm)",
                      display: "flex",
                      flexDirection: "column",
                      gap: 4,
                      background: "var(--bg-tertiary)",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                      }}
                    >
                      <span style={{ fontSize: "0.68rem", fontWeight: 700 }}>
                        {g.group_name || g.group_id}
                      </span>
                      <button
                        onClick={() => handleDeleteCharGroup(g.group_id)}
                        style={{
                          background: "none",
                          border: "none",
                          color: "var(--accent-red)",
                          cursor: "pointer",
                          fontSize: "0.68rem",
                        }}
                        title="Csoport törlése"
                      >
                        ✕
                      </button>
                    </div>
                    <input
                      placeholder="mezőnevek vesszővel (pl. zip1,zip2,zip3,zip4)"
                      value={(g.member_fields ?? []).join(",")}
                      onChange={(e) =>
                        handleUpdateCharGroup(g.group_id, {
                          member_fields: e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        })
                      }
                      style={{ ...inputStyle, fontFamily: "monospace" }}
                    />
                    <select
                      value={g.canonical_field ?? ""}
                      onChange={(e) =>
                        handleUpdateCharGroup(g.group_id, {
                          canonical_field: e.target.value || null,
                        })
                      }
                      style={inputStyle}
                    >
                      <option value="">— kanonikus mező —</option>
                      {canonicals.map((c) => (
                        <option key={c.path} value={c.path}>
                          {c.path}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}

                {/* new group row */}
                <div
                  style={{
                    borderTop: "1px dashed var(--border-subtle)",
                    paddingTop: "var(--space-xs)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 4,
                  }}
                >
                  <input
                    placeholder="új csoport mezői (vesszővel elválasztva)"
                    value={newCharGroupMembers}
                    onChange={(e) => setNewCharGroupMembers(e.target.value)}
                    style={{ ...inputStyle, fontFamily: "monospace" }}
                  />
                  <select
                    value={newCharGroupCanonical}
                    onChange={(e) => setNewCharGroupCanonical(e.target.value)}
                    style={inputStyle}
                  >
                    <option value="">— kanonikus mező —</option>
                    {canonicals.map((c) => (
                      <option key={c.path} value={c.path}>
                        {c.path}
                      </option>
                    ))}
                  </select>
                  <button
                    onClick={() => {
                      handleCreateCharGroup(newCharGroupMembers, newCharGroupCanonical);
                      setNewCharGroupMembers("");
                      setNewCharGroupCanonical("");
                    }}
                    disabled={
                      !newCharGroupMembers.trim() || saving
                    }
                    style={{
                      background: "var(--accent-green)",
                      color: "#fff",
                      border: "none",
                      borderRadius: "var(--radius-sm)",
                      padding: "4px 8px",
                      fontSize: "0.72rem",
                      cursor:
                        !newCharGroupMembers.trim() || saving
                          ? "not-allowed"
                          : "pointer",
                      opacity: !newCharGroupMembers.trim() || saving ? 0.5 : 1,
                    }}
                  >
                    + Csoport hozzáadása
                  </button>
                </div>
              </div>
            )}
          </div>
        </aside>
      </div>
      
      {/* ── preview modal ──────────────────────────────────────────────── */}
      {previewImage && (
        <div
          style={{
            position: "fixed",
            top: 0,
            left: 0,
            width: "100vw",
            height: "100vh",
            background: "rgba(0, 0, 0, 0.8)",
            zIndex: 9999,
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            padding: "var(--space-lg)",
          }}
          onClick={() => setPreviewImage(null)}
        >
          <div style={{ display: "flex", justifyContent: "flex-end", width: "100%", maxWidth: 1200, marginBottom: "var(--space-md)" }}>
            <button
              className="btn btn-ghost"
              style={{ background: "rgba(255,255,255,0.1)", color: "#fff" }}
              onClick={() => setPreviewImage(null)}
            >
              Bezárás (X)
            </button>
          </div>
          <img
            src={`data:image/png;base64,${previewImage}`}
            alt="Preview"
            style={{
              maxWidth: "100%",
              maxHeight: "calc(100vh - 100px)",
              objectFit: "contain",
              backgroundColor: "#fff",
              borderRadius: "var(--radius-md)",
            }}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}

      {/* Checkbox grouping dialog */}
      {showGroupDialog && (
        <CheckboxGroupDialog
          fields={pageFields}
          onApply={handleBulkCheckboxGroup}
          onClose={() => setShowGroupDialog(false)}
        />
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-sm)",
  padding: "3px 6px",
  fontSize: "0.72rem",
};

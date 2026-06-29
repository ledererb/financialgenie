// ---------------------------------------------------------------------------
// PageEditor – focused page-level editor with image + field overlays + sidebar.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CanonicalField,
  Confidence,
  MappingConfig,
  MappingField,
  PdfField,
  PdfFieldsResponse,
} from "@/types";
import {
  getCanonicalFields,
  getMapping,
  getPdfFields,
  pageImageUrl,
  updateField,
} from "@/api/client";

interface PageEditorProps {
  pdfId: string;
  pageNumber: number;
  onBack: () => void;
}

// ── helpers ────────────────────────────────────────────────────────────────

function overlayClass(
  field: MappingField,
  isSelected: boolean,
): string {
  if (isSelected) return "field-overlay selected";
  if (!field.canonical_field) return "field-overlay unmapped";
  if (field.confidence === "high") return "field-overlay mapped-high";
  if (field.confidence === "medium") return "field-overlay mapped-medium";
  return "field-overlay mapped-low";
}

function confidenceBadgeClass(c: Confidence): string {
  if (c === "high" || c === "manual") return "badge badge-green";
  if (c === "medium") return "badge badge-amber";
  return "badge badge-red";
}

// ── component ──────────────────────────────────────────────────────────────

export default function PageEditor({
  pdfId,
  pageNumber,
  onBack,
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

  const imageContainerRef = useRef<HTMLDivElement>(null);
  const [imageNaturalSize, setImageNaturalSize] = useState<{
    w: number;
    h: number;
  } | null>(null);
  const [imageDisplaySize, setImageDisplaySize] = useState<{
    w: number;
    h: number;
  } | null>(null);

  // ── data fetching ──────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setLoadedPage(null);

    Promise.all([getMapping(pdfId), getPdfFields(pdfId), getCanonicalFields()])
      .then(([m, f, c]) => {
        if (cancelled) return;
        setMapping(m);
        setFieldsRes(f);
        setCanonicals(c);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [pdfId]);

  // Reset selection when page changes
  useEffect(() => {
    setSelectedField(null);
    setEditingField(null);
    setLoadedPage(null);
  }, [pageNumber]);

  // ── derived: fields for this page ─────────────────────────────────────

  const pageFields = useMemo(() => {
    if (!mapping) return [];
    return mapping.fields.filter((f) => f.page_number === pageNumber);
  }, [mapping, pageNumber]);

  const pdfFieldsByName = useMemo(() => {
    if (!fieldsRes) return new Map<string, PdfField>();
    const m = new Map<string, PdfField>();
    for (const f of fieldsRes.fields) m.set(f.pdf_field_name, f);
    return m;
  }, [fieldsRes]);

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

  // ── scale factor for overlays ─────────────────────────────────────────

  const scale = useMemo(() => {
    if (!imageNaturalSize || !imageDisplaySize) return 1;
    return imageDisplaySize.w / imageNaturalSize.w;
  }, [imageNaturalSize, imageDisplaySize]);

  // ── canonical‐field update ────────────────────────────────────────────

  const handleCanonicalChange = useCallback(
    async (fieldName: string, canonicalPath: string | null) => {
      if (!mapping) return;
      setSaving(true);
      const patch: Partial<MappingField> = {
        canonical_field: canonicalPath,
        confidence: canonicalPath ? "manual" : null,
      };
      try {
        await updateField(pdfId, fieldName, patch);
        // Optimistic update
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
        setEditingField(null);
      }
    },
    [mapping, pdfId],
  );

  // ── checkbox-group update ──────────────────────────────────────────────

  const handleCheckboxGroupChange = useCallback(
    async (
      fieldName: string,
      groupId: string,
      matchValue: string,
    ) => {
      if (!mapping) return;
      setSaving(true);
      const cbg =
        groupId || matchValue
          ? { group_id: groupId, match_value: matchValue }
          : null;
      const patch: Partial<MappingField> = { checkbox_group: cbg };
      try {
        await updateField(pdfId, fieldName, patch);
        setMapping((prev) => {
          if (!prev) return prev;
          return {
            ...prev,
            fields: prev.fields.map((f) =>
              f.pdf_field_name === fieldName
                ? { ...f, checkbox_group: cbg }
                : f,
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
        Loading page…
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
        <span className="badge badge-red">Error</span>
        <p style={{ color: "var(--text-secondary)" }}>{error}</p>
        <button className="btn btn-ghost" onClick={onBack}>
          ← Back to Dashboard
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
          padding: "var(--space-sm) var(--space-lg)",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          ← Back to Dashboard
        </button>
        <h2
          style={{
            fontSize: "0.95rem",
            fontWeight: 600,
          }}
        >
          Page {pageNumber}
        </h2>
        <span
          style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}
        >
          {pageFields.length} field{pageFields.length !== 1 ? "s" : ""}
        </span>
        {saving && (
          <span
            className="badge badge-blue"
            style={{ marginLeft: "auto" }}
          >
            Saving…
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
          <div style={{ position: "relative", display: "inline-block" }}>
            <img
              src={pageImageUrl(pdfId, pageNumber)}
              alt={`Page ${pageNumber}`}
              onLoad={handleImageLoad}
              style={{
                maxWidth: "100%",
                height: "auto",
                display: "block",
                borderRadius: "var(--radius-md)",
                boxShadow: "var(--shadow-lg)",
              }}
            />

            {/* field overlays – only when the right page is loaded */}
            {loadedPage === pageNumber &&
              pageFields.map((mf) => {
                const pf = pdfFieldsByName.get(mf.pdf_field_name);
                const rect = pf?.rect ?? mf.coordinates;
                if (!rect) return null;

                return (
                  <div
                    key={mf.pdf_field_name}
                    className={overlayClass(
                      mf,
                      selectedField === mf.pdf_field_name,
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
                  />
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
            }}
          >
            Fields on Page {pageNumber}
          </div>

          <div
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
                No fields on this page.
              </div>
            )}

            {pageFields.map((mf) => {
              const isSelected = selectedField === mf.pdf_field_name;
              const isEditing = editingField === mf.pdf_field_name;

              return (
                <div
                  key={mf.pdf_field_name}
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
                      }}
                      title={mf.pdf_field_name}
                    >
                      {mf.pdf_field_name}
                    </span>
                    <span className={confidenceBadgeClass(mf.confidence)}>
                      {mf.confidence ?? "none"}
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

                    {isEditing ? (
                      <select
                        autoFocus
                        value={mf.canonical_field ?? ""}
                        onChange={(e) => {
                          const val = e.target.value || null;
                          handleCanonicalChange(mf.pdf_field_name, val);
                        }}
                        onBlur={() => setEditingField(null)}
                        onClick={(e) => e.stopPropagation()}
                        style={{
                          flex: 1,
                          background: "var(--bg-tertiary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border-strong)",
                          borderRadius: "var(--radius-sm)",
                          padding: "4px 8px",
                          fontSize: "0.75rem",
                          outline: "none",
                        }}
                      >
                        <option value="">— unmapped —</option>
                        {(() => {
                          // Group canonicals by SF object prefix
                          const groups = new Map<string, CanonicalField[]>();
                          canonicals.forEach((c) => {
                            const dot = c.path.indexOf(".");
                            const obj = dot > 0 ? c.path.slice(0, dot) : "Other";
                            const arr = groups.get(obj);
                            if (arr) arr.push(c);
                            else groups.set(obj, [c]);
                          });
                          return Array.from(groups.entries()).map(
                            ([obj, fields]) => (
                              <optgroup key={obj} label={obj}>
                                {fields.map((c) => (
                                  <option key={c.path} value={c.path}>
                                    {c.path.split(".").pop()} — {c.label}
                                  </option>
                                ))}
                              </optgroup>
                            ),
                          );
                        })()}
                      </select>
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
                          color: mf.canonical_field
                            ? "var(--text-primary)"
                            : "var(--accent-red)",
                          fontStyle: mf.canonical_field ? "normal" : "italic",
                          cursor: "pointer",
                          padding: "2px 4px",
                          borderRadius: "var(--radius-sm)",
                          transition: "background var(--transition-fast)",
                        }}
                        title="Click to edit mapping"
                      >
                        {mf.canonical_field ?? "unmapped"}
                      </span>
                    )}
                  </div>

                  {/* ── checkbox group inputs ──────────────────────── */}
                  {mf.field_type === "checkbox" && (
                    <div
                      onClick={(e) => e.stopPropagation()}
                      style={{
                        marginTop: "var(--space-xs)",
                        padding: "var(--space-xs) var(--space-sm)",
                        background: "var(--bg-tertiary)",
                        borderRadius: "var(--radius-sm)",
                        display: "flex",
                        flexDirection: "column",
                        gap: 4,
                      }}
                    >
                      <span
                        style={{
                          fontSize: "0.65rem",
                          fontWeight: 700,
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                          color: "var(--text-tertiary)",
                        }}
                      >
                        Checkbox Group
                      </span>
                      <input
                        placeholder="group_id"
                        value={mf.checkbox_group?.group_id ?? ""}
                        onChange={(e) =>
                          handleCheckboxGroupChange(
                            mf.pdf_field_name,
                            e.target.value,
                            mf.checkbox_group?.match_value ?? "",
                          )
                        }
                        style={{
                          width: "100%",
                          background: "var(--bg-primary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border-subtle)",
                          borderRadius: "var(--radius-sm)",
                          padding: "3px 6px",
                          fontSize: "0.72rem",
                        }}
                      />
                      <input
                        placeholder="match_value"
                        value={mf.checkbox_group?.match_value ?? ""}
                        onChange={(e) =>
                          handleCheckboxGroupChange(
                            mf.pdf_field_name,
                            mf.checkbox_group?.group_id ?? "",
                            e.target.value,
                          )
                        }
                        style={{
                          width: "100%",
                          background: "var(--bg-primary)",
                          color: "var(--text-primary)",
                          border: "1px solid var(--border-subtle)",
                          borderRadius: "var(--radius-sm)",
                          padding: "3px 6px",
                          fontSize: "0.72rem",
                        }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </aside>
      </div>
    </div>
  );
}

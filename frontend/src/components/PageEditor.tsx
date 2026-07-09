// ---------------------------------------------------------------------------
// PageEditor – focused page-level editor with image + field overlays + sidebar.
// Enhanced with: fill_rule editor, auto-scroll sync, label/notes/field_type
// editing, SF type display, field add/delete, scroll sync.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  CanonicalField,
  Confidence,
  FillRule,
  MappingConfig,
  MappingField,
  PdfField,
  PdfFieldsResponse,
} from "@/types";
import {
  addField,
  deleteField,
  getCanonicalFields,
  getMapping,
  getPdfFields,
  pageImageUrl,
  previewPage,
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

  const [previewImage, setPreviewImage] = useState<string | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);

  // ── draw mode (flat PDF field creation) ────────────────────────────
  const [drawMode, setDrawMode] = useState(false);
  const [drawStart, setDrawStart] = useState<{ x: number; y: number } | null>(null);
  const [drawEnd, setDrawEnd] = useState<{ x: number; y: number } | null>(null);
  const [pendingRect, setPendingRect] = useState<{ x: number; y: number; width: number; height: number } | null>(null);
  const [newFieldName, setNewFieldName] = useState("");
  const [newFieldType, setNewFieldType] = useState("text");

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

  // Unmapped PDF fields (for "add field" feature)
  const unmappedPdfFields = useMemo(() => {
    if (!fieldsRes || !mapping) return [];
    const mappedNames = new Set(mapping.fields.map((f) => f.pdf_field_name));
    return fieldsRes.fields
      .filter((f) => f.page_number === pageNumber && !mappedNames.has(f.pdf_field_name))
      .map((f) => f.pdf_field_name);
  }, [fieldsRes, mapping, pageNumber]);

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

  // ── canonical‐field update ────────────────────────────────────────────

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
        <h2
          style={{
            fontSize: "1.1rem",
            fontWeight: 600,
          }}
        >
          {pageNumber}. oldal
        </h2>
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

                return (
                  <div
                    key={mf.pdf_field_name}
                    data-field-name={mf.pdf_field_name}
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
                <option value="">+ Mezo hozzaadasa ({unmappedPdfFields.length} elerheto)</option>
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
              title="Mapping ujratoltes"
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

            {pageFields.map((mf) => {
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
                      }}
                      title={mf.pdf_field_name}
                    >
                      {mf.pdf_field_name}
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
                      <select
                        autoFocus={isEditing}
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
                        <option value="">— nem mappelt —</option>
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
                                    {c.path.split(".").pop()} — {c.label}{c.sf_type ? ` [${c.sf_type}]` : ""}
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
                        title="Kattints a mapping szerkesztéséhez"
                      >
                        {mf.canonical_field ?? "nem mappelt"}
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

                      {/* Label (Task 3.2) */}
                      <input
                        placeholder="Címke"
                        value={mf.label ?? ""}
                        onChange={(e) => handleFieldUpdate(mf.pdf_field_name, { label: e.target.value || null })}
                        style={inputStyle}
                      />

                      {/* Field type (Task 3.2) */}
                      <select
                        value={mf.field_type}
                        onChange={(e) => handleFieldUpdate(mf.pdf_field_name, { field_type: e.target.value })}
                        style={inputStyle}
                      >
                        <option value="text">Text</option>
                        <option value="number">Number</option>
                        <option value="date">Date</option>
                        <option value="checkbox">Checkbox</option>
                        <option value="dropdown">Dropdown</option>
                        <option value="money">Money</option>
                        <option value="character_split">Character Split</option>
                      </select>

                      {/* Fill rule editor (Task 1.3 — dict format) */}
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

                      {/* ── checkbox group inputs ──────────────────── */}
                      {mf.field_type === "checkbox" && (
                        <div style={{
                          padding: "var(--space-xs) var(--space-sm)",
                          background: "var(--bg-tertiary)",
                          borderRadius: "var(--radius-sm)",
                          display: "flex",
                          flexDirection: "column",
                          gap: 4,
                        }}>
                          <span style={{ fontSize: "0.65rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-tertiary)" }}>
                            Jelölőnégyzet csoport
                          </span>
                          <datalist id="existing-groups">
                            {Array.from(
                              new Set(
                                mapping?.fields
                                  .map((f) => f.checkbox_group?.group_id)
                                  .filter(Boolean)
                              )
                            ).map((g) => (
                              <option key={g} value={g} />
                            ))}
                          </datalist>
                          <input
                            list="existing-groups"
                            placeholder="csoport_azonosító (pl. csaladi_allapot)"
                            value={mf.checkbox_group?.group_id ?? ""}
                            onChange={(e) =>
                              handleFieldUpdate(mf.pdf_field_name, {
                                checkbox_group: e.target.value || mf.checkbox_group?.match_value
                                  ? { group_id: e.target.value, match_value: mf.checkbox_group?.match_value ?? "" }
                                  : null,
                              })
                            }
                            style={inputStyle}
                          />
                          <input
                            placeholder="egyező_érték"
                            value={mf.checkbox_group?.match_value ?? ""}
                            onChange={(e) =>
                              handleFieldUpdate(mf.pdf_field_name, {
                                checkbox_group: mf.checkbox_group?.group_id || e.target.value
                                  ? { group_id: mf.checkbox_group?.group_id ?? "", match_value: e.target.value }
                                  : null,
                              })
                            }
                            style={inputStyle}
                          />
                        </div>
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
            })}
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

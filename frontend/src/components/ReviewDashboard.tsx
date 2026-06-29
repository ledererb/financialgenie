// ---------------------------------------------------------------------------
// ReviewDashboard – shows AI recognition results at a glance.
// Stat cards → page heatmap → conflicts → approve / go back.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useState } from "react";
import type { MappingConfig, MappingField, PdfFieldsResponse } from "@/types";
import { getMapping, getPdfFields } from "@/api/client";

interface ReviewDashboardProps {
  pdfId: string;
  onPageClick: (page: number) => void;
  onBack: () => void;
  onApprove: () => void;
  onStatsReady?: (mapped: number, total: number) => void;
}

// ── helpers ────────────────────────────────────────────────────────────────

function confidenceBadge(level: "high" | "medium" | "low", count: number) {
  const cls =
    level === "high"
      ? "badge badge-green"
      : level === "medium"
        ? "badge badge-amber"
        : "badge badge-red";
  return (
    <span className={cls} key={level}>
      {level} {count}
    </span>
  );
}

// ── component ──────────────────────────────────────────────────────────────

export default function ReviewDashboard({
  pdfId,
  onPageClick,
  onBack,
  onApprove,
  onStatsReady,
}: ReviewDashboardProps) {
  const [mapping, setMapping] = useState<MappingConfig | null>(null);
  const [fieldsRes, setFieldsRes] = useState<PdfFieldsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── data fetching ──────────────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([getMapping(pdfId), getPdfFields(pdfId)])
      .then(([m, f]) => {
        if (cancelled) return;
        setMapping(m);
        setFieldsRes(f);
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

  // ── derived stats ──────────────────────────────────────────────────────

  const stats = useMemo(() => {
    if (!mapping) return null;
    const fields = mapping.fields;
    const total = fields.length;
    const mapped = fields.filter((f) => f.canonical_field).length;
    const unmapped = total - mapped;
    const high = fields.filter((f) => f.confidence === "high").length;
    const medium = fields.filter((f) => f.confidence === "medium").length;
    const low = fields.filter(
      (f) => f.confidence === "low" || f.confidence === null,
    ).length;
    return { total, mapped, unmapped, high, medium, low };
  }, [mapping]);

  // Notify parent of mapped/total counts when stats change
  useEffect(() => {
    if (stats && onStatsReady) {
      onStatsReady(stats.mapped, stats.total);
    }
  }, [stats, onStatsReady]);

  // ── page heatmap data ─────────────────────────────────────────────────

  const pageHeatmap = useMemo(() => {
    if (!mapping || !fieldsRes) return [];
    const totalPages = fieldsRes.total_pages;
    const pages: { page: number; total: number; mapped: number }[] = [];

    for (let p = 1; p <= totalPages; p++) {
      const pageFields = mapping.fields.filter((f) => f.page_number === p);
      const total = pageFields.length;
      const mapped = pageFields.filter((f) => f.canonical_field).length;
      pages.push({ page: p, total, mapped });
    }
    return pages;
  }, [mapping, fieldsRes]);

  const heatmapClass = useCallback(
    (entry: { total: number; mapped: number }) => {
      if (entry.total === 0) return "heatmap-cell none";
      const pct = entry.mapped / entry.total;
      if (pct > 0.8) return "heatmap-cell full";
      if (pct >= 0.2) return "heatmap-cell partial";
      return "heatmap-cell empty";
    },
    [],
  );

  // ── conflicts: same canonical mapped >1 times ─────────────────────────

  const conflicts = useMemo(() => {
    if (!mapping) return [];
    const byCanonical = new Map<string, MappingField[]>();

    for (const f of mapping.fields) {
      if (!f.canonical_field) continue;
      const arr = byCanonical.get(f.canonical_field);
      if (arr) arr.push(f);
      else byCanonical.set(f.canonical_field, [f]);
    }

    const result: { canonical: string; fields: MappingField[] }[] = [];
    byCanonical.forEach((fields, canonical) => {
      if (fields.length > 1) result.push({ canonical, fields });
    });
    return result;
  }, [mapping]);

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
        Mapping adatok betöltése…
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
          ← Vissza
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
          borderRadius: 0,
          borderLeft: "none",
          borderRight: "none",
          borderTop: "none",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
        }}
      >
        <h2 style={{ fontSize: "1.15rem", fontWeight: 600 }}>
          Mapping áttekintése
        </h2>
        <span
          style={{ fontSize: "0.8rem", color: "var(--text-tertiary)" }}
        >
          {mapping?.bank_name ?? ""} — {mapping?.form_name ?? pdfId}
        </span>
      </header>

      {/* ── scrollable body ──────────────────────────────────────────── */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--space-lg)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-lg)",
        }}
      >
        {/* ─── stat cards ──────────────────────────────────────────── */}
        {stats && (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(4, 1fr)",
              gap: "var(--space-md)",
            }}
          >
            <div className="card card-stat">
              <div className="stat-value">{stats.total}</div>
              <div className="stat-label">Összes mező</div>
            </div>

            <div className="card card-stat">
              <div className="stat-value" style={{ color: "var(--accent-green)" }}>
                {stats.mapped}
              </div>
              <div className="stat-label">
                Leképezett <span className="badge badge-green">✓</span>
              </div>
            </div>

            <div className="card card-stat">
              <div className="stat-value" style={{ color: "var(--accent-red)" }}>
                {stats.unmapped}
              </div>
              <div className="stat-label">
                Nem mappelt <span className="badge badge-red">✗</span>
              </div>
            </div>

            <div className="card card-stat">
              <div
                style={{
                  display: "flex",
                  justifyContent: "center",
                  gap: "var(--space-sm)",
                  marginBottom: "var(--space-xs)",
                }}
              >
                {confidenceBadge("high", stats.high)}
                {confidenceBadge("medium", stats.medium)}
                {confidenceBadge("low", stats.low)}
              </div>
              <div className="stat-label">Magabiztosság</div>
            </div>
          </div>
        )}

        {/* ─── page heatmap ────────────────────────────────────────── */}
        <section className="card" style={{ padding: "var(--space-lg)" }}>
          <h3
            style={{
              fontSize: "0.85rem",
              fontWeight: 600,
              marginBottom: "var(--space-md)",
              color: "var(--text-secondary)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Oldal lefedettség
          </h3>

          <div className="heatmap-grid">
            {pageHeatmap.map((entry) => (
              <div
                key={entry.page}
                className={heatmapClass(entry)}
                onClick={() => onPageClick(entry.page)}
                title={`${entry.page}. oldal: ${entry.mapped}/${entry.total} leképezve`}
              >
                {entry.page}
              </div>
            ))}
          </div>

          <div
            style={{
              display: "flex",
              gap: "var(--space-md)",
              marginTop: "var(--space-md)",
              fontSize: "0.7rem",
              color: "var(--text-tertiary)",
            }}
          >
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: "rgba(34,197,94,0.4)",
                  marginRight: 4,
                  verticalAlign: "middle",
                }}
              />
              &gt;80%
            </span>
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: "rgba(245,158,11,0.3)",
                  marginRight: 4,
                  verticalAlign: "middle",
                }}
              />
              20–80%
            </span>
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: "rgba(239,68,68,0.25)",
                  marginRight: 4,
                  verticalAlign: "middle",
                }}
              />
              &lt;20%
            </span>
            <span>
              <span
                style={{
                  display: "inline-block",
                  width: 10,
                  height: 10,
                  borderRadius: 2,
                  background: "var(--bg-elevated)",
                  marginRight: 4,
                  verticalAlign: "middle",
                }}
              />
              Nincs mező
            </span>
          </div>
        </section>

        {/* ─── conflicts ───────────────────────────────────────────── */}
        {conflicts.length > 0 && (
          <section className="card" style={{ padding: "var(--space-lg)" }}>
            <h3
              style={{
                fontSize: "0.85rem",
                fontWeight: 600,
                marginBottom: "var(--space-md)",
                color: "var(--accent-amber)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
              }}
            >
              Ütközések ({conflicts.length})
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {conflicts.map(({ canonical, fields }) => (
                <div className="mapping-row" key={canonical}>
                  <span
                    className="badge badge-amber"
                    style={{ flexShrink: 0 }}
                  >
                    {fields.length}×
                  </span>
                  <span
                    style={{
                      fontWeight: 600,
                      fontSize: "0.85rem",
                      minWidth: 180,
                    }}
                  >
                    {canonical}
                  </span>
                  <span
                    style={{
                      fontSize: "0.75rem",
                      color: "var(--text-tertiary)",
                    }}
                  >
                    {fields.map((f) => f.pdf_field_name).join(" · ")}
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {conflicts.length === 0 && (
          <section
            className="card"
            style={{
              padding: "var(--space-lg)",
              textAlign: "center",
              color: "var(--text-secondary)",
              fontSize: "0.85rem",
            }}
          >
            Nincs mapping ütközés
          </section>
        )}
      </div>

      {/* ── action bar ───────────────────────────────────────────────── */}
      <div
        className="glass-strong"
        style={{
          padding: "var(--space-md) var(--space-lg)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <button className="btn btn-ghost" onClick={onBack}>
          ← Vissza
        </button>
        <button className="btn btn-success" onClick={onApprove}>
          Mapping jóváhagyása
        </button>
      </div>
    </div>
  );
}

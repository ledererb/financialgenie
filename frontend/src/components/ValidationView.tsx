import { useState, useEffect } from "react";
import type { PackageResult } from "@/types";

interface ValidationField {
  pdf_field_name: string;
  expected: string;
  actual_pikepdf: string;
  actual_mupdf: string;
  status: "ok" | "mismatch" | "missing" | "empty";
  detail: string;
}

interface AIIssue {
  severity: "critical" | "warning" | "info";
  field: string;
  problem: string;
  suggestion: string;
}

interface ValidationData {
  total_fields: number;
  ok_fields: number;
  mismatch_fields: number;
  missing_fields: number;
  empty_fields: number;
  score: number;
  fields: ValidationField[];
  ai_issues: AIIssue[];
  ai_summary: string;
}

interface ValidationViewProps {
  pdfId: string;
  dealId: string;
  onClose: () => void;
}

export default function ValidationView({ pdfId, dealId, onClose }: ValidationViewProps) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ValidationData | null>(null);
  const [activeTab, setActiveTab] = useState<"fields" | "ai">("fields");

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetch("/api/pdf/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pdf_id: pdfId, deal_id: dealId, ai_review: true }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (res.detail) {
          setError(res.detail);
        } else {
          setData(res.validation);
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [pdfId, dealId]);

  const severityColor = (sev: string) => {
    if (sev === "critical") return "var(--accent-red)";
    if (sev === "warning") return "var(--accent-amber)";
    return "var(--accent-blue)";
  };

  const statusColor = (status: string) => {
    if (status === "ok") return "var(--accent-green)";
    if (status === "mismatch") return "var(--accent-red)";
    if (status === "missing") return "var(--accent-amber)";
    return "var(--text-tertiary)";
  };

  const statusLabel = (status: string) => {
    if (status === "ok") return "OK";
    if (status === "mismatch") return "Nem egyezik";
    if (status === "missing") return "Hiányzik";
    return "Üres";
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-primary)" }}>
      {/* Header */}
      <div
        className="glass"
        style={{
          padding: "12px 24px",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
          borderLeft: "none",
          borderRight: "none",
          borderTop: "none",
        }}
      >
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Vissza</button>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
          Kitöltés ellenőrzése
        </h2>
        {data && (
          <span
            style={{
              fontSize: "0.85rem",
              fontWeight: 600,
              color: data.score >= 90 ? "var(--accent-green)" : data.score >= 70 ? "var(--accent-amber)" : "var(--accent-red)",
            }}
          >
            {data.score}% egyezés
          </span>
        )}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-xl) 24px" }}>
        {loading ? (
          <p style={{ textAlign: "center", color: "var(--text-tertiary)", paddingTop: "var(--space-2xl)" }}>
            Validálás folyamatban...
          </p>
        ) : error ? (
          <p style={{ textAlign: "center", color: "var(--accent-red)", paddingTop: "var(--space-2xl)" }}>
            {error}
          </p>
        ) : data ? (
          <div style={{ maxWidth: 900, margin: "0 auto" }}>
            {/* Summary stats */}
            <div style={{ display: "flex", gap: "16px", marginBottom: "var(--space-xl)" }}>
              {[
                { label: "Összes mező", value: data.total_fields, color: "var(--text-secondary)" },
                { label: "Egyezik", value: data.ok_fields, color: "var(--accent-green)" },
                { label: "Nem egyezik", value: data.mismatch_fields, color: "var(--accent-red)" },
                { label: "Hiányzik", value: data.missing_fields, color: "var(--accent-amber)" },
                { label: "Üres", value: data.empty_fields, color: "var(--text-tertiary)" },
              ].map((s) => (
                <div key={s.label} style={{ textAlign: "center" }}>
                  <div style={{ fontSize: "1.5rem", fontWeight: 700, color: s.color }}>{s.value}</div>
                  <div style={{ fontSize: "0.7rem", color: "var(--text-tertiary)" }}>{s.label}</div>
                </div>
              ))}
            </div>

            {/* Tabs */}
            <div style={{ display: "flex", gap: "2px", marginBottom: "var(--space-md)", borderBottom: "1px solid var(--border-subtle)" }}>
              {(["fields", "ai"] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveTab(tab)}
                  style={{
                    background: activeTab === tab ? "var(--bg-tertiary)" : "transparent",
                    border: "none",
                    borderBottom: activeTab === tab ? "2px solid var(--accent-blue)" : "2px solid transparent",
                    color: activeTab === tab ? "var(--text-primary)" : "var(--text-tertiary)",
                    padding: "8px 16px",
                    fontSize: "0.8rem",
                    fontWeight: activeTab === tab ? 600 : 400,
                    cursor: "pointer",
                  }}
                >
                  {tab === "fields" ? "Mező ellenőrzés" : "AI elemzés"}
                </button>
              ))}
            </div>

            {/* Fields tab */}
            {activeTab === "fields" && (
              <div style={{ display: "flex", flexDirection: "column", gap: "1px" }}>
                {data.fields
                  .filter((f) => f.status !== "empty")
                  .sort((a, b) => {
                    const order = { mismatch: 0, missing: 1, ok: 2, empty: 3 };
                    return (order[a.status as keyof typeof order] ?? 3) - (order[b.status as keyof typeof order] ?? 3);
                  })
                  .map((f, i) => (
                    <div
                      key={i}
                      style={{
                        display: "flex",
                        gap: "12px",
                        padding: "6px 12px",
                        background: f.status !== "ok" ? "var(--bg-tertiary)" : "transparent",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.78rem",
                      }}
                    >
                      <span style={{ color: statusColor(f.status), fontWeight: 600, width: 70, flexShrink: 0 }}>
                        {statusLabel(f.status)}
                      </span>
                      <span style={{ flex: 1, color: "var(--text-primary)" }}>{f.pdf_field_name}</span>
                      <span style={{ color: "var(--text-secondary)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis" }}>
                        {f.expected}
                      </span>
                      {f.status !== "ok" && f.detail && (
                        <span style={{ color: "var(--accent-amber)", fontSize: "0.7rem" }}>{f.detail}</span>
                      )}
                    </div>
                  ))}
              </div>
            )}

            {/* AI tab */}
            {activeTab === "ai" && (
              <div>
                {data.ai_summary && (
                  <div
                    style={{
                      padding: "12px 16px",
                      background: "var(--bg-tertiary)",
                      borderRadius: "var(--radius-sm)",
                      border: "1px solid var(--border-subtle)",
                      marginBottom: "var(--space-md)",
                      fontSize: "0.85rem",
                      color: "var(--text-secondary)",
                      lineHeight: 1.6,
                    }}
                  >
                    {data.ai_summary}
                  </div>
                )}

                {data.ai_issues.length === 0 ? (
                  <p style={{ color: "var(--accent-green)", fontSize: "0.85rem" }}>
                    Az AI nem talált problémát.
                  </p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {data.ai_issues.map((issue, i) => (
                      <div
                        key={i}
                        style={{
                          padding: "10px 12px",
                          background: "var(--bg-tertiary)",
                          borderRadius: "var(--radius-sm)",
                          border: `1px solid ${severityColor(issue.severity)}`,
                          borderLeftWidth: 3,
                        }}
                      >
                        <div style={{ display: "flex", gap: "8px", alignItems: "center", marginBottom: "4px" }}>
                          <span
                            style={{
                              fontSize: "0.7rem",
                              fontWeight: 700,
                              color: severityColor(issue.severity),
                              textTransform: "uppercase",
                            }}
                          >
                            {issue.severity === "critical" ? "Kritikus" : issue.severity === "warning" ? "Figyelmeztetés" : "Info"}
                          </span>
                          <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-primary)" }}>
                            {issue.field}
                          </span>
                        </div>
                        <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "4px" }}>
                          {issue.problem}
                        </div>
                        <div style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", fontStyle: "italic" }}>
                          {issue.suggestion}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}

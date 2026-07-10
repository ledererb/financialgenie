"use client";

import { useStore } from "@/store";

export function ApplicantSelector() {
  const applicants = useStore((s) => s.applicants);
  const selectedApplicantId = useStore((s) => s.selectedApplicantId);
  const selectApplicant = useStore((s) => s.selectApplicant);
  const catalog = useStore((s) => s.catalog);
  const selectedProductId = useStore((s) => s.selectedProductId);

  if (applicants.length === 0) return null;

  // Count per-applicant documents for the selected product
  const productDocs = catalog?.documents.filter((d) =>
    d.product_ids.includes(selectedProductId ?? ""),
  ) ?? [];
  const perApplicantCount = productDocs.filter((d) => d.per_applicant).length;

  const useDropdown = applicants.length > 3;

  return (
    <div
      className="animate-fade-in"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-sm)",
      }}
    >
      {useDropdown ? (
        <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)" }}>
          <label
            style={{
              fontSize: "0.8rem",
              fontWeight: 600,
              color: "var(--text-secondary)",
            }}
          >
            Aktív igénylő
          </label>
          <select
            value={selectedApplicantId ?? ""}
            onChange={(e) => selectApplicant(e.target.value)}
            style={{
              flex: 1,
              padding: "8px 12px",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              color: "var(--text-primary)",
              fontSize: "0.85rem",
              cursor: "pointer",
            }}
          >
            {applicants.map((a) => (
              <option key={a.id} value={a.id}>
                {a.role === "primary" ? "👤 " : "👥 "}
                {a.name}
                {a.role === "primary" ? " (Főadós)" : " (Adóstárs)"}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <div
          style={{
            display: "flex",
            gap: "var(--space-xs)",
            flexWrap: "wrap",
          }}
        >
          {applicants.map((a) => {
            const isActive = a.id === selectedApplicantId;
            return (
              <button
                key={a.id}
                onClick={() => selectApplicant(a.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "8px 16px",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.85rem",
                  fontWeight: isActive ? 600 : 400,
                  cursor: "pointer",
                  border: isActive
                    ? "1px solid var(--accent-green)"
                    : "1px solid var(--border-default)",
                  background: isActive
                    ? "var(--accent-green-glow)"
                    : "var(--bg-secondary)",
                  color: isActive
                    ? "var(--accent-green)"
                    : "var(--text-secondary)",
                  transition: "all var(--transition-fast)",
                }}
              >
                <span style={{ fontSize: "0.95rem" }}>
                  {a.role === "primary" ? "👤" : "👥"}
                </span>
                {a.name}
              </button>
            );
          })}
        </div>
      )}

      {/* Summary hint */}
      {perApplicantCount > 0 && applicants.length > 1 && (
        <p
          style={{
            fontSize: "0.75rem",
            color: "var(--accent-amber)",
            margin: 0,
          }}
        >
          {perApplicantCount} dokumentum kell fejenként kitölteni
        </p>
      )}
    </div>
  );
}

export default ApplicantSelector;

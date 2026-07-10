"use client";

import { useStore } from "@/store";

export function FillSummary() {
  const catalog = useStore((s) => s.catalog);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const applicants = useStore((s) => s.applicants);

  const applicantCount = applicants.length;

  const productDocs = catalog?.documents.filter((d) =>
    d.product_ids.includes(selectedProductId ?? ""),
  ) ?? [];

  const perApplicantDocs = productDocs.filter((d) => d.per_applicant);
  const uniqueDocs = productDocs.filter((d) => !d.per_applicant);

  const perApplicantTotal = perApplicantDocs.length * applicantCount;
  const grandTotal = perApplicantTotal + uniqueDocs.length;

  // No product selected or no documents — don't show the card
  if (!selectedProductId || productDocs.length === 0) return null;

  return (
    <div
      className="card animate-fade-in"
      style={{
        padding: "var(--space-md) var(--space-lg)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "var(--space-lg)",
        flexWrap: "wrap",
      }}
    >
      <div style={{ display: "flex", gap: "var(--space-lg)", flexWrap: "wrap" }}>
        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: "1.3rem",
              fontWeight: 700,
              color: "var(--text-primary)",
            }}
          >
            {productDocs.length}
          </div>
          <div
            style={{
              fontSize: "0.7rem",
              color: "var(--text-tertiary)",
              textTransform: "uppercase",
            }}
          >
            Összes dokumentum
          </div>
        </div>

        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: "1.3rem",
              fontWeight: 700,
              color: "var(--accent-amber)",
            }}
          >
            {perApplicantDocs.length} × {applicantCount}
          </div>
          <div
            style={{
              fontSize: "0.7rem",
              color: "var(--text-tertiary)",
              textTransform: "uppercase",
            }}
          >
            Ebből fejenkénti ({perApplicantTotal})
          </div>
        </div>

        <div style={{ textAlign: "center" }}>
          <div
            style={{
              fontSize: "1.3rem",
              fontWeight: 700,
              color: "var(--accent-blue)",
            }}
          >
            {uniqueDocs.length}
          </div>
          <div
            style={{
              fontSize: "0.7rem",
              color: "var(--text-tertiary)",
              textTransform: "uppercase",
            }}
          >
            Egyedi (egyszer kitöltendő)
          </div>
        </div>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "var(--space-sm) var(--space-md)",
          background: "var(--accent-green-glow)",
          borderRadius: "var(--radius-md)",
          border: "1px solid rgba(34,197,94,0.3)",
        }}
      >
        <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
          Generálandó fájlok összesen:
        </span>
        <span
          style={{
            fontSize: "1.5rem",
            fontWeight: 700,
            color: "var(--accent-green)",
          }}
        >
          {grandTotal}
        </span>
      </div>
    </div>
  );
}

export default FillSummary;

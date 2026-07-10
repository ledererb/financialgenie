import { useEffect } from "react";
import { useStore } from "@/store";
import AdminCatalogPage from "./AdminCatalogPage";

interface AdminPageProps {
  onBack: () => void;
}

/**
 * Phase 6 — Admin page container.
 *
 * Holds the top-level navigation back to the Mapping Studio and renders
 * the catalog management section. Additional admin sections can be added
 * as tabs here in the future.
 */
export default function AdminPage({ onBack }: AdminPageProps) {
  const catalog = useStore((s) => s.catalog);
  const loadCatalog = useStore((s) => s.loadCatalog);

  useEffect(() => {
    loadCatalog();
  }, [loadCatalog]);

  return (
    <div
      style={{
        height: "100%",
        display: "flex",
        flexDirection: "column",
        background: "var(--bg-primary)",
      }}
    >
      {/* Header */}
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "16px 28px",
          borderBottom: "1px solid var(--border-subtle)",
          background: "var(--bg-secondary)",
          flexShrink: 0,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <button
            className="btn btn-ghost"
            onClick={onBack}
            style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 12px", fontSize: "0.85rem" }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M19 12H5M12 19l-7-7 7-7" />
            </svg>
            Vissza a Stúdióhoz
          </button>
          <h1
            style={{
              fontSize: "1.15rem",
              fontWeight: 600,
              color: "var(--text-primary)",
              letterSpacing: "-0.01em",
            }}
          >
            Admin — Katalógus
          </h1>
        </div>

        {catalog && (
          <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
            {catalog.banks.length} bank · {catalog.documents.length} dokumentum
          </span>
        )}
      </header>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto" }} className="animate-fade-in">
        <AdminCatalogPage />
      </div>
    </div>
  );
}

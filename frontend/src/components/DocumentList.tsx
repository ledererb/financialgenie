import { memo, useState } from "react";
import type { CatalogDocument } from "@/types";

interface DocumentListProps {
  documents: CatalogDocument[];
  selectedDocumentId: string | null;
  onSelectDocument: (docId: string) => void;
  onDeleteDocument: (docId: string, title: string) => void;
}

function DocumentListImpl({
  documents,
  selectedDocumentId,
  onSelectDocument,
  onDeleteDocument,
}: DocumentListProps) {
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  if (documents.length === 0) {
    return (
      <p
        style={{
          fontSize: "0.75rem",
          color: "var(--text-tertiary)",
          fontStyle: "italic",
          padding: "var(--space-xs) var(--space-sm)",
        }}
      >
        Nincs dokumentum
      </p>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
      {documents.map((doc) => {
        const isSelected = doc.id === selectedDocumentId;
        const isShared = doc.product_ids.length > 1;
        const hasNoProducts = doc.product_ids.length === 0;

        return (
          <div key={doc.id}>
            <div
              className="tree-leaf"
              onClick={() => onSelectDocument(doc.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "5px var(--space-sm) 5px var(--space-sm)",
                borderRadius: "var(--radius-sm)",
                cursor: "pointer",
                transition: "background var(--transition-fast)",
                background: isSelected ? "var(--accent-blue-glow)" : "transparent",
              }}
              onMouseEnter={(e) => {
                if (!isSelected) e.currentTarget.style.background = "var(--bg-hover)";
              }}
              onMouseLeave={(e) => {
                if (!isSelected) e.currentTarget.style.background = "transparent";
              }}
            >
              {/* File icon */}
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke={isSelected ? "var(--accent-blue)" : "var(--text-tertiary)"}
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ flexShrink: 0 }}
              >
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>

              <span
                style={{
                  flex: 1,
                  fontSize: "0.78rem",
                  color: isSelected
                    ? "var(--text-primary)"
                    : "var(--text-secondary)",
                  fontWeight: isSelected ? 500 : 400,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
                title={doc.title}
              >
                {doc.title}
              </span>

              {/* Badges */}
              <div style={{ display: "flex", gap: "3px", flexShrink: 0, alignItems: "center" }}>
                {hasNoProducts && (
                  <span
                    className="badge"
                    title="Nincs termékhez rendelve"
                    style={{
                      fontSize: "0.6rem",
                      padding: "1px 6px",
                      background: "var(--accent-red-glow)",
                      color: "var(--accent-red)",
                    }}
                  >
                    ⚠︎
                  </span>
                )}
                {isShared && (
                  <span
                    className="badge badge-purple"
                    title={`Megosztott ${doc.product_ids.length} termékben`}
                    style={{ fontSize: "0.6rem", padding: "1px 6px" }}
                  >
                    ⊕ {doc.product_ids.length}×
                  </span>
                )}
                {doc.per_applicant && (
                  <span
                    className="badge badge-amber"
                    title="Igénylőnként kitöltendő"
                    style={{ fontSize: "0.6rem", padding: "1px 6px" }}
                  >
                    👤
                  </span>
                )}
                {/* Delete button */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDeleteId(doc.id);
                  }}
                  title="Dokumentum törlése"
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--text-tertiary)",
                    cursor: "pointer",
                    padding: "0 2px",
                    fontSize: "0.8rem",
                    lineHeight: 1,
                  }}
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Delete confirmation */}
            {confirmDeleteId === doc.id && (
              <div
                style={{
                  margin: "2px var(--space-sm)",
                  padding: "var(--space-xs) var(--space-sm)",
                  background: "var(--accent-red-glow)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid rgba(239,68,68,0.3)",
                }}
              >
                <p
                  style={{
                    fontSize: "0.7rem",
                    color: "var(--accent-red)",
                    margin: "0 0 var(--space-xs) 0",
                    lineHeight: 1.4,
                  }}
                >
                  Törli a dokumentumot? A fájl is törlődik a lemezről.
                </p>
                <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                  <button
                    className="btn btn-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteDocument(doc.id, doc.title);
                      setConfirmDeleteId(null);
                    }}
                    style={{
                      background: "var(--accent-red)",
                      color: "white",
                      fontSize: "0.7rem",
                      padding: "3px 10px",
                    }}
                  >
                    Igen
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmDeleteId(null);
                    }}
                    style={{ fontSize: "0.7rem", padding: "3px 10px" }}
                  >
                    Mégse
                  </button>
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default memo(DocumentListImpl);

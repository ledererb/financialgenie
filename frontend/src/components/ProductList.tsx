import { useState, memo } from "react";
import type { Product, CatalogDocument } from "@/types";
import DocumentList from "./DocumentList";

interface ProductListProps {
  product: Product;
  documents: CatalogDocument[];
  selectedProductId: string | null;
  selectedDocumentId: string | null;
  onSelectProduct: (productId: string) => void;
  onSelectDocument: (docId: string) => void;
  onDeleteProduct: (productId: string, name: string) => void;
  onDeleteDocument: (docId: string, title: string) => void;
}

function ProductListImpl({
  product,
  documents,
  selectedProductId,
  selectedDocumentId,
  onSelectProduct,
  onSelectDocument,
  onDeleteProduct,
  onDeleteDocument,
}: ProductListProps) {
  const [expanded, setExpanded] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const isProductSelected = product.id === selectedProductId;
  const docCount = documents.length;

  return (
    <div>
      {/* Product header row */}
      <div
        className="tree-node"
        onClick={() => {
          onSelectProduct(product.id);
          setExpanded((prev) => !prev);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "5px var(--space-xs)",
          borderRadius: "var(--radius-sm)",
          cursor: "pointer",
          transition: "background var(--transition-fast)",
          background: isProductSelected ? "var(--accent-blue-glow)" : "transparent",
        }}
        onMouseEnter={(e) => {
          if (!isProductSelected) e.currentTarget.style.background = "var(--bg-hover)";
        }}
        onMouseLeave={(e) => {
          if (!isProductSelected) e.currentTarget.style.background = "transparent";
        }}
      >
        {/* Expand/collapse arrow */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--text-tertiary)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            flexShrink: 0,
            transition: "transform var(--transition-fast)",
            transform: expanded ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>

        {/* Folder icon */}
        <svg
          width="14"
          height="14"
          viewBox="0 0 24 24"
          fill="none"
          stroke={isProductSelected ? "var(--accent-blue)" : "var(--accent-amber)"}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0 }}
        >
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>

        <span
          style={{
            flex: 1,
            fontSize: "0.82rem",
            fontWeight: isProductSelected ? 600 : 500,
            color: isProductSelected
              ? "var(--text-primary)"
              : "var(--text-secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={product.name}
        >
          {product.name}
        </span>

        {/* Document count badge */}
        <span
          className="badge badge-blue"
          style={{ fontSize: "0.6rem", padding: "1px 7px", flexShrink: 0 }}
        >
          {docCount} {docCount === 1 ? "dok." : "dok."}
        </span>

        {/* Delete product button */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            setConfirmDelete(true);
          }}
          title="Termék törlése"
          style={{
            background: "none",
            border: "none",
            color: "var(--text-tertiary)",
            cursor: "pointer",
            padding: "0 2px",
            fontSize: "0.8rem",
            lineHeight: 1,
            flexShrink: 0,
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--accent-red)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-tertiary)")}
        >
          ✕
        </button>
      </div>

      {/* Delete confirmation */}
      {confirmDelete && (
        <div
          style={{
            margin: "2px var(--space-xs)",
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
            Törli a terméket? A dokumentumok megmaradnak, de eltűnnek alóla.
          </p>
          <div style={{ display: "flex", gap: "var(--space-xs)" }}>
            <button
              className="btn btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteProduct(product.id, product.name);
                setConfirmDelete(false);
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
                setConfirmDelete(false);
              }}
              style={{ fontSize: "0.7rem", padding: "3px 10px" }}
            >
              Mégse
            </button>
          </div>
        </div>
      )}

      {/* Expanded content */}
      {expanded && !confirmDelete && (
        <div style={{ paddingLeft: "26px" }}>
          <DocumentList
            documents={documents}
            selectedDocumentId={selectedDocumentId}
            onSelectDocument={onSelectDocument}
            onDeleteDocument={onDeleteDocument}
          />
        </div>
      )}
    </div>
  );
}

export default memo(ProductListImpl);

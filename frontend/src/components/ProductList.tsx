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
}

function ProductListImpl({
  product,
  documents,
  selectedProductId,
  selectedDocumentId,
  onSelectProduct,
  onSelectDocument,
}: ProductListProps) {
  const [expanded, setExpanded] = useState(true);
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
      </div>

      {/* Expanded content */}
      {expanded && (
        <div style={{ paddingLeft: "26px" }}>
          <DocumentList
            documents={documents}
            selectedDocumentId={selectedDocumentId}
            onSelectDocument={onSelectDocument}
          />
        </div>
      )}
    </div>
  );
}

export default memo(ProductListImpl);

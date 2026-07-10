import { useState, memo } from "react";
import type { Bank, CatalogDocument } from "@/types";
import ProductList from "./ProductList";

interface BankSelectorProps {
  bank: Bank;
  documents: CatalogDocument[];
  selectedBankId: string | null;
  selectedProductId: string | null;
  selectedDocumentId: string | null;
  onSelectBank: (bankId: string) => void;
  onSelectProduct: (productId: string) => void;
  onSelectDocument: (docId: string) => void;
  onAddProduct: (bankId: string) => void;
}

function BankSelectorImpl({
  bank,
  documents,
  selectedBankId,
  selectedProductId,
  selectedDocumentId,
  onSelectBank,
  onSelectProduct,
  onSelectDocument,
  onAddProduct,
}: BankSelectorProps) {
  const [expanded, setExpanded] = useState(true);
  const isBankSelected = bank.id === selectedBankId;

  // Auto-expand if this bank or any of its products is selected
  const hasSelectedChild =
    selectedProductId !== null &&
    bank.products.some((p) => p.id === selectedProductId);

  const docsForProduct = (productId: string): CatalogDocument[] =>
    documents.filter((d) => d.product_ids.includes(productId));

  return (
    <div
      style={{
        borderRadius: "var(--radius-md)",
        border: isBankSelected
          ? "1px solid var(--accent-blue)"
          : "1px solid var(--border-subtle)",
        overflow: "hidden",
        transition: "border-color var(--transition-fast)",
      }}
    >
      {/* Bank header row */}
      <div
        className="tree-node"
        onClick={() => {
          onSelectBank(bank.id);
          setExpanded((prev) => !prev);
        }}
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-xs)",
          padding: "8px var(--space-sm)",
          cursor: "pointer",
          background: isBankSelected
            ? "var(--accent-blue-glow)"
            : "var(--bg-tertiary)",
          transition: "background var(--transition-fast)",
        }}
        onMouseEnter={(e) => {
          if (!isBankSelected) e.currentTarget.style.background = "var(--bg-hover)";
        }}
        onMouseLeave={(e) => {
          if (!isBankSelected) e.currentTarget.style.background = "var(--bg-tertiary)";
        }}
      >
        {/* Expand/collapse arrow */}
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--text-secondary)"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{
            flexShrink: 0,
            transition: "transform var(--transition-fast)",
            transform: expanded || hasSelectedChild ? "rotate(90deg)" : "rotate(0deg)",
          }}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>

        {/* Bank icon */}
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke={isBankSelected ? "var(--accent-blue)" : "var(--accent-green)"}
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ flexShrink: 0 }}
        >
          <path d="M3 21h18" />
          <path d="M3 7v14" />
          <path d="M21 7v14" />
          <path d="M3 7l9-4 9 4" />
          <path d="M9 21v-8h6v8" />
        </svg>

        <span
          style={{
            flex: 1,
            fontSize: "0.88rem",
            fontWeight: 600,
            color: isBankSelected
              ? "var(--text-primary)"
              : "var(--text-secondary)",
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
          title={bank.name}
        >
          {bank.name}
        </span>

        {/* Product count badge */}
        <span
          className="badge badge-green"
          style={{ fontSize: "0.6rem", padding: "1px 7px", flexShrink: 0 }}
        >
          {bank.products.length} termék
        </span>

        {/* Add Product button */}
        <button
          className="btn btn-ghost btn-sm"
          onClick={(e) => {
            e.stopPropagation();
            onAddProduct(bank.id);
          }}
          title="Új termék hozzáadása"
          style={{
            padding: "3px 8px",
            fontSize: "0.7rem",
            flexShrink: 0,
          }}
        >
          + Termék
        </button>
      </div>

      {/* Expanded content — products */}
      {(expanded || hasSelectedChild) && (
        <div
          style={{
            padding: "var(--space-xs) var(--space-sm) var(--space-sm)",
            display: "flex",
            flexDirection: "column",
            gap: "2px",
          }}
        >
          {bank.products.length === 0 ? (
            <p
              style={{
                fontSize: "0.75rem",
                color: "var(--text-tertiary)",
                fontStyle: "italic",
                padding: "var(--space-xs) var(--space-sm)",
              }}
            >
              Még nincs termék — kattintson a „+ Termék" gombra.
            </p>
          ) : (
            bank.products.map((product) => (
              <ProductList
                key={product.id}
                product={product}
                documents={docsForProduct(product.id)}
                selectedProductId={selectedProductId}
                selectedDocumentId={selectedDocumentId}
                onSelectProduct={onSelectProduct}
                onSelectDocument={onSelectDocument}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default memo(BankSelectorImpl);

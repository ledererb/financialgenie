import { useState, memo, useRef, useEffect } from "react";
import type { Bank, CatalogDocument } from "@/types";
import { useStore } from "@/store";
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
  onOpenDocument: (docId: string) => void;
  onAddProduct: (bankId: string) => void;
  onDeleteBank: (bankId: string, name: string) => void;
  onDeleteProduct: (productId: string, name: string) => void;
  onDeleteDocument: (docId: string, title: string) => void;
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
  onOpenDocument,
  onAddProduct,
  onDeleteBank,
  onDeleteProduct,
  onDeleteDocument,
}: BankSelectorProps) {
  const [expanded, setExpanded] = useState(true);
  const [confirmDeleteBank, setConfirmDeleteBank] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState(bank.name);
  const renameBank = useStore((s) => s.renameBank);
  const inputRef = useRef<HTMLInputElement>(null);
  const isBankSelected = bank.id === selectedBankId;

  useEffect(() => {
    if (editingName) inputRef.current?.focus();
  }, [editingName]);

  const handleRenameSave = async () => {
    const trimmed = nameValue.trim();
    if (trimmed && trimmed !== bank.name) {
      try {
        await renameBank(bank.id, trimmed);
      } catch {}
    }
    setEditingName(false);
  };

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

        {editingName ? (
          <input
            ref={inputRef}
            value={nameValue}
            onChange={(e) => setNameValue(e.target.value)}
            onBlur={handleRenameSave}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRenameSave();
              if (e.key === "Escape") { setEditingName(false); setNameValue(bank.name); }
            }}
            onClick={(e) => e.stopPropagation()}
            style={{
              flex: 1,
              fontSize: "0.88rem",
              fontWeight: 600,
              padding: "2px 6px",
              border: "1px solid var(--accent-blue)",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-primary)",
              color: "var(--text-primary)",
            }}
          />
        ) : (
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
        )}

        {/* Edit name button */}
        {!editingName && (
          <button
            onClick={(e) => { e.stopPropagation(); setEditingName(true); setNameValue(bank.name); }}
            title="Bank átnevezése"
            style={{
              background: "var(--bg-hover)", border: "1px solid var(--border-subtle)",
              color: "var(--text-secondary)", cursor: "pointer",
              padding: "2px 6px", fontSize: "0.7rem", lineHeight: 1,
              flexShrink: 0, borderRadius: "var(--radius-sm)",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--accent-blue-glow)"; e.currentTarget.style.color = "var(--accent-blue)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.color = "var(--text-secondary)"; }}
          >
            ✏︎
          </button>
        )}

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

        {/* Delete bank button */}
        <button
          onClick={(e) => {
            e.stopPropagation();
            setConfirmDeleteBank(true);
          }}
          title="Bank törlése"
          style={{
            background: "none",
            border: "none",
            color: "var(--text-tertiary)",
            cursor: "pointer",
            padding: "0 4px",
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

      {/* Delete bank confirmation */}
      {confirmDeleteBank && (
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
            Biztosan törli a bankot? Az összes termék és a hozzájuk tartozó
            dokumentumok és fájlok is törlődnek.
          </p>
          <div style={{ display: "flex", gap: "var(--space-xs)" }}>
            <button
              className="btn btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                onDeleteBank(bank.id, bank.name);
                setConfirmDeleteBank(false);
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
                setConfirmDeleteBank(false);
              }}
              style={{ fontSize: "0.7rem", padding: "3px 10px" }}
            >
              Mégse
            </button>
          </div>
        </div>
      )}

      {/* Expanded content — products */}
      {(expanded || hasSelectedChild) && !confirmDeleteBank && (
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
                bankId={bank.id}
                bankName={bank.name}
                selectedProductId={selectedProductId}
                selectedDocumentId={selectedDocumentId}
                onSelectProduct={onSelectProduct}
                onSelectDocument={onSelectDocument}
                onOpenDocument={onOpenDocument}
                onDeleteProduct={onDeleteProduct}
                onDeleteDocument={onDeleteDocument}
              />
            ))
          )}
        </div>
      )}
    </div>
  );
}

export default memo(BankSelectorImpl);

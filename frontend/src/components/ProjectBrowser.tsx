"use client";

import { useState, useEffect } from "react";
import { useStore } from "@/store";
import EmptyState from "./EmptyState";
import BankSelector from "./BankSelector";

interface ProjectBrowserProps {
  onAddBank: () => void;
  onAddProduct: (bankId: string) => void;
  onDeleteBank: (bankId: string, name: string) => void;
  onDeleteProduct: (productId: string, name: string) => void;
  onDeleteDocument: (docId: string, title: string) => void;
  onOpenDocument: (docId: string) => void;
}

export function ProjectBrowser({ onAddBank, onAddProduct, onDeleteBank, onDeleteProduct, onDeleteDocument, onOpenDocument }: ProjectBrowserProps) {
  const catalog = useStore((s) => s.catalog);
  const catalogLoading = useStore((s) => s.catalogLoading);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const quickStartOTP = useStore((s) => s.quickStartOTP);
  const selectedBankId = useStore((s) => s.selectedBankId);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const selectedDocumentId = useStore((s) => s.selectedDocumentId);
  const selectBank = useStore((s) => s.selectBank);
  const selectProduct = useStore((s) => s.selectProduct);
  const selectDocument = useStore((s) => s.selectDocument);
  const [quickStarting, setQuickStarting] = useState(false);

  useEffect(() => {
    if (!catalog) loadCatalog();
  }, [catalog, loadCatalog]);

  const handleQuickStart = async () => {
    setQuickStarting(true);
    try {
      await quickStartOTP();
    } finally {
      setQuickStarting(false);
    }
  };

  const hasBanks = catalog && catalog.banks.length > 0;

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "var(--bg-primary)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-sm) var(--space-md)",
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <h3
          style={{
            fontSize: "0.75rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--text-secondary)",
            margin: 0,
          }}
        >
          Projektek
        </h3>
        <div style={{ display: "flex", gap: "var(--space-xs)" }}>
          {hasBanks && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={handleQuickStart}
              disabled={quickStarting}
              title="OTP Bank + 4 termék egy kattintással"
              style={{ padding: "3px 8px", fontSize: "0.7rem" }}
            >
              {quickStarting ? "..." : "Quick Start"}
            </button>
          )}
          <button
            className="btn btn-ghost btn-sm"
            onClick={onAddBank}
            style={{ padding: "3px 8px", fontSize: "0.7rem" }}
          >
            + Bank
          </button>
        </div>
      </div>

      {/* Body */}
      <div
        style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--space-sm)",
        }}
      >
        {catalogLoading && !catalog ? (
          <p
            style={{
              textAlign: "center",
              color: "var(--text-tertiary)",
              fontSize: "0.8rem",
              padding: "var(--space-xl)",
            }}
          >
            Betöltés…
          </p>
        ) : !hasBanks ? (
          <EmptyState onAddBank={onAddBank} />
        ) : (
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "var(--space-xs)",
            }}
          >
            {catalog!.banks.map((bank) => (
              <BankSelector
                key={bank.id}
                bank={bank}
                documents={catalog!.documents.filter((d) =>
                  d.product_ids.some((pid) => bank.products.some((p) => p.id === pid))
                )}
                bankId={bank.id}
                bankName={bank.name}
                selectedBankId={selectedBankId}
                selectedProductId={selectedProductId}
                selectedDocumentId={selectedDocumentId}
                onSelectBank={selectBank}
                onSelectProduct={selectProduct}
                onSelectDocument={selectDocument}
                onOpenDocument={onOpenDocument}
                onAddProduct={onAddProduct}
                onDeleteBank={onDeleteBank}
                onDeleteProduct={onDeleteProduct}
                onDeleteDocument={onDeleteDocument}
                onGenerate={() => {}}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default ProjectBrowser;

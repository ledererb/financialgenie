import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/store";
import BankSelector from "./BankSelector";
import BankSetupDialog from "./BankSetupDialog";
import ProductSetupDialog from "./ProductSetupDialog";

interface LibraryViewProps {
  onOpenDocument: (docId: string) => void;
  onGenerate: (bankId: string, bankName: string, productId: string, productName: string) => void;
}

export default function LibraryView({ onOpenDocument, onGenerate }: LibraryViewProps) {
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
  const deleteBank = useStore((s) => s.deleteBank);
  const deleteCatalogDocument = useStore((s) => s.deleteCatalogDocument);
  const deleteProduct = useStore((s) => s.deleteProduct);

  const [showBankDialog, setShowBankDialog] = useState(false);
  const [productDialogBankId, setProductDialogBankId] = useState<string | null>(null);
  const [quickStarting, setQuickStarting] = useState(false);

  useEffect(() => {
    if (!catalog) loadCatalog();
  }, [catalog, loadCatalog]);

  const hasBanks = catalog && catalog.banks.length > 0;

  const handleQuickStart = async () => {
    setQuickStarting(true);
    try {
      await quickStartOTP();
    } finally {
      setQuickStarting(false);
    }
  };

  const handleAddProduct = useCallback((bankId: string) => {
    setProductDialogBankId(bankId);
  }, []);

  const handleDeleteBank = useCallback(async (bankId: string, _name: string) => {
    try {
      await deleteBank(bankId);
    } catch (e) {
      console.error("Bank delete error:", e);
    }
  }, [deleteBank]);

  const handleDeleteProduct = useCallback(async (productId: string, _name: string) => {
    try {
      await deleteProduct(productId);
    } catch (e) {
      console.error("Product delete error:", e);
    }
  }, [deleteProduct]);

  const handleDeleteDocument = useCallback(async (docId: string, _title: string) => {
    try {
      await deleteCatalogDocument(docId);
    } catch (e) {
      console.error("Document delete error:", e);
    }
  }, [deleteCatalogDocument]);

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
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
          Dokumentumtár
        </h2>
        <span style={{ fontSize: "0.8rem", color: "var(--text-tertiary)" }}>
          {catalog ? `${catalog.banks.length} bank · ${catalog.documents.length} dokumentum` : ""}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", gap: "8px" }}>
          {hasBanks && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={handleQuickStart}
              disabled={quickStarting}
            >
              {quickStarting ? "Betöltés..." : "Quick Start (OTP)"}
            </button>
          )}
          <button
            className="btn btn-primary btn-sm"
            onClick={() => setShowBankDialog(true)}
          >
            + Bank
          </button>
        </div>
      </div>

      {/* Tree */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-md) 24px" }}>
        {catalogLoading && !catalog ? (
          <p style={{ textAlign: "center", color: "var(--text-tertiary)", paddingTop: "var(--space-2xl)" }}>
            Betöltés...
          </p>
        ) : !hasBanks ? (
          <div style={{ textAlign: "center", paddingTop: "var(--space-2xl)" }}>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", marginBottom: "var(--space-md)" }}>
              Meg nincs bank letrehozva.
            </p>
            <button className="btn btn-primary" onClick={() => setShowBankDialog(true)}>
              + Bank letrehozasa
            </button>
          </div>
        ) : (
          catalog.banks.map((bank) => (
            <BankSelector
              key={bank.id}
              bank={bank}
              documents={catalog.documents.filter((d) =>
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
              onAddProduct={handleAddProduct}
              onDeleteBank={handleDeleteBank}
              onDeleteProduct={handleDeleteProduct}
              onDeleteDocument={handleDeleteDocument}
              onGenerate={onGenerate}
            />
          ))
        )}
      </div>

      {/* Dialogs */}
      {showBankDialog && (
        <BankSetupDialog
          onClose={() => setShowBankDialog(false)}
          onCreated={(bankId) => {
            setShowBankDialog(false);
            setProductDialogBankId(bankId);
          }}
        />
      )}
      {productDialogBankId && (
        <ProductSetupDialog
          bankId={productDialogBankId}
          onClose={() => setProductDialogBankId(null)}
        />
      )}
    </div>
  );
}

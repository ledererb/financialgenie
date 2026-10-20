import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/store";
import BankSelector from "./BankSelector";
import BankSetupDialog from "./BankSetupDialog";
import ProductSetupDialog from "./ProductSetupDialog";
import DocumentList from "./DocumentList";

interface LibraryViewProps {
  onOpenDocument: (docId: string) => void;
  onGenerate: (bankId: string, bankName: string, productId: string, productName: string) => void;
  readOnly?: boolean;
}

export default function LibraryView({ onOpenDocument, onGenerate, readOnly = false }: LibraryViewProps) {
  const catalog = useStore((s) => s.catalog);
  const catalogLoading = useStore((s) => s.catalogLoading);
  const loadCatalog = useStore((s) => s.loadCatalog);
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
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!catalog) loadCatalog();
  }, [catalog, loadCatalog]);

  const hasBanks = catalog && catalog.banks.length > 0;
  const orphanDocs = catalog ? catalog.documents.filter((d) => d.product_ids.length === 0) : [];

  const handleAddProduct = useCallback((bankId: string) => {
    setProductDialogBankId(bankId);
  }, []);

  const handleDeleteBank = useCallback(async (bankId: string, _name: string) => {
    setActionError(null);
    try {
      await deleteBank(bankId);
    } catch (e) {
      setActionError((e as Error).message || "A bank törlése sikertelen.");
    }
  }, [deleteBank]);

  const handleDeleteProduct = useCallback(async (productId: string, _name: string) => {
    setActionError(null);
    try {
      await deleteProduct(productId);
    } catch (e) {
      setActionError((e as Error).message || "A termék törlése sikertelen.");
    }
  }, [deleteProduct]);

  const handleDeleteDocument = useCallback(async (docId: string, _title: string) => {
    setActionError(null);
    try {
      await deleteCatalogDocument(docId);
    } catch (e) {
      setActionError((e as Error).message || "A dokumentum törlése sikertelen.");
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
        <div style={{ marginLeft: "auto", display: "flex", gap: "8px", alignItems: "center" }}>
          {readOnly ? (
            <span className="badge" style={{ fontSize: "0.7rem", color: "var(--text-tertiary)", border: "1px solid var(--border-subtle)" }}>
              Csak olvasási mód
            </span>
          ) : (
            <button
              className="btn btn-primary btn-sm"
              onClick={() => setShowBankDialog(true)}
            >
              + Bank
            </button>
          )}
        </div>
      </div>

      {actionError && (
        <div
          style={{
            margin: "12px 24px 0",
            padding: "8px 12px",
            background: "var(--accent-red-glow)",
            border: "1px solid var(--accent-red)",
            borderRadius: "var(--radius-sm)",
            color: "var(--accent-red)",
            fontSize: "0.8rem",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <span>{actionError}</span>
          <button
            onClick={() => setActionError(null)}
            style={{ background: "none", border: "none", color: "var(--accent-red)", cursor: "pointer" }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Tree */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-md) 24px" }}>
        {catalogLoading && !catalog ? (
          <p style={{ textAlign: "center", color: "var(--text-tertiary)", paddingTop: "var(--space-2xl)" }}>
            Betöltés...
          </p>
        ) : !hasBanks ? (
          <div style={{ textAlign: "center", paddingTop: "var(--space-2xl)" }}>
            <p style={{ color: "var(--text-secondary)", fontSize: "0.9rem", marginBottom: "var(--space-md)" }}>
              Még nincs bank létrehozva.
            </p>
            <button className="btn btn-primary" onClick={() => setShowBankDialog(true)}>
              + Bank létrehozása
            </button>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
            {catalog.banks.map((bank) => (
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
                readOnly={readOnly}
              />
            ))}

            {!readOnly && orphanDocs.length > 0 && (
              <div
                style={{
                  borderRadius: "var(--radius-md)",
                  border: "1px dashed var(--accent-amber)",
                  padding: "var(--space-sm) var(--space-md)",
                  background: "var(--bg-secondary)",
                }}
              >
                <div style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--accent-amber)", marginBottom: "6px" }}>
                  Termékhez nem rendelt dokumentumok ({orphanDocs.length})
                </div>
                <DocumentList
                  documents={orphanDocs}
                  selectedDocumentId={selectedDocumentId}
                  onSelectDocument={selectDocument}
                  onOpenDocument={onOpenDocument}
                  onDeleteDocument={handleDeleteDocument}
                />
              </div>
            )}
          </div>
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

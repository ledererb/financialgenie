import { useState, useCallback, useEffect } from "react";
import { useStore } from "@/store";
import UploadStep from "./UploadStep";
import AnalysisStep from "./AnalysisStep";
import ReviewDashboard from "./ReviewDashboard";
import PageEditor from "./PageEditor";
import PointsEditor from "./PointsEditor";
import FillPreviewStep from "./FillPreviewStep";
import ProjectBrowser from "./ProjectBrowser";
import SectionEditor from "./SectionEditor";
import BankSetupDialog from "./BankSetupDialog";
import ProductSetupDialog from "./ProductSetupDialog";

type WizardStep = "upload" | "analysis" | "review" | "fill";

const STEPS: { key: WizardStep; label: string }[] = [
  { key: "upload", label: "Feltöltés" },
  { key: "analysis", label: "AI elemzés" },
  { key: "review", label: "Áttekintés" },
  { key: "fill", label: "Kitöltés" },
];

export default function MappingStudio() {
  const [step, setStep] = useState<WizardStep>("upload");
  const [activePdfId, setActivePdfId] = useState<string | null>(null);
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [editingPoints, setEditingPoints] = useState(false);
  const [sectionEditorFile, setSectionEditorFile] = useState<File | null>(null);
  const [mappedCount, setMappedCount] = useState(0);
  const [totalFields, setTotalFields] = useState(0);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);


  // Catalog / gate state
  const catalog = useStore((s) => s.catalog);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const selectedBankId = useStore((s) => s.selectedBankId);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const deleteBank = useStore((s) => s.deleteBank);
  const deleteProduct = useStore((s) => s.deleteProduct);
  const deleteCatalogDocument = useStore((s) => s.deleteCatalogDocument);

  const [showBankDialog, setShowBankDialog] = useState(false);
  const [productDialogBankId, setProductDialogBankId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    if (!catalog) loadCatalog();
  }, [catalog, loadCatalog]);

  const gatePassed =
    selectedBankId !== null && selectedProductId !== null;

  // Whether the catalog has at least one bank (loaded + non-empty).
  const hasBanks = !!catalog && catalog.banks.length > 0;

  // Look up the selected bank name for gate messaging
  const selectedBank = selectedBankId
    ? catalog?.banks.find((b) => b.id === selectedBankId)
    : null;

  const handleUploadComplete = useCallback((pdfId: string) => {
    setActivePdfId(pdfId);
    setStep("analysis");
  }, []);

  const handleAnalysisComplete = useCallback(() => {
    setStep("review");
  }, []);

  const handlePageClick = useCallback((page: number) => {
    setEditingPage(page);
  }, []);

  const handleBackFromEditor = useCallback(() => {
    setEditingPage(null);
  }, []);

  const handleOpenPoints = useCallback(() => {
    setEditingPoints(true);
  }, []);

  const handleBackFromPoints = useCallback(() => {
    setEditingPoints(false);
  }, []);

  const handleApprove = useCallback(() => {
    setStep("fill");
  }, []);

  const handleRemap = useCallback(() => {
    if (!activePdfId) return;
    // Delete the mapping, then go back to analysis step which will re-run recognition
    fetch(`/api/mapping?pdf_id=${encodeURIComponent(activePdfId)}`, { method: "DELETE" })
      .then(() => {
        setStep("analysis");
      })
      .catch(() => {});
  }, [activePdfId]);

  const handleOpenExisting = useCallback((pdfId: string) => {
    setActivePdfId(pdfId);
    setStep("review");
  }, []);

  // Open a catalog document from the sidebar tree. Resolves the PDF file_path
  // from the catalog and opens it in the review step (skipping analysis if a
  // mapping already exists — the AnalysisStep handles that auto-skip).
  const handleOpenDocument = useCallback(
    (docId: string) => {
      if (!catalog) return;
      const doc = catalog.documents.find((d) => d.id === docId);
      if (doc?.file_path) {
        setActivePdfId(doc.file_path);
        setStep("review");
      }
    },
    [catalog],
  );

  const handleFillDone = useCallback(() => {
    setActivePdfId(null);
    setEditingPage(null);
    setStep("upload");
  }, []);

  const handleAddBank = useCallback(() => setShowBankDialog(true), []);

  const handleAddProduct = useCallback((bankId: string) => {
    setProductDialogBankId(bankId);
  }, []);

  const handleDeleteBank = useCallback(async (bankId: string, _name: string) => {
    try {
      await deleteBank(bankId);
    } catch (e) {
      console.error("deleteBank failed:", e);
    }
  }, [deleteBank]);

  const handleDeleteProduct = useCallback(async (productId: string, name: string) => {
    try {
      const orphaned = await deleteProduct(productId);
      if (orphaned.length > 0) {
        // Could surface a toast here; for now just log.
        console.info(`${orphaned.length} document(s) orphaned after deleting product "${name}"`);
      }
    } catch (e) {
      console.error("deleteProduct failed:", e);
    }
  }, [deleteProduct]);

  const handleDeleteDocument = useCallback(async (docId: string, _title: string) => {
    try {
      await deleteCatalogDocument(docId);
    } catch (e) {
      console.error("deleteDocument failed:", e);
    }
  }, [deleteCatalogDocument]);

  // After a bank is created, advance to the product dialog to complete
  // the define-first flow.
  const handleBankCreated = useCallback(
    (bankId: string) => {
      setShowBankDialog(false);
      setProductDialogBankId(bankId);
    },
    [],
  );

  const handleProductCreated = useCallback(() => {
    setProductDialogBankId(null);
  }, []);

  // If editing a specific page, show the page editor full-screen
  if (editingPage !== null && activePdfId) {
    return (
      <PageEditor
        pdfId={activePdfId}
        pageNumber={editingPage}
        onBack={handleBackFromEditor}
        onPageChange={setEditingPage}
      />
    );
  }

  // Points/Blocks editor full-screen
  if (editingPoints && activePdfId) {
    return (
      <PointsEditor
        pdfId={activePdfId}
        onBack={handleBackFromPoints}
      />
    );
  }

  // Manual section editor full-screen
  if (sectionEditorFile && selectedBankId) {
    return (
      <SectionEditor
        bankId={selectedBankId}
        initialFile={sectionEditorFile}
        onClose={() => setSectionEditorFile(null)}
      />
    );
  }

  const stepIndex = STEPS.findIndex((s) => s.key === step);

  return (
    <div style={{
      height: "100%",
      display: "flex",
      flexDirection: "column",
      background: "var(--bg-primary)",
    }}>
      {/* Header */}
      <header style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "16px 28px",
        borderBottom: "1px solid var(--border-subtle)",
        background: "var(--bg-secondary)",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <h1 style={{
            fontSize: "1.15rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            letterSpacing: "-0.01em",
          }}>
            Mapping Stúdió
          </h1>
        </div>

        {/* Step indicator */}
        <div className="step-indicator">
          {STEPS.map((s, i) => (
            <div key={s.key} style={{ display: "flex", alignItems: "center", gap: "4px" }}>
              <div
                className={`step-dot ${i < stepIndex ? "completed" : i === stepIndex ? "active" : ""}`}
                title={s.label}
              />
              {i < STEPS.length - 1 && (
                <div className={`step-connector ${i < stepIndex ? "completed" : ""}`} />
              )}
            </div>
          ))}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
          {STEPS.map((s, i) => (
            <span
              key={s.key}
              style={{
                fontSize: "0.75rem",
                color: i === stepIndex ? "var(--accent-blue)" : "var(--text-tertiary)",
                fontWeight: i === stepIndex ? 600 : 400,
                transition: "color var(--transition-default)",
              }}
            >
              {s.label}
            </span>
          ))}
        </div>
      </header>

      {/* Body: sidebar + content */}
      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Sidebar — ProjectBrowser tree */}
        <aside
          style={{
            width: sidebarCollapsed ? 0 : 420,
            flexShrink: 0,
            borderRight: sidebarCollapsed ? "none" : "1px solid var(--border-subtle)",
            background: "var(--bg-secondary)",
            overflow: "hidden",
            display: "flex",
            flexDirection: "column",
            transition: "width 0.2s ease",
          }}
        >
          {!sidebarCollapsed && (
            <div style={{ flex: 1, overflow: "hidden" }}>
              <ProjectBrowser
                onAddBank={handleAddBank}
                onAddProduct={handleAddProduct}
                onDeleteBank={handleDeleteBank}
                onDeleteProduct={handleDeleteProduct}
                onDeleteDocument={handleDeleteDocument}
                onOpenDocument={handleOpenDocument}
              />
            </div>
          )}
        </aside>

        {/* Sidebar collapse/expand toggle */}
        <button
          onClick={() => setSidebarCollapsed((prev) => !prev)}
          title={sidebarCollapsed ? "Oldalsáv megnyitása" : "Oldalsáv elrejtése"}
          style={{
            width: 20,
            flexShrink: 0,
            border: "none",
            borderRight: "1px solid var(--border-subtle)",
            background: "var(--bg-secondary)",
            color: "var(--text-tertiary)",
            cursor: "pointer",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "0.7rem",
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--text-primary)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-tertiary)")}
        >
          {sidebarCollapsed ? ">" : "<"}
        </button>

        {/* Main content area */}
        <div
          style={{ flex: 1, overflow: "auto" }}
          className="animate-fade-in"
          key={step}
        >
          {/* Upload step with define-first gate */}
          {step === "upload" && !gatePassed && (
            <div
              className="animate-fade-in"
              style={{
                maxWidth: 680,
                margin: "0 auto",
                padding: "var(--space-2xl) var(--space-xl)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  justifyContent: "center",
                  textAlign: "center",
                  gap: "var(--space-lg)",
                  padding: "var(--space-2xl) var(--space-xl)",
                  border: "2px dashed var(--border-default)",
                  borderRadius: "var(--radius-xl)",
                  background: "var(--bg-secondary)",
                }}
              >
                <svg
                  width="48"
                  height="48"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="var(--accent-amber)"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                </svg>

                {!hasBanks ? (
                  <>
                    <div>
                      <p
                        style={{
                          fontSize: "1rem",
                          fontWeight: 500,
                          color: "var(--text-primary)",
                          marginBottom: "var(--space-xs)",
                        }}
                      >
                        Először hozzon létre egy bankot
                      </p>
                      <p
                        style={{
                          fontSize: "0.85rem",
                          color: "var(--text-secondary)",
                        }}
                      >
                        A dokumentumok feltöltéséhez előbb definiálni kell egy
                        bankot és egy terméket.
                      </p>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: "var(--space-sm)",
                        flexWrap: "wrap",
                        justifyContent: "center",
                      }}
                    >
                      <button
                        className="btn btn-primary"
                        onClick={() => setShowBankDialog(true)}
                      >
                        + Bank létrehozása
                      </button>
                    </div>
                  </>
                ) : !selectedBankId ? (
                  <>
                    <div>
                      <p
                        style={{
                          fontSize: "1rem",
                          fontWeight: 500,
                          color: "var(--text-primary)",
                          marginBottom: "var(--space-xs)",
                        }}
                      >
                        Válasszon ki egy bankot
                      </p>
                      <p
                        style={{
                          fontSize: "0.85rem",
                          color: "var(--text-secondary)",
                        }}
                      >
                        A bal oldali fán kattintson egy bankra, majd válasszon
                        egy terméket a dokumentumok feltöltéséhez.
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <div>
                      <p
                        style={{
                          fontSize: "1rem",
                          fontWeight: 500,
                          color: "var(--text-primary)",
                          marginBottom: "var(--space-xs)",
                        }}
                      >
                        Hozzon létre egy terméket
                      </p>
                      <p
                        style={{
                          fontSize: "0.85rem",
                          color: "var(--text-secondary)",
                        }}
                      >
                        A(z){" "}
                        <strong style={{ color: "var(--text-primary)" }}>
                          {selectedBank?.name || selectedBankId}
                        </strong>{" "}
                        bank alatt még nincs termék kiválasztva.
                      </p>
                    </div>
                    <div
                      style={{
                        display: "flex",
                        gap: "var(--space-sm)",
                        flexWrap: "wrap",
                        justifyContent: "center",
                      }}
                    >
                      <button
                        className="btn btn-primary"
                        onClick={() =>
                          setProductDialogBankId(selectedBankId)
                        }
                      >
                        + Termék létrehozása
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {step === "upload" && gatePassed && (
            <UploadStep
              onComplete={handleUploadComplete}
              onOpenExisting={handleOpenExisting}
              onOpenSectionEditor={setSectionEditorFile}
            />
          )}
          {step === "analysis" && activePdfId && (
            <AnalysisStep
              pdfId={activePdfId}
              onComplete={handleAnalysisComplete}
            />
          )}
          {step === "review" && activePdfId && (
            <ReviewDashboard
              pdfId={activePdfId}
              onPageClick={handlePageClick}
              onOpenPoints={handleOpenPoints}
              onBack={() => setStep("upload")}
              onApprove={handleApprove}
              onRemap={handleRemap}
              onStatsReady={(mapped, total) => {
                setMappedCount(mapped);
                setTotalFields(total);
              }}
            />
          )}
          {step === "fill" && activePdfId && (
            <FillPreviewStep
              pdfId={activePdfId}
              onBack={() => setStep("review")}
              onDone={handleFillDone}
            />
          )}
        </div>
      </div>

      {/* Dialogs */}
      {showBankDialog && (
        <BankSetupDialog
          onClose={() => setShowBankDialog(false)}
          onCreated={handleBankCreated}
        />
      )}
      {productDialogBankId && (
        <ProductSetupDialog
          bankId={productDialogBankId}
          onClose={() => setProductDialogBankId(null)}
          onCreated={handleProductCreated}
        />
      )}
    </div>
  );
}

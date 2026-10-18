import { useState, useCallback } from "react";
import { useStore } from "@/store";
import { getMapping, saveMapping } from "@/api/client";
import TopNav from "@/components/TopNav";
import LibraryView from "@/components/LibraryView";
import GenerateView from "@/components/GenerateView";
import ReviewDashboard from "@/components/ReviewDashboard";
import PageEditor from "@/components/PageEditor";
import PointsEditor from "@/components/PointsEditor";
import AnalysisStep from "@/components/AnalysisStep";

type View = "library" | "generate";

export default function App() {
  const [view, setView] = useState<View>("library");
  const [activePdfId, setActivePdfId] = useState<string | null>(null);
  const [analyzingPdfId, setAnalyzingPdfId] = useState<string | null>(null);
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [editingPoints, setEditingPoints] = useState(false);

  // Pre-selection for GenerateView (when coming from ProductList generate button)
  const [genBankId, setGenBankId] = useState<string | null>(null);
  const [genBankName, setGenBankName] = useState<string | null>(null);
  const [genProductId, setGenProductId] = useState<string | null>(null);
  const [genProductName, setGenProductName] = useState<string | null>(null);

  const catalog = useStore((s) => s.catalog);

  // Open a document from the library tree or upload completion → activate MappingEditor overlay
  const handleOpenDocument = useCallback((docIdOrPath: string) => {
    if (catalog) {
      const doc = catalog.documents.find((d) => d.id === docIdOrPath || d.file_path === docIdOrPath);
      if (doc?.file_path) {
        setActivePdfId(doc.file_path);
        return;
      }
    }
    if (docIdOrPath.toLowerCase().endsWith(".pdf")) {
      setActivePdfId(docIdOrPath);
      setAnalyzingPdfId(docIdOrPath);
    }
  }, [catalog]);

  // Switch to GenerateView with pre-selected bank/product
  const handleGenerate = useCallback(
    (bankId: string, bankName: string, productId: string, productName: string) => {
      setGenBankId(bankId);
      setGenBankName(bankName);
      setGenProductId(productId);
      setGenProductName(productName);
      setView("generate");
    },
    [],
  );

  const handlePageClick = useCallback((page: number) => {
    setEditingPage(page);
  }, []);

  const handleRemap = useCallback(() => {
    if (!activePdfId) return;
    fetch(`/api/mapping?pdf_id=${encodeURIComponent(activePdfId)}`, { method: "DELETE" })
      .then(() => {
        setAnalyzingPdfId(activePdfId);
      })
      .catch(() => {
        setAnalyzingPdfId(activePdfId);
      });
  }, [activePdfId]);

  const handleApprove = useCallback(async () => {
    if (!activePdfId) return;
    try {
      const m = await getMapping(activePdfId);
      await saveMapping(activePdfId, { ...m, approved: true }, m._mtime);
    } catch {
      /* ignore */
    }
    setActivePdfId(null);
    setEditingPage(null);
    setEditingPoints(false);
  }, [activePdfId]);

  // MappingEditor overlay (full-screen)
  const showAnalysis = analyzingPdfId !== null;
  const showPageEditor = !showAnalysis && editingPage !== null && activePdfId;
  const showPointsEditor = !showAnalysis && editingPoints && activePdfId;
  const showReview = !showAnalysis && !showPageEditor && !showPointsEditor && activePdfId;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TopNav view={view} onViewChange={(v) => {
        setView(v);
        setActivePdfId(null);
        setAnalyzingPdfId(null);
        setEditingPage(null);
        setEditingPoints(false);
      }} />

      <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
        {/* Main views */}
        {view === "library" && (
          <LibraryView
            onOpenDocument={handleOpenDocument}
            onGenerate={handleGenerate}
          />
        )}
        {view === "generate" && (
          <GenerateView
            preselectBankId={genBankId}
            preselectBankName={genBankName}
            preselectProductId={genProductId}
            preselectProductName={genProductName}
          />
        )}

        {/* AI Analysis overlay */}
        {showAnalysis && analyzingPdfId && (
          <div
            className="animate-fade-in"
            style={{
              position: "absolute",
              inset: 0,
              background: "var(--bg-primary)",
              zIndex: 250,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "12px 24px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-secondary)",
                flexShrink: 0,
              }}
            >
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setAnalyzingPdfId(null)}
              >
                ← Vissza az áttekintéshez
              </button>
              <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                AI Mezőfelismerés — {analyzingPdfId}
              </span>
            </div>
            <div style={{ flex: 1, overflow: "auto", padding: "var(--space-2xl) 24px" }}>
              <AnalysisStep
                pdfId={analyzingPdfId}
                onComplete={() => setAnalyzingPdfId(null)}
              />
            </div>
          </div>
        )}

        {/* MappingEditor ReviewDashboard overlay */}
        {showReview && activePdfId && (
          <div
            className="animate-fade-in"
            style={{
              position: "absolute",
              inset: 0,
              background: "var(--bg-primary)",
              zIndex: 100,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <ReviewDashboard
              pdfId={activePdfId}
              onPageClick={handlePageClick}
              onOpenPoints={() => setEditingPoints(true)}
              onBack={() => { setActivePdfId(null); setEditingPage(null); setEditingPoints(false); }}
              onApprove={handleApprove}
              onRemap={handleRemap}
            />
          </div>
        )}

        {showPageEditor && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "var(--bg-primary)",
              zIndex: 200,
            }}
          >
            {activePdfId && editingPage !== null && (
              <PageEditor
                pdfId={activePdfId}
                pageNumber={editingPage}
                onBack={() => setEditingPage(null)}
                onPageChange={(p) => setEditingPage(p)}
              />
            )}
          </div>
        )}

        {showPointsEditor && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              background: "var(--bg-primary)",
              zIndex: 200,
            }}
          >
            {activePdfId && (
              <PointsEditor
                pdfId={activePdfId}
                onBack={() => setEditingPoints(false)}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

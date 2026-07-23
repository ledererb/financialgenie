import { useState, useCallback } from "react";
import { useStore } from "@/store";
import TopNav from "@/components/TopNav";
import LibraryView from "@/components/LibraryView";
import GenerateView from "@/components/GenerateView";
import ReviewDashboard from "@/components/ReviewDashboard";
import PageEditor from "@/components/PageEditor";
import PointsEditor from "@/components/PointsEditor";

type View = "library" | "generate";

export default function App() {
  const [view, setView] = useState<View>("library");
  const [activePdfId, setActivePdfId] = useState<string | null>(null);
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [editingPoints, setEditingPoints] = useState(false);

  // Pre-selection for GenerateView (when coming from ProductList generate button)
  const [genBankId, setGenBankId] = useState<string | null>(null);
  const [genBankName, setGenBankName] = useState<string | null>(null);
  const [genProductId, setGenProductId] = useState<string | null>(null);
  const [genProductName, setGenProductName] = useState<string | null>(null);

  const catalog = useStore((s) => s.catalog);

  // Open a document from the library tree → activate MappingEditor overlay
  const handleOpenDocument = useCallback((docId: string) => {
    if (!catalog) return;
    const doc = catalog.documents.find((d) => d.id === docId);
    if (doc?.file_path) {
      setActivePdfId(doc.file_path);
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
      .then(() => { setActivePdfId(null); })
      .catch(() => {});
  }, [activePdfId]);

  // MappingEditor overlay (full-screen)
  const showPageEditor = editingPage !== null && activePdfId;
  const showPointsEditor = editingPoints && activePdfId;
  const showReview = !showPageEditor && !showPointsEditor && activePdfId;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TopNav view={view} onViewChange={setView} />

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

        {/* MappingEditor overlay */}
        {showReview && (
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
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "8px 16px",
                borderBottom: "1px solid var(--border-subtle)",
                background: "var(--bg-secondary)",
                flexShrink: 0,
              }}
            >
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => { setActivePdfId(null); setEditingPage(null); setEditingPoints(false); }}
              >
                Vissza
              </button>
              <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                Mapping attekintes
              </span>
            </div>
            <div style={{ flex: 1, overflow: "auto" }}>
              {activePdfId && (
                <ReviewDashboard
                  pdfId={activePdfId}
                  onPageClick={handlePageClick}
                  onOpenPoints={() => setEditingPoints(true)}
                  onBack={() => setActivePdfId(null)}
                  onApprove={() => {}}
                  onRemap={handleRemap}
                />
              )}
            </div>
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

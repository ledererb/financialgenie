import { useState, useCallback, useEffect } from "react";
import { useStore } from "@/store";
import type { User } from "@/types";
import { getMapping, saveMapping, getAuthToken, setAuthToken, getMe } from "@/api/client";
import TopNav, { type NavView } from "@/components/TopNav";
import LibraryView from "@/components/LibraryView";
import GenerateView from "@/components/GenerateView";
import UsersView from "@/components/UsersView";
import LoginView from "@/components/LoginView";
import ReviewDashboard from "@/components/ReviewDashboard";
import PageEditor from "@/components/PageEditor";
import PointsEditor from "@/components/PointsEditor";
import AnalysisStep from "@/components/AnalysisStep";

export default function App() {
  const [currentUser, setCurrentUser] = useState<User | null>(null);
  const [authChecking, setAuthChecking] = useState<boolean>(true);
  const [view, setView] = useState<NavView>("library");
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
  const loadCatalog = useStore((s) => s.loadCatalog);

  // Check stored session on startup
  useEffect(() => {
    const token = getAuthToken();
    if (!token) {
      setAuthChecking(false);
      return;
    }
    getMe()
      .then((res) => {
        setCurrentUser(res.user);
      })
      .catch(() => {
        setAuthToken(null);
        setCurrentUser(null);
      })
      .finally(() => {
        setAuthChecking(false);
      });
  }, []);

  // Listen for 401 unauthorized events from fetch interceptor
  useEffect(() => {
    const onUnauthorized = () => {
      setAuthToken(null);
      setCurrentUser(null);
      setActivePdfId(null);
      setAnalyzingPdfId(null);
      setEditingPage(null);
      setEditingPoints(false);
    };
    window.addEventListener("fg:unauthorized", onUnauthorized);
    return () => window.removeEventListener("fg:unauthorized", onUnauthorized);
  }, []);

  const handleLoginSuccess = useCallback(
    (user: User) => {
      setCurrentUser(user);
      setView("library");
      loadCatalog();
    },
    [loadCatalog],
  );

  const handleLogout = useCallback(() => {
    setAuthToken(null);
    setCurrentUser(null);
    setActivePdfId(null);
    setAnalyzingPdfId(null);
    setEditingPage(null);
    setEditingPoints(false);
    setView("library");
  }, []);

  const isAdmin = currentUser?.role === "admin";

  // Open a document from the library tree or upload completion → activate MappingEditor overlay (Admin only)
  const handleOpenDocument = useCallback(
    (docIdOrPath: string) => {
      if (!isAdmin) return;
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
    },
    [catalog, isAdmin],
  );

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

  if (authChecking) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100vh",
          background: "var(--bg-primary)",
          color: "var(--text-tertiary)",
          fontSize: "0.9rem",
        }}
      >
        Betöltés...
      </div>
    );
  }

  if (!currentUser) {
    return <LoginView onLoginSuccess={handleLoginSuccess} />;
  }

  // MappingEditor overlay (full-screen, Admin only)
  const showAnalysis = isAdmin && analyzingPdfId !== null;
  const showPageEditor = isAdmin && !showAnalysis && editingPage !== null && activePdfId;
  const showPointsEditor = isAdmin && !showAnalysis && editingPoints && activePdfId;
  const showReview = isAdmin && !showAnalysis && !showPageEditor && !showPointsEditor && activePdfId;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100vh", overflow: "hidden" }}>
      <TopNav
        view={view}
        user={currentUser}
        onLogout={handleLogout}
        onViewChange={(v) => {
          setView(v);
          setActivePdfId(null);
          setAnalyzingPdfId(null);
          setEditingPage(null);
          setEditingPoints(false);
        }}
      />

      <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
        {/* Main views */}
        {view === "library" && (
          <LibraryView
            onOpenDocument={handleOpenDocument}
            onGenerate={handleGenerate}
            readOnly={!isAdmin}
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
        {view === "users" && isAdmin && <UsersView currentUser={currentUser} />}

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

import { useState, useCallback } from "react";
import UploadStep from "./UploadStep";
import AnalysisStep from "./AnalysisStep";
import ReviewDashboard from "./ReviewDashboard";
import PageEditor from "./PageEditor";
import LockStep from "./LockStep";
import FillPreviewStep from "./FillPreviewStep";

type WizardStep = "upload" | "analysis" | "review" | "lock" | "fill";

const STEPS: { key: WizardStep; label: string }[] = [
  { key: "upload", label: "Feltöltés" },
  { key: "analysis", label: "AI elemzés" },
  { key: "review", label: "Áttekintés" },
  { key: "lock", label: "Jóváhagyás" },
  { key: "fill", label: "Kitöltés" },
];

export default function MappingStudio() {
  const [step, setStep] = useState<WizardStep>("upload");
  const [activePdfId, setActivePdfId] = useState<string | null>(null);
  const [editingPage, setEditingPage] = useState<number | null>(null);
  const [mappedCount, setMappedCount] = useState(0);
  const [totalFields, setTotalFields] = useState(0);

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

  const handleApprove = useCallback(() => {
    setStep("lock");
  }, []);

  const handleBackToReview = useCallback(() => {
    setStep("review");
  }, []);

  const handleLockApprove = useCallback(() => {
    // After locking → go to fill preview
    setStep("fill");
  }, []);

  const handleOpenExisting = useCallback((pdfId: string) => {
    setActivePdfId(pdfId);
    setStep("review");
  }, []);

  const handleFillDone = useCallback(() => {
    setActivePdfId(null);
    setEditingPage(null);
    setStep("upload");
  }, []);

  // If editing a specific page, show the page editor full-screen
  if (editingPage !== null && activePdfId) {
    return (
      <PageEditor
        pdfId={activePdfId}
        pageNumber={editingPage}
        onBack={handleBackFromEditor}
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

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto" }} className="animate-fade-in" key={step}>
        {step === "upload" && (
          <UploadStep
            onComplete={handleUploadComplete}
            onOpenExisting={handleOpenExisting}
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
            onBack={() => setStep("upload")}
            onApprove={handleApprove}
            onStatsReady={(mapped, total) => {
              setMappedCount(mapped);
              setTotalFields(total);
            }}
          />
        )}
        {step === "lock" && activePdfId && (
          <LockStep
            pdfId={activePdfId}
            mappedCount={mappedCount}
            totalFields={totalFields}
            onApprove={handleLockApprove}
            onBack={handleBackToReview}
          />
        )}
        {step === "fill" && activePdfId && (
          <FillPreviewStep
            pdfId={activePdfId}
            onBack={() => setStep("lock")}
            onDone={handleFillDone}
          />
        )}
      </div>
    </div>
  );
}

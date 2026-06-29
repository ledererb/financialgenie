import { useState, useEffect, useCallback } from "react";

interface Deal {
  Id: string;
  Name: string;
  StageName?: string;
  CloseDate?: string;
  AccountName?: string;
}

interface FillResult {
  filled_pdf_url: string;
  deal_id: string;
  filled_fields: string[];
  skipped_fields: string[];
  pages?: string[]; // base64 PNGs
  total_pages?: number;
  raw_path?: string;
}

interface FillPreviewStepProps {
  pdfId: string;
  onBack: () => void;
  onDone: () => void;
}

export default function FillPreviewStep({ pdfId, onBack, onDone }: FillPreviewStepProps) {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [selectedDealId, setSelectedDealId] = useState<string>("");
  const [dealsLoading, setDealsLoading] = useState(true);
  const [dealsError, setDealsError] = useState<string | null>(null);

  const [filling, setFilling] = useState(false);
  const [fillResult, setFillResult] = useState<FillResult | null>(null);
  const [fillError, setFillError] = useState<string | null>(null);

  const [previewLoading, setPreviewLoading] = useState(false);
  const [currentPage, setCurrentPage] = useState(0);

  // Load deals from SF
  useEffect(() => {
    setDealsLoading(true);
    fetch("/api/sf/deals")
      .then((r) => r.json())
      .then((d) => {
        const list: Deal[] = d.deals || [];
        setDeals(list);
        if (list.length > 0) setSelectedDealId(list[0].Id);
      })
      .catch((e) => setDealsError(String(e)))
      .finally(() => setDealsLoading(false));
  }, []);

  const handleFill = useCallback(async () => {
    if (!selectedDealId) return;
    setFilling(true);
    setFillResult(null);
    setFillError(null);
    setCurrentPage(0);

    try {
      const res = await fetch("/api/pdf/fill", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pdf_id: pdfId, deal_id: selectedDealId }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Fill failed");
      }
      const data = await res.json();

      // Extract raw path from the download URL for page preview
      const urlParams = new URLSearchParams(data.filled_pdf_url.split("?")[1]);
      const rawPath = urlParams.get("path") || "";

      setFillResult({ ...data, raw_path: rawPath });

      // Load page previews
      if (rawPath) {
        setPreviewLoading(true);
        try {
          const pagesRes = await fetch(
            `/api/pdf/fill/pages?path=${encodeURIComponent(rawPath)}&count=20`
          );
          if (pagesRes.ok) {
            const pagesData = await pagesRes.json();
            setFillResult((prev) =>
              prev ? { ...prev, pages: pagesData.pages, total_pages: pagesData.total_pages } : prev
            );
          }
        } finally {
          setPreviewLoading(false);
        }
      }
    } catch (e: any) {
      setFillError(e.message || "Unknown error");
    } finally {
      setFilling(false);
    }
  }, [pdfId, selectedDealId]);

  const handleDownload = () => {
    if (!fillResult) return;
    const link = document.createElement("a");
    link.href = fillResult.filled_pdf_url;
    link.download = `filled_${selectedDealId}.pdf`;
    link.click();
  };

  const selectedDeal = deals.find((d) => d.Id === selectedDealId);

  return (
    <div
      className="animate-fade-in"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-lg)",
        maxWidth: 960,
        margin: "0 auto",
        padding: "var(--space-lg)",
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "var(--space-md)" }}>
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          ← Back
        </button>
        <div>
          <h2 style={{ fontSize: "1.1rem", fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            Fill Preview
          </h2>
          <p style={{ fontSize: "0.78rem", color: "var(--text-tertiary)", margin: 0 }}>
            Select a Salesforce deal, generate and preview the filled PDF
          </p>
        </div>
      </div>

      {/* Deal selector card */}
      <div className="card" style={{ padding: "var(--space-lg)" }}>
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "0.8rem",
              fontWeight: 600,
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
            }}
          >
            Salesforce Deal
          </label>

          {dealsLoading ? (
            <div style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>
              Loading deals from Salesforce...
            </div>
          ) : dealsError ? (
            <div style={{ color: "var(--accent-red)", fontSize: "0.85rem" }}>{dealsError}</div>
          ) : deals.length === 0 ? (
            <div style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>No deals found</div>
          ) : (
            <select
              value={selectedDealId}
              onChange={(e) => {
                setSelectedDealId(e.target.value);
                setFillResult(null);
                setFillError(null);
              }}
              style={{
                width: "100%",
                padding: "10px 14px",
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                color: "var(--text-primary)",
                fontSize: "0.9rem",
                cursor: "pointer",
                appearance: "none",
                backgroundImage: `url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23888' stroke-width='2'%3E%3Cpolyline points='6 9 12 15 18 9'%3E%3C/polyline%3E%3C/svg%3E")`,
                backgroundRepeat: "no-repeat",
                backgroundPosition: "right 12px center",
                paddingRight: 36,
              }}
            >
              {deals.map((d) => (
                <option key={d.Id} value={d.Id}>
                  {d.Name || d.Id}
                  {d.StageName ? ` — ${d.StageName}` : ""}
                  {d.AccountName ? ` (${d.AccountName})` : ""}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Selected deal info row */}
        {selectedDeal && (
          <div
            style={{
              display: "flex",
              gap: "var(--space-md)",
              flexWrap: "wrap",
              padding: "var(--space-sm) var(--space-md)",
              background: "var(--bg-glass)",
              borderRadius: "var(--radius-md)",
              marginBottom: "var(--space-md)",
            }}
          >
            <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
              ID: <strong style={{ color: "var(--text-secondary)" }}>{selectedDeal.Id}</strong>
            </span>
            {selectedDeal.StageName && (
              <span className="badge badge-blue" style={{ fontSize: "0.7rem" }}>
                {selectedDeal.StageName}
              </span>
            )}
            {selectedDeal.CloseDate && (
              <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
                Close: <strong style={{ color: "var(--text-secondary)" }}>{selectedDeal.CloseDate}</strong>
              </span>
            )}
          </div>
        )}

        <button
          className="btn btn-primary btn-lg"
          onClick={handleFill}
          disabled={filling || !selectedDealId || dealsLoading}
          style={{ width: "100%" }}
        >
          {filling ? (
            <>
              <span
                style={{
                  display: "inline-block",
                  width: 14,
                  height: 14,
                  border: "2px solid rgba(255,255,255,0.3)",
                  borderTopColor: "#fff",
                  borderRadius: "50%",
                  animation: "spin 0.8s linear infinite",
                  marginRight: 8,
                }}
              />
              Filling PDF...
            </>
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 8 }}>
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10 9 9 9 8 9" />
              </svg>
              Generate Filled PDF
            </>
          )}
        </button>
      </div>

      {/* Error */}
      {fillError && (
        <div
          style={{
            padding: "var(--space-md)",
            background: "rgba(239,68,68,0.1)",
            border: "1px solid rgba(239,68,68,0.3)",
            borderRadius: "var(--radius-md)",
            color: "var(--accent-red)",
            fontSize: "0.85rem",
          }}
        >
          {fillError}
        </div>
      )}

      {/* Result */}
      {fillResult && (
        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
          {/* Stats + Download */}
          <div
            className="card"
            style={{
              padding: "var(--space-md) var(--space-lg)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "var(--space-md)",
              flexWrap: "wrap",
            }}
          >
            <div style={{ display: "flex", gap: "var(--space-lg)" }}>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--accent-green)" }}>
                  {fillResult.filled_fields?.length ?? "?"}
                </div>
                <div style={{ fontSize: "0.72rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                  Filled
                </div>
              </div>
              <div style={{ textAlign: "center" }}>
                <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--accent-amber)" }}>
                  {fillResult.skipped_fields?.length ?? "?"}
                </div>
                <div style={{ fontSize: "0.72rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                  Skipped
                </div>
              </div>
              {fillResult.total_pages && (
                <div style={{ textAlign: "center" }}>
                  <div style={{ fontSize: "1.5rem", fontWeight: 700, color: "var(--accent-blue)" }}>
                    {fillResult.total_pages}
                  </div>
                  <div style={{ fontSize: "0.72rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                    Pages
                  </div>
                </div>
              )}
            </div>

            <button className="btn btn-success" onClick={handleDownload}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ marginRight: 6 }}>
                <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                <polyline points="7 10 12 15 17 10" />
                <line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Download PDF
            </button>
          </div>

          {/* Page preview */}
          {previewLoading && (
            <div style={{ textAlign: "center", color: "var(--text-muted)", padding: "var(--space-xl)" }}>
              Loading preview...
            </div>
          )}

          {fillResult.pages && fillResult.pages.length > 0 && (
            <div className="card" style={{ padding: "var(--space-md)" }}>
              {/* Page nav */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  gap: "var(--space-md)",
                  marginBottom: "var(--space-md)",
                }}
              >
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
                  disabled={currentPage === 0}
                >
                  ←
                </button>
                <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                  Page {currentPage + 1} / {fillResult.total_pages ?? fillResult.pages.length}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => setCurrentPage((p) => Math.min(fillResult.pages!.length - 1, p + 1))}
                  disabled={currentPage >= fillResult.pages.length - 1}
                >
                  →
                </button>
              </div>

              {/* Page image */}
              <div style={{ textAlign: "center" }}>
                <img
                  src={`data:image/png;base64,${fillResult.pages[currentPage]}`}
                  alt={`Page ${currentPage + 1}`}
                  style={{
                    maxWidth: "100%",
                    borderRadius: "var(--radius-md)",
                    boxShadow: "var(--shadow-lg)",
                    border: "1px solid var(--border-subtle)",
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}

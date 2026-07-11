import { useState, useEffect, useCallback } from "react";
import { generatePackage, listDeals } from "@/api/client";
import type { PackageResult } from "@/types";

interface PackageGenerationDialogProps {
  bankId: string;
  bankName: string;
  productId: string;
  productName: string;
  onClose: () => void;
}

interface Deal {
  Id: string;
  Name?: string;
  StageName?: string;
}

export default function PackageGenerationDialog({
  bankId,
  bankName,
  productId,
  productName,
  onClose,
}: PackageGenerationDialogProps) {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [dealsLoading, setDealsLoading] = useState(true);
  const [selectedDealId, setSelectedDealId] = useState<string>("");
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<PackageResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDeals()
      .then((list) => {
        setDeals(list);
        if (list.length > 0) setSelectedDealId(list[0].Id);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setDealsLoading(false));
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!selectedDealId) return;
    setGenerating(true);
    setResult(null);
    setError(null);
    try {
      const res = await generatePackage(bankId, productId, selectedDealId);
      setResult(res);
    } catch (e) {
      setError((e as Error).message || "Csomag generálás sikertelen.");
    } finally {
      setGenerating(false);
    }
  }, [bankId, productId, selectedDealId]);

  const handleDownload = useCallback(() => {
    if (result?.package_url) {
      const a = document.createElement("a");
      a.href = result.package_url;
      a.download = `package_${bankId}_${productId}.zip`;
      a.click();
    }
  }, [result, bankId, productId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !generating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [generating, onClose]);

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0, 0, 0, 0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        backdropFilter: "blur(4px)",
      }}
      onClick={() => !generating && onClose()}
    >
      <div
        className="animate-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-secondary)",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border-subtle)",
          padding: "24px",
          maxWidth: 560,
          width: "90%",
          maxHeight: "85vh",
          overflowY: "auto",
          boxShadow: "0 20px 60px rgba(0, 0, 0, 0.4)",
        }}
      >
        <h3
          style={{
            fontSize: "1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            margin: "0 0 var(--space-md) 0",
          }}
        >
          📦 Dokumentumcsomag generálása
        </h3>

        <div style={{ marginBottom: "var(--space-md)", fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          <strong style={{ color: "var(--text-primary)" }}>{bankName}</strong>
          {" → "}
          <strong style={{ color: "var(--text-primary)" }}>{productName}</strong>
        </div>

        {/* Deal selector */}
        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            style={{
              display: "block",
              fontSize: "0.8rem",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Ügylet (Salesforce)
          </label>
          {dealsLoading ? (
            <div style={{ fontSize: "0.85rem", color: "var(--text-tertiary)" }}>Betöltés…</div>
          ) : deals.length === 0 ? (
            <div style={{ fontSize: "0.85rem", color: "var(--danger)" }}>
              Nincs elérhető ügylet.
            </div>
          ) : (
            <select
              value={selectedDealId}
              onChange={(e) => setSelectedDealId(e.target.value)}
              disabled={generating}
              style={{
                width: "100%",
                padding: "8px 10px",
                background: "var(--bg-tertiary)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: "0.85rem",
                cursor: "pointer",
              }}
            >
              {deals.map((d) => (
                <option key={d.Id} value={d.Id}>
                  {d.Name || d.Id} {d.StageName ? `(${d.StageName})` : ""}
                </option>
              ))}
            </select>
          )}
        </div>

        {/* Error */}
        {error && (
          <div
            style={{
              marginBottom: "var(--space-md)",
              padding: "10px 12px",
              background: "var(--danger-bg)",
              border: "1px solid var(--danger)",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.85rem",
              color: "var(--danger)",
            }}
          >
            {error}
          </div>
        )}

        {/* Generate button */}
        {!result && (
          <button
            onClick={handleGenerate}
            disabled={generating || !selectedDealId}
            style={{
              width: "100%",
              padding: "10px 16px",
              background: generating ? "var(--accent-pressed)" : "var(--accent)",
              color: "white",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.9rem",
              fontWeight: 500,
              cursor: generating || !selectedDealId ? "not-allowed" : "pointer",
              opacity: generating || !selectedDealId ? 0.6 : 1,
            }}
          >
            {generating ? "⏳ Generálás folyamatban…" : "📦 Csomag generálása"}
          </button>
        )}

        {/* Result */}
        {result && (
          <div>
            <div
              style={{
                marginBottom: "var(--space-md)",
                padding: "12px",
                background: "var(--bg-tertiary)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border-subtle)",
              }}
            >
              <div style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                {result.total_documents} dokumentum sikeresen generálva
                {result.errors.length > 0 && (
                  <span style={{ color: "var(--danger)" }}> ({result.errors.length} hiba)</span>
                )}
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                {result.documents.map((doc, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      fontSize: "0.82rem",
                      color: "var(--text-primary)",
                    }}
                  >
                    <span style={{ color: doc.success ? "var(--success)" : "var(--danger)" }}>
                      {doc.success ? "✓" : "✗"}
                    </span>
                    <span style={{ flex: 1 }}>
                      {doc.title}
                      {doc.per_applicant && (
                        <span
                          style={{
                            marginLeft: "6px",
                            padding: "1px 5px",
                            background: "var(--warning-bg)",
                            borderRadius: "3px",
                            fontSize: "0.7rem",
                            color: "var(--warning)",
                          }}
                        >
                          👤 adósonként
                        </span>
                      )}
                    </span>
                    {doc.success && (
                      <span style={{ color: "var(--text-tertiary)", fontSize: "0.75rem" }}>
                        {doc.filled_fields || 0} mező
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Error details */}
            {result.errors.length > 0 && (
              <div
                style={{
                  marginBottom: "var(--space-md)",
                  padding: "10px 12px",
                  background: "var(--danger-bg)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.8rem",
                  color: "var(--danger)",
                }}
              >
                {result.errors.map((e, i) => (
                  <div key={i}>
                    <strong>{e.document}:</strong> {e.error}
                  </div>
                ))}
              </div>
            )}

            {/* Download + close */}
            <div style={{ display: "flex", gap: "8px" }}>
              <button
                onClick={handleDownload}
                style={{
                  flex: 1,
                  padding: "10px 16px",
                  background: "var(--success)",
                  color: "white",
                  border: "none",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.9rem",
                  fontWeight: 500,
                  cursor: "pointer",
                }}
              >
                💾 ZIP letöltése
              </button>
              <button
                onClick={onClose}
                style={{
                  flex: 1,
                  padding: "10px 16px",
                  background: "var(--bg-tertiary)",
                  color: "var(--text-primary)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.9rem",
                  cursor: "pointer",
                }}
              >
                ✓ Befejezés
              </button>
            </div>
          </div>
        )}

        {/* Cancel button when not yet generated */}
        {!result && (
          <button
            onClick={onClose}
            disabled={generating}
            style={{
              width: "100%",
              marginTop: "var(--space-sm)",
              padding: "8px 16px",
              background: "transparent",
              color: "var(--text-secondary)",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.85rem",
              cursor: generating ? "not-allowed" : "pointer",
            }}
          >
            Mégse
          </button>
        )}
      </div>
    </div>
  );
}

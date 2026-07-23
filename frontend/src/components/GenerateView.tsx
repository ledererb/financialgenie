import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/store";
import { generatePackage, listDeals } from "@/api/client";
import type { PackageResult } from "@/types";

interface GenerateViewProps {
  preselectBankId?: string | null;
  preselectBankName?: string | null;
  preselectProductId?: string | null;
  preselectProductName?: string | null;
}

interface Deal {
  Id: string;
  Name?: string;
  StageName?: string;
}

export default function GenerateView({
  preselectBankId,
  preselectBankName,
  preselectProductId,
  preselectProductName,
}: GenerateViewProps) {
  const catalog = useStore((s) => s.catalog);

  const [deals, setDeals] = useState<Deal[]>([]);
  const [dealsLoading, setDealsLoading] = useState(true);
  const [selectedDealId, setSelectedDealId] = useState<string>("");
  const [selectedBankId, setSelectedBankId] = useState<string>(preselectBankId || "");
  const [selectedProductId, setSelectedProductId] = useState<string>(preselectProductId || "");
  const [uploadToSf, setUploadToSf] = useState(true);
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

  const banks = catalog?.banks ?? [];
  const products = banks.find((b) => b.id === selectedBankId)?.products ?? [];
  const selectedProductDocs = catalog?.documents.filter(
    (d) => selectedProductId && d.product_ids.includes(selectedProductId),
  ) ?? [];

  const handleGenerate = useCallback(async () => {
    if (!selectedDealId || !selectedBankId || !selectedProductId) return;
    setGenerating(true);
    setResult(null);
    setError(null);
    try {
      const res = await generatePackage(selectedBankId, selectedProductId, selectedDealId, uploadToSf);
      setResult(res);
    } catch (e) {
      setError((e as Error).message || "Csomag generalas sikertelen.");
    } finally {
      setGenerating(false);
    }
  }, [selectedBankId, selectedProductId, selectedDealId, uploadToSf]);

  const handleDownload = useCallback(() => {
    if (result?.package_url) {
      const a = document.createElement("a");
      a.href = result.package_url;
      a.download = `package_${selectedBankId}_${selectedProductId}.zip`;
      a.click();
    }
  }, [result, selectedBankId, selectedProductId]);

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
          Dokumentumcsomag generalasa
        </h2>
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-xl) 24px" }}>
        <div style={{ maxWidth: 680, margin: "0 auto" }}>
          {/* Bank & Product selector */}
          <div style={{ marginBottom: "var(--space-md)" }}>
            <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
              Bank
            </label>
            <select
              value={selectedBankId}
              onChange={(e) => {
                setSelectedBankId(e.target.value);
                setSelectedProductId("");
              }}
              disabled={generating}
              style={{
                width: "100%", padding: "8px 10px",
                background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)", color: "var(--text-primary)",
                fontSize: "0.85rem", cursor: "pointer",
              }}
            >
              <option value="">-- Valassz bankot --</option>
              {banks.map((b) => (
                <option key={b.id} value={b.id}>{b.name}</option>
              ))}
            </select>
          </div>

          <div style={{ marginBottom: "var(--space-md)" }}>
            <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
              Termek
            </label>
            <select
              value={selectedProductId}
              onChange={(e) => setSelectedProductId(e.target.value)}
              disabled={generating || !selectedBankId}
              style={{
                width: "100%", padding: "8px 10px",
                background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)", color: "var(--text-primary)",
                fontSize: "0.85rem", cursor: "pointer",
                opacity: !selectedBankId ? 0.5 : 1,
              }}
            >
              <option value="">-- Valassz termesket --</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Doc count info */}
          {selectedProductId && selectedProductDocs.length > 0 && (
            <div style={{ marginBottom: "var(--space-md)", fontSize: "0.8rem", color: "var(--text-tertiary)" }}>
              {selectedProductDocs.length} dokumentum lesz generalva
            </div>
          )}

          {/* Deal selector */}
          <div style={{ marginBottom: "var(--space-md)" }}>
            <label style={{ display: "block", fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
              Ugylet (Salesforce)
            </label>
            {dealsLoading ? (
              <div style={{ fontSize: "0.85rem", color: "var(--text-tertiary)" }}>Betoltes...</div>
            ) : deals.length === 0 ? (
              <div style={{ fontSize: "0.85rem", color: "var(--accent-red)" }}>Nincs elerheto ugylet.</div>
            ) : (
              <select
                value={selectedDealId}
                onChange={(e) => setSelectedDealId(e.target.value)}
                disabled={generating}
                style={{
                  width: "100%", padding: "8px 10px",
                  background: "var(--bg-tertiary)", border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-sm)", color: "var(--text-primary)",
                  fontSize: "0.85rem", cursor: "pointer",
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
                background: "var(--accent-red-glow)",
                border: "1px solid var(--accent-red)",
                borderRadius: "var(--radius-sm)",
                fontSize: "0.85rem",
                color: "var(--accent-red)",
              }}
            >
              {error}
            </div>
          )}

          {/* SF upload toggle */}
          {!result && (
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "8px",
                marginBottom: "var(--space-md)",
                cursor: "pointer",
                fontSize: "0.85rem",
                color: "var(--text-secondary)",
              }}
            >
              <input
                type="checkbox"
                checked={uploadToSf}
                onChange={(e) => setUploadToSf(e.target.checked)}
                disabled={generating}
                style={{ cursor: "pointer" }}
              />
              Feltoltes Salesforce-ba a generalas utan
            </label>
          )}

          {/* Generate button */}
          {!result && (
            <button
              onClick={handleGenerate}
              disabled={generating || !selectedDealId || !selectedProductId}
              style={{
                width: "100%",
                padding: "12px 16px",
                background: generating ? "var(--bg-hover)" : "var(--accent-blue)",
                color: "white",
                border: "none",
                borderRadius: "var(--radius-sm)",
                fontSize: "0.9rem",
                fontWeight: 500,
                cursor: generating || !selectedDealId || !selectedProductId ? "not-allowed" : "pointer",
                opacity: generating || !selectedDealId || !selectedProductId ? 0.5 : 1,
              }}
            >
              {generating ? "Generalas folyamatban..." : "Csomag generalasa"}
            </button>
          )}

          {/* Result */}
          {result && (
            <div>
              <div
                style={{
                  marginBottom: "var(--space-md)",
                  padding: "16px",
                  background: "var(--bg-tertiary)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid var(--border-subtle)",
                }}
              >
                <div style={{ fontSize: "0.9rem", color: "var(--text-secondary)", marginBottom: "var(--space-sm)" }}>
                  {result.total_documents} dokumentum sikeresen generalva
                  {result.errors.length > 0 && (
                    <span style={{ color: "var(--accent-red)" }}> ({result.errors.length} hiba)</span>
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
                      <span style={{
                        color: doc.success ? "var(--accent-green)" : "var(--accent-red)",
                        fontWeight: 600,
                        width: 12,
                      }}>
                        {doc.success ? "+" : "x"}
                      </span>
                      <span style={{ flex: 1 }}>
                        {doc.title}
                        {doc.per_applicant && (
                          <span style={{
                            marginLeft: "6px",
                            padding: "1px 5px",
                            background: "var(--accent-amber-glow)",
                            borderRadius: "3px",
                            fontSize: "0.7rem",
                            color: "var(--accent-amber)",
                          }}>
                            adosonkent
                          </span>
                        )}
                      </span>
                      {doc.success && (
                        <span style={{ color: "var(--text-tertiary)", fontSize: "0.75rem" }}>
                          {doc.filled_fields || 0} mezo
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* SF upload results */}
              {result.sf_uploads && result.sf_uploads.length > 0 && (
                <div
                  style={{
                    marginBottom: "var(--space-md)",
                    padding: "12px 16px",
                    background: "var(--bg-tertiary)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--border-subtle)",
                  }}
                >
                  <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginBottom: "var(--space-xs)" }}>
                    Salesforce feltoltes ({result.sf_uploads.filter((u) => u.success).length}/{result.sf_uploads.length})
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    {result.sf_uploads.map((u, i) => (
                      <div key={i} style={{ display: "flex", gap: "6px", fontSize: "0.75rem" }}>
                        <span style={{
                          color: u.success ? "var(--accent-green)" : "var(--accent-red)",
                          fontWeight: 600,
                          width: 12,
                        }}>
                          {u.success ? "+" : "x"}
                        </span>
                        <span style={{ color: "var(--text-tertiary)", flex: 1 }}>{u.file}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Actions */}
              <div style={{ display: "flex", gap: "8px" }}>
                <button
                  onClick={handleDownload}
                  style={{
                    flex: 1,
                    padding: "10px 16px",
                    background: "var(--accent-green)",
                    color: "white",
                    border: "none",
                    borderRadius: "var(--radius-sm)",
                    fontSize: "0.9rem",
                    fontWeight: 500,
                    cursor: "pointer",
                  }}
                >
                  ZIP letoltese
                </button>
                <button
                  onClick={() => { setResult(null); setError(null); }}
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
                  Uj generalas
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

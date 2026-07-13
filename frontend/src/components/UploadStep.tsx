import { useState, useRef, useCallback } from "react";
import { uploadPdf } from "@/api/client";
import { useStore } from "@/store";
import SplitProgressIndicator from "./SplitProgressIndicator";

interface UploadStepProps {
  onComplete: (pdfId: string) => void;
  onOpenExisting: (pdfId: string) => void;
  onOpenSectionEditor: (file: File) => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function productName(catalog: { banks: { id: string; name: string; products: { id: string; name: string }[] }[] } | null, pid: string): string {
  if (!catalog) return pid;
  for (const bank of catalog.banks) {
    const prod = bank.products.find((p) => p.id === pid);
    if (prod) return prod.name;
  }
  return pid;
}

function bankName(catalog: { banks: { id: string; name: string; products: { id: string; name: string }[] }[] } | null, pid: string): string {
  if (!catalog) return "";
  for (const bank of catalog.banks) {
    if (bank.products.some((p) => p.id === pid)) return bank.name;
  }
  return "";
}

function isMasterPdfCandidate(f: File): boolean {
  const folded = f.name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "");
  return (
    folded.includes("igenylesi") ||
    /_v\d/i.test(folded) ||
    f.size > 2 * 1024 * 1024
  );
}

export default function UploadStep({ onComplete, onOpenExisting, onOpenSectionEditor }: UploadStepProps) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [splitting, setSplitting] = useState(false);
  const [splitId, setSplitId] = useState<string | null>(null);
  const [dismissedMaster, setDismissedMaster] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Store state ---
  const selectedBankId = useStore((s) => s.selectedBankId);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const catalog = useStore((s) => s.catalog);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const registerDocument = useStore((s) => s.registerDocument);
  const uploadedHashes = useStore((s) => s.uploadedHashes);
  const addUploadedHash = useStore((s) => s.addUploadedHash);
  const startMasterSplit = useStore((s) => s.startMasterSplit);

  const hasSelection = !!(selectedBankId && selectedProductId);

  const handleFile = useCallback((f: File) => {
    if (f.type !== "application/pdf") {
      setError("Kérlek, válassz PDF fájlt.");
      return;
    }
    setFile(f);
    setError(null);
    setSplitId(null);
    setDismissedMaster(false);
  }, []);

  // Drag-and-drop handlers
  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const droppedFile = e.dataTransfer.files[0];
      if (droppedFile) handleFile(droppedFile);
    },
    [handleFile],
  );

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const selected = e.target.files?.[0];
      if (selected) handleFile(selected);
    },
    [handleFile],
  );

  const handleUpload = useCallback(async () => {
    if (!file || !selectedProductId) return;
    setUploading(true);
    setProgress(0);
    setError(null);

    try {
      // Simulated progress for UX
      const progressTimer = setInterval(() => {
        setProgress((p) => Math.min(p + 10, 90));
      }, 200);

      const res = await uploadPdf(file);
      clearInterval(progressTimer);
      setProgress(100);

      const fileHash = res.hash || "";
      const isDuplicate = fileHash !== "" && uploadedHashes.has(fileHash);

      if (fileHash) addUploadedHash(fileHash);

      // Register the document in the catalog and associate with the selected product
      const docId = `doc_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
      const title = file.name.replace(/\.pdf$/i, "");

      if (isDuplicate) {
        setError(`Ez a fájl már fel lett töltve: ${file.name}`);
      } else {
        await registerDocument({
          id: docId,
          title,
          file_path: res.path || res.pdf_id,
          product_ids: [selectedProductId],
          page_count: 0,
          source: `upload:${file.name}`,
          sha256: fileHash,
        });
      }

      await loadCatalog();
      onComplete(res.pdf_id);
    } catch (e) {
      setError((e as Error).message || "A feltöltés sikertelen. Próbáld újra.");
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }, [file, selectedProductId, uploadedHashes, addUploadedHash, registerDocument, loadCatalog, onComplete]);

  const handleClearFile = useCallback(() => {
    setFile(null);
    setError(null);
    setSplitId(null);
    setDismissedMaster(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const handleSplit = useCallback(async () => {
    if (!file || !selectedBankId) return;
    setSplitting(true);
    setError(null);
    try {
      const id = await startMasterSplit(selectedBankId, file);
      setSplitId(id);
    } catch (e) {
      setError((e as Error).message || "A darabolás indítása sikertelen.");
    } finally {
      setSplitting(false);
    }
  }, [file, selectedBankId, startMasterSplit]);

  const handleSplitComplete = useCallback(() => {
    setFile(null);
    setSplitId(null);
    setDismissedMaster(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  // --- No bank/product selected: show message instead of drop zone ---
  if (!hasSelection) {
    return (
      <div className="animate-fade-in" style={{ maxWidth: 680, margin: "0 auto" }}>
        <div
          style={{
            padding: "var(--space-xl)",
            textAlign: "center",
            background: "var(--bg-secondary)",
            borderRadius: "var(--radius-lg)",
            border: "1px dashed var(--border-subtle)",
            color: "var(--text-secondary)",
          }}
        >
          <svg
            width="40"
            height="40"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--text-tertiary)"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{ margin: "0 auto var(--space-md)", display: "block" }}
          >
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
          <p style={{ fontSize: "0.9rem", color: "var(--text-primary)", fontWeight: 500 }}>
            Válasszon bankot és terméket a bal oldali fán
          </p>
          <p style={{ fontSize: "0.8rem", color: "var(--text-tertiary)", marginTop: "var(--space-xs)" }}>
            A dokumentum feltöltés előtt ki kell választani egy bankot és egy terméket.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-fade-in" style={{ maxWidth: 680, margin: "0 auto" }}>
      {/* Context banner: selected bank / product */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          padding: "10px 16px",
          background: "var(--accent-blue-glow, rgba(59,130,246,0.1))",
          borderRadius: "var(--radius-md)",
          border: "1px solid var(--border-subtle)",
          marginBottom: "var(--space-lg)",
        }}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="var(--accent-blue)"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
        </svg>
        <span style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>
          <strong>Feltöltés:</strong> {bankName(catalog, selectedProductId!)} / {productName(catalog, selectedProductId!)}
        </span>
      </div>

      {/* Drop zone */}
      <div
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onClick={() => !file && fileInputRef.current?.click()}
        style={{ marginBottom: "var(--space-lg)" }}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,application/pdf"
          onChange={handleInputChange}
          style={{ display: "none" }}
        />

        {!file ? (
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "var(--space-md)" }}>
            {/* Upload icon */}
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-blue)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
              <polyline points="17 8 12 3 7 8" />
              <line x1="12" y1="3" x2="12" y2="15" />
            </svg>
            <div>
              <p style={{ fontSize: "1rem", fontWeight: 500, color: "var(--text-primary)" }}>
                Húzd ide a PDF-et
              </p>
              <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginTop: "var(--space-xs)" }}>
                vagy kattints a tallózáshoz
              </p>
            </div>
          </div>
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "var(--space-md)",
              justifyContent: "center",
            }}
          >
            {/* PDF icon */}
            <svg
              width="36"
              height="36"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-red)"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
              <polyline points="14 2 14 8 20 8" />
            </svg>
            <div style={{ textAlign: "left" }}>
              <p style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: "0.9rem" }}>
                {file.name}
              </p>
              <p style={{ fontSize: "0.75rem", color: "var(--text-secondary)" }}>
                {formatBytes(file.size)}
              </p>
            </div>
            <button
              className="btn btn-ghost btn-sm"
              onClick={(e) => {
                e.stopPropagation();
                handleClearFile();
              }}
              style={{ marginLeft: "var(--space-sm)" }}
            >
              ✕
            </button>
          </div>
        )}
      </div>

      {/* Upload progress bar */}
      {uploading && (
        <div style={{ marginBottom: "var(--space-md)" }}>
          <div
            style={{
              height: 6,
              background: "var(--bg-primary)",
              borderRadius: 3,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${progress}%`,
                height: "100%",
                background: "var(--accent-blue)",
                borderRadius: 3,
                transition: "width 0.2s ease",
              }}
            />
          </div>
          <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", textAlign: "center", marginTop: "var(--space-xs)" }}>
            {progress}%
          </p>
        </div>
      )}

      {/* Error / dedup warning */}
      {error && (
        <div
          className="animate-fade-in"
          style={{
            padding: "var(--space-sm) var(--space-md)",
            background: "var(--accent-red-glow)",
            borderRadius: "var(--radius-md)",
            color: "var(--accent-red)",
            fontSize: "0.8rem",
            marginBottom: "var(--space-md)",
          }}
        >
          {error}
        </div>
      )}

      {/* Split progress indicator (Phase 4 — async master split) */}
      {splitId && (
        <SplitProgressIndicator splitId={splitId} onComplete={handleSplitComplete} />
      )}

      {/* Master PDF detection prompt (Phase 4) */}
      {file && !splitId && !dismissedMaster && isMasterPdfCandidate(file) && (
        <div
          className="animate-fade-in"
          style={{
            padding: "var(--space-md)",
            background: "var(--accent-blue-glow, rgba(59,130,246,0.1))",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--accent-blue, #3b82f6)",
            marginBottom: "var(--space-md)",
          }}
        >
          <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", margin: 0, marginBottom: "var(--space-sm)" }}>
            Ez a fájl nagy méretű mester PDF-nek tűnik. Hogyan szeretné feldolgozni?
          </p>
          <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap" }}>
            <button
              className="btn btn-primary btn-sm"
              onClick={handleSplit}
              disabled={splitting}
            >
              {splitting ? "Indítás…" : "⚡ Automatikus darabolás"}
            </button>
            <button
              className="btn btn-sm"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--accent-blue)" }}
              onClick={() => file && onOpenSectionEditor(file)}
              disabled={splitting}
            >
              ✎ Manuális szerkesztő
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setDismissedMaster(true)}
              disabled={splitting}
            >
              Feltöltés egészben
            </button>
          </div>
        </div>
      )}

      {/* Upload button — hidden during split or when master prompt is shown */}
      {file && !splitId && !(isMasterPdfCandidate(file) && !dismissedMaster) && (
        <div className="animate-fade-in" style={{ textAlign: "center", marginBottom: "var(--space-xl)" }}>
          <button
            className="btn btn-primary btn-lg"
            onClick={handleUpload}
            disabled={uploading}
          >
            {uploading ? (
              <>
                <span
                  style={{
                    width: 16,
                    height: 16,
                    border: "2px solid rgba(255,255,255,0.3)",
                    borderTopColor: "white",
                    borderRadius: "50%",
                    display: "inline-block",
                    animation: "spin 0.8s linear infinite",
                  }}
                />
                Feltöltés…
              </>
            ) : (
              <>Feltöltés</>
            )}
          </button>
        </div>
      )}

      {/* Inline keyframes for the spinner */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

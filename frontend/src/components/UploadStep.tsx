import { useState, useEffect, useRef, useCallback } from "react";
import { uploadPdf, listPdfs, deletePdf } from "@/api/client";
import type { PdfSummary } from "@/types";

interface UploadStepProps {
  onComplete: (pdfId: string) => void;
  onOpenExisting: (pdfId: string) => void;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadStep({ onComplete, onOpenExisting }: UploadStepProps) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pdfs, setPdfs] = useState<PdfSummary[]>([]);
  const [pdfsLoading, setPdfsLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<PdfSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Load existing PDFs on mount
  useEffect(() => {
    let cancelled = false;
    setPdfsLoading(true);
    listPdfs()
      .then((data) => {
        if (!cancelled) setPdfs(data);
      })
      .catch(() => {
        /* silent */
      })
      .finally(() => {
        if (!cancelled) setPdfsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleFile = useCallback((f: File) => {
    if (f.type !== "application/pdf") {
      setError("Please select a PDF file.");
      return;
    }
    setFile(f);
    setError(null);
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
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const res = await uploadPdf(file);
      onComplete(res.pdf_id);
    } catch (e) {
      setError((e as Error).message || "Upload failed. Please try again.");
    } finally {
      setUploading(false);
    }
  }, [file, onComplete]);

  const handleClearFile = useCallback(() => {
    setFile(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deletePdf(deleteTarget.pdf_id);
      setPdfs((prev) => prev.filter((p) => p.pdf_id !== deleteTarget.pdf_id));
    } catch (e) {
      setError(`Delete failed: ${(e as Error).message}`);
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  }, [deleteTarget]);

  return (
    <div className="animate-fade-in" style={{ maxWidth: 680, margin: "0 auto" }}>
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
                Drop your PDF here
              </p>
              <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", marginTop: "var(--space-xs)" }}>
                or click to browse files
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

      {/* Error */}
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

      {/* Upload button */}
      {file && (
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
                Uploading…
              </>
            ) : (
              <>Upload & Analyze</>
            )}
          </button>
        </div>
      )}

      {/* Recent mappings list */}
      {pdfs.length > 0 && (
        <div>
          <h3
            style={{
              fontSize: "0.8rem",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-md)",
            }}
          >
            Recent PDFs
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
            {pdfs.map((pdf) => (
              <div
                key={pdf.pdf_id}
                className="mapping-row"
                style={{ cursor: "pointer" }}
              >
                <div
                  style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flex: 1 }}
                  onClick={() => onOpenExisting(pdf.pdf_id)}
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="var(--text-tertiary)"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <span style={{ flex: 1, fontSize: "0.875rem", color: "var(--text-primary)" }}>
                    {pdf.name}
                  </span>
                  <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
                    {formatBytes(pdf.size_bytes)}
                  </span>
                  <span className="badge badge-blue">Open</span>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  title="Delete PDF"
                  onClick={(e) => {
                    e.stopPropagation();
                    setDeleteTarget(pdf);
                  }}
                  style={{
                    padding: "4px 8px",
                    color: "var(--text-tertiary)",
                    transition: "color 0.15s",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.color = "var(--accent-red)")}
                  onMouseLeave={(e) => (e.currentTarget.style.color = "var(--text-tertiary)")}
                >
                  🗑️
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {pdfsLoading && (
        <p style={{ textAlign: "center", color: "var(--text-tertiary)", fontSize: "0.8rem" }}>
          Loading existing PDFs…
        </p>
      )}

      {/* Delete confirmation modal */}
      {deleteTarget && (
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
          onClick={() => !deleting && setDeleteTarget(null)}
        >
          <div
            className="animate-fade-in"
            onClick={(e) => e.stopPropagation()}
            style={{
              background: "var(--bg-secondary)",
              borderRadius: "var(--radius-lg)",
              border: "1px solid var(--border-subtle)",
              padding: "24px",
              maxWidth: 420,
              width: "90%",
              boxShadow: "0 20px 60px rgba(0, 0, 0, 0.4)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "16px" }}>
              <div style={{
                width: 40,
                height: 40,
                borderRadius: "50%",
                background: "var(--accent-red-glow)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: "1.2rem",
              }}>
                🗑️
              </div>
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
                  Delete PDF?
                </h3>
                <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", margin: "4px 0 0" }}>
                  This will also remove its mapping configuration.
                </p>
              </div>
            </div>

            <div style={{
              padding: "12px 16px",
              background: "var(--bg-primary)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-subtle)",
              marginBottom: "20px",
            }}>
              <p style={{ fontSize: "0.85rem", color: "var(--text-primary)", fontWeight: 500, margin: 0 }}>
                {deleteTarget.name}
              </p>
              <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", margin: "4px 0 0" }}>
                {formatBytes(deleteTarget.size_bytes)}
              </p>
            </div>

            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
              <button
                className="btn btn-ghost"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
                style={{ padding: "8px 16px", fontSize: "0.85rem" }}
              >
                Cancel
              </button>
              <button
                onClick={handleDeleteConfirm}
                disabled={deleting}
                style={{
                  padding: "8px 16px",
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  border: "none",
                  borderRadius: "var(--radius-md)",
                  background: "var(--accent-red)",
                  color: "white",
                  cursor: deleting ? "wait" : "pointer",
                  opacity: deleting ? 0.6 : 1,
                  transition: "opacity 0.15s, background 0.15s",
                }}
              >
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
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

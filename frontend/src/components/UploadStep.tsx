import { useState, useEffect, useRef, useCallback } from "react";
import { uploadPdf, listPdfs, deletePdf } from "@/api/client";
import type { PdfSummary, CatalogDocument } from "@/types";
import { useStore } from "@/store";
import ProductAssociationDialog from "./ProductAssociationDialog";
import SplitProgressIndicator from "./SplitProgressIndicator";

interface UploadStepProps {
  onComplete: (pdfId: string) => void;
  onOpenExisting: (pdfId: string) => void;
  onOpenSectionEditor: (file: File) => void;
}

interface UploadedDoc {
  docId: string;
  title: string;
  hash: string;
  duplicate: boolean;
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
  const [pdfs, setPdfs] = useState<PdfSummary[]>([]);
  const [pdfsLoading, setPdfsLoading] = useState(true);
  const [deleteTarget, setDeleteTarget] = useState<PdfSummary | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [uploadedDocs, setUploadedDocs] = useState<UploadedDoc[]>([]);
  const [associationDoc, setAssociationDoc] = useState<CatalogDocument | null>(null);
  const [splitting, setSplitting] = useState(false);
  const [splitId, setSplitId] = useState<string | null>(null);
  const [dismissedMaster, setDismissedMaster] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // --- Store state (Phase 3 context awareness) ---
  const selectedBankId = useStore((s) => s.selectedBankId);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const catalog = useStore((s) => s.catalog);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const registerDocument = useStore((s) => s.registerDocument);
  const associateDocumentWithProduct = useStore((s) => s.associateDocumentWithProduct);
  const uploadedHashes = useStore((s) => s.uploadedHashes);
  const addUploadedHash = useStore((s) => s.addUploadedHash);
  const startMasterSplit = useStore((s) => s.startMasterSplit);

  const hasSelection = !!(selectedBankId && selectedProductId);

  // Look up catalog documents for the selected product (for association display)
  const selectedProductDocs: CatalogDocument[] = (catalog?.documents ?? []).filter(
    (d) => selectedProductId && d.product_ids.includes(selectedProductId),
  );

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
        // Dedup: file already uploaded this session — just associate with the new product
        const existingDoc = uploadedDocs.find((d) => d.hash === fileHash);
        if (existingDoc && !existingDoc.duplicate) {
          await associateDocumentWithProduct(existingDoc.docId, [
            ...new Set([...(catalog?.documents.find((d) => d.id === existingDoc.docId)?.product_ids ?? []), selectedProductId]),
          ]);
        }
        setError(`Ez a fájl már létezik: ${file.name} — termék társítva.`);
      } else {
        const doc = await registerDocument({
          id: docId,
          title,
          file_path: res.path || res.pdf_id,
          product_ids: [selectedProductId],
          page_count: 0,
          source: `upload:${file.name}`,
          sha256: fileHash,
        });
        setUploadedDocs((prev) => [
          ...prev,
          { docId: doc.id, title, hash: fileHash, duplicate: false },
        ]);
      }

      await loadCatalog();
      onComplete(res.pdf_id);
    } catch (e) {
      setError((e as Error).message || "A feltöltés sikertelen. Próbáld újra.");
    } finally {
      setUploading(false);
      setProgress(0);
    }
  }, [file, selectedProductId, uploadedHashes, addUploadedHash, uploadedDocs, catalog, associateDocumentWithProduct, registerDocument, loadCatalog, onComplete]);

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

  const handleDeleteConfirm = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deletePdf(deleteTarget.pdf_id);
      setPdfs((prev) => prev.filter((p) => p.pdf_id !== deleteTarget.pdf_id));
      // The backend now also removes any catalog document pointing to this
      // file, so refresh the catalog to clear orphaned entries.
      await loadCatalog();
    } catch (e) {
      setError(`Törlés sikertelen: ${(e as Error).message}`);
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  }, [deleteTarget, loadCatalog]);

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

        {/* Keep existing PDF list accessible even without selection */}
        {pdfs.length > 0 && (
          <div style={{ marginTop: "var(--space-xl)" }}>
            <h3
              style={{
                fontSize: "0.8rem",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                color: "var(--text-secondary)",
                marginBottom: "var(--space-md)",
              }}
            >
              Meglévő PDF-ek
            </h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
              {pdfs.map((pdf) => (
                <div key={pdf.pdf_id} className="mapping-row" style={{ cursor: "pointer" }}>
                  <div
                    style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", flex: 1 }}
                    onClick={() => onOpenExisting(pdf.pdf_id)}
                  >
                    <span style={{ flex: 1, fontSize: "0.875rem", color: "var(--text-primary)" }}>
                      {pdf.name}
                    </span>
                    <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
                      {formatBytes(pdf.size_bytes)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
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

      {/* Product Association section — documents in the selected product */}
      {selectedProductDocs.length > 0 && (
        <div style={{ marginBottom: "var(--space-xl)" }}>
          <h3
            style={{
              fontSize: "0.8rem",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-md)",
            }}
          >
            Feltöltött dokumentumok ({selectedProductDocs.length})
          </h3>
          <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-sm)" }}>
            {selectedProductDocs.map((doc) => (
              <div
                key={doc.id}
                className="mapping-row"
                style={{ flexDirection: "column", alignItems: "flex-start", gap: "var(--space-xs)" }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "var(--space-sm)", width: "100%" }}>
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
                    {doc.title}
                  </span>
                </div>
                {/* Product association badges */}
                <div style={{ display: "flex", flexWrap: "wrap", gap: "4px", alignItems: "center", paddingLeft: 26 }}>
                  {doc.product_ids.map((pid) => (
                    <span
                      key={pid}
                      className="badge badge-blue"
                      style={{ cursor: "pointer" }}
                      onClick={() => setAssociationDoc(doc)}
                      title="Kattintson a termékek szerkesztéséhez"
                    >
                      {productName(catalog, pid)}
                    </span>
                  ))}
                  <button
                    className="btn btn-ghost btn-sm"
                    style={{ padding: "2px 8px", fontSize: "0.75rem" }}
                    onClick={() => setAssociationDoc(doc)}
                  >
                    ✎ Szerkesztés
                  </button>
                </div>
              </div>
            ))}
          </div>
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
            Meglévő PDF-ek
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
                  <span className="badge badge-blue">Megnyitás</span>
                </div>
                <button
                  className="btn btn-ghost btn-sm"
                  title="PDF törlése"
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
                  Törlés
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {pdfsLoading && (
        <p style={{ textAlign: "center", color: "var(--text-tertiary)", fontSize: "0.8rem" }}>
          Meglévő PDF-ek betöltése…
        </p>
      )}

      {/* Product Association Dialog */}
      {associationDoc && (
        <ProductAssociationDialog
          docId={associationDoc.id}
          docTitle={associationDoc.title}
          currentProductIds={associationDoc.product_ids}
          onClose={() => setAssociationDoc(null)}
        />
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
                ×
              </div>
              <div>
                <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
                  PDF törlése?
                </h3>
                <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", margin: "4px 0 0" }}>
                  A mapping konfiguráció is törlésre kerül.
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
                Mégse
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
                {deleting ? "Törlés…" : "Törlés"}
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

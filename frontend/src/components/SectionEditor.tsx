import { useState, useEffect, useCallback, useRef } from "react";
import { useStore } from "@/store";
import { uploadMasterPdf, extractSection, pageImageUrl } from "@/api/client";
import type { CatalogDocument } from "@/types";

interface SectionEditorProps {
  bankId: string;
  initialFile?: File | null;
  onClose: () => void;
}

interface CreatedSection {
  title: string;
  startPage: number;
  endPage: number;
  productIds: string[];
}

export default function SectionEditor({ bankId, initialFile, onClose }: SectionEditorProps) {
  const catalog = useStore((s) => s.catalog);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const selectedProductId = useStore((s) => s.selectedProductId);
  const deleteCatalogDocument = useStore((s) => s.deleteCatalogDocument);

  const [file, setFile] = useState<File | null>(initialFile ?? null);
  const [pdfId, setPdfId] = useState<string | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [sectionTitle, setSectionTitle] = useState("");
  const [selectedProductIds, setSelectedProductIds] = useState<Set<string>>(
    new Set(selectedProductId ? [selectedProductId] : []),
  );
  const [extracting, setExtracting] = useState(false);
  const [createdSections, setCreatedSections] = useState<CreatedSection[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const bank = catalog?.banks.find((b) => b.id === bankId);

  // Auto-upload when a file is provided
  const handleUpload = useCallback(async (f: File) => {
    setUploading(true);
    setError(null);
    try {
      const res = await uploadMasterPdf(bankId, f);
      setPdfId(res.pdf_id);
      setPageCount(res.page_count);
      setSelectedPages(new Set());
      setCreatedSections([]);
    } catch (e) {
      setError((e as Error).message || "A feltöltés sikertelen.");
    } finally {
      setUploading(false);
    }
  }, [bankId]);

  useEffect(() => {
    if (initialFile && !pdfId) {
      handleUpload(initialFile);
    }
  }, [initialFile, pdfId, handleUpload]);

  const handleFileChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (!f) return;
    setFile(f);
    setPdfId(null);
    setSelectedPages(new Set());
    setCreatedSections([]);
    handleUpload(f);
  }, [handleUpload]);

  const togglePage = useCallback((page: number) => {
    setSelectedPages((prev) => {
      const next = new Set(prev);
      if (next.has(page)) {
        next.delete(page);
      } else {
        next.add(page);
      }
      return next;
    });
  }, []);

  const toggleProduct = useCallback((productId: string) => {
    setSelectedProductIds((prev) => {
      const next = new Set(prev);
      if (next.has(productId)) {
        next.delete(productId);
      } else {
        next.add(productId);
      }
      return next;
    });
  }, []);

  const selectedStart = selectedPages.size > 0 ? Math.min(...selectedPages) : null;
  const selectedEnd = selectedPages.size > 0 ? Math.max(...selectedPages) : null;

  const handleExtract = useCallback(async () => {
    if (!file || selectedPages.size === 0 || !sectionTitle.trim() || !selectedStart || !selectedEnd) return;
    setExtracting(true);
    setError(null);
    try {
      await extractSection(
        bankId,
        file,
        selectedStart,
        selectedEnd,
        sectionTitle.trim(),
        [...selectedProductIds],
      );
      setCreatedSections((prev) => [
        ...prev,
        { title: sectionTitle.trim(), startPage: selectedStart, endPage: selectedEnd, productIds: [...selectedProductIds] },
      ]);
      setSelectedPages(new Set());
      setSectionTitle("");
      await loadCatalog();
    } catch (e) {
      setError((e as Error).message || "Az extractálás sikertelen.");
    } finally {
      setExtracting(false);
    }
  }, [file, selectedPages, sectionTitle, selectedStart, selectedEnd, selectedProductIds, bankId, loadCatalog]);

  const handleDeleteSection = useCallback(async (docId: string) => {
    try {
      await deleteCatalogDocument(docId);
      setCreatedSections((prev) => {
        // Remove from the local "created" list too if present (by matching
        // the docId pattern section_<bank>_<stem>).
        return prev;
      });
      await loadCatalog();
    } catch (e) {
      setError((e as Error).message || "A törlés sikertelen.");
    }
  }, [deleteCatalogDocument, loadCatalog]);

  const productName = (pid: string) => {
    for (const b of catalog?.banks ?? []) {
      const p = b.products.find((pr) => pr.id === pid);
      if (p) return p.name;
    }
    return pid;
  };

  // Existing documents for this bank's products (from catalog)
  const existingDocs: CatalogDocument[] = (catalog?.documents ?? []).filter((d) =>
    d.file_path?.includes(`documents/${bankId}/sections/`),
  );

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
        padding: "14px 24px",
        borderBottom: "1px solid var(--border-subtle)",
        background: "var(--bg-secondary)",
        flexShrink: 0,
      }}>
        <div>
          <h2 style={{ fontSize: "1.05rem", fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
            Manuális szekció szerkesztő
          </h2>
          <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)", margin: "2px 0 0" }}>
            {bank?.name} — jelölj ki oldalakat és rendeld termékekhez
          </p>
        </div>
        <button className="btn btn-ghost" onClick={onClose}>Bezaras</button>
      </header>

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        {/* Left: page thumbnails */}
        <div style={{
          flex: 1,
          overflow: "auto",
          padding: "var(--space-lg)",
        }}>
          {/* File selector */}
          {!pdfId && (
            <div style={{ marginBottom: "var(--space-lg)" }}>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,application/pdf"
                onChange={handleFileChange}
                style={{ display: "none" }}
              />
              <button
                className="btn btn-primary"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
              >
                {uploading ? "Feltöltés…" : file ? "Újra feltöltés" : "+ Master PDF kiválasztása"}
              </button>
            </div>
          )}

          {uploading && (
            <p style={{ color: "var(--text-tertiary)", fontSize: "0.85rem" }}>Master PDF feldolgozása…</p>
          )}

          {/* Thumbnail grid */}
          {pdfId && pageCount > 0 && (
            <>
              <div style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                marginBottom: "var(--space-md)",
              }}>
                <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", margin: 0 }}>
                  {pageCount} oldal — kattints az oldalakra a kijelöléshez
                </p>
                {selectedPages.size > 0 && (
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setSelectedPages(new Set())}
                  >
                    Kijelölés törlése
                  </button>
                )}
              </div>

              <div style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
                gap: "var(--space-sm)",
              }}>
                {Array.from({ length: pageCount }, (_, i) => i + 1).map((page) => {
                  const isSelected = selectedPages.has(page);
                  return (
                    <div
                      key={page}
                      onClick={() => togglePage(page)}
                      style={{
                        position: "relative",
                        border: isSelected
                          ? "3px solid var(--accent-blue)"
                          : "1px solid var(--border-subtle)",
                        borderRadius: "var(--radius-sm)",
                        overflow: "hidden",
                        cursor: "pointer",
                        background: "var(--bg-secondary)",
                        transition: "border-color var(--transition-fast)",
                      }}
                    >
                      <img
                        src={pageImageUrl(pdfId, page)}
                        alt={`Oldal ${page}`}
                        style={{ width: "100%", display: "block" }}
                        loading="lazy"
                      />
                      <div style={{
                        position: "absolute",
                        top: 4,
                        left: 4,
                        background: isSelected ? "var(--accent-blue)" : "rgba(0,0,0,0.6)",
                        color: "white",
                        fontSize: "0.65rem",
                        fontWeight: 600,
                        padding: "1px 6px",
                        borderRadius: "var(--radius-sm)",
                      }}>
                        {page}
                      </div>
                      {isSelected && (
                        <div style={{
                          position: "absolute",
                          top: 4,
                          right: 4,
                          width: 18,
                          height: 18,
                          borderRadius: "50%",
                          background: "var(--accent-blue)",
                          color: "white",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          fontSize: "0.7rem",
                          fontWeight: 700,
                        }}>+</div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}

          {error && (
            <div style={{
              marginTop: "var(--space-md)",
              padding: "var(--space-sm) var(--space-md)",
              background: "var(--accent-red-glow)",
              borderRadius: "var(--radius-md)",
              color: "var(--accent-red)",
              fontSize: "0.85rem",
            }}>
              {error}
            </div>
          )}
        </div>

        {/* Right: extraction panel */}
        {pdfId && (
          <aside style={{
            width: 320,
            flexShrink: 0,
            borderLeft: "1px solid var(--border-subtle)",
            background: "var(--bg-secondary)",
            overflow: "auto",
            padding: "var(--space-lg)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-md)",
          }}>
            <div>
              <h3 style={{
                fontSize: "0.8rem",
                fontWeight: 600,
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                color: "var(--text-secondary)",
                margin: "0 0 var(--space-sm)",
              }}>
                Kiválasztott oldalak
              </h3>
              {selectedPages.size === 0 ? (
                <p style={{ fontSize: "0.8rem", color: "var(--text-tertiary)", margin: 0 }}>
                  Még nincs oldal kijelölve. Kattints a bal oldalon.
                </p>
              ) : (
                <div style={{
                  padding: "var(--space-sm) var(--space-md)",
                  background: "var(--bg-primary)",
                  borderRadius: "var(--radius-md)",
                  fontSize: "0.85rem",
                  color: "var(--text-primary)",
                }}>
                  <strong>{selectedPages.size} oldal</strong>
                  {selectedStart && selectedEnd && (
                    <span style={{ color: "var(--text-tertiary)" }}> ({selectedStart}–{selectedEnd})</span>
                  )}
                </div>
              )}
            </div>

            <div>
              <label style={{
                display: "block",
                fontSize: "0.8rem",
                fontWeight: 600,
                color: "var(--text-secondary)",
                marginBottom: "var(--space-xs)",
              }}>
                Szekció neve
              </label>
              <input
                type="text"
                value={sectionTitle}
                onChange={(e) => setSectionTitle(e.target.value)}
                placeholder="pl. CSOK Támogatáskérelmi adatlap"
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-default)",
                  borderRadius: "var(--radius-md)",
                  color: "var(--text-primary)",
                  fontSize: "0.85rem",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            <div>
              <label style={{
                display: "block",
                fontSize: "0.8rem",
                fontWeight: 600,
                color: "var(--text-secondary)",
                marginBottom: "var(--space-xs)",
              }}>
                Termékek
              </label>
              <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                {bank?.products.map((product) => (
                  <label
                    key={product.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      padding: "6px var(--space-sm)",
                      borderRadius: "var(--radius-sm)",
                      cursor: "pointer",
                      background: selectedProductIds.has(product.id) ? "var(--accent-blue-glow)" : "transparent",
                      fontSize: "0.82rem",
                      color: "var(--text-primary)",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selectedProductIds.has(product.id)}
                      onChange={() => toggleProduct(product.id)}
                    />
                    {product.name}
                  </label>
                ))}
              </div>
            </div>

            <button
              className="btn btn-primary"
              onClick={handleExtract}
              disabled={extracting || selectedPages.size === 0 || !sectionTitle.trim()}
              style={{ width: "100%" }}
            >
              {extracting ? "Extractálás…" : "Szekcio letrehozasa"}
            </button>

            {createdSections.length > 0 && (
              <div>
                <h3 style={{
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--text-secondary)",
                  margin: "var(--space-md) 0 var(--space-sm)",
                }}>
                  Létrehozott szekciók ({createdSections.length})
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {createdSections.map((s, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "var(--space-sm)",
                        background: "var(--bg-primary)",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.78rem",
                      }}
                    >
                      <div style={{ fontWeight: 500, color: "var(--text-primary)" }}>{s.title}</div>
                      <div style={{ color: "var(--text-tertiary)", marginTop: "2px" }}>
                        {s.startPage}–{s.endPage}. oldal
                      </div>
                      {s.productIds.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "3px", marginTop: "4px" }}>
                          {s.productIds.map((pid) => (
                            <span key={pid} className="badge badge-blue" style={{ fontSize: "0.6rem", padding: "1px 5px" }}>
                              {productName(pid)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            {existingDocs.length > 0 && (
              <div>
                <h3 style={{
                  fontSize: "0.8rem",
                  fontWeight: 600,
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--text-secondary)",
                  margin: "var(--space-md) 0 var(--space-sm)",
                }}>
                  Korábbi szekciók ({existingDocs.length})
                </h3>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  {existingDocs.map((doc) => (
                    <div
                      key={doc.id}
                      style={{
                        padding: "var(--space-sm)",
                        background: "var(--bg-primary)",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.78rem",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "var(--space-xs)" }}>
                        <span style={{ fontWeight: 500, color: "var(--text-primary)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {doc.title}
                        </span>
                        <button
                          onClick={() => handleDeleteSection(doc.id)}
                          title="Szekció törlése"
                          style={{
                            background: "none",
                            border: "none",
                            color: "var(--text-tertiary)",
                            cursor: "pointer",
                            padding: "0 2px",
                            fontSize: "0.8rem",
                            lineHeight: 1,
                            flexShrink: 0,
                          }}
                        >
                          X
                        </button>
                      </div>
                      {doc.product_ids.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "3px", marginTop: "4px" }}>
                          {doc.product_ids.map((pid) => (
                            <span key={pid} className="badge badge-blue" style={{ fontSize: "0.6rem", padding: "1px 5px" }}>
                              {productName(pid)}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}

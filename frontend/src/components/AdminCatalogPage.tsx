import { useState, useMemo, useCallback } from "react";
import { useStore } from "@/store";
import type { AdminTab, CatalogDocument } from "@/types";
import BankSetupDialog from "./BankSetupDialog";
import ProductAssociationDialog from "./ProductAssociationDialog";

/**
 * Phase 6 — Admin Catalog Page.
 *
 * Two tabs:
 *   - Bankok:     table of banks with product count + delete (cascade).
 *   - Dokumentumok: table of all catalog documents with associations, tags,
 *                  and per_applicant flag. Edit opens ProductAssociationDialog;
 *                  Delete removes from catalog only (not disk).
 */
export default function AdminCatalogPage() {
  const catalog = useStore((s) => s.catalog);
  const deleteBank = useStore((s) => s.deleteBank);
  const deleteCatalogDocument = useStore((s) => s.deleteCatalogDocument);
  const setPerApplicant = useStore((s) => s.setPerApplicant);

  const [tab, setTab] = useState<AdminTab>("banks");
  const [search, setSearch] = useState("");
  const [showBankDialog, setShowBankDialog] = useState(false);

  // Delete confirmation state
  const [confirmBankId, setConfirmBankId] = useState<string | null>(null);
  const [confirmDocId, setConfirmDocId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  // Edit association state
  const [editingDoc, setEditingDoc] = useState<{
    id: string;
    title: string;
    productIds: string[];
  } | null>(null);

  // -- Product name lookup map (product_id -> "Bank / Product") --
  const productNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const bank of catalog?.banks ?? []) {
      for (const prod of bank.products) {
        m.set(prod.id, `${bank.name} / ${prod.name}`);
      }
    }
    return m;
  }, [catalog]);

  const banks = catalog?.banks ?? [];
  const documents = catalog?.documents ?? [];

  const filteredDocs = useMemo(() => {
    if (!search.trim()) return documents;
    const q = search.toLowerCase();
    return documents.filter((d) => d.title.toLowerCase().includes(q));
  }, [documents, search]);

  // -- Handlers --
  const handleDeleteBank = useCallback(
    async (bankId: string) => {
      setDeleting(true);
      setDeleteError(null);
      try {
        await deleteBank(bankId);
        setConfirmBankId(null);
      } catch (e) {
        setDeleteError((e as Error).message || "Törlés sikertelen.");
      } finally {
        setDeleting(false);
      }
    },
    [deleteBank],
  );

  const handleDeleteDoc = useCallback(
    async (docId: string) => {
      setDeleting(true);
      setDeleteError(null);
      try {
        await deleteCatalogDocument(docId);
        setConfirmDocId(null);
      } catch (e) {
        setDeleteError((e as Error).message || "Törlés sikertelen.");
      } finally {
        setDeleting(false);
      }
    },
    [deleteCatalogDocument],
  );

  // -- Styles --
  const tabButtonStyle = (active: boolean): React.CSSProperties => ({
    padding: "8px 18px",
    fontSize: "0.85rem",
    fontWeight: active ? 600 : 400,
    color: active ? "var(--text-primary)" : "var(--text-secondary)",
    background: active ? "var(--bg-tertiary)" : "transparent",
    border: "1px solid var(--border-subtle)",
    borderBottom: active ? "1px solid var(--bg-tertiary)" : "1px solid var(--border-subtle)",
    borderRadius: "8px 8px 0 0",
    cursor: "pointer",
    transition: "all 0.15s",
    marginBottom: "-1px",
  });

  const tableStyle: React.CSSProperties = {
    width: "100%",
    borderCollapse: "collapse",
    fontSize: "0.85rem",
  };

  const thStyle: React.CSSProperties = {
    textAlign: "left",
    padding: "10px 14px",
    color: "var(--text-tertiary)",
    fontWeight: 500,
    fontSize: "0.75rem",
    textTransform: "uppercase",
    letterSpacing: "0.04em",
    borderBottom: "1px solid var(--border-subtle)",
    whiteSpace: "nowrap",
  };

  const tdStyle: React.CSSProperties = {
    padding: "12px 14px",
    color: "var(--text-primary)",
    borderBottom: "1px solid var(--border-subtle)",
    verticalAlign: "middle",
  };

  const badgeStyle: React.CSSProperties = {
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "0.72rem",
    fontWeight: 500,
  };

  const actionBtnStyle: React.CSSProperties = {
    padding: "4px 10px",
    fontSize: "0.78rem",
    borderRadius: "var(--radius-md)",
    cursor: "pointer",
    border: "1px solid var(--border-default)",
    background: "transparent",
    color: "var(--text-secondary)",
    transition: "all 0.15s",
  };

  return (
    <div style={{ padding: "var(--space-xl)", maxWidth: 1100, margin: "0 auto" }}>
      {/* Tabs */}
      <div style={{ display: "flex", gap: "4px", borderBottom: "1px solid var(--border-subtle)", marginBottom: "var(--space-lg)" }}>
        <button style={tabButtonStyle(tab === "banks")} onClick={() => setTab("banks")}>
          Bankok ({banks.length})
        </button>
        <button style={tabButtonStyle(tab === "documents")} onClick={() => setTab("documents")}>
          Dokumentumok ({documents.length})
        </button>
      </div>

      {/* --- BANKOK TAB --- */}
      {tab === "banks" && (
        <div className="animate-fade-in">
          <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "var(--space-md)" }}>
            <button className="btn btn-primary" onClick={() => setShowBankDialog(true)}>
              + Új bank
            </button>
          </div>

          {banks.length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: "0.9rem", textAlign: "center", padding: "var(--space-2xl)" }}>
              Még nincsenek bankok a katalógusban.
            </p>
          ) : (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Név</th>
                  <th style={thStyle}>Slug</th>
                  <th style={thStyle}>Létrehozva</th>
                  <th style={thStyle}>Termékek</th>
                  <th style={thStyle}></th>
                </tr>
              </thead>
              <tbody>
                {banks.map((bank) => (
                  <tr key={bank.id}>
                    <td style={tdStyle}>{bank.name}</td>
                    <td style={{ ...tdStyle, color: "var(--text-tertiary)", fontFamily: "monospace", fontSize: "0.78rem" }}>
                      {bank.id}
                    </td>
                    <td style={{ ...tdStyle, color: "var(--text-secondary)" }}>
                      {bank.created_at ?? "—"}
                    </td>
                    <td style={tdStyle}>
                      <span style={{ ...badgeStyle, background: "var(--accent-blue-glow)", color: "var(--accent-blue)" }}>
                        {bank.products.length} termék
                      </span>
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>
                      {confirmBankId === bank.id ? (
                        <span style={{ display: "flex", gap: "6px", justifyContent: "flex-end", alignItems: "center" }}>
                          <span style={{ fontSize: "0.76rem", color: "var(--accent-red)" }}>Biztos?</span>
                          <button
                            className="btn"
                            style={{ ...actionBtnStyle, color: "var(--accent-red)", borderColor: "var(--accent-red)" }}
                            disabled={deleting}
                            onClick={() => handleDeleteBank(bank.id)}
                          >
                            Igen, törlés
                          </button>
                          <button
                            className="btn"
                            style={actionBtnStyle}
                            disabled={deleting}
                            onClick={() => { setConfirmBankId(null); setDeleteError(null); }}
                          >
                            Mégse
                          </button>
                        </span>
                      ) : (
                        <button
                          className="btn"
                          style={{ ...actionBtnStyle, color: "var(--accent-red)" }}
                          onClick={() => setConfirmBankId(bank.id)}
                        >
                          Törlés
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {confirmBankId && (
            <div
              style={{
                marginTop: "var(--space-md)",
                padding: "var(--space-sm) var(--space-md)",
                background: "var(--accent-red-glow)",
                borderRadius: "var(--radius-md)",
                color: "var(--accent-red)",
                fontSize: "0.8rem",
              }}
            >
              Biztos törli? A bank összes terméke és kapcsolatai törlődnek. A fizikai fájlok a lemezen maradnak.
            </div>
          )}
        </div>
      )}

      {/* --- DOKUMENTUMOK TAB --- */}
      {tab === "documents" && (
        <div className="animate-fade-in">
          {/* Search */}
          <div style={{ marginBottom: "var(--space-md)" }}>
            <input
              type="text"
              placeholder="Keresés cím szerint…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                width: "100%",
                maxWidth: 360,
                padding: "8px 12px",
                fontSize: "0.85rem",
                background: "var(--bg-primary)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                color: "var(--text-primary)",
                outline: "none",
                boxSizing: "border-box",
              }}
            />
          </div>

          {filteredDocs.length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: "0.9rem", textAlign: "center", padding: "var(--space-2xl)" }}>
              {documents.length === 0
                ? "Még nincsenek dokumentumok a katalógusban."
                : "Nincs találat a keresésre."}
            </p>
          ) : (
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Cím</th>
                  <th style={thStyle}>Forrás</th>
                  <th style={thStyle}>Oldal</th>
                  <th style={thStyle}>Adosanként</th>
                  <th style={thStyle}>Termékek</th>
                  <th style={thStyle}>Címkék</th>
                  <th style={thStyle}></th>
                </tr>
              </thead>
              <tbody>
                {filteredDocs.map((doc: CatalogDocument) => (
                  <tr key={doc.id}>
                    <td style={tdStyle}>{doc.title}</td>
                    <td style={{ ...tdStyle, color: "var(--text-tertiary)", fontSize: "0.78rem" }}>
                      {doc.source || "—"}
                    </td>
                    <td style={tdStyle}>{doc.page_count}</td>
                    <td style={tdStyle}>
                      <button
                        onClick={() => setPerApplicant(doc.id, !doc.per_applicant)}
                        title={doc.per_applicant ? "Adósonként kitöltendő — kattints a kikapcsoláshoz" : "Kattints az adósonkénti kitöltés bekapcsolásához"}
                        style={{
                          ...badgeStyle,
                          cursor: "pointer",
                          border: "none",
                          background: doc.per_applicant ? "var(--accent-green-glow)" : "var(--bg-tertiary)",
                          color: doc.per_applicant ? "var(--accent-green)" : "var(--text-tertiary)",
                        }}
                      >
                        {doc.per_applicant ? "👤 Igen" : "—"}
                      </button>
                    </td>
                    <td style={{ ...tdStyle, maxWidth: 240 }}>
                      {doc.product_ids.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {doc.product_ids.map((pid) => (
                            <span
                              key={pid}
                              style={{ ...badgeStyle, background: "var(--bg-tertiary)", color: "var(--text-secondary)" }}
                            >
                              {productNameMap.get(pid) ?? pid}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span style={{ color: "var(--text-tertiary)" }}>—</span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      {doc.tags && doc.tags.length > 0 ? (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
                          {doc.tags.map((tag) => (
                            <span
                              key={tag}
                              style={{ ...badgeStyle, background: "var(--accent-amber-glow)", color: "var(--accent-amber)" }}
                            >
                              {tag}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span style={{ color: "var(--text-tertiary)" }}>—</span>
                      )}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", whiteSpace: "nowrap" }}>
                      <div style={{ display: "flex", gap: "6px", justifyContent: "flex-end" }}>
                        <button
                          className="btn"
                          style={actionBtnStyle}
                          onClick={() =>
                            setEditingDoc({
                              id: doc.id,
                              title: doc.title,
                              productIds: doc.product_ids,
                            })
                          }
                        >
                          Szerkesztés
                        </button>
                        {confirmDocId === doc.id ? (
                          <>
                            <button
                              className="btn"
                              style={{ ...actionBtnStyle, color: "var(--accent-red)", borderColor: "var(--accent-red)" }}
                              disabled={deleting}
                              onClick={() => handleDeleteDoc(doc.id)}
                            >
                              Igen
                            </button>
                            <button
                              className="btn"
                              style={actionBtnStyle}
                              disabled={deleting}
                              onClick={() => { setConfirmDocId(null); setDeleteError(null); }}
                            >
                              Nem
                            </button>
                          </>
                        ) : (
                          <button
                            className="btn"
                            style={{ ...actionBtnStyle, color: "var(--accent-red)" }}
                            onClick={() => setConfirmDocId(doc.id)}
                          >
                            Törlés
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {confirmDocId && (
            <div
              style={{
                marginTop: "var(--space-md)",
                padding: "var(--space-sm) var(--space-md)",
                background: "var(--accent-red-glow)",
                borderRadius: "var(--radius-md)",
                color: "var(--accent-red)",
                fontSize: "0.8rem",
              }}
            >
              A dokumentum csak a katalógusból kerül törlésre — a fájl a lemezen marad.
            </div>
          )}
        </div>
      )}

      {/* Global delete error */}
      {deleteError && (
        <div
          style={{
            marginTop: "var(--space-md)",
            padding: "var(--space-sm) var(--space-md)",
            background: "var(--accent-red-glow)",
            borderRadius: "var(--radius-md)",
            color: "var(--accent-red)",
            fontSize: "0.8rem",
          }}
        >
          {deleteError}
        </div>
      )}

      {/* Dialogs */}
      {showBankDialog && <BankSetupDialog onClose={() => setShowBankDialog(false)} />}
      {editingDoc && (
        <ProductAssociationDialog
          docId={editingDoc.id}
          docTitle={editingDoc.title}
          currentProductIds={editingDoc.productIds}
          onClose={() => setEditingDoc(null)}
        />
      )}
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/store";

interface ProductAssociationDialogProps {
  docId: string;
  docTitle: string;
  currentProductIds: string[];
  onClose: () => void;
}

export default function ProductAssociationDialog({
  docId,
  docTitle,
  currentProductIds,
  onClose,
}: ProductAssociationDialogProps) {
  const catalog = useStore((s) => s.catalog);
  const associateDocumentWithProduct = useStore((s) => s.associateDocumentWithProduct);
  const [selected, setSelected] = useState<Set<string>>(new Set(currentProductIds));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = useCallback((pid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    try {
      await associateDocumentWithProduct(docId, [...selected]);
      onClose();
    } catch (e) {
      setError((e as Error).message || "A mentés sikertelen.");
    } finally {
      setSaving(false);
    }
  }, [docId, selected, associateDocumentWithProduct, onClose]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saving, onClose]);

  const banks = catalog?.banks ?? [];

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
      onClick={() => !saving && onClose()}
    >
      <div
        className="animate-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-secondary)",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border-subtle)",
          padding: "24px",
          maxWidth: 480,
          width: "90%",
          maxHeight: "70vh",
          overflowY: "auto",
          boxShadow: "0 20px 60px rgba(0, 0, 0, 0.4)",
        }}
      >
        <h3
          style={{
            fontSize: "1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            margin: "0 0 var(--space-xs) 0",
          }}
        >
          Termékek társítása
        </h3>
        <p style={{ fontSize: "0.8rem", color: "var(--text-secondary)", margin: "0 0 var(--space-md) 0" }}>
          {docTitle}
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
          {banks.length === 0 && (
            <p style={{ fontSize: "0.85rem", color: "var(--text-tertiary)" }}>
              Még nincsenek bankok és termékek.
            </p>
          )}
          {banks.map((bank) => (
            <div key={bank.id}>
              <p
                style={{
                  fontSize: "0.75rem",
                  textTransform: "uppercase",
                  letterSpacing: "0.05em",
                  color: "var(--text-tertiary)",
                  marginBottom: "var(--space-xs)",
                }}
              >
                {bank.name}
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: "var(--space-xs)" }}>
                {bank.products.length === 0 && (
                  <span style={{ fontSize: "0.8rem", color: "var(--text-tertiary)" }}>
                    Nincs termék
                  </span>
                )}
                {bank.products.map((prod) => (
                  <label
                    key={prod.id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "var(--space-sm)",
                      cursor: "pointer",
                      padding: "6px 10px",
                      borderRadius: "var(--radius-md)",
                      background: selected.has(prod.id) ? "var(--accent-blue-glow, rgba(59,130,246,0.1))" : "transparent",
                      transition: "background 0.15s",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={selected.has(prod.id)}
                      onChange={() => toggle(prod.id)}
                      disabled={saving}
                      style={{ cursor: "pointer", accentColor: "var(--accent-blue)" }}
                    />
                    <span style={{ fontSize: "0.85rem", color: "var(--text-primary)" }}>
                      {prod.name}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>

        {error && (
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
            {error}
          </div>
        )}

        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "var(--space-lg)" }}>
          <button
            className="btn btn-ghost"
            onClick={onClose}
            disabled={saving}
            style={{ padding: "8px 16px", fontSize: "0.85rem" }}
          >
            Mégse
          </button>
          <button
            className="btn btn-primary"
            onClick={handleSave}
            disabled={saving}
            style={{ padding: "8px 16px", fontSize: "0.85rem" }}
          >
            {saving ? "Mentés…" : "Mentés"}
          </button>
        </div>
      </div>
    </div>
  );
}

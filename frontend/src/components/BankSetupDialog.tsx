import { useState, useEffect, useCallback } from "react";
import { useStore } from "@/store";

interface BankSetupDialogProps {
  onClose: () => void;
  onCreated?: (bankId: string) => void;
}

export default function BankSetupDialog({ onClose, onCreated }: BankSetupDialogProps) {
  const createBank = useStore((s) => s.createBank);
  const [name, setName] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = useCallback(async () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("A bank neve kötelező.");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const bankId = await createBank(trimmed);
      onCreated?.(bankId);
      onClose();
    } catch (e) {
      setError((e as Error).message || "A bank létrehozása sikertelen.");
    } finally {
      setCreating(false);
    }
  }, [name, createBank, onCreated, onClose]);

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !creating) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [creating, onClose]);

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
      onClick={() => !creating && onClose()}
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
        <h3
          style={{
            fontSize: "1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            margin: "0 0 var(--space-md) 0",
          }}
        >
          Új bank létrehozása
        </h3>

        <div style={{ marginBottom: "var(--space-md)" }}>
          <label
            htmlFor="bank-name"
            style={{
              display: "block",
              fontSize: "0.8rem",
              color: "var(--text-secondary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            Bank neve
          </label>
          <input
            id="bank-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !creating) handleCreate();
            }}
            autoFocus
            placeholder="pl. OTP Bank"
            disabled={creating}
            style={{
              width: "100%",
              padding: "10px 12px",
              fontSize: "0.9rem",
              background: "var(--bg-primary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              color: "var(--text-primary)",
              outline: "none",
              boxSizing: "border-box",
            }}
          />
        </div>

        {error && (
          <div
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

        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
          <button
            className="btn btn-ghost"
            onClick={onClose}
            disabled={creating}
            style={{ padding: "8px 16px", fontSize: "0.85rem" }}
          >
            Mégse
          </button>
          <button
            className="btn btn-primary"
            onClick={handleCreate}
            disabled={creating || !name.trim()}
            style={{ padding: "8px 16px", fontSize: "0.85rem" }}
          >
            {creating ? "Létrehozás…" : "Létrehozás"}
          </button>
        </div>
      </div>
    </div>
  );
}

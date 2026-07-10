import { useState } from "react";
import { useStore } from "@/store";

interface EmptyStateProps {
  onAddBank: () => void;
}

export default function EmptyState({ onAddBank }: EmptyStateProps) {
  const quickStartOTP = useStore((s) => s.quickStartOTP);
  const [quickStarting, setQuickStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleQuickStart = async () => {
    setQuickStarting(true);
    setError(null);
    try {
      await quickStartOTP();
    } catch (e) {
      setError((e as Error).message || "A Quick Start sikertelen.");
    } finally {
      setQuickStarting(false);
    }
  };

  return (
    <div
      className="animate-fade-in"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: "var(--space-xxl) var(--space-xl)",
        textAlign: "center",
        gap: "var(--space-lg)",
      }}
    >
      <svg
        width="56"
        height="56"
        viewBox="0 0 24 24"
        fill="none"
        stroke="var(--text-tertiary)"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M3 21h18" />
        <path d="M3 7v14" />
        <path d="M21 7v14" />
        <path d="M3 7l9-4 9 4" />
        <path d="M9 21v-8h6v8" />
      </svg>

      <div>
        <p
          style={{
            fontSize: "1rem",
            fontWeight: 500,
            color: "var(--text-primary)",
            marginBottom: "var(--space-xs)",
          }}
        >
          Még nincs bank definiálva.
        </p>
        <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
          Hozzon létre egyet, vagy használja a Quick Start-ot.
        </p>
      </div>

      {error && (
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            background: "var(--accent-red-glow)",
            borderRadius: "var(--radius-md)",
            color: "var(--accent-red)",
            fontSize: "0.8rem",
            maxWidth: 360,
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "var(--space-sm)", flexWrap: "wrap", justifyContent: "center" }}>
        <button
          className="btn btn-primary"
          onClick={handleQuickStart}
          disabled={quickStarting}
        >
          {quickStarting ? (
            <>
              <span
                style={{
                  width: 14,
                  height: 14,
                  border: "2px solid rgba(255,255,255,0.3)",
                  borderTopColor: "white",
                  borderRadius: "50%",
                  display: "inline-block",
                  animation: "spin 0.8s linear infinite",
                  marginRight: 6,
                }}
              />
              Quick Start…
            </>
          ) : (
            <>Quick Start: OTP</>
          )}
        </button>
        <button
          className="btn btn-ghost"
          onClick={onAddBank}
          disabled={quickStarting}
        >
          + Bank hozzáadása
        </button>
      </div>

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

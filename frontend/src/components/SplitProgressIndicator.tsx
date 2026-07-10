import { useEffect, useState, useRef, useCallback } from "react";
import { useStore } from "@/store";

interface SplitProgressFile {
  filename: string;
  section: string;
  page_start: number;
  page_end: number;
  page_count: number;
  is_base: boolean;
}

interface SplitProgress {
  split_id: string;
  status: "pending" | "processing" | "done" | "error";
  progress: number;
  total_pages: number;
  done_pages: number;
  error?: string | null;
  output_files?: SplitProgressFile[];
}

interface SplitProgressIndicatorProps {
  splitId: string;
  onComplete: () => void;
}

export default function SplitProgressIndicator({
  splitId,
  onComplete,
}: SplitProgressIndicatorProps) {
  const [progress, setProgress] = useState<SplitProgress | null>(null);
  const loadCatalog = useStore((s) => s.loadCatalog);
  const completedRef = useRef(false);

  const poll = useCallback(async () => {
    try {
      const res = await fetch(
        `/api/catalog/split/${encodeURIComponent(splitId)}`,
      );
      if (!res.ok) {
        setProgress({
          split_id: splitId,
          status: "error",
          progress: 0,
          total_pages: 0,
          done_pages: 0,
          error: `HTTP ${res.status}`,
        });
        return;
      }
      const data: SplitProgress = await res.json();
      setProgress(data);

      if (
        (data.status === "done" || data.status === "error") &&
        !completedRef.current
      ) {
        completedRef.current = true;
        if (data.status === "done") {
          await loadCatalog();
          onComplete();
        }
      }
    } catch (e) {
      console.error("Split poll error:", e);
    }
  }, [splitId, loadCatalog, onComplete]);

  useEffect(() => {
    completedRef.current = false;
    poll();
    const interval = setInterval(poll, 1000);
    return () => clearInterval(interval);
  }, [splitId]); // eslint-disable-line react-hooks/exhaustive-deps

  const status = progress?.status ?? "pending";
  const pct = progress?.progress ?? 0;
  const total = progress?.total_pages ?? 0;
  const done = progress?.done_pages ?? 0;
  const files = progress?.output_files ?? [];

  return (
    <div
      className="animate-fade-in"
      style={{
        padding: "var(--space-lg)",
        background: "var(--bg-secondary)",
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border-subtle)",
        marginBottom: "var(--space-lg)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          marginBottom: "var(--space-md)",
        }}
      >
        {status === "error" ? (
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--accent-red)"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <circle cx="12" cy="12" r="10" />
            <line x1="15" y1="9" x2="9" y2="15" />
            <line x1="9" y1="9" x2="15" y2="15" />
          </svg>
        ) : status === "done" ? (
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--accent-green, #22c55e)"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
            <polyline points="22 4 12 14.01 9 11.01" />
          </svg>
        ) : (
          <span
            style={{
              width: 18,
              height: 18,
              border: "2px solid var(--border-subtle)",
              borderTopColor: "var(--accent-blue)",
              borderRadius: "50%",
              display: "inline-block",
              animation: "spin 0.8s linear infinite",
            }}
          />
        )}
        <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--text-primary)" }}>
          {status === "done"
            ? "Darabolás befejezve"
            : status === "error"
              ? "Darabolás sikertelen"
              : "Master PDF darabolása…"}
        </span>
      </div>

      {/* Progress bar */}
      {status !== "done" && status !== "error" && (
        <>
          <div
            style={{
              height: 8,
              background: "var(--bg-primary)",
              borderRadius: 4,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${pct}%`,
                height: "100%",
                background: "var(--accent-blue)",
                borderRadius: 4,
                transition: "width 0.3s ease",
              }}
            />
          </div>
          <p
            style={{
              fontSize: "0.75rem",
              color: "var(--text-tertiary)",
              textAlign: "center",
              marginTop: "var(--space-xs)",
            }}
          >
            {done}/{total} oldal feldolgozva ({pct}%)
          </p>
        </>
      )}

      {/* Error message */}
      {status === "error" && progress?.error && (
        <p
          style={{
            fontSize: "0.8rem",
            color: "var(--accent-red)",
            marginTop: "var(--space-xs)",
          }}
        >
          {progress.error}
        </p>
      )}

      {/* Output files list */}
      {status === "done" && files.length > 0 && (
        <div style={{ marginTop: "var(--space-sm)" }}>
          <p
            style={{
              fontSize: "0.75rem",
              color: "var(--text-tertiary)",
              marginBottom: "var(--space-xs)",
            }}
          >
            {files.length} szekció létrehozva:
          </p>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              gap: "4px",
              maxHeight: 200,
              overflowY: "auto",
            }}
          >
            {files.map((f, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-xs)",
                  padding: "4px 8px",
                  background: "var(--bg-primary)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.75rem",
                }}
              >
                <span style={{ color: "var(--text-primary)", flex: 1 }}>
                  {f.section}
                </span>
                <span style={{ color: "var(--text-tertiary)" }}>
                  {f.page_start === f.page_end
                    ? `o.${f.page_start}`
                    : `o.${f.page_start}-${f.page_end}`}
                </span>
                {f.is_base && (
                  <span
                    className="badge badge-blue"
                    style={{ fontSize: "0.65rem", padding: "1px 6px" }}
                  >
                    base
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

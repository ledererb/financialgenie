import { useState, useEffect, useRef, useCallback } from "react";
import { startRecognition, recognizeStatus } from "@/api/client";
import type { RecognizeStatus } from "@/api/client";

interface AnalysisStepProps {
  pdfId: string;
  onComplete: () => void;
}

export default function AnalysisStep({ pdfId, onComplete }: AnalysisStepProps) {
  const [status, setStatus] = useState<RecognizeStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const taskIdRef = useRef<string | null>(null);
  // Prevent React 18 StrictMode double-fire
  const startedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const beginRecognition = useCallback(async () => {
    setStarting(true);
    setError(null);
    setStatus(null);

    try {
      const { task_id } = await startRecognition(pdfId, "auto");
      taskIdRef.current = task_id;
      setStarting(false);

      // Start polling
      pollRef.current = setInterval(async () => {
        try {
          const res = await recognizeStatus(task_id);
          setStatus(res);

          if (res.status === "done") {
            stopPolling();
            // Small delay so user can see 100 %
            setTimeout(() => onComplete(), 600);
          } else if (res.status === "error") {
            stopPolling();
            setError(res.error || "Recognition failed.");
          }
        } catch (e) {
          stopPolling();
          setError((e as Error).message || "Lost connection to server.");
        }
      }, 2000);
    } catch (e) {
      setStarting(false);
      setError((e as Error).message || "Failed to start recognition.");
    }
  }, [pdfId, onComplete, stopPolling]);

  // Auto-start on mount
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    beginRecognition();
    return () => stopPolling();
  }, [beginRecognition, stopPolling]);

  const handleRetry = useCallback(() => {
    stopPolling();
    startedRef.current = false;
    beginRecognition();
  }, [beginRecognition, stopPolling]);

  const progress = status?.progress ?? 0;
  const message = status?.message ?? (starting ? "Initializing AI recognition…" : "Processing…");
  const isRunning = !error && status?.status !== "done";

  return (
    <div className="animate-fade-in" style={{ maxWidth: 520, margin: "0 auto" }}>
      <div className="card" style={{ textAlign: "center", padding: "var(--space-2xl) var(--space-lg)" }}>
        {/* Spinner / status icon */}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            marginBottom: "var(--space-lg)",
          }}
        >
          {error ? (
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: "var(--accent-red-glow)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--accent-red)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="12" r="10" />
                <line x1="15" y1="9" x2="9" y2="15" />
                <line x1="9" y1="9" x2="15" y2="15" />
              </svg>
            </div>
          ) : status?.status === "done" ? (
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: "var(--accent-green-glow)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--accent-green)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <polyline points="20 6 9 17 4 12" />
              </svg>
            </div>
          ) : (
            <div
              className="animate-pulse-glow"
              style={{
                width: 56,
                height: 56,
                borderRadius: "50%",
                background: "var(--accent-blue-glow)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <svg
                width="28"
                height="28"
                viewBox="0 0 24 24"
                fill="none"
                stroke="var(--accent-blue)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                style={{ animation: "spin 2s linear infinite" }}
              >
                <path d="M12 2v4" />
                <path d="M12 18v4" />
                <path d="M4.93 4.93l2.83 2.83" />
                <path d="M16.24 16.24l2.83 2.83" />
                <path d="M2 12h4" />
                <path d="M18 12h4" />
                <path d="M4.93 19.07l2.83-2.83" />
                <path d="M16.24 7.76l2.83-2.83" />
              </svg>
            </div>
          )}
        </div>

        {/* Title */}
        <h2
          style={{
            fontSize: "1.1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            marginBottom: "var(--space-sm)",
          }}
        >
          {error
            ? "Recognition Failed"
            : status?.status === "done"
              ? "Analysis Complete"
              : "Analyzing PDF…"}
        </h2>

        {/* Status message */}
        <p
          style={{
            fontSize: "0.8rem",
            color: error ? "var(--accent-red)" : "var(--text-secondary)",
            marginBottom: "var(--space-lg)",
            lineHeight: 1.5,
          }}
        >
          {error || message}
        </p>

        {/* Progress bar */}
        {isRunning && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.max(progress, starting ? 5 : 0)}%` }}
              />
            </div>
            <p
              style={{
                fontSize: "0.7rem",
                color: "var(--text-tertiary)",
                marginTop: "var(--space-xs)",
                textAlign: "right",
              }}
            >
              {Math.round(progress)}%
            </p>
          </div>
        )}

        {/* Done progress bar (full) */}
        {status?.status === "done" && (
          <div style={{ marginBottom: "var(--space-md)" }}>
            <div className="progress-track">
              <div className="progress-fill" style={{ width: "100%" }} />
            </div>
          </div>
        )}

        {/* Retry button */}
        {error && (
          <button className="btn btn-primary" onClick={handleRetry}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="23 4 23 10 17 10" />
              <path d="M20.49 15a9 9 0 11-2.12-9.36L23 10" />
            </svg>
            Retry
          </button>
        )}
      </div>

      {/* Inline keyframes for the spinner */}
      <style>{`
        @keyframes spin {
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

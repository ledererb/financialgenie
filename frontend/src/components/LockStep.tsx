interface LockStepProps {
  pdfId: string;
  mappedCount: number;
  totalFields: number;
  onApprove: () => void;
  onBack: () => void;
}

export default function LockStep({
  pdfId,
  mappedCount,
  totalFields,
  onApprove,
  onBack,
}: LockStepProps) {
  const unmappedCount = totalFields - mappedCount;
  const pct = totalFields > 0 ? Math.round((mappedCount / totalFields) * 100) : 0;
  const allMapped = unmappedCount === 0 && totalFields > 0;

  return (
    <div className="animate-fade-in" style={{ maxWidth: 520, margin: "0 auto" }}>
      <div className="card" style={{ padding: "var(--space-2xl) var(--space-lg)" }}>
        {/* Header icon */}
        <div style={{ textAlign: "center", marginBottom: "var(--space-lg)" }}>
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: "50%",
              background: allMapped ? "var(--accent-green-glow)" : "var(--accent-amber-glow)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke={allMapped ? "var(--accent-green)" : "var(--accent-amber)"}
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0110 0v4" />
            </svg>
          </div>
        </div>

        {/* Title */}
        <h2
          style={{
            textAlign: "center",
            fontSize: "1.1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            marginBottom: "var(--space-xs)",
          }}
        >
          Approve & Lock Mapping
        </h2>
        <p
          style={{
            textAlign: "center",
            fontSize: "0.8rem",
            color: "var(--text-secondary)",
            marginBottom: "var(--space-lg)",
          }}
        >
          Review the summary below before finalizing.
        </p>

        {/* Stats row */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr 1fr",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-lg)",
          }}
        >
          <div className="card card-stat" style={{ padding: "var(--space-md)" }}>
            <div className="stat-value" style={{ color: "var(--accent-green)" }}>
              {mappedCount}
            </div>
            <div className="stat-label">Mapped</div>
          </div>
          <div className="card card-stat" style={{ padding: "var(--space-md)" }}>
            <div
              className="stat-value"
              style={{ color: unmappedCount > 0 ? "var(--accent-amber)" : "var(--text-tertiary)" }}
            >
              {unmappedCount}
            </div>
            <div className="stat-label">Unmapped</div>
          </div>
          <div className="card card-stat" style={{ padding: "var(--space-md)" }}>
            <div className="stat-value" style={{ color: "var(--accent-blue)" }}>
              {pct}%
            </div>
            <div className="stat-label">Coverage</div>
          </div>
        </div>

        {/* PDF ID */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "var(--space-sm)",
            marginBottom: "var(--space-lg)",
          }}
        >
          <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>PDF:</span>
          <span className="badge badge-purple">{pdfId}</span>
        </div>

        {/* Warning */}
        <div
          style={{
            padding: "var(--space-sm) var(--space-md)",
            background: "var(--accent-amber-glow)",
            borderRadius: "var(--radius-md)",
            border: "1px solid rgba(245, 158, 11, 0.2)",
            marginBottom: "var(--space-lg)",
          }}
        >
          <p
            style={{
              fontSize: "0.78rem",
              color: "var(--accent-amber)",
              lineHeight: 1.5,
              display: "flex",
              alignItems: "flex-start",
              gap: "var(--space-sm)",
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-amber)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ flexShrink: 0, marginTop: 2 }}
            >
              <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
            <span>
              Locking is <strong>permanent</strong>. Once approved, the mapping configuration
              cannot be edited. Make sure all fields are correctly mapped before proceeding.
            </span>
          </p>
        </div>

        {/* Action buttons */}
        <div
          style={{
            display: "flex",
            gap: "var(--space-md)",
            justifyContent: "center",
          }}
        >
          <button className="btn btn-ghost" onClick={onBack}>
            ← Back to Review
          </button>
          <button className="btn btn-success btn-lg" onClick={onApprove}>
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
            Approve & Lock
          </button>
        </div>
      </div>
    </div>
  );
}

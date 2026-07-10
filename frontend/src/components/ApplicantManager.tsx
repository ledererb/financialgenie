"use client";

import { useState } from "react";
import { useStore } from "@/store";

export function ApplicantManager() {
  const applicants = useStore((s) => s.applicants);
  const addApplicant = useStore((s) => s.addApplicant);
  const removeApplicant = useStore((s) => s.removeApplicant);

  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [confirmRemoveId, setConfirmRemoveId] = useState<string | null>(null);

  const handleAdd = () => {
    const trimmed = name.trim();
    if (!trimmed) return;
    addApplicant(trimmed);
    setName("");
    setAdding(false);
  };

  const handleCancel = () => {
    setName("");
    setAdding(false);
  };

  const handleRemove = (id: string) => {
    removeApplicant(id);
    setConfirmRemoveId(null);
  };

  return (
    <div
      style={{
        borderBottom: "1px solid var(--border-subtle)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "var(--space-sm) var(--space-md)",
        }}
      >
        <h3
          style={{
            fontSize: "0.75rem",
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            color: "var(--text-secondary)",
            margin: 0,
          }}
        >
          Igénylők
        </h3>
        {!adding && (
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setAdding(true)}
            style={{ padding: "3px 8px", fontSize: "0.7rem" }}
          >
            + Adóstárs
          </button>
        )}
      </div>

      {/* Applicant list */}
      <div
        style={{
          padding: "0 var(--space-md) var(--space-sm)",
          display: "flex",
          flexDirection: "column",
          gap: "2px",
        }}
      >
        {applicants.map((a) => (
          <div key={a.id}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-sm)",
                padding: "4px var(--space-sm)",
                borderRadius: "var(--radius-sm)",
              }}
            >
              <span style={{ fontSize: "0.8rem", flex: 1, color: "var(--text-primary)" }}>
                {a.name}
              </span>
              {a.role === "primary" ? (
                <span
                  className="badge badge-blue"
                  style={{ fontSize: "0.6rem", padding: "1px 6px" }}
                >
                  Főadós
                </span>
              ) : (
                <span
                  className="badge badge-amber"
                  style={{ fontSize: "0.6rem", padding: "1px 6px" }}
                >
                  Adóstárs
                </span>
              )}
              {a.role === "coapplicant" && (
                <button
                  onClick={() => setConfirmRemoveId(a.id)}
                  style={{
                    background: "none",
                    border: "none",
                    color: "var(--text-tertiary)",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                    padding: "0 2px",
                    lineHeight: 1,
                  }}
                  title="Törlés"
                >
                  ✕
                </button>
              )}
            </div>

            {/* Remove confirmation */}
            {confirmRemoveId === a.id && (
              <div
                style={{
                  margin: "var(--space-xs) var(--space-sm)",
                  padding: "var(--space-xs) var(--space-sm)",
                  background: "var(--accent-red-glow)",
                  borderRadius: "var(--radius-sm)",
                  border: "1px solid rgba(239,68,68,0.3)",
                }}
              >
                <p
                  style={{
                    fontSize: "0.7rem",
                    color: "var(--accent-red)",
                    margin: "0 0 var(--space-xs) 0",
                    lineHeight: 1.4,
                  }}
                >
                  Biztosan törli? A per-applicant dokumentumokat újra kell
                  tölteni.
                </p>
                <div style={{ display: "flex", gap: "var(--space-xs)" }}>
                  <button
                    className="btn btn-sm"
                    onClick={() => handleRemove(a.id)}
                    style={{
                      background: "var(--accent-red)",
                      color: "white",
                      fontSize: "0.7rem",
                      padding: "3px 10px",
                    }}
                  >
                    Igen
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setConfirmRemoveId(null)}
                    style={{ fontSize: "0.7rem", padding: "3px 10px" }}
                  >
                    Mégse
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}

        {/* Inline add form */}
        {adding && (
          <div
            style={{
              padding: "var(--space-xs) var(--space-sm)",
              display: "flex",
              gap: "var(--space-xs)",
            }}
          >
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAdd();
                if (e.key === "Escape") handleCancel();
              }}
              placeholder="Adóstárs neve"
              autoFocus
              style={{
                flex: 1,
                padding: "4px 8px",
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-default)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: "0.8rem",
                outline: "none",
              }}
            />
            <button
              className="btn btn-primary btn-sm"
              onClick={handleAdd}
              disabled={!name.trim()}
              style={{ padding: "4px 10px", fontSize: "0.7rem" }}
            >
              Hozzáadás
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={handleCancel}
              style={{ padding: "4px 10px", fontSize: "0.7rem" }}
            >
              ✕
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

export default ApplicantManager;

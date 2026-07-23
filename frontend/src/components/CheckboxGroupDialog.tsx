import { useState, useMemo } from "react";
import type { MappingField } from "@/types";

interface CheckboxGroupDialogProps {
  fields: MappingField[];
  onApply: (updates: { fieldName: string; checkboxGroup: NonNullable<MappingField["checkbox_group"]> }[]) => Promise<void>;
  onClose: () => void;
}

export default function CheckboxGroupDialog({ fields, onApply, onClose }: CheckboxGroupDialogProps) {
  const checkboxFields = useMemo(
    () => fields.filter((f) => f.field_type === "checkbox"),
    [fields],
  );

  // Pre-select fields that already share a checkbox_group, plus all unmapped
  // checkboxes. The user can toggle individual ones.
  const [selected, setSelected] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    for (const f of checkboxFields) {
      if (f.checkbox_group?.group_id) initial.add(f.pdf_field_name);
    }
    return initial;
  });

  // Gather existing group_ids for the datalist
  const existingGroupIds = useMemo(() => {
    const ids = new Set<string>();
    for (const f of checkboxFields) {
      if (f.checkbox_group?.group_id) ids.add(f.checkbox_group.group_id);
    }
    return Array.from(ids);
  }, [checkboxFields]);

  const [groupId, setGroupId] = useState(() => {
    // Pre-fill with the most common existing group_id among selected
    const counts: Record<string, number> = {};
    for (const f of checkboxFields) {
      const gid = f.checkbox_group?.group_id;
      if (gid && selected.has(f.pdf_field_name)) {
        counts[gid] = (counts[gid] || 0) + 1;
      }
    }
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return sorted[0]?.[0] ?? "";
  });

  const [groupLabel, setGroupLabel] = useState(() => {
    for (const f of checkboxFields) {
      if (selected.has(f.pdf_field_name) && f.checkbox_group?.group_label) {
        return f.checkbox_group.group_label;
      }
    }
    return "";
  });

  // option_value per field: pre-fill from existing, or auto-derive from label/name
  const [optionValues, setOptionValues] = useState<Record<string, string>>(() => {
    const vals: Record<string, string> = {};
    for (const f of checkboxFields) {
      vals[f.pdf_field_name] = f.checkbox_group?.option_value ?? "";
    }
    return vals;
  });

  const toggleField = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const selectAll = () => setSelected(new Set(checkboxFields.map((f) => f.pdf_field_name)));
  const selectNone = () => setSelected(new Set());

  const handleApply = async () => {
    if (!groupId.trim() || selected.size === 0) return;
    const updates = [];
    for (const name of selected) {
      const f = checkboxFields.find((cf) => cf.pdf_field_name === name);
      if (!f) continue;
      const ov = optionValues[name]?.trim() || f.label || name;
      updates.push({
        fieldName: name,
        checkboxGroup: {
          group_id: groupId.trim(),
          group_label: groupLabel.trim() || groupId.trim(),
          option_value: ov,
          option_label: f.label || ov,
        },
      });
    }
    await onApply(updates);
    onClose();
  };

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        backdropFilter: "blur(4px)",
      }}
      onClick={onClose}
    >
      <div
        className="animate-fade-in"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-secondary)",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border-subtle)",
          padding: "24px",
          maxWidth: 640,
          width: "90%",
          maxHeight: "85vh",
          overflow: "auto",
          boxShadow: "0 20px 60px rgba(0,0,0,0.4)",
        }}
      >
        <h3 style={{ fontSize: "1.1rem", fontWeight: 600, color: "var(--text-primary)", margin: "0 0 16px" }}>
          Checkbox csoportosítás
        </h3>

        {checkboxFields.length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: "0.85rem" }}>
            Nincs checkbox típusú mező ezen az oldalon.
          </p>
        ) : (
          <>
            {/* Group settings */}
            <div style={{ display: "flex", gap: "12px", marginBottom: "16px", flexWrap: "wrap" }}>
              <div style={{ flex: "1 1 200px" }}>
                <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "4px" }}>
                  Csoport azonosító
                </label>
                <input
                  type="text"
                  list="existing-group-ids"
                  value={groupId}
                  onChange={(e) => setGroupId(e.target.value)}
                  placeholder="pl. ingatlan_jellege"
                  style={{
                    width: "100%",
                    padding: "6px 10px",
                    background: "var(--bg-elevated)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: "0.85rem",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
                <datalist id="existing-group-ids">
                  {existingGroupIds.map((id) => (
                    <option key={id} value={id} />
                  ))}
                </datalist>
              </div>
              <div style={{ flex: "1 1 200px" }}>
                <label style={{ display: "block", fontSize: "0.75rem", fontWeight: 600, color: "var(--text-secondary)", marginBottom: "4px" }}>
                  Csoport neve (címkéje)
                </label>
                <input
                  type="text"
                  value={groupLabel}
                  onChange={(e) => setGroupLabel(e.target.value)}
                  placeholder="pl. Ingatlan jellege"
                  style={{
                    width: "100%",
                    padding: "6px 10px",
                    background: "var(--bg-elevated)",
                    border: "1px solid var(--border-default)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    fontSize: "0.85rem",
                    outline: "none",
                    boxSizing: "border-box",
                  }}
                />
              </div>
            </div>

            {/* Field list */}
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
              <span style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                Jelöld ki a csoportba tartozó mezőket ({selected.size}/{checkboxFields.length})
              </span>
              <div style={{ display: "flex", gap: "8px" }}>
                <button className="btn btn-ghost btn-sm" onClick={selectAll} style={{ fontSize: "0.7rem", padding: "2px 8px" }}>
                  Mind
                </button>
                <button className="btn btn-ghost btn-sm" onClick={selectNone} style={{ fontSize: "0.7rem", padding: "2px 8px" }}>
                  Egyik sem
                </button>
              </div>
            </div>

            <div style={{ display: "flex", flexDirection: "column", gap: "4px", maxHeight: "300px", overflow: "auto" }}>
              {checkboxFields.map((f) => {
                const isSel = selected.has(f.pdf_field_name);
                const existingGid = f.checkbox_group?.group_id;
                return (
                  <div
                    key={f.pdf_field_name}
                    onClick={() => toggleField(f.pdf_field_name)}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "8px",
                      padding: "6px 10px",
                      borderRadius: "var(--radius-sm)",
                      cursor: "pointer",
                      background: isSel ? "var(--accent-blue-glow)" : "var(--bg-primary)",
                      border: isSel ? "1px solid var(--accent-blue)" : "1px solid transparent",
                      transition: "all 0.15s",
                    }}
                  >
                    <input
                      type="checkbox"
                      checked={isSel}
                      onChange={() => toggleField(f.pdf_field_name)}
                      style={{ flexShrink: 0 }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: "0.82rem", color: "var(--text-primary)", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {f.label || f.pdf_field_name}
                      </div>
                      <div style={{ fontSize: "0.7rem", color: "var(--text-tertiary)" }}>
                        {f.pdf_field_name}
                        {existingGid && (
                          <span style={{ marginLeft: "6px", color: "var(--accent-purple)" }}>+ {existingGid}</span>
                        )}
                      </div>
                    </div>
                    {isSel && (
                      <input
                        type="text"
                        value={optionValues[f.pdf_field_name] ?? ""}
                        onChange={(e) =>
                          setOptionValues((prev) => ({ ...prev, [f.pdf_field_name]: e.target.value }))
                        }
                        onClick={(e) => e.stopPropagation()}
                        placeholder="option_value"
                        title="A picklist érték, ami ezt az opciót kiválasztja"
                        style={{
                          width: 100,
                          padding: "3px 6px",
                          background: "var(--bg-elevated)",
                          border: "1px solid var(--border-default)",
                          borderRadius: "var(--radius-sm)",
                          color: "var(--text-primary)",
                          fontSize: "0.72rem",
                          outline: "none",
                          flexShrink: 0,
                        }}
                      />
                    )}
                  </div>
                );
              })}
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end", marginTop: "20px" }}>
              <button className="btn btn-ghost" onClick={onClose}>
                Mégse
              </button>
              <button
                className="btn btn-primary"
                onClick={handleApply}
                disabled={!groupId.trim() || selected.size === 0}
              >
                Csoportosítás ({selected.size})
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

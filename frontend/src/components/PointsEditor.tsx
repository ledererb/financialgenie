// ---------------------------------------------------------------------------
// PointsEditor – editor for the top-level `points` array in the mapping JSON.
//
// Each "point" is a numbered question on a bank form containing one or more
// blocks of checkboxes. rule_type (1-7) selects which checkbox-engine rule
// applies (see src/engine/fill_rules.py); params are rule-specific.
//
// This component is framework-agnostic: no bank names or form-specific labels
// are hard-coded. Everything comes from the mapping JSON.
//
// Save strategy: the existing `PUT /api/mapping` full-mapping endpoint already
// persists any top-level key, so we read the whole mapping, mutate `points`
// in local state (optimistic), and debounce-save the full mapping back. A
// manual Save button + dirty badge act as a safety net.
// ---------------------------------------------------------------------------

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MappingConfig, MappingField, PointBlock, PointData } from "@/types";
import { getMapping, saveMapping } from "@/api/client";

interface PointsEditorProps {
  pdfId: string;
  onBack: () => void;
}

// ── rule metadata ──────────────────────────────────────────────────────────

interface RuleMeta {
  label: string;
  desc: string;
}

export const RULE_TYPES: Record<number, RuleMeta> = {
  1: { label: "Teljes körű pipázás", desc: "Feltétel nélküli – minden mező pipa." },
  2: { label: "Szerepkörös pipázás", desc: "Szerepkör → checkbox mezők tábla." },
  3: { label: "Részleges blokk", desc: "Csak egy aktív alblokk kap pipát." },
  4: { label: "Kétlépéses", desc: "Kiinduló érték + záró szerepkör-pipa." },
  5: { label: "Termékfüggő", desc: "Terméknév egyezés esetén pipa." },
  6: { label: "Hitelcél-függő", desc: "Hitelcél egyezés esetén pipa." },
  7: { label: "Többszörös blokk", desc: "Blokkonként eltérő pipa-érték." },
};

export const FRAMEWORKS = ["ALAP", "CSOK_Plusz", "Otthon_Start", "*"];

/** Default params when a point switches to a given rule type. */
export function defaultParamsForRule(ruleType: number): Record<string, any> {
  switch (ruleType) {
    case 1:
      return {};
    case 2:
      return { role_to_ticks: {} };
    case 3:
      return { active_block: "" };
    case 4:
      return {
        step1_value: "",
        closing_field: "",
        role_field: "",
        closing_value: "igen",
      };
    case 5:
    case 6:
      return { condition_value: "" };
    case 7:
      return { block_rules: {} };
    default:
      return {};
  }
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg-primary)",
  color: "var(--text-primary)",
  border: "1px solid var(--border-subtle)",
  borderRadius: "var(--radius-sm)",
  padding: "4px 8px",
  fontSize: "0.78rem",
};

const labelStyle: React.CSSProperties = {
  fontSize: "0.65rem",
  fontWeight: 700,
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  color: "var(--text-tertiary)",
  marginBottom: 4,
  display: "block",
};

// ── FieldMultiSelect sub-component ─────────────────────────────────────────
// A compact dropdown of checkboxes for selecting PDF field names. Renders a
// button showing the selected count; clicking opens a searchable panel.

function FieldMultiSelect({
  available,
  selected,
  onChange,
  placeholder,
}: {
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return available;
    return available.filter((n) => n.toLowerCase().includes(q));
  }, [available, query]);

  const toggle = (name: string) => {
    if (selectedSet.has(name)) {
      onChange(selected.filter((n) => n !== name));
    } else {
      onChange([...selected, name]);
    }
  };

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          background: "var(--bg-primary)",
          color: selected.length
            ? "var(--text-primary)"
            : "var(--text-tertiary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-sm)",
          padding: "5px 8px",
          fontSize: "0.75rem",
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 6,
        }}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selected.length
            ? `${selected.length} kiválasztva`
            : placeholder}
        </span>
        <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>▾</span>
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            zIndex: 50,
            top: "calc(100% + 2px)",
            left: 0,
            right: 0,
            maxHeight: 260,
            display: "flex",
            flexDirection: "column",
            background: "var(--bg-secondary)",
            border: "1px solid var(--border-default)",
            borderRadius: "var(--radius-sm)",
            boxShadow: "var(--shadow-lg)",
          }}
        >
          <input
            autoFocus
            placeholder="Keresés…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            style={{
              ...inputStyle,
              margin: 6,
              width: "auto",
            }}
          />
          <div style={{ overflowY: "auto", flex: 1, padding: "0 6px 6px" }}>
            {filtered.length === 0 && (
              <div style={{ padding: "var(--space-sm)", fontSize: "0.72rem", color: "var(--text-muted)" }}>
                Nincs elérhető mező.
              </div>
            )}
            {filtered.map((name) => (
              <label
                key={name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "4px 6px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  fontSize: "0.72rem",
                  fontFamily: "monospace",
                  wordBreak: "break-all",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "var(--bg-hover)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.background = "transparent";
                }}
              >
                <input
                  type="checkbox"
                  checked={selectedSet.has(name)}
                  onChange={() => toggle(name)}
                  style={{ flexShrink: 0 }}
                />
                <span>{name}</span>
              </label>
            ))}
          </div>
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              gap: 6,
              padding: "6px",
              borderTop: "1px solid var(--border-subtle)",
            }}
          >
            <button
              type="button"
              onClick={() => {
                onChange([]);
              }}
              style={{
                background: "none",
                border: "1px solid var(--border-subtle)",
                color: "var(--text-secondary)",
                borderRadius: "var(--radius-sm)",
                padding: "2px 8px",
                fontSize: "0.68rem",
                cursor: "pointer",
              }}
            >
              Ürítés
            </button>
            <button
              type="button"
              onClick={() => setOpen(false)}
              style={{
                background: "var(--accent-blue)",
                border: "none",
                color: "#fff",
                borderRadius: "var(--radius-sm)",
                padding: "2px 10px",
                fontSize: "0.68rem",
                cursor: "pointer",
              }}
            >
              Kész
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── RuleParamsForm sub-component ───────────────────────────────────────────
// Dynamic form whose fields depend on the point's rule_type.

function RuleParamsForm({
  point,
  fieldNames,
  onChange,
}: {
  point: PointData;
  fieldNames: string[];
  onChange: (params: Record<string, any>) => void;
}) {
  const rt = point.rule_type;

  if (rt === 1) {
    return (
      <div style={{ ...paramsBoxStyle, color: "var(--text-tertiary)", fontStyle: "italic" }}>
        (feltétel nélküli) — minden aktív fél pipát kap.
      </div>
    );
  }

  if (rt === 2) {
    const roleToTicks: Record<string, string[]> =
      point.params.role_to_ticks ?? {};
    const roles = Object.keys(roleToTicks);
    const updateRole = (role: string, members: string[]) => {
      onChange({ ...point.params, role_to_ticks: { ...roleToTicks, [role]: members } });
    };
    const addRole = () => {
      const name = window.prompt("Új szerepkör érték (pl. adós):");
      if (!name) return;
      if (roleToTicks[name] !== undefined) return;
      onChange({ ...point.params, role_to_ticks: { ...roleToTicks, [name]: [] } });
    };
    const removeRole = (role: string) => {
      const next = { ...roleToTicks };
      delete next[role];
      onChange({ ...point.params, role_to_ticks: next });
    };
    return (
      <div style={paramsBoxStyle}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <span style={labelStyle as React.CSSProperties}>Szerepkör → Checkbox mezők</span>
          <button type="button" onClick={addRole} style={addBtnStyle}>+ Sor</button>
        </div>
        {roles.length === 0 && (
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
            Még nincs sor. Add hozzá a szerepköröket (adós, adóstárs, …).
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {roles.map((role) => (
            <div
              key={role}
              style={{
                display: "grid",
                gridTemplateColumns: "140px 1fr auto",
                gap: 6,
                alignItems: "center",
              }}
            >
              <input
                style={inputStyle}
                value={role}
                onChange={(e) => {
                  const next = { ...roleToTicks };
                  const members = next[role];
                  delete next[role];
                  next[e.target.value] = members;
                  onChange({ ...point.params, role_to_ticks: next });
                }}
              />
              <FieldMultiSelect
                available={fieldNames}
                selected={roleToTicks[role] ?? []}
                onChange={(m) => updateRole(role, m)}
                placeholder="Válassz checkbox mezőket"
              />
              <button type="button" onClick={() => removeRole(role)} style={iconBtnStyle} title="Sor törlése">
                ✕
              </button>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (rt === 3) {
    const blockIds = point.blocks.map((b) => b.block_id);
    return (
      <div style={paramsBoxStyle}>
        <label style={labelStyle as React.CSSProperties}>Aktív blokk</label>
        <select
          style={inputStyle}
          value={point.params.active_block ?? ""}
          onChange={(e) => onChange({ ...point.params, active_block: e.target.value })}
        >
          <option value="">— válassz blokkot —</option>
          {blockIds.map((id) => (
            <option key={id} value={id}>{id}</option>
          ))}
        </select>
        {blockIds.length === 0 && (
          <div style={{ fontSize: "0.7rem", color: "var(--accent-amber)", marginTop: 4 }}>
            Először adj hozzá blokkokat a ponthoz.
          </div>
        )}
      </div>
    );
  }

  if (rt === 4) {
    const p = point.params;
    const set = (k: string, v: any) => onChange({ ...point.params, [k]: v });
    return (
      <div style={paramsBoxStyle}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <div>
            <label style={labelStyle as React.CSSProperties}>step1_value</label>
            <input style={inputStyle} value={p.step1_value ?? ""} onChange={(e) => set("step1_value", e.target.value)} />
          </div>
          <div>
            <label style={labelStyle as React.CSSProperties}>closing_value</label>
            <input style={inputStyle} value={p.closing_value ?? ""} onChange={(e) => set("closing_value", e.target.value)} />
          </div>
          <div>
            <label style={labelStyle as React.CSSProperties}>closing_field</label>
            <select style={inputStyle} value={p.closing_field ?? ""} onChange={(e) => set("closing_field", e.target.value)}>
              <option value="">— pdf mező —</option>
              {fieldNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={labelStyle as React.CSSProperties}>role_field</label>
            <select style={inputStyle} value={p.role_field ?? ""} onChange={(e) => set("role_field", e.target.value)}>
              <option value="">— pdf mező —</option>
              {fieldNames.map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
        </div>
      </div>
    );
  }

  if (rt === 5 || rt === 6) {
    return (
      <div style={paramsBoxStyle}>
        <label style={labelStyle as React.CSSProperties}>
          {rt === 5 ? "Termék neve (condition_value)" : "Hitelcél (condition_value)"}
        </label>
        <input
          style={inputStyle}
          value={point.params.condition_value ?? ""}
          onChange={(e) => onChange({ ...point.params, condition_value: e.target.value })}
        />
        <div style={{ fontSize: "0.68rem", color: "var(--text-muted)", marginTop: 4 }}>
          Pipa csak akkor, ha az ügylet {rt === 5 ? "termékneve" : "hitelcelcel"} egyezik ezzel az értékkel.
        </div>
      </div>
    );
  }

  if (rt === 7) {
    const blockRules: Record<string, { value: string }> = point.params.block_rules ?? {};
    return (
      <div style={paramsBoxStyle}>
        <label style={labelStyle as React.CSSProperties}>Blokkonkénti érték</label>
        {point.blocks.length === 0 && (
          <div style={{ fontSize: "0.72rem", color: "var(--text-muted)" }}>
            Először adj hozzá blokkokat a ponthoz.
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {point.blocks.map((b) => {
            const cur = blockRules[b.block_id]?.value ?? "igen";
            return (
              <div
                key={b.block_id}
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 120px",
                  gap: 8,
                  alignItems: "center",
                }}
              >
                <span style={{ fontSize: "0.75rem", fontFamily: "monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {b.block_id}
                </span>
                <select
                  style={inputStyle}
                  value={cur}
                  onChange={(e) =>
                    onChange({
                      ...point.params,
                      block_rules: {
                        ...blockRules,
                        [b.block_id]: { value: e.target.value },
                      },
                    })
                  }
                >
                  <option value="igen">igen</option>
                  <option value="nem">nem</option>
                </select>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  return (
    <div style={{ ...paramsBoxStyle, color: "var(--accent-red)" }}>
      Ismeretlen szabálytípus: {rt}
    </div>
  );
}

const paramsBoxStyle: React.CSSProperties = {
  padding: "var(--space-sm)",
  background: "var(--bg-tertiary)",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--border-subtle)",
};

const addBtnStyle: React.CSSProperties = {
  background: "var(--accent-green)",
  color: "#fff",
  border: "none",
  borderRadius: "var(--radius-sm)",
  padding: "3px 10px",
  fontSize: "0.68rem",
  cursor: "pointer",
  flexShrink: 0,
};

const iconBtnStyle: React.CSSProperties = {
  background: "none",
  border: "1px solid var(--border-subtle)",
  color: "var(--accent-red)",
  borderRadius: "var(--radius-sm)",
  width: 28,
  height: 28,
  cursor: "pointer",
  flexShrink: 0,
};

// ── PointCard sub-component ────────────────────────────────────────────────

function PointCard({
  point,
  fieldNames,
  index,
  onChange,
  onDelete,
}: {
  point: PointData;
  fieldNames: string[];
  index: number;
  onChange: (next: PointData) => void;
  onDelete: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const ruleMeta = RULE_TYPES[point.rule_type];
  const memberCount = point.blocks.reduce((n, b) => n + b.members.length, 0);

  const set = (patch: Partial<PointData>) => onChange({ ...point, ...patch });

  const setRuleType = (rt: number) => {
    set({ rule_type: rt, params: defaultParamsForRule(rt) });
  };

  // ── block handlers ─────────────────────────────────────────────────
  const addBlock = () => {
    const id = window.prompt("Új blokk azonosító:", `${point.point_id}.block_${point.blocks.length + 1}`);
    if (!id) return;
    const next: PointBlock = { block_id: id, members: [] };
    set({ blocks: [...point.blocks, next] });
  };
  const updateBlock = (idx: number, patch: Partial<PointBlock>) => {
    set({ blocks: point.blocks.map((b, i) => (i === idx ? { ...b, ...patch } : b)) });
  };
  const removeBlock = (idx: number) => {
    set({ blocks: point.blocks.filter((_, i) => i !== idx) });
  };

  return (
    <div
      className="card"
      style={{
        padding: 0,
        overflow: "hidden",
        borderLeft: "3px solid var(--accent-blue)",
      }}
    >
      {/* header row */}
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        style={{
          width: "100%",
          background: "none",
          border: "none",
          color: "var(--text-primary)",
          padding: "var(--space-sm) var(--space-md)",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-sm)",
          textAlign: "left",
        }}
      >
        <span style={{ color: "var(--text-tertiary)", flexShrink: 0 }}>{expanded ? "▾" : "▸"}</span>
        <span
          className="badge badge-blue"
          style={{ flexShrink: 0 }}
          title={ruleMeta?.desc}
        >
          #{point.rule_type}
        </span>
        <span style={{ fontSize: "0.85rem", fontWeight: 600, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {point.label || point.point_id}
        </span>
        <span style={{ fontSize: "0.7rem", color: "var(--text-tertiary)", flexShrink: 0 }}>
          {point.framework}
        </span>
        <span style={{ fontSize: "0.7rem", color: "var(--text-tertiary)", flexShrink: 0 }}>
          {point.blocks.length} blokk · {memberCount} mező
        </span>
      </button>

      {expanded && (
        <div
          style={{
            padding: "var(--space-md)",
            borderTop: "1px solid var(--border-subtle)",
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-md)",
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* identity row */}
          <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 0.8fr 0.6fr", gap: 10 }}>
            <div>
              <label style={labelStyle as React.CSSProperties}>Point ID</label>
              <input
                style={{ ...inputStyle, fontFamily: "monospace" }}
                value={point.point_id}
                onChange={(e) => set({ point_id: e.target.value })}
              />
            </div>
            <div>
              <label style={labelStyle as React.CSSProperties}>Keretprogram</label>
              <select
                style={inputStyle}
                value={point.framework}
                onChange={(e) => set({ framework: e.target.value })}
              >
                {FRAMEWORKS.map((f) => (
                  <option key={f} value={f}>{f === "*" ? "* (univerzális)" : f}</option>
                ))}
                {!FRAMEWORKS.includes(point.framework) && (
                  <option value={point.framework}>{point.framework}</option>
                )}
              </select>
            </div>
            <div>
              <label style={labelStyle as React.CSSProperties}>Oldal</label>
              <input
                type="number"
                min={1}
                style={inputStyle}
                value={point.page_number}
                onChange={(e) => set({ page_number: parseInt(e.target.value, 10) || 1 })}
              />
            </div>
            <div>
              <label style={labelStyle as React.CSSProperties}>Szabály</label>
              <select
                style={inputStyle}
                value={point.rule_type}
                onChange={(e) => setRuleType(parseInt(e.target.value, 10))}
                title={ruleMeta?.desc}
              >
                {Object.entries(RULE_TYPES).map(([k, m]) => (
                  <option key={k} value={k}>
                    {k}. {m.label}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* label */}
          <div>
            <label style={labelStyle as React.CSSProperties}>Címke</label>
            <input
              style={inputStyle}
              placeholder="Pl. Adós szerepkörök"
              value={point.label}
              onChange={(e) => set({ label: e.target.value })}
            />
          </div>

          {/* blocks section */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
              <label style={{ ...labelStyle as React.CSSProperties, marginBottom: 0 }}>Blokkok</label>
              <button type="button" onClick={addBlock} style={addBtnStyle}>+ Blokk</button>
            </div>
            {point.blocks.length === 0 && (
              <div style={{ fontSize: "0.72rem", color: "var(--text-muted)", padding: "var(--space-xs) 0" }}>
                Még nincs blokk. Egy blokk egy csoport checkbox mező.
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {point.blocks.map((b, idx) => (
                <div
                  key={`${b.block_id}-${idx}`}
                  style={{
                    border: "1px solid var(--border-subtle)",
                    borderRadius: "var(--radius-sm)",
                    padding: "var(--space-sm)",
                    background: "var(--bg-secondary)",
                    display: "flex",
                    flexDirection: "column",
                    gap: 6,
                  }}
                >
                  <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                    <input
                      style={{ ...inputStyle, fontFamily: "monospace", flex: 1 }}
                      value={b.block_id}
                      onChange={(e) => updateBlock(idx, { block_id: e.target.value })}
                    />
                    <span style={{ fontSize: "0.68rem", color: "var(--text-tertiary)", flexShrink: 0 }}>
                      {b.members.length} mező
                    </span>
                    <button type="button" onClick={() => removeBlock(idx)} style={iconBtnStyle} title="Blokk törlése">
                      ✕
                    </button>
                  </div>
                  <FieldMultiSelect
                    available={fieldNames}
                    selected={b.members}
                    onChange={(m) => updateBlock(idx, { members: m })}
                    placeholder="Válassz checkbox mezőket a blokkba"
                  />
                </div>
              ))}
            </div>
          </div>

          {/* params section */}
          <div>
            <label style={labelStyle as React.CSSProperties}>
              Paraméterek — {ruleMeta?.label}
            </label>
            <RuleParamsForm point={point} fieldNames={fieldNames} onChange={(params) => set({ params })} />
          </div>

          {/* delete */}
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button
              type="button"
              onClick={onDelete}
              style={{
                background: "var(--accent-red)",
                color: "#fff",
                border: "none",
                borderRadius: "var(--radius-sm)",
                padding: "5px 12px",
                fontSize: "0.72rem",
                cursor: "pointer",
              }}
            >
              🗑 Pont törlése
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── main PointsEditor ──────────────────────────────────────────────────────

export default function PointsEditor({ pdfId, onBack }: PointsEditorProps) {
  const [mapping, setMapping] = useState<MappingConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // debounce auto-save timer
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    getMapping(pdfId)
      .then((m) => {
        if (!cancelled) setMapping(m);
      })
      .catch((e) => {
        if (!cancelled) setError((e as Error).message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [pdfId]);

  const points = mapping?.points ?? [];

  // Available PDF field names for member multi-select (from mapping.fields).
  const fieldNames = useMemo(() => {
    if (!mapping) return [];
    return (mapping.fields as MappingField[]).map((f) => f.pdf_field_name);
  }, [mapping]);

  // ── persistence ────────────────────────────────────────────────────
  // Optimistic local mutation + debounced full-mapping save. The existing
  // PUT /api/mapping endpoint persists every top-level key (it only strips
  // `_`-prefixed metadata), so `points` round-trips automatically.

  const persist = useCallback(
    async (nextMapping: MappingConfig) => {
      setSaving(true);
      try {
        const res = await saveMapping(pdfId, nextMapping, nextMapping._mtime);
        setMapping((prev) =>
          prev ? { ...nextMapping, _mtime: res._mtime ?? prev._mtime } : prev,
        );
        setDirty(false);
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setSaving(false);
      }
    },
    [pdfId],
  );

  const scheduleSave = useCallback(
    (nextMapping: MappingConfig) => {
      setMapping(nextMapping);
      setDirty(true);
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        persist(nextMapping);
      }, 800);
    },
    [persist],
  );

  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
    };
  }, []);

  const mutatePoints = useCallback(
    (next: PointData[]) => {
      if (!mapping) return;
      scheduleSave({ ...mapping, points: next });
    },
    [mapping, scheduleSave],
  );

  const handlePointChange = useCallback(
    (idx: number, next: PointData) => {
      mutatePoints(points.map((p, i) => (i === idx ? next : p)));
    },
    [mutatePoints, points],
  );

  const handlePointDelete = useCallback(
    (idx: number) => {
      const p = points[idx];
      if (!p) return;
      if (!window.confirm(`Biztosan törlöd a pontot: ${p.label || p.point_id}?`)) return;
      mutatePoints(points.filter((_, i) => i !== idx));
    },
    [mutatePoints, points],
  );

  const handleAddPoint = useCallback(() => {
    const next: PointData = {
      point_id: `POINT_${Date.now().toString(36).toUpperCase()}`,
      framework: "*",
      label: "",
      page_number: 1,
      blocks: [],
      rule_type: 1,
      params: defaultParamsForRule(1),
    };
    mutatePoints([...points, next]);
  }, [mutatePoints, points]);

  const handleManualSave = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    if (mapping) persist({ ...mapping, points });
  }, [mapping, points, persist]);

  // ── render ─────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "var(--text-secondary)" }}>
        Pontok betöltése…
      </div>
    );
  }

  if (error && !mapping) {
    return (
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: "var(--space-md)" }}>
        <span className="badge badge-red">Hiba</span>
        <p style={{ color: "var(--text-secondary)" }}>{error}</p>
        <button className="btn btn-ghost" onClick={onBack}>← Vissza</button>
      </div>
    );
  }

  return (
    <div
      className="animate-fade-in"
      style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}
    >
      {/* header */}
      <header
        className="glass"
        style={{
          padding: "16px 28px",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
          borderBottom: "1px solid var(--border-subtle)",
        }}
      >
        <button className="btn btn-ghost btn-sm" onClick={onBack}>
          ← Vissza
        </button>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600 }}>
          Pontok &amp; Blokkok
        </h2>
        <span style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
          {points.length} pont · {fieldNames.length} elérhető mező
        </span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          {dirty && <span className="badge badge-amber">mentésre vár</span>}
          {saving && <span className="badge badge-blue">Mentés…</span>}
          <button
            className="btn btn-ghost btn-sm"
            onClick={handleManualSave}
            disabled={!dirty || saving}
          >
            Mentés
          </button>
          <button className="btn btn-primary btn-sm" onClick={handleAddPoint}>
            + Új pont
          </button>
        </div>
      </header>

      {error && (
        <div className="badge badge-red" style={{ margin: "var(--space-sm) var(--space-lg)" }}>
          {error}
        </div>
      )}

      {/* body */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "var(--space-lg)",
          display: "flex",
          flexDirection: "column",
          gap: "var(--space-md)",
        }}
      >
        {points.length === 0 && (
          <section className="card" style={{ textAlign: "center", color: "var(--text-secondary)", fontSize: "0.85rem" }}>
            <p style={{ marginBottom: "var(--space-sm)" }}>
              Még nincsenek pontok definiálva.
            </p>
            <p style={{ fontSize: "0.75rem", color: "var(--text-tertiary)" }}>
              Egy „pont” egy sorszámozott nyomtatványszám checkbox csoport,
              amelyre a 7-szabályos kitöltő motor egyik szabálya érvényesül.
            </p>
            <button className="btn btn-primary" style={{ marginTop: "var(--space-md)" }} onClick={handleAddPoint}>
              + Új pont létrehozása
            </button>
          </section>
        )}

        {points.map((p, idx) => (
          <PointCard
            key={`${p.point_id}-${idx}`}
            point={p}
            fieldNames={fieldNames}
            index={idx}
            onChange={(next) => handlePointChange(idx, next)}
            onDelete={() => handlePointDelete(idx)}
          />
        ))}
      </div>
    </div>
  );
}

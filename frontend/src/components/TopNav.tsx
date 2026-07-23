interface TopNavProps {
  view: "library" | "generate";
  onViewChange: (view: "library" | "generate") => void;
}

export default function TopNav({ view, onViewChange }: TopNavProps) {
  const tabs: { key: "library" | "generate"; label: string }[] = [
    { key: "library", label: "Dokumentumtár" },
    { key: "generate", label: "Generálás" },
  ];

  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        gap: "0",
        padding: "0 24px",
        height: 48,
        background: "var(--bg-secondary)",
        borderBottom: "1px solid var(--border-subtle)",
        flexShrink: 0,
      }}
    >
      <span
        style={{
          fontSize: "0.9rem",
          fontWeight: 700,
          color: "var(--text-primary)",
          marginRight: "var(--space-xl)",
          letterSpacing: "-0.01em",
        }}
      >
        FinancialGenie
      </span>
      <nav style={{ display: "flex", gap: "2px", height: "100%" }}>
        {tabs.map((tab) => {
          const active = view === tab.key;
          return (
            <button
              key={tab.key}
              onClick={() => onViewChange(tab.key)}
              style={{
                background: active ? "var(--bg-tertiary)" : "transparent",
                border: "none",
                borderBottom: active ? "2px solid var(--accent-blue)" : "2px solid transparent",
                color: active ? "var(--text-primary)" : "var(--text-tertiary)",
                padding: "0 16px",
                fontSize: "0.82rem",
                fontWeight: active ? 600 : 400,
                cursor: "pointer",
                transition: "color var(--transition-fast), background var(--transition-fast)",
              }}
              onMouseEnter={(e) => {
                if (!active) e.currentTarget.style.color = "var(--text-secondary)";
              }}
              onMouseLeave={(e) => {
                if (!active) e.currentTarget.style.color = "var(--text-tertiary)";
              }}
            >
              {tab.label}
            </button>
          );
        })}
      </nav>
    </header>
  );
}

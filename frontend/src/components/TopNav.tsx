import type { User } from "@/types";

export type NavView = "library" | "generate" | "users";

interface TopNavProps {
  view: NavView;
  onViewChange: (view: NavView) => void;
  user: User;
  onLogout: () => void;
}

export default function TopNav({ view, onViewChange, user, onLogout }: TopNavProps) {
  const isAdmin = user.role === "admin";
  const tabs: { key: NavView; label: string }[] = [
    { key: "library", label: "Dokumentumtár" },
    { key: "generate", label: "Generálás" },
    ...(isAdmin ? ([{ key: "users", label: "Felhasználók" }] as const) : []),
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

      {/* Right side: logged-in user + logout */}
      <div
        style={{
          marginLeft: "auto",
          display: "flex",
          alignItems: "center",
          gap: "10px",
        }}
      >
        <span
          className={isAdmin ? "badge badge-blue" : "badge badge-green"}
          style={{ fontSize: "0.65rem", padding: "2px 8px" }}
        >
          {isAdmin ? "Admin" : "Member"}
        </span>
        <span
          style={{
            fontSize: "0.8rem",
            color: "var(--text-secondary)",
            fontWeight: 500,
          }}
          title={user.username}
        >
          {user.display_name || user.username}
        </span>
        <button
          className="btn btn-ghost btn-sm"
          onClick={onLogout}
          style={{
            fontSize: "0.75rem",
            padding: "4px 10px",
            color: "var(--text-tertiary)",
          }}
        >
          Kijelentkezés
        </button>
      </div>
    </header>
  );
}

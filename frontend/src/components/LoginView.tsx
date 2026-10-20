import { useState } from "react";
import type { User } from "@/types";
import { loginUser, setAuthToken } from "@/api/client";

interface LoginViewProps {
  onLoginSuccess: (user: User, token: string) => void;
}

export default function LoginView({ onLoginSuccess }: LoginViewProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true);
    setError(null);
    try {
      const res = await loginUser(username.trim(), password);
      setAuthToken(res.token);
      onLoginSuccess(res.user, res.token);
    } catch (err) {
      setError((err as Error).message || "Sikertelen bejelentkezés.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        width: "100vw",
        background: "var(--bg-primary)",
        padding: "24px",
      }}
    >
      <div
        className="card animate-fade-in"
        style={{
          width: "100%",
          maxWidth: 400,
          padding: "32px 28px",
          background: "var(--bg-secondary)",
          border: "1px solid var(--border-subtle)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "0 20px 50px rgba(0, 0, 0, 0.45)",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "24px" }}>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              width: 44,
              height: 44,
              borderRadius: "12px",
              background: "var(--accent-blue-glow)",
              border: "1px solid rgba(91, 154, 255, 0.3)",
              marginBottom: "12px",
            }}
          >
            <svg
              width="22"
              height="22"
              viewBox="0 0 24 24"
              fill="none"
              stroke="var(--accent-blue)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          </div>
          <h1
            style={{
              fontSize: "1.35rem",
              fontWeight: 700,
              color: "var(--text-primary)",
              margin: "0 0 6px 0",
              letterSpacing: "-0.02em",
            }}
          >
            FinancialGenie
          </h1>
          <p
            style={{
              fontSize: "0.82rem",
              color: "var(--text-tertiary)",
              margin: 0,
            }}
          >
            Jelentkezz be a folytatáshoz
          </p>
        </div>

        {error && (
          <div
            style={{
              marginBottom: "16px",
              padding: "10px 12px",
              background: "var(--accent-red-glow)",
              border: "1px solid var(--accent-red)",
              borderRadius: "var(--radius-sm)",
              color: "var(--accent-red)",
              fontSize: "0.8rem",
            }}
          >
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <div>
            <label
              htmlFor="fg-login-username"
              style={{
                display: "block",
                fontSize: "0.78rem",
                fontWeight: 500,
                color: "var(--text-secondary)",
                marginBottom: "6px",
              }}
            >
              Felhasználónév
            </label>
            <input
              id="fg-login-username"
              type="text"
              autoFocus
              autoComplete="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={loading}
              placeholder="pl. admin"
              style={{
                width: "100%",
                padding: "10px 12px",
                background: "var(--bg-tertiary)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: "0.88rem",
              }}
            />
          </div>

          <div>
            <label
              htmlFor="fg-login-password"
              style={{
                display: "block",
                fontSize: "0.78rem",
                fontWeight: 500,
                color: "var(--text-secondary)",
                marginBottom: "6px",
              }}
            >
              Jelszó
            </label>
            <input
              id="fg-login-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
              placeholder="••••••••"
              style={{
                width: "100%",
                padding: "10px 12px",
                background: "var(--bg-tertiary)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-sm)",
                color: "var(--text-primary)",
                fontSize: "0.88rem",
              }}
            />
          </div>

          <button
            type="submit"
            disabled={loading || !username.trim() || !password}
            style={{
              marginTop: "4px",
              width: "100%",
              padding: "11px 16px",
              background: "var(--accent-blue)",
              color: "#fff",
              border: "none",
              borderRadius: "var(--radius-sm)",
              fontSize: "0.9rem",
              fontWeight: 600,
              cursor: loading || !username.trim() || !password ? "not-allowed" : "pointer",
              opacity: loading || !username.trim() || !password ? 0.55 : 1,
              transition: "opacity var(--transition-fast)",
            }}
          >
            {loading ? "Bejelentkezés..." : "Bejelentkezés"}
          </button>
        </form>
      </div>
    </div>
  );
}

import { useState, useEffect, useCallback } from "react";
import type { User, UserRole } from "@/types";
import { listUsers, createUser, updateUser, deleteUser } from "@/api/client";

interface UsersViewProps {
  currentUser: User;
}

export default function UsersView({ currentUser }: UsersViewProps) {
  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // New user form
  const [showCreate, setShowCreate] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newRole, setNewRole] = useState<UserRole>("member");
  const [creating, setCreating] = useState(false);

  // Edit user state
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editRole, setEditRole] = useState<UserRole>("member");
  const [editPassword, setEditPassword] = useState("");
  const [savingEdit, setSavingEdit] = useState(false);

  // Delete confirmation state
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await listUsers();
      setUsers(list);
    } catch (e) {
      setError((e as Error).message || "Nem sikerült betölteni a felhasználókat.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newUsername.trim() || !newPassword) return;
    setCreating(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const created = await createUser({
        username: newUsername.trim(),
        password: newPassword,
        display_name: newDisplayName.trim() || newUsername.trim(),
        role: newRole,
      });
      setUsers((prev) => [...prev, created]);
      setNewUsername("");
      setNewDisplayName("");
      setNewPassword("");
      setNewRole("member");
      setShowCreate(false);
      setSuccessMsg(`A(z) „${created.username}” felhasználó sikeresen létrejött.`);
    } catch (e) {
      setError((e as Error).message || "A felhasználó létrehozása sikertelen.");
    } finally {
      setCreating(false);
    }
  };

  const openEditModal = (u: User) => {
    setEditingUser(u);
    setEditDisplayName(u.display_name);
    setEditRole(u.role);
    setEditPassword("");
    setError(null);
    setSuccessMsg(null);
  };

  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingUser) return;
    setSavingEdit(true);
    setError(null);
    setSuccessMsg(null);
    try {
      const updated = await updateUser(editingUser.id, {
        display_name: editDisplayName.trim() || editingUser.username,
        role: editRole,
        ...(editPassword.trim() ? { password: editPassword.trim() } : {}),
      });
      setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
      setEditingUser(null);
      setSuccessMsg(`A(z) „${updated.username}” felhasználó adatai frissültek.`);
    } catch (e) {
      setError((e as Error).message || "A módosítás mentése sikertelen.");
    } finally {
      setSavingEdit(false);
    }
  };

  const handleDelete = async (userId: string, username: string) => {
    setError(null);
    setSuccessMsg(null);
    try {
      await deleteUser(userId);
      setUsers((prev) => prev.filter((u) => u.id !== userId));
      setConfirmDeleteId(null);
      setSuccessMsg(`A(z) „${username}” felhasználó törölve lett.`);
    } catch (e) {
      setError((e as Error).message || "A felhasználó törlése sikertelen.");
    }
  };

  const adminCount = users.filter((u) => u.role === "admin").length;
  const memberCount = users.filter((u) => u.role === "member").length;

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "var(--bg-primary)" }}>
      {/* Header */}
      <div
        className="glass"
        style={{
          padding: "12px 24px",
          display: "flex",
          alignItems: "center",
          gap: "var(--space-md)",
          flexShrink: 0,
          borderLeft: "none",
          borderRight: "none",
          borderTop: "none",
        }}
      >
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, margin: 0 }}>
          Felhasználók kezelése
        </h2>
        <span style={{ fontSize: "0.8rem", color: "var(--text-tertiary)" }}>
          {adminCount} adminisztrátor · {memberCount} member
        </span>
        <div style={{ marginLeft: "auto" }}>
          <button
            className="btn btn-primary btn-sm"
            onClick={() => {
              setShowCreate((v) => !v);
              setError(null);
              setSuccessMsg(null);
            }}
          >
            {showCreate ? "Mégse" : "+ Új felhasználó"}
          </button>
        </div>
      </div>

      {/* Main content */}
      <div style={{ flex: 1, overflow: "auto", padding: "var(--space-lg) 24px" }}>
        <div style={{ maxWidth: 920, margin: "0 auto", display: "flex", flexDirection: "column", gap: "var(--space-md)" }}>
          {/* Role permissions summary */}
          <div
            style={{
              padding: "12px 16px",
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              fontSize: "0.8rem",
              color: "var(--text-secondary)",
              display: "flex",
              gap: "24px",
              flexWrap: "wrap",
            }}
          >
            <div>
              <span className="badge badge-blue" style={{ marginRight: 6 }}>Admin</span>
              Teljes jogosultság: bankok, termékek, dokumentumok, AI mező-mapping és felhasználók kezelése.
            </div>
            <div>
              <span className="badge badge-green" style={{ marginRight: 6 }}>Member</span>
              Dokumentumtár megtekintése (csak olvasásra) + Dokumentumcsomag generálása, ellenőrzése és letöltése.
            </div>
          </div>

          {error && (
            <div
              style={{
                padding: "10px 14px",
                background: "var(--accent-red-glow)",
                border: "1px solid var(--accent-red)",
                borderRadius: "var(--radius-sm)",
                color: "var(--accent-red)",
                fontSize: "0.82rem",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{error}</span>
              <button
                onClick={() => setError(null)}
                style={{ background: "none", border: "none", color: "var(--accent-red)", cursor: "pointer" }}
              >
                ✕
              </button>
            </div>
          )}

          {successMsg && (
            <div
              style={{
                padding: "10px 14px",
                background: "rgba(34, 197, 94, 0.12)",
                border: "1px solid var(--accent-green)",
                borderRadius: "var(--radius-sm)",
                color: "var(--accent-green)",
                fontSize: "0.82rem",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span>{successMsg}</span>
              <button
                onClick={() => setSuccessMsg(null)}
                style={{ background: "none", border: "none", color: "var(--accent-green)", cursor: "pointer" }}
              >
                ✕
              </button>
            </div>
          )}

          {/* Create user card */}
          {showCreate && (
            <form
              onSubmit={handleCreate}
              className="card animate-fade-in"
              style={{
                padding: "20px",
                background: "var(--bg-secondary)",
                border: "1px solid var(--accent-blue)",
                borderRadius: "var(--radius-md)",
              }}
            >
              <h3 style={{ fontSize: "0.95rem", fontWeight: 600, margin: "0 0 14px 0" }}>
                Új felhasználó hozzáadása
              </h3>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
                  gap: "12px",
                  marginBottom: "14px",
                }}
              >
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                    Felhasználónév *
                  </label>
                  <input
                    type="text"
                    required
                    autoFocus
                    value={newUsername}
                    onChange={(e) => setNewUsername(e.target.value)}
                    placeholder="pl. kovacs.anna"
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      background: "var(--bg-tertiary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-primary)",
                      fontSize: "0.84rem",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                    Megjelenített név
                  </label>
                  <input
                    type="text"
                    value={newDisplayName}
                    onChange={(e) => setNewDisplayName(e.target.value)}
                    placeholder="pl. Kovács Anna"
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      background: "var(--bg-tertiary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-primary)",
                      fontSize: "0.84rem",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                    Kezdő jelszó * (min. 4 karakter)
                  </label>
                  <input
                    type="password"
                    required
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="••••••••"
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      background: "var(--bg-tertiary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-primary)",
                      fontSize: "0.84rem",
                    }}
                  />
                </div>
                <div>
                  <label style={{ display: "block", fontSize: "0.75rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                    Szerepkör
                  </label>
                  <select
                    value={newRole}
                    onChange={(e) => setNewRole(e.target.value as UserRole)}
                    style={{
                      width: "100%",
                      padding: "8px 10px",
                      background: "var(--bg-tertiary)",
                      border: "1px solid var(--border-subtle)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-primary)",
                      fontSize: "0.84rem",
                      cursor: "pointer",
                    }}
                  >
                    <option value="member">Member (Tag — csak generálás + olvasás)</option>
                    <option value="admin">Admin (Teljes hozzáférés)</option>
                  </select>
                </div>
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setShowCreate(false)}
                >
                  Mégse
                </button>
                <button
                  type="submit"
                  className="btn btn-primary btn-sm"
                  disabled={creating || !newUsername.trim() || newPassword.length < 4}
                >
                  {creating ? "Létrehozás..." : "Felhasználó létrehozása"}
                </button>
              </div>
            </form>
          )}

          {/* Users table */}
          <div
            className="card"
            style={{
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              overflow: "hidden",
            }}
          >
            {loading ? (
              <div style={{ padding: "32px", textAlign: "center", color: "var(--text-tertiary)", fontSize: "0.85rem" }}>
                Felhasználók betöltése...
              </div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-tertiary)" }}>
                    <th style={{ textAlign: "left", padding: "10px 16px", fontSize: "0.74rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                      Felhasználónév
                    </th>
                    <th style={{ textAlign: "left", padding: "10px 16px", fontSize: "0.74rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                      Név
                    </th>
                    <th style={{ textAlign: "left", padding: "10px 16px", fontSize: "0.74rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                      Szerepkör
                    </th>
                    <th style={{ textAlign: "left", padding: "10px 16px", fontSize: "0.74rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                      Létrehozva
                    </th>
                    <th style={{ textAlign: "right", padding: "10px 16px", fontSize: "0.74rem", color: "var(--text-tertiary)", textTransform: "uppercase" }}>
                      Műveletek
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isSelf = u.id === currentUser.id;
                    return (
                      <tr
                        key={u.id}
                        style={{ borderBottom: "1px solid var(--border-subtle)" }}
                      >
                        <td style={{ padding: "12px 16px", fontWeight: 600, color: "var(--text-primary)" }}>
                          {u.username}
                          {isSelf && (
                            <span
                              style={{
                                marginLeft: 8,
                                fontSize: "0.68rem",
                                color: "var(--text-tertiary)",
                                fontWeight: 400,
                              }}
                            >
                              (te)
                            </span>
                          )}
                        </td>
                        <td style={{ padding: "12px 16px", color: "var(--text-secondary)" }}>
                          {u.display_name}
                        </td>
                        <td style={{ padding: "12px 16px" }}>
                          <span className={u.role === "admin" ? "badge badge-blue" : "badge badge-green"}>
                            {u.role === "admin" ? "Admin" : "Member"}
                          </span>
                        </td>
                        <td style={{ padding: "12px 16px", color: "var(--text-tertiary)", fontSize: "0.78rem" }}>
                          {u.created_at ? u.created_at.slice(0, 16) : "—"}
                        </td>
                        <td style={{ padding: "12px 16px", textAlign: "right" }}>
                          {confirmDeleteId === u.id ? (
                            <span style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                              <span style={{ fontSize: "0.75rem", color: "var(--accent-red)" }}>Biztosan törlöd?</span>
                              <button
                                className="btn btn-sm"
                                onClick={() => handleDelete(u.id, u.username)}
                                style={{
                                  background: "var(--accent-red)",
                                  color: "#fff",
                                  padding: "3px 10px",
                                  fontSize: "0.74rem",
                                }}
                              >
                                Igen
                              </button>
                              <button
                                className="btn btn-ghost btn-sm"
                                onClick={() => setConfirmDeleteId(null)}
                                style={{ padding: "3px 8px", fontSize: "0.74rem" }}
                              >
                                Mégse
                              </button>
                            </span>
                          ) : (
                            <div style={{ display: "inline-flex", gap: "8px" }}>
                              <button
                                className="btn btn-ghost btn-sm"
                                onClick={() => openEditModal(u)}
                                style={{ fontSize: "0.75rem", padding: "4px 10px" }}
                              >
                                Szerkesztés / Jelszó
                              </button>
                              {!isSelf && (
                                <button
                                  className="btn btn-ghost btn-sm"
                                  onClick={() => setConfirmDeleteId(u.id)}
                                  style={{
                                    fontSize: "0.75rem",
                                    padding: "4px 10px",
                                    color: "var(--accent-red)",
                                  }}
                                >
                                  Törlés
                                </button>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* Edit modal */}
      {editingUser && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0, 0, 0, 0.6)",
            zIndex: 400,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "24px",
          }}
          onClick={() => setEditingUser(null)}
        >
          <form
            onSubmit={handleSaveEdit}
            onClick={(e) => e.stopPropagation()}
            className="card animate-fade-in"
            style={{
              width: "100%",
              maxWidth: 420,
              padding: "24px",
              background: "var(--bg-secondary)",
              border: "1px solid var(--border-subtle)",
              borderRadius: "var(--radius-md)",
              display: "flex",
              flexDirection: "column",
              gap: "14px",
            }}
          >
            <h3 style={{ margin: 0, fontSize: "1rem", fontWeight: 600 }}>
              Felhasználó szerkesztése: {editingUser.username}
            </h3>

            <div>
              <label style={{ display: "block", fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                Megjelenített név
              </label>
              <input
                type="text"
                value={editDisplayName}
                onChange={(e) => setEditDisplayName(e.target.value)}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--text-primary)",
                  fontSize: "0.85rem",
                }}
              />
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                Szerepkör
              </label>
              <select
                value={editRole}
                onChange={(e) => setEditRole(e.target.value as UserRole)}
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--text-primary)",
                  fontSize: "0.85rem",
                }}
              >
                <option value="member">Member (Tag — csak generálás + olvasás)</option>
                <option value="admin">Admin (Teljes hozzáférés)</option>
              </select>
            </div>

            <div>
              <label style={{ display: "block", fontSize: "0.76rem", color: "var(--text-secondary)", marginBottom: 4 }}>
                Új jelszó (hagyd üresen, ha nem változik)
              </label>
              <input
                type="password"
                value={editPassword}
                onChange={(e) => setEditPassword(e.target.value)}
                placeholder="Új jelszó megadása..."
                style={{
                  width: "100%",
                  padding: "8px 10px",
                  background: "var(--bg-tertiary)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--text-primary)",
                  fontSize: "0.85rem",
                }}
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px", marginTop: "6px" }}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setEditingUser(null)}
              >
                Mégse
              </button>
              <button
                type="submit"
                className="btn btn-primary btn-sm"
                disabled={savingEdit}
              >
                {savingEdit ? "Mentés..." : "Mentés"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

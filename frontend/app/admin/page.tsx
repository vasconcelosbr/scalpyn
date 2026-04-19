"use client";

import { useState, useEffect, useCallback } from "react";
import { apiGet, apiFetch } from "@/lib/api";
import { Users, ShieldAlert } from "lucide-react";

interface User {
  id: string;
  name: string;
  email: string;
  role: string;
  status: string;
  created_at: string;
}

interface UsersResponse {
  users: User[];
  total: number;
  page: number;
  per_page: number;
}

const ROLE_OPTIONS = ["admin", "operator", "viewer", "trader"] as const;

function roleBadgeStyle(role: string): React.CSSProperties {
  switch (role) {
    case "admin":
      return { background: "rgba(59,130,246,0.15)", color: "#3b82f6", border: "1px solid rgba(59,130,246,0.3)" };
    case "operator":
      return { background: "rgba(245,158,11,0.15)", color: "#f59e0b", border: "1px solid rgba(245,158,11,0.3)" };
    case "trader":
      return { background: "rgba(34,197,94,0.15)", color: "#22c55e", border: "1px solid rgba(34,197,94,0.3)" };
    default:
      return { background: "var(--bg-hover)", color: "var(--text-secondary)", border: "1px solid var(--border-subtle)" };
  }
}

export default function AdminPage() {
  const [users, setUsers] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const perPage = 20;

  const fetchUsers = useCallback(async () => {
    setLoading(true);
    setForbidden(false);
    try {
      const data = await apiGet<UsersResponse>(`/backoffice/admin/users?page=${page}&per_page=${perPage}`);
      setUsers(data.users);
      setTotal(data.total);
    } catch (e: unknown) {
      if (e instanceof Error && e.message.includes("403")) {
        setForbidden(true);
      }
      setUsers([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  const handleRoleChange = async (userId: string, newRole: string) => {
    try {
      await apiFetch(`/backoffice/admin/users/${userId}/role`, {
        method: "PUT",
        body: JSON.stringify({ role: newRole }),
      });
      fetchUsers();
    } catch {
      // Role change failed
    }
  };

  const totalPages = Math.ceil(total / perPage);

  if (forbidden) {
    return (
      <div style={{ padding: "24px", maxWidth: 960, margin: "0 auto" }}>
        <div style={{
          padding: 40,
          textAlign: "center",
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 12,
        }}>
          <ShieldAlert size={36} style={{ color: "var(--color-warning)" }} />
          <h2 style={{ fontSize: 18, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>Access Denied</h2>
          <p style={{ fontSize: 14, color: "var(--text-tertiary)", margin: 0 }}>
            You do not have admin privileges to view this page.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ padding: "24px", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 24 }}>
        <Users size={22} style={{ color: "var(--accent-primary)" }} />
        <h1 style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
          User Administration
        </h1>
      </div>

      {loading ? (
        <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>Loading…</div>
      ) : (
        <>
          {/* User Table */}
          <div style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 8,
            overflow: "hidden",
          }}>
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border-subtle)", background: "var(--bg-surface)" }}>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Name</th>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Email</th>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Role</th>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Status</th>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Created</th>
                    <th style={{ padding: "10px 14px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td style={{ padding: "10px 14px", color: "var(--text-primary)", fontWeight: 500 }}>{user.name}</td>
                      <td style={{ padding: "10px 14px", color: "var(--text-secondary)" }}>{user.email}</td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{
                          padding: "2px 8px",
                          borderRadius: 4,
                          fontSize: 11,
                          fontWeight: 600,
                          textTransform: "capitalize",
                          ...roleBadgeStyle(user.role),
                        }}>
                          {user.role}
                        </span>
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
                          <span style={{
                            width: 7,
                            height: 7,
                            borderRadius: "50%",
                            background: user.status === "active" ? "#22c55e" : "#ef4444",
                          }} />
                          <span style={{ color: "var(--text-secondary)", textTransform: "capitalize" }}>{user.status}</span>
                        </span>
                      </td>
                      <td style={{ padding: "10px 14px", color: "var(--text-tertiary)", fontSize: 12 }}>
                        {new Date(user.created_at).toLocaleDateString()}
                      </td>
                      <td style={{ padding: "10px 14px" }}>
                        <select
                          value={user.role}
                          onChange={(e) => handleRoleChange(user.id, e.target.value)}
                          style={{
                            padding: "4px 8px",
                            borderRadius: 4,
                            border: "1px solid var(--border-default)",
                            background: "var(--bg-input)",
                            color: "var(--text-primary)",
                            fontSize: 12,
                            cursor: "pointer",
                          }}
                        >
                          {ROLE_OPTIONS.map((r) => (
                            <option key={r} value={r}>{r}</option>
                          ))}
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination */}
          {totalPages > 1 && (
            <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: 20 }}>
              <button
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
                style={{
                  padding: "6px 12px",
                  borderRadius: 5,
                  border: "1px solid var(--border-subtle)",
                  background: "var(--bg-surface)",
                  color: "var(--text-secondary)",
                  cursor: page <= 1 ? "not-allowed" : "pointer",
                  opacity: page <= 1 ? 0.5 : 1,
                  fontSize: 13,
                }}
              >
                Previous
              </button>
              <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
                Page {page} of {totalPages}
              </span>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage((p) => p + 1)}
                style={{
                  padding: "6px 12px",
                  borderRadius: 5,
                  border: "1px solid var(--border-subtle)",
                  background: "var(--bg-surface)",
                  color: "var(--text-secondary)",
                  cursor: page >= totalPages ? "not-allowed" : "pointer",
                  opacity: page >= totalPages ? 0.5 : 1,
                  fontSize: 13,
                }}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

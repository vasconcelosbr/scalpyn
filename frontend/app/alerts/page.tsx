"use client";

import { useState, useEffect, useCallback } from "react";
import { apiGet, apiPost } from "@/lib/api";
import { Bell, AlertTriangle, AlertCircle, Info, CheckCircle } from "lucide-react";

interface Alert {
  id: string;
  alert_type: string;
  category: string;
  message: string;
  status: string;
  created_at: string;
}

interface AlertsResponse {
  alerts: Alert[];
  total: number;
  page: number;
  per_page: number;
}

function timeAgo(d: string) {
  const s = (Date.now() - new Date(d).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h ago";
  return Math.floor(s / 86400) + "d ago";
}

const STATUS_TABS = ["all", "active", "acknowledged", "resolved"] as const;
const TYPE_FILTERS = ["all", "warning", "critical", "info"] as const;

export default function AlertCenterPage() {
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState<string>("all");
  const [alertType, setAlertType] = useState<string>("all");
  const [loading, setLoading] = useState(true);
  const perPage = 20;

  const fetchAlerts = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (status !== "all") params.set("status", status);
      if (alertType !== "all") params.set("type", alertType);
      params.set("page", String(page));
      params.set("per_page", String(perPage));
      const data = await apiGet<AlertsResponse>(`/backoffice/alerts?${params.toString()}`);
      setAlerts(data.alerts);
      setTotal(data.total);
    } catch {
      setAlerts([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [status, alertType, page]);

  useEffect(() => {
    fetchAlerts();
  }, [fetchAlerts]);

  const handleAcknowledge = async (alertId: string) => {
    await apiPost("/backoffice/alerts/acknowledge", { alert_id: alertId });
    fetchAlerts();
  };

  const handleResolve = async (alertId: string) => {
    await apiPost("/backoffice/alerts/resolve", { alert_id: alertId });
    fetchAlerts();
  };

  const totalPages = Math.ceil(total / perPage);

  const typeBadgeStyle = (type: string) => {
    switch (type) {
      case "critical":
        return { background: "rgba(239,68,68,0.15)", border: "1px solid rgba(239,68,68,0.4)", color: "#ef4444" };
      case "warning":
        return { background: "rgba(245,158,11,0.15)", border: "1px solid rgba(245,158,11,0.4)", color: "#f59e0b" };
      case "info":
        return { background: "rgba(59,130,246,0.15)", border: "1px solid rgba(59,130,246,0.4)", color: "#3b82f6" };
      default:
        return { background: "var(--bg-elevated)", border: "1px solid var(--border-subtle)", color: "var(--text-secondary)" };
    }
  };

  const typeIcon = (type: string) => {
    switch (type) {
      case "critical": return <AlertCircle size={14} />;
      case "warning": return <AlertTriangle size={14} />;
      case "info": return <Info size={14} />;
      default: return <Bell size={14} />;
    }
  };

  return (
    <div style={{ padding: "24px", maxWidth: 960, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", marginBottom: 24 }}>
        Alert Center
      </h1>

      {/* Status Tabs */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {STATUS_TABS.map((tab) => (
          <button
            key={tab}
            onClick={() => { setStatus(tab); setPage(1); }}
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid var(--border-subtle)",
              background: status === tab ? "var(--accent-primary)" : "var(--bg-surface)",
              color: status === tab ? "#fff" : "var(--text-secondary)",
              cursor: "pointer",
              fontSize: 13,
              fontWeight: 500,
              textTransform: "capitalize",
            }}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Type Filter */}
      <div style={{ display: "flex", gap: 8, marginBottom: 24 }}>
        {TYPE_FILTERS.map((t) => (
          <button
            key={t}
            onClick={() => { setAlertType(t); setPage(1); }}
            style={{
              padding: "4px 12px",
              borderRadius: 4,
              border: "1px solid var(--border-subtle)",
              background: alertType === t ? "var(--bg-active)" : "var(--bg-surface)",
              color: alertType === t ? "var(--text-primary)" : "var(--text-tertiary)",
              cursor: "pointer",
              fontSize: 12,
              textTransform: "capitalize",
            }}
          >
            {t}
          </button>
        ))}
      </div>

      {/* Alert Cards */}
      {loading ? (
        <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>Loading…</div>
      ) : alerts.length === 0 ? (
        <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <CheckCircle size={32} />
          <span>No alerts found</span>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {alerts.map((alert) => (
            <div
              key={alert.id}
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border-subtle)",
                borderRadius: 8,
                padding: 16,
                display: "flex",
                flexDirection: "column",
                gap: 10,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                {/* Type badge */}
                <span style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  padding: "2px 8px",
                  borderRadius: 4,
                  fontSize: 11,
                  fontWeight: 600,
                  textTransform: "uppercase",
                  ...typeBadgeStyle(alert.alert_type),
                }}>
                  {typeIcon(alert.alert_type)}
                  {alert.alert_type}
                </span>
                {/* Category badge */}
                <span style={{
                  padding: "2px 8px",
                  borderRadius: 4,
                  fontSize: 11,
                  background: "var(--bg-hover)",
                  color: "var(--text-secondary)",
                  border: "1px solid var(--border-subtle)",
                }}>
                  {alert.category}
                </span>
                {/* Time */}
                <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--text-tertiary)" }}>
                  {timeAgo(alert.created_at)}
                </span>
              </div>

              <p style={{ fontSize: 14, color: "var(--text-primary)", margin: 0 }}>{alert.message}</p>

              {/* Actions */}
              <div style={{ display: "flex", gap: 8 }}>
                {alert.status === "active" && (
                  <button
                    onClick={() => handleAcknowledge(alert.id)}
                    style={{
                      padding: "5px 12px",
                      borderRadius: 5,
                      border: "1px solid var(--accent-primary-border)",
                      background: "var(--accent-primary-muted)",
                      color: "var(--accent-primary)",
                      cursor: "pointer",
                      fontSize: 12,
                      fontWeight: 500,
                    }}
                  >
                    Acknowledge
                  </button>
                )}
                {alert.status === "acknowledged" && (
                  <button
                    onClick={() => handleResolve(alert.id)}
                    style={{
                      padding: "5px 12px",
                      borderRadius: 5,
                      border: "1px solid rgba(34,197,94,0.4)",
                      background: "rgba(34,197,94,0.1)",
                      color: "#22c55e",
                      cursor: "pointer",
                      fontSize: 12,
                      fontWeight: 500,
                    }}
                  >
                    Resolve
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: 24 }}>
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
    </div>
  );
}

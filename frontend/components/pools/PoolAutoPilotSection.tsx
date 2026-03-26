"use client";

import { useState } from "react";
import { Bot, Zap } from "lucide-react";

interface Props {
  poolId: string;
  autopilotEnabled: boolean;
  onToggle?: (enabled: boolean) => void;
}

export default function PoolAutoPilotSection({ poolId, autopilotEnabled, onToggle }: Props) {
  const [enabled, setEnabled] = useState(autopilotEnabled);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/pools/${poolId}/autopilot/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !enabled }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || "Erro ao alterar AutoPilot");
        return;
      }
      setEnabled(data.autopilot_enabled);
      onToggle?.(data.autopilot_enabled);
    } catch (e: any) {
      setError(`Erro de conexão: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 8,
        padding: "12px 14px",
        borderRadius: 10,
        background: enabled
          ? "linear-gradient(135deg,rgba(59,130,246,0.1),rgba(37,99,235,0.06))"
          : "var(--bg-elevated, #1a1a2e)",
        border: `1px solid ${enabled ? "rgba(59,130,246,0.3)" : "rgba(255,255,255,0.07)"}`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <Bot size={16} color={enabled ? "#3B82F6" : "#6B7280"} />
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: enabled ? "#93C5FD" : "#9CA3AF" }}>
              Auto-Pilot
            </div>
            <div style={{ fontSize: 11, color: "#6B7280" }}>
              {enabled
                ? "Ativo — pool atualiza automaticamente"
                : "Inativo — pool requer atualização manual"}
            </div>
          </div>
        </div>
        <button
          onClick={toggle}
          disabled={loading}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "6px 12px",
            fontSize: 12,
            fontWeight: 700,
            borderRadius: 6,
            border: "none",
            cursor: loading ? "not-allowed" : "pointer",
            background: enabled ? "#3B82F6" : "rgba(255,255,255,0.08)",
            color: enabled ? "#fff" : "#9CA3AF",
            transition: "all 200ms",
          }}
        >
          <Zap size={12} />
          {loading ? "..." : enabled ? "Desativar" : "Ativar"}
        </button>
      </div>
      {error && (
        <div style={{ fontSize: 11, color: "#F87171", paddingTop: 4 }}>{error}</div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { Sparkles, CheckCircle2, XCircle, ChevronDown, ChevronUp } from "lucide-react";

interface PoolPresetIAResult {
  status: string;
  pool_id: string;
  regime: string;
  macro_risk: string;
  analysis_summary: string;
  recommendations: {
    min_volume_24h?: number | null;
    min_market_cap?: number | null;
    max_assets?: number | null;
    remove_symbols?: string[];
    add_symbols?: string[];
  };
  executed_at: string;
}

interface Props {
  poolId: string;
  onSuccess?: (result: PoolPresetIAResult) => void;
}

const RISK_COLOR: Record<string, string> = {
  LOW: "#34D399",
  MEDIUM: "#FBBF24",
  HIGH: "#F97316",
  EXTREME: "#F87171",
};

export default function PoolConfigureButton({ poolId, onSuccess }: Props) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PoolPresetIAResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showDetail, setShowDetail] = useState(false);
  const [applying, setApplying] = useState(false);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const res = await fetch(`/api/pools/${poolId}/preset-ia`, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `Erro ${res.status}`);
        return;
      }
      setResult(data);
      onSuccess?.(data);
    } catch (e: any) {
      setError(`Erro de conexão: ${e.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleApply = async () => {
    if (!result) return;
    setApplying(true);
    try {
      const res = await fetch(`/api/pools/${poolId}/preset-ia/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendations: result.recommendations }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.detail || `Erro ao aplicar: ${res.status}`);
        return;
      }
      onSuccess?.(result);
      setResult(null);
    } catch (e: any) {
      setError(`Erro ao aplicar: ${e.message}`);
    } finally {
      setApplying(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <button
        onClick={handleRun}
        disabled={loading}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 6,
          padding: "9px 14px",
          fontSize: 13,
          fontWeight: 700,
          borderRadius: 8,
          border: "none",
          cursor: loading ? "not-allowed" : "pointer",
          background: loading
            ? "linear-gradient(135deg,#6B3FA0,#4F7BF7)"
            : "linear-gradient(135deg,#8B5CF6,#4F7BF7)",
          color: "#fff",
          transition: "all 200ms",
          boxShadow: !loading ? "0 2px 16px rgba(139,92,246,0.25)" : "none",
          width: "100%",
        }}
      >
        <Sparkles size={14} style={loading ? { animation: "spin 1s linear infinite" } : {}} />
        {loading ? "Analisando pool..." : "✨ Configurar com IA"}
      </button>

      {error && (
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 8,
            padding: "10px 12px",
            borderRadius: 8,
            fontSize: 12,
            background: "rgba(248,113,113,0.1)",
            border: "1px solid rgba(248,113,113,0.3)",
            color: "#F87171",
          }}
        >
          <XCircle size={13} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div
          style={{
            background: "var(--bg-elevated, #1a1a2e)",
            border: "1px solid rgba(139,92,246,0.25)",
            borderRadius: 10,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              padding: "10px 14px",
              background: "linear-gradient(135deg,rgba(139,92,246,0.1),rgba(79,123,247,0.06))",
              borderBottom: "1px solid rgba(255,255,255,0.05)",
            }}
          >
            <CheckCircle2 size={14} color="#8B5CF6" />
            <span style={{ fontSize: 12, fontWeight: 700, flex: 1 }}>
              Análise concluída — {result.regime}
            </span>
            <span
              style={{
                fontSize: 10,
                fontWeight: 700,
                color: RISK_COLOR[result.macro_risk] || "#8B92A5",
              }}
            >
              {result.macro_risk}
            </span>
            <button
              onClick={() => setShowDetail((v) => !v)}
              style={{ background: "none", border: "none", cursor: "pointer", color: "#8B92A5" }}
            >
              {showDetail ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
          </div>

          {showDetail && (
            <div style={{ padding: "10px 14px", fontSize: 12, color: "#8B92A5", lineHeight: 1.6 }}>
              <p style={{ marginBottom: 8 }}>{result.analysis_summary}</p>
              <pre
                style={{
                  background: "rgba(0,0,0,0.2)",
                  borderRadius: 6,
                  padding: 8,
                  fontSize: 11,
                  overflowX: "auto",
                }}
              >
                {JSON.stringify(result.recommendations, null, 2)}
              </pre>
            </div>
          )}

          <div
            style={{
              display: "flex",
              gap: 8,
              padding: "10px 14px",
              borderTop: "1px solid rgba(255,255,255,0.05)",
            }}
          >
            <button
              onClick={() => setResult(null)}
              style={{
                flex: 1,
                padding: "7px 12px",
                fontSize: 12,
                borderRadius: 6,
                border: "1px solid rgba(255,255,255,0.1)",
                background: "transparent",
                color: "#8B92A5",
                cursor: "pointer",
              }}
            >
              Descartar
            </button>
            <button
              onClick={handleApply}
              disabled={applying}
              style={{
                flex: 2,
                padding: "7px 12px",
                fontSize: 12,
                fontWeight: 700,
                borderRadius: 6,
                border: "none",
                background: "#8B5CF6",
                color: "#fff",
                cursor: applying ? "not-allowed" : "pointer",
              }}
            >
              {applying ? "Aplicando..." : "Aplicar Recomendações"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";
import { apiPost } from "@/lib/api";
import { Play, ArrowUp, ArrowDown, Minus, FlaskConical } from "lucide-react";

interface SimResult {
  original: { score: number; decision: string; confidence: number };
  simulated: { score: number; decision: string; confidence: number };
  notes: string[];
}

interface ReplayApiResponse {
  result: {
    original: { score: number; decision: string; latency_ms?: number };
    replay: { score: number; decision: string; latency_ms?: number };
    diff?: { score_delta?: number };
  };
  note?: string;
}

export default function ReplayPage() {
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("L1");
  const [rsiPeriod, setRsiPeriod] = useState(14);
  const [adxPeriod, setAdxPeriod] = useState(14);
  const [minScore, setMinScore] = useState(60);
  const [result, setResult] = useState<SimResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runSimulation = async () => {
    if (!symbol.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const response = await apiPost<ReplayApiResponse>("/backoffice/replay/run", {
        symbol: symbol.trim().toUpperCase(),
        strategy,
        params: { rsi_period: rsiPeriod, adx_period: adxPeriod, min_score: minScore },
      });
      setResult({
        original: {
          score: response.result.original.score,
          decision: response.result.original.decision,
          confidence: 0,
        },
        simulated: {
          score: response.result.replay.score,
          decision: response.result.replay.decision,
          confidence: 0,
        },
        notes: response.note ? [response.note] : [],
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Simulation failed");
      setResult(null);
    } finally {
      setLoading(false);
    }
  };

  const deltaIndicator = (original: number, simulated: number) => {
    const diff = simulated - original;
    if (diff > 0) return <span style={{ color: "var(--color-profit)", display: "inline-flex", alignItems: "center", gap: 2 }}><ArrowUp size={12} />+{diff.toFixed(1)}</span>;
    if (diff < 0) return <span style={{ color: "var(--color-loss)", display: "inline-flex", alignItems: "center", gap: 2 }}><ArrowDown size={12} />{diff.toFixed(1)}</span>;
    return <span style={{ color: "var(--text-tertiary)", display: "inline-flex", alignItems: "center", gap: 2 }}><Minus size={12} />0</span>;
  };

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "8px 12px",
    borderRadius: 6,
    border: "1px solid var(--border-default)",
    background: "var(--bg-input)",
    color: "var(--text-primary)",
    fontSize: 13,
    outline: "none",
  };

  const labelStyle: React.CSSProperties = {
    fontSize: 12,
    fontWeight: 500,
    color: "var(--text-secondary)",
    marginBottom: 4,
    display: "block",
  };

  return (
    <div style={{ padding: "24px", maxWidth: 1100, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", marginBottom: 24 }}>
        Replay &amp; Simulation
      </h1>

      <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 24 }} className="md:!grid-cols-2">
        {/* Left Panel — Config */}
        <div style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          padding: 20,
        }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", marginBottom: 16 }}>Configuration</h2>

          <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
            <div>
              <label style={labelStyle}>Symbol</label>
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="e.g. BTC_USDT"
                style={inputStyle}
              />
            </div>

            <div>
              <label style={labelStyle}>Strategy</label>
              <select
                value={strategy}
                onChange={(e) => setStrategy(e.target.value)}
                style={inputStyle}
              >
                <option value="L1">L1</option>
                <option value="L2">L2</option>
                <option value="L3">L3</option>
              </select>
            </div>

            <div>
              <label style={labelStyle}>RSI Period</label>
              <input
                type="number"
                value={rsiPeriod}
                onChange={(e) => setRsiPeriod(Math.max(5, Math.min(50, Number(e.target.value))))}
                min={5}
                max={50}
                style={inputStyle}
              />
            </div>

            <div>
              <label style={labelStyle}>ADX Period</label>
              <input
                type="number"
                value={adxPeriod}
                onChange={(e) => setAdxPeriod(Math.max(5, Math.min(50, Number(e.target.value))))}
                min={5}
                max={50}
                style={inputStyle}
              />
            </div>

            <div>
              <label style={labelStyle}>Min Score</label>
              <input
                type="number"
                value={minScore}
                onChange={(e) => setMinScore(Math.max(0, Math.min(100, Number(e.target.value))))}
                min={0}
                max={100}
                style={inputStyle}
              />
            </div>

            <button
              onClick={runSimulation}
              disabled={loading || !symbol.trim()}
              style={{
                marginTop: 8,
                padding: "10px 20px",
                borderRadius: 6,
                border: "none",
                background: "var(--accent-primary)",
                color: "#fff",
                fontSize: 14,
                fontWeight: 600,
                cursor: loading || !symbol.trim() ? "not-allowed" : "pointer",
                opacity: loading || !symbol.trim() ? 0.6 : 1,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 8,
              }}
            >
              <Play size={14} />
              {loading ? "Running…" : "Run Simulation"}
            </button>
          </div>
        </div>

        {/* Right Panel — Results */}
        <div style={{
          background: "var(--bg-elevated)",
          border: "1px solid var(--border-subtle)",
          borderRadius: 8,
          padding: 20,
        }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", marginBottom: 16 }}>Results</h2>

          {loading ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-tertiary)" }}>
              Running simulation…
            </div>
          ) : error ? (
            <div style={{ padding: 20, textAlign: "center", color: "var(--color-loss)" }}>{error}</div>
          ) : !result ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-tertiary)", display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
              <FlaskConical size={32} />
              <span>Configure parameters and run a simulation</span>
            </div>
          ) : (
            <>
              {/* Comparison Table */}
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <th style={{ padding: "8px 12px", textAlign: "left", color: "var(--text-tertiary)", fontWeight: 500 }}></th>
                      <th style={{ padding: "8px 12px", textAlign: "center", color: "var(--text-tertiary)", fontWeight: 500 }}>Score</th>
                      <th style={{ padding: "8px 12px", textAlign: "center", color: "var(--text-tertiary)", fontWeight: 500 }}>Decision</th>
                      <th style={{ padding: "8px 12px", textAlign: "center", color: "var(--text-tertiary)", fontWeight: 500 }}>Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td style={{ padding: "10px 12px", color: "var(--text-secondary)", fontWeight: 500 }}>Original</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.original.score}</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.original.decision}</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.original.confidence}%</td>
                    </tr>
                    <tr style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                      <td style={{ padding: "10px 12px", color: "var(--text-secondary)", fontWeight: 500 }}>Simulated</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.simulated.score}</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.simulated.decision}</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-primary)" }}>{result.simulated.confidence}%</td>
                    </tr>
                    <tr>
                      <td style={{ padding: "10px 12px", color: "var(--text-tertiary)", fontWeight: 500 }}>Delta</td>
                      <td style={{ padding: "10px 12px", textAlign: "center" }}>{deltaIndicator(result.original.score, result.simulated.score)}</td>
                      <td style={{ padding: "10px 12px", textAlign: "center", color: "var(--text-tertiary)" }}>—</td>
                      <td style={{ padding: "10px 12px", textAlign: "center" }}>{deltaIndicator(result.original.confidence, result.simulated.confidence)}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              {/* Notes */}
              {result.notes && result.notes.length > 0 && (
                <div style={{ marginTop: 16, padding: 12, background: "var(--bg-hover)", borderRadius: 6 }}>
                  <h3 style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>Notes</h3>
                  <ul style={{ margin: 0, paddingLeft: 16 }}>
                    {result.notes.map((note, i) => (
                      <li key={i} style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 4 }}>{note}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

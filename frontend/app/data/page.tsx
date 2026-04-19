"use client";

import { useState, useEffect } from "react";
import { apiGet } from "@/lib/api";
import { Activity, Clock, AlertTriangle, Gauge } from "lucide-react";

interface IntegrityData {
  feed_delay_seconds: number;
  stale_symbols: number;
  pipeline_latency_ms: number;
}

function statusColor(level: "green" | "amber" | "red") {
  switch (level) {
    case "green": return "#22c55e";
    case "amber": return "#f59e0b";
    case "red": return "#ef4444";
  }
}

function feedDelayLevel(v: number): "green" | "amber" | "red" {
  if (v < 5) return "green";
  if (v < 30) return "amber";
  return "red";
}

function staleSymbolsLevel(v: number): "green" | "amber" | "red" {
  if (v === 0) return "green";
  if (v < 5) return "amber";
  return "red";
}

function pipelineLatencyLevel(v: number): "green" | "amber" | "red" {
  if (v < 500) return "green";
  if (v < 2000) return "amber";
  return "red";
}

function overallHealth(data: IntegrityData): "HEALTHY" | "DEGRADED" | "CRITICAL" {
  const levels = [
    feedDelayLevel(data.feed_delay_seconds),
    staleSymbolsLevel(data.stale_symbols),
    pipelineLatencyLevel(data.pipeline_latency_ms),
  ];
  if (levels.includes("red")) return "CRITICAL";
  if (levels.includes("amber")) return "DEGRADED";
  return "HEALTHY";
}

export default function DataMonitorPage() {
  const [data, setData] = useState<IntegrityData | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const result = await apiGet<IntegrityData>("/backoffice/data/integrity");
      setData(result);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const health = data ? overallHealth(data) : null;
  const healthColor = health === "HEALTHY" ? "#22c55e" : health === "DEGRADED" ? "#f59e0b" : "#ef4444";

  return (
    <div style={{ padding: "24px", maxWidth: 960, margin: "0 auto" }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", marginBottom: 4 }}>
          Data Monitor
        </h1>
        <p style={{ fontSize: 14, color: "var(--text-tertiary)", margin: 0 }}>
          Feed integrity &amp; health
        </p>
      </div>

      {loading ? (
        <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>Loading…</div>
      ) : !data ? (
        <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>
          Unable to fetch integrity data
        </div>
      ) : (
        <>
          {/* Health Status Bar */}
          <div style={{
            background: "var(--bg-elevated)",
            border: "1px solid var(--border-subtle)",
            borderRadius: 8,
            padding: "12px 20px",
            marginBottom: 24,
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}>
            <span style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: healthColor,
              boxShadow: `0 0 6px ${healthColor}`,
              flexShrink: 0,
            }} />
            <span style={{ fontSize: 14, fontWeight: 600, color: healthColor }}>
              {health}
            </span>
            <span style={{ fontSize: 12, color: "var(--text-tertiary)", marginLeft: "auto" }}>
              Auto-refresh every 30s
            </span>
          </div>

          {/* Metric Cards */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16 }}>
            {/* Feed Delay */}
            {(() => {
              const level = feedDelayLevel(data.feed_delay_seconds);
              const color = statusColor(level);
              return (
                <div style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 8,
                  padding: 20,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <Clock size={16} style={{ color }} />
                    <span style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 500 }}>Feed Delay</span>
                    <span style={{
                      marginLeft: "auto",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: color,
                      boxShadow: `0 0 4px ${color}`,
                    }} />
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, color }}>
                    {data.feed_delay_seconds.toFixed(1)}
                    <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 4 }}>s</span>
                  </div>
                </div>
              );
            })()}

            {/* Stale Symbols */}
            {(() => {
              const level = staleSymbolsLevel(data.stale_symbols);
              const color = statusColor(level);
              return (
                <div style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 8,
                  padding: 20,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <AlertTriangle size={16} style={{ color }} />
                    <span style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 500 }}>Stale Symbols</span>
                    <span style={{
                      marginLeft: "auto",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: color,
                      boxShadow: `0 0 4px ${color}`,
                    }} />
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, color }}>
                    {data.stale_symbols}
                    <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 4 }}>symbols</span>
                  </div>
                </div>
              );
            })()}

            {/* Pipeline Latency */}
            {(() => {
              const level = pipelineLatencyLevel(data.pipeline_latency_ms);
              const color = statusColor(level);
              return (
                <div style={{
                  background: "var(--bg-elevated)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 8,
                  padding: 20,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
                    <Gauge size={16} style={{ color }} />
                    <span style={{ fontSize: 13, color: "var(--text-secondary)", fontWeight: 500 }}>Pipeline Latency</span>
                    <span style={{
                      marginLeft: "auto",
                      width: 8,
                      height: 8,
                      borderRadius: "50%",
                      background: color,
                      boxShadow: `0 0 4px ${color}`,
                    }} />
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 700, color }}>
                    {data.pipeline_latency_ms.toFixed(0)}
                    <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 4 }}>ms</span>
                  </div>
                </div>
              );
            })()}
          </div>
        </>
      )}
    </div>
  );
}

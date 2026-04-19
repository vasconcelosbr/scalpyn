"use client";

import { useEffect, useState, useCallback } from "react";
import { Search, Filter, Download, ChevronDown, ChevronRight, FileText } from "lucide-react";
import { apiGet } from "@/lib/api";

interface DecisionEntry {
  id: string;
  trace_id: string;
  time: string;
  symbol: string;
  strategy: string;
  score: number;
  signal: string;
  confidence: number;
  decision: "approved" | "rejected";
  payload_json?: Record<string, unknown>;
}

interface DecisionResponse {
  items: DecisionEntry[];
  total: number;
  page: number;
  per_page: number;
}

function scoreColor(score: number): string {
  if (score >= 80) return "var(--score-excellent)";
  if (score >= 60) return "var(--score-good)";
  if (score >= 40) return "var(--score-neutral)";
  if (score >= 25) return "var(--score-low)";
  return "var(--score-critical)";
}

function strategyBadge(strategy: string): string {
  switch (strategy) {
    case "L1":
      return "bg-[rgba(79,123,247,0.12)] text-[#4F7BF7] border border-[rgba(79,123,247,0.25)]";
    case "L2":
      return "bg-[var(--color-warning-muted)] text-[var(--color-warning)] border border-[rgba(251,191,36,0.25)]";
    case "L3":
      return "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border border-[var(--color-profit-border)]";
    default:
      return "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-default)]";
  }
}

export default function DecisionsPage() {
  const [data, setData] = useState<DecisionResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Filters
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [scoreMin, setScoreMin] = useState("");
  const [scoreMax, setScoreMax] = useState("");
  const [decision, setDecision] = useState("");
  const [page, setPage] = useState(1);
  const perPage = 20;

  const buildParams = useCallback(() => {
    const params = new URLSearchParams();
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    if (symbol) params.set("symbol", symbol);
    if (strategy) params.set("strategy", strategy);
    if (scoreMin) params.set("score_min", scoreMin);
    if (scoreMax) params.set("score_max", scoreMax);
    if (decision) params.set("decision", decision);
    params.set("page", String(page));
    params.set("per_page", String(perPage));
    return params;
  }, [startDate, endDate, symbol, strategy, scoreMin, scoreMax, decision, page]);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params = buildParams();
      const res = await apiGet<DecisionResponse>(`/backoffice/decisions?${params.toString()}`);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [buildParams]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleApply = () => {
    setPage(1);
    fetchData();
  };

  async function downloadCSV() {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    const params = new URLSearchParams();
    if (startDate) params.set("start_date", startDate);
    if (endDate) params.set("end_date", endDate);
    if (symbol) params.set("symbol", symbol);
    if (strategy) params.set("strategy", strategy);
    if (scoreMin) params.set("score_min", scoreMin);
    if (scoreMax) params.set("score_max", scoreMax);
    if (decision) params.set("decision", decision);

    const res = await fetch(`/api/backoffice/decisions/export?${params.toString()}`, {
      method: "POST",
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `decisions_${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Decision Log</h1>
          <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Complete audit trail</p>
        </div>
        <button
          onClick={downloadCSV}
          className="px-4 py-1.5 text-[12px] font-medium bg-[var(--bg-elevated)] border border-[var(--border-default)] text-[var(--text-secondary)] rounded-[var(--radius-sm)] hover:text-[var(--text-primary)] hover:border-[var(--border-strong)] transition-colors flex items-center gap-1.5"
        >
          <Download className="w-3.5 h-3.5" />
          Export CSV
        </button>
      </div>

      {/* Filter Bar */}
      <div className="card">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Start Date</label>
            <input
              type="date"
              value={startDate}
              onChange={(e) => setStartDate(e.target.value)}
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">End Date</label>
            <input
              type="date"
              value={endDate}
              onChange={(e) => setEndDate(e.target.value)}
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Symbol</label>
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[var(--text-tertiary)]" />
              <input
                type="text"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                placeholder="BTC, ETH..."
                className="pl-8 pr-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] w-[130px] focus:outline-none focus:border-[var(--accent-primary)]"
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Strategy</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
            >
              <option value="">All</option>
              <option value="L1">L1</option>
              <option value="L2">L2</option>
              <option value="L3">L3</option>
            </select>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Score Min</label>
            <input
              type="number"
              value={scoreMin}
              onChange={(e) => setScoreMin(e.target.value)}
              placeholder="0"
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] w-[80px] focus:outline-none focus:border-[var(--accent-primary)]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Score Max</label>
            <input
              type="number"
              value={scoreMax}
              onChange={(e) => setScoreMax(e.target.value)}
              placeholder="100"
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] placeholder:text-[var(--text-tertiary)] w-[80px] focus:outline-none focus:border-[var(--accent-primary)]"
            />
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-[11px] text-[var(--text-tertiary)] uppercase tracking-wider">Decision</label>
            <select
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
              className="px-3 py-1.5 text-[12px] bg-[var(--bg-input)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]"
            >
              <option value="">All</option>
              <option value="approved">Approved</option>
              <option value="rejected">Rejected</option>
            </select>
          </div>

          <button
            onClick={handleApply}
            className="px-4 py-1.5 text-[12px] font-medium bg-[var(--accent-primary)] text-white rounded-[var(--radius-sm)] hover:bg-[var(--accent-primary-hover)] transition-colors flex items-center gap-1.5"
          >
            <Filter className="w-3.5 h-3.5" />
            Apply
          </button>
        </div>
      </div>

      {/* Results Table */}
      <div className="card">
        <div className="card-header">
          <h3>Audit Entries</h3>
          <span className="caption">{data?.total ?? 0} decisions</span>
        </div>
        <div className="overflow-x-auto">
          {loading ? (
            <div className="p-8 space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="skeleton h-10 w-full" />
              ))}
            </div>
          ) : !data || data.items.length === 0 ? (
            <div className="text-center py-16 text-[var(--text-tertiary)]">
              <FileText className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-[13px]">No decisions found. Adjust filters or check back later.</p>
            </div>
          ) : (
            <table className="data-table text-[12px]">
              <thead>
                <tr>
                  <th className="w-8"></th>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th className="text-right">Score</th>
                  <th>Signal</th>
                  <th className="text-right">Confidence</th>
                  <th>Decision</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <DecisionRow
                    key={item.id}
                    item={item}
                    expanded={expandedId === item.id}
                    onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-[var(--border-default)]">
            <span className="text-[12px] text-[var(--text-secondary)]">
              Page {page} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="px-3 py-1 text-[12px] bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="px-3 py-1 text-[12px] bg-[var(--bg-elevated)] border border-[var(--border-default)] rounded-[var(--radius-sm)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function DecisionRow({ item, expanded, onToggle }: { item: DecisionEntry; expanded: boolean; onToggle: () => void }) {
  return (
    <>
      <tr onClick={onToggle} className="cursor-pointer">
        <td>
          {expanded ? (
            <ChevronDown className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-[var(--text-tertiary)]" />
          )}
        </td>
        <td className="text-[var(--text-secondary)]">
          {new Date(item.time).toLocaleString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
        </td>
        <td className="font-semibold">{item.symbol}</td>
        <td>
          <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${strategyBadge(item.strategy)}`}>
            {item.strategy}
          </span>
        </td>
        <td className="text-right font-mono" style={{ color: scoreColor(item.score) }}>
          {item.score.toFixed(1)}
        </td>
        <td className="text-[var(--text-secondary)]">{item.signal}</td>
        <td className="text-right font-mono text-[var(--text-primary)]">{(item.confidence * 100).toFixed(0)}%</td>
        <td>
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium ${
              item.decision === "approved"
                ? "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border border-[var(--color-profit-border)]"
                : "bg-[var(--color-loss-muted)] text-[var(--color-loss)] border border-[var(--color-loss-border)]"
            }`}
          >
            {item.decision}
          </span>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={8} className="!p-0">
            <DetailPanel item={item} />
          </td>
        </tr>
      )}
    </>
  );
}

function DetailPanel({ item }: { item: DecisionEntry }) {
  return (
    <div className="bg-[var(--bg-elevated)] border-t border-[var(--border-subtle)] p-4 space-y-4">
      {/* Trace ID */}
      <div>
        <span className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] font-semibold">Trace ID</span>
        <p className="font-mono text-[12px] text-[var(--text-primary)] mt-1 bg-[var(--bg-input)] px-3 py-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] inline-block">
          {item.trace_id}
        </p>
      </div>

      {/* Confidence Bar */}
      <div>
        <span className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] font-semibold">Confidence</span>
        <div className="mt-2 w-full max-w-[300px] h-2 bg-[var(--bg-input)] rounded-full overflow-hidden border border-[var(--border-default)]">
          <div
            className="h-full rounded-full bg-[var(--accent-primary)] transition-all"
            style={{ width: `${item.confidence * 100}%` }}
          />
        </div>
        <span className="text-[11px] text-[var(--text-secondary)] mt-1 inline-block">
          {(item.confidence * 100).toFixed(1)}%
        </span>
      </div>

      {/* Payload JSON Viewer */}
      <div>
        <span className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] font-semibold">Payload</span>
        <pre className="mt-2 text-[11px] font-mono text-[var(--text-primary)] bg-[var(--bg-input)] p-3 rounded-[var(--radius-sm)] border border-[var(--border-default)] overflow-x-auto max-h-[240px] overflow-y-auto">
          {item.payload_json ? JSON.stringify(item.payload_json, null, 2) : "No payload data"}
        </pre>
      </div>
    </div>
  );
}

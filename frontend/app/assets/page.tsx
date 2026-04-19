"use client";

import { useEffect, useState, useCallback } from "react";
import { Search, Filter, ChevronDown, ChevronRight, CheckCircle, XCircle } from "lucide-react";
import { apiGet } from "@/lib/api";

interface AssetTrace {
  id: string;
  symbol: string;
  decision: "approved" | "rejected";
  score: number;
  strategy: string;
  time: string;
  market_data_json?: Record<string, unknown>;
  indicators_json?: Record<string, unknown>;
  conditions_json?: Record<string, { passed: boolean; label?: string }>;
}

interface AssetResponse {
  items: AssetTrace[];
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

export default function AssetsPage() {
  const [data, setData] = useState<AssetResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Filters
  const [symbol, setSymbol] = useState("");
  const [decision, setDecision] = useState("");
  const [scoreMin, setScoreMin] = useState("");
  const [scoreMax, setScoreMax] = useState("");
  const [strategy, setStrategy] = useState("");
  const [page, setPage] = useState(1);
  const perPage = 20;

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (symbol) params.set("symbol", symbol);
      if (decision) params.set("decision", decision);
      if (scoreMin) params.set("score_min", scoreMin);
      if (scoreMax) params.set("score_max", scoreMax);
      if (strategy) params.set("strategy", strategy);
      params.set("page", String(page));
      params.set("per_page", String(perPage));

      const res = await apiGet<AssetResponse>(`/backoffice/assets?${params.toString()}`);
      setData(res);
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [symbol, decision, scoreMin, scoreMax, strategy, page]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const handleApply = () => {
    setPage(1);
    fetchData();
  };

  const totalPages = data ? Math.ceil(data.total / data.per_page) : 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Asset Trace</h1>
        <p className="text-[var(--text-secondary)] mt-1 text-[13px]">Deep pipeline debug</p>
      </div>

      {/* Filter Bar */}
      <div className="card">
        <div className="flex flex-wrap items-end gap-3 p-4">
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
          <h3>Pipeline Results</h3>
          <span className="caption">{data?.total ?? 0} traces</span>
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
              <Search className="w-8 h-8 mx-auto mb-2 opacity-30" />
              <p className="text-[13px]">No traces found. Adjust filters and try again.</p>
            </div>
          ) : (
            <table className="data-table text-[12px]">
              <thead>
                <tr>
                  <th className="w-8"></th>
                  <th>Symbol</th>
                  <th>Decision</th>
                  <th className="text-right">Score</th>
                  <th>Strategy</th>
                  <th>Time</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <TraceRow
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

function TraceRow({ item, expanded, onToggle }: { item: AssetTrace; expanded: boolean; onToggle: () => void }) {
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
        <td className="font-semibold">{item.symbol}</td>
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
        <td className="text-right font-mono" style={{ color: scoreColor(item.score) }}>
          {item.score.toFixed(1)}
        </td>
        <td>{item.strategy}</td>
        <td className="text-[var(--text-secondary)]">
          {new Date(item.time).toLocaleString("en", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="!p-0">
            <DetailPanel item={item} />
          </td>
        </tr>
      )}
    </>
  );
}

function DetailPanel({ item }: { item: AssetTrace }) {
  return (
    <div className="bg-[var(--bg-elevated)] border-t border-[var(--border-subtle)] p-4 space-y-4">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Market Data */}
        <div>
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] mb-2 font-semibold">Market Data</h4>
          <div className="space-y-1">
            {item.market_data_json ? (
              Object.entries(item.market_data_json).map(([key, value]) => (
                <div key={key} className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-secondary)]">{key}</span>
                  <span className="text-[var(--text-primary)] font-mono">{String(value)}</span>
                </div>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-tertiary)]">No data</span>
            )}
          </div>
        </div>

        {/* Indicators */}
        <div>
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] mb-2 font-semibold">Indicators</h4>
          <div className="space-y-1">
            {item.indicators_json ? (
              Object.entries(item.indicators_json).map(([key, value]) => (
                <div key={key} className="flex justify-between text-[12px]">
                  <span className="text-[var(--text-secondary)]">{key}</span>
                  <span className="text-[var(--text-primary)] font-mono text-[11px]">{String(value)}</span>
                </div>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-tertiary)]">No data</span>
            )}
          </div>
        </div>

        {/* Conditions */}
        <div>
          <h4 className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)] mb-2 font-semibold">Conditions</h4>
          <div className="space-y-1.5">
            {item.conditions_json ? (
              Object.entries(item.conditions_json).map(([key, cond]) => (
                <div key={key} className="flex items-center gap-2 text-[12px]">
                  {cond.passed ? (
                    <CheckCircle className="w-3.5 h-3.5 text-[var(--color-profit)] flex-shrink-0" />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-[var(--color-loss)] flex-shrink-0" />
                  )}
                  <span className="text-[var(--text-secondary)]">{cond.label ?? key}</span>
                </div>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-tertiary)]">No data</span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

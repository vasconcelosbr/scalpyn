"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight, CheckCircle2, XCircle } from "lucide-react";
import {
  EvaluationTraceBreakdown,
  blockRuleOutcome,
  classifySkip,
  formatEvaluationTraceValue,
  type EvaluationTraceItem,
} from "./EvaluationTraceBreakdown";
import type { ScoreRule } from "./PipelineAssetTable";
import { RULE_COLORS, fmtPts, sortScoreRules } from "./PipelineAssetTable";
import { scoreBand, scorePct, SCORE_TOOLTIP, RULES_TOOLTIP } from "@/lib/scoreBand";
import { summarizeScoreRules, fmtConfidence } from "@/lib/scoreRulesSummary";

const DECISION_SUMMARY_INDICATOR_LIMIT = 3;

export interface DecisionTraceItem {
  type: "filter" | "block_rule" | "entry_trigger" | "signal";
  indicator: string;
  condition: string;
  expected?: string | null;
  current_value?: unknown;
  status: "PASS" | "FAIL" | "SKIPPED";
  reason?: string | null;
  outcome?: "OK" | "TRIPPED" | "SKIPPED";
  condition_matched?: boolean | null;
}

export interface DecisionDetails {
  filters: DecisionTraceItem[];
  indicators: string[];
  conditions: string[];
  current_values: Record<string, unknown>;
  expected_values: Record<string, string | null>;
  evaluation_trace: DecisionTraceItem[];
}

export interface WatchlistDecisionItem {
  symbol: string;
  status: "approved" | "rejected";
  stage?: string | null;
  profile_id?: string | null;
  timestamp?: string | null;
  alpha_score?: number | null;
  score_rules?: ScoreRule[];
  failed_indicators: string[];
  conditions: string[];
  current_values: Record<string, unknown>;
  expected_values: Record<string, string | null>;
  details: DecisionDetails;
  /** ML model outputs — populated when ml_enabled + use_ml_ranking are active. */
  ml_probability?: number | null;
  ml_final_score?: number | null;
  blocked_by_ml?: boolean | null;
  crypto_ev?: CryptoEVSummary | null;
}

export type RejectedTraceItem = DecisionTraceItem;
export type RejectedAssetItem = WatchlistDecisionItem;

interface CryptoEVSummary {
  score: number | null;
  state: string;
  n_trades: number;
  n_excluded_unreplayable?: number | null;
  w?: number | null;
  computed_at?: string | null;
}

function fmtValue(value: unknown): string {
  return formatEvaluationTraceValue(value);
}

function scoreColor(pct: number | null): string {
  if (pct == null) return "#64748B";
  if (pct >= 70) return "#34D399";
  if (pct >= 45) return "#FBBF24";
  return "#F87171";
}

function ScoreBar({ value }: { value?: number | null }) {
  if (value == null) return <span className="text-[#334155] text-xs">—</span>;
  const pct = Math.min(100, Math.max(0, value));
  const color = scoreColor(pct);
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <div className="relative flex-1 h-1.5 bg-[#1A2035] rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-sm font-bold tabular-nums w-7 text-right" style={{ color }}>
        {Math.round(value)}
      </span>
    </div>
  );
}

function cryptoEvClass(state: string | null | undefined): string {
  if (state === "FAVORABLE") return "bg-[#34D399]/10 text-[#34D399] border-[#34D399]/20";
  if (state === "RISKY") return "bg-[#FBBF24]/10 text-[#FBBF24] border-[#FBBF24]/20";
  if (state === "AVOID") return "bg-[#F87171]/10 text-[#F87171] border-[#F87171]/20";
  if (state === "NEUTRAL") return "bg-[#1E2433] text-[#94A3B8] border-[#334155]";
  return "bg-[#0A0C14] text-[#4B5563] border-[#1A2035]";
}

function CryptoEVBadge({ value }: { value?: CryptoEVSummary | null }) {
  if (!value || value.score == null) return <span className="text-[#4B5563]">-</span>;
  const tip = [
    `Crypto EV: ${Number(value.score).toFixed(1)}`,
    value.state ? `state ${value.state}` : null,
    `N=${value.n_trades ?? 0}`,
    value.n_excluded_unreplayable != null ? `unreplayable=${value.n_excluded_unreplayable}` : null,
    value.w != null ? `w=${Number(value.w).toFixed(2)}` : null,
    value.computed_at ? `as of ${value.computed_at}` : null,
  ].filter(Boolean).join(" | ");
  return (
    <span
      className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border tabular-nums ${cryptoEvClass(value.state)}`}
      title={tip}
    >
      {Number(value.score).toFixed(0)}
    </span>
  );
}

function itemPalette(status: "approved" | "rejected") {
  return status === "approved"
    ? {
        badge: "bg-[#34D399]/10 text-[#86EFAC] border border-[#34D399]/25",
        row: "border-l-2 border-l-[#34D399]/60",
        accent: "text-[#86EFAC]",
      }
    : {
        badge: "bg-[#F87171]/10 text-[#FCA5A5] border border-[#F87171]/25",
        row: "border-l-2 border-l-[#F87171]/60",
        accent: "text-[#FCA5A5]",
      };
}

function summarizeIndicators(item: WatchlistDecisionItem): string {
  if (item.failed_indicators.length > 0) return item.failed_indicators.join(", ");
  return item.details.indicators.slice(0, DECISION_SUMMARY_INDICATOR_LIMIT).join(", ") || "—";
}

function summarizeConditions(item: WatchlistDecisionItem): string {
  if (item.details.conditions.length === 0) return "—";
  if (item.details.conditions.length === 1) return item.details.conditions[0];
  return `${item.details.conditions[0]} +${item.details.conditions.length - 1}`;
}

export interface IndicatorColumnSpec {
  /** Storage key inside `current_values` (e.g. `_meta:price`, `adx`). */
  key: string;
  /** Header label rendered to the user. */
  label: string;
  /** Original profile field name (e.g. `price`, `taker_ratio`). */
  field: string;
}

/** Render a per-row indicator value for the dynamic column set. */
function fmtIndicatorCell(key: string, raw: unknown): string {
  if (raw == null) return "—";
  if (typeof raw === "boolean") return raw ? "✓" : "✗";
  const num = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(num)) return String(raw);
  if (key === "_meta:price") {
    if (num >= 1) return `$${num.toFixed(2)}`;
    if (num >= 0.01) return `$${num.toFixed(4)}`;
    return `$${num.toPrecision(4)}`;
  }
  if (key === "_meta:volume_24h" || key === "_meta:market_cap") {
    if (num >= 1e9) return `$${(num / 1e9).toFixed(1)}B`;
    if (num >= 1e6) return `$${(num / 1e6).toFixed(1)}M`;
    if (num >= 1e3) return `$${(num / 1e3).toFixed(0)}K`;
    return `$${num.toFixed(0)}`;
  }
  if (key === "_meta:price_change_24h") {
    return `${num >= 0 ? "+" : ""}${num.toFixed(2)}%`;
  }
  if (Math.abs(num) >= 1_000_000_000) return `${(num / 1e9).toFixed(1)}B`;
  if (Math.abs(num) >= 1_000_000)     return `${(num / 1e6).toFixed(1)}M`;
  if (Math.abs(num) >= 1_000)         return `${(num / 1e3).toFixed(1)}K`;
  if (Math.abs(num) >= 100)           return num.toFixed(1);
  if (Math.abs(num) >= 1)             return num.toFixed(2);
  return num.toFixed(4);
}

function metricTopIndicator(items: WatchlistDecisionItem[]): string {
  const counts = new Map<string, number>();
  for (const item of items) {
    const indicators = item.failed_indicators.length > 0 ? item.failed_indicators : item.details.indicators;
    for (const indicator of indicators) {
      counts.set(indicator, (counts.get(indicator) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))[0]?.[0] ?? "—";
}

export function WatchlistDecisionTable({
  items,
  loading,
  emptyMessage,
  indicatorCols,
  showScore = true,
}: {
  items: WatchlistDecisionItem[];
  loading: boolean;
  emptyMessage?: string;
  /**
   * Dynamic indicator columns derived from the watchlist's profile (Score
   * tab + filters). When provided, replaces the legacy "Indicators" /
   * "Conditions" summary columns with one column per indicator. ``price``
   * is always the first column. When omitted (or empty), falls back to
   * the legacy summary view.
   */
  indicatorCols?: IndicatorColumnSpec[];
  /** Hide the Score column (e.g. for L1 where scoring is not yet applied). */
  showScore?: boolean;
}) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("all");
  const [status, setStatus] = useState<"all" | "approved" | "rejected">("all");
  const [indicator, setIndicator] = useState("all");
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  const metrics = useMemo(() => {
    const approved = items.filter((item) => item.status === "approved").length;
    const rejected = items.length - approved;
    const stages = [...new Set(items.map((item) => item.stage).filter(Boolean))] as string[];
    const availableIndicators = [...new Set(items.flatMap((item) => item.details.indicators))].sort();
    return {
      total: items.length,
      approved,
      rejected,
      topIndicator: metricTopIndicator(items),
      stages,
      availableIndicators,
    };
  }, [items]);

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (stage !== "all" && item.stage !== stage) return false;
      if (status !== "all" && item.status !== status) return false;
      if (indicator !== "all" && !item.details.indicators.includes(indicator)) return false;
      if (search && !item.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [indicator, items, search, stage, status]);

  const BOOLEAN_DECISION_KEYS = new Set([
    "ema_trend", "ema9_gt_ema50", "ema9_gt_ema21", "ema_full_alignment", "di_trend",
  ]);

  const toggleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === "desc" ? "asc" : "desc"));
    } else {
      setSortKey(key);
      setSortDir("desc");
    }
  };

  const sortedFiltered = sortKey
    ? [...filtered].sort((a, b) => {
        let aVal: number, bVal: number;
        if (sortKey === "score") {
          aVal = a.alpha_score ?? -Infinity;
          bVal = b.alpha_score ?? -Infinity;
        } else if (sortKey === "ml") {
          aVal = a.ml_probability ?? -Infinity;
          bVal = b.ml_probability ?? -Infinity;
        } else if (sortKey === "crypto_ev") {
          aVal = a.crypto_ev?.score ?? -Infinity;
          bVal = b.crypto_ev?.score ?? -Infinity;
        } else {
          const av = a.current_values?.[sortKey];
          const bv = b.current_values?.[sortKey];
          aVal = typeof av === "number" ? av : av == null ? -Infinity : Number(av);
          bVal = typeof bv === "number" ? bv : bv == null ? -Infinity : Number(bv);
          if (isNaN(aVal)) aVal = -Infinity;
          if (isNaN(bVal)) bVal = -Infinity;
        }
        return sortDir === "desc" ? bVal - aVal : aVal - bVal;
      })
    : filtered;

  if (loading) {
    return <div className="px-4 py-6 text-sm text-[#4B5563]">Loading decision snapshot…</div>;
  }

  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard label="Total" value={metrics.total} valueClass="text-[#E2E8F0]" />
        <MetricCard label="Approved" value={metrics.approved} valueClass="text-[#86EFAC]" />
        <MetricCard label="Rejected" value={metrics.rejected} valueClass="text-[#FCA5A5]" />
        <MetricCard label="Top Indicator" value={metrics.topIndicator} valueClass="text-[#E2E8F0]" compact />
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search symbol"
          className="min-w-[180px] rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        />
        <select
          value={stage}
          onChange={(event) => setStage(event.target.value)}
          className="rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        >
          <option value="all">All stages</option>
          {metrics.stages.map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
        <select
          value={status}
          onChange={(event) => setStatus(event.target.value as "all" | "approved" | "rejected")}
          className="rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        >
          <option value="all">All statuses</option>
          <option value="approved">approved</option>
          <option value="rejected">rejected</option>
        </select>
        <select
          value={indicator}
          onChange={(event) => setIndicator(event.target.value)}
          className="rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        >
          <option value="all">All indicators</option>
          {metrics.availableIndicators.map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </div>

      {sortedFiltered.length === 0 ? (
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-10 text-center text-sm text-[#4B5563]">
          {emptyMessage ?? "No decision snapshots for the current filters."}
        </div>
      ) : (() => {
          // Dynamic column mode: when the backend supplies profile_indicators
          // (always at least `price`), render one column per indicator and
          // drop the legacy summary columns. Fallback to the legacy view
          // only when the watchlist truly has no profile-driven columns.
          const dynCols = indicatorCols ?? [];
          const useDynamic = dynCols.length > 0;
          // Column count: chevron + Symbol + [Score] + ML + [dynCols] + Exaustão + Stage + Status + Timestamp
          //          OR  chevron + Symbol + [Score] + ML + Exaustão + Stage + Status + Indicators + Conditions + Timestamp
          const scoreCol = showScore ? 1 : 0;
          const totalCols = useDynamic ? 8 + scoreCol + dynCols.length : 10 + scoreCol;
          const minWidth = useDynamic ? Math.max(870, 570 + dynCols.length * 110) : 1190;
          return (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ minWidth: `${minWidth}px` }}>
            <thead>
              <tr className="border-b border-[#1A2035] bg-[#060810]">
                <th className="w-8 px-2 py-2.5" />
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Symbol</th>
                {showScore && (
                  <th className="px-3 py-2.5 text-left text-[#4B5563] min-w-[130px]">
                    <button onClick={() => toggleSort("score")} className="flex items-center gap-1 hover:text-[#94A3B8] transition-colors">
                      Score
                      <span className={`text-[9px] ${sortKey === "score" ? "text-[#60A5FA]" : "opacity-30"}`}>
                        {sortKey === "score" ? (sortDir === "desc" ? "▼" : "▲") : "⇅"}
                      </span>
                    </button>
                  </th>
                )}
                <th className="px-3 py-2.5 text-center text-[#4B5563] whitespace-nowrap">
                  <button onClick={() => toggleSort("ml")} className="flex items-center gap-1 justify-center hover:text-[#94A3B8] transition-colors w-full">
                    ML
                    <span className={`text-[9px] ${sortKey === "ml" ? "text-[#60A5FA]" : "opacity-30"}`}>
                      {sortKey === "ml" ? (sortDir === "desc" ? "▼" : "▲") : "⇅"}
                    </span>
                  </button>
                </th>
                <th className="px-3 py-2.5 text-center text-[#4B5563] whitespace-nowrap min-w-[86px]">
                  <button onClick={() => toggleSort("crypto_ev")} className="flex items-center gap-1 justify-center hover:text-[#94A3B8] transition-colors w-full">
                    EV Cripto
                    <span className={`text-[9px] ${sortKey === "crypto_ev" ? "text-[#60A5FA]" : "opacity-30"}`}>
                      {sortKey === "crypto_ev" ? (sortDir === "desc" ? "v" : "^") : "<>"}
                    </span>
                  </button>
                </th>
                {useDynamic ? (
                  dynCols.map((col) => {
                    const isNumeric = !BOOLEAN_DECISION_KEYS.has(col.key) && !BOOLEAN_DECISION_KEYS.has(col.field);
                    return (
                      <th
                        key={col.key}
                        className="px-3 py-2.5 text-right text-[#4B5563] whitespace-nowrap"
                        title={col.field}
                      >
                        {isNumeric ? (
                          <button onClick={() => toggleSort(col.key)} className="flex items-center gap-1 justify-end hover:text-[#94A3B8] transition-colors w-full">
                            {col.label}
                            <span className={`text-[9px] ${sortKey === col.key ? "text-[#60A5FA]" : "opacity-30"}`}>
                              {sortKey === col.key ? (sortDir === "desc" ? "▼" : "▲") : "⇅"}
                            </span>
                          </button>
                        ) : (
                          col.label
                        )}
                      </th>
                    );
                  })
                ) : (
                  <>
                    <th className="px-3 py-2.5 text-left text-[#4B5563]">Indicators</th>
                    <th className="px-3 py-2.5 text-left text-[#4B5563]">Conditions</th>
                  </>
                )}
                <th className="px-3 py-2.5 text-right text-[#4B5563] whitespace-nowrap">
                  <button onClick={() => toggleSort("entry_exhaustion_score")} className="flex items-center gap-1 justify-end hover:text-[#94A3B8] transition-colors w-full">
                    Exaustão
                    <span className={`text-[9px] ${sortKey === "entry_exhaustion_score" ? "text-[#60A5FA]" : "opacity-30"}`}>
                      {sortKey === "entry_exhaustion_score" ? (sortDir === "desc" ? "▼" : "▲") : "⇅"}
                    </span>
                  </button>
                </th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Stage</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Status</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {sortedFiltered.map((item) => {
                const palette = itemPalette(item.status);
                const rowKey = `${item.symbol}-${item.status}-${item.timestamp ?? "na"}`;
                const isExpanded = expandedRow === rowKey;
                return (
                  <Fragment key={rowKey}>
                    <tr
                      className={`cursor-pointer border-b border-[#1A2035]/60 hover:bg-[#0D1118] ${palette.row}`}
                      onClick={() => setExpandedRow(isExpanded ? null : rowKey)}
                    >
                      <td className="px-2 py-2.5 text-[#334155]">
                        {isExpanded ? <ChevronDown size={13} className="text-[#60A5FA]" /> : <ChevronRight size={13} />}
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-[#E2E8F0]">{item.symbol}</td>
                      {showScore && <td className="px-3 py-2.5"><ScoreBar value={item.alpha_score} /></td>}
                      <td className="px-3 py-2.5 text-center">
                        {item.ml_probability != null ? (
                          <span
                            className={`text-[10px] font-semibold px-1.5 py-0.5 rounded ${
                              item.blocked_by_ml
                                ? "bg-[#F87171]/10 text-[#F87171] border border-[#F87171]/20"
                                : item.ml_probability >= 0.6
                                ? "bg-[#34D399]/10 text-[#34D399] border border-[#34D399]/20"
                                : item.ml_probability >= 0.4
                                ? "bg-[#FBBF24]/10 text-[#FBBF24] border border-[#FBBF24]/20"
                                : "bg-[#F87171]/10 text-[#F87171] border border-[#F87171]/20"
                            }`}
                            title={`ML win probability: ${(item.ml_probability * 100).toFixed(1)}%${item.blocked_by_ml ? " (blocked by ML)" : ""}`}
                          >
                            {(item.ml_probability * 100).toFixed(0)}%
                          </span>
                        ) : (
                          <span className="text-[#4B5563]">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2.5 text-center">
                        <CryptoEVBadge value={item.crypto_ev} />
                      </td>
                      {useDynamic ? (
                        dynCols.map((col) => (
                          <td key={col.key} className="px-3 py-2.5 text-right tabular-nums text-[#CBD5E1] whitespace-nowrap">
                            {fmtIndicatorCell(col.key, item.current_values?.[col.key])}
                          </td>
                        ))
                      ) : (
                        <>
                          <td className={`px-3 py-2.5 font-medium ${palette.accent}`}>{summarizeIndicators(item)}</td>
                          <td className="px-3 py-2.5 text-[#CBD5E1]">{summarizeConditions(item)}</td>
                        </>
                      )}
                      <td className="px-3 py-2.5 text-right tabular-nums whitespace-nowrap">
                        {(() => {
                          const raw = item.current_values?.entry_exhaustion_score;
                          if (raw == null) return <span className="text-[#334155]">—</span>;
                          const val = typeof raw === 'number' ? raw : Number(raw);
                          if (!isFinite(val)) return <span className="text-[#334155]">—</span>;
                          const color = val >= 75 ? '#F87171' : val >= 50 ? '#FBBF24' : '#34D399';
                          return (
                            <span className="font-mono text-[10px] font-semibold" style={{ color }} title="Exaustão de Entrada (0=baixa · 100=máxima)">
                              {val.toFixed(0)}
                            </span>
                          );
                        })()}
                      </td>
                      <td className="px-3 py-2.5 text-[#94A3B8]">{item.stage ?? "—"}</td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex rounded px-2 py-0.5 text-[10px] font-semibold ${palette.badge}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 text-[#64748B]">{item.timestamp ? new Date(item.timestamp).toLocaleString() : "—"}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-[#1A2035] bg-[#06080E]">
                        <td colSpan={totalCols} className="p-4">
                          <div className="grid gap-4 lg:grid-cols-2">
                            <TraceSection
                              title="Block Rules"
                              items={item.details.evaluation_trace.filter((trace) => trace.type === "block_rule")}
                            />
                            <TraceSection
                              title="Filters"
                              items={item.details.evaluation_trace.filter((trace) => trace.type === "filter")}
                            />
                            <TraceSection
                              title="Entry Triggers"
                              items={item.details.evaluation_trace.filter((trace) => trace.type === "entry_trigger")}
                            />
                            <TraceSection
                              title="Signals"
                              items={item.details.evaluation_trace.filter((trace) => trace.type === "signal")}
                            />
                          </div>
                          {/* Entry Exhaustion Score — Fase 1 Shadow Mode (observacional) */}
                          {(() => {
                            const raw = item.current_values?.entry_exhaustion_score;
                            if (raw == null) return null;
                            const score = typeof raw === 'number' ? raw : Number(raw);
                            if (!isFinite(score)) return null;
                            const color = score >= 75 ? '#F87171' : score >= 50 ? '#FBBF24' : '#34D399';
                            const label = score >= 75 ? 'ALTO' : score >= 50 ? 'MÉDIO' : 'BAIXO';
                            return (
                              <div className="mt-4 rounded-xl border border-[#1E2433] bg-[#0A0B10] p-4">
                                <div className="mb-2 flex items-center justify-between">
                                  <span className="text-[11px] font-semibold uppercase tracking-wider text-[#4B5563]">
                                    Exaustão de Entrada{' '}
                                    <span className="ml-1 rounded bg-[#0F1825] px-1 py-px text-[9px] text-[#334155]">SHADOW</span>
                                  </span>
                                  <span className="font-mono text-xs" style={{ color }}>
                                    {score.toFixed(1)} / 100 · {label}
                                  </span>
                                </div>
                                <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#1A2035]">
                                  <div
                                    className="h-full rounded-full transition-all"
                                    style={{ width: `${Math.min(100, Math.max(0, score))}%`, backgroundColor: color }}
                                  />
                                </div>
                                <div className="mt-1.5 text-[10px] text-[#4B5563]">
                                  Observacional — não bloqueia trades. 0 = sem exaustão · 100 = máxima exaustão de entrada.
                                </div>
                              </div>
                            );
                          })()}
                          <div className="mt-4">
                            <ScoreBreakdownSection
                              rules={item.score_rules ?? []}
                              alphaScore={item.alpha_score ?? null}
                            />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
          );
        })()}
    </div>
  );
}

function MetricCard({
  label,
  value,
  valueClass,
  compact = false,
}: {
  label: string;
  value: string | number;
  valueClass: string;
  compact?: boolean;
}) {
  return (
    <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-[#4B5563]">{label}</div>
      <div className={`mt-1 ${compact ? "text-sm" : "text-lg"} font-semibold ${valueClass}`}>{value}</div>
    </div>
  );
}

function TraceSection({ title, items }: { title: string; items: DecisionTraceItem[] }) {
  return (
    <div className="rounded-xl border border-[#1E2433] bg-[#0A0B10] p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[#4B5563]">{title}</div>
      <div className="space-y-2">
        {items.map((item, index) => {
          const skip = classifySkip(item);
          const isBlockRule = item.type === "block_rule";
          const blockOutcome = isBlockRule ? blockRuleOutcome(item) : null;
          // Block rules: OK em laranja (não verde) — regra de bloqueio que
          // não disparou ainda é um risk gate, verde daria impressão de
          // "tudo liberado" (vocabulário de Filter/Signal). TRIPPED em roxo.
          const cls = isBlockRule
            ? skip
              ? skip.cls
              : blockOutcome === "OK"
                ? "border-[#7C2D12]/40 bg-[#1A0E08] text-[#FDBA74]"
                : blockOutcome === "TRIPPED"
                  ? "border-[#6B21A8]/40 bg-[#1A0A2A] text-[#D8B4FE]"
                  : "border-[#1E2433] bg-[#06080E] text-[#64748B]"
            : item.status === "PASS"
              ? "border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]"
              : skip
                ? skip.cls
                : item.status === "FAIL"
                  ? "border-[#7F1D1D]/25 bg-[#150A0A] text-[#FCA5A5]"
                  : "border-[#1E2433] bg-[#06080E] text-[#64748B]";
          const badgeLabel = skip
            ? skip.label
            : isBlockRule
              ? blockOutcome
              : item.status;
          const intentLine = isBlockRule && !skip
            ? blockOutcome === "TRIPPED"
              ? "condição disparou — ativo bloqueado"
              : blockOutcome === "OK"
                ? "condição não disparou — ativo livre"
                : null
            : null;
          const expectedLabel = isBlockRule ? "Threshold" : "Expected";
          return (
            <div key={index} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold">{item.indicator}</span>
                <span className="font-mono text-[10px]">{badgeLabel}</span>
              </div>
              <div className="mt-1 text-[#CBD5E1]">{item.condition}</div>
              {intentLine && (
                <div className="mt-0.5 text-[10px] italic opacity-75">{intentLine}</div>
              )}
              <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
                <span>
                  Current:{" "}
                  <span className="font-mono">
                    {skip && skip.currentText
                      ? <span className="italic opacity-60">{skip.currentText}</span>
                      : fmtValue(item.current_value)}
                  </span>
                </span>
                <span>
                  {expectedLabel}:{" "}
                  <span className="font-mono">
                    {skip?.expectedOverride ?? (item.expected ?? "—")}
                  </span>
                </span>
              </div>
            </div>
          );
        })}
        {items.length === 0 && <div className="text-xs text-[#4B5563]">No rules configured.</div>}
      </div>
    </div>
  );
}

const SCORE_CATEGORY_ORDER = ["momentum", "market_structure", "liquidity", "signal", "other"];
const SCORE_CATEGORY_LABELS: Record<string, string> = {
  momentum: "Momentum",
  market_structure: "Estrutura de Mercado",
  liquidity: "Liquidez",
  signal: "Sinal",
  other: "Outros",
};

function fmtRuleValue(value: ScoreRule["actual_value"]): string {
  if (value == null) return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") {
    if (Math.abs(value) >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(1)}B`;
    if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
    if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
    return value % 1 === 0 ? String(value) : value.toFixed(2);
  }
  return String(value);
}

function ScoreBreakdownSection({
  rules,
  alphaScore,
}: {
  rules: ScoreRule[];
  alphaScore: number | null;
}) {
  const summary = summarizeScoreRules(rules);
  const {
    matchedCount,
    positiveCount,
    totalPossible,
    nominalEarned,
    awardedEarned,
    hasEnriched,
    totalPenalties,
  } = summary;
  const earnedDisplay = hasEnriched ? awardedEarned : nominalEarned;
  // Single source of truth for label + color (Task #187 review fix). The
  // file-local `scoreColor` helper still backs the table-row ScoreBar
  // (different visual context, kept out of scope), but the breakdown
  // panel now derives both from the unified robust-engine thresholds so
  // the band label and bar color can never disagree.
  const band = scoreBand(alphaScore);
  const pct = scorePct(alphaScore);

  const byCategory = SCORE_CATEGORY_ORDER.reduce<Record<string, ScoreRule[]>>((acc, cat) => {
    const catRules = sortScoreRules(rules.filter((r) => r.category === cat));
    if (catRules.length) acc[cat] = catRules;
    return acc;
  }, {});

  const uncategorized = sortScoreRules(rules.filter((r) => !SCORE_CATEGORY_ORDER.includes(r.category)));
  if (uncategorized.length) byCategory["other"] = [...(byCategory["other"] ?? []), ...uncategorized];

  return (
    <div className="rounded-xl border border-[#1E2433] bg-[#0A0B10] p-4">
      <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[#4B5563]">
        Score Breakdown
      </div>

      {/* Score row — robust 0–100 metric */}
      <div className="flex items-center gap-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#4B5563] w-[90px] shrink-0">
          Score
        </span>
        <div
          className="flex-1 h-1.5 bg-[#1A2035] rounded-full overflow-hidden"
          title={SCORE_TOOLTIP}
        >
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${pct}%`, backgroundColor: band.color }}
          />
        </div>
        <span
          className="text-sm font-bold tabular-nums w-16 text-right"
          style={{ color: band.color }}
          title={SCORE_TOOLTIP}
        >
          {alphaScore == null ? '—' : `${alphaScore.toFixed(1)}/100`}
        </span>
        <span
          className="text-[10px] font-semibold uppercase tracking-wider w-14 text-right"
          style={{ color: band.color }}
          title={SCORE_TOOLTIP}
          data-testid="score-band-label"
        >
          {band.label}
        </span>
      </div>

      {/* Rules row — secondary counter, distinct visual (no bar) */}
      <div className="flex items-center gap-3 mt-1.5 mb-3">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#4B5563] w-[90px] shrink-0">
          Regras
        </span>
        <span
          className="text-[11px] text-[#64748B] flex-1"
          title={RULES_TOOLTIP}
        >
          {rules.length === 0 ? (
            'Sem regras configuradas'
          ) : (
            <>
              {matchedCount}/{positiveCount} matched ·{' '}
              {`+${earnedDisplay.toFixed(0)}`}
              /{totalPossible.toFixed(0)} pts
              {!hasEnriched && matchedCount > 0 && (
                <span className="ml-1.5 text-[9px] text-[#475569] uppercase tracking-wider">
                  (legacy)
                </span>
              )}
            </>
          )}
        </span>
        {totalPenalties !== 0 && (
          <span className="text-[10px] text-[#F87171] shrink-0">
            Penalty: {fmtPts(totalPenalties)}
          </span>
        )}
      </div>

      {rules.length === 0 ? (
        <p className="text-xs text-[#334155] text-center py-2">
          Sem regras de scoring configuradas.
        </p>
      ) : (
        <div className="space-y-3">
          {Object.entries(byCategory).map(([cat, catRules]) => (
            <div key={cat}>
              <div className="text-[10px] font-medium text-[#334155] uppercase tracking-wider mb-1.5">
                {SCORE_CATEGORY_LABELS[cat] ?? cat}
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1">
                {catRules.map((rule) => {
                  const isPenalty = rule.type === 'penalty';
                  const isNeutral = rule.type === 'neutral';
                  const isFired   = rule.passed;
                  const isGood    = !isPenalty && !isNeutral && isFired;
                  const colors    = isNeutral
                    ? RULE_COLORS.positiveUnmatched
                    : isPenalty
                      ? (isFired ? RULE_COLORS.penaltyFired : RULE_COLORS.penaltyIdle)
                      : (isFired ? RULE_COLORS.positiveMatched : RULE_COLORS.positiveUnmatched);

                  const hasAwarded =
                    rule.passed &&
                    typeof rule.awarded_points === 'number' &&
                    Number.isFinite(rule.awarded_points);
                  const awardedDisplay = rule.passed
                    ? hasAwarded
                      ? `+${(rule.awarded_points as number).toFixed(0)}`
                      : fmtPts(rule.points_awarded)
                    : '0';
                  const ptsTooltip = hasAwarded
                    ? `${(rule.awarded_points as number).toFixed(0)}` +
                      ` / ${rule.points_possible.toFixed(0)} pts` +
                      ` (conf ${fmtConfidence(rule.indicator_confidence)})`
                    : `${rule.passed ? rule.points_awarded.toFixed(2) : '0'} /` +
                      ` ${rule.points_possible.toFixed(2)} pts`;

                  return (
                    <div
                      key={rule.id}
                      className={`flex items-center gap-2 px-3 py-2 rounded-lg text-xs border ${colors.bg}`}
                    >
                      {isGood || (isPenalty && !isFired) || (isNeutral && isFired) ? (
                        <CheckCircle2 size={11} className={`${isGood ? 'text-[#34D399]' : 'text-[#4B5563]'} shrink-0`} />
                      ) : (
                        <XCircle size={11} className={`${isNeutral ? 'text-[#4B5563]' : 'text-[#F87171]'} shrink-0`} />
                      )}
                      <span
                        className={`flex-1 truncate ${
                          isGood ? 'text-[#94A3B8]' : isPenalty && isFired ? 'text-[#F87171]' : 'text-[#4B5563]'
                        }`}
                        title={rule.condition_text}
                      >
                        {rule.condition_text}
                      </span>
                      <span
                        className={`font-mono text-[10px] shrink-0 ${
                          rule.actual_value != null
                            ? isGood ? 'text-[#CBD5E1]' : isPenalty && isFired ? 'text-[#FCA5A5]' : 'text-[#64748B]'
                            : 'text-[#334155]'
                        }`}
                      >
                        {fmtRuleValue(rule.actual_value)}
                      </span>
                      <span
                        className={`font-mono text-[10px] shrink-0 w-20 text-right ${colors.text}`}
                        title={ptsTooltip}
                      >
                        {awardedDisplay}/{fmtPts(rule.points_possible)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export const RejectedAssetTable = WatchlistDecisionTable;

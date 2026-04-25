"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import {
  EvaluationTraceBreakdown,
  formatEvaluationTraceValue,
  type EvaluationTraceItem,
} from "./EvaluationTraceBreakdown";

const DECISION_SUMMARY_INDICATOR_LIMIT = 3;

export interface DecisionTraceItem {
  type: "filter" | "block_rule" | "entry_trigger" | "signal";
  indicator: string;
  condition: string;
  expected?: string | null;
  current_value?: unknown;
  status: "PASS" | "FAIL" | "SKIPPED";
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
  failed_indicators: string[];
  conditions: string[];
  current_values: Record<string, unknown>;
  expected_values: Record<string, string | null>;
  details: DecisionDetails;
}

export type RejectedTraceItem = DecisionTraceItem;
export type RejectedAssetItem = WatchlistDecisionItem;

function fmtValue(value: unknown): string {
  return formatEvaluationTraceValue(value);
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
}: {
  items: WatchlistDecisionItem[];
  loading: boolean;
  emptyMessage?: string;
}) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("all");
  const [status, setStatus] = useState<"all" | "approved" | "rejected">("all");
  const [indicator, setIndicator] = useState("all");

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

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-10 text-center text-sm text-[#4B5563]">
          {emptyMessage ?? "No decision snapshots for the current filters."}
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] text-xs">
            <thead>
              <tr className="border-b border-[#1A2035] bg-[#060810]">
                <th className="w-8 px-2 py-2.5" />
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Symbol</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Stage</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Status</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Indicators</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Conditions</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
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
                      <td className="px-3 py-2.5 text-[#94A3B8]">{item.stage ?? "—"}</td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex rounded px-2 py-0.5 text-[10px] font-semibold ${palette.badge}`}>
                          {item.status}
                        </span>
                      </td>
                      <td className={`px-3 py-2.5 font-medium ${palette.accent}`}>{summarizeIndicators(item)}</td>
                      <td className="px-3 py-2.5 text-[#CBD5E1]">{summarizeConditions(item)}</td>
                      <td className="px-3 py-2.5 text-[#64748B]">{item.timestamp ? new Date(item.timestamp).toLocaleString() : "—"}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-[#1A2035] bg-[#06080E]">
                        <td colSpan={7} className="p-4">
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
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
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
          const noData = item.status === "SKIPPED" || (item.status === "FAIL" && item.current_value == null);
          const cls =
            item.status === "PASS"
              ? "border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]"
              : noData
                ? "border-[#78350F]/40 bg-[#1A1205] text-[#FCD34D]"
                : item.status === "FAIL"
                  ? item.type === "block_rule"
                    ? "border-[#6B21A8]/40 bg-[#1A0A2A] text-[#D8B4FE]"
                    : "border-[#7F1D1D]/25 bg-[#150A0A] text-[#FCA5A5]"
                  : "border-[#1E2433] bg-[#06080E] text-[#64748B]";
          return (
            <div key={index} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold">{item.indicator}</span>
                <span className="font-mono text-[10px]">{noData ? "SEM DADOS" : item.status}</span>
              </div>
              <div className="mt-1 text-[#CBD5E1]">{item.condition}</div>
              <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
                <span>
                  Current:{" "}
                  <span className="font-mono">
                    {noData ? <span className="italic opacity-60">aguardando coleta</span> : fmtValue(item.current_value)}
                  </span>
                </span>
                <span>Expected: <span className="font-mono">{item.expected ?? "—"}</span></span>
              </div>
            </div>
          );
        })}
        {items.length === 0 && <div className="text-xs text-[#4B5563]">No rules configured.</div>}
      </div>
    </div>
  );
}

export const RejectedAssetTable = WatchlistDecisionTable;

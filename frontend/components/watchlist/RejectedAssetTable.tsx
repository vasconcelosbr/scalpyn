"use client";

import { Fragment, useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

export interface RejectedTraceItem {
  type: "filter" | "block_rule";
  indicator: string;
  condition: string;
  expected?: string | null;
  current_value?: unknown;
  status: "PASS" | "FAIL" | "SKIPPED";
}

export interface RejectedAssetItem {
  symbol: string;
  stage: string;
  profile_id?: string | null;
  failed_type: "filter" | "block_rule";
  failed_indicator: string;
  condition: string;
  current_value?: unknown;
  expected?: string | null;
  timestamp?: string | null;
  evaluation_trace: RejectedTraceItem[];
}

export interface RejectedMetrics {
  total_rejected: number;
  block_rule_count: number;
  filter_count: number;
  block_rule_rate: number;
  top_indicator?: string | null;
  available_indicators?: string[];
  stages?: string[];
}

function fmtValue(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "number") {
    return Math.abs(value) >= 100 ? value.toFixed(1) : Math.abs(value) >= 1 ? value.toFixed(2) : value.toFixed(4);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return value;
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function rowPalette(type: "filter" | "block_rule") {
  return type === "block_rule"
    ? {
        badge: "bg-[#A855F7]/10 text-[#D8B4FE] border border-[#A855F7]/25",
        row: "border-l-2 border-l-[#A855F7]/60",
        status: "text-[#D8B4FE]",
      }
    : {
        badge: "bg-[#F87171]/10 text-[#FCA5A5] border border-[#F87171]/25",
        row: "border-l-2 border-l-[#F87171]/60",
        status: "text-[#FCA5A5]",
      };
}

export function RejectedAssetTable({
  items,
  metrics,
  loading,
}: {
  items: RejectedAssetItem[];
  metrics: RejectedMetrics | null;
  loading: boolean;
}) {
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [stage, setStage] = useState("all");
  const [type, setType] = useState<"all" | "filter" | "block_rule">("all");
  const [indicator, setIndicator] = useState("all");

  const filtered = useMemo(() => {
    return items.filter((item) => {
      if (stage !== "all" && item.stage !== stage) return false;
      if (type !== "all" && item.failed_type !== type) return false;
      if (indicator !== "all" && item.failed_indicator !== indicator) return false;
      if (search && !item.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    });
  }, [indicator, items, search, stage, type]);

  if (loading) {
    return <div className="px-4 py-6 text-sm text-[#4B5563]">Loading rejected assets…</div>;
  }

  return (
    <div className="space-y-4 p-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-[#4B5563]">Rejected</div>
          <div className="mt-1 text-lg font-semibold text-[#E2E8F0]">{metrics?.total_rejected ?? 0}</div>
        </div>
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-[#4B5563]">Filters</div>
          <div className="mt-1 text-lg font-semibold text-[#FCA5A5]">{metrics?.filter_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-[#4B5563]">Block Rules</div>
          <div className="mt-1 text-lg font-semibold text-[#D8B4FE]">{metrics?.block_rule_count ?? 0}</div>
        </div>
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-3">
          <div className="text-[10px] uppercase tracking-wider text-[#4B5563]">Top Rejector</div>
          <div className="mt-1 text-sm font-semibold text-[#E2E8F0]">{metrics?.top_indicator ?? "—"}</div>
        </div>
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
          {(metrics?.stages ?? []).map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
        <select
          value={type}
          onChange={(event) => setType(event.target.value as "all" | "filter" | "block_rule")}
          className="rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        >
          <option value="all">All types</option>
          <option value="filter">filter</option>
          <option value="block_rule">block_rule</option>
        </select>
        <select
          value={indicator}
          onChange={(event) => setIndicator(event.target.value)}
          className="rounded-lg border border-[#1E2433] bg-[#0A0B10] px-3 py-2 text-sm text-[#E2E8F0] focus:outline-none"
        >
          <option value="all">All indicators</option>
          {(metrics?.available_indicators ?? []).map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </select>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-[#1E2433] bg-[#06080E] px-4 py-10 text-center text-sm text-[#4B5563]">
          No rejected assets for the current filters.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1040px] text-xs">
            <thead>
              <tr className="border-b border-[#1A2035] bg-[#060810]">
                <th className="w-8 px-2 py-2.5" />
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Symbol</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Stage</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Type</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Failed Indicator</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Condition</th>
                <th className="px-3 py-2.5 text-right text-[#4B5563]">Current Value</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Expected</th>
                <th className="px-3 py-2.5 text-left text-[#4B5563]">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => {
                const palette = rowPalette(item.failed_type);
                const isExpanded = expandedRow === `${item.symbol}-${item.failed_type}`;
                return (
                  <Fragment key={`${item.symbol}-${item.failed_type}`}>
                    <tr
                      className={`cursor-pointer border-b border-[#1A2035]/60 hover:bg-[#0D1118] ${palette.row}`}
                      onClick={() => setExpandedRow(isExpanded ? null : `${item.symbol}-${item.failed_type}`)}
                    >
                      <td className="px-2 py-2.5 text-[#334155]">
                        {isExpanded ? <ChevronDown size={13} className="text-[#60A5FA]" /> : <ChevronRight size={13} />}
                      </td>
                      <td className="px-3 py-2.5 font-semibold text-[#E2E8F0]">{item.symbol}</td>
                      <td className="px-3 py-2.5 text-[#94A3B8]">{item.stage}</td>
                      <td className="px-3 py-2.5">
                        <span className={`inline-flex rounded px-2 py-0.5 text-[10px] font-semibold ${palette.badge}`}>
                          {item.failed_type}
                        </span>
                      </td>
                      <td className={`px-3 py-2.5 font-medium ${palette.status}`}>{item.failed_indicator}</td>
                      <td className="px-3 py-2.5 text-[#CBD5E1]">{item.condition}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-[#E2E8F0]">{fmtValue(item.current_value)}</td>
                      <td className="px-3 py-2.5 text-[#94A3B8]">{item.expected ?? "—"}</td>
                      <td className="px-3 py-2.5 text-[#64748B]">{item.timestamp ? new Date(item.timestamp).toLocaleString() : "—"}</td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-b border-[#1A2035] bg-[#06080E]">
                        <td colSpan={9} className="p-4">
                          <div className="grid gap-4 lg:grid-cols-2">
                            <TraceSection
                              title="Block Rules"
                              items={item.evaluation_trace.filter((trace) => trace.type === "block_rule")}
                            />
                            <TraceSection
                              title="Filters"
                              items={item.evaluation_trace.filter((trace) => trace.type === "filter")}
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

function TraceSection({ title, items }: { title: string; items: RejectedTraceItem[] }) {
  return (
    <div className="rounded-xl border border-[#1E2433] bg-[#0A0B10] p-4">
      <div className="mb-3 text-[11px] font-semibold uppercase tracking-wider text-[#4B5563]">{title}</div>
      <div className="space-y-2">
        {items.map((item, index) => {
          const cls =
            item.status === "PASS"
              ? "border-[#14532D]/40 bg-[#061E14] text-[#86EFAC]"
              : item.status === "FAIL"
                ? item.type === "block_rule"
                  ? "border-[#6B21A8]/40 bg-[#1A0A2A] text-[#D8B4FE]"
                  : "border-[#7F1D1D]/25 bg-[#150A0A] text-[#FCA5A5]"
                : "border-[#1E2433] bg-[#06080E] text-[#64748B]";
          return (
            <div key={`${item.indicator}-${index}`} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
              <div className="flex items-center justify-between gap-3">
                <span className="font-semibold">{item.indicator}</span>
                <span className="font-mono text-[10px]">{item.status}</span>
              </div>
              <div className="mt-1 text-[#CBD5E1]">{item.condition}</div>
              <div className="mt-1 flex flex-wrap gap-3 text-[11px]">
                <span>Current: <span className="font-mono">{fmtValue(item.current_value)}</span></span>
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

"use client";

import { ReactNode, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  ArrowDown,
  ArrowUp,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  FileText,
  Filter,
  RefreshCw,
  Search,
} from "lucide-react";

import { apiGet } from "@/lib/api";
import { DecisionCreatedMessage, DecisionItem, useWebSocket } from "@/hooks/useWebSocket";

interface DecisionsResponse {
  items: DecisionItem[];
  next_cursor: string | null;
}

interface DecisionLogConfig {
  page_size?: number;
  client_buffer_size?: number;
  max_displayed_metrics?: number;
  realtime_highlight_ms?: number;
}

interface ApprovedSnapshotItem {
  symbol: string;
  score: number | null;
  alpha_score: number | null;
  score_long: number | null;
  score_short: number | null;
  direction: "LONG" | "SHORT" | "NEUTRAL" | null;
  watchlist_id: string;
  watchlist_name: string;
  stage: string;
  market_mode: "spot" | "futures";
  approved_at: string | null;
  indicators: Array<Record<string, unknown> | string>;
  score_rules: Array<Record<string, unknown> | string>;
}

interface ApprovedSnapshotResponse {
  items: ApprovedSnapshotItem[];
  total: number;
  as_of: string | null;
}

interface SnapshotWatchlist {
  id: string;
  name: string;
  market_mode: "spot" | "futures";
}

type Tab = "audit" | "approved";

type Filters = {
  startDate: string;
  endDate: string;
  symbol: string;
  strategy: string;
  scoreMin: string;
  scoreMax: string;
  decision: "ALL" | "ALLOW" | "BLOCK";
};

type SnapshotFilters = {
  symbol: string;
  marketMode: "all" | "spot" | "futures";
  watchlistId: string;
  sort: "score_desc" | "score_asc" | "symbol_asc" | "approved_at_desc";
};

const DEFAULT_FILTERS: Filters = {
  startDate: "",
  endDate: "",
  symbol: "",
  strategy: "",
  scoreMin: "0",
  scoreMax: "100",
  decision: "ALL",
};

const DEFAULT_SNAPSHOT_FILTERS: SnapshotFilters = {
  symbol: "",
  marketMode: "all",
  watchlistId: "",
  sort: "score_desc",
};

const SNAPSHOT_REFRESH_MS = 30_000;

const INPUT_CLASS =
  "rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-input)] px-3 py-1.5 text-[12px] text-[var(--text-primary)] outline-none transition-colors placeholder:text-[var(--text-tertiary)] focus:border-[var(--accent-primary)]";

function buildParams(filters: Filters, config?: DecisionLogConfig | null, cursor?: string | null) {
  const params = new URLSearchParams();
  if (filters.startDate) params.set("start_date", filters.startDate);
  if (filters.endDate) params.set("end_date", filters.endDate);
  if (filters.symbol) params.set("symbol", filters.symbol.trim().toUpperCase());
  if (filters.strategy) params.set("strategy", filters.strategy.trim().toUpperCase());
  params.set("score_min", filters.scoreMin || "0");
  params.set("score_max", filters.scoreMax || "100");
  params.set("decision", filters.decision);
  if (config?.page_size) params.set("limit", String(config.page_size));
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

function buildSnapshotParams(filters: SnapshotFilters) {
  const params = new URLSearchParams();
  if (filters.symbol) params.set("symbol", filters.symbol.trim().toUpperCase());
  if (filters.marketMode !== "all") params.set("market_mode", filters.marketMode);
  if (filters.watchlistId) params.set("watchlist_id", filters.watchlistId);
  if (filters.sort) params.set("sort", filters.sort);
  return params.toString();
}

function matchesFilters(item: DecisionItem, filters: Filters) {
  const score = typeof item.score === "number" ? item.score : 0;
  const createdAt = new Date(item.created_at).getTime();

  if (filters.symbol && item.symbol !== filters.symbol.trim().toUpperCase()) return false;
  if (filters.strategy && item.strategy !== filters.strategy.trim().toUpperCase()) return false;
  if (filters.decision !== "ALL" && item.decision !== filters.decision) return false;
  if (score < Number(filters.scoreMin || 0)) return false;
  if (score > Number(filters.scoreMax || 100)) return false;
  if (filters.startDate && createdAt < new Date(`${filters.startDate}T00:00:00Z`).getTime()) return false;
  if (filters.endDate && createdAt > new Date(`${filters.endDate}T23:59:59.999Z`).getTime()) return false;
  return true;
}

function eventTypeTone(eventType?: string | null): string {
  switch (eventType) {
    case "NEW_SIGNAL":       return "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border-[var(--color-profit-border)]";
    case "SIGNAL_LOST":      return "bg-[var(--color-loss-muted)] text-[var(--color-loss)] border-[var(--color-loss-border)]";
    case "SIGNAL_REGAINED":  return "bg-[rgba(20,184,166,0.12)] text-[rgb(20,184,166)] border-[rgba(20,184,166,0.3)]";
    case "SIGNAL_EVOLVED_SCORE":
    case "SIGNAL_EVOLVED_DIRECTION": return "bg-[rgba(251,191,36,0.12)] text-[var(--color-warning)] border-[rgba(251,191,36,0.25)]";
    default:                 return "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-default)]";
  }
}

function eventTypeLabel(eventType?: string | null): string {
  switch (eventType) {
    case "NEW_SIGNAL":               return "New";
    case "SIGNAL_LOST":              return "Lost";
    case "SIGNAL_REGAINED":          return "Regained";
    case "SIGNAL_EVOLVED_SCORE":     return "Δ Score";
    case "SIGNAL_EVOLVED_DIRECTION": return "Δ Dir";
    default:                         return eventType ?? "";
  }
}

function scoreTone(score?: number | null) {
  if ((score ?? 0) >= 80) return "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border-[var(--color-profit-border)]";
  if ((score ?? 0) >= 60) return "bg-[rgba(251,191,36,0.12)] text-[var(--color-warning)] border-[rgba(251,191,36,0.25)]";
  return "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-default)]";
}

function decisionTone(decision: DecisionItem["decision"]) {
  return decision === "ALLOW"
    ? "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border-[var(--color-profit-border)]"
    : "bg-[var(--color-loss-muted)] text-[var(--color-loss)] border-[var(--color-loss-border)]";
}

function directionTone(direction?: ApprovedSnapshotItem["direction"]) {
  if (direction === "LONG") return "bg-[var(--color-profit-muted)] text-[var(--color-profit)] border-[var(--color-profit-border)]";
  if (direction === "SHORT") return "bg-[var(--color-loss-muted)] text-[var(--color-loss)] border-[var(--color-loss-border)]";
  return "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border-[var(--border-default)]";
}

function gateMark(value?: boolean | null) {
  return value ? "✓" : "✗";
}

function formatMetricValue(value: unknown) {
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (value === null || value === undefined) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function indicatorChipLabel(entry: Record<string, unknown> | string): string {
  if (typeof entry === "string") return entry;
  const raw = entry as Record<string, unknown>;
  const label =
    (raw.label as string | undefined) ??
    (raw.name as string | undefined) ??
    (raw.indicator as string | undefined) ??
    (raw.key as string | undefined);
  return label ?? JSON.stringify(raw);
}

function DecisionsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialTab: Tab = searchParams.get("tab") === "approved" ? "approved" : "audit";
  const [tab, setTab] = useState<Tab>(initialTab);

  const switchTab = useCallback(
    (next: Tab) => {
      setTab(next);
      const sp = new URLSearchParams(Array.from(searchParams.entries()));
      if (next === "audit") {
        sp.delete("tab");
      } else {
        sp.set("tab", next);
      }
      const qs = sp.toString();
      router.replace(qs ? `/decisions?${qs}` : "/decisions");
    },
    [router, searchParams]
  );

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-[var(--text-primary)]">Decision Log</h1>
          <p className="mt-1 text-[13px] text-[var(--text-secondary)]">
            {tab === "audit"
              ? "Real pipeline audit trail — state transitions only"
              : "Snapshot of every asset currently approved at L3"}
          </p>
        </div>
        <TabSwitcher tab={tab} onChange={switchTab} />
      </div>

      {tab === "audit" ? <AuditTrailView /> : <ApprovedSnapshotView />}
    </div>
  );
}

function TabSwitcher({ tab, onChange }: { tab: Tab; onChange: (next: Tab) => void }) {
  const baseClass =
    "rounded-[var(--radius-sm)] px-3 py-1.5 text-[12px] font-medium transition-colors";
  const activeClass = "bg-[var(--accent-primary)] text-white";
  const inactiveClass =
    "bg-[var(--bg-elevated)] text-[var(--text-secondary)] border border-[var(--border-default)] hover:text-[var(--text-primary)]";
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => onChange("audit")}
        className={`${baseClass} ${tab === "audit" ? activeClass : inactiveClass}`}
      >
        Audit Trail
      </button>
      <button
        type="button"
        onClick={() => onChange("approved")}
        className={`${baseClass} ${tab === "approved" ? activeClass : inactiveClass}`}
      >
        Currently Approved (L3)
      </button>
    </div>
  );
}

export default function DecisionsPage() {
  return (
    <Suspense
      fallback={
        <div className="space-y-6">
          <div className="space-y-3">
            <div className="skeleton h-8 w-64" />
            <div className="skeleton h-4 w-80" />
          </div>
          <div className="card space-y-3 p-8">
            {Array.from({ length: 6 }).map((_, idx) => (
              <div key={idx} className="skeleton h-10 w-full" />
            ))}
          </div>
        </div>
      }
    >
      <DecisionsPageInner />
    </Suspense>
  );
}

function AuditTrailView() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [items, setItems] = useState<DecisionItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [config, setConfig] = useState<DecisionLogConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [highlightedIds, setHighlightedIds] = useState<number[]>([]);

  const { lastMessage } = useWebSocket<DecisionCreatedMessage>("decisions");

  const fetchDecisions = useCallback(
    async (currentFilters: Filters, cursor?: string | null, append = false) => {
      const setter = append ? setLoadingMore : setLoading;
      setter(true);
      if (!append) setError(null);
      try {
        const response = await apiGet<DecisionsResponse>(`/decisions?${buildParams(currentFilters, config, cursor)}`);
        setItems((prev) => {
          if (!append) return response.items;
          const seen = new Set(prev.map((item) => item.id));
          return [...prev, ...response.items.filter((item) => !seen.has(item.id))];
        });
        setNextCursor(response.next_cursor);
      } catch (err) {
        if (!append) {
          setItems([]);
          setNextCursor(null);
          setError(err instanceof Error ? err.message : "Failed to load decisions");
        }
      } finally {
        setter(false);
      }
    },
    [config]
  );

  useEffect(() => {
    void apiGet<{ data?: DecisionLogConfig }>("/config/decision_log")
      .then((response) => setConfig(response.data ?? {}))
      .catch(() => setConfig({}));
  }, []);

  useEffect(() => {
    if (config === null) return;
    void fetchDecisions(appliedFilters);
  }, [appliedFilters, config, fetchDecisions]);

  useEffect(() => {
    if (lastMessage?.type !== "decision.created" || !lastMessage.data) return;
    const incoming = lastMessage.data;
    if (!matchesFilters(incoming, appliedFilters)) return;

    setItems((prev) => {
      const next = [incoming, ...prev.filter((item) => item.id !== incoming.id)];
      return config?.client_buffer_size ? next.slice(0, config.client_buffer_size) : next;
    });
    setHighlightedIds((prev) => [...prev.filter((id) => id !== incoming.id), incoming.id]);

    if (!config?.realtime_highlight_ms) return;

    const timer = window.setTimeout(() => {
      setHighlightedIds((prev) => prev.filter((id) => id !== incoming.id));
    }, config.realtime_highlight_ms);

    return () => window.clearTimeout(timer);
  }, [appliedFilters, config, lastMessage]);

  const symbolOptions = useMemo(
    () => Array.from(new Set(items.map((item) => item.symbol))).sort(),
    [items]
  );

  const applyFilters = () => {
    setExpandedId(null);
    setAppliedFilters({
      ...filters,
      symbol: filters.symbol.trim().toUpperCase(),
      strategy: filters.strategy.trim().toUpperCase(),
      scoreMin: filters.scoreMin || "0",
      scoreMax: filters.scoreMax || "100",
    });
  };

  const downloadCsv = async () => {
    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null;
    const response = await fetch(`/api/decisions/export?${buildParams(appliedFilters, config)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
    if (!response.ok) return;

    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `decisions_${new Date().toISOString().slice(0, 10)}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-end">
        <button
          onClick={downloadCsv}
          className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-1.5 text-[12px] font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
        >
          <Download className="h-3.5 w-3.5" />
          Export CSV
        </button>
      </div>

      <div className="card">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <FilterField label="Start Date">
            <input
              type="date"
              value={filters.startDate}
              onChange={(event) => setFilters((prev) => ({ ...prev, startDate: event.target.value }))}
              className={INPUT_CLASS}
            />
          </FilterField>
          <FilterField label="End Date">
            <input
              type="date"
              value={filters.endDate}
              onChange={(event) => setFilters((prev) => ({ ...prev, endDate: event.target.value }))}
              className={INPUT_CLASS}
            />
          </FilterField>
          <FilterField label="Symbol">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-tertiary)]" />
              <input
                list="decision-symbols"
                value={filters.symbol}
                onChange={(event) => setFilters((prev) => ({ ...prev, symbol: event.target.value }))}
                placeholder="BTC_USDT"
                className={`${INPUT_CLASS} w-[150px] pl-8`}
              />
              <datalist id="decision-symbols">
                {symbolOptions.map((symbol) => (
                  <option key={symbol} value={symbol} />
                ))}
              </datalist>
            </div>
          </FilterField>
          <FilterField label="Strategy">
            <select
              value={filters.strategy}
              onChange={(event) => setFilters((prev) => ({ ...prev, strategy: event.target.value }))}
              className={INPUT_CLASS}
            >
              <option value="">All</option>
              <option value="L1">L1</option>
              <option value="L2">L2</option>
              <option value="L3">L3</option>
            </select>
          </FilterField>
          <FilterField label="Score Min">
            <input
              type="number"
              min={0}
              max={100}
              value={filters.scoreMin}
              onChange={(event) => setFilters((prev) => ({ ...prev, scoreMin: event.target.value }))}
              className={`${INPUT_CLASS} w-[84px]`}
            />
          </FilterField>
          <FilterField label="Score Max">
            <input
              type="number"
              min={0}
              max={100}
              value={filters.scoreMax}
              onChange={(event) => setFilters((prev) => ({ ...prev, scoreMax: event.target.value }))}
              className={`${INPUT_CLASS} w-[84px]`}
            />
          </FilterField>
          <FilterField label="Decision">
            <select
              value={filters.decision}
              onChange={(event) => setFilters((prev) => ({ ...prev, decision: event.target.value as Filters["decision"] }))}
              className={INPUT_CLASS}
            >
              <option value="ALL">All</option>
              <option value="ALLOW">Allow</option>
              <option value="BLOCK">Block</option>
            </select>
          </FilterField>
          <button
            onClick={applyFilters}
            className="flex items-center gap-1.5 rounded-[var(--radius-sm)] bg-[var(--accent-primary)] px-4 py-1.5 text-[12px] font-medium text-white transition-colors hover:bg-[var(--accent-primary-hover)]"
          >
            <Filter className="h-3.5 w-3.5" />
            Apply
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Audit Entries</h3>
          <span className="caption">{items.length} loaded</span>
        </div>
        <div className="overflow-x-auto">
          {loading ? (
            <div className="space-y-3 p-8">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="skeleton h-10 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center text-[var(--text-secondary)]">
              <FileText className="h-8 w-8 opacity-40" />
              <p className="text-[13px]">{error}</p>
              <button
                onClick={() => void fetchDecisions(appliedFilters)}
                className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[12px] text-[var(--text-primary)]"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="py-16 text-center text-[var(--text-tertiary)]">
              <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
              <p className="text-[13px]">No decisions found…</p>
            </div>
          ) : (
            <table className="data-table text-[12px]">
              <thead>
                <tr>
                  <th className="w-8" />
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Score</th>
                  <th>Decision</th>
                  <th>Event</th>
                  <th>L1</th>
                  <th>L2</th>
                  <th>L3</th>
                  <th>Latency</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <DecisionRow
                    key={item.id}
                    item={item}
                    config={config}
                    expanded={expandedId === item.id}
                    highlighted={highlightedIds.includes(item.id)}
                    onToggle={() => setExpandedId((prev) => (prev === item.id ? null : item.id))}
                  />
                ))}
              </tbody>
            </table>
          )}
        </div>

        {nextCursor && !loading && !error && (
          <div className="flex justify-center border-t border-[var(--border-default)] px-4 py-3">
            <button
              onClick={() => void fetchDecisions(appliedFilters, nextCursor, true)}
              disabled={loadingMore}
              className="rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-1.5 text-[12px] text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60"
            >
              {loadingMore ? "Loading..." : "Load more"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function ApprovedSnapshotView() {
  const [filters, setFilters] = useState<SnapshotFilters>(DEFAULT_SNAPSHOT_FILTERS);
  const [items, setItems] = useState<ApprovedSnapshotItem[]>([]);
  const [watchlists, setWatchlists] = useState<SnapshotWatchlist[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const fetchSnapshot = useCallback(
    async (current: SnapshotFilters, isInitial: boolean) => {
      if (isInitial) {
        setLoading(true);
        setError(null);
      } else {
        setRefreshing(true);
      }
      try {
        const qs = buildSnapshotParams(current);
        const response = await apiGet<ApprovedSnapshotResponse>(
          qs ? `/decisions/approved-snapshot?${qs}` : `/decisions/approved-snapshot`
        );
        setItems(response.items ?? []);
        setAsOf(response.as_of ?? null);
        setError(null);
      } catch (err) {
        if (isInitial) {
          setItems([]);
          setError(err instanceof Error ? err.message : "Failed to load snapshot");
        }
      } finally {
        if (isInitial) setLoading(false);
        else setRefreshing(false);
      }
    },
    []
  );

  useEffect(() => {
    void apiGet<{ items: SnapshotWatchlist[] }>("/decisions/approved-snapshot/watchlists")
      .then((response) => setWatchlists(response.items ?? []))
      .catch(() => setWatchlists([]));
  }, []);

  useEffect(() => {
    void fetchSnapshot(filters, true);
  }, [filters, fetchSnapshot]);

  useEffect(() => {
    const handle = window.setInterval(() => {
      void fetchSnapshot(filters, false);
    }, SNAPSHOT_REFRESH_MS);
    return () => window.clearInterval(handle);
  }, [filters, fetchSnapshot]);

  const updateFilter = <K extends keyof SnapshotFilters>(key: K, value: SnapshotFilters[K]) => {
    setExpandedKey(null);
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const toggleSort = () => {
    setExpandedKey(null);
    setFilters((prev) => ({
      ...prev,
      sort: prev.sort === "score_desc" ? "score_asc" : "score_desc",
    }));
  };

  const formatTimestamp = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

  return (
    <div className="space-y-6">
      <div className="card">
        <div className="flex flex-wrap items-end gap-3 p-4">
          <FilterField label="Symbol">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[var(--text-tertiary)]" />
              <input
                value={filters.symbol}
                onChange={(event) => updateFilter("symbol", event.target.value)}
                placeholder="BTC_USDT"
                className={`${INPUT_CLASS} w-[150px] pl-8`}
              />
            </div>
          </FilterField>
          <FilterField label="Market">
            <select
              value={filters.marketMode}
              onChange={(event) => updateFilter("marketMode", event.target.value as SnapshotFilters["marketMode"])}
              className={INPUT_CLASS}
            >
              <option value="all">All</option>
              <option value="spot">Spot</option>
              <option value="futures">Futures</option>
            </select>
          </FilterField>
          <FilterField label="Watchlist">
            <select
              value={filters.watchlistId}
              onChange={(event) => updateFilter("watchlistId", event.target.value)}
              className={INPUT_CLASS}
            >
              <option value="">All L3</option>
              {watchlists.map((wl) => (
                <option key={wl.id} value={wl.id}>
                  {wl.name} ({wl.market_mode})
                </option>
              ))}
            </select>
          </FilterField>
          <FilterField label="Sort">
            <select
              value={filters.sort}
              onChange={(event) => updateFilter("sort", event.target.value as SnapshotFilters["sort"])}
              className={INPUT_CLASS}
            >
              <option value="score_desc">Score (high → low)</option>
              <option value="score_asc">Score (low → high)</option>
              <option value="symbol_asc">Symbol (A → Z)</option>
              <option value="approved_at_desc">Most recently approved</option>
            </select>
          </FilterField>
          <button
            onClick={() => void fetchSnapshot(filters, false)}
            disabled={refreshing}
            className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-elevated)] px-4 py-1.5 text-[12px] font-medium text-[var(--text-secondary)] transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)] disabled:cursor-not-allowed disabled:opacity-60"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <div className="ml-auto flex items-center gap-1.5 text-[11px] text-[var(--text-tertiary)]">
            <Clock className="h-3.5 w-3.5" />
            <span>Auto-refresh every 30s</span>
            {asOf && <span className="font-mono">· as of {new Date(asOf).toLocaleTimeString()}</span>}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <h3>Currently Approved (L3)</h3>
          <span className="caption">{items.length} approved</span>
        </div>
        <div className="overflow-x-auto">
          {loading ? (
            <div className="space-y-3 p-8">
              {Array.from({ length: 6 }).map((_, index) => (
                <div key={index} className="skeleton h-10 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center text-[var(--text-secondary)]">
              <FileText className="h-8 w-8 opacity-40" />
              <p className="text-[13px]">{error}</p>
              <button
                onClick={() => void fetchSnapshot(filters, true)}
                className="flex items-center gap-1.5 rounded-[var(--radius-sm)] border border-[var(--border-default)] bg-[var(--bg-elevated)] px-3 py-1.5 text-[12px] text-[var(--text-primary)]"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                Retry
              </button>
            </div>
          ) : items.length === 0 ? (
            <div className="py-16 text-center text-[var(--text-tertiary)]">
              <FileText className="mx-auto mb-2 h-8 w-8 opacity-30" />
              <p className="text-[13px]">Nenhuma cripto aprovada em L3 no momento.</p>
            </div>
          ) : (
            <table className="data-table text-[12px]">
              <thead>
                <tr>
                  <th className="w-8" />
                  <th>Symbol</th>
                  <th>
                    <button
                      type="button"
                      onClick={toggleSort}
                      className="inline-flex items-center gap-1 text-inherit"
                    >
                      Score
                      {filters.sort === "score_desc" ? (
                        <ArrowDown className="h-3 w-3" />
                      ) : filters.sort === "score_asc" ? (
                        <ArrowUp className="h-3 w-3" />
                      ) : null}
                    </button>
                  </th>
                  <th>Direction</th>
                  <th>Watchlist</th>
                  <th>Stage</th>
                  <th>Market</th>
                  <th>Approved at</th>
                  <th>Indicators</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const key = `${item.watchlist_id}:${item.symbol}`;
                  const expanded = expandedKey === key;
                  return (
                    <SnapshotRow
                      key={key}
                      item={item}
                      expanded={expanded}
                      onToggle={() => setExpandedKey((prev) => (prev === key ? null : key))}
                      formatTimestamp={formatTimestamp}
                    />
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

function SnapshotRow({
  item,
  expanded,
  onToggle,
  formatTimestamp,
}: {
  item: ApprovedSnapshotItem;
  expanded: boolean;
  onToggle: () => void;
  formatTimestamp: (iso: string | null) => string;
}) {
  const indicatorChips = item.indicators.slice(0, 3);
  const moreIndicators = Math.max(0, item.indicators.length - indicatorChips.length);

  return (
    <>
      <tr onClick={onToggle} className="cursor-pointer transition-colors">
        <td>
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />
          )}
        </td>
        <td className="font-semibold text-[var(--text-primary)]">{item.symbol}</td>
        <td>
          <span className={`inline-flex rounded border px-2 py-0.5 font-mono text-[11px] ${scoreTone(item.score)}`}>
            {item.score === null ? "—" : item.score.toFixed(1)}
          </span>
        </td>
        <td>
          {item.direction ? (
            <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-medium ${directionTone(item.direction)}`}>
              {item.direction}
            </span>
          ) : (
            <span className="text-[var(--text-tertiary)]">—</span>
          )}
        </td>
        <td className="text-[var(--text-secondary)]">{item.watchlist_name}</td>
        <td>
          <span className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-primary)]">
            {item.stage}
          </span>
        </td>
        <td className="text-[var(--text-secondary)]">{item.market_mode}</td>
        <td className="text-[var(--text-secondary)]">{formatTimestamp(item.approved_at)}</td>
        <td>
          <div className="flex flex-wrap gap-1">
            {indicatorChips.map((entry, idx) => (
              <span
                key={idx}
                className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[11px] text-[var(--text-secondary)]"
              >
                {indicatorChipLabel(entry)}
              </span>
            ))}
            {moreIndicators > 0 && (
              <span className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-0.5 text-[11px] text-[var(--text-tertiary)]">
                +{moreIndicators}
              </span>
            )}
          </div>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={9} className="!p-0">
            <SnapshotDetailPanel item={item} />
          </td>
        </tr>
      )}
    </>
  );
}

function SnapshotDetailPanel({ item }: { item: ApprovedSnapshotItem }) {
  return (
    <div className="space-y-4 border-t border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
      <div className="grid gap-4 md:grid-cols-3">
        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Scores</p>
          <div className="space-y-1 text-[12px]">
            <div className="flex justify-between gap-3 rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1">
              <span className="text-[var(--text-secondary)]">Alpha</span>
              <span className="font-mono text-[var(--text-primary)]">{formatMetricValue(item.alpha_score)}</span>
            </div>
            <div className="flex justify-between gap-3 rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1">
              <span className="text-[var(--text-secondary)]">Long</span>
              <span className="font-mono text-[var(--text-primary)]">{formatMetricValue(item.score_long)}</span>
            </div>
            <div className="flex justify-between gap-3 rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1">
              <span className="text-[var(--text-secondary)]">Short</span>
              <span className="font-mono text-[var(--text-primary)]">{formatMetricValue(item.score_short)}</span>
            </div>
          </div>
        </section>

        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Indicators</p>
          <div className="flex flex-wrap gap-2">
            {item.indicators.length > 0 ? (
              item.indicators.map((entry, idx) => (
                <span
                  key={idx}
                  className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1 text-[11px] text-[var(--text-secondary)]"
                >
                  {indicatorChipLabel(entry)}
                </span>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-secondary)]">No indicators captured.</span>
            )}
          </div>
        </section>

        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Score rules</p>
          <div className="flex flex-wrap gap-2">
            {item.score_rules.length > 0 ? (
              item.score_rules.map((entry, idx) => (
                <span
                  key={idx}
                  className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1 text-[11px] text-[var(--text-secondary)]"
                >
                  {indicatorChipLabel(entry)}
                </span>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-secondary)]">No score rules captured.</span>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[11px] uppercase tracking-wider text-[var(--text-tertiary)]">{label}</label>
      {children}
    </div>
  );
}

function DecisionRow({
  item,
  config,
  expanded,
  highlighted,
  onToggle,
}: {
  item: DecisionItem;
  config: DecisionLogConfig | null;
  expanded: boolean;
  highlighted: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        onClick={onToggle}
        className={`cursor-pointer transition-colors ${highlighted ? "bg-[rgba(79,123,247,0.08)]" : ""}`}
      >
        <td>{expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--text-tertiary)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--text-tertiary)]" />}</td>
        <td className="text-[var(--text-secondary)]">{new Date(item.created_at).toLocaleString()}</td>
        <td className="font-semibold text-[var(--text-primary)]">{item.symbol}</td>
        <td>
          <span className="inline-flex rounded border border-[var(--border-default)] bg-[var(--bg-elevated)] px-2 py-0.5 font-mono text-[11px] text-[var(--text-primary)]">
            {item.strategy}
          </span>
        </td>
        <td>
          <span className={`inline-flex rounded border px-2 py-0.5 font-mono text-[11px] ${scoreTone(item.score)}`}>
            {(item.score ?? 0).toFixed(1)}
          </span>
        </td>
        <td>
          <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-medium ${decisionTone(item.decision)}`}>
            {item.decision}
          </span>
        </td>
        <td>
          {item.event_type && (
            <span className={`inline-flex rounded border px-2 py-0.5 text-[11px] font-medium ${eventTypeTone(item.event_type)}`}>
              {eventTypeLabel(item.event_type)}
            </span>
          )}
        </td>
        <td>{gateMark(item.l1_pass)}</td>
        <td>{gateMark(item.l2_pass)}</td>
        <td>{gateMark(item.l3_pass)}</td>
        <td className="font-mono text-[var(--text-secondary)]">{item.latency_ms ?? 0}ms</td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={11} className="!p-0">
            <DetailPanel item={item} config={config} />
          </td>
        </tr>
      )}
    </>
  );
}

function DetailPanel({ item, config }: { item: DecisionItem; config: DecisionLogConfig | null }) {
  const reasons = Object.entries(item.reasons ?? {});
  const metrics = Object.entries(item.metrics ?? {});

  return (
    <div className="space-y-4 border-t border-[var(--border-subtle)] bg-[var(--bg-elevated)] p-4">
      <div className="grid gap-4 md:grid-cols-3">
        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Reasons</p>
          <div className="flex flex-wrap gap-2">
            {reasons.length > 0 ? (
              reasons.map(([key, value]) => (
                <span
                  key={key}
                  className={`inline-flex rounded border px-2 py-1 text-[11px] ${
                    String(value).toUpperCase() === "OK"
                      ? "border-[var(--color-profit-border)] bg-[var(--color-profit-muted)] text-[var(--color-profit)]"
                      : "border-[var(--color-loss-border)] bg-[var(--color-loss-muted)] text-[var(--color-loss)]"
                  }`}
                >
                  {key}: {String(value)}
                </span>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-secondary)]">No reasons captured.</span>
            )}
          </div>
        </section>

        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Metrics</p>
          <div className="space-y-1 text-[12px]">
            {metrics.length > 0 ? (
              metrics.slice(0, config?.max_displayed_metrics).map(([key, value]) => (
                <div key={key} className="flex justify-between gap-3 rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1">
                  <span className="text-[var(--text-secondary)]">{key}</span>
                  <span className="font-mono text-[var(--text-primary)]">{formatMetricValue(value)}</span>
                </div>
              ))
            ) : (
              <span className="text-[12px] text-[var(--text-secondary)]">No metrics captured.</span>
            )}
          </div>
        </section>

        <section>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">Timeline</p>
          <div className="space-y-2 text-[12px]">
            {[
              { label: "L1", passed: item.l1_pass },
              { label: "L2", passed: item.l2_pass },
              { label: "L3", passed: item.l3_pass },
            ].map((step) => (
              <div key={step.label} className="flex items-center justify-between rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1.5">
                <span className="font-mono text-[var(--text-primary)]">{step.label}</span>
                <span className={step.passed ? "text-[var(--color-profit)]" : "text-[var(--color-loss)]"}>
                  {step.passed ? "PASS" : "FAIL"}
                </span>
              </div>
            ))}
            <div className="rounded border border-[var(--border-default)] bg-[var(--bg-input)] px-2 py-1.5 text-[var(--text-secondary)]">
              Total latency: <span className="font-mono text-[var(--text-primary)]">{item.latency_ms ?? 0}ms</span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

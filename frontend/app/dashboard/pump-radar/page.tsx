"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CalendarDays, ChevronLeft, ChevronRight, Download, EyeOff, FileSearch,
  ListFilter, Pause, Play, RefreshCw, Search, Settings2, ShieldAlert,
} from "lucide-react";

import { ApiError, apiGet, apiPost, apiPut } from "@/lib/api";
import { PumpRadarChart, type RadarCandle, type RadarMarker } from "@/components/pump-radar/PumpRadarChart";
import { ModuleAIAnalysisAction } from "@/components/ai/ModuleAIAnalysisAction";
import styles from "./pump-radar.module.css";

type Envelope<T> = { schema: string; timezone: string; generated_at: string; provenance: Record<string, unknown>; data: T };
type Capabilities = { market: string; capture_enabled: boolean; analysis_enabled: boolean; ui_enabled: boolean; timeframes: string[]; exports: string[]; profile_mutation: false };
type Run = { id: string; status: string; universe_compatible: boolean; mode: string; date_from: string | null; date_to: string | null; config_hash: string; total_assets: number; processed_assets: number; failed_assets: number; event_count: number; quality_badge: string; requested_at: string; summary?: { pumps_identified: number; with_shadow_entry: number; without_entry: number; shadow_links: number; median_delay_seconds: number | null } };
type AssetEvent = { event_id: string; symbol: string; rise_pct: number; start_at: string; confirmed_market_at: string; detected_at: string | null; peak_at: string; end_at: string | null; reconstruction_status: string; quality_status: string; shadow_links: number; shadow_entries: number };
type EventLink = { id: string; shadow_trade_id: string | null; decision_id: number | null; link_kind: string; is_primary: boolean; approval_at: string | null; simulation_created_at: string | null; entry_at: string | null; exit_at: string | null; delay_seconds: number | null; realized_pnl_pct: number | null; profile_id: string | null; profile_version_id: string | null; profile_config_hash: string | null; provenance: Record<string, unknown> };
type EventDetail = AssetEvent & { id: string; start_price: number; peak_price: number; end_price: number | null; retracement_pct: number | null; is_incomplete: boolean; provenance: Record<string, unknown>; links: EventLink[] };
type ChartData = { event_id: string; symbol: string; selected_at: string; hide_future: boolean; reconstruction_status: string; candles: Record<"1h" | "15m" | "5m", RadarCandle[]>; markers: RadarMarker[] };
type RangeRow = { id: string; indicator_id: string; layer: string; timeframe: string; range_key: string; lower_bound: number | null; upper_bound: number | null; pump_numerator: number; pump_denominator: number; control_numerator: number; control_denominator: number; coverage: number | null; validation_status: string; confidence_interval: unknown };
type RadarConfig = { minimum_rise_pct: number; maximum_window_minutes: number; retracement_pct: number; no_new_high_minutes: number; maximum_duration_minutes: number; merge_gap_minutes: number; backfill_days: number; universe_max_assets: number; volume_filter_enabled: boolean; liquidity_filter_enabled: boolean; atr_filter_enabled: boolean; [key: string]: unknown };
type AnchorSample = { event_id: string; link_id?: string; symbol: string; value: number | string };
type AnchorStat = { count: number; numeric_coverage: number; min: number | null; max: number | null; samples: AnchorSample[] };
type IndicatorSummaryRow = { layer: string; timeframe: string; indicator_id: string; anchors: { before: AnchorStat; start: AnchorStat; approval: AnchorStat; entry: AnchorStat } };
type IndicatorSummary = { event_count: number; link_count: number; indicators: IndicatorSummaryRow[] };
type ReportRun = { id: string; selection_mode: string; total_events: number; selection_hash: string; created_at: string };

const EMPTY_SUMMARY = { pumps_identified: 0, with_shadow_entry: 0, without_entry: 0, shadow_links: 0, median_delay_seconds: null };
const LAYER_COLORS: Record<string, string> = { POOL: "#50d5a3", L1: "#aa76ed", L2: "#efbd42", L3: "#8e63e8" };

function localTime(value: string | null | undefined, withDate = false) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("pt-BR", withDate ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false } : { hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value));
}
function percent(value: number | null | undefined) { return value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`; }
function delay(value: number | null | undefined) { return value == null ? "—" : `${value < 0 ? "−" : "+"}${Math.round(Math.abs(value) / 60)} min`; }
function compactId(value: string | null | undefined) { return value ? `${value.slice(0, 8)}…${value.slice(-5)}` : "—"; }
function valueText(value: number | string | null | undefined) { return typeof value === "number" ? new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 4 }).format(value) : value ?? "—"; }
function midpoint(anchor?: AnchorStat) { return anchor && anchor.numeric_coverage ? ((anchor.min ?? 0) + (anchor.max ?? 0)) / 2 : null; }
function anchorText(anchor?: AnchorStat) {
  if (!anchor || anchor.count === 0) return "—";
  if (anchor.numeric_coverage > 0) {
    return anchor.min === anchor.max ? `${valueText(anchor.min)} (n=${anchor.count})` : `${valueText(anchor.min)}–${valueText(anchor.max)} (n=${anchor.count})`;
  }
  const unique = Array.from(new Set(anchor.samples.map((sample) => String(sample.value))));
  return `${unique.slice(0, 2).join(", ")}${unique.length > 2 ? "…" : ""} (n=${anchor.count})`;
}
function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
}

export default function PumpRadarPage() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<AssetEvent[]>([]);
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [chart, setChart] = useState<ChartData | null>(null);
  const [ranges, setRanges] = useState<RangeRow[]>([]);
  const [config, setConfig] = useState<RadarConfig | null>(null);
  const [search, setSearch] = useState("");
  const [contextTf, setContextTf] = useState("5min");
  const [rangeTf, setRangeTf] = useState("combined");
  const [layer, setLayer] = useState("TODAS");
  const [hideFuture, setHideFuture] = useState(true);
  const [syncViews, setSyncViews] = useState(true);
  const [selectedAt, setSelectedAt] = useState<string | null>(null);
  const [selectionMode, setSelectionMode] = useState<"asset" | "all">("asset");
  const [selectionSummary, setSelectionSummary] = useState<IndicatorSummary | null>(null);
  const [reportRun, setReportRun] = useState<ReportRun | null>(null);
  const [materializing, setMaterializing] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileRankOpen, setMobileRankOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const eventRequest = useRef(0);
  // Mirrors `event` without being a `load` dependency, so the 10s poll
  // (below) can tell whether the current selection is still valid without
  // recreating `load` on every selection change -- recreating it would
  // retrigger the mount-effect that calls it and cause a request loop.
  const selectedEventIdRef = useRef<string | null>(null);
  useEffect(() => { selectedEventIdRef.current = event?.event_id ?? null; }, [event]);

  const loadEvent = useCallback(async (runId: string, eventId: string, point?: string | null, conceal = true) => {
    const request = ++eventRequest.current;
    const detail = await apiGet<Envelope<EventDetail>>(`/pump-radar/runs/${runId}/events/${eventId}`);
    if (request !== eventRequest.current) return;
    setChart(null); setEvent(detail.data); setSelectedAt(point ?? detail.data.start_at);
    setHideFuture(conceal);
  }, []);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [caps, runs, configResponse] = await Promise.all([
        apiGet<Envelope<Capabilities>>("/pump-radar/capabilities"),
        apiGet<Envelope<Run[]>>("/pump-radar/runs?limit=1"),
        apiGet<{ data: RadarConfig }>("/config/pump_radar"),
      ]);
      setCapabilities(caps.data); setConfig(configResponse.data);
      const latest = runs.data[0] ?? null;
      if (!latest) { setRun(null); setEvents([]); setEvent(null); return; }
      if (!latest.universe_compatible) {
        ++eventRequest.current;
        setRun(latest); setEvents([]); setEvent(null); setChart(null); setRanges([]);
        return;
      }
      const [runResponse, assetResponse] = await Promise.all([
        apiGet<Envelope<Run>>(`/pump-radar/runs/${latest.id}`),
        apiGet<Envelope<{ items: AssetEvent[] }>>(`/pump-radar/runs/${latest.id}/assets?limit=100`),
      ]);
      setRun(runResponse.data); setEvents(assetResponse.data.items);
      // Only touch the detail panel (event/chart) when the previously
      // selected event fell out of the refreshed list -- otherwise every
      // periodic poll blanks and re-fetches an unchanged selection, which
      // reads as the whole page flickering while a run is still processing.
      const items = assetResponse.data.items;
      const stillSelected = items.some((item) => item.event_id === selectedEventIdRef.current);
      if (!stillSelected) {
        const first = items[0];
        if (first) await loadEvent(latest.id, first.event_id, null, true);
        else { setEvent(null); setChart(null); setRanges([]); }
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.detail ?? cause.message : cause instanceof Error ? cause.message : "Falha ao carregar o Radar de Pumps");
    } finally { setLoading(false); }
  }, [loadEvent]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!run || !["QUEUED", "RUNNING", "CANCELLING"].includes(run.status)) return;
    const timer = window.setInterval(() => void load(), 10000);
    return () => window.clearInterval(timer);
  }, [load, run]);

  useEffect(() => {
    if (!run || !event) return;
    let current = true;
    void apiGet<Envelope<ChartData>>(`/pump-radar/runs/${run.id}/events/${event.id}/chart?hide_future=${hideFuture}${hideFuture && selectedAt ? `&selected_at=${encodeURIComponent(selectedAt)}` : ""}`)
      .then((response) => { if (current) setChart(response.data); })
      .catch(() => { if (current) { setChart(null); setError("Falha ao carregar os candles deste instante."); } });
    return () => { current = false; };
  }, [run, event, selectedAt, hideFuture]);

  useEffect(() => {
    if (!run?.universe_compatible) return;
    let current = true;
    void apiGet<Envelope<RangeRow[]>>(`/pump-radar/runs/${run.id}/ranges?timeframe=${rangeTf}`)
      .then((response) => { if (current) setRanges(response.data); })
      .catch(() => { if (current) { setRanges([]); setError("Falha ao carregar as faixas comparativas."); } });
    return () => { current = false; };
  }, [run?.id, run?.status, run?.universe_compatible, rangeTf]);

  useEffect(() => {
    if (!playing || !event || !selectedAt) return;
    const end = +new Date(event.end_at ?? event.peak_at);
    const timer = window.setInterval(() => {
      setSelectedAt((current) => {
        if (!current) return event.start_at;
        const next = Math.min(+new Date(current) + 5 * 60_000, end);
        if (next >= end) setPlaying(false);
        const iso = new Date(next).toISOString();
        return iso;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [event, playing, selectedAt]);

  async function selectEvent(item: AssetEvent) {
    if (!run) return;
    setBusy(true); setPlaying(false);
    try { await loadEvent(run.id, item.event_id, null, true); }
    catch { setError("Falha ao carregar o evento selecionado."); }
    finally { setBusy(false); }
  }
  async function createRun(mode: "incremental" | "backfill") {
    setBusy(true); setError(null);
    try { await apiPost("/pump-radar/runs", { mode }); await load(); }
    catch (cause) { setError(cause instanceof ApiError ? cause.detail : "Não foi possível iniciar a execução"); }
    finally { setBusy(false); }
  }
  async function saveConfig() {
    if (!config) return;
    setBusy(true);
    try { await apiPut("/config/pump_radar", config); setConfigOpen(false); }
    catch (cause) { setError(cause instanceof ApiError ? cause.detail : "Configuração inválida"); }
    finally { setBusy(false); }
  }
  async function exportEvents() {
    if (!run) return;
    const token = localStorage.getItem("token");
    const response = await fetch(`/api/pump-radar/runs/${run.id}/export?dataset=events&format=csv`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!response.ok) { setError("Falha ao exportar os eventos"); return; }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `pump-radar-${run.id}-events.csv`; anchor.click(); URL.revokeObjectURL(url);
  }

  const filteredEvents = useMemo(() => events.filter((item) => item.symbol.toLowerCase().includes(search.toLowerCase())), [events, search]);
  const summary = run?.universe_compatible ? run.summary ?? EMPTY_SUMMARY : EMPTY_SUMMARY;
  const primaryLink = event?.links.find((link) => link.is_primary) ?? event?.links[0] ?? null;
  const timelineStart = event ? +new Date(event.start_at) - 30 * 60_000 : 0;
  const timelineEnd = event ? +new Date(event.end_at ?? event.peak_at) : 0;
  const selectedOffset = event && selectedAt ? Math.max(0, Math.round((+new Date(selectedAt) - timelineStart) / 60_000)) : 0;
  const maxOffset = event ? Math.max(5, Math.round((timelineEnd - timelineStart) / 60_000)) : 5;

  const selectionEventIds = useMemo(() => (
    selectionMode === "all" ? filteredEvents.map((item) => item.event_id) : event ? [event.id] : []
  ), [selectionMode, filteredEvents, event]);
  const selectionKey = selectionEventIds.join(",");

  useEffect(() => {
    if (!run?.universe_compatible || !selectionEventIds.length) { setSelectionSummary(null); return; }
    let current = true;
    void apiGet<Envelope<IndicatorSummary>>(`/pump-radar/runs/${run.id}/indicator-summary?event_ids=${selectionKey}`)
      .then((response) => { if (current) setSelectionSummary(response.data); })
      .catch(() => { if (current) { setSelectionSummary(null); setError("Falha ao carregar o comparativo da seleção."); } });
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.id, run?.universe_compatible, selectionKey]);

  useEffect(() => { setReportRun(null); }, [selectionKey, selectionMode]);

  const rows = useMemo(() => {
    const indicators = selectionSummary?.indicators ?? [];
    const filtered = layer === "TODAS" ? indicators : indicators.filter((row) => row.layer === layer);
    return filtered.map((row) => {
      const beforeMid = midpoint(row.anchors.before);
      const approvalMid = midpoint(row.anchors.approval);
      return { ...row, change: beforeMid != null && approvalMid != null ? approvalMid - beforeMid : null };
    });
  }, [selectionSummary, layer]);

  async function materializeSelection() {
    if (!run || !selectionEventIds.length) return;
    setMaterializing(true); setError(null);
    try {
      const response = await apiPost<Envelope<ReportRun>>(`/pump-radar/runs/${run.id}/report-runs`, { event_ids: selectionEventIds });
      setReportRun(response.data);
    } catch (cause) { setError(cause instanceof ApiError ? cause.detail : "Não foi possível materializar a seleção"); }
    finally { setMaterializing(false); }
  }
  async function exportReportRun() {
    if (!reportRun) return;
    const token = localStorage.getItem("token");
    const response = await fetch(`/api/pump-radar/report-runs/${reportRun.id}/export`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!response.ok) { setError("Falha ao exportar o relatório consolidado"); return; }
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = `pump-radar-report-${reportRun.id}.json`; anchor.click(); URL.revokeObjectURL(url);
  }

  const markers = chart?.markers ?? [];
  const shortcutTimes = event ? [
    ["T−30", new Date(+new Date(event.start_at) - 30 * 60_000).toISOString()],
    ["T−15", new Date(+new Date(event.start_at) - 15 * 60_000).toISOString()],
    ["T−10", new Date(+new Date(event.start_at) - 10 * 60_000).toISOString()],
    ["T−5", new Date(+new Date(event.start_at) - 5 * 60_000).toISOString()],
    ["T0", event.start_at], ["Aprovação", primaryLink?.approval_at], ["Entrada", primaryLink?.entry_at], ["Pico", event.peak_at],
  ].filter((item): item is [string, string] => Boolean(item[1])) : [];

  if (loading) return <div className={styles.empty}><RefreshCw className="animate-spin" size={22} /><span>Carregando contratos e cobertura do Radar…</span></div>;

  return (
    <div className={styles.page}>
      <div className={styles.toolbar}>
        <div>
          <div className="mb-1 text-[10px] text-[#7185a4]">Intelligence <span className="mx-1 text-[#3c526f]">/</span> Radar de Pumps</div>
          <h1 className="text-[25px] tracking-[-.04em] text-[#edf3fb]">Radar de Pumps</h1>
          <p className="mt-0.5 text-[11px] text-[#8294b0]">Padrões do movimento × entradas Shadow</p>
        </div>
        <div className={styles.actions}>
          <span className={`${styles.badge} ${run?.quality_badge === "COBERTURA PARCIAL" ? styles.badgePartial : ""}`}>{run?.quality_badge ?? "DADOS INSUFICIENTES"}</span>
          <button className={styles.control}><CalendarDays size={14} />{run?.date_from ? localTime(run.date_from, true) : "Sem execução"}</button>
          <button className={styles.control}>Gate • Spot</button><button className={styles.control}>UTC−3</button>
          <button className={styles.control} onClick={() => setConfigOpen((value) => !value)}><Settings2 size={14} />Configurar</button>
          <button className={`${styles.control} ${styles.mobileRankButton}`} onClick={() => { setCollapsed(false); setMobileRankOpen(true); }}><ListFilter size={14} />Ranking</button>
          <button className={styles.primary} onClick={exportEvents} disabled={!run}><Download size={14} />Exportar</button>
        </div>
      </div>

      {error && <div className={`${styles.notice} mb-3 border-[#7b3744] text-[#f29aaa]`}><ShieldAlert size={14} className="mr-2 inline" />{error}</div>}
      {!capabilities?.ui_enabled && <div className={`${styles.notice} mb-3`}><ShieldAlert size={14} className="mr-2 inline" />A interface está instalada e protegida pela flag <span className={styles.mono}>PUMP_RADAR_UI_ENABLED</span>. Os controles de captura e análise permanecem desligados até os gates de qualidade serem aprovados.</div>}

      {configOpen && config && <section className={`${styles.panel} ${styles.configPanel}`}>
        <div className="mb-3 flex items-center justify-between"><div><h2 className="text-[13px]">Detector exploratório</h2><p className="text-[9px] text-[#7e91ad]">Configuração versionada em pump_radar_v1. Não altera profiles operacionais.</p></div><button className={styles.primary} onClick={saveConfig} disabled={busy}>Salvar</button></div>
        <div className={styles.configGrid}>
          {([ ["minimum_rise_pct", "Alta mínima (%)"], ["maximum_window_minutes", "Janela máxima (min)"], ["retracement_pct", "Retração de término (%)"], ["no_new_high_minutes", "Sem nova máxima (min)"], ["maximum_duration_minutes", "Duração máxima (min)"], ["merge_gap_minutes", "Fusão entre eventos (min)"], ["backfill_days", "Backfill (dias)"], ["universe_max_assets", "Máximo de ativos do pool"], ["context_candles", "Candles de contexto por TF"] ] as const).map(([key, label]) => <div className={styles.field} key={key}><label>{label}</label><input type="number" value={config[key] as number} onChange={(e) => setConfig({ ...config, [key]: Number(e.target.value) })} /></div>)}
          {([ ["volume_filter_enabled", "Filtro de volume"], ["liquidity_filter_enabled", "Filtro de liquidez"], ["atr_filter_enabled", "Filtro de ATR"] ] as const).map(([key, label]) => <label className="flex h-8 items-center gap-2 self-end rounded-md border border-[#273c59] bg-[#0a1524] px-3 text-[9px] text-[#8da0bc]" key={key}><input type="checkbox" checked={Boolean(config[key])} onChange={(e) => setConfig({ ...config, [key]: e.target.checked })} />{label}</label>)}
        </div>
      </section>}

      <div className={styles.metrics}>
        {[ ["Pumps identificados", summary.pumps_identified, "#51d9a9"], ["Com entrada shadow", summary.with_shadow_entry, "#aa76ed"], ["Sem entrada", summary.without_entry, "#9aa8bd"], ["Atraso mediano", summary.median_delay_seconds == null ? "—" : delay(summary.median_delay_seconds), "#f4bd3f"] ].map(([label, value, color]) => <div className={styles.metric} style={{ "--metric-color": color } as React.CSSProperties} key={label as string}><div className={styles.metricLabel}>{label}</div><div className={styles.metricValue}>{value}</div></div>)}
      </div>

      {!run && <div className={styles.notice}><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="font-semibold text-[#c9d7e9]">Nenhuma execução disponível</div><div className="mt-1 text-[10px]">A tela não usa dados de demonstração. Inicie uma captura quando a flag operacional estiver habilitada.</div></div><div className="flex gap-2"><button className={styles.control} disabled={!capabilities?.capture_enabled || busy} onClick={() => createRun("incremental")}>Executar hoje</button><button className={styles.primary} disabled={!capabilities?.capture_enabled || busy} onClick={() => createRun("backfill")}>Backfill configurado</button></div></div></div>}

      {run && <div className={`${styles.notice} mb-3`}><div className="flex flex-wrap items-center justify-between gap-3"><span>{run.universe_compatible ? "Universo: pool + listas L1/L2/L3 deste usuário, congelado na execução. A composição histórica do pool não é comprovada." : "Execução antiga fora do contrato do pool. Seus resultados foram preservados, mas não são apresentados como análise do seu universo. Inicie uma nova execução."}</span><div className="flex gap-2"><button className={styles.control} disabled={!capabilities?.capture_enabled || busy || ["RUNNING", "QUEUED"].includes(run.status)} onClick={() => createRun("incremental")}>Executar hoje no pool</button><button className={styles.primary} disabled={!capabilities?.capture_enabled || busy || ["RUNNING", "QUEUED"].includes(run.status)} onClick={() => createRun("backfill")}>Backfill do pool</button></div></div></div>}

      {run?.universe_compatible && <>
        <div className={`${styles.mainGrid} ${collapsed ? styles.mainGridCollapsed : ""}`}>
          {collapsed ? <aside className={`${styles.panel} ${styles.collapsedRail}`}><button className={styles.ghost} onClick={() => setCollapsed(false)} title="Abrir ranking"><ChevronRight size={15} /></button></aside> : <aside className={`${styles.panel} ${styles.rankPanel} ${mobileRankOpen ? styles.rankOpen : ""}`}>
            <div className={styles.panelHeader}><strong className="text-[12px]">TOP pumps do dia</strong><div className={styles.segmented}>{["5min", "15min", "1h"].map((item) => <button key={item} onClick={() => setContextTf(item)} className={`${styles.segment} ${contextTf === item ? styles.segmentActive : ""}`}>{item}</button>)}</div><button onClick={() => { setCollapsed(true); setMobileRankOpen(false); }} className="text-[#7589a6]" title="Recolher ranking"><ChevronLeft size={14} /></button></div>
            <label className={styles.search}><Search size={13} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar ativo…" /></label>
            <div className={styles.rankHead}><span>#</span><span>Ativo</span><span>Pump</span><span>Shadow</span></div>
            <div className={`${styles.rankList} px-1`}>
              <button onClick={() => { setSelectionMode("all"); setMobileRankOpen(false); }} className={`${styles.rankRow} ${selectionMode === "all" ? styles.rankSelected : ""} w-full text-left`} title="Comparativo agregado de todos os pumps filtrados abaixo"><span /><span className="truncate font-semibold text-[#e7edf7]">Todos</span><span className={styles.positive}>{filteredEvents.length}</span><span>{filteredEvents.length} pump{filteredEvents.length === 1 ? "" : "s"}</span></button>
              {filteredEvents.map((item, index) => <button key={item.event_id} onClick={() => { setSelectionMode("asset"); void selectEvent(item); setMobileRankOpen(false); }} className={`${styles.rankRow} ${selectionMode === "asset" && event?.id === item.event_id ? styles.rankSelected : ""} w-full text-left`}><span>{index + 1}</span><span className="truncate">{item.symbol.replace("_", "/")}</span><span className={styles.positive}>{percent(item.rise_pct)}</span><span>{item.shadow_entries ? `${item.shadow_entries} entrada${item.shadow_entries > 1 ? "s" : ""}` : item.shadow_links ? `${item.shadow_links} vínculo${item.shadow_links > 1 ? "s" : ""}` : "Sem entrada"}</span></button>)}
              {!filteredEvents.length && <div className="p-5 text-center text-[10px] text-[#71839e]">Nenhum evento real corresponde ao filtro.</div>}
            </div>
            <div className={styles.profile}><div className="mb-2 flex items-center gap-2 text-[11px] font-semibold"><FileSearch size={14} />Profile selecionado</div><div className={styles.profileCard}><div className="text-[10px] font-semibold text-[#d4dfed]">{primaryLink?.profile_id ? compactId(primaryLink.profile_id) : "Sem profile histórico vinculado"}</div><div className="mt-1 text-[9px] text-[#8295b0]">Trade {compactId(primaryLink?.shadow_trade_id)} • versão {compactId(primaryLink?.profile_version_id)}</div><div className="mt-1 break-all text-[8px] text-[#657a98]">hash {compactId(primaryLink?.profile_config_hash)}</div><button className={`${styles.ghost} mt-2 w-full justify-between`} disabled={!event?.links.length}>Ver {event?.links.length ?? 0} aprovação(ões) <ChevronRight size={13} /></button></div>
              <div className={styles.facts}>{[["Início", localTime(event?.start_at)], ["Aprovação", localTime(primaryLink?.approval_at)], ["Entrada", localTime(primaryLink?.entry_at)], ["Atraso", delay(primaryLink?.delay_seconds)], ["Mov. até pico", percent(event?.rise_pct)]].map(([label, value]) => <div key={label}><div className={styles.factLabel}>{label}</div><div className={`${styles.factValue} ${label === "Mov. até pico" ? "text-[#55d9a9]" : ""}`}>{value}</div></div>)}</div></div>
          </aside>}

          <section className={`${styles.panel} ${styles.charts}`}>
            {selectionMode === "all" ? (
              <>
                <div className={styles.chartHeader}><div><div className="text-[16px] font-bold">Todos os pumps filtrados</div><div className="mt-0.5 text-[9px] text-[#7487a4]">{filteredEvents.length} evento{filteredEvents.length === 1 ? "" : "s"} agregados no comparativo abaixo</div></div></div>
                <div className={styles.empty}>O gráfico e a linha do tempo são por ativo. Selecione um pump específico na lista à esquerda para vê-los; o comparativo e os indicadores da seleção abaixo já refletem todos os {filteredEvents.length} pumps filtrados.</div>
              </>
            ) : <>
            <div className={styles.chartHeader}><div><div className="text-[16px] font-bold">{event?.symbol.replace("_", "/") ?? "Selecione um evento"}</div><div className="mt-0.5 text-[9px] text-[#7487a4]">{event ? `Evento ${compactId(event.id)} • ${localTime(event.start_at)}–${localTime(event.end_at)}` : "Sem dados disponíveis"}</div></div><div className="flex flex-wrap items-center gap-3"><div className={styles.segmented}>{["1h", "15min", "5min"].map((item) => <span key={item} className={`${styles.segment} ${item === "5min" ? styles.segmentActive : ""} grid place-items-center`}>{item}</span>)}</div><label className="flex items-center gap-2 text-[9px] text-[#8799b5]"><input type="checkbox" checked={syncViews} onChange={(e) => setSyncViews(e.target.checked)} />Sincronizar visões</label><label className="flex items-center gap-2 text-[9px] text-[#8799b5]"><input type="checkbox" checked={hideFuture} onChange={(e) => setHideFuture(e.target.checked)} /><EyeOff size={12} />Ocultar futuro</label></div></div>
            <div className={styles.miniGrid}><PumpRadarChart candles={chart?.candles["1h"] ?? []} title="1h · Contexto" subtitle="Candles fechados e médias móveis" compact showMarkers={false} /><PumpRadarChart candles={chart?.candles["15m"] ?? []} title="15min · Formação" subtitle="Janela point-in-time sincronizada" compact showMarkers={false} /></div>
            <div className={styles.mainChart}><PumpRadarChart candles={chart?.candles["5m"] ?? []} markers={markers} title="5min · Pump e aprovações shadow" subtitle={chart?.reconstruction_status === "RECONSTRUCTED" ? "RECONSTRUÍDO · disponibilidade histórica não comprovada" : "Gate Spot · somente candles fechados disponíveis no instante"} showVolume highlightStart={event?.start_at} highlightEnd={event?.end_at} /></div>
            <div className={styles.timeline}><button className={styles.play} onClick={() => { setHideFuture(true); setPlaying((value) => !value); }} disabled={!event}>{playing ? <Pause size={16} /> : <Play size={16} />}</button><div><input className={styles.range} type="range" min={0} max={maxOffset} step={5} value={selectedOffset} disabled={!event} onChange={(e) => { const iso = new Date(timelineStart + Number(e.target.value) * 60_000).toISOString(); setSelectedAt(iso); setHideFuture(true);  }} /><div className="text-[9px] text-[#7990af]">Instante selecionado: <span className="font-mono font-semibold text-[#c1d1e6]">{localTime(selectedAt)}</span></div></div><div className="flex items-center gap-1 text-[8px] text-[#7488a6]"><EyeOff size={11} />Somente candles fechados no instante</div></div>
            <div className={styles.shortcuts}>{shortcutTimes.map(([label, time]) => <button className={styles.shortcut} key={`${label}-${time}`} onClick={() => { setSelectedAt(time); setHideFuture(true);  }}>{label}</button>)}</div>
            </>}
          </section>
        </div>

        <div className={styles.bottomGrid}>
          <section className={styles.panel}><div className={styles.panelHeader}><div><strong className="text-[12px]">Comparativo dos indicadores</strong><div className="text-[9px] text-[#7f92ae]">{selectionMode === "all" ? `Todos (${selectionSummary?.event_count ?? 0} pumps) — antes/início × aprovação/entrada` : "Este pump — antes/início × todas as aprovações/entradas shadow"}</div></div><div className="flex items-center gap-2"><div className={styles.segmented}>{["TODAS", "POOL", "L1", "L2", "L3"].map((item) => <button className={`${styles.segment} ${layer === item ? styles.segmentActive : ""}`} onClick={() => setLayer(item)} key={item}>{item}</button>)}</div><button className={styles.ghost} disabled={!rows.length} onClick={() => downloadJson(`pump-radar-comparativo-${selectionMode}-${Date.now()}.json`, { selection_mode: selectionMode, event_ids: selectionEventIds, layer, generated_at: new Date().toISOString(), indicators: rows })} title="Exportar este comparativo em JSON"><Download size={12} /></button></div></div><div className={styles.tableWrap}>{rows.length ? <table className={styles.table}><thead><tr><th>Camada / indicador</th><th>TF</th><th>Antes</th><th>Início</th><th>Aprovação</th><th>Entrada</th><th>Variação (méd.)</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.layer}-${row.timeframe}-${row.indicator_id}`} style={{ "--layer-color": LAYER_COLORS[row.layer] ?? "#6e7f98" } as React.CSSProperties}><td className={styles.layer}><span className="mr-2 font-semibold" style={{ color: LAYER_COLORS[row.layer] }}>{row.layer}</span>{row.indicator_id}</td><td className={styles.mono}>{row.timeframe}</td><td className={styles.mono}>{anchorText(row.anchors.before)}</td><td className={styles.mono}>{anchorText(row.anchors.start)}</td><td className={styles.mono}>{anchorText(row.anchors.approval)}</td><td className={styles.mono}>{anchorText(row.anchors.entry)}</td><td className={`${styles.mono} ${row.change != null ? "text-[#55d9a9]" : ""}`}>{row.change == null ? "—" : valueText(row.change)}</td></tr>)}</tbody></table> : <div className={styles.empty}>Não há snapshots point-in-time suficientes para comparar esta seleção.</div>}</div><div className="px-3 py-2 text-[8px] text-[#6f839f]">RECONSTRUÍDO usa candles fechados e a configuração de indicadores congelada nesta execução; não comprova avaliação histórica. Faixas agregam min–max entre as amostras (n) da seleção atual, não um único trade.</div></section>
          <section className={styles.panel}><div className={styles.panelHeader}><div><strong className="text-[12px]">TOP indicadores no início</strong><div className="text-[9px] text-[#7f92ae]">Validado · run inteiro × controles pareados (70/30, Wilson CI)</div></div><div className="flex items-center gap-2"><div className={styles.segmented}>{[["1h", "1h"], ["15min", "15m"], ["5min", "5m"], ["Combinado", "combined"]].map(([label, key]) => <button className={`${styles.segment} ${rangeTf === key ? styles.segmentActive : ""}`} onClick={() => setRangeTf(key)} key={key}>{label}</button>)}</div><button className={styles.ghost} disabled={!ranges.length} onClick={() => downloadJson(`pump-radar-top-indicadores-${run?.id}-${rangeTf}.json`, { run_id: run?.id, timeframe: rangeTf, generated_at: new Date().toISOString(), validated: true, ranges })} title="Exportar esta tabela em JSON"><Download size={12} /></button></div></div><div className={styles.ranges}>{ranges.length ? <><div className={`${styles.rangeRow} text-[#7e91ad]`}><span>Indicador / TF</span><span>Faixa recorrente</span><span>Pump</span><span>Controle</span></div>{ranges.map((row) => { const pump = row.pump_denominator ? row.pump_numerator / row.pump_denominator * 100 : 0; const control = row.control_denominator ? row.control_numerator / row.control_denominator * 100 : 0; return <div className={styles.rangeRow} key={row.id} title={`Cobertura ${row.coverage ?? "indisponível"}; IC ${JSON.stringify(row.confidence_interval)}`}><span>{row.indicator_id} · <span className={styles.mono}>{row.timeframe}</span></span><span className={styles.mono}>{valueText(row.lower_bound)}–{valueText(row.upper_bound)}</span><span><b className="text-[#55d9a9]">{pump.toFixed(0)}%</b><span className="ml-1 text-[#627896]">{row.pump_numerator}/{row.pump_denominator}</span><span className={styles.bar}><span style={{ width: `${Math.min(100, pump)}%` }} /></span></span><span><b>{control.toFixed(0)}%</b><span className="ml-1 text-[#627896]">{row.control_numerator}/{row.control_denominator}</span><span className={styles.bar}><span style={{ width: `${Math.min(100, control)}%`, background: "#8fa3c2" }} /></span></span></div>; })}</> : <div className={styles.empty}>Faixas não publicadas: amostra e controles ainda insuficientes.</div>}<div className="mt-2 flex items-center justify-between gap-2 text-[8px] text-[#7387a4]"><span>Faixas hipotéticas · validar no período posterior</span><button className={styles.ghost} disabled={!ranges.length}><ListFilter size={12} />Comparar eventos</button></div></div></section>
        </div>

        <section className={`${styles.panel} mt-[10px]`}>
          <div className={styles.panelHeader}><div><strong className="text-[12px]">Indicadores da seleção</strong><div className="text-[9px] text-[#7f92ae]">Descritivo · não validado estatisticamente — apenas os {selectionSummary?.event_count ?? 0} pump(s) da seleção atual ({selectionMode === "all" ? "Todos filtrados" : event?.symbol.replace("_", "/") ?? "—"})</div></div><button className={styles.ghost} disabled={!rows.length} onClick={() => downloadJson(`pump-radar-indicadores-selecao-${selectionMode}-${Date.now()}.json`, { selection_mode: selectionMode, event_ids: selectionEventIds, layer, validated: false, generated_at: new Date().toISOString(), indicators: rows })} title="Exportar em JSON"><Download size={12} /></button></div>
          <div className={styles.ranges}>
            {rows.length ? <><div className={`${styles.rangeRow} text-[#7e91ad]`}><span>Indicador / TF</span><span>Faixa observada (início)</span><span>Amostras</span><span /></div>
              {rows.map((row) => { const anchor = row.anchors.start; const coverage = selectionSummary?.event_count ? anchor.count / selectionSummary.event_count * 100 : 0; return <div className={styles.rangeRow} key={`sel-${row.layer}-${row.timeframe}-${row.indicator_id}`}><span>{row.indicator_id} · <span className={styles.mono}>{row.timeframe}</span></span><span className={styles.mono}>{anchor.count ? (anchor.numeric_coverage ? `${valueText(anchor.min)}–${valueText(anchor.max)}` : Array.from(new Set(anchor.samples.map((s) => String(s.value)))).slice(0, 3).join(", ")) : "—"}</span><span><span className="ml-1 text-[#627896]">{anchor.count}/{selectionSummary?.event_count ?? 0}</span><span className={styles.bar}><span style={{ width: `${Math.min(100, coverage)}%` }} /></span></span><span /></div>; })}
            </> : <div className={styles.empty}>Selecione um pump ou &quot;Todos&quot; para ver os indicadores em comum.</div>}
          </div>
        </section>

        <section className={`${styles.panel} mt-[10px]`}>
          <div className={styles.panelHeader}>
            <div><strong className="text-[12px]">Amostra materializada</strong><div className="text-[9px] text-[#7f92ae]">Materializa a seleção atual em um run imutável — downloads e análise usam exatamente os mesmos eventos</div></div>
            <button className={styles.primary} disabled={!selectionEventIds.length || materializing} onClick={() => void materializeSelection()}><ShieldAlert size={14} />{materializing ? "Materializando…" : "Materializar seleção"}</button>
          </div>
          {reportRun ? <div className="flex flex-wrap items-center justify-between gap-3 p-3">
            <div><div className="font-mono text-[10px] text-[#8fa2be]">run {reportRun.id} · hash {reportRun.selection_hash.slice(0, 14)}…</div><div className="mt-1 text-[10px] text-[#8fa2be]">{reportRun.total_events} evento(s) materializado(s)</div></div>
            <div className="flex flex-wrap items-center gap-2">
              <button className={styles.control} onClick={() => void exportReportRun()}><Download size={14} />JSON consolidado</button>
              <ModuleAIAnalysisAction originModule="pump_radar" originView="pump-radar" entityIds={[]} reportRunId={reportRun.id} label="Analisar seleção" />
            </div>
          </div> : <div className="px-3 py-3 text-[10px] text-[#7387a4]">Nenhuma amostra materializada ainda para esta seleção.</div>}
        </section>
      </>}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CalendarDays, ChevronLeft, ChevronRight, Download, EyeOff, FileSearch,
  ListFilter, Pause, Play, RefreshCw, Search, Settings2, ShieldAlert,
} from "lucide-react";

import { ApiError, apiGet, apiPost, apiPut } from "@/lib/api";
import { PumpRadarChart, type RadarCandle, type RadarMarker } from "@/components/pump-radar/PumpRadarChart";
import styles from "./pump-radar.module.css";

type Envelope<T> = { schema: string; timezone: string; generated_at: string; provenance: Record<string, unknown>; data: T };
type Capabilities = { market: string; capture_enabled: boolean; analysis_enabled: boolean; ui_enabled: boolean; timeframes: string[]; exports: string[]; profile_mutation: false };
type Run = { id: string; status: string; mode: string; date_from: string | null; date_to: string | null; config_hash: string; total_assets: number; processed_assets: number; failed_assets: number; event_count: number; quality_badge: string; requested_at: string; summary?: { pumps_identified: number; with_shadow_entry: number; without_entry: number; shadow_links: number; median_delay_seconds: number | null } };
type AssetEvent = { event_id: string; symbol: string; rise_pct: number; start_at: string; confirmed_market_at: string; detected_at: string | null; peak_at: string; end_at: string | null; reconstruction_status: string; quality_status: string; shadow_links: number; shadow_entries: number };
type EventLink = { id: string; shadow_trade_id: string | null; decision_id: number | null; link_kind: string; is_primary: boolean; approval_at: string | null; simulation_created_at: string | null; entry_at: string | null; exit_at: string | null; delay_seconds: number | null; realized_pnl_pct: number | null; profile_id: string | null; profile_version_id: string | null; profile_config_hash: string | null; provenance: Record<string, unknown> };
type EventDetail = AssetEvent & { id: string; start_price: number; peak_price: number; end_price: number | null; retracement_pct: number | null; is_incomplete: boolean; provenance: Record<string, unknown>; links: EventLink[] };
type ChartData = { event_id: string; symbol: string; selected_at: string; hide_future: boolean; candles: Record<"1h" | "15m" | "5m", RadarCandle[]>; markers: RadarMarker[] };
type Comparison = { event_id: string; symbol: string; snapshot_at: string; indicator_id: string; layer: string; timeframe: string; state: string; value: number | string | null; rule: unknown; source: string; version: string | null; numerator: number | null; denominator: number | null; coverage: number | null; provenance: unknown };
type RangeRow = { id: string; indicator_id: string; layer: string; timeframe: string; range_key: string; lower_bound: number | null; upper_bound: number | null; pump_numerator: number; pump_denominator: number; control_numerator: number; control_denominator: number; coverage: number | null; validation_status: string; confidence_interval: unknown };
type RadarConfig = { minimum_rise_pct: number; maximum_window_minutes: number; retracement_pct: number; no_new_high_minutes: number; maximum_duration_minutes: number; merge_gap_minutes: number; backfill_days: number; universe_max_assets: number; volume_filter_enabled: boolean; liquidity_filter_enabled: boolean; atr_filter_enabled: boolean; [key: string]: unknown };

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

export default function PumpRadarPage() {
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<AssetEvent[]>([]);
  const [event, setEvent] = useState<EventDetail | null>(null);
  const [chart, setChart] = useState<ChartData | null>(null);
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [ranges, setRanges] = useState<RangeRow[]>([]);
  const [config, setConfig] = useState<RadarConfig | null>(null);
  const [search, setSearch] = useState("");
  const [contextTf, setContextTf] = useState("5min");
  const [rangeTf, setRangeTf] = useState("combined");
  const [layer, setLayer] = useState("TODAS");
  const [hideFuture, setHideFuture] = useState(true);
  const [syncViews, setSyncViews] = useState(true);
  const [selectedAt, setSelectedAt] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileRankOpen, setMobileRankOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadEvent = useCallback(async (runId: string, eventId: string, point?: string | null, conceal = true) => {
    const detail = await apiGet<Envelope<EventDetail>>(`/pump-radar/runs/${runId}/events/${eventId}`);
    const at = point ?? detail.data.start_at;
    setEvent(detail.data);
    setSelectedAt(at);
    const [chartResponse, comparisonResponse, rangeResponse] = await Promise.all([
      apiGet<Envelope<ChartData>>(`/pump-radar/runs/${runId}/events/${eventId}/chart?hide_future=${conceal}${conceal ? `&selected_at=${encodeURIComponent(at)}` : ""}`),
      apiGet<Envelope<Comparison[]>>(`/pump-radar/runs/${runId}/comparisons?event_id=${eventId}`),
      apiGet<Envelope<RangeRow[]>>(`/pump-radar/runs/${runId}/ranges?timeframe=${rangeTf}`),
    ]);
    setChart(chartResponse.data); setComparisons(comparisonResponse.data); setRanges(rangeResponse.data);
  }, [rangeTf]);

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
      const [runResponse, assetResponse] = await Promise.all([
        apiGet<Envelope<Run>>(`/pump-radar/runs/${latest.id}`),
        apiGet<Envelope<{ items: AssetEvent[] }>>(`/pump-radar/runs/${latest.id}/assets?limit=100`),
      ]);
      setRun(runResponse.data); setEvents(assetResponse.data.items);
      const first = assetResponse.data.items[0];
      if (first) await loadEvent(latest.id, first.event_id, null, true);
      else { setEvent(null); setChart(null); setComparisons([]); setRanges([]); }
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

  const refetchChart = useCallback(async (at: string | null, conceal: boolean) => {
    if (!run || !event) return;
    const response = await apiGet<Envelope<ChartData>>(`/pump-radar/runs/${run.id}/events/${event.id}/chart?hide_future=${conceal}${conceal && at ? `&selected_at=${encodeURIComponent(at)}` : ""}`);
    setChart(response.data);
  }, [event, run]);

  useEffect(() => { if (event) void refetchChart(selectedAt, hideFuture); }, [event, hideFuture, refetchChart, selectedAt]);

  useEffect(() => {
    if (!playing || !event || !selectedAt) return;
    const end = +new Date(event.end_at ?? event.peak_at);
    const timer = window.setInterval(() => {
      setSelectedAt((current) => {
        if (!current) return event.start_at;
        const next = Math.min(+new Date(current) + 5 * 60_000, end);
        if (next >= end) setPlaying(false);
        const iso = new Date(next).toISOString();
        void refetchChart(iso, true);
        return iso;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [event, playing, refetchChart, selectedAt]);

  async function selectEvent(item: AssetEvent) {
    if (!run) return;
    setBusy(true); setPlaying(false);
    try { await loadEvent(run.id, item.event_id, null, true); } finally { setBusy(false); }
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
  const summary = run?.summary ?? EMPTY_SUMMARY;
  const primaryLink = event?.links.find((link) => link.is_primary) ?? event?.links[0] ?? null;
  const timelineStart = event ? +new Date(event.start_at) : 0;
  const timelineEnd = event ? +new Date(event.end_at ?? event.peak_at) : 0;
  const selectedOffset = event && selectedAt ? Math.max(0, Math.round((+new Date(selectedAt) - timelineStart) / 60_000)) : 0;
  const maxOffset = event ? Math.max(5, Math.round((timelineEnd - timelineStart) / 60_000)) : 5;
  const rows = useMemo(() => {
    const filtered = layer === "TODAS" ? comparisons : comparisons.filter((row) => row.layer === layer);
    const byKey = new Map<string, Comparison[]>();
    for (const row of filtered) { const key = `${row.layer}|${row.timeframe}|${row.indicator_id}`; byKey.set(key, [...(byKey.get(key) ?? []), row]); }
    const anchorValue = (items: Comparison[], anchor: string | null | undefined) => {
      if (!anchor) return null;
      return [...items].filter((item) => +new Date(item.snapshot_at) <= +new Date(anchor)).sort((a, b) => +new Date(b.snapshot_at) - +new Date(a.snapshot_at))[0]?.value ?? null;
    };
    return [...byKey.entries()].map(([key, items]) => {
      const [rowLayer, timeframe, indicator] = key.split("|");
      const before = event ? anchorValue(items, new Date(+new Date(event.start_at) - 5 * 60_000).toISOString()) : null;
      const start = anchorValue(items, event?.start_at);
      const approval = anchorValue(items, primaryLink?.approval_at);
      const entry = anchorValue(items, primaryLink?.entry_at);
      const change = typeof before === "number" && typeof approval === "number" ? approval - before : null;
      return { layer: rowLayer, timeframe, indicator, before, start, approval, entry, change, sample: items.at(-1)! };
    });
  }, [comparisons, event, layer, primaryLink]);

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
          {([ ["minimum_rise_pct", "Alta mínima (%)"], ["maximum_window_minutes", "Janela máxima (min)"], ["retracement_pct", "Retração de término (%)"], ["no_new_high_minutes", "Sem nova máxima (min)"], ["maximum_duration_minutes", "Duração máxima (min)"], ["merge_gap_minutes", "Fusão entre eventos (min)"], ["backfill_days", "Backfill (dias)"], ["universe_max_assets", "Máximo de ativos"] ] as const).map(([key, label]) => <div className={styles.field} key={key}><label>{label}</label><input type="number" value={config[key] as number} onChange={(e) => setConfig({ ...config, [key]: Number(e.target.value) })} /></div>)}
          {([ ["volume_filter_enabled", "Filtro de volume"], ["liquidity_filter_enabled", "Filtro de liquidez"], ["atr_filter_enabled", "Filtro de ATR"] ] as const).map(([key, label]) => <label className="flex h-8 items-center gap-2 self-end rounded-md border border-[#273c59] bg-[#0a1524] px-3 text-[9px] text-[#8da0bc]" key={key}><input type="checkbox" checked={Boolean(config[key])} onChange={(e) => setConfig({ ...config, [key]: e.target.checked })} />{label}</label>)}
        </div>
      </section>}

      <div className={styles.metrics}>
        {[ ["Pumps identificados", summary.pumps_identified, "#51d9a9"], ["Com entrada shadow", summary.with_shadow_entry, "#aa76ed"], ["Sem entrada", summary.without_entry, "#9aa8bd"], ["Atraso mediano", summary.median_delay_seconds == null ? "—" : delay(summary.median_delay_seconds), "#f4bd3f"] ].map(([label, value, color]) => <div className={styles.metric} style={{ "--metric-color": color } as React.CSSProperties} key={label as string}><div className={styles.metricLabel}>{label}</div><div className={styles.metricValue}>{value}</div></div>)}
      </div>

      {!run && <div className={styles.notice}><div className="flex flex-wrap items-center justify-between gap-3"><div><div className="font-semibold text-[#c9d7e9]">Nenhuma execução disponível</div><div className="mt-1 text-[10px]">A tela não usa dados de demonstração. Inicie uma captura quando a flag operacional estiver habilitada.</div></div><div className="flex gap-2"><button className={styles.control} disabled={!capabilities?.capture_enabled || busy} onClick={() => createRun("incremental")}>Executar hoje</button><button className={styles.primary} disabled={!capabilities?.capture_enabled || busy} onClick={() => createRun("backfill")}>Backfill configurado</button></div></div></div>}

      {run && <>
        <div className={`${styles.mainGrid} ${collapsed ? styles.mainGridCollapsed : ""}`}>
          {collapsed ? <aside className={`${styles.panel} ${styles.collapsedRail}`}><button className={styles.ghost} onClick={() => setCollapsed(false)} title="Abrir ranking"><ChevronRight size={15} /></button></aside> : <aside className={`${styles.panel} ${styles.rankPanel} ${mobileRankOpen ? styles.rankOpen : ""}`}>
            <div className={styles.panelHeader}><strong className="text-[12px]">TOP pumps do dia</strong><div className={styles.segmented}>{["5min", "15min", "1h"].map((item) => <button key={item} onClick={() => setContextTf(item)} className={`${styles.segment} ${contextTf === item ? styles.segmentActive : ""}`}>{item}</button>)}</div><button onClick={() => { setCollapsed(true); setMobileRankOpen(false); }} className="text-[#7589a6]" title="Recolher ranking"><ChevronLeft size={14} /></button></div>
            <label className={styles.search}><Search size={13} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Buscar ativo…" /></label>
            <div className={styles.rankHead}><span>#</span><span>Ativo</span><span>Pump</span><span>Shadow</span></div>
            <div className="min-h-0 flex-1 overflow-y-auto px-1">
              {filteredEvents.map((item, index) => <button key={item.event_id} onClick={() => { void selectEvent(item); setMobileRankOpen(false); }} className={`${styles.rankRow} ${event?.id === item.event_id ? styles.rankSelected : ""} w-full text-left`}><span>{index + 1}</span><span className="truncate">{item.symbol.replace("_", "/")}</span><span className={styles.positive}>{percent(item.rise_pct)}</span><span>{item.shadow_entries ? `${item.shadow_entries} entrada${item.shadow_entries > 1 ? "s" : ""}` : item.shadow_links ? `${item.shadow_links} vínculo${item.shadow_links > 1 ? "s" : ""}` : "Sem entrada"}</span></button>)}
              {!filteredEvents.length && <div className="p-5 text-center text-[10px] text-[#71839e]">Nenhum evento real corresponde ao filtro.</div>}
            </div>
            <div className={styles.profile}><div className="mb-2 flex items-center gap-2 text-[11px] font-semibold"><FileSearch size={14} />Profile selecionado</div><div className={styles.profileCard}><div className="text-[10px] font-semibold text-[#d4dfed]">{primaryLink?.profile_id ? compactId(primaryLink.profile_id) : "Sem profile histórico vinculado"}</div><div className="mt-1 text-[9px] text-[#8295b0]">Trade {compactId(primaryLink?.shadow_trade_id)} • versão {compactId(primaryLink?.profile_version_id)}</div><div className="mt-1 break-all text-[8px] text-[#657a98]">hash {compactId(primaryLink?.profile_config_hash)}</div><button className={`${styles.ghost} mt-2 w-full justify-between`} disabled={!event?.links.length}>Ver {event?.links.length ?? 0} aprovação(ões) <ChevronRight size={13} /></button></div>
              <div className={styles.facts}>{[["Início", localTime(event?.start_at)], ["Aprovação", localTime(primaryLink?.approval_at)], ["Entrada", localTime(primaryLink?.entry_at)], ["Atraso", delay(primaryLink?.delay_seconds)], ["Mov. até pico", percent(event?.rise_pct)]].map(([label, value]) => <div key={label}><div className={styles.factLabel}>{label}</div><div className={`${styles.factValue} ${label === "Mov. até pico" ? "text-[#55d9a9]" : ""}`}>{value}</div></div>)}</div></div>
          </aside>}

          <section className={`${styles.panel} ${styles.charts}`}>
            <div className={styles.chartHeader}><div><div className="text-[16px] font-bold">{event?.symbol.replace("_", "/") ?? "Selecione um evento"}</div><div className="mt-0.5 text-[9px] text-[#7487a4]">{event ? `Evento ${compactId(event.id)} • ${localTime(event.start_at)}–${localTime(event.end_at)}` : "Sem dados disponíveis"}</div></div><div className="flex flex-wrap items-center gap-3"><div className={styles.segmented}>{["1h", "15min", "5min"].map((item) => <span key={item} className={`${styles.segment} ${item === "5min" ? styles.segmentActive : ""} grid place-items-center`}>{item}</span>)}</div><label className="flex items-center gap-2 text-[9px] text-[#8799b5]"><input type="checkbox" checked={syncViews} onChange={(e) => setSyncViews(e.target.checked)} />Sincronizar visões</label><label className="flex items-center gap-2 text-[9px] text-[#8799b5]"><input type="checkbox" checked={hideFuture} onChange={(e) => setHideFuture(e.target.checked)} /><EyeOff size={12} />Ocultar futuro</label></div></div>
            <div className={styles.miniGrid}><PumpRadarChart candles={chart?.candles["1h"] ?? []} title="1h · Contexto" subtitle="Candles fechados e médias móveis" compact showMarkers={false} /><PumpRadarChart candles={chart?.candles["15m"] ?? []} title="15min · Formação" subtitle="Janela point-in-time sincronizada" compact showMarkers={false} /></div>
            <div className={styles.mainChart}><PumpRadarChart candles={chart?.candles["5m"] ?? []} markers={markers} title="5min · Pump e aprovações shadow" subtitle={event?.reconstruction_status === "RECONSTRUCTED" ? "RECONSTRUÍDO · disponibilidade histórica não comprovada" : "Gate Spot · somente candles fechados disponíveis no instante"} showVolume highlightStart={event?.start_at} highlightEnd={event?.end_at} /></div>
            <div className={styles.timeline}><button className={styles.play} onClick={() => { setHideFuture(true); setPlaying((value) => !value); }} disabled={!event}>{playing ? <Pause size={16} /> : <Play size={16} />}</button><div><input className={styles.range} type="range" min={0} max={maxOffset} step={5} value={selectedOffset} disabled={!event} onChange={(e) => { const iso = new Date(timelineStart + Number(e.target.value) * 60_000).toISOString(); setSelectedAt(iso); setHideFuture(true); void refetchChart(iso, true); }} /><div className="text-[9px] text-[#7990af]">Instante selecionado: <span className="font-mono font-semibold text-[#c1d1e6]">{localTime(selectedAt)}</span></div></div><div className="flex items-center gap-1 text-[8px] text-[#7488a6]"><EyeOff size={11} />Somente dados disponíveis no instante</div></div>
            <div className={styles.shortcuts}>{shortcutTimes.map(([label, time]) => <button className={styles.shortcut} key={`${label}-${time}`} onClick={() => { setSelectedAt(time); setHideFuture(true); void refetchChart(time, true); }}>{label}</button>)}</div>
          </section>
        </div>

        <div className={styles.bottomGrid}>
          <section className={styles.panel}><div className={styles.panelHeader}><div><strong className="text-[12px]">Comparativo dos indicadores</strong><div className="text-[9px] text-[#7f92ae]">Início do pump × aprovação/profile selecionado</div></div><div className={styles.segmented}>{["TODAS", "POOL", "L1", "L2", "L3"].map((item) => <button className={`${styles.segment} ${layer === item ? styles.segmentActive : ""}`} onClick={() => setLayer(item)} key={item}>{item}</button>)}</div></div><div className={styles.tableWrap}>{rows.length ? <table className={styles.table}><thead><tr><th>Camada / indicador</th><th>TF</th><th>Antes</th><th>Início</th><th>Aprovação</th><th>Entrada</th><th>Variação</th><th>Regra</th><th>Proveniência</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.layer}-${row.timeframe}-${row.indicator}`} style={{ "--layer-color": LAYER_COLORS[row.layer] ?? "#6e7f98" } as React.CSSProperties}><td className={styles.layer}><span className="mr-2 font-semibold" style={{ color: LAYER_COLORS[row.layer] }}>{row.layer}</span>{row.indicator}</td><td className={styles.mono}>{row.timeframe}</td><td className={styles.mono}>{valueText(row.before)}</td><td className={styles.mono}>{valueText(row.start)}</td><td className={styles.mono}>{valueText(row.approval)}</td><td className={styles.mono}>{valueText(row.entry)}</td><td className={`${styles.mono} ${row.change != null ? "text-[#55d9a9]" : ""}`}>{row.change == null ? "—" : valueText(row.change)}</td><td>{row.sample.rule ? "Configurada" : "—"}</td><td title={JSON.stringify(row.sample.provenance)}>{row.sample.source} · {row.sample.version ?? "versão ausente"}</td></tr>)}</tbody></table> : <div className={styles.empty}>Não há snapshots point-in-time suficientes para comparar este evento.</div>}</div><div className="px-3 py-2 text-[8px] text-[#6f839f]">Estados ausentes permanecem UNAVAILABLE; nenhum profile atual substitui versão histórica.</div></section>
          <section className={styles.panel}><div className={styles.panelHeader}><div><strong className="text-[12px]">TOP indicadores no início</strong><div className="text-[9px] text-[#7f92ae]">Pumps × controles pareados</div></div></div><div className="px-3 pt-2"><div className={styles.segmented}>{[["1h", "1h"], ["15min", "15m"], ["5min", "5m"], ["Combinado", "combined"]].map(([label, key]) => <button className={`${styles.segment} ${rangeTf === key ? styles.segmentActive : ""}`} onClick={async () => { setRangeTf(key); if (run) setRanges((await apiGet<Envelope<RangeRow[]>>(`/pump-radar/runs/${run.id}/ranges?timeframe=${key}`)).data); }} key={key}>{label}</button>)}</div></div><div className={styles.ranges}>{ranges.length ? <><div className={`${styles.rangeRow} text-[#7e91ad]`}><span>Indicador / TF</span><span>Faixa recorrente</span><span>Pump</span><span>Controle</span></div>{ranges.map((row) => { const pump = row.pump_denominator ? row.pump_numerator / row.pump_denominator * 100 : 0; const control = row.control_denominator ? row.control_numerator / row.control_denominator * 100 : 0; return <div className={styles.rangeRow} key={row.id} title={`Cobertura ${row.coverage ?? "indisponível"}; IC ${JSON.stringify(row.confidence_interval)}`}><span>{row.indicator_id} · <span className={styles.mono}>{row.timeframe}</span></span><span className={styles.mono}>{valueText(row.lower_bound)}–{valueText(row.upper_bound)}</span><span><b className="text-[#55d9a9]">{pump.toFixed(0)}%</b><span className="ml-1 text-[#627896]">{row.pump_numerator}/{row.pump_denominator}</span><span className={styles.bar}><span style={{ width: `${Math.min(100, pump)}%` }} /></span></span><span><b>{control.toFixed(0)}%</b><span className="ml-1 text-[#627896]">{row.control_numerator}/{row.control_denominator}</span><span className={styles.bar}><span style={{ width: `${Math.min(100, control)}%`, background: "#8fa3c2" }} /></span></span></div>; })}</> : <div className={styles.empty}>Faixas não publicadas: amostra e controles ainda insuficientes.</div>}<div className="mt-2 flex items-center justify-between gap-2 text-[8px] text-[#7387a4]"><span>Faixas hipotéticas · validar no período posterior</span><button className={styles.ghost} disabled={!ranges.length}><ListFilter size={12} />Comparar eventos</button></div></div></section>
        </div>
      </>}
    </div>
  );
}

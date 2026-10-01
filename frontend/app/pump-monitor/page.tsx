"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Columns3, RefreshCw, ShieldAlert } from "lucide-react";

import { apiGet, apiPut } from "@/lib/api";
import styles from "./pump-monitor.module.css";
import PumpOpportunityPanel from "@/components/pump-monitor/PumpOpportunityPanel";

type Cell = {
  value: number | string | boolean | null;
  reason?: string | null;
  source?: string | null;
  status?: string | null;
  color_state?: "green" | "amber" | "red" | "gray" | null;
  coverage_pct?: number | null;
  age_seconds?: number | null;
};
type Component = { value: unknown; normalized?: number; weight: number; contribution: number | null; reason?: string | null };
type Row = {
  symbol: string;
  indicators: Record<string, Cell>;
  pump_monitor_score: number | null;
  score_components: Record<string, Component>;
  score_version: string;
  score_confidence: number;
  alerts_active: { type: string }[];
  only_rising: boolean;
  data_age_seconds: number | null;
  collection_error?: string;
};
type Spec = { group?: string; polarity?: string };
type Response = {
  notice: string;
  generated_at: string | null;
  data_age_seconds?: number | null;
  cycle_seconds: number;
  config_version: number;
  config_hash: string;
  score_status: string;
  pool_id: string | null;
  total_assets: number;
  rows: Row[];
  reason?: string;
  display?: { top_n: number; top_n_options: number[]; stale_after_cycles: number };
  indicator_specs?: Record<string, Spec>;
};

const GROUPS: { key: string; label: string }[] = [
  { key: "flow", label: "Fluxo" },
  { key: "liquidity", label: "Liquidez" },
  { key: "price", label: "Preço/Estrutura" },
  { key: "momentum", label: "Momentum" },
  { key: "trend", label: "Tendência" },
  { key: "scores", label: "Scores" },
  { key: "alerts", label: "Alertas" },
];

const LABELS: Record<string, string> = {
  pump_monitor_score: "Pump Score", delta_norm: "Delta norm", window_delta_norm: "Delta norm (janela)",
  cvd_60m: "CVD 60m", cvd_slope: "CVD slope", buy_persistence: "Persist. compra",
  volume_acceleration: "Acel. volume", flow_change: "Flow change", rvol_strict: "RVOL",
  volume_spike: "Volume spike", taker_ratio: "Taker ratio", volume_delta: "Volume delta", obv: "OBV",
  spread_pct: "Spread %", orderbook_depth_usdt: "Book top-10 USDT", bid_ask_imbalance: "Imbalance",
  bid_depth_usdt_0_5pct: "Bid ±0,5%", ask_depth_usdt_0_5pct: "Ask ±0,5%",
  bid_depth_usdt_1pct: "Bid ±1%", ask_depth_usdt_1pct: "Ask ±1%",
  bid_depth_usdt_2pct: "Bid ±2%", ask_depth_usdt_2pct: "Ask ±2%",
  depth_imbalance_1pct: "Depth imb. 1%", estimated_slippage_buy_pct: "Slippage compra %",
  estimated_slippage_sell_pct: "Slippage venda %", volume_24h_usdt: "Volume 24h",
  price: "Preço", price_change_1m_pct: "Var 1m %", price_change_5m_pct: "Var 5m %",
  price_change_15m_pct: "Var 15m %", price_progress_atr: "Progresso ATR", price_extension_atr: "Extensão ATR",
  breakout_distance_atr: "Rompimento ATR", breakout_hold_ratio: "Sustentação", upper_wick_ratio: "Pavio sup.",
  vwap_distance_pct: "VWAP dist %", bb_upper_distance_pct: "BB sup. %", recent_high_1h_distance_pct: "Máx 1h %",
  rsi: "RSI", macd_histogram: "MACD hist", stoch_k: "Stoch %K", adx: "ADX", di_trend: "DI+ > DI-",
  psar_trend: "PSAR", ema_full_alignment: "EMA align", atr_pct: "ATR %", bb_width: "BB width",
  score: "Alpha", liquidity_score: "Liquidity", momentum_score: "Momentum",
};

const COMPACT = [
  "pump_monitor_score", "delta_norm", "buy_persistence", "cvd_slope", "rvol_strict",
  "price_progress_atr", "price_change_5m_pct", "spread_pct", "estimated_slippage_buy_pct", "bid_depth_usdt_1pct",
];
const ALERT_LABELS: Record<string, string> = {
  effort_no_progress: "Esforço sem avanço", breakout_failure: "Perda do rompimento", sell_absorption: "Absorção vendedora",
};
const PRESET_KEY = "pump-monitor:columns";

function fmt(value: Cell["value"]) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "sim" : "não";
  if (typeof value === "string") return value;
  const abs = Math.abs(value);
  const digits = abs >= 1000 ? 0 : abs >= 10 ? 2 : 4;
  return new Intl.NumberFormat("pt-BR", { maximumFractionDigits: digits }).format(value);
}

function readPreset(): string[] | null {
  try {
    const raw = window.localStorage.getItem(PRESET_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savePreset(columns: string[]) {
  try {
    window.localStorage.setItem(PRESET_KEY, JSON.stringify(columns));
  } catch {
    /* per-viewer convenience only */
  }
}

export default function PumpMonitorPage() {
  const [legacy,setLegacy]=useState(false);
  return <div className={styles.page}>
    <button type="button" className={styles.control} onClick={()=>setLegacy(v=>!v)}>
      {legacy?"Abrir radar de continuidade":"Abrir monitor clássico"}
    </button>
    {legacy?<LegacyPumpMonitorPage/>:<PumpOpportunityPanel/>}
  </div>;
}

function LegacyPumpMonitorPage() {
  const [data, setData] = useState<Response | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [sort, setSort] = useState("pump_monitor_score");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [onlyRising, setOnlyRising] = useState(true);
  const [topN, setTopN] = useState<number | null>(null);
  const [columns, setColumns] = useState<string[]>(COMPACT);
  const [showPicker, setShowPicker] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const preset = readPreset();
    if (preset?.length) setColumns(preset);
  }, []);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const params = new URLSearchParams({ sort, order, only_rising: String(onlyRising) });
      if (topN !== null) params.set("limit", String(topN));
      const result = await apiGet<Response>(`/pump-monitor/assets?${params}`);
      setData(result);
      if (topN === null && result.display) setTopN(result.display.top_n);
      setError("");
    } catch {
      setError("Não foi possível consultar o Pump Monitor.");
    } finally {
      setBusy(false);
    }
  }, [sort, order, onlyRising, topN]);

  const cycle = data?.cycle_seconds ?? 30;
  useEffect(() => {
    load();
    const id = window.setInterval(load, cycle * 1000);
    return () => window.clearInterval(id);
  }, [load, cycle]);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const specs = useMemo(() => data?.indicator_specs ?? {}, [data]);
  const grouped = useMemo(() => {
    const byGroup: Record<string, string[]> = {};
    for (const key of columns) {
      if (key === "alerts") continue;
      const group = specs[key]?.group ?? "other";
      (byGroup[group] ??= []).push(key);
    }
    return GROUPS.filter(g => g.key !== "alerts" && byGroup[g.key]?.length).map(g => ({ ...g, keys: byGroup[g.key] }));
  }, [columns, specs]);
  const showAlerts = columns.includes("alerts");

  const age = data?.generated_at ? Math.max(0, Math.round((now - new Date(data.generated_at).getTime()) / 1000)) : null;
  const staleAfter = (data?.display?.stale_after_cycles ?? 2) * cycle;
  const isStale = age !== null && age > staleAfter;

  const toggleSort = (key: string) => {
    if (sort === key) setOrder(o => (o === "desc" ? "asc" : "desc"));
    else { setSort(key); setOrder("desc"); }
  };

  const changeTopN = async (value: number) => {
    setTopN(value);
    try {
      await apiPut("/pump-monitor/config", { display: { top_n: value } });
    } catch {
      setError("TOP-N aplicado nesta tela, mas não foi salvo na configuração.");
    }
  };

  const toggleColumn = (key: string) => {
    const next = columns.includes(key) ? columns.filter(c => c !== key) : [...columns, key];
    setColumns(next);
    savePreset(next);
  };
  const toggleGroup = (group: string) => {
    const keys = group === "alerts" ? ["alerts"] : Object.keys(specs).filter(k => specs[k]?.group === group);
    const allOn = keys.every(k => columns.includes(k));
    const next = allOn ? columns.filter(c => !keys.includes(c)) : Array.from(new Set([...columns, ...keys]));
    setColumns(next);
    savePreset(next);
  };

  const cellTitle = (key: string, cell?: Cell) => {
    if (!cell) return "Não disponível nesta linha";
    const parts = [`${LABELS[key] ?? key}`, `fonte: ${cell.source ?? "—"}`];
    if (cell.value === null) parts.push(`indisponível: ${cell.reason ?? "sem dado"}`);
    if (cell.coverage_pct != null) parts.push(`cobertura: ${cell.coverage_pct}%`);
    if (cell.age_seconds != null) parts.push(`idade: ${cell.age_seconds}s`);
    return parts.join("\n");
  };
  const scoreTitle = (row: Row) => {
    const lines = [`${row.score_version} — ${data?.score_status ?? ""}`, `confiança: ${row.score_confidence}`];
    for (const [name, c] of Object.entries(row.score_components)) {
      lines.push(`${name}: ${c.value === null ? `— (${c.reason ?? "sem dado"})` : `${fmt(c.value as number)} → ${c.contribution}`}`);
    }
    return lines.join("\n");
  };

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <div>
          <div className={styles.title}>Pump Monitor</div>
          <div className={styles.subtitle}>
            Fluxo, liquidez executável e preço dos ativos do pool monitorado · score {data?.score_status === "HYPOTHESIS_NOT_VALIDATED" ? "v0 (hipótese, não calibrado)" : data?.score_status ?? "—"}
          </div>
        </div>
        <span className={styles.notice} data-testid="observation-badge">
          <ShieldAlert size={14} /> OBSERVAÇÃO — não é sinal de entrada
        </span>
      </div>

      <div className={styles.toolbar}>
        <div className={styles.segmented} role="group" aria-label="TOP-N">
          {(data?.display?.top_n_options ?? [10, 20, 50, 0]).map(n => (
            <button key={n} type="button" className={`${styles.segment} ${topN === n ? styles.active : ""}`}
              onClick={() => changeTopN(n)}>{n === 0 ? "Todos" : `TOP ${n}`}</button>
          ))}
        </div>
        <button type="button" className={`${styles.control} ${onlyRising ? styles.active : ""}`}
          onClick={() => setOnlyRising(v => !v)} aria-pressed={onlyRising}>
          Subida em andamento
        </button>
        <button type="button" className={styles.control} onClick={() => setShowPicker(v => !v)} aria-expanded={showPicker}>
          <Columns3 size={14} /> Colunas
        </button>
        <button type="button" className={styles.control} onClick={() => { setColumns(COMPACT); savePreset(COMPACT); }}>
          Preset compacto
        </button>
        <button type="button" className={styles.control} onClick={load} disabled={busy}>
          <RefreshCw size={14} /> Atualizar
        </button>
        <span className={styles.status} role="status">
          {age === null ? "sem ciclo ainda" : <>atualizado há {age}s{isStale && <span className={styles.stale}> · dado velho</span>}</>}
          {data && <span>{data.rows.length}/{data.total_assets} ativos · config v{data.config_version}</span>}
        </span>
      </div>

      <div className={styles.panel}>
        {showPicker && (
          <div className={styles.picker}>
            {GROUPS.map(g => (
              <button key={g.key} type="button" className={styles.control} onClick={() => toggleGroup(g.key)}>{g.label}</button>
            ))}
            {Object.keys(specs).map(k => (
              <button key={k} type="button" onClick={() => toggleColumn(k)}
                className={`${styles.control} ${columns.includes(k) ? styles.active : ""}`}>{LABELS[k] ?? k}</button>
            ))}
          </div>
        )}
        {error && <p role="alert" className={styles.error}>{error}</p>}
        {!data ? <div className={styles.empty}>Carregando…</div> : !data.rows.length ? (
          <div className={styles.empty}>
            {data.reason === "universe_pool_not_configured" ? "Pool monitorado não configurado."
              : data.reason === "no_cycle_yet" ? "Aguardando o primeiro ciclo do monitor."
              : onlyRising ? "Nenhum ativo em subida agora. Desligue o filtro para ver todos." : "Nenhum ativo."}
          </div>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr className={styles.groupRow}>
                  <th className={styles.symbol} />
                  {grouped.map(g => <th key={g.key} colSpan={g.keys.length}>{g.label}</th>)}
                  {showAlerts && <th>Alertas</th>}
                </tr>
                <tr className={styles.headRow}>
                  <th className={styles.symbol}>
                    <button type="button" onClick={() => toggleSort("symbol")}>Symbol{sort === "symbol" && (order === "desc" ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}</button>
                  </th>
                  {grouped.flatMap(g => g.keys).map(k => (
                    <th key={k} aria-sort={sort === k ? (order === "desc" ? "descending" : "ascending") : "none"}>
                      <button type="button" onClick={() => toggleSort(k)}>
                        {LABELS[k] ?? k}{sort === k && (order === "desc" ? <ArrowDown size={11} /> : <ArrowUp size={11} />)}
                      </button>
                    </th>
                  ))}
                  {showAlerts && <th>—</th>}
                </tr>
              </thead>
              <tbody>
                {data.rows.map(row => (
                  <tr key={row.symbol}>
                    <td className={styles.symbol} title={row.collection_error ? `falha na coleta: ${row.collection_error}` : undefined}>{row.symbol}</td>
                    {grouped.flatMap(g => g.keys).map(k => {
                      const cell = row.indicators[k];
                      const color = cell?.color_state ? styles[cell.color_state] : "";
                      const isScore = k === "pump_monitor_score";
                      return (
                        <td key={k} className={`${color} ${isScore ? styles.score : ""}`}
                          title={isScore ? scoreTitle(row) : cellTitle(k, cell)}>
                          {fmt(cell?.value ?? null)}
                        </td>
                      );
                    })}
                    {showAlerts && (
                      <td>{row.alerts_active.length ? row.alerts_active.map(a => (
                        <span key={a.type} className={styles.alert}>{ALERT_LABELS[a.type] ?? a.type}</span>
                      )) : "—"}</td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

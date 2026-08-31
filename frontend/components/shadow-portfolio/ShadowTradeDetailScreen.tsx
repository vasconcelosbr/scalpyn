"use client";

import Link from "next/link";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Activity, AlertTriangle, ArrowLeft, Clock, Crosshair, Database, Download } from "lucide-react";

import { ModuleAIAnalysisAction } from "@/components/ai/ModuleAIAnalysisAction";
import { ApiError, apiGet } from "@/lib/api";
import { buildShadowTradeExport, shadowTradeExportFilename } from "./shadowTradeExport";
import { TradeCandlestickChart } from "./TradeCandlestickChart";
import type { ShadowTradeChartResponse, ShadowTradeDetail } from "./types";
import styles from "./ShadowTradeDetailScreen.module.css";

const CHART_CONTEXT_MINUTES = 30;
const MACRO_KEYS = new Set([
  "sp500_change_1h", "nasdaq_change_1h", "russell2000_change_1h",
  "vix_value", "vix_change_1h", "dxy_value", "dxy_change_1h",
  "us10y_yield", "us10y_change_1h", "btc_dominance",
  "btc_dominance_change", "crypto_market_cap_change",
  "crypto_volume_change", "fear_greed_index", "macro_context_available",
]);

function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.toDescriptiveString();
  if (error instanceof Error) return error.message;
  return "Erro desconhecido";
}

function fmtDateTime(value: string | null | undefined, withSeconds = false): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: withSeconds ? "2-digit" : undefined,
    hour12: false,
  }).format(date);
}

function fmtPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (Math.abs(value) >= 1) return `$${value.toLocaleString("en-US", { maximumFractionDigits: 4 })}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 8 })}`;
}

function fmtPct(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function fmtUsd(value: number | null | undefined, signed = true): string {
  if (value === null || value === undefined) return "—";
  const prefix = signed ? (value >= 0 ? "+" : "-") : (value < 0 ? "-" : "");
  return `${prefix}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function fmtHolding(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return String(value);
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if ("value" in obj && typeof obj.value !== "object") return fmtValue(obj.value);
    return JSON.stringify(value);
  }
  return String(value);
}

function outcomeLabel(data: ShadowTradeDetail): string {
  if (data.outcome === "TP_HIT") return "TP";
  if (data.outcome === "SL_HIT") return "SL";
  if (data.outcome === "TRAILING_STOP") return "STOP MÓVEL";
  if (data.outcome === "TIMEOUT") return "TIMEOUT";
  return data.status;
}

function DetailRow({ label, value, className }: { label: string; value: ReactNode; className?: string }) {
  return (
    <div className={styles.detailRow}>
      <span className={styles.detailLabel}>{label}</span>
      <span className={`${styles.detailValue} ${className ?? ""}`}>{value}</span>
    </div>
  );
}

function SnapshotPanel({
  title,
  data,
  emptyMessage = "Sem dados.",
  filterMacro = false,
}: {
  title: string;
  data: Record<string, unknown> | null;
  emptyMessage?: string;
  filterMacro?: boolean;
}) {
  const captureFailed = data?.["_capture_failed"] === true;
  const entries = Object.entries(data ?? {})
    .filter(([key]) => !key.startsWith("_") && (!filterMacro || !MACRO_KEYS.has(key)))
    .sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className={styles.snapshot}>
      <div className={styles.snapshotTitle}>{title}</div>
      {entries.length === 0 || captureFailed ? (
        <div className={styles.empty}>{emptyMessage}</div>
      ) : (
        entries.map(([key, value]) => (
          <div className={styles.snapshotRow} key={key}>
            <span className={styles.snapshotKey}>{key}</span>
            <span className={styles.snapshotValue}>{fmtValue(value)}</span>
          </div>
        ))
      )}
    </div>
  );
}

function exitSnapshotMessage(data: ShadowTradeDetail): string {
  if (data.features_snapshot_exit?.["_capture_failed"] === true) {
    return "Snapshot indisponível no fechamento — indicadores estavam stale ou ausentes no provider.";
  }
  return "Snapshot de indicadores da saída não disponível para este trade.";
}

function DecisionAudit({ data }: { data: ShadowTradeDetail }) {
  const reasons = Object.entries(data.decision_reasons ?? {});
  const metrics = Object.entries(data.decision_metrics ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const pass = (value: boolean | null) => value === null ? "—" : value ? "PASS" : "FAIL";
  const passClass = (value: boolean | null) => value === true ? styles.positive : value === false ? styles.negative : "";
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}>
        <h2 className={styles.cardTitle}><Activity size={15} /> Auditoria da decisão (L1/L2/L3)</h2>
        <div className={styles.cardMeta}>
          <span>{data.decision_strategy ?? "Estratégia não informada"}</span>
          <span>score {data.decision_score === null ? "—" : data.decision_score.toFixed(1)}</span>
          <span>{fmtDateTime(data.decision_created_at, true)}</span>
        </div>
      </div>
      <div className={styles.section}>
        <div className={styles.auditGrid}>
          <div className={styles.subcard}>
            <div className={styles.subcardTitle}>Reasons</div>
            {reasons.length ? <div className={styles.reasonList}>{reasons.map(([key, value]) => {
              const ok = String(value).toUpperCase() === "OK";
              return <span className={`${styles.reason} ${ok ? styles.reasonOk : ""}`} key={key}>{key}: {String(value)}</span>;
            })}</div> : <div className={styles.empty}>—</div>}
          </div>
          <div className={styles.subcard}>
            <div className={styles.subcardTitle}>Metrics</div>
            {metrics.length ? metrics.map(([key, value]) => (
              <div className={styles.metricRow} key={key}>
                <span className={styles.metricName}>{key}</span>
                <span className={styles.metricValue}>{fmtValue(value)}</span>
              </div>
            )) : <div className={styles.empty}>—</div>}
          </div>
          <div className={styles.subcard}>
            <div className={styles.subcardTitle}>Timeline</div>
            {(["L1", "L2", "L3"] as const).map((layer) => {
              const value = layer === "L1" ? data.decision_l1_pass : layer === "L2" ? data.decision_l2_pass : data.decision_l3_pass;
              return <div className={styles.metricRow} key={layer}><span>{layer}</span><span className={`${styles.metricValue} ${passClass(value)}`}>{pass(value)}</span></div>;
            })}
            <div className={styles.metricRow}><span className={styles.metricName}>Latency total</span><span className={styles.metricValue}>{data.decision_latency_ms === null ? "—" : `${data.decision_latency_ms}ms`}</span></div>
          </div>
        </div>
      </div>
    </div>
  );
}

function DeltaComparison({ entry, exit }: { entry: Record<string, unknown>; exit: Record<string, unknown> }) {
  const keys = Array.from(new Set([...Object.keys(entry), ...Object.keys(exit)]))
    .filter((key) => !key.startsWith("_"))
    .sort((a, b) => a.localeCompare(b));
  if (keys.length === 0) return null;
  return (
    <div className={styles.card}>
      <div className={styles.cardHeader}><h2 className={styles.cardTitle}><Crosshair size={15} /> Comparativo entrada × saída</h2></div>
      <div className={styles.section}>
        <div className={styles.comparisonWrap}>
          <table className={styles.comparison}>
            <thead><tr><th>Indicador</th><th>Entrada</th><th>Saída</th><th>Δ absoluto</th><th>Δ %</th></tr></thead>
            <tbody>{keys.map((key) => {
              const before = entry[key];
              const after = exit[key];
              const numeric = typeof before === "number" && Number.isFinite(before) && typeof after === "number" && Number.isFinite(after);
              const delta = numeric ? after - before : null;
              const deltaPct = numeric && before !== 0 ? (delta! / Math.abs(before)) * 100 : null;
              const colorClass = delta === null || delta === 0 ? "" : delta > 0 ? styles.positive : styles.negative;
              return <tr key={key}><td>{key}</td><td>{fmtValue(before)}</td><td>{fmtValue(after)}</td><td className={colorClass}>{delta === null ? "—" : `${delta >= 0 ? "+" : ""}${delta.toFixed(4)}`}</td><td className={colorClass}>{deltaPct === null ? "—" : `${deltaPct >= 0 ? "+" : ""}${deltaPct.toFixed(2)}%`}</td></tr>;
            })}</tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export function ShadowTradeDetailScreen({ shadowId }: { shadowId: string }) {
  const [data, setData] = useState<ShadowTradeDetail | null>(null);
  const [chart, setChart] = useState<ShadowTradeChartResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chartError, setChartError] = useState<string | null>(null);
  const [chartSettled, setChartSettled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([
      apiGet<ShadowTradeDetail>(`/api/shadow-trades/${shadowId}`),
      apiGet<ShadowTradeChartResponse>(`/api/shadow-trades/${shadowId}/chart?context_minutes=${CHART_CONTEXT_MINUTES}`),
    ]).then(([detailResult, chartResult]) => {
      if (cancelled) return;
      if (detailResult.status === "fulfilled") setData(detailResult.value);
      else setError(errorText(detailResult.reason));
      if (chartResult.status === "fulfilled") setChart(chartResult.value);
      else setChartError(errorText(chartResult.reason));
      setChartSettled(true);
    });
    return () => { cancelled = true; };
  }, [shadowId]);

  const entryMetrics = data?.entry_metrics ?? data?.features_snapshot ?? {};
  const exitMetrics = data?.exit_metrics ?? (
    data?.features_snapshot_exit?.["_capture_failed"] === true ? {} : data?.features_snapshot_exit ?? {}
  );
  const entryRisk = data?.entry_risk_features ?? {};
  const legacyRisk = (entryRisk.legacy ?? {}) as Record<string, unknown>;
  const momentumRisk = (entryRisk.momentum_intensity ?? {}) as Record<string, unknown>;
  const exhaustionRisk = (entryRisk.exhaustion_risk ?? {}) as Record<string, unknown>;
  const pnlClass = data?.pnl_pct === null || data?.pnl_pct === undefined ? "" : data.pnl_pct >= 0 ? styles.positive : styles.negative;
  const chartWindow = useMemo(() => chart ? `${fmtDateTime(chart.window_start, true)} → ${fmtDateTime(chart.window_end, true)}` : "—", [chart]);

  function downloadJson() {
    if (!data || !chartSettled) return;
    const payload = buildShadowTradeExport(data, chart, {
      sourcePath: window.location.pathname,
      chartError,
    });
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = shadowTradeExportFilename(data);
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  if (error) {
    return <main className={styles.shell}><div className={styles.content}><Link href="/dashboard/shadow-portfolio" className={styles.breadcrumb}><ArrowLeft size={14} /> Voltar ao Shadow Portfolio</Link><div className={styles.error}><AlertTriangle size={16} /> {error}</div></div></main>;
  }
  if (!data) return <main className={styles.shell}><div className={styles.loading}>Carregando replay completo do trade…</div></main>;

  return (
    <main className={styles.shell}>
      <div className={styles.content}>
        <Link href="/dashboard/shadow-portfolio" className={styles.breadcrumb}><ArrowLeft size={14} /> Shadow Portfolio / Finalizados</Link>
        <header className={styles.hero}>
          <div>
            <div className={styles.eyebrow}>Trade replay · leitura histórica</div>
            <div className={styles.titleRow}>
              <h1 className={styles.title}>{data.symbol}</h1>
              <span className={styles.badge}>{data.status}</span>
              <span className={`${styles.badge} ${data.outcome === "TP_HIT" || (data.outcome === "TRAILING_STOP" && (data.pnl_pct ?? 0) >= 0) ? styles.badgeTp : data.outcome === "SL_HIT" || data.outcome === "TRAILING_STOP" ? styles.badgeSl : ""}`}>{outcomeLabel(data)}</span>
            </div>
          </div>
          <div className={styles.heroAside}>
            <div className={styles.heroActions}>
              <ModuleAIAnalysisAction
                originModule="shadow_portfolio"
                originView="shadow-portfolio-trade-detail"
                entityIds={[data.id]}
                label="Análise por IA"
                compact
              />
              <button
                type="button"
                className={styles.downloadButton}
                onClick={downloadJson}
                disabled={!chartSettled}
                title={chartSettled ? "Baixar todas as informações deste trade em JSON" : "Aguardando os dados do gráfico"}
              >
                <Download size={14} aria-hidden="true" />
                Baixar JSON completo
              </button>
            </div>
            <div className={styles.heroMeta}>
              <div className={styles.heroStat}><div className={styles.heroStatLabel}>P&amp;L</div><div className={`${styles.heroStatValue} ${pnlClass}`}>{fmtPct(data.pnl_pct)}</div></div>
              <div className={styles.heroStat}><div className={styles.heroStatLabel}>Resultado</div><div className={`${styles.heroStatValue} ${pnlClass}`}>{fmtUsd(data.pnl_usdt)}</div></div>
              <div className={styles.heroStat}><div className={styles.heroStatLabel}>Em posição</div><div className={styles.heroStatValue}>{fmtHolding(data.holding_seconds)}</div></div>
            </div>
          </div>
        </header>

        <div className={styles.stack}>
          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <h2 className={styles.cardTitle}><Crosshair size={15} /> Gráfico do trade</h2>
              <div className={styles.cardMeta}><span>{chart?.timeframe ?? "timeframe indisponível"}</span><span>{chart?.exchange ?? "exchange não informado"}</span><span>{chartWindow}</span></div>
            </div>
            <div className={styles.chartBody}>
              {chartError ? <div className={styles.error}><AlertTriangle size={15} /> Não foi possível carregar o gráfico: {chartError}</div> : chart ? <TradeCandlestickChart data={chart} /> : <div className={styles.loading}>Carregando candles…</div>}
            </div>
            <div className={styles.chartLegend}>
              <span className={styles.legendItem} style={{ color: "var(--green)" }}><span className={styles.legendDot} /> B · compra em {fmtDateTime(data.entry_timestamp ?? data.created_at, true)}</span>
              <span className={styles.legendItem} style={{ color: data.outcome === "TP_HIT" || (data.outcome === "TRAILING_STOP" && (data.pnl_pct ?? 0) >= 0) ? "var(--green)" : "var(--red)" }}><span className={styles.legendDot} /> S · fechamento {outcomeLabel(data)} em {fmtDateTime(data.exit_timestamp ?? data.completed_at, true)}</span>
              <span>Janela: {CHART_CONTEXT_MINUTES} min antes da entrada até {CHART_CONTEXT_MINUTES} min após o fechamento.</span>
              {chart?.timeframe && chart.timeframe !== "1m" ? <span>Candles disponíveis em {chart.timeframe}; os rótulos B/S preservam o timestamp exato do trade.</span> : null}
            </div>
          </section>

          <section className={styles.card}>
            <div className={styles.cardHeader}><h2 className={styles.cardTitle}><Database size={15} /> Registro completo</h2><div className={styles.cardMeta}><span>Decision ID {data.decision_id ?? "—"}</span><span>{data.profile_name ?? "Sem perfil associado"}</span></div></div>
            <div className={styles.section}>
              <div className={styles.summaryGrid}>
                <div className={styles.detailGroup}><div className={styles.detailGroupTitle}>Trade</div>
                  <DetailRow label="Direção" value={data.direction ?? "—"} />
                  <DetailRow label="Estratégia" value={data.strategy ?? "—"} />
                  <DetailRow label="Aberto em" value={fmtDateTime(data.entry_timestamp ?? data.created_at, true)} />
                  <DetailRow label="Fechado em" value={fmtDateTime(data.exit_timestamp ?? data.completed_at, true)} />
                  <DetailRow label="Tempo em posição" value={fmtHolding(data.holding_seconds)} />
                  <DetailRow label="Profile (Lab)" value={data.profile_name ?? "—"} className={data.profile_name ? styles.accent : ""} />
                  <DetailRow label="Profile Version" value={fmtDateTime(data.profile_version, true)} />
                  <DetailRow label="ML Probability" value={data.ml_probability === null ? "—" : `${(data.ml_probability * 100).toFixed(1)}%`} />
                  <DetailRow label="Priority Score" value={data.final_priority_score?.toFixed(3) ?? "—"} />
                </div>
                <div className={styles.detailGroup}><div className={styles.detailGroupTitle}>Preços e trajetória</div>
                  <DetailRow label="Entrada" value={fmtPrice(data.entry_price)} />
                  <DetailRow label="Take Profit" value={`${fmtPrice(data.tp_price)} (${fmtPct(data.tp_pct)})`} className={styles.positive} />
                  <DetailRow label="Stop Loss" value={`${fmtPrice(data.sl_price)} (${fmtPct(data.sl_pct)})`} className={styles.negative} />
                  <DetailRow label="Saída" value={fmtPrice(data.exit_price)} className={pnlClass} />
                  <DetailRow label="Tamanho" value={fmtUsd(data.amount_usdt, false)} />
                  <DetailRow label="P&L" value={`${fmtPct(data.pnl_pct)} (${fmtUsd(data.pnl_usdt)})`} className={pnlClass} />
                  <DetailRow label="MAE / drawdown" value={fmtPct(data.mae_pct ?? data.max_drawdown_pct)} />
                  <DetailRow label="MFE / máximo" value={fmtPct(data.mfe_pct ?? data.max_profit_pct)} />
                  <DetailRow label="BTC na entrada" value={fmtPrice(data.btc_price_at_entry)} />
                  <DetailRow label="Sinais simultâneos" value={data.n_concurrent_signals ?? "—"} />
                </div>
              </div>
            </div>
          </section>

          {data.consolidation ? (
            <section className={styles.card}>
              <div className={styles.cardHeader}>
                <h2 className={styles.cardTitle}><Database size={15} /> Consolidação de profiles</h2>
                <div className={styles.cardMeta}>
                  <span>{data.consolidation.rule_version}</span>
                  <span>{data.consolidation.candidate_count} candidatos</span>
                  <span>{data.consolidation.associated_count} associados</span>
                </div>
              </div>
              <div className={styles.section}>
                <div className={styles.auditGrid}>
                  {data.consolidation.candidates.map((candidate) => (
                    <div className={styles.subcard} key={`${candidate.rank}-${candidate.profile_id ?? candidate.profile_name}`}>
                      <div className={styles.subcardTitle}>
                        #{candidate.rank} · {candidate.rank === 1 ? "Profile principal" : "Profile associado"}
                      </div>
                      <div className={styles.metricRow}><span className={styles.metricName}>Profile</span><span className={styles.metricValue}>{candidate.profile_name ?? candidate.profile_id ?? "—"}</span></div>
                      <div className={styles.metricRow}><span className={styles.metricName}>Watchlist</span><span className={styles.metricValue}>{candidate.watchlist_name ?? candidate.watchlist_id ?? "—"}</span></div>
                      <div className={styles.metricRow}><span className={styles.metricName}>Origem da rejeição</span><span className={styles.metricValue}>{candidate.rejection_stage ?? "—"}</span></div>
                      <div className={styles.metricRow}><span className={styles.metricName}>Motivos</span><span className={styles.metricValue}>{candidate.rejection_reasons ? JSON.stringify(candidate.rejection_reasons) : "—"}</span></div>
                    </div>
                  ))}
                </div>
              </div>
            </section>
          ) : null}

          <DecisionAudit data={data} />

          <section className={styles.card}>
            <div className={styles.cardHeader}>
              <h2 className={styles.cardTitle}><Activity size={15} /> Risco de entrada · observacional</h2>
              <div className={styles.cardMeta}>
                <span>MONITOR_ONLY</span>
                <span>{data.entry_risk_capture_status}</span>
              </div>
            </div>
            <div className={styles.section}>
              <div className={styles.summaryGrid}>
                <div className={styles.detailGroup}>
                  <div className={styles.detailGroupTitle}>Entry Exhaustion Legacy</div>
                  <DetailRow label="Score" value={fmtValue(legacyRisk.entry_exhaustion_score)} />
                  <DetailRow label="Uso" value="Observational / deprecated for decision" />
                </div>
                <div className={styles.detailGroup}>
                  <div className={styles.detailGroupTitle}>Novos conceitos</div>
                  <DetailRow label="Momentum Intensity" value={fmtValue(momentumRisk.momentum_intensity_score)} />
                  <DetailRow label="Exhaustion Risk" value={fmtValue(exhaustionRisk.exhaustion_risk_score)} />
                  <DetailRow label="Efeito operacional" value="FALSE · não são probabilidades" />
                </div>
              </div>
              <SnapshotPanel title="Entry Risk Features v1" data={data.entry_risk_features ?? null} />
            </div>
          </section>

          <section className={styles.card}>
            <div className={styles.cardHeader}><h2 className={styles.cardTitle}><Activity size={15} /> Indicadores e configuração</h2><div className={styles.cardMeta}><span>Todos os campos persistidos, sem rolagem interna</span></div></div>
            <div className={styles.section}><div className={styles.snapshotGrid}>
              <SnapshotPanel title="Config Snapshot" data={data.config_snapshot} />
              <SnapshotPanel title="Indicadores na entrada" data={data.features_snapshot} filterMacro />
              <SnapshotPanel title="Indicadores na saída" data={data.features_snapshot_exit} filterMacro emptyMessage={exitSnapshotMessage(data)} />
            </div></div>
          </section>

          {data.rules_snapshot ? <section className={styles.card}><div className={styles.cardHeader}><h2 className={styles.cardTitle}><Database size={15} /> Regras do perfil</h2></div><div className={styles.section}><SnapshotPanel title="Rules Snapshot" data={data.rules_snapshot} /></div></section> : null}

          <DeltaComparison entry={entryMetrics} exit={exitMetrics} />

          <footer className={styles.footer}>
            <span><Clock size={11} style={{ verticalAlign: "middle", marginRight: 5 }} />Última candle processada: {fmtDateTime(data.last_processed_time, true)}</span>
            <span>Atualizado: {fmtDateTime(data.updated_at, true)}</span>
            <span>ID: {data.id}</span>
          </footer>
        </div>
      </div>
    </main>
  );
}

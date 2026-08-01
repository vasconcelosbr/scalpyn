import type { ShadowTradeChartResponse, ShadowTradeDetail } from "./types";

export const SHADOW_TRADE_EXPORT_SCHEMA = "scalpyn.shadow_trade_export";
export const SHADOW_TRADE_EXPORT_VERSION = "1.0.0";

interface ExportOptions {
  generatedAt?: string;
  sourcePath?: string;
  chartError?: string | null;
}

function numericDelta(before: unknown, after: unknown): { absolute: number | null; percent: number | null } {
  if (
    typeof before !== "number" ||
    !Number.isFinite(before) ||
    typeof after !== "number" ||
    !Number.isFinite(after)
  ) {
    return { absolute: null, percent: null };
  }

  const absolute = after - before;
  return {
    absolute,
    percent: before === 0 ? null : (absolute / Math.abs(before)) * 100,
  };
}

function buildIndicatorComparison(entry: Record<string, unknown>, exit: Record<string, unknown>) {
  return Array.from(new Set([...Object.keys(entry), ...Object.keys(exit)]))
    .filter((key) => !key.startsWith("_"))
    .sort((a, b) => a.localeCompare(b))
    .map((indicator) => {
      const entryValue = entry[indicator] ?? null;
      const exitValue = exit[indicator] ?? null;
      const delta = numericDelta(entryValue, exitValue);
      return {
        indicator,
        entry: entryValue,
        exit: exitValue,
        delta_absolute: delta.absolute,
        delta_percent: delta.percent,
      };
    });
}

export function buildShadowTradeExport(
  detail: ShadowTradeDetail,
  chart: ShadowTradeChartResponse | null,
  options: ExportOptions = {},
) {
  const generatedAt = options.generatedAt ?? new Date().toISOString();
  const sourcePath = options.sourcePath ?? `/dashboard/shadow-portfolio/${detail.id}`;
  const entryMetrics = detail.entry_metrics ?? detail.features_snapshot ?? {};
  const exitCaptureFailed = detail.features_snapshot_exit?.["_capture_failed"] === true;
  const exitMetrics = detail.exit_metrics ?? (exitCaptureFailed ? {} : detail.features_snapshot_exit ?? {});

  return {
    export_metadata: {
      schema: SHADOW_TRADE_EXPORT_SCHEMA,
      schema_version: SHADOW_TRADE_EXPORT_VERSION,
      generated_at: generatedAt,
      source_application: "Scalpyn",
      source_path: sourcePath,
      completeness: {
        trade_detail_loaded: true,
        chart_loaded: chart !== null,
        chart_error: options.chartError ?? null,
      },
    },
    trade: {
      identity: {
        id: detail.id,
        decision_id: detail.decision_id,
        symbol: detail.symbol,
        direction: detail.direction,
        strategy: detail.strategy,
        strategy_type: detail.strategy_type,
      },
      lifecycle: {
        status: detail.status,
        outcome: detail.outcome,
        skip_reason: detail.skip_reason,
        created_at: detail.created_at,
        entry_timestamp: detail.entry_timestamp,
        completed_at: detail.completed_at,
        exit_timestamp: detail.exit_timestamp,
        updated_at: detail.updated_at,
        last_processed_time: detail.last_processed_time,
        holding_seconds: detail.holding_seconds,
        timeout_candles: detail.timeout_candles,
      },
      position: {
        amount_usdt: detail.amount_usdt,
        entry_price: detail.entry_price,
        current_price: detail.current_price,
        take_profit_price: detail.tp_price,
        take_profit_percent: detail.tp_pct,
        stop_loss_price: detail.sl_price,
        stop_loss_percent: detail.sl_pct,
        exit_price: detail.exit_price,
      },
      performance: {
        pnl_percent: detail.pnl_pct,
        pnl_usdt: detail.pnl_usdt,
        mae_percent: detail.mae_pct,
        mfe_percent: detail.mfe_pct,
        max_drawdown_percent: detail.max_drawdown_pct,
        max_profit_percent: detail.max_profit_pct,
      },
      profile_and_model: {
        profile_id: detail.profile_id,
        profile_name: detail.profile_name,
        profile_version: detail.profile_version,
        ml_probability: detail.ml_probability,
        ml_model_id: detail.ml_model_id,
        final_priority_score: detail.final_priority_score,
      },
      market_context_at_entry: {
        btc_price: detail.btc_price_at_entry,
        btc_change_1h_percent: detail.btc_change_1h_pct,
        funding_rate: detail.funding_rate_at_entry,
        concurrent_signals: detail.n_concurrent_signals,
      },
    },
    decision_audit: {
      strategy: detail.decision_strategy,
      score: detail.decision_score,
      decision: detail.decision_decision,
      event_type: detail.decision_event_type,
      l1_pass: detail.decision_l1_pass,
      l2_pass: detail.decision_l2_pass,
      l3_pass: detail.decision_l3_pass,
      latency_ms: detail.decision_latency_ms,
      created_at: detail.decision_created_at,
      reasons: detail.decision_reasons,
      metrics: detail.decision_metrics,
    },
    snapshots: {
      config: detail.config_snapshot,
      profile_rules: detail.rules_snapshot,
      indicators_at_entry: detail.features_snapshot,
      indicators_at_exit: detail.features_snapshot_exit,
    },
    indicator_analysis: {
      entry_metrics: entryMetrics,
      exit_metrics: exitMetrics,
      exit_capture_failed: exitCaptureFailed,
      comparison: buildIndicatorComparison(entryMetrics, exitMetrics),
    },
    chart: chart ? {
      ...chart,
      markers: {
        buy: { label: "B", timestamp: chart.entry_timestamp, price: chart.entry_price },
        sell: {
          label: "S",
          timestamp: chart.exit_timestamp,
          price: chart.exit_price,
          outcome: chart.outcome,
        },
      },
    } : null,
    raw: {
      trade_detail: detail,
      chart_response: chart,
    },
  };
}

function filenamePart(value: string): string {
  return value
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80) || "trade";
}

export function shadowTradeExportFilename(detail: ShadowTradeDetail): string {
  const timestamp = detail.exit_timestamp ?? detail.completed_at ?? detail.entry_timestamp ?? detail.created_at;
  const date = timestamp && !Number.isNaN(new Date(timestamp).getTime())
    ? new Date(timestamp).toISOString().replace(/[:.]/g, "-")
    : "sem-data";
  const outcome = detail.outcome ?? detail.status;
  return `shadow-trade_${filenamePart(detail.symbol)}_${filenamePart(outcome)}_${date}_${filenamePart(detail.id)}.json`;
}

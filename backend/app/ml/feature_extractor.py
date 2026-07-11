"""Feature Extractor — Extract and engineer features from decisions_log.metrics JSONB."""

import hashlib
import json
import logging
import math
import os
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

FEATURE_SCHEMA_VERSION = "directional_v1"

# Feature columns for XGBoost model (order must be stable across train/predict)
BASE_FEATURE_COLUMNS: List[str] = [
    # Core market microstructure
    "taker_ratio",
    "volume_delta",
    "rsi",
    "macd_histogram_pct",     # macd_histogram / close * 100 — stationary cross-symbol (P1-5)
    "macd_histogram_slope",   # (hist[t]-hist[t-1]) / close * 100 — momentum acceleration (P1-5)
    "adx",
    "adx_acceleration",       # adx[t] - adx[t-1] — dimensionless, trend strength change rate
    "spread_pct",
    "volume_spike",
    # Decision-context score components persisted from the L3 decision cycle.
    # Keep the aggregate "score" excluded below; these components let the model
    # learn which profile score dimensions survive TP/SL without target leakage.
    "liquidity_score",
    "market_structure_score",
    "momentum_score",
    "signal_score",
    "di_trend",
    # Volatility — compression proxy; bb_width is already (upper-lower)/sma, scale-free
    "bb_width",
    "atr_pct",              # (atr/close)*100 — volatility magnitude; absent pre-audit (2.55σ finding)
    # Trend — booleans only; absolute EMA values removed (P1-2: non-stationary across symbols)
    # P2-6: ema9_gt_ema21 is redundant with ema_distance_pct (sign(ema_distance_pct) = ema9_gt_ema21).
    # Kept both — XGBoost feature importance will confirm if one can be dropped.
    "ema9_gt_ema21",
    "ema50_gt_ema200",
    # Liquidity — log1p-transformed in extract_features() (P1-4: multi-order-of-magnitude range)
    "volume_24h_usdt",
    "orderbook_depth_usdt",
    # Microstructure — ratio only; absolute taker volumes removed (P1-3: scale with market cap)
    "vwap_distance_pct",
    # obv_slope_5 removed — OBV disabled in production config → 1.1% coverage (12/1056).
    # Feature was always-NaN in training; XGBoost treated it as a dead slot.
    # Re-enable by adding it back here after enabling obv in indicators config.
    # Engineered (computed below)
    "flow_strength",
    "trend_alignment",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",       # (ema9 - ema21) / ema21 * 100
    "ema50_distance_pct",     # (close - ema50) / ema50 * 100  (P1-2)
    "ema200_distance_pct",    # (close - ema200) / ema200 * 100 (P1-2)
    # Directional features (L2 validation): additive, point-in-time derivatives
    # emitted by feature_engine.py when candle history is available. For legacy
    # snapshots the extractor keeps them as NaN rather than fabricating zeros.
    "rsi_slope_3",             # (rsi[t] - rsi[t-3]) / 3
    "rsi_slope_5",             # (rsi[t] - rsi[t-5]) / 5
    "macd_hist_slope_3",       # ((hist[t] - hist[t-3]) / close[t]) * 100
    "macd_hist_slope_5",       # ((hist[t] - hist[t-5]) / close[t]) * 100
    "ema21_ema50_distance_pct",  # ((ema21 - ema50) / ema50) * 100
    "di_plus_minus_diff",      # di_plus - di_minus
    "adx_slope_3",             # (adx[t] - adx[t-3]) / 3
    "vwap_reclaim_bool",       # vwap_distance_pct crosses from <0 to >=0
    "higher_highs_5",          # last 5 closed highs strictly ascending
    "higher_lows_5",           # last 5 closed lows strictly ascending
]

FEATURE_ALIASES: Dict[str, str] = {
    "ema9_ema21_distance_pct": "ema_distance_pct",
    "price_vs_vwap_pct": "vwap_distance_pct",
    "volume_spike_ratio": "volume_spike",
    "taker_buy_pressure_5m": "taker_ratio",
    "adx_slope_1": "adx_acceleration",
    "macd_hist_slope_1": "macd_histogram_slope",
}
# ── ML_EXCLUDED_FIELDS — defesa em profundidade ──────────────────────────────
# Filtro global aplicado ao dict de input em ``extract_features`` E asserts
# em ``build_training_dataframe`` / ``trainer.train``. Garante que mesmo se
# alguém reintroduzir um desses nomes em FEATURE_COLUMNS por engano, a
# entrada do modelo (treino E inferência) recusa o campo.
#
# Por que cada um está aqui:
# - score / score_raw / score_normalized / score_max:
#     leakage circular. ``score`` é derivado de rsi, ema9_gt_ema21,
#     volume_spike, atr_pct, macd_signal, price>vwap — features que JÁ
#     estão no modelo. XGBoost aprende o atalho ``if score > 80`` em vez
#     de aprender a relação estrutural (ADX forte + compressão + fluxo +
#     expansão).
# - score_classification:
#     derivado do ``score`` (neutral / buy / strong_buy); informação 100%
#     redundante.
# - score_components:
#     metadado operacional (ex.: ``{"engine":"robust"}``); zero conteúdo
#     preditivo.
# - signal_direction:
#     decisão operacional (long/short), não comportamento de mercado;
#     introduz viés de execução no modelo.
ML_EXCLUDED_FIELDS: frozenset = frozenset({
    "score",
    "score_raw",
    "score_normalized",
    "score_classification",
    "score_components",
    "score_max",
    "signal_direction",
})

FORBIDDEN_OPERATIONAL_PREFIXES: tuple[str, ...] = (
    "crypto_ev",
    "post_model_operational",
)


class MLLeakageError(RuntimeError):
    """Raised when post-model operational fields reach the ML pipeline."""


def assert_no_operational_feature_leakage(columns) -> None:
    leaked = [
        str(c)
        for c in columns
        if str(c).lower().startswith(FORBIDDEN_OPERATIONAL_PREFIXES)
    ]
    if leaked:
        raise MLLeakageError(f"Colunas operacionais proibidas no treino: {sorted(leaked)}")

FEATURE_COLUMNS = BASE_FEATURE_COLUMNS


def feature_columns_hash(feature_columns: List[str]) -> str:
    """Return a deterministic hash for an ordered feature schema."""
    payload = json.dumps(list(feature_columns), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── Label registry ────────────────────────────────────────────────────────────
# Maps the win-time threshold (seconds) to a canonical label version string
# stored in ml_models.label_version. Add new entries here when new label
# variants are introduced — do NOT change existing mappings.
_LABEL_THRESHOLD_REGISTRY: Dict[float, str] = {
    1800.0: "is_win_fast_v1",         # TP_HIT AND holding ≤ 30 min (original)
    14400.0: "is_tp_4h_v2_sim_outcome",  # TP_HIT AND holding ≤ 4 h, simulator outcome only (ttt_* prohibited as target)
    # Legacy alias — kept so old model rows with label_version='is_tp_4h_v1' remain readable.
    # New models always write 'is_tp_4h_v2_sim_outcome'; config ml_label_version overrides at save time.
}


def label_version_for_threshold(win_fast_threshold_s: float) -> str:
    """Return the canonical label_version string for a given time threshold.

    Falls back to a generic name for thresholds not in the registry so that
    experimental runs never silently reuse a production label name.
    """
    key = float(win_fast_threshold_s)
    if key in _LABEL_THRESHOLD_REGISTRY:
        return _LABEL_THRESHOLD_REGISTRY[key]
    return f"is_win_custom_{int(key)}s"

# FEATURES DELIBERATELY EXCLUDED FROM FEATURE_COLUMNS:
# "score"             — P0-2: calculated from rsi/ema9_gt_ema21/volume_spike/atr_pct/
#                       macd_signal/price>vwap; circular dependency dominates model.
#                       Reforçado por ML_EXCLUDED_FIELDS acima.
# "buy_pressure"      — P1-1: exact duplicate of taker_ratio (buy_pressure = taker_ratio
#                       in order_flow_service.py). Kept in pipeline for scoring layers.
# "orderbook_pressure"— P1-1: exact duplicate of bid_ask_imbalance. High blast radius in
#                       futures_pipeline_scorer; not in FEATURE_COLUMNS to avoid redundancy.
# "macd_histogram"    — P1-5: absolute value in price units — non-stationary cross-symbol
#                       (BTC histogram ≫ altcoin histogram). Replaced by macd_histogram_pct
#                       = macd_histogram / close * 100, which is scale-free.
#                       Raw macd_histogram_slope has the same P1-5 issue; it is normalized
#                       to (hist[t]-hist[t-1]) / close * 100 inside extract_features().
# "macd_signal"       — P1-1: string "positive"/"negative" encodes sign(macd_histogram_pct).
#                       Active trading logic in signal_engine.py depends on the string form;
#                       macd_histogram_pct (numeric) is already in FEATURE_COLUMNS.
# "ema5/9/21/50/200"  — P1-2: absolute price levels; non-stationary across symbols/time.
#                       Replaced by ema_distance_pct, ema50_distance_pct, ema200_distance_pct.
# "taker_buy_volume"  — P1-3: absolute volumes scale with market cap; non-stationary.
# "taker_sell_volume" — P1-3: same as above. taker_ratio already captures the balance.


def extract_features(metrics: dict) -> Dict[str, float]:
    """
    Extract and engineer features from a metrics dict.

    Args:
        metrics: decisions_log.metrics JSONB field

    Returns:
        Feature dictionary keyed by FEATURE_COLUMNS
    """
    _nan = float("nan")

    if not metrics:
        # P0-3: retornar nan (não 0.0) para features ausentes.
        # 0.0 é um sinal válido (ex.: taker_ratio=0 = 100% venda).
        # O pipeline de treino deve descartar rows com excesso de nan.
        return {f: _nan for f in FEATURE_COLUMNS}

    assert_no_operational_feature_leakage(metrics.keys())

    # ML_EXCLUDED_FIELDS — strip leakage/redundant fields BEFORE qualquer
    # processamento. Defesa em profundidade: mesmo se um desses nomes
    # estiver no JSONB do decisions_log.metrics, ele é descartado aqui
    # antes do _float() loop ou de qualquer engenharia. Cópia shallow
    # apenas se necessário (evita mutação do dict do caller).
    if any(k in metrics for k in ML_EXCLUDED_FIELDS):
        metrics = {k: v for k, v in metrics.items() if k not in ML_EXCLUDED_FIELDS}

    def _float(key: str, default: float = _nan) -> float:
        val = metrics.get(key, default)
        if isinstance(val, dict):
            val = val.get("value", default)
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    # P2-3 / P2-4: encode string categorical fields to numeric before the raw
    # copy loop.  _float() on a non-numeric string returns nan (ValueError),
    # silently losing the signal.  We normalise lazily — only copy the dict
    # when at least one string field is present.
    _needs_copy = (
        isinstance(metrics.get("macd_signal"), str)
        or isinstance(metrics.get("psar_trend"), str)
        or isinstance(metrics.get("psar_signal"), str)
    )
    if _needs_copy:
        metrics = dict(metrics)  # shallow copy — do not mutate the caller's dict
        _ms = metrics.get("macd_signal")
        if isinstance(_ms, str):
            metrics["macd_signal"] = 1.0 if _ms == "positive" else 0.0
        _pt = metrics.get("psar_trend")
        if isinstance(_pt, str):
            metrics["psar_trend"] = 1.0 if _pt == "up" else (0.0 if _pt == "down" else _nan)
        _ps = metrics.get("psar_signal")
        if isinstance(_ps, str):
            metrics["psar_signal"] = (
                1.0 if _ps == "BUY" else (-1.0 if _ps == "SELL" else (0.0 if _ps == "HOLD" else _nan))
            )

    f: Dict[str, float] = {}

    # Raw features — copy with safe float cast
    for col in FEATURE_COLUMNS:
        f[col] = _float(col)

    # P2-2: VWAP warm-up guard — first ≤12 candles of a session the VWAP is
    # essentially equal to the last traded price (≈0 pct distance), which is
    # not informative and biases the model toward early-session data.
    vwap_candle_count = metrics.get("vwap_candle_count")
    if vwap_candle_count is not None and int(vwap_candle_count) < 12:
        f["vwap_distance_pct"] = _nan

    # P1-4: log1p transform for volume fields (span multiple orders of magnitude).
    # nan when source absent; log1p(0) = 0 which is a valid signal.
    vol24h_raw = _float("volume_24h_usdt")
    f["volume_24h_usdt"] = math.log1p(vol24h_raw) if vol24h_raw >= 0 else _nan

    ob_depth_raw = _float("orderbook_depth_usdt")
    f["orderbook_depth_usdt"] = math.log1p(ob_depth_raw) if ob_depth_raw >= 0 else _nan

    # close — used by several engineered features below; resolve once here.
    close = _float("close") if not math.isnan(_float("close")) else _float("price")

    # Engineered features (override raw placeholder)

    # macd_histogram_slope: raw value is in absolute price units (P1-5, same as macd_histogram).
    # Normalize to (hist[t]-hist[t-1]) / close * 100 for cross-symbol stationarity.
    _hist_slope_raw = _float("macd_histogram_slope")
    f["macd_histogram_slope"] = (
        _hist_slope_raw / close * 100
        if not math.isnan(_hist_slope_raw) and not math.isnan(close) and close != 0
        else _nan
    )

    # P0-4: flow_strength = nan quando qualquer operando for ausente.
    # _float() já retorna nan por padrão; nan * qualquer_coisa = nan.
    f["flow_strength"] = _float("taker_ratio") * _float("volume_delta")

    # P2-7: trend_alignment — NaN propagation quando qualquer EMA bool ausente.
    # Anterior: (1 if x else 0) convertia None silenciosamente para 0,
    # mascarando dado ausente como "sinal bearish". _float() retorna nan para
    # chaves ausentes; a soma só ocorre quando ambos os operandos são válidos.
    _e9_e21 = _float("ema9_gt_ema21")
    _e50_e200 = _float("ema50_gt_ema200")
    f["trend_alignment"] = (
        _e9_e21 + _e50_e200
        if not math.isnan(_e9_e21) and not math.isnan(_e50_e200)
        else _nan
    )

    f["momentum_strength"] = _float("macd_histogram_pct") * _float("adx")

    # delta_normalized uses raw vol24h (not log-transformed) to preserve ratio semantics
    f["delta_normalized"] = _float("volume_delta") / vol24h_raw if vol24h_raw > 0 else _nan

    ema21 = _float("ema21")
    # nan quando ema21 ausente — used only for engineered features, not in FEATURE_COLUMNS directly
    f["ema_distance_pct"] = (
        (_float("ema9") - ema21) / ema21 * 100 if ema21 > 0 else _nan
    )

    # P1-2: price distance from medium/long-term EMAs (stationary cross-symbol proxy)
    ema50 = _float("ema50")
    f["ema50_distance_pct"] = (close - ema50) / ema50 * 100 if ema50 > 0 and not math.isnan(close) else _nan

    ema200 = _float("ema200")
    f["ema200_distance_pct"] = (close - ema200) / ema200 * 100 if ema200 > 0 and not math.isnan(close) else _nan

    # Directional feature fallbacks that can be derived from scalar snapshot
    # fields. Slope/crossing features need prior candles and are therefore
    # emitted by feature_engine.py; legacy rows remain NaN.
    f["ema21_ema50_distance_pct"] = (
        (ema21 - ema50) / ema50 * 100
        if ema50 > 0 and not math.isnan(ema21)
        else f.get("ema21_ema50_distance_pct", _nan)
    )

    di_plus = _float("di_plus")
    di_minus = _float("di_minus")
    f["di_plus_minus_diff"] = (
        di_plus - di_minus
        if not math.isnan(di_plus) and not math.isnan(di_minus)
        else f.get("di_plus_minus_diff", _nan)
    )

    return f


# Kept only for caller/log compatibility. The supervised label is simulator
# outcome + holding time, not realized PnL or post-entry TTT analysis.
_FEE_ROUND_TRIP_PCT = float(os.getenv("FEE_ROUND_TRIP_PCT", "0.16"))  # 0.16%


def build_training_dataframe(
    records: list,
    fee_roundtrip_pct: Optional[float] = None,
    label_net_of_fees: bool = False,
    win_fast_threshold_s: int = 1800,
    label_objective: str = "fast_tp",
    backfilled_feature_names: Optional[List[str]] = None,
    backfill_marker_key: Optional[str] = None,
) -> pd.DataFrame:
    """
    Build training DataFrame from shadow_trades records (fonte canônica — Bloco B).

    Args:
        records: List of dicts from shadow_trades query.
                 Expected fields: symbol, source, created_at,
                 features_snapshot (JSONB flat — indicadores na entrada),
                 pnl_pct, net_return_pct (optional), holding_seconds, outcome.
        fee_roundtrip_pct: Deprecated; kept for caller compatibility.
        label_net_of_fees: Deprecated; kept for caller compatibility.
        win_fast_threshold_s: Holding ceiling (seconds) above which a TP_HIT
                 is classified as slow win → label 0. Read from config_profiles
                 (ml_win_fast_threshold_seconds). Default 1800 (30 min).

    Returns:
        DataFrame with FEATURE_COLUMNS + is_win_fast + _created_at columns.

    Label semantics:
        Tier 1 (simulator ground truth): outcome == 'TP_HIT' AND
            holding_seconds <= win_fast_threshold_s → 1; everything else → 0.
            Slow wins (TP_HIT but holding > threshold) are labeled 0: a slow win
            is a bad entry with a lucky exit and must not teach the model to
            approve prolonged capital exposure.
        Rows with pnl_pct IS NULL are DROPPED.

        Vocabulário de shadow_trades.outcome: 'TP_HIT' / 'SL_HIT' (uppercase).
    """
    backfilled_features = [f for f in (backfilled_feature_names or []) if f in FEATURE_COLUMNS]
    marker_key = backfill_marker_key or ""
    rows = []
    dropped_null_pnl = 0
    rows_with_backfill_neutralized = 0
    for r in records:
        pnl_pct = r.get("pnl_pct")
        if pnl_pct is None:
            dropped_null_pnl += 1
            continue

        metrics = r.get("features_snapshot") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        if isinstance(metrics, dict):
            assert_no_operational_feature_leakage(metrics.keys())

        if marker_key and backfilled_features and isinstance(metrics, dict) and marker_key in metrics:
            metrics = dict(metrics)
            for feature_name in backfilled_features:
                metrics[feature_name] = float("nan")
            rows_with_backfill_neutralized += 1

        features = extract_features(metrics)

        # ``score`` NÃO é mais propagado nem como metadado (ML_EXCLUDED_FIELDS).
        # Análises de score vs. desfecho devem usar ``decisions_log.score``
        # diretamente, fora do pipeline ML.

        try:
            pnl_val = float(pnl_pct)
        except (TypeError, ValueError):
            dropped_null_pnl += 1
            continue

        # Target: simulator ground truth only. TTT buckets and realized PnL are
        # post-entry analysis signals and must not define the supervised label.
        holding_s = r.get("holding_seconds")
        holding_ok = holding_s is not None and holding_s <= win_fast_threshold_s
        net_return = r.get("net_return_pct")
        if net_return is None:
            net_return = pnl_val
        if label_objective == "positive_net_return":
            features["is_win_fast"] = 1 if float(net_return) > 0.0 else 0
        elif label_objective == "fast_tp":
            features["is_win_fast"] = 1 if (r.get("outcome") == "TP_HIT" and holding_ok) else 0
        else:
            raise ValueError(f"unsupported_ml_label_objective:{label_objective}")

        # Metadata for time-based split — NOT model features
        features["_created_at"] = r.get("created_at")
        features["_outcome"] = r.get("outcome")
        features["_pnl_pct"] = pnl_val
        features["_net_return_pct"] = float(net_return)
        features["_holding_seconds"] = r.get("holding_seconds", 0)
        rows.append(features)

    if dropped_null_pnl:
        logger.info(
            f"Dropped {dropped_null_pnl} records with NULL pnl_pct "
            f"(cannot label without realized PnL)"
        )

    df = pd.DataFrame(rows)
    df.attrs["rows_with_backfill_neutralized"] = rows_with_backfill_neutralized
    df.attrs["backfilled_feature_names"] = backfilled_features
    df.attrs["backfill_marker_key"] = marker_key
    logger.info(f"Training dataframe: {len(df)} rows, {len(df.columns)} cols")
    if marker_key and backfilled_features:
        logger.info(
            "BACKFILL_NEUTRALIZATION|marker=%s|features=%d|rows_with_backfill_neutralized=%d",
            marker_key,
            len(backfilled_features),
            rows_with_backfill_neutralized,
        )

    # ML_EXCLUDED_FIELDS — guardrail final: nenhum dos campos vazados pode
    # estar no df. Falha alta e cedo (fail-fast no Cloud Run Job) em vez de
    # contaminar silenciosamente o modelo.
    _leaked = ML_EXCLUDED_FIELDS.intersection(df.columns)
    assert not _leaked, (
        f"ML_EXCLUDED_FIELDS detectados no training dataframe: {sorted(_leaked)}. "
        f"Verificar build_training_dataframe e extract_features."
    )
    assert_no_operational_feature_leakage(df.columns)

    if len(df) > 0:
        win_rate = df["is_win_fast"].mean() * 100
        logger.info(
            "Base WIN rate: %.1f%% (label_version=%s; "
            "label_net_of_fees ignored=%s; fee ignored=%.4f)",
            win_rate, label_version_for_threshold(win_fast_threshold_s),
            label_net_of_fees, _FEE_ROUND_TRIP_PCT,
        )

    return df


def filter_trainable_features(
    df: pd.DataFrame,
    feature_cols: list,
    min_coverage: float = 0.30,
) -> tuple:
    """Filter feature columns to those with sufficient coverage and variance.

    Excludes features with:
    - Coverage (non-NaN fraction) < min_coverage — too sparse to be useful
    - std == 0 (constant when present) — no discriminative power

    Returns:
        (kept_cols, excluded_list) where excluded_list is a list of
        (col, reason) tuples for logging / notes.

    This is the BLOCO C dynamic feature filter from PROMPT_ARQUITETURA_ML_SPOT.
    Macro features with 0% coverage (MDH not yet live) are silently excluded
    here and re-enter training automatically once coverage rises above min_coverage.
    """
    kept: list = []
    excluded: list = []

    for col in feature_cols:
        if col not in df.columns:
            excluded.append((col, "not_in_df"))
            continue

        series = df[col]
        n_total = len(series)
        if n_total == 0:
            excluded.append((col, "empty_df"))
            continue

        coverage = float(series.notna().sum()) / n_total
        if coverage < min_coverage:
            excluded.append((col, f"low_coverage_{coverage:.2f}"))
            continue

        # Check std only on non-NaN values
        non_null = series.dropna()
        if len(non_null) > 0 and float(non_null.std()) == 0.0:
            excluded.append((col, "zero_std"))
            continue

        kept.append(col)

    if excluded:
        logger.info(
            "FEATURE_FILTER|kept=%d|excluded=%d|details=%s",
            len(kept),
            len(excluded),
            excluded[:20],  # cap log length
        )
    else:
        logger.info("FEATURE_FILTER|kept=%d|excluded=0", len(kept))

    return kept, excluded


def train_val_test_split(
    df: pd.DataFrame,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
    target_window_seconds: int = 14400,
) -> tuple:
    """
    Time-based split with Purge and Embargo to prevent leakage.

    Args:
        df: DataFrame with _created_at column
        train_ratio: Fraction for training (default 70%)
        val_ratio: Fraction for validation (default 15%); rest goes to test
        target_window_seconds: Embargo gap in seconds

    Returns:
        (train_df, val_df, test_df)
    """
    df = df.sort_values("_created_at").reset_index(drop=True)
    n = len(df)
    train_end = int(n * train_ratio)
    val_end = int(n * (train_ratio + val_ratio))

    train_df = df.iloc[:train_end].copy()
    val_df = df.iloc[train_end:val_end].copy()
    test_df = df.iloc[val_end:].copy()

    n_purged = 0
    n_val_embargoed = 0
    n_test_embargoed = 0

    if len(train_df) > 0 and len(val_df) > 0:
        val_start_time = val_df["_created_at"].min()
        
        # Purge: remove from train any row whose (created_at + holding_seconds) >= val_start_time
        if "_holding_seconds" in train_df.columns:
            holding_td = pd.to_timedelta(train_df["_holding_seconds"].fillna(0), unit='s')
            purge_mask = (train_df["_created_at"] + holding_td) < val_start_time
            n_purged = len(train_df) - purge_mask.sum()
            train_df = train_df[purge_mask].copy()

        # Embargo: remove from val/test any row whose created_at <= (last_train_created_at + target_window_seconds)
        if len(train_df) > 0:
            train_end_time = train_df["_created_at"].max()
            embargo_end_time = train_end_time + pd.Timedelta(seconds=target_window_seconds)
            
            val_embargo_mask = val_df["_created_at"] > embargo_end_time
            n_val_embargoed = len(val_df) - val_embargo_mask.sum()
            val_df = val_df[val_embargo_mask].copy()
            
            test_embargo_mask = test_df["_created_at"] > embargo_end_time
            n_test_embargoed = len(test_df) - test_embargo_mask.sum()
            test_df = test_df[test_embargo_mask].copy()

    logger.info(
        "TEMPORAL_SPLIT|train=%d(%s→%s)|val=%d|test=%d(%s→%s)|purged=%d|embargoed=%d",
        len(train_df),
        str(train_df["_created_at"].min())[:10] if len(train_df) else "n/a",
        str(train_df["_created_at"].max())[:10] if len(train_df) else "n/a",
        len(val_df),
        len(test_df),
        str(test_df["_created_at"].min())[:10] if len(test_df) else "n/a",
        str(test_df["_created_at"].max())[:10] if len(test_df) else "n/a",
        n_purged,
        n_val_embargoed + n_test_embargoed
    )
    return train_df, val_df, test_df

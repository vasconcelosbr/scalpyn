"""Feature Extractor — Extract and engineer features from decisions_log.metrics JSONB."""

import json
import logging
import math
import os
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# Feature columns for XGBoost model (order must be stable across train/predict)
FEATURE_COLUMNS: List[str] = [
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
    # Volatility — compression proxy; bb_width is already (upper-lower)/sma, scale-free
    "bb_width",
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
]
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

# ── Macro / intermarket features (Market Data Hub enrichment layer) ──────────
# Added at the END so that models trained before this change can be used
# by truncating X to model.n_features_in_ in prediction_service.py.
# When the model is retrained with macro data, truncation is removed automatically.
from .macro_features import MACRO_FEATURE_COLUMNS as _MACRO_COLS  # noqa: E402
FEATURE_COLUMNS = FEATURE_COLUMNS + _MACRO_COLS

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

    # ML_EXCLUDED_FIELDS — strip leakage/redundant fields BEFORE qualquer
    # processamento. Defesa em profundidade: mesmo se um desses nomes
    # estiver no JSONB do decisions_log.metrics, ele é descartado aqui
    # antes do _float() loop ou de qualquer engenharia. Cópia shallow
    # apenas se necessário (evita mutação do dict do caller).
    if any(k in metrics for k in ML_EXCLUDED_FIELDS):
        metrics = {k: v for k, v in metrics.items() if k not in ML_EXCLUDED_FIELDS}

    def _float(key: str, default: float = _nan) -> float:
        val = metrics.get(key, default)
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

    return f


_MIN_WIN_PNL_PCT = float(os.getenv("MIN_WIN_PNL_PCT", "0.008"))


def build_training_dataframe(records: list) -> pd.DataFrame:
    """
    Build training DataFrame from decisions_log records.

    Args:
        records: List of dicts from decisions_log query.
                 Expected fields: id, symbol, created_at, metrics (JSONB),
                 score, pnl_pct, holding_seconds, outcome.

    Returns:
        DataFrame with FEATURE_COLUMNS + is_win_fast + _created_at columns.

    Label semantics (Task #324):
        is_win_fast = 1 when pnl_pct > MIN_WIN_PNL_PCT (env, default 0.008 = 0.8%),
        else 0. Rows with pnl_pct IS NULL are DROPPED — we cannot label them.

        Vocabulário canônico de ``decisions_log.outcome`` é lowercase
        ``tp``/``sl`` (regime pós-14/05; timeout foi descontinuado — trades
        agora ficam abertos até TP ou SL). NÃO usar `outcome == "WIN"` para
        derivar o label: esse vocabulário uppercase nunca existiu em prod,
        produzia 0% de positivos e colapsava o treino (scale_pos_weight=1.0,
        Optuna AUC=0 silencioso).
    """
    rows = []
    dropped_null_pnl = 0
    for r in records:
        pnl_pct = r.get("pnl_pct")
        if pnl_pct is None:
            dropped_null_pnl += 1
            continue

        metrics = r.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        features = extract_features(metrics)

        # ``score`` NÃO é mais propagado nem como metadado (ML_EXCLUDED_FIELDS).
        # Análises de score vs. desfecho devem usar ``decisions_log.score``
        # diretamente, fora do pipeline ML.

        try:
            pnl_val = float(pnl_pct)
        except (TypeError, ValueError):
            dropped_null_pnl += 1
            continue

        # Target: WIN_FAST = trade with PnL > MIN_WIN_PNL_PCT (default 0).
        features["is_win_fast"] = 1 if pnl_val > _MIN_WIN_PNL_PCT else 0

        # Metadata for time-based split — NOT model features
        features["_created_at"] = r.get("created_at")
        features["_outcome"] = r.get("outcome")
        features["_pnl_pct"] = pnl_val

        rows.append(features)

    if dropped_null_pnl:
        logger.info(
            f"Dropped {dropped_null_pnl} records with NULL pnl_pct "
            f"(cannot label without realized PnL)"
        )

    df = pd.DataFrame(rows)
    logger.info(f"Training dataframe: {len(df)} rows, {len(df.columns)} cols")

    # ML_EXCLUDED_FIELDS — guardrail final: nenhum dos campos vazados pode
    # estar no df. Falha alta e cedo (fail-fast no Cloud Run Job) em vez de
    # contaminar silenciosamente o modelo.
    _leaked = ML_EXCLUDED_FIELDS.intersection(df.columns)
    assert not _leaked, (
        f"ML_EXCLUDED_FIELDS detectados no training dataframe: {sorted(_leaked)}. "
        f"Verificar build_training_dataframe e extract_features."
    )

    if len(df) > 0:
        win_rate = df["is_win_fast"].mean() * 100
        logger.info(f"Base WIN rate: {win_rate:.1f}%")

    return df


def train_val_test_split(
    df: pd.DataFrame,
    train_ratio: float = 0.70,
    val_ratio: float = 0.15,
) -> tuple:
    """
    Time-based split — no shuffle to avoid look-ahead bias.

    Args:
        df: DataFrame with _created_at column
        train_ratio: Fraction for training (default 70%)
        val_ratio: Fraction for validation (default 15%); rest goes to test

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

    logger.info(
        f"Time split: train={len(train_df)} val={len(val_df)} test={len(test_df)}"
    )
    return train_df, val_df, test_df

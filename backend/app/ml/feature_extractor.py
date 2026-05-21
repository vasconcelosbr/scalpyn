"""Feature Extractor — Extract and engineer features from decisions_log.metrics JSONB."""

import json
import logging
import math
from typing import Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# Feature columns for XGBoost model (order must be stable across train/predict)
FEATURE_COLUMNS: List[str] = [
    # Core market microstructure
    "taker_ratio",
    "volume_delta",
    "rsi",
    "macd_histogram",
    "adx",
    "spread_pct",
    "volume_spike",
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
    # OBV derivative — stationary (P2-1: raw obv cumulative excluded)
    "obv_slope_5",            # (obv[-1] - obv[-5]) / 5
    # Engineered (computed below)
    "flow_strength",
    "trend_alignment",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",       # (ema9 - ema21) / ema21 * 100
    "ema50_distance_pct",     # (close - ema50) / ema50 * 100  (P1-2)
    "ema200_distance_pct",    # (close - ema200) / ema200 * 100 (P1-2)
]
# FEATURES DELIBERATELY EXCLUDED FROM FEATURE_COLUMNS:
# "score"             — P0-2: calculated from rsi/ema9_gt_ema21/volume_spike/atr_pct/
#                       macd_signal/price>vwap; circular dependency dominates model.
#                       Stored as _score_meta in build_training_dataframe() for analysis.
# "buy_pressure"      — P1-1: exact duplicate of taker_ratio (buy_pressure = taker_ratio
#                       in order_flow_service.py). Kept in pipeline for scoring layers.
# "orderbook_pressure"— P1-1: exact duplicate of bid_ask_imbalance. High blast radius in
#                       futures_pipeline_scorer; not in FEATURE_COLUMNS to avoid redundancy.
# "macd_signal"       — P1-1: string "positive"/"negative" encodes sign(macd_histogram).
#                       Active trading logic in signal_engine.py depends on the string form;
#                       macd_histogram (numeric) is already in FEATURE_COLUMNS.
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

    # Engineered features (override raw placeholder)

    # P0-4: flow_strength = nan quando qualquer operando for ausente.
    # _float() já retorna nan por padrão; nan * qualquer_coisa = nan.
    f["flow_strength"] = _float("taker_ratio") * _float("volume_delta")

    f["trend_alignment"] = float(
        (1 if metrics.get("ema9_gt_ema21") else 0)
        + (1 if metrics.get("ema50_gt_ema200") else 0)
    )

    f["momentum_strength"] = _float("macd_histogram") * _float("adx")

    # delta_normalized uses raw vol24h (not log-transformed) to preserve ratio semantics
    f["delta_normalized"] = _float("volume_delta") / vol24h_raw if vol24h_raw > 0 else _nan

    ema21 = _float("ema21")
    # nan quando ema21 ausente — used only for engineered features, not in FEATURE_COLUMNS directly
    f["ema_distance_pct"] = (
        (_float("ema9") - ema21) / ema21 * 100 if ema21 > 0 else _nan
    )

    # P1-2: price distance from medium/long-term EMAs (stationary cross-symbol proxy)
    close = _float("close") if not math.isnan(_float("close")) else _float("price")
    ema50 = _float("ema50")
    f["ema50_distance_pct"] = (close - ema50) / ema50 * 100 if ema50 > 0 and not math.isnan(close) else _nan

    ema200 = _float("ema200")
    f["ema200_distance_pct"] = (close - ema200) / ema200 * 100 if ema200 > 0 and not math.isnan(close) else _nan

    return f


def build_training_dataframe(records: list) -> pd.DataFrame:
    """
    Build training DataFrame from decisions_log records.

    Args:
        records: List of dicts from decisions_log query.
                 Expected fields: id, symbol, created_at, metrics (JSONB),
                 score, pnl_pct, holding_seconds, outcome.

    Returns:
        DataFrame with FEATURE_COLUMNS + is_win_fast + _created_at columns.
    """
    rows = []
    for r in records:
        metrics = r.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}

        features = extract_features(metrics)

        # score armazenado como metadado para análise — NÃO é feature do modelo
        # (removido de FEATURE_COLUMNS em P0-2). Não passar para df[FEATURE_COLUMNS].
        features["_score_meta"] = float(r.get("score") or 0.0)

        # Target: WIN_FAST = trade that resulted in a win
        features["is_win_fast"] = 1 if r.get("outcome") == "WIN" else 0

        # Metadata for time-based split — NOT a model feature
        features["_created_at"] = r.get("created_at")

        rows.append(features)

    df = pd.DataFrame(rows)
    logger.info(f"Training dataframe: {len(df)} rows, {len(df.columns)} cols")

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

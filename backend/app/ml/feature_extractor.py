"""Feature Extractor — Extract and engineer features from decisions_log.metrics JSONB."""

import json
import logging
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
    # Trend
    "ema5",
    "ema9",
    "ema21",
    "ema50",
    "ema200",
    "ema9_gt_ema21",
    "ema50_gt_ema200",
    # Liquidity
    "volume_24h_usdt",
    "orderbook_depth_usdt",
    # Microstructure
    "taker_buy_volume",
    "taker_sell_volume",
    "vwap_distance_pct",
    # Engineered (computed below)
    "flow_strength",
    "trend_alignment",
    "momentum_strength",
    "delta_normalized",
    "ema_distance_pct",
    # Signal quality
    "score",
]


def extract_features(metrics: dict) -> Dict[str, float]:
    """
    Extract and engineer features from a metrics dict.

    Args:
        metrics: decisions_log.metrics JSONB field

    Returns:
        Feature dictionary keyed by FEATURE_COLUMNS
    """
    if not metrics:
        return {f: 0.0 for f in FEATURE_COLUMNS}

    def _float(key: str, default: float = 0.0) -> float:
        val = metrics.get(key, default)
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    f: Dict[str, float] = {}

    # Raw features — copy with safe float cast
    for col in FEATURE_COLUMNS:
        f[col] = _float(col)

    # Engineered features (override raw placeholder)
    f["flow_strength"] = _float("taker_ratio") * _float("volume_delta")

    f["trend_alignment"] = float(
        (1 if metrics.get("ema9_gt_ema21") else 0)
        + (1 if metrics.get("ema50_gt_ema200") else 0)
    )

    f["momentum_strength"] = _float("macd_histogram") * _float("adx")

    vol24h = _float("volume_24h_usdt")
    f["delta_normalized"] = _float("volume_delta") / vol24h if vol24h > 0 else 0.0

    ema21 = _float("ema21")
    f["ema_distance_pct"] = (
        (_float("ema9") - ema21) / ema21 * 100 if ema21 > 0 else 0.0
    )

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

        # decisions_log.score overrides the score from metrics JSONB
        features["score"] = float(r.get("score") or 0.0)

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

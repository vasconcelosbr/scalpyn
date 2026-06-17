"""
Indicator Lift Analyzer — computes per-bucket lift statistics from shadow trades.

Each indicator is split into discrete buckets; for each bucket we measure
win_rate, avg_pnl_pct, lift_vs_base, presence rates, etc. and classify the
bucket's role (winning_indicator / losing_indicator / neutral / low_sample).
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile_intelligence import ProfileIndicatorStats

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bucket definitions
# ---------------------------------------------------------------------------

def _get_indicator_buckets() -> list:
    """Return list of bucket definitions (indicator, bucket_label, condition callable)."""
    return [
        # RSI
        {"indicator": "rsi", "bucket_label": "rsi_lt_24", "condition": lambda v: v < 24, "range_max": 24},
        {"indicator": "rsi", "bucket_label": "rsi_24_30", "condition": lambda v: 24 <= v < 30, "range_min": 24, "range_max": 30},
        {"indicator": "rsi", "bucket_label": "rsi_30_38", "condition": lambda v: 30 <= v < 38, "range_min": 30, "range_max": 38},
        {"indicator": "rsi", "bucket_label": "rsi_38_45", "condition": lambda v: 38 <= v < 45, "range_min": 38, "range_max": 45},
        {"indicator": "rsi", "bucket_label": "rsi_45_55", "condition": lambda v: 45 <= v < 55, "range_min": 45, "range_max": 55},
        {"indicator": "rsi", "bucket_label": "rsi_55_65", "condition": lambda v: 55 <= v < 65, "range_min": 55, "range_max": 65},
        {"indicator": "rsi", "bucket_label": "rsi_65_72", "condition": lambda v: 65 <= v < 72, "range_min": 65, "range_max": 72},
        {"indicator": "rsi", "bucket_label": "rsi_gte_72", "condition": lambda v: v >= 72, "range_min": 72},
        # ADX
        {"indicator": "adx", "bucket_label": "adx_lt_12", "condition": lambda v: v < 12, "range_max": 12},
        {"indicator": "adx", "bucket_label": "adx_12_18", "condition": lambda v: 12 <= v < 18, "range_min": 12, "range_max": 18},
        {"indicator": "adx", "bucket_label": "adx_18_20", "condition": lambda v: 18 <= v < 20, "range_min": 18, "range_max": 20},
        {"indicator": "adx", "bucket_label": "adx_20_25", "condition": lambda v: 20 <= v < 25, "range_min": 20, "range_max": 25},
        {"indicator": "adx", "bucket_label": "adx_25_35", "condition": lambda v: 25 <= v < 35, "range_min": 25, "range_max": 35},
        {"indicator": "adx", "bucket_label": "adx_gte_35", "condition": lambda v: v >= 35, "range_min": 35},
        # ZScore
        {"indicator": "zscore", "bucket_label": "zscore_lt_neg3", "condition": lambda v: v < -3, "range_max": -3},
        {"indicator": "zscore", "bucket_label": "zscore_neg3_neg1_5", "condition": lambda v: -3 <= v < -1.5, "range_min": -3, "range_max": -1.5},
        {"indicator": "zscore", "bucket_label": "zscore_neg1_5_0_8", "condition": lambda v: -1.5 <= v <= 0.8, "range_min": -1.5, "range_max": 0.8},
        {"indicator": "zscore", "bucket_label": "zscore_0_8_1_5", "condition": lambda v: 0.8 < v <= 1.5, "range_min": 0.8, "range_max": 1.5},
        {"indicator": "zscore", "bucket_label": "zscore_1_5_2_2", "condition": lambda v: 1.5 < v <= 2.2, "range_min": 1.5, "range_max": 2.2},
        {"indicator": "zscore", "bucket_label": "zscore_gt_2_2", "condition": lambda v: v > 2.2, "range_min": 2.2},
        # VWAP Distance
        {"indicator": "vwap_distance_pct", "bucket_label": "vwap_lte_1_5", "condition": lambda v: v <= 1.5, "range_max": 1.5},
        {"indicator": "vwap_distance_pct", "bucket_label": "vwap_1_5_2_0", "condition": lambda v: 1.5 < v <= 2.0, "range_min": 1.5, "range_max": 2.0},
        {"indicator": "vwap_distance_pct", "bucket_label": "vwap_2_0_3_0", "condition": lambda v: 2.0 < v <= 3.0, "range_min": 2.0, "range_max": 3.0},
        {"indicator": "vwap_distance_pct", "bucket_label": "vwap_gt_3_0", "condition": lambda v: v > 3.0, "range_min": 3.0},
        # ATR
        {"indicator": "atr_pct", "bucket_label": "atr_lt_0_3", "condition": lambda v: v < 0.3, "range_max": 0.3},
        {"indicator": "atr_pct", "bucket_label": "atr_0_3_0_5", "condition": lambda v: 0.3 <= v < 0.5, "range_min": 0.3, "range_max": 0.5},
        {"indicator": "atr_pct", "bucket_label": "atr_0_5_2_5", "condition": lambda v: 0.5 <= v <= 2.5, "range_min": 0.5, "range_max": 2.5},
        {"indicator": "atr_pct", "bucket_label": "atr_2_5_5_0", "condition": lambda v: 2.5 < v <= 5.0, "range_min": 2.5, "range_max": 5.0},
        {"indicator": "atr_pct", "bucket_label": "atr_gt_5_0", "condition": lambda v: v > 5.0, "range_min": 5.0},
        # BB Width
        {"indicator": "bb_width", "bucket_label": "bb_lt_0_012", "condition": lambda v: v < 0.012, "range_max": 0.012},
        {"indicator": "bb_width", "bucket_label": "bb_0_012_0_015", "condition": lambda v: 0.012 <= v < 0.015, "range_min": 0.012, "range_max": 0.015},
        {"indicator": "bb_width", "bucket_label": "bb_0_015_0_050", "condition": lambda v: 0.015 <= v <= 0.050, "range_min": 0.015, "range_max": 0.050},
        {"indicator": "bb_width", "bucket_label": "bb_0_050_0_080", "condition": lambda v: 0.050 < v <= 0.080, "range_min": 0.050, "range_max": 0.080},
        {"indicator": "bb_width", "bucket_label": "bb_gt_0_080", "condition": lambda v: v > 0.080, "range_min": 0.080},
        # Volume Delta
        {"indicator": "volume_delta", "bucket_label": "vol_delta_lt_neg20", "condition": lambda v: v < -20, "range_max": -20},
        {"indicator": "volume_delta", "bucket_label": "vol_delta_neg20_0", "condition": lambda v: -20 <= v < 0, "range_min": -20, "range_max": 0},
        {"indicator": "volume_delta", "bucket_label": "vol_delta_0_10", "condition": lambda v: 0 <= v < 10, "range_min": 0, "range_max": 10},
        {"indicator": "volume_delta", "bucket_label": "vol_delta_gte_10", "condition": lambda v: v >= 10, "range_min": 10},
        # Taker Ratio
        {"indicator": "taker_ratio", "bucket_label": "taker_lt_0_35", "condition": lambda v: v < 0.35, "range_max": 0.35},
        {"indicator": "taker_ratio", "bucket_label": "taker_0_35_0_45", "condition": lambda v: 0.35 <= v < 0.45, "range_min": 0.35, "range_max": 0.45},
        {"indicator": "taker_ratio", "bucket_label": "taker_0_45_0_55", "condition": lambda v: 0.45 <= v < 0.55, "range_min": 0.45, "range_max": 0.55},
        {"indicator": "taker_ratio", "bucket_label": "taker_gte_0_55", "condition": lambda v: v >= 0.55, "range_min": 0.55},
        # Spread
        {"indicator": "spread_pct", "bucket_label": "spread_lte_0_15", "condition": lambda v: v <= 0.15, "range_max": 0.15},
        {"indicator": "spread_pct", "bucket_label": "spread_0_15_0_20", "condition": lambda v: 0.15 < v <= 0.20, "range_min": 0.15, "range_max": 0.20},
        {"indicator": "spread_pct", "bucket_label": "spread_0_20_0_30", "condition": lambda v: 0.20 < v <= 0.30, "range_min": 0.20, "range_max": 0.30},
        {"indicator": "spread_pct", "bucket_label": "spread_gt_0_30", "condition": lambda v: v > 0.30, "range_min": 0.30},
        # Orderbook Depth
        {"indicator": "orderbook_depth_usdt", "bucket_label": "depth_lt_10k", "condition": lambda v: v < 10000, "range_max": 10000},
        {"indicator": "orderbook_depth_usdt", "bucket_label": "depth_10k_20k", "condition": lambda v: 10000 <= v < 20000, "range_min": 10000, "range_max": 20000},
        {"indicator": "orderbook_depth_usdt", "bucket_label": "depth_gte_20k", "condition": lambda v: v >= 20000, "range_min": 20000},
        # Boolean/categorical
        {"indicator": "macd_histogram_pct", "bucket_label": "macd_hist_gt_0", "condition": lambda v: v > 0, "value_text": ">0"},
        {"indicator": "macd_histogram_pct", "bucket_label": "macd_hist_lte_0", "condition": lambda v: v <= 0, "value_text": "<=0"},
        {"indicator": "adx_acceleration", "bucket_label": "adx_acc_gt_0", "condition": lambda v: v > 0, "value_text": ">0"},
        {"indicator": "adx_acceleration", "bucket_label": "adx_acc_lte_0", "condition": lambda v: v <= 0, "value_text": "<=0"},
        {"indicator": "ema9_gt_ema21", "bucket_label": "ema9_gt_ema21_true", "condition": lambda v: bool(v), "value_text": "true"},
        {"indicator": "ema9_gt_ema21", "bucket_label": "ema9_gt_ema21_false", "condition": lambda v: not bool(v), "value_text": "false"},
        {"indicator": "ema50_gt_ema200", "bucket_label": "ema50_gt_ema200_true", "condition": lambda v: bool(v), "value_text": "true"},
        {"indicator": "ema50_gt_ema200", "bucket_label": "ema50_gt_ema200_false", "condition": lambda v: not bool(v), "value_text": "false"},
        {"indicator": "orderbook_pressure", "bucket_label": "obp_gte_0_35", "condition": lambda v: v >= 0.35, "range_min": 0.35},
        {"indicator": "orderbook_pressure", "bucket_label": "obp_lt_0_20", "condition": lambda v: v < 0.20, "range_max": 0.20},
        {"indicator": "buy_pressure", "bucket_label": "buy_pres_gte_0_55", "condition": lambda v: v >= 0.55, "range_min": 0.55},
        {"indicator": "volume_spike", "bucket_label": "vol_spike_gte_1_2", "condition": lambda v: v >= 1.2, "range_min": 1.2},
        {"indicator": "volume_spike", "bucket_label": "vol_spike_gte_1_5", "condition": lambda v: v >= 1.5, "range_min": 1.5},
        {"indicator": "volume_spike", "bucket_label": "vol_spike_gt_2_5", "condition": lambda v: v > 2.5, "range_min": 2.5},
    ]


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class IndicatorLiftAnalyzer:
    """Computes per-bucket lift statistics for each indicator from shadow trades."""

    async def analyze(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        lookback_days: int,
        min_closed_trades: int,
        base_win_rate: float,
        base_avg_pnl_pct: float,
        discovery_start: datetime,
        discovery_end: datetime,
    ) -> List[dict]:
        """
        Load closed shadow trades in the discovery window, compute bucket stats,
        bulk-insert into profile_indicator_stats and return list sorted by
        lift_vs_base DESC.
        """
        logger.info(
            "[IndicatorLift] Starting analysis for user=%s run=%s window=%s→%s",
            user_id, run_id, discovery_start, discovery_end,
        )

        # ------------------------------------------------------------------
        # 1. Load trades
        # ------------------------------------------------------------------
        rows = (
            await db.execute(
                text("""
                    SELECT
                        outcome,
                        pnl_pct,
                        mae_pct,
                        mfe_pct,
                        holding_seconds,
                        features_snapshot
                    FROM shadow_trades
                    WHERE user_id = :uid
                      AND created_at >= :disc_start
                      AND created_at < :disc_end
                      AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                      AND features_snapshot IS NOT NULL
                      AND features_snapshot != '{}'::jsonb
                    ORDER BY created_at
                    LIMIT 50000
                """),
                {
                    "uid": str(user_id),
                    "disc_start": discovery_start,
                    "disc_end": discovery_end,
                },
            )
        ).fetchall()

        if not rows:
            logger.info("[IndicatorLift] No trades found in discovery window.")
            return []

        # ------------------------------------------------------------------
        # 2. Pre-process trades into Python dicts
        # ------------------------------------------------------------------
        trades = []
        global_wins = 0
        global_losses = 0
        for row in rows:
            features = row.features_snapshot
            if isinstance(features, str):
                try:
                    features = json.loads(features)
                except Exception:
                    features = {}
            if not isinstance(features, dict):
                features = {}

            outcome = row.outcome or ""
            is_win = outcome == "TP_HIT"
            is_loss = outcome == "SL_HIT"
            is_timeout = outcome == "TIMEOUT"
            if is_win:
                global_wins += 1
            elif is_loss:
                global_losses += 1

            trades.append({
                "outcome": outcome,
                "is_win": is_win,
                "is_loss": is_loss,
                "is_timeout": is_timeout,
                "pnl_pct": row.pnl_pct if row.pnl_pct is not None else 0.0,
                "mae_pct": row.mae_pct if row.mae_pct is not None else 0.0,
                "mfe_pct": row.mfe_pct if row.mfe_pct is not None else 0.0,
                "holding_seconds": row.holding_seconds if row.holding_seconds is not None else 0,
                "features": features,
            })

        total_trades = len(trades)
        logger.info(
            "[IndicatorLift] Loaded %d trades (wins=%d losses=%d)",
            total_trades, global_wins, global_losses,
        )

        # ------------------------------------------------------------------
        # 3. Per-bucket aggregation
        # ------------------------------------------------------------------
        buckets = _get_indicator_buckets()

        # bucket_key = (indicator, bucket_label)
        bucket_data: Dict[tuple, dict] = {}

        for bdef in buckets:
            key = (bdef["indicator"], bdef["bucket_label"])
            bucket_data[key] = {
                "bucket_def": bdef,
                "total": 0,
                "wins": 0,
                "losses": 0,
                "timeouts": 0,
                "pnl_sum": 0.0,
                "pnl_count": 0,
                "mae_sum": 0.0,
                "mfe_sum": 0.0,
                "holding_sum": 0,
                "winner_holding_sum": 0,
                "tp15": 0,
                "tp30": 0,
                "tp60": 0,
            }

        for trade in trades:
            feat = trade["features"]
            for bdef in buckets:
                ind = bdef["indicator"]
                raw_val = feat.get(ind)
                if raw_val is None:
                    continue
                try:
                    numeric_val = float(raw_val)
                except (TypeError, ValueError):
                    # For booleans stored as strings
                    if isinstance(raw_val, bool):
                        numeric_val = float(raw_val)
                    else:
                        continue

                try:
                    passes = bdef["condition"](numeric_val)
                except Exception:
                    continue

                if not passes:
                    continue

                key = (ind, bdef["bucket_label"])
                bd = bucket_data[key]
                bd["total"] += 1
                if trade["is_win"]:
                    bd["wins"] += 1
                    bd["winner_holding_sum"] += trade["holding_seconds"]
                    hs = trade["holding_seconds"]
                    if hs <= 900:
                        bd["tp15"] += 1
                    if hs <= 1800:
                        bd["tp30"] += 1
                    if hs <= 3600:
                        bd["tp60"] += 1
                elif trade["is_loss"]:
                    bd["losses"] += 1
                elif trade["is_timeout"]:
                    bd["timeouts"] += 1

                bd["pnl_sum"] += trade["pnl_pct"]
                bd["pnl_count"] += 1
                bd["mae_sum"] += trade["mae_pct"]
                bd["mfe_sum"] += trade["mfe_pct"]
                bd["holding_sum"] += trade["holding_seconds"]

        # ------------------------------------------------------------------
        # 4. Compute derived metrics and build result rows
        # ------------------------------------------------------------------
        safe_base_wr = max(base_win_rate, 0.001)
        results = []

        for key, bd in bucket_data.items():
            indicator, bucket_label = key
            total = bd["total"]
            wins = bd["wins"]
            losses = bd["losses"]
            timeouts = bd["timeouts"]
            closed = wins + losses + timeouts  # all are closed (filtered at load)

            if total == 0:
                continue  # skip empty buckets entirely — no row saved

            win_rate = wins / max(closed, 1)
            loss_rate = losses / max(closed, 1)
            avg_pnl_pct = bd["pnl_sum"] / bd["pnl_count"] if bd["pnl_count"] > 0 else 0.0
            avg_holding = bd["holding_sum"] / total if total > 0 else 0.0
            avg_winner_holding = bd["winner_holding_sum"] / wins if wins > 0 else 0.0
            avg_mae_pct = bd["mae_sum"] / total
            avg_mfe_pct = bd["mfe_sum"] / total
            tp15_rate = bd["tp15"] / max(closed, 1)
            tp30_rate = bd["tp30"] / max(closed, 1)
            tp60_rate = bd["tp60"] / max(closed, 1)
            lift_vs_base = win_rate / safe_base_wr
            pnl_lift = avg_pnl_pct / base_avg_pnl_pct if base_avg_pnl_pct != 0 else 1.0

            winner_presence_pct = (wins / global_wins * 100) if global_wins > 0 else 0.0
            loser_presence_pct = (losses / global_losses * 100) if global_losses > 0 else 0.0

            raw_confidence = min(1.0, total / 100.0) * lift_vs_base
            confidence_score = raw_confidence * 100.0

            if total < min_closed_trades:
                confidence_level = "LOW"
            elif total < 100:
                confidence_level = "MEDIUM"
            else:
                confidence_level = "HIGH"

            if total >= min_closed_trades and lift_vs_base >= 1.15 and avg_pnl_pct > base_avg_pnl_pct:
                role_detected = "winning_indicator"
            elif total >= min_closed_trades and (
                win_rate < safe_base_wr * 0.85
                or avg_pnl_pct < base_avg_pnl_pct * 0.85
            ):
                role_detected = "losing_indicator"
            elif total < min_closed_trades:
                role_detected = "low_sample"
            else:
                role_detected = "neutral"

            bdef = bd["bucket_def"]
            result = {
                "indicator": indicator,
                "bucket_label": bucket_label,
                "operator": None,
                "range_min": bdef.get("range_min"),
                "range_max": bdef.get("range_max"),
                "value_text": bdef.get("value_text"),
                "total_cases": total,
                "wins": wins,
                "losses": losses,
                "timeouts": timeouts,
                "win_rate": win_rate,
                "loss_rate": loss_rate,
                "avg_pnl_pct": avg_pnl_pct,
                "avg_holding_seconds": avg_holding,
                "avg_winner_holding_seconds": avg_winner_holding,
                "avg_mae_pct": avg_mae_pct,
                "avg_mfe_pct": avg_mfe_pct,
                "tp_15m_rate": tp15_rate,
                "tp_30m_rate": tp30_rate,
                "tp_60m_rate": tp60_rate,
                "lift_vs_base": lift_vs_base,
                "pnl_lift_vs_base": pnl_lift,
                "winner_presence_pct": winner_presence_pct,
                "loser_presence_pct": loser_presence_pct,
                "confidence_score": confidence_score,
                "confidence_level": confidence_level,
                "role_detected": role_detected,
            }
            results.append(result)

        # ------------------------------------------------------------------
        # 5. Bulk-insert into profile_indicator_stats
        # ------------------------------------------------------------------
        for r in results:
            row_obj = ProfileIndicatorStats(
                user_id=user_id,
                run_id=run_id,
                indicator=r["indicator"],
                operator=r["operator"],
                range_min=r["range_min"],
                range_max=r["range_max"],
                value_text=r["value_text"],
                bucket_label=r["bucket_label"],
                total_cases=r["total_cases"],
                wins=r["wins"],
                losses=r["losses"],
                timeouts=r["timeouts"],
                win_rate=r["win_rate"],
                loss_rate=r["loss_rate"],
                avg_pnl_pct=r["avg_pnl_pct"],
                avg_holding_seconds=r["avg_holding_seconds"],
                avg_winner_holding_seconds=r["avg_winner_holding_seconds"],
                avg_mae_pct=r["avg_mae_pct"],
                avg_mfe_pct=r["avg_mfe_pct"],
                tp_15m_rate=r["tp_15m_rate"],
                tp_30m_rate=r["tp_30m_rate"],
                tp_60m_rate=r["tp_60m_rate"],
                lift_vs_base=r["lift_vs_base"],
                pnl_lift_vs_base=r["pnl_lift_vs_base"],
                winner_presence_pct=r["winner_presence_pct"],
                loser_presence_pct=r["loser_presence_pct"],
                confidence_score=r["confidence_score"],
                confidence_level=r["confidence_level"],
                role_detected=r["role_detected"],
            )
            db.add(row_obj)

        if results:
            await db.flush()

        # Sort by lift_vs_base DESC
        results.sort(key=lambda x: x["lift_vs_base"], reverse=True)

        logger.info(
            "[IndicatorLift] Saved %d bucket stats for run=%s", len(results), run_id
        )
        return results

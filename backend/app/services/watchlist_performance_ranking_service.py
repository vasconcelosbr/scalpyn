"""Canonical performance ordering for Shadow Portfolio and L3 watchlists."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigProfile


logger = logging.getLogger(__name__)

CONFIG_TYPE = "watchlist_performance_ranking"

# Seed template only. Runtime scoring always loads the persisted, user-editable
# config_profiles row and fails closed when it is absent or invalid.
DEFAULT_RANKING_CONFIG: Dict[str, Any] = {
    "version": 1,
    "source_filter": ["L3", "L3_LAB"],
    "weights": {"pnl": 35, "win_rate": 20, "sample": 15, "tp4h": 15, "pnl_total": 10},
    "normalization": {
        "avg_pnl_pct_target": 1.0,
        "sample_target": 500,
        "pnl_total_usdt_target": 1000,
    },
    "limits": {"score_min": 0, "score_max": 100, "pnl_component_min": -20},
    "penalties": {
        "holding_over_4h": 5,
        "holding_over_8h": 10,
        "low_n_under_30": 30,
        "low_n_under_50": 15,
        "low_n_under_100": 5,
        "negative_avg_pnl": 25,
        "negative_total_pnl": 10,
    },
    "thresholds": {
        "sample_low_n": 30,
        "sample_low": 50,
        "sample_medium": 100,
        "sample_high": 300,
        "priority_a_plus": 75,
        "priority_a": 60,
        "priority_b": 45,
        "priority_c": 30,
        "low_n_score_cap": 44.99,
        "good_win_rate": 0.50,
        "good_tp4h_rate": 0.40,
        "shadow_tp4h_rate": 0.20,
        "tp4h_seconds": 14_400,
        "holding_warning_seconds": 14_400,
        "holding_severe_seconds": 28_800,
    },
}


class RankingConfigError(ValueError):
    """Raised when the DB-backed ranking contract is missing or invalid."""


def _number(config: Mapping[str, Any], path: str) -> float:
    value: Any = config
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            raise RankingConfigError(f"missing ranking config key: {path}")
        value = value[part]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RankingConfigError(f"ranking config key must be numeric: {path}")
    return float(value)


def validate_ranking_config(config: Mapping[str, Any]) -> Dict[str, Any]:
    if not config:
        raise RankingConfigError(f"active config_profiles.{CONFIG_TYPE} is required")
    sources = config.get("source_filter")
    if not isinstance(sources, list) or not sources or not all(isinstance(v, str) for v in sources):
        raise RankingConfigError("source_filter must be a non-empty string list")

    required = (
        "weights.pnl",
        "weights.win_rate",
        "weights.sample",
        "weights.tp4h",
        "weights.pnl_total",
        "normalization.avg_pnl_pct_target",
        "normalization.sample_target",
        "normalization.pnl_total_usdt_target",
        "limits.score_min",
        "limits.score_max",
        "limits.pnl_component_min",
        "penalties.holding_over_4h",
        "penalties.holding_over_8h",
        "penalties.low_n_under_30",
        "penalties.low_n_under_50",
        "penalties.low_n_under_100",
        "penalties.negative_avg_pnl",
        "penalties.negative_total_pnl",
        "thresholds.sample_low_n",
        "thresholds.sample_low",
        "thresholds.sample_medium",
        "thresholds.sample_high",
        "thresholds.priority_a_plus",
        "thresholds.priority_a",
        "thresholds.priority_b",
        "thresholds.priority_c",
        "thresholds.low_n_score_cap",
        "thresholds.good_win_rate",
        "thresholds.good_tp4h_rate",
        "thresholds.shadow_tp4h_rate",
        "thresholds.tp4h_seconds",
        "thresholds.holding_warning_seconds",
        "thresholds.holding_severe_seconds",
    )
    for path in required:
        _number(config, path)
    return dict(config)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _priority(completed: int, avg_pnl: float, score: float, cfg: Mapping[str, Any]) -> str:
    low_n = int(_number(cfg, "thresholds.sample_low_n"))
    low = int(_number(cfg, "thresholds.sample_low"))
    medium = int(_number(cfg, "thresholds.sample_medium"))
    if completed == 0:
        return "BLOCKED"
    if completed < low_n:
        return "LOW_N"
    if score >= _number(cfg, "thresholds.priority_a_plus") and completed >= medium and avg_pnl > 0:
        return "A+"
    if score >= _number(cfg, "thresholds.priority_a") and completed >= low and avg_pnl > 0:
        return "A"
    if score >= _number(cfg, "thresholds.priority_b") and completed >= low_n:
        return "B"
    if score >= _number(cfg, "thresholds.priority_c"):
        return "C"
    return "D"


def _confidence(completed: int, cfg: Mapping[str, Any]) -> str:
    if completed == 0:
        return "EMPTY"
    if completed >= int(_number(cfg, "thresholds.sample_high")):
        return "HIGH"
    if completed >= int(_number(cfg, "thresholds.sample_medium")):
        return "MEDIUM"
    if completed >= int(_number(cfg, "thresholds.sample_low_n")):
        return "LOW"
    return "LOW_N"


def _operational_class(completed: int, win_rate: float, tp4h_rate: float, cfg: Mapping[str, Any]) -> str:
    if completed == 0:
        return "EMPTY"
    if completed < int(_number(cfg, "thresholds.sample_low_n")):
        return "LOW_N"
    good_wr = _number(cfg, "thresholds.good_win_rate")
    good_tp4h = _number(cfg, "thresholds.good_tp4h_rate")
    shadow_tp4h = _number(cfg, "thresholds.shadow_tp4h_rate")
    if win_rate >= good_wr and tp4h_rate >= good_tp4h:
        return "GOOD_4H"
    if win_rate >= good_wr:
        return "SLOW_WINNER"
    if tp4h_rate >= shadow_tp4h:
        return "GOOD_SHADOW_BAD_4H"
    return "BAD_BOTH"


def score_metrics(metrics: Mapping[str, Any], config: Mapping[str, Any]) -> Dict[str, Any]:
    """Score one aggregated row. PnL uses the canonical DB unit: 1.0 == 1%."""

    cfg = validate_ranking_config(config)
    completed = int(metrics.get("completed_trades") or 0)
    wins = int(metrics.get("wins") or 0)
    avg_pnl = float(metrics.get("avg_pnl_pct") or 0.0)
    pnl_total = float(metrics.get("pnl_total_usdt") or 0.0)
    win_rate = wins / completed if completed else 0.0
    tp4h_rate = float(metrics.get("tp_4h_wins") or 0) / wins if wins else 0.0
    avg_holding = metrics.get("avg_holding_win_seconds")
    avg_holding_value = float(avg_holding) if avg_holding is not None else None

    pnl_weight = _number(cfg, "weights.pnl")
    win_weight = _number(cfg, "weights.win_rate")
    sample_weight = _number(cfg, "weights.sample")
    tp4h_weight = _number(cfg, "weights.tp4h")
    total_weight = _number(cfg, "weights.pnl_total")
    pnl_target = _number(cfg, "normalization.avg_pnl_pct_target")
    sample_target = _number(cfg, "normalization.sample_target")
    total_target = _number(cfg, "normalization.pnl_total_usdt_target")
    if min(pnl_target, sample_target, total_target) <= 0:
        raise RankingConfigError("normalization targets must be positive")

    components = {
        "pnl_component": _clamp((avg_pnl / pnl_target) * pnl_weight, _number(cfg, "limits.pnl_component_min"), pnl_weight),
        "winrate_component": _clamp(win_rate * win_weight, 0.0, win_weight),
        "sample_component": _clamp(
            math.log(max(completed, 1)) / math.log(sample_target) * sample_weight,
            0.0,
            sample_weight,
        ),
        "tp4h_component": _clamp(tp4h_rate * tp4h_weight, 0.0, tp4h_weight),
        "pnl_total_component": _clamp((pnl_total / total_target) * total_weight, -total_weight, total_weight),
    }

    holding_penalty = 0.0
    if avg_holding_value is not None:
        if avg_holding_value > _number(cfg, "thresholds.holding_severe_seconds"):
            holding_penalty = _number(cfg, "penalties.holding_over_8h")
        elif avg_holding_value > _number(cfg, "thresholds.holding_warning_seconds"):
            holding_penalty = _number(cfg, "penalties.holding_over_4h")

    low_n = int(_number(cfg, "thresholds.sample_low_n"))
    low = int(_number(cfg, "thresholds.sample_low"))
    medium = int(_number(cfg, "thresholds.sample_medium"))
    if completed < low_n:
        low_n_penalty = _number(cfg, "penalties.low_n_under_30")
    elif completed < low:
        low_n_penalty = _number(cfg, "penalties.low_n_under_50")
    elif completed < medium:
        low_n_penalty = _number(cfg, "penalties.low_n_under_100")
    else:
        low_n_penalty = 0.0

    negative_pnl_penalty = 0.0
    if avg_pnl < 0:
        negative_pnl_penalty += _number(cfg, "penalties.negative_avg_pnl")
    if pnl_total < 0:
        negative_pnl_penalty += _number(cfg, "penalties.negative_total_pnl")

    raw_score = sum(components.values()) - holding_penalty - low_n_penalty - negative_pnl_penalty
    ev_score = _clamp(raw_score, _number(cfg, "limits.score_min"), _number(cfg, "limits.score_max"))
    if 0 < completed < low_n:
        ev_score = min(ev_score, _number(cfg, "thresholds.low_n_score_cap"))
    ev_score = round(ev_score, 2)
    priority = _priority(completed, avg_pnl, ev_score, cfg)
    stat_confidence = _confidence(completed, cfg)
    operational_class = _operational_class(completed, win_rate, tp4h_rate, cfg)

    if completed == 0:
        reason = "Bloqueado: nenhuma operação concluída; aguardar amostra antes de priorizar."
    elif completed < low_n:
        reason = f"Rebaixado para LOW_N: apenas {completed} trades concluídos; aguardar mais amostra."
    elif avg_pnl < 0 or pnl_total < 0:
        reason = (
            f"Prioridade {priority}: penalidade por P&L negativo; {completed} trades, "
            f"win rate {win_rate * 100:.1f}% e TP4h {tp4h_rate * 100:.1f}%."
        )
    else:
        reason = (
            f"Prioridade {priority}: P&L médio {avg_pnl:+.2f}%, {completed} trades, "
            f"win rate {win_rate * 100:.1f}%, TP4h {tp4h_rate * 100:.1f}%, classe {operational_class}."
        )

    return {
        "win_rate": round(win_rate, 6) if completed else None,
        "tp_4h_rate": round(tp4h_rate, 6) if wins else None,
        "ev_score": ev_score,
        "stat_confidence": stat_confidence,
        "priority": priority,
        "priority_reason": reason,
        "operational_class": operational_class,
        "score_components": {**{k: round(v, 4) for k, v in components.items()},
                             "holding_penalty": holding_penalty,
                             "low_n_penalty": low_n_penalty,
                             "negative_pnl_penalty": negative_pnl_penalty},
    }


_CONFIDENCE_ORDER = {"HIGH": 4, "MEDIUM": 3, "LOW": 2, "LOW_N": 1, "EMPTY": 0}


def sort_rankings(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ordered = sorted(
        rows,
        key=lambda row: (
            -(row.get("ev_score") or 0.0),
            -_CONFIDENCE_ORDER.get(str(row.get("stat_confidence")), 0),
            -(row.get("avg_pnl_pct") or 0.0),
            -(row.get("completed_trades") or 0),
            -(row.get("tp_4h_rate") or 0.0),
            -(row.get("pnl_total_usdt") or 0.0),
            str(row.get("profile_name") or ""),
            str(row.get("watchlist_id") or ""),
        ),
    )
    for position, row in enumerate(ordered, start=1):
        row["rank_position"] = position
    return ordered


async def get_performance_rankings(
    db: AsyncSession,
    user_id: UUID,
    *,
    level: str | None = None,
) -> List[Dict[str, Any]]:
    config_row = (
        await db.execute(
            select(ConfigProfile)
            .where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == CONFIG_TYPE,
                ConfigProfile.is_active.is_(True),
            )
            .order_by(ConfigProfile.updated_at.desc())
            .limit(1)
        )
    ).scalars().first()
    config = validate_ranking_config(config_row.config_json if config_row else {})
    sources = config["source_filter"]
    query = text("""
        WITH selected AS (
            SELECT *
            FROM watchlist_performance_priority_base_view
            WHERE user_id = :uid
              AND (source IS NULL OR source = ANY(CAST(:sources AS text[])))
        ), aggregated AS (
            SELECT
                user_id,
                profile_id,
                MAX(profile_name) AS profile_name,
                watchlist_id,
                MAX(watchlist_name) AS watchlist_name,
                COALESCE(MAX(level), 'L3') AS level,
                SUM(total_trades)::integer AS total_trades,
                SUM(open_trades)::integer AS open_trades,
                SUM(completed_trades)::integer AS completed_trades,
                SUM(wins)::integer AS wins,
                SUM(tp_4h_wins)::integer AS tp_4h_wins,
                SUM(pnl_pct_sum)::double precision AS pnl_pct_sum,
                SUM(pnl_count)::integer AS pnl_count,
                SUM(pnl_total_usdt)::double precision AS pnl_total_usdt,
                SUM(holding_win_sum)::double precision AS holding_win_sum,
                SUM(holding_win_count)::integer AS holding_win_count,
                MIN(first_trade) AS first_trade,
                MAX(last_trade) AS last_trade
            FROM selected
            WHERE profile_id IS NOT NULL
            GROUP BY user_id, profile_id, watchlist_id
        ), baseline AS (
            SELECT
                SUM(wins)::double precision / NULLIF(SUM(completed_trades), 0) AS baseline_win_rate,
                SUM(pnl_pct_sum)::double precision / NULLIF(SUM(pnl_count), 0) AS baseline_avg_pnl_pct
            FROM selected
            WHERE source IS NOT NULL
        )
        SELECT aggregated.*, baseline.baseline_win_rate, baseline.baseline_avg_pnl_pct, now() AS computed_at
        FROM aggregated CROSS JOIN baseline
    """)
    raw_rows = (await db.execute(query, {"uid": str(user_id), "sources": sources})).mappings().all()
    rankings: List[Dict[str, Any]] = []
    for raw in raw_rows:
        completed = int(raw["completed_trades"] or 0)
        wins = int(raw["wins"] or 0)
        avg_pnl = (
            float(raw["pnl_pct_sum"] or 0.0) / int(raw["pnl_count"])
            if int(raw["pnl_count"] or 0) > 0 else None
        )
        avg_holding = (
            float(raw["holding_win_sum"] or 0.0) / int(raw["holding_win_count"])
            if int(raw["holding_win_count"] or 0) > 0 else None
        )
        base = {
            "profile_id": raw["profile_id"],
            "profile_name": raw["profile_name"],
            "watchlist_id": raw["watchlist_id"],
            "watchlist_name": raw["watchlist_name"],
            "level": raw["level"],
            "total": int(raw["total_trades"] or 0),
            "total_trades": int(raw["total_trades"] or 0),
            "open_count": int(raw["open_trades"] or 0),
            "open_trades": int(raw["open_trades"] or 0),
            "win_count": wins,
            "wins": wins,
            "decided_count": completed,
            "completed_trades": completed,
            "tp_4h_count": int(raw["tp_4h_wins"] or 0),
            "tp_4h_wins": int(raw["tp_4h_wins"] or 0),
            "pnl_avg_pct": round(avg_pnl, 6) if avg_pnl is not None else None,
            "avg_pnl_pct": round(avg_pnl, 6) if avg_pnl is not None else None,
            "pnl_total_usdt": round(float(raw["pnl_total_usdt"] or 0.0), 4),
            "avg_holding_win_seconds": round(avg_holding, 1) if avg_holding is not None else None,
            "baseline_win_rate": float(raw["baseline_win_rate"] or 0.0),
            "baseline_avg_pnl_pct": float(raw["baseline_avg_pnl_pct"] or 0.0),
            "first_trade": raw["first_trade"],
            "last_trade": raw["last_trade"],
            "computed_at": raw["computed_at"] or datetime.now(timezone.utc),
        }
        scored = score_metrics(base, config)
        base.update(scored)
        base["delta_win_rate_vs_baseline"] = round(
            (base["win_rate"] or 0.0) - base["baseline_win_rate"], 6
        )
        base["delta_pnl_vs_baseline"] = round(
            (base["avg_pnl_pct"] or 0.0) - base["baseline_avg_pnl_pct"], 6
        )
        if level is None or str(base["level"]).upper() == level.upper():
            rankings.append(base)
    ordered = sort_rankings(rankings)
    logger.info(
        "[watchlist-ranking] refreshed count=%d top_ev_score=%s computed_at=%s",
        len(ordered),
        ordered[0]["ev_score"] if ordered else None,
        ordered[0]["computed_at"].isoformat() if ordered else datetime.now(timezone.utc).isoformat(),
    )
    return ordered

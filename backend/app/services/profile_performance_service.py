"""Daily, read-only profile performance monitor built from shadow trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping, Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..schemas.profile_performance import (
    ProfileDailyPerformancePoint,
    ProfileDailyPerformanceResponse,
    ProfileDailyRange,
    ProfilePerformanceHighlight,
    ProfilePerformanceHighlights,
    ProfilePerformanceHistoryPoint,
    ProfilePerformanceResponse,
    ProfilePerformanceRow,
    ProfilePerformanceSummary,
    ProfileTrendEvidence,
)
from .watchlist_performance_ranking_service import (
    get_ranking_config,
    score_metrics,
    sort_rankings,
)


CONTRACT_VERSION = "profile-performance-v2"
DAILY_CONTRACT_VERSION = "profile-performance-daily-v2"
DISPLAY_TIMEZONE = "UTC"
ALLOWED_RANGE_DAYS = {7, 14, 30}
DAILY_RANGE_DAYS: Dict[ProfileDailyRange, int | None] = {
    "7d": 7,
    "15d": 15,
    "30d": 30,
    "90d": 90,
    "total": None,
}


@dataclass(frozen=True)
class MonitoringPolicy:
    trend_days: int = 7
    trend_min_points: int = 3
    trend_persistence_ratio: float = 0.60
    trend_min_ev_change: float = 1.0
    attention_daily_ev_drop: float = 2.0


DEFAULT_MONITORING_POLICY = MonitoringPolicy()


PROFILE_PERFORMANCE_QUERY = text("""
    WITH days AS (
        SELECT
            day::date AS metric_date,
            day AS day_start,
            day + INTERVAL '1 day' AS day_end
        FROM generate_series(
            CAST(:series_start AS date)::timestamp,
            CAST(:as_of AS date)::timestamp,
            INTERVAL '1 day'
        ) AS day
    ), entities AS (
        SELECT
            pw.profile_id,
            MAX(p.name) AS profile_name,
            CASE WHEN COUNT(DISTINCT pw.id) = 1 THEN MAX(pw.name) ELSE NULL END AS watchlist_name
        FROM pipeline_watchlists AS pw
        JOIN profiles AS p
          ON p.id = pw.profile_id
         AND p.user_id = pw.user_id
        WHERE pw.user_id = :uid
          AND UPPER(pw.level) = 'L3'
          AND (CAST(:profile_id AS uuid) IS NULL OR pw.profile_id = CAST(:profile_id AS uuid))
        GROUP BY pw.profile_id
    ), eligible_trades AS (
        SELECT
            st.*,
            CASE
                WHEN st.status = 'COMPLETED'
                THEN COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at)
                ELSE NULL
            END AS close_at
        FROM shadow_trades AS st
        JOIN pipeline_watchlists AS pw
          ON pw.id = st.watchlist_id
         AND pw.user_id = st.user_id
         AND pw.profile_id = st.profile_id
         AND UPPER(pw.level) = 'L3'
        WHERE st.user_id = :uid
          AND st.profile_id IS NOT NULL
          AND st.source = ANY(CAST(:sources AS text[]))
          AND (CAST(:profile_id AS uuid) IS NULL OR st.profile_id = CAST(:profile_id AS uuid))
    )
    SELECT
        d.metric_date,
        e.profile_id,
        e.profile_name,
        e.watchlist_name,
        COUNT(t.id) FILTER (WHERE t.created_at < d.day_end)::integer AS total_trades,
        COUNT(t.id) FILTER (
            WHERE t.created_at < d.day_end
              AND t.status <> 'ERROR'
              AND (t.close_at IS NULL OR t.close_at >= d.day_end)
        )::integer AS open_trades,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED'
              AND t.pnl_pct IS NOT NULL
              AND t.close_at < d.day_end
        )::integer AS completed_trades,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED'
              AND t.pnl_pct > 0
              AND t.close_at < d.day_end
        )::integer AS wins,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED'
              AND t.pnl_pct > 0
              AND t.holding_seconds IS NOT NULL
              AND t.holding_seconds <= :tp4h_seconds
              AND t.close_at < d.day_end
        )::integer AS tp_4h_wins,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'TP_HIT' AND t.close_at < d.day_end
        )::integer AS tp_count,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'SL_HIT' AND t.close_at < d.day_end
        )::integer AS sl_count,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'TIMEOUT' AND t.close_at < d.day_end
        )::integer AS timeout_count,
        AVG(t.pnl_pct) FILTER (
            WHERE t.status = 'COMPLETED' AND t.pnl_pct IS NOT NULL AND t.close_at < d.day_end
        )::double precision AS avg_pnl_pct,
        COALESCE(SUM(t.pnl_usdt) FILTER (
            WHERE t.status = 'COMPLETED' AND t.pnl_usdt IS NOT NULL AND t.close_at < d.day_end
        ), 0)::double precision AS pnl_total_usdt,
        AVG(t.holding_seconds) FILTER (
            WHERE t.status = 'COMPLETED'
              AND t.pnl_pct > 0
              AND t.holding_seconds IS NOT NULL
              AND t.close_at < d.day_end
        )::double precision AS avg_holding_win_seconds,
        COUNT(t.id) FILTER (
            WHERE t.created_at >= d.day_start AND t.created_at < d.day_end
        )::integer AS daily_trades,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED'
              AND t.pnl_pct IS NOT NULL
              AND t.close_at >= d.day_start AND t.close_at < d.day_end
        )::integer AS daily_closed_trades,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'TP_HIT'
              AND t.close_at >= d.day_start AND t.close_at < d.day_end
        )::integer AS daily_tp,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'SL_HIT'
              AND t.close_at >= d.day_start AND t.close_at < d.day_end
        )::integer AS daily_sl,
        COUNT(t.id) FILTER (
            WHERE t.status = 'COMPLETED' AND t.outcome = 'TIMEOUT'
              AND t.close_at >= d.day_start AND t.close_at < d.day_end
        )::integer AS daily_timeout,
        COALESCE(SUM(t.pnl_usdt) FILTER (
            WHERE t.status = 'COMPLETED' AND t.pnl_usdt IS NOT NULL
              AND t.close_at >= d.day_start AND t.close_at < d.day_end
        ), 0)::double precision AS daily_pnl_usdt,
        MIN(t.created_at) FILTER (WHERE t.created_at < d.day_end) AS first_trade,
        MAX(GREATEST(t.created_at, COALESCE(t.close_at, t.created_at)))
            FILTER (WHERE t.created_at < d.day_end) AS latest_trade
    FROM days AS d
    CROSS JOIN entities AS e
    LEFT JOIN eligible_trades AS t ON t.profile_id = e.profile_id
    GROUP BY d.metric_date, d.day_start, d.day_end,
             e.profile_id, e.profile_name, e.watchlist_name
    ORDER BY e.profile_name, e.profile_id, d.metric_date
""")


PROFILE_DAILY_PERFORMANCE_QUERY = text("""
    WITH eligible_trades AS (
        SELECT
            COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at) AS close_at,
            st.outcome,
            st.pnl_pct,
            st.pnl_usdt
        FROM shadow_trades AS st
        JOIN pipeline_watchlists AS pw
          ON pw.id = st.watchlist_id
         AND pw.user_id = st.user_id
         AND pw.profile_id = st.profile_id
         AND UPPER(pw.level) = 'L3'
        WHERE st.user_id = :uid
          AND st.profile_id IS NOT NULL
          AND st.status = 'COMPLETED'
          AND st.source = ANY(CAST(:sources AS text[]))
          AND COALESCE(st.exit_timestamp, st.completed_at, st.updated_at, st.created_at)
              < CAST(:as_of AS date) + INTERVAL '1 day'
    ), bounds AS (
        SELECT COALESCE(
            CAST(:series_start AS date),
            MIN(close_at)::date,
            CAST(:as_of AS date)
        ) AS series_start
        FROM eligible_trades
    ), days AS (
        SELECT day::date AS metric_date
        FROM generate_series(
            (SELECT series_start FROM bounds)::timestamp,
            CAST(:as_of AS date)::timestamp,
            INTERVAL '1 day'
        ) AS day
    )
    SELECT
        d.metric_date,
        COUNT(t.close_at) FILTER (WHERE t.pnl_pct IS NOT NULL)::integer AS daily_closed_trades,
        COUNT(t.close_at) FILTER (WHERE t.outcome = 'TP_HIT')::integer AS daily_tp,
        COUNT(t.close_at) FILTER (WHERE t.outcome = 'SL_HIT')::integer AS daily_sl,
        COALESCE(SUM(t.pnl_usdt), 0)::double precision AS daily_pnl_usdt
    FROM days AS d
    LEFT JOIN eligible_trades AS t
      ON t.close_at >= d.metric_date::timestamp
     AND t.close_at < d.metric_date::timestamp + INTERVAL '1 day'
    GROUP BY d.metric_date
    ORDER BY d.metric_date
""")


def _int(row: Mapping[str, Any], key: str) -> int:
    return int(row.get(key) or 0)


def _float(row: Mapping[str, Any], key: str) -> float:
    return float(row.get(key) or 0.0)


def _optional_float(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if value is not None else None


def build_profile_daily_performance_response(
    rows: Sequence[Mapping[str, Any]],
    *,
    as_of: date,
    range_key: ProfileDailyRange,
) -> ProfileDailyPerformanceResponse:
    points = []
    for row in rows:
        closed_trades = _int(row, "daily_closed_trades")
        tp_count = _int(row, "daily_tp")
        sl_count = _int(row, "daily_sl")
        decided_trades = tp_count + sl_count
        points.append(ProfileDailyPerformancePoint(
            date=_as_date(row["metric_date"]),
            closed_trades=closed_trades,
            wins=tp_count,
            win_rate=round(tp_count / decided_trades, 6) if decided_trades else None,
            pnl_usdt=round(_float(row, "daily_pnl_usdt"), 4),
        ))
    return ProfileDailyPerformanceResponse(
        contract_version=DAILY_CONTRACT_VERSION,
        as_of=as_of,
        range=range_key,
        timezone=DISPLAY_TIMEZONE,
        points=points,
        metric_definitions={
            "win_rate": "Daily TP_HIT divided by TP_HIT + SL_HIT; TRAILING_STOP and TIMEOUT are excluded.",
            "pnl_day": "Sum of pnl_usdt for L3 trades assigned to the UTC day in which each trade closed.",
        },
    )


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def monitoring_policy_from_config(config: Mapping[str, Any]) -> MonitoringPolicy:
    raw = config.get("monitoring")
    if not isinstance(raw, Mapping):
        return DEFAULT_MONITORING_POLICY

    def number(key: str, default: float) -> float:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"monitoring.{key} must be numeric")
        return float(value)

    policy = MonitoringPolicy(
        trend_days=int(number("trend_days", DEFAULT_MONITORING_POLICY.trend_days)),
        trend_min_points=int(number("trend_min_points", DEFAULT_MONITORING_POLICY.trend_min_points)),
        trend_persistence_ratio=number(
            "trend_persistence_ratio", DEFAULT_MONITORING_POLICY.trend_persistence_ratio
        ),
        trend_min_ev_change=number(
            "trend_min_ev_change", DEFAULT_MONITORING_POLICY.trend_min_ev_change
        ),
        attention_daily_ev_drop=number(
            "attention_daily_ev_drop", DEFAULT_MONITORING_POLICY.attention_daily_ev_drop
        ),
    )
    if policy.trend_days < 2 or policy.trend_min_points < 2:
        raise ValueError("monitoring trend windows must be at least 2")
    if not 0.5 <= policy.trend_persistence_ratio <= 1.0:
        raise ValueError("monitoring.trend_persistence_ratio must be between 0.5 and 1.0")
    if policy.trend_min_ev_change < 0 or policy.attention_daily_ev_drop < 0:
        raise ValueError("monitoring EV thresholds cannot be negative")
    return policy


def calculate_trend(
    history: Sequence[Mapping[str, Any]], policy: MonitoringPolicy
) -> tuple[str, ProfileTrendEvidence]:
    usable = [point for point in history if _int(point, "closed_trades") > 0]
    usable = usable[-policy.trend_days :]
    values = [_float(point, "ev_score") for point in usable]
    if len(values) < policy.trend_min_points:
        return "INSUFFICIENT_DATA", ProfileTrendEvidence(
            points=len(values), slope=0.0, net_change=0.0, positive_days=0, negative_days=0
        )

    count = len(values)
    x_mean = (count - 1) / 2
    y_mean = sum(values) / count
    denominator = sum((index - x_mean) ** 2 for index in range(count))
    slope = (
        sum((index - x_mean) * (value - y_mean) for index, value in enumerate(values))
        / denominator
        if denominator
        else 0.0
    )
    deltas = [current - previous for previous, current in zip(values, values[1:])]
    positive_days = sum(delta > 0 for delta in deltas)
    negative_days = sum(delta < 0 for delta in deltas)
    net_change = values[-1] - values[0]
    persistence_denominator = max(len(deltas), 1)
    positive_ratio = positive_days / persistence_denominator
    negative_ratio = negative_days / persistence_denominator

    evidence = ProfileTrendEvidence(
        points=count,
        slope=round(slope, 4),
        net_change=round(net_change, 2),
        positive_days=positive_days,
        negative_days=negative_days,
    )
    if (
        net_change >= policy.trend_min_ev_change
        and slope > 0
        and positive_ratio >= policy.trend_persistence_ratio
    ):
        return "IMPROVING", evidence
    if (
        net_change <= -policy.trend_min_ev_change
        and slope < 0
        and negative_ratio >= policy.trend_persistence_ratio
    ):
        return "DETERIORATING", evidence
    return "STABLE", evidence


def classify_monitor_status(
    *, sample_status: str, trend: str, priority: str, ev_delta: float | None,
    policy: MonitoringPolicy,
) -> str:
    if sample_status in {"LOW_N", "EMPTY"}:
        return "LOW_SAMPLE"
    if trend == "DETERIORATING":
        return "DETERIORATING"
    if priority in {"C", "D", "BLOCKED"} or (
        ev_delta is not None and ev_delta <= -policy.attention_daily_ev_drop
    ):
        return "ATTENTION"
    if trend == "IMPROVING" or priority in {"A+", "A"}:
        return "POSITIVE"
    return "STABLE"


def _history_point(snapshot: Mapping[str, Any]) -> ProfilePerformanceHistoryPoint:
    return ProfilePerformanceHistoryPoint(
        date=snapshot["date"],
        trades=snapshot["daily_trades"],
        closed_trades=snapshot["daily_closed_trades"],
        tp=snapshot["daily_tp"],
        sl=snapshot["daily_sl"],
        timeout=snapshot["daily_timeout"],
        ev_score=snapshot["ev_score"],
        win_rate=snapshot["win_rate"],
        pnl_usdt=snapshot["daily_pnl_usdt"],
        holding_seconds=snapshot["avg_holding_win_seconds"],
    )


def _highlight(profile: Mapping[str, Any] | None) -> ProfilePerformanceHighlight | None:
    if profile is None:
        return None
    return ProfilePerformanceHighlight(
        profile_id=profile["profile_id"],
        profile_name=profile["profile_name"],
        ev_score=profile["ev_score"],
        ev_delta=profile["ev_delta"],
        ev_period_change=profile["period_ev_change"],
        win_rate=profile["win_rate"],
        pnl_period_usdt=profile["pnl_period_usdt"],
    )


def build_profile_performance_response(
    rows: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    as_of: date,
    range_days: int,
) -> ProfilePerformanceResponse:
    """Build the API response from one batched profile/date aggregation."""

    policy = monitoring_policy_from_config(config)
    history_start = as_of - timedelta(days=range_days - 1)
    previous_date = as_of - timedelta(days=1)
    grouped: Dict[str, list[Dict[str, Any]]] = {}

    for raw in rows:
        tp_count = _int(raw, "tp_count")
        sl_count = _int(raw, "sl_count")
        metrics = {
            "completed_trades": _int(raw, "completed_trades"),
            "wins": _int(raw, "wins"),
            "win_rate_wins": tp_count,
            "win_rate_denominator": tp_count + sl_count,
            "avg_pnl_pct": _optional_float(raw, "avg_pnl_pct"),
            "pnl_total_usdt": _float(raw, "pnl_total_usdt"),
            "tp_4h_wins": _int(raw, "tp_4h_wins"),
            "avg_holding_win_seconds": _optional_float(raw, "avg_holding_win_seconds"),
        }
        scored = score_metrics(metrics, config)
        snapshot = {
            "date": _as_date(raw["metric_date"]),
            "profile_id": raw["profile_id"],
            "profile_name": str(raw.get("profile_name") or "Profile sem nome"),
            "watchlist_name": raw.get("watchlist_name"),
            "total_trades": _int(raw, "total_trades"),
            "open_trades": _int(raw, "open_trades"),
            "completed_trades": metrics["completed_trades"],
            "tp_4h_wins": metrics["tp_4h_wins"],
            "tp": tp_count,
            "sl": sl_count,
            "timeout": _int(raw, "timeout_count"),
            "avg_pnl_pct": metrics["avg_pnl_pct"],
            "pnl_total_usdt": metrics["pnl_total_usdt"],
            "avg_holding_win_seconds": metrics["avg_holding_win_seconds"],
            "daily_trades": _int(raw, "daily_trades"),
            "daily_closed_trades": _int(raw, "daily_closed_trades"),
            "daily_tp": _int(raw, "daily_tp"),
            "daily_sl": _int(raw, "daily_sl"),
            "daily_timeout": _int(raw, "daily_timeout"),
            "daily_pnl_usdt": round(_float(raw, "daily_pnl_usdt"), 4),
            "first_trade": raw.get("first_trade"),
            "latest_trade": raw.get("latest_trade"),
            **scored,
        }
        grouped.setdefault(str(raw["profile_id"]), []).append(snapshot)

    payloads: list[Dict[str, Any]] = []
    available_from_values: list[date] = []
    available_to_values: list[date] = []
    for snapshots in grouped.values():
        snapshots.sort(key=lambda item: item["date"])
        by_date = {item["date"]: item for item in snapshots}
        current = by_date.get(as_of)
        if current is None:
            continue
        previous = by_date.get(previous_date)
        visible_snapshots = [item for item in snapshots if history_start <= item["date"] <= as_of]
        history = [_history_point(item) for item in visible_snapshots]
        trend, trend_evidence = calculate_trend(visible_snapshots, policy)
        period_ev_points = [
            item for item in visible_snapshots if item["completed_trades"] > 0
        ]
        period_ev_change = (
            round(period_ev_points[-1]["ev_score"] - period_ev_points[0]["ev_score"], 2)
            if len(period_ev_points) >= 2 else None
        )
        ev_delta = (
            round(current["ev_score"] - previous["ev_score"], 2)
            if previous is not None else None
        )
        win_rate_delta_pp = (
            round(((current["win_rate"] or 0.0) - (previous["win_rate"] or 0.0)) * 100, 2)
            if previous is not None and current["win_rate"] is not None and previous["win_rate"] is not None
            else None
        )
        pnl_period_usdt = round(sum(item["daily_pnl_usdt"] for item in visible_snapshots), 4)
        trades_period = sum(item["daily_trades"] for item in visible_snapshots)
        closed_period = sum(item["daily_closed_trades"] for item in visible_snapshots)
        status = classify_monitor_status(
            sample_status=current["stat_confidence"],
            trend=trend,
            priority=current["priority"],
            ev_delta=ev_delta,
            policy=policy,
        )
        first_trade = _as_date(current.get("first_trade"))
        latest_trade = _as_date(current.get("latest_trade"))
        if first_trade:
            available_from_values.append(first_trade)
        if latest_trade:
            available_to_values.append(latest_trade)

        payloads.append({
            "profile_id": current["profile_id"],
            "profile_name": current["profile_name"],
            "watchlist_name": current["watchlist_name"],
            "trades": current["total_trades"],
            "closed_trades": current["completed_trades"],
            "open_trades": current["open_trades"],
            "tp": current["tp"],
            "sl": current["sl"],
            "timeout": current["timeout"],
            "previous_tp": previous["tp"] if previous else 0,
            "previous_sl": previous["sl"] if previous else 0,
            "ev_score": current["ev_score"],
            "ev_delta": ev_delta,
            "period_ev_change": period_ev_change,
            "win_rate": current["win_rate"],
            "win_rate_delta_pp": win_rate_delta_pp,
            "pnl_day_usdt": current["daily_pnl_usdt"],
            "pnl_period_usdt": pnl_period_usdt,
            "avg_pnl_pct": current["avg_pnl_pct"],
            "holding_seconds": current["avg_holding_win_seconds"],
            "trend": trend,
            "trend_evidence": trend_evidence,
            "sample_status": current["stat_confidence"],
            "status": status,
            "priority": current["priority"],
            "priority_reason": current["priority_reason"],
            "history": history,
            "trades_period": trades_period,
            "closed_trades_period": closed_period,
            "completed_trades": current["completed_trades"],
            "tp_4h_rate": current["tp_4h_rate"],
            "stat_confidence": current["stat_confidence"],
            "pnl_total_usdt": current["pnl_total_usdt"],
        })

    ranked = sort_rankings(payloads)
    profiles = [
        ProfilePerformanceRow(
            rank=item["rank_position"],
            profile_id=item["profile_id"],
            profile_name=item["profile_name"],
            watchlist_name=item["watchlist_name"],
            trades=item["trades"],
            closed_trades=item["closed_trades"],
            open_trades=item["open_trades"],
            tp=item["tp"],
            sl=item["sl"],
            timeout=item["timeout"],
            ev_score=item["ev_score"],
            ev_delta=item["ev_delta"],
            win_rate=item["win_rate"],
            win_rate_delta_pp=item["win_rate_delta_pp"],
            pnl_day_usdt=item["pnl_day_usdt"],
            pnl_period_usdt=item["pnl_period_usdt"],
            avg_pnl_pct=item["avg_pnl_pct"],
            holding_seconds=item["holding_seconds"],
            trend=item["trend"],
            trend_evidence=item["trend_evidence"],
            sample_status=item["sample_status"],
            status=item["status"],
            priority=item["priority"],
            priority_reason=item["priority_reason"],
            history=item["history"],
        )
        for item in ranked
    ]

    active = [item for item in ranked if item["trades_period"] > 0 or item["closed_trades_period"] > 0]
    total_tp = sum(item["tp"] for item in active)
    total_sl = sum(item["sl"] for item in active)
    previous_tp = sum(item["previous_tp"] for item in active)
    previous_sl = sum(item["previous_sl"] for item in active)
    total_decided = total_tp + total_sl
    previous_decided = previous_tp + previous_sl
    win_rate = total_tp / total_decided if total_decided else None
    previous_win_rate = previous_tp / previous_decided if previous_decided else None
    trusted = [item for item in ranked if item["sample_status"] not in {"LOW_N", "EMPTY"}]
    period_change_pool = [item for item in trusted if item["period_ev_change"] is not None]
    improvement_pool = [item for item in period_change_pool if item["period_ev_change"] > 0]
    deterioration_pool = [item for item in period_change_pool if item["period_ev_change"] < 0]
    pnl_pool = [item for item in ranked if item["trades_period"] > 0 or item["closed_trades_period"] > 0]
    active_ev_deltas = [item["ev_delta"] for item in active if item["ev_delta"] is not None]

    summary = ProfilePerformanceSummary(
        active_profiles=len(active),
        ev_score_mean=round(sum(item["ev_score"] for item in active) / len(active), 2) if active else None,
        ev_score_delta=round(sum(active_ev_deltas) / len(active_ev_deltas), 2)
            if active_ev_deltas else None,
        win_rate=round(win_rate, 6) if win_rate is not None else None,
        win_rate_delta_pp=round((win_rate - previous_win_rate) * 100, 2)
            if win_rate is not None and previous_win_rate is not None else None,
        pnl_day_usdt=round(sum(item["pnl_day_usdt"] for item in ranked), 4),
        pnl_period_usdt=round(sum(item["pnl_period_usdt"] for item in ranked), 4),
        trades_period=sum(item["trades_period"] for item in ranked),
        closed_trades_period=sum(item["closed_trades_period"] for item in ranked),
        alerts=sum(item["status"] in {"ATTENTION", "DETERIORATING"} for item in ranked),
    )
    highlights = ProfilePerformanceHighlights(
        best_profile=_highlight(next((item for item in ranked if item["closed_trades"] > 0), None)),
        biggest_improvement=_highlight(max(improvement_pool, key=lambda item: item["period_ev_change"], default=None)),
        biggest_deterioration=_highlight(min(deterioration_pool, key=lambda item: item["period_ev_change"], default=None)),
        highest_pnl=_highlight(max(pnl_pool, key=lambda item: item["pnl_period_usdt"], default=None)),
    )

    return ProfilePerformanceResponse(
        contract_version=CONTRACT_VERSION,
        as_of=as_of,
        range_days=range_days,
        timezone=DISPLAY_TIMEZONE,
        available_from=min(available_from_values) if available_from_values else None,
        available_to=max(available_to_values) if available_to_values else None,
        summary=summary,
        highlights=highlights,
        profiles=profiles,
        metric_definitions={
            "ev_score": "Canonical DB-backed ranking score accumulated through the selected UTC day.",
            "win_rate": "TP_HIT divided by TP_HIT + SL_HIT; TRAILING_STOP and TIMEOUT are excluded.",
            "pnl_day": "Sum of pnl_usdt assigned to the UTC day in which each trade closed.",
            "pnl_period": "Sum of daily pnl_usdt across the selected 7, 14 or 30 day window.",
            "holding": "Average holding time of completed trades with positive pnl_pct, matching the ranking scorer.",
            "delta": "Selected-day accumulated value minus the accumulated value at D-1.",
            "trend": "Seven-point EV slope plus persistent direction, calculated in the backend.",
            "sample": "Canonical LOW_N, LOW, MEDIUM or HIGH thresholds from watchlist_performance_ranking.",
        },
    )


async def get_profile_performance(
    db: AsyncSession,
    user_id: UUID,
    *,
    as_of: date,
    range_days: int,
    profile_id: UUID | None = None,
) -> ProfilePerformanceResponse:
    if range_days not in ALLOWED_RANGE_DAYS:
        raise ValueError("range_days must be one of 7, 14 or 30")
    if as_of > datetime.now(timezone.utc).date():
        raise ValueError("as_of cannot be in the future")

    config = await get_ranking_config(db, user_id)
    policy = monitoring_policy_from_config(config)
    series_days = max(range_days, policy.trend_days)
    rows = (
        await db.execute(
            PROFILE_PERFORMANCE_QUERY,
            {
                "uid": str(user_id),
                "profile_id": str(profile_id) if profile_id else None,
                "sources": config["source_filter"],
                "series_start": as_of - timedelta(days=series_days),
                "as_of": as_of,
                "tp4h_seconds": int(config["thresholds"]["tp4h_seconds"]),
            },
        )
    ).mappings().all()
    return build_profile_performance_response(rows, config, as_of=as_of, range_days=range_days)


async def get_profile_daily_performance(
    db: AsyncSession,
    user_id: UUID,
    *,
    as_of: date,
    range_key: ProfileDailyRange,
) -> ProfileDailyPerformanceResponse:
    if range_key not in DAILY_RANGE_DAYS:
        raise ValueError("range must be one of 7d, 15d, 30d, 90d or total")
    if as_of > datetime.now(timezone.utc).date():
        raise ValueError("as_of cannot be in the future")

    config = await get_ranking_config(db, user_id)
    range_days = DAILY_RANGE_DAYS[range_key]
    series_start = as_of - timedelta(days=range_days - 1) if range_days else None
    rows = (
        await db.execute(
            PROFILE_DAILY_PERFORMANCE_QUERY,
            {
                "uid": str(user_id),
                "sources": config["source_filter"],
                "series_start": series_start,
                "as_of": as_of,
            },
        )
    ).mappings().all()
    return build_profile_daily_performance_response(
        rows,
        as_of=as_of,
        range_key=range_key,
    )

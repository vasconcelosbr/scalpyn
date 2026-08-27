from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.profile_performance_service import (
    DEFAULT_MONITORING_POLICY,
    build_profile_daily_performance_response,
    build_profile_performance_response,
    calculate_trend,
    monitoring_policy_from_config,
)
from app.services.watchlist_performance_ranking_service import DEFAULT_RANKING_CONFIG


CONFIG = deepcopy(DEFAULT_RANKING_CONFIG)


def raw_row(
    *, profile_id, profile_name, metric_date, closed, wins, avg_pnl, pnl_total,
    daily_trades, daily_closed, daily_pnl,
):
    return {
        "metric_date": metric_date,
        "profile_id": profile_id,
        "profile_name": profile_name,
        "watchlist_name": f"WL {profile_name}",
        "total_trades": closed,
        "open_trades": 0,
        "completed_trades": closed,
        "wins": wins,
        "tp_4h_wins": int(wins * 0.7),
        "tp_count": wins,
        "sl_count": max(closed - wins, 0),
        "timeout_count": 0,
        "avg_pnl_pct": avg_pnl,
        "pnl_total_usdt": pnl_total,
        "avg_holding_win_seconds": 1_200,
        "daily_trades": daily_trades,
        "daily_closed_trades": daily_closed,
        "daily_tp": min(daily_closed, max(wins, 0)),
        "daily_sl": 0,
        "daily_timeout": 0,
        "daily_pnl_usdt": daily_pnl,
        "first_trade": datetime(2026, 7, 1, tzinfo=timezone.utc),
        "latest_trade": datetime.combine(metric_date, datetime.min.time(), tzinfo=timezone.utc),
    }


def test_calculate_trend_requires_persistence_and_exposes_evidence():
    improving = [
        {"closed_trades": 40, "ev_score": value}
        for value in (50.0, 51.5, 52.0, 54.0, 55.0)
    ]
    trend, evidence = calculate_trend(improving, DEFAULT_MONITORING_POLICY)
    assert trend == "IMPROVING"
    assert evidence.slope > 0
    assert evidence.net_change == 5.0

    noisy = [
        {"closed_trades": 40, "ev_score": value}
        for value in (50.0, 52.0, 49.5, 51.0, 50.5)
    ]
    assert calculate_trend(noisy, DEFAULT_MONITORING_POLICY)[0] == "STABLE"


def test_monitoring_policy_is_centralized_and_configurable():
    configured = deepcopy(CONFIG)
    configured["monitoring"] = {
        "trend_days": 5,
        "trend_min_points": 4,
        "trend_persistence_ratio": 0.75,
        "trend_min_ev_change": 2.5,
        "attention_daily_ev_drop": 3.0,
    }
    policy = monitoring_policy_from_config(configured)
    assert policy.trend_days == 5
    assert policy.trend_min_ev_change == 2.5

    configured["monitoring"]["trend_persistence_ratio"] = 0.2
    with pytest.raises(ValueError, match="persistence_ratio"):
        monitoring_policy_from_config(configured)


def test_build_response_keeps_canonical_sample_gate_and_daily_deltas():
    as_of = date(2026, 8, 21)
    strong_id = uuid4()
    small_id = uuid4()
    rows = []
    for index in range(8):
        day = as_of - timedelta(days=7 - index)
        strong_closed = 50 + index * 10
        strong_wins = 30 + index * 8
        rows.append(raw_row(
            profile_id=strong_id,
            profile_name="L3_STRONG",
            metric_date=day,
            closed=strong_closed,
            wins=strong_wins,
            avg_pnl=0.3 + index * 0.1,
            pnl_total=100 + index * 100,
            daily_trades=10,
            daily_closed=10,
            daily_pnl=10.0,
        ))
        rows.append(raw_row(
            profile_id=small_id,
            profile_name="L3_SMALL",
            metric_date=day,
            closed=index + 1,
            wins=index + 1,
            avg_pnl=2.0,
            pnl_total=10 + index,
            daily_trades=1,
            daily_closed=1,
            daily_pnl=1.0,
        ))

    response = build_profile_performance_response(rows, CONFIG, as_of=as_of, range_days=7)

    assert [row.profile_name for row in response.profiles] == ["L3_STRONG", "L3_SMALL"]
    assert response.profiles[0].rank == 1
    assert response.profiles[0].ev_delta is not None
    assert response.profiles[0].ev_delta > 0
    assert response.profiles[0].win_rate_delta_pp is not None
    assert response.profiles[0].tp == 86
    assert response.profiles[0].sl == 34
    assert response.profiles[0].timeout == 0
    assert response.profiles[0].holding_seconds == 1_200
    assert response.profiles[0].pnl_day_usdt == 10.0
    assert response.profiles[0].pnl_period_usdt == 70.0
    assert len(response.profiles[0].history) == 7
    assert response.profiles[1].sample_status == "LOW_N"
    assert response.profiles[1].status == "LOW_SAMPLE"
    assert response.summary.active_profiles == 2
    assert response.summary.trades_period == 77
    assert response.summary.closed_trades_period == 77
    assert response.highlights.best_profile.profile_id == strong_id
    assert response.available_from == date(2026, 7, 1)
    assert response.available_to == as_of


def test_profile_and_summary_win_rate_use_only_tp_and_sl():
    as_of = date(2026, 8, 27)
    row = raw_row(
        profile_id=uuid4(),
        profile_name="L3_TP_SL",
        metric_date=as_of,
        closed=72,
        wins=33,
        avg_pnl=0.1,
        pnl_total=10,
        daily_trades=72,
        daily_closed=72,
        daily_pnl=10,
    )
    row.update(tp_count=33, sl_count=31, timeout_count=8)

    response = build_profile_performance_response([row], CONFIG, as_of=as_of, range_days=7)

    expected = round(33 / (33 + 31), 6)
    assert response.profiles[0].closed_trades == 72
    assert response.profiles[0].win_rate == expected
    assert response.summary.win_rate == expected
    assert "TRAILING_STOP and TIMEOUT are excluded" in response.metric_definitions["win_rate"]


def test_highlights_do_not_label_a_positive_delta_as_deterioration():
    as_of = date(2026, 8, 21)
    profile_id = uuid4()
    rows = [
        raw_row(
            profile_id=profile_id,
            profile_name="L3_ONLY_IMPROVES",
            metric_date=as_of - timedelta(days=1 - index),
            closed=50 + index * 20,
            wins=30 + index * 15,
            avg_pnl=0.5 + index,
            pnl_total=100 + index * 100,
            daily_trades=20,
            daily_closed=20,
            daily_pnl=20.0,
        )
        for index in range(2)
    ]

    response = build_profile_performance_response(rows, CONFIG, as_of=as_of, range_days=7)

    assert response.highlights.biggest_improvement is not None
    assert response.highlights.biggest_improvement.ev_period_change > 0
    assert response.highlights.biggest_deterioration is None


def test_contract_is_read_only_and_endpoint_is_batched():
    service = Path("backend/app/services/profile_performance_service.py").read_text(encoding="utf-8").upper()
    api = Path("backend/app/api/performance_rankings.py").read_text(encoding="utf-8")
    assert "UPDATE PROFILES" not in service
    assert "DELETE FROM" not in service
    assert "GENERATE_SERIES" in service
    assert '"/api/shadow-portfolio/profile-performance"' in api
    assert "range_days" in api
    assert "profile_id" in api


def test_daily_l3_performance_uses_daily_closed_trades_and_preserves_empty_days():
    as_of = date(2026, 8, 21)
    response = build_profile_daily_performance_response(
        [
            {
                "metric_date": as_of - timedelta(days=1),
                "daily_closed_trades": 72,
                "daily_tp": 33,
                "daily_sl": 31,
                "daily_pnl_usdt": 12.5,
            },
            {
                "metric_date": as_of,
                "daily_closed_trades": 8,
                "daily_tp": 0,
                "daily_sl": 0,
                "daily_pnl_usdt": 0,
            },
        ],
        as_of=as_of,
        range_key="15d",
    )

    assert response.range == "15d"
    assert response.points[0].closed_trades == 72
    assert response.points[0].wins == 33
    assert response.points[0].win_rate == round(33 / (33 + 31), 6)
    assert response.points[0].pnl_usdt == 12.5
    assert response.points[1].win_rate is None
    assert response.points[1].pnl_usdt == 0
    assert "TRAILING_STOP and TIMEOUT are excluded" in response.metric_definitions["win_rate"]


def test_daily_contract_is_read_only_and_supports_total_range():
    service = Path("backend/app/services/profile_performance_service.py").read_text(encoding="utf-8").upper()
    api = Path("backend/app/api/performance_rankings.py").read_text(encoding="utf-8")
    assert "PROFILE_DAILY_PERFORMANCE_QUERY" in service
    assert "UPDATE SHADOW_TRADES" not in service
    assert "DELETE FROM SHADOW_TRADES" not in service
    assert '"/api/shadow-portfolio/profile-performance/daily"' in api
    assert '"TOTAL"' in service

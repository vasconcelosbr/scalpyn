from pathlib import Path

from app.services.watchlist_performance_ranking_service import score_metrics, sort_rankings


CONFIG = {
    "version": 1,
    "source_filter": ["L3", "L3_LAB"],
    "weights": {"pnl": 35, "win_rate": 20, "sample": 15, "tp4h": 15, "pnl_total": 10},
    "normalization": {"avg_pnl_pct_target": 1.0, "sample_target": 500, "pnl_total_usdt_target": 1000},
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


def metrics(*, completed=100, wins=60, avg_pnl=0.5, pnl_total=500, tp4h=45, holding=10_000):
    return {
        "completed_trades": completed,
        "wins": wins,
        "avg_pnl_pct": avg_pnl,
        "pnl_total_usdt": pnl_total,
        "tp_4h_wins": tp4h,
        "avg_holding_win_seconds": holding,
    }


def test_ev_score_prioritizes_positive_avg_pnl_and_sample_size():
    reliable = score_metrics(metrics(completed=120, wins=72, tp4h=54), CONFIG)
    small = score_metrics(metrics(completed=35, wins=21, tp4h=16), CONFIG)
    assert reliable["ev_score"] > small["ev_score"]


def test_low_n_penalized_even_with_high_win_rate():
    low_n = score_metrics(metrics(completed=12, wins=12, avg_pnl=2.0, pnl_total=1000, tp4h=12), CONFIG)
    trusted = score_metrics(metrics(completed=300, wins=225, avg_pnl=1.0, pnl_total=1000, tp4h=180), CONFIG)
    assert low_n["priority"] == "LOW_N"
    assert low_n["ev_score"] < trusted["ev_score"]
    assert low_n["ev_score"] <= CONFIG["thresholds"]["low_n_score_cap"]


def test_negative_avg_pnl_penalized():
    positive = score_metrics(metrics(avg_pnl=0.5, pnl_total=500), CONFIG)
    negative = score_metrics(metrics(avg_pnl=-0.5, pnl_total=-500), CONFIG)
    assert negative["ev_score"] < positive["ev_score"]
    assert negative["score_components"]["negative_pnl_penalty"] == 35


def test_good_4h_with_enough_sample_gets_high_priority():
    scored = score_metrics(metrics(completed=300, wins=225, avg_pnl=1.0, pnl_total=1000, tp4h=180), CONFIG)
    assert scored["priority"] == "A+"
    assert scored["stat_confidence"] == "HIGH"
    assert scored["operational_class"] == "GOOD_4H"


def test_shadow_portfolio_default_order_ev_score_desc():
    rows = sort_rankings([
        {"profile_name": "low", "ev_score": 20, "stat_confidence": "HIGH"},
        {"profile_name": "high", "ev_score": 80, "stat_confidence": "HIGH"},
    ])
    assert [row["profile_name"] for row in rows] == ["high", "low"]
    assert [row["rank_position"] for row in rows] == [1, 2]


def test_priority_reason_present():
    assert score_metrics(metrics(), CONFIG)["priority_reason"]


def test_delta_vs_baseline_computed():
    win_rate = score_metrics(metrics(), CONFIG)["win_rate"]
    baseline = 0.50
    assert round((win_rate or 0) - baseline, 6) == 0.10


def test_watchlist_l3_uses_same_performance_order():
    source = Path("backend/app/api/watchlists.py").read_text(encoding="utf-8")
    assert "get_performance_rankings(db, user_id, level=\"L3\")" in source
    assert "performance_priority_order" in source


def test_ranking_scope_is_current_l3_watchlist_profile_pair():
    source = Path("backend/app/services/watchlist_performance_ranking_service.py").read_text(encoding="utf-8")
    assert "JOIN pipeline_watchlists AS watchlist" in source
    assert "watchlist.id = base.watchlist_id" in source
    assert "watchlist.profile_id = base.profile_id" in source
    assert "UPPER(watchlist.level) = 'L3'" in source


def test_l3_api_uses_same_performance_order():
    source = Path("backend/app/api/performance_rankings.py").read_text(encoding="utf-8")
    assert '"/api/l3/watchlists"' in source
    assert 'return await _rankings(db, user_id, level="L3")' in source


def test_no_live_or_profile_mutation():
    source = Path("backend/app/services/watchlist_performance_ranking_service.py").read_text(encoding="utf-8").upper()
    assert "UPDATE PROFILES" not in source
    assert "LIVE_TRADING_ENABLED" not in source
    assert "MUTATION_APPLIED" not in source
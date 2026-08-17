"""Before shadow_portfolio_handlers.py, 10 of the 11 shadow_portfolio AI
tools shared one generic row-dump handler -- confirmed 2026-08-17 against a
live root-cause-audit request where shadow.get_mae_mfe, get_score_buckets,
get_data_quality, get_delayed_tp, compare_champion_candidate,
get_experiment_status, get_experiment_result, get_outcome_horizons,
get_profile_performance and get_performance_summary all returned the exact
same 20-row array (identical data hash). These tests pin each handler to a
real, distinct computation so that regression can't silently return to the
shared-stub behaviour.
"""
from __future__ import annotations

from app.ai_orchestration.shadow_portfolio_handlers import (
    SHADOW_PORTFOLIO_HANDLERS,
    compare_champion_candidate,
    data_quality,
    delayed_tp,
    mae_mfe,
    no_experiment_data,
    outcome_horizons,
    performance_summary,
    profile_performance,
    score_buckets,
)
from app.ai_orchestration.tool_registry import SideEffect, ToolCapability

DATASET_HASH = "hash-abc"
DATASET_WINDOW_END = "2026-08-17T00:00:00+00:00"


def _capability(name: str) -> ToolCapability:
    return ToolCapability(
        name=name, version="1.0.0", domain="shadow_portfolio",
        input_schema={}, output_schema={}, side_effect=SideEffect.NONE,
        max_runtime_seconds=30, freshness_sla_seconds=900,
    )


def _row(**overrides) -> dict:
    base = {
        "id": "row-1", "status": "COMPLETED", "outcome": "TP_HIT",
        "profile_id": "profile-a", "pnl_pct": 1.5, "pnl_usdt": 15.0,
        "mae_pct": -0.5, "mfe_pct": 1.8, "delayed_tp": None, "delayed_tp_hours": None,
        "timeout_post_analysis_done": False,
        "horizon_change_pct": {"1h": None, "2h": None, "4h": None, "12h": None, "24h": None},
        "final_score": 75.0, "features_coverage": 0.95, "market_data_confidence": 0.9,
        "oldest_indicator_age_s": 30, "eligible_for_training": True,
        "lineage_status": "CANONICAL",
    }
    base.update(overrides)
    return base


def test_all_eleven_tool_names_are_distinct_across_the_catalog():
    """freeze_analysis_dataset intentionally falls back to the raw-row
    reader (module_tool_runtime.py) -- everything else must have its own
    entry here, one function per name."""
    expected = {
        "shadow.get_performance_summary", "shadow.get_profile_performance",
        "shadow.get_score_buckets", "shadow.get_mae_mfe", "shadow.get_delayed_tp",
        "shadow.get_outcome_horizons", "shadow.get_data_quality",
        "shadow.compare_champion_candidate", "shadow.get_experiment_status",
        "shadow.get_experiment_result",
    }
    assert set(SHADOW_PORTFOLIO_HANDLERS) == expected
    # get_experiment_status/result are legitimately the same function (see
    # no_experiment_data's docstring) -- everything else must be unique.
    handlers = dict(SHADOW_PORTFOLIO_HANDLERS)
    del handlers["shadow.get_experiment_status"]
    del handlers["shadow.get_experiment_result"]
    assert len(set(id(fn) for fn in handlers.values())) == len(handlers)


def test_handlers_on_the_same_rows_produce_different_data_shapes():
    """The actual regression this module fixes: same input, different
    tools must not collapse to the same output."""
    rows = [
        _row(id="1", profile_id="a", outcome="TP_HIT", pnl_pct=1.0),
        _row(id="2", profile_id="b", outcome="SL_HIT", pnl_pct=-1.0, mae_pct=-2.5, mfe_pct=1.5),
    ]
    cap = _capability("x")
    outputs = [
        performance_summary(cap, rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END),
        profile_performance(cap, rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END),
        mae_mfe(cap, rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END),
        data_quality(cap, rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END),
    ]
    data_shapes = [set(o["data"].keys()) for o in outputs]
    for i in range(len(data_shapes)):
        for j in range(i + 1, len(data_shapes)):
            assert data_shapes[i] != data_shapes[j], f"handlers {i} and {j} produced the same data shape"


def test_performance_summary_computes_real_win_rate():
    rows = [
        _row(id="1", outcome="TP_HIT", status="COMPLETED"),
        _row(id="2", outcome="TP_HIT", status="COMPLETED"),
        _row(id="3", outcome="SL_HIT", status="COMPLETED"),
        _row(id="4", outcome=None, status="PENDING", pnl_pct=None),
    ]
    out = performance_summary(_capability("shadow.get_performance_summary"), rows=rows,
                               dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["total"] == 4
    assert out["data"]["completed"] == 3
    assert out["data"]["pending"] == 1
    assert out["data"]["win"] == 2
    assert out["data"]["loss"] == 1
    assert out["data"]["win_rate_pct"] == round(2 / 3 * 100, 2)


def test_performance_summary_empty_is_no_data():
    out = performance_summary(_capability("x"), rows=[], dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["quality"] == "NO_DATA"
    assert out["data"]["total"] == 0


def test_profile_performance_ranks_by_win_rate_best_first():
    rows = [
        _row(id="1", profile_id="low", outcome="SL_HIT"),
        _row(id="2", profile_id="low", outcome="SL_HIT"),
        _row(id="3", profile_id="high", outcome="TP_HIT"),
        _row(id="4", profile_id="high", outcome="TP_HIT"),
    ]
    out = profile_performance(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    profiles = out["data"]["profiles"]
    assert profiles[0]["profile_id"] == "high"
    assert profiles[0]["win_rate_pct"] == 100.0
    assert profiles[1]["profile_id"] == "low"
    assert profiles[1]["win_rate_pct"] == 0.0


def test_score_buckets_splits_into_quartiles_by_rank_not_fixed_range():
    rows = [_row(id=str(i), final_score=float(i * 5)) for i in range(1, 21)]  # scores 5..100
    out = score_buckets(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    quartiles = out["data"]["quartiles"]
    assert len(quartiles) == 4
    assert sum(q["sample_size"] for q in quartiles) == 20
    assert quartiles[0]["score_min"] < quartiles[-1]["score_min"]


def test_score_buckets_flags_missing_final_score():
    rows = [_row(id="1", final_score=None), _row(id="2", final_score=50.0)]
    out = score_buckets(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["quality"] == "PASS_WITH_MISSINGNESS"
    assert "final_score" in out["missingness"]


def test_mae_mfe_near_sl_winners_and_recovery():
    rows = [
        _row(id="1", outcome="TP_HIT", mae_pct=-3.0, mfe_pct=2.0),  # near_sl_winner
        _row(id="2", outcome="TP_HIT", mae_pct=-0.2, mfe_pct=1.0),
        _row(id="3", outcome="SL_HIT", mae_pct=-1.0, mfe_pct=1.5),  # sl_after_strong_mfe
    ]
    out = mae_mfe(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["near_sl_winners_pct"] == 50.0  # 1 of 2 TP_HIT
    assert out["data"]["sl_after_strong_mfe_pct"] == 100.0  # 1 of 1 SL_HIT
    assert out["data"]["tp"]["count"] == 2
    assert out["data"]["sl"]["count"] == 1


def test_delayed_tp_scoped_to_timeout_only():
    rows = [
        _row(id="1", outcome="TIMEOUT", status="COMPLETED", timeout_post_analysis_done=True, delayed_tp=True, delayed_tp_hours=3.5),
        _row(id="2", outcome="TIMEOUT", status="COMPLETED", timeout_post_analysis_done=True, delayed_tp=False),
        _row(id="3", outcome="TP_HIT", status="COMPLETED"),  # excluded, not a timeout
    ]
    out = delayed_tp(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["total_timeouts"] == 2
    assert out["data"]["delayed_tp_count"] == 1
    assert out["data"]["timeout_recovery_rate_pct"] == 50.0
    assert out["data"]["avg_delayed_tp_hours"] == 3.5


def test_outcome_horizons_averages_only_timeout_rows():
    rows = [
        _row(id="1", outcome="TIMEOUT", horizon_change_pct={"1h": 2.0, "2h": None, "4h": None, "12h": None, "24h": None}),
        _row(id="2", outcome="TIMEOUT", horizon_change_pct={"1h": 4.0, "2h": None, "4h": None, "12h": None, "24h": None}),
        _row(id="3", outcome="TP_HIT", horizon_change_pct={"1h": 999.0, "2h": None, "4h": None, "12h": None, "24h": None}),
    ]
    out = outcome_horizons(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["avg_price_change_pct"]["1h"] == 3.0  # (2+4)/2, TP_HIT row excluded


def test_data_quality_reports_lineage_and_coverage():
    rows = [
        _row(id="1", lineage_status="CANONICAL", eligible_for_training=True, features_coverage=1.0),
        _row(id="2", lineage_status="UNRESOLVED", eligible_for_training=False, features_coverage=0.5),
    ]
    out = data_quality(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["canonical_lineage_pct"] == 50.0
    assert out["data"]["eligible_for_training_pct"] == 50.0
    assert out["data"]["avg_features_coverage"] == 0.75


def test_compare_champion_candidate_needs_two_profiles():
    rows = [_row(id="1", profile_id="only-one")]
    out = compare_champion_candidate(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["quality"] == "PASS_WITH_MISSINGNESS"
    assert out["data"]["champion"]["profile_id"] == "only-one"
    assert out["data"]["candidates"] == []


def test_compare_champion_candidate_ranks_two_profiles():
    rows = [
        _row(id="1", profile_id="strong", outcome="TP_HIT"),
        _row(id="2", profile_id="strong", outcome="TP_HIT"),
        _row(id="3", profile_id="weak", outcome="SL_HIT"),
        _row(id="4", profile_id="weak", outcome="TP_HIT"),
    ]
    out = compare_champion_candidate(_capability("x"), rows=rows, dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["data"]["champion"]["profile_id"] == "strong"
    assert out["data"]["champion"]["role"] == "CHAMPION"
    assert len(out["data"]["candidates"]) == 1
    assert out["data"]["candidates"][0]["profile_id"] == "weak"
    assert out["data"]["candidates"][0]["win_rate_pct_vs_champion"] < 0


def test_no_experiment_data_is_honest_not_fabricated():
    """Regression target: this must NOT return the trade rows it's given --
    that was exactly the bug (get_experiment_status/result silently
    returning the shadow_trades population as if it were experiment data)."""
    rows = [_row(id="1"), _row(id="2")]
    out = no_experiment_data(_capability("shadow.get_experiment_status"), rows=rows,
                              dataset_hash=DATASET_HASH, dataset_window_end=DATASET_WINDOW_END)
    assert out["quality"] == "NO_DATA"
    assert out["data"] == {"experiments_found": 0}
    assert out["evidence_ids"] == []

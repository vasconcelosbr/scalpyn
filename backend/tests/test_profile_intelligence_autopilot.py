from app.services.profile_intelligence_autopilot_service import (
    DEFAULT_AUTOPILOT_SETTINGS,
    canonical_signature,
    evaluation_ready,
    promotion_decision,
    rollback_required,
    semantic_rules_equivalent,
)


def test_semantic_dedup_is_order_independent_and_uses_relative_tolerance():
    left = [
        {"field": "RSI", "operator": ">=", "value": 70},
        {"field": "ADX", "operator": ">", "value": 25},
    ]
    right = [
        {"indicator": "adx_14", "operator": "gt", "value": 27},
        {"indicator": "relative_strength_index", "operator": "gte", "value": 65},
    ]
    assert semantic_rules_equivalent(left, right, tolerance=0.20)
    assert canonical_signature(left) == canonical_signature(list(reversed(left)))
    assert canonical_signature([left[0]]) == canonical_signature([right[1]])


def test_semantic_dedup_preserves_indicator_operator_and_direction():
    base = [{"field": "rsi", "operator": ">=", "value": 70, "direction": "spot"}]
    assert not semantic_rules_equivalent(
        base,
        [{"field": "adx", "operator": ">=", "value": 70, "direction": "spot"}],
    )
    assert not semantic_rules_equivalent(
        base,
        [{"field": "rsi", "operator": "<=", "value": 70, "direction": "spot"}],
    )
    assert not semantic_rules_equivalent(
        base,
        [{"field": "rsi", "operator": ">=", "value": 70, "direction": "short"}],
    )


def test_review_window_requires_50_trades_after_36_hours():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert not evaluation_ready(49, 100, settings)
    assert evaluation_ready(50, 36, settings)
    assert evaluation_ready(100, 1, settings)


def test_promotion_requires_both_minimums():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.80,
        avg_pnl_pct=0.005,
        settings=settings,
    )[0] == "APPROVE"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.79,
        avg_pnl_pct=0.01,
        settings=settings,
    )[0] == "REJECT"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.90,
        avg_pnl_pct=0.0049,
        settings=settings,
    )[0] == "REJECT"


def test_incumbent_must_improve_one_without_degrading_other():
    settings = DEFAULT_AUTOPILOT_SETTINGS
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.85,
        avg_pnl_pct=0.006,
        incumbent_exists=True,
        incumbent_win_rate=0.82,
        incumbent_avg_pnl_pct=0.006,
        settings=settings,
    )[0] == "APPROVE"
    assert promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.85,
        avg_pnl_pct=0.005,
        incumbent_exists=True,
        incumbent_win_rate=0.82,
        incumbent_avg_pnl_pct=0.006,
        settings=settings,
    )[0] == "REJECT"


def test_missing_metrics_never_causes_decision():
    decision, _ = promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=None,
        avg_pnl_pct=0.01,
        settings=DEFAULT_AUTOPILOT_SETTINGS,
    )
    assert decision == "INSUFFICIENT_EVIDENCE"
    incumbent_decision, _ = promotion_decision(
        trades=100,
        elapsed_hours=1,
        win_rate=0.90,
        avg_pnl_pct=0.01,
        incumbent_exists=True,
        incumbent_win_rate=None,
        incumbent_avg_pnl_pct=0.01,
        settings=DEFAULT_AUTOPILOT_SETTINGS,
    )
    assert incumbent_decision == "INSUFFICIENT_EVIDENCE"
    assert not rollback_required(None, 0.01, 0.80, 0.01, 0.80)


def test_rollback_on_either_metric_below_relative_floor():
    assert rollback_required(0.639, 0.01, 0.80, 0.01, 0.80)
    assert rollback_required(0.80, 0.0079, 0.80, 0.01, 0.80)
    assert not rollback_required(0.64, 0.008, 0.80, 0.01, 0.80)

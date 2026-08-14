from app._critical_schema import CRITICAL_COLUMNS


def test_critical_schema_includes_indicators_columns() -> None:
    assert ("indicators", "scheduler_group") in CRITICAL_COLUMNS
    assert ("indicators", "market_type") in CRITICAL_COLUMNS


def test_critical_schema_includes_causal_shadow_lineage() -> None:
    assert ("shadow_trades", "feature_source_at") in CRITICAL_COLUMNS
    assert ("shadow_trades", "feature_source_times") in CRITICAL_COLUMNS


def test_critical_schema_includes_durable_graph_dispatch() -> None:
    assert ("ai_graph_runs", "dispatch_kind") in CRITICAL_COLUMNS
    assert ("ai_graph_runs", "dispatch_interrupt_id") in CRITICAL_COLUMNS
    assert ("ai_graph_runs", "dispatch_decision_id") in CRITICAL_COLUMNS

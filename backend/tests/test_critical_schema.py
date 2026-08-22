from app._critical_schema import CRITICAL_COLUMNS


def test_critical_schema_includes_indicators_columns() -> None:
    assert ("indicators", "scheduler_group") in CRITICAL_COLUMNS
    assert ("indicators", "market_type") in CRITICAL_COLUMNS


def test_critical_schema_includes_causal_shadow_lineage() -> None:
    assert ("shadow_trades", "feature_source_at") in CRITICAL_COLUMNS
    assert ("shadow_trades", "feature_source_times") in CRITICAL_COLUMNS


def test_critical_schema_includes_durable_l3_gate_v2_capture() -> None:
    assert (
        "l3_gate_v2_evaluations",
        "evaluation_envelope_hash",
    ) in CRITICAL_COLUMNS
    assert ("l3_gate_v2_evaluations", "operational_effect") in CRITICAL_COLUMNS
    assert ("l3_gate_v2_evaluations", "payload") in CRITICAL_COLUMNS


def test_critical_schema_includes_durable_graph_dispatch() -> None:
    assert ("ai_graph_runs", "dispatch_kind") in CRITICAL_COLUMNS
    assert ("ai_graph_runs", "dispatch_interrupt_id") in CRITICAL_COLUMNS
    assert ("ai_graph_runs", "dispatch_decision_id") in CRITICAL_COLUMNS

"""Tests for metric contract definitions and labeling invariants.

Ensures metric contracts are present, correctly labeled, and that
bucket-level vs trade-level metrics are never presented as equivalent.
"""

import pytest
from app.services.metric_contracts import (
    build_metric_contract,
    SHADOW_SOURCE_CONTRACT,
)


# ── SHADOW_SOURCE_CONTRACT ────────────────────────────────────────────────────

def test_shadow_source_contract_maps_all_five_tabs():
    expected = {"L3", "L3_REJECTED", "L3_SIMULATED", "L1_SPECTRUM", "L3_LAB"}
    assert set(SHADOW_SOURCE_CONTRACT.keys()) == expected


def test_shadow_source_contract_has_required_fields():
    required = {"view", "tab", "description", "purpose", "sql_filter"}
    for source, contract in SHADOW_SOURCE_CONTRACT.items():
        missing = required - set(contract.keys())
        assert not missing, f"{source} missing: {missing}"


def test_shadow_source_contract_l3_is_approved_tab():
    assert SHADOW_SOURCE_CONTRACT["L3"]["tab"] == "Aprovados"
    assert "ALLOW" in SHADOW_SOURCE_CONTRACT["L3"]["description"]


def test_shadow_source_contract_l3_rejected_is_rejected_tab():
    assert SHADOW_SOURCE_CONTRACT["L3_REJECTED"]["tab"] == "Rejeitados"


def test_shadow_source_contract_l3_lab_is_strategy_lab():
    assert SHADOW_SOURCE_CONTRACT["L3_LAB"]["tab"] == "Strategy Lab"


def test_shadow_source_contract_l1_spectrum_is_dataset_ml():
    assert SHADOW_SOURCE_CONTRACT["L1_SPECTRUM"]["tab"] == "Dataset ML"


# ── build_metric_contract ─────────────────────────────────────────────────────

def test_build_metric_contract_returns_required_fields():
    mc = build_metric_contract(
        metric_id="test.metric",
        label="Test Metric",
        source_table="shadow_trades",
        aggregation_type="trade_level",
        aggregation_level="per_trade",
        formula="COUNT(wins)/COUNT(*)",
    )
    required = {"metric_id", "label", "source_table", "aggregation", "filters",
                "comparable_with", "not_comparable_with", "is_snapshot", "shadow_sources"}
    assert required.issubset(mc.keys())


def test_build_metric_contract_snapshot_flag():
    mc = build_metric_contract(
        metric_id="overview.win_rate",
        label="Win Rate Base",
        source_table="profile_intelligence_runs",
        aggregation_type="trade_level",
        aggregation_level="per_trade",
        formula="TP_HIT/(TP+SL+TIMEOUT)",
        is_snapshot=True,
        snapshot_computed_at="2026-06-27T14:45:00",
    )
    assert mc["is_snapshot"] is True
    assert mc["snapshot_computed_at"] == "2026-06-27T14:45:00"


def test_build_metric_contract_window_present_when_label_given():
    mc = build_metric_contract(
        metric_id="calibration.bucket_win_rate",
        label="Win Rate Buckets",
        source_table="profile_indicator_performance",
        aggregation_type="simple_avg",
        aggregation_level="indicator_bucket",
        formula="AVG(win_rate)",
        window_label="48h",
        window_hours=48,
        window_field="created_at",
    )
    assert mc["window"]["label"] == "48h"
    assert mc["window"]["window_hours"] == 48
    assert mc["window"]["field"] == "created_at"


def test_build_metric_contract_warning_propagated():
    mc = build_metric_contract(
        metric_id="calibration.bucket_pnl",
        label="P&L Buckets",
        source_table="profile_indicator_performance",
        aggregation_type="simple_avg",
        aggregation_level="indicator_bucket",
        formula="AVG(avg_pnl_pct)",
        warning="Not portfolio P&L",
    )
    assert "warning" in mc
    assert mc["warning"] == "Not portfolio P&L"


def test_build_metric_contract_no_window_when_no_label():
    mc = build_metric_contract(
        metric_id="calibration.suggestions",
        label="Suggestions",
        source_table="profile_adjustment_suggestions",
        aggregation_type="count",
        aggregation_level="row",
        formula="COUNT(*)",
    )
    assert "window" not in mc


# ── Labeling invariants ───────────────────────────────────────────────────────

def test_bucket_win_rate_not_comparable_with_trade_level():
    mc = build_metric_contract(
        metric_id="calibration.bucket_avg_win_rate",
        label="Win Rate Buckets (48h)",
        source_table="profile_indicator_performance",
        aggregation_type="simple_avg",
        aggregation_level="indicator_bucket",
        formula="AVG(win_rate)",
        not_comparable_with=[
            "calibration.portfolio_win_rate",
            "overview.run_snapshot_win_rate",
            "shadow.trade_level_win_rate",
        ],
    )
    assert "calibration.portfolio_win_rate" in mc["not_comparable_with"]
    assert "overview.run_snapshot_win_rate" in mc["not_comparable_with"]
    assert "shadow.trade_level_win_rate" in mc["not_comparable_with"]


def test_bucket_pnl_not_comparable_with_portfolio_pnl():
    mc = build_metric_contract(
        metric_id="calibration.bucket_avg_pnl_pct",
        label="P&L Buckets (48h)",
        source_table="profile_indicator_performance",
        aggregation_type="simple_avg",
        aggregation_level="indicator_bucket",
        formula="AVG(avg_pnl_pct)",
        not_comparable_with=[
            "calibration.portfolio_avg_pnl_pct",
            "shadow.trade_level_avg_pnl_pct",
        ],
    )
    assert "calibration.portfolio_avg_pnl_pct" in mc["not_comparable_with"]
    assert "shadow.trade_level_avg_pnl_pct" in mc["not_comparable_with"]


def test_suggestions_registered_not_comparable_with_overview_pending():
    mc = build_metric_contract(
        metric_id="calibration.suggestions_registered",
        label="Sugestões Registradas",
        source_table="profile_adjustment_suggestions",
        aggregation_type="count",
        aggregation_level="row",
        formula="COUNT(*)",
        not_comparable_with=["overview.run_suggestions_pending"],
        warning="Diferente de profile_suggestions (PI Engine legado).",
    )
    assert "overview.run_suggestions_pending" in mc["not_comparable_with"]
    assert "profile_suggestions" in mc["warning"]


# ── Safety: no trading mutation side effects ──────────────────────────────────

def test_metric_contracts_module_has_no_write_sql():
    import inspect
    import app.services.metric_contracts as mc_module
    source = inspect.getsource(mc_module)
    for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE TABLE"):
        assert keyword not in source.upper(), (
            f"metric_contracts.py contains write SQL: {keyword}"
        )


def test_shadow_source_contract_sql_filters_are_read_only():
    for source, contract in SHADOW_SOURCE_CONTRACT.items():
        sql = contract["sql_filter"].upper()
        for keyword in ("INSERT", "UPDATE", "DELETE", "DROP"):
            assert keyword not in sql, (
                f"{source}.sql_filter contains write keyword: {keyword}"
            )

"""AUD-001/AUD-002 (auditoria shadow SL_HIT 2026-09-11): regression coverage
for the two confirmed, correctable findings, refined per follow-up review:

1. ``timeframe_conflict=true`` alone must never gate anything -- the same
   indicator can legitimately exist at 5m/30m/1h. The operational signal is
   "a condition requested one timeframe and a different one was selected"
   (``mismatch``), scoped to indicators that have a candle-timeframe
   identity at all.
2. ``default_timeframe`` must not be applied indiscriminately: composite
   scores, live order-book snapshots and rolling-flow windows have no
   "5m vs 30m" identity to compare against.

AUD-002: an indicator merged from conflicting timeframes (e.g. bb_width
resolved from a 30m row when the profile's default_timeframe is 5m) must be
visible in decision_audit even though it is not yet enforced (see
profile_engine._build_eval_data for why blanking it out would make entry
*more* permissive, not less, given BlockEngine/SignalEngine's SKIPPED
semantics).

AUD-001: a profile with both entry_triggers and signals populated must warn
at save time, since entry_triggers silently shadows signals entirely.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.indicator_classifier import timeframe_semantics
from app.services.profile_engine import (
    ProfileEngine,
    condition_timeframe_evidence,
    indicator_timeframe_conflicts,
)
from app.services.profile_config_validation import profile_config_warnings
from app.utils.indicator_merge import merge_indicator_rows


def _profile(**overrides):
    base = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "scoring": {"enabled": True, "weights": {}, "rules": [], "selected_rule_ids": []},
        "signals": {"logic": "AND", "conditions": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
    }
    base.update(overrides)
    return base


def _merged_asset(symbol: str, rows) -> dict:
    now = rows[-1][1] + timedelta(seconds=1)
    merged = merge_indicator_rows(list(rows), now=now)
    return {"symbol": symbol, "indicators": merged.as_flat_dict(), "_merged_indicators": merged}


# ── Point 2: which indicators even have a candle-timeframe identity ─────────

def test_timeframe_semantics_classifies_candle_vs_non_candle_indicators():
    # Candle-derived: legitimately comparable against a requested timeframe.
    for name in ("rsi", "macd_histogram", "adx", "bb_width", "atr", "ema9", "stoch_k", "volume_spike"):
        assert timeframe_semantics(name) == "CANDLE_TIMEFRAME", name
    # Rolling window: live-injected from a trade-tape window, not a candle.
    for name in ("taker_ratio", "volume_delta", "buy_pressure"):
        assert timeframe_semantics(name) == "ROLLING_WINDOW", name
    # Live snapshot: point-in-time order book / ticker, not a candle aggregate.
    for name in ("orderbook_pressure", "bid_ask_imbalance", "spread_pct", "orderbook_depth_usdt"):
        assert timeframe_semantics(name) == "LIVE_SNAPSHOT", name
    # Composite: a formula over other fields, no timeframe of its own.
    for name in ("score", "momentum_score", "liquidity_score", "signal_score"):
        assert timeframe_semantics(name) == "COMPOSITE", name


# ── Point 1: mismatch (requested vs selected), not bare conflict ────────────

def test_condition_timeframe_evidence_flags_real_mismatch():
    """Reproduces T04/T06: bb_width merged from a 30m row winning over a 5m
    row under the same 'structural' scheduler group, while the condition
    asked for 5m."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=25), "30m", {"bb_width": 0.054002}),
        ("structural", now - timedelta(minutes=2), "5m", {"bb_width": 0.02254}),
    ])
    evidence = condition_timeframe_evidence("bb_width", "5m", asset)
    assert evidence is not None
    assert evidence["requested_timeframe"] == "5m"
    assert evidence["selected_timeframe"] == "5m"  # microstructure-freshness wins here
    assert evidence["available_timeframes"] == ["30m", "5m"]
    assert evidence["timeframe_conflict"] is True


def test_condition_timeframe_evidence_mismatch_true_when_selected_differs():
    """When the 30m row is the fresher one, it wins the merge -- that IS the
    T04/T06 bug: a 5m-requesting condition silently gets the 30m value."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=25), "5m", {"bb_width": 0.02254}),
        ("structural", now - timedelta(minutes=2), "30m", {"bb_width": 0.054002}),
    ])
    evidence = condition_timeframe_evidence("bb_width", "5m", asset)
    assert evidence["selected_timeframe"] == "30m"
    assert evidence["mismatch"] is True
    assert evidence["selection_reason"] == "LATEST_TIMESTAMP_WINS"


def test_condition_timeframe_evidence_no_mismatch_when_timeframes_agree():
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=2), "5m", {"bb_width": 0.02254}),
    ])
    evidence = condition_timeframe_evidence("bb_width", "5m", asset)
    assert evidence["timeframe_conflict"] is False
    assert evidence["mismatch"] is False


def test_condition_timeframe_evidence_none_for_non_candle_indicators():
    """A rolling-window/composite/live-snapshot field is a category error to
    compare against "requested timeframe" -- must return None, not a
    misleading mismatch=False that implies the comparison was meaningful."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=25), "1h", {"taker_ratio": 0.6}),
        ("microstructure", now - timedelta(minutes=2), "5m", {"taker_ratio": 0.8}),
    ])
    assert condition_timeframe_evidence("taker_ratio", "5m", asset) is None
    assert condition_timeframe_evidence("momentum_score", "5m", {"indicators": {}}) is None


def test_condition_timeframe_evidence_none_without_merged_indicators():
    assert condition_timeframe_evidence("bb_width", "5m", {"indicators": {"bb_width": 1}}) is None
    assert condition_timeframe_evidence("bb_width", "5m", {}) is None


# ── Fase 1 wiring: eval_data / asset carry the evidence, values unchanged ───

def test_build_eval_data_still_reports_raw_conflicts_unchanged():
    """The coarser, asset-level signal (indicator_timeframe_conflicts) keeps
    working exactly as before -- it is a valid lower-level building block,
    just not precise enough to gate anything by itself (see Point 1)."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=25), "30m", {"bb_width": 0.054002}),
        ("structural", now - timedelta(minutes=2), "5m", {"bb_width": 0.02254}),
    ])
    conflicts = indicator_timeframe_conflicts(asset)
    assert "bb_width" in conflicts

    engine = ProfileEngine(_profile())
    eval_data = engine._build_eval_data(asset)
    assert eval_data["bb_width"] == asset["indicators"]["bb_width"]  # value untouched
    assert "bb_width" in eval_data["_timeframe_conflicts"]


def test_entry_trigger_evaluation_records_condition_level_evidence_on_asset():
    """End-to-end: an entry_triggers condition on a mismatched candle
    indicator leaves a record on asset["_condition_timeframe_evidence"],
    exactly what pipeline_scan._decision_metrics exposes as
    decision_audit.metrics.timeframe_integrity."""
    now = datetime(2026, 9, 11, 7, 20, tzinfo=timezone.utc)
    asset = _merged_asset("NEAR_USDT", [
        ("structural", now - timedelta(minutes=25), "5m", {"bb_width": 0.02254}),
        ("structural", now - timedelta(minutes=2), "30m", {"bb_width": 0.054002}),
    ])
    asset["indicators"].setdefault("rsi", 50)
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"indicator": "bb_width", "operator": "between", "min": 0.03, "max": 0.06,
         "enabled": True, "required": True},
    ]})
    engine = ProfileEngine(profile)
    engine.evaluate_asset(asset)
    records = asset.get("_condition_timeframe_evidence") or []
    matches = [r for r in records if r["indicator"] == "bb_width" and r["section"] == "entry_triggers"]
    assert len(matches) == 1
    assert matches[0]["mismatch"] is True
    assert matches[0]["requested_timeframe"] == "5m"
    assert matches[0]["selected_timeframe"] == "30m"


# ── AUD-001: entry_triggers silently shadowing signals ──────────────────────

def test_profile_config_warnings_flags_entry_triggers_shadowing_signals():
    """Reproduces T00/T01: signals.momentum_score>=56 never runs because
    entry_triggers has its own, unrelated conditions."""
    config = _profile(
        entry_triggers={"logic": "AND", "conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55, "enabled": True, "required": True},
        ]},
        signals={"logic": "AND", "conditions": [
            {"field": "momentum_score", "operator": ">=", "value": 56},
        ]},
    )
    warnings = profile_config_warnings(config)
    assert len(warnings) == 1
    assert "entry_triggers" in warnings[0] and "signals" in warnings[0]


def test_profile_config_warnings_silent_when_only_one_section_populated():
    only_entry_triggers = _profile(
        entry_triggers={"logic": "AND", "conditions": [
            {"indicator": "taker_ratio", "operator": ">=", "value": 0.55, "enabled": True, "required": True},
        ]},
    )
    assert profile_config_warnings(only_entry_triggers) == []

    only_signals = _profile(
        signals={"logic": "AND", "conditions": [
            {"field": "momentum_score", "operator": ">=", "value": 56},
        ]},
    )
    assert profile_config_warnings(only_signals) == []

    neither = _profile()
    assert profile_config_warnings(neither) == []

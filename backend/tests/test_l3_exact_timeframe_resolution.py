"""Point 3 (AUD-002 architectural fix, follow-up to PRs #136/#137): stop
collapsing multiple OHLCV timeframes into one merged value per indicator
name for CANDLE_TIMEFRAME fields.

Etapa A (this file, always on): ProfileEngine._apply_exact_timeframe_override
wires the previously-dead ProfileEngine._get_indicators_for_condition into
filters/signals/entry_triggers evaluation, preferring
asset["_indicators_by_tf"][requested_timeframe] over the flat,
potentially-cross-timeframe merged value -- but only when that per-timeframe
data has actually been populated. No current caller populates it for plain
L3 profiles, so this is a no-op today; see test_l3_timeframe_integrity_and_
gate_precedence.py for the "nothing changes without Etapa B" regression
coverage.

Etapa B (pipeline_scan.py, gated by L3_EXACT_TIMEFRAME_RESOLUTION, default
"false"): actually populates asset["_indicators_by_tf"] for L3 by fetching
each CANDLE_TIMEFRAME timeframe a profile's conditions need via the
existing fetch_timeframe_indicators() exact-identity path.

AUD12-001 (2026-09-12 audit, confirmed live in production on trade
08983ccb.../UNI_USDT): Etapa A/B above never actually reached two real
consumers of the flat merge --
  1. ProfileEngine.evaluate_asset()'s own block_rules evaluation
     (self.block_engine.evaluate(eval_data)) runs BEFORE
     _process_single_asset ever applies the override, so every block
     condition always saw the ambiguous flat value regardless of Etapa B.
  2. l3_gate_compiler_v2.evaluate_l3_gate_v2 -- the gate with
     operational_effect=true today -- independently rebuilds its own
     eval_data from asset["indicators"] and never called the override at
     all.
Both are fixed below; see also the _collect_required_timeframes fix for a
third, related gap (block_rules conditions were grouped by the whole block,
which has no "field"/"indicator" key, so an indicator referenced only
inside a block was never in Etapa B's pre-fetch set).
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.profile_engine import ProfileEngine, _collect_required_timeframes


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


def test_entry_trigger_uses_exact_timeframe_value_when_available():
    """Reproduces T04/NEAR BB_MIDDLE_RETEST exactly: bb_width's flat merged
    value (0.054002, from a 30m row that won the ambiguous merge) would
    incorrectly pass the profile's 0.03-0.06 band; the real 5m value
    (0.02254) correctly fails it. With Etapa A wired and _indicators_by_tf
    populated for 5m (what Etapa B would do), the condition must use the
    correct 5m value."""
    asset = {
        "symbol": "NEAR_USDT",
        "indicators": {"bb_width": 0.054002, "rsi": 50, "adx": 20},
        # What Etapa B (pipeline_scan.py, flag-gated) would populate via
        # fetch_timeframe_indicators(symbols, timeframe="5m", ...).
        "_indicators_by_tf": {"5m": {"bb_width": 0.02254}},
    }
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"indicator": "bb_width", "operator": "between", "min": 0.03, "max": 0.06,
         "enabled": True, "required": True},
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["entry"]["allowed"] is False
    assert "bb_width" in result["entry"]["failed_required"] or result["entry"]["failed_required"] == ["bb_width"] \
        or any("bb_width" in str(x) for x in result["entry"]["failed_required"])


def test_entry_trigger_falls_back_to_flat_merge_without_per_timeframe_data():
    """Same profile, no _indicators_by_tf at all (today's reality for every
    L3 profile) -- must reproduce today's (buggy) behavior exactly: the
    30m-contaminated flat value passes the band, entry is allowed."""
    asset = {
        "symbol": "NEAR_USDT",
        "indicators": {"bb_width": 0.054002, "rsi": 50, "adx": 20},
    }
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"indicator": "bb_width", "operator": "between", "min": 0.03, "max": 0.06,
         "enabled": True, "required": True},
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["entry"]["allowed"] is True


def test_rolling_window_and_composite_fields_never_use_per_timeframe_cache():
    """taker_ratio (ROLLING_WINDOW) and momentum_score (COMPOSITE) must
    never be resolved via _indicators_by_tf even if a caller mistakenly
    populates it under those names -- they have no candle-timeframe
    identity, so exact-timeframe resolution is a category error for them."""
    asset = {
        "symbol": "NEAR_USDT",
        "indicators": {"taker_ratio": 0.9, "momentum_score": 10},
        "_indicators_by_tf": {"5m": {"taker_ratio": 0.1, "momentum_score": 99}},
    }
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"indicator": "taker_ratio", "operator": ">=", "value": 0.55,
         "enabled": True, "required": True},
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    # Uses the flat 0.9 (passes >=0.55), NOT the bogus per-tf 0.1 (would fail).
    assert result["entry"]["allowed"] is True


# ── Etapa B: pipeline_scan._evaluate_l3_decisions pre-fetch, flag-gated ─────

_PROFILE = {
    "default_timeframe": "5m",
    "signals": {"logic": "AND", "conditions": []},
    "entry_triggers": {
        "logic": "AND",
        "conditions": [
            {"id": "bb_width", "indicator": "bb_width", "operator": "between",
             "min": 0.03, "max": 0.06, "required": True},
        ],
    },
}


@pytest.mark.asyncio
async def test_etapa_b_disabled_by_default_leaves_flat_merge_untouched(monkeypatch):
    """Flag unset (the shipped default): no pre-fetch attempted at all, byte
    -identical to before this fix -- reproduces today's T04 bug faithfully."""
    from app.tasks import pipeline_scan

    monkeypatch.delenv("L3_EXACT_TIMEFRAME_RESOLUTION", raising=False)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("get_timeframe_indicators must not be called when the flag is off")

    monkeypatch.setattr(
        "app.services.indicators_provider.get_timeframe_indicators", fail_if_called
    )

    asset = {"symbol": "NEAR_USDT", "indicators": {"bb_width": 0.054002}}
    decisions = await pipeline_scan._evaluate_l3_decisions(
        [asset], deepcopy(_PROFILE), "L3", score_config={}, db=object(), user_id=None,
    )
    assert decisions[0]["decision"] == "ALLOW"  # today's (buggy) behavior, unchanged
    assert "_indicators_by_tf" not in asset


@pytest.mark.asyncio
async def test_etapa_b_enabled_prefetches_exact_timeframe_and_corrects_entry(monkeypatch):
    """Flag on: pre-fetch runs, populates _indicators_by_tf, and Etapa A
    (profile_engine.py) uses the corrected 5m value -- BLOCK instead of the
    ALLOW that the ambiguous flat merge (30m value) produced for T04."""
    from app.tasks import pipeline_scan
    from app.utils.indicator_merge import MergedIndicators

    monkeypatch.setenv("L3_EXACT_TIMEFRAME_RESOLUTION", "true")

    calls = []

    async def fake_get_timeframe_indicators(db, symbols, *, timeframe, market_type, groups=None):
        calls.append(timeframe)
        mi = MergedIndicators()
        mi.values = {"bb_width": 0.02254}  # the correct 5m value
        mi.meta = {"bb_width": {"timeframe": "5m", "observed_timeframes": ["5m"]}}
        return {sym: mi for sym in symbols}

    monkeypatch.setattr(
        "app.services.indicators_provider.get_timeframe_indicators",
        fake_get_timeframe_indicators,
    )

    asset = {"symbol": "NEAR_USDT", "indicators": {"bb_width": 0.054002}}  # ambiguous 30m value
    decisions = await pipeline_scan._evaluate_l3_decisions(
        [asset], deepcopy(_PROFILE), "L3", score_config={}, db=object(), user_id=None,
    )
    assert calls == ["5m"]
    assert asset["_indicators_by_tf"]["5m"]["bb_width"] == 0.02254
    assert decisions[0]["decision"] == "BLOCK"  # corrected: 0.02254 fails the 0.03-0.06 band


# ── AUD12-001: block_rules (ProfileEngine.evaluate_asset) ───────────────────

def test_block_rule_uses_exact_timeframe_value_when_available():
    """Reproduces T16/UNI ORDERBOOK_ABSORPTION_BREAK: 'MACD Momentum Decay'
    blocks on macd_hist_slope_3 < 0 at 5m. The flat merge carries the 30m
    value (+0.153057, does not block); the real 5m value (-0.073912) should
    block. Before this fix, block_rules ran before any override existed."""
    asset = {
        "symbol": "UNI_USDT",
        "indicators": {"macd_histogram": 0.00639111, "macd_hist_slope_3": 0.153057},
        "_indicators_by_tf": {"5m": {"macd_hist_slope_3": -0.073912}},
    }
    profile = _profile(block_rules={"blocks": [
        {
            "name": "MACD Momentum Decay", "logic": "AND", "enabled": True, "timeframe": "5m",
            "conditions": [
                {"type": "threshold", "value": 0, "operator": ">", "indicator": "macd_histogram", "timeframe": "5m"},
                {"type": "threshold", "value": 0, "operator": "<", "indicator": "macd_hist_slope_3", "timeframe": "5m"},
            ],
        },
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["blocked"] is True


def test_block_rule_falls_back_to_flat_merge_without_per_timeframe_data():
    """Same profile, no _indicators_by_tf -- must reproduce today's (buggy)
    behavior exactly: the 30m-contaminated flat value does not block."""
    asset = {
        "symbol": "UNI_USDT",
        "indicators": {"macd_histogram": 0.00639111, "macd_hist_slope_3": 0.153057},
    }
    profile = _profile(block_rules={"blocks": [
        {
            "name": "MACD Momentum Decay", "logic": "AND", "enabled": True, "timeframe": "5m",
            "conditions": [
                {"type": "threshold", "value": 0, "operator": ">", "indicator": "macd_histogram", "timeframe": "5m"},
                {"type": "threshold", "value": 0, "operator": "<", "indicator": "macd_hist_slope_3", "timeframe": "5m"},
            ],
        },
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["blocked"] is False


# ── AUD12-001: l3_gate_v2 (l3_gate_compiler_v2.evaluate_l3_gate_v2) ─────────

def test_gate_v2_entry_trigger_uses_exact_timeframe_value_when_available():
    """The gate with operational_effect=true today never called the Etapa A
    override at all -- it independently rebuilds eval_data from
    asset["indicators"]. Reproduces T16's exact entry trigger."""
    from app.services.l3_gate_compiler_v2 import evaluate_l3_gate_v2
    from datetime import datetime, timezone

    asset = {
        "symbol": "UNI_USDT",
        "indicators": {"macd_hist_slope_3": 0.153057},
        "_indicators_by_tf": {"5m": {"macd_hist_slope_3": -0.073912}},
    }
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"id": "macd_hist_slope_3", "indicator": "macd_hist_slope_3", "operator": ">",
         "value": 0, "enabled": True, "required": True},
    ]})
    result = evaluate_l3_gate_v2(
        asset=asset, profile_config=profile, score=50.0, score_context={},
        evaluated_at=datetime.now(timezone.utc), base_eligible=True,
        legacy_decision="BLOCK",
        block_rules_audit={"rules": [], "matched_blocks": [], "blocked": False, "blocked_by": [], "skipped_blocks": []},
    )
    condition = result["entry_triggers"]["conditions"][0]
    assert condition["actual"] == -0.073912
    assert result["entry_triggers"]["gate_passed"] is False


def test_gate_v2_falls_back_to_flat_merge_without_per_timeframe_data():
    """Same profile, no _indicators_by_tf -- must reproduce today's (buggy)
    ALLOW faithfully."""
    from app.services.l3_gate_compiler_v2 import evaluate_l3_gate_v2
    from datetime import datetime, timezone

    asset = {"symbol": "UNI_USDT", "indicators": {"macd_hist_slope_3": 0.153057}}
    profile = _profile(entry_triggers={"logic": "AND", "conditions": [
        {"id": "macd_hist_slope_3", "indicator": "macd_hist_slope_3", "operator": ">",
         "value": 0, "enabled": True, "required": True},
    ]})
    result = evaluate_l3_gate_v2(
        asset=asset, profile_config=profile, score=50.0, score_context={},
        evaluated_at=datetime.now(timezone.utc), base_eligible=True,
        legacy_decision="ALLOW",
        block_rules_audit={"rules": [], "matched_blocks": [], "blocked": False, "blocked_by": [], "skipped_blocks": []},
    )
    condition = result["entry_triggers"]["conditions"][0]
    assert condition["actual"] == 0.153057
    assert result["entry_triggers"]["gate_passed"] is True


# ── _collect_required_timeframes: block_rules grouped by inner condition ───

def test_collect_required_timeframes_groups_block_conditions_not_whole_block():
    """Before this fix, block_rules entries were grouped by the whole block
    dict (no 'field'/'indicator' key), so an indicator referenced ONLY
    inside a block_rules condition never made it into Etapa B's pre-fetch
    set. Each inner condition must appear individually, tagged
    _section='block_rules', using its own timeframe (or the block's, or the
    profile default, in that order)."""
    profile = _profile(block_rules={"blocks": [
        {
            "name": "Only in block", "enabled": True, "timeframe": "15m",
            "conditions": [
                {"indicator": "rsi", "operator": ">", "value": 50},
                {"indicator": "adx", "operator": ">", "value": 20, "timeframe": "1h"},
            ],
        },
    ]})
    grouped = _collect_required_timeframes(profile)
    fifteen_min = [c for c in grouped.get("15m", []) if c.get("indicator") == "rsi"]
    one_hour = [c for c in grouped.get("1h", []) if c.get("indicator") == "adx"]
    assert len(fifteen_min) == 1 and fifteen_min[0]["_section"] == "block_rules"
    assert len(one_hour) == 1 and one_hour[0]["_section"] == "block_rules"


# ── AUD12-003: ema21_distance_pct is the same bug class as AUD12-001 ───────

def test_block_rule_ema21_distance_pct_uses_exact_timeframe_value():
    """Reproduces T16/UNI ORDERBOOK_ABSORPTION_BREAK: three block conditions
    reference ema21_distance_pct (two with explicit timeframe '5m').
    Production data confirmed the flat merge can carry a stale/ambiguous
    value while the real 5m value is available in
    asset["_indicators_by_tf"] -- same mechanism as AUD12-001, verified
    here for this specific field rather than only by analogy."""
    asset = {
        "symbol": "UNI_USDT",
        "indicators": {"ema21_distance_pct": 2.9},  # ambiguous/stale flat value
        "_indicators_by_tf": {"5m": {"ema21_distance_pct": 0.7449}},
    }
    profile = _profile(block_rules={"blocks": [
        {
            "name": "Limite de extensao da entrada", "logic": "AND", "enabled": True, "timeframe": "5m",
            "conditions": [
                {"type": "threshold", "value": 2.5, "operator": ">", "indicator": "ema21_distance_pct", "timeframe": "5m"},
            ],
        },
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["blocked"] is False  # 0.7449 does not exceed the 2.5 threshold


def test_block_rule_ema21_distance_pct_falls_back_without_per_timeframe_data():
    """Same profile, no _indicators_by_tf -- must reproduce the ambiguous
    (ungated) flat value blocking when the correct 5m value wouldn't have."""
    asset = {"symbol": "UNI_USDT", "indicators": {"ema21_distance_pct": 2.9}}
    profile = _profile(block_rules={"blocks": [
        {
            "name": "Limite de extensao da entrada", "logic": "AND", "enabled": True, "timeframe": "5m",
            "conditions": [
                {"type": "threshold", "value": 2.5, "operator": ">", "indicator": "ema21_distance_pct", "timeframe": "5m"},
            ],
        },
    ]})
    engine = ProfileEngine(profile)
    result = engine.evaluate_asset(asset)
    assert result["blocked"] is True  # 2.9 > 2.5 -- the stale/ambiguous value blocks

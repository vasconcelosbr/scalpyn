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
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.profile_engine import ProfileEngine


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

"""Bloco A (release do monitor de Shadow, 2026-09-03): C1/C2 evaluator fix,
A.2 fast-scan priority SQL shape, A.3 monitor-mode schema + resolution,
A.4 config validation, A.6/A.7 closure_path/feature_source_at plumbing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.strategy_settings import MLShadowConfig, SHADOW_SOURCES
from app.services.shadow_barrier_evaluator import (
    BARRIER_CONTRACT_VERSION_V2,
    evaluate_closed_candles_policy_v2,
)
from app.services.shadow_trade_service import _resolve_shadow_monitor_mode
from app.tasks.shadow_trade_monitor import _canonical_barrier_enabled


BASE = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def candle(minute, high, low, close=100.0):
    return {"time": BASE + timedelta(minutes=minute), "open": 100.0, "high": high, "low": low, "close": close}


# ── C2: evaluate_closed_candles_policy_v2 no longer freezes on an
# ambiguous entry-boundary candle ──────────────────────────────────────────

def test_ambiguous_entry_candle_records_once_and_continues_to_outcome():
    entry_at = BASE + timedelta(seconds=20)
    candles = [
        candle(0, high=100.5, low=97.0),   # entry candle itself touches SL -> ambiguous
        candle(1, high=101.5, low=100.2),  # unambiguous, no touch
        candle(2, high=103.5, low=101.0),  # unambiguous, touches TP
    ]
    policy = {"policy_family": "FIXED", "activation_profit_pct": 1.0, "hwm_trail_pct": 0.5}
    result = evaluate_closed_candles_policy_v2(
        candles, entry_price=100.0, entry_timestamp=entry_at,
        tp_price=103.0, sl_price=98.0, timeout_candles=1440,
        trailing_policy=policy,
    )
    assert result["status"] == "OUTCOME"
    assert result["outcome"] == "TP_HIT"
    assert result["entry_boundary_ambiguous_at"] == candle(0, 0, 0)["time"]
    assert result["barrier_contract_version"] == BARRIER_CONTRACT_VERSION_V2


def test_ambiguous_entry_candle_with_no_later_touch_advances_cursor_instead_of_freezing():
    entry_at = BASE + timedelta(seconds=20)
    candles = [
        candle(0, high=100.5, low=97.0),   # ambiguous
        candle(1, high=100.6, low=100.1),  # no touch
        candle(2, high=100.7, low=100.2),  # no touch
    ]
    policy = {"policy_family": "FIXED", "activation_profit_pct": 1.0, "hwm_trail_pct": 0.5}
    result = evaluate_closed_candles_policy_v2(
        candles, entry_price=100.0, entry_timestamp=entry_at,
        tp_price=103.0, sl_price=98.0, timeout_candles=1440,
        trailing_policy=policy,
    )
    # PENDING (not the old terminal UNRESOLVED) with last_candle_at advanced
    # to the last candle scanned -- the caller advances last_processed_time
    # from this, which is exactly what was frozen before this fix.
    assert result["status"] == "PENDING"
    assert result["last_candle_at"] == candle(2, 0, 0)["time"]
    assert result["entry_boundary_ambiguous_at"] == candle(0, 0, 0)["time"]


def test_non_ambiguous_path_unaffected():
    entry_at = BASE  # exactly on the bucket -> not entry_boundary_partial
    candles = [candle(1, high=101.0, low=99.5), candle(2, high=103.5, low=100.0)]
    result = evaluate_closed_candles_policy_v2(
        candles, entry_price=100.0, entry_timestamp=entry_at,
        tp_price=103.0, sl_price=98.0, timeout_candles=1440,
        trailing_policy=None,
    )
    assert result["entry_boundary_ambiguous_at"] is None
    assert result["outcome"] == "TP_HIT"


# ── _canonical_barrier_enabled accepts v1 or v2 ────────────────────────────

class _FakeShadow:
    def __init__(self, config_snapshot, profile_id="p1"):
        self.config_snapshot = config_snapshot
        self.profile_id = profile_id


@pytest.mark.parametrize("version", [
    "shadow_closed_ohlcv_first_touch_v1",
    "shadow_closed_ohlcv_first_touch_v2",
])
def test_canonical_barrier_enabled_accepts_both_versions(version):
    shadow = _FakeShadow({
        "canonical_barrier_evaluator": {
            "enabled": True, "policy_version": version,
            "profile_allowlist": ["p1"],
        }
    })
    assert _canonical_barrier_enabled(shadow) is True


def test_canonical_barrier_enabled_rejects_unknown_version():
    shadow = _FakeShadow({
        "canonical_barrier_evaluator": {
            "enabled": True, "policy_version": "some_future_v3",
            "profile_allowlist": ["p1"],
        }
    })
    assert _canonical_barrier_enabled(shadow) is False


# ── A.3: schema + mode resolution ──────────────────────────────────────────

def test_default_monitor_mode_matches_proposed_default():
    cfg = MLShadowConfig()
    assert cfg.shadow_monitor_mode_by_source["L3"] == "CONTINUOUS"
    for source in SHADOW_SOURCES:
        if source != "L3":
            assert cfg.shadow_monitor_mode_by_source[source] == "BATCH"


def test_monitor_mode_by_source_requires_every_known_source():
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_monitor_mode_by_source={"L3": "CONTINUOUS"})


def test_resolve_shadow_monitor_mode_reads_frozen_map():
    user_config = {"shadow_monitor_mode_by_source": {"L3": "CONTINUOUS", "L3_LAB": "BATCH"}}
    assert _resolve_shadow_monitor_mode(user_config, "L3") == "CONTINUOUS"
    assert _resolve_shadow_monitor_mode(user_config, "L3_LAB") == "BATCH"


def test_resolve_shadow_monitor_mode_fails_open_to_continuous_when_unmapped():
    assert _resolve_shadow_monitor_mode({}, "L3") == "CONTINUOUS"
    assert _resolve_shadow_monitor_mode(
        {"shadow_monitor_mode_by_source": {"L3": "not_a_real_mode"}}, "L3"
    ) == "CONTINUOUS"


# ── A.2/A.4: config validation for the new tuning knobs ────────────────────

def test_fast_scan_and_l3_quota_fields_have_sane_defaults_and_bounds():
    cfg = MLShadowConfig()
    assert cfg.shadow_fast_scan_priority == "AGE_THEN_MAGNITUDE"
    assert cfg.shadow_fast_scan_batch_size == 20
    assert cfg.shadow_l3_batch_quota_pct == 20.0
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_fast_scan_batch_size=0)
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_l3_batch_quota_pct=101)

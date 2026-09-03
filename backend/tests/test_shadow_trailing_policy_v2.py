"""shadow_trailing_policy_v2: Shadow-only trailing (R1 trailing-policy study).

Covers the three policy families in evaluate_closed_candles_policy_v2, the
schema contract, and that the Shadow-only wiring never touches live-spot
selling. Defaults must reproduce shadow_hwm_trailing_v1 behaviour exactly
until an operator explicitly opts a profile into v2.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.strategy_settings import MLShadowConfig
from app.services.shadow_barrier_evaluator import (
    evaluate_closed_candles,
    evaluate_closed_candles_policy_v2,
)
from app.services.shadow_trade_service import _apply_shadow_trailing_policy


BASE = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def candle(minute: int, *, high: float, low: float, close: float = 100.0):
    return {
        "time": BASE + timedelta(minutes=minute),
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
    }


# ── Schema ────────────────────────────────────────────────────────────────

def test_default_shadow_trailing_stays_on_v1_contract():
    cfg = MLShadowConfig()
    assert cfg.shadow_trailing_contract_version == "shadow_hwm_trailing_v1"
    assert cfg.shadow_trailing_policy_family == "FIXED"


def test_stepped_requires_non_empty_increasing_steps():
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_trailing_policy_family="STEPPED")
    with pytest.raises(ValidationError):
        MLShadowConfig(
            shadow_trailing_policy_family="STEPPED",
            shadow_trailing_stepped_steps=[
                {"peak_profit_pct": 4.0, "floor_profit_pct": 2.5},
                {"peak_profit_pct": 2.0, "floor_profit_pct": 1.5},
            ],
        )
    ok = MLShadowConfig(
        shadow_trailing_policy_family="STEPPED",
        shadow_trailing_stepped_steps=[
            {"peak_profit_pct": 2.0, "floor_profit_pct": 1.5},
            {"peak_profit_pct": 4.0, "floor_profit_pct": 2.5},
        ],
    )
    assert len(ok.shadow_trailing_stepped_steps) == 2


def test_proportional_k_must_be_in_open_unit_interval():
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_trailing_proportional_k=1.0)
    with pytest.raises(ValidationError):
        MLShadowConfig(shadow_trailing_proportional_k=0.0)


# ── evaluate_closed_candles_policy_v2: FIXED reproduces v1 exactly ────────

def test_v2_fixed_matches_v1_evaluator_bit_for_bit():
    candles = [
        candle(1, high=101.0, low=99.6),
        candle(2, high=101.3, low=100.6),
        candle(3, high=101.1, low=100.5),
        candle(4, high=100.9, low=100.35),
    ]
    v1 = evaluate_closed_candles(
        candles, entry_price=100.0, entry_timestamp=BASE,
        tp_price=103.0, sl_price=98.0,
        trailing_activation_profit_pct=1.0, trailing_hwm_pct=0.5,
    )
    v2 = evaluate_closed_candles_policy_v2(
        candles, entry_price=100.0, entry_timestamp=BASE,
        tp_price=103.0, sl_price=98.0,
        trailing_policy={
            "policy_family": "FIXED",
            "activation_profit_pct": 1.0,
            "hwm_trail_pct": 0.5,
        },
    )
    assert v1["outcome"] == v2["outcome"] == "TRAILING_STOP"
    assert v1["exit_price_nominal"] == v2["exit_price_nominal"]
    assert v1["barrier_touched_at"] == v2["barrier_touched_at"]


# ── PROPORTIONAL: worked reference from the prompt ─────────────────────────

def test_v2_proportional_worked_reference():
    # peak=1% profit, k=0.30 -> floor=0.7% profit; peak=4%, k=0.30 -> floor=2.8%.
    # HWM (candle 1's high) only governs the floor from candle 2 onward.
    candles = [candle(1, high=101.0, low=100.9), candle(2, high=100.95, low=100.71)]
    result = evaluate_closed_candles_policy_v2(
        candles, entry_price=100.0, entry_timestamp=BASE,
        tp_price=200.0, sl_price=50.0,
        trailing_policy={"policy_family": "PROPORTIONAL", "k": 0.30},
    )
    assert result["outcome"] is None  # 100.71 > 100.70 floor, not touched yet

    candles2 = [candle(1, high=101.0, low=100.9), candle(2, high=100.95, low=100.69)]
    result2 = evaluate_closed_candles_policy_v2(
        candles2, entry_price=100.0, entry_timestamp=BASE,
        tp_price=200.0, sl_price=50.0,
        trailing_policy={"policy_family": "PROPORTIONAL", "k": 0.30},
    )
    assert result2["outcome"] == "TRAILING_STOP"
    assert result2["exit_price_nominal"] == pytest.approx(100.7, abs=1e-9)


# ── STEPPED: flat floor within a tier, jumps at the next tier ─────────────

def test_v2_stepped_floor_is_flat_within_tier_and_jumps_at_next():
    policy = {
        "policy_family": "STEPPED",
        "steps": [
            {"peak_profit_pct": 2.0, "floor_profit_pct": 1.5},
            {"peak_profit_pct": 4.0, "floor_profit_pct": 2.5},
        ],
        "base_activation_profit_pct": None,
        "base_hwm_trail_pct": None,
    }
    # below the first step: no trailing at all (base=None) -> SL still governs
    below_first_step = [candle(1, high=101.5, low=101.0)]  # peak 1.5%, never reaches 2%
    result = evaluate_closed_candles_policy_v2(
        below_first_step, entry_price=100.0, entry_timestamp=BASE,
        tp_price=200.0, sl_price=50.0, trailing_policy=policy,
    )
    assert result["outcome"] is None

    # crosses first step (peak >= 2%): floor becomes 101.5 (1.5% of entry)
    crosses_step = [
        candle(1, high=102.0, low=101.9),   # hwm now 102.0 (2% peak) -> floor 101.5
        candle(2, high=102.0, low=101.4),   # dips below 101.5 floor
    ]
    result2 = evaluate_closed_candles_policy_v2(
        crosses_step, entry_price=100.0, entry_timestamp=BASE,
        tp_price=200.0, sl_price=50.0, trailing_policy=policy,
    )
    assert result2["outcome"] == "TRAILING_STOP"
    assert result2["exit_price_nominal"] == pytest.approx(101.5, abs=1e-9)


# ── Shadow-only wiring: never touches spot's live-selling policy ──────────

def test_default_ml_config_leaves_frozen_trailing_untouched():
    user_config = {"trailing": {"enabled": True, "activation_profit_pct": 1.0,
                                 "hwm_trail_pct": 0.35, "contract_version": "shadow_hwm_trailing_v1",
                                 "never_sell_at_loss": True, "min_profit_pct": 0.6}}
    before = dict(user_config["trailing"])
    _apply_shadow_trailing_policy(user_config, ml_config={})
    assert user_config["trailing"] == before


def test_v2_opt_in_overrides_mechanism_but_keeps_protection_fields():
    user_config = {"trailing": {"enabled": True, "activation_profit_pct": 1.0,
                                 "hwm_trail_pct": 0.35, "contract_version": "shadow_hwm_trailing_v1",
                                 "never_sell_at_loss": True, "min_profit_pct": 0.6,
                                 "safety_margin_above_entry_pct": 0.3}}
    ml_config = {
        "shadow_trailing_contract_version": "shadow_trailing_policy_v2",
        "shadow_trailing_policy_family": "PROPORTIONAL",
        "shadow_trailing_proportional_k": 0.25,
    }
    _apply_shadow_trailing_policy(user_config, ml_config)
    trailing = user_config["trailing"]
    assert trailing["contract_version"] == "shadow_trailing_policy_v2"
    assert trailing["policy"] == {"policy_family": "PROPORTIONAL", "k": 0.25}
    # protection fields untouched -- still spot-sourced, out of this contract's scope
    assert trailing["never_sell_at_loss"] is True
    assert trailing["min_profit_pct"] == 0.6
    assert trailing["safety_margin_above_entry_pct"] == 0.3

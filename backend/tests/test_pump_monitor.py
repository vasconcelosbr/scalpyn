"""Pump Monitor — CP-5 (hysteresis), CP-7 (formulas), CP-8 (parity), D1 (observation only)."""
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.services import flow_metrics as fm
from app.services import pump_monitor_engine as eng
from app.services.feature_engine import FeatureEngine

ROOT = Path(__file__).resolve().parents[1]
M = 60_000


def _bucket(start, buy, sell, close=None, partial=False, reason=None):
    return {"bucket_start_ms": start, "buy_base": buy, "sell_base": sell, "buy_quote": buy, "sell_quote": sell,
            "trade_count": 1, "close_price": close, "partial": partial, "gap_reason": reason, "source": "ws"}


# ── CP-7: flow formulas ──────────────────────────────────────────────────────

def test_bucket_trades_dedups_by_trade_id_and_keeps_quote_units():
    trades = [
        {"trade_id": "1", "side": "buy", "amount": "2", "price": "10", "ts_ms": 1_000},
        {"trade_id": "1", "side": "buy", "amount": "2", "price": "10", "ts_ms": 1_000},  # WS replay
        {"trade_id": "2", "side": "sell", "amount": "1", "price": "12", "ts_ms": 30_000},
    ]
    b = fm.bucket_trades(trades)[0]
    assert (b["buy_base"], b["sell_base"]) == (2.0, 1.0)
    assert (b["buy_quote"], b["sell_quote"]) == (20.0, 12.0)
    assert b["trade_count"] == 2 and (b["first_trade_id"], b["last_trade_id"]) == ("1", "2")
    assert (b["open_price"], b["close_price"], b["high_price"]) == (10.0, 12.0, 12.0)


def test_bucket_quote_is_null_when_any_price_missing():
    b = fm.bucket_trades([{"trade_id": "1", "side": "buy", "amount": 1, "price": None, "ts_ms": 5}])[0]
    assert b["buy_quote"] is None and b["buy_base"] == 1.0


@pytest.mark.parametrize("buy,sell,expected,reason", [
    (5, 0, 1.0, None), (0, 5, -1.0, None), (0, 0, None, "no_trades"), (3, 1, 0.5, None),
])
def test_delta_norm_edge_cases(buy, sell, expected, reason):
    out = fm.delta_norm(_bucket(0, buy, sell))
    assert out["value"] == expected and out["reason"] == reason


def test_partial_and_missing_buckets_propagate_null_not_zero():
    assert fm.delta_norm(_bucket(0, 1, 1, partial=True, reason="ws_gap")) == {"value": None, "reason": "ws_gap"}
    assert fm.delta_norm(None) == {"value": None, "reason": "missing_bucket"}
    win = [_bucket(i * M, 1, 0) for i in range(5)] + [None] * 5
    out = fm.cvd(win, min_coverage_pct=80)
    assert out["value"] is None and out["reason"] == "insufficient_coverage" and out["coverage_pct"] == 50.0


def test_cvd_sums_non_overlapping_buckets():
    win = [_bucket(i * M, 3, 1) for i in range(10)]
    assert fm.cvd(win)["value"] == 20.0
    assert fm.cvd_slope(win)["value"] > 0


def test_buy_and_sell_persistence():
    win = [_bucket(0, 2, 1), _bucket(M, 1, 2), _bucket(2 * M, 3, 1), _bucket(3 * M, 1, 1)]
    assert fm.buy_persistence(win, min_coverage_pct=0)["value"] == 0.5
    assert fm.buy_persistence(win, min_coverage_pct=0, side="sell")["value"] == 0.25


def test_volume_acceleration_and_flow_change():
    assert fm.volume_acceleration(_bucket(0, 0, 0), _bucket(M, 1, 1)) == {"value": None, "reason": "zero_denominator"}
    assert fm.volume_acceleration(_bucket(0, 1, 1), _bucket(M, 2, 2))["value"] == 1.0
    assert fm.flow_change(_bucket(0, 1, 1), _bucket(M, 3, 1))["value"] == 0.5
    assert fm.flow_change(_bucket(0, 0, 0), _bucket(M, 3, 1))["reason"] == "no_trades"


def test_materialize_buckets_distinguishes_zero_flow_from_gap():
    minutes = [0, M, 2 * M]
    alive = {s for s in range(0, 120, 10)}  # minute 2 had no WS frames
    rows = fm.materialize_buckets({}, minutes, covered_from_ms=0, source="ws", alive_slots=alive)
    assert rows[0]["partial"] is False and rows[0]["buy_base"] == 0.0  # real zero
    assert rows[2]["partial"] is True and rows[2]["gap_reason"] == "ws_gap" and rows[2]["buy_base"] is None
    # CP-3 production finding: a handover long before bucketing still taints its minutes
    handover = fm.materialize_buckets({}, minutes, covered_from_ms=0, source="ws",
                                      gap_windows=[(M + 3_509, M + 63_509, "ws_leader_handover")])
    assert [r["gap_reason"] for r in handover] == [None, "ws_leader_handover", "ws_leader_handover"]
    uncovered = fm.materialize_buckets({}, minutes, covered_from_ms=M, source="rest_fallback")
    assert uncovered[0]["partial"] is True and uncovered[0]["gap_reason"] == "not_covered"


# ── CP-7: ATR measures and candles ───────────────────────────────────────────

def test_atr_measures_guard_zero_and_missing_atr():
    assert fm.price_extension_atr(11, 10, 0)["reason"] == "atr_zero"
    assert fm.price_progress_atr(10, 11, None)["reason"] == "atr_unavailable"
    assert fm.breakout_distance_atr(12, 10, 2)["value"] == 1.0
    assert fm.price_progress_atr(10, 13, 2)["value"] == 1.5


def test_upper_wick_ratio_and_flat_candle():
    assert fm.upper_wick_ratio(10, 14, 9, 12)["value"] == 0.4
    assert fm.upper_wick_ratio(10, 10, 10, 10) == {"value": None, "reason": "zero_range"}


def test_breakout_hold_ratio_uses_frozen_level():
    win = [_bucket(i * M, 1, 1, close=c) for i, c in enumerate([101, 99, 102, 103])]
    assert fm.breakout_hold_ratio(win, 100, min_coverage_pct=0)["value"] == 0.75
    assert fm.breakout_hold_ratio(win, None)["reason"] == "no_active_breakout"


def test_advance_breakout_freezes_previous_level_and_expires():
    cfg = eng.DEFAULT_CONFIG
    s = eng.advance_breakout(None, price=9.0, level=10.0, minute_ms=0, config=cfg)
    s = eng.advance_breakout(s, price=10.5, level=10.6, minute_ms=M, config=cfg)
    assert s["breakout_level"] == 10.0 and s["breakout_at_ms"] == M and s["last_level"] == 10.6
    s = eng.advance_breakout(s, price=12, level=12, minute_ms=M * 2, config=cfg)
    assert s["breakout_level"] == 10.0  # frozen, not moved to the new high
    later = eng.advance_breakout(s, price=5, level=12, minute_ms=M * 200, config=cfg)
    assert "breakout_level" not in later


# ── CP-7: liquidity ──────────────────────────────────────────────────────────

BOOK_BIDS = [["99.9", "10"], ["99.5", "10"], ["99", "10"], ["97", "100"]]
BOOK_ASKS = [["100.1", "10"], ["100.5", "10"], ["101", "10"], ["103", "100"]]


def test_slippage_walks_levels_and_reports_insufficient_depth():
    mid = fm.book_mid(BOOK_BIDS, BOOK_ASKS)
    assert mid == 100.0
    buy = fm.estimated_slippage_pct(BOOK_ASKS, mid, 1001, "buy")["value"]
    # 1001 USDT fills level 1 fully; VWAP = 100.1 -> slippage 0.1 %
    assert buy == pytest.approx(0.1, abs=1e-6)
    two_levels = fm.estimated_slippage_pct(BOOK_ASKS, mid, 2006, "buy")["value"]
    assert two_levels == pytest.approx((2006 / (10 + 1005 / 100.5) - 100) / 100 * 100, rel=1e-6)
    assert fm.estimated_slippage_pct(BOOK_ASKS, mid, 10**9, "buy") == {"value": None, "reason": "insufficient_depth"}
    assert fm.estimated_slippage_pct(BOOK_BIDS, mid, 999, "sell")["value"] == pytest.approx(0.1, abs=1e-6)


def test_band_depth_per_side_and_truncated_book():
    mid = 100.0
    assert fm.band_depth_quote(BOOK_BIDS, mid, 1, "bid")["value"] == pytest.approx(999 + 995 + 990)
    truncated = [["99.9", "1"], ["99.8", "1"]]
    assert fm.band_depth_quote(truncated, mid, 1, "bid") == {"value": None, "reason": "insufficient_depth"}
    # fewer levels than requested = whole side received, so the sum is exact
    assert fm.band_depth_quote(truncated, mid, 1, "bid", book_complete=True)["value"] == pytest.approx(199.7)
    bid = fm.band_depth_quote(BOOK_BIDS, mid, 1, "bid")
    ask = fm.band_depth_quote(BOOK_ASKS, mid, 1, "ask")
    assert fm.depth_imbalance(bid, ask)["value"] == pytest.approx(
        (2984 - (1001 + 1005 + 1010)) / (2984 + 3016), abs=1e-6)


# ── CP-7: colours by polarity, score ─────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [(0.05, "green"), (0.15, "amber"), (0.25, "red"), (None, "gray")])
def test_slippage_colour_bands_follow_d3(value, expected):
    assert eng.color_state("estimated_slippage_buy_pct", value, eng.DEFAULT_CONFIG) == expected


def test_colours_are_never_invented():
    cfg = eng.DEFAULT_CONFIG
    assert eng.color_state("atr_pct", 3.0, cfg) is None           # neutral
    assert eng.color_state("unknown_indicator", 1.0, cfg) is None  # no spec
    assert eng.color_state("delta_norm", 0.2, cfg) == "green"
    assert eng.color_state("delta_norm", -0.2, cfg) == "red"
    assert eng.color_state("psar_trend", "bullish", cfg) == "green"


def test_score_excludes_missing_components_and_reports_confidence():
    cfg = eng.DEFAULT_CONFIG
    cells = {k: {"value": None, "reason": "x"} for k in cfg["score"]["components"]}
    assert eng.score_row(dict(cells), cfg)["pump_monitor_score"] is None
    full = {k: {"value": spec["hi"]} for k, spec in cfg["score"]["components"].items()}
    out = eng.score_row(full, cfg)
    assert out["pump_monitor_score"] == 100.0 and out["score_confidence"] == 1.0
    assert out["score_status"] == "HYPOTHESIS_NOT_VALIDATED"


def test_config_versioning_and_validation():
    v1 = eng.next_config_version(None, {}, changed_by="u", now_iso="t")
    v2 = eng.next_config_version(v1, {"cvd": {"window_minutes": 30}}, changed_by="u", now_iso="t")
    assert (v1["_meta"]["version"], v2["_meta"]["version"]) == (1, 2)
    assert v1["_meta"]["config_hash"] != v2["_meta"]["config_hash"]
    assert eng.effective_config(v2)["_meta"]["config_hash"] == v2["_meta"]["config_hash"]
    v3 = eng.next_config_version(v2, {"display": {"top_n": 20}}, changed_by="u", now_iso="t")
    assert v3["cvd"]["window_minutes"] == 30 and v3["display"]["top_n"] == 20  # partial update keeps v2
    with pytest.raises(ValueError):
        eng.next_config_version(v2, {"slippage": {"amber_pct": 0.5, "red_pct": 0.2}}, changed_by="u", now_iso="t")


# ── CP-5: hysteresis ─────────────────────────────────────────────────────────

HYST = dict(min_score=50.0, exit_consecutive_cycles=3, min_hold_seconds=300)


def test_cp5_score_oscillating_around_min_score_produces_zero_flapping():
    state = eng.advance_membership(None, {"A": 60.0}, now_ms=0, **HYST)
    changes = 0
    for cycle in range(1, 21):
        scores = {"A": 45.0 if cycle % 2 else 55.0}  # dips below min_score every other cycle
        new = eng.advance_membership(state, scores, now_ms=cycle * 30_000, **HYST)
        changes += len(set(new["members"]) ^ set(state["members"]))
        state = new
    assert changes == 0 and "A" in state["members"]


def test_entry_requires_min_score_and_has_no_size_cap():
    scores = {f"S{i}": 50.0 + i for i in range(30)}
    scores["LOW"] = 49.99
    state = eng.advance_membership(None, scores, now_ms=0, **HYST)
    assert set(state["members"]) == {f"S{i}" for i in range(30)}


def test_sustained_exit_requires_consecutive_cycles_and_min_hold():
    state = eng.advance_membership(None, {"A": 70.0}, now_ms=0, **HYST)
    for cycle in range(1, 3):
        state = eng.advance_membership(state, {"A": 10.0}, now_ms=cycle * 30_000, **HYST)
    assert "A" in state["members"]  # 2 cycles below, less than 3
    state = eng.advance_membership(state, {}, now_ms=90_000, **HYST)
    assert "A" in state["members"]  # 3 cycles out but held only 90 s < 300 s
    state = eng.advance_membership(state, {}, now_ms=301_000, **HYST)
    assert "A" not in state["members"]


def test_membership_rejects_min_score_outside_scale():
    with pytest.raises(ValueError):
        eng.advance_membership(None, {}, now_ms=0, min_score=101, exit_consecutive_cycles=3, min_hold_seconds=0)


# ── D1: REALTIME is never an execution universe ──────────────────────────────

def test_execution_universes_filter_observation_only_pools():
    from app.services.pool_service import EXECUTION_POOL_PREDICATE_SQL
    assert "observation_only" in EXECUTION_POOL_PREDICATE_SQL
    for path in ("app/tasks/execute_buy.py", "app/tasks/evaluate_signals.py"):
        source = (ROOT / path).read_text(encoding="utf-8")
        assert "AND {EXECUTION_POOL_PREDICATE_SQL}" in source, path


def test_pump_monitor_sync_forces_observation_only_and_keeps_worker_health():
    from app.services.radar_pool_sync import operator_pool_overrides
    current = {"pump_monitor_sync_enabled": True, "pump_monitor_feed_health": {"status": "healthy"}}
    result = operator_pool_overrides(current, {"pump_monitor_sync_enabled": True, "observation_only": False,
                                               "pump_monitor_feed_health": {"status": "forged"}})
    assert result["observation_only"] is True
    assert result["pump_monitor_feed_health"] == {"status": "healthy"}


def test_generic_discovery_skips_pump_monitor_pools():
    from app.services.pool_selection import is_auto_discovery_enabled
    assert is_auto_discovery_enabled({"auto_refresh": True, "pump_monitor_sync_enabled": True}) is False


# ── rvol_strict (new indicator; volume_spike untouched) ─────────────────────

def _frame(volumes):
    n = len(volumes)
    return pd.DataFrame({"open": [1.0] * n, "high": [1.1] * n, "low": [0.9] * n, "close": [1.0] * n,
                         "volume": volumes})


def test_rvol_strict_excludes_current_candle_and_volume_spike_is_unchanged():
    df = _frame([1.0] * 20 + [5.0])
    engine = FeatureEngine({"volume_spike": {"lookback": 20}, "rvol_strict": {"lookback": 20}})
    assert engine._calc_rvol_strict(df)["rvol_strict"] == 5.0
    assert engine._calc_volume_spike(df)["volume_spike"] == round(5.0 / ((19 + 5) / 20), 2)
    assert engine._calc_rvol_strict(_frame([1.0] * 5))["rvol_strict"] is None


# ── CP-8: live and backtest use the same functions ───────────────────────────

def test_cp8_pump_radar_research_uses_shared_measures():
    from app.services import pump_radar_research as research
    Row = namedtuple("Row", "open_time close_time is_closed open high low close volume_base volume_quote")
    t0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = [Row(t0 + timedelta(minutes=5 * i), t0 + timedelta(minutes=5 * (i + 1)), True,
                100 + i * 0.1, 101 + i * 0.1 + (i % 3) * 0.2, 99 + i * 0.1, 100.5 + i * 0.1, 10 + i, 1000)
            for i in range(60)]
    at = rows[-1].close_time
    values, history = research.reconstruct_indicators(
        rows, at, "5m", {"atr": {"enabled": True, "period": 14}, "vwap": {"enabled": True}})
    last = history[-1]
    shared = research.pump_monitor_candle_measures(history, values)
    assert "upper_wick_ratio" not in values  # Pump Radar output unchanged (D5)
    assert shared["upper_wick_ratio"] == fm.upper_wick_ratio(last.open, last.high, last.low, last.close)
    assert shared["price_extension_atr"] == fm.price_extension_atr(values["close"], values["vwap"], values["atr"])
    assert shared["price_extension_atr"]["value"] is not None
    live = FeatureEngine({"rvol_strict": {"lookback": 20}})._calc_rvol_strict(
        pd.DataFrame({"volume": [float(r.volume_base) for r in history]}))
    assert values["rvol_strict"] == live["rvol_strict"]
    assert "delta_norm" in research.NON_OHLCV and "cvd_60m" in research.NON_OHLCV

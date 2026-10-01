import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.services import pump_monitor_engine as eng
from app.services import pump_research as pr

LABELS = deepcopy(eng.DEFAULT_CONFIG["research"]["labels"])
T0 = 1_759_276_800_000  # 2025-10-01 00:00:00 UTC, minute aligned
M = pr.MINUTE_MS


def _series(closes, highs=None, lows=None, start=T0, skip=()):
    """closes[0] is the entry minute; later items are t+1, t+2, ..."""
    out = {}
    for i, c in enumerate(closes):
        if i in skip:
            continue
        out[start + i * M] = {"bucket_close": c, "bucket_high": (highs or closes)[i],
                              "bucket_low": (lows or closes)[i], "bucket_partial": False}
    return out


def test_default_config_has_versioned_research_block():
    spec = eng.DEFAULT_CONFIG["research"]
    assert spec["every_n_minutes"] == 1 and spec["retention_days"] == 30
    assert pr.max_horizon_minutes(spec["labels"]) == 60
    with pytest.raises(ValueError):
        eng.next_config_version(None, {"research": {"labels": {"max_gap_pct": 150}}}, changed_by="u", now_iso="t")


def test_no_label_before_t_plus_hmax_plus_settle():
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    cutoff = pr.label_cutoff(now, LABELS)
    assert cutoff == now - timedelta(minutes=60, seconds=LABELS["settle_seconds"])

    captured = {}

    class FakeResult:
        def __init__(self, rows): self._rows = rows
        def first(self): return None
        def mappings(self): return self
        def all(self): return self._rows

    class FakeDB:
        async def execute(self, stmt, params=None):
            if params and "cutoff" in params:
                captured.update(params)
            return FakeResult([])

    out = asyncio.run(pr.label_pending(FakeDB(), {"labels": LABELS}, now=now))
    assert out["labelled"] == 0 and captured["cutoff"] == cutoff


def test_row_without_enough_future_prices_gets_null_labels():
    lab = pr.compute_labels(_series([100.0, 100.5]), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab["returns"]["15"]["gross"] is None and lab["returns"]["15"]["reason"] == "price_gap"
    assert all(b["y"] is None and b["reason"] == "price_gap" for b in lab["barriers"].values())


def test_tp_and_sl_in_the_same_minute_counts_as_sl():
    closes = [100.0] * 61
    highs, lows = list(closes), list(closes)
    highs[3], lows[3] = 110.0, 90.0
    lab = pr.compute_labels(_series(closes, highs, lows), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab["barriers"]["L1"] == {"y": 0, "t_touch": 3, "r_at_exit": -0.5, "reason": None}


def test_cost_is_applied_to_returns_and_barriers():
    closes = [100.0] * 61
    closes[5] = 101.2
    highs = list(closes)
    highs[2] = 101.2  # +1.2% gross: below TP 1.0% + cost 0.3% = 1.3% → no TP yet
    highs[7] = 101.4  # +1.4% gross: covers TP + cost
    lab = pr.compute_labels(_series(closes, highs), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab["returns"]["5"]["gross"] == pytest.approx(1.2)
    assert lab["returns"]["5"]["net"] == pytest.approx(0.9)
    assert lab["barriers"]["L1"]["y"] == 1 and lab["barriers"]["L1"]["t_touch"] == 7
    cost = pr.row_cost_pct({"slippage_buy_pct": 0.05, "slippage_sell_pct": 0.07}, 0.2)
    assert cost == pytest.approx(0.32)
    assert pr.row_cost_pct({"slippage_buy_pct": None, "slippage_sell_pct": 0.07}, 0.2) is None


def test_gap_above_threshold_nulls_the_label_and_never_interpolates():
    closes = [100.0] * 61
    lab = pr.compute_labels(_series(closes, skip={1}), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab["returns"]["15"]["gross"] == pytest.approx(0.0)  # 1 of 15 missing = 6.7% <= 10%
    lab5 = lab["returns"]["5"]                                   # 1 of 5 missing = 20% > 10%
    assert lab5["gross"] is None and lab5["reason"] == "price_gap"
    lab2 = pr.compute_labels(_series(closes, skip={1, 2}), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab2["returns"]["15"]["gross"] is None               # 2 of 15 missing = 13.3% > 10%
    assert lab2["barriers"]["L1"]["reason"] == "price_gap"
    lab_end = pr.compute_labels(_series(closes, skip={15}), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab_end["returns"]["15"]["gross"] is None and lab_end["returns"]["15"]["reason"] == "price_gap_endpoint"


def test_expired_barrier_reports_net_return_at_horizon():
    closes = [100.0] * 61
    closes[15] = 100.4
    lab = pr.compute_labels(_series(closes), t_ms=T0, cost_pct=0.3, labels=LABELS)
    assert lab["barriers"]["L1"]["y"] == -1
    assert lab["barriers"]["L1"]["r_at_exit"] == pytest.approx(0.1)


def test_write_failure_never_reaches_the_cycle():
    async def failing_run_db_task(fn, celery=False):
        raise RuntimeError("db down")

    assert asyncio.run(pr.write_safely(failing_run_db_task, [{"x": 1}], {})) is False


def test_research_minute_cadence_and_dedup():
    spec = {"enabled": True, "every_n_minutes": 2}
    assert pr.is_research_minute(T0, None, spec)
    assert not pr.is_research_minute(T0, T0, spec)            # second cycle in the same minute
    assert not pr.is_research_minute(T0 + M, None, spec)       # every 2 minutes
    assert not pr.is_research_minute(T0, None, {**spec, "enabled": False})


def test_research_row_records_what_the_cycle_computed():
    row = {
        "symbol": "TEST_USDT",
        "indicators": {
            "price": {"value": 99.0}, "spread_pct": {"value": 0.04},
            "estimated_slippage_buy_pct": {"value": None, "reason": "insufficient_depth"},
            "estimated_slippage_sell_pct": {"value": 0.08}, "psar_trend": {"value": "bullish"},
            "di_trend": {"value": True}, "delta_norm": {"value": None, "reason": "no_trades"},
        },
        "score_components": {"delta_norm": {"contribution": None}, "rvol_strict": {"contribution": 0.4}},
        "pump_monitor_score": 20.0, "score_pre_veto": 33.0, "score_confidence": 0.9,
        "exhaustion_flag": True, "exhaustion_triggers": ["wick"], "alerts_active": [{"type": "sell_absorption"}],
        "data_age_seconds": 120.0,
    }
    book = {"bids": [["99.9", "1"], ["99.8", "1"]], "asks": [["100.1", "1"]]}
    bucket = {"open_price": 99.5, "high_price": 100.2, "low_price": 99.4, "close_price": 100.0, "partial": False}
    rec, vk, ck = pr.research_row(row, minute_ms=T0, bucket=bucket, book=book, cycle_at_ms=T0 + 63_000,
                                  config_meta={"version": 26, "config_hash": "h"})
    assert vk == tuple(sorted(row["indicators"])) and ck == ("delta_norm", "rvol_strict")
    assert rec["ts"] == datetime.fromtimestamp(T0 / 1000, tz=timezone.utc)
    assert rec["price"] == 100.0 and rec["mid"] == pytest.approx(100.0)
    assert (rec["best_bid"], rec["best_ask"]) == (99.9, 100.1)
    assert rec["insufficient_depth_buy"] is True and rec["slippage_buy_pct"] is None
    assert rec["score"] == 20.0 and rec["score_pre_veto"] == 33.0 and rec["alerts"] == ["sell_absorption"]
    values = dict(zip(vk, rec["vals"]))
    assert values["di_trend"] == 1.0 and values["psar_trend"] is None
    assert rec["categorical"] == {"psar_trend": "bullish"}
    assert rec["null_reasons"] == {"delta_norm": "no_trades", "estimated_slippage_buy_pct": "insufficient_depth"}
    assert rec["contributions"] == [None, 0.4]

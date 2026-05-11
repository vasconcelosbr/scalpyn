"""Unit tests for position_lifecycle_service FIFO engine (Task #257).

These exercise the pure in-memory FIFO logic without touching the DB —
specifically the futures position-flip case and the spot DRIFT fallback,
both raised during code review.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.position_lifecycle_service import PositionLifecycleService


def _fill(*, side, qty, price, ts, fee=0.0, trade_id=None, role=None, order_id=None):
    return SimpleNamespace(
        side=side, quantity=qty, price=price, fee=fee, executed_at=ts,
        trade_id=trade_id or f"tid-{int(ts.timestamp())}-{side}",
        order_id=order_id, role=role,
    )


def test_spot_simple_round_trip_long():
    svc = PositionLifecycleService()
    uid = uuid4()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        _fill(side="buy",  qty=1.0, price=100.0, ts=t0),
        _fill(side="sell", qty=1.0, price=110.0, ts=t0 + timedelta(hours=1)),
    ]
    closed, opens = svc._process_group(uid, "BTC_USDT", "spot", fills)
    assert opens == []
    assert len(closed) == 1
    row = closed[0]
    assert row["status"] == "closed"
    assert row["data_quality"] == "OK"
    assert float(row["pnl_usdt"]) == 10.0
    assert row["direction"] == "long"


def test_spot_orphan_sell_emits_drift():
    """A sell that overshoots open lots must emit a DRIFT row for the
    leftover qty, not crash."""
    svc = PositionLifecycleService()
    uid = uuid4()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        _fill(side="buy",  qty=0.5, price=100.0, ts=t0),
        _fill(side="sell", qty=1.0, price=110.0, ts=t0 + timedelta(hours=1)),
    ]
    closed, _opens = svc._process_group(uid, "BTC_USDT", "spot", fills)
    quals = [r["data_quality"] for r in closed]
    assert "OK" in quals
    assert "DRIFT" in quals
    drift = [r for r in closed if r["data_quality"] == "DRIFT"][0]
    assert float(drift["qty"]) == 0.5


def test_spot_sell_with_empty_queue_emits_drift_not_silent_skip():
    """REGRESSION: a spot sell hitting an empty lot queue must NOT be
    silently dropped — it must produce a DRIFT row so the orphan close
    is visible in the audit trail (e.g. a deposit-derived asset that
    is later sold with no on-platform purchase fill).
    """
    svc = PositionLifecycleService()
    uid = uuid4()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        _fill(side="sell", qty=0.7, price=100.0, ts=t0),  # no prior buy at all
    ]
    closed, opens = svc._process_group(uid, "BTC_USDT", "spot", fills)
    assert opens == []
    assert len(closed) == 1, "orphan sell must produce exactly one DRIFT row"
    row = closed[0]
    assert row["data_quality"] == "DRIFT"
    assert row["status"] == "closed"
    assert float(row["qty"]) == 0.7
    assert row["exit_trade_ids"] == [fills[0].trade_id]


def test_futures_position_flip_opens_reverse():
    """A close fill exceeding open exposure must flip into a reverse position."""
    svc = PositionLifecycleService()
    uid = uuid4()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        _fill(side="buy",  qty=1.0, price=100.0, ts=t0),                     # open long 1
        _fill(side="sell", qty=3.0, price=110.0, ts=t0 + timedelta(hours=1)),  # close long + flip short 2
        _fill(side="buy",  qty=2.0, price=105.0, ts=t0 + timedelta(hours=2)),  # close short
    ]
    closed, opens = svc._process_group(uid, "BTC_USDT", "futures", fills)
    assert opens == [], "all positions should be flat after final fill"
    long_rows = [r for r in closed if r["direction"] == "long" and r["data_quality"] == "OK"]
    short_rows = [r for r in closed if r["direction"] == "short" and r["data_quality"] == "OK"]
    assert len(long_rows) == 1, "the original long should close exactly once"
    assert float(long_rows[0]["pnl_usdt"]) == 10.0  # (110-100)*1
    assert len(short_rows) == 1, "the flipped short should close exactly once"
    # short: opened at 110 (the sell), closed at 105 → (110-105)*2 = 10
    assert float(short_rows[0]["pnl_usdt"]) == 10.0
    assert float(short_rows[0]["qty"]) == 2.0


def test_futures_partial_close_keeps_remainder_open():
    svc = PositionLifecycleService()
    uid = uuid4()
    t0 = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    fills = [
        _fill(side="buy",  qty=2.0, price=100.0, ts=t0),
        _fill(side="sell", qty=0.5, price=110.0, ts=t0 + timedelta(hours=1)),
    ]
    closed, opens = svc._process_group(uid, "BTC_USDT", "futures", fills)
    assert len(closed) == 1
    assert float(closed[0]["qty"]) == 0.5
    assert len(opens) == 1
    assert float(opens[0]["qty"]) == 1.5
    assert opens[0]["status"] == "open"

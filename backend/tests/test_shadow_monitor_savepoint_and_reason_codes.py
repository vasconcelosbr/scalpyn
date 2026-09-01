from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models.shadow_trade import ShadowTrade
from app.models.trade_simulation import TradeSimulation
from app.services.shadow_barrier_evaluator import evaluate_closed_candles
from app.tasks import shadow_trade_monitor as monitor


class _NestedTransaction(AbstractAsyncContextManager):
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        self.db.savepoint_entered += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.savepoint_exits.append(exc_type)
        return False


class _FakeDb:
    def __init__(self, *, flush_error: Exception | None = None):
        self.flush_error = flush_error
        self.flush_calls = 0
        self.savepoint_entered = 0
        self.savepoint_exits = []

    def begin_nested(self):
        return _NestedTransaction(self)

    async def flush(self):
        self.flush_calls += 1
        if self.flush_error is not None:
            raise self.flush_error


def _shadow(**overrides):
    values = {
        "id": uuid4(),
        "symbol": "TEST_USDT",
        "entry_timestamp": object(),
        "decision_id": 10,
        "btc_price_at_entry": None,
        "btc_change_1h_pct": None,
        "funding_rate_at_entry": None,
        "n_concurrent_signals": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_shadow_advance_flushes_inside_savepoint(monkeypatch):
    db = _FakeDb()
    shadow = _shadow()
    advance = AsyncMock(return_value="completed")
    monkeypatch.setattr(monitor, "_advance_shadow", advance)

    transition, target = await monitor._advance_shadow_in_savepoint(
        db, shadow, {"enabled": False}
    )

    assert transition == "completed"
    assert target["shadow_id"] == shadow.id
    assert db.savepoint_entered == 1
    assert db.flush_calls == 1
    assert db.savepoint_exits == [None]


@pytest.mark.asyncio
async def test_shadow_flush_error_rolls_back_only_nested_transaction(monkeypatch):
    db = _FakeDb(flush_error=ValueError("varchar overflow"))
    shadow = _shadow()
    monkeypatch.setattr(
        monitor,
        "_advance_shadow",
        AsyncMock(return_value="running"),
    )

    with pytest.raises(ValueError, match="varchar overflow"):
        await monitor._advance_shadow_in_savepoint(db, shadow, None)

    assert db.savepoint_entered == 1
    assert db.flush_calls == 1
    assert db.savepoint_exits == [ValueError]


@pytest.mark.asyncio
async def test_invalid_shadow_does_not_stop_later_rows(monkeypatch):
    good_before = _shadow(symbol="GOOD1_USDT")
    invalid = _shadow(symbol="BAD_USDT")
    good_after = _shadow(symbol="GOOD2_USDT")

    async def _isolated(_db, shadow, _policy):
        if shadow.id == invalid.id:
            raise ValueError("varchar overflow")
        return "completed", monitor._snapshot_shadow_enrichment_target(shadow)

    monkeypatch.setattr(monitor, "_advance_shadow_in_savepoint", _isolated)
    summary = {"processed": 0, "completed": 0, "errors": 0}

    sim_targets, enrich_targets = await monitor._advance_shadow_batch_isolated(
        object(),
        [good_before, invalid, good_after],
        None,
        summary,
    )

    assert summary == {"processed": 3, "completed": 2, "errors": 1}
    assert sim_targets == [good_before.id, good_after.id]
    assert [item["shadow_id"] for item in enrich_targets] == [
        good_before.id,
        good_after.id,
    ]


@pytest.mark.parametrize(
    ("column", "codes"),
    [
        (ShadowTrade.__table__.c.status, ("PENDING", "RUNNING", "COMPLETED", "ERROR")),
        (ShadowTrade.__table__.c.outcome, ("TP_HIT", "SL_HIT", "TRAILING_STOP", "TIMEOUT")),
        (
            ShadowTrade.__table__.c.barrier_touched,
            ("TP", "SL", "TRAILING", "NONE", "BOTH_SAME_CANDLE"),
        ),
        (ShadowTrade.__table__.c.intrabar_convention, ("SL_FIRST",)),
        (
            ShadowTrade.__table__.c.exit_price_semantics,
            (
                "CLOSED_OHLCV_1M_FIRST_TOUCH_NOMINAL",
                "ENTRY_PARTIAL_CANDLE_UNRESOLVED",
                "TIMEOUT_CANDLE_CLOSE",
                "OBSERVED_SAMPLE_TRIGGER",
                "INTRABAR_TOUCH_NOMINAL",
            ),
        ),
        (ShadowTrade.__table__.c.ttt_close_reason, ("TP_HIT_IN_WINDOW", "HARD_TIMEOUT")),
        (
            TradeSimulation.__table__.c.barrier_touched,
            ("TP", "SL", "TRAILING", "NONE", "BOTH_SAME_CANDLE"),
        ),
        (TradeSimulation.__table__.c.intrabar_convention, ("SL_FIRST",)),
    ],
)
def test_persisted_shadow_codes_fit_declared_orm_width(column, codes):
    assert column.type.length is not None
    assert all(len(code) <= column.type.length for code in codes)


def test_unresolved_reason_code_is_exact_and_known_not_persistable_under_c1_schema():
    entry_at = datetime(2026, 9, 1, 5, 41, 47, tzinfo=timezone.utc)
    result = evaluate_closed_candles(
        [
            {
                "time": entry_at.replace(second=0, microsecond=0),
                "open": 5.529,
                "high": 5.58,
                "low": 5.529,
                "close": 5.57,
            }
        ],
        entry_price=5.466,
        entry_timestamp=entry_at,
        tp_price=5.54799,
        sl_price=5.38401,
    )

    code = result["reason_code"]
    width = ShadowTrade.__table__.c.barrier_touched.type.length
    assert code == "BARRIER_PATH_UNRESOLVED"
    assert len(code) == 23
    assert width == 20
    assert len(code) > width

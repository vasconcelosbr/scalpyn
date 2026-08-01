"""Read-only chart contract for the Shadow Portfolio trade replay."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.shadow_trades import get_shadow_trade_chart  # noqa: E402


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def fetchall(self):
        return self._rows


class _Session:
    def __init__(self, trade, candles):
        self._results = [_Result(scalar=trade), _Result(rows=candles)]
        self.params = []

    async def execute(self, _statement, params=None):
        self.params.append(params)
        return self._results.pop(0)


def test_chart_uses_30_minute_context_and_exact_trade_timestamps():
    shadow_id = uuid4()
    user_id = uuid4()
    entry = datetime(2026, 7, 31, 13, 40, 17, tzinfo=timezone.utc)
    exit_ = datetime(2026, 7, 31, 14, 21, 43, tzinfo=timezone.utc)
    trade = SimpleNamespace(
        id=shadow_id,
        user_id=user_id,
        symbol="DEXE_USDT",
        exchange="gateio",
        entry_timestamp=entry,
        created_at=entry,
        exit_timestamp=exit_,
        completed_at=exit_,
        entry_price=2.58,
        exit_price=2.66,
        tp_price=2.66,
        sl_price=2.50,
        outcome="TP_HIT",
    )
    candles = [
        SimpleNamespace(
            time=datetime(2026, 7, 31, 13, 40, tzinfo=timezone.utc),
            open=2.57,
            high=2.59,
            low=2.56,
            close=2.58,
            volume=100,
            timeframe="1m",
            exchange="gateio",
        )
    ]
    session = _Session(trade, candles)

    payload = asyncio.run(
        get_shadow_trade_chart(
            shadow_id=shadow_id,
            context_minutes=30,
            db=session,
            user_id=user_id,
        )
    )

    assert payload.entry_timestamp == entry
    assert payload.exit_timestamp == exit_
    assert payload.window_start == datetime(2026, 7, 31, 13, 10, 17, tzinfo=timezone.utc)
    assert payload.window_end == datetime(2026, 7, 31, 14, 51, 43, tzinfo=timezone.utc)
    assert payload.timeframe == "1m"
    assert payload.candles[0].close == 2.58
    assert session.params[1]["window_start"] == payload.window_start
    assert session.params[1]["window_end"] == payload.window_end


def test_chart_hides_trades_owned_by_another_user():
    session = _Session(None, [])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            get_shadow_trade_chart(
                shadow_id=uuid4(),
                context_minutes=30,
                db=session,
                user_id=uuid4(),
            )
        )

    assert exc_info.value.status_code == 404

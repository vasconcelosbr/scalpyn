"""Integration test for executions_sync_service paginated backfill (Task #257).

Uses an in-memory stub adapter that mimics Gate.io's ``page`` (spot) and
``last_id`` (futures) cursor semantics. Verifies:
  - exhaustion across many pages with no silent gap
  - dedup across overlapping page boundaries
  - max_pages cap surfaces in telemetry
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.executions_sync_service import ExecutionsSyncService


def _spot_row(idx: int, ts: datetime) -> Dict[str, Any]:
    return {
        "id": str(1_000_000 + idx),
        "order_id": f"o{idx}",
        "currency_pair": "BTC_USDT",
        "side": "buy" if idx % 2 == 0 else "sell",
        "amount": "0.01",
        "price": "100.0",
        "role": "taker",
        "fee": "0.0001",
        "fee_currency": "USDT",
        "create_time_ms": str(int(ts.timestamp() * 1000)),
    }


def _fut_row(idx: int, ts: datetime) -> Dict[str, Any]:
    return {
        "id": str(2_000_000 + idx),
        "order_id": f"of{idx}",
        "contract": "BTC_USDT",
        "size": "1" if idx % 2 == 0 else "-1",
        "price": "100.0",
        "role": "maker",
        "fee": "0.001",
        "create_time_ms": str(int(ts.timestamp() * 1000)),
    }


class _StubAdapter:
    """Mimics GateAdapter.get_my_spot_trades / get_my_futures_trades."""

    def __init__(self, spot_total: int, fut_total: int, page_size: int = 100):
        self.page_size = page_size
        base = datetime(2026, 5, 1, tzinfo=timezone.utc)
        # Spot: store as descending by id (newest first) to match Gate.
        self._spot_all: List[Dict[str, Any]] = [
            _spot_row(i, base + timedelta(seconds=i)) for i in range(spot_total)
        ][::-1]
        self._fut_all: List[Dict[str, Any]] = [
            _fut_row(i, base + timedelta(seconds=i)) for i in range(fut_total)
        ][::-1]
        self.spot_calls: List[Dict[str, Any]] = []
        self.fut_calls: List[Dict[str, Any]] = []

    async def get_my_spot_trades(self, **kwargs):
        self.spot_calls.append(kwargs)
        page = kwargs.get("page", 1)
        limit = kwargs.get("limit", self.page_size)
        start = (page - 1) * limit
        return self._spot_all[start:start + limit]

    async def get_my_futures_trades(self, **kwargs):
        self.fut_calls.append(kwargs)
        last_id = kwargs.get("last_id")
        limit = kwargs.get("limit", self.page_size)
        if last_id is None:
            window = self._fut_all
        else:
            # Rows strictly older (smaller id) than last_id, preserving order.
            target = int(last_id)
            window = [r for r in self._fut_all if int(r["id"]) < target]
        return window[:limit]


@pytest.mark.asyncio
async def test_spot_pagination_walks_all_pages_no_gap():
    svc = ExecutionsSyncService()
    stub = _StubAdapter(spot_total=257, fut_total=0, page_size=100)
    rows, tele = await svc._paginate_spot(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=25,
    )
    assert tele["raw_rows"] == 257
    assert tele["normalized_rows"] == 257
    assert tele["pages_walked"] == 3  # 100 + 100 + 57 (short page)
    assert tele["exhausted"] is True
    assert len({r["trade_id"] for r in rows}) == 257  # all unique


@pytest.mark.asyncio
async def test_spot_pagination_dedups_overlap():
    """Two pages returning an overlapping row must not double-import."""
    svc = ExecutionsSyncService()

    class _OverlapAdapter:
        def __init__(self):
            self.calls = 0
        async def get_my_spot_trades(self, **kwargs):
            self.calls += 1
            page = kwargs.get("page", 1)
            base = datetime(2026, 5, 1, tzinfo=timezone.utc)
            if page == 1:
                return [_spot_row(i, base) for i in range(100)]
            if page == 2:
                # Repeat last 5 rows of page 1 + 50 new rows, then short page.
                return ([_spot_row(i, base) for i in range(95, 100)]
                        + [_spot_row(i, base) for i in range(100, 150)])
            return []

    stub = _OverlapAdapter()
    rows, tele = await svc._paginate_spot(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=10,
    )
    # 100 + 50 unique = 150; the 5 overlapped rows must be dedup'd.
    assert len(rows) == 150
    assert len({r["trade_id"] for r in rows}) == 150


@pytest.mark.asyncio
async def test_spot_pagination_hits_max_pages_cap():
    svc = ExecutionsSyncService()
    # Provide enough rows so we *would* need >3 pages but cap at 3.
    stub = _StubAdapter(spot_total=1000, fut_total=0, page_size=100)
    rows, tele = await svc._paginate_spot(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=3,
    )
    assert tele["pages_walked"] == 3
    assert tele["exhausted"] is False
    assert len(rows) == 300


@pytest.mark.asyncio
async def test_futures_pagination_uses_last_id_cursor():
    svc = ExecutionsSyncService()
    stub = _StubAdapter(spot_total=0, fut_total=257, page_size=100)
    rows, tele = await svc._paginate_futures(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=25,
    )
    assert tele["raw_rows"] == 257
    assert tele["normalized_rows"] == 257
    assert tele["exhausted"] is True
    assert len({r["trade_id"] for r in rows}) == 257
    # First call must NOT have last_id; subsequent calls MUST.
    assert stub.fut_calls[0].get("last_id") is None
    for call in stub.fut_calls[1:]:
        assert call.get("last_id") is not None


@pytest.mark.asyncio
async def test_futures_pagination_dense_same_timestamp():
    """Even when many trades share the same create_time_ms, the id-based
    cursor must still advance correctly and not loop forever."""
    svc = ExecutionsSyncService()
    same_ts = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)

    class _DenseAdapter:
        def __init__(self):
            # 250 fills all at the exact same timestamp, ids 2_000_000..
            self.rows = sorted(
                [_fut_row(i, same_ts) for i in range(250)],
                key=lambda r: int(r["id"]),
                reverse=True,
            )
            self.calls = []
        async def get_my_futures_trades(self, **kwargs):
            self.calls.append(kwargs)
            last_id = kwargs.get("last_id")
            limit = kwargs.get("limit", 100)
            if last_id is None:
                window = self.rows
            else:
                tgt = int(last_id)
                window = [r for r in self.rows if int(r["id"]) < tgt]
            return window[:limit]

    stub = _DenseAdapter()
    rows, tele = await svc._paginate_futures(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=25,
    )
    assert len(rows) == 250
    assert tele["exhausted"] is True

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

from app.exchange_adapters.gate_adapter import GateHistoryWindowExceeded
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

    @staticmethod
    def _row_in_window(row: Dict[str, Any], from_ts: Optional[int], to_ts: Optional[int]) -> bool:
        # Honour Gate.io's ``from``/``to`` semantics so slice-based
        # paginators (Task #257 30-day chunking) don't over-count by
        # returning the same rows on every slice.
        ts_ms_raw = row.get("create_time_ms")
        try:
            ts_s = float(ts_ms_raw) / 1000.0
        except (TypeError, ValueError):
            return True
        if from_ts is not None and ts_s < from_ts:
            return False
        if to_ts is not None and ts_s > to_ts:
            return False
        return True

    async def get_my_spot_trades(self, **kwargs):
        self.spot_calls.append(kwargs)
        page = kwargs.get("page", 1)
        limit = kwargs.get("limit", self.page_size)
        from_ts = kwargs.get("from_ts")
        to_ts = kwargs.get("to_ts")
        windowed = [r for r in self._spot_all
                    if self._row_in_window(r, from_ts, to_ts)]
        start = (page - 1) * limit
        return windowed[start:start + limit]

    async def get_my_futures_trades(self, **kwargs):
        self.fut_calls.append(kwargs)
        last_id = kwargs.get("last_id")
        limit = kwargs.get("limit", self.page_size)
        from_ts = kwargs.get("from_ts")
        to_ts = kwargs.get("to_ts")
        windowed = [r for r in self._fut_all
                    if self._row_in_window(r, from_ts, to_ts)]
        if last_id is None:
            window = windowed
        else:
            # Rows strictly older (smaller id) than last_id, preserving order.
            target = int(last_id)
            window = [r for r in windowed if int(r["id"]) < target]
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
    # days=90 → 3 slices. Slice 1 (most recent 30d) walks 3 pages
    # (100 + 100 + 57 short page). Slices 2 and 3 are older than the
    # row timestamps, each walks 1 empty page and breaks. 3 + 1 + 1 = 5.
    assert tele["pages_walked"] == 5
    assert tele["slices"] == 3
    assert tele["exhausted"] is True
    assert tele["history_window_capped"] is False
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
    # Slice 1 walks max_pages=3 (300 rows, capped). Slices 2 and 3 each
    # walk 1 empty page and break (rows live in slice 1 only).
    assert tele["pages_walked"] == 5
    assert tele["slices"] == 3
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
    # The cursor is per-slice: every slice's first call MUST start with
    # last_id=None and at least one subsequent call within the slice that
    # actually paginated MUST carry a last_id. Filter to the slice that
    # produced data (slice 1 — the others returned empty pages).
    productive_calls = [c for c in stub.fut_calls if c.get("last_id") is not None]
    assert productive_calls, "expected at least one call with last_id set"
    assert stub.fut_calls[0].get("last_id") is None


@pytest.mark.asyncio
async def test_spot_pagination_stops_on_gate_history_window_exceeded():
    """Task #272: when Gate.io rejects a slice with ``invalid time range``
    (out of historical retention), the paginator must stop walking older
    slices, NOT propagate the 400 to the caller. Telemetry must record
    the cap so the UI can show a friendly message."""
    svc = ExecutionsSyncService()

    class _CappedAdapter:
        """Returns 100 fills for the most recent 30-day slice, then
        Gate.io's ``invalid time range`` error for older slices."""
        def __init__(self):
            self.calls: List[Dict[str, Any]] = []
            self._first_slice_from: Optional[int] = None

        async def get_my_spot_trades(self, **kwargs):
            self.calls.append(kwargs)
            from_ts = kwargs.get("from_ts")
            if self._first_slice_from is None:
                self._first_slice_from = from_ts
                base = datetime(2026, 5, 1, tzinfo=timezone.utc)
                page = kwargs.get("page", 1)
                if page == 1:
                    return [_spot_row(i, base) for i in range(50)]
                return []
            # Any older slice → Gate rejects with "invalid time range".
            raise GateHistoryWindowExceeded(
                400, "INVALID_PARAM_VALUE", "invalid time range"
            )

    stub = _CappedAdapter()
    rows, tele = await svc._paginate_spot(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=10,
    )
    # First slice succeeded with 50 rows; older slices were capped.
    assert len(rows) == 50
    assert tele["history_window_capped"] is True
    assert tele["effective_days"] == 30
    assert tele["requested_days"] == 90
    # Slices counter excludes the capped one; we walked exactly the
    # first slice plus one rejected probe of the second.
    assert tele["slices"] == 1
    # Pages walked counts only the successfully-walked first slice.
    assert tele["pages_walked"] == 1


@pytest.mark.asyncio
async def test_futures_pagination_stops_on_gate_history_window_exceeded():
    svc = ExecutionsSyncService()

    class _CappedFutAdapter:
        def __init__(self):
            self._first_call = True
        async def get_my_futures_trades(self, **kwargs):
            if self._first_call:
                self._first_call = False
                base = datetime(2026, 5, 1, tzinfo=timezone.utc)
                return [_fut_row(i, base) for i in range(20)]
            raise GateHistoryWindowExceeded(
                400, "INVALID_PARAM_VALUE", "invalid time range"
            )

    stub = _CappedFutAdapter()
    rows, tele = await svc._paginate_futures(
        adapter=stub, user_id=uuid4(),
        days=90, page_size=100, max_pages=10,
    )
    assert len(rows) == 20
    assert tele["history_window_capped"] is True
    assert tele["effective_days"] == 30
    assert tele["requested_days"] == 90


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

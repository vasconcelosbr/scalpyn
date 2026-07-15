from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.utils import indicator_merge


NOW = datetime(2026, 7, 12, 23, 45, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _NestedCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        self._db.begin_nested_calls += 1
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Db:
    def __init__(self):
        self.begin_nested_calls = 0
        self.execute_calls = 0

    def begin_nested(self):
        return _NestedCtx(self)

    async def execute(self, _stmt, _params):
        self.execute_calls += 1
        if self.execute_calls == 1:
            raise RuntimeError("optional dual query failed")
        return _Result(
            [
                SimpleNamespace(
                    symbol="BTC_USDT",
                    time=NOW,
                    indicators_json={
                        "rsi": 55.0,
                        "adx": 24.0,
                        "macd_histogram": 0.002,
                    },
                )
            ]
        )


@pytest.mark.asyncio
async def test_fetch_merged_indicators_isolates_optional_reads_with_savepoints():
    db = _Db()

    merged = await indicator_merge.fetch_merged_indicators(
        db,
        ["BTC_USDT"],
        now=NOW,
    )

    assert db.begin_nested_calls == 2
    assert "BTC_USDT" in merged
    flat = merged["BTC_USDT"].as_flat_dict()
    assert flat["rsi"] == 55.0
    assert flat["adx"] == 24.0
    assert flat["macd_histogram"] == 0.002

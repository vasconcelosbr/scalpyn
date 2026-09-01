from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.services import ohlcv_backfill_service as backfill_module
from app.services.ohlcv_backfill_service import OHLCVBackfillService
from app.services.research_ohlcv_service import (
    GateClosedCandleBatch,
    _closed_records,
    fetch_gate_closed_candles,
    validate_retention_contract,
)
from app.tasks import collect_research_ohlcv
from app.tasks.celery_app import (
    QUEUE_RESEARCH_OHLCV,
    TASK_ROUTES,
    celery_app,
)
from app.utils.gate_market_data import parse_gate_spot_candle


def _raw_candle(timestamp: int, closed: str) -> list[str]:
    return [
        str(timestamp),
        "100.0",
        "10.0",
        "11.0",
        "9.0",
        "9.5",
        "10.0",
        closed,
    ]


def test_gate_parser_preserves_explicit_closed_flag() -> None:
    assert parse_gate_spot_candle(_raw_candle(1_700_000_000, "true"))["is_closed"] is True
    assert parse_gate_spot_candle(_raw_candle(1_700_000_000, "false"))["is_closed"] is False


def test_research_filter_fails_closed_for_open_or_future_candles() -> None:
    observed_at = datetime.fromtimestamp(1_700_002_000, tz=timezone.utc)
    records, rejected = _closed_records(
        [
            _raw_candle(1_700_000_000, "true"),
            _raw_candle(1_700_000_900, "false"),
            _raw_candle(1_700_001_800, "true"),
        ],
        symbol="BTC_USDT",
        timeframe="15m",
        observed_at=observed_at,
    )
    assert len(records) == 1
    assert rejected == 2


@pytest.mark.asyncio
async def test_gate_range_request_never_combines_limit_with_from_to() -> None:
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(dict(request.url.params))
        return httpx.Response(
            200,
            json=[_raw_candle(1_700_000_000, "true")],
            headers={
                "X-Gate-RateLimit-Limit": "200",
                "X-Gate-RateLimit-Requests-Remain": "199",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        await fetch_gate_closed_candles(
            client,
            symbol="BTC_USDT",
            timeframe="15m",
            points=1000,
        )
        await fetch_gate_closed_candles(
            client,
            symbol="BTC_USDT",
            timeframe="15m",
            points=1000,
            to_timestamp=1_700_000_000,
        )

    assert seen_queries[0]["limit"] == "1000"
    assert "from" not in seen_queries[0]
    assert "to" not in seen_queries[0]
    assert "limit" not in seen_queries[1]
    assert seen_queries[1]["to"] == "1700000000"
    assert "from" in seen_queries[1]


def test_retention_values_are_distinct_and_exceed_targets(monkeypatch) -> None:
    monkeypatch.setenv("OHLCV_RESEARCH_RETENTION_15M_DAYS", "180")
    monkeypatch.setenv("OHLCV_RESEARCH_RETENTION_1H_DAYS", "730")
    monkeypatch.setenv("OHLCV_RESEARCH_TARGET_15M_CANDLES", "2000")
    monkeypatch.setenv("OHLCV_RESEARCH_TARGET_1H_CANDLES", "1000")
    assert validate_retention_contract() == {"15m": 180, "1h": 730}


def test_research_tasks_are_isolated_and_have_no_decision_dispatch() -> None:
    names = {
        "app.tasks.collect_research_ohlcv.collect_15m",
        "app.tasks.collect_research_ohlcv.collect_1h",
        "app.tasks.collect_research_ohlcv.enforce_retention",
        "app.tasks.collect_research_ohlcv.capture_readiness",
        "app.tasks.ohlcv_backfill.backfill_research",
    }
    assert all(
        TASK_ROUTES[name]["queue"] == QUEUE_RESEARCH_OHLCV
        for name in names
    )
    source = inspect.getsource(collect_research_ohlcv)
    assert "task_dispatch" not in source
    assert "compute_indicators" not in source
    assert "compute_scores" not in source
    assert "evaluate_signals" not in source
    assert "timeframe = '1m'" not in inspect.getsource(
        collect_research_ohlcv._retention_async
    )
    assert "timeframe = '5m'" not in inspect.getsource(
        collect_research_ohlcv._retention_async
    )


def test_research_beat_entries_explicitly_target_isolated_queue() -> None:
    schedule = celery_app.conf.beat_schedule
    for name in (
        "collect_research_15m_after_close",
        "collect_research_1h_after_close",
        "capture_research_ohlcv_readiness",
        "enforce_research_ohlcv_retention",
    ):
        assert schedule[name]["options"]["queue"] == QUEUE_RESEARCH_OHLCV


@pytest.mark.asyncio
async def test_research_backfill_is_idempotent_at_target(monkeypatch) -> None:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    persisted: set[datetime] = set()

    class FakeRepository:
        def __init__(self, _session) -> None:
            pass

        async def count_records(self, *_args) -> int:
            return len(persisted)

        async def bulk_insert_ohlcv(self, records, batch_size=1000) -> int:
            before = len(persisted)
            persisted.update(record["time"] for record in records)
            return len(persisted) - before

    async def fake_fetch(
        _client,
        *,
        symbol,
        timeframe,
        points,
        to_timestamp=None,
    ) -> GateClosedCandleBatch:
        end = (
            datetime.fromtimestamp(to_timestamp, tz=timezone.utc)
            if to_timestamp is not None
            else base.replace(day=20)
        )
        records = tuple(
            {
                "time": end.replace(microsecond=0) - timedelta(minutes=15 * i),
                "symbol": symbol,
                "exchange": "gate.io",
                "timeframe": timeframe,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1.0,
                "quote_volume": 1.0,
            }
            for i in range(points)
        )
        return GateClosedCandleBatch(
            symbol=symbol,
            timeframe=timeframe,
            observed_at=datetime.now(timezone.utc),
            records=tuple(sorted(records, key=lambda row: row["time"])),
            rejected_open_candles=0,
            rate_limit="200",
            rate_limit_remaining="199",
        )

    async def no_delay() -> None:
        return None

    monkeypatch.setattr(backfill_module, "OHLCVRepository", FakeRepository)
    monkeypatch.setattr(backfill_module, "fetch_gate_closed_candles", fake_fetch)
    monkeypatch.setattr(backfill_module, "paced_request_delay", no_delay)
    service = OHLCVBackfillService(session=object())

    first = await service.backfill_research_symbol(
        "BTC_USDT", "15m", target_candles=2_000
    )
    second = await service.backfill_research_symbol(
        "BTC_USDT", "15m", target_candles=2_000
    )
    assert first["target_reached"] is True
    assert first["inserted"] == 2_000
    assert second["skipped"] is True
    assert second["inserted"] == 0

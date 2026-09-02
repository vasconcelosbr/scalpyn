"""Exchange-native OHLCV collection with explicit candle-state separation.

This module deliberately has no indicator, score, signal, profile, or trading
imports. The established 15m/1h path writes closed candles to ``ohlcv``. The
1m/5m/30m dual-run writes closed candles to ``ohlcv_shadow`` and the current
mutable candle to ``ohlcv_live``; neither table is a decision input in v1.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.ohlcv_repository import OHLCVRepository
from ..utils.gate_market_data import parse_gate_spot_candle

logger = logging.getLogger(__name__)

GATE_SPOT_CANDLES_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
SUPPORTED_TIMEFRAMES: Mapping[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
}
CANONICAL_RESEARCH_TIMEFRAMES = ("15m", "1h")
SHADOW_STATE_TIMEFRAMES = ("1m", "5m", "30m")
DEFAULT_TARGET_CANDLES: Mapping[str, int] = {
    "1m": 1_000,
    "5m": 1_000,
    "15m": 2_000,
    "30m": 1_000,
    "1h": 1_000,
}
DEFAULT_RETENTION_DAYS: Mapping[str, int] = {"15m": 180, "1h": 730}
STATE_CAPTURE_CONTRACT_VERSION = "gate_ohlcv_state_v1"


def _positive_env_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _non_negative_env_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def target_candles(timeframe: str) -> int:
    validate_timeframe(timeframe)
    return _positive_env_int(
        f"OHLCV_RESEARCH_TARGET_{timeframe.upper()}_CANDLES",
        DEFAULT_TARGET_CANDLES[timeframe],
    )


def retention_days(timeframe: str) -> int:
    if timeframe not in CANONICAL_RESEARCH_TIMEFRAMES:
        raise ValueError(
            f"retention is not defined for shadow-state timeframe {timeframe!r}"
        )
    return _positive_env_int(
        f"OHLCV_RESEARCH_RETENTION_{timeframe.upper()}_DAYS",
        DEFAULT_RETENTION_DAYS[timeframe],
    )


def request_delay_seconds() -> float:
    return _non_negative_env_float("OHLCV_RESEARCH_REQUEST_DELAY_SECONDS", 0.25)


def validate_timeframe(timeframe: str) -> None:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"unsupported research OHLCV timeframe {timeframe!r}; "
            f"allowed={tuple(SUPPORTED_TIMEFRAMES)}"
        )


def validate_retention_contract() -> dict[str, int]:
    values = {tf: retention_days(tf) for tf in CANONICAL_RESEARCH_TIMEFRAMES}
    if values["15m"] == values["1h"]:
        raise ValueError("15m and 1h retention values must remain distinct")
    for timeframe, days in values.items():
        minimum_days = (
            target_candles(timeframe) * SUPPORTED_TIMEFRAMES[timeframe]
        ) / 86_400
        if days <= minimum_days:
            raise ValueError(
                f"{timeframe} retention ({days} days) must exceed the "
                f"target-candle horizon ({minimum_days:.3f} days)"
            )
    return values


@dataclass(frozen=True)
class GateClosedCandleBatch:
    symbol: str
    timeframe: str
    observed_at: datetime
    records: tuple[dict[str, Any], ...]
    rejected_open_candles: int
    rate_limit: str | None
    rate_limit_remaining: str | None
    live_records: tuple[dict[str, Any], ...] = ()

    @property
    def latest_open_time(self) -> datetime | None:
        return self.records[-1]["time"] if self.records else None

    @property
    def latest_close_time(self) -> datetime | None:
        if self.latest_open_time is None:
            return None
        return self.latest_open_time + timedelta(
            seconds=SUPPORTED_TIMEFRAMES[self.timeframe]
        )

    @property
    def availability_lag_seconds(self) -> float | None:
        if self.latest_close_time is None:
            return None
        return max(
            0.0,
            (self.observed_at - self.latest_close_time).total_seconds(),
        )


def _closed_records(
    raw_candles: Iterable[Sequence[Any]],
    *,
    symbol: str,
    timeframe: str,
    observed_at: datetime,
) -> tuple[tuple[dict[str, Any], ...], int]:
    """Normalize and fail closed unless Gate marks each candle complete."""
    closed, _live, rejected = _partition_records(
        raw_candles,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
    )
    return closed, rejected


def _partition_records(
    raw_candles: Iterable[Sequence[Any]],
    *,
    symbol: str,
    timeframe: str,
    observed_at: datetime,
) -> tuple[
    tuple[dict[str, Any], ...],
    tuple[dict[str, Any], ...],
    int,
]:
    """Partition Gate candles into immutable closed and mutable live states."""
    interval_seconds = SUPPORTED_TIMEFRAMES[timeframe]
    closed_by_time: dict[datetime, dict[str, Any]] = {}
    live_by_time: dict[datetime, dict[str, Any]] = {}
    rejected_open = 0

    for raw in raw_candles:
        parsed = parse_gate_spot_candle(raw)
        opened_at = parsed["time"]
        close_time = opened_at + timedelta(seconds=interval_seconds)
        record = {
            "time": opened_at,
            "symbol": symbol,
            "exchange": "gate.io",
            "timeframe": timeframe,
            "market_type": "spot",
            "open": parsed["open"],
            "high": parsed["high"],
            "low": parsed["low"],
            "close": parsed["close"],
            "volume": parsed["volume"],
            "quote_volume": parsed["quote_volume"],
        }
        if parsed.get("is_closed") is not True or close_time > observed_at:
            rejected_open += 1
            live_by_time[opened_at] = record
            continue
        closed_by_time[opened_at] = record

    return (
        tuple(closed_by_time[key] for key in sorted(closed_by_time)),
        tuple(live_by_time[key] for key in sorted(live_by_time)),
        rejected_open,
    )


async def fetch_gate_closed_candles(
    client: httpx.AsyncClient,
    *,
    symbol: str,
    timeframe: str,
    points: int,
    to_timestamp: int | None = None,
) -> GateClosedCandleBatch:
    """Fetch one Gate window, using API-compatible recent/range parameters."""
    validate_timeframe(timeframe)
    if not 1 <= points <= 1_000:
        raise ValueError("Gate candlestick points must be between 1 and 1000")

    params: dict[str, Any] = {
        "currency_pair": symbol,
        "interval": timeframe,
    }
    if to_timestamp is None:
        params["limit"] = points
    else:
        interval_seconds = SUPPORTED_TIMEFRAMES[timeframe]
        params["from"] = to_timestamp - ((points - 1) * interval_seconds)
        params["to"] = to_timestamp

    response = await client.get(GATE_SPOT_CANDLES_URL, params=params)
    if response.status_code == 429:
        retry_after = max(float(response.headers.get("Retry-After", "5")), 0.0)
        raise RuntimeError(f"GATE_RATE_LIMITED retry_after={retry_after}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError(
            f"unexpected Gate candlestick payload for {symbol}/{timeframe}"
        )

    observed_at = datetime.now(timezone.utc)
    records, live_records, rejected_open = _partition_records(
        payload,
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
    )
    return GateClosedCandleBatch(
        symbol=symbol,
        timeframe=timeframe,
        observed_at=observed_at,
        records=records,
        rejected_open_candles=rejected_open,
        rate_limit=response.headers.get("X-Gate-RateLimit-Limit"),
        rate_limit_remaining=response.headers.get(
            "X-Gate-RateLimit-Requests-Remain"
        ),
        live_records=live_records,
    )


async def record_ingestion_observation(
    session: AsyncSession,
    *,
    batch: GateClosedCandleBatch,
    inserted_rows: int,
    status: str,
    error_code: str | None = None,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO ohlcv_ingestion_observations
                (observed_at, symbol, timeframe, source,
                 latest_open_time, close_time, availability_lag_seconds,
                 received_rows, inserted_rows, rejected_open_candles,
                 status, error_code)
            VALUES
                (:observed_at, :symbol, :timeframe, 'gate.io',
                 :latest_open_time, :close_time, :availability_lag_seconds,
                 :received_rows, :inserted_rows, :rejected_open_candles,
                 :status, :error_code)
            ON CONFLICT (observed_at, symbol, timeframe) DO NOTHING
            """
        ),
        {
            "observed_at": batch.observed_at,
            "symbol": batch.symbol,
            "timeframe": batch.timeframe,
            "latest_open_time": batch.latest_open_time,
            "close_time": batch.latest_close_time,
            "availability_lag_seconds": batch.availability_lag_seconds,
            "received_rows": len(batch.records),
            "inserted_rows": inserted_rows,
            "rejected_open_candles": batch.rejected_open_candles,
            "status": status,
            "error_code": error_code,
        },
    )
    await session.commit()


async def persist_gate_closed_batch(
    session: AsyncSession,
    batch: GateClosedCandleBatch,
) -> int:
    repository = OHLCVRepository(session)
    inserted = await repository.bulk_insert_ohlcv(list(batch.records))
    await record_ingestion_observation(
        session,
        batch=batch,
        inserted_rows=inserted,
        status="ok" if batch.records else "empty",
    )
    return inserted


async def persist_gate_state_batch(
    session: AsyncSession,
    batch: GateClosedCandleBatch,
    *,
    capture_contract_version: str = STATE_CAPTURE_CONTRACT_VERSION,
) -> tuple[int, int]:
    """Persist one dual-run batch without mutating canonical ``ohlcv``."""
    if batch.timeframe not in SHADOW_STATE_TIMEFRAMES:
        raise ValueError(
            f"state capture is unsupported for timeframe {batch.timeframe!r}"
        )

    inserted_closed = 0
    for record in batch.records:
        result = await session.execute(
            text(
                """
                INSERT INTO ohlcv_shadow
                    (time, symbol, exchange, timeframe, market_type,
                     open, high, low, close, volume, quote_volume,
                     is_closed, capture_contract_version)
                VALUES
                    (:time, :symbol, :exchange, :timeframe, :market_type,
                     :open, :high, :low, :close, :volume, :quote_volume,
                     TRUE, :capture_contract_version)
                ON CONFLICT (time, symbol, exchange, timeframe) DO NOTHING
                """
            ),
            {**record, "capture_contract_version": capture_contract_version},
        )
        inserted_closed += max(int(result.rowcount or 0), 0)

    latest_closed_open = batch.latest_open_time
    if latest_closed_open is not None:
        await session.execute(
            text(
                """
                DELETE FROM ohlcv_live
                 WHERE symbol = :symbol
                   AND exchange = 'gate.io'
                   AND timeframe = :timeframe
                   AND time <= :latest_closed_open
                """
            ),
            {
                "symbol": batch.symbol,
                "timeframe": batch.timeframe,
                "latest_closed_open": latest_closed_open,
            },
        )

    upserted_live = 0
    for record in batch.live_records:
        result = await session.execute(
            text(
                """
                INSERT INTO ohlcv_live
                    (time, symbol, exchange, timeframe, market_type,
                     open, high, low, close, volume, quote_volume,
                     is_closed, capture_contract_version)
                VALUES
                    (:time, :symbol, :exchange, :timeframe, :market_type,
                     :open, :high, :low, :close, :volume, :quote_volume,
                     FALSE, :capture_contract_version)
                ON CONFLICT (time, symbol, exchange, timeframe) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    quote_volume = EXCLUDED.quote_volume,
                    is_closed = FALSE,
                    ingested_at = clock_timestamp(),
                    capture_contract_version = EXCLUDED.capture_contract_version
                """
            ),
            {**record, "capture_contract_version": capture_contract_version},
        )
        upserted_live += max(int(result.rowcount or 0), 0)

    await session.execute(
        text(
            """
            INSERT INTO ohlcv_state_ingestion_observations
                (observed_at, symbol, timeframe, source,
                 capture_contract_version, latest_closed_open_time,
                 latest_closed_close_time, availability_lag_seconds,
                 received_rows, inserted_closed_rows, upserted_live_rows,
                 rejected_from_closed_rows, status, error_code)
            VALUES
                (:observed_at, :symbol, :timeframe, 'gate.io',
                 :capture_contract_version, :latest_closed_open_time,
                 :latest_closed_close_time, :availability_lag_seconds,
                 :received_rows, :inserted_closed_rows, :upserted_live_rows,
                 :rejected_from_closed_rows, :status, NULL)
            ON CONFLICT
                (observed_at, symbol, timeframe, capture_contract_version)
            DO NOTHING
            """
        ),
        {
            "observed_at": batch.observed_at,
            "symbol": batch.symbol,
            "timeframe": batch.timeframe,
            "capture_contract_version": capture_contract_version,
            "latest_closed_open_time": batch.latest_open_time,
            "latest_closed_close_time": batch.latest_close_time,
            "availability_lag_seconds": batch.availability_lag_seconds,
            "received_rows": len(batch.records) + len(batch.live_records),
            "inserted_closed_rows": inserted_closed,
            "upserted_live_rows": upserted_live,
            "rejected_from_closed_rows": batch.rejected_open_candles,
            "status": "ok" if batch.records else "empty",
        },
    )
    await session.commit()
    return inserted_closed, upserted_live


async def record_gate_state_error(
    session: AsyncSession,
    *,
    symbol: str,
    timeframe: str,
    observed_at: datetime,
    error_code: str,
    capture_contract_version: str = STATE_CAPTURE_CONTRACT_VERSION,
) -> None:
    await session.execute(
        text(
            """
            INSERT INTO ohlcv_state_ingestion_observations
                (observed_at, symbol, timeframe, source,
                 capture_contract_version, received_rows,
                 inserted_closed_rows, upserted_live_rows,
                 rejected_from_closed_rows, status, error_code)
            VALUES
                (:observed_at, :symbol, :timeframe, 'gate.io',
                 :capture_contract_version, 0, 0, 0, 0, 'error', :error_code)
            ON CONFLICT
                (observed_at, symbol, timeframe, capture_contract_version)
            DO NOTHING
            """
        ),
        {
            "observed_at": observed_at,
            "symbol": symbol,
            "timeframe": timeframe,
            "capture_contract_version": capture_contract_version,
            "error_code": error_code[:100],
        },
    )
    await session.commit()


async def paced_request_delay() -> None:
    delay = request_delay_seconds()
    if delay:
        await asyncio.sleep(delay)

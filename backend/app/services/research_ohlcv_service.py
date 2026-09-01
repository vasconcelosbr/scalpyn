"""Exchange-native OHLCV collection for the research-only 15m/1h layer.

This module deliberately has no indicator, score, signal, profile, or trading
imports.  Its only write targets are ``ohlcv`` for the explicitly supported
timeframes and the additive ingestion-observation table.
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
SUPPORTED_TIMEFRAMES: Mapping[str, int] = {"15m": 15 * 60, "1h": 60 * 60}
DEFAULT_TARGET_CANDLES: Mapping[str, int] = {"15m": 2_000, "1h": 1_000}
DEFAULT_RETENTION_DAYS: Mapping[str, int] = {"15m": 180, "1h": 730}


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
    validate_timeframe(timeframe)
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
    values = {tf: retention_days(tf) for tf in SUPPORTED_TIMEFRAMES}
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
    interval_seconds = SUPPORTED_TIMEFRAMES[timeframe]
    by_time: dict[datetime, dict[str, Any]] = {}
    rejected_open = 0

    for raw in raw_candles:
        parsed = parse_gate_spot_candle(raw)
        opened_at = parsed["time"]
        close_time = opened_at + timedelta(seconds=interval_seconds)
        if parsed.get("is_closed") is not True or close_time > observed_at:
            rejected_open += 1
            continue
        by_time[opened_at] = {
            "time": opened_at,
            "symbol": symbol,
            "exchange": "gate.io",
            "timeframe": timeframe,
            "open": parsed["open"],
            "high": parsed["high"],
            "low": parsed["low"],
            "close": parsed["close"],
            "volume": parsed["volume"],
            "quote_volume": parsed["quote_volume"],
        }

    return tuple(by_time[key] for key in sorted(by_time)), rejected_open


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
    records, rejected_open = _closed_records(
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


async def paced_request_delay() -> None:
    delay = request_delay_seconds()
    if delay:
        await asyncio.sleep(delay)

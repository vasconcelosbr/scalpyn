"""Canonical, append-only Shadow Portfolio measurement reconciliation.

The functions in this module are observational.  They never choose barriers,
change an outcome, or mutate ``shadow_trades``.  Candle timestamps identify the
start of the bucket containing an extreme; they are not sub-candle event times.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.shadow_trade import ShadowTrade
from ..models.shadow_trade_measurement import ShadowTradeMeasurementRevision


MEASUREMENT_CONTRACT_VERSION = "shadow_measurement_v1"
COST_CONTRACT_VERSION = "fee_only_v1"
_TIMEFRAME_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600}


@dataclass(frozen=True)
class MeasurementResult:
    status: str
    method: str
    source: str
    timeframe: str | None
    resolution_seconds: int | None
    input_hash: str
    input_snapshot: dict[str, Any]
    mae_pct: float | None
    mfe_pct: float | None
    mae_at: datetime | None
    mfe_at: datetime | None
    gross_return_pct: float | None
    entry_boundary_partial: bool
    exit_boundary_partial: bool
    unavailable_reason: str | None = None


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value.normalize())
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _hash(value: Any) -> str:
    payload = json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def calculate_measurement(
    *,
    entry_price: float,
    entry_at: datetime,
    exit_price: float,
    exit_at: datetime,
    timeframe: str,
    candles: Iterable[Mapping[str, Any]],
    observed_at: datetime | None = None,
) -> MeasurementResult:
    """Calculate full-life excursions from every overlapping closed candle."""
    resolution = _TIMEFRAME_SECONDS.get(timeframe)
    if resolution is None:
        raise ValueError(f"unsupported_measurement_timeframe:{timeframe}")
    if entry_price <= 0:
        raise ValueError("entry_price_must_be_positive")
    entry_at, exit_at = _utc(entry_at), _utc(exit_at)
    if exit_at < entry_at:
        raise ValueError("exit_precedes_entry")
    observed_at = _utc(observed_at or datetime.now(timezone.utc))

    rows: list[dict[str, Any]] = []
    for raw in candles:
        candle_at = _utc(raw["time"])
        candle_end = candle_at + timedelta(seconds=resolution)
        if candle_at > exit_at or candle_end < entry_at:
            continue
        rows.append({"time": candle_at, "high": float(raw["high"]), "low": float(raw["low"])})
    rows.sort(key=lambda row: row["time"])

    source = f"ohlcv_{timeframe}"
    hash_snapshot = {
        "contract_version": MEASUREMENT_CONTRACT_VERSION,
        "entry_price": entry_price,
        "entry_at": entry_at,
        "exit_price": exit_price,
        "exit_at": exit_at,
        "source": source,
        "resolution_seconds": resolution,
        "candles": rows,
    }
    compact_snapshot = {
        key: value for key, value in hash_snapshot.items() if key != "candles"
    }
    compact_snapshot.update(
        {
            "candle_count": len(rows),
            "first_candle_at": rows[0]["time"] if rows else None,
            "last_candle_at": rows[-1]["time"] if rows else None,
            "candles_hash": _hash(rows),
        }
    )
    if not rows:
        input_hash = _hash(hash_snapshot)
        return MeasurementResult(
            status="UNAVAILABLE",
            method="full_life_overlapping_closed_ohlcv",
            source="unavailable",
            timeframe=timeframe,
            resolution_seconds=resolution,
            input_hash=input_hash,
            input_snapshot=_json_value(compact_snapshot),
            mae_pct=None,
            mfe_pct=None,
            mae_at=None,
            mfe_at=None,
            gross_return_pct=None,
            entry_boundary_partial=False,
            exit_boundary_partial=False,
            unavailable_reason="OHLCV_NOT_AVAILABLE",
        )

    exit_bucket = rows[-1]["time"] + timedelta(seconds=resolution)
    hash_snapshot["exit_boundary_closed"] = exit_bucket <= observed_at
    compact_snapshot["exit_boundary_closed"] = exit_bucket <= observed_at
    input_hash = _hash(hash_snapshot)
    if exit_bucket > observed_at:
        return MeasurementResult(
            status="PENDING",
            method="full_life_overlapping_closed_ohlcv",
            source=source,
            timeframe=timeframe,
            resolution_seconds=resolution,
            input_hash=input_hash,
            input_snapshot=_json_value(compact_snapshot),
            mae_pct=None,
            mfe_pct=None,
            mae_at=None,
            mfe_at=None,
            gross_return_pct=None,
            entry_boundary_partial=True,
            exit_boundary_partial=True,
            unavailable_reason="EXIT_BOUNDARY_CANDLE_NOT_CLOSED",
        )

    lows = [(entry_price, entry_at), (exit_price, exit_at), *((row["low"], row["time"]) for row in rows)]
    highs = [(entry_price, entry_at), (exit_price, exit_at), *((row["high"], row["time"]) for row in rows)]
    low, low_at = min(lows, key=lambda item: item[0])
    high, high_at = max(highs, key=lambda item: item[0])
    mae_pct = ((low / entry_price) - 1.0) * 100.0
    mfe_pct = ((high / entry_price) - 1.0) * 100.0
    gross_return_pct = ((exit_price / entry_price) - 1.0) * 100.0
    status = "READY" if mae_pct <= gross_return_pct <= mfe_pct else "ERROR"
    reason = None if status == "READY" else "EXTREMA_INVARIANT_VIOLATION"
    return MeasurementResult(
        status=status,
        method="full_life_overlapping_closed_ohlcv",
        source=source,
        timeframe=timeframe,
        resolution_seconds=resolution,
        input_hash=input_hash,
        input_snapshot=_json_value(compact_snapshot),
        mae_pct=mae_pct,
        mfe_pct=mfe_pct,
        mae_at=low_at,
        mfe_at=high_at,
        gross_return_pct=gross_return_pct,
        entry_boundary_partial=rows[0]["time"] < entry_at,
        exit_boundary_partial=rows[-1]["time"] < exit_at,
        unavailable_reason=reason,
    )


async def load_candles_for_measurement(
    db: AsyncSession,
    *,
    symbol: str,
    entry_at: datetime,
    exit_at: datetime,
    timeframe_priority: Sequence[str] | None,
) -> tuple[str | None, list[Mapping[str, Any]]]:
    """Use the first configured timeframe that has overlapping OHLCV."""
    if not timeframe_priority:
        return None, []
    for timeframe in timeframe_priority:
        resolution = _TIMEFRAME_SECONDS.get(timeframe)
        if resolution is None:
            raise ValueError(f"unsupported_measurement_timeframe:{timeframe}")
        result = await db.execute(
            text(
                """
                SELECT time, high, low
                  FROM ohlcv
                 WHERE symbol = :symbol
                   AND timeframe = :timeframe
                   AND time <= :exit_at
                   AND time >= :entry_floor
                 ORDER BY time ASC
                """
            ),
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "exit_at": exit_at,
                "entry_floor": entry_at - timedelta(seconds=resolution),
            },
        )
        rows = list(result.mappings().all())
        if rows:
            return timeframe, rows
    return None, []


def _entry_quality(*, source_at: datetime | None, decision_at: datetime, max_lag_seconds: int | None) -> tuple[str, float | None]:
    if max_lag_seconds is None:
        return "UNCONFIGURED", None if source_at is None else (decision_at - source_at).total_seconds()
    if source_at is None:
        return "UNAVAILABLE", None
    lag = (decision_at - source_at).total_seconds()
    return ("DEGRADED" if lag > max_lag_seconds else "OK"), lag


async def build_measurement_revision(
    db: AsyncSession,
    shadow: ShadowTrade,
    *,
    timeframe_priority: Sequence[str] | None,
    max_entry_lag_seconds: int | None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a revision dictionary without writing it."""
    config = shadow.config_snapshot if isinstance(shadow.config_snapshot, dict) else {}
    source_raw = config.get("entry_price_source_at")
    source_at = datetime.fromisoformat(source_raw.replace("Z", "+00:00")) if isinstance(source_raw, str) else source_raw
    entry_quality, lag = _entry_quality(
        source_at=source_at,
        decision_at=shadow.entry_timestamp,
        max_lag_seconds=max_entry_lag_seconds,
    )
    base = {
        "shadow_trade_id": shadow.id,
        "measurement_contract_version": MEASUREMENT_CONTRACT_VERSION,
        "legacy_entry_price": shadow.entry_price,
        "entry_price_reference": config.get("entry_price_reference", shadow.entry_price),
        "entry_price_observed": config.get("entry_price_observed"),
        "entry_price_realized": None,
        "entry_price_source_at": source_at,
        "entry_price_lag_seconds": lag,
        "entry_quality": entry_quality,
        "legacy_mae_pct": shadow.mae_pct,
        "legacy_mfe_pct": shadow.mfe_pct,
        "legacy_mae_at": shadow.mae_at,
        "legacy_mfe_at": shadow.mfe_at,
        "gross_return_pct": shadow.pnl_pct,
        "fee_roundtrip_pct_applied": shadow.fee_roundtrip_pct_applied,
        "net_return_pct": shadow.net_return_pct,
        "cost_contract_version": COST_CONTRACT_VERSION,
    }
    if not timeframe_priority:
        unavailable = {
            "status": "UNAVAILABLE",
            "method": "full_life_overlapping_closed_ohlcv",
            "source": "unavailable",
            "timeframe": None,
            "resolution_seconds": None,
            "input_hash": _hash({"shadow_trade_id": shadow.id, "reason": "MEASUREMENT_TIMEFRAME_UNCONFIGURED"}),
            "input_snapshot": {"reason": "MEASUREMENT_TIMEFRAME_UNCONFIGURED"},
            "mae_pct": None,
            "mfe_pct": None,
            "mae_at": None,
            "mfe_at": None,
            "entry_boundary_partial": False,
            "exit_boundary_partial": False,
            "unavailable_reason": "MEASUREMENT_TIMEFRAME_UNCONFIGURED",
        }
        return {**base, **unavailable}
    if not all((shadow.entry_price, shadow.entry_timestamp, shadow.exit_price, shadow.exit_timestamp)):
        unavailable = {
            "status": "UNAVAILABLE",
            "method": "full_life_overlapping_closed_ohlcv",
            "source": "unavailable",
            "timeframe": None,
            "resolution_seconds": None,
            "input_hash": _hash({"shadow_trade_id": shadow.id, "reason": "TERMINAL_FIELDS_MISSING"}),
            "input_snapshot": {"reason": "TERMINAL_FIELDS_MISSING"},
            "mae_pct": None,
            "mfe_pct": None,
            "mae_at": None,
            "mfe_at": None,
            "entry_boundary_partial": False,
            "exit_boundary_partial": False,
            "unavailable_reason": "TERMINAL_FIELDS_MISSING",
        }
        return {**base, **unavailable}
    timeframe, candles = await load_candles_for_measurement(
        db,
        symbol=shadow.symbol,
        entry_at=shadow.entry_timestamp,
        exit_at=shadow.exit_timestamp,
        timeframe_priority=timeframe_priority,
    )
    if timeframe is None:
        result = calculate_measurement(
            entry_price=float(shadow.entry_price),
            entry_at=shadow.entry_timestamp,
            exit_price=float(shadow.exit_price),
            exit_at=shadow.exit_timestamp,
            timeframe=timeframe_priority[0],
            candles=[],
            observed_at=observed_at,
        )
    else:
        result = calculate_measurement(
            entry_price=float(shadow.entry_price),
            entry_at=shadow.entry_timestamp,
            exit_price=float(shadow.exit_price),
            exit_at=shadow.exit_timestamp,
            timeframe=timeframe,
            candles=candles,
            observed_at=observed_at,
        )
    return {**base, **asdict(result)}


async def persist_measurement_revision(db: AsyncSession, values: Mapping[str, Any]) -> bool:
    """Insert once by immutable input identity. Existing revisions are untouched."""
    stmt = insert(ShadowTradeMeasurementRevision).values(**dict(values))
    stmt = stmt.on_conflict_do_nothing(
        constraint="uq_shadow_measurement_revision_input"
    ).returning(ShadowTradeMeasurementRevision.id)
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def latest_measurement_by_trade_ids(
    db: AsyncSession, trade_ids: Sequence[Any]
) -> dict[Any, ShadowTradeMeasurementRevision]:
    if not trade_ids:
        return {}
    rows = (
        await db.execute(
            select(ShadowTradeMeasurementRevision)
            .where(ShadowTradeMeasurementRevision.shadow_trade_id.in_(trade_ids))
            .order_by(
                ShadowTradeMeasurementRevision.shadow_trade_id,
                ShadowTradeMeasurementRevision.created_at.desc(),
                ShadowTradeMeasurementRevision.id.desc(),
            )
        )
    ).scalars()
    latest: dict[Any, ShadowTradeMeasurementRevision] = {}
    for row in rows:
        latest.setdefault(row.shadow_trade_id, row)
    return latest

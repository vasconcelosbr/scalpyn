"""Isolated, idempotent Pump Radar jobs. No task writes operational OHLCV."""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert

from ..config import settings
from ..models.pump_radar import (
    PumpRadarControl,
    PumpRadarEvent,
    PumpRadarEventLink,
    PumpRadarIndicatorSnapshot,
    PumpRadarIndicatorValue,
    PumpRadarOHLCV,
    PumpRadarRun,
    PumpRadarRunAsset,
    PumpRadarRangeResult,
)
from ..models.shadow_trade import ShadowTrade
from ..schemas.pump_radar import PumpRadarConfig
from ..services.pump_radar_detector import RadarCandle, detect_pumps
from ..services.pump_radar_research import ENGINE_VERSION, UNIVERSE_SOURCE, TIMEFRAMES, reconstruct_indicators, normalize_symbol
from ..utils.gate_market_data import parse_gate_spot_candle
from .celery_app import QUEUE_PUMP_RADAR, celery_app
from .ohlcv_backfill import _run_async
from .task_dispatch import enqueue

logger = logging.getLogger(__name__)
GATE_CANDLES_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"
TIMEFRAME_SECONDS = {"5m": 300, "15m": 900, "1h": 3600}
CAPTURE_CONTRACT = "pump_radar_gate_spot_closed_v1"
# Gate.io's public candlesticks endpoint rejects any request whose start is
# older than "10000 points ago" (confirmed live: INVALID_PARAM_VALUE
# "Candlestick too long ago. Maximum 10000 points ago are allowed"),
# regardless of interval. For 5m that's ~34.7 days -- far short of the
# 180-day default backfill window, which made every asset fail outright on
# its very first page request. A 50-point safety margin absorbs the clock
# drift between when we compute this clamp and when Gate.io evaluates "now".
GATE_MAX_CANDLES_BACK = 10000 - 50


def _earliest_fetchable(timeframe: str) -> datetime:
    return datetime.now(timezone.utc) - timedelta(
        seconds=TIMEFRAME_SECONDS[timeframe] * GATE_MAX_CANDLES_BACK
    )


def _clamp_capture_start(capture_start: datetime, timeframe: str) -> datetime:
    """Never ask Gate.io for candles older than it can actually serve."""
    return max(capture_start, _earliest_fetchable(timeframe))


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _select_universe(tickers: list[dict], limit: int, members: list[dict]) -> list[dict]:
    def rank(item: dict) -> tuple[Decimal, str]:
        try:
            quote_volume = Decimal(str(item.get("quote_volume") or 0))
        except Exception:
            quote_volume = Decimal(0)
        return (-quote_volume, str(item.get("currency_pair") or ""))

    allowed = {normalize_symbol(item["symbol"]) for item in members}
    eligible = [item for item in tickers if normalize_symbol(item.get("currency_pair")) in allowed]
    return sorted(eligible, key=rank)[:limit]


async def _cancel_asset_if_requested(db, run_id: UUID, symbol: str) -> bool:
    run = await db.get(PumpRadarRun, run_id)
    if run is None or run.cancel_requested_at is None:
        return False
    await db.execute(
        update(PumpRadarRunAsset)
        .where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol)
        .values(status="CANCELLED", finished_at=datetime.now(timezone.utc))
    )
    await _refresh_run(db, run_id)
    await db.commit()
    return True


async def _fetch_range(symbol: str, timeframe: str, start: datetime, end: datetime, *, historical: bool) -> list[dict]:
    step = TIMEFRAME_SECONDS[timeframe]
    cursor = int(_utc(start).timestamp())
    end_ts = int(_utc(end).timestamp())
    observed_at = datetime.now(timezone.utc)
    records: dict[int, dict] = {}
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        while cursor <= end_ts:
            page_end = min(end_ts, cursor + step * 999)
            response = await client.get(
                GATE_CANDLES_URL,
                params={"currency_pair": symbol, "interval": timeframe, "from": cursor, "to": page_end, "limit": 1000},
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError("Gate candle response is not a list")
            for raw in payload:
                normalized = parse_gate_spot_candle(raw)
                open_time = normalized["time"]
                close_time = open_time + timedelta(seconds=step)
                if close_time > observed_at:
                    continue
                if normalized["is_closed"] is False:
                    continue
                records[int(open_time.timestamp())] = {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "open_time": open_time,
                    "close_time": close_time,
                    "available_at": None,
                    "ingested_at": observed_at,
                    "open": Decimal(str(normalized["open"])),
                    "high": Decimal(str(normalized["high"])),
                    "low": Decimal(str(normalized["low"])),
                    "close": Decimal(str(normalized["close"])),
                    "volume_base": Decimal(str(normalized["volume"])),
                    "volume_quote": Decimal(str(normalized["quote_volume"])),
                    "is_closed": True,
                    "source": "gate_spot",
                    "contract_version": CAPTURE_CONTRACT,
                    "quality_status": "VALID",
                    "provenance": {"endpoint": "/spot/candlesticks", "historical_availability_proven": False},
                }
            cursor = page_end + step
            await asyncio.sleep(0.06)
    return [records[key] for key in sorted(records)]


async def _inventory(run_id: UUID) -> dict:
    from ..database import CeleryAsyncSessionLocal
    from ..services.market_data_service import market_data_service

    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        if run is None:
            return {"status": "missing"}
        if not settings.PUMP_RADAR_CAPTURE_ENABLED:
            run.status = "FAILED"
            run.error_message = "PUMP_RADAR_CAPTURE_DISABLED"
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "disabled"}
        if run.cancel_requested_at:
            run.status = "CANCELLED"
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "cancelled"}
        run.status = "RUNNING"
        run.started_at = run.started_at or datetime.now(timezone.utc)
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        if (run.provenance or {}).get("universe_source") != UNIVERSE_SOURCE:
            run.status = "FAILED"
            run.error_message = "PUMP_RADAR_UNIVERSE_REQUIRES_NEW_RUN"
            await db.commit()
            return {"status": "failed", "reason": run.error_message}
        members = run.universe_snapshot or []
        await db.commit()

    tickers = await market_data_service.fetch_all_tickers()
    selected_tickers = _select_universe(tickers, config.universe_max_assets, members)
    symbols = [str(item["currency_pair"]) for item in selected_tickers]
    selected_symbols = set(symbols)
    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        if run is None:
            return {"status": "missing"}
        ticker_by_symbol = {item["currency_pair"]: item for item in selected_tickers}
        run.universe_snapshot = [
            {
                **member,
                "quote_volume": ticker_by_symbol.get(member["symbol"], {}).get("quote_volume"),
                "selected": member["symbol"] in selected_symbols,
            }
            for member in members
        ]
        run.provenance = {
            **(run.provenance or {}),
            "eligible_assets": len(members),
            "selected_assets": len(symbols),
            "selection": "user_pool_pipeline_quote_volume_desc",
        }
        run.total_assets = len(symbols)
        if not symbols:
            run.status = "FAILED"
            run.error_message = "Gate Spot universe returned no eligible assets"
            run.finished_at = datetime.now(timezone.utc)
            await db.commit()
            return {"status": "failed", "assets": 0}
        for symbol in symbols:
            stmt = insert(PumpRadarRunAsset).values(run_id=run_id, symbol=symbol, status="QUEUED")
            stmt = stmt.on_conflict_do_nothing(index_elements=["run_id", "symbol"])
            await db.execute(stmt)
        await db.commit()

    for symbol in symbols:
        enqueue(
            "app.tasks.pump_radar.backfill_asset",
            dedup_key=f"pump-radar:{run_id}:{symbol}:capture",
            ttl_seconds=3900,
            queue=QUEUE_PUMP_RADAR,
            args=(str(run_id), symbol),
        )
    return {"status": "running", "assets": len(symbols)}


@celery_app.task(name="app.tasks.pump_radar.inventory")
def inventory(run_id: str) -> str:
    return json.dumps(_run_async(_inventory(UUID(run_id))), default=str)


async def _backfill_asset(run_id: UUID, symbol: str) -> dict:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        asset = (await db.execute(select(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol))).scalar_one_or_none()
        if run is None or asset is None:
            return {"status": "missing"}
        if await _cancel_asset_if_requested(db, run_id, symbol):
            return {"status": "cancelled"}
        asset.status = "CAPTURING"
        asset.started_at = asset.started_at or datetime.now(timezone.utc)
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        date_from = run.date_from or (datetime.now(timezone.utc) - timedelta(days=config.backfill_days))
        date_to = run.date_to or datetime.now(timezone.utc)
        historical = run.mode == "backfill"
        await db.commit()

    try:
        inserted = 0
        captured = 0
        expected = 0
        for timeframe in config.capture_timeframes:
            capture_start = date_from - timedelta(seconds=TIMEFRAME_SECONDS[timeframe] * config.context_candles)
            capture_start = _clamp_capture_start(capture_start, timeframe)
            rows = await _fetch_range(symbol, timeframe, capture_start, date_to, historical=historical)
            captured += len(rows)
            expected += max(0, int((_utc(date_to) - _utc(capture_start)).total_seconds() // TIMEFRAME_SECONDS[timeframe]))
            if not rows:
                continue
            async with CeleryAsyncSessionLocal() as db:
                # Bound bulk inserts below the driver's bind-parameter limit.
                for offset in range(0, len(rows), 500):
                    stmt = insert(PumpRadarOHLCV).values(rows[offset:offset + 500])
                    excluded = stmt.excluded
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["symbol", "timeframe", "open_time"],
                        set_={
                            "close_time": excluded.close_time,
                            "available_at": func.coalesce(PumpRadarOHLCV.available_at, excluded.available_at),
                            "ingested_at": excluded.ingested_at,
                            "open": excluded.open,
                            "high": excluded.high,
                            "low": excluded.low,
                            "close": excluded.close,
                            "volume_base": excluded.volume_base,
                            "volume_quote": excluded.volume_quote,
                            "is_closed": excluded.is_closed,
                            "contract_version": excluded.contract_version,
                            "quality_status": excluded.quality_status,
                            "provenance": excluded.provenance,
                        },
                    )
                    result = await db.execute(stmt)
                    await db.commit()
                    inserted += result.rowcount or 0
        async with CeleryAsyncSessionLocal() as db:
            next_status = "DETECTING" if settings.PUMP_RADAR_ANALYSIS_ENABLED else "COMPLETED"
            await db.execute(update(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol).values(status=next_status, finished_at=None if settings.PUMP_RADAR_ANALYSIS_ENABLED else datetime.now(timezone.utc), coverage=min(1.0, captured / expected) if expected else None, source_metadata={"capture_contract": CAPTURE_CONTRACT, "captured_rows": captured, "upserted_rows": inserted, "expected_rows": expected, "analysis_enabled": settings.PUMP_RADAR_ANALYSIS_ENABLED}))
            if not settings.PUMP_RADAR_ANALYSIS_ENABLED:
                await _refresh_run(db, run_id)
            await db.commit()
        if settings.PUMP_RADAR_ANALYSIS_ENABLED:
            enqueue("app.tasks.pump_radar.detect_asset", dedup_key=f"pump-radar:{run_id}:{symbol}:detect", ttl_seconds=900, queue=QUEUE_PUMP_RADAR, args=(str(run_id), symbol))
        return {"status": "captured", "rows": inserted, "analysis_dispatched": settings.PUMP_RADAR_ANALYSIS_ENABLED}
    except Exception as exc:
        logger.exception("[PUMP-RADAR] capture failed run=%s symbol=%s", run_id, symbol)
        async with CeleryAsyncSessionLocal() as db:
            await db.execute(update(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol).values(status="FAILED", finished_at=datetime.now(timezone.utc), error_code=type(exc).__name__, error_message=str(exc)[:1000]))
            await _refresh_run(db, run_id)
            await db.commit()
        return {"status": "failed", "error": type(exc).__name__}


@celery_app.task(name="app.tasks.pump_radar.backfill_asset")
def backfill_asset(run_id: str, symbol: str) -> str:
    return json.dumps(_run_async(_backfill_asset(UUID(run_id), symbol)), default=str)


async def _detect_asset(run_id: UUID, symbol: str) -> dict:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        if run is None:
            return {"status": "missing"}
        if await _cancel_asset_if_requested(db, run_id, symbol):
            return {"status": "cancelled"}
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        rows = (await db.execute(select(PumpRadarOHLCV).where(PumpRadarOHLCV.symbol == symbol, PumpRadarOHLCV.timeframe == config.detector_timeframe, PumpRadarOHLCV.open_time >= run.date_from, PumpRadarOHLCV.open_time <= run.date_to).order_by(PumpRadarOHLCV.open_time))).scalars().all()
        candles = [RadarCandle(open_time=row.open_time, close_time=row.close_time, open=Decimal(row.open), high=Decimal(row.high), low=Decimal(row.low), close=Decimal(row.close), volume_base=Decimal(row.volume_base), available_at=row.available_at if (row.provenance or {}).get("historical_availability_proven") is True else None, is_closed=row.is_closed) for row in rows]
        events = detect_pumps(candles, config)
        for event in events:
            stmt = insert(PumpRadarEvent).values(
                run_id=run_id, symbol=symbol, start_at=event.start_at,
                confirmed_market_at=event.confirmed_market_at, detected_at=event.detected_at,
                peak_at=event.peak_at, end_at=event.end_at, start_price=event.start_price,
                peak_price=event.peak_price, end_price=event.end_price, rise_pct=event.rise_pct,
                retracement_pct=event.retracement_pct, is_incomplete=event.is_incomplete,
                reconstruction_status=event.reconstruction_status,
                quality_status="INCOMPLETE" if event.is_incomplete else "VALID",
                provenance={"detector": config.schema_version, "config_hash": run.config_hash},
            ).on_conflict_do_nothing(index_elements=["run_id", "symbol", "start_at"])
            await db.execute(stmt)
        await db.execute(update(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol).values(status="ASSOCIATING", event_count=len(events)))
        await db.commit()
    enqueue("app.tasks.pump_radar.associate_asset", dedup_key=f"pump-radar:{run_id}:{symbol}:associate", ttl_seconds=900, queue=QUEUE_PUMP_RADAR, args=(str(run_id), symbol))
    return {"status": "detected", "events": len(events)}


@celery_app.task(name="app.tasks.pump_radar.detect_asset")
def detect_asset(run_id: str, symbol: str) -> str:
    parsed_run_id = UUID(run_id)
    try:
        result = _run_async(_detect_asset(parsed_run_id, symbol))
    except Exception as exc:
        logger.exception("[PUMP-RADAR] detection failed run=%s symbol=%s", run_id, symbol)
        _run_async(_mark_asset_failed(parsed_run_id, symbol, exc))
        result = {"status": "failed", "error": type(exc).__name__}
    return json.dumps(result, default=str)


def _flatten_features(value: object, prefix: str = "") -> list[tuple[str, object]]:
    if not isinstance(value, dict):
        return [(prefix, value)] if prefix else []
    rows: list[tuple[str, object]] = []
    for key, nested in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(nested, dict):
            rows.extend(_flatten_features(nested, path))
        elif not isinstance(nested, list):
            rows.append((path, nested))
    return rows


def _layer_state(layer_verdicts: object, layer: str) -> str:
    if not isinstance(layer_verdicts, dict):
        return "NOT_EVALUATED"
    raw = layer_verdicts.get(layer)
    verdict = raw.get("verdict") if isinstance(raw, dict) else raw
    if verdict in {"PASS", "PASSED", "ALLOW"}:
        return "PASSED"
    if verdict in {"REJECT", "REJECTED", "BLOCK"}:
        return "REJECTED"
    if verdict in {"UNAVAILABLE", "MISSING"}:
        return "UNAVAILABLE"
    return "NOT_EVALUATED"


async def _associate_asset(run_id: UUID, symbol: str) -> dict:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        if await _cancel_asset_if_requested(db, run_id, symbol):
            return {"status": "cancelled"}
        events = (await db.execute(select(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id, PumpRadarEvent.symbol == symbol).order_by(PumpRadarEvent.start_at))).scalars().all()
        links = 0
        for event in events:
            window_end = event.end_at or event.confirmed_market_at
            shadows = (await db.execute(select(ShadowTrade).where(
                ShadowTrade.user_id == (select(PumpRadarRun.user_id).where(PumpRadarRun.id == run_id).scalar_subquery()),
                func.replace(func.replace(ShadowTrade.symbol, "_", ""), "/", "") == normalize_symbol(symbol).replace("_", ""),
                ShadowTrade.direction.is_distinct_from("SHORT"),
                ShadowTrade.created_at >= event.start_at - timedelta(minutes=30),
                ShadowTrade.created_at <= window_end,
            ).order_by(ShadowTrade.created_at))).scalars().all()
            for shadow in shadows:
                existing_primary = (await db.execute(select(PumpRadarEventLink.id).where(PumpRadarEventLink.shadow_trade_id == shadow.id, PumpRadarEventLink.is_primary.is_(True)))).scalar_one_or_none()
                approval_at = shadow.created_at
                eligible_events = [
                    candidate for candidate in events
                    if candidate.start_at - timedelta(minutes=30) <= approval_at <= (candidate.end_at or candidate.confirmed_market_at)
                ]
                explicit_event = next((candidate for candidate in eligible_events if shadow.event_id == candidate.id), None)
                primary_event = explicit_event or min(
                    eligible_events,
                    key=lambda candidate: abs((approval_at - candidate.confirmed_market_at).total_seconds()),
                    default=event,
                )
                delay_seconds = int((shadow.entry_timestamp - approval_at).total_seconds()) if shadow.entry_timestamp and approval_at else None
                stmt = insert(PumpRadarEventLink).values(
                    event_id=event.id, user_id=shadow.user_id, shadow_trade_id=shadow.id,
                    decision_id=shadow.decision_id, link_kind="EXPLICIT_SHADOW" if shadow.event_id == event.id else "TEMPORAL_CORRELATION",
                    is_primary=existing_primary is None and primary_event.id == event.id, approval_at=approval_at,
                    simulation_created_at=shadow.created_at, entry_at=shadow.entry_timestamp,
                    exit_at=shadow.exit_timestamp, delay_seconds=delay_seconds,
                    realized_pnl_pct=shadow.pnl_pct, profile_id=shadow.profile_id,
                    profile_version_id=shadow.profile_version_id,
                    profile_config_hash=shadow.profile_config_hash,
                    provenance={"association_window": "T-30m_to_event_end", "causal": bool(shadow.event_id == event.id)},
                ).on_conflict_do_nothing(index_elements=["event_id", "shadow_trade_id"])
                await db.execute(stmt)
                links += 1

        await db.execute(update(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol).values(status="SNAPSHOTS"))
        await db.commit()
    enqueue("app.tasks.pump_radar.snapshots", dedup_key=f"pump-radar:{run_id}:{symbol}:snapshots", ttl_seconds=900, queue=QUEUE_PUMP_RADAR, args=(str(run_id), symbol))
    return {"status": "associated", "links": links}


async def _refresh_run(db, run_id: UUID) -> None:
    # Assets finish concurrently across forked Celery workers, each in its
    # own transaction, each calling this right before its own commit. Under
    # READ COMMITTED, a transaction's SELECT here can miss a sibling asset's
    # not-yet-committed terminal update -- both undercount by one, neither
    # flips the run to a terminal status, and it's stuck at N-1/N forever
    # even once every asset has actually finished (confirmed in production,
    # 2026-09-10: run 3fb987ac stuck RUNNING with 65/65 assets COMPLETED).
    # Locking the run row serializes concurrent callers so each one's count
    # is taken only after the previous holder's write has committed.
    await db.execute(select(PumpRadarRun.id).where(PumpRadarRun.id == run_id).with_for_update())
    statuses = (await db.execute(select(PumpRadarRunAsset.status, func.count()).where(PumpRadarRunAsset.run_id == run_id).group_by(PumpRadarRunAsset.status))).all()
    counts = {status: count for status, count in statuses}
    completed = counts.get("COMPLETED", 0)
    failed = counts.get("FAILED", 0)
    cancelled = counts.get("CANCELLED", 0)
    total = sum(counts.values())
    values = {"processed_assets": completed + failed + cancelled, "failed_assets": failed}
    if total and completed + failed + cancelled == total:
        values["status"] = "CANCELLED" if cancelled else ("PARTIAL" if failed else "COMPLETED")
        values["finished_at"] = datetime.now(timezone.utc)
    await db.execute(update(PumpRadarRun).where(PumpRadarRun.id == run_id).values(**values))


async def _mark_asset_failed(run_id: UUID, symbol: str, exc: Exception) -> None:
    """Persist an asset-scoped failure without hiding successful work from other assets."""
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        await db.execute(
            update(PumpRadarRunAsset)
            .where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol)
            .values(
                status="FAILED",
                finished_at=datetime.now(timezone.utc),
                error_code=type(exc).__name__,
                error_message=str(exc)[:1000],
            )
        )
        await _refresh_run(db, run_id)
        await db.commit()


@celery_app.task(name="app.tasks.pump_radar.associate_asset")
def associate_asset(run_id: str, symbol: str) -> str:
    parsed_run_id = UUID(run_id)
    try:
        result = _run_async(_associate_asset(parsed_run_id, symbol))
    except Exception as exc:
        logger.exception("[PUMP-RADAR] association failed run=%s symbol=%s", run_id, symbol)
        _run_async(_mark_asset_failed(parsed_run_id, symbol, exc))
        result = {"status": "failed", "error": type(exc).__name__}
    return json.dumps(result, default=str)


async def _snapshots(run_id: UUID, symbol: str) -> dict:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        if await _cancel_asset_if_requested(db, run_id, symbol):
            return {"status": "cancelled"}
        pairs = (await db.execute(
            select(PumpRadarEvent, PumpRadarEventLink, ShadowTrade)
            .join(PumpRadarEventLink, PumpRadarEventLink.event_id == PumpRadarEvent.id)
            .join(ShadowTrade, ShadowTrade.id == PumpRadarEventLink.shadow_trade_id)
            .where(PumpRadarEvent.run_id == run_id, PumpRadarEvent.symbol == symbol)
        )).all()
        snapshots_created = 0
        run = await db.get(PumpRadarRun, run_id)
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        events = (await db.execute(select(PumpRadarEvent).where(
            PumpRadarEvent.run_id == run_id, PumpRadarEvent.symbol == symbol,
        ))).scalars().all()
        histories = {}
        for timeframe, (seconds, _) in TIMEFRAMES.items():
            histories[timeframe] = (await db.execute(select(PumpRadarOHLCV).where(
                PumpRadarOHLCV.symbol == symbol, PumpRadarOHLCV.timeframe == timeframe,
                PumpRadarOHLCV.open_time >= run.date_from - timedelta(seconds=seconds * config.context_candles),
                PumpRadarOHLCV.close_time <= run.date_to,
                PumpRadarOHLCV.is_closed.is_(True), PumpRadarOHLCV.quality_status == "VALID",
            ).order_by(PumpRadarOHLCV.open_time))).scalars().all()
        for event in events:
            anchors = {event.start_at - timedelta(minutes=m) for m in (30, 15, 10, 5, 0)}
            for linked_event, link, _ in pairs:
                if linked_event.id == event.id:
                    anchors.update(at for at in (link.approval_at, link.entry_at) if at is not None)
            for at in sorted(anchors):
                provenance = {"point_in_time_basis": "closed_candles", "historical_availability_proven": False,
                              "historical_profile_config_proven": False,
                              "indicators_config_hash": (run.provenance or {}).get("indicators_config_hash"),
                              "indicators_config_observed_at": (run.provenance or {}).get("indicators_config_observed_at")}
                stmt = insert(PumpRadarIndicatorSnapshot).values(
                    event_id=event.id, user_id=run.user_id, snapshot_at=at,
                    source_priority="OHLCV_RECONSTRUCTION", state="RECONSTRUCTED",
                    schema_version="pump_radar_ohlcv_features_v1", feature_engine_version=ENGINE_VERSION,
                    provenance=provenance,
                ).on_conflict_do_nothing(index_elements=["event_id", "snapshot_at", "source_priority"]).returning(PumpRadarIndicatorSnapshot.id)
                snapshot_id = (await db.execute(stmt)).scalar_one_or_none()
                if snapshot_id is None:
                    continue
                snapshots_created += 1
                value_rows = []
                for timeframe, (_, layer) in TIMEFRAMES.items():
                    values, history = reconstruct_indicators(histories[timeframe], at, timeframe,
                        (run.provenance or {}).get("indicators_config"), config.context_candles)
                    for indicator_id, value in values.items():
                        value_rows.append(dict(snapshot_id=snapshot_id, indicator_id=indicator_id,
                            layer=layer, timeframe=timeframe, state="RECONSTRUCTED",
                            numeric_value=float(value), source="OHLCV_RECONSTRUCTION", version=ENGINE_VERSION,
                            provenance={**provenance, "closed_candles": len(history),
                                        "last_close_at": history[-1].close_time.isoformat() if history else None}))
                if value_rows:
                    await db.execute(insert(PumpRadarIndicatorValue).values(value_rows))
                else:
                    await db.execute(update(PumpRadarIndicatorSnapshot).where(PumpRadarIndicatorSnapshot.id == snapshot_id).values(state="UNAVAILABLE"))
        for event, link, shadow in pairs:
            source = "DECISION_SNAPSHOT" if shadow.features_snapshot else ("EVALUATION_ENVELOPE" if shadow.orchestrator_payload else "UNAVAILABLE")
            snapshot_payload = shadow.features_snapshot or shadow.orchestrator_payload or {}
            snapshot_at = shadow.feature_source_at or shadow.entry_timestamp or shadow.created_at
            snapshot_stmt = insert(PumpRadarIndicatorSnapshot).values(
                event_id=event.id, user_id=link.user_id, snapshot_at=snapshot_at,
                source_priority=source, state="UNAVAILABLE" if source == "UNAVAILABLE" else "PASSED",
                schema_version=shadow.feature_schema_version or "unknown",
                feature_engine_version=shadow.feature_extractor_version,
                profile_id=shadow.profile_id, profile_version_id=shadow.profile_version_id,
                profile_config_hash=shadow.profile_config_hash, coverage=shadow.features_coverage,
                provenance={"shadow_trade_id": str(shadow.id), "point_in_time": True},
            ).on_conflict_do_nothing(index_elements=["event_id", "snapshot_at", "source_priority"]).returning(PumpRadarIndicatorSnapshot.id)
            snapshot_id = (await db.execute(snapshot_stmt)).scalar_one_or_none()
            if snapshot_id is None:
                continue
            snapshots_created += 1
            for indicator_id, value in _flatten_features(snapshot_payload):
                numeric_value = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
                text_value = None if numeric_value is not None or value is None else str(value)
                layer = "L1" if "1h" in indicator_id else "L2" if "15m" in indicator_id else "L3"
                timeframe = "1h" if layer == "L1" else "15m" if layer == "L2" else "5m"
                await db.execute(insert(PumpRadarIndicatorValue).values(
                    snapshot_id=snapshot_id, indicator_id=indicator_id, layer=layer,
                    timeframe=timeframe, state=_layer_state(shadow.layer_verdicts, layer), numeric_value=numeric_value,
                    text_value=text_value, source=source, version=shadow.feature_extractor_version,
                    provenance={"shadow_trade_id": str(shadow.id)},
                ).on_conflict_do_nothing(index_elements=["snapshot_id", "indicator_id", "timeframe"]))
        await db.execute(update(PumpRadarRunAsset).where(PumpRadarRunAsset.run_id == run_id, PumpRadarRunAsset.symbol == symbol).values(status="COMPLETED", finished_at=datetime.now(timezone.utc)))
        await _refresh_run(db, run_id)
        await db.commit()
    enqueue("app.tasks.pump_radar.build_controls", dedup_key=f"pump-radar:{run_id}:controls", ttl_seconds=900, queue=QUEUE_PUMP_RADAR, args=(str(run_id),))
    return {"status": "completed", "snapshots": snapshots_created}


@celery_app.task(name="app.tasks.pump_radar.snapshots")
def snapshots(run_id: str, symbol: str) -> str:
    parsed_run_id = UUID(run_id)
    try:
        result = _run_async(_snapshots(parsed_run_id, symbol))
    except Exception as exc:
        logger.exception("[PUMP-RADAR] snapshot stage failed run=%s symbol=%s", run_id, symbol)
        _run_async(_mark_asset_failed(parsed_run_id, symbol, exc))
        result = {"status": "failed", "error": type(exc).__name__}
    return json.dumps(result, default=str)


def _atr_pct(rows: list[PumpRadarOHLCV], index: int, period: int = 14) -> float | None:
    if index < period:
        return None
    window = rows[index - period : index + 1]
    true_ranges: list[Decimal] = []
    for offset in range(1, len(window)):
        current = window[offset]
        previous = window[offset - 1]
        true_ranges.append(max(
            Decimal(current.high) - Decimal(current.low),
            abs(Decimal(current.high) - Decimal(previous.close)),
            abs(Decimal(current.low) - Decimal(previous.close)),
        ))
    close = Decimal(window[-1].close)
    return float((sum(true_ranges) / len(true_ranges)) / close * Decimal("100")) if true_ranges and close else None


def _anchor_features(
    rows: list[PumpRadarOHLCV],
    index: int,
    btc_by_time: dict[datetime, PumpRadarOHLCV],
    btc_times_sorted: list[datetime],
) -> dict[str, float] | None:
    if index < 288:
        return None
    anchor = rows[index]
    history = rows[index - 288:index]
    if any(not row.is_closed or row.close_time > anchor.open_time for row in history):
        return None
    if any(history[i].open_time - history[i-1].open_time != timedelta(minutes=5) for i in range(1, len(history))):
        return None
    atr = _atr_pct(rows, index - 1)
    quote_volume = sum(Decimal(row.volume_quote or 0) for row in rows[index - 288 : index])
    if atr is None:
        return None
    now_pos = bisect.bisect_right(btc_times_sorted, anchor.open_time) - 1
    if now_pos < 0:
        return None
    btc_now = btc_by_time[btc_times_sorted[now_pos]]
    prior_pos = bisect.bisect_right(btc_times_sorted, anchor.open_time - timedelta(hours=1)) - 1
    if prior_pos < 0:
        return None
    btc_prior = btc_by_time[btc_times_sorted[prior_pos]]
    prior_close = Decimal(btc_prior.close)
    if prior_close <= 0:
        return None
    return {
        "liquidity_quote_24h": float(quote_volume),
        "atr_pct_5m": atr,
        "btc_regime_1h_pct": float((Decimal(btc_now.close) / prior_close - Decimal("1")) * Decimal("100")),
        "period_hour_utc": float(anchor.open_time.hour),
    }


def _match_distance(event_features: dict[str, float], control_features: dict[str, float]) -> float:
    liquidity_scale = max(abs(event_features["liquidity_quote_24h"]), 1.0)
    atr_scale = max(abs(event_features["atr_pct_5m"]), 0.01)
    btc_scale = max(abs(event_features["btc_regime_1h_pct"]), 0.1)
    hour_delta = abs(event_features["period_hour_utc"] - control_features["period_hour_utc"])
    hour_delta = min(hour_delta, 24 - hour_delta) / 12
    return (
        abs(event_features["liquidity_quote_24h"] - control_features["liquidity_quote_24h"]) / liquidity_scale
        + abs(event_features["atr_pct_5m"] - control_features["atr_pct_5m"]) / atr_scale
        + abs(event_features["btc_regime_1h_pct"] - control_features["btc_regime_1h_pct"]) / btc_scale
        + hour_delta
    )


async def _build_controls(run_id: UUID) -> dict:
    from ..database import CeleryAsyncSessionLocal

    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        if run is None:
            return {"status": "missing"}
        if run.status not in {"COMPLETED", "PARTIAL"}:
            return {"status": "waiting"}
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        events = (await db.execute(select(PumpRadarEvent).where(PumpRadarEvent.run_id == run_id).order_by(PumpRadarEvent.start_at))).scalars().all()
        if not events:
            return {"status": "insufficient", "controls": 0}
        btc_rows = (await db.execute(select(PumpRadarOHLCV).where(
            PumpRadarOHLCV.symbol == "BTC_USDT", PumpRadarOHLCV.timeframe == "5m",
            PumpRadarOHLCV.open_time >= run.date_from - timedelta(days=1),
            PumpRadarOHLCV.open_time <= run.date_to,
        ).order_by(PumpRadarOHLCV.open_time))).scalars().all()
        btc_by_time = {row.close_time: row for row in btc_rows if row.is_closed}
        btc_times_sorted = sorted(btc_by_time)
        await db.execute(delete(PumpRadarControl).where(PumpRadarControl.run_id == run_id))
        inserted = 0
        by_symbol: dict[str, list[PumpRadarEvent]] = {}
        for event in events:
            by_symbol.setdefault(event.symbol, []).append(event)
        for symbol, symbol_events in by_symbol.items():
            rows = (await db.execute(select(PumpRadarOHLCV).where(
                PumpRadarOHLCV.symbol == symbol, PumpRadarOHLCV.timeframe == "5m",
                PumpRadarOHLCV.open_time >= run.date_from - timedelta(days=1),
                PumpRadarOHLCV.open_time <= run.date_to,
            ).order_by(PumpRadarOHLCV.open_time))).scalars().all()
            index_by_time = {row.open_time: index for index, row in enumerate(rows)}
            for event in symbol_events:
                event_index = index_by_time.get(event.start_at)
                if event_index is None:
                    continue
                event_features = _anchor_features(rows, event_index, btc_by_time, btc_times_sorted)
                if event_features is None:
                    continue
                candidates: list[tuple[float, int, dict[str, float]]] = []
                for index, row in enumerate(rows):
                    if index < 288 or row.open_time + timedelta(minutes=config.control_followup_minutes) > run.date_to:
                        continue
                    if row.open_time < run.date_from:
                        continue
                    if any(row.open_time <= (candidate_event.end_at or candidate_event.confirmed_market_at) and row.open_time + timedelta(minutes=config.control_followup_minutes) >= candidate_event.start_at - timedelta(minutes=30) for candidate_event in symbol_events):
                        continue
                    followup = rows[index : index + config.control_followup_minutes // 5]
                    if len(followup) < config.control_followup_minutes // 5 or not all(c.is_closed and c.close_time <= run.date_to for c in followup):
                        continue
                    if any(followup[offset].open_time - followup[offset - 1].open_time != timedelta(minutes=5) for offset in range(1, len(followup))):
                        continue
                    candidate_features = _anchor_features(rows, index, btc_by_time, btc_times_sorted)
                    if candidate_features is None:
                        continue
                    candidates.append((_match_distance(event_features, candidate_features), index, candidate_features))
                for rank, (distance, index, candidate_features) in enumerate(sorted(candidates, key=lambda item: (item[0], rows[item[1]].open_time))[: config.controls_per_event], start=1):
                    anchor = rows[index]
                    followup = rows[index : index + config.control_followup_minutes // 5]
                    max_high = max(Decimal(row.high) for row in followup)
                    outcome = {"max_rise_pct_180m": float((max_high / Decimal(anchor.open) - Decimal("1")) * Decimal("100")), "followup_minutes": config.control_followup_minutes}
                    await db.execute(insert(PumpRadarControl).values(
                        run_id=run_id, event_id=event.id, symbol=symbol, anchor_at=anchor.open_time,
                        followup_complete=True, overlap_excluded=False, match_rank=rank,
                        match_features={"event": event_features, "control": candidate_features, "distance": distance},
                        outcome=outcome,
                        provenance={"matcher": "period_liquidity_atr_btc_v1", "point_in_time": True, "candle_contract": CAPTURE_CONTRACT},
                    ).on_conflict_do_nothing(index_elements=["run_id", "event_id", "symbol", "anchor_at"]))
                    inserted += 1
        await db.commit()
    enqueue("app.tasks.pump_radar.statistics", dedup_key=f"pump-radar:{run_id}:statistics", ttl_seconds=600, queue=QUEUE_PUMP_RADAR, args=(str(run_id),))
    return {"status": "completed", "controls": inserted}


@celery_app.task(name="app.tasks.pump_radar.build_controls")
def build_controls(run_id: str) -> str:
    return json.dumps(_run_async(_build_controls(UUID(run_id))), default=str)


def _percentile(values: list[float], percentile: int) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _wilson(successes: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def _inside(value: float, threshold: float, direction: str) -> bool:
    return value >= threshold if direction == "GE" else value <= threshold


def _range_samples(controls, indicator, boundary, validation):
    """Count each event/control once, never reuse a future split as discovery."""
    pumps, matched = {}, {}
    for control, event in controls:
        if (event.start_at.date() >= boundary) != validation:
            continue
        if (control.anchor_at.date() >= boundary) != validation:
            continue
        followup_minutes = (control.outcome or {}).get("followup_minutes")
        if followup_minutes is None:
            continue
        if not validation and (control.anchor_at + timedelta(minutes=followup_minutes)).date() >= boundary:
            continue
        features = control.match_features or {}
        if indicator not in features.get("event", {}) or indicator not in features.get("control", {}):
            continue
        pumps[event.id] = float(features["event"][indicator])
        matched[(control.symbol, control.anchor_at)] = float(features["control"][indicator])
    return list(pumps.values()), list(matched.values())


async def _statistics(run_id: UUID) -> dict:
    """Discovery/validation ranges from real event and matched-control features."""
    from ..database import CeleryAsyncSessionLocal
    async with CeleryAsyncSessionLocal() as db:
        run = await db.get(PumpRadarRun, run_id)
        if run is None:
            return {"status": "missing"}
        terminal = run.status in {"COMPLETED", "PARTIAL"}
        controls = (await db.execute(select(PumpRadarControl, PumpRadarEvent).join(PumpRadarEvent, PumpRadarEvent.id == PumpRadarControl.event_id).where(PumpRadarControl.run_id == run_id, PumpRadarControl.followup_complete.is_(True), PumpRadarControl.overlap_excluded.is_(False)).order_by(PumpRadarEvent.start_at))).all()
        if not terminal or not controls:
            return {"status": "waiting" if not terminal else "insufficient", "controls": len(controls)}
        config = PumpRadarConfig.model_validate(run.config_snapshot)
        days = sorted({event.start_at.date() for _, event in controls})
        if len(days) < 2:
            await db.execute(delete(PumpRadarRangeResult).where(PumpRadarRangeResult.run_id == run_id))
            await db.commit()
            return {"status": "insufficient", "reason": "chronological_day_split_unavailable", "controls": len(controls)}
        boundary_index = min(len(days) - 1, max(1, math.ceil(len(days) * config.discovery_fraction)))
        validation_start_day = days[boundary_index]
        await db.execute(delete(PumpRadarRangeResult).where(PumpRadarRangeResult.run_id == run_id))
        indicators = ("liquidity_quote_24h", "atr_pct_5m", "btc_regime_1h_pct")
        published = 0
        for indicator in indicators:
            pump_discovery_values, control_discovery_values = _range_samples(controls, indicator, validation_start_day, False)
            pump_validation_values, control_validation_values = _range_samples(controls, indicator, validation_start_day, True)
            if not pump_discovery_values or not control_discovery_values:
                continue
            for percentile in config.range_percentiles:
                threshold = _percentile(pump_discovery_values, percentile)
                for direction in ("GE", "LE"):
                    pump_success = sum(_inside(value, threshold, direction) for value in pump_discovery_values)
                    control_success = sum(_inside(value, threshold, direction) for value in control_discovery_values)
                    pump_n = len(pump_discovery_values); control_n = len(control_discovery_values)
                    pump_rate = pump_success / pump_n; control_rate = control_success / control_n
                    p_low, p_high = _wilson(pump_success, pump_n); c_low, c_high = _wilson(control_success, control_n)
                    val_p_success = sum(_inside(value, threshold, direction) for value in pump_validation_values)
                    val_c_success = sum(_inside(value, threshold, direction) for value in control_validation_values)
                    vp_low, vp_high = _wilson(val_p_success, len(pump_validation_values)); vc_low, vc_high = _wilson(val_c_success, len(control_validation_values))
                    validated = bool(pump_validation_values and vp_low is not None and vc_high is not None and vp_low > vc_high and pump_rate > control_rate)
                    validation_status = "VALIDATED" if validated else ("CANDIDATE" if pump_validation_values else "INSUFFICIENT_SAMPLE")
                    await db.execute(insert(PumpRadarRangeResult).values(
                        run_id=run_id, indicator_id=indicator, layer="L3", timeframe="5m",
                        range_key=f"{direction}_P{percentile}",
                        lower_bound=threshold if direction == "GE" else None,
                        upper_bound=threshold if direction == "LE" else None,
                        pump_numerator=pump_success, pump_denominator=pump_n,
                        control_numerator=control_success, control_denominator=control_n,
                        coverage=pump_n / max(len({event.id for _, event in controls if event.start_at.date() < validation_start_day}), 1),
                        difference=pump_rate - control_rate,
                        ratio=(pump_rate / control_rate) if control_rate else None,
                        confidence_interval={"discovery_difference_conservative": [(p_low or 0) - (c_high or 1), (p_high or 1) - (c_low or 0)], "validation_pump": [vp_low, vp_high], "validation_control": [vc_low, vc_high]},
                        validation_status=validation_status,
                        discovery_boundary=datetime.combine(validation_start_day, datetime.min.time(), tzinfo=timezone.utc),
                        provenance={"split": "chronological_day_70_30", "validation_events": len(pump_validation_values), "validation_controls": len(control_validation_values), "recalibrated_on_validation": False, "source": "pump_radar_controls.match_features"},
                    ))
                    published += 1
        await db.commit()
        return {"status": "ready" if published else "insufficient", "controls": len(controls), "ranges": published}


@celery_app.task(name="app.tasks.pump_radar.statistics")
def statistics(run_id: str) -> str:
    return json.dumps(_run_async(_statistics(UUID(run_id))), default=str)

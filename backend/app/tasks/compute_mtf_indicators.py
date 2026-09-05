"""Governed Spot MTF producers for closed 1h and 15m candles."""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from sqlalchemy import text

from ..services.profile_runtime_config import canonical_hash
from ..tasks.celery_app import celery_app
from ..utils.indicator_merge import envelop_results

logger = logging.getLogger(__name__)

_TIMEFRAME_SECONDS = {"15m": 900, "1h": 3600}
_PRODUCER_VERSION = "mtf_indicator_producer_v1"
_CAPTURE_CONTRACT_VERSION = "spot_mtf_closed_ohlcv_v2"


def required_warmup_candles(config: dict[str, Any], timeframe: str) -> int:
    """Derive the complete warmup from the active indicator configuration."""
    if timeframe not in _TIMEFRAME_SECONDS:
        raise ValueError("MTF_TIMEFRAME_UNSUPPORTED")

    required = [2, (86400 + _TIMEFRAME_SECONDS[timeframe] - 1) // _TIMEFRAME_SECONDS[timeframe]]

    def _positive_int(value: Any, field: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"INDICATOR_PERIOD_INVALID:{field}") from exc
        if parsed <= 0:
            raise ValueError(f"INDICATOR_PERIOD_INVALID:{field}")
        return parsed

    rsi = config.get("rsi") or {}
    if rsi.get("enabled"):
        periods = list(rsi.get("periods") or [])
        if rsi.get("period") is not None:
            periods.append(rsi["period"])
        if not periods:
            raise ValueError("INDICATOR_PERIOD_CONFIG_REQUIRED:rsi")
        required.append(max(_positive_int(value, "rsi") for value in periods) + 5)

    adx = config.get("adx") or {}
    if adx.get("enabled"):
        required.append(_positive_int(adx.get("period"), "adx") * 2 + 3)

    ema = config.get("ema") or {}
    if ema.get("enabled"):
        periods = list(ema.get("periods") or [])
        if not periods:
            raise ValueError("INDICATOR_PERIOD_CONFIG_REQUIRED:ema")
        required.append(max(_positive_int(value, "ema") for value in periods) + 1)

    atr = config.get("atr") or {}
    if atr.get("enabled"):
        required.append(_positive_int(atr.get("period"), "atr") + 1)

    macd = config.get("macd") or {}
    if macd.get("enabled"):
        required.append(
            _positive_int(macd.get("slow"), "macd.slow")
            + _positive_int(macd.get("signal"), "macd.signal")
            + 5
        )

    for family, key in (("bollinger", "period"), ("zscore", "lookback")):
        item = config.get(family) or {}
        if item.get("enabled"):
            required.append(_positive_int(item.get(key), f"{family}.{key}"))

    stochastic = config.get("stochastic") or {}
    if stochastic.get("enabled"):
        required.append(
            _positive_int(stochastic.get("k"), "stochastic.k")
            + _positive_int(stochastic.get("smooth"), "stochastic.smooth")
            + _positive_int(stochastic.get("d"), "stochastic.d")
        )

    for family in ("volume_spike", "volume_delta", "taker_ratio"):
        item = config.get(family) or {}
        if item.get("enabled") and item.get("lookback") is not None:
            required.append(_positive_int(item["lookback"], f"{family}.lookback") + 1)

    return max(required)


async def _load_governed_indicator_config(db) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT id, user_id, config_json, updated_at
          FROM config_profiles
         WHERE config_type = 'indicators' AND is_active IS TRUE
         ORDER BY updated_at DESC, id
    """))).mappings().all()
    if len(rows) != 1:
        raise RuntimeError(
            f"INDICATOR_CONFIG_CARDINALITY_INVALID: expected=1 actual={len(rows)}"
        )
    row = rows[0]
    config = dict(row["config_json"] or {})
    if not config:
        raise RuntimeError("INDICATOR_CONFIG_EMPTY")
    identity = {
        "config_profile_id": str(row["id"]),
        "config_user_id": str(row["user_id"]),
        "config_updated_at": row["updated_at"].isoformat(),
        "config_hash": canonical_hash(config),
    }
    return config, identity


def _period_for_indicator(key: str, config: dict[str, Any]) -> Any:
    family = key.split("_", 1)[0]
    if key.startswith("di_") or key.startswith("adx"):
        family = "adx"
    elif key.startswith("ema"):
        family = "ema"
    elif key.startswith("bb_"):
        family = "bollinger"
    item = config.get(family) or {}
    if not isinstance(item, dict):
        return None
    if family == "ema":
        match = re.match(r"ema(\d+)", key)
        if match:
            period = int(match.group(1))
            configured = {int(value) for value in item.get("periods") or []}
            return period if period in configured else None
    return item.get("period")


def _parameters_for_indicator(key: str, config: dict[str, Any]) -> dict[str, Any]:
    family = key.split("_", 1)[0]
    if key.startswith("di_") or key.startswith("adx"):
        family = "adx"
    elif key.startswith("ema"):
        family = "ema"
    elif key.startswith("bb_"):
        family = "bollinger"
    item = config.get(family)
    return dict(item) if isinstance(item, dict) else {}


def _governed_envelopes(
    results: dict[str, Any],
    *,
    timeframe: str,
    source_timestamp: datetime,
    source_provider: str,
    config: dict[str, Any],
    config_identity: dict[str, Any],
    computed_at: datetime,
    capture_contract_version: str,
    source_ingested_at: datetime,
) -> dict[str, Any]:
    wrapped = envelop_results(results)
    for key, envelope in wrapped.items():
        if not isinstance(envelope, dict):
            continue
        envelope.update({
            "timeframe": timeframe,
            "market_type": "spot",
            "scheduler_group": "structural",
            "source_provider": source_provider,
            "provider_policy_id": "spot_gate_closed_ohlcv_v1",
            "candle_policy": "CLOSED_ONLY",
            "candle_closed": True,
            "source_timestamp": source_timestamp.isoformat(),
            "computed_at": computed_at.isoformat(),
            "available_at": computed_at.isoformat(),
            "producer_version": _PRODUCER_VERSION,
            "capture_contract_version": capture_contract_version,
            "source_ingested_at": source_ingested_at.isoformat(),
            "period": _period_for_indicator(key, config),
            "parameters": _parameters_for_indicator(key, config),
            **config_identity,
        })
        envelope.pop("envelope_hash", None)
        envelope["envelope_hash"] = canonical_hash(envelope)
    return wrapped


async def _compute_symbol(
    db,
    *,
    symbol: str,
    timeframe: str,
    duration: timedelta,
    now: datetime,
    config: dict[str, Any],
    config_identity: dict[str, Any],
    engine,
    required_warmup: int,
) -> bool:
    rows = (await db.execute(text("""
        SELECT time, open, high, low, close, volume, quote_volume, exchange,
               is_closed, ingested_at, capture_contract_version
          FROM ohlcv
         WHERE symbol = :symbol
           AND market_type = 'spot'
           AND timeframe = :timeframe
           AND time <= :latest_closed_open
         ORDER BY time DESC
         LIMIT :query_limit
    """), {
        "symbol": symbol,
        "timeframe": timeframe,
        "latest_closed_open": now - duration,
        "query_limit": required_warmup,
    })).fetchall()
    if len(rows) < required_warmup:
        return False
    if any(
        row.is_closed is not True
        or row.capture_contract_version != _CAPTURE_CONTRACT_VERSION
        or row.time + duration > now
        or row.ingested_at is None
        for row in rows
    ):
        return False
    if any(
        not math.isfinite(float(value))
        for row in rows
        for value in (row.open, row.high, row.low, row.close, row.volume)
    ):
        return False
    source_row = rows[0]
    frame = pd.DataFrame([{
        "time": row.time,
        "open": float(row.open),
        "high": float(row.high),
        "low": float(row.low),
        "close": float(row.close),
        "volume": float(row.volume),
        "quote_volume": (
            float(row.quote_volume) if row.quote_volume is not None else None
        ),
    } for row in reversed(rows)])
    results = engine.calculate(frame, market_data=None)
    if not results:
        return False
    source_timestamp = source_row.time
    source_provider = str(source_row.exchange or "gate.io")
    payload = _governed_envelopes(
        results,
        timeframe=timeframe,
        source_timestamp=source_timestamp,
        source_provider=source_provider,
        config=config,
        config_identity=config_identity,
        computed_at=now,
        capture_contract_version=str(source_row.capture_contract_version),
        source_ingested_at=source_row.ingested_at,
    )
    await db.execute(text("""
        INSERT INTO indicators
            (time, symbol, timeframe, market_type, scheduler_group, indicators_json)
        VALUES
            (:time, :symbol, :timeframe, 'spot', 'structural', CAST(:payload AS JSONB))
    """), {
        "time": now,
        "symbol": symbol,
        "timeframe": timeframe,
        "payload": json.dumps(payload, default=str),
    })
    return True


async def compute_timeframe(timeframe: str) -> dict[str, Any]:
    if timeframe not in _TIMEFRAME_SECONDS:
        raise ValueError("MTF_TIMEFRAME_UNSUPPORTED")

    from ..database import CeleryAsyncSessionLocal
    from ..services.feature_engine import FeatureEngine

    computed = 0
    skipped = 0
    async with CeleryAsyncSessionLocal() as db:
        config, config_identity = await _load_governed_indicator_config(db)
        engine = FeatureEngine(config)
        symbols = [row.symbol for row in (await db.execute(text("""
            SELECT DISTINCT p.symbol
              FROM pool_coins p
             WHERE p.is_active IS TRUE AND p.market_type = 'spot'
             ORDER BY p.symbol
        """))).fetchall()]
        duration = timedelta(seconds=_TIMEFRAME_SECONDS[timeframe])
        now = datetime.now(timezone.utc)
        required_warmup = required_warmup_candles(config, timeframe)

        for symbol in symbols:
            try:
                async with db.begin_nested():
                    created = await _compute_symbol(
                        db,
                        symbol=symbol,
                        timeframe=timeframe,
                        duration=duration,
                        now=now,
                        config=config,
                        config_identity=config_identity,
                        engine=engine,
                        required_warmup=required_warmup,
                    )
                if created:
                    computed += 1
                else:
                    skipped += 1
            except Exception as exc:
                skipped += 1
                logger.exception(
                    "[MTF-PRODUCER] symbol isolated timeframe=%s symbol=%s error=%s",
                    timeframe,
                    symbol,
                    exc,
                )
        await db.commit()
    result = {
        "timeframe": timeframe,
        "computed": computed,
        "skipped": skipped,
        "producer_version": _PRODUCER_VERSION,
        "config_hash": config_identity["config_hash"],
        "required_warmup_candles": required_warmup,
    }
    logger.info("[MTF-PRODUCER] %s", result)
    return result


def _run(coro):
    from .compute_indicators import _run_async

    return _run_async(coro)


@celery_app.task(name="app.tasks.compute_mtf_indicators.compute_15m")
def compute_15m():
    return _run(compute_timeframe("15m"))


@celery_app.task(name="app.tasks.compute_mtf_indicators.compute_1h")
def compute_1h():
    return _run(compute_timeframe("1h"))

"""Governed Spot MTF producers for closed 1h and 15m candles."""

from __future__ import annotations

import json
import logging
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
    return item.get("period") if isinstance(item, dict) else None


def _governed_envelopes(
    results: dict[str, Any],
    *,
    timeframe: str,
    source_timestamp: datetime,
    source_provider: str,
    config: dict[str, Any],
    config_identity: dict[str, Any],
    computed_at: datetime,
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
            "period": _period_for_indicator(key, config),
            **config_identity,
        })
        envelope.pop("envelope_hash", None)
        envelope["envelope_hash"] = canonical_hash(envelope)
    return wrapped


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

        for symbol in symbols:
            rows = (await db.execute(text("""
                SELECT time, open, high, low, close, volume, quote_volume, exchange
                  FROM ohlcv
                 WHERE symbol = :symbol
                   AND market_type = 'spot'
                   AND timeframe = :timeframe
                   AND time <= :latest_closed_open
                 ORDER BY time DESC
                 LIMIT 500
            """), {
                "symbol": symbol,
                "timeframe": timeframe,
                "latest_closed_open": now - duration,
            })).fetchall()
            if not rows:
                skipped += 1
                continue
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
                skipped += 1
                continue
            source_timestamp = rows[0].time
            source_provider = str(rows[0].exchange or "gate.io")
            payload = _governed_envelopes(
                results,
                timeframe=timeframe,
                source_timestamp=source_timestamp,
                source_provider=source_provider,
                config=config,
                config_identity=config_identity,
                computed_at=now,
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
            computed += 1
        await db.commit()
    result = {
        "timeframe": timeframe,
        "computed": computed,
        "skipped": skipped,
        "producer_version": _PRODUCER_VERSION,
        "config_hash": config_identity["config_hash"],
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

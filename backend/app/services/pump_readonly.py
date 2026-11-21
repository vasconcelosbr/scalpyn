"""PUMP_STRONG_READONLY: project the final display result; never run the engine."""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from . import pump_monitor_service as monitor
from ..schemas.pump_readonly import PumpStrongResponse, StrongAsset


class PumpExportUnavailable(Exception):
    pass


def project_strong_assets(envelope, config, *, now=None):
    if not envelope or envelope.get("served_from") != "redis_latest_cycle":
        raise PumpExportUnavailable("complete_cycle_unavailable")
    if str(envelope.get("pool_id")) != str(config.get("universe_pool_id")):
        raise PumpExportUnavailable("pool_changed_waiting_for_cycle")
    meta = config["_meta"]
    if any(envelope.get(k) != meta[source] for k, source in
           (("config_version", "version"), ("config_hash", "config_hash"))):
        raise PumpExportUnavailable("config_changed_waiting_for_cycle")
    rows = envelope.get("rows")
    if (not isinstance(rows, list) or envelope.get("total_assets") != len(rows)
            or envelope.get("failed_assets") != 0):
        raise PumpExportUnavailable("incomplete_cycle")
    try:
        generated = datetime.fromisoformat(envelope["generated_at"])
        if generated.tzinfo is None:
            raise ValueError("timezone required")
        age = max(0.0, ((now or datetime.now(timezone.utc)) - generated).total_seconds())
        stale_after = float(config["display"]["stale_after_cycles"]) * float(envelope["cycle_seconds"])
        selected = []
        symbols = set()
        for row in rows:
            symbol, direction = row["symbol"], row.get("direction")
            if (not isinstance(symbol, str) or not symbol or symbol in symbols
                    or not isinstance(direction, dict)
                    or direction.get("direction") not in ("long", "short", "neutral")):
                raise ValueError("incomplete direction data")
            symbols.add(symbol)
            if direction.get("direction") == "long" and direction.get("strength") == "forte":
                # This cell, not the legacy v0 top-level score, drives the UI.
                cell = (row.get("indicators") or {}).get("pump_monitor_score") or {}
                selected.append(StrongAsset(symbol=symbol, direction=direction,
                                            pump_monitor_score=cell.get("value")))
        selected.sort(key=lambda row: row.symbol)
        return PumpStrongResponse(
            pool_id=envelope["pool_id"], generated_at=generated,
            data_age_seconds=round(age, 1), stale=age > stale_after,
            config_version=envelope["config_version"], config_hash=envelope["config_hash"],
            total_assets=len(rows), matched_assets=len(selected), rows=selected,
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PumpExportUnavailable("incomplete_cycle") from None


async def read_strong_assets(db, owner: UUID):
    config = await monitor.get_config(db, owner)
    pool_id = config.get("universe_pool_id")
    if not pool_id:
        raise PumpExportUnavailable("universe_pool_not_configured")
    # Do not trust a stale cache or a config pointing to another tenant's pool.
    owned = (await db.execute(text(
        "SELECT id FROM pools WHERE id = :pool AND user_id = :owner"
    ), {"pool": UUID(str(pool_id)), "owner": owner})).scalar()
    if owned is None:
        raise PumpExportUnavailable("monitored_pool_unavailable")
    envelope = await monitor.latest_envelope(db, owner)
    return project_strong_assets(envelope, config)

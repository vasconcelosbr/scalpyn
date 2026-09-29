"""Atomic radar membership, collection retention and feed-health reconciliation."""
from datetime import datetime, timezone

from sqlalchemy import select

from ..models.pool import Pool, PoolAssetExclusion, PoolCoin
from .pool_service import cascade_invalidate_removed_symbols, symbols_with_open_shadow_trades


# Worker-owned health key → the operator toggle that owns it.
_WORKER_OWNED_HEALTH = {
    "radar_feed_health": "radar_enabled",
    "pump_monitor_feed_health": "pump_monitor_sync_enabled",
}


def operator_pool_overrides(current: dict | None, requested: dict) -> dict:
    """Feed health is worker-owned; saving settings cannot replay old health."""
    previous = current or {}
    result = {k: v for k, v in requested.items() if k not in _WORKER_OWNED_HEALTH}
    for health_key, enabled_key in _WORKER_OWNED_HEALTH.items():
        if previous.get(enabled_key) == result.get(enabled_key) and health_key in previous:
            result[health_key] = previous[health_key]
    # A pool fed by the Pump Monitor is observation-only; the flag cannot be
    # cleared while the sync is on (execution filters on it).
    if result.get("pump_monitor_sync_enabled"):
        result["observation_only"] = True
    return result


async def reconcile_radar_pool(db, *, pool_id, user_id, radar_pairs: set[str] | None,
                               reason: str | None = None, now=None, origin: str = "radar",
                               enabled_key: str = "radar_enabled",
                               health_key: str = "radar_feed_health",
                               hold_open_positions: bool = True):
    """None is unavailable, while an empty set is a healthy empty selection.

    The pool row serializes sync and settings changes. Existing radar membership
    is retained during an outage; invalid discovery residue is never eligibility.
    No Shadow or historical decision is modified here.

    ``origin``/``enabled_key``/``health_key`` select the feed (Market Catalyst
    radar by default, ``pump_monitor`` for the observation-only REALTIME pool).
    Observation pools pass ``hold_open_positions=False``: they carry no trades,
    so an open Shadow elsewhere must not pin a symbol in them.
    """
    now = now or datetime.now(timezone.utc)
    pool = (await db.execute(select(Pool).where(
        Pool.id == pool_id, Pool.user_id == user_id,
    ).with_for_update())).scalar_one_or_none()
    if (pool is None or not pool.is_active or pool.market_type != "spot"
            or not (pool.overrides or {}).get(enabled_key)):
        return {"added": 0, "removed": 0, "held": 0, "duplicates": 0, "skipped": True}

    overrides = dict(pool.overrides or {})
    previous_health = overrides.get(health_key) or {}
    overrides[health_key] = {
        "status": "healthy" if radar_pairs is not None else "unavailable",
        "checked_at": now.isoformat(),
        "last_success_at": now.isoformat() if radar_pairs is not None else previous_health.get("last_success_at"),
        "reason": reason if radar_pairs is None else None,
    }
    pool.overrides = overrides
    coins = (await db.execute(select(PoolCoin).where(
        PoolCoin.pool_id == pool_id,
    ).with_for_update())).scalars().all()
    excluded = set((await db.execute(select(PoolAssetExclusion.symbol).where(
        PoolAssetExclusion.pool_id == pool_id,
    ))).scalars().all())
    grouped = {}
    for coin in coins:
        grouped.setdefault(coin.symbol, []).append(coin)
    open_symbols = (await symbols_with_open_shadow_trades(db, user_id, set(grouped))
                    if hold_open_positions else set())
    present = (radar_pairs - excluded) if radar_pairs is not None else set()
    stats = {"added": 0, "removed": 0, "held": 0, "duplicates": 0, "skipped": False}
    invalidated = set()

    for symbol, rows in grouped.items():
        # Keep the established radar row/ID whenever it exists. Preserve explicit
        # operator permissions from existing duplicates, never invent permissions.
        rows.sort(key=lambda c: (c.origin != origin, str(c.id)))
        coin = rows[0]
        coin.is_tradable = any(c.is_tradable for c in rows)
        coin.is_approved = any(c.is_approved for c in rows)
        for duplicate in rows[1:]:
            await db.delete(duplicate)
            stats["duplicates"] += 1

        if symbol in present:
            coin.origin = origin
            coin.is_active = True
            coin.held_for_open_position = False
            if origin == "radar":
                coin.radar_last_seen_at = now
            continue

        invalidated.add(symbol)
        if symbol in open_symbols:
            coin.is_active = True
            # During an outage keep last-known presence only for genuine radar
            # rows; health blocks eligibility independently of this retention flag.
            if radar_pairs is not None or coin.origin != origin or symbol in excluded:
                coin.held_for_open_position = True
            coin.origin = origin
            stats["held"] += 1
        elif radar_pairs is not None or coin.origin != origin or coin.held_for_open_position or symbol in excluded:
            # Invalid non-radar residue and completed retained positions can be
            # retired even during an outage without inferring absence from it.
            await db.delete(coin)
            stats["removed"] += 1

    for symbol in sorted(present - set(grouped)):
        db.add(PoolCoin(pool_id=pool_id, symbol=symbol, market_type="spot",
                        is_active=True, origin=origin, discovered_at=now,
                        radar_last_seen_at=now if origin == "radar" else None,
                        held_for_open_position=False))
        stats["added"] += 1

    if invalidated:
        await cascade_invalidate_removed_symbols(db, pool_id, invalidated)
    await db.flush()
    return stats

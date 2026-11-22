"""Observation timing, independent of UI filters and the expiring Redis state.

Only the cycle writes here. The caller owns the transaction. Missing direction
data never becomes a false loss/re-entry into strong-long.
"""
from datetime import datetime

from sqlalchemy import text


TIMING_FIELDS = ("monitor_entered_at", "last_strong_long_at", "tracking_started_at",
                 "timing_initial_baseline", "timing_status")


def strong_long(row):
    direction = row.get("direction") or {}
    if (row.get("collection_error") or not direction
            or direction.get("reason", "").split("+")[0] == "sem_dados"
            or direction.get("direction") not in ("long", "short", "neutral")):
        return None
    if direction["direction"] != "neutral" and direction.get("strength") not in ("normal", "forte"):
        return None
    return direction["direction"] == "long" and direction.get("strength") == "forte"


def advance(previous, row, cycle_at, *, baseline):
    """Advance one membership episode using observed states, never wall-clock reads."""
    new_episode = not previous or not previous["is_present"]
    if new_episode:
        current = dict(is_present=True, tracking_started_at=cycle_at,
                       monitor_entered_at=None if baseline else cycle_at,
                       last_strong_long_at=None, last_strong_long=None,
                       last_cycle_at=cycle_at)
    else:
        current = dict(previous)
        if cycle_at <= current["last_cycle_at"]:
            return current
        current["last_cycle_at"] = cycle_at
    strong = strong_long(row)
    if strong is not None:
        if strong and (current["last_strong_long"] is False or (new_episode and not baseline)):
            current["last_strong_long_at"] = cycle_at
        current["last_strong_long"] = strong
    return current


def public_timing(state):
    return {
        **{key: state[key].isoformat() if state[key] else None for key in
           ("monitor_entered_at", "last_strong_long_at", "tracking_started_at")},
        "timing_initial_baseline": state["monitor_entered_at"] is None,
        "timing_status": "available",
    }


def unavailable_timing():
    return {**dict.fromkeys(TIMING_FIELDS), "timing_status": "unavailable"}


async def record_cycle(db, user_id, pool_id, rows, *, cycle_at: datetime,
                       candidates, market_caps):
    """Return symbol timing, or None for an obsolete cycle (do not publish it).

    A per-user row lock serializes concurrent workers, including pool changes.
    Unknown market cap may hide a row temporarily but cannot end its episode.
    """
    params = {"user_id": str(user_id), "pool_id": str(pool_id), "cycle_at": cycle_at}
    await db.execute(text("""
        INSERT INTO pump_monitor_timing_cycles (user_id, pool_id)
        VALUES (CAST(:user_id AS uuid), CAST(:pool_id AS uuid))
        ON CONFLICT (user_id) DO NOTHING
    """), params)
    checkpoint = (await db.execute(text("""
        SELECT pool_id, last_cycle_at FROM pump_monitor_timing_cycles
         WHERE user_id = CAST(:user_id AS uuid) FOR UPDATE
    """), params)).mappings().one()
    if checkpoint["last_cycle_at"] and cycle_at <= checkpoint["last_cycle_at"]:
        return None
    baseline = checkpoint["last_cycle_at"] is None
    stored = (await db.execute(text("""
        SELECT * FROM pump_monitor_asset_timing WHERE user_id = CAST(:user_id AS uuid)
    """), params)).mappings().all()
    current = {r["symbol"]: dict(r) for r in stored if str(r["pool_id"]) == str(pool_id)}
    symbols = {r["symbol"] for r in rows}
    candidates = set(candidates)
    # A successful universe read proves removals; missing metadata does not.
    removed = [r["symbol"] for r in current.values() if r["is_present"]
               and r["symbol"] not in symbols
               and (r["symbol"] not in candidates or r["symbol"] in market_caps)]
    await db.execute(text("""
        UPDATE pump_monitor_asset_timing SET is_present = false, last_cycle_at = :cycle_at
         WHERE user_id = CAST(:user_id AS uuid) AND is_present
           AND (pool_id <> CAST(:pool_id AS uuid) OR symbol = ANY(CAST(:removed AS text[])))
    """), {**params, "removed": removed})
    values, timing = [], {}
    for row in rows:
        state = advance(current.get(row["symbol"]), row, cycle_at, baseline=baseline)
        values.append({**state, **params, "symbol": row["symbol"]})
        timing[row["symbol"]] = public_timing(state)
    if values:
        await db.execute(text("""
            INSERT INTO pump_monitor_asset_timing
                (user_id, pool_id, symbol, is_present, tracking_started_at,
                 monitor_entered_at, last_strong_long_at, last_strong_long, last_cycle_at)
            VALUES (CAST(:user_id AS uuid), CAST(:pool_id AS uuid), :symbol, :is_present,
                    :tracking_started_at, :monitor_entered_at, :last_strong_long_at,
                    :last_strong_long, :last_cycle_at)
            ON CONFLICT (user_id, pool_id, symbol) DO UPDATE SET
                is_present = EXCLUDED.is_present,
                tracking_started_at = EXCLUDED.tracking_started_at,
                monitor_entered_at = EXCLUDED.monitor_entered_at,
                last_strong_long_at = EXCLUDED.last_strong_long_at,
                last_strong_long = EXCLUDED.last_strong_long,
                last_cycle_at = EXCLUDED.last_cycle_at
        """), values)
    await db.execute(text("""
        UPDATE pump_monitor_timing_cycles SET pool_id = CAST(:pool_id AS uuid), last_cycle_at = :cycle_at
         WHERE user_id = CAST(:user_id AS uuid)
    """), params)
    return timing

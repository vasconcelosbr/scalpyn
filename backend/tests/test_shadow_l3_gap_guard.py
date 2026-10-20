"""Missing-candle guard of the L3 continuation sweep (local PostgreSQL)."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio  # noqa: F401  (fixture plugin)
from pydantic import ValidationError
from sqlalchemy import text

from test_shadow_l3_persistence import db, URL  # noqa: F401  (shared isolated-schema fixture)
from app.models.shadow_trade import ShadowTrade
from app.schemas.shadow_l3_exit_policy import frozen_policy
from app.schemas.shadow_l3_exit_runtime import ShadowL3ExitRuntimeConfig
from app.services.shadow_l3_exit_service import advance_shadow

pytestmark = pytest.mark.skipif(not URL, reason="Local PostgreSQL URL required")
RUNTIME = ShadowL3ExitRuntimeConfig(version="shadow_l3_exit_runtime_v1", missing_candle_wait_seconds=600)
M = timedelta(minutes=1)


async def _shadow(db, entry):
    uid = uuid4()
    await db.execute(text("INSERT INTO users VALUES(:uid)"), {"uid": uid})
    shadow = ShadowTrade(id=uuid4(), user_id=uid, symbol="UNI_USDT", source="L3", status="RUNNING", entry_price=100,
                         entry_timestamp=entry, tp_price=150, sl_price=50, amount_usdt=100, exchange="gate.io",
                         config_snapshot={"shadow_l3_exit_policy": frozen_policy({})}, timeout_candles=600)
    db.add(shadow)
    await db.commit()
    return shadow


async def _candle(db, at, ingested):
    await db.execute(text("INSERT INTO ohlcv VALUES(:at,'UNI_USDT','gate.io','spot','1m',100,101,99,100,true,:ing)"),
                     {"at": at, "ing": ingested})
    await db.commit()


async def _decided(db, shadow):
    return [r[0] for r in (await db.execute(text(
        "SELECT candle_at FROM shadow_l3_exit_decisions WHERE shadow_id=:id ORDER BY candle_at"), {"id": shadow.id}))]


async def _stored_state(db, shadow):
    return (await db.execute(text("SELECT state FROM shadow_l3_exit_states WHERE shadow_id=:id"), {"id": shadow.id})).scalar_one()


@pytest.mark.asyncio
async def test_without_runtime_a_late_minute_is_skipped_for_good(db):
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    shadow = await _shadow(db, entry)
    await _candle(db, entry, entry + M)
    await _candle(db, entry + 2 * M, entry + 3 * M)  # minute +1 not there yet
    await advance_shadow(db, shadow, runtime=None); await db.commit()
    await _candle(db, entry + M, entry + 10 * M)      # arrives late
    await advance_shadow(db, shadow, runtime=None); await db.commit()
    assert await _decided(db, shadow) == [entry, entry + 2 * M]  # legacy behaviour, unchanged


@pytest.mark.asyncio
async def test_runtime_waits_for_a_recent_missing_minute_then_evaluates_it_in_order(db):
    entry = datetime.now(timezone.utc).replace(second=0, microsecond=0) - 5 * M
    shadow = await _shadow(db, entry)
    await _candle(db, entry, entry + M)
    await _candle(db, entry + 2 * M, entry + 3 * M)
    stats = {}
    await advance_shadow(db, shadow, runtime=RUNTIME, stats=stats); await db.commit()
    assert await _decided(db, shadow) == [entry]       # cursor held before the hole
    assert stats == {"gap_waits": 1}
    await _candle(db, entry + M, datetime.now(timezone.utc))
    await advance_shadow(db, shadow, runtime=RUNTIME, stats=stats); await db.commit()
    assert await _decided(db, shadow) == [entry, entry + M, entry + 2 * M]
    assert "price_gaps" not in await _stored_state(db, shadow)


@pytest.mark.asyncio
async def test_runtime_gives_up_after_the_wait_and_records_the_gap_outside_decision_state(db):
    entry = datetime(2026, 1, 1, tzinfo=timezone.utc)
    shadow = await _shadow(db, entry)
    await _candle(db, entry, entry + M)
    await _candle(db, entry + 3 * M, entry + 4 * M)   # minutes +1 and +2 never arrive
    stats = {}
    await advance_shadow(db, shadow, runtime=RUNTIME, stats=stats); await db.commit()
    assert await _decided(db, shadow) == [entry, entry + 3 * M]
    assert stats["gaps_skipped"] == 1 and stats["gap_minutes_skipped"] == 2
    gaps = (await _stored_state(db, shadow))["price_gaps"]
    assert [(g["from"], g["until"], g["minutes"]) for g in gaps] == [
        ((entry + M).isoformat(), (entry + 3 * M).isoformat(), 2)]
    states = [r[0] for r in (await db.execute(text(
        "SELECT state FROM shadow_l3_exit_decisions WHERE shadow_id=:id"), {"id": shadow.id}))]
    assert all("price_gaps" not in s for s in states)  # certify() replays these
    # bookkeeping survives later runs instead of being dropped or duplicated
    await _candle(db, entry + 4 * M, entry + 5 * M)
    await advance_shadow(db, shadow, runtime=RUNTIME, stats=stats); await db.commit()
    assert len((await _stored_state(db, shadow))["price_gaps"]) == 1


@pytest.mark.asyncio
async def test_runtime_is_resolved_from_config_when_the_caller_omits_it(db):
    """The price monitor calls advance_shadow(db, shadow); it must get the guard too."""
    from app.models.config_profile import ConfigProfile
    entry = datetime.now(timezone.utc).replace(second=0, microsecond=0) - 5 * M
    shadow = await _shadow(db, entry)
    db.add(ConfigProfile(user_id=shadow.user_id, pool_id=None, config_type="shadow_l3_exit_runtime",
                         is_active=True, config_json=RUNTIME.model_dump()))
    await db.commit()
    await _candle(db, entry, entry + M)
    await _candle(db, entry + 2 * M, entry + 3 * M)
    await advance_shadow(db, shadow); await db.commit()
    assert await _decided(db, shadow) == [entry]  # waited instead of skipping


@pytest.mark.asyncio
async def test_unaligned_entry_partial_minute_is_not_a_gap(db):
    entry = datetime(2026, 1, 1, 0, 0, 27, tzinfo=timezone.utc)
    shadow = await _shadow(db, entry)
    start = entry.replace(second=0)
    for i in range(3):
        await _candle(db, start + i * M, start + (i + 1) * M)
    stats = {}
    await advance_shadow(db, shadow, runtime=RUNTIME, stats=stats); await db.commit()
    # OBSERVE records the partial entry minute too; what matters is no false gap.
    assert stats == {} and await _decided(db, shadow) == [start, start + M, start + 2 * M]


def test_runtime_config_is_explicit_and_fail_closed():
    for raw in ({}, {"version": "shadow_l3_exit_runtime_v1"}, {"version": "x", "missing_candle_wait_seconds": 60},
                {"version": "shadow_l3_exit_runtime_v1", "missing_candle_wait_seconds": 0},
                {"version": "shadow_l3_exit_runtime_v1", "missing_candle_wait_seconds": True},
                {"version": "shadow_l3_exit_runtime_v1", "missing_candle_wait_seconds": 60, "extra": 1}):
        with pytest.raises(ValidationError):
            ShadowL3ExitRuntimeConfig.model_validate(raw)

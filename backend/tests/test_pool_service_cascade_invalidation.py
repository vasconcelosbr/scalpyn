from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.services.pool_service import (
    cascade_invalidate_removed_symbols,
    resolve_root_pool_ids,
    set_held_for_open_position,
    symbols_with_open_shadow_trades,
)


@pytest.mark.asyncio
async def test_cascade_invalidate_removed_symbols_noop_on_empty_input():
    db = Obj(execute=AsyncMock())
    count = await cascade_invalidate_removed_symbols(db, uuid4(), set())
    assert count == 0
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_cascade_invalidate_removed_symbols_walks_the_full_tree_and_returns_count():
    pool_id = uuid4()
    db = Obj(execute=AsyncMock(
        return_value=Obj(fetchall=lambda: [Obj(symbol="ENA_USDT"), Obj(symbol="BNB_USDT")])
    ))
    count = await cascade_invalidate_removed_symbols(db, pool_id, {"ENA_USDT", "BNB_USDT"})
    assert count == 2
    query, params = db.execute.call_args.args[0], db.execute.call_args.args[1]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "WITH RECURSIVE" in sql
    assert "source_pool_id" in sql and "source_watchlist_id" in sql
    assert "level_direction" in sql and "'down'" in sql
    assert params["pool_id"] == pool_id
    assert set(params["symbols"]) == {"ENA_USDT", "BNB_USDT"}


@pytest.mark.asyncio
async def test_resolve_root_pool_ids_empty_input_short_circuits():
    db = Obj(execute=AsyncMock())
    result = await resolve_root_pool_ids(db, [])
    assert result == {}
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_root_pool_ids_maps_watchlist_to_pool_and_omits_standalone():
    wl_pool_linked = uuid4()
    wl_standalone = uuid4()
    pool_id = uuid4()
    db = Obj(execute=AsyncMock(
        return_value=Obj(fetchall=lambda: [Obj(start_id=wl_pool_linked, source_pool_id=pool_id)])
    ))
    result = await resolve_root_pool_ids(db, [wl_pool_linked, wl_standalone])
    assert result == {wl_pool_linked: pool_id}
    assert wl_standalone not in result


# ── 2026-09-25: held-for-open-position (keep collection, block new candidacy) ──


@pytest.mark.asyncio
async def test_symbols_with_open_shadow_trades_noop_on_empty_input():
    db = Obj(execute=AsyncMock())
    result = await symbols_with_open_shadow_trades(db, uuid4(), [])
    assert result == set()
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_symbols_with_open_shadow_trades_returns_only_pending_running_symbols():
    user_id = uuid4()
    db = Obj(execute=AsyncMock(
        return_value=Obj(fetchall=lambda: [Obj(symbol="SUI_USDT"), Obj(symbol="LIT_USDT")])
    ))
    result = await symbols_with_open_shadow_trades(
        db, user_id, {"SUI_USDT", "LIT_USDT", "BTC_USDT"}
    )
    assert result == {"SUI_USDT", "LIT_USDT"}
    query, params = db.execute.call_args.args[0], db.execute.call_args.args[1]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "shadow_trades" in sql
    assert "'PENDING'" in sql and "'RUNNING'" in sql
    assert params["user_id"] == user_id
    assert set(params["symbols"]) == {"SUI_USDT", "LIT_USDT", "BTC_USDT"}


@pytest.mark.asyncio
async def test_set_held_for_open_position_noop_on_empty_input():
    db = Obj(execute=AsyncMock())
    count = await set_held_for_open_position(db, uuid4(), [], held=True)
    assert count == 0
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_set_held_for_open_position_marks_held_true():
    pool_id = uuid4()
    db = Obj(execute=AsyncMock(
        return_value=Obj(fetchall=lambda: [Obj(symbol="SUI_USDT")])
    ))
    count = await set_held_for_open_position(db, pool_id, {"SUI_USDT"}, held=True)
    assert count == 1
    query, params = db.execute.call_args.args[0], db.execute.call_args.args[1]
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False}))
    assert "held_for_open_position" in sql
    assert params["pool_id"] == pool_id
    assert params["held"] is True
    assert set(params["symbols"]) == {"SUI_USDT"}


@pytest.mark.asyncio
async def test_set_held_for_open_position_clears_held_false_on_reactivation():
    pool_id = uuid4()
    db = Obj(execute=AsyncMock(
        return_value=Obj(fetchall=lambda: [Obj(symbol="SUI_USDT")])
    ))
    count = await set_held_for_open_position(db, pool_id, {"SUI_USDT"}, held=False)
    assert count == 1
    params = db.execute.call_args.args[1]
    assert params["held"] is False

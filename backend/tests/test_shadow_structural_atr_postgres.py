"""Integration regression: a partial micro row must not mask a structural ATR."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from app.services.shadow_trade_service import _load_point_in_time_atr_pct, _load_point_in_time_atr_map


@pytest.mark.asyncio
async def test_newer_micro_row_cannot_mask_point_in_time_structural_atr():
    url = os.environ.get('TEST_DATABASE_URL')
    if not url:
        pytest.skip('TEST_DATABASE_URL required; never use application DATABASE_URL')
    url = url.replace('postgresql://', 'postgresql+asyncpg://')
    engine = create_async_engine(url)
    now = datetime.now(timezone.utc)
    try:
        async with engine.begin() as connection:
            await connection.execute(text('CREATE TEMP TABLE indicators (time timestamptz, symbol text, '
                'market_type text, timeframe text, scheduler_group text, indicators_json jsonb) ON COMMIT DROP'))
            for offset, group, payload in [(-60, 'structural', '{"atr_percent":{"value":0.6}}'),
                                          (-1, 'microstructure', '{"taker_ratio":{"value":0.4}}'),
                                          (1, 'structural', '{"atr_percent":{"value":99}}')]:
                await connection.execute(text("INSERT INTO indicators VALUES (:at,'SOL_USDT','spot','5m',:grp,CAST(:payload AS jsonb))"),
                    {'at': now + timedelta(seconds=offset), 'grp': group, 'payload': payload})
            async with AsyncSession(bind=connection) as db:
                value, at = await _load_point_in_time_atr_pct(db, symbol='SOL_USDT', timeframe='5m', as_of=now)
                assert value == pytest.approx(0.6)
                assert at == now - timedelta(seconds=60)
                assert await _load_point_in_time_atr_map(db, symbols=['SOL_USDT'], timeframe='5m', as_of=now) == {'SOL_USDT': (value, at)}
                assert await _load_point_in_time_atr_pct(db, symbol='SOL_USDT', timeframe='1h', as_of=now) == (None, None)
                # Invalid latest structural input must still fail closed.
                await db.execute(text("INSERT INTO indicators VALUES (:at,'SOL_USDT','spot','5m','structural',CAST(:payload AS jsonb))"),
                                 {'at':now, 'payload':'{"atr_percent":{"value":0}}'})
                assert (await _load_point_in_time_atr_pct(db, symbol='SOL_USDT', timeframe='5m', as_of=now))[0] is None
                assert await _load_point_in_time_atr_map(db, symbols=['SOL_USDT'], timeframe='5m', as_of=now) == {}
    finally:
        await engine.dispose()

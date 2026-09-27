import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import fakeredis.aioredis

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import l3_flow_diagnostics as diagnostics, redis_client


def test_flow_check_is_scoped_and_fresh_observation_replaces_stale():
    async def scenario():
        redis_client.set_async_redis(fakeredis.aioredis.FakeRedis())
        try:
            check = {"status": "STALE", "checked_at": "2026-09-27T19:23:34Z", "retention_seconds": 300}
            await diagnostics.record_flow_check(user_id="u", watchlist_id="w", symbol="TAO_USDT", observation=check)
            assert await diagnostics.load_flow_checks(user_id="other", watchlist_id="w", symbols=["TAO_USDT"]) == {}
            assert await diagnostics.load_flow_checks(user_id="u", watchlist_id="other", symbols=["TAO_USDT"]) == {}
            check["status"] = "CURRENT"
            await diagnostics.record_flow_check(user_id="u", watchlist_id="w", symbol="TAO_USDT", observation=check)
            actual = await diagnostics.load_flow_checks(user_id="u", watchlist_id="w", symbols=["TAO_USDT", "BTC_USDT"])
            assert actual == {"TAO_USDT": check}
            client = await redis_client.get_async_redis()
            assert 0 < await client.ttl(diagnostics._key("u", "w", "TAO_USDT")) <= 300
        finally:
            await redis_client.reset_async_redis()
    asyncio.run(scenario())


def test_diagnostic_failure_does_not_change_execution(monkeypatch):
    monkeypatch.setattr(diagnostics, "get_async_redis", AsyncMock(side_effect=RuntimeError("offline")))
    asyncio.run(diagnostics.record_flow_check(user_id="u", watchlist_id="w", symbol="TAO_USDT", observation={"status": "STALE"}))
    assert asyncio.run(diagnostics.load_flow_checks(user_id="u", watchlist_id="w", symbols=["TAO_USDT"])) == {}


def test_stale_flow_still_blocks_and_reports_observed_age(monkeypatch):
    from app.tasks.pipeline_scan import _inject_live_order_flow
    from app.services.config_service import config_service
    from app.services import order_flow_service
    monkeypatch.setattr(config_service, "get_config", AsyncMock(return_value={"l3_order_flow_max_age_seconds": 15}))
    monkeypatch.setattr(order_flow_service, "get_order_flow_data", AsyncMock(return_value={
        "data_age_seconds": 40.8, "taker_source": "gate_io_trades", "taker_ratio": 0.8,
    }))
    updated, ok = asyncio.run(_inject_live_order_flow(symbol="TAO_USDT", indicators={"taker_ratio": 0.7}, db=object(), user_id="u", pool_id=None))
    assert ok is False
    assert updated["taker_ratio"] == 0.7
    assert updated["_l3_flow_check"]["status"] == "STALE"
    assert updated["_l3_flow_check"]["data_age_seconds"] == 40.8
    assert updated["_l3_flow_check"]["max_age_seconds"] == 15

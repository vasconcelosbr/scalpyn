import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.tasks import compute_indicators as compute
from app.tasks.celery_app import TASK_ROUTES, QUEUE_STRUCTURAL_COMPUTE
from app.utils.bounded_async import bounded_map


def test_bounded_workers_overlap_without_exceeding_limit():
    async def run():
        active = peak = 0
        ready = asyncio.Event()
        async def work(n):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 3:
                ready.set()
            await asyncio.wait_for(ready.wait(), 1)
            await asyncio.sleep(0)
            active -= 1
            return n * 2
        result = await bounded_map(work, range(12), 3)
        assert result == list(range(0, 24, 2))
        assert peak == 3
    asyncio.run(run())


def test_bounded_workers_cancel_siblings_on_failure():
    async def run():
        started = asyncio.Event()
        cleaned = asyncio.Event()
        async def work(n):
            if n == 0:
                await started.wait()
                raise ValueError('failure')
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        try:
            await bounded_map(work, [0, 1], 2)
        except ValueError:
            pass
        else:
            raise AssertionError('failure swallowed')
        assert cleaned.is_set()
    asyncio.run(run())


def test_symbol_publication_waits_for_commit(monkeypatch):
    from app import database
    from app.services import feature_engine, market_data_service, order_flow_service
    from app.services import indicator_calculation_identity
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(time=now, open=1, high=2, low=1, close=2,
                          volume=10, quote_volume=20, exchange='gate.io',
                          is_closed=True, ingested_at=now,
                          capture_contract_version='gate_ohlcv_canonical_v1')
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[MagicMock(fetchall=lambda: [row]),
                                       MagicMock(fetchall=lambda: []), MagicMock()])
    db.commit = AsyncMock()
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=db)
    session.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested.return_value = session
    monkeypatch.setattr(database, 'CeleryAsyncSessionLocal', lambda: session)
    monkeypatch.setattr(feature_engine, 'FeatureEngine', lambda cfg: SimpleNamespace(calculate=lambda *a, **k: {'price': 2}))
    monkeypatch.setattr(market_data_service.market_data_service, 'fetch_indicator_fallbacks', AsyncMock(return_value={}))
    monkeypatch.setattr(order_flow_service, 'get_order_flow_data', AsyncMock(return_value={}))
    monkeypatch.setattr(indicator_calculation_identity, 'calculation_identities', lambda *a: {})
    monkeypatch.setattr(compute, '_upsert_market_metadata_snapshot', AsyncMock())
    monkeypatch.setattr(compute, '_compute_score_fields', lambda *a: {})
    envelopes = []
    monkeypatch.setattr(compute, 'envelop_results', lambda results, **kw: envelopes.append(kw) or results)
    monkeypatch.setattr(compute._pq, 'enqueue_or_log', AsyncMock(side_effect=AssertionError('must await DB commit')))
    async def run():
        result = await compute._process_one_symbol_5m('ONDO_USDT', {}, {}, {}, 'spot', 1, 288)
        assert result == 1
        db.commit.assert_awaited_once()
        assert envelopes[0]['envelope_metadata']['source_timestamp'] == now.isoformat()
        assert envelopes[0]['envelope_metadata']['producer_version'] == 'compute_5m_v2'
        db.commit.reset_mock()
        db.execute.side_effect = [MagicMock(fetchall=lambda: [row]), MagicMock(fetchall=lambda: []), MagicMock()]
        db.commit.side_effect = RuntimeError('commit failed')
        assert await compute._process_one_symbol_5m('ONDO_USDT', {}, {}, {}, 'spot', 1, 288) == 0
        row.is_closed = False
        db.execute.side_effect = [MagicMock(fetchall=lambda: [row])]
        db.commit.reset_mock()
        assert await compute._process_one_symbol_5m('ONDO_USDT', {}, {}, {}, 'spot', 1, 288) == 0
        db.commit.assert_not_awaited()
    asyncio.run(run())


def test_scan_only_after_successful_publication(monkeypatch):
    from app.tasks import task_dispatch
    calls = []
    monkeypatch.setattr(compute, '_run_async', lambda coroutine: coroutine.close() or 0)
    monkeypatch.setattr(task_dispatch, 'enqueue', lambda *a, **k: calls.append((a, k)))
    compute.compute_5m.run()
    assert calls == []
    monkeypatch.setattr(compute, '_run_async', lambda coroutine: coroutine.close() or 2)
    compute.compute_5m.run()
    assert calls[0][0] == ('app.tasks.pipeline_scan.scan',)
    assert TASK_ROUTES['app.tasks.compute_indicators.compute_5m']['queue'] == QUEUE_STRUCTURAL_COMPUTE


def test_collector_prefetches_before_write_transaction(monkeypatch):
    import pandas as pd
    from app.tasks import collect_market_data as collect
    from app import database
    from app.services.market_data_service import market_data_service
    symbols = {'AAA_USDT': 'spot', 'BBB_USDT': 'spot'}
    fetched = set()
    writing = False
    db = MagicMock()
    db.is_active = True
    db.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
    tx = MagicMock()
    tx.__aenter__ = AsyncMock()
    tx.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested.return_value = tx
    async def run_db(fn, **kwargs):
        nonlocal writing
        if fn.__name__ == '_load_active':
            return symbols, 1
        assert fetched == set(symbols)
        writing = True
        return await fn(db)
    async def fetch(symbol, *args, **kwargs):
        assert not writing
        fetched.add(symbol)
        return pd.DataFrame([dict(time=datetime.now(timezone.utc), open=1, high=1, low=1, close=1, volume=1)])
    async def book(*args, **kwargs):
        assert not writing
        return {'spread_pct': 0.1, 'orderbook_depth_usdt': 10000}
    monkeypatch.setattr(database, 'run_db_task', run_db)
    monkeypatch.setattr(market_data_service, 'fetch_ohlcv', fetch)
    monkeypatch.setattr(market_data_service, 'fetch_orderbook_metrics', book)
    monkeypatch.setattr(market_data_service, 'fetch_all_tickers', AsyncMock(return_value=[]))
    monkeypatch.setattr(collect._pq, 'is_enabled', lambda: False)
    assert asyncio.run(collect._collect_5m_async()) == 2

"""Recovery can collect and simulate without reaching an exchange mutation."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from app.exchange_adapters.gate_adapter import GateAdapter
from scripts.run_observation_beat import ALLOWED, M5_SHADOW_ALLOWED, observation_schedule


def test_recovery_schedule_requires_explicit_shadow_opt_in_and_order_block():
    entries = {name: {'task': name} for name in (*ALLOWED, *M5_SHADOW_ALLOWED,
                                               'execute_buy_cycle', 'evaluate_signals', 'retrain')}
    assert set(observation_schedule(entries, {})) == set(ALLOWED)
    with pytest.raises(RuntimeError):
        observation_schedule(entries, {'RECOVERY_M5_SHADOW_ENABLED': '1'})
    selected = observation_schedule(entries, {'RECOVERY_M5_SHADOW_ENABLED': '1',
                                              'RECOVERY_OBSERVATION_ONLY': '1'})
    assert set(selected) == set(ALLOWED) | set(M5_SHADOW_ALLOWED)
    assert selected['collect_5m_data_every_5min'] is entries['collect_5m_data_every_5min']
    assert not {'execute_buy_cycle', 'evaluate_signals', 'retrain'} & set(selected)


@pytest.mark.parametrize('method,endpoint,write', [
    ('POST', '/spot/orders', True), ('POST', '/spot/orders', False),
    ('POST', '/futures/usdt/orders', True), ('POST', '/spot/price_orders', True),
    ('POST', '/wallet/transfers', True), ('PUT', '/futures/usdt/price_orders/1', False),
    ('DELETE', '/spot/orders/1', True), ('GET', '/spot/orders', True),
])
def test_recovery_blocks_exchange_writes_before_signing_or_network(monkeypatch, method, endpoint, write):
    monkeypatch.setenv('RECOVERY_OBSERVATION_ONLY', '1')
    adapter = GateAdapter('test-key', 'test-secret')
    adapter._sign = lambda *args: pytest.fail('Must block before signing')
    adapter._write_limiter.acquire = AsyncMock(side_effect=AssertionError('Must block before limiter'))
    with pytest.raises(PermissionError, match='RECOVERY_OBSERVATION_ONLY'):
        asyncio.run(adapter._request(method, endpoint, write=write))


def test_recovery_keeps_read_requests_available(monkeypatch):
    monkeypatch.setenv('RECOVERY_OBSERVATION_ONLY', '1')
    adapter = GateAdapter('test-key', 'test-secret')
    class ReadReached(Exception):
        pass
    adapter._read_limiter.acquire = AsyncMock(side_effect=ReadReached)
    with pytest.raises(ReadReached):
        asyncio.run(adapter._request('GET', '/spot/candlesticks'))


def test_normal_mode_preserves_existing_write_path(monkeypatch):
    monkeypatch.delenv('RECOVERY_OBSERVATION_ONLY', raising=False)
    adapter = GateAdapter('test-key', 'test-secret')
    class WriteReached(Exception):
        pass
    adapter._write_limiter.acquire = AsyncMock(side_effect=WriteReached)
    with pytest.raises(WriteReached):
        asyncio.run(adapter._request('POST', '/spot/orders', write=True))

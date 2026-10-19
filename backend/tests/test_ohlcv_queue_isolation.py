import importlib
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.tasks.celery_app import celery_app, TASK_ROUTES, QUEUE_CANONICAL_OHLCV, _configured_task_modules
from app.schemas.ohlcv_comparison import OHLCVComparisonConfig
from app.tasks import ohlcv_task_telemetry as telemetry

CONFIG = dict(enabled=True, window_seconds=120, max_rounds_per_run=1,
              max_shadow_rows=10, statement_timeout_ms=5000, lock_timeout_ms=1000,
              settlement_delay_seconds=0)


def test_only_canonical_collectors_use_dedicated_queue():
    names = {name for name, route in TASK_ROUTES.items() if route['queue'] == QUEUE_CANONICAL_OHLCV}
    assert names == {f'app.tasks.collect_research_ohlcv.collect_{tf}_shadow' for tf in ('1m', '5m', '30m')}
    for entry in celery_app.conf.beat_schedule.values():
        if entry['task'] in names:
            assert entry['options']['queue'] == QUEUE_CANONICAL_OHLCV
            assert entry['options']['expires'] > 0
    assert celery_app.conf.worker_prefetch_multiplier == 1


def test_dedicated_worker_imports_and_mixed_queue_rejection(monkeypatch):
    monkeypatch.setenv('WORKER_QUEUES', 'canonical_ohlcv')
    assert _configured_task_modules() == ('app.tasks.collect_research_ohlcv',)
    monkeypatch.setenv('WORKER_QUEUES', 'canonical_ohlcv,research_ohlcv')
    with pytest.raises(ValueError, match='dedicated'):
        _configured_task_modules()


@pytest.mark.parametrize('key,value', [('window_seconds', 0), ('max_rounds_per_run', -1),
    ('max_shadow_rows', True), ('statement_timeout_ms', '10'), ('lock_timeout_ms', 0),
    ('settlement_delay_seconds', -1), ('enabled', 'true')])
def test_comparison_rejects_invalid_budgets(key, value):
    with pytest.raises(ValidationError):
        OHLCVComparisonConfig.model_validate({**CONFIG, key: value})


def test_comparison_requires_complete_explicit_config():
    with pytest.raises(ValidationError):
        OHLCVComparisonConfig.model_validate({'enabled': True})
    with pytest.raises(ValidationError):
        OHLCVComparisonConfig.model_validate({**CONFIG, 'unexpected': 1})


def test_publish_start_finish_and_expiration_are_traceable(monkeypatch):
    events = []
    monkeypatch.setattr(telemetry, 'emit', lambda event, **fields: events.append((event, fields)))
    headers = {'id': 'sample-id', 'eta': None, 'expires': 'test-expiry'}
    name = 'app.tasks.collect_research_ohlcv.collect_1m_shadow'
    telemetry.published(sender=name, headers=headers, routing_key='canonical_ohlcv')
    telemetry.publish_confirmed(sender=name, headers=headers, routing_key='canonical_ohlcv')
    sender = SimpleNamespace(name=name, request=SimpleNamespace(headers=headers))
    telemetry.started(sender=sender, task_id='sample-id')
    telemetry.finished(sender=sender, task_id='sample-id', state='SUCCESS')
    telemetry.revoked(request=SimpleNamespace(name=name,id='sample-id'), expired=True, terminated=False)
    assert [event for event, fields in events] == ['publish_attempt','published','started','finished','discarded']
    assert events[2][1]['queue_wait_seconds'] >= 0
    assert events[-1][1]['expired'] is True
    assert events[0][1]['scheduled_eta'] is None  # Never invent a beat due timestamp.


@pytest.mark.asyncio
async def test_config_service_enforces_owner_before_write():
    from unittest.mock import AsyncMock, Mock
    from app.services.config_service import ConfigService
    result = Mock(); result.scalar.return_value = False
    db = SimpleNamespace(execute=AsyncMock(return_value=result), add=Mock(), commit=AsyncMock())
    with pytest.raises(PermissionError):
        await ConfigService().update_config(db, 'ohlcv_comparison', 'owner', CONFIG, 'owner')
    db.add.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('consumers,expected', [({}, True),
    ({'canonical-worker':[{'name':'canonical_ohlcv'}]},False), (None,None)])
async def test_status_distinguishes_absent_consumer_from_unknown(monkeypatch,consumers,expected):
    import sys
    from app.api import system
    from test_celery_queue_status_and_dedup import _MiniSyncRedis
    fake = _MiniSyncRedis()
    monkeypatch.setitem(sys.modules,'redis',SimpleNamespace(from_url=lambda *a,**kw:fake))
    inspector=SimpleNamespace(ping=lambda:{'worker':{'ok':'pong'}},active=lambda:{},
        registered=lambda:{},active_queues=lambda:consumers)
    monkeypatch.setattr('app.tasks.celery_app.celery_app',SimpleNamespace(
        control=SimpleNamespace(inspect=lambda **kw:inspector)))
    monkeypatch.setattr(system,'_CELERY_STATUS_CACHE',{'payload':None,'expires_at':0})
    result=await system.get_celery_status()
    assert result['queues']['canonical_ohlcv']['consumer_missing'] is expected

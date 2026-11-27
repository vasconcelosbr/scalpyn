from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.backoffice import DecisionLog, L3AuthorizationOutbox
from app.services.l3_rejected_profile_audit import build_rejected_profile_audits
from app.services.profile_display_status import classify_profile_snapshot
from app.services.profile_runtime_config import canonical_profile_config_hash
from app.tasks.pipeline_scan import _persist_decision_logs


def fixture(value=23, age=20):
    now = datetime.now(timezone.utc)
    wl = SimpleNamespace(id=uuid4(), profile_id=uuid4(), name='M5', level='L3', source_watchlist_id=uuid4())
    condition = dict(id='adx', field='adx', operator='>=', value=24, period=14,
                     source='ohlcv', source_provider='gate.io', provider_policy_id='closed-v1',
                     timeframe='5m', candle_policy='CLOSED_ONLY', max_age_seconds=360, required=False)
    profile = dict(default_timeframe='5m', filters=dict(logic='AND', conditions=[condition]),
                   block_rules=dict(blocks=[]), signals=dict(conditions=[]), entry_triggers=dict(conditions=[]))
    profile['_execution_contract'] = dict(contract_valid=True, profile_id=str(wl.profile_id),
        profile_version_id=str(uuid4()), profile_projection_hash=canonical_profile_config_hash(profile))
    candidate = dict(indicator='adx', actual=value, source='ohlcv', source_provider='gate.io',
        provider_policy_id='closed-v1', timeframe='5m', period=14, candle_policy='CLOSED_ONLY',
        candle_closed=True, source_timestamp=now-timedelta(seconds=age),
        computed_at=now-timedelta(seconds=age), available_at=now-timedelta(seconds=age),
        age_seconds=age, stale=False)
    asset = dict(symbol='TAO_USDT', indicators={'adx': value},
                 _merged_indicators=SimpleNamespace(candidates=[candidate] if value is not None else []))
    return now, wl, profile, asset


@pytest.mark.parametrize('value,age,status', [(23,20,'rejected'), (24,20,'approved'),
    (25,20,'approved'), (23,400,'awaiting_data'), (None,20,'awaiting_data')])
def test_pre_gate_rejection_refreshes_current_profile_without_execution(value, age, status):
    now, wl, profile, asset = fixture(value, age)
    before = deepcopy(profile)
    decision, = build_rejected_profile_audits([asset], [], profile_config=profile,
        watchlist=wl, profile_meta=dict(name='M5', version=now), evaluated_at=now)
    contract = decision['metrics']['l3_authorization_contract_v3']
    row = dict(sections=contract['sections'], lineage=contract['lineage'],
        evaluated_at=contract['evaluated_at'],
        profile_hash=contract['profile_execution_contract']['profile_projection_hash'])
    assert classify_profile_snapshot(row, profile, watchlist_id=wl.id, profile_id=wl.profile_id,
        profile_version=now, now=now)['status'] == status
    assert decision['decision'] == 'BLOCK'
    assert decision['_shadow_creation_required'] is decision['_consolidation_required'] is False
    assert profile == before
    assert not build_rejected_profile_audits([asset], [asset], profile_config=profile,
        watchlist=wl, profile_meta=dict(name='M5', version=now), evaluated_at=now)


@pytest.mark.asyncio
async def test_diagnostic_persists_despite_recent_decision_and_never_creates_outbox():
    now, wl, profile, asset = fixture()
    decisions = build_rejected_profile_audits([asset], [], profile_config=profile,
        watchlist=wl, profile_meta=dict(name='M5', version=now), evaluated_at=now)
    class DB:
        def __init__(self): self.added = []
        async def execute(self, statement):
            return SimpleNamespace(fetchall=lambda: [SimpleNamespace(symbol='TAO_USDT',
                strategy='L3', direction='SPOT', profile_id=wl.profile_id)])
        def add_all(self, rows): self.added.extend(rows)
        async def flush(self):
            for row in self.added: row.id = 1
    db = DB()
    result = await _persist_decision_logs(db, uuid4(), decisions)
    assert len(result) == 1
    assert len(db.added) == 1 and isinstance(db.added[0], DecisionLog)
    assert not any(isinstance(row, L3AuthorizationOutbox) for row in db.added)
    assert result[0]['decision'] == 'BLOCK'


def test_scanner_audits_excluded_candidates_before_operational_evaluation():
    import inspect
    from app.tasks import pipeline_scan
    source = inspect.getsource(pipeline_scan)
    start = source.index('_capture_profile_name = _capture_prof_meta.get("name")')
    end = source.index('decisions = await _evaluate_l3_decisions(', start)
    assert 'assets, profile_passed, profile_config=profile_config' in source[start:end]
    assert 'await _persist_decision_logs(db, wl.user_id, _rejected_audits)' in source[start:end]

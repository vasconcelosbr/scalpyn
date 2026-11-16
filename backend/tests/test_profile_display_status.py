from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.l3_authorization_contract_v3 import build_authorization_contract
from app.services.profile_display_status import classify_profile_snapshot
from app.services.profile_runtime_config import canonical_profile_config_hash

NOW = datetime(2026, 11, 16, 22, 21, tzinfo=timezone.utc)


def fixture(value=65, age=20, section='block_rules', logic='AND', second=False):
    condition = {'id': 'rsi', 'indicator': 'rsi', 'operator': '>=', 'value': 90,
        'source': 'ohlcv', 'source_provider': 'gate_io_candles',
        'provider_policy_id': 'closed-v1', 'timeframe': '5m', 'period': 14,
        'candle_policy': 'CLOSED_ONLY', 'max_age_seconds': 360, 'required': True}
    profile = {name: {'logic': 'AND', 'conditions': []}
               for name in ['filters', 'signals', 'entry_triggers']}
    profile['block_rules'] = {'blocks': []}
    if section == 'block_rules':
        profile[section]['blocks'] = [{'id': 'block-rsi', 'logic': logic, 'conditions': [condition]}]
    else:
        profile[section] = {'logic': logic, 'conditions': [condition]}
    if second:
        profile['block_rules']['blocks'][0]['conditions'].append({**condition, 'id':'rsi-upper', 'operator':'<', 'value':95})
    asset = {'symbol': 'BTC_USDT', '_merged_indicators': SimpleNamespace(candidates=[{
        'indicator': 'rsi', 'actual': value, 'source': 'gate_candles',
        'source_provider': 'gate_io_candles', 'provider_policy_id': 'closed-v1',
        'timeframe': '5m', 'period': 14, 'candle_policy': 'CLOSED_ONLY',
        'candle_closed': True, 'source_timestamp': NOW-timedelta(seconds=age),
        'computed_at': NOW-timedelta(seconds=age), 'available_at': NOW-timedelta(seconds=age),
        'age_seconds': age, 'stale': False}])}
    contract = build_authorization_contract(asset=asset, profile_config=profile,
        legacy_decision='ALLOW', evaluated_at=NOW, profile_id='profile',
        profile_name='M5', profile_version=NOW, watchlist_id='watchlist')
    row = {'sections': contract['sections'], 'lineage': contract['lineage'],
           'evaluated_at': contract['evaluated_at'],
           'profile_hash': canonical_profile_config_hash(profile)}
    return profile, row, contract


def classify(profile, row, **kw):
    return classify_profile_snapshot(row, profile, watchlist_id='watchlist',
        profile_id='profile', profile_version=NOW, now=kw.get('now', NOW))


@pytest.mark.parametrize('value,status', [(89.99, 'approved'), (90, 'rejected'), (90.01, 'rejected')])
def test_only_configured_rsi_boundary_rejects(value, status):
    profile, row, _ = fixture(value)
    before = deepcopy(row)
    result = classify(profile, row)
    assert result['status'] == status
    assert result['executable'] is result['execution_authority'] is False
    assert row == before


@pytest.mark.parametrize('value', [65, 95])
def test_expired_data_is_separate_even_when_last_value_tripped_rule(value):
    profile, row, contract = fixture(value, age=382)
    assert 'FEATURE_TTL_EXPIRED' in contract['reason_codes']
    result = classify(profile, row)
    assert result['status'] == 'awaiting_data'
    assert 'FEATURE_TTL_EXPIRED' in result['data_reasons']


def test_expiry_after_evaluation_has_no_display_grace_that_looks_current():
    profile, row, _ = fixture()
    assert classify(profile, row, now=NOW+timedelta(seconds=341))['status'] == 'awaiting_data'


def test_global_rules_score_and_shadow_contract_are_not_profile_vetoes():
    profile, row, _ = fixture()
    row.update(decision='BLOCK', score=0, valid=False, mode='SHADOW')
    row['sections']['global_entry_triggers'] = {'passed': False, 'contract_reject': True}
    assert classify(profile, row)['status'] == 'approved'


@pytest.mark.parametrize('section', ['filters', 'signals', 'entry_triggers'])
def test_configured_required_predicate_replay_blocks_below_and_passes_at_boundary(section):
    profile, row, _ = fixture(89.99, section=section)
    assert classify(profile, row)['status'] == 'rejected'
    assert classify(profile, row)['trace'][0]['condition_id'] == 'rsi'
    profile, row, _ = fixture(90, section=section)
    assert classify(profile, row)['status'] == 'approved'


def test_missing_or_changed_profile_evidence_waits_instead_of_rejecting():
    profile, row, _ = fixture()
    assert classify(profile, None)['status'] == 'awaiting_data'
    profile['block_rules']['blocks'][0]['conditions'][0]['value'] = 80
    assert classify(profile, row)['data_reasons'] == ['PROFILE_EVALUATION_OUTDATED']


def test_disabled_block_does_not_reject():
    profile, row, _ = fixture(95)
    profile['block_rules']['blocks'][0]['enabled'] = False
    row['profile_hash'] = canonical_profile_config_hash(profile)
    assert classify(profile, row)['status'] == 'approved'


@pytest.mark.asyncio
async def test_api_returns_three_disjoint_states_without_executable_authority(monkeypatch):
    from app.api.watchlists import _profile_display_payload
    from app.services import profile_display_status
    rows = [{'symbol': symbol, 'status': status, 'trace': [], 'evaluated_at': NOW.isoformat(),
        'alpha_score': 0, 'current_price': 1, 'decision_id': i,
        'data_reasons': ['FEATURE_TTL_EXPIRED'] if status == 'awaiting_data' else []}
        for i, (symbol, status) in enumerate([('BTC_USDT','approved'),('ETH_USDT','rejected'),('AAVE_USDT','awaiting_data')])]
    monkeypatch.setattr(profile_display_status, 'load_profile_display_rows', AsyncMock(return_value=rows))
    payload = await _profile_display_payload(SimpleNamespace(id='watchlist',profile_id='profile'), 'user', None, {})
    assert payload['metrics'] == {'total':3,'approved_count':1,'rejected_count':1,'awaiting_data_count':1}
    assert payload['items'][0]['symbol'] == 'ETH_USDT'
    assert payload['approved_items'][0]['current_values']['_meta:price'] == 1
    assert payload['approved_items'][0]['details']['current_values']['_meta:price'] == 1
    assert payload['awaiting_data_items'][0]['status'] == 'awaiting_data'
    assert all(not item['executable'] for key in ['items','approved_items','awaiting_data_items'] for item in payload[key])


@pytest.mark.parametrize('logic,value,status', [('AND',92,'rejected'),('AND',97,'approved'),('OR',97,'rejected'),('AND',89,'approved')])
def test_configured_boolean_logic_and_short_circuit_remain_intact(logic,value,status):
    profile,row,_ = fixture(value,logic=logic,second=True)
    assert classify(profile,row)['status'] == status


def test_global_short_circuit_does_not_claim_profile_approval():
    profile,row,_ = fixture()
    row['sections']['block_rules']['blocks'][0].update(status='NOT_NEEDED_FOR_BOOLEAN_RESULT',conditions=[])
    assert classify(profile,row)['status'] == 'awaiting_data'


def test_missing_condition_evidence_waits():
    profile,row,_ = fixture()
    row['sections']['block_rules']['blocks'][0]['conditions'] = []
    assert classify(profile,row)['status'] == 'awaiting_data'

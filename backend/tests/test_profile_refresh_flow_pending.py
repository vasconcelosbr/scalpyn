from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.l3_authorization_contract_v3 import contract_authorizes_shadow_capture
from app.services.l3_public_authorization import public_authorization
from app.services.profile_display_status import classify_profile_snapshot
from app.services.profile_runtime_config import canonical_profile_config_hash
from app.utils.indicator_merge import MergedIndicators
from app.tasks import pipeline_scan


@pytest.mark.asyncio
@pytest.mark.parametrize('rsi,age,expected', [(64, 90, 'approved'), (90, 90, 'rejected'),
                                          (64, 400, 'awaiting_data')])
async def test_profile_refreshes_without_authorizing_stale_operational_flow(monkeypatch, rsi, age, expected):
    from app.services import block_condition_timeframe, l3_contract_inputs

    profile = {'default_timeframe': '5m', 'filters': {'conditions': []},
               'entry_triggers': {'conditions': []}, 'signals': {'conditions': []},
               'block_rules': {'blocks': [{'id': 'rsi-veto', 'enabled': True,
                   'logic': 'AND', 'conditions': [{'id': 'rsi', 'type': 'threshold',
                       'indicator': 'rsi', 'source': 'ohlcv', 'source_provider': 'gate.io',
                       'provider_policy_id': 'spot_gate_closed_ohlcv_v1', 'period': 14,
                       'timeframe': '5m', 'candle_policy': 'CLOSED_ONLY',
                       'operator': '>=', 'value': 90, 'max_age_seconds': 360}]}]}}
    original = deepcopy(profile)
    at = datetime.now(timezone.utc)
    merged = MergedIndicators()
    merged.candidates = [{'indicator': 'rsi', 'actual': rsi, 'source': 'ohlcv',
        'source_provider': 'gate.io', 'provider_policy_id': 'spot_gate_closed_ohlcv_v1',
        'period': 14, 'timeframe': '5m', 'candle_policy': 'CLOSED_ONLY', 'candle_closed': True,
        'scheduler_group': 'structural', 'source_timestamp': (at-timedelta(seconds=age)).isoformat(),
        'computed_at': at.isoformat(), 'available_at': at.isoformat(), 'stale': False}]
    assets = [{'symbol': 'BTC_USDT', 'rsi': rsi, 'indicators': {'rsi': rsi},
               '_merged_indicators': merged}]
    monkeypatch.setenv('L3_EXACT_TIMEFRAME_RESOLUTION', 'false')
    monkeypatch.setattr(block_condition_timeframe, 'prepare_block_candle_inputs', AsyncMock(return_value=assets))
    monkeypatch.setattr(l3_contract_inputs, 'prepare_l3_contract_inputs', AsyncMock(return_value=assets))
    monkeypatch.setattr(pipeline_scan, '_apply_robust_authoritative_scoring', AsyncMock())
    monkeypatch.setattr(pipeline_scan, '_inject_live_order_flow', AsyncMock(return_value=(
        {'rsi': rsi, '_l3_flow_check': {'status': 'STALE'}}, False)))
    # Even an operational gate promotion cannot bypass the existing flow block.
    monkeypatch.setattr(pipeline_scan, '_apply_l3_gate_v2_operational_promotion',
                        lambda **kwargs: ('ALLOW', True))
    decisions = await pipeline_scan._evaluate_l3_decisions(
        assets, profile, 'L3', {}, db=object(), user_id='u', watchlist_id='w',
        profile_id='p', profile_name='M5', profile_version=at, read_only=True)
    assert len(decisions) == 1
    decision = decisions[0]
    contract = decision['metrics']['l3_authorization_contract_v3']
    assert decision['decision'] == 'BLOCK' and decision['l3_pass'] is False
    assert decision['metrics']['operational_data']['reason_codes'] == ['LIVE_ORDER_FLOW_PENDING']
    assert contract['technical_decision'] == contract['final_decision'] == 'BLOCK'
    assert not contract_authorizes_shadow_capture(contract, legacy_decision=decision['decision'])
    assert public_authorization(SimpleNamespace(**decision), None, None, watchlist_id='w') is None
    row = {**contract, 'profile_hash': canonical_profile_config_hash(profile)}
    assert classify_profile_snapshot(row, profile, watchlist_id='w', profile_id='p',
                                     profile_version=at)['status'] == expected
    assert profile == original

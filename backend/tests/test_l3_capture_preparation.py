from copy import deepcopy
from app.services.indicator_calculation_identity import calculation_identities
from app.services.l3_authorization_contract_v3 import _latest_same_identity, _freshness_reasons
from app.utils.indicator_merge import envelop_results
import pytest
from datetime import datetime, timezone
from app.services.l3_capture_preparation import prepare_profile_metadata
from app.services.l3_capture_diagnostics import capture_stage


def test_metadata_repair_preserves_predicates_and_rejects_other_period_changes():
    policies = {'ohlcv': {'source_provider': 'gate.io', 'provider_policy_id': 'test',
                         'max_age_seconds': 700, 'candle_policy': 'CLOSED_ONLY'}}
    config = {'default_timeframe': '5m', 'entry_triggers': {'conditions': [
        {'id': 'vwap', 'indicator': 'vwap_distance_pct', 'period': 20, 'operator': '>', 'value': 0.5,
         'required': True, 'points': 7}]}, 'scoring': {'thresholds': {'buy': 70}}}
    before = deepcopy(config)
    actual = prepare_profile_metadata(config, {}, policies)
    assert config == before
    rule = actual['entry_triggers']['conditions'][0]
    assert rule['period'] is None and rule['parameters'] == {'reset': 'UTC_DAY'}
    assert {k: rule[k] for k in ('id', 'indicator', 'operator', 'value', 'required', 'points')} == {
        k: before['entry_triggers']['conditions'][0][k] for k in ('id', 'indicator', 'operator', 'value', 'required', 'points')}
    assert actual['scoring'] == before['scoring']
    config['entry_triggers']['conditions'][0]['indicator'] = 'rsi'
    with pytest.raises(ValueError, match='PERIOD_CONFLICT'):
        prepare_profile_metadata(config, {'rsi': {'period': 14}}, policies)


def test_waiting_diagnosis_does_not_certify_immature_or_excluded_capture():
    now = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)
    cfg = {'ml_active_barrier_contract_version': 'active', 'ml_maturity_embargo_margin_minutes': 10}
    row = {'id': 'x', 'created_at': now, 'lineage_status': 'EXACT', 'eligible_for_training': True,
           'barrier_contract_version': 'active', 'outcome': 'TP_HIT', 'measurement_status': 'READY',
           'entry_quality': 'OK', 'completed_at': now, 'ttt_timeout_minutes': 240}
    assert capture_stage(row, cutoff=now, config=cfg, eligible_ids=set())['stage'] == 'AWAITING_MATURITY'
    assert capture_stage({**row, 'outcome': None}, cutoff=now, config=cfg, eligible_ids=set())['stage'] == 'AWAITING_OUTCOME'
    assert capture_stage({**row, 'outcome': 'TRAILING_STOP'}, cutoff=now, config=cfg, eligible_ids=set())['stage'] == 'EXCLUDED'
    assert capture_stage({**row, 'lineage_status': 'INVALID_AUTHORIZATION_CONTRACT'}, cutoff=now, config=cfg, eligible_ids=set())['reason'] == 'INVALID_AUTHORIZATION_CONTRACT'
    assert capture_stage(row, cutoff=now, config=cfg, eligible_ids={'x'})['stage'] == 'ELIGIBLE'


def test_book_metadata_preserves_observation_clock_and_does_not_invent_it():
    from app.services.market_data_service import MarketDataNormalized
    book = MarketDataNormalized(symbol='BTC_USDT', bid_ask_imbalance=0.2,
        source_map={'bid_ask_imbalance': 'gate'}, source_times={'bid_ask_imbalance': '2026-09-16T12:00:00Z'})
    first = book.to_indicator_payload()
    assert first['_source_metadata']['bid_ask_imbalance']['source_timestamp'] == '2026-09-16T12:00:00Z'
    assert book.to_indicator_payload() == first
    book.source_times.clear()
    assert book.to_indicator_payload()['_source_metadata'] == {}


@pytest.mark.asyncio
async def test_on_demand_uses_canonical_evaluator_and_preserves_block(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services import profile_execution_contract as contracts
    from app.services.config_service import config_service
    from app.tasks import pipeline_scan
    from app.services.l3_on_demand_decisions import evaluate_on_demand_l3
    snapshot = {'name': 'profile', 'version': 'v1', 'version_id': 'version',
                'config': {}, 'contract': {'profile_id': 'p', 'contract_valid': True}}
    monkeypatch.setattr(contracts, 'load_profile_execution_snapshots', AsyncMock(return_value={'p': snapshot}))
    monkeypatch.setattr(config_service, 'get_config', AsyncMock(return_value={}))
    monkeypatch.setattr(pipeline_scan, '_fetch_market_data', AsyncMock(return_value=[{'symbol': 'BTC_USDT'}]))
    evaluate = AsyncMock(return_value=[{'decision': 'BLOCK', 'metrics': {'l3_gate_v2': {'status': 'BLOCK'}}}])
    monkeypatch.setattr(pipeline_scan, '_evaluate_l3_decisions', evaluate)
    watchlist = SimpleNamespace(id='wl', profile_id='p', name='watchlist', level='L3', source_watchlist_id=None)
    result = await evaluate_on_demand_l3(object(), user_id='u', watchlist=watchlist, symbols=['BTC_USDT'], score_config={})
    assert result[0]['decision'] == 'BLOCK'
    assert evaluate.call_args.args[1]['_execution_contract']['watchlist_profile_id'] == 'p'
    assert evaluate.call_args.kwargs['watchlist_id'] == 'wl'
    snapshot['contract']['contract_valid'] = False
    with pytest.raises(ValueError, match='PROFILE_CONTRACT_INVALID'):
        await evaluate_on_demand_l3(object(), user_id='u', watchlist=watchlist, symbols=['BTC_USDT'], score_config={})
    assert evaluate.await_count == 1


def test_calculation_identity_is_producer_config_not_profile_request():
    values = {'rsi': 50, 'rsi_6': 60, 'macd_histogram': 1, 'vwap_distance_pct': 2, 'volume_delta': 30}
    metadata = calculation_identities({'rsi': {'period': 17}, 'macd': {'fast': 8, 'slow': 21, 'signal': 5}}, values)
    assert metadata['rsi']['period'] == 17
    assert metadata['rsi_6']['period'] == 6
    assert metadata['macd_histogram']['parameters'] == {'fast': 8, 'slow': 21, 'signal': 5}
    assert metadata['vwap_distance_pct'] == {'period': None, 'parameters': {'reset': 'UTC_DAY'}}
    assert 'volume_delta' not in metadata
    envelopes = envelop_results(values, key_metadata=metadata)
    assert {k: v['value'] for k, v in envelopes.items()} == values
    assert envelopes['rsi']['period'] == 17
    assert envelopes['rsi']['envelope_hash']


def test_book_identity_overrides_candle_metadata_without_changing_value():
    out = envelop_results({'bid_ask_imbalance': 0.2},
        envelope_metadata={'timeframe': '5m', 'source_timestamp': 'candle'},
        key_metadata={'bid_ask_imbalance': {'timeframe': None, 'source_timestamp': 'book', 'snapshot': True}})
    assert out['bid_ask_imbalance']['value'] == 0.2
    assert out['bid_ask_imbalance']['timeframe'] is None
    assert out['bid_ask_imbalance']['source_timestamp'] == 'book'


def test_repeated_identity_selects_newest_without_collapsing_other_timeframes():
    old = {'indicator': 'rsi', 'source': 'ohlcv', 'timeframe': '5m', 'actual': 50,
           'source_timestamp': '2026-09-16T12:00:00Z', 'computed_at': '2026-09-16T12:05:00Z'}
    new = {**old, 'source_timestamp': '2026-09-16T12:05:00Z', 'actual': 55}
    other = {**new, 'timeframe': '1h'}
    assert _latest_same_identity([old, new, other]) == [new, other]
    assert len(_latest_same_identity([new, deepcopy(new)])) == 2
    conflict = {**new, 'actual': 60}
    assert len(_latest_same_identity([new, conflict])) == 2


def test_future_provenance_cannot_be_certified_fresh():
    candidate = {'source': 'decision_context', 'source_timestamp': '2026-09-16T12:01:00Z',
                 'evaluated_at': '2026-09-16T12:00:00Z', 'age_seconds': 0}
    assert 'SOURCE_TIMESTAMP_IN_FUTURE' in _freshness_reasons({'max_age_seconds': 10}, candidate)

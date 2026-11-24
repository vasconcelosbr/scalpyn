"""Offline decision replay; never substitute current candles/config for history."""
from copy import deepcopy
from .profile_runtime_config import canonical_hash
from .mtf_chart_engine import evaluate, timestamp
from ..schemas.mtf_chart import MTFChartConfig


def freeze_inputs(data, cfg):
    analysis = data['analysis']
    material = dict(contract='mtf_decision_replay_v1',
        symbol=analysis['symbol'], config=cfg.model_dump(),
        cutoff=timestamp(analysis['evaluated_at_utc']),
        evaluated_at=data.get('replay_evaluated_at', timestamp(analysis['evaluated_at_utc'])),
        frames={tf: {k: deepcopy(f[k]) for k in ('candles', 'pivots', 'patterns', 'atr') if k in f}
                for tf, f in data['frames'].items()},
        spread_pct=analysis.get('spread_pct'), btc_trend=analysis.get('btc_trend'),
        flow=deepcopy(analysis.get('pump_flow')), rule_series=deepcopy(data.get('rule_series') or {}))
    return {**material, 'input_hash': canonical_hash(material)}


def replay(payload):
    inputs = deepcopy(payload.get('replay_inputs'))
    if not inputs:
        return {'status': 'NOT_REPRODUCIBLE', 'reason': 'LEGACY_INPUTS_NOT_FROZEN',
                'scope': 'decision', 'recorded_status': payload['analysis'].get('status')}
    identity = inputs.pop('input_hash', None)
    if identity != canonical_hash(inputs):
        return {'status': 'INVALID', 'reason': 'INPUT_HASH_MISMATCH'}
    cfg = MTFChartConfig.model_validate(inputs['config'])
    result = evaluate(inputs['symbol'], inputs['frames'], cfg, inputs['cutoff'],
                      inputs['spread_pct'], inputs['btc_trend'],
                      rule_series=inputs['rule_series'], flow=inputs['flow'])
    from .mtf_chart_engine import TF_SECONDS
    trigger = cfg.stage('trigger')
    if inputs['evaluated_at'] - inputs['cutoff'] > cfg.stale_tf_multiplier * TF_SECONDS[trigger]:
        result['vetoes'].append('DATA_STALE:' + trigger)
        result['status'] = 'NO_TRADE'
        result['score']['total'] = 0
    keys = ('status', 'plan', 'score', 'vetoes', 'trigger_5m')
    matches = {key: result.get(key) == payload['analysis'].get(key) for key in keys}
    return {'status': 'MATCH' if all(matches.values()) else 'MISMATCH', 'scope': 'decision',
            'matches': matches, 'input_hash': identity,
            'indicator_recalculation': 'NOT_PERFORMED_FROZEN_INTERMEDIATES'}

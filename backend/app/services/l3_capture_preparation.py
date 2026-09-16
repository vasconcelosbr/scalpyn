"""Deterministic metadata-only preparation and read-only L3 capture diagnosis."""
from copy import deepcopy
from .indicator_calculation_identity import calculation_identities
from .profile_indicator_contract import PROFILE_INDICATOR_CONTRACT
from .l3_authorization_contract_v3 import validate_profile_contract

FLOW = {'taker_ratio', 'volume_delta', 'buy_pressure', 'taker_buy_volume', 'taker_sell_volume'}
BOOK = {'orderbook_pressure', 'bid_ask_imbalance'}
CONTEXT = {'volume_24h', 'market_cap', 'price', 'change_24h', 'spread_pct', 'orderbook_depth_usdt',
           'score', 'alpha_score', 'liquidity_score', 'momentum_score', 'market_structure_score', 'signal_score'}


def prepare_profile_metadata(config: dict, indicator_config: dict, source_policies: dict) -> dict:
    """Preserve every trading predicate. Resolve only producer-owned identity.

    VWAP/flow legacy period cleanup is explicitly authorized by the operator.
    Any other conflicting period/source is an error, not an automatic rewrite.
    """
    result = deepcopy(config)
    def reference(name, original, timeframe):
        if name not in PROFILE_INDICATOR_CONTRACT and name not in FLOW | CONTEXT:
            raise ValueError(f'UNKNOWN_INDICATOR:{name}')
        source = 'live_trade_flow' if name in FLOW else 'live_order_book' if name in BOOK else 'decision_context' if name in CONTEXT else 'ohlcv'
        expected = deepcopy(source_policies[source])
        if original.get('source') and original['source'] != source:
            raise ValueError(f'SOURCE_CONFLICT:{name}')
        expected['source'] = source
        expected['indicator'] = name
        if source == 'decision_context':
            expected['source_provider'] = 'robust_score' if name in {
                'score', 'alpha_score', 'liquidity_score', 'momentum_score',
                'market_structure_score', 'signal_score'} else 'market_metadata'
        if source == 'ohlcv':
            expected['timeframe'] = original.get('timeframe') or timeframe
            if not expected['timeframe']:
                raise ValueError(f'TIMEFRAME_REQUIRED:{name}')
        metadata = calculation_identities(indicator_config, {name: 0}).get(name, {'period': None, 'parameters': {}})
        if original.get('period') is not None and original['period'] != metadata['period']:
            if name not in FLOW | {'vwap', 'vwap_distance_pct', 'vwap_reclaim_bool'}:
                raise ValueError(f'PERIOD_CONFLICT:{name}:{original["period"]}:{metadata["period"]}')
        expected.update(metadata)
        if original.get('max_age_seconds') is not None:
            expected['max_age_seconds'] = original['max_age_seconds']
        if original.get('parameters') and original['parameters'] != metadata['parameters']:
            raise ValueError(f'PARAMETERS_CONFLICT:{name}')
        if original.get('reference_window') is not None:
            expected['reference_window'] = original['reference_window']
        return expected
    def condition(item, timeframe):
        if item.get('type') == 'comparison':
            item['resolved_operands'] = {
                side: reference(item[side], (item.get('resolved_operands') or {}).get(side, {}), timeframe)
                for side in ('left', 'right') if side in item
            }
        else:
            name = item.get('indicator') or item.get('field')
            identity = reference(name, item, timeframe)
            # Preserve the original indicator/field spelling and all predicates.
            identity.pop('indicator')
            item.update(identity)
    timeframe = result.get('default_timeframe')
    for section in ('filters', 'signals', 'entry_triggers'):
        for item in (result.get(section) or {}).get('conditions') or []:
            condition(item, timeframe)
    for block in (result.get('block_rules') or {}).get('blocks') or []:
        for item in block.get('conditions') or [block]:
            condition(item, block.get('timeframe') or timeframe)
    errors = validate_profile_contract(result)
    if errors:
        raise ValueError(f'PREPARED_PROFILE_INVALID:{errors}')
    return result

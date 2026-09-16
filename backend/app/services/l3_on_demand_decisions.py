"""Use the scanner's canonical evaluator for on-demand L3 decision records."""
from copy import deepcopy


async def evaluate_on_demand_l3(db, *, user_id, watchlist, symbols, score_config, read_only=False):
    from .config_service import config_service
    from .profile_execution_contract import load_profile_execution_snapshots
    from .profile_runtime_config import merge_profile_runtime_block_config
    from .l3_gate_runtime_policy import build_policy_snapshot
    from ..tasks.pipeline_scan import _fetch_market_data, _evaluate_l3_decisions

    snapshots = await load_profile_execution_snapshots(db, [watchlist.profile_id], user_id=user_id)
    snapshot = snapshots.get(watchlist.profile_id)
    if not snapshot or not snapshot['contract'].get('contract_valid'):
        raise ValueError('L3_ON_DEMAND_PROFILE_CONTRACT_INVALID')
    block = await config_service.get_config(db, 'block', user_id)
    spot = await config_service.get_config(db, 'spot_engine', user_id)
    config = merge_profile_runtime_block_config(
        snapshot['config'], block or {}, profile_id=watchlist.profile_id,
        profile_version_id=snapshot['version_id'])
    config['_execution_contract'] = deepcopy(snapshot['contract'])
    config['_execution_contract']['watchlist_profile_id'] = str(watchlist.profile_id)
    if config['_execution_contract'].get('profile_id') != str(watchlist.profile_id):
        raise ValueError('L3_ON_DEMAND_PROFILE_ID_MISMATCH')
    config['_l3_gate_runtime_policy'] = build_policy_snapshot(
        (spot or {}).get('scanner') or {}, profile_id=str(watchlist.profile_id))
    assets = await _fetch_market_data(db, symbols)
    if assets is None:
        raise ValueError('L3_ON_DEMAND_MARKET_DATA_UNAVAILABLE')
    for asset in assets:
        asset['is_futures'] = getattr(watchlist, 'market_mode', 'spot') == 'futures'
    return await _evaluate_l3_decisions(
        assets, config, 'L3', score_config, db=db, user_id=user_id,
        watchlist_id=watchlist.id, profile_id=watchlist.profile_id,
        profile_name=snapshot['name'], profile_version=snapshot['version'],
        watchlist_name=watchlist.name, watchlist_level=watchlist.level,
        source_watchlist_id=watchlist.source_watchlist_id, read_only=read_only)

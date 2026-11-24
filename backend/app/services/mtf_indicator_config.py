"""Resolve the MTF-only ATR and its identity identically for producers/readers."""
from copy import deepcopy

from .profile_runtime_config import canonical_hash


def effective_indicators(indicators, identity, cfg):
    effective = deepcopy(indicators)
    if cfg.atr_period is not None:
        effective['atr'] = {**effective.get('atr', {}), 'enabled': True, 'period': cfg.atr_period}
    return effective, {**identity, 'config_hash': canonical_hash(effective)}


def mtf_indicator_identity(cfg, user_id):
    return {'config_user_id': str(user_id), 'config_hash': canonical_hash({
        key: instance.model_dump(exclude={'display'}) for key, instance in cfg.indicators.items()
    })}

"""Resolve the MTF-only ATR and its identity identically for producers/readers."""
from copy import deepcopy

from .profile_runtime_config import canonical_hash


def effective_indicators(indicators, identity, cfg):
    effective = deepcopy(indicators)
    if cfg.atr_period is not None:
        effective['atr'] = {**effective.get('atr', {}), 'enabled': True, 'period': cfg.atr_period}
    return effective, {**identity, 'config_hash': canonical_hash(effective)}

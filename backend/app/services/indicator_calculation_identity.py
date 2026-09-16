"""Calculation identities emitted by FeatureEngine's actual configured formulas.

This is producer metadata, never an inference from a consumer's requested period.
It does not change indicator values or retrofit historical observations.
"""
from copy import deepcopy


def calculation_identities(config: dict, values: dict) -> dict:
    identities = {}
    for name in values:
        period = None
        parameters = {}
        if name == 'rsi' or name.startswith('rsi_slope_'):
            period = int(config.get('rsi', {}).get('period', 14))
        elif name.startswith('rsi_') and name[4:].isdigit():
            period = int(name[4:])
        elif name in {'adx', 'di_plus', 'di_minus', 'di_trend', 'di_plus_minus_diff', 'adx_acceleration', 'adx_slope_3'}:
            period = int(config.get('adx', {}).get('period', 14))
        elif name in {'atr', 'atr_pct', 'atr_percent'}:
            period = int(config.get('atr', {}).get('period', 14))
        elif name.startswith('macd'):
            cfg = config.get('macd', {})
            period = int(cfg.get('fast', 12))
            parameters = {'fast': period, 'slow': int(cfg.get('slow', 26)), 'signal': int(cfg.get('signal', 9))}
        elif name.startswith('bb_'):
            cfg = config.get('bollinger', {})
            period = int(cfg.get('period', 20))
            parameters = {'deviation': float(cfg.get('deviation', 2.0))}
        elif name in {'stoch_k', 'stoch_d'}:
            cfg = config.get('stochastic', {})
            period = int(cfg.get('k' if name == 'stoch_k' else 'd', 14 if name == 'stoch_k' else 3))
            parameters = {'k': int(cfg.get('k', 14)), 'd': int(cfg.get('d', 3)), 'smooth': int(cfg.get('smooth', 3))}
        elif name == 'volume_spike':
            period = max(int(config.get('volume_spike', {}).get('lookback', 20)), 1)
        elif name in {'vwap', 'vwap_distance_pct', 'vwap_reclaim_bool'}:
            parameters = {'reset': 'UTC_DAY'}
        elif name.startswith('ema') and name[3:].isdigit():
            period = int(name[3:])
        if period is not None or parameters:
            identities[name] = {'period': period, 'parameters': deepcopy(parameters)}
    return identities

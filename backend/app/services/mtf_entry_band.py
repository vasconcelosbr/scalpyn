"""MTF_CANDIDATE_ENTRY_DISPLAY_ONLY: describe the selected support, never gate a trade."""
from math import isfinite
from .mtf_chart_engine import TF_SECONDS, iso, timestamp


def candidate_entry_band(analysis, frames, cfg, now):
    if not cfg.presentation or not cfg.presentation.entry_band_enabled:
        return None
    cutoff = timestamp(analysis['evaluated_at_utc'])
    valid_until = cutoff + cfg.stale_tf_multiplier * TF_SECONDS[cfg.stage('trigger')]
    result = dict(state='UNAVAILABLE', reason=None, timeframe=cfg.stage('zone'), low=None, high=None,
                  support_low=None, support_high=None, atr=None, proximity_atr=cfg.zone_proximity_atr,
                  entry=None, evaluated_at=analysis['evaluated_at_utc'], valid_until=iso(valid_until),
                  config_hash=analysis['config_hash'], vetoes=list(analysis.get('vetoes', [])),
                  operational_effect=False)
    if now >= valid_until or any(v.startswith(('DATA_', 'EVALUATION_STALE', 'CONFIG_CHANGED')) for v in result['vetoes']):
        return {**result, 'reason': 'DATA_UNAVAILABLE'}
    zone = analysis.get('zone_1h')
    if not zone:
        return {**result, 'reason': 'NO_SUPPORT'}
    low, high, atr = zone.get('low'), zone.get('high'), frames.get(cfg.stage('zone'), {}).get('atr')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not isfinite(v) or v <= 0 for v in (low, high, atr)) or low > high:
        return {**result, 'reason': 'INVALID_SUPPORT'}
    if zone.get('available_at', cutoff + 1) > cutoff:
        return {**result, 'reason': 'UNCONFIRMED_SUPPORT'}
    # evaluate() selects supports only when support.low <= candle.close.
    # Therefore extending below support.low would depict prices it cannot select.
    upper = high + cfg.zone_proximity_atr * atr
    if not isfinite(upper):
        return {**result, 'reason': 'INVALID_SUPPORT'}
    result.update(low=low, high=upper, support_low=low, support_high=high, atr=atr)
    if result['vetoes'] or analysis['status'] == 'NO_TRADE':
        return {**result, 'state': 'BLOCKED'}
    if analysis['status'] == 'TRIGGERED':
        plan = analysis.get('plan') or {}
        entry = plan.get('entry')
        if not isinstance(entry, (int, float)) or not isfinite(entry) or not low <= entry <= upper:
            return {**result, 'state': 'UNAVAILABLE', 'reason': 'PLAN_UNAVAILABLE', 'low': None, 'high': None}
        expires = plan.get('expires_at')
        if expires:
            valid_until = min(valid_until, timestamp(expires))
        if now >= valid_until:
            return {**result, 'state': 'UNAVAILABLE', 'reason': 'PLAN_EXPIRED', 'low': None, 'high': None}
        return {**result, 'state': 'CONFIRMED', 'entry': entry, 'valid_until': iso(valid_until)}
    return {**result, 'state': 'WAITING'}

"""Display-only intrabar diagnostics. Never returned to observers or persisted.

The official closed-candle analysis is an input, never a mutation target. Only
diagnostics leave this module; hypothetical status, plans and triggers do not.
"""
from copy import deepcopy
from .mtf_chart_engine import evaluate, prepare_frame, iso, TF_SECONDS
from .profile_runtime_config import canonical_hash


def forming_preview(symbol, raw, official_frames, cfg, now, spread, btc_trend,
                    *, indicators=None, families=None, windows=None, flow=None):
    trigger = cfg.stage('trigger')
    duration = TF_SECONDS[trigger]
    result = dict(version='mtf_forming_preview_v1', symbol=symbol,
                  config_hash=canonical_hash(cfg.engine_dump()), observed_at_utc=iso(now),
                  valid_until=iso(min(now + cfg.poll_seconds * cfg.stale_tf_multiplier,
                                      now - now % duration + duration)),
                  state='UNAVAILABLE', reason=None, operational_effect=False, refresh_seconds=cfg.poll_seconds,
                  confirmation='CLOSE_ONLY', diagnostics=None,
                  candle_open_at_utc=None, candle_close_at_utc=None)
    forming = [c for c in raw.get(trigger, []) if c['time'] <= now < c['time'] + duration]
    if len(forming) != 1 or forming[0].get('is_closed') is not False:
        result['reason'] = 'FORMING_CANDLE_UNAVAILABLE'
        return result
    result.update(candle_open_at_utc=iso(forming[0]['time']),
                  candle_close_at_utc=iso(forming[0]['time'] + duration))
    try:
        if cfg.schema_version == 'mtf_chart_v3':
            from .mtf_chart_v3 import calculate_frames
            frames, series, errors = calculate_frames(raw, cfg, now, forming_at=now)
            if errors:
                result['reason'] = 'PREVIEW_DATA_UNAVAILABLE'
                return result
        else:
            frames = {tf: prepare_frame(candles, tf, now, indicators or {}, families or set(),
                                        cfg, (windows or {}).get(tf), forming_at=now)
                      for tf, candles in raw.items()}
            series = None
        # An open candle cannot confirm pivots or silently move the structural basis.
        for tf, frame in frames.items():
            frame['pivots'] = deepcopy(official_frames.get(tf, {}).get('pivots', []))
        projected = evaluate(symbol, frames, cfg, now, spread, btc_trend,
                             rule_series=series, flow=flow, preview_at=now)
        diagnostic = deepcopy(projected['trigger_diagnostics'])
        for row in diagnostic['checks']:
            if row['id'] == 'trigger':
                row['label'] = 'Padrão de compra no candle em formação'
            if row['id'].startswith('age:') and isinstance(row['actual'], (int, float)) and row['actual'] < 0:
                row['actual'] = 0
                row['note'] = 'Candle em formação; a confirmação oficial aguarda o fechamento.'
        # No status field is exposed, even if hypothetical predicates all pass.
        result.update(state='AVAILABLE', diagnostics=dict(checks=diagnostic['checks'],
                                                          patterns=diagnostic['patterns']))
    except (ValueError, KeyError, TypeError, IndexError):
        result['reason'] = 'PREVIEW_DATA_UNAVAILABLE'
    return result

"""MTF_CONTEXT_RISK_AUDIT: causal, mandatory gates; never sends orders."""
from math import isfinite, isclose
from .mtf_trigger_diagnostics import check
from ..schemas.mtf_rules import DURATIONS

VERSION = 'mtf_context_risk_v1'


def structure(frame, tf, cutoff):
    from .mtf_chart_engine import trend, closed_candles
    candles = closed_candles(frame.get('candles', []), tf, cutoff)
    points = sorted((p for p in frame.get('pivots', [])
                     if p.get('confirmed_at', cutoff + 1) <= cutoff),
                    key=lambda p: (p['confirmed_at'], p['time']))
    direction = trend(points)
    complete = all(sum(p['kind'] == kind for p in points) >= 2 for kind in ('H', 'L'))
    # Replay successive confirmations and closed candles. A return above a broken
    # level does not heal it; a new complete structure must form after the break.
    broken_at = None
    last_break = None
    for candle in candles:
        end = candle['time'] + DURATIONS[tf]
        known = [p for p in points if p['confirmed_at'] <= end]
        if broken_at is not None:
            fresh = [p for p in known if p['time'] >= broken_at]
            if all(sum(p['kind'] == k for p in fresh) >= 2 for k in ('H', 'L')) and trend(fresh) != 'RANGE':
                broken_at = None
        side = trend(known)
        label = 'HL' if side == 'UP' else 'LH' if side == 'DOWN' else None
        pivot = next((p for p in reversed(known) if p['label'] == label), None) if label else None
        if pivot and ((side == 'UP' and candle['close'] < pivot['price']) or
                      (side == 'DOWN' and candle['close'] > pivot['price'])):
            if broken_at is None:
                broken_at = end
            last_break = dict(at=end, pivot=pivot, close=candle['close'])
    return dict(direction=direction if complete else None,
                intact=(broken_at is None) if complete and candles else None,
                broken_at=broken_at, last_break=last_break, pivots=points,
                closed_at=candles[-1]['time'] + DURATIONS[tf] if candles else None)


def audit(result, frames, cfg, cutoff, *, preview=False):
    settings = cfg.context_audit
    rows, contexts = [], {}
    def add(key, label, actual, op, expected, tf=None, enabled=True, waiting=None):
        row = check('audit:'+key, label, actual, op, expected, timeframe=tf,
                    enabled=enabled, waiting=waiting)
        if key in ('reference_rr', 'stop_atr') and row['state'] == 'FAIL' and isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12):
            row.update(state='PASS', gap=0, note='Igualdade dentro da precisão numérica de ponto flutuante.')
        row['source_cutoff'] = cutoff
        rows.append(row)
    for stage, required in [('structure', settings.require_structure_alignment), ('zone', settings.require_zone_alignment)]:
        tf = cfg.stage(stage)
        ctx = structure(frames.get(tf, {}), tf, cutoff)
        contexts[stage] = ctx
        add(stage+'_alignment', 'Direção '+stage, ctx['direction'], '=', 'UP', tf, required)
        add(stage+'_integrity', 'Estrutura íntegra '+stage, ctx['intact'], '=', True, tf,
            settings.require_structure_integrity)
    tf = cfg.stage('trigger')
    ctx = structure(frames.get(tf, {}), tf, cutoff)
    from .mtf_chart_engine import closed_candles
    candles = closed_candles(frames.get(tf, {}).get('candles', []), tf, cutoff)
    last = candles[-1] if candles else None
    if preview:
        last = next((c for c in reversed(frames.get(tf, {}).get('candles', [])) if c['time'] <= cutoff), last)
    highs = [p for p in ctx['pivots'] if p['kind'] == 'H']
    lows = [p for p in ctx['pivots'] if p['kind'] == 'L']
    high = highs[-1] if highs else None
    # Even when no LH break is needed, a known high is required.
    actual = (last['close'] > high['price'] if high['label'] == 'LH' else True) if high and last else None
    add('lh_break', 'Fechamento supera o LH operacional', actual, '=', True, tf, settings.require_trigger_lh_break)
    rows[-1].update(level=high['price'] if high else None, close=last['close'] if last else None)
    p = result.get('plan')
    waiting = None if p else 'Aguardando plano confirmado para validar stop e retorno/risco.'
    atr = frames.get(tf, {}).get('atr')
    atr = atr if isinstance(atr, (int, float)) and isfinite(atr) and atr > 0 else None
    distance = p['entry'] - p['stop'] if p else None
    add('stop_atr', 'Distância mínima do stop em ATR', distance / atr if distance is not None and atr else None,
        '>=', settings.min_stop_atr_trigger, tf, waiting=waiting)
    add('stop_swing', 'Stop abaixo do fundo operacional', p['stop'] if p else None, '<', lows[-1]['price'] if lows else None,
        tf, settings.require_stop_beyond_trigger_swing, waiting)
    zone = result.get('zone_1h') or {}
    add('stop_zone', 'Stop abaixo do suporte que armou', p['stop'] if p else None, '<', zone.get('low'),
        cfg.stage('zone'), settings.require_stop_beyond_arming_zone, waiting)
    target = p['entry'] * (1 + cfg.stop_sizing.target_pct / 100) if p else None
    rr = (p['entry'] * cfg.stop_sizing.target_pct / 100)/distance if p and distance and distance > 0 else None
    fee, slip = cfg.fee_per_side_pct/100, cfg.slippage_pct/100
    cost = p['entry']*(1+slip)*(1+fee) if p else None
    reward = target*(1-slip)*(1-fee)-cost if p else None
    loss = cost-p['stop']*(1-slip)*(1-fee) if p else None
    add('reference_rr', 'RR bruto do alvo percentual', rr, '>=', settings.min_reference_rr_gross, waiting=waiting)
    add('reference_reward', 'Retorno líquido positivo no alvo percentual', reward, '>', 0,
        enabled=settings.require_positive_reference_reward_net, waiting=waiting)
    failures = [r for r in rows if r['state'] in ('FAIL', 'UNAVAILABLE')]
    pending = any(r['state'] == 'WAITING' for r in rows)
    return dict(version=VERSION, status='BLOCK' if failures else 'PENDING' if pending or preview else 'PASS',
                confirmed_at=cutoff, preview=preview, contexts=contexts, checks=rows,
                reference_target=target, reference_rr_gross=rr,
                reference_rr_net=reward/loss if loss and loss > 0 else None,
                reasons=[r['id'] for r in failures])


def finalize(result, frames, cfg, cutoff, *, preview=False):
    if not cfg.context_audit.enabled:
        return result
    try:
        evidence = audit(result, frames, cfg, cutoff, preview=preview)
    except (ValueError, KeyError, TypeError, ArithmeticError):
        evidence = dict(version=VERSION, status='BLOCK', confirmed_at=cutoff, preview=preview,
                        reasons=['AUDIT_DATA_UNAVAILABLE'], checks=[check('audit:data', 'Dados da auditoria', None, '=', True)])
    result['risk_audit'] = evidence
    result['candidate_status'] = result['status']
    structural = [r for r in evidence['checks'] if r['state'] in ('FAIL', 'UNAVAILABLE') and r['id'] != 'audit:lh_break']
    if structural:
        result['status'] = 'NO_TRADE'
    elif evidence['status'] != 'PASS' and result['status'] == 'TRIGGERED':
        result['status'] = 'ARMED'
    result['vetoes'].extend('RISK_AUDIT:'+r['id'] for r in structural)
    if structural:
        result['score']['total'] = 0
    result['trigger_diagnostics']['checks'].extend(evidence['checks'])
    result['trigger_diagnostics']['status'] = result['status']
    return result


def publication(analysis, cfg, now):
    """Read-time eligibility. Frozen evidence is never an execution permission."""
    from datetime import datetime, timezone
    from .profile_runtime_config import canonical_hash
    evidence = analysis.get('risk_audit') or {}
    expected = canonical_hash(cfg.engine_dump())
    close = analysis.get('candle_close_at_utc')
    expires = (analysis.get('plan') or {}).get('expires_at')
    stamp = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    try:
        valid = bool(close and expires and stamp(close) <= now < stamp(expires)
                     and now-stamp(close) <= cfg.stale_tf_multiplier*DURATIONS[cfg.stage('trigger')])
    except (ValueError, TypeError, AttributeError, OverflowError):
        valid = False
    eligible = (cfg.enabled and cfg.context_audit.enabled and analysis.get('status') == 'TRIGGERED'
                and analysis.get('config_hash') == expected and valid
                and evidence.get('status') == 'PASS' and evidence.get('version') == VERSION
                and evidence.get('preview') is False)
    analysis['signal_contract'] = dict(
        signal_id=canonical_hash({k: analysis.get(k) for k in
                                 ('symbol', 'profile_id', 'config_hash', 'candle_close_at_utc')}),
        signal_status=analysis.get('status'), eligible=eligible, confirmed_at=close,
        expires_at=expires, config_hash=analysis.get('config_hash'),
        audit_version=evidence.get('version'), evaluated_at=datetime.fromtimestamp(now, timezone.utc).isoformat(),
        operational_effect=False)
    if evidence:
        evidence['config_hash'] = analysis.get('config_hash')
    return analysis

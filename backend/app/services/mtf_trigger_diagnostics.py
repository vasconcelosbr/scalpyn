"""MTF_TRIGGER_DIAGNOSTICS: explain observed predicates without changing policy."""
from operator import eq, ne, ge, gt, le, lt


OPERATORS = {'=': eq, '!=': ne, '>=': ge, '>': gt, '<=': le, '<': lt}


def check(key, label, actual, operator, expected, *, unit='', role='required',
          timeframe=None, enabled=True, waiting=None, note=None):
    state = ('DISABLED' if not enabled else 'WAITING' if waiting else
             'UNAVAILABLE' if actual is None or expected is None else
             'PASS' if OPERATORS[operator](actual, expected) else 'FAIL')
    gap = None
    if state == 'FAIL' and operator in ('>=', '>', '<=', '<'):
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            gap = max(0, expected-actual if operator in ('>=', '>') else actual-expected)
    return dict(id=key, label=label, actual=actual, operator=operator, expected=expected,
                unit=unit, role=role, timeframe=timeframe, state=state, gap=gap,
                note=waiting or note)


def pattern_conditions(c, previous, atr, cfg):
    """Single source for pattern recognition and explanations, including boundaries.

    Use the original products/comparisons rather than ratio rewrites to retain
    floating-point boundary behavior. Zero bodies are valid pin candidates.
    """
    amplitude = c['high']-c['low']
    body = abs(c['close']-c['open'])
    upper = c['high']-max(c['open'], c['close'])
    lower = min(c['open'], c['close'])-c['low']
    def p(key, label, value, op, target, note=None):
        return check(key, label, value, op, target, unit='price', timeframe=cfg.stage('trigger'), note=note)
    common = [p('atr', 'ATR disponível', atr, '>', 0),
              p('range', 'Amplitude positiva', amplitude, '>', 0),
              p('min_range', 'Amplitude mínima do candle', amplitude, '>=', cfg.min_range_atr*atr if atr else None,
                f'Amplitude exigida = ATR × {cfg.min_range_atr}.')]
    rules = {
        'DOJI': [p('body', 'Corpo máximo', body, '<=', amplitude*cfg.doji_body_pct)],
        'PIN_BULL': [
            p('wick_body', 'Pavio inferior em relação ao corpo', lower, '>=', cfg.pin_wick_ratio*body,
              f'Pavio exigido = corpo × {cfg.pin_wick_ratio}.'),
            p('wick_range', 'Pavio inferior em relação à amplitude', lower, '>=', cfg.pin_wick_pct*amplitude,
              f'Pavio exigido = amplitude × {cfg.pin_wick_pct}.'),
            p('close', 'Fechamento próximo da máxima', c['close'], '>=', c['high']-amplitude*cfg.pin_close_fraction)],
        'PIN_BEAR': [p('wick_body', 'Pavio superior / corpo', upper, '>=', cfg.pin_wick_ratio*body),
                     p('wick_range', 'Pavio superior / amplitude', upper, '>=', cfg.pin_wick_pct*amplitude),
                     p('close', 'Fechamento próximo da mínima', c['close'], '<=', c['low']+amplitude*cfg.pin_close_fraction)],
    }
    for name, op in [('MARUBOZU_BULL', '>'), ('MARUBOZU_BEAR', '<=')]:
        rules[name] = [p('direction', 'Fechamento em relação à abertura', c['close'], op, c['open']),
                       p('wicks', 'Soma máxima dos pavios', upper+lower, '<=', cfg.marubozu_wick_pct*amplitude,
                         f'Limite dos pavios = amplitude × {cfg.marubozu_wick_pct}.')]
    low, high = (c['low'], c['high']) if cfg.engulf_include_wicks else sorted([c['open'], c['close']])
    plow, phigh = ((previous['low'], previous['high']) if cfg.engulf_include_wicks else
                  sorted([previous['open'], previous['close']])) if previous else (None, None)
    for name, op, prev_op in [('ENGULF_BULL', '>', '<'), ('ENGULF_BEAR', '<', '>')]:
        rules[name] = [p('direction', 'Direção do candle atual', c['close'], op, c['open']),
                       p('previous_direction', 'Direção oposta do candle anterior',
                         previous['close'] if previous else None, prev_op, previous['open'] if previous else None),
                       p('engulf_low', 'Engolfo: limite inferior', low, '<=', plow),
                       p('engulf_high', 'Engolfo: limite superior', high, '>=', phigh)]
    rules['INSIDE_BAR'] = [p('low', 'Mínima acima da anterior', c['low'], '>', previous['low'] if previous else None),
                           p('high', 'Máxima abaixo da anterior', c['high'], '<', previous['high'] if previous else None)]
    rules['HARAMI'] = [p('body_low', 'Base do corpo acima da anterior', min(c['open'], c['close']), '>', min(previous['open'], previous['close']) if previous else None),
                       p('body_high', 'Topo do corpo abaixo do anterior', max(c['open'], c['close']), '<', max(previous['open'], previous['close']) if previous else None)]
    return {name: common+conditions for name, conditions in rules.items()}


def passed(conditions):
    return all(c['state'] == 'PASS' for c in conditions)


def build_diagnostics(result, frames, cfg, as_of, spread, btc_trend, *, ratio=None,
                      flags=None, plan_inputs=None):
    """Expose the exact evaluation's operands; never fetch or invent observations."""
    checks = []
    from ..schemas.mtf_rules import DURATIONS
    for tf in dict.fromkeys(cfg.stage(k) for k in ('structure','zone','trigger')):
        duration = DURATIONS[tf]
        frame = frames.get(tf) or {}
        candles = frame.get('candles') or []
        atr = frame.get('atr')
        checks.append(check('data:'+tf, 'Candles e ATR disponíveis', bool(candles and atr), '=', True, timeframe=tf))
        age = as_of-(candles[-1]['time']+duration) if candles else None
        checks.append(check('age:'+tf, 'Idade do candle fechado', age, '<=', cfg.stale_tf_multiplier*duration, unit='seconds', timeframe=tf))
    evaluated = flags is not None
    waiting_data = None if evaluated else 'Aguardando dados válidos para avaliar a estratégia.'
    zone = result.get('zone_1h')
    checks.append(check('zone', 'Proximidade do suporte', zone.get('distance_atr') if zone else None, '<=',
                        cfg.zone_proximity_atr, unit='ATR', timeframe=cfg.stage('zone'), waiting=waiting_data,
                        note=None if zone else 'Nenhuma zona de suporte confirmada disponível.'))
    trigger = result.get('trigger_5m')
    checks.append(check('trigger', 'Padrão de compra no candle fechado', bool(trigger) if evaluated else None, '=', True,
                        timeframe=cfg.stage('trigger'), waiting=waiting_data, note='Basta atender um dos padrões habilitados abaixo.'))
    pattern_details = []
    f5 = frames.get(cfg.stage('trigger')) or {}
    candles = f5.get('candles') or []
    if candles and f5.get('atr'):
        rules = pattern_conditions(candles[-1], candles[-2] if len(candles)>1 else None, f5['atr'], cfg)
        for name in cfg.trigger_patterns:
            conditions = rules[name]
            pattern_details.append(dict(pattern=name, state='PASS' if passed(conditions) else
                                        'UNAVAILABLE' if any(c['state']=='UNAVAILABLE' for c in conditions) else 'FAIL',
                                        conditions=conditions))
    direction = result['context_4h']['trend'] if evaluated else None
    for key, label, actual, forbidden, enabled, tf in [
        ('asset_downtrend', 'Tendência do ativo: veto de baixa', direction, 'DOWN', cfg.veto_filters.asset_downtrend, cfg.stage('structure')),
        ('asset_range', 'Tendência do ativo: veto de lateralidade', direction, 'RANGE', cfg.veto_filters.asset_range, cfg.stage('structure')),
        ('btc_downtrend', 'Tendência do Bitcoin', btc_trend, 'DOWN', cfg.veto_filters.btc_downtrend and result['symbol']!='BTC_USDT', cfg.btc_trend_timeframe),
    ]:
        checks.append(check(key, label, actual, '!=', forbidden, role='veto', timeframe=tf, enabled=enabled, waiting=waiting_data))
    checks.append(check('spread_available', 'Spread disponível', spread is not None, '=', True, waiting=waiting_data))
    checks.append(check('spread', 'Spread máximo', spread, '<=', cfg.max_spread_pct, role='veto', unit='%',
                        enabled=cfg.veto_filters.spread, waiting=waiting_data))
    pending_plan = waiting_data or ('Aguardando padrão de gatilho.' if not trigger else
                                   'Aguardando aproximação do suporte.' if not zone or not zone.get('in_zone') else None)
    if cfg.rules is not None:
        stages = result.get('stage_results', {})
        pending_plan = waiting_data or (None if stages.get('armed') and stages.get('trigger') else 'Aguardando regras JSON de ARMADO e GATILHO.')
    inputs = plan_inputs or {}
    checks.append(check('stop_valid', 'Stop positivo', inputs.get('stop'), '>', 0, unit='price', waiting=pending_plan))
    checks.append(check('stop_distance', 'Distância máxima do stop', inputs.get('stop_distance'), '<=',
                        cfg.max_stop_atr*f5['atr'] if f5.get('atr') else None, unit='price', role='veto',
                        enabled=cfg.veto_filters.stop_distance, waiting=pending_plan,
                        note=f'Limite = ATR × {cfg.max_stop_atr}.'))
    checks.append(check('targets', 'Alvos estruturais disponíveis', inputs.get('target_count'), '>=', 2, waiting=pending_plan))
    plan = result.get('plan')
    plan_wait = pending_plan or ('Aguardando alvos estruturais.' if inputs.get('target_count', 0)<2 else None)
    checks.append(check('risk_valid', 'Risco líquido positivo', inputs.get('loss'), '>', 0, unit='price', waiting=plan_wait))
    checks.append(check('risk_reward', 'Retorno/risco líquido mínimo', plan.get('rr_net') if plan else None, '>=',
                        cfg.min_rr_net, role='veto', unit='ratio', enabled=cfg.veto_filters.risk_reward, waiting=plan_wait))
    checks.append(check('plan', 'Plano disponível', bool(plan) if evaluated else None, '=', True, waiting=pending_plan))
    checks.append(check('score', 'Score mínimo', result['score']['total'] if evaluated else None, '>=', cfg.min_score,
                        unit='points', waiting=waiting_data, note='Vetos ativos zeram o score final.'))
    if evaluated:
        for key, label in [('trend', 'Estrutura de alta'), ('force', 'Candle de força'), ('zone', 'Proximidade do suporte'),
                           ('rejection', 'Rejeição no suporte'), ('trigger', 'Padrão de gatilho')]:
            row = check('score:'+key, label, flags[key], '=', True, role='score',
                        note='Contribui para o score; não constitui veto independente.')
            row.update(points=result['score']['components'][key], maximum_points=cfg.score_weights[key])
            checks.append(row)
        row = check('score:rr', 'Bônus de retorno/risco', plan.get('rr_net') if plan else None, '>=',
                    cfg.rr_bonus_threshold, role='score', unit='ratio', waiting=plan_wait,
                    note='Contribui para o score; não constitui veto independente.')
        row.update(points=result['score']['components']['rr'], maximum_points=cfg.score_weights['rr'])
        checks.append(row)
        row = check('score:volume', 'Volume relativo', ratio, '>=', cfg.vol_mult, unit='ratio', role='score',
                    timeframe=cfg.stage('trigger'), note=f'Volume do candle / média dos {cfg.vol_lookback} candles anteriores. Contribui para o score; não constitui veto independente.')
        row.update(points=result['score']['components']['volume'], maximum_points=cfg.score_weights['volume'])
        checks.append(row)
    return dict(version='mtf_trigger_diagnostics_v1', status=result['status'],
                candle_close_at_utc=result.get('candle_close_at_utc'), checks=checks, patterns=pattern_details)


def finalize_diagnostics(analysis, cfg, now, cutoff):
    """Service freshness checks run after the pure engine; reflect their final result."""
    from ..schemas.mtf_rules import DURATIONS
    diagnostic = analysis['trigger_diagnostics']
    diagnostic['status'] = analysis['status']
    diagnostic['observed_at_utc'] = analysis['observed_at_utc']
    diagnostic['config_hash'] = analysis['config_hash']
    diagnostic['checks'].append(check('evaluation_current', 'Atualidade da avaliação',
        now-cutoff, '<=', cfg.stale_tf_multiplier*DURATIONS[cfg.stage("trigger")], unit='seconds'))
    for row in diagnostic['checks']:
        if row['id'] == 'score' and row['state'] not in ('WAITING', 'UNAVAILABLE'):
            row.update(check('score', row['label'], analysis['score']['total'], '>=', cfg.min_score,
                             unit='points', note=row['note']))
        if row['id'].startswith('data:'):
            row['note'] = analysis.get('data_errors', {}).get(row['timeframe']) or row['note']

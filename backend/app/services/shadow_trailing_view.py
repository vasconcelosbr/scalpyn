"""Projection of frozen execution evidence. Never writes or evaluates an exit."""
from datetime import datetime, timezone, timedelta
from math import floor, isfinite
from sqlalchemy import text
from ..schemas.shadow_trailing_view import ShadowTrailingView, TrailingLevel, TrailingRegime
from .shadow_l3_exit_service import canonical_exchange
from .market_regime_engine import MarketRegimeEngine
from ..schemas.entry_risk_observation import DEFAULT_ENTRY_RISK_OBSERVATION


def number(value):
    try:
        n = float(value)
        return n if isfinite(n) else None
    except (ValueError, TypeError):
        return None


def instant(value):
    if not value:
        return None
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except ValueError:
        return None


def project(row, *, quote=None, decision=None, activation=None, now=None):
    now = now or datetime.now(timezone.utc)
    snapshot = row.config_snapshot or {}
    state = getattr(row, 'l3_exit', None) or {}
    policy = (snapshot.get('shadow_l3_exit_policy') or {}).get('config') or {}
    trailing = snapshot.get('trailing') or {}
    mode = policy.get('mode', 'LEGACY') if row.source == 'L3' else 'LEGACY'
    entry = number(row.entry_price)
    sign = -1 if str(row.direction).upper() == 'SHORT' else 1

    def level(price=None, at=None):
        p = number(price)
        return TrailingLevel(price=p, pct=sign*(p/entry-1)*100 if p is not None and entry and entry>0 else None, at=instant(at))

    v = ShadowTrailingView(state='UNAVAILABLE', mode=mode, entry=level(entry, row.entry_timestamp),
        policy_version=state.get('version'), last_evaluated_at=instant(state.get('last_evaluated_at')),
        quality=state.get('quality', 'UNAVAILABLE'))
    closed = row.status not in ('RUNNING', 'PENDING')
    max_age = number(policy.get('max_age_seconds'))
    if not closed and max_age and v.last_evaluated_at and (now-v.last_evaluated_at).total_seconds()>max_age:
        v.quality = 'STALE'
    if closed:
        v.state = 'CLOSED'
        v.observed = level(row.exit_price, row.exit_timestamp)
    elif quote and instant(quote['at']) and (not row.entry_timestamp or instant(quote['at']) >= instant(row.entry_timestamp)):
        v.observed = level(quote['close'], quote['at'])
    v.maximum = level(state.get('high_water_mark'), state.get('last_candle_at'))
    is_candidate = mode == 'OBSERVE'
    enabled = trailing.get('enabled') is True or mode == 'APPLY'
    if not closed:
        v.state = 'OBSERVATION' if is_candidate else 'WAITING' if enabled else 'DISABLED'

    # A schedule is the source of truth for when protection becomes actionable.
    schedule = state.get('floor_schedule') or []
    cutoff = v.last_evaluated_at if closed else now
    eligible = [f for f in schedule if instant(f.get('available_at')) and cutoff and instant(f['available_at']) <= cutoff]
    future = [f for f in schedule if instant(f.get('available_at')) and cutoff and instant(f['available_at']) > cutoff]
    floor_price = max((number(f.get('price')) for f in eligible if number(f.get('price')) is not None), default=None)
    floor_at = next((f['available_at'] for f in reversed(eligible) if number(f.get('price')) == floor_price), None)
    if not schedule and v.last_evaluated_at:
        floor_price, floor_at = number(state.get('floor_price')), v.last_evaluated_at
    # Never present an initial SL as an armed trailing, or an observation as applied.
    if floor_price is not None and number(row.sl_price) is not None and floor_price <= float(row.sl_price):
        floor_price = None
    if not is_candidate and mode == 'APPLY':
        v.floor = level(floor_price, floor_at)
        if future:
            f = max(future, key=lambda x: x['price'])
            v.pending_floor = level(f['price'], f['available_at'])
        if not closed and v.floor.price is not None:
            v.state = 'TIGHTENED' if state.get('state') == 'TIGHTENED' else 'ACTIVE'
        if not closed and (state.get('pending_exit') or state.get('outcome')):
            v.state = 'EXIT_PENDING'
    elif enabled and not is_candidate and not closed:
        # Legacy monitor has no persisted current floor; do not infer one from MFE.
        v.state = 'UNAVAILABLE'

    v.origin = 'POST_TP' if state.get('continuation') else 'PRE_TP' if v.floor.price is not None else None
    if activation:
        # Recorded TP trigger is a reference, not an exact intrabar transaction.
        v.activation = level(row.tp_price, activation['candle_at'])
    if entry and v.floor.pct is not None and v.observed.pct is not None:
        v.distance_to_floor_pp = v.observed.pct - v.floor.pct

    trigger_price = number(row.tp_price) if mode in ('APPLY', 'OBSERVE') else None
    if not state.get('continuation') and v.floor.price is None and trailing.get('enabled'):
        config = trailing.get('policy') or trailing
        pct = number(config.get('activation_profit_pct'))
        if pct is not None and entry:
            pre_trigger = entry*(1+sign*pct/100)
            trigger_price = min(pre_trigger,trigger_price) if trigger_price is not None else pre_trigger
    v.trigger = level(trigger_price)
    if v.state in ('WAITING', 'OBSERVATION', 'UNAVAILABLE'):
        if v.trigger.pct is not None and v.observed.pct is not None:
            v.remaining_pp = max(0, v.trigger.pct-v.observed.pct)
        reached = v.remaining_pp == 0 or (v.maximum.pct is not None and v.trigger.pct is not None and v.maximum.pct >= v.trigger.pct)
        v.pending_reason = ('LEGACY_STATE_NOT_RECORDED' if v.state == 'UNAVAILABLE' else
            'WAITING_EVALUATION' if not v.last_evaluated_at else
            'DATA_UNAVAILABLE' if reached and v.quality != 'VALID' else
            'WAITING_FLOW_PRICE_CONFIRMATION' if reached else 'WAITING_TRIGGER')
    else:
        v.pending_reason = state.get('reason')
    if not closed and max_age and v.observed.at and (now-v.observed.at).total_seconds()>max_age:
        v.remaining_pp = None
        if v.state in ('WAITING','OBSERVATION','UNAVAILABLE'):
            v.pending_reason = 'STALE_QUOTE'
    if state.get('continuation') and entry and mode in ('APPLY','OBSERVE'):
        tp_pct = level(row.tp_price).pct
        step = number(policy.get('step_trigger_pct'))
        increment = number(policy.get('step_floor_pct'))
        buffer = number(policy.get('initial_buffer_pct'))
        if tp_pct is not None and step and increment and buffer is not None and v.maximum.pct is not None:
            next_n = max(0, floor((v.maximum.pct-tp_pct)/step))+1
            v.next_step = level(entry*(1+(tp_pct+next_n*step)/100))
            v.next_step_floor = level(max(v.floor.price or 0,entry*(1+(tp_pct-buffer+next_n*increment)/100)))
    return v


def regime_label(original):
    return {'BULL':'Bullish','BULLISH':'Bullish','TRENDING_BULL':'Bullish',
        'BEAR':'Bearish','BEARISH':'Bearish','TRENDING_BEAR':'Bearish',
        'SIDEWAYS':'Neutral','NEUTRAL':'Neutral'}.get(str(original).upper())


def asset_regime(raw, row, now):
    if not raw:
        return TrailingRegime()
    values, timestamps = {}, []
    tf = DEFAULT_ENTRY_RISK_OBSERVATION['source_timeframe']
    for name, env in raw.items():
        if not isinstance(env,dict) or env.get('status') != 'VALID' or env.get('candle_closed') is not True:
            continue
        if env.get('timeframe') != tf or env.get('market_type') != 'spot' or canonical_exchange(env.get('source_provider')) != canonical_exchange(row.exchange) or not env.get('source_provider'):
            continue
        if env.get('config_user_id') and str(env['config_user_id']) != str(row.user_id):
            continue
        at, available = instant(env.get('source_timestamp')), instant(env.get('available_at'))
        if not at or not available or available>now:
            continue
        val = number(env.get('value'))
        if val is not None:
            values[name] = val
            timestamps.append(at)
    at = min(timestamps) if timestamps else None
    result = TrailingRegime(source='indicators / '+canonical_exchange(row.exchange),timeframe=tf,at=at)
    # Require the classifier's direction and trend inputs; missing data isn't sideways.
    directional = all(k in values for k in ('ema20','ema50','ema200')) or ('rsi' in values and ('macd' in values or 'macd_value' in values))
    if 'adx' not in values or not directional:
        result.quality = 'INCOMPLETE'
        return result
    signal = MarketRegimeEngine().detect_asset_regime(row.symbol,values)
    result.original = signal.regime.value
    result.quality = 'STALE' if (now-at).total_seconds()>DEFAULT_ENTRY_RISK_OBSERVATION['source_stale_seconds'] else 'VALID'
    result.label = regime_label(result.original) if result.quality == 'VALID' else None
    return result


async def attach_trailing_views(db, rows, user_id):
    rows = [r for r in rows if r.user_id == user_id]
    if not rows:
        return
    now = datetime.now(timezone.utc)
    ids = [r.id for r in rows]
    # All lateral lookups are bounded by the authorized page, never all history.
    data = (await db.execute(text("""
        SELECT s.id,q.close,q.time AS quote_at,q.timeframe,
               a.candle_at AS activation_at,i.indicators_json
        FROM shadow_trades s
        LEFT JOIN LATERAL (
          SELECT close,time,timeframe FROM ohlcv
          WHERE symbol=s.symbol AND exchange=CASE WHEN lower(s.exchange) IN ('gate','gateio','gate.io') THEN 'gate.io' ELSE lower(s.exchange) END
            AND market_type='spot' AND timeframe='1m' AND is_closed IS TRUE
            AND time<date_trunc('minute',now()) AND ingested_at<=now()
          ORDER BY time DESC LIMIT 1
        ) q ON true
        LEFT JOIN LATERAL (
          SELECT candle_at FROM shadow_l3_exit_decisions WHERE shadow_id=s.id
            AND state->>'continuation'='true' ORDER BY candle_at LIMIT 1
        ) a ON true
        LEFT JOIN LATERAL (
          SELECT indicators_json FROM indicators WHERE symbol=s.symbol AND market_type='spot'
            AND timeframe=:tf AND time<=now() ORDER BY time DESC LIMIT 1
        ) i ON true
        WHERE s.user_id=:uid AND s.id=ANY(:ids)
    """),{'uid':user_id,'ids':ids,'tf':DEFAULT_ENTRY_RISK_OBSERVATION['source_timeframe']})).mappings().all()
    macro = (await db.execute(text("""SELECT regime,detected_at FROM regime_history
        WHERE source='macro' AND detected_at<=now() ORDER BY detected_at DESC LIMIT 1"""))).mappings().first()
    market = TrailingRegime(source='macro')
    if macro:
        from ..tasks.macro_regime_update import _REDIS_TTL
        market = TrailingRegime(original=macro['regime'],at=macro['detected_at'],source='macro',
            quality='VALID' if (now-macro['detected_at']).total_seconds()<=_REDIS_TTL else 'STALE')
        market.label = regime_label(market.original) if market.quality=='VALID' else None
    indexed = {d['id']:d for d in data}
    for row in rows:
        d = indexed.get(row.id) or {}
        quote = {'close':d['close'],'at':d['quote_at']+timedelta(minutes=1)} if d.get('quote_at') else None
        v = project(row,quote=quote,activation={'candle_at':d['activation_at']} if d.get('activation_at') else None,now=now)
        v.asset_regime = asset_regime(d.get('indicators_json'),row,now)
        v.market_regime = market
        row.trailing_view = v

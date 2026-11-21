"""MTF_STRUCTURE_READ_ONLY: project existing data, never evaluate or persist decisions."""
from math import floor, isfinite
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import text
from ..schemas.mtf_structure import StructureContext
from .mtf_chart_engine import TF_SECONDS, trend, timestamp, iso
from .mtf_structure_indicators import STRUCTURE_EMA_NAME


def positive(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and value > 0 else None


def structure_context(data, cfg, *, source='chart', now=None):
    analysis = data.get('analysis') or {}
    try:
        at = timestamp(data['as_of'])
        cutoff = timestamp(analysis['evaluated_at_utc'])
    except (KeyError, TypeError, ValueError):
        return None
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    expires = min(at, cutoff) + cfg.stale_tf_multiplier * TF_SECONDS['5m']
    if now >= expires or at > now or cutoff > at:
        return None
    result = dict(source=source, as_of=iso(at), evaluated_at=iso(cutoff), valid_until=iso(expires),
                  config_hash=analysis['config_hash'], profile_id=analysis.get('profile_id'), frames={})
    for tf, duration in TF_SECONDS.items():
        frame = data.get('frames', {}).get(tf, {})
        closed = [c for c in frame.get('candles', []) if c.get('closed') is True and c['time'] + duration <= cutoff]
        last = closed[-1] if closed else None
        if not last or frame.get('error') or at - (last['time'] + duration) > cfg.stale_tf_multiplier * duration:
            result['frames'][tf] = {}
            continue
        points = [dict(p) for p in frame.get('pivots', []) if p.get('confirmed_at', cutoff + 1) <= cutoff and positive(p.get('price'))]
        # Reuse the existing classifier. Initial H/L alone cannot establish structure.
        complete = any(p['label'] in ('HH', 'LH') for p in points) and any(p['label'] in ('HL', 'LL') for p in points)
        direction = (analysis.get('context_4h') or {}).get('trend') if tf == '4h' else trend(points) if complete else None
        atr = positive(frame.get('atr'))
        ema = next((positive(p.get('value')) for p in frame.get('indicators', {}).get(STRUCTURE_EMA_NAME, []) if p['time'] == last['time']), None)
        close = positive(last.get('close'))
        live = next((dict(c) for c in reversed(frame.get('candles', [])) if c.get('closed') is False and c['time'] <= at < c['time'] + duration), None)
        change = ((live['close'] / live['open']) - 1) * 100 if live and positive(live.get('open')) and positive(live.get('close')) else None
        result['frames'][tf] = dict(trend=direction, pivots=points,
            last_hl=next((p for p in reversed(points) if p['label'] == 'HL'), None),
            last_lh=next((p for p in reversed(points) if p['label'] == 'LH'), None),
            last_pivot_age_candles=floor((at - points[-1]['confirmed_at']) / duration) if points else None,
            atr=atr, ema50=ema, closed_close=close, closed_at=iso(last['time'] + duration),
            extension_atr=(close - ema) / atr if close and ema and atr else None,
            live_candle=live, live_change_pct=change)
    recent = data.get('frames', {}).get('5m', {}).get('candles', [])
    latest = recent[-1] if recent else None
    price = positive(latest.get('close')) if latest and latest['time'] <= at and at - (latest['time'] + TF_SECONDS['5m']) <= cfg.stale_tf_multiplier * TF_SECONDS['5m'] else None
    h4 = result['frames']['4h']
    direction = h4.get('trend')
    pivot = h4.get('last_hl') if direction == 'UP' else h4.get('last_lh') if direction == 'DOWN' else None
    result.update(current_price=price, current_price_source='latest_5m_candle_close' if price else None,
                  invalidation=pivot, invalidation_distance_atr=(price - pivot['price']) / h4['atr'] if price and pivot and h4.get('atr') else None)
    conflicts = []
    complete = direction is not None
    capital = (analysis.get('pump_flow') or {}).get('capital') or {}
    for tf in ('1h', '5m'):
        lower = result['frames'][tf].get('trend')
        complete = complete and lower is not None
        if direction and lower and lower != direction:
            conflicts.append(dict(kind='structure', timeframe=tf, state=lower,
                sequence=[p['label'] for p in result['frames'][tf]['pivots']]))
        flow = capital.get(tf) or {}
        if cfg.pump_flow and cfg.pump_flow.enabled:
            complete = complete and flow.get('state') in ('inflow', 'outflow', 'neutral')
        if (direction == 'UP' and flow.get('state') == 'outflow') or (direction == 'DOWN' and flow.get('state') == 'inflow'):
            conflicts.append(dict(kind='flow', timeframe=tf, state=flow['state'], ratio=flow.get('ratio')))
    result.update(conflicts=conflicts, conflict=True if conflicts else False if complete else None)
    return StructureContext.model_validate(result).model_dump()


async def enrich_watchlist(db, uid, wid, items, cfg):
    """Read exact already-validated observation IDs; do not touch the shared L3 loader."""
    ids = []
    for item in items:
        if item.get('mtf'):
            try:
                ids.append(UUID((item.get('mtf_observation') or {}).get('observation_id', '')))
            except (ValueError, TypeError):
                pass
    if not ids:
        return [{**i, 'structure_context': None} for i in items]
    rows = (await db.execute(text('''SELECT id,payload FROM mtf_history_observations
        WHERE user_id=:uid AND watchlist_id=:wid AND id=ANY(CAST(:ids AS uuid[]))'''),
        {'uid':uid, 'wid':wid, 'ids':ids})).mappings().all()
    payloads = {str(r['id']):r['payload'] for r in rows}
    enriched = []
    for item in items:
        snapshot = payloads.get((item.get('mtf_observation') or {}).get('observation_id'))
        context = None
        if snapshot and snapshot.get('analysis') == item.get('mtf'):
            frames = {}
            for tf, frame in snapshot.get('frames', {}).items():
                candle = frame.get('last_closed_candle')
                indicators = {p['name']:[{'time':timestamp(p['candle_open_at']), 'value':p['value']}] for p in snapshot.get('indicators', [])
                              if p['timeframe'] == tf and p.get('quality') == 'current' and p.get('candle_open_at') and p.get('value') is not None}
                frames[tf] = {**frame, 'candles':[{**candle, 'closed':True}] if candle else [], 'indicators':indicators}
            context = structure_context({'analysis':snapshot['analysis'], 'as_of':snapshot['observed_at'], 'frames':frames}, cfg, source='observation')
        enriched.append({**item, 'structure_context':context})
    return enriched

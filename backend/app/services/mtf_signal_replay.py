"""Signal studies, independent of trade admission. Never submits orders or Shadows."""
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import math
import logging

from sqlalchemy import text
from .mtf_history import utc, serial, snapshot
from .profile_runtime_config import canonical_hash
from ..schemas.mtf_history import HistoryConfig

log = logging.getLogger(__name__)


def context_key(uid, wid, symbol, profile):
    return canonical_hash([str(uid), wid, symbol, str(profile or '')])


def activation(previous, status, candle_at):
    """Only forward closed evaluations can change the activation latch."""
    if previous and candle_at <= previous['candle_at']:
        return False, False
    return True, status == 'TRIGGERED' and (not previous or previous['status'] != 'TRIGGERED')


def barrier(candle, baseline, target, stop):
    """Closed one-minute evidence: do not invent intraminute chronology."""
    opened = float(candle['open'])
    # At the opening print, chronology is known even if both barriers occur later.
    if opened >= target:
        status, price, precision = 'TARGET', opened, 'open'
    elif stop is not None and opened <= stop:
        status, price, precision = 'STOP', opened, 'open'
    else:
        up = float(candle['high']) >= target
        down = stop is not None and float(candle['low']) <= stop
        if up and down:
            return dict(status='INDETERMINATE', return_pct=None,
                        reason='BOTH_BARRIERS_IN_ONE_MINUTE', precision='1m')
        if not up and not down:
            return None
        status, price, precision = ('TARGET', target, '1m') if up else ('STOP', stop, '1m')
    return dict(status=status, price=price, return_pct=(price / baseline - 1) * 100,
                precision=precision, formula='(price / trigger_close - 1) * 100', costs_included=False)


async def record_data(db, uid, wid, data, cfg):
    from .config_service import config_service
    hc = HistoryConfig.model_validate(await config_service.get_config(db, 'mtf_history', uid) or {})
    payload = snapshot(data, {'symbol': data['analysis']['symbol'],
                             'profile_id': data['analysis'].get('profile_id')}, cfg, hc,
                       data['analysis'].get('observed_at_utc'))
    await record(db, uid, wid, payload, hc)


async def record(db, uid, wid, payload, hc, *, reconstruct=False):
    a = payload['analysis']
    at = utc(a.get('candle_close_at_utc'))
    if not at or at > datetime.now(timezone.utc) or not a.get('candle_closed_5m'):
        return
    # Missing/stale data are not evidence that a signal exited its state.
    if any(str(v).startswith(('DATA_', 'EVALUATION_STALE', 'CONFIG_CHANGED')) for v in a.get('vetoes', [])):
        return
    symbol, profile = a['symbol'], a.get('profile_id')
    key = context_key(uid, wid, symbol, profile)
    await db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key': 'replay:' + key})
    if not reconstruct:
        active = (await db.execute(text('''SELECT r.id FROM mtf_signal_replays r
            JOIN mtf_replay_progress p ON p.replay_id=r.id WHERE r.context_id=:key AND p.status='OPEN' '''),
            {'key': key})).scalars().all()
        for rid in active:
            await store_payload(db, rid, payload)
    previous = (await db.execute(text('SELECT * FROM mtf_replay_contexts WHERE identity=:key'), {'key': key})).mappings().first()
    if previous is None and not reconstruct:
        # Bootstrap only from previously frozen evidence, never today's indicators.
        history = (await db.execute(text('''WITH ordered AS (SELECT payload,status,observed_at,id,
            lag(status) OVER (ORDER BY observed_at,id) AS previous_status FROM mtf_history_observations
            WHERE user_id=:uid AND watchlist_id=:wid AND symbol=:symbol
            AND profile_id IS NOT DISTINCT FROM CAST(:profile AS text)
            AND observed_at<:at AND payload->'replay_inputs' IS NOT NULL)
            SELECT CASE WHEN status='TRIGGERED' THEN payload ELSE
                jsonb_build_object('analysis',payload->'analysis','observed_at',payload->'observed_at',
                    'candle_close_price',payload->'candle_close_price','replay_inputs',true) END AS payload
            FROM ordered WHERE status!='TRIGGERED' OR previous_status IS DISTINCT FROM status
            ORDER BY observed_at,id'''),
            dict(uid=uid,wid=wid,symbol=symbol,profile=str(profile) if profile else None,at=utc(payload['observed_at'])))).scalars().all()
        for historical in history:
            if historical.get('replay_inputs') and historical.get('candle_close_price'):
                await record(db, uid, wid, historical, hc, reconstruct=True)
        previous = (await db.execute(text('SELECT * FROM mtf_replay_contexts WHERE identity=:key'), {'key': key})).mappings().first()
    advance, new = activation(previous, a['status'], at)
    if not advance:
        return
    armed = list(previous['armed']) if previous else []
    if a['status'] == 'ARMED':
        if previous and previous['status'] == 'TRIGGERED':
            armed = []
        armed.append(dict(time=at.isoformat(), price=payload.get('candle_close_price'),
                          captured_at=payload['observed_at'], analysis=a))
    elif a['status'] not in ('TRIGGERED', 'ARMED'):
        armed = []
    await db.execute(text('''INSERT INTO mtf_replay_contexts(identity,user_id,watchlist_id,symbol,profile_id,candle_at,status,armed)
        VALUES(:key,:uid,:wid,:symbol,:profile,:at,:status,CAST(:armed AS jsonb))
        ON CONFLICT(identity) DO UPDATE SET candle_at=excluded.candle_at,status=excluded.status,armed=excluded.armed'''),
        dict(key=key, uid=uid, wid=wid, symbol=symbol, profile=str(profile) if profile else None,
             at=at, status=a['status'], armed=serial(armed)))
    if not new:
        return
    base = payload.get('candle_close_price')
    if not isinstance(base, (float, int)) or not math.isfinite(base) or base <= 0:
        raise ValueError('REPLAY_TRIGGER_CLOSE_MISSING')  # rollback the latch; retry can capture
    stop = (a.get('plan') or {}).get('stop')
    stop = float(stop) if stop is not None and math.isfinite(float(stop)) and 0 < float(stop) < base else None
    rid = uuid4()
    payload = dict(payload, replay_target_pct=hc.replay_target_pct,
                   capture_kind='reconstructed_from_frozen_observations' if reconstruct else 'prospective',
                   activation_boundary='observed_transition' if previous else 'first_observation',
                   operational_effect=False)
    await db.execute(text('''INSERT INTO mtf_signal_replays
        (id,context_id,user_id,watchlist_id,symbol,profile_id,triggered_at,captured_at,baseline,target,stop,snapshot,armed)
        VALUES(:id,:key,:uid,:wid,:symbol,:profile,:at,:captured,:base,:target,:stop,CAST(:payload AS jsonb),CAST(:armed AS jsonb))'''),
        dict(id=rid, key=key, uid=uid, wid=wid, symbol=symbol, profile=str(profile) if profile else None,
             at=at, captured=utc(payload['observed_at']), base=base, target=base*(1+hc.replay_target_pct/100),
             stop=stop, payload=serial(payload), armed=serial(armed)))
    await db.execute(text('INSERT INTO mtf_replay_progress(replay_id,next_at,issue) VALUES(:id,:at,:issue)'),
                     dict(id=rid, at=at, issue='STOP_UNAVAILABLE' if stop is None else None))


async def store_followup(db, row, data):
    from ..schemas.mtf_chart import MTFChartConfig
    cfg = MTFChartConfig.model_validate(row['snapshot']['engine_config'])
    hc = HistoryConfig.model_validate(row['snapshot']['history_config'])
    p = snapshot(data, row['snapshot']['membership'], cfg, hc, datetime.now(timezone.utc))
    await store_payload(db, row['id'], p)


async def store_payload(db, rid, p):
    # Identity describes the set of closed candles, not the wall clock or ticker.
    identity = canonical_hash(dict(frames={tf: f.get('last_closed_candle') for tf, f in p['frames'].items()},
                                   producers=sorted({i.get('producer', '') for i in p['indicators']})))
    await db.execute(text('''INSERT INTO mtf_replay_snapshots(replay_id,identity,captured_at,payload)
        VALUES(:id,:key,:at,CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING'''),
        dict(id=rid, key=identity, at=utc(p['observed_at']), payload=serial(p)))


async def advance_one(db, row):
    import httpx
    from .research_ohlcv_service import fetch_gate_closed_candles
    now = datetime.now(timezone.utc)
    start = row['next_at']
    end = min(now.replace(second=0, microsecond=0), start + timedelta(minutes=1000))
    if end <= start:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        batch = await fetch_gate_closed_candles(client, symbol=row['symbol'], timeframe='1m',
            points=int((end-start).total_seconds()/60), to_timestamp=int(end.timestamp())-60)
    candles = {utc(c['time']): c for c in batch.records}
    issue = 'STOP_UNAVAILABLE' if row['stop'] is None else None
    result = None
    while start < end:
        candle = candles.get(start)
        if candle is None:
            issue = 'CANDLE_GAP'
            break
        evidence = dict(candle, resolution='1m', received_at=batch.observed_at.isoformat())
        await db.execute(text('''INSERT INTO mtf_replay_candles(replay_id,time,payload)
            VALUES(:id,:time,CAST(:payload AS jsonb)) ON CONFLICT DO NOTHING'''),
            dict(id=row['id'], time=start, payload=serial(evidence)))
        result = barrier(candle, row['baseline'], row['target'], row['stop'])
        start += timedelta(minutes=1)
        if result:
            result.update(candle_open_at=utc(candle['time']).isoformat(), candle_close_at=start.isoformat(),
                          detected_at=now.isoformat(), source='gate.io', evidence=evidence)
            break
    await db.execute(text('''UPDATE mtf_replay_progress SET status=:status,next_at=:at,checked_at=:now,
        result=CAST(:result AS jsonb),issue=:issue WHERE replay_id=:id'''),
        dict(id=row['id'], status=result['status'] if result else 'OPEN', at=start, now=now,
             result=serial(result), issue=issue))


async def advance_all(db, batch_size=50):
    from .mtf_chart_service import chart_data
    from ..schemas.mtf_chart import MTFChartConfig
    ids = (await db.execute(text('''SELECT replay_id FROM mtf_replay_progress WHERE status='OPEN'
        ORDER BY checked_at NULLS FIRST,next_at LIMIT :limit'''), {'limit': batch_size})).scalars().all()
    for rid in ids:
        row = (await db.execute(text('''SELECT r.*,p.next_at FROM mtf_signal_replays r
            JOIN mtf_replay_progress p ON p.replay_id=r.id WHERE r.id=:id AND p.status='OPEN'
            FOR UPDATE OF p SKIP LOCKED'''), {'id': rid})).mappings().first()
        if not row:
            continue
        try:
            async with db.begin_nested():
                await advance_one(db, row)
            # Capture actual available closed indicators, not a reconstruction at an old clock.
            async with db.begin_nested():
                cfg = MTFChartConfig.model_validate(row['snapshot']['engine_config'])
                data = await chart_data(db, row['user_id'], row['symbol'], row['profile_id'], cfg, include_display=False)
                await store_followup(db, row, data)
        except Exception:
            log.exception('MTF replay advance failed id=%s', row['id'])
            await db.execute(text("UPDATE mtf_replay_progress SET checked_at=now(),issue='CAPTURE_ERROR' WHERE replay_id=:id"), {'id': row['id']})
        await db.commit()
    return len(ids)

"""Observation-only evidence. No execution or Shadow admission dependencies."""
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4
import json
import math
from sqlalchemy import select, text
from ..schemas.mtf_history import HistoryConfig, HistoryFilter
from .profile_runtime_config import canonical_hash
from . import mtf_chart_service as chart
from .config_service import config_service
from .mtf_structure_indicators import enrich_observer_frames


def serial(value):
    return json.dumps(value, default=str, allow_nan=False)


def utc(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def history_members(db, uid, wid):
    """Full display universe; never use the execution-authorized capped feed."""
    from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
    from .profile_display_status import load_profile_display_rows
    if wid == 'l3-consolidated':
        from .l3_profile_consolidation import load_l3_profile_consolidation
        result = await load_l3_profile_consolidation(db, user_id=uid)
        return result['items'] + result['awaiting_data_items']
    wl = await db.scalar(select(PipelineWatchlist).where(
        PipelineWatchlist.id == UUID(wid), PipelineWatchlist.user_id == uid,
        PipelineWatchlist.market_mode == 'spot'))
    if wl is None:
        raise ValueError('WATCHLIST_NOT_FOUND')
    if wl.level == 'L3':
        return await load_profile_display_rows(db, user_id=uid, watchlist_id=wl.id)
    rows = (await db.execute(select(PipelineWatchlistAsset).where(
        PipelineWatchlistAsset.watchlist_id == wl.id))).scalars().all()
    return [dict(symbol=r.symbol, profile_id=str(wl.profile_id) if wl.profile_id else None,
                 current_price=r.current_price, refreshed_at=r.refreshed_at) for r in rows]


def snapshot(data, member, cfg, history_cfg, scheduled_at):
    from .mtf_replay import freeze_inputs
    analysis = data['analysis']
    observed = utc(data.get('captured_at') or analysis['observed_at_utc'])
    indicators, frames = [], {}
    reasons = list(analysis.get('data_errors', {}).values())
    reasons += [v for v in analysis.get('vetoes', []) if v.startswith(('DATA_', 'BTC_CONTEXT_UNAVAILABLE', 'RULE_DATA_UNAVAILABLE'))]
    for tf, frame in data['frames'].items():
        candles = frame.get('candles', [])
        frames[tf] = {k: v for k,v in frame.items() if k not in ('candles','indicators')}
        frames[tf]['last_closed_candle'] = candles[-1] if candles else None
        for name, series in frame.get('indicators', {}).items():
            display = frame.get('display_only_indicators', {}).get(name)
            point = series[-1] if series else None
            source_at = utc(point['time']) + timedelta(seconds=chart.TF_SECONDS[tf]) if point else None
            age = (observed-source_at).total_seconds() if source_at else None
            indicators.append(dict(name=name, timeframe=tf, value=point.get('value') if point else None,
                candle_open_at=utc(point['time']).isoformat() if point else None,
                source_at=source_at.isoformat() if source_at else None, age_seconds=age,
                quality='missing' if point is None else 'invalid_time' if age < 0 else 'stale' if age > cfg.stale_tf_multiplier*chart.TF_SECONDS[tf] else 'current',
                source='gate.io', producer='mtf_chart', role='structural_display_only' if display else 'available_to_evaluation',
                configuration=display['configuration'] if display else data.get('indicator_config'),
                configuration_identity={'version': display['version'], 'sample_count': display['sample_count']}
                    if display else analysis.get('indicator_identity')))
    if analysis.get('unavailable_profile_indicators'):
        reasons.append('PROFILE_INDICATORS_UNAVAILABLE')
    if not data.get('quote'):
        reasons.append('QUOTE_UNAVAILABLE')
    last = frames.get(cfg.stage('trigger'), {}).get('last_closed_candle')
    quote = data.get('quote') or {}
    price_time = utc(quote.get('received_at'))
    price = quote.get('price')
    if price is not None:
        price = float(price)
        if not math.isfinite(price):
            price = None
    return dict(schema_version='mtf_history_v1', operational_effect=False, observation_only=True,
        scheduled_at=utc(scheduled_at).isoformat(), observed_at=observed.isoformat(),
        capture_delay_seconds=(observed-utc(scheduled_at)).total_seconds(),
        analysis=analysis, profile_config=data.get('profile_config'), engine_config=cfg.model_dump(),
        history_config=history_cfg.model_dump(), membership=json.loads(serial(member)),
        indicators=indicators, frames=frames, market_context=data.get('market_context'),
        replay_inputs=freeze_inputs(data, cfg) if analysis.get('evaluated_at_utc') else None,
        observed_price=price, price_source_at=None,
        price_received_at=price_time.isoformat() if price_time else None,
        price_source='gate.io/ticker', price_timestamp_kind='local_response_receipt; exchange_event_timestamp_unavailable',
        price_age_seconds=(observed-price_time).total_seconds() if price_time else None,
        price_quality='unknown' if price_time is None else 'stale' if (observed-price_time).total_seconds() > cfg.stale_tf_multiplier*chart.TF_SECONDS[cfg.stage("trigger")] else 'current',
        candle_close_price=last.get('close') if last else None,
        candle_close_at=analysis.get('candle_close_at_utc'),
        quality='pending' if reasons else 'current', data_reasons=reasons,
        completeness='prospective_snapshot', future_results_separate=True)


async def capture(db, uid, wid, member, scheduled_at):
    uid = UUID(str(uid))
    hc = HistoryConfig.model_validate(await config_service.get_config(db, 'mtf_history', uid) or {})
    cfg = await chart.load_config(db, uid)
    if not cfg.enabled or wid not in cfg.monitored_watchlists:
        return False
    identity = canonical_hash(dict(user=str(uid), watchlist=wid, symbol=member['symbol'],
                                  profile=str(member.get('profile_id')), scheduled_at=scheduled_at))
    # Transaction lock and uniqueness protect retries and overlapping workers.
    if not await db.scalar(text('SELECT pg_try_advisory_xact_lock(hashtext(:k))'), {'k': 'mtf-history:'+identity}):
        return False
    if await db.scalar(text('SELECT id FROM mtf_history_observations WHERE identity=:key'), {'key': identity}):
        return False
    data = await chart.chart_data(db, uid, member['symbol'], member.get('profile_id'), cfg, include_display=False)
    # Display enrichment is outside chart_data: scores, gates, plans and their
    # identities are already finalized, including unavailable profile inputs.
    if cfg.schema_version != 'mtf_chart_v3':
        enrich_observer_frames(data)
    # Fresh public read, no cache and no credentials. Gate ticker has no event
    # timestamp: receipt time is recorded under its own name, never fabricated.
    import httpx
    from .market_data_service import GATE_TICKERS_URL, market_data_service
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(GATE_TICKERS_URL,params={'currency_pair':market_data_service.to_gate_symbol(member['symbol'])})
            response.raise_for_status()
            tickers = response.json()
            price = float(tickers[0]['last'])
            if not math.isfinite(price) or price <= 0:
                raise ValueError('INVALID_PRICE')
            data['quote'] = {'price':price,'received_at':datetime.now(timezone.utc).isoformat()}
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):
        data['quote'] = None
    data['captured_at'] = datetime.now(timezone.utc).isoformat()
    payload = snapshot(data, member, cfg, hc, scheduled_at)
    observed = utc(payload['observed_at'])
    # Preserve entire producer envelopes, including their original provenance.
    # These are explicitly context-only, never claimed as consumed by MTF.
    rows = (await db.execute(text('''SELECT DISTINCT ON (timeframe,scheduler_group)
        time,timeframe,scheduler_group,indicators_json FROM indicators
        WHERE symbol=:symbol AND market_type='spot' AND time<=:at
        ORDER BY timeframe,scheduler_group,time DESC'''), {'symbol': member['symbol'], 'at': observed})).mappings().all()
    payload['producer_snapshots'] = [dict(row) for row in rows]
    from ..utils.indicator_payload import expand_indicator_payload
    for row in rows:
        for name,envelope in expand_indicator_payload(row['indicators_json']).items():
            if name.startswith('_'):
                continue
            envelope = envelope if isinstance(envelope,dict) else {'value':envelope}
            source_at = envelope.get('source_timestamp') or envelope.get('candle_close_time')
            age = None
            try:
                age = (observed-utc(source_at)).total_seconds() if source_at else None
            except (ValueError,TypeError):
                pass
            ttl = cfg.stale_tf_multiplier*chart.TF_SECONDS[row['timeframe']] if row['timeframe'] in chart.TF_SECONDS else None
            quality = ('missing' if envelope.get('value') is None else 'unknown' if age is None or ttl is None
                       else 'invalid_time' if age < 0 else 'stale' if age > ttl else 'current')
            payload['indicators'].append(dict(name=name,timeframe=row['timeframe'],
                producer=row['scheduler_group'],source_at=source_at,computed_at=row['time'].isoformat(),
                role='context_only',quality=quality,age_seconds=age,producer_status=envelope.get('status'),value=envelope.get('value'),
                metadata=envelope))
    from .mtf_signal_replay import record
    await record(db, uid, wid, payload, hc)
    if not hc.enabled and not cfg.context_audit.enabled:
        return True
    oid = uuid4()
    await db.execute(text('''INSERT INTO mtf_history_observations
        (id,user_id,watchlist_id,symbol,profile_id,identity,observed_at,status,trend,quality,payload)
        VALUES(:id,:uid,:wid,:symbol,:profile,:identity,:at,:status,:trend,:quality,CAST(:payload AS jsonb))
        ON CONFLICT(identity) DO NOTHING'''), dict(id=oid, uid=uid, wid=wid, symbol=member['symbol'],
        profile=str(member['profile_id']) if member.get('profile_id') else None, identity=identity, at=observed,
        status=data['analysis']['status'], trend=data['analysis'].get('context_4h', {}).get('trend'),
        quality=payload['quality'], payload=serial(payload)))
    for horizon in hc.horizons_minutes:
        await db.execute(text('''INSERT INTO mtf_history_outcomes(observation_id,horizon_minutes,due_at)
            VALUES(:id,:h,:due) ON CONFLICT DO NOTHING'''),
            {'id': oid, 'h': horizon, 'due': observed+timedelta(minutes=horizon)})
    await db.commit()
    return True


def outcome(payload, horizon, candles, now):
    """Closed Gate candles after observation only; incomplete coverage has no label."""
    observed = utc(payload['observed_at'])
    due = observed + timedelta(minutes=horizon)
    if now < due:
        return {'status': 'PENDING'}
    start = math.ceil(observed.timestamp()/300)*300
    end = math.ceil(due.timestamp()/300)*300
    by_time = {int(utc(c['time']).timestamp()): c for c in candles
               if c.get('is_closed') is True and c.get('exchange') in ('gate.io','gate')
               and c.get('ingested_at') is not None and utc(c['time'])+timedelta(seconds=300) <= now}
    expected = list(range(start, end, 300))
    present = [by_time[t] for t in expected if t in by_time]
    quote_base = payload.get('observed_price') if payload.get('price_quality') == 'current' else None
    base = quote_base or payload.get('candle_close_price')
    base_at = payload.get('price_received_at') if quote_base else payload.get('candle_close_at')
    result = dict(status='INCOMPLETE', target_at=due.isoformat(), expected_candles=len(expected),
                  available_candles=len(present), source='gate.io', resolution='5m',
                  coverage_kind='full_closed_candles_only', excluded_initial_seconds=start-observed.timestamp(),
                  endpoint_delay_seconds=end-due.timestamp(),
                  baseline_price=base, baseline_at=base_at,
                  baseline_kind='observed_quote' if quote_base else 'last_closed_candle', return_pct=None, max_return_pct=None, min_return_pct=None)
    if not expected or len(present) != len(expected):
        result['reason'] = 'POST_OBSERVATION_CANDLE_GAP'
        return result
    if not base or base <= 0 or any(c.get(k) is None for c in present for k in ('close','high','low')):
        result['reason'] = 'PRICE_UNAVAILABLE'
        return result
    if not base_at or (not quote_base and (observed-utc(base_at)).total_seconds() > payload['engine_config']['stale_tf_multiplier']*300):
        result['reason'] = 'BASELINE_DATA_PENDING'
        return result
    price = float(present[-1]['close'])
    high, low = max(float(c['high']) for c in present), min(float(c['low']) for c in present)
    result.update(status='COMPLETE', price=price, high=high, low=low,
        price_at=utc(end).isoformat(), coverage_start=utc(start).isoformat(), coverage_end=utc(end).isoformat(),
        return_pct=(price/base-1)*100, max_return_pct=(high/base-1)*100, min_return_pct=(low/base-1)*100,
        formula='(future_price / baseline_price - 1) * 100; descriptive, no execution or costs')
    return result


FILTER_SQL = '''o.user_id=:uid
    AND (CAST(:wid AS text) IS NULL OR o.watchlist_id=:wid) AND (CAST(:symbol AS text) IS NULL OR o.symbol=:symbol)
    AND (CAST(:profile AS text) IS NULL OR o.profile_id=:profile) AND (CAST(:status AS text) IS NULL OR o.status=:status)
    AND (CAST(:trend AS text) IS NULL OR o.trend=:trend) AND (CAST(:quality AS text) IS NULL OR o.quality=:quality)
    AND (CAST(:start AS timestamptz) IS NULL OR o.observed_at>=:start)
    AND (CAST(:end AS timestamptz) IS NULL OR o.observed_at<=:end)'''


def filter_params(uid, filters):
    return dict(uid=uid, wid=filters.watchlist_id, symbol=filters.symbol, profile=filters.profile_id,
                status=filters.status, trend=filters.trend, quality=filters.quality, start=filters.start, end=filters.end)


async def list_observations(db, uid, filters, before=None, limit=50):
    params = filter_params(uid, filters) | {'limit':limit+1, 'before_at':None,'before_id':None}
    if before:
        at, oid = before.split('|')
        params.update(before_at=utc(at), before_id=UUID(oid))
    rows = (await db.execute(text('''SELECT o.id,o.watchlist_id,o.symbol,o.profile_id,o.observed_at,o.status,o.trend,o.quality,
        o.payload->'observed_price' AS observed_price, o.payload->'candle_close_price' AS candle_close_price,
        o.payload->'analysis'->'score' AS score, o.payload->'price_quality' AS price_quality
        FROM mtf_history_observations o WHERE '''+FILTER_SQL+'''
        AND (CAST(:before_at AS timestamptz) IS NULL OR (o.observed_at,o.id)<(:before_at,CAST(:before_id AS uuid)))
        ORDER BY o.observed_at DESC,o.id DESC LIMIT :limit'''),params)).mappings().all()
    items = [dict(r) for r in rows[:limit]]
    return {'items':items, 'next_cursor':f"{items[-1]['observed_at'].isoformat()}|{items[-1]['id']}" if len(rows)>limit else None,
            'operational_effect':False, 'legacy_note':'Avaliações anteriores à captura completa permanecem no histórico legado, com dados parciais.'}


async def detail(db, uid, oid):
    row = (await db.execute(text('SELECT * FROM mtf_history_observations WHERE user_id=:uid AND id=:id'),
                           {'uid':uid,'id':oid})).mappings().first()
    if row is None:
        return None
    results = (await db.execute(text('SELECT horizon_minutes,due_at,status,payload FROM mtf_history_outcomes WHERE observation_id=:id ORDER BY horizon_minutes'), {'id':oid})).mappings().all()
    next_row = (await db.execute(text('''SELECT id,status,observed_at FROM mtf_history_observations
        WHERE user_id=:uid AND watchlist_id=:wid AND symbol=:symbol
        AND profile_id IS NOT DISTINCT FROM :profile AND observed_at>:at AND status<>:status
        ORDER BY observed_at,id LIMIT 1'''), dict(uid=uid,wid=row['watchlist_id'],symbol=row['symbol'],
        profile=row['profile_id'],at=row['observed_at'],status=row['status']))).mappings().first()
    return dict(row) | {'outcomes':[dict(r) for r in results],
        'next_status_change': (dict(next_row) | {'seconds':(next_row['observed_at']-row['observed_at']).total_seconds()}) if next_row else None}


async def advance_outcomes(db, batch=100):
    rows = (await db.execute(text('''SELECT r.observation_id,r.horizon_minutes,o.symbol,o.payload
        FROM mtf_history_outcomes r JOIN mtf_history_observations o ON o.id=r.observation_id
        WHERE r.status='PENDING' AND r.due_at<=now()-interval '5 minutes'
        ORDER BY r.due_at LIMIT :batch FOR UPDATE OF r SKIP LOCKED'''), {'batch':batch})).mappings().all()
    for row in rows:
        at = utc(row['payload']['observed_at'])
        end = at+timedelta(minutes=row['horizon_minutes']+5)
        candles = (await db.execute(text('''SELECT time,close,high,low,is_closed,exchange,ingested_at FROM ohlcv
            WHERE symbol=:symbol AND market_type='spot' AND timeframe='5m'
            AND exchange IN ('gate.io','gate') AND is_closed IS TRUE AND time>=:start AND time<=:end
            ORDER BY time'''), {'symbol':row['symbol'],'start':at,'end':end})).mappings().all()
        result = outcome(row['payload'],row['horizon_minutes'],candles,datetime.now(timezone.utc))
        if result.get('reason') == 'POST_OBSERVATION_CANDLE_GAP':
            # Removed assets may no longer be collected by the live pipeline.
            # Continue labels with public historical Gate reads, without mutating
            # canonical candles, inputs, watchlists or execution authorization.
            try:
                candles = await future_candles(row['symbol'],at,row['horizon_minutes'])
                result = outcome(row['payload'],row['horizon_minutes'],candles,datetime.now(timezone.utc))
                result['read_path'] = 'gate_historical_public_read'
                result['retrieved_at'] = datetime.now(timezone.utc).isoformat()
            except Exception as exc:
                result['read_error'] = type(exc).__name__
        await db.execute(text('''UPDATE mtf_history_outcomes SET status=:status,payload=CAST(:payload AS jsonb),updated_at=clock_timestamp()
            WHERE observation_id=:id AND horizon_minutes=:h'''),
            {'status':result['status'],'payload':serial(result),'id':row['observation_id'],'h':row['horizon_minutes']})
    await db.commit()
    return len(rows)


async def future_candles(symbol, observed, horizon):
    import httpx
    from .research_ohlcv_service import fetch_gate_closed_candles
    start = math.ceil(observed.timestamp()/300)*300
    end = math.ceil((observed+timedelta(minutes=horizon)).timestamp()/300)*300
    records = []
    async with httpx.AsyncClient(timeout=8) as client:
        while start < end:
            points = min(1000,(end-start)//300)
            batch = await fetch_gate_closed_candles(client,symbol=symbol,timeframe='5m',
                points=points,to_timestamp=start+(points-1)*300)
            records.extend(dict(c,is_closed=True,ingested_at=batch.observed_at) for c in batch.records)
            start += points*300
    return records

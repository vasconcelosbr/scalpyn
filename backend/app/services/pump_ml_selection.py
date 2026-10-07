"""Pump-only bounded payload reads; retain newest eligible decision ordering.

Backend copy of ``pump_ml/selection.py`` (2026-10-07) so the daily trainer can run
in the Celery worker image, which only ships ``backend/``. Since 2026-10-07 this copy
is the source of truth (optional context projection, configurable lookback); the
Railway ``pump_ml`` service is superseded by the Celery runner."""
from datetime import timedelta
from app.services.pump_opportunity_engine import number,utc

IDS_SQL = """SELECT observation_id FROM pump_opportunity_observations
 WHERE user_id=$1 AND ($2::timestamptz IS NULL OR decision_at >= $2)
 ORDER BY decision_at DESC,observation_id"""
CONTRACT_SQL = """SELECT payload->'manifest' FROM pump_opportunity_observations
 WHERE user_id=$1 AND observation_id=ANY($2::uuid[])
 AND payload->'manifest'->>'listing_certified'='true'
 ORDER BY decision_at DESC,observation_id LIMIT 1"""
ROWS_SQL = """SELECT jsonb_build_object('observation_id',o.observation_id,'decision_at',o.decision_at,
 'episode_id',o.episode_id,'instrument_id',o.instrument_id,'manifest',o.payload->'manifest',
 'label_spec',o.payload->'label_spec',
 'values',(SELECT jsonb_object_agg(k,v) FROM jsonb_each(o.payload->'values') AS f(k,v) WHERE k=ANY($2::text[])),
 'simulation',o.payload->'simulation','target',l.payload->'targets'->'0.8'->'hit',
 'label_status',l.payload->>'status','label_coverage_complete',l.payload->'coverage_complete') AS row
 FROM pump_opportunity_observations o
 CROSS JOIN LATERAL (SELECT payload FROM pump_opportunity_labels
   WHERE observation_id=o.observation_id AND horizon_minutes=5
   AND label_spec_hash=$5 LIMIT 1) l
 WHERE o.user_id=$1 AND o.observation_id=ANY($6::uuid[])
 AND o.payload->'manifest'->>'listing_certified'='true'
 AND l.payload->>'coverage_complete'='true'
 AND l.payload->>'status'='known'
 AND l.payload->'targets'->'0.8'->>'hit' IN('true','false')
 AND o.payload->'manifest'->>'feature_spec_hash'=$3
 AND o.payload->'manifest'->>'legacy_config_hash'=$4
 AND o.payload->'manifest'->>'label_spec_hash'=$5
 ORDER BY o.decision_at DESC,o.observation_id LIMIT $7"""


async def select_training_rows(conn,owner,features,feature_hash,max_rows,diagnostics,*,batch_size=500):
    """One consistent snapshot. Batch size changes I/O only, never eligibility.

    Iterate all candidates needed to find max_rows eligible observations, rather
    than taking max_rows raw observations first. Unknown labels remain excluded;
    feature completeness and independent temporal gates remain in the trainer.
    """
    if max_rows<=0 or batch_size<=0:raise ValueError('Invalid Pump selection read bound')
    rows=[];contract=None
    diagnostics.update(phase='latest_contract',candidate_rows=0,batches=0)
    async with conn.transaction(isolation='repeatable_read',readonly=True):
        cutoff=await conn.fetchval('SELECT now()')
        cursor=await conn.cursor(IDS_SQL,owner,None)
        while True:
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            contract=await conn.fetchval(CONTRACT_SQL,owner,ids)
            if contract:break
        if not contract:return None,[]
        diagnostics['phase']='dataset_candidate_ids'
        cursor=await conn.cursor(IDS_SQL,owner,cutoff-timedelta(days=30))
        while len(rows)<max_rows:
            diagnostics['phase']='dataset_candidate_ids'
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            diagnostics['candidate_rows']+=len(ids);diagnostics['batches']+=1
            diagnostics['phase']='dataset_rows'
            selected=await conn.fetch(ROWS_SQL,owner,features,feature_hash,
                contract['legacy_config_hash'],contract['label_spec_hash'],ids,max_rows-len(rows))
            rows.extend(r['row'] for r in selected)
        diagnostics.update(phase='selection_complete',selected_rows=len(rows),selection_as_of=cutoff.isoformat())
    return contract,rows


BOUNDED_IDS_SQL = """SELECT observation_id FROM pump_opportunity_observations
 WHERE user_id=$1 AND decision_at >= $2
 AND ($3::timestamptz IS NULL OR decision_at < $3 OR ($4 AND decision_at=$3))
 ORDER BY decision_at DESC,observation_id"""
EARLIEST_IDS_SQL = BOUNDED_IDS_SQL.replace('decision_at DESC','decision_at ASC')
TEMPORAL_SELECTION_VERSION='pump_temporal_equal_duration_v1'

# Direction is endpoint return relative to the captured reference, never touch.
DIRECTIONAL_ROWS_SQL = ROWS_SQL.replace(
    "'simulation',o.payload->'simulation','target',l.payload->'targets'->'0.8'->'hit',",
    "'simulation',o.payload->'simulation',"
    "'reference',o.payload->'reference','endpoint_return_pct',l.payload->'endpoint_return_pct','horizon_minutes',$8::int,"
    "'slot_at',o.slot_at,'symbol',o.symbol,"
    "'target',(l.payload->>'endpoint_return_pct')::numeric>0,")
DIRECTIONAL_ROWS_SQL = DIRECTIONAL_ROWS_SQL.replace('horizon_minutes=5', 'horizon_minutes=$8 AND labeled_at<=$9')
DIRECTIONAL_ROWS_SQL = DIRECTIONAL_ROWS_SQL.replace(
    "AND l.payload->'targets'->'0.8'->>'hit' IN('true','false')",
    "AND jsonb_typeof(l.payload->'endpoint_return_pct')='number' "
    "AND (l.payload->>'endpoint_return_pct')::numeric<>0 AND o.published_at<=$9 "
    "AND jsonb_typeof(o.payload->'reference'->'price')='number' "
    "AND (o.payload->'reference'->>'price')::numeric>0 "
    "AND o.payload->'reference'->>'policy'='gate_best_ask_v1'")

# Relative objective benchmark (2026-10-07). For each minute of the selected rows:
#   * cross-section = MID-referenced endpoint return of EVERY labelled asset captured in
#     that minute (certified or not: it is the market), same horizon/label spec,
#     labelled by the snapshot cutoff. The stored label is referenced to the best ASK,
#     which embeds ~half the spread as a loss growing with illiquidity;
#     mid = ask / (1 + spread/200) removes it.
#   * beta_i = OLS slope of asset 5m returns on the universe-median 5m return over the
#     ``window`` closed candles BEFORE the decision (ohlcv, Gate preferred). Without it
#     the relative label is mostly beta x market direction (Gate 5m, 02-07/10: median
#     |P(beat | mkt down) - P(beat | mkt up)| = 0.553 raw vs 0.111 beta-adjusted).
#   * residual e_j = r_j - beta_j * median(r); benchmark for row i = beta_i * median(r)
#     + median(e), so (own - benchmark) = e_i - median(e).
CROSS_SECTION_SQL = """SELECT o.slot_at, o.symbol,
 ((1+(l.payload->>'endpoint_return_pct')::float8/100)*(1+(o.payload->'values'->>'spread_pct')::float8/200)-1)*100 AS r
 FROM pump_opportunity_observations o
 JOIN pump_opportunity_labels l ON l.observation_id=o.observation_id
  AND l.horizon_minutes=$3 AND l.label_spec_hash=$4 AND l.labeled_at<=$5
 WHERE o.user_id=$1 AND o.slot_at=ANY($2::timestamptz[])
  AND l.payload->>'status'='known' AND l.payload->>'coverage_complete'='true'
  AND jsonb_typeof(l.payload->'endpoint_return_pct')='number'
  AND jsonb_typeof(o.payload->'values'->'spread_pct')='number'"""
CANDLES_SQL = """SELECT DISTINCT ON (symbol, time) symbol, time, close::float8 AS close FROM ohlcv
 WHERE symbol=ANY($1::text[]) AND timeframe=$2 AND market_type='spot' AND is_closed IS TRUE
  AND time>=$3 AND time<$4
 ORDER BY symbol, time, CASE WHEN exchange ILIKE 'gate%' THEN 0 ELSE 1 END"""
_TF_SECONDS={'1m':60,'5m':300,'15m':900}


def _median(values):
    v=sorted(values);n=len(v)
    return None if not n else (v[n//2] if n%2 else (v[n//2-1]+v[n//2])/2)


def rolling_betas(closes,needed,*,step_seconds,window,min_points):
    """``closes``: {symbol: {open_epoch: close}}; ``needed``: {(symbol, decision_epoch)}.
    Beta from candles CLOSED at or before the decision (open + step <= decision), over
    the previous ``window`` candles. Prefix sums make each lookup O(1)."""
    import numpy as np
    grid=sorted({t for series in closes.values() for t in series})
    if len(grid)<2:return {}
    index={t:i for i,t in enumerate(grid)}
    ret={}
    for sym,series in closes.items():
        y=np.full(len(grid),np.nan)
        for t,c in series.items():
            prev=series.get(t-step_seconds)
            if prev and prev>0 and c and c>0:y[index[t]]=(c/prev-1)*100
        ret[sym]=y
    mat=np.vstack(list(ret.values()))
    with np.errstate(all='ignore'):
        mkt=np.nanmedian(mat,axis=0)
    out={}
    for sym,y in ret.items():
        ok=~np.isnan(y)&~np.isnan(mkt)
        x=np.where(ok,mkt,0.0);yy=np.where(ok,y,0.0)
        cs=lambda a:np.concatenate([[0.0],np.cumsum(a)])
        n,sx,sy,sxx,sxy=cs(ok.astype(float)),cs(x),cs(yy),cs(x*x),cs(x*yy)
        for (s,dec) in needed:
            if s!=sym:continue
            # last candle closed by the decision: open <= dec - step
            hi=int(np.searchsorted(grid,dec-step_seconds,side='right'))
            lo=max(0,hi-window)
            cnt=n[hi]-n[lo]
            if cnt<min_points:continue
            vx=(sxx[hi]-sxx[lo])-(sx[hi]-sx[lo])**2/cnt
            if vx<=0:continue
            out[(s,dec)]=float(((sxy[hi]-sxy[lo])-(sx[hi]-sx[lo])*(sy[hi]-sy[lo])/cnt)/vx)
    return out


async def attach_universe_benchmark(conn,owner,rows,horizon_minutes,label_hash,cutoff,diagnostics,*,
                                    beta=None,chunk=500):
    """Adds ``benchmark_return_pct`` / ``benchmark_assets`` / ``beta`` to each row
    (None/0 when the minute or the asset's beta is unavailable). Runs inside the
    caller's snapshot. ``beta=None`` → plain same-minute median (no beta)."""
    slots=sorted({utc(r['slot_at']) for r in rows if r.get('slot_at')})
    cross={}
    diagnostics['phase']='universe_cross_section'
    for i in range(0,len(slots),chunk):
        for rec in await conn.fetch(CROSS_SECTION_SQL,owner,slots[i:i+chunk],horizon_minutes,label_hash,cutoff):
            if rec['r'] is not None:cross.setdefault(utc(rec['slot_at']),[]).append((rec['symbol'],float(rec['r'])))
    betas={}
    if beta and cross:
        diagnostics['phase']='universe_betas'
        step=_TF_SECONDS[beta['timeframe']];window=int(beta['window_candles'])
        symbols=sorted({s for xs in cross.values() for s,_ in xs})
        lo=min(cross)-timedelta(seconds=step*(window+2));hi=max(cross)+timedelta(seconds=step)
        closes={}
        for rec in await conn.fetch(CANDLES_SQL,symbols,beta['timeframe'],lo,hi):
            closes.setdefault(rec['symbol'],{})[int(utc(rec['time']).timestamp())]=rec['close']
        needed={(s,int(t.timestamp())) for t,xs in cross.items() for s,_ in xs}
        betas=rolling_betas(closes,needed,step_seconds=step,window=window,min_points=int(beta['min_points']))
    bench={}
    for t,xs in cross.items():
        m=_median([r for _,r in xs])
        if beta:
            b={s:betas.get((s,int(t.timestamp()))) for s,_ in xs}
            resid=[r-b[s]*m for s,r in xs if b[s] is not None]
        else:
            b={s:0.0 for s,_ in xs};resid=[r for _,r in xs]
        if resid:bench[t]=(m,_median(resid),len(resid),b)
    for r in rows:
        t=utc(r['slot_at']) if r.get('slot_at') else None
        hit=bench.get(t)
        bi=hit[3].get(r.get('symbol')) if hit else None
        if hit and bi is not None:
            r['benchmark_return_pct'],r['benchmark_assets'],r['beta']=bi*hit[0]+hit[1],hit[2],bi
        else:
            r['benchmark_return_pct'],r['benchmark_assets'],r['beta']=None,0,None
    sizes=[v[2] for v in bench.values()]
    diagnostics['benchmark']={'policy':'universe_beta_residual_median_mid_v1' if beta else 'universe_median_same_slot_mid_v1',
        'slots':len(slots),'slots_with_benchmark':len(bench),'betas':len(betas),
        'rows_with_benchmark':sum(r['benchmark_assets']>0 for r in rows),
        'min_assets':min(sizes) if sizes else None,'median_assets':sorted(sizes)[len(sizes)//2] if sizes else None,
        'beta':beta}
    return rows


def complete_features(row,features):
    return all(number((row['values'] or {}).get(f)) for f in features)

def temporal_windows(first,last,max_rows,bins):
    """Equal elapsed-time quotas; neither class values nor scores select cuts."""
    if max_rows<=0 or bins<=0:raise ValueError('Invalid Pump temporal selection bound')
    first,last=utc(first),utc(last)
    if last<first:raise ValueError('Invalid Pump selection extent')
    count=1 if first==last else min(bins,max_rows)
    quota,remainder=divmod(max_rows,count)
    return [(first+(last-first)*i/count,first+(last-first)*(i+1)/count,
        quota+(i<remainder),i==count-1) for i in range(count)]

async def select_temporal_training_rows(conn,owner,features,feature_hash,max_rows,diagnostics,*,batch_size=500,bins=20,horizon_minutes=None,
                                        extra_features=(),lookback_days=30,benchmark=False,beta=None):
    """Sample at most max_rows across the complete-feature compatible extent.

    Empty/underfilled windows remain explicit, never filled with newest-only
    observations or label-aware picks. Original trainer gates still apply.
    All endpoint and bucket reads share the same snapshot and timeout settings.
    """
    if max_rows<=0 or batch_size<=0 or bins<=0:raise ValueError('Invalid Pump temporal selection bound')
    if horizon_minutes is not None and (type(horizon_minutes) is not int or horizon_minutes<=0):
        raise ValueError('Invalid directional horizon')
    diagnostics.update(phase='latest_contract',candidate_rows=0,endpoint_candidate_rows=0,batches=0)
    async with conn.transaction(isolation='repeatable_read',readonly=True):
        cutoff=await conn.fetchval('SELECT now()')
        cursor=await conn.cursor(IDS_SQL,owner,None);contract=None
        while True:
            ids=[r['observation_id'] for r in await cursor.fetch(batch_size)]
            if not ids:break
            contract=await conn.fetchval(CONTRACT_SQL,owner,ids)
            if contract:break
        if not contract:return None,[]
        since=cutoff-timedelta(days=lookback_days)
        # Optional context keys are projected but never required (missing → NaN in the trainer).
        projection=list(features)+[f for f in extra_features if f not in features]
        async def projected(ids,limit):
            args=(owner,projection,feature_hash,contract['legacy_config_hash'],contract['label_spec_hash'],ids,limit)
            sql=ROWS_SQL
            if horizon_minutes is not None:
                sql=DIRECTIONAL_ROWS_SQL;args=(*args,horizon_minutes,cutoff)
            return [r['row'] for r in await conn.fetch(sql,*args)]
        async def endpoint(ascending):
            diagnostics['phase']='earliest_eligible' if ascending else 'latest_eligible'
            cur=await conn.cursor(EARLIEST_IDS_SQL if ascending else BOUNDED_IDS_SQL,owner,since,None,False)
            while True:
                ids=[r['observation_id'] for r in await cur.fetch(batch_size)]
                if not ids:return None
                diagnostics['endpoint_candidate_rows']+=len(ids)
                valid=[r for r in await projected(ids,len(ids)) if complete_features(r,features)]
                if valid:
                    times=[utc(r['decision_at']) for r in valid]
                    return min(times) if ascending else max(times)
        first=await endpoint(True)
        if first is None:
            diagnostics.update(phase='selection_complete',selected_rows=0)
            return contract,[]
        last=await endpoint(False)
        windows=temporal_windows(first,last,max_rows,bins);rows=[];window_evidence=[]
        for lo,hi,quota,inclusive in windows:
            diagnostics['phase']='temporal_candidate_ids'
            cur=await conn.cursor(BOUNDED_IDS_SQL,owner,lo,hi,inclusive)
            selected=[];candidate_count=0
            while len(selected)<quota:
                diagnostics['phase']='temporal_candidate_ids'
                ids=[r['observation_id'] for r in await cur.fetch(batch_size)]
                if not ids:break
                candidate_count+=len(ids);diagnostics['candidate_rows']+=len(ids);diagnostics['batches']+=1
                diagnostics['phase']='temporal_rows'
                # Do not apply the remaining quota before feature validation:
                # invalid numeric features must not displace older eligible rows.
                valid=[r for r in await projected(ids,len(ids)) if complete_features(r,features)]
                selected.extend(valid[:quota-len(selected)])
            rows.extend(selected)
            window_evidence.append({'from':lo.isoformat(),'to':hi.isoformat(),'last_inclusive':inclusive,
                'quota':quota,'selected':len(selected),'candidate_rows':candidate_count})
        if benchmark and horizon_minutes is not None and rows:
            await attach_universe_benchmark(conn,owner,rows,horizon_minutes,contract['label_spec_hash'],cutoff,diagnostics,
                                            beta=beta)
        rows.sort(key=lambda r:(-utc(r['decision_at']).timestamp(),r['observation_id']))
        diagnostics.update(phase='selection_complete',selected_rows=len(rows),selection_as_of=cutoff.isoformat(),
            policy={'version':TEMPORAL_SELECTION_VERSION,'requested_bins':bins,'actual_bins':len(windows),
                'max_rows':max_rows,'first':first.isoformat(),'last':last.isoformat(),'lookback_days':lookback_days,
                'optional_context_features':len(projection)-len(features),
                'feature_eligibility_before_sampling':True,'outcome_balancing':False,
                'objective':'endpoint_direction_v1' if horizon_minutes is not None else 'legacy_touch',
                'horizon_minutes':horizon_minutes,
                'empty_window_policy':'leave_underfilled_no_newest_backfill','windows':window_evidence})
    return contract,rows

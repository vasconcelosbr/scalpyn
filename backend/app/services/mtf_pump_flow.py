"""MTF_PUMP_FLOW_SHADOW: frozen context, no score, veto, plan or order mutation."""
from datetime import datetime, timedelta, timezone
import json
import logging
from math import isfinite
from sqlalchemy import text
from .profile_runtime_config import canonical_hash

log = logging.getLogger(__name__)
from ..schemas.mtf_rules import DURATIONS
WINDOWS = {tf: seconds // 60 for tf, seconds in DURATIONS.items()}

CAPITAL_SQL = '''SELECT minute, buy_usdt, sell_usdt, symbols, complete_symbols, computed_at
    FROM pump_capital_flow_1m WHERE user_id=:uid AND minute>=:lo AND minute<:cutoff
    AND computed_at<=:known_at ORDER BY minute'''
ASSET_SQL = '''SELECT bucket_start AS minute, buy_quote AS buy_usdt, sell_quote AS sell_usdt,
    partial, computed_at FROM flow_buckets_1m
    WHERE symbol=:symbol AND bucket_start>=:lo AND bucket_start<:cutoff
    AND computed_at<=:known_at ORDER BY bucket_start'''


def utc(value):
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('SOURCE_TIMEZONE_REQUIRED')
    return dt.astimezone(timezone.utc)


def numeric(value):
    return value is not None and isfinite(float(value)) and float(value) >= 0


def summarize(rows, minutes, cutoff, known_at, min_coverage, *, capital=False):
    """Unique complete closed minutes, fixed denominator; never extrapolate gaps."""
    lo = cutoff-timedelta(minutes=minutes)
    selected = {}
    for row in rows:
        minute = utc(row['minute'])
        if not lo <= minute < cutoff or minute.second or minute.microsecond or utc(row['computed_at']) > known_at:
            continue
        if minute in selected:
            return {'state': 'unavailable', 'reason': 'duplicate_minute'}
        selected[minute] = row
    usable = [r for r in selected.values() if numeric(r['buy_usdt']) and numeric(r['sell_usdt'])
              and (int(r.get('symbols') or 0) > 0 and 0 <= int(r.get('complete_symbols') or 0) <= int(r['symbols'])
                   if capital else not r.get('partial'))]
    time_coverage = len(usable)/minutes
    symbol_coverage = (sum(int(r.get('complete_symbols') or 0) for r in usable)/sum(int(r['symbols']) for r in usable)) if capital and usable else 1.0
    coverage = min(time_coverage, symbol_coverage)
    buy = sum(float(r['buy_usdt']) for r in usable)
    sell = sum(float(r['sell_usdt']) for r in usable)
    gross = buy+sell
    result = {'window_minutes': minutes, 'window_start': lo.isoformat(), 'window_end': cutoff.isoformat(),
              'coverage': coverage, 'time_coverage': time_coverage, 'symbol_coverage': symbol_coverage,
              'usable_minutes': len(usable), 'expected_minutes': minutes,
              'buy_usdt': buy, 'sell_usdt': sell, 'net_usdt': buy-sell, 'gross_usdt': gross,
              'ratio': (buy-sell)/gross if gross else None,
              'buy_persistence': sum(float(r['buy_usdt']) > float(r['sell_usdt']) for r in usable)/len(usable) if usable else None,
              'latest_minute': max((utc(r['minute']) for r in usable), default=None)}
    # Missing the trailing minute is different from a complete, quiet minute.
    trailing_ready = result['latest_minute'] == cutoff-timedelta(minutes=1)
    result['trailing_minute_ready'] = trailing_ready
    result['state'] = 'available' if coverage >= min_coverage and gross > 0 and trailing_ready else 'unavailable'
    # Distinct causes: coverage below the minimum (data gap) vs. enough coverage but the
    # last closed minute not published yet (read before the Pump cycle consolidated it).
    result['reason'] = (None if result['state'] == 'available'
                        else 'incomplete_window' if coverage < min_coverage
                        else 'delayed_window' if not trailing_ready
                        else 'no_trades')
    result['latest_minute'] = result['latest_minute'].isoformat() if result['latest_minute'] else None
    return result


def build_context(capital_rows, asset_rows, *, symbol, monitored_symbols, cutoff, known_at, cfg):
    config = cfg.identity_dump()
    out = {'version': 'mtf_pump_flow_v1', 'config_hash': canonical_hash(config), 'config': config,
           'captured_at': known_at.isoformat(), 'cutoff': cutoff.isoformat(), 'symbol': symbol,
           'mode': 'SHADOW', 'operational_effect': False, 'capital': {}, 'asset': {},
           'confirmation': 'unavailable', 'reasons': [], 'universe': 'historical_pump_universe_per_minute'}
    if not cfg.enabled:
        return {**out, 'confirmation': 'disabled'}
    if not 0 <= (known_at-cutoff).total_seconds() <= cfg.max_evaluation_delay_seconds:
        return {**out, 'reasons': ['evaluation_outside_flow_freshness']}
    for tf in cfg.capital_neutral_ratio:
        minutes = WINDOWS[tf]
        reading = summarize(capital_rows, minutes, cutoff, known_at, cfg.min_coverage, capital=True)
        if reading['state'] == 'available':
            threshold = cfg.capital_neutral_ratio[tf]
            reading['state'] = 'inflow' if reading['ratio'] >= threshold else 'outflow' if reading['ratio'] <= -threshold else 'neutral'
        out['capital'][tf] = reading
    if symbol not in monitored_symbols:
        out['asset'] = {'state': 'unavailable', 'reason': 'asset_not_in_pump_universe'}
    else:
        out['asset'] = summarize(asset_rows, WINDOWS[cfg.asset_window], cutoff, known_at, cfg.min_coverage)
    asset = out['asset']
    if asset['state'] == 'available':
        asset['state'] = ('confirmed' if asset['ratio'] >= cfg.asset_min_delta and asset['buy_persistence'] >= cfg.asset_min_buy_persistence
                          else 'divergent' if asset['ratio'] <= -cfg.asset_min_delta else 'neutral')
    unavailable = asset['state'] == 'unavailable' or any(out['capital'][tf]['state'] == 'unavailable' for tf,required in cfg.require_capital.items() if required)
    capital_ok = all(out['capital'][tf]['state'] in ('neutral', 'inflow') for tf,required in cfg.require_capital.items() if required)
    out['confirmation'] = 'unavailable' if unavailable else 'confirmed' if asset['state'] == 'confirmed' and capital_ok else 'not_confirmed'
    if asset['state'] != 'confirmed':
        out['reasons'].append('asset_'+asset['state'])
    out['reasons'] += ['capital_'+tf+'_'+out['capital'][tf]['state'] for tf, required in cfg.require_capital.items()
                       if required and out['capital'][tf]['state'] not in ('neutral','inflow')]
    return out


async def read_rows(db, user_id, symbol, cutoff, known_at, cfg):
    """Capital and asset minutes visible at ``known_at``, under the configured timeout."""
    previous_timeout = await db.scalar(text('SHOW statement_timeout'))
    async with db.begin_nested():
        await db.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {'timeout': str(cfg.query_timeout_ms)})
        capitals = (await db.execute(text(CAPITAL_SQL), {'uid':user_id,'lo':cutoff-timedelta(minutes=max(WINDOWS[tf] for tf in cfg.capital_neutral_ratio)),
                    'cutoff':cutoff,'known_at':known_at})).mappings().all()
        assets = (await db.execute(text(ASSET_SQL), {'symbol':symbol,'lo':cutoff-timedelta(minutes=WINDOWS[cfg.asset_window]),
                  'cutoff':cutoff,'known_at':known_at})).mappings().all()
        await db.execute(text("SELECT set_config('statement_timeout', :timeout, true)"), {'timeout': previous_timeout})
    return capitals, assets


async def load_context(db, user_id, symbol, cutoff, cfg):
    if cfg is None or not cfg.enabled:
        return {'confirmation': 'disabled', 'capital': {}, 'asset': {}, 'operational_effect': False}
    known_at = datetime.now(timezone.utc)
    cutoff = utc(cutoff)
    unavailable = {'version': 'mtf_pump_flow_v1', 'config_hash': canonical_hash(cfg.identity_dump()),
                   'config': cfg.identity_dump(), 'captured_at': known_at.isoformat(), 'cutoff': cutoff.isoformat(),
                   'confirmation': 'unavailable', 'capital': {}, 'asset': {}, 'operational_effect': False}
    try:
        from . import pump_monitor_service as pump
        # Redis only: sampled snapshot fallback cannot certify current membership.
        envelope = await pump._read_json(await pump._redis(), pump._LATEST_KEY.format(user_id=user_id))
        if not envelope or not 0 <= (known_at-utc(envelope['generated_at'])).total_seconds() <= cfg.max_source_age_seconds:
            return {**unavailable, 'reasons': ['pump_snapshot_stale_or_missing']}
        capitals, assets = await read_rows(db, user_id, symbol, cutoff, known_at, cfg)
        result = build_context(capitals, assets, symbol=symbol, monitored_symbols={r['symbol'] for r in envelope.get('rows',[])},
                               cutoff=cutoff, known_at=known_at, cfg=cfg)
        result['pump_generated_at'] = envelope['generated_at']
        result['pump_config_hash'] = envelope.get('config_hash')
        return result
    except Exception as exc:
        log.warning('MTF Pump context unavailable symbol=%s error=%s', symbol, type(exc).__name__)
        return {**unavailable, 'reasons': ['source_unavailable']}


REPORT_SQL = '''WITH final AS (
    SELECT DISTINCT ON (e.id) e.id,e.payload,e.config_snapshot,s.state,r.confirmation AS revised
    FROM mtf_chart_evaluations e JOIN mtf_chart_shadow_events s ON s.evaluation_id=e.id
    LEFT JOIN mtf_pump_flow_revisions r ON r.evaluation_id=e.id
    WHERE e.user_id=:uid AND e.watchlist_id=:wid
      AND e.payload->'pump_flow'->'config'->>'enabled'='true'
    ORDER BY e.id,s.id DESC
), cohorts AS (
    SELECT *, 'baseline'::text AS cohort FROM final
    UNION ALL
    SELECT *, COALESCE(payload->'pump_flow'->>'confirmation','unavailable') AS cohort FROM final
    UNION ALL
    -- Deferred re-reading of evaluations unavailable at capture: a subset, never a replacement.
    SELECT *, 'revised_'||revised AS cohort FROM final WHERE revised IS NOT NULL
)
SELECT payload->'pump_flow'->>'config_hash' AS flow_config_hash,
       payload->>'config_hash' AS mtf_config_hash, cohort,
       count(*) AS observations,
       count(*) FILTER(WHERE state->>'status' IN ('TP','SL','EXPIRED') AND state->>'pnl_pct' IS NOT NULL) AS closed,
       avg((state->>'pnl_pct')::double precision) FILTER(WHERE state->>'status' IN ('TP','SL','EXPIRED')) AS mean_net_pct,
       count(*) FILTER(WHERE (state->>'ambiguous')::boolean) AS ambiguous
FROM cohorts GROUP BY flow_config_hash,mtf_config_hash,cohort
ORDER BY flow_config_hash,mtf_config_hash,cohort'''


async def comparison_report(db, uid, wid, cfg):
    rows = (await db.execute(text(REPORT_SQL), {'uid':uid,'wid':wid})).mappings().all()
    return {'groups': [dict(r) for r in rows], 'operational_effect': False, 'calibrated': False,
            'method': 'Same unchanged MTF Shadow fills; confirmed is a subset of baseline, not an independent portfolio. Frozen entry context. Missing flow stays separate. No causal or significance claim.',
            'status': 'collecting' if not rows else 'descriptive',
            'minimum_sample': cfg.min_report_sample}


# ── MTF_PUMP_FLOW_REVISION: deferred, immutable re-reading ───────────────────
# An evaluation frozen before the Pump cycle published its last closed minute keeps
# its original context untouched; one later reading is stored apart, with provenance.

REVISION_CANDIDATES_SQL = '''SELECT e.id, e.symbol, e.payload->'pump_flow' AS flow
    FROM mtf_chart_evaluations e
    WHERE e.user_id=:uid AND e.observed_at>=:since
      AND e.payload->'pump_flow'->>'confirmation'='unavailable'
      AND e.payload->'pump_flow'->'asset'->'window_minutes' IS NOT NULL
      AND (e.payload->'pump_flow'->>'cutoff')::timestamptz<=:due
      AND NOT EXISTS (SELECT 1 FROM mtf_pump_flow_revisions r WHERE r.evaluation_id=e.id)
    ORDER BY e.observed_at LIMIT :limit'''

REVISION_INSERT_SQL = '''INSERT INTO mtf_pump_flow_revisions
    (evaluation_id, user_id, symbol, cutoff, original_captured_at, revised_at, delay_seconds,
     original_confirmation, confirmation, config_hash, context)
    VALUES (:eid, :uid, :symbol, :cutoff, :original_captured_at, :revised_at, :delay_seconds,
     :original_confirmation, :confirmation, :config_hash, CAST(:context AS jsonb))
    ON CONFLICT (evaluation_id) DO NOTHING RETURNING evaluation_id'''


def revisable(flow):
    """Only evaluations whose flow was actually read at capture: the symbol was in the
    Pump universe then (membership is never re-derived later) and windows were summarized."""
    flow = flow or {}
    return (flow.get('confirmation') == 'unavailable' and 'window_minutes' in (flow.get('asset') or {})
            and bool(flow.get('cutoff')) and isinstance(flow.get('config'), dict))


def revise(capital_rows, asset_rows, *, flow, symbol, known_at):
    """Same thresholds as the original evaluation; only the reading time differs."""
    from ..schemas.mtf_chart import MTFPumpFlowConfig
    cfg = MTFPumpFlowConfig.model_validate(flow['config'])
    cutoff = utc(flow['cutoff'])
    context = build_context(capital_rows, asset_rows, symbol=symbol, monitored_symbols={symbol},
                            cutoff=cutoff, known_at=known_at, cfg=cfg)
    context['revision_of'] = {
        'original_confirmation': flow.get('confirmation'),
        'original_captured_at': flow.get('captured_at'),
        'original_reasons': flow.get('reasons') or [],
        'original_asset_reason': (flow.get('asset') or {}).get('reason'),
        'delay_seconds': (known_at-cutoff).total_seconds(),
    }
    return context


async def revise_due(db, user_id, flow_cfg, now, clock=None):
    """Revise this user's due evaluations once each. Returns how many were written."""
    from ..schemas.mtf_chart import MTFPumpFlowConfig
    rev = flow_cfg.revision if flow_cfg else None
    if flow_cfg is None or not flow_cfg.enabled or rev is None or not rev.enabled:
        return 0
    clock = clock or (lambda: datetime.now(timezone.utc))
    rows = (await db.execute(text(REVISION_CANDIDATES_SQL), {
        'uid': user_id, 'since': now-timedelta(seconds=flow_cfg.max_evaluation_delay_seconds),
        'due': now-timedelta(seconds=rev.delay_seconds), 'limit': rev.batch_limit})).mappings().all()
    written = 0
    for row in rows:
        flow = row['flow']
        if not revisable(flow):
            continue
        # One bad evaluation never blocks the batch (no poison pill): its savepoint is
        # rolled back and it ages out of the freshness window on its own.
        try:
            async with db.begin_nested():
                written += await _revise_one(db, user_id, row, flow, rev, clock, MTFPumpFlowConfig)
        except Exception:
            log.exception('MTF Pump flow revision skipped evaluation=%s', row['id'])
    return written


async def _revise_one(db, user_id, row, flow, rev, clock, config_type):
    original_cfg = config_type.model_validate(flow['config'])
    cutoff = utc(flow['cutoff'])
    known_at = clock()
    age = (known_at-cutoff).total_seconds()
    # Past the original freshness limit a reading would only restate that failure.
    if age < rev.delay_seconds or age > original_cfg.max_evaluation_delay_seconds:
        return 0
    capitals, assets = await read_rows(db, user_id, row['symbol'], cutoff, known_at, original_cfg)
    context = revise(capitals, assets, flow=flow, symbol=row['symbol'], known_at=known_at)
    captured = flow.get('captured_at')
    created = await db.scalar(text(REVISION_INSERT_SQL), {
        'eid': row['id'], 'uid': user_id, 'symbol': row['symbol'], 'cutoff': cutoff,
        'original_captured_at': utc(captured) if captured else None, 'revised_at': known_at,
        'delay_seconds': int(age), 'original_confirmation': flow.get('confirmation'),
        'confirmation': context['confirmation'], 'config_hash': context['config_hash'],
        'context': json.dumps(context, default=str, allow_nan=False)})
    return 1 if created else 0


async def revise_all(db):
    """Every active MTF user, isolated per user: one failure never blocks the others."""
    from sqlalchemy import select
    from ..models.config_profile import ConfigProfile
    from .mtf_chart_service import load_config
    users = (await db.execute(select(ConfigProfile.user_id).where(
        ConfigProfile.config_type == 'mtf_analysis', ConfigProfile.is_active.is_(True),
        ConfigProfile.pool_id.is_(None)))).scalars().all()
    total = 0
    for user_id in users:
        try:
            cfg = await load_config(db, user_id)
            total += await revise_due(db, user_id, cfg.pump_flow, datetime.now(timezone.utc))
            await db.commit()
        except Exception:
            await db.rollback()
            log.exception('MTF Pump flow revision failed user=%s', user_id)
    return total

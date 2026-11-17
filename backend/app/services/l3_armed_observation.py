"""L3_ARMED_OBSERVATION_V1: shared read-only projection for UI and external consumers."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, text

from ..models.config_profile import ConfigProfile
from ..models.profile import Profile
from .l3_profile_consolidation import load_l3_profile_consolidation
from .mtf_chart_service import ENGINE_VERSION, TF_SECONDS, load_config
from .profile_runtime_config import canonical_hash


def timestamp(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('MISSING_SOURCE_TIME')
    return value.astimezone(timezone.utc)


def reading(item, row, *, now, cfg, profile_hashes, indicator_identity):
    result = dict(status=None, observed_status=None, data_state='pending', reason_codes=[],
                  observation_id=None, observed_at=None, candle_close_at=None, valid_until=None,
                  profile_id=item['profile_id'], source='mtf_history_observations')
    reasons = result['reason_codes']
    if row is None:
        reasons.append('EVALUATION_UNAVAILABLE')
        return result
    analysis = row['analysis'] or {}
    result.update(observation_id=str(row['id']), observed_status=row['status'],
                  observed_at=row['observed_at'].isoformat(),
                  candle_close_at=analysis.get('candle_close_at_utc'))
    if row['profile_id'] != item['profile_id'] or analysis.get('profile_id') != item['profile_id']:
        reasons.append('REFERENCE_PROFILE_CHANGED')
    if not profile_hashes.get(item['profile_id']) or analysis.get('profile_hash') != profile_hashes[item['profile_id']]:
        reasons.append('PROFILE_CHANGED')
    if cfg is None:
        reasons.append('CONFIG_UNAVAILABLE')
    else:
        if not cfg.enabled:
            reasons.append('MTF_DISABLED')
        if analysis.get('config_hash') != canonical_hash(cfg.engine_dump()):
            reasons.append('CONFIG_CHANGED')
        try:
            observed = timestamp(row['observed_at'])
            closed = timestamp(analysis.get('candle_close_at_utc'))
            expires = min(observed, closed) + timedelta(seconds=cfg.stale_tf_multiplier * TF_SECONDS['5m'])
            result['valid_until'] = expires.isoformat()
            if observed > now or closed > now:
                reasons.append('INVALID_SOURCE_TIME')
            elif now >= expires:
                reasons.append('EVALUATION_STALE')
                result['data_state'] = 'stale'
        except (ValueError, TypeError):
            reasons.append('MISSING_SOURCE_TIME')
    if not indicator_identity or analysis.get('indicator_identity') != indicator_identity:
        reasons.append('INDICATOR_CONFIG_CHANGED')
    if analysis.get('engine_version') != ENGINE_VERSION:
        reasons.append('ENGINE_CHANGED')
    if row['quality'] != 'current':
        reasons.append('EVALUATION_DATA_PENDING')
    if row['status'] not in ('WAIT', 'ARMED', 'TRIGGERED', 'NO_TRADE') or analysis.get('status') != row['status']:
        reasons.append('INVALID_MTF_STATUS')
    if not reasons:
        result.update(status=row['status'], data_state='current')
    return result


async def load_l3_armed_display(db, *, user_id):
    # Enrich outside the base loader: history capture itself calls that loader.
    result = await load_l3_profile_consolidation(db, user_id=user_id)
    now = timestamp(result['as_of'])
    items = result['items']
    if not items:
        return {**result, 'armed_items': [], 'armed_count': 0}
    try:
        cfg = await load_config(db, user_id)
    except ValueError:
        cfg = None
    profiles = (await db.execute(select(Profile.id, Profile.config).where(
        Profile.user_id == user_id, Profile.is_active.is_(True),
        Profile.id.in_([UUID(p) for p in {i['profile_id'] for i in items}])))).all()
    profile_hashes = {str(p.id): canonical_hash(p.config) for p in profiles}
    configs = (await db.execute(select(ConfigProfile.id, ConfigProfile.config_json).where(
        ConfigProfile.user_id == user_id, ConfigProfile.config_type == 'indicators',
        ConfigProfile.pool_id.is_(None), ConfigProfile.is_active.is_(True)))).all()
    identity = None
    if len(configs) == 1 and configs[0].config_json:
        identity = dict(config_profile_id=str(configs[0].id), config_user_id=str(user_id),
                        config_hash=canonical_hash(configs[0].config_json))
    # Select latest first, then validate. Never resurrect an older ARMED reading.
    rows = (await db.execute(text('''SELECT DISTINCT ON (symbol)
        id,symbol,profile_id,observed_at,status,quality,payload->'analysis' AS analysis
        FROM mtf_history_observations
        WHERE user_id=:uid AND watchlist_id='l3-consolidated'
          AND symbol=ANY(CAST(:symbols AS text[])) AND observed_at<=:now
        ORDER BY symbol,observed_at DESC,id DESC'''),
        {'uid':user_id, 'symbols':[i['symbol'] for i in items], 'now':now})).mappings().all()
    latest = {r['symbol']: r for r in rows}
    enriched = [{**item, 'mtf':reading(item, latest.get(item['symbol']), now=now, cfg=cfg,
                 profile_hashes=profile_hashes, indicator_identity=identity)} for item in items]
    armed = [i for i in enriched if i['mtf']['status'] == 'ARMED']
    return {**result, 'items':enriched, 'armed_items':armed, 'armed_count':len(armed)}


async def load_l3_armed_assets(db, *, user_id):
    display = await load_l3_armed_display(db, user_id=user_id)
    return dict(id='l3-consolidated', name='L3 Consolidado', level='L3', virtual=True,
                read_only=True, semantic='MTF_ARMED_PROFILE_REFERENCE',
                observation_contract='L3_ARMED_OBSERVATION_V1',
                executable=False, execution_authority=False,
                consumer_policy='Observation only. ARMED is not order authorization. Real orders remain blocked.',
                items=[{**i, 'profile_status':i['status'], 'status':'ARMED'} for i in display['armed_items']],
                total=display['armed_count'], as_of=display['as_of'])

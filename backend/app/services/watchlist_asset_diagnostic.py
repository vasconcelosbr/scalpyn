"""WATCHLIST_ASSET_DIAGNOSTIC_V1: complete latest evidence; SELECT-only, no scoring."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, text

from ..models.pipeline_watchlist import PipelineWatchlist
from ..models.profile import Profile


async def diagnostic(db, *, user_id, watchlist_id, symbol):
    wl = await db.scalar(select(PipelineWatchlist).where(
        PipelineWatchlist.id == watchlist_id, PipelineWatchlist.user_id == user_id))
    if wl is None:
        raise HTTPException(404, "Watchlist não encontrada.")
    # SQL includes both UUID columns and textual MTF lineage. asyncpg requires
    # strings for parameters explicitly cast to text; PostgreSQL infers UUID elsewhere.
    params = dict(uid=str(user_id), wid=str(watchlist_id), symbol=symbol,
                  market=wl.market_mode or 'spot', pid=str(wl.profile_id) if wl.profile_id else None)
    async def one(sql):
        return await db.scalar(text(sql), params)
    async def many(sql):
        return list((await db.execute(text(sql), params)).scalars().all())

    asset = await one("""SELECT to_jsonb(a) FROM pipeline_watchlist_assets a
        WHERE a.watchlist_id=:wid AND a.symbol=:symbol""")
    rejections = await many("""SELECT to_jsonb(r) FROM pipeline_watchlist_rejections r
        WHERE r.watchlist_id=:wid AND r.user_id=:uid AND r.symbol=:symbol
        ORDER BY r.recorded_at DESC,r.id""")
    classification = None
    if wl.level == 'L3' and wl.market_mode == 'spot':
        from .profile_display_status import load_profile_display_rows
        rows = await load_profile_display_rows(db, user_id=user_id, watchlist_id=watchlist_id)
        classification = next((r for r in rows if r['symbol'] == symbol), None)
    if asset is None and not rejections and classification is None:
        raise HTTPException(404, "Ativo não encontrado nesta watchlist. Atualize a listagem.")

    profile = await db.scalar(select(Profile).where(Profile.id == wl.profile_id,
        Profile.user_id == user_id)) if wl.profile_id else None
    # Preserve every producer/timeframe independently: a micro row cannot hide EMAs/RSI.
    indicators = await many("""SELECT to_jsonb(i) FROM (
        SELECT DISTINCT ON (timeframe,coalesce(scheduler_group,'combined')) *
        FROM indicators WHERE symbol=:symbol AND market_type=:market AND time<=now()
        ORDER BY timeframe,coalesce(scheduler_group,'combined'),time DESC
    ) i""")
    metadata = await one("""SELECT to_jsonb(m) FROM market_metadata m WHERE symbol=:symbol""")
    decision = await one("""SELECT to_jsonb(d) FROM decisions_log d
        WHERE d.user_id=:uid AND d.profile_id=:pid AND d.symbol=:symbol
          AND d.event_type IS DISTINCT FROM 'PROFILE_CONSOLIDATION'
          AND d.event_type IS DISTINCT FROM 'PROFILE_CONSOLIDATION_REJECTED'
          AND (d.metrics #>> '{l3_authorization_contract_v3,lineage,watchlist_id}' IS NULL
               OR d.metrics #>> '{l3_authorization_contract_v3,lineage,watchlist_id}'=CAST(:wid AS text))
        ORDER BY d.created_at DESC,d.id DESC LIMIT 1""") if profile else None
    mtf = await one("""SELECT to_jsonb(o) FROM mtf_history_observations o
        WHERE o.user_id=:uid AND o.watchlist_id=CAST(:wid AS text) AND o.symbol=:symbol
        ORDER BY o.observed_at DESC,o.id DESC LIMIT 1""")
    evaluation = await one("""SELECT to_jsonb(e) FROM mtf_chart_evaluations e
        WHERE e.user_id=:uid AND e.watchlist_id=CAST(:wid AS text) AND e.symbol=:symbol
        ORDER BY e.observed_at DESC,e.id DESC LIMIT 1""")
    consolidated = await one("""SELECT to_jsonb(o) FROM mtf_history_observations o
        WHERE o.user_id=:uid AND o.watchlist_id='l3-consolidated' AND o.symbol=:symbol
          AND o.profile_id=CAST(:pid AS text)
        ORDER BY o.observed_at DESC,o.id DESC LIMIT 1""") if profile and wl.level == 'L3' else None
    configs = await many("""SELECT jsonb_build_object('id',id,'config_type',config_type,
        'updated_at',updated_at,'config_json',config_json) FROM config_profiles
        WHERE user_id=:uid AND pool_id IS NULL AND is_active
          AND config_type IN ('indicators','mtf_analysis') ORDER BY config_type,id""")
    sources = dict(pipeline_asset=asset, pipeline_rejections=rejections,
        profile_classification=classification, profile_decision=decision,
        market_metadata=metadata, indicators=indicators, mtf_observation=mtf,
        mtf_evaluation=evaluation, consolidated_mtf_observation=consolidated)
    return dict(schema_version='WATCHLIST_ASSET_DIAGNOSTIC_V1',
        captured_at=datetime.now(timezone.utc).isoformat(), symbol=symbol,
        watchlist=dict(id=str(wl.id), name=wl.name, level=wl.level,
            market_mode=wl.market_mode, profile_id=str(wl.profile_id) if wl.profile_id else None,
            last_scanned_at=wl.last_scanned_at),
        profile=dict(id=str(profile.id),name=profile.name,config=profile.config,
                     updated_at=profile.updated_at) if profile else None,
        configuration=configs, sources=sources,
        availability={key: 'available' if value is not None and value != [] else 'unavailable'
                      for key,value in sources.items()},
        scope='Latest persisted snapshot per source, timeframe and producer. Source times differ; current readings do not replace frozen decision inputs.',
        read_only=True, execution_authority=False)


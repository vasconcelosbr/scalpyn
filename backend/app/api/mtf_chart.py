"""Authenticated chart reads. GET routes never schedule or persist evaluations."""
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import get_db
from .config import get_current_user_id
from ..models.profile import Profile
from ..schemas.mtf_chart import MTFChartConfig, MTFPreferences, experimental_preset, presentation_preset
from ..services.config_service import config_service
from ..services import mtf_chart_service as service

router = APIRouter(prefix="/api/mtf", tags=["MTF observational chart"])


async def config_or_error(db, uid):
    try:
        return await service.load_config(db, uid)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, detail="MTF_CONFIG_REQUIRED: configure o preset experimental") from exc


@router.get("/bootstrap")
async def bootstrap(response: Response, db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    config = await config_service.get_config(db, "mtf_analysis", uid)
    profiles = (await db.execute(select(Profile).where(Profile.user_id == uid, Profile.is_active.is_(True)))).scalars().all()
    return {"config": config or None, "preset": experimental_preset(), "watchlists": await service.watchlist_options(db, uid),
            "profiles": [{"id": str(p.id), "name": p.name, "default_timeframe": (p.config or {}).get("default_timeframe")} for p in profiles],
            "preferences": await config_service.get_config(db, "mtf_preferences", uid), 'presentation_preset': presentation_preset(),
            "mode": "SHADOW", "operational_effect": False}


@router.put("/config")
async def update_config(payload: MTFChartConfig, db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    allowed = {w["id"] for w in await service.watchlist_options(db, uid)}
    if payload.default_watchlist not in allowed or not set(payload.monitored_watchlists) <= allowed:
        raise HTTPException(422, "WATCHLIST_NOT_OWNED_OR_NOT_SPOT")
    return await config_service.update_config(db, "mtf_analysis", uid, payload.model_dump(), uid,
        change_description="Gráfico MTF: configuração experimental exclusivamente observacional")


@router.put("/preferences")
async def preferences(payload: MTFPreferences, db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    return await config_service.update_config(db, "mtf_preferences", uid, payload.model_dump(), uid,
        change_description="Preferências visuais do Gráfico MTF")


@router.get("/watchlist")
async def watchlist(response: Response, watchlist_id: str, db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    cfg = await config_or_error(db, uid)
    try:
        items = await service.members(db, uid, watchlist_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    latest = await service.latest_evaluations(db, uid, watchlist_id)
    from datetime import datetime, timezone
    from ..services.profile_runtime_config import canonical_hash
    expected_hash = canonical_hash(cfg.engine_dump())
    now = datetime.now(timezone.utc).timestamp()
    for analysis in latest.values():
        closed_at = analysis.get('candle_close_at_utc')
        stale = not closed_at or now-datetime.fromisoformat(closed_at).timestamp() > cfg.stale_tf_multiplier*300
        if stale or analysis.get('config_hash') != expected_hash:
            analysis['status'] = 'NO_TRADE'
            analysis['score']['total'] = 0
            analysis['vetoes'] = list(analysis['vetoes']) + ['EVALUATION_STALE' if stale else 'CONFIG_CHANGED']
    return {"items": [{**item, "mtf": latest.get(item["symbol"])} for item in items[:cfg.max_assets_per_watchlist]],
            "total": len(items), "limited": len(items) > cfg.max_assets_per_watchlist,
            "monitored": watchlist_id in cfg.monitored_watchlists,
            'as_of': datetime.now(timezone.utc).isoformat(),
            'snapshot_id': canonical_hash({'members': items, 'config': expected_hash})}


@router.get("/chart")
async def chart(response: Response, symbol: str, watchlist_id: str, profile_id: UUID | None = None,
                since: str | None = Query(default=None, max_length=64),
                db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    response.headers["Cache-Control"] = "private, no-store"
    cfg = await config_or_error(db, uid)
    try:
        items = await service.members(db, uid, watchlist_id)
        member = next((m for m in items if m["symbol"] == symbol), None)
        if member is None:
            raise HTTPException(404, "ASSET_NO_LONGER_IN_WATCHLIST")
        data = await service.chart_data(db, uid, symbol, profile_id or member.get("profile_id"), cfg)
        # A cold chart can outlive an L3 authorization while computing history.
        if not any(m['symbol'] == symbol for m in await service.members(db, uid, watchlist_id)):
            raise HTTPException(404, 'ASSET_NO_LONGER_IN_WATCHLIST')
        return service.chart_transport(data, context={'user_id': str(uid), 'watchlist_id': watchlist_id,
            'symbol': symbol, 'profile_id': str(profile_id or member.get('profile_id'))},
            since=since, max_entries=cfg.max_assets_per_watchlist * len(service.TF_SECONDS))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/history")
async def history(symbol: str, watchlist_id: str, before: str | None = None,
                  db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    cfg = await config_or_error(db, uid)
    # Cursor is parsed, never interpolated into SQL. Historical membership may
    # expire; ownership is enforced by the persisted evaluation's user_id.
    from datetime import datetime
    try:
        cursor = datetime.fromisoformat(before) if before else None
    except ValueError as exc:
        raise HTTPException(422, "INVALID_CURSOR") from exc
    rows = (await db.execute(text("""SELECT e.payload,e.observed_at,s.state FROM mtf_chart_evaluations e
        LEFT JOIN LATERAL (SELECT state FROM mtf_chart_shadow_events WHERE evaluation_id=e.id ORDER BY id DESC LIMIT 1) s ON true
        WHERE e.user_id=:uid AND e.symbol=:symbol AND e.watchlist_id=:wid
        AND (CAST(:before AS timestamptz) IS NULL OR e.observed_at < CAST(:before AS timestamptz))
        ORDER BY e.observed_at DESC LIMIT :limit"""), {"uid": uid, "symbol": symbol, "wid": watchlist_id,
        "before": cursor, "limit": cfg.history_page_size})).mappings().all()
    return {"items": [{"evaluation": r["payload"], "shadow": r["state"], "observed_at": r["observed_at"].isoformat()} for r in rows],
            "next_cursor": rows[-1]["observed_at"].isoformat() if len(rows) == cfg.history_page_size else None}


@router.get("/report")
async def report(watchlist_id: str, db: AsyncSession = Depends(get_db), uid: UUID = Depends(get_current_user_id)):
    """Descriptive net expectancy, never a calibration/promotion gate."""
    from statistics import NormalDist
    from math import sqrt
    cfg = await config_or_error(db, uid)
    rows = (await db.execute(text("""WITH final AS (
        SELECT e.payload,e.config_snapshot,s.state FROM mtf_chart_evaluations e
        JOIN LATERAL (SELECT state FROM mtf_chart_shadow_events WHERE evaluation_id=e.id ORDER BY id DESC LIMIT 1) s ON true
        WHERE e.user_id=:uid AND e.watchlist_id=:wid
    ) SELECT payload->'trigger_5m'->>'pattern' AS pattern, payload->>'config_hash' AS config_hash,
        count(*) AS n, avg((state->>'pnl_pct')::double precision) AS mean_pct,
        stddev_samp((state->>'pnl_pct')::double precision) AS stddev_pct,
        count(*) FILTER (WHERE (state->>'pnl_usdt')::double precision > 0) AS profitable,
        count(*) FILTER (WHERE (state->>'ambiguous')::boolean) AS ambiguous,
        min((config_snapshot->>'fee_per_side_pct')::double precision) AS fee_per_side_pct,
        min((config_snapshot->>'slippage_pct')::double precision) AS slippage_pct
        FROM final WHERE state->>'status' IN ('TP','SL','EXPIRED') AND state->>'pnl_pct' IS NOT NULL
        GROUP BY payload->'trigger_5m'->>'pattern', payload->>'config_hash'
        ORDER BY pattern,config_hash"""), {"uid": uid, "wid": watchlist_id})).mappings().all()
    groups = []
    for row in rows:
        item = dict(row)
        n = item['n']
        enough = n >= cfg.min_report_sample and item['stddev_pct'] is not None
        half = NormalDist().inv_cdf((1+cfg.confidence_level)/2)*item['stddev_pct']/sqrt(n) if enough else None
        groups.append({**item, 'timeframe': '5m', 'sample_sufficient': enough,
            'confidence_interval': [item['mean_pct']-half, item['mean_pct']+half] if half is not None else None,
            'positive_fraction': item['profitable']/n if n else None})
    return {'groups': groups, 'confidence_level': cfg.confidence_level, 'calibrated': False,
            'method': 'Normal approximation; descriptive only; overlapping observations are not independent.',
            'origin': 'mtf_chart_shadow_events; latest event per evaluation; pnl net of frozen configured costs',
            'formula': 'mean(pnl_pct) ± NormalQuantile((1+confidence)/2) × stddev_samp(pnl_pct)/sqrt(N)',
            'operational_effect': False}

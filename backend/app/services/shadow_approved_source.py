"""SHADOW_CONSOLIDATED_SIGNALS: ARMED/TRIGGERED simulation admission, never order authority."""
from copy import deepcopy
from datetime import datetime, timezone
import logging
from uuid import UUID

from sqlalchemy import select

from ..models.config_profile import ConfigProfile
from ..models.profile import Profile
from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..models.shadow_trade import ShadowTrade
from ..schemas.watchlist_lineage_context import WatchlistLineageContext
from .l3_armed_observation import load_l3_armed_display

LEGACY = "L3"
CONSOLIDATED = "L3_CONSOLIDATED"
APPROVED_SOURCES = (LEGACY, CONSOLIDATED)
SOURCE_REPLACED = "SHADOW_SOURCE_REPLACED"
logger = logging.getLogger(__name__)


def eligible_signal_items(display):
    return [item for item in display['items']
            if item['mtf']['status'] in ('ARMED', 'TRIGGERED')]


async def configured_source(db, user_id, *, lock=False):
    query = select(ConfigProfile.config_json).where(
        ConfigProfile.user_id == user_id, ConfigProfile.config_type == "spot_engine",
        ConfigProfile.is_active.is_(True), ConfigProfile.pool_id.is_(None),
    )
    if lock:
        # Serialize source switching with captures, but allow unrelated symbols.
        query = query.with_for_update(read=True)
    rows = (await db.execute(query)).scalars().all()
    if len(rows) != 1:
        raise ValueError("shadow_source_config_missing_or_ambiguous")
    value = (rows[0].get("shadow") or {}).get("approved_source", LEGACY)
    if value not in APPROVED_SOURCES:
        raise ValueError("shadow_source_invalid")
    return value


async def admission(db, *, user_id, symbol, direction, source):
    """Shared final boundary for *every* approved writer, including legacy ones."""
    if source not in APPROVED_SOURCES:
        return True
    selected = await configured_source(db, user_id, lock=True)
    if source != selected:
        logger.info("%s user=%s symbol=%s source=%s selected=%s",
                    SOURCE_REPLACED, user_id, symbol, source, selected)
        return False
    from .l3_trade_consolidation import acquire_consolidation_lock
    await acquire_consolidation_lock(db, user_id=user_id, symbol=symbol, direction=direction)
    active = await db.scalar(select(ShadowTrade.id).where(
        ShadowTrade.user_id == user_id, ShadowTrade.symbol == symbol,
        ShadowTrade.direction == direction,
        ShadowTrade.source.in_(APPROVED_SOURCES if source == CONSOLIDATED else (CONSOLIDATED,)),
        ShadowTrade.status.in_(("PENDING", "RUNNING")),
    ).limit(1))
    if active is not None:
        logger.info('SHADOW_CONSOLIDATED_SKIPPED user=%s symbol=%s reason=ACTIVE_APPROVED_SHADOW trade=%s',
                    user_id, symbol, active)
    return active is None


async def current_observation(db, *, user_id, symbol, observation_id):
    display = await load_l3_armed_display(db, user_id=user_id, include_analysis=True)
    for item in eligible_signal_items(display):
        if item["symbol"] == symbol and item["mtf"]["observation_id"] == str(observation_id):
            if datetime.fromisoformat(item["mtf"]["valid_until"]) <= datetime.now(timezone.utc):
                break
            return item, display["analyses"][symbol]
    raise ValueError("SHADOW_SIGNAL_OBSERVATION_NO_LONGER_CURRENT")


async def capture_one(db, *, user_id, symbol, observation_id, user_config):
    from .shadow_trade_service import _SyntheticDecision, _create_from_decision, _flatten_analysis_snapshot
    if not await admission(db, user_id=user_id, symbol=symbol, direction="SPOT", source=CONSOLIDATED):
        return None
    # A completed trade also consumes its immutable observation permanently.
    used = await db.scalar(select(ShadowTrade.id).where(
        ShadowTrade.user_id == user_id, ShadowTrade.mtf_observation_id == UUID(str(observation_id)),
    ).limit(1))
    if used:
        return None
    item, analysis = await current_observation(
        db, user_id=user_id, symbol=symbol, observation_id=observation_id)
    profile = await db.scalar(select(Profile).where(
        Profile.id == UUID(item["profile_id"]), Profile.user_id == user_id,
        Profile.is_active.is_(True),
    ).with_for_update())
    if profile is None:
        return None
    wl = await db.scalar(select(PipelineWatchlist).where(
        PipelineWatchlist.id == UUID(item["watchlist_id"]), PipelineWatchlist.user_id == user_id,
        PipelineWatchlist.profile_id == profile.id,
        PipelineWatchlist.level == 'L3', PipelineWatchlist.market_mode == 'spot',
    ))
    # SHADOW_L2_PARENT_ASSET: the display universe exposes the live L2 asset,
    # not an L3 asset row (normal L3 scans write decisions, not membership rows).
    asset = await db.scalar(select(PipelineWatchlistAsset).where(
        PipelineWatchlistAsset.id == UUID(item["asset_id"]),
        PipelineWatchlistAsset.symbol == symbol,
        PipelineWatchlistAsset.watchlist_id == wl.source_watchlist_id,
    ).join(PipelineWatchlist, PipelineWatchlist.id == PipelineWatchlistAsset.watchlist_id).where(
        PipelineWatchlist.user_id == user_id, PipelineWatchlist.level == 'L2',
        PipelineWatchlist.market_mode == 'spot',
    )) if wl is not None else None
    if asset is None:
        logger.info('SHADOW_CONSOLIDATED_SKIPPED user=%s symbol=%s reason=PARENT_ASSET_UNAVAILABLE', user_id, symbol)
        return None
    signal_status = item['mtf']['status']
    now = datetime.now(timezone.utc)
    lineage = WatchlistLineageContext(
        watchlist_id=str(wl.id), watchlist_name=wl.name, watchlist_level=wl.level,
        source_watchlist_id=str(wl.source_watchlist_id) if wl.source_watchlist_id else None,
        profile_id=str(profile.id), profile_name=profile.name,
        profile_version=profile.profile_version or profile.updated_at,
        rules_snapshot=deepcopy(profile.config), lineage_confidence="EXACT",
        lineage_source=f"l3_consolidated_{signal_status.lower()}", lineage_resolved_at=now,
    )
    # Capture is a new simulation now, not a retroactive fill at the MTF candle.
    # Keep the original profile decision only as provenance, never as authority.
    snapshot = deepcopy(asset.analysis_snapshot or {})
    decision = _SyntheticDecision(user_id=user_id, symbol=symbol, created_at=now,
        metrics={"indicators_snapshot": snapshot.get("indicators_snapshot") or {
            key: {"value": value} for key, value in _flatten_analysis_snapshot(snapshot).items()}})
    return await _create_from_decision(db, decision, f"L3_CONSOLIDATED_{signal_status}", user_config,
        source=CONSOLIDATED, lineage=lineage, mtf_observation_id=UUID(str(observation_id)),
        extra_config={"approved_source": CONSOLIDATED, "l3_consolidated": {
            "entry_signal": signal_status, "source_asset_id": str(asset.id),
            "source_asset_watchlist_id": str(asset.watchlist_id),
            "observation_id": str(observation_id), "reference_decision_id": item.get("decision_id"),
            "mtf": deepcopy(item["mtf"]), "analysis": deepcopy(analysis),
            "candidate_profile_ids": item["candidate_profile_ids"],
            "candidate_profile_names": item["candidate_profile_names"],
            "executable": False, "execution_authority": False,
        }})


async def capture_approved_shadows(user_id, user_config):
    from ..database import CeleryAsyncSessionLocal
    async with CeleryAsyncSessionLocal() as db:
        if await configured_source(db, user_id) != CONSOLIDATED:
            return 0
        display = await load_l3_armed_display(db, user_id=user_id)
    created = 0
    candidates = eligible_signal_items(display)
    for item in candidates:
        try:
            async with CeleryAsyncSessionLocal() as db:
                async with db.begin():
                    trade_id = await capture_one(db, user_id=user_id, symbol=item["symbol"],
                        observation_id=item["mtf"]["observation_id"], user_config=user_config)
                    created += int(trade_id is not None)
        except Exception:
            logger.exception("SHADOW_CONSOLIDATED_CAPTURE_FAILED user=%s symbol=%s", user_id, item["symbol"])
    logger.info("SHADOW_CONSOLIDATED_CAPTURE user=%s eligible=%s created=%s", user_id, len(candidates), created)
    return created

"""Canonical resolution of the live Spot pipeline and its current L3 candidates.

This module deliberately reads ``pipeline_watchlist_assets`` rather than
``shadow_trades``.  A watchlist represents the opportunity *now*; an open
Shadow trade represents historical position follow-up and must not keep a
symbol in the opportunity funnel after its indicators stop qualifying.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..models.pipeline_watchlist import PipelineWatchlist, PipelineWatchlistAsset
from ..models.profile import Profile


def _active_asset(model):
    return or_(model.level_direction.is_(None), model.level_direction == "up")


@dataclass(frozen=True)
class SpotPipelineChain:
    pool_watchlist: PipelineWatchlist
    l1_watchlist: PipelineWatchlist
    l2_watchlist: PipelineWatchlist
    l3_watchlists: tuple[PipelineWatchlist, ...]


@dataclass(frozen=True)
class LiveL3Contribution:
    asset_id: UUID
    watchlist_id: UUID
    profile_id: UUID
    profile_name: str
    symbol: str
    alpha_score: Optional[float]
    current_price: Optional[float]
    refreshed_at: object


@dataclass(frozen=True)
class ConsolidatedL3Candidate:
    symbol: str
    winner: LiveL3Contribution
    contributors: tuple[LiveL3Contribution, ...]


async def resolve_spot_pipeline_chain(
    db: AsyncSession,
    *,
    user_id: UUID,
    pool_id: UUID,
) -> Optional[SpotPipelineChain]:
    """Resolve POOL -> L1 -> L2 -> every L3 using the persisted links.

    ``None`` means the mandatory chain is incomplete.  An empty
    ``l3_watchlists`` tuple is therefore never returned.
    """

    async def _first(level: str, **where):
        clauses = [
            PipelineWatchlist.user_id == user_id,
            PipelineWatchlist.level == level,
            PipelineWatchlist.market_mode == "spot",
        ]
        clauses.extend(getattr(PipelineWatchlist, key) == value for key, value in where.items())
        return (await db.execute(
            select(PipelineWatchlist)
            .join(Profile, Profile.id == PipelineWatchlist.profile_id)
            .where(*clauses)
            .where(
                PipelineWatchlist.auto_refresh.is_(True),
                Profile.user_id == user_id,
                Profile.is_active.is_(True),
            )
            .order_by(PipelineWatchlist.created_at.asc(), PipelineWatchlist.id.asc())
            .limit(1)
        )).scalars().first()

    pool_watchlist = await _first("POOL", source_pool_id=pool_id)
    if pool_watchlist is None:
        return None
    l1_watchlist = await _first("L1", source_watchlist_id=pool_watchlist.id)
    if l1_watchlist is None:
        return None
    l2_watchlist = await _first("L2", source_watchlist_id=l1_watchlist.id)
    if l2_watchlist is None:
        return None

    l3_rows = await db.execute(
        select(PipelineWatchlist)
        .join(Profile, Profile.id == PipelineWatchlist.profile_id)
        .where(
            PipelineWatchlist.user_id == user_id,
            PipelineWatchlist.level == "L3",
            PipelineWatchlist.market_mode == "spot",
            PipelineWatchlist.source_watchlist_id == l2_watchlist.id,
            PipelineWatchlist.profile_id.is_not(None),
            PipelineWatchlist.auto_refresh.is_(True),
            Profile.user_id == user_id,
            Profile.is_active.is_(True),
        )
        .order_by(PipelineWatchlist.created_at.asc(), PipelineWatchlist.id.asc())
    )
    l3_watchlists = tuple(l3_rows.scalars().all())
    if not l3_watchlists:
        return None

    return SpotPipelineChain(
        pool_watchlist=pool_watchlist,
        l1_watchlist=l1_watchlist,
        l2_watchlist=l2_watchlist,
        l3_watchlists=l3_watchlists,
    )


async def load_live_l3_candidates(
    db: AsyncSession,
    *,
    user_id: UUID,
    l2_watchlist_id: Optional[UUID] = None,
) -> list[ConsolidatedL3Candidate]:
    """Return current L3 opportunities, intersected with their live L2 parent.

    The parent intersection is evaluated in the same read.  It closes the
    short cascade window in which a symbol has already left L2 but a previous
    L3 snapshot has not yet been marked ``down``.  Only active profiles are
    eligible.  Open Shadow trades are intentionally absent from this query.
    """

    l2_watchlist = aliased(PipelineWatchlist)
    l2_asset = aliased(PipelineWatchlistAsset)
    l1_watchlist = aliased(PipelineWatchlist)
    l1_asset = aliased(PipelineWatchlistAsset)
    pool_watchlist = aliased(PipelineWatchlist)
    pool_asset = aliased(PipelineWatchlistAsset)
    l2_profile = aliased(Profile)
    l1_profile = aliased(Profile)
    pool_profile = aliased(Profile)
    statement = (
        select(
            PipelineWatchlistAsset.id.label("asset_id"),
            PipelineWatchlistAsset.watchlist_id.label("watchlist_id"),
            PipelineWatchlist.profile_id.label("profile_id"),
            Profile.name.label("profile_name"),
            PipelineWatchlistAsset.symbol.label("symbol"),
            PipelineWatchlistAsset.alpha_score.label("alpha_score"),
            PipelineWatchlistAsset.current_price.label("current_price"),
            PipelineWatchlistAsset.refreshed_at.label("refreshed_at"),
        )
        .join(
            PipelineWatchlist,
            PipelineWatchlist.id == PipelineWatchlistAsset.watchlist_id,
        )
        .join(Profile, Profile.id == PipelineWatchlist.profile_id)
        .join(
            l2_watchlist,
            and_(
                l2_watchlist.id == PipelineWatchlist.source_watchlist_id,
                l2_watchlist.user_id == user_id,
                l2_watchlist.level == "L2",
                l2_watchlist.market_mode == "spot",
            ),
        )
        .join(
            l2_asset,
            and_(
                l2_asset.watchlist_id == l2_watchlist.id,
                l2_asset.symbol == PipelineWatchlistAsset.symbol,
                _active_asset(l2_asset),
            ),
        )
        .join(
            l2_profile,
            and_(
                l2_profile.id == l2_watchlist.profile_id,
                l2_profile.user_id == user_id,
                l2_profile.is_active.is_(True),
            ),
        )
        .join(
            l1_watchlist,
            and_(
                l1_watchlist.id == l2_watchlist.source_watchlist_id,
                l1_watchlist.user_id == user_id,
                l1_watchlist.level == "L1",
                l1_watchlist.market_mode == "spot",
            ),
        )
        .join(
            l1_asset,
            and_(
                l1_asset.watchlist_id == l1_watchlist.id,
                l1_asset.symbol == PipelineWatchlistAsset.symbol,
                _active_asset(l1_asset),
            ),
        )
        .join(
            l1_profile,
            and_(
                l1_profile.id == l1_watchlist.profile_id,
                l1_profile.user_id == user_id,
                l1_profile.is_active.is_(True),
            ),
        )
        .join(
            pool_watchlist,
            and_(
                pool_watchlist.id == l1_watchlist.source_watchlist_id,
                pool_watchlist.user_id == user_id,
                pool_watchlist.level == "POOL",
                pool_watchlist.market_mode == "spot",
            ),
        )
        .join(
            pool_asset,
            and_(
                pool_asset.watchlist_id == pool_watchlist.id,
                pool_asset.symbol == PipelineWatchlistAsset.symbol,
                _active_asset(pool_asset),
            ),
        )
        .join(
            pool_profile,
            and_(
                pool_profile.id == pool_watchlist.profile_id,
                pool_profile.user_id == user_id,
                pool_profile.is_active.is_(True),
            ),
        )
        .where(
            PipelineWatchlist.user_id == user_id,
            PipelineWatchlist.level == "L3",
            PipelineWatchlist.market_mode == "spot",
            PipelineWatchlist.profile_id.is_not(None),
            PipelineWatchlist.auto_refresh.is_(True),
            l2_watchlist.auto_refresh.is_(True),
            l1_watchlist.auto_refresh.is_(True),
            pool_watchlist.auto_refresh.is_(True),
            Profile.user_id == user_id,
            Profile.is_active.is_(True),
            _active_asset(PipelineWatchlistAsset),
        )
    )
    if l2_watchlist_id is not None:
        statement = statement.where(PipelineWatchlist.source_watchlist_id == l2_watchlist_id)

    rows = (await db.execute(statement)).mappings().all()
    by_symbol: dict[str, list[LiveL3Contribution]] = {}
    seen_asset_ids: set[UUID] = set()
    for row in rows:
        if row["asset_id"] in seen_asset_ids:
            continue
        seen_asset_ids.add(row["asset_id"])
        contribution = LiveL3Contribution(
            asset_id=row["asset_id"],
            watchlist_id=row["watchlist_id"],
            profile_id=row["profile_id"],
            profile_name=str(row["profile_name"]),
            symbol=str(row["symbol"]).upper(),
            alpha_score=float(row["alpha_score"]) if row["alpha_score"] is not None else None,
            current_price=float(row["current_price"]) if row["current_price"] is not None else None,
            refreshed_at=row["refreshed_at"],
        )
        by_symbol.setdefault(contribution.symbol, []).append(contribution)

    consolidated: list[ConsolidatedL3Candidate] = []
    for symbol, contributions in by_symbol.items():
        ordered = sorted(
            contributions,
            key=lambda item: (
                item.alpha_score is None,
                -(item.alpha_score or 0.0),
                item.profile_name,
                str(item.watchlist_id),
            ),
        )
        consolidated.append(
            ConsolidatedL3Candidate(
                symbol=symbol,
                winner=ordered[0],
                contributors=tuple(ordered),
            )
        )

    return sorted(consolidated, key=lambda item: item.symbol)

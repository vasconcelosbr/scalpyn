"""Canonical resolution of the live Spot pipeline and its current L3 candidates.

The L3 symbol universe is read from the L2 parent's ``pipeline_watchlist_assets``
row, not the L3 watchlist's own (spot L3 is never written there in the normal
scan cycle -- authorization flows through ``decisions_log``/the outbox
instead). This module also deliberately reads asset tables rather than
``shadow_trades``: a watchlist represents the opportunity *now*; an open
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
    authorization: Optional[dict] = None


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


def _l3_symbol_universe_statement(
    *,
    user_id: UUID,
    l2_watchlist_id: Optional[UUID] = None,
    l3_watchlist_id: Optional[UUID] = None,
):
    """Every symbol currently active in an L3 profile's live L2 parent.

    Anchored on the L2 asset (confirmed live-maintained on every read), not on
    the L3 watchlist's own ``pipeline_watchlist_assets`` row: nothing writes
    that row for spot L3 in the normal scan cycle (L3 authorization flows
    through ``decisions_log``/the outbox instead), so anchoring there made
    this query -- and every one of its callers -- structurally always empty.
    The POOL/L1 intersection still guards against the short cascade window in
    which a symbol has already left an upstream level but a snapshot has not
    yet been marked ``down``. Only active profiles are eligible.
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
            l2_asset.id.label("asset_id"),
            PipelineWatchlist.id.label("watchlist_id"),
            PipelineWatchlist.profile_id.label("profile_id"),
            Profile.name.label("profile_name"),
            Profile.profile_version.label("profile_version"),
            PipelineWatchlist.filters_json.label("watchlist_filters"),
            l2_asset.symbol.label("symbol"),
            l2_asset.alpha_score.label("alpha_score"),
            l2_asset.current_price.label("current_price"),
            l2_asset.refreshed_at.label("refreshed_at"),
        )
        .select_from(PipelineWatchlist)
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
                l1_asset.symbol == l2_asset.symbol,
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
                pool_asset.symbol == l2_asset.symbol,
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
        )
    )
    if l2_watchlist_id is not None:
        statement = statement.where(PipelineWatchlist.source_watchlist_id == l2_watchlist_id)
    if l3_watchlist_id is not None:
        statement = statement.where(PipelineWatchlist.id == l3_watchlist_id)
    return statement


async def load_live_l3_candidates(
    db: AsyncSession,
    *,
    user_id: UUID,
    l2_watchlist_id: Optional[UUID] = None,
) -> list[ConsolidatedL3Candidate]:
    """Return current L3 opportunities, intersected with their live L2 parent.

    Open Shadow trades are intentionally absent from this query.
    """
    statement = _l3_symbol_universe_statement(user_id=user_id, l2_watchlist_id=l2_watchlist_id)
    rows = (await db.execute(statement)).mappings().all()
    from .l3_public_authorization import load_public_authorizations
    authorizations = await load_public_authorizations(db, user_id=user_id, candidates=rows)
    by_symbol: dict[str, list[LiveL3Contribution]] = {}
    seen_pairs: set[tuple] = set()
    for row in rows:
        authority = authorizations.get((row["watchlist_id"], row["symbol"]))
        if authority is None:
            continue
        minimum = float((row.get("watchlist_filters") or {}).get("min_alpha_score") or 0)
        if minimum > 0 and (authority["alpha_score"] is None or float(authority["alpha_score"]) < minimum):
            continue
        pair = (row["watchlist_id"], row["symbol"])
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        contribution = LiveL3Contribution(
            asset_id=row["asset_id"],
            watchlist_id=row["watchlist_id"],
            profile_id=row["profile_id"],
            profile_name=str(row["profile_name"]),
            symbol=str(row["symbol"]).upper(),
            alpha_score=float(authority["alpha_score"]) if authority["alpha_score"] is not None else None,
            current_price=float(authority["current_price"]) if authority["current_price"] is not None else None,
            refreshed_at=authority["evaluated_at"],
            authorization=authority,
        )
        by_symbol.setdefault(contribution.symbol, []).append(contribution)

    consolidated: list[ConsolidatedL3Candidate] = []
    for symbol, contributions in by_symbol.items():
        ordered = sorted(
            contributions,
            key=lambda item: (item.authorization or {}).get("_rank_key") or (
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


async def load_live_l3_rejections(
    db: AsyncSession,
    *,
    user_id: UUID,
    l3_watchlist_id: UUID,
) -> list[dict]:
    """Symbols live-active in this L3 profile's L2 parent but not authorized.

    The live complement of ``load_live_l3_candidates`` for a single L3
    watchlist: same symbol universe, minus whatever is currently part of the
    executable authorized population. Exists so the Rejected tab has a live
    source too, instead of only the periodic batch snapshot in
    ``pipeline_watchlist_rejections`` (which spot L3 is exempted from
    refreshing on every read for the same reason POOL/L1/L2 are not: cost).
    """
    statement = _l3_symbol_universe_statement(user_id=user_id, l3_watchlist_id=l3_watchlist_id)
    rows = (await db.execute(statement)).mappings().all()
    if not rows:
        return []
    from .l3_public_authorization import load_public_authorizations
    authorizations = await load_public_authorizations(db, user_id=user_id, candidates=rows)
    seen_symbols: set[str] = set()
    rejected: list[dict] = []
    for row in rows:
        symbol = str(row["symbol"]).upper()
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        if authorizations.get((row["watchlist_id"], row["symbol"])) is not None:
            continue
        rejected.append({
            "symbol": symbol,
            "profile_id": row["profile_id"],
            "watchlist_id": row["watchlist_id"],
        })
    return rejected

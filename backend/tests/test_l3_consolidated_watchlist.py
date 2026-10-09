from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import watchlists
from app.api.watchlists import list_l3_consolidated_assets, router
from app.services.pipeline_live_candidates import (
    ConsolidatedL3Candidate,
    LiveL3Contribution,
    load_live_l3_candidates,
    resolve_spot_pipeline_chain,
)


def test_l3_consolidated_static_route_precedes_dynamic_assets_route():
    paths = [route.path for route in router.routes]
    assert paths.index("/api/watchlists/l3-consolidated/assets") < paths.index(
        "/api/watchlists/{watchlist_id}/assets"
    )


@pytest.mark.asyncio
async def test_l3_consolidated_assets_exposes_only_current_watchlist_candidates(monkeypatch):
    refreshed_at = datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)
    winner = LiveL3Contribution(
        asset_id=uuid4(),
        watchlist_id=uuid4(),
        profile_id=uuid4(),
        profile_name="Winner Profile",
        symbol="BTC_USDT",
        alpha_score=87.5,
        current_price=123.45,
        refreshed_at=refreshed_at,
    )
    contributor = LiveL3Contribution(
        asset_id=uuid4(),
        watchlist_id=uuid4(),
        profile_id=uuid4(),
        profile_name="Profile B",
        symbol="BTC_USDT",
        alpha_score=81.0,
        current_price=123.45,
        refreshed_at=refreshed_at,
    )

    async def _load(db, *, user_id):
        return [
            ConsolidatedL3Candidate(
                symbol="BTC_USDT",
                winner=winner,
                contributors=(winner, contributor),
            )
        ]

    monkeypatch.setattr(watchlists, "load_live_l3_candidates", _load)
    response = await list_l3_consolidated_assets(user_id=uuid4(), db=object())

    assert response["semantic"] == "LIVE_L3_CANDIDATES"
    assert response["total"] == 1
    item = response["items"][0]
    assert item["profile_name"] == "Winner Profile"
    assert item["alpha_score"] == 87.5
    assert item["candidate_count"] == 2
    assert item["candidate_profile_names"] == ["Winner Profile", "Profile B"]
    assert "shadow_id" not in item
    assert "status" not in item
    assert "entry_price" not in item


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _StubSession:
    def __init__(self, result_rows):
        self._result_rows = iter(result_rows)

    async def execute(self, statement):
        return _ScalarResult(next(self._result_rows))


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _MappingSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, statement):
        self.statement = statement
        return _MappingResult(self._rows)


@pytest.mark.asyncio
async def test_child_snapshot_is_intersected_with_current_parent():
    parent_id = uuid4()
    user_id = uuid4()
    wl = SimpleNamespace(id=uuid4(), level="L3", source_watchlist_id=parent_id, user_id=user_id)
    keep = SimpleNamespace(symbol="BTC_USDT")
    stale = SimpleNamespace(symbol="SOL_USDT")
    parent_wl = SimpleNamespace(id=parent_id, level="L2", source_watchlist_id=None, user_id=user_id)
    parent_asset = SimpleNamespace(symbol="BTC_USDT")

    result = await watchlists._intersect_assets_with_active_parent(
        wl,
        [keep, stale],
        _StubSession([[parent_wl], [parent_asset]]),
    )

    assert result == [keep]


@pytest.mark.asyncio
async def test_live_candidates_consider_every_l3_and_pick_highest_score(monkeypatch):
    from app.services import l3_public_authorization
    async def authorities(db, *, user_id, candidates):
        return {(r["watchlist_id"], r["symbol"]): {
            "alpha_score": r["alpha_score"], "current_price": r["current_price"],
            "evaluated_at": "2026-09-16T16:00:00Z",
        } for r in candidates}
    monkeypatch.setattr(l3_public_authorization, "load_public_authorizations", authorities)
    symbol = "BTC_USDT"
    low_profile = uuid4()
    high_profile = uuid4()
    low_row = {
            "asset_id": uuid4(), "watchlist_id": uuid4(),
            "profile_id": low_profile, "profile_name": "Profile Low",
            "symbol": symbol, "alpha_score": 70, "current_price": 100,
            "refreshed_at": None,
        }
    db = _MappingSession([
        low_row,
        dict(low_row),  # duplicate produced by corrupt/repeated parent rows
        {
            "asset_id": uuid4(), "watchlist_id": uuid4(),
            "profile_id": high_profile, "profile_name": "Profile High",
            "symbol": symbol, "alpha_score": 90, "current_price": 100,
            "refreshed_at": None,
        },
    ])

    result = await load_live_l3_candidates(db, user_id=uuid4())

    assert len(result) == 1
    assert result[0].winner.profile_id == high_profile
    assert len(result[0].contributors) == 2


@pytest.mark.asyncio
async def test_chain_resolver_anchors_l1_to_pool_watchlist_and_returns_all_l3():
    pool_watchlist = SimpleNamespace(id=uuid4())
    l1_watchlist = SimpleNamespace(id=uuid4())
    l2_watchlist = SimpleNamespace(id=uuid4())
    l3_watchlists = [SimpleNamespace(id=uuid4()), SimpleNamespace(id=uuid4())]
    db = _StubSession([
        [pool_watchlist],
        [l1_watchlist],
        [l2_watchlist],
        l3_watchlists,
    ])

    chain = await resolve_spot_pipeline_chain(
        db,
        user_id=uuid4(),
        pool_id=uuid4(),
    )

    assert chain is not None
    assert chain.pool_watchlist is pool_watchlist
    assert chain.l1_watchlist is l1_watchlist
    assert chain.l2_watchlist is l2_watchlist
    assert chain.l3_watchlists == tuple(l3_watchlists)


def test_live_candidate_universe_does_not_read_shadow_trades():
    """The symbol UNIVERSE (what's live in L2/L1/POOL) must never depend on
    shadow_trades -- an open Shadow represents historical position follow-up,
    not a current opportunity. load_recently_authorized_l3_shadows is a
    deliberate, documented exception (2026-09-18 part 3: public-visibility
    floor) scoped to its own function, not the universe/candidate/rejection
    resolution -- see the module docstring."""
    import inspect
    from app.services import pipeline_live_candidates as m

    universe_source = "".join([
        inspect.getsource(m._l3_symbol_universe_statement),
        inspect.getsource(m.load_live_l3_candidates),
        inspect.getsource(m.load_live_l3_rejections),
    ]).lower()
    assert "shadowtrade" not in universe_source

    full_source = inspect.getsource(m).lower()
    # 2026-09-18 (part 3): the L3 symbol universe is anchored on the L2
    # asset, not the L3 watchlist's own (spot L3 never gets a
    # pipeline_watchlist_assets row written in the normal scan cycle, so
    # anchoring there made every caller of load_live_l3_candidates --
    # Approved, Consolidado, execute_buy, evaluate_signals -- structurally
    # always empty).
    assert "l1_asset.symbol == l2_asset.symbol" in full_source
    assert "pool_asset.symbol == l2_asset.symbol" in full_source
    assert "profile.is_active.is_(true)" in full_source

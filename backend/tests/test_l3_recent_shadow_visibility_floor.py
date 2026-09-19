"""2026-09-18 shadow-trade collapse fix, part 3: public-visibility floor.

Production case: ASTER_USDT (L3_DI_PLUS_START_V1) got an ALLOW + confirmed
Shadow at 19:44:06, but its authorization contract's tightest feature
(live_trade_flow taker_ratio/volume_delta, max_age_seconds=60) expired by
19:45:06 -- about 60 seconds later -- so it was visible in the Consolidado
feed for barely a minute. External systems polling that feed need a
guaranteed minimum window (5 minutes, per the user) to observe a listing.

load_recently_authorized_l3_shadows anchors on Shadow-creation time instead
of "is the latest decisions_log row still fresh", so it stays immune to a
routine re-evaluation minutes later landing on BLOCK/SUPPRESSED (normal --
conditions that trigger an entry don't have to persist) burying the
original authorization the way load_public_authorizations' "latest decision
only" lookup does.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.api import watchlists
from app.services import pipeline_live_candidates as m
from app.services.pipeline_live_candidates import ConsolidatedL3Candidate, LiveL3Contribution


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _RowsSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, statement):
        return _RowsResult(self._rows)


def _row(*, watchlist_id, symbol, decision_id=1, profile_name="L3_DI_PLUS_START_V1", profile_version=None):
    decision = type("D", (), {"id": decision_id, "symbol": symbol, "profile_id": uuid4()})()
    event = object()
    shadow = type("S", (), {"id": uuid4()})()
    return decision, event, shadow, watchlist_id, profile_name, profile_version


@pytest.mark.asyncio
async def test_zero_floor_never_queries_the_database():
    db = AsyncMock()
    result = await m.load_recently_authorized_l3_shadows(db, user_id=uuid4(), floor_seconds=0)
    assert result == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_deduplicates_by_watchlist_and_symbol(monkeypatch):
    wl = uuid4()
    rows = [_row(watchlist_id=wl, symbol="ASTER_USDT"), _row(watchlist_id=wl, symbol="ASTER_USDT")]
    db = _RowsSession(rows)

    def fake_auth(decision, event, shadow, *, watchlist_id, profile_version, now, ignore_expiry):
        assert ignore_expiry is True  # the whole point of this function
        return {"alpha_score": 50.0, "current_price": 1.0, "evaluated_at": now.isoformat(), "executable": True}

    import app.services.l3_public_authorization as auth_mod
    monkeypatch.setattr(auth_mod, "public_authorization", fake_auth)

    result = await m.load_recently_authorized_l3_shadows(db, user_id=uuid4(), floor_seconds=300)
    assert len(result) == 1
    assert result[0].symbol == "ASTER_USDT"


@pytest.mark.asyncio
async def test_excludes_rows_where_authorization_fails_every_other_check(monkeypatch):
    """ignore_expiry only skips the freshness upper bound -- every other
    correctness check (contract hash, lineage, outbox reconciliation) still
    applies via the real public_authorization, so a row that fails those
    must not surface here either."""
    wl = uuid4()
    rows = [_row(watchlist_id=wl, symbol="ASTER_USDT"), _row(watchlist_id=wl, symbol="ETH_USDT")]
    db = _RowsSession(rows)

    def fake_auth(decision, event, shadow, *, watchlist_id, profile_version, now, ignore_expiry):
        if decision.symbol == "ETH_USDT":
            return None  # e.g. hash mismatch, lineage mismatch, etc.
        return {"alpha_score": 50.0, "current_price": 1.0, "evaluated_at": now.isoformat(), "executable": True}

    import app.services.l3_public_authorization as auth_mod
    monkeypatch.setattr(auth_mod, "public_authorization", fake_auth)

    result = await m.load_recently_authorized_l3_shadows(db, user_id=uuid4(), floor_seconds=300)
    assert [c.symbol for c in result] == ["ASTER_USDT"]


@pytest.mark.asyncio
async def test_excludes_non_executable_results(monkeypatch):
    wl = uuid4()
    rows = [_row(watchlist_id=wl, symbol="ASTER_USDT")]
    db = _RowsSession(rows)

    def fake_auth(decision, event, shadow, *, watchlist_id, profile_version, now, ignore_expiry):
        return {"alpha_score": 50.0, "current_price": 1.0, "evaluated_at": now.isoformat(), "executable": False}

    import app.services.l3_public_authorization as auth_mod
    monkeypatch.setattr(auth_mod, "public_authorization", fake_auth)

    result = await m.load_recently_authorized_l3_shadows(db, user_id=uuid4(), floor_seconds=300)
    assert result == []


def test_l3_visible_until_computes_evaluated_plus_floor():
    evaluated = datetime(2026, 9, 18, 19, 44, 6, tzinfo=timezone.utc)
    auth = {"evaluated_at": evaluated.isoformat()}
    result = watchlists._l3_visible_until(auth, 300)
    assert result == watchlists._iso_utc(evaluated + timedelta(seconds=300))


@pytest.mark.parametrize("auth,floor_seconds", [
    ({"evaluated_at": None}, 300),
    ({}, 300),
    ({"evaluated_at": datetime.now(timezone.utc).isoformat()}, 0),
])
def test_l3_visible_until_returns_none_when_uncomputable(auth, floor_seconds):
    assert watchlists._l3_visible_until(auth, floor_seconds) is None


def _contribution(symbol, watchlist_id):
    return LiveL3Contribution(
        asset_id=uuid4(), watchlist_id=watchlist_id, profile_id=uuid4(),
        profile_name="P", symbol=symbol, alpha_score=50.0, current_price=1.0,
        refreshed_at=None, authorization={"evaluated_at": datetime.now(timezone.utc).isoformat()},
    )


@pytest.mark.asyncio
async def test_merge_recent_l3_shadows_adds_only_missing_symbols(monkeypatch):
    wl = uuid4()
    existing = _contribution("BTC_USDT", wl)
    candidates = [ConsolidatedL3Candidate(symbol="BTC_USDT", winner=existing, contributors=(existing,))]
    new_contribution = _contribution("ASTER_USDT", wl)

    async def fake_floor(db, user_id):
        return 300

    async def fake_recent(db, *, user_id, floor_seconds, l3_watchlist_id=None):
        return [existing, new_contribution]  # BTC_USDT already present, must not duplicate

    monkeypatch.setattr(watchlists, "_l3_public_visibility_floor_seconds", fake_floor)
    monkeypatch.setattr(
        "app.services.pipeline_live_candidates.load_recently_authorized_l3_shadows", fake_recent)

    merged, floor_seconds = await watchlists._merge_recent_l3_shadows(object(), uuid4(), candidates)

    assert floor_seconds == 300
    symbols = sorted(c.symbol for c in merged)
    assert symbols == ["ASTER_USDT", "BTC_USDT"]
    btc_candidate = next(c for c in merged if c.symbol == "BTC_USDT")
    assert len(btc_candidate.contributors) == 1  # not duplicated


@pytest.mark.asyncio
async def test_merge_recent_l3_shadows_fails_safe_on_error(monkeypatch):
    candidates = [ConsolidatedL3Candidate(
        symbol="BTC_USDT", winner=_contribution("BTC_USDT", uuid4()), contributors=())]

    async def fake_floor(db, user_id):
        return 300

    async def broken_recent(db, *, user_id, floor_seconds, l3_watchlist_id=None):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(watchlists, "_l3_public_visibility_floor_seconds", fake_floor)
    monkeypatch.setattr(
        "app.services.pipeline_live_candidates.load_recently_authorized_l3_shadows", broken_recent)

    merged, floor_seconds = await watchlists._merge_recent_l3_shadows(object(), uuid4(), candidates)

    assert merged is candidates  # untouched
    assert floor_seconds == 0  # no visible_until stamped on failure


def test_real_execution_paths_never_reference_the_visibility_floor():
    """execute_buy.py and evaluate_signals.py build their real-money/paper
    l3_symbols filter from load_live_l3_candidates -- the visibility floor
    must stay confined to the two display endpoints (Consolidado, per-
    watchlist Approved) and never leak into execution eligibility."""
    import inspect
    from app.tasks import evaluate_signals, execute_buy

    for module in (evaluate_signals, execute_buy):
        source = inspect.getsource(module)
        assert "load_recently_authorized_l3_shadows" not in source
        assert "ignore_expiry" not in source
        assert "l3_public_visibility_floor" not in source

"""2026-09-18 shadow-trade collapse fix, part 3.

Production evidence: spot L3's Approved tab could never render anything, and
its Rejected tab could silently miss symbols ("out of flow"), because two
independent things all anchored on ``pipeline_watchlist_assets`` for the L3
watchlist's own row -- and nothing writes that row in the normal scan cycle
for spot L3 (authorization flows through decisions_log/the outbox instead;
confirmed empty in production for every spot L3 watchlist).

  * ``load_live_l3_candidates``'s query used the L3 watchlist's own asset row
    as its SQL FROM anchor, so it structurally always returned zero rows --
    silently breaking every one of its callers: the Approved tab, the
    Consolidado page, and the real-money filters in execute_buy.py /
    evaluate_signals.py (live_trading_enabled is False in production today,
    so this had no live financial impact, but it was a real, load-bearing
    bug independent of that).
  * Even after fixing the query, ``get_watchlist_assets``'s L3-spot branch
    computed the live authority map correctly but then iterated over
    ``assets`` (that same always-empty table) instead of the live
    contributions -- so the loop body never ran either.
  * ``_get_watchlist_rejections_payload`` only reads the periodic batch
    snapshot (``pipeline_watchlist_rejections``), which spot L3 is exempted
    from refreshing on every read (same cost reasoning as POOL/L1/L2's
    opposite exemption) -- so a symbol live in L2 but not yet
    batch-refreshed showed in neither Approved nor Rejected.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import watchlists
from app.services.pipeline_live_candidates import load_live_l3_rejections


class _MappingResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows

    def fetchall(self):
        # Existing fixtures model non-radar watchlists.
        return []


class _FetchallResult:
    """Result shape for the plain ``text()`` held-symbols lookup -- distinct
    from ``_MappingResult`` (which backs the ORM-style symbol-universe
    query) since real code calls ``.fetchall()`` on it, not ``.mappings()``.
    """

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _MappingSession:
    def __init__(self, rows, held_rows=()):
        self._rows = rows
        self._held_rows = held_rows

    async def execute(self, statement, *args, **kwargs):
        if args or kwargs:
            # the held-symbols query is issued as text(...) with a params dict
            return _FetchallResult(self._held_rows)
        return _MappingResult(self._rows)


@pytest.mark.asyncio
async def test_live_rejections_excludes_authorized_symbols(monkeypatch):
    from app.services import l3_public_authorization

    watchlist_id = uuid4()
    profile_id = uuid4()

    async def authorities(db, *, user_id, candidates):
        # Only BTC_USDT clears authorization; ETH_USDT is live in L2 but
        # never got an executable L3 authorization (e.g. real BLOCK, or an
        # ALLOW whose contract already expired).
        return {
            (r["watchlist_id"], r["symbol"]): {"alpha_score": r["alpha_score"], "current_price": r["current_price"]}
            for r in candidates
            if r["symbol"] == "BTC_USDT"
        }

    monkeypatch.setattr(l3_public_authorization, "load_public_authorizations", authorities)

    db = _MappingSession([
        {"asset_id": uuid4(), "watchlist_id": watchlist_id, "profile_id": profile_id,
         "profile_name": "L3 Profile", "symbol": "BTC_USDT", "alpha_score": 90, "current_price": 100,
         "refreshed_at": None, "watchlist_filters": {}, "profile_version": None},
        {"asset_id": uuid4(), "watchlist_id": watchlist_id, "profile_id": profile_id,
         "profile_name": "L3 Profile", "symbol": "ETH_USDT", "alpha_score": 60, "current_price": 50,
         "refreshed_at": None, "watchlist_filters": {}, "profile_version": None},
    ])

    result = await load_live_l3_rejections(db, user_id=uuid4(), l3_watchlist_id=watchlist_id)

    symbols = {item["symbol"] for item in result}
    assert symbols == {"ETH_USDT"}


@pytest.mark.asyncio
async def test_live_rejections_empty_universe_returns_empty_list():
    db = _MappingSession([])
    result = await load_live_l3_rejections(db, user_id=uuid4(), l3_watchlist_id=uuid4())
    assert result == []


@pytest.mark.asyncio
async def test_live_rejections_include_symbols_with_open_shadow(monkeypatch):
    """2026-10-05: classification is independent of open Shadows. The former
    open-position exclusion (2026-09-27) hid held symbols whose authorization
    had lapsed from BOTH tabs (BTC_USDT/TAO_USDT live in RealtimeL2, absent
    from RealtimeL3 Approved and Rejected). A held symbol whose current
    evaluation does not authorize it is Rejected."""
    from app.services import l3_public_authorization

    watchlist_id = uuid4()
    profile_id = uuid4()

    async def authorities(db, *, user_id, candidates):
        # Neither symbol clears a fresh authorization this cycle.
        return {}

    monkeypatch.setattr(l3_public_authorization, "load_public_authorizations", authorities)

    db = _MappingSession(
        [
            {"asset_id": uuid4(), "watchlist_id": watchlist_id, "profile_id": profile_id,
             "profile_name": "L3 Profile", "symbol": "ONDO_USDT", "alpha_score": 45, "current_price": 1,
             "refreshed_at": None, "watchlist_filters": {}, "profile_version": None},
            {"asset_id": uuid4(), "watchlist_id": watchlist_id, "profile_id": profile_id,
             "profile_name": "L3 Profile", "symbol": "ETH_USDT", "alpha_score": 60, "current_price": 50,
             "refreshed_at": None, "watchlist_filters": {}, "profile_version": None},
        ],
        held_rows=[SimpleNamespace(symbol="ONDO_USDT")],
    )

    result = await load_live_l3_rejections(db, user_id=uuid4(), l3_watchlist_id=watchlist_id)

    symbols = {item["symbol"] for item in result}
    assert symbols == {"ONDO_USDT", "ETH_USDT"}


@pytest.mark.asyncio
async def test_rejections_payload_excludes_symbols_shown_in_approved(monkeypatch):
    """Approved and Rejected are mutually exclusive: a symbol in Approved
    (live authorization or the recent-shadow visibility floor) is dropped
    from Rejected, including stale persisted rows."""
    wl = SimpleNamespace(
        id=uuid4(), level="L3", market_mode="spot", profile_id=uuid4(),
        source_pool_id=None, auto_refresh=True, user_id=uuid4(),
    )

    async def _fake_profile_config(_wl, _db):
        return {}

    async def _empty(*_a, **_k):
        return []

    async def _floor(_db, _user_id):
        return 300

    async def _fake_rejections(_db, *, user_id, l3_watchlist_id):
        return [{"symbol": s, "profile_id": wl.profile_id, "watchlist_id": wl.id}
                for s in ("ETH_USDT", "BTC_USDT", "SOL_USDT")]

    async def _fake_live_candidates(_db, *, user_id):
        item = SimpleNamespace(symbol="BTC_USDT", watchlist_id=wl.id)
        return [SimpleNamespace(contributors=(item,))]

    async def _fake_recent(_db, *, user_id, floor_seconds, l3_watchlist_id):
        return [SimpleNamespace(symbol="SOL_USDT", watchlist_id=wl.id)]

    persisted = SimpleNamespace(
        symbol="BTC_USDT", profile_id=wl.profile_id, recorded_at=datetime.now(timezone.utc),
        analysis_snapshot=None, evaluation_trace=None, stage="L3", failed_type="block_rule",
        failed_indicator="RSI", condition_text=None, current_value=None, expected_value=None,
    )

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

        def fetchall(self):
            return []

    class _NullAsyncCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _Db:
        def __init__(self):
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            return _Rows([persisted] if self.calls == 1 else [])

        def begin_nested(self):
            return _NullAsyncCtx()

    monkeypatch.setattr(watchlists, "_load_watchlist_profile_config", _fake_profile_config)
    monkeypatch.setattr(watchlists, "_load_active_watchlist_assets", _empty)
    monkeypatch.setattr(watchlists, "_load_user_score_rules", _empty)
    monkeypatch.setattr(watchlists, "_fetch_indicators_map", lambda *a, **k: _empty_map())
    monkeypatch.setattr(watchlists, "_l3_public_visibility_floor_seconds", _floor)
    monkeypatch.setattr(watchlists, "_intersect_assets_with_active_parent", lambda _wl, rows, _db: _identity(rows))
    monkeypatch.setattr("app.services.pipeline_live_candidates.load_live_l3_rejections", _fake_rejections)
    monkeypatch.setattr("app.services.pipeline_live_candidates.load_live_l3_candidates", _fake_live_candidates)
    monkeypatch.setattr("app.services.pipeline_live_candidates.load_recently_authorized_l3_shadows", _fake_recent)

    async def _fake_classify(_db, *, user_id, l3_watchlist_id, display_floor_seconds=None):
        assert display_floor_seconds == 300
        approved = [SimpleNamespace(symbol="BTC_USDT", watchlist_id=wl.id),
                    SimpleNamespace(symbol="SOL_USDT", watchlist_id=wl.id)]
        return approved, [{"symbol": "ETH_USDT", "profile_id": wl.profile_id, "watchlist_id": wl.id}]

    monkeypatch.setattr("app.services.pipeline_live_candidates.classify_live_l3_for_display", _fake_classify)

    payload = await watchlists._get_watchlist_rejections_payload(wl, uuid4(), _Db())

    assert {item["symbol"] for item in payload["items"]} == {"ETH_USDT"}
    assert payload["metrics"]["approved_count"] == 2


async def _empty_map():
    return {}


async def _identity(rows):
    return rows


def test_get_watchlist_assets_l3_branch_iterates_live_contributions_not_stale_table():
    """Regression test for the loop-gating bug: the branch must iterate the
    live ``contributions`` list, never ``assets`` (this watchlist's own
    ``pipeline_watchlist_assets`` rows, which spot L3 never populates)."""
    import inspect

    source = inspect.getsource(watchlists.get_watchlist_assets)
    branch_idx = source.index('effective_level == "L3" and getattr(wl, "market_mode", "spot") == "spot"')
    branch = source[branch_idx:source.index("return {", branch_idx)]

    assert "for contribution in contributions:" in branch
    assert "for asset in assets:" not in branch


@pytest.mark.asyncio
async def test_rejections_payload_fills_gap_with_live_complement_for_l3_spot(monkeypatch):
    """The Rejected payload must merge in live-only symbols for spot L3
    without duplicating anything already persisted."""
    wl = SimpleNamespace(
        id=uuid4(), level="L3", market_mode="spot", profile_id=uuid4(),
        source_pool_id=None, source_watchlist_id=None, auto_refresh=True, user_id=uuid4(),
    )
    user_id = uuid4()

    async def _no_floor(_db, _user_id):
        return 0

    monkeypatch.setattr(watchlists, "_l3_public_visibility_floor_seconds", _no_floor)

    async def _fake_profile_config(_wl, _db):
        return {}

    async def _fake_active_assets(_watchlist_id, _db):
        return []

    async def _fake_score_rules(_db, _user_id):
        return []

    async def _fake_indicators_map(_db, _symbols, include_stale=True):
        return {}

    async def _fake_rejections(_db, *, user_id, l3_watchlist_id):
        return [
            {"symbol": "ETH_USDT", "profile_id": wl.profile_id, "watchlist_id": wl.id},
            {"symbol": "BTC_USDT", "profile_id": wl.profile_id, "watchlist_id": wl.id},
        ]

    async def _fake_live_candidates(_db, *, user_id):
        return []

    class _EmptyScalars:
        def scalars(self):
            return self

        def all(self):
            return []

        def fetchall(self):
            return []

    class _NullAsyncCtx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _NoopDb:
        async def execute(self, *args, **kwargs):
            return _EmptyScalars()

        def begin_nested(self):
            return _NullAsyncCtx()

    monkeypatch.setattr(watchlists, "_load_watchlist_profile_config", _fake_profile_config)
    monkeypatch.setattr(watchlists, "_load_active_watchlist_assets", _fake_active_assets)
    monkeypatch.setattr(watchlists, "_load_user_score_rules", _fake_score_rules)
    monkeypatch.setattr(watchlists, "_fetch_indicators_map", _fake_indicators_map)
    monkeypatch.setattr(
        "app.services.pipeline_live_candidates.load_live_l3_rejections", _fake_rejections
    )
    monkeypatch.setattr(
        "app.services.pipeline_live_candidates.load_live_l3_candidates", _fake_live_candidates
    )

    async def _fake_classify(_db, *, user_id, l3_watchlist_id, display_floor_seconds=None):
        return [], await _fake_rejections(_db, user_id=user_id, l3_watchlist_id=l3_watchlist_id)

    monkeypatch.setattr(
        "app.services.pipeline_live_candidates.classify_live_l3_for_display", _fake_classify
    )

    payload = await watchlists._get_watchlist_rejections_payload(wl, user_id, _NoopDb())

    symbols = {item["symbol"] for item in payload["items"]}
    assert symbols == {"ETH_USDT", "BTC_USDT"}
    assert payload["metrics"]["approved_count"] == 0

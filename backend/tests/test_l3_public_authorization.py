from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as Obj
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services.l3_authorization_contract_v3 import canonical_hash
from app.services.l3_public_authorization import (
    authorization_expiry, public_authorization, load_public_authorizations,
)


def objects():
    now = datetime.now(timezone.utc)
    profile, watchlist = uuid4(), uuid4()
    body = {
        "valid": True, "authorization_status": "ALLOW", "mode": "SHADOW",
        "final_decision": "ALLOW", "technical_decision": "ALLOW",
        "contract_technical_decision": "ALLOW", "evaluated_at": now.isoformat(),
        "lineage": {"profile_id": str(profile), "watchlist_id": str(watchlist),
                    "watchlist_level": "L3", "profile_version": now.isoformat()},
        "feature_evaluations": [{"indicator": "taker_ratio", "status": "PASS",
            "max_age_seconds": 300, "resolved_feature": {
                "age_seconds": 20, "source_timestamp": (now - timedelta(seconds=20)).isoformat()}}],
    }
    body["authorization_contract_hash"] = canonical_hash(body)
    decision = Obj(id=1, symbol="LIT_USDT", profile_id=profile, decision="ALLOW", score=64.6,
                   metrics={"l3_authorization_contract_v3": body, "price": 1.2})
    event = Obj(authorization_contract_hash=body["authorization_contract_hash"],
                status="PENDING", payload={"shadow_creation_required": True}, last_error=None)
    shadow = None  # no confirmed Shadow by default — most fixtures exercise PENDING/RETRY
    return now, watchlist, decision, event, shadow, body


def test_lit_allow_requires_transactional_outbox():
    now, wl, decision, event, shadow, _ = objects()
    assert public_authorization(decision, None, shadow, watchlist_id=wl, now=now) is None
    auth = public_authorization(decision, event, shadow, watchlist_id=wl, now=now)
    assert auth["decision_id"] == decision.id
    assert auth["shadow_status"] == "PENDING"
    assert auth["shadow_id"] is None
    assert auth["executable"] is False


def test_uni_membership_never_overrides_block_or_expired_contract():
    now, wl, decision, event, shadow, body = objects()
    decision.decision = "BLOCK"
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now) is None
    decision.decision = "ALLOW"
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now + timedelta(seconds=280)) is None
    body["valid"] = False
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now) is None


def test_hash_profile_version_and_shadow_suppression_fail_closed():
    now, wl, decision, event, shadow, body = objects()
    assert public_authorization(decision, event, shadow, watchlist_id=uuid4(), now=now) is None
    assert public_authorization(decision, event, shadow, watchlist_id=wl,
                                profile_version=now - timedelta(seconds=1), now=now) is None
    event.status = "PROCESSED"
    event.payload["processing_result"] = "SUPPRESSED/SAME_SYMBOL_LOWER_PRIORITY"
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now) is None
    event.payload["processing_result"] = "CREATED_OR_RECONCILED"
    # S0.3: CREATED_OR_RECONCILED alone is not enough — without an actual
    # confirmed Shadow row, the opportunity is STARTED-by-outbox-result but
    # still not executable (defensive: a stale/mismatched join must never
    # authorize execution just because the processing_result string matches).
    auth_without_shadow = public_authorization(decision, event, None, watchlist_id=wl, now=now)
    assert auth_without_shadow["shadow_status"] == "STARTED"
    assert auth_without_shadow["executable"] is False
    shadow = Obj(id=uuid4())
    auth = public_authorization(decision, event, shadow, watchlist_id=wl, now=now)
    assert auth["shadow_status"] == "STARTED"
    assert auth["shadow_id"] == str(shadow.id)
    assert auth["executable"] is True
    body["feature_evaluations"][0]["max_age_seconds"] = 999999
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now) is None


def test_comparison_operands_use_shortest_remaining_lifetime():
    now, _, _, _, _, body = objects()
    body["feature_evaluations"].append({"resolved_operands": {"left": {
        "max_age_seconds": 60, "resolved_feature": {"age_seconds": 50}}}})
    assert authorization_expiry(body) == now + timedelta(seconds=10)
    body["feature_evaluations"][-1]["resolved_operands"]["left"].pop("max_age_seconds")
    assert authorization_expiry(body) is None


def test_ignore_expiry_is_for_the_public_visibility_floor_only():
    """2026-09-18 (part 3): ignore_expiry must default to False (every
    existing caller -- shadow creation, consolidation, outbox, execute_buy's
    l3_symbols filter -- keeps the exact same strict TTL behavior), and even
    when explicitly set True it must still refuse a nonsensical
    evaluated-in-the-future contract."""
    now, wl, decision, event, shadow, body = objects()
    event.status = "PROCESSED"
    event.payload["processing_result"] = "CREATED_OR_RECONCILED"
    shadow = Obj(id=uuid4())
    past_expiry = now + timedelta(seconds=280)  # feature TTL: 300 - age(20) = 280s

    # Default (and explicit False) behavior is byte-for-byte unchanged.
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=past_expiry) is None
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=past_expiry,
                                ignore_expiry=False) is None

    # ignore_expiry=True is the only thing that changes this outcome.
    auth = public_authorization(decision, event, shadow, watchlist_id=wl, now=past_expiry,
                                ignore_expiry=True)
    assert auth is not None
    assert auth["executable"] is True

    # A future-dated evaluated_at is never valid, regardless of the flag --
    # ignore_expiry only skips the UPPER bound, never the sanity check.
    assert public_authorization(decision, event, shadow, watchlist_id=wl, now=now - timedelta(seconds=1),
                                ignore_expiry=True) is None


def _no_pool_ancestry():
    """A resolve_root_pool_ids() DB response with no pool-linked watchlist —
    the common case for every fixture here (fake/standalone watchlist ids)."""
    return Obj(fetchall=lambda: [])


@pytest.mark.asyncio
async def test_latest_block_is_not_filtered_out_before_latest_decision_selection():
    now, wl, decision, event, shadow, body = objects()
    decision.decision = "BLOCK"
    db = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, shadow)]),
        _no_pool_ancestry(),
    ]))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=[{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl}])
    assert result == {}
    main_query = db.execute.call_args_list[0].args[0]
    sql = str(main_query.compile(dialect=postgresql.dialect()))
    assert "DISTINCT ON" in sql and "created_at DESC" in sql
    assert "decision =" not in sql
    # 2026-09-21: PROFILE_CONSOLIDATION rows are pure audit records with no
    # contract -- excluded from "latest" so a real ALLOW decision is never
    # masked by its own later suppression audit row.
    sql_literal = str(main_query.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "event_type != 'PROFILE_CONSOLIDATION'" in sql_literal


@pytest.mark.asyncio
async def test_load_public_authorizations_excludes_pending_and_includes_started():
    """S0.3: the population this feeds (the executable API list) must never
    include a PENDING/RETRY opportunity just because its contract is valid
    and unexpired — only a confirmed Shadow makes it in.
    """
    now, wl, decision, event, _shadow, body = objects()
    candidates = [{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl,
    }]

    # No Shadow yet (still PENDING) -> excluded entirely.
    db_pending = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, None)]),
        _no_pool_ancestry(),
    ]))
    result_pending = await load_public_authorizations(db_pending, user_id=uuid4(), candidates=candidates)
    assert result_pending == {}

    # Confirmed Shadow -> included, with executable=True and the real shadow_id.
    event.status, event.payload["processing_result"] = "PROCESSED", "CREATED_OR_RECONCILED"
    confirmed_shadow = Obj(id=uuid4())
    db_started = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, confirmed_shadow)]),
        _no_pool_ancestry(),
    ]))
    result_started = await load_public_authorizations(db_started, user_id=uuid4(), candidates=candidates)
    entry = result_started[(wl, decision.symbol)]
    assert entry["executable"] is True
    assert entry["shadow_id"] == str(confirmed_shadow.id)


def test_active_trade_already_exists_is_executable_with_a_shadow():
    """2026-09-21: a symbol suppressed only because a Shadow already covers
    this (symbol, direction) must stay executable -- external consumers run
    their own execution tracking and must not lose a live opportunity just
    because Scalpyn's own single-Shadow bookkeeping already has a position."""
    now, wl, decision, event, _shadow, body = objects()
    event.status = "PROCESSED"
    event.payload["processing_result"] = "SUPPRESSED/ACTIVE_TRADE_ALREADY_EXISTS"

    # No shadow resolved (e.g. it closed between queries) -- fail closed, same
    # as any other PROCESSED-but-not-CREATED_OR_RECONCILED outcome.
    assert public_authorization(decision, event, None, watchlist_id=wl, now=now) is None

    covering_shadow = Obj(id=uuid4())
    auth = public_authorization(decision, event, covering_shadow, watchlist_id=wl, now=now)
    assert auth is not None
    assert auth["shadow_status"] == "STARTED"
    assert auth["shadow_id"] == str(covering_shadow.id)
    assert auth["executable"] is True
    assert auth["shadow_reason"] == "SUPPRESSED/ACTIVE_TRADE_ALREADY_EXISTS"


def test_other_suppression_reasons_stay_non_executable_even_with_a_shadow():
    """Only ACTIVE_TRADE_ALREADY_EXISTS gets this treatment -- a same-candle
    lower-priority candidate, a rate-limited capture, or an expired-after-lock
    contract must never become executable, even if some unrelated Shadow for
    the same (symbol, direction) happens to exist."""
    now, wl, decision, event, _shadow, body = objects()
    event.status = "PROCESSED"
    unrelated_shadow = Obj(id=uuid4())
    for reason in (
        "SUPPRESSED/SAME_SYMBOL_LOWER_PRIORITY",
        "SUPPRESSED/REJECTED_CAPTURE_RATE_LIMIT",
        "SUPPRESSED/AUTHORIZATION_EXPIRED_AFTER_LOCK",
    ):
        event.payload["processing_result"] = reason
        assert public_authorization(
            decision, event, unrelated_shadow, watchlist_id=wl, now=now
        ) is None


@pytest.mark.asyncio
async def test_load_public_authorizations_resolves_active_trade_shadow_by_symbol():
    """The DB join matches Shadow.decision_id == DecisionLog.id, which is
    always NULL for an ACTIVE_TRADE_ALREADY_EXISTS decision (the covering
    Shadow belongs to an earlier decision) -- load_public_authorizations must
    fall back to a (symbol, direction) lookup instead of leaving it excluded.
    """
    now, wl, decision, event, _shadow, body = objects()
    event.status = "PROCESSED"
    event.payload["processing_result"] = "SUPPRESSED/ACTIVE_TRADE_ALREADY_EXISTS"
    candidates = [{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl,
    }]
    covering_shadow = Obj(id=uuid4(), symbol=decision.symbol, direction="SPOT")
    decision.direction = None  # defaults to SPOT on both sides of the lookup

    db = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, None)]),
        Obj(scalars=lambda: Obj(all=lambda: [covering_shadow])),
        _no_pool_ancestry(),
    ]))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=candidates)
    entry = result[(wl, decision.symbol)]
    assert entry["executable"] is True
    assert entry["shadow_id"] == str(covering_shadow.id)
    # Second call only happens because a lookup key existed -- assert it ran.
    # Third is the (always-on) pool-ancestry resolution.
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_load_public_authorizations_skips_no_active_shadow_lookup_when_unneeded():
    """The common CREATED_OR_RECONCILED path (a real confirmed Shadow already
    joined) must not pay for the extra ACTIVE_TRADE fallback query. It still
    pays for the (always-on) pool-ancestry resolution -- 2 calls total, not 3."""
    now, wl, decision, event, _shadow, body = objects()
    event.status, event.payload["processing_result"] = "PROCESSED", "CREATED_OR_RECONCILED"
    confirmed_shadow = Obj(id=uuid4())
    candidates = [{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl,
    }]
    db = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, confirmed_shadow)]),
        _no_pool_ancestry(),
    ]))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=candidates)
    assert result[(wl, decision.symbol)]["executable"] is True
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_load_public_authorizations_excludes_symbol_removed_from_its_pool():
    """2026-09-23: a symbol the operator (or radar_auto_discover) already
    removed from its source pool must stop being executable immediately,
    even though its authorization contract is still perfectly valid and
    unexpired -- the pool membership check is an independent, earlier gate."""
    now, wl, decision, event, _shadow, body = objects()
    event.status, event.payload["processing_result"] = "PROCESSED", "CREATED_OR_RECONCILED"
    confirmed_shadow = Obj(id=uuid4())
    pool_id = uuid4()
    candidates = [{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl,
    }]
    db = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, confirmed_shadow)]),
        Obj(fetchall=lambda: [Obj(start_id=wl, source_pool_id=pool_id)]),
        # pool_coins currently active for this pool: does NOT include decision.symbol.
        Obj(fetchall=lambda: [Obj(pool_id=pool_id, symbol="SOME_OTHER_USDT")]),
    ]))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=candidates)
    assert result == {}


@pytest.mark.asyncio
async def test_load_public_authorizations_keeps_symbol_still_in_its_pool():
    """Same setup as above, but the symbol IS still an active pool_coins row
    -- the pool-membership gate must not exclude a genuinely current symbol."""
    now, wl, decision, event, _shadow, body = objects()
    event.status, event.payload["processing_result"] = "PROCESSED", "CREATED_OR_RECONCILED"
    confirmed_shadow = Obj(id=uuid4())
    pool_id = uuid4()
    candidates = [{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl,
    }]
    db = Obj(execute=AsyncMock(side_effect=[
        Obj(all=lambda: [(decision, event, confirmed_shadow)]),
        Obj(fetchall=lambda: [Obj(start_id=wl, source_pool_id=pool_id)]),
        Obj(fetchall=lambda: [Obj(pool_id=pool_id, symbol=decision.symbol)]),
    ]))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=candidates)
    assert result[(wl, decision.symbol)]["executable"] is True


@pytest.mark.asyncio
async def test_l3_card_count_excludes_raw_membership_without_authorization(monkeypatch):
    from app.api import watchlists
    l3 = Obj(id=uuid4(), level="L3", market_mode="spot", profile_id=None, source_watchlist_id=None)
    l1 = Obj(id=uuid4(), level="L1", market_mode="spot", profile_id=None, source_watchlist_id=None)
    db = Obj(execute=AsyncMock(side_effect=[
        Obj(scalars=lambda: Obj(all=lambda: [l3, l1])),
        Obj(fetchall=lambda: [Obj(watchlist_id=l3.id, cnt=2), Obj(watchlist_id=l1.id, cnt=2)]),
    ]))
    monkeypatch.setattr(watchlists, "load_live_l3_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(watchlists, "_wl_to_dict", lambda w, **kwargs: {"id": str(w.id), "level": w.level})
    result = await watchlists.list_watchlists(order_by="created_at", user_id=uuid4(), db=db)
    assert result["watchlists"][0]["asset_count"] == 0
    assert result["watchlists"][1]["asset_count"] == 2


@pytest.mark.asyncio
async def test_empty_l3_get_never_runs_a_producer_or_commits(monkeypatch):
    from fastapi import BackgroundTasks, Response
    from app.api import watchlists
    wl = Obj(id=uuid4(), level="L3", market_mode="spot", auto_refresh=True,
             profile_id=uuid4(), source_pool_id=None)
    db = Obj(execute=AsyncMock(return_value=Obj(scalars=lambda: Obj(first=lambda: wl))))
    producer = AsyncMock(side_effect=AssertionError("GET must not evaluate or write"))
    monkeypatch.setattr(watchlists, "_load_watchlist_profile_config", AsyncMock(return_value={}))
    monkeypatch.setattr(watchlists, "_load_active_watchlist_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(watchlists, "_intersect_assets_with_active_parent", AsyncMock(return_value=[]))
    monkeypatch.setattr(watchlists, "load_live_l3_candidates", AsyncMock(return_value=[]))
    monkeypatch.setattr(watchlists, "_auto_refresh_watchlist_assets_if_needed", producer)
    tasks, response = BackgroundTasks(), Response()
    result = await watchlists.get_watchlist_assets(watchlist_id=wl.id, background_tasks=tasks,
        user_id=uuid4(), db=db, response=response)
    assert result["total"] == 0 and result["authorization_contract"] == "L3_PUBLIC_AUTHORIZATION_V1"
    assert response.headers["cache-control"] == "private, no-store"
    producer.assert_not_called()
    assert not tasks.tasks


@pytest.mark.asyncio
async def test_on_demand_persists_decision_outbox_and_membership_atomically(monkeypatch):
    from app.models.backoffice import DecisionLog, L3AuthorizationOutbox
    from app.models.pipeline_watchlist import PipelineWatchlistAsset
    from app.services import l3_watchlist_publication as publication
    from app.services import l3_gate_evaluation_store

    now, wl_id, decision, event, _shadow, contract = objects()
    profile = Obj(id=decision.profile_id, name="L3_TEST", config={}, profile_version=now)
    wl = Obj(id=wl_id, profile_id=profile.id, last_scanned_at=None)
    old_uni = PipelineWatchlistAsset(watchlist_id=wl_id, symbol="UNI_USDT", level_direction="up")
    new_decision = {"symbol": "LIT_USDT", "strategy": "test", "direction": "LONG",
                    "score": 64.6, "decision": "ALLOW", "created_at": now,
                    "metrics": {**decision.metrics, "l3_gate_v2": {"present": True}}}

    class DB:
        def __init__(self): self.added = []; self.commits = 0
        async def execute(self, statement):
            if "FROM profiles" in str(statement):
                return Obj(scalar_one_or_none=lambda: profile)
            if "FROM pipeline_watchlist_assets" in str(statement):
                return Obj(scalars=lambda: Obj(all=lambda: [old_uni]))
            return Obj(fetchall=lambda: [])
        def add_all(self, rows): self.added.extend(rows)
        def add(self, row): self.added.append(row)
        async def flush(self):
            for index, row in enumerate(self.added):
                if isinstance(row, DecisionLog): row.id = index + 1
        async def commit(self):
            assert any(isinstance(row, DecisionLog) for row in self.added)
            assert any(isinstance(row, L3AuthorizationOutbox) for row in self.added)
            assert any(isinstance(row, PipelineWatchlistAsset) for row in self.added)
            self.commits += 1
        def begin_nested(self):
            class Context:
                async def __aenter__(self): return self
                async def __aexit__(self, *args): pass
            return Context()

    monkeypatch.setattr(publication, "evaluate_on_demand_l3", AsyncMock(return_value=[new_decision]))
    monkeypatch.setattr(publication.config_service, "get_config", AsyncMock(return_value={}))
    monkeypatch.setattr(l3_gate_evaluation_store, "link_decision_evaluation", AsyncMock())
    db = DB()
    result = await publication.publish_l3_watchlist(db, user_id=uuid4(), watchlist=wl, symbols=["UNI_USDT", "LIT_USDT"])
    assert [row["symbol"] for row in result] == ["LIT_USDT"]
    assert old_uni.level_direction == "down"
    assert db.commits == 1
    outbox = next(row for row in db.added if isinstance(row, L3AuthorizationOutbox))
    assert outbox.payload["shadow_creation_required"] or outbox.payload["consolidation_required"]
    assert outbox.decision_id == result[0]["analysis_snapshot"]["decision_id"]

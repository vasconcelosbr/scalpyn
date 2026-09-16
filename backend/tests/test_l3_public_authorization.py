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
    return now, watchlist, decision, event, body


def test_lit_allow_requires_transactional_outbox():
    now, wl, decision, event, _ = objects()
    assert public_authorization(decision, None, watchlist_id=wl, now=now) is None
    auth = public_authorization(decision, event, watchlist_id=wl, now=now)
    assert auth["decision_id"] == decision.id
    assert auth["shadow_status"] == "PENDING"


def test_uni_membership_never_overrides_block_or_expired_contract():
    now, wl, decision, event, body = objects()
    decision.decision = "BLOCK"
    assert public_authorization(decision, event, watchlist_id=wl, now=now) is None
    decision.decision = "ALLOW"
    assert public_authorization(decision, event, watchlist_id=wl, now=now + timedelta(seconds=280)) is None
    body["valid"] = False
    assert public_authorization(decision, event, watchlist_id=wl, now=now) is None


def test_hash_profile_version_and_shadow_suppression_fail_closed():
    now, wl, decision, event, body = objects()
    assert public_authorization(decision, event, watchlist_id=uuid4(), now=now) is None
    assert public_authorization(decision, event, watchlist_id=wl,
                                profile_version=now - timedelta(seconds=1), now=now) is None
    event.status = "PROCESSED"
    event.payload["processing_result"] = "SUPPRESSED/SAME_SYMBOL_LOWER_PRIORITY"
    assert public_authorization(decision, event, watchlist_id=wl, now=now) is None
    event.payload["processing_result"] = "CREATED_OR_RECONCILED"
    assert public_authorization(decision, event, watchlist_id=wl, now=now)["shadow_status"] == "STARTED"
    body["feature_evaluations"][0]["max_age_seconds"] = 999999
    assert public_authorization(decision, event, watchlist_id=wl, now=now) is None


def test_comparison_operands_use_shortest_remaining_lifetime():
    now, _, _, _, body = objects()
    body["feature_evaluations"].append({"resolved_operands": {"left": {
        "max_age_seconds": 60, "resolved_feature": {"age_seconds": 50}}}})
    assert authorization_expiry(body) == now + timedelta(seconds=10)
    body["feature_evaluations"][-1]["resolved_operands"]["left"].pop("max_age_seconds")
    assert authorization_expiry(body) is None


@pytest.mark.asyncio
async def test_latest_block_is_not_filtered_out_before_latest_decision_selection():
    now, wl, decision, event, body = objects()
    decision.decision = "BLOCK"
    db = Obj(execute=AsyncMock(return_value=Obj(all=lambda: [(decision, event)])))
    result = await load_public_authorizations(db, user_id=uuid4(), candidates=[{
        "profile_id": decision.profile_id, "symbol": decision.symbol, "watchlist_id": wl}])
    assert result == {}
    sql = str(db.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "DISTINCT ON" in sql and "created_at DESC" in sql
    assert "decision =" not in sql


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
async def test_on_demand_persists_decision_outbox_and_membership_atomically(monkeypatch):
    from app.models.backoffice import DecisionLog, L3AuthorizationOutbox
    from app.models.pipeline_watchlist import PipelineWatchlistAsset
    from app.services import l3_watchlist_publication as publication
    from app.services import l3_gate_evaluation_store

    now, wl_id, decision, event, contract = objects()
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

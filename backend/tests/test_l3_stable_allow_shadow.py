"""Exercise the scheduled scan's actual routing block, then its outbox boundary.

The scan owns Redis/DB/exchange orchestration. Compile its synchronous routing
prefix directly so the regression exercises production ordering without running
those unrelated side effects or maintaining a copied version of the algorithm.
"""
import ast
import logging
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.tasks import pipeline_scan as scan
from app.services.l3_authorization_contract_v3 import canonical_hash, contract_authorizes_shadow_capture
from app.services.l3_public_authorization import public_authorization
from app.models.backoffice import DecisionLog, L3AuthorizationOutbox


def scheduled_route(decision, *, consolidate=False):
    tree = ast.parse(Path(scan.__file__).read_text(encoding="utf-8"))
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)
             and isinstance(node.target, ast.Name) and node.target.id == "d"
             and any(isinstance(n, ast.Name) and n.id == "prior_states"
                     for n in ast.walk(node))]
    loop = next(node for node in loops if any(
        isinstance(n, ast.Name) and n.id == "_contract_shadow_eligible"
        for n in ast.walk(node)))
    body = []
    for stmt in loop.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
            and t.value.id == "new_states" for t in stmt.targets):
            break
        body.append(stmt)
    prior = {"state": "ALLOW", "score": decision["score"],
             "direction": decision["direction"], "db_confirmed_at": "confirmed"}
    env = dict(vars(scan), d=decision, prior_states={decision["symbol"]: prior},
               prior_visibility={decision["symbol"]}, current_l3_visibility=set(),
               dl_score_delta=5, dl_direction_logs=True, wl_id="test",
               _wl_consolidation_enabled=consolidate, execution_id="test-scan",
               _wl_spot_cfg=Obj(scanner=Obj(l3_profile_consolidation_rule_version="test")),
               _wl_buy_threshold=None, _wl_strong_buy_threshold=None,
               _ml_gate_enabled=False, logger=logging.getLogger(__name__),
               contract_authorizes_shadow_capture=contract_authorizes_shadow_capture)
    exec(compile(ast.Module(body=body, type_ignores=[]), scan.__file__, "exec"), env)
    return env["should_log"], env["event_type"]


def decision_fixture():
    now = datetime.now(timezone.utc)
    profile, watchlist = uuid4(), uuid4()
    rules = {"block_rules": {"blocks": []}}
    contract = {"valid": True, "authorization_status": "ALLOW", "mode": "SHADOW",
        "operational_effect": False, "technical_decision": "ALLOW", "final_decision": "ALLOW",
        "contract_technical_decision": "ALLOW", "evaluated_at": now.isoformat(),
        "lineage": {"profile_id": str(profile), "watchlist_id": str(watchlist),
            "watchlist_level": "L3", "profile_version": now.isoformat(), "rules_snapshot": rules},
        "feature_evaluations": [{"indicator": "rsi", "status": "PASS", "max_age_seconds": 360,
            "resolved_feature": {"age_seconds": 20,
                "source_timestamp": (now - timedelta(seconds=20)).isoformat()}}]}
    contract["authorization_contract_hash"] = canonical_hash(contract)
    return {"symbol": "SOL_USDT", "strategy": "test", "direction": "SPOT", "score": 70,
        "decision": "ALLOW", "created_at": now, "_profile_id": profile,
        "_profile_version": now, "metrics": {"l3_authorization_contract_v3": contract}}


class DB:
    def __init__(self): self.added = []
    async def execute(self, statement): return Obj(fetchall=lambda: [])
    def add_all(self, rows): self.added.extend(rows)
    def add(self, row): self.added.append(row)
    async def flush(self):
        for index, row in enumerate(self.added):
            if isinstance(row, DecisionLog): row.id = index + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("consolidate", [False, True])
async def test_stable_allow_keeps_shadow_route_and_public_visibility(monkeypatch, consolidate):
    decision = decision_fixture()
    should_log, event_type = scheduled_route(decision, consolidate=consolidate)
    assert should_log
    assert decision["_shadow_creation_required"] is (not consolidate)
    assert decision["_consolidation_required"] is consolidate
    decision["event_type"] = event_type
    monkeypatch.setattr("app.services.l3_gate_evaluation_store.link_decision_evaluation", AsyncMock())
    db = DB()
    await scan._persist_decision_logs(db, uuid4(), [decision])
    row = next(r for r in db.added if isinstance(r, DecisionLog))
    event = next(r for r in db.added if isinstance(r, L3AuthorizationOutbox))
    assert event.payload["shadow_creation_required"] is (not consolidate)
    assert event.payload["consolidation_required"] is consolidate
    assert event.payload["public_authorization_v1"] is True
    # The existing idempotent consumer associates an active trade; it does not
    # need a duplicate position for this new decision to remain visible.
    event.status = "PROCESSED"
    event.payload["processing_result"] = "SUPPRESSED/ACTIVE_TRADE_ALREADY_EXISTS"
    shadow = Obj(id=uuid4(), status="RUNNING")
    contract = row.metrics["l3_authorization_contract_v3"]
    auth = public_authorization(row, event, shadow,
        watchlist_id=UUID(contract["lineage"]["watchlist_id"]),
        now=datetime.now(timezone.utc))
    assert auth and auth["executable"] is True
    assert auth["shadow_id"] == str(shadow.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["blocked", "invalid", "expired", "contract_veto"])
async def test_stable_scan_never_routes_failed_public_authorization(monkeypatch, failure):
    decision = decision_fixture()
    contract = decision["metrics"]["l3_authorization_contract_v3"]
    if failure == "blocked":
        decision["decision"] = "BLOCK"
        contract["technical_decision"] = contract["final_decision"] = "BLOCK"
    elif failure == "invalid": contract["valid"] = False
    elif failure == "expired":
        contract["feature_evaluations"][0]["resolved_feature"]["source_timestamp"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    else: contract["contract_technical_decision"] = "BLOCK"
    body = deepcopy(contract); body.pop("authorization_contract_hash")
    contract["authorization_contract_hash"] = canonical_hash(body)
    _, decision["event_type"] = scheduled_route(decision)
    monkeypatch.setattr("app.services.l3_gate_evaluation_store.link_decision_evaluation", AsyncMock())
    db = DB()
    await scan._persist_decision_logs(db, uuid4(), [decision])
    event = next(r for r in db.added if isinstance(r, L3AuthorizationOutbox))
    assert not event.payload["shadow_creation_required"]
    assert not event.payload["consolidation_required"]

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from app.models.profile_audit_log import ProfileAuditLog
from app.services import governed_change_service as service
from app.services.governed_change_service import (
    ALLOWED_CONFIG_TYPES,
    PROFILE_ROOTS,
    apply_typed_patch,
)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self, result):
        self.result = result
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return _ScalarResult(self.result)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        return None


def test_profile_patch_returns_an_auditable_before_after_diff():
    source = {
        "default_timeframe": "5m",
        "filters": {"logic": "AND", "conditions": []},
        "scoring": {"enabled": True, "selected_rule_ids": ["r1"]},
        "signals": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
    }
    candidate, diff = apply_typed_patch(source, [{
        "op": "replace",
        "path": "/default_timeframe",
        "value": "15m",
        "reason": "Use the evidenced decision horizon",
        "evidence_refs": ["evidence-1"],
    }], allowed_roots=PROFILE_ROOTS)

    assert source["default_timeframe"] == "5m"
    assert candidate["default_timeframe"] == "15m"
    assert diff == [{
        "op": "replace",
        "path": "/default_timeframe",
        "old_value": "5m",
        "value": "15m",
        "reason": "Use the evidenced decision horizon",
        "evidence_refs": ["evidence-1"],
    }]


def test_patch_rejects_sensitive_or_unknown_configuration_paths():
    with pytest.raises(ValueError, match="Sensitive field"):
        apply_typed_patch(
            {"provider": {}},
            [{"op": "add", "path": "/provider/api_key", "value": "x"}],
        )
    with pytest.raises(ValueError, match="Unknown configuration root"):
        apply_typed_patch(
            {"thresholds": {}},
            [{"op": "add", "path": "/new_runtime_gate", "value": True}],
        )


def test_chat_config_authority_excludes_self_modifying_and_secret_families():
    assert {"score", "risk", "strategy", "spot_engine", "futures_engine"}.issubset(
        ALLOWED_CONFIG_TYPES
    )
    assert "ai_analysis_chat_runtime" not in ALLOWED_CONFIG_TYPES
    assert "ai_provider_runtime" not in ALLOWED_CONFIG_TYPES
    assert "ml" not in ALLOWED_CONFIG_TYPES


def test_bulk_profile_patch_keeps_each_profile_diff_separate():
    first = {"scoring": {"weights": {"rsi": 4}}}
    second = {"scoring": {"weights": {"rsi": 3}}}
    first_candidate, first_diff = apply_typed_patch(first, [{
        "op": "replace", "path": "/scoring/weights/rsi", "value": 2,
        "reason": "evidence", "evidence_refs": ["e1"],
    }], allowed_roots=PROFILE_ROOTS)
    second_candidate, second_diff = apply_typed_patch(second, [{
        "op": "replace", "path": "/scoring/weights/rsi", "value": 2,
        "reason": "evidence", "evidence_refs": ["e2"],
    }], allowed_roots=PROFILE_ROOTS)

    assert first_candidate["scoring"]["weights"]["rsi"] == 2
    assert second_candidate["scoring"]["weights"]["rsi"] == 2
    assert first_diff[0]["old_value"] == 4
    assert second_diff[0]["old_value"] == 3


@pytest.mark.asyncio
async def test_dry_run_requires_evidence_on_every_individual_change():
    evidence_id = str(uuid.uuid4())
    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "target": {"profile_id": str(uuid.uuid4())},
        "objective": "Adjust two evidenced profile parameters",
        "risk": "Operational change",
        "changes": [
            {
                "op": "replace",
                "path": "/default_timeframe",
                "value": "15m",
                "evidence_refs": [evidence_id],
            },
            {
                "op": "replace",
                "path": "/scoring/thresholds/buy",
                "value": 65,
                "evidence_refs": [],
            },
        ],
    }

    with pytest.raises(ValueError, match="Every proposed change requires evidence"):
        await service.create_dry_run(
            _FakeDB(None),
            uuid.uuid4(),
            proposal=proposal,
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            evidence_ids={evidence_id},
        )


@pytest.mark.asyncio
async def test_human_confirmed_profile_change_updates_live_config_and_audit(monkeypatch):
    user_id = uuid.uuid4()
    profile_id = uuid.uuid4()
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    profile = SimpleNamespace(
        id=profile_id,
        user_id=user_id,
        name="Profile A",
        config={"default_timeframe": "5m"},
        profile_version=now,
        updated_at=now,
    )
    state_hash = service.document_hash({
        "config": profile.config,
        "profile_version": profile.profile_version,
        "updated_at": profile.updated_at,
    })
    plan = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user_id,
        action_type=service.ACTION_TYPE,
        target_type="PROFILE",
        target_id=str(profile_id),
        objective="Use 15m",
        evidence={"evidence_ids": [str(uuid.uuid4())]},
        proposed_diff=[{
            "op": "replace", "path": "/default_timeframe",
            "old_value": "5m", "value": "15m", "reason": "evidence",
        }],
        execution_payload={
            "operation_type": "UPDATE_PROFILE_CONFIG",
            "profile_id": str(profile_id),
            "profile_name": "Profile A",
            "source_document": {"default_timeframe": "5m"},
            "candidate_document": {"default_timeframe": "15m"},
        },
        risk_assessment="Operational change",
        rollback_plan={"source_document": {"default_timeframe": "5m"}},
        target_state_hash=state_hash,
        status="DRY_RUN",
        approved_at=None,
        approved_by=None,
        approval_text=None,
        executed_at=None,
        execution_result=None,
    )
    db = _FakeDB(profile)

    async def _allowed(_db, _user_id):
        return True

    async def _plan(_db, _user_id, _plan_id, *, lock=False):
        assert lock is True
        return plan

    monkeypatch.setattr(service, "_runtime_allows_write", _allowed)
    monkeypatch.setattr(service, "get_plan", _plan)

    result = await service.approve_and_execute(
        db, user_id, plan.id, decision_id=str(uuid.uuid4())
    )

    assert profile.config["default_timeframe"] == "15m"
    assert plan.status == "EXECUTED"
    assert result["execution_result"]["live_config_changed"] is True
    assert db.commits == 1
    assert any(isinstance(item, ProfileAuditLog) for item in db.added)

from datetime import datetime, timezone
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.copilot.action_service import ActionService, profile_state_hash
from app.copilot.agent import CopilotAgent, MAX_TOOL_ROUNDS
from app.copilot.query_executor import QueryExecutor
from app.models.profile import Profile


class _AnthropicResponse:
    def __init__(self, content):
        self.content = content


class _AnthropicMessages:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= MAX_TOOL_ROUNDS:
            return _AnthropicResponse([SimpleNamespace(
                type="tool_use", id=f"tool-{len(self.calls)}", name="retrieve_skills",
                input={"query": "profiles"},
            )])
        return _AnthropicResponse([SimpleNamespace(type="text", text="Síntese final")])


class _AnthropicClient:
    def __init__(self, *_args, **_kwargs):
        self.messages = _AnthropicMessages()


@pytest.mark.asyncio
async def test_anthropic_forces_final_answer_after_tool_limit(monkeypatch):
    client = _AnthropicClient()
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(AsyncAnthropic=lambda **_kwargs: client))
    monkeypatch.setattr("app.copilot.agent.get_decrypted_api_key", AsyncMock(return_value="test-key"))
    agent = CopilotAgent()
    monkeypatch.setattr(agent, "_tool", AsyncMock(return_value={"ok": True}))

    answer = await agent._run_anthropic(
        object(), uuid4(), "analise", uuid4(), "system", None,
        {"queries": [], "evidence": [], "action_plan": None, "skills_used": []},
    )

    assert answer == "Síntese final"
    assert len(client.messages.calls) == MAX_TOOL_ROUNDS + 1
    assert "tools" not in client.messages.calls[-1]


class _OpenAIResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _OpenAIClient:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def post(self, _url, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) <= MAX_TOOL_ROUNDS:
            message = {"role": "assistant", "tool_calls": [{
                "id": f"tool-{len(self.calls)}", "type": "function",
                "function": {"name": "retrieve_skills", "arguments": '{"query":"profiles"}'},
            }]}
        else:
            message = {"role": "assistant", "content": "Síntese final"}
        return _OpenAIResponse({"choices": [{"message": message}]})


@pytest.mark.asyncio
async def test_openai_forces_final_answer_after_tool_limit(monkeypatch):
    client = _OpenAIClient()
    monkeypatch.setattr("httpx.AsyncClient", lambda **_kwargs: client)
    monkeypatch.setattr("app.copilot.agent.get_decrypted_api_key", AsyncMock(return_value="test-key"))
    agent = CopilotAgent()
    monkeypatch.setattr(agent, "_tool", AsyncMock(return_value={"ok": True}))

    answer = await agent._run_openai(
        object(), uuid4(), "analise", uuid4(), "system", None,
        {"queries": [], "evidence": [], "action_plan": None, "skills_used": []},
    )

    assert answer == "Síntese final"
    assert len(client.calls) == MAX_TOOL_ROUNDS + 1
    assert client.calls[-1]["json"]["tool_choice"] == "none"


class _AsyncContext:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Rows:
    def keys(self):
        return ["id", "name"]

    def fetchmany(self, _limit):
        return [(1, "alpha"), (2, "beta")]


class _ReadSession(_AsyncContext):
    def __init__(self):
        self.calls = []

    def begin(self):
        return _AsyncContext()

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return _Rows() if len(self.calls) == 3 else None


class _SessionFactory:
    def __init__(self):
        self.session = _ReadSession()

    def __call__(self):
        return self.session


class _AuditDb:
    def __init__(self):
        self.added = []

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        return None

    async def refresh(self, row):
        if getattr(row, "id", None) is None:
            row.id = uuid4()


@pytest.mark.asyncio
async def test_query_executor_uses_readonly_transaction_and_audits():
    factory = _SessionFactory()
    executor = QueryExecutor(session_factory=factory)
    db = _AuditDb()
    result = await executor.execute(db, uuid4(), "SELECT id, name FROM profiles", {}, reason="test")
    assert result["rows_returned"] == 2
    assert result["classification"] == "READ_ONLY"
    assert "SET TRANSACTION READ ONLY" in factory.session.calls[1][0]
    assert "_copilot_bounded" in factory.session.calls[2][0]
    assert len(db.added) == 2


def _plan(user_id, source, status="APPROVED"):
    return SimpleNamespace(
        id=uuid4(), user_id=user_id, session_id=None, action_type="UPDATE_PROFILE_CONFIG",
        target_type="PROFILE", target_id=str(source.id), objective="calibrar ADX",
        evidence={"sample_size": 50}, proposed_diff=[{"path": "signals.min", "old_value": 20, "new_value": 25}],
        execution_payload={"candidate_config": {"signals": {"min": 25}}},
        risk_assessment="redução de volume", rollback_plan={"active_profile_unchanged": True},
        target_state_hash=profile_state_hash(source), status=status,
        approved_at=datetime.now(timezone.utc), approved_by=user_id, approval_text="CONFIRMO EXECUTAR",
        executed_at=None, execution_result=None,
    )


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _ActionDb(_AuditDb):
    def __init__(self, source=None):
        super().__init__()
        self.source = source
        self.rolled_back = False

    async def execute(self, _query):
        return _ScalarResult(self.source)

    async def flush(self):
        for row in self.added:
            if isinstance(row, Profile) and row.id is None:
                row.id = uuid4()

    def in_transaction(self):
        return True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_approval_requires_exact_phrase():
    service = ActionService()
    with pytest.raises(ValueError, match="Confirmação inválida"):
        await service.approve(_ActionDb(), uuid4(), uuid4(), "sim")


@pytest.mark.asyncio
async def test_approved_execution_creates_shadow_candidate_without_mutating_source(monkeypatch):
    user_id = uuid4()
    source = SimpleNamespace(
        id=uuid4(), user_id=user_id, name="L3 Base", config={"signals": {"min": 20}},
        updated_at=datetime.now(timezone.utc), profile_version=datetime.now(timezone.utc),
        profile_role="acquisition_queue", pipeline_order="3", pipeline_label="L3",
        auto_pilot_config={}, profile_type="STANDARD",
    )
    plan = _plan(user_id, source)
    service = ActionService()
    monkeypatch.setattr(service, "_get", AsyncMock(return_value=plan))
    db = _ActionDb(source)
    result = await service.execute(db, user_id, plan.id)
    candidates = [row for row in db.added if isinstance(row, Profile)]
    assert len(candidates) == 1
    assert candidates[0].is_shadow_only is True
    assert candidates[0].live_trading_enabled is False
    assert candidates[0].config["signals"]["min"] == 25
    assert source.config["signals"]["min"] == 20
    assert result["execution_result"]["live_profile_changed"] is False

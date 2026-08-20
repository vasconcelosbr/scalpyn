from __future__ import annotations

import inspect
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from app.ai_orchestration.contracts import AIRequestIntent
from app.ai_orchestration.langgraph.graphs import build_graph
from app.ai_orchestration.langgraph.registry import resolve_graph
from app.ai_orchestration.langgraph.state import assert_checkpoint_safe
from app.schemas.analysis_chat import (
    AnalysisChatDataMode,
    AnalysisChatOutput,
    AnalysisChatRequestKind,
    AnalysisChatRuntimeConfig,
)
from app.services.analysis_chat_service import AnalysisChatError, AnalysisChatService


def test_chat_does_not_create_new_provider_intent():
    assert {item.value for item in AIRequestIntent} == {
        "NORMAL_ANALYSIS", "FAKE_PROVIDER_CANARY", "REAL_PROVIDER_CANARY",
    }
    assert "FOLLOW_UP_CHAT" in {item.value for item in AnalysisChatRequestKind}


def test_analysis_chat_provider_transport_rejects_unknown_or_real_canary_intent():
    from app.ai_orchestration.errors import ProviderBlockedError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _validated_analysis_chat_provider_intent,
    )

    assert _validated_analysis_chat_provider_intent(
        {"request_intent": "NORMAL_ANALYSIS"}
    ) == "NORMAL_ANALYSIS"
    assert _validated_analysis_chat_provider_intent(
        {"request_intent": "FAKE_PROVIDER_CANARY"}
    ) == "FAKE_PROVIDER_CANARY"
    for intent in ("REAL_PROVIDER_CANARY", "UNKNOWN", ""):
        with pytest.raises(ProviderBlockedError) as exc_info:
            _validated_analysis_chat_provider_intent({"request_intent": intent})
        assert exc_info.value.reason_code == "ANALYSIS_CHAT_INTENT_NOT_ALLOWED"


def test_explicit_spot_command_is_parsed_without_provider_inference():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _parse_explicit_spot_proposal_command,
    )

    command = _parse_explicit_spot_proposal_command(
        "Altere somente o spot_engine: activation_profit_pct de 2% para 3%, "
        "max_drawdown_from_hwm_pct de 1% para 2% e never_sell_at_loss de false "
        "para true. Mantenha atr_stop_multiplier em 2x e hwm_trail_pct em 1%. "
        "Gere uma prévia auditável e não aplique sem minha confirmação final."
    )

    assert command is not None
    assert [(item.field, item.old_value, item.new_value) for item in command.changes] == [
        ("activation_profit_pct", 2, 3),
        ("max_drawdown_from_hwm_pct", 1, 2),
        ("never_sell_at_loss", False, True),
    ]
    assert [(item.field, item.value) for item in command.assertions] == [
        ("hwm_trail_pct", 1),
        ("atr_stop_multiplier", 2),
    ]


def test_explicit_spot_command_falls_back_for_unknown_or_ambiguous_fields():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _parse_explicit_spot_proposal_command,
    )

    assert _parse_explicit_spot_proposal_command(
        "Altere spot_engine: unknown_runtime_gate de 1 para 2."
    ) is None
    assert _parse_explicit_spot_proposal_command(
        "Altere spot_engine: activation_profit_pct para 3%."
    ) is None


def test_explicit_spot_preview_precedes_provider_transport_and_is_zero_cost():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    invoke_source = inspect.getsource(AnalysisChatGraphNodeHandler._handle_provider_node)
    deterministic_index = invoke_source.index("_validated_explicit_spot_proposal")
    runtime_index = invoke_source.index('ConfigProfile.config_type == "ai_provider_runtime"')
    provider_index = invoke_source.index("self._prepare_normal_provider")
    assert deterministic_index < runtime_index < provider_index
    assert '"provider_transport_attempted": False' in invoke_source

    persist_source = inspect.getsource(AnalysisChatGraphNodeHandler._persist_answer)
    assert "DETERMINISTIC_GOVERNED_PROPOSAL_RECONCILED" in persist_source
    assert 'provider = "deterministic"' in persist_source
    assert 'model = "governed-explicit-v1"' in persist_source


@pytest.mark.asyncio
async def test_explicit_spot_preview_binds_current_values_and_fresh_evidence():
    from app.ai_orchestration.hashing import canonical_hash
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _validated_explicit_spot_proposal,
    )

    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    prompt_id = uuid.uuid4()
    parent_run_id = uuid.uuid4()
    output_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "answer", "answer_type", "based_on", "parent_analysis_run_id",
            "evidence_refs", "proposal",
        ],
        "properties": {
            "answer": {"type": "string"},
            "answer_type": {"type": "string"},
            "based_on": {"type": "string"},
            "parent_analysis_run_id": {"type": "string", "format": "uuid"},
            "evidence_refs": {"type": "array", "items": {"type": "object"}},
            "proposal": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "operation_type", "target", "objective", "risk", "changes",
                        ],
                        "properties": {
                            "operation_type": {"const": "UPDATE_CONFIG_PROFILE"},
                            "target": {"type": "object"},
                            "objective": {"type": "string"},
                            "risk": {"type": "string"},
                            "changes": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "additionalProperties": False,
                                    "required": [
                                        "op", "path", "value_json", "old_value_json",
                                        "array_guards_json", "reason", "evidence_refs",
                                        "profile_id", "profile_name", "profile_indexes",
                                    ],
                                    "properties": {
                                        "op": {"const": "replace"},
                                        "path": {"type": "string"},
                                        "value_json": {"type": "string"},
                                        "old_value_json": {"type": "string"},
                                        "array_guards_json": {"type": "string"},
                                        "reason": {"type": "string"},
                                        "evidence_refs": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "pattern": "^E([1-9]|1[0-2])$",
                                            },
                                        },
                                        "profile_id": {"type": "null"},
                                        "profile_name": {"type": "null"},
                                        "profile_indexes": {"type": "array"},
                                    },
                                },
                            },
                        },
                    },
                    {"type": "null"},
                ],
            },
        },
    }
    prompt_fields = {
        "prompt_key": "analysis-chat-governed-change",
        "semantic_version": "1.12.0",
        "system_template": "system",
        "user_template": "user",
        "input_schema_json": {},
        "output_schema_json": output_schema,
        "tool_policy_json": {},
        "provider_constraints_json": {},
    }
    prompt = SimpleNamespace(
        id=prompt_id,
        **prompt_fields,
        content_hash=canonical_hash(prompt_fields),
        status="APPROVED",
        approved_at=datetime.now(timezone.utc),
    )
    resource = SimpleNamespace(config_json={
        "sell_flow": {
            "trailing": {
                "activation_profit_pct": 2,
                "hwm_trail_pct": 1,
            },
            "kill_switch": {
                "atr_stop_multiplier": 2,
                "max_drawdown_from_hwm_pct": 1,
            },
        },
        "selling": {"never_sell_at_loss": False},
    })
    evidence_rows = [
        SimpleNamespace(
            id=uuid.uuid4(), tool_name="global_risk.get_effective_policy"
        ),
        SimpleNamespace(
            id=uuid.uuid4(), tool_name="strategies.get_execution_policy"
        ),
    ]

    class _Result:
        def __init__(self, *, scalar=None, rows=None):
            self.scalar = scalar
            self.rows = rows or []

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class _DB:
        def __init__(self):
            self.results = iter((
                _Result(scalar=resource),
                _Result(rows=evidence_rows),
            ))

        async def get(self, _model, key):
            return prompt if key == prompt_id else None

        async def execute(self, _statement):
            return next(self.results)

    request = SimpleNamespace(
        id=request_id,
        prompt_version_id=prompt_id,
        request_kind="PROPOSAL_DRAFT",
        request_json={
            "data_mode": "DRAFT_PROPOSAL",
            "request_intent": "NORMAL_ANALYSIS",
            "question": (
                "Altere somente o spot_engine: activation_profit_pct de 2% para 3%, "
                "max_drawdown_from_hwm_pct de 1% para 2% e never_sell_at_loss de "
                "false para true. Mantenha atr_stop_multiplier em 2x e "
                "hwm_trail_pct em 1%."
            ),
        },
    )
    selected_refs = [
        {
            "evidence_id": str(uuid.uuid4()),
            "module": "shadow_portfolio",
            "source": "FROZEN_ANALYSIS",
        }
        for _index in range(10)
    ] + [
        {
            "evidence_id": str(row.id),
            "module": row.tool_name.split(".", 1)[0],
            "source": "REFRESHED_READONLY_DATA",
        }
        for row in evidence_rows
    ]

    answer = await _validated_explicit_spot_proposal(
        _DB(),
        run=SimpleNamespace(tenant_id=tenant_id),
        request=request,
        conversation=SimpleNamespace(parent_analysis_run_id=parent_run_id),
        selected_evidence_refs=selected_refs,
    )

    assert answer is not None
    assert answer["answer_type"] == "PROPOSAL"
    assert answer["new_data_queried"] is True
    proposal = answer["proposal"]
    assert proposal["target"]["config_type"] == "spot_engine"
    assert [(change["path"], change["old_value_json"], change["value_json"])
            for change in proposal["changes"]] == [
        ("/sell_flow/trailing/activation_profit_pct", "2", "3"),
        ("/sell_flow/kill_switch/max_drawdown_from_hwm_pct", "1", "2"),
        ("/selling/never_sell_at_loss", "false", "true"),
    ]


def test_analysis_chat_flags_fail_closed():
    config = AnalysisChatRuntimeConfig()
    assert config.enabled is False
    assert config.readonly_refresh_enabled is False
    assert config.child_analysis_enabled is False
    assert config.proposals_enabled is False
    assert config.governed_actions_enabled is False
    assert config.live_config_write_enabled is False
    assert config.streaming_enabled is False
    assert config.budget_enforcement_enabled is True
    assert config.provider_max_cost_usd == 0
    assert config.request_token_limit == 0


def test_analysis_chat_runtime_config_is_jsonb_serializable():
    payload = AnalysisChatRuntimeConfig(
        enabled=True,
        budget_enforcement_enabled=False,
        provider_max_cost_usd="0.45",
    ).model_dump(mode="json")
    json.dumps(payload)
    assert payload["provider_max_cost_usd"] == "0.45"
    assert payload["budget_enforcement_enabled"] is False


def test_chat_budget_audit_only_mode_disables_every_financial_blocker():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    send_source = inspect.getsource(AnalysisChatService.send_message)
    invoke_source = inspect.getsource(AnalysisChatGraphNodeHandler._prepare_normal_provider)
    assert "config.budget_enforcement_enabled" in send_source
    assert '"AUDIT_ONLY"' in send_source
    assert 'request_json.get("budget_enforcement_enabled") is not False' in invoke_source
    assert invoke_source.count("if budget_enforcement_enabled") >= 3
    assert "BudgetReservationAudit.activate_placeholder" in invoke_source
    reconcile_source = inspect.getsource(
        AnalysisChatGraphNodeHandler._reconcile_provider_response
    )
    assert "AUDIT_ONLY_PROVIDER_RESPONSE_RECEIVED" in reconcile_source
    assert "BudgetReservationAudit.reconcile" in reconcile_source


def test_governed_proposal_has_configured_output_allowance_and_refreshes_legacy_turns():
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposal_max_output_tokens=16384,
    )
    assert config.proposal_max_output_tokens == 16384
    send_source = inspect.getsource(AnalysisChatService.send_message)
    refresh_source = inspect.getsource(
        AnalysisChatService.refresh_proposal_confirmation_contract
    )
    assert "config.proposal_max_output_tokens" in send_source
    assert 'interrupt.interrupt_type != "PROPOSAL_CONFIRMATION"' in refresh_source
    assert 'request_json.get("request_intent") != "NORMAL_ANALYSIS"' in refresh_source
    assert "ANALYSIS_CHAT_PROPOSAL_CONFIRMATION" in refresh_source
    assert "PROPOSAL_CONTRACT_REFRESHED" in refresh_source


@pytest.mark.asyncio
async def test_proposal_confirmation_reissues_an_expired_approval(monkeypatch):
    from app.models.systemic_ai import AIModelApprovalRecord
    from app.services import analysis_chat_service as service_module

    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    interrupt_id = uuid.uuid4()
    old_approval_id = uuid.uuid4()
    now = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    locked_run = SimpleNamespace(id=run_id, tenant_id=tenant_id)
    interrupt = SimpleNamespace(
        id=interrupt_id,
        tenant_id=tenant_id,
        graph_run_id=run_id,
        interrupt_type="PROPOSAL_CONFIRMATION",
        status="PENDING",
    )
    request = SimpleNamespace(
        id=request_id,
        tenant_id=tenant_id,
        prompt_version_id=uuid.uuid4(),
        request_json={
            "request_intent": "NORMAL_ANALYSIS",
            "model_approval_id": str(old_approval_id),
        },
    )
    reservation = SimpleNamespace(
        status="RESERVED",
        provider_transport_attempted=False,
        model_approval_id=old_approval_id,
        max_output_tokens=16384,
    )
    latest_prompt = SimpleNamespace(id=uuid.uuid4())
    current_approval = SimpleNamespace(
        id=old_approval_id,
        tenant_id=tenant_id,
        provider="anthropic",
        model="claude-model",
        max_cost_usd=Decimal("0.45"),
        input_cost_per_million=Decimal("0.80"),
        output_cost_per_million=Decimal("4.00"),
        max_output_tokens=16384,
        pricing_source_url="https://provider.invalid/pricing",
        pricing_observed_at=now - timedelta(days=30),
        pricing_snapshot_hash="pricing-hash",
        analysis_profile_id=uuid.uuid4(),
        scope="ANALYSIS_CHAT_TURN",
        status="APPROVED",
        expires_at=now - timedelta(hours=1),
    )
    message = SimpleNamespace(
        id=uuid.uuid4(),
        graph_run_id=run_id,
        ai_request_id=request_id,
        data_mode=AnalysisChatDataMode.DRAFT_PROPOSAL.value,
        prompt_version_id=request.prompt_version_id,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((locked_run, interrupt, reservation, latest_prompt))
            self.added = []
            self.flush_snapshots = []

        async def execute(self, _statement):
            return _Result(next(self.results))

        async def get(self, model, key):
            if key == request_id:
                return request
            if model is AIModelApprovalRecord and key == old_approval_id:
                return current_approval
            return None

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            self.flush_snapshots.append(reservation.model_approval_id)

    async def _runtime_config(_db, _tenant_id):
        return AnalysisChatRuntimeConfig(proposal_max_output_tokens=16384)

    monkeypatch.setattr(service_module, "_now", lambda: now)
    monkeypatch.setattr(
        service_module,
        "get_langgraph_settings",
        lambda: SimpleNamespace(model_approval_ttl_seconds=900),
    )
    monkeypatch.setattr(
        AnalysisChatService, "runtime_config", staticmethod(_runtime_config)
    )
    db = _DB()

    await AnalysisChatService.refresh_proposal_confirmation_contract(
        db,
        tenant_id=tenant_id,
        user_id=tenant_id,
        message=message,
        interrupt_id=interrupt_id,
        decision="approve",
    )

    replacements = [
        item for item in db.added if isinstance(item, AIModelApprovalRecord)
    ]
    assert len(replacements) == 1
    replacement = replacements[0]
    assert replacement.id != old_approval_id
    assert replacement.approved_at == now
    assert replacement.expires_at == now + timedelta(seconds=900)
    assert replacement.provider == current_approval.provider
    assert replacement.model == current_approval.model
    assert replacement.max_cost_usd == current_approval.max_cost_usd
    assert replacement.input_cost_per_million == current_approval.input_cost_per_million
    assert replacement.output_cost_per_million == current_approval.output_cost_per_million
    assert request.request_json["model_approval_id"] == str(replacement.id)
    assert reservation.model_approval_id == replacement.id
    assert db.flush_snapshots == [old_approval_id, replacement.id]


@pytest.mark.asyncio
async def test_provider_response_reconciles_after_exact_key_is_deactivated(monkeypatch):
    from app.ai_orchestration.budget_reservation_audit import BudgetReservationAudit
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
        _ProviderInvocation,
    )
    from app.models.systemic_ai import AIUsageRecord

    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    message_id = uuid.uuid4()
    provider_key_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=tenant_id, status="RUNNING")
    message = SimpleNamespace(
        id=message_id,
        tenant_id=tenant_id,
        ai_request_id=request_id,
        status="STREAMING",
        provider_transport_attempted=True,
    )
    deactivated_key = SimpleNamespace(
        id=provider_key_id,
        user_id=tenant_id,
        provider="anthropic",
        is_active=False,
        is_validated=False,
        tokens_used_month=100,
        last_used_at=None,
    )
    reservation = SimpleNamespace(
        status="TRANSPORT_STARTED",
        actual_tokens=None,
        actual_cost_usd=None,
        terminal_reason=None,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((run, message, None, deactivated_key))
            self.added = []
            self.statements = []

        async def execute(self, statement):
            self.statements.append(str(statement))
            return _Result(next(self.results))

        def add(self, value):
            self.added.append(value)

        async def flush(self):
            return None

    async def _reconcile(
        _db,
        *,
        tenant_id,
        ai_request_id,
        reserved_tokens,
        actual_tokens,
        actual_cost_usd,
        terminal_reason,
    ):
        assert tenant_id == invocation.tenant_id
        assert ai_request_id == invocation.request_id
        assert reserved_tokens == invocation.reserved_tokens
        reservation.status = "RECONCILED"
        reservation.actual_tokens = actual_tokens
        reservation.actual_cost_usd = actual_cost_usd
        reservation.terminal_reason = terminal_reason
        return {
            "released_tokens": max(reserved_tokens - actual_tokens, 0),
            "overage_tokens": max(actual_tokens - reserved_tokens, 0),
        }

    monkeypatch.setattr(BudgetReservationAudit, "reconcile", staticmethod(_reconcile))
    invocation = _ProviderInvocation(
        tenant_id=tenant_id,
        request_id=request_id,
        message_id=message_id,
        parent_analysis_run_id=uuid.uuid4(),
        provider_key_id=provider_key_id,
        provider="anthropic",
        model="claude-model",
        system_prompt="system",
        user_prompt="user",
        api_key="not-used",
        max_output_tokens=1024,
        output_schema={},
        budget_enforcement_enabled=True,
        reserved_tokens=20,
        reserved_cost=Decimal("0.00003000"),
        used_today=0,
        used_month=0,
        request_token_limit=1000,
        daily_token_limit=10000,
        monthly_token_limit=100000,
        provider_key_monthly_token_limit=100000,
        provider_key_tokens_used_month_before=100,
        input_rate=Decimal("1.00"),
        output_rate=Decimal("2.00"),
        max_cost_usd=Decimal("0.45"),
        pricing_snapshot_version="pricing-v1",
        selected_evidence_refs=(),
        data_mode=AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY.value,
    )
    response = SimpleNamespace(
        tokens_input=10,
        tokens_output=5,
        terminal_error_code=None,
    )
    db = _DB()

    usage_audit, terminal_status = await AnalysisChatGraphNodeHandler(
        run_id, celery=False
    )._reconcile_provider_response(
        db,
        invocation=invocation,
        response=response,
    )

    expected_cost = Decimal("0.000020")
    assert terminal_status is None
    assert reservation.status == "RECONCILED"
    assert reservation.actual_tokens == 15
    assert reservation.actual_cost_usd == expected_cost
    usages = [item for item in db.added if isinstance(item, AIUsageRecord)]
    assert len(usages) == 1
    assert usages[0].tokens_input == 10
    assert usages[0].tokens_output == 5
    assert usages[0].actual_cost == expected_cost
    assert deactivated_key.is_active is False
    assert deactivated_key.is_validated is False
    assert deactivated_key.tokens_used_month == 115
    assert usage_audit == {"provider_tokens_used_month": 115}
    key_lookup = next(
        statement for statement in db.statements if "ai_provider_keys" in statement
    )
    key_predicates = key_lookup.split("WHERE", 1)[1]
    assert "ai_provider_keys.id" in key_predicates
    assert "is_active" not in key_predicates
    assert "is_validated" not in key_predicates


@pytest.mark.asyncio
async def test_cancel_after_provider_transport_attributes_known_usage_once(monkeypatch):
    from app.ai_orchestration.budget_reservation_audit import BudgetReservationAudit
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
        _ProviderInvocation,
    )

    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    message_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, tenant_id=tenant_id, status="CANCELLED")
    message = SimpleNamespace(
        id=message_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        ai_request_id=request_id,
        status="CANCELLED",
        provider_transport_attempted=True,
        tokens_input=None,
        tokens_output=None,
        cost_usd=None,
        lock_version=3,
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        tenant_id=tenant_id,
        total_tokens_input=100,
        total_tokens_output=20,
        total_cost_usd=Decimal("0.10"),
        updated_at=None,
        lock_version=7,
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((run, message, conversation))

        async def execute(self, _statement):
            return _Result(next(self.results))

    async def _reconcile(_db, **_kwargs):
        return {"released_tokens": 0, "overage_tokens": 0}

    async def _record_usage(_self, _db, **kwargs):
        assert kwargs["tokens_input"] == 10
        assert kwargs["tokens_output"] == 5
        assert kwargs["actual_cost"] == Decimal("0.000020")
        return {"provider_tokens_used_month": 15}

    monkeypatch.setattr(BudgetReservationAudit, "reconcile", staticmethod(_reconcile))
    monkeypatch.setattr(
        AnalysisChatGraphNodeHandler,
        "_record_provider_usage",
        _record_usage,
    )
    invocation = _ProviderInvocation(
        tenant_id=tenant_id,
        request_id=request_id,
        message_id=message_id,
        parent_analysis_run_id=uuid.uuid4(),
        provider_key_id=uuid.uuid4(),
        provider="anthropic",
        model="claude-model",
        system_prompt="system",
        user_prompt="user",
        api_key="not-used",
        max_output_tokens=1024,
        output_schema={},
        budget_enforcement_enabled=True,
        reserved_tokens=20,
        reserved_cost=Decimal("0.00003000"),
        used_today=0,
        used_month=0,
        request_token_limit=1000,
        daily_token_limit=10000,
        monthly_token_limit=100000,
        provider_key_monthly_token_limit=100000,
        provider_key_tokens_used_month_before=0,
        input_rate=Decimal("1.00"),
        output_rate=Decimal("2.00"),
        max_cost_usd=Decimal("0.45"),
        pricing_snapshot_version="pricing-v1",
        selected_evidence_refs=(),
        data_mode=AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY.value,
    )
    handler = AnalysisChatGraphNodeHandler(run_id, celery=False)

    usage_audit, terminal_status = await handler._reconcile_provider_response(
        _DB(),
        invocation=invocation,
        response=SimpleNamespace(
            tokens_input=10,
            tokens_output=5,
            terminal_error_code=None,
        ),
    )

    assert usage_audit == {"provider_tokens_used_month": 15}
    assert terminal_status == "CANCELLED"
    assert message.tokens_input == 10
    assert message.tokens_output == 5
    assert message.cost_usd == Decimal("0.000020")
    assert conversation.total_tokens_input == 110
    assert conversation.total_tokens_output == 25
    assert conversation.total_cost_usd == Decimal("0.100020")
    assert message.lock_version == 4
    assert conversation.lock_version == 8

    class _NoQueryDB:
        async def execute(self, _statement):
            raise AssertionError("idempotent attribution must not reload totals")

    await handler._attribute_terminal_provider_usage(
        _NoQueryDB(),
        tenant_id=tenant_id,
        message=message,
        tokens_input=10,
        tokens_output=5,
        actual_cost=Decimal("0.000020"),
    )
    assert conversation.total_cost_usd == Decimal("0.100020")


def test_compact_multi_profile_changes_expand_to_the_existing_audited_contract():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _expand_compact_profile_changes,
    )

    profile_ids = [str(uuid.uuid4()) for _ in range(3)]
    evidence_id = str(uuid.uuid4())
    changes = _expand_compact_profile_changes({
        "operation_type": "UPDATE_PROFILE_CONFIG_SET",
        "target": {"profile_ids": profile_ids},
        "changes": [{
            "op": "replace",
            "path": "/scoring/thresholds/buy",
            "value_json": "65",
            "reason": "Shared evidence-backed threshold",
            "evidence_refs": [evidence_id],
            "profile_id": None,
            "profile_name": None,
            "profile_indexes": [0, 2],
        }],
    })

    assert [change["profile_id"] for change in changes] == [profile_ids[0], profile_ids[2]]
    assert all(change["evidence_refs"] == [evidence_id] for change in changes)
    assert all("profile_indexes" not in change for change in changes)


@pytest.mark.parametrize("indexes", [[0, 0], [2], [True]])
def test_compact_profile_changes_reject_ambiguous_or_invalid_indexes(indexes):
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _expand_compact_profile_changes,
    )

    with pytest.raises(ValueError, match="Grouped profile"):
        _expand_compact_profile_changes({
            "operation_type": "UPDATE_PROFILE_CONFIG_SET",
            "target": {"profile_ids": [str(uuid.uuid4())]},
            "changes": [{
                "profile_indexes": indexes,
                "profile_id": None,
                "profile_name": None,
            }],
        })


def test_proposal_evidence_keeps_only_canonical_refs_without_weakening_gate():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _retain_canonical_change_evidence,
    )

    canonical_id = str(uuid.uuid4())
    invalid_id = str(uuid.uuid4())
    changes = _retain_canonical_change_evidence([{
        "op": "replace",
        "path": "/config/filters/rsi/max",
        "evidence_refs": [invalid_id, canonical_id, canonical_id],
    }], {canonical_id})

    assert changes[0]["evidence_refs"] == [canonical_id]


def test_proposal_evidence_fails_closed_without_a_canonical_ref():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _retain_canonical_change_evidence,
    )

    with pytest.raises(ValueError, match="requires evidence from the parent analysis"):
        _retain_canonical_change_evidence([{
            "op": "replace",
            "path": "/config/filters/rsi/max",
            "evidence_refs": [str(uuid.uuid4())],
        }], {str(uuid.uuid4())})


def test_governed_proposal_authorizes_against_the_complete_parent_ledger():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
        _load_canonical_evidence_ids,
        _load_canonical_evidence_refs,
    )

    bounded_source = inspect.getsource(_load_canonical_evidence_refs)
    authority_source = inspect.getsource(_load_canonical_evidence_ids)
    draft_source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "_ANALYSIS_CHAT_MAX_REFRESHED_EVIDENCE" in bounded_source
    assert "_ANALYSIS_CHAT_MAX_LABELED_EVIDENCE - len(refreshed_rows)" in bounded_source
    assert ".limit(\n        parent_limit\n    )" in bounded_source
    assert bounded_source.index("refreshed_rows =") < bounded_source.index("parent_rows =")
    assert ".limit(" not in authority_source
    assert "evidence_ids = await _load_canonical_evidence_ids" in draft_source


def test_inapplicable_governed_diff_has_a_stable_typed_failure():
    from app.ai_orchestration.errors import GovernedProposalError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    error = GovernedProposalError()
    assert error.reason_code == "ANALYSIS_CHAT_PROPOSAL_NOT_APPLICABLE"
    assert error.error_kind == "GOVERNED_PROPOSAL_INVALID"
    assert "current configuration contract" in error.safe_message
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert (
        "except (AttributeError, KeyError, LookupError, TypeError, ValueError) as exc"
        in source
    )
    assert "raise GovernedProposalError() from exc" in source


def test_compact_proposal_prompt_is_versioned_and_bounded():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/166_chat_compact_profile_proposals.py"
    )
    spec = importlib.util.spec_from_file_location("chat_compact_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    prompt = migration._prompt_content()
    change = prompt["output_schema_json"]["properties"]["proposal"]["properties"]["changes"]
    assert migration.down_revision == "165_chat_bulk_profile_config"
    assert migration.revision == "166_chat_compact_proposals"
    assert len(migration.revision) <= 32
    assert prompt["semantic_version"] == "1.3.0"
    assert change["maxItems"] == 64
    assert change["items"]["properties"]["profile_indexes"]["maxItems"] == 32
    assert "Never repeat identical changes per profile" in prompt["system_template"]


def test_governed_actions_support_bulk_profile_activation_without_deletion():
    from app.services import governed_change_service

    source = inspect.getsource(governed_change_service)
    assert 'operation == "SET_PROFILE_ACTIVE_STATUS"' in source
    assert 'change.get("path") != "/is_active"' in source
    assert '"profiles_deleted": False' in source
    assert 'payload.get("operation_type") == "SET_PROFILE_ACTIVE_STATUS"' in source


def test_governed_actions_support_atomic_bulk_profile_configuration_and_rollback():
    from app.services import governed_change_service

    source = inspect.getsource(governed_change_service)
    assert 'operation == "UPDATE_PROFILE_CONFIG_SET"' in source
    assert 'payload.get("operation_type") == "UPDATE_PROFILE_CONFIG_SET"' in source
    assert 'change_source="analysis_chat_human_confirmed_bulk"' in source
    assert 'change_source="analysis_chat_human_confirmed_bulk_rollback"' in source
    assert '"profiles_deleted": False' in source


@pytest.mark.parametrize("mode", list(AnalysisChatDataMode))
def test_disabled_chat_rejects_every_mode(mode):
    with pytest.raises(AnalysisChatError, match="ANALYSIS_CHAT_DISABLED"):
        AnalysisChatService._require_mode(AnalysisChatRuntimeConfig(), mode)


def test_frozen_mode_is_the_only_default_enabled_mode():
    config = AnalysisChatRuntimeConfig(enabled=True)
    AnalysisChatService._require_mode(config, AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY)
    for mode in (
        AnalysisChatDataMode.ALLOW_READONLY_REFRESH,
        AnalysisChatDataMode.CREATE_CHILD_ANALYSIS,
        AnalysisChatDataMode.DRAFT_PROPOSAL,
    ):
        with pytest.raises(AnalysisChatError, match="MODE_DISABLED"):
            AnalysisChatService._require_mode(config, mode)


def test_request_kind_is_separate_from_data_mode():
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY
    ) is AnalysisChatRequestKind.FOLLOW_UP_CHAT
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.CREATE_CHILD_ANALYSIS
    ) is AnalysisChatRequestKind.CHILD_ANALYSIS
    assert AnalysisChatService._request_kind(
        AnalysisChatDataMode.DRAFT_PROPOSAL
    ) is AnalysisChatRequestKind.PROPOSAL_DRAFT


def test_explicit_profile_change_is_routed_to_governed_proposal():
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposals_enabled=True,
        governed_actions_enabled=True,
        live_config_write_enabled=True,
    )
    assert AnalysisChatService._resolve_data_mode(
        config,
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
        "realizar os ajustes em todos os perfis analisados.",
    ) is AnalysisChatDataMode.DRAFT_PROPOSAL


def test_analytical_deletion_question_stays_frozen_readonly():
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposals_enabled=True,
        governed_actions_enabled=True,
        live_config_write_enabled=True,
    )
    assert AnalysisChatService._resolve_data_mode(
        config,
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
        "quais seriam os perfis elegíveis para deletar?",
    ) is AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY


def test_explicit_change_never_auto_routes_when_governed_writes_are_disabled():
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposals_enabled=True,
        governed_actions_enabled=True,
        live_config_write_enabled=False,
    )
    assert AnalysisChatService._resolve_data_mode(
        config,
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
        "aplique os ajustes nos perfis",
    ) is AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY


def test_question_phrased_change_request_is_routed_to_governed_proposal():
    """A user asking 'how do I do X' about a specific, already-named target is
    routed the same as a direct command -- 'como remover o modo LEGACY dos 19
    profiles?' vetoed every governed action this session because it neither
    matched a prefix (question, not imperative) nor a target keyword ('profile'
    is not 'perfi')."""
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposals_enabled=True,
        governed_actions_enabled=True,
        live_config_write_enabled=True,
    )
    assert AnalysisChatService._resolve_data_mode(
        config,
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
        "como remover o modo LEGACY dos 19 profiles?",
    ) is AnalysisChatDataMode.DRAFT_PROPOSAL


def test_open_ended_question_about_profiles_stays_frozen_readonly():
    """'como' alone must not become a blanket trigger -- only 'como <verb>'
    matches; a question with no governed verb right after 'como' stays read-only."""
    config = AnalysisChatRuntimeConfig(
        enabled=True,
        proposals_enabled=True,
        governed_actions_enabled=True,
        live_config_write_enabled=True,
    )
    assert AnalysisChatService._resolve_data_mode(
        config,
        AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY,
        "como o RSI afeta o score desses profiles?",
    ) is AnalysisChatDataMode.FROZEN_ANALYSIS_ONLY


def test_aplicar_and_modificar_imperative_stem_change_is_matched():
    """aplicar/modificar take a c->qu stem change in the imperative (aplique,
    modifique) that the ar|e suffix group used by other -ar verbs cannot
    produce -- this previously never matched regardless of mode or targets."""
    assert AnalysisChatService._GOVERNED_ACTION_PREFIX.search("aplique os ajustes nos perfis")
    assert AnalysisChatService._GOVERNED_ACTION_PREFIX.search("modifique os thresholds dos perfis")


def test_handler_feeds_response_language_into_the_prompt_values():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    source = inspect.getsource(AnalysisChatGraphNodeHandler._prepare_normal_provider)
    assert '"response_language": str(request_json.get("response_language") or "pt-BR")' in source


def test_response_language_migration_is_well_formed_and_format_map_safe():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/174_chat_response_language.py"
    )
    spec = importlib.util.spec_from_file_location("chat_response_language_migration", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert len(migration.revision) <= 32
    assert migration.down_revision == "173_ai_profile_model_variants"

    # The instruction text must be format_map-safe on its own: exactly one
    # placeholder ({response_language}), no stray single braces that would
    # raise KeyError once substituted into a template and formatted -- this
    # is exactly the class of bug the orphaned governed-change 1.10.0 prompt
    # shipped (a literal, unescaped {"indicator":"rsi"} JSON example).
    instruction = migration.LANGUAGE_INSTRUCTION
    assert instruction.format_map({"response_language": "pt-BR"})
    assert instruction.count("{response_language}") == 1
    assert instruction.count("{") == 1 and instruction.count("}") == 1

    # The bump must target the version production actually serves --
    # selection is `ORDER BY approved_at DESC, semantic_version DESC`, not
    # the highest semantic_version string (semantic_version is plain text,
    # so "1.10.0" < "1.9.0" lexicographically and never wins that tiebreak;
    # 1.10.0 is also stale/orphaned in this table and unsafe to build on --
    # see the comment in migration.upgrade()).
    upgrade_source = inspect.getsource(migration.upgrade)
    assert 'from_version="1.1.0", to_version="1.2.0"' in upgrade_source
    assert 'from_version="1.9.0", to_version="1.9.1"' in upgrade_source
    assert 'from_version="1.10.0"' not in upgrade_source


def test_governed_config_scope_migration_exposes_only_typed_human_approved_families():
    from app.ai_orchestration.provider_adapters.http_adapter import (
        anthropic_output_config,
    )

    versions = Path(__file__).resolve().parents[1] / "alembic/versions"

    base_spec = importlib.util.spec_from_file_location(
        "chat_governed_scope_base",
        versions / "171_chat_no_if_then_else.py",
    )
    assert base_spec is not None and base_spec.loader is not None
    base_migration = importlib.util.module_from_spec(base_spec)
    base_spec.loader.exec_module(base_migration)
    previous = base_migration._prompt_content()
    previous["semantic_version"] = "1.9.1"
    previous["system_template"] = previous["system_template"].replace(
        "Answer in the question language.",
        "The required response language is {response_language}.",
    )

    scope_spec = importlib.util.spec_from_file_location(
        "chat_governed_config_scope_migration",
        versions / "189_chat_governed_config_scope.py",
    )
    assert scope_spec is not None and scope_spec.loader is not None
    migration = importlib.util.module_from_spec(scope_spec)
    scope_spec.loader.exec_module(migration)

    content = migration._expanded_prompt(previous)
    schema = content["output_schema_json"]
    system = content["system_template"]
    target_config_type = (
        schema["properties"]["proposal"]["anyOf"][0]["properties"]
        ["target"]["properties"]["config_type"]
    )

    assert len(migration.revision) <= 32
    assert migration.down_revision == "188_deepseek_quota_unbounded"
    assert content["semantic_version"] == "1.11.0"
    assert target_config_type["enum"] == [
        "score", "spot_engine", "futures_engine", "risk", "strategy", None,
    ]
    assert "lack a complete governed semantic validator" not in system
    assert "/sell_flow/trailing/activation_profit_pct" in system
    assert "/sell_flow/kill_switch/max_drawdown_from_hwm_pct" in system
    assert "Every other config family, runtime gate" in system
    assert content["provider_constraints_json"]["authority"] == "PROPOSAL_ONLY"
    assert content["tool_policy_json"]["execution_requires_human_interrupt"] is True
    assert system.format_map({"response_language": "pt-BR"})

    prepared = anthropic_output_config(schema)["format"]["schema"]
    prepared_config_type = (
        prepared["properties"]["proposal"]["anyOf"][0]["properties"]
        ["target"]["properties"]["config_type"]
    )
    assert prepared_config_type["enum"] == target_config_type["enum"]
    assert prepared_config_type["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


def test_governed_config_scope_migration_rejects_an_unexpected_base_contract():
    versions = Path(__file__).resolve().parents[1] / "alembic/versions"
    scope_spec = importlib.util.spec_from_file_location(
        "chat_governed_config_scope_rejection",
        versions / "189_chat_governed_config_scope.py",
    )
    assert scope_spec is not None and scope_spec.loader is not None
    migration = importlib.util.module_from_spec(scope_spec)
    scope_spec.loader.exec_module(migration)

    with pytest.raises(RuntimeError, match="AUTHORITY_TEXT_NOT_FOUND"):
        migration._expanded_prompt({
            "system_template": "unexpected",
            "output_schema_json": {},
        })


def test_governed_readback_prompt_treats_trailing_and_kill_switch_as_one_target():
    versions = Path(__file__).resolve().parents[1] / "alembic/versions"
    base_spec = importlib.util.spec_from_file_location(
        "chat_governed_scope_base_for_readback",
        versions / "171_chat_no_if_then_else.py",
    )
    assert base_spec is not None and base_spec.loader is not None
    base = importlib.util.module_from_spec(base_spec)
    base_spec.loader.exec_module(base)
    previous = base._prompt_content()
    previous["semantic_version"] = "1.9.1"
    previous["system_template"] = previous["system_template"].replace(
        "Answer in the question language.",
        "The required response language is {response_language}.",
    )

    scope_spec = importlib.util.spec_from_file_location(
        "chat_governed_scope_for_readback",
        versions / "189_chat_governed_config_scope.py",
    )
    assert scope_spec is not None and scope_spec.loader is not None
    scope = importlib.util.module_from_spec(scope_spec)
    scope_spec.loader.exec_module(scope)
    scoped = scope._expanded_prompt(previous)

    readback_spec = importlib.util.spec_from_file_location(
        "chat_governed_readback",
        versions / "190_chat_governed_readback.py",
    )
    assert readback_spec is not None and readback_spec.loader is not None
    readback = importlib.util.module_from_spec(readback_spec)
    readback_spec.loader.exec_module(readback)
    content = readback._expanded_prompt(scoped)
    system = content["system_template"]
    graph = readback._expanded_graph({
        "graph_key": "analysis-chat-v1",
        "state_schema_version": "analysis-chat-state-v1.1",
        "node_manifest": ["interrupt_proposal_confirmation", "plan_readonly_tools"],
        "edge_manifest": [[
            "interrupt_proposal_confirmation", "retrieve_relevant_evidence",
        ]],
    })

    assert len(readback.revision) <= 32
    assert readback.down_revision == "189_chat_governed_config_scope"
    assert content["semantic_version"] == "1.12.0"
    assert "fields of one spot_engine resource" in system
    assert "multiple non-overlapping changes for the same target" in system
    assert "global_risk.get_effective_policy" in system
    assert "strategies.get_execution_policy" in system
    assert "backend always re-reads the persisted document" in system
    assert "/selling/never_sell_at_loss=true" in system
    assert graph["semantic_version"] == "1.2.0"
    assert graph["tool_policy_version"] == "analysis-chat-governed-write-policy-v2"
    assert graph["edge_manifest"] == [[
        "interrupt_proposal_confirmation", "plan_readonly_tools",
    ]]


def test_explicit_proposal_graph_migration_versions_transport_free_policy():
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    spec = importlib.util.spec_from_file_location(
        "chat_explicit_proposal",
        versions / "191_chat_explicit_proposal.py",
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    graph = migration._expanded_graph({
        "graph_key": "analysis-chat-v1",
        "semantic_version": "1.2.0",
        "state_schema_version": "analysis-chat-state-v1.1",
        "node_manifest": ["invoke_provider"],
        "edge_manifest": [],
        "tool_policy_version": "analysis-chat-governed-write-policy-v2",
    })

    assert len(migration.revision) <= 32
    assert migration.down_revision == "190_chat_governed_readback"
    assert graph["semantic_version"] == "1.3.0"
    assert graph["tool_policy_version"] == "analysis-chat-governed-write-policy-v3"


def test_staging_fake_intent_is_environment_and_flag_bounded(monkeypatch):
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "systemic-ai-staging-20260807")
    monkeypatch.setenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "true")
    assert AnalysisChatService._intent() == "FAKE_PROVIDER_CANARY"
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    assert AnalysisChatService._intent() == "NORMAL_ANALYSIS"


def test_analysis_chat_graph_is_immutable_and_separate():
    graph = resolve_graph("analysis-chat-v1")
    assert graph.semantic_version == "1.3.0"
    assert graph.state_schema_version == "analysis-chat-state-v1.1"
    assert graph.tool_policy_version == "analysis-chat-governed-write-policy-v3"
    assert graph.content_hash == "0f4dcf16ab49053b90512d8987f32b89358d45ec636b9dcd77f82037b13e834e"


def test_analysis_chat_graph_compiles_all_human_gates():
    graph = build_graph("analysis-chat-v1")
    rendered = graph.get_graph().draw_mermaid()
    assert "interrupt_child_analysis_confirmation" in rendered
    assert "interrupt_proposal_confirmation" in rendered
    assert "interrupt_proposal_approval" in rendered


def test_chat_thread_mapping_cannot_reuse_parent_thread():
    conversation_id = uuid.uuid4()
    thread_label = f"analysis-chat:{conversation_id}"
    checkpoint_thread = uuid.uuid5(uuid.NAMESPACE_URL, thread_label)
    parent_thread = uuid.uuid4()
    assert thread_label.startswith("analysis-chat:")
    assert checkpoint_thread != parent_thread


def test_chat_on_chat_selection_resolves_to_canonical_analysis_parent():
    source = inspect.getsource(AnalysisChatService._canonical_parent)
    send_source = inspect.getsource(AnalysisChatService.send_message)
    create_source = inspect.getsource(AnalysisChatService.create_conversation)
    list_source = inspect.getsource(AnalysisChatService.list_conversations)
    assert 'definition.graph_key != "analysis-chat-v1"' in source
    assert "request.parent_analysis_run_id" in source
    assert "ANALYSIS_CHAT_PARENT_NESTING_LIMIT_EXCEEDED" in source
    assert "canonical_run.id != parent_run.id" in send_source
    assert "ANALYSIS_CHAT_NESTED_CONVERSATION_NOT_EMPTY" in send_source
    assert "_canonical_parent" in create_source
    assert "_canonical_parent" in list_source


def test_checkpoint_contract_rejects_secrets_and_accepts_chat_ids():
    assert_checkpoint_safe({
        "conversation_id": str(uuid.uuid4()),
        "message_id": str(uuid.uuid4()),
        "selected_evidence_refs": [{"evidence_id": str(uuid.uuid4())}],
    })
    with pytest.raises(ValueError, match="forbidden key"):
        assert_checkpoint_safe({"conversation_id": "x", "provider_key": "secret"})


def test_chat_output_requires_parent_and_evidence_contract():
    parent_id = uuid.uuid4()
    output = AnalysisChatOutput(
        answer="Resposta limitada ao snapshot.",
        answer_type="EXPLANATION",
        based_on="FROZEN_ANALYSIS",
        parent_analysis_run_id=parent_id,
        evidence_refs=[{"evidence_id": str(uuid.uuid4()), "module": "score_engine"}],
    )
    assert output.parent_analysis_run_id == parent_id
    assert output.new_data_queried is False


def test_provider_parent_id_is_normalized_to_the_canonical_conversation_run():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _normalize_provider_parent,
    )

    canonical_parent = uuid.uuid4()
    provider_output = AnalysisChatOutput(
        answer="Resposta limitada ao snapshot.",
        answer_type="EXPLANATION",
        based_on="FROZEN_ANALYSIS",
        parent_analysis_run_id=uuid.uuid4(),
        evidence_refs=[],
    )

    normalized = _normalize_provider_parent(provider_output, canonical_parent)

    assert normalized.parent_analysis_run_id == canonical_parent
    assert "PROVIDER_PARENT_ANALYSIS_RUN_ID_NORMALIZED" in normalized.warnings


def test_failed_chat_turn_persists_audited_provider_usage():
    from app.tasks.ai_orchestration import (
        _audited_provider_transport_attempted,
        _mark_failed,
    )

    source = inspect.getsource(_mark_failed)
    assert "AIUsageRecord" in source
    assert "message.tokens_input" in source
    assert "message.tokens_output" in source
    assert "message.cost_usd" in source
    assert "conversation.total_cost_usd" in source
    assert _audited_provider_transport_attempted(
        False,
        reservation=SimpleNamespace(provider_transport_attempted=True),
        usage=None,
    ) is True
    assert _audited_provider_transport_attempted(
        False,
        reservation=SimpleNamespace(provider_transport_attempted=False),
        usage=SimpleNamespace(tokens_input=10, tokens_output=0, actual_cost=0),
    ) is True
    assert _audited_provider_transport_attempted(
        False,
        reservation=SimpleNamespace(provider_transport_attempted=False),
        usage=SimpleNamespace(tokens_input=0, tokens_output=0, actual_cost=0),
    ) is False


def test_frozen_mode_executes_no_new_tools():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert '["market_regime.get_current"]' in source
    assert "if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL else []" in source


def test_readonly_refresh_has_a_single_none_side_effect_tool_path():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert 'allowlist != ["market_regime.get_current"]' in source
    assert "ModuleToolRuntime" in source
    assert "LIVE_WRITE" not in source
    assert "READONLY_REFRESH_CURRENT_SNAPSHOT_ONLY" in source
    assert '"effective_coverage": "CURRENT_SNAPSHOT_ONLY"' in source
    assert "message.tool_call_ids_json" in source


def test_child_analysis_requires_interrupt_and_does_not_reuse_parent_snapshot():
    graph = resolve_graph("analysis-chat-v1")
    assert "interrupt_child_analysis_confirmation" in graph.node_manifest
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "CHILD_ANALYSIS_REQUIRES_FRESH_DATASET_AND_BUNDLE" in source
    assert "Nenhuma análise filha foi criada" in source


def test_proposal_is_typed_and_human_gated_twice_before_execution():
    graph = resolve_graph("analysis-chat-v1")
    assert "interrupt_proposal_confirmation" in graph.node_manifest
    assert "interrupt_proposal_approval" in graph.node_manifest
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "create_governed_change_dry_run" in source
    assert "canonical_refs = await _load_canonical_evidence_refs" in source
    assert "evidence_ids = await _load_canonical_evidence_ids" in source
    assert "_materialize_governed_proposal" in source
    assert "execute_governed_proposal_if_confirmed" in source
    assert "approve_and_execute_governed_change" in source
    assert "ANALYSIS_CHAT_GOVERNED_CHANGE_ACTOR_MISMATCH" in source
    assert 'decision.get("decision") != "approve"' in source
    assert "if not actor_user_id or str(actor_user_id) != str(request.requested_by_user_id)" in source
    assert 'not in {"approve", "edit"}' not in source
    assert graph.edge_manifest.index(
        ("interrupt_proposal_approval", "execute_governed_proposal_if_confirmed")
    ) > graph.edge_manifest.index(
        ("draft_proposal_if_confirmed", "validate_risk_and_strategy")
    )


def test_risk_strategy_gate_uses_candidate_aware_db_validation_not_catalog_tools():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert "validate_candidate_for_second_gate" in source
    assert "ANALYSIS_CHAT_RISK_STRATEGY_CANDIDATE_VETO" in source
    assert "GOVERNED_CANDIDATE_DETERMINISTIC_VALIDATION_PASSED" in source
    assert 'answer["modules_consulted"] = ["governed_change_candidate_validator"]' in source
    validator_block = source.split(
        'if node_name == "validate_risk_and_strategy":', 1
    )[1].split(
        'if node_name == "execute_governed_proposal_if_confirmed":', 1
    )[0]
    assert "ModuleToolRuntime" not in validator_block
    assert "runtime.execute" not in validator_block
    assert "candidate_validation" in validator_block
    assert '"validation_scope": validation["validation_scope"]' in validator_block
    assert '"policy_semantic_validation": validation[' in validator_block
    assert '"risk_validation": validation["risk_validation"]' not in validator_block
    assert '"strategy_validation": validation["strategy_validation"]' not in validator_block


def test_provider_normalization_preserves_typed_proposal_for_materialization():
    from app.ai_orchestration.errors import ProviderOutputError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
        _normalized_provider_mode,
    )
    from app.schemas.analysis_chat import AnalysisChatOutput

    source = inspect.getsource(AnalysisChatGraphNodeHandler._build_provider_answer)
    assert 'is_proposal = invocation.data_mode == "DRAFT_PROPOSAL"' in source
    assert "_normalized_provider_mode" in source

    base = AnalysisChatOutput(
        answer="No concrete path",
        answer_type="LIMITATION",
        based_on="PROPOSAL_DRAFT",
        parent_analysis_run_id=uuid.uuid4(),
        proposal=None,
    )
    assert _normalized_provider_mode(
        base,
        is_proposal=True,
        refreshed=False,
    ) == ("LIMITATION", "PROPOSAL_DRAFT", None)

    with pytest.raises(ProviderOutputError) as exc_info:
        _normalized_provider_mode(
            base.model_copy(update={"answer_type": "PROPOSAL"}),
            is_proposal=True,
            refreshed=False,
        )
    assert exc_info.value.reason_code == "ANALYSIS_CHAT_PROPOSAL_OUTPUT_MISSING"

    with pytest.raises(ProviderOutputError) as exc_info:
        _normalized_provider_mode(
            base.model_copy(update={"proposal": {"changes": []}}),
            is_proposal=True,
            refreshed=False,
        )
    assert exc_info.value.reason_code == "ANALYSIS_CHAT_PROPOSAL_OUTPUT_INCONSISTENT"


def test_each_successful_turn_reconciles_a_budget_reservation():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._persist_answer)
    assert "await self._ensure_reservation" in source
    assert 'reservation.status = "RECONCILED"' in source
    assert "provider_transport_attempted = False" in source


def test_every_accepted_turn_reserves_budget_before_human_interrupts():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert "AIBudgetReservationRecord(" in source
    assert 'status="RESERVED"' in source
    assert "provider_transport_attempted=False" in source


def test_cancel_terminalizes_job_and_delegates_reservation_reconciliation():
    source = inspect.getsource(AnalysisChatService.cancel)
    assert "_cancel_budget_reservation(reservation, now=now)" in source
    assert 'job.status = "CANCELLED"' in source
    assert '"CANCELLED_BY_AUTHORIZED_ACTOR"' in source


@pytest.mark.asyncio
async def test_exact_chat_run_cancel_terminalizes_every_durable_record():
    from app.models.ai_graph import AIGraphEvent

    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    message = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        graph_run_id=run_id,
        ai_request_id=request_id,
        role="ASSISTANT",
        status="STREAMING",
        sequence_number=2,
        provider_transport_attempted=False,
        lock_version=0,
        cancelled_at=None,
        completed_at=None,
    )
    run = SimpleNamespace(
        id=run_id,
        tenant_id=tenant_id,
        status="RUNNING",
        current_node="invoke_provider",
        cancelled_at=None,
        completed_at=None,
        terminal_reason=None,
        provider_transport_attempted=False,
        heartbeat_at=None,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        updated_at=None,
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        tenant_id=tenant_id,
        lock_version=4,
        updated_at=None,
    )
    reservation = SimpleNamespace(
        status="RESERVED",
        provider_transport_attempted=False,
        reserved_tokens=1234,
        actual_tokens=None,
        actual_cost_usd=None,
        released_tokens=0,
        terminal_reason=None,
        released_at=None,
        updated_at=None,
    )
    job = SimpleNamespace(
        status="RUNNING",
        completed_at=None,
        terminal_reason=None,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter(
                (message, run, conversation, message, reservation, job, None)
            )
            self.statements = []
            self.added = []

        async def execute(self, statement):
            self.statements.append(str(statement))
            return _Result(next(self.results))

        def add(self, value):
            self.added.append(value)

    db = _DB()
    cancelled = await AnalysisChatService.cancel(
        db,
        tenant_id=tenant_id,
        user_id=tenant_id,
        conversation_id=conversation_id,
        graph_run_id=run_id,
    )

    assert cancelled is message
    assert "ai_analysis_messages.graph_run_id" in db.statements[0]
    assert message.status == "CANCELLED"
    assert message.completed_at is not None
    assert job.status == "CANCELLED"
    assert job.terminal_reason == "CANCELLED_BY_AUTHORIZED_ACTOR"
    assert reservation.status == "RELEASED"
    assert reservation.released_tokens == 1234
    assert reservation.terminal_reason == "CANCELLED_BEFORE_PROVIDER_TRANSPORT"
    assert run.status == "CANCELLED"
    assert run.terminal_reason == "CANCELLED_BY_AUTHORIZED_ACTOR"
    events = [item for item in db.added if isinstance(item, AIGraphEvent)]
    assert len(events) == 1
    assert events[0].graph_run_id == run_id
    assert events[0].event_type == "CANCELLED"
    assert events[0].payload["message_id"] == str(message.id)


@pytest.mark.asyncio
async def test_cancel_after_usage_reconciliation_attributes_message_and_conversation():
    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    message = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        graph_run_id=run_id,
        ai_request_id=request_id,
        role="ASSISTANT",
        status="STREAMING",
        sequence_number=2,
        provider_transport_attempted=True,
        tokens_input=None,
        tokens_output=None,
        cost_usd=None,
        lock_version=0,
        cancelled_at=None,
        completed_at=None,
    )
    run = SimpleNamespace(
        id=run_id,
        tenant_id=tenant_id,
        status="RUNNING",
        current_node="invoke_provider",
        cancelled_at=None,
        completed_at=None,
        terminal_reason=None,
        provider_transport_attempted=True,
        heartbeat_at=None,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        updated_at=None,
    )
    conversation = SimpleNamespace(
        id=conversation_id,
        tenant_id=tenant_id,
        total_tokens_input=100,
        total_tokens_output=20,
        total_cost_usd=Decimal("0.10"),
        lock_version=4,
        updated_at=None,
    )
    reservation = SimpleNamespace(
        status="RECONCILED",
        provider_transport_attempted=True,
        reserved_tokens=1234,
        actual_tokens=15,
        actual_cost_usd=Decimal("0.000020"),
        released_tokens=1219,
        terminal_reason="PROVIDER_RESPONSE_RECEIVED",
        released_at=None,
        updated_at=None,
    )
    job = SimpleNamespace(
        status="RUNNING",
        completed_at=None,
        terminal_reason=None,
        lease_owner="worker-1",
        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
    )
    usage = SimpleNamespace(
        tokens_input=10,
        tokens_output=5,
        actual_cost=Decimal("0.000020"),
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter(
                (message, run, conversation, message, reservation, job, usage)
            )
            self.added = []

        async def execute(self, _statement):
            return _Result(next(self.results))

        def add(self, value):
            self.added.append(value)

    cancelled = await AnalysisChatService.cancel(
        _DB(),
        tenant_id=tenant_id,
        user_id=tenant_id,
        conversation_id=conversation_id,
        graph_run_id=run_id,
    )

    assert cancelled is message
    assert message.status == "CANCELLED"
    assert message.tokens_input == 10
    assert message.tokens_output == 5
    assert message.cost_usd == Decimal("0.000020")
    assert conversation.total_tokens_input == 110
    assert conversation.total_tokens_output == 25
    assert conversation.total_cost_usd == Decimal("0.100020")
    assert reservation.status == "RECONCILED"
    assert run.status == "CANCELLED"


@pytest.mark.asyncio
async def test_generic_graph_cancel_delegates_exact_analysis_chat_run(monkeypatch):
    from app.api.ai_graphs import cancel_graph_run
    from app.models.ai_graph import AIGraphDefinition
    from app.models.systemic_ai import AIRequestRecord
    from app.services.ai_graph_service import AIGraphRunService

    tenant_id = uuid.uuid4()
    conversation_id = uuid.uuid4()
    run_id = uuid.uuid4()
    request_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        ai_request_id=request_id,
        graph_definition_id=definition_id,
        status="RUNNING",
        current_node="invoke_provider",
        last_completed_node="assemble_evidence",
        failed_node=None,
        authority="ANALYSIS_ONLY",
        state_schema_version="1.1.0",
        started_at=None,
        completed_at=None,
        terminal_reason=None,
        last_error_code=None,
        last_error_safe_message=None,
        error_kind=None,
        provider_transport_attempted=False,
        created_at=None,
        updated_at=None,
    )
    definition = SimpleNamespace(
        id=definition_id,
        graph_key="analysis-chat-v1",
        semantic_version="1.1.0",
    )
    request = SimpleNamespace(
        id=request_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        request_json={"request_intent": "NORMAL_ANALYSIS"},
        origin_module="analysis_chat",
        origin_view="intelligence-runs",
        correlation_id="chat-correlation",
    )
    delegated = {}

    class _DB:
        def __init__(self):
            self.committed = False
            self.refreshed = False

        async def get(self, model, key):
            if model is AIGraphDefinition and key == definition_id:
                return definition
            if model is AIRequestRecord and key == request_id:
                return request
            return None

        async def commit(self):
            self.committed = True

        async def refresh(self, value):
            assert value is run
            self.refreshed = True

    async def _get(_db, *, tenant_id, run_id):
        assert tenant_id == tenant_id_value
        assert run_id == run_id_value
        return run

    async def _cancel(
        _db,
        *,
        tenant_id,
        user_id,
        conversation_id,
        graph_run_id,
    ):
        delegated.update(
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            graph_run_id=graph_run_id,
        )
        run.status = "CANCELLED"
        return SimpleNamespace(status="CANCELLED")

    tenant_id_value = tenant_id
    run_id_value = run_id
    monkeypatch.setattr(AIGraphRunService, "get", staticmethod(_get))
    monkeypatch.setattr(AnalysisChatService, "cancel", staticmethod(_cancel))
    db = _DB()

    payload = await cancel_graph_run(run_id, db=db, user_id=tenant_id)

    assert delegated == {
        "tenant_id": tenant_id,
        "user_id": tenant_id,
        "conversation_id": conversation_id,
        "graph_run_id": run_id,
    }
    assert db.committed is True
    assert db.refreshed is True
    assert payload["status"] == "CANCELLED"
    assert payload["graph_key"] == "analysis-chat-v1"


@pytest.mark.asyncio
async def test_broker_publish_failure_returns_durable_dispatch_pending():
    from app.api.analysis_chat import (
        _publish_durable_graph_dispatch,
        decide_message,
        send_message,
    )
    from app.tasks import ai_orchestration as task_module

    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        tenant_id=tenant_id,
        current_node="invoke_provider",
    )

    class _BrokerDownTask:
        @staticmethod
        def apply_async(**_kwargs):
            raise ConnectionError("broker unavailable")

    class _DB:
        def __init__(self):
            self.statements = []
            self.commits = 0
            self.rollbacks = 0

        async def execute(self, statement):
            self.statements.append(statement)

        async def commit(self):
            self.commits += 1

        async def rollback(self):
            self.rollbacks += 1

    db = _DB()
    dispatch = await _publish_durable_graph_dispatch(
        db,
        run=run,
        task=_BrokerDownTask,
        args=[str(run_id)],
        dispatch_key="START",
        audit_payload={"dispatch_kind": "START"},
    )

    assert dispatch == {
        "dispatch_pending": True,
        "dispatch_retry": "DURABLE_QUEUED_DISPATCHER",
        "dispatch_audit_persisted": True,
    }
    assert db.commits == 1
    assert db.rollbacks == 0
    assert len(db.statements) == 1
    params = db.statements[0].compile().params
    assert "DISPATCH_PENDING" in params.values()
    assert any(
        isinstance(value, dict)
        and value.get("retry_strategy") == "DURABLE_QUEUED_DISPATCHER"
        and value.get("publish_error_type") == "ConnectionError"
        for value in params.values()
    )

    send_source = inspect.getsource(send_message)
    decision_source = inspect.getsource(decide_message)
    assert "_publish_durable_graph_dispatch" in send_source
    assert "_publish_durable_graph_dispatch" in decision_source
    dispatcher_source = inspect.getsource(task_module.dispatch_queued_graph_runs)
    assert 'AIGraphRun.status == "QUEUED"' in dispatcher_source
    assert "_queued_dispatch_spec" in dispatcher_source


def test_rejected_human_gate_releases_the_pretransport_budget_reservation():
    from app.tasks.ai_orchestration import _mark_terminal

    source = inspect.getsource(_mark_terminal)
    assert 'reservation.status == "RESERVED"' in source
    assert 'reservation.status = "RELEASED"' in source
    assert 'reservation.provider_transport_attempted = False' in source
    assert 'reservation.terminal_reason = "HUMAN_GATE_REJECTED"' in source


def test_provider_blocked_is_typed_and_releases_before_transport():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._handle_provider_node)
    assert "ProviderBlockedError" in source
    assert "NORMAL_ANALYSIS_PROVIDER_DISABLED" in source
    assert "_prepare_normal_provider" in source


def test_chat_real_provider_is_per_turn_approved_and_budget_audited():
    from app.ai_orchestration.budget_reservation_audit import BudgetReservationAudit
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    send_source = inspect.getsource(AnalysisChatService.send_message)
    prepare_source = inspect.getsource(AnalysisChatGraphNodeHandler._prepare_normal_provider)
    invoke_source = inspect.getsource(AnalysisChatGraphNodeHandler._handle_provider_node)
    assert 'scope="ANALYSIS_CHAT_TURN"' in send_source
    assert 'approval_method="ANALYSIS_CHAT_SEND_ACTION"' in send_source
    assert "activate_placeholder" in prepare_source
    assert "mark_transport_started" in invoke_source
    assert "_reconcile_provider_response" in invoke_source
    assert hasattr(BudgetReservationAudit, "activate_placeholder")


def test_provider_decision_context_keeps_rows_and_outputs_without_ledger_duplication():
    from app.services.systemic_langgraph_bridge import _provider_decision_context

    rows = [{"id": "row-1", "score": 42}]
    output = {"quality": "PASS", "freshness": {"age": 1}, "data": rows}
    evidence = SimpleNamespace(
        id=uuid.uuid4(), module_key="shadow_portfolio", tool_name="shadow.test",
        output_json=output, output_hash="hash", quality="PASS",
        freshness_json={"age": 1},
    )
    payload = json.loads(_provider_decision_context({
        "rows": rows,
        "context": {"timeframe": "1h"},
        "context_manifest": {"ledger_only": True},
        "model_approval_id": str(uuid.uuid4()),
    }, [evidence]))
    assert payload["frozen_context"] == {
        "rows": rows, "context": {"timeframe": "1h"},
    }
    assert payload["typed_tool_evidence"][0]["output"] == output
    assert "output_hash" not in payload["typed_tool_evidence"][0]


def test_running_summary_is_versioned_hashed_and_does_not_replace_evidence():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    assert 'conversation.summary_version = "analysis-chat-summary@1.0.0"' in source
    assert "conversation.summary_hash = _sha(summary)" in source
    assert "selected_evidence_refs" in source


def test_message_idempotency_and_sequence_are_transactional():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert "lock=True" in source
    assert "idempotency_key == idempotency_key" in source
    assert "conversation.message_count" in source


def test_prompt_injection_cannot_expand_tools_or_authority():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert '"PROPOSAL_ONLY"' in source
    assert 'else "ANALYSIS_ONLY"' in source
    assert "if data_mode is AnalysisChatDataMode.DRAFT_PROPOSAL" in source
    assert "data_mode=data_mode.value" in source
    assert "tool_allowlist" in source
    assert "normalized" not in source.split('"tool_allowlist":', 1)[1].split("},", 1)[0]


def test_no_order_ml_promotion_or_spot_mutation_path_exists():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler
    source = inspect.getsource(AnalysisChatGraphNodeHandler)
    forbidden = ("Order(", "create_order", "promote_model", "spot_engine", "LIVE_WRITE")
    assert not any(value in source for value in forbidden)


def test_run_acquisition_does_not_read_terminal_state_before_execution():
    from app.tasks.ai_orchestration import _acquire_run

    source = inspect.getsource(_acquire_run)
    assert "final_state" not in source
    assert "provider_transport_attempted = None" in source


def test_terminal_message_cannot_regress_to_streaming_in_tail_nodes():
    from app.ai_orchestration.langgraph.analysis_chat_handler import AnalysisChatGraphNodeHandler

    source = inspect.getsource(AnalysisChatGraphNodeHandler._lock_node_context)
    assert 'message.status not in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}' in source


def test_each_turn_has_a_unique_checkpoint_thread_under_the_conversation():
    source = inspect.getsource(AnalysisChatService.send_message)
    assert 'f"{conversation.thread_id}:message:{user_message.id}"' in source


def test_chat_stream_starts_after_the_accepted_turn_boundary():
    from app.api import analysis_chat

    source = inspect.getsource(analysis_chat)
    assert source.count('"stream_after_event_id": stream_after_event_id') == 2
    assert "AIGraphEvent.graph_run_id == graph_run_id" in source


def test_cancel_before_transport_releases_the_reservation():
    from datetime import datetime, timezone
    from app.services.analysis_chat_service import _cancel_budget_reservation

    reservation = SimpleNamespace(
        status="RESERVED",
        provider_transport_attempted=False,
        reserved_tokens=321,
        actual_tokens=None,
        actual_cost_usd=None,
        released_tokens=0,
        terminal_reason=None,
        released_at=None,
        updated_at=None,
    )
    now = datetime.now(timezone.utc)
    assert _cancel_budget_reservation(reservation, now=now) is False
    assert reservation.status == "RELEASED"
    assert reservation.released_tokens == 321
    assert reservation.provider_transport_attempted is False
    assert reservation.terminal_reason == "CANCELLED_BEFORE_PROVIDER_TRANSPORT"


def test_cancel_after_transport_keeps_the_reservation_reconcilable():
    from datetime import datetime, timezone
    from app.services.analysis_chat_service import _cancel_budget_reservation

    reservation = SimpleNamespace(
        status="TRANSPORT_STARTED",
        provider_transport_attempted=True,
        reserved_tokens=321,
        released_tokens=0,
        terminal_reason=None,
        released_at=None,
        updated_at=None,
    )
    now = datetime.now(timezone.utc)
    assert _cancel_budget_reservation(reservation, now=now) is True
    assert reservation.status == "TRANSPORT_STARTED"
    assert reservation.released_tokens == 0
    assert reservation.released_at is None
    assert reservation.provider_transport_attempted is True
    assert (
        reservation.terminal_reason
        == "CANCELLED_AFTER_PROVIDER_TRANSPORT_STARTED"
    )


def test_cancel_reconciled_fake_reservation_does_not_invent_provider_transport():
    from datetime import datetime, timezone
    from app.services.analysis_chat_service import _cancel_budget_reservation

    reservation = SimpleNamespace(
        status="RECONCILED",
        provider_transport_attempted=False,
        actual_tokens=0,
        actual_cost_usd=Decimal("0"),
        reserved_tokens=0,
        released_tokens=0,
        terminal_reason="FAKE_PROVIDER_RESPONSE_RECEIVED",
        released_at=None,
        updated_at=None,
    )

    assert _cancel_budget_reservation(
        reservation,
        now=datetime.now(timezone.utc),
    ) is False
    assert reservation.status == "RECONCILED"
    assert reservation.provider_transport_attempted is False
    assert reservation.terminal_reason == "FAKE_PROVIDER_RESPONSE_RECEIVED"


@pytest.mark.asyncio
async def test_terminal_graph_run_cannot_execute_another_chat_node():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    run_id = uuid.uuid4()
    run = SimpleNamespace(id=run_id, status="CANCELLED")

    class _Result:
        def scalar_one_or_none(self):
            return run

    class _DB:
        async def execute(self, _statement):
            return _Result()

    handler = AnalysisChatGraphNodeHandler(run_id, celery=False)
    with pytest.raises(RuntimeError, match="ANALYSIS_CHAT_GRAPH_RUN_CANCELLED"):
        await handler._lock_node_context(
            _DB(), node_name="validate_chat_output", state={"tenant_id": "unused"}
        )


@pytest.mark.asyncio
async def test_chat_node_lifecycle_matches_the_canonical_run_lease(monkeypatch):
    from app.ai_orchestration.langgraph import analysis_chat_handler as handler_module
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )

    run_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        tenant_id=tenant_id,
        ai_request_id=request_id,
        status="RUNNING",
        current_node=None,
        last_completed_node=None,
        heartbeat_at=None,
        lease_expires_at=None,
        updated_at=None,
    )
    request = SimpleNamespace(
        id=request_id,
        tenant_id=tenant_id,
        conversation_id=uuid.uuid4(),
        request_json={"data_mode": "FROZEN_ANALYSIS_ONLY"},
    )
    message = SimpleNamespace(
        id=uuid.uuid4(), status="QUEUED", lock_version=0
    )

    class _Result:
        def __init__(self, value):
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    class _DB:
        def __init__(self):
            self.results = iter((_Result(run), _Result(message)))

        async def execute(self, _statement):
            return next(self.results, _Result(None))

        async def get(self, _model, key):
            return request if key == request_id else None

    monkeypatch.setattr(
        handler_module,
        "get_langgraph_settings",
        lambda: SimpleNamespace(lease_seconds=90),
    )
    db = _DB()
    handler = AnalysisChatGraphNodeHandler(run_id, celery=False)
    locked_run, locked_request, locked_message = await handler._lock_node_context(
        db,
        node_name="validate_chat_output",
        state={"tenant_id": str(tenant_id)},
    )
    assert (locked_run, locked_request, locked_message) == (run, request, message)
    assert run.current_node == "validate_chat_output"
    assert run.heartbeat_at is not None
    assert run.lease_expires_at > run.heartbeat_at
    assert message.status == "STREAMING"

    await handler._complete_node(
        db, run, request, message, "validate_chat_output", {}
    )
    assert run.last_completed_node == "validate_chat_output"
    assert run.current_node == "validate_chat_output"


@pytest.mark.asyncio
async def test_provider_transport_method_has_no_database_session(monkeypatch):
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )
    from app.services.systemic_langgraph_bridge import SystemicLangGraphBridge

    parameters = inspect.signature(
        AnalysisChatGraphNodeHandler._invoke_normal_provider
    ).parameters
    assert "db" not in parameters

    called = {}

    async def _fake_transport(**kwargs):
        called.update(kwargs)
        return "provider-response"

    monkeypatch.setattr(
        SystemicLangGraphBridge, "execute_json_provider", _fake_transport
    )
    invocation = SimpleNamespace(
        provider="anthropic",
        model="model",
        system_prompt="system",
        user_prompt="user",
        api_key="secret",
        request_id=uuid.uuid4(),
        max_output_tokens=10,
        output_schema={"type": "object"},
    )
    handler = AnalysisChatGraphNodeHandler(uuid.uuid4(), celery=False)
    assert await handler._invoke_normal_provider(invocation) == "provider-response"
    assert called["request_id"] == str(invocation.request_id)


def test_sse_route_does_not_hold_an_injected_database_session():
    from app.api.analysis_chat import stream_conversation

    assert "db" not in inspect.signature(stream_conversation).parameters


def _label_selected_evidence_refs():
    return (
        {"evidence_id": "11111111-1111-1111-1111-111111111111"},
        {"evidence_id": "22222222-2222-2222-2222-222222222222"},
        {"evidence_id": "33333333-3333-3333-3333-333333333333"},
    )


def test_change_evidence_label_translates_to_the_real_uuid_in_presented_order():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_change_evidence_labels,
    )

    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "changes": [
            {"op": "replace", "path": "/x", "evidence_refs": ["E1", "E3"]},
            {"op": "replace", "path": "/y", "evidence_refs": ["E2"]},
        ],
    }
    translated = _translate_change_evidence_labels(proposal, _label_selected_evidence_refs())
    assert translated["changes"][0]["evidence_refs"] == [
        "11111111-1111-1111-1111-111111111111",
        "33333333-3333-3333-3333-333333333333",
    ]
    assert translated["changes"][1]["evidence_refs"] == [
        "22222222-2222-2222-2222-222222222222",
    ]


def test_change_evidence_label_outside_the_presented_menu_is_rejected_with_valid_labels():
    from app.ai_orchestration.errors import ProviderOutputError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_change_evidence_labels,
    )

    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "changes": [{"op": "replace", "path": "/x", "evidence_refs": ["E7"]}],
    }
    with pytest.raises(ProviderOutputError) as excinfo:
        _translate_change_evidence_labels(proposal, _label_selected_evidence_refs())
    message = str(excinfo.value)
    assert "E1" in message and "E2" in message and "E3" in message


def test_change_evidence_raw_uuid_is_rejected_same_as_an_invalid_label():
    # Regression guard for the exact fabricated UUID found in production by
    # CHAT-003: a raw identifier is not a presented label, so it is rejected
    # by the same path as any other unlisted label.
    from app.ai_orchestration.errors import ProviderOutputError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_change_evidence_labels,
    )

    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "changes": [{
            "op": "replace", "path": "/x",
            "evidence_refs": ["d78d1afb-be11-42c5-aa3e-bcd963c9b58f"],
        }],
    }
    with pytest.raises(ProviderOutputError):
        _translate_change_evidence_labels(proposal, _label_selected_evidence_refs())


def test_change_evidence_label_translation_is_a_noop_for_a_limitation_answer():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_change_evidence_labels,
    )

    assert _translate_change_evidence_labels(None, _label_selected_evidence_refs()) is None


class _ScoreDocResult:
    def __init__(self, config_profile):
        self._config_profile = config_profile

    def scalar_one_or_none(self):
        return self._config_profile


class _ScoreDocDB:
    def __init__(self, config_profile):
        self._config_profile = config_profile

    async def execute(self, _query):
        return _ScoreDocResult(self._config_profile)


def _score_document_config_profile():
    from types import SimpleNamespace

    return SimpleNamespace(config_json={
        "weights": {"signal": 15},
        "thresholds": {"buy": 68},
        "scoring_rules": [
            {"id": "rule_volume_24h_ge_1000000", "points": 4},
            {"id": "rule_rsi_between_68_78", "points": 4},
            {"id": "rule_macd_gt_0", "points": 5},
        ],
    })


@pytest.mark.asyncio
async def test_score_rule_id_translates_to_the_real_array_index_and_old_value():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_score_rule_points,
    )

    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": "score"},
        "changes": [{
            "op": "replace", "rule_id": "rule_rsi_between_68_78",
            "value_json": "2", "evidence_refs": ["E1"],
        }],
    }
    translated = await _translate_score_rule_points(
        _ScoreDocDB(_score_document_config_profile()),
        tenant_id=uuid.uuid4(),
        proposal=proposal,
    )
    change = translated["changes"][0]
    assert change["path"] == "/scoring_rules/1/points"
    assert change["old_value_json"] == "4"
    assert json.loads(change["array_guards_json"]) == [
        {"path": "/scoring_rules/1", "identity": {"id": "rule_rsi_between_68_78"}}
    ]
    assert "rule_id" not in change


@pytest.mark.asyncio
async def test_score_rule_id_outside_the_persisted_set_is_rejected_with_valid_ids():
    # Regression guard for the exact historical production failure:
    # EXPECTED_AN_OBJECT_AT_/SCORING/RULES/RSI_OVERBOUGHT_PENALTY. The model
    # invented "rsi_overbought_penalty"; the real id is
    # rule_rsi_between_68_78. This must now fail before create_dry_run, with
    # a message naming the real persisted ids.
    from app.ai_orchestration.errors import ProviderOutputError
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_score_rule_points,
    )

    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": "score"},
        "changes": [{
            "op": "replace", "rule_id": "rsi_overbought_penalty",
            "value_json": "2", "evidence_refs": ["E1"],
        }],
    }
    with pytest.raises(ProviderOutputError) as excinfo:
        await _translate_score_rule_points(
            _ScoreDocDB(_score_document_config_profile()),
            tenant_id=uuid.uuid4(),
            proposal=proposal,
        )
    assert "rule_rsi_between_68_78" in str(excinfo.value)


@pytest.mark.asyncio
async def test_score_rule_id_translation_is_a_noop_without_any_rule_id():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _translate_score_rule_points,
    )

    proposal = {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "target": {"profile_id": "x"},
        "changes": [{"op": "replace", "path": "/filters/conditions/0/value", "value_json": "0.6"}],
    }
    translated = await _translate_score_rule_points(
        _ScoreDocDB(_score_document_config_profile()),
        tenant_id=uuid.uuid4(),
        proposal=proposal,
    )
    assert translated == proposal


@pytest.mark.asyncio
async def test_spot_proposal_preconditions_are_hydrated_from_current_config():
    from types import SimpleNamespace

    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        _hydrate_config_change_preconditions,
    )

    resource = SimpleNamespace(config_json={
        "sell_flow": {
            "trailing": {
                "activation_profit_pct": 2.5,
                "hwm_trail_pct": 1.25,
            },
            "kill_switch": {
                "atr_stop_multiplier": 2.25,
                "max_drawdown_from_hwm_pct": 1.5,
            },
        },
    })
    proposal = {
        "operation_type": "UPDATE_CONFIG_PROFILE",
        "target": {"config_type": "spot_engine", "pool_id": None},
        "changes": [{
            "op": "replace",
            "path": path,
            "value_json": value,
            "old_value_json": "null",
            "array_guards_json": "[]",
            "evidence_refs": ["E1"],
        } for path, value in (
            ("/sell_flow/trailing/activation_profit_pct", "2.0"),
            ("/sell_flow/trailing/hwm_trail_pct", "1.0"),
            ("/sell_flow/kill_switch/atr_stop_multiplier", "2.0"),
            ("/sell_flow/kill_switch/max_drawdown_from_hwm_pct", "1.0"),
        )],
    }

    hydrated = await _hydrate_config_change_preconditions(
        _ScoreDocDB(resource),
        tenant_id=uuid.uuid4(),
        proposal=proposal,
    )
    assert [json.loads(change["old_value_json"]) for change in hydrated["changes"]] == [
        2.5, 1.25, 2.25, 1.5,
    ]
    assert all(change["array_guards_json"] == "[]" for change in hydrated["changes"])


def test_proposal_confirmation_refreshes_governed_policy_evidence():
    from app.ai_orchestration.langgraph.analysis_chat_handler import (
        AnalysisChatGraphNodeHandler,
    )
    from app.ai_orchestration.langgraph.graphs import _wire_analysis_chat

    graph_source = inspect.getsource(_wire_analysis_chat)
    handler_source = inspect.getsource(AnalysisChatGraphNodeHandler._node_updates)
    send_source = inspect.getsource(AnalysisChatService.send_message)

    assert 'route_after_confirmation(state, "plan_readonly_tools")' in graph_source
    assert '"global_risk.get_effective_policy"' in handler_source
    assert '"strategies.get_execution_policy"' in handler_source
    assert '"proposal_evidence_tool_allowlist"' in send_source

"""Isolated, zero-cost staging canaries for systemic and regenerative graphs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from ...api.auth import pwd_context
from ...database import run_db_task
from ...models.ai_graph import (
    AI_GRAPH_DISPATCH_RESUME,
    AIGraphEvent,
    AIGraphInterrupt,
)
from ...models.analysis_chat import AIAnalysisMessage
from ...models.config_profile import ConfigProfile
from ...models.copilot import CopilotActionPlan, CopilotAuditLog
from ...models.profile import Profile
from ...models.profile_audit_log import ProfileAuditLog
from ...models.systemic_ai import (
    AIBudgetReservationRecord, AIConfigurationBundleRecord,
    AIDatasetSnapshotRecord, AIModelResolutionRecord, AIPromptVersion,
    AIRequestRecord, AIToolEvidenceRecord, AIUsageRecord,
)
from ...models.user import User
from ...schemas.analysis_chat import AnalysisChatDataMode, AnalysisChatRuntimeConfig
from ...services.ai_graph_service import AIGraphRunService
from ...services.analysis_chat_service import AnalysisChatService
from ...services.governed_change_service import (
    ROLLBACK_TEXT,
    SpotEngineConfig,
    document_hash,
    rollback as rollback_governed_change,
)
from ...tasks.ai_orchestration import execute_graph_run
from .config import get_langgraph_settings
from .analysis_chat_handler import (
    GOVERNED_STAGING_CANARY_CONTRACT,
    GOVERNED_STAGING_CANARY_EMAIL,
    GOVERNED_STAGING_CANARY_PROFILE_NAME,
    GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
    GOVERNED_STAGING_CANARY_SOURCE_VALUE,
    governed_staging_canary_profile_config,
)


CANARY_EMAIL = GOVERNED_STAGING_CANARY_EMAIL


def _canary_risk_policy() -> dict:
    return {
        "take_profit_pct": 1.5,
        "stop_loss_atr_multiplier": 1.5,
        "trailing_stop_enabled": False,
        "max_positions": 5,
        "daily_loss_limit_pct": 3.0,
        "max_exposure_per_asset_pct": 20,
        "circuit_breaker_consecutive_losses": 3,
        "circuit_breaker_pause_minutes": 60,
        "default_order_type": "limit",
        "max_slippage_pct": 0.1,
        "capital_per_trade_pct": 10,
        "max_capital_in_use_pct": 80,
    }


def _canary_strategy_policy() -> dict:
    return {
        "strategies": [
            {
                "id": "momentum_breakout",
                "name": "Momentum Breakout",
                "enabled": True,
                "params": {
                    "volume_spike_multiplier": 2,
                    "adx_min": 25,
                    "lookback": 20,
                },
            },
            {
                "id": "mean_reversion",
                "name": "Mean Reversion",
                "enabled": False,
                "params": {
                    "rsi_threshold": 30,
                    "bollinger_deviation": 2.0,
                    "zscore_threshold": -2.0,
                },
            },
        ],
    }


def _canary_score_policy() -> dict:
    return {
        "weights": {
            "liquidity": 25,
            "market_structure": 25,
            "momentum": 25,
            "signal": 25,
        },
        "scoring_rules": [{
            "id": "rule-adx",
            "indicator": "adx",
            "operator": ">=",
            "value": 25,
            "points": 25,
            "category": "momentum",
        }],
        "thresholds": {"strong_buy": 80, "buy": 65, "neutral": 40},
        "auto_select_top_n": 5,
        "auto_select_min_score": 80,
    }


def _canary_chat_runtime(*, writes_enabled: bool) -> dict:
    return AnalysisChatRuntimeConfig(
        enabled=writes_enabled,
        readonly_refresh_enabled=False,
        child_analysis_enabled=False,
        proposals_enabled=writes_enabled,
        governed_actions_enabled=writes_enabled,
        live_config_write_enabled=writes_enabled,
        streaming_enabled=False,
        summary_enabled=False,
        budget_enforcement_enabled=False,
    ).model_dump(mode="json")


async def _upsert_canary_config(db, user_id: UUID, config_type: str, value: dict):
    rows = list((await db.execute(select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.config_type == config_type,
    ).order_by(ConfigProfile.created_at))).scalars().all())
    if len(rows) > 1:
        raise RuntimeError(f"LANGGRAPH_STAGING_CANARY_DUPLICATE_CONFIG:{config_type}")
    if rows:
        record = rows[0]
        record.config_json = value
        record.is_active = True
        record.updated_at = datetime.now(timezone.utc)
    else:
        record = ConfigProfile(
            user_id=user_id,
            pool_id=None,
            config_type=config_type,
            config_json=value,
            is_active=True,
        )
        db.add(record)
        await db.flush()
    return record

READONLY_CANARY_TOOLS = (
    "strategy_profiles.get_profile",
    "ml_models.get_authority_status",
    "shadow.get_performance_summary",
    "score_engine.get_effective_configuration_at",
    "global_risk.validate_recommendation",
    "strategies.validate_recommendation",
    "intelligence_runs.list_runs",
    "social_score.get_snapshot",
    "market_regime.get_current",
    "audit_memory.find_similar_decisions",
)


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _assert_staging() -> str:
    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
    if "staging" not in environment:
        raise RuntimeError("LANGGRAPH_CANARY_STAGING_ONLY")
    if os.getenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false").lower() != "true":
        raise RuntimeError("LANGGRAPH_FAKE_PROVIDER_CANARY_DISABLED")
    password = os.getenv("LANGGRAPH_STAGING_CANARY_PASSWORD", "")
    if len(password) < 20:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_PASSWORD_REQUIRED")
    settings = get_langgraph_settings()
    settings.require_runtime()
    if not settings.entrypoints_enabled or not settings.regenerative_shadow_enabled:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_FLAGS_REQUIRED")
    if settings.real_provider_canary_enabled:
        raise RuntimeError("LANGGRAPH_REAL_PROVIDER_CANARY_MUST_REMAIN_DISABLED")
    return password


async def _seed(db, password: str) -> dict:
    user = (await db.execute(select(User).where(User.email == CANARY_EMAIL))).scalar_one_or_none()
    if user is None:
        user = User(
            id=uuid4(), email=CANARY_EMAIL, password_hash=pwd_context.hash(password),
            name="LangGraph Staging Canary", role="admin", is_active=True,
        )
        db.add(user)
        await db.flush()
    else:
        user.password_hash = pwd_context.hash(password)
        user.role = "admin"
        user.is_active = True

    candidate_config = {
        "default_timeframe": "5m",
        "scoring": {"weights": {"evidence": 1}, "thresholds": {"minimum": 1}, "selected_rule_ids": []},
        "signals": {"logic": "AND", "conditions": []},
        "live_trading_enabled": False,
    }
    score_config = candidate_config["scoring"]
    profile = (await db.execute(select(Profile).where(
        Profile.user_id == user.id, Profile.name == "LangGraph Canary Shadow",
    ))).scalar_one_or_none()
    if profile is None:
        profile = Profile(
            user_id=user.id, name="LangGraph Canary Shadow", description="Isolated staging canary",
            config=candidate_config, is_active=False, is_shadow_only=True,
            live_trading_enabled=False, auto_pilot_enabled=False,
        )
        db.add(profile)
        await db.flush()

    governed_source = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
    )
    governed_profile = (await db.execute(select(Profile).where(
        Profile.user_id == user.id,
        Profile.name == GOVERNED_STAGING_CANARY_PROFILE_NAME,
    ))).scalar_one_or_none()
    if governed_profile is None:
        governed_profile = Profile(
            user_id=user.id,
            name=GOVERNED_STAGING_CANARY_PROFILE_NAME,
            description="Deterministic governed Analysis Chat staging canary",
            config=governed_source,
            is_active=False,
            is_shadow_only=True,
            live_trading_enabled=False,
            auto_pilot_enabled=False,
        )
        db.add(governed_profile)
        await db.flush()
    elif (
        dict(governed_profile.config or {}) != governed_source
        or bool(governed_profile.is_active)
        or not bool(governed_profile.is_shadow_only)
        or bool(governed_profile.live_trading_enabled)
        or bool(governed_profile.auto_pilot_enabled)
    ):
        raise RuntimeError("LANGGRAPH_STAGING_GOVERNED_CANARY_PROFILE_DIRTY")

    active_profile_count = int((await db.execute(select(func.count(Profile.id)).where(
        Profile.user_id == user.id,
        Profile.is_active.is_(True),
    ))).scalar_one())
    if active_profile_count != 0:
        raise RuntimeError("LANGGRAPH_STAGING_GOVERNED_CANARY_ACTIVE_PROFILE")

    await _upsert_canary_config(
        db, user.id, "ai_analysis_chat_runtime", _canary_chat_runtime(writes_enabled=True)
    )
    await _upsert_canary_config(db, user.id, "risk", _canary_risk_policy())
    await _upsert_canary_config(db, user.id, "strategy", _canary_strategy_policy())
    await _upsert_canary_config(db, user.id, "score", _canary_score_policy())
    spot_engine = SpotEngineConfig().default_json()
    if spot_engine.get("selling", {}).get("never_sell_at_loss") is not True:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_SPOT_INVARIANT_INVALID")
    await _upsert_canary_config(db, user.id, "spot_engine", spot_engine)

    resolution = AIModelResolutionRecord(
        tenant_id=user.id, requested_provider="fake", requested_model="fake-analysis-v1",
        configured_provider="fake", configured_model="fake-analysis-v1",
        effective_provider="fake", effective_model="fake-analysis-v1",
        catalog_snapshot_hash=_hash({"fake": "staging-only"}),
        capabilities=["text", "structured_output"],
        resolution_policy_version="staging-fake-v1", resolution_reason="isolated_zero_cost_canary",
    )
    db.add(resolution)
    prompt = (await db.execute(select(AIPromptVersion).where(
        AIPromptVersion.prompt_key == "systemic-multimodule",
        AIPromptVersion.status == "APPROVED",
    ).order_by(AIPromptVersion.semantic_version.desc()).limit(1))).scalar_one()
    bundle_payload = {"profile_id": str(profile.id), "config": candidate_config, "live_write": False}
    bundle = AIConfigurationBundleRecord(
        tenant_id=user.id, profile_id=profile.id, lineage_refs={"source": "staging_canary"},
        bundle_json=bundle_payload, bundle_hash=_hash(bundle_payload), lineage_status="COMPLETE",
    )
    db.add(bundle)
    await db.flush()
    now = datetime.now(timezone.utc)
    dataset = AIDatasetSnapshotRecord(
        tenant_id=user.id, contract_version="staging-canary-v1",
        source_tables=["synthetic_staging_canary"], source_labels=["FAKE_ADAPTER"],
        event_identity_contract="canary_event_id", outcome_contract="analysis_only",
        time_anchor="observed_at", window_start=now - timedelta(minutes=5), window_end=now,
        filters={"environment": "staging"}, exclusions=[], row_count=1,
        row_ids_hash=_hash(["canary-row-1"]), query_hash=_hash({"query": "synthetic"}),
        dataset_hash=_hash({"row": "canary-row-1", "environment": "staging"}),
        configuration_bundle_id=bundle.id, quality_status="PASS", quality_findings=[],
    )
    db.add(dataset)
    await db.flush()
    canary_context_id = str(uuid4())

    async def make_request(
        mode: str, authority: str, suffix: str, *, market_regime: str = "synthetic",
    ) -> AIRequestRecord:
        request_id = uuid4()
        context = {
            "canary_context_id": canary_context_id,
            "profile_family": "staging-canary",
            "timeframe": "5m",
            "market_regime": market_regime,
            "social_regime": "missing",
            "risk_policy_version": "staging-readonly-v1",
            "strategy_exit_policy": "never-sell-at-loss",
            "feature_contract": "unchanged",
            "label_contract": "unchanged",
            "model_lane": "none",
        }
        mutation = {
            "origin_module": "shadow_portfolio",
            "analysis_mode": mode,
            "target": "profile.candidate.shadow_only",
        }
        payload = {
            "request_intent": "FAKE_PROVIDER_CANARY",
            "staging_canary": True, "fake_provider": True,
            "candidate_config": candidate_config, "score_config": score_config,
            "mutation_reason": "staging_canary_shadow_only",
            "tool_allowlist": list(READONLY_CANARY_TOOLS),
            "dataset_request": {
                "entity_ids": [str(profile.id), str(governed_profile.id)],
                "filters": {
                    "max_rows": 20,
                    "staging_canary": True,
                    "entity_ids": [str(profile.id), str(governed_profile.id)],
                },
            },
            "frozen_context": {
                "origin_module": "shadow_portfolio",
                "entity_ids": [str(profile.id), str(governed_profile.id)],
                **context,
                "context": context,
                "context_fingerprint": _hash(context),
                "mutation_fingerprint": _hash(mutation),
                "proposed_changes": [{
                    "target_module": "strategy_profiles",
                    "target_path": "profile.candidate.shadow_only",
                    "side_effect_class": "CANDIDATE_WRITE",
                }],
                "global_risk_veto": False,
                "strategy_invariant_conflict": False,
            },
        }
        record = AIRequestRecord(
            id=request_id, tenant_id=user.id, requested_by_user_id=user.id,
            origin_module="shadow_portfolio", origin_view="/intelligence-runs",
            analysis_mode=mode, authority=authority, question_hash=_hash("staging canary"),
            correlation_id=f"langgraph-staging-{suffix}-{request_id}",
            model_resolution_id=resolution.id, prompt_version_id=prompt.id,
            dataset_snapshot_id=dataset.id, configuration_bundle_id=bundle.id,
            request_json=payload,
        )
        db.add(record)
        await db.flush()
        return record

    analysis_request = await make_request("SYSTEMIC", "ANALYSIS_ONLY", "analysis")
    result_payload = {
        "ai_request_id": str(analysis_request.id), "status": "COMPLETED",
        "tenant_id": str(user.id), "provider": "fake", "requested_model": "fake-analysis-v1",
        "configured_model": "fake-analysis-v1", "effective_model": "fake-analysis-v1",
        "model_resolution_id": str(resolution.id), "prompt_version_id": str(prompt.id),
        "prompt_hash": prompt.content_hash, "dataset_snapshot_id": str(dataset.id),
        "dataset_hash": dataset.dataset_hash, "configuration_bundle_id": str(bundle.id),
        "configuration_bundle_hash": bundle.bundle_hash,
        "analysis": {"verdict": "STAGING_FAKE_ADAPTER_CANARY", "live_write": False},
        "recommendations": [], "evidence_refs": [], "tool_calls": [],
        "usage": {
            "tokens_input": 0, "tokens_output": 0, "estimated_cost": str(Decimal("0")),
            "actual_cost": str(Decimal("0")), "currency": "USD",
            "pricing_snapshot_version": "ZERO_COST_FAKE_STAGING", "reservation": str(Decimal("0")),
            "limit": str(Decimal("0")), "remaining": str(Decimal("0")),
        },
        "warnings": [], "limitations": ["isolated fake adapter; no provider claim"],
        "terminal_reason": "STAGING_CANARY", "completed_at": now.isoformat(),
    }
    analysis_request.request_json = {
        **dict(analysis_request.request_json or {}),
        "fake_provider_result": result_payload,
    }
    regenerative_request_a = await make_request("REGENERATIVE", "SHADOW_ONLY", "regenerative-a")
    regenerative_request_b = await make_request("REGENERATIVE", "SHADOW_ONLY", "regenerative-b")
    regenerative_request_c = await make_request(
        "REGENERATIVE", "SHADOW_ONLY", "regenerative-c", market_regime="synthetic-change",
    )
    analysis_run = await AIGraphRunService.create(
        db, tenant_id=user.id, user_id=user.id, graph_key="systemic-analysis-v2",
        ai_request_id=analysis_request.id, idempotency_key=f"canary-analysis-{analysis_request.id}",
    )
    regenerative_runs = []
    for label, request in (
        ("a", regenerative_request_a),
        ("b", regenerative_request_b),
        ("c", regenerative_request_c),
    ):
        regenerative_runs.append(await AIGraphRunService.create(
            db, tenant_id=user.id, user_id=user.id, graph_key="regenerative-shadow-v2",
            ai_request_id=request.id, idempotency_key=f"canary-regenerative-{label}-{request.id}",
        ))
    return {
        "tenant_id": user.id, "analysis_run_id": analysis_run.id,
        "analysis_thread_id": analysis_run.thread_id,
        "analysis_request_id": analysis_request.id,
        "regenerative_run_id": regenerative_runs[0].id,
        "regenerative_run_a_id": regenerative_runs[0].id,
        "regenerative_run_b_id": regenerative_runs[1].id,
        "regenerative_run_c_id": regenerative_runs[2].id,
        "regenerative_thread_a_id": regenerative_runs[0].thread_id,
        "regenerative_thread_b_id": regenerative_runs[1].thread_id,
        "regenerative_thread_c_id": regenerative_runs[2].thread_id,
        "regenerative_request_a_id": regenerative_request_a.id,
        "regenerative_request_b_id": regenerative_request_b.id,
        "regenerative_request_c_id": regenerative_request_c.id,
        "context_fingerprint_ab": regenerative_request_a.request_json["frozen_context"]["context_fingerprint"],
        "context_fingerprint_c": regenerative_request_c.request_json["frozen_context"]["context_fingerprint"],
        "mutation_fingerprint": regenerative_request_a.request_json["frozen_context"]["mutation_fingerprint"],
        "canary_started_at": now,
        "dataset_id": dataset.id,
        "bundle_id": bundle.id, "prompt_id": prompt.id, "model_resolution_id": resolution.id,
        "profile_id": profile.id,
        "governed_profile_id": governed_profile.id,
        "governed_source_hash": document_hash(governed_source),
    }


async def _pending_interrupt(db, run_id: UUID) -> AIGraphInterrupt:
    return (await db.execute(select(AIGraphInterrupt).where(
        AIGraphInterrupt.graph_run_id == run_id,
        AIGraphInterrupt.status == "PENDING",
    ).order_by(AIGraphInterrupt.created_at.desc()))).scalars().first()


async def _resolve(
    db, context: dict, run_id: UUID, interrupt_id: UUID, decision: str = "approve",
):
    run, _reused, persisted_decision_id = await AIGraphRunService.resume(
        db, tenant_id=context["tenant_id"], actor_user_id=context["tenant_id"],
        run_id=run_id, interrupt_id=interrupt_id,
        decision=decision, decision_id=uuid4(), idempotency_key=f"canary-resume-{uuid4()}", edits={},
    )
    return run, persisted_decision_id


def _require_canary(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(f"LANGGRAPH_STAGING_GOVERNED_CANARY_INCOMPLETE:{reason}")


async def _create_governed_chat_turn(db, context: dict) -> dict:
    evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == context["tenant_id"],
        AIToolEvidenceRecord.ai_request_id == context["analysis_request_id"],
        AIToolEvidenceRecord.tool_name == "strategy_profiles.get_profile",
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars().all())
    source = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
    )
    matching = []
    for evidence in evidence_rows:
        data = dict(evidence.output_json or {}).get("data")
        if isinstance(data, list) and any(
            isinstance(row, dict)
            and row.get("profile_id") == str(context["governed_profile_id"])
            and row.get("config") == source
            and row.get("is_active") is False
            and row.get("is_shadow_only") is True
            and row.get("live_trading_enabled") is False
            for row in data
        ):
            matching.append(evidence)
    _require_canary(len(matching) == 1, "PARENT_PROFILE_EVIDENCE")

    conversation = await AnalysisChatService.create_conversation(
        db,
        tenant_id=context["tenant_id"],
        user_id=context["tenant_id"],
        run_id=context["analysis_run_id"],
        title="Governed staging canary v1.5",
    )
    _user, assistant, graph_run, reused = await AnalysisChatService.send_message(
        db,
        tenant_id=context["tenant_id"],
        user_id=context["tenant_id"],
        conversation_id=conversation.id,
        message=(
            "Aplicar o estreitamento monotônico comprovado do taker_ratio "
            "somente no perfil canário shadow/inativo."
        ),
        data_mode=AnalysisChatDataMode.DRAFT_PROPOSAL,
        idempotency_key=f"governed-staging-canary-{uuid4()}",
        response_language="pt-BR",
    )
    _require_canary(not reused, "CHAT_TURN_REUSED")
    request = await db.get(AIRequestRecord, assistant.ai_request_id)
    _require_canary(request is not None, "CHAT_REQUEST_MISSING")
    request.request_json = {
        **dict(request.request_json or {}),
        "governed_staging_canary": {
            "contract_version": GOVERNED_STAGING_CANARY_CONTRACT,
            "profile_id": str(context["governed_profile_id"]),
            "profile_name": GOVERNED_STAGING_CANARY_PROFILE_NAME,
        },
    }
    return {
        "conversation_id": conversation.id,
        "assistant_message_id": assistant.id,
        "chat_run_id": graph_run.id,
        "chat_thread_id": graph_run.thread_id,
        "chat_request_id": request.id,
        "profile_evidence_id": matching[0].id,
    }


async def _resolve_chat_interrupt(
    db,
    context: dict,
    interrupt: AIGraphInterrupt,
) -> tuple[object, UUID]:
    message = await db.get(AIAnalysisMessage, context["assistant_message_id"])
    _require_canary(message is not None, "CHAT_MESSAGE_MISSING")
    await AnalysisChatService.refresh_proposal_confirmation_contract(
        db,
        tenant_id=context["tenant_id"],
        user_id=context["tenant_id"],
        message=message,
        interrupt_id=interrupt.id,
        decision="approve",
    )
    return await _resolve(
        db,
        context,
        context["chat_run_id"],
        interrupt.id,
        "approve",
    )


async def _drive_governed_chat(context: dict) -> dict:
    stages = [await execute_graph_run(context["chat_run_id"])]
    decisions = []
    for expected_type in ("PROPOSAL_CONFIRMATION", "PROPOSAL_APPROVAL"):
        interrupt = await run_db_task(
            lambda db: _pending_interrupt(db, context["chat_run_id"])
        )
        _require_canary(interrupt is not None, f"{expected_type}_MISSING")
        _require_canary(
            interrupt.interrupt_type == expected_type,
            f"{expected_type}_ORDER",
        )
        _run, decision_id = await run_db_task(
            lambda db, current=interrupt: _resolve_chat_interrupt(
                db, context, current
            )
        )
        decisions.append({
            "interrupt_id": str(interrupt.id),
            "interrupt_type": interrupt.interrupt_type,
            "decision_id": str(decision_id),
        })
        stages.append(await execute_graph_run(
            context["chat_run_id"],
            dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
            interrupt_id=interrupt.id,
            decision_id=decision_id,
        ))
    _require_canary(stages[-1].get("status") == "COMPLETED", "CHAT_NOT_COMPLETED")
    _require_canary(
        len({item["decision_id"] for item in decisions}) == 2,
        "DECISIONS_NOT_DISTINCT",
    )
    return {"stages": stages, "decisions": decisions}


async def _governed_execution_proof(db, context: dict) -> dict:
    message = await db.get(AIAnalysisMessage, context["assistant_message_id"])
    _require_canary(message is not None and message.proposal_id is not None, "PLAN_LINK")
    plan = await db.get(CopilotActionPlan, message.proposal_id)
    profile = await db.get(Profile, context["governed_profile_id"])
    reservation = (await db.execute(select(AIBudgetReservationRecord).where(
        AIBudgetReservationRecord.ai_request_id == context["chat_request_id"],
    ))).scalar_one_or_none()
    usage = (await db.execute(select(AIUsageRecord).where(
        AIUsageRecord.ai_request_id == context["chat_request_id"],
    ))).scalar_one_or_none()
    audits = list((await db.execute(select(CopilotAuditLog).where(
        CopilotAuditLog.action_plan_id == message.proposal_id,
    ).order_by(CopilotAuditLog.created_at, CopilotAuditLog.id))).scalars().all())
    profile_audits = list((await db.execute(select(ProfileAuditLog).where(
        ProfileAuditLog.profile_id == context["governed_profile_id"],
        ProfileAuditLog.change_source == "analysis_chat_human_confirmed",
    ).order_by(ProfileAuditLog.created_at, ProfileAuditLog.id))).scalars().all())
    order_count = int((await db.execute(text("""
        SELECT count(*) FROM orders
         WHERE user_id = :tenant_id AND created_at >= :started_at
    """), {
        "tenant_id": context["tenant_id"],
        "started_at": context["canary_started_at"],
    })).scalar_one())

    _require_canary(plan is not None and plan.status == "EXECUTED", "PLAN_NOT_EXECUTED")
    execution = dict(plan.execution_result or {})
    validation = dict((plan.evidence or {}).get("candidate_validation") or {})
    expected_candidate = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_CANDIDATE_VALUE
    )
    _require_canary(profile is not None and profile.config == expected_candidate, "WRITE")
    _require_canary(
        not bool(profile.is_active)
        and bool(profile.is_shadow_only)
        and not bool(profile.live_trading_enabled)
        and not bool(profile.auto_pilot_enabled),
        "TARGET_FLAGS",
    )
    _require_canary(validation.get("decision") == "PASS", "POLICY_NOT_PASS")
    _require_canary(execution.get("cache_invalidation_status") == "NOT_REQUIRED", "CACHE_STATUS")
    _require_canary(message.provider_transport_attempted is False, "TRANSPORT")
    _require_canary(
        reservation is not None
        and reservation.status == "RECONCILED"
        and reservation.provider_transport_attempted is False
        and int(reservation.actual_tokens or 0) == 0
        and Decimal(str(reservation.actual_cost_usd or 0)) == Decimal("0"),
        "BUDGET",
    )
    _require_canary(
        usage is not None
        and int(usage.tokens_input or 0) == 0
        and int(usage.tokens_output or 0) == 0
        and Decimal(str(usage.actual_cost or 0)) == Decimal("0"),
        "USAGE",
    )
    required_audits = {
        "ANALYSIS_CHAT_CHANGE_DRY_RUN_CREATED",
        "ANALYSIS_CHAT_CANDIDATE_VALIDATION_PASS",
        "ANALYSIS_CHAT_CHANGE_APPROVED",
        "ANALYSIS_CHAT_CHANGE_EXECUTED",
    }
    event_types = {item.event_type for item in audits}
    exact_profile_audits = [
        item for item in profile_audits
        if item.change_description
        == f"Governed Analysis Chat proposal {plan.id}: {plan.objective}"
        and item.previous_config
        == governed_staging_canary_profile_config(
            value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
        )
        and item.new_config == expected_candidate
    ]
    _require_canary(required_audits.issubset(event_types), "AUDIT_EVENTS")
    _require_canary(len(exact_profile_audits) == 1, "PROFILE_AUDIT")
    _require_canary(order_count == 0, "ORDERS_CREATED")
    return {
        "plan_id": str(plan.id),
        "plan_status": plan.status,
        "policy_decision": validation.get("decision"),
        "risk_validation": validation.get("risk_validation"),
        "strategy_validation": validation.get("strategy_validation"),
        "cache_invalidation_status": execution.get("cache_invalidation_status"),
        "provider_transport_attempted": message.provider_transport_attempted,
        "tokens_input": int(usage.tokens_input or 0),
        "tokens_output": int(usage.tokens_output or 0),
        "cost_usd": str(usage.actual_cost or Decimal("0")),
        "orders_created": order_count,
        "audit_events": sorted(event_types),
        "profile_audit_count": len(exact_profile_audits),
        "candidate_hash": document_hash(profile.config),
    }


async def _rollback_governed_canary(db, context: dict, plan_id: UUID) -> dict:
    result = await rollback_governed_change(
        db,
        context["tenant_id"],
        plan_id,
        confirmation_text=ROLLBACK_TEXT,
    )
    profile = await db.get(Profile, context["governed_profile_id"])
    expected = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
    )
    rollback_result = dict((result.get("execution_result") or {}).get("rollback") or {})
    _require_canary(profile is not None and profile.config == expected, "ROLLBACK_DOCUMENT")
    _require_canary(result.get("status") == "ROLLED_BACK", "ROLLBACK_STATUS")
    _require_canary(
        rollback_result.get("cache_invalidation_status") == "NOT_REQUIRED",
        "ROLLBACK_CACHE_STATUS",
    )
    return {
        "status": result.get("status"),
        "restored_hash": document_hash(profile.config),
        "cache_invalidation_status": rollback_result.get("cache_invalidation_status"),
    }


async def _disable_governed_canary_writes(db) -> dict:
    """First cleanup transaction: never couple flag shutdown to rollback."""
    user = (await db.execute(select(User).where(User.email == CANARY_EMAIL))).scalar_one_or_none()
    if user is None:
        return {"status": "NOT_CREATED"}
    runtime = (await db.execute(select(ConfigProfile).where(
        ConfigProfile.user_id == user.id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.config_type == "ai_analysis_chat_runtime",
        ConfigProfile.is_active.is_(True),
    ).order_by(ConfigProfile.updated_at.desc()).limit(1))).scalar_one_or_none()
    if runtime is not None:
        runtime.config_json = _canary_chat_runtime(writes_enabled=False)
        runtime.updated_at = datetime.now(timezone.utc)
        await db.flush()
    return {
        "status": "COMPLETED",
        "runtime_write_enabled": False,
    }


async def _cleanup_governed_canary_profile(db) -> dict:
    """Second cleanup transaction: rollback and verify the exact fixture."""
    user = (await db.execute(select(User).where(User.email == CANARY_EMAIL))).scalar_one_or_none()
    if user is None:
        return {"status": "NOT_CREATED"}
    profile = (await db.execute(select(Profile).where(
        Profile.user_id == user.id,
        Profile.name == GOVERNED_STAGING_CANARY_PROFILE_NAME,
    ))).scalar_one_or_none()
    if profile is not None:
        expected = governed_staging_canary_profile_config(
            value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
        )
        if profile.config != expected:
            executed = (await db.execute(select(CopilotActionPlan).where(
                CopilotActionPlan.user_id == user.id,
                CopilotActionPlan.target_type == "PROFILE",
                CopilotActionPlan.target_id == str(profile.id),
                CopilotActionPlan.status == "EXECUTED",
            ).order_by(CopilotActionPlan.created_at.desc()).limit(1))).scalar_one_or_none()
            if executed is not None:
                await rollback_governed_change(
                    db,
                    user.id,
                    executed.id,
                    confirmation_text=ROLLBACK_TEXT,
                )
                await db.refresh(profile)
        _require_canary(profile.config == expected, "CLEANUP_PROFILE_DIRTY")
        _require_canary(
            not bool(profile.is_active)
            and bool(profile.is_shadow_only)
            and not bool(profile.live_trading_enabled)
            and not bool(profile.auto_pilot_enabled),
            "CLEANUP_TARGET_FLAGS",
        )
    return {
        "status": "COMPLETED",
        "profile_restored": profile is not None,
    }


async def _run_governed_canary_cleanup() -> dict:
    """Disable write authority durably before attempting profile recovery.

    These calls must remain separate ``run_db_task`` transactions.  If the
    profile rollback or its verification fails, the first transaction has
    already committed the fail-safe runtime shutdown.
    """
    runtime = await run_db_task(_disable_governed_canary_writes)
    profile = await run_db_task(_cleanup_governed_canary_profile)
    return {"runtime": runtime, "profile": profile}


async def _drive_regenerative(context: dict, run_id: UUID) -> list[dict]:
    stages = [await execute_graph_run(run_id)]
    for _ in range(3):
        interrupt = await run_db_task(lambda db: _pending_interrupt(db, run_id))
        if interrupt is None:
            break
        _run, decision_id = await run_db_task(
            lambda db, iid=interrupt.id: _resolve(db, context, run_id, iid)
        )
        stages.append(await execute_graph_run(
            run_id,
            dispatch_kind=AI_GRAPH_DISPATCH_RESUME,
            interrupt_id=interrupt.id,
            decision_id=decision_id,
        ))
    return stages


async def _runtime_proof(db, context: dict) -> dict:
    run_ids = [
        context["regenerative_run_a_id"], context["regenerative_run_b_id"],
        context["regenerative_run_c_id"],
    ]
    events = list((await db.execute(select(AIGraphEvent).where(
        AIGraphEvent.tenant_id == context["tenant_id"],
        AIGraphEvent.graph_run_id.in_(run_ids),
        AIGraphEvent.node_name.in_((
            "retrieve_contextual_memory", "create_profile_candidate_version",
        )),
    ).order_by(AIGraphEvent.graph_run_id, AIGraphEvent.id))).scalars())
    by_run: dict[str, dict] = {str(run_id): {} for run_id in run_ids}
    for event in events:
        by_run[str(event.graph_run_id)][str(event.node_name)] = dict(event.payload or {})
    memories = (await db.execute(text("""
        SELECT id, ai_request_id, status, mutation_fingerprint, context_fingerprint
          FROM decision_memory
         WHERE tenant_id = :tenant_id
           AND ai_request_id = ANY(CAST(:request_ids AS uuid[]))
         ORDER BY created_at
    """), {
        "tenant_id": context["tenant_id"],
        "request_ids": [
            str(context["regenerative_request_a_id"]),
            str(context["regenerative_request_b_id"]),
            str(context["regenerative_request_c_id"]),
        ],
    })).mappings().all()
    memory_by_request = {str(row["ai_request_id"]): str(row["id"]) for row in memories}
    memory_a_id = memory_by_request.get(str(context["regenerative_request_a_id"]))
    memory_b_hits = (
        by_run[str(context["regenerative_run_b_id"])]
        .get("retrieve_contextual_memory", {})
        .get("decision_memory_ids", [])
    )
    memory_c_hits = (
        by_run[str(context["regenerative_run_c_id"])]
        .get("retrieve_contextual_memory", {})
        .get("decision_memory_ids", [])
    )
    tool_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == context["tenant_id"],
        AIToolEvidenceRecord.ai_request_id == context["analysis_request_id"],
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id))).scalars())
    order_count = int((await db.execute(text("""
        SELECT count(*) FROM orders
         WHERE user_id = :tenant_id AND created_at >= :started_at
    """), {
        "tenant_id": context["tenant_id"], "started_at": context["canary_started_at"],
    })).scalar_one())
    return {
        "tool_evidence_count": len(tool_rows),
        "tool_evidence": [{
            "id": str(row.id), "module": row.module_key, "tool": row.tool_name,
            "output_hash": row.output_hash, "quality": row.quality,
        } for row in tool_rows],
        "regenerative_events": by_run,
        "decision_memory": [{key: str(value) if value is not None else None for key, value in row.items()}
                            for row in memories],
        "run_b_reused_run_a": bool(memory_a_id and memory_a_id in memory_b_hits),
        "run_b_memory_hit_ids": memory_b_hits,
        "run_c_different_context_memory_hit_ids": memory_c_hits,
        "run_c_avoided_global_block": len(memory_c_hits) == 0,
        "orders_created_during_canary": order_count,
    }


async def run_canaries() -> dict:
    password = _assert_staging()
    context: dict | None = None
    result: dict | None = None
    try:
        context = await run_db_task(lambda db: _seed(db, password))
        analysis = await execute_graph_run(context["analysis_run_id"])
        _require_canary(analysis["status"] == "COMPLETED", "ANALYSIS")

        chat = await run_db_task(lambda db: _create_governed_chat_turn(db, context))
        context.update(chat)
        governed_drive = await _drive_governed_chat(context)
        governed_proof = await run_db_task(
            lambda db: _governed_execution_proof(db, context)
        )
        governed_rollback = await run_db_task(
            lambda db: _rollback_governed_canary(
                db, context, UUID(governed_proof["plan_id"])
            )
        )

        stages_a = await _drive_regenerative(context, context["regenerative_run_a_id"])
        stages_b = await _drive_regenerative(context, context["regenerative_run_b_id"])
        stages_c = await _drive_regenerative(context, context["regenerative_run_c_id"])
        final_statuses = [stages[-1]["status"] if stages else "NOT_RUN" for stages in (
            stages_a, stages_b, stages_c,
        )]
        runtime_proof = await run_db_task(lambda db: _runtime_proof(db, context))
        from .checkpoint_admin import inspect_metadata
        checkpoint_proof = {
            "analysis": await inspect_metadata(context["tenant_id"], context["analysis_thread_id"]),
            "governed_chat": await inspect_metadata(
                context["tenant_id"],
                context["chat_thread_id"],
            ),
            "run_a": await inspect_metadata(context["tenant_id"], context["regenerative_thread_a_id"]),
            "run_b": await inspect_metadata(context["tenant_id"], context["regenerative_thread_b_id"]),
            "run_c": await inspect_metadata(context["tenant_id"], context["regenerative_thread_c_id"]),
        }
        failures = []
        if any(status != "COMPLETED" for status in final_statuses):
            failures.append("REGENERATIVE")
        if not runtime_proof["run_b_reused_run_a"]:
            failures.append("MEMORY_REUSE")
        if not runtime_proof["run_c_avoided_global_block"]:
            failures.append("MEMORY_CONTEXT_ISOLATION")
        if runtime_proof["orders_created_during_canary"] != 0:
            failures.append("ORDER_RECONCILIATION")
        if any(item["checkpoint_count"] < 1 for item in checkpoint_proof.values()):
            failures.append("CHECKPOINT")
        if failures:
            raise RuntimeError("LANGGRAPH_STAGING_CANARY_INCOMPLETE:" + ",".join(failures))
        result = {
            "status": "COMPLETED", "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME"),
            "analysis": analysis, "regenerative_stages": stages_a,
            "governed_analysis_chat": {
                "contract_version": GOVERNED_STAGING_CANARY_CONTRACT,
                "run_id": str(context["chat_run_id"]),
                "stages": governed_drive["stages"],
                "human_decisions": governed_drive["decisions"],
                "execution_proof": governed_proof,
                "rollback_proof": governed_rollback,
                "cache_scope": "PROFILE_NOT_REQUIRED",
                "config_profile_cache_live_proven": False,
            },
            "regenerative_runs": {
                "run_a": {"run_id": str(context["regenerative_run_a_id"]), "stages": stages_a},
                "run_b": {"run_id": str(context["regenerative_run_b_id"]), "stages": stages_b},
                "run_c": {"run_id": str(context["regenerative_run_c_id"]), "stages": stages_c},
            },
            "runtime_proof": runtime_proof,
            "checkpoint_proof": checkpoint_proof,
            **{key: str(value) for key, value in context.items() if key != "canary_started_at"},
            "provider": "fake", "configured_model": "fake-analysis-v1",
            "effective_model": "fake-analysis-v1", "cost_usd": "0",
            "authority": [
                "ANALYSIS_ONLY",
                "SHADOW_ONLY",
                "GOVERNED_PROFILE_WRITE_HUMAN_CONFIRMED",
            ],
            "live_write_after_rollback": False,
            "real_provider_canary": "NOT_RUN_REQUIRES_COST_APPROVAL",
        }
    finally:
        cleanup = await _run_governed_canary_cleanup()
    if result is None:
        raise RuntimeError("LANGGRAPH_STAGING_CANARY_NO_RESULT")
    result["governed_cleanup"] = cleanup
    return result


def main() -> None:
    print(json.dumps(asyncio.run(run_canaries()), sort_keys=True))


if __name__ == "__main__":
    main()

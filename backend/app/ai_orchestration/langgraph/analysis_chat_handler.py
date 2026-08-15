"""Durable node handler for the derived Analysis Chat graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from typing import Any
import uuid
from uuid import UUID

from jsonschema import (
    FormatChecker,
    SchemaError,
    ValidationError,
    validate as validate_json_schema,
)
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from ...database import run_db_task
from ...ai_orchestration.budget_reservation_audit import BudgetReservationAudit
from ...ai_orchestration.provider_adapters import anthropic_output_config
from ...ai_orchestration.sanitizer import TrustLabel, structured_block
from ...models.ai_graph import AIGraphEvent, AIGraphRun
from ...models.analysis_chat import (
    AIAnalysisConversation,
    AIAnalysisMessage,
    AIAnalysisMessageEvidence,
)
from ...models.config_profile import ConfigProfile
from ...models.ai_provider_key import AIProviderKey
from ...models.profile import Profile
from ...models.user import User
from ...models.systemic_ai import (
    AIBudgetPolicyRecord,
    AIBudgetReservationRecord,
    AIDatasetSnapshotRecord,
    AIModelApprovalRecord,
    AIModelResolutionRecord,
    AIPromptVersion,
    AIRequestRecord,
    AIResultRecord,
    AIToolEvidenceRecord,
    AIUsageRecord,
)
from ...schemas.analysis_chat import AnalysisChatOutput, AnalysisChatRuntimeConfig
from ...services.ai_keys_service import decrypt_value
from ...services.governed_change_service import (
    approve_and_execute as approve_and_execute_governed_change,
    create_dry_run as create_governed_change_dry_run,
    reconcile_execution_cache,
    validate_candidate_for_second_gate,
)
from ...services.systemic_langgraph_bridge import SystemicLangGraphBridge
from ..hashing import canonical_hash
from ..errors import (
    GovernedProposalError,
    GraphNodeExecutionError,
    ProviderBlockedError,
    ProviderOutputError,
    ProviderTransportError,
)
from .state import ScalpynGraphState
from .config import get_langgraph_settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


_TERMINAL_GRAPH_RUN_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_ALLOWED_ANALYSIS_CHAT_PROVIDER_INTENTS = frozenset({
    "NORMAL_ANALYSIS",
    "FAKE_PROVIDER_CANARY",
})


def _validated_analysis_chat_provider_intent(request_json: dict[str, Any]) -> str:
    """Return the exact provider intent or fail closed before transport."""

    intent = str(request_json.get("request_intent") or "")
    if intent not in _ALLOWED_ANALYSIS_CHAT_PROVIDER_INTENTS:
        raise ProviderBlockedError(
            "ANALYSIS_CHAT_INTENT_NOT_ALLOWED",
            "Analysis Chat provider transport requires an explicit allowed intent",
        )
    return intent

# This fixture is deliberately narrower than the general fake-provider path.
# It exists only so staging can prove the complete governed Profile lifecycle
# without a network/provider call or an active/live trading target.
GOVERNED_STAGING_CANARY_CONTRACT = "analysis-chat-governed-profile-v1.5"
GOVERNED_STAGING_CANARY_EMAIL = "langgraph-canary@staging.scalpyn.com.br"
GOVERNED_STAGING_CANARY_PROFILE_NAME = "Analysis Chat Governed Canary v1.5"
GOVERNED_STAGING_CANARY_SOURCE_VALUE = 0.52
GOVERNED_STAGING_CANARY_CANDIDATE_VALUE = 0.58


def governed_staging_canary_profile_config(*, value: float) -> dict[str, Any]:
    """Canonical strict Profile fixture; never used by normal user requests."""
    return {
        "default_timeframe": "5m",
        "filters": {
            "logic": "AND",
            "conditions": [{
                "field": "taker_ratio",
                "operator": ">=",
                "value": value,
            }],
        },
        "scoring": {
            "enabled": True,
            "weights": {
                "signal": 25,
                "momentum": 25,
                "liquidity": 25,
                "market_structure": 25,
            },
            "rules": [],
            "selected_rule_ids": ["rule-adx"],
            "thresholds": {"buy": 65, "strong_buy": 80, "neutral": 40},
        },
        "signals": {"logic": "AND", "conditions": []},
        "block_rules": {"blocks": []},
        "entry_triggers": {"logic": "AND", "conditions": []},
    }


def _governed_staging_canary_raw_proposal(
    *,
    profile_id: UUID,
    evidence_id: UUID,
) -> dict[str, Any]:
    """Return the exact provider-facing prompt 1.5 proposal contract."""
    return {
        "operation_type": "UPDATE_PROFILE_CONFIG",
        "target": {
            "profile_id": str(profile_id),
            "profile_name": GOVERNED_STAGING_CANARY_PROFILE_NAME,
            "config_type": None,
            "pool_id": None,
            "profile_ids": [],
        },
        "objective": "Tighten the isolated staging canary taker-ratio filter",
        "risk": "Inactive shadow-only staging fixture; mandatory rollback after proof",
        "changes": [{
            "op": "replace",
            "path": "/filters/conditions/0/value",
            "value_json": json.dumps(
                GOVERNED_STAGING_CANARY_CANDIDATE_VALUE,
                separators=(",", ":"),
            ),
            "old_value_json": json.dumps(
                GOVERNED_STAGING_CANARY_SOURCE_VALUE,
                separators=(",", ":"),
            ),
            "array_guards_json": json.dumps([{
                "path": "/filters/conditions/0",
                "identity": {"field": "taker_ratio"},
            }], separators=(",", ":")),
            "reason": "Deterministic staging proof of a monotonic eligibility restriction",
            "evidence_refs": [str(evidence_id)],
            "profile_id": None,
            "profile_name": None,
            "profile_indexes": [],
        }],
    }


def _governed_canary_block(reason_code: str) -> ProviderBlockedError:
    return ProviderBlockedError(
        reason_code,
        "The governed fake proposal can run only against the exact inactive staging canary fixture",
    )


async def _validated_governed_staging_canary_proposal(
    db,
    *,
    run: AIGraphRun,
    request: AIRequestRecord,
    conversation: AIAnalysisConversation,
    selected_evidence_refs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Materialize the deterministic proposal only for the private canary marker.

    The marker is written by ``staging_canary`` after the normal chat service
    creates the turn; it is not accepted by any HTTP request schema.  Every
    operational fact is nevertheless re-read and matched here so a copied or
    stale marker cannot authorize a different tenant/profile/configuration.
    """
    request_json = dict(request.request_json or {})
    marker = request_json.get("governed_staging_canary")
    if marker is None:
        return None
    if not isinstance(marker, dict) or set(marker) != {
        "contract_version", "profile_id", "profile_name",
    }:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_MARKER_INVALID")
    if (
        marker.get("contract_version") != GOVERNED_STAGING_CANARY_CONTRACT
        or marker.get("profile_name") != GOVERNED_STAGING_CANARY_PROFILE_NAME
        or request_json.get("request_intent") != "FAKE_PROVIDER_CANARY"
        or request_json.get("data_mode") != "DRAFT_PROPOSAL"
    ):
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_CONTRACT_MISMATCH")

    environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
    fake_enabled = os.getenv(
        "LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false"
    ).lower() == "true"
    real_enabled = get_langgraph_settings().real_provider_canary_enabled
    if "staging" not in environment or not fake_enabled or real_enabled:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_RUNTIME_DENIED")

    prompt = await db.get(AIPromptVersion, request.prompt_version_id)
    prompt_payload = (
        {
            key: getattr(prompt, key)
            for key in (
                "prompt_key",
                "semantic_version",
                "system_template",
                "user_template",
                "input_schema_json",
                "output_schema_json",
                "tool_policy_json",
                "provider_constraints_json",
            )
        }
        if prompt is not None
        else None
    )
    if (
        prompt is None
        or prompt.prompt_key != "analysis-chat-governed-change"
        or prompt.semantic_version != "1.5.0"
        or prompt.status != "APPROVED"
        or prompt.approved_at is None
        or not isinstance(prompt.output_schema_json, dict)
        or canonical_hash(prompt_payload) != prompt.content_hash
    ):
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_PROMPT_INVALID")

    user = await db.get(User, run.tenant_id)
    if (
        user is None
        or user.email != GOVERNED_STAGING_CANARY_EMAIL
        or not bool(user.is_active)
        or request.requested_by_user_id != user.id
    ):
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_TENANT_DENIED")
    try:
        profile_id = UUID(str(marker["profile_id"]))
    except (TypeError, ValueError) as exc:
        raise _governed_canary_block(
            "GOVERNED_STAGING_CANARY_PROFILE_ID_INVALID"
        ) from exc
    profile = (
        await db.execute(select(Profile).where(
            Profile.id == profile_id,
            Profile.user_id == run.tenant_id,
        ))
    ).scalar_one_or_none()
    expected_config = governed_staging_canary_profile_config(
        value=GOVERNED_STAGING_CANARY_SOURCE_VALUE
    )
    if (
        profile is None
        or profile.name != GOVERNED_STAGING_CANARY_PROFILE_NAME
        or bool(profile.is_active)
        or not bool(profile.is_shadow_only)
        or bool(profile.live_trading_enabled)
        or bool(profile.auto_pilot_enabled)
        or dict(profile.config or {}) != expected_config
    ):
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_PROFILE_DIRTY")
    active_profiles = int((await db.execute(select(func.count(Profile.id)).where(
        Profile.user_id == run.tenant_id,
        Profile.is_active.is_(True),
    ))).scalar_one())
    if active_profiles != 0:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_ACTIVE_PROFILE_FOUND")

    parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
    if parent_run is None or parent_run.tenant_id != run.tenant_id:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_PARENT_INVALID")
    evidence_ids: list[UUID] = []
    for ref in selected_evidence_refs:
        try:
            evidence_ids.append(UUID(str(ref.get("evidence_id"))))
        except (TypeError, ValueError):
            continue
    if not evidence_ids:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_EVIDENCE_MISSING")
    evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.id.in_(evidence_ids),
        AIToolEvidenceRecord.tenant_id == run.tenant_id,
        AIToolEvidenceRecord.ai_request_id == parent_run.ai_request_id,
        AIToolEvidenceRecord.tool_name == "strategy_profiles.get_profile",
    ))).scalars().all())
    matching_evidence: list[AIToolEvidenceRecord] = []
    for evidence in evidence_rows:
        output = dict(evidence.output_json or {})
        rows = output.get("data") if isinstance(output.get("data"), list) else []
        if any(
            isinstance(row, dict)
            and row.get("profile_id") == str(profile.id)
            and row.get("profile_name") == profile.name
            and row.get("config") == expected_config
            and row.get("is_active") is False
            and row.get("is_shadow_only") is True
            and row.get("live_trading_enabled") is False
            for row in rows
        ):
            matching_evidence.append(evidence)
    if len(matching_evidence) != 1:
        raise _governed_canary_block("GOVERNED_STAGING_CANARY_EVIDENCE_INVALID")
    return _governed_staging_canary_raw_proposal(
        profile_id=profile.id,
        evidence_id=matching_evidence[0].id,
    )


@dataclass(frozen=True)
class _ProviderInvocation:
    tenant_id: UUID
    request_id: UUID
    message_id: UUID
    parent_analysis_run_id: UUID
    provider_key_id: UUID
    provider: str
    model: str
    system_prompt: str
    user_prompt: str
    api_key: str = field(repr=False)
    max_output_tokens: int
    output_schema: dict[str, Any]
    budget_enforcement_enabled: bool
    reserved_tokens: int
    reserved_cost: Decimal
    used_today: int
    used_month: int
    request_token_limit: int
    daily_token_limit: int | None
    monthly_token_limit: int | None
    provider_key_monthly_token_limit: int | None
    provider_key_tokens_used_month_before: int
    input_rate: Decimal
    output_rate: Decimal
    max_cost_usd: Decimal
    pricing_snapshot_version: str
    selected_evidence_refs: tuple[dict[str, Any], ...]
    data_mode: str


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_provider_parent(
    provider_answer: AnalysisChatOutput,
    canonical_parent_id: UUID,
) -> AnalysisChatOutput:
    if provider_answer.parent_analysis_run_id == canonical_parent_id:
        return provider_answer
    return provider_answer.model_copy(update={
        "parent_analysis_run_id": canonical_parent_id,
        "warnings": [
            *provider_answer.warnings,
            "PROVIDER_PARENT_ANALYSIS_RUN_ID_NORMALIZED",
        ],
    })


def _validated_provider_answer(
    output: Any,
    output_schema: dict[str, Any],
) -> AnalysisChatOutput:
    try:
        validate_json_schema(
            instance=output,
            schema=output_schema,
            format_checker=FormatChecker(),
        )
    except (SchemaError, ValidationError) as exc:
        raise ProviderOutputError("ANALYSIS_CHAT_OUTPUT_SCHEMA_INVALID") from exc
    try:
        return AnalysisChatOutput.model_validate(output)
    except Exception as exc:
        raise ProviderOutputError("ANALYSIS_CHAT_OUTPUT_SCHEMA_INVALID") from exc


def _translate_change_evidence_labels(
    proposal: dict[str, Any] | None,
    selected_evidence_refs: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    """Translate short evidence labels (E1..E12) cited in proposal.changes
    back into real evidence UUIDs, in the exact order presented to the model
    as ``evidence_labels`` in the prompt. A label outside the presented set
    fails before any other proposal validation -- FIX-AC-GOV-002 Fase 4: the
    model selects from an enumerated menu, it never emits a raw identifier.
    """
    if not isinstance(proposal, dict):
        return proposal
    label_to_id = {
        f"E{index + 1}": str(ref.get("evidence_id"))
        for index, ref in enumerate(selected_evidence_refs)
    }
    valid_labels = ", ".join(label_to_id) or "(none)"
    changes = proposal.get("changes")
    if not isinstance(changes, list):
        return proposal
    translated_changes = []
    for raw_change in changes:
        change = dict(raw_change)
        raw_refs = change.get("evidence_refs")
        if isinstance(raw_refs, list):
            translated_refs = []
            for label in raw_refs:
                real_id = label_to_id.get(str(label))
                if real_id is None:
                    raise ProviderOutputError(
                        f"ANALYSIS_CHAT_EVIDENCE_LABEL_INVALID: '{label}' is not "
                        f"a supplied evidence label. Valid labels: {valid_labels}"
                    )
                translated_refs.append(real_id)
            change["evidence_refs"] = translated_refs
        translated_changes.append(change)
    return {**proposal, "changes": translated_changes}


def _normalized_provider_mode(
    provider_answer: AnalysisChatOutput,
    *,
    is_proposal: bool,
    refreshed: bool,
) -> tuple[str, str, dict[str, Any] | None]:
    if not is_proposal:
        return (
            "READONLY_REFRESH" if refreshed else "EXPLANATION",
            "REFRESHED_READONLY_DATA" if refreshed else "FROZEN_ANALYSIS",
            None,
        )
    if provider_answer.answer_type == "LIMITATION":
        if provider_answer.proposal is not None:
            raise ProviderOutputError("ANALYSIS_CHAT_PROPOSAL_OUTPUT_INCONSISTENT")
        return "LIMITATION", "PROPOSAL_DRAFT", None
    if provider_answer.answer_type == "PROPOSAL":
        if not isinstance(provider_answer.proposal, dict):
            raise ProviderOutputError("ANALYSIS_CHAT_PROPOSAL_OUTPUT_MISSING")
        return "PROPOSAL", "PROPOSAL_DRAFT", provider_answer.proposal
    raise ProviderOutputError("ANALYSIS_CHAT_PROPOSAL_OUTPUT_INCONSISTENT")


def _expand_compact_profile_changes(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand grouped profile indexes into the existing audited change contract."""
    operation = str(proposal.get("operation_type") or "")
    target = dict(proposal.get("target") or {})
    profile_ids = [str(item) for item in target.get("profile_ids") or []]
    expanded: list[dict[str, Any]] = []
    for raw_change in proposal.get("changes") or []:
        change = dict(raw_change)
        raw_indexes = change.pop("profile_indexes", []) or []
        if not raw_indexes:
            expanded.append(change)
            continue
        if operation not in {"UPDATE_PROFILE_CONFIG_SET", "SET_PROFILE_ACTIVE_STATUS"}:
            raise ValueError("Grouped profile indexes require a multi-profile operation")
        if change.get("profile_id") or change.get("profile_name"):
            raise ValueError("Grouped profile changes cannot also name one profile")
        if not profile_ids:
            raise ValueError("Grouped profile changes require target.profile_ids")
        if any(isinstance(index, bool) or not isinstance(index, int) for index in raw_indexes):
            raise ValueError("Grouped profile indexes must be integers")
        if len(raw_indexes) != len(set(raw_indexes)):
            raise ValueError("Grouped profile indexes must be unique")
        if any(index < 0 or index >= len(profile_ids) for index in raw_indexes):
            raise ValueError("Grouped profile index is outside target.profile_ids")
        for index in raw_indexes:
            expanded.append({
                **change,
                "profile_id": profile_ids[index],
                "profile_name": None,
            })
    if len(expanded) > 100:
        raise ValueError("A governed change is limited to 100 expanded changes")
    return expanded


def _retain_canonical_change_evidence(
    changes: list[dict[str, Any]],
    evidence_ids: set[str],
) -> list[dict[str, Any]]:
    """Retain only persisted parent-ledger refs and fail closed if none remain."""
    normalized: list[dict[str, Any]] = []
    for raw_change in changes:
        change = dict(raw_change)
        canonical_refs = list(dict.fromkeys(
            str(ref)
            for ref in change.get("evidence_refs") or []
            if str(ref) in evidence_ids
        ))
        if not canonical_refs:
            raise ValueError("Every proposed change requires evidence from the parent analysis")
        change["evidence_refs"] = canonical_refs
        normalized.append(change)
    return normalized


def _materialize_governed_proposal(
    raw_proposal: dict[str, Any],
    evidence_ids: set[str],
) -> dict[str, Any]:
    """Decode the provider's bounded string fields into the audited patch contract."""
    changes: list[dict[str, Any]] = []
    for raw_change in _expand_compact_profile_changes(raw_proposal):
        change = dict(raw_change)
        try:
            change["value"] = json.loads(str(change.pop("value_json")))
            change["old_value"] = json.loads(str(change.pop("old_value_json")))
            change["array_guards"] = json.loads(
                str(change.pop("array_guards_json"))
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Invalid governed change JSON contract") from exc
        changes.append(change)
    return {
        "operation_type": raw_proposal.get("operation_type"),
        "target": raw_proposal.get("target") or {},
        "objective": raw_proposal.get("objective"),
        "risk": raw_proposal.get("risk"),
        "changes": _retain_canonical_change_evidence(changes, evidence_ids),
    }


async def _load_canonical_evidence_refs(
    db,
    *,
    run: AIGraphRun,
    request: AIRequestRecord,
    conversation: AIAnalysisConversation,
) -> list[dict[str, Any]]:
    """Load a bounded tenant-scoped evidence selection for provider context.

    The bounded selection keeps the provider input stable.  Governed proposal
    authorization uses the complete persisted ledger loaded separately below.
    """
    parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
    if parent_run is None or parent_run.tenant_id != run.tenant_id:
        raise RuntimeError("ANALYSIS_CHAT_PARENT_EVIDENCE_SCOPE_INVALID")

    parent_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == run.tenant_id,
        AIToolEvidenceRecord.ai_request_id == parent_run.ai_request_id,
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id).limit(12))).scalars().all())
    refreshed_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
        AIToolEvidenceRecord.tenant_id == run.tenant_id,
        AIToolEvidenceRecord.ai_request_id == request.id,
    ).order_by(AIToolEvidenceRecord.created_at, AIToolEvidenceRecord.id).limit(4))).scalars().all())

    refs = [{
        "evidence_id": str(row.id),
        "module": row.module_key,
        "label": row.tool_name,
        "source_timestamp": row.created_at.isoformat() if row.created_at else None,
        "source": "FROZEN_ANALYSIS",
    } for row in parent_rows]
    refs.extend({
        "evidence_id": str(row.id),
        "module": row.module_key,
        "label": row.tool_name,
        "source_timestamp": row.created_at.isoformat() if row.created_at else None,
        "source": "REFRESHED_READONLY_DATA",
    } for row in refreshed_rows)
    return refs


async def _load_canonical_evidence_ids(
    db,
    *,
    run: AIGraphRun,
    request: AIRequestRecord,
    conversation: AIAnalysisConversation,
) -> set[str]:
    """Load the complete persisted evidence authority without prompt payloads."""
    parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
    if parent_run is None or parent_run.tenant_id != run.tenant_id:
        raise RuntimeError("ANALYSIS_CHAT_PARENT_EVIDENCE_SCOPE_INVALID")
    rows = (await db.execute(select(AIToolEvidenceRecord.id).where(
        AIToolEvidenceRecord.tenant_id == run.tenant_id,
        AIToolEvidenceRecord.ai_request_id.in_((parent_run.ai_request_id, request.id)),
    ))).scalars().all()
    return {str(evidence_id) for evidence_id in rows}


async def _translate_score_rule_points(
    db,
    *,
    tenant_id: UUID,
    proposal: dict[str, Any],
) -> dict[str, Any]:
    """For UPDATE_CONFIG_PROFILE / config_type=score changes, translate a
    change.rule_id (a real persisted scoring_rules[].id) into the exact
    path/old_value_json/array_guards_json against the live score document.
    The model selects a known rule id; the backend derives the array index,
    old value and identity guard -- it never constructs the JSON pointer
    itself. FIX-AC-GOV-002 Fase 5: this structurally prevents the invented-
    path failure mode found in production (e.g.
    /scoring/rules/rsi_overbought_penalty, which does not exist anywhere in
    the real schema), rather than relying only on prompt guidance -- the
    prompt already named and forbade that exact path and the model produced
    it anyway.
    """
    if proposal.get("operation_type") != "UPDATE_CONFIG_PROFILE":
        return proposal
    target = proposal.get("target") or {}
    if target.get("config_type") != "score":
        return proposal
    changes = proposal.get("changes")
    if not isinstance(changes, list) or not any(
        isinstance(item, dict) and item.get("rule_id") is not None for item in changes
    ):
        return proposal
    score_resource = (
        await db.execute(select(ConfigProfile).where(
            ConfigProfile.user_id == tenant_id,
            ConfigProfile.pool_id.is_(None),
            ConfigProfile.config_type == "score",
            ConfigProfile.is_active.is_(True),
        ).order_by(ConfigProfile.updated_at.desc()).limit(1))
    ).scalar_one_or_none()
    rules = list((score_resource.config_json or {}).get("scoring_rules") or []) if score_resource else []
    id_to_index = {str(rule.get("id")): index for index, rule in enumerate(rules)}
    valid_ids = ", ".join(sorted(id_to_index)) or "(no persisted scoring rules)"
    translated_changes = []
    for raw_change in changes:
        change = dict(raw_change)
        rule_id = change.pop("rule_id", None)
        if rule_id is not None:
            index = id_to_index.get(str(rule_id))
            if index is None:
                raise ProviderOutputError(
                    f"ANALYSIS_CHAT_SCORE_RULE_ID_INVALID: '{rule_id}' is not a "
                    f"persisted scoring_rules id. Valid ids: {valid_ids}"
                )
            change["path"] = f"/scoring_rules/{index}/points"
            change["old_value_json"] = json.dumps(rules[index].get("points"))
            change["array_guards_json"] = json.dumps([
                {"path": f"/scoring_rules/{index}", "identity": {"id": rule_id}}
            ])
        translated_changes.append(change)
    return {**proposal, "changes": translated_changes}


class AnalysisChatGraphNodeHandler:
    def __init__(self, graph_run_id: UUID, *, celery: bool = True):
        self.graph_run_id = graph_run_id
        self.celery = celery

    async def _transaction(self, fn):
        return await run_db_task(fn, celery=self.celery)

    async def handle(self, node_name: str, state: ScalpynGraphState) -> dict[str, Any]:
        if node_name == "invoke_provider":
            return await self._handle_provider_node(state)

        committed_tenant_id: UUID | None = None

        async def _handle(db):
            nonlocal committed_tenant_id
            run, request, message = await self._lock_node_context(
                db, node_name=node_name, state=state
            )
            committed_tenant_id = run.tenant_id
            updates = await self._node_updates(db, run, request, message, node_name, state)
            await self._complete_node(db, run, request, message, node_name, updates)
            return updates

        try:
            updates = await self._transaction(_handle)
        except GraphNodeExecutionError:
            raise
        except Exception as exc:
            raise GraphNodeExecutionError(node_name, exc) from exc

        # The governed write and NODE_COMPLETED event above are now committed.
        # Only after that boundary may Redis be invalidated.  Reconciliation is
        # deliberately non-terminal: an already-durable operational write must
        # never be presented to the user as if it failed or rolled back.
        execution = dict(updates.get("proposal_execution_result") or {})
        execution_result = dict(execution.get("execution_result") or {})
        if (
            execution_result.get("cache_invalidation_status") == "PENDING_AFTER_COMMIT"
            and committed_tenant_id is not None
        ):
            try:
                reconciled = await self._transaction(
                    lambda db: reconcile_execution_cache(
                        db,
                        committed_tenant_id,
                        UUID(str(execution["proposal_id"])),
                        decision_id=execution_result.get("approval_decision_id"),
                    )
                )
            except Exception:
                # PENDING_AFTER_COMMIT remains truthful and retryable if the
                # reconciliation transaction itself cannot be opened.
                return updates
            updates["proposal_execution_result"] = reconciled
            answer = dict(updates.get("answer") or {})
            answer["proposal"] = reconciled
            updates["answer"] = answer
        return updates

    async def _lock_node_context(
        self,
        db,
        *,
        node_name: str,
        state: ScalpynGraphState,
    ) -> tuple[AIGraphRun, AIRequestRecord, AIAnalysisMessage]:
        run = (
            await db.execute(select(AIGraphRun).where(
                AIGraphRun.id == self.graph_run_id
            ).with_for_update())
        ).scalar_one_or_none()
        if run is None:
            raise RuntimeError("ANALYSIS_CHAT_GRAPH_RUN_NOT_FOUND")
        if run.status in _TERMINAL_GRAPH_RUN_STATUSES:
            raise RuntimeError(f"ANALYSIS_CHAT_GRAPH_RUN_{run.status}")
        if str(run.tenant_id) != state.get("tenant_id"):
            raise RuntimeError("ANALYSIS_CHAT_TENANT_SCOPE_INVALID")

        request = await db.get(AIRequestRecord, run.ai_request_id)
        if request is None or request.tenant_id != run.tenant_id:
            raise RuntimeError("ANALYSIS_CHAT_REQUEST_SCOPE_INVALID")
        message = (
            await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.ai_request_id == request.id,
                AIAnalysisMessage.role == "ASSISTANT",
                AIAnalysisMessage.tenant_id == run.tenant_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if message is None:
            raise RuntimeError("ANALYSIS_CHAT_MESSAGE_NOT_FOUND")
        if message.status == "CANCELLED":
            raise RuntimeError("ANALYSIS_CHAT_MESSAGE_CANCELLED")

        now = _now()
        settings = get_langgraph_settings()
        run.status = "RUNNING"
        run.current_node = node_name
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=settings.lease_seconds)
        run.updated_at = now
        # ``persist_message_result_usage`` terminalizes the canonical message
        # before summary/completion bookkeeping nodes run. Never regress it.
        if message.status not in {"COMPLETED", "BLOCKED", "FAILED", "CANCELLED"}:
            message.status = "STREAMING"
            message.lock_version = int(message.lock_version or 0) + 1
        return run, request, message

    async def _complete_node(
        self,
        db,
        run: AIGraphRun,
        request: AIRequestRecord,
        message: AIAnalysisMessage,
        node_name: str,
        updates: dict[str, Any],
    ) -> None:
        now = _now()
        run.last_completed_node = node_name
        run.heartbeat_at = now
        run.updated_at = now
        await db.execute(insert(AIGraphEvent).values(
            tenant_id=run.tenant_id,
            graph_run_id=run.id,
            event_key=f"{run.id}:{node_name}:completed",
            event_type="NODE_COMPLETED",
            node_name=node_name,
            status="COMPLETED",
            payload={
                "conversation_id": str(request.conversation_id),
                "message_id": str(message.id),
                "data_mode": request.request_json.get("data_mode"),
                "evidence_count": len(
                    updates.get("selected_evidence_refs")
                    or updates.get("evidence_refs") or []
                ),
            },
        ).on_conflict_do_nothing(
            index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
        ))

    async def _handle_provider_node(
        self,
        state: ScalpynGraphState,
    ) -> dict[str, Any]:
        """Run provider I/O between two short, committed DB phases."""

        async def _prepare(db):
            run, request, message = await self._lock_node_context(
                db, node_name="invoke_provider", state=state
            )
            conversation = await db.get(
                AIAnalysisConversation, request.conversation_id
            )
            if conversation is None or conversation.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_CONVERSATION_SCOPE_INVALID")
            intent = _validated_analysis_chat_provider_intent(
                dict(request.request_json or {})
            )
            if intent == "FAKE_PROVIDER_CANARY":
                updates = await self._node_updates(
                    db, run, request, message, "invoke_provider", state
                )
                await self._complete_node(
                    db, run, request, message, "invoke_provider", updates
                )
                return updates, None

            runtime_config = (
                await db.execute(select(ConfigProfile).where(
                    ConfigProfile.user_id == run.tenant_id,
                    ConfigProfile.pool_id.is_(None),
                    ConfigProfile.config_type == "ai_provider_runtime",
                    ConfigProfile.is_active.is_(True),
                ).order_by(ConfigProfile.updated_at.desc()).limit(1))
            ).scalar_one_or_none()
            enabled = bool(
                runtime_config
                and (runtime_config.config_json or {}).get(
                    "normal_analysis_provider_enabled"
                ) is True
            )
            if not enabled:
                raise ProviderBlockedError(
                    "NORMAL_ANALYSIS_PROVIDER_DISABLED",
                    "The tenant-governed normal analysis provider gate is disabled",
                )
            invocation = await self._prepare_normal_provider(
                db, run, request, message, conversation, state
            )
            return None, invocation

        try:
            local_updates, invocation = await self._transaction(_prepare)
            if invocation is None:
                return local_updates or {}

            async def _start_transport(db):
                run, request, message = await self._lock_node_context(
                    db, node_name="invoke_provider", state=state
                )
                if request.id != invocation.request_id or message.id != invocation.message_id:
                    raise RuntimeError("ANALYSIS_CHAT_PROVIDER_START_SCOPE_INVALID")
                await BudgetReservationAudit.mark_transport_started(
                    db,
                    tenant_id=invocation.tenant_id,
                    ai_request_id=invocation.request_id,
                )
                run.provider_transport_attempted = True
                message.provider_transport_attempted = True

            # A cancel committed after preparation but before this fence stops
            # transport.  Once this transaction commits, the ledger truthfully
            # records that the external request may be billable.
            await self._transaction(_start_transport)

            try:
                response = await self._invoke_normal_provider(invocation)
            except Exception as exc:
                await self._transaction(
                    lambda db: BudgetReservationAudit.mark_transport_error(
                        db,
                        tenant_id=invocation.tenant_id,
                        ai_request_id=invocation.request_id,
                        reason_code="ANALYSIS_CHAT_PROVIDER_TRANSPORT_FAILED",
                    )
                )
                raise ProviderTransportError(
                    "ANALYSIS_CHAT_PROVIDER_TRANSPORT_FAILED"
                ) from exc

            usage_audit, terminal_status = await self._transaction(
                lambda db: self._reconcile_provider_response(
                    db, invocation=invocation, response=response
                )
            )
            if terminal_status is not None:
                raise RuntimeError(
                    "ANALYSIS_CHAT_MESSAGE_CANCELLED_AFTER_TRANSPORT"
                    if terminal_status == "CANCELLED"
                    else f"ANALYSIS_CHAT_GRAPH_RUN_{terminal_status}"
                )

            actual_tokens = int(response.tokens_input) + int(response.tokens_output)
            actual_cost = (
                Decimal(response.tokens_input) * invocation.input_rate
                + Decimal(response.tokens_output) * invocation.output_rate
            ) / Decimal("1000000")
            reconciliation_error = None
            if invocation.budget_enforcement_enabled:
                checks = (
                    (
                        actual_tokens > invocation.request_token_limit,
                        "ANALYSIS_CHAT_REQUEST_RECONCILIATION_EXCEEDED",
                    ),
                    (
                        invocation.daily_token_limit is not None
                        and invocation.used_today + actual_tokens
                        > invocation.daily_token_limit,
                        "ANALYSIS_CHAT_DAILY_RECONCILIATION_EXCEEDED",
                    ),
                    (
                        invocation.monthly_token_limit is not None
                        and invocation.used_month + actual_tokens
                        > invocation.monthly_token_limit,
                        "ANALYSIS_CHAT_MONTHLY_RECONCILIATION_EXCEEDED",
                    ),
                    (
                        invocation.provider_key_monthly_token_limit is not None
                        and usage_audit["provider_tokens_used_month"]
                        > invocation.provider_key_monthly_token_limit,
                        "ANALYSIS_CHAT_PROVIDER_KEY_RECONCILIATION_EXCEEDED",
                    ),
                    (
                        actual_cost > invocation.max_cost_usd,
                        "ANALYSIS_CHAT_COST_RECONCILIATION_EXCEEDED",
                    ),
                )
                reconciliation_error = next(
                    (code for exceeded, code in checks if exceeded), None
                )
            if response.terminal_error_code is not None:
                raise ProviderOutputError(str(response.terminal_error_code))
            if reconciliation_error is not None:
                raise ProviderTransportError(reconciliation_error)

            answer = self._build_provider_answer(invocation, response)

            updates = {
                "answer": answer.model_dump(mode="json"),
                "provider_transport_attempted": True,
            }

            async def _complete(db):
                run, request, message = await self._lock_node_context(
                    db, node_name="invoke_provider", state=state
                )
                if request.id != invocation.request_id or message.id != invocation.message_id:
                    raise RuntimeError("ANALYSIS_CHAT_PROVIDER_FINALIZATION_SCOPE_INVALID")
                await self._emit_tokens(
                    db, run, request, message, answer.answer
                )
                await self._complete_node(
                    db, run, request, message, "invoke_provider", updates
                )
                return updates

            return await self._transaction(_complete)
        except GraphNodeExecutionError:
            raise
        except Exception as exc:
            raise GraphNodeExecutionError("invoke_provider", exc) from exc

    async def _node_updates(
        self,
        db,
        run: AIGraphRun,
        request: AIRequestRecord,
        message: AIAnalysisMessage,
        node_name: str,
        state: ScalpynGraphState,
    ) -> dict[str, Any]:
        request_json = dict(request.request_json or {})
        conversation = await db.get(AIAnalysisConversation, request.conversation_id)
        if conversation is None or conversation.tenant_id != run.tenant_id:
            raise RuntimeError("ANALYSIS_CHAT_CONVERSATION_SCOPE_INVALID")

        if node_name == "load_conversation":
            return {
                "conversation_id": str(conversation.id),
                "message_id": str(message.id),
                "parent_analysis_run_id": str(conversation.parent_analysis_run_id),
                "parent_result_id": str(conversation.parent_result_id),
                "request_intent": request_json.get("request_intent"),
                "request_kind": request.request_kind,
                "data_mode": request_json.get("data_mode") or "FROZEN_ANALYSIS_ONLY",
                "question": str(request_json.get("question") or ""),
            }
        if node_name == "authorize_tenant":
            expected_authority = (
                "PROPOSAL_ONLY"
                if request_json.get("data_mode") == "DRAFT_PROPOSAL"
                else "ANALYSIS_ONLY"
            )
            if request.authority != expected_authority or run.authority != expected_authority:
                raise RuntimeError("ANALYSIS_CHAT_AUTHORITY_DENIED")
            if request.parent_analysis_run_id != conversation.parent_analysis_run_id:
                raise RuntimeError("ANALYSIS_CHAT_PARENT_LINK_MISMATCH")
            return {"authority": expected_authority}
        if node_name == "load_parent_analysis":
            parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
            result = await db.get(AIResultRecord, conversation.parent_result_id)
            if (
                parent_run is None or result is None
                or parent_run.tenant_id != run.tenant_id or result.tenant_id != run.tenant_id
            ):
                raise RuntimeError("ANALYSIS_CHAT_PARENT_SCOPE_INVALID")
            document = result.result_json if isinstance(result.result_json, dict) else {}
            analysis = document.get("analysis") if isinstance(document.get("analysis"), dict) else {}
            summary = (
                analysis.get("executive_summary")
                or analysis.get("summary")
                or document.get("terminal_reason")
                or "Análise original concluída; consulte as evidências vinculadas."
            )
            return {"parent_result_summary": str(summary)[:3000]}
        if node_name == "validate_parent_contracts":
            parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
            result = await db.get(AIResultRecord, conversation.parent_result_id)
            if parent_run is None or result is None or parent_run.status != "COMPLETED" or result.status != "COMPLETED":
                raise RuntimeError("ANALYSIS_CHAT_PARENT_NOT_COMPLETED")
            if result.ai_request_id != parent_run.ai_request_id:
                raise RuntimeError("ANALYSIS_CHAT_PARENT_RESULT_MISMATCH")
            return {}
        if node_name == "load_conversation_memory":
            config = await self._runtime_config(db, run.tenant_id)
            rows = list((await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == run.tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.sequence_number < message.sequence_number,
            ).order_by(AIAnalysisMessage.sequence_number.desc()).limit(
                config.recent_message_limit
            ))).scalars().all())
            return {
                "conversation_summary": conversation.running_summary,
                "recent_messages": [{
                    "sequence": row.sequence_number,
                    "role": row.role,
                    "content": (row.content or "")[:2000],
                    "content_hash": row.content_hash,
                } for row in reversed(rows)],
            }
        if node_name == "classify_followup":
            mode = request_json.get("data_mode") or "FROZEN_ANALYSIS_ONLY"
            reason = {
                "FROZEN_ANALYSIS_ONLY": "FROZEN_CONTEXT_SUFFICIENT",
                "ALLOW_READONLY_REFRESH": "READONLY_REFRESH_REQUIRED",
                "CREATE_CHILD_ANALYSIS": "CHILD_ANALYSIS_REQUIRED",
                "DRAFT_PROPOSAL": "PROPOSAL_CONFIRMATION_REQUIRED",
            }.get(mode, "AMBIGUOUS_REQUEST_DEFAULTED_SAFE")
            return {"data_mode": mode if mode in {
                "FROZEN_ANALYSIS_ONLY", "ALLOW_READONLY_REFRESH",
                "CREATE_CHILD_ANALYSIS", "DRAFT_PROPOSAL",
            } else "FROZEN_ANALYSIS_ONLY", "reason_code": reason}
        if node_name == "select_data_mode":
            return {"data_mode": state.get("data_mode") or "FROZEN_ANALYSIS_ONLY"}
        if node_name == "plan_readonly_tools":
            allowlist = request_json.get("tool_allowlist") or []
            if allowlist != ["market_regime.get_current"]:
                raise RuntimeError("ANALYSIS_CHAT_READONLY_TOOL_ALLOWLIST_INVALID")
            return {"tool_plan": [{
                "name": "market_regime.get_current",
                "input": {"tenant_id": str(run.tenant_id), "filters": {}},
            }]}
        if node_name == "execute_readonly_tools":
            from ..module_tool_runtime import ModuleToolRuntime

            dataset = await db.get(AIDatasetSnapshotRecord, request.dataset_snapshot_id)
            if dataset is None or dataset.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_DATASET_SCOPE_INVALID")
            runtime = ModuleToolRuntime()
            call_ids: list[str] = []
            refs: list[dict[str, Any]] = []
            for planned in state.get("tool_plan") or []:
                audit, _output = await runtime.execute(
                    db,
                    tenant_id=run.tenant_id,
                    request=request,
                    dataset=dataset,
                    tool_name=planned["name"],
                    tool_input=planned["input"],
                )
                call_ids.append(str(audit.id))
                refs.append({
                    "kind": "REFRESHED_READONLY_DATA",
                    "tool_call_id": str(audit.id),
                    "module": "market_regime",
                    "tool": planned["name"],
                    "queried_at": _now().isoformat(),
                })
            return {
                "readonly_tool_call_ids": call_ids,
                "new_data_snapshot_refs": refs,
                "tool_call_ids": call_ids,
            }
        if node_name == "retrieve_relevant_evidence":
            refs = await _load_canonical_evidence_refs(
                db,
                run=run,
                request=request,
                conversation=conversation,
            )
            return {"selected_evidence_refs": refs, "evidence_refs": refs}
        if node_name == "decide_if_new_data_required":
            return {"new_data_queried": state.get("data_mode") == "ALLOW_READONLY_REFRESH"}
        if node_name == "create_child_analysis_if_confirmed":
            answer = AnalysisChatOutput(
                answer=(
                    "A confirmação foi registrada, mas a criação da análise filha permaneceu "
                    "bloqueada porque este runtime não pode reutilizar o dataset ou o Configuration "
                    "Bundle do run pai. Nenhuma análise filha foi criada."
                ),
                answer_type="LIMITATION",
                based_on="CHILD_ANALYSIS",
                parent_analysis_run_id=conversation.parent_analysis_run_id,
                limitations=["CHILD_ANALYSIS_REQUIRES_FRESH_DATASET_AND_BUNDLE"],
                suggested_questions=["Quais contratos seriam congelados em uma análise filha?"],
            ).model_dump(mode="json")
            return {"answer": answer, "limitations": answer["limitations"]}
        if node_name == "draft_proposal_if_confirmed":
            answer = dict(state.get("answer") or {})
            raw_proposal = answer.get("proposal")
            if not isinstance(raw_proposal, dict):
                answer["answer_type"] = "LIMITATION"
                answer["limitations"] = [
                    *(answer.get("limitations") or []),
                    "GOVERNED_CHANGE_REQUIRES_AN_UNAMBIGUOUS_TYPED_TARGET",
                ]
                return {"answer": answer, "proposal_id": None}
            raw_proposal = await _translate_score_rule_points(
                db,
                tenant_id=run.tenant_id,
                proposal=raw_proposal,
            )
            canonical_refs = await _load_canonical_evidence_refs(
                db,
                run=run,
                request=request,
                conversation=conversation,
            )
            evidence_ids = await _load_canonical_evidence_ids(
                db,
                run=run,
                request=request,
                conversation=conversation,
            )
            try:
                typed_proposal = _materialize_governed_proposal(
                    raw_proposal,
                    evidence_ids,
                )
                plan = await create_governed_change_dry_run(
                    db,
                    run.tenant_id,
                    proposal=typed_proposal,
                    conversation_id=conversation.id,
                    message_id=message.id,
                    evidence_ids=evidence_ids,
                )
                proposal_id = UUID(plan["proposal_id"])
            except (AttributeError, KeyError, LookupError, TypeError, ValueError) as exc:
                raise GovernedProposalError() from exc
            message.proposal_id = proposal_id
            answer["answer"] = (
                "A prévia foi validada. Revise o diff abaixo; a configuração só será "
                "alterada depois da confirmação final."
            )
            answer["answer_type"] = "PROPOSAL"
            answer["based_on"] = "PROPOSAL_DRAFT"
            answer["proposal"] = plan
            answer["warnings"] = [
                *(answer.get("warnings") or []),
                "DETERMINISTIC_CANDIDATE_VALIDATION_PENDING",
            ]
            return {"proposal_id": str(proposal_id), "answer": answer}
        if node_name == "validate_risk_and_strategy":
            expected = [
                "global_risk.validate_recommendation",
                "strategies.validate_recommendation",
            ]
            if request_json.get("tool_allowlist") != expected:
                raise RuntimeError("ANALYSIS_CHAT_PROPOSAL_VALIDATOR_POLICY_INVALID")
            proposal_id = state.get("proposal_id")
            if not proposal_id:
                raise GovernedProposalError(
                    "ANALYSIS_CHAT_CANDIDATE_PLAN_REQUIRED"
                )
            validation = await validate_candidate_for_second_gate(
                db,
                run.tenant_id,
                UUID(str(proposal_id)),
            )
            answer = dict(state.get("answer") or {})
            proposal = dict(answer.get("proposal") or {})
            proposal.update({
                "candidate_validation": validation,
                "validation_scope": validation["validation_scope"],
                "policy_semantic_validation": validation[
                    "policy_semantic_validation"
                ],
                "candidate_created": False,
                "shadow_started": False,
            })
            answer["proposal"] = proposal
            answer["modules_consulted"] = ["governed_change_candidate_validator"]
            if validation["decision"] != "PASS":
                raise GovernedProposalError(
                    "ANALYSIS_CHAT_RISK_STRATEGY_CANDIDATE_VETO"
                )
            answer["warnings"] = [
                item for item in answer.get("warnings") or []
                if item != "DETERMINISTIC_CANDIDATE_VALIDATION_PENDING"
            ]
            answer["warnings"] = [
                *answer["warnings"],
                *validation.get("warnings", []),
                "GOVERNED_CANDIDATE_DETERMINISTIC_VALIDATION_PASSED",
            ]
            return {"answer": answer}
        if node_name == "execute_governed_proposal_if_confirmed":
            proposal_id = state.get("proposal_id")
            decision = dict(state.get("interrupt_decision") or {})
            # Editing never implies approval. A changed request must produce a
            # fresh preview before the user can approve its exact diff.
            if not proposal_id or decision.get("decision") != "approve":
                raise RuntimeError("ANALYSIS_CHAT_GOVERNED_CHANGE_APPROVAL_MISSING")
            actor_user_id = decision.get("actor_user_id")
            if not actor_user_id or str(actor_user_id) != str(request.requested_by_user_id):
                raise RuntimeError("ANALYSIS_CHAT_GOVERNED_CHANGE_ACTOR_MISMATCH")
            executed = await approve_and_execute_governed_change(
                db,
                run.tenant_id,
                UUID(str(proposal_id)),
                decision_id=decision.get("decision_id"),
            )
            answer = dict(state.get("answer") or {})
            execution_result = dict(executed.get("execution_result") or {})
            if execution_result.get("status") == "BLOCKED":
                reason_code = str(
                    execution_result.get("reason_code")
                    or "ANALYSIS_CHAT_EXECUTION_FENCE_BLOCKED"
                )
                answer["answer"] = (
                    "A execução foi bloqueada pela validação final. "
                    "Nenhuma configuração operacional foi alterada; gere uma nova prévia."
                )
                answer["proposal"] = executed
                answer["limitations"] = list(dict.fromkeys([
                    *(answer.get("limitations") or []),
                    reason_code,
                ]))
                return {
                    "answer": answer,
                    "proposal_execution_result": executed,
                }
            answer["answer"] = (
                "Alteração confirmada e aplicada com registro de auditoria e rollback disponível."
            )
            answer["proposal"] = executed
            answer["limitations"] = [
                item for item in answer.get("limitations") or []
                if item != "DRAFT_NOT_APPLIED"
            ]
            return {"answer": answer, "proposal_execution_result": executed}
        if node_name == "assemble_chat_context":
            return {
                "limitations": list(state.get("limitations") or []),
                "warnings": list(state.get("warnings") or []),
            }
        if node_name == "reserve_budget":
            await self._ensure_reservation(db, run, request)
            return {}
        if node_name == "invoke_provider":
            intent = str(request_json.get("request_intent") or "")
            if intent == "FAKE_PROVIDER_CANARY":
                environment = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
                fake_enabled = os.getenv("LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED", "false").lower() == "true"
                real_enabled = get_langgraph_settings().real_provider_canary_enabled
                if "staging" not in environment or not fake_enabled or real_enabled:
                    raise ProviderBlockedError(
                        "FAKE_PROVIDER_CANARY_DISABLED",
                        "Fake provider transport requires governed staging with the real-provider canary disabled",
                    )
                refs = list(state.get("selected_evidence_refs") or [])
                modules = list(dict.fromkeys(str(item.get("module")) for item in refs if item.get("module")))
                based_on = (
                    "REFRESHED_READONLY_DATA"
                    if state.get("data_mode") == "ALLOW_READONLY_REFRESH"
                    else "FROZEN_ANALYSIS"
                )
                answer_type = "READONLY_REFRESH" if based_on == "REFRESHED_READONLY_DATA" else "EXPLANATION"
                is_proposal = state.get("data_mode") == "DRAFT_PROPOSAL"
                canary_proposal = (
                    await _validated_governed_staging_canary_proposal(
                        db,
                        run=run,
                        request=request,
                        conversation=conversation,
                        selected_evidence_refs=refs,
                    )
                    if is_proposal
                    else None
                )
                summary = str(state.get("parent_result_summary") or "A análise original foi concluída.")
                if canary_proposal is not None:
                    prompt = await db.get(AIPromptVersion, request.prompt_version_id)
                    if prompt is None:  # defensive against a concurrent prompt deletion
                        raise _governed_canary_block(
                            "GOVERNED_STAGING_CANARY_PROMPT_INVALID"
                        )
                    # The helper verifies prompt identity, approval and hash.
                    # Validate this transport-free provider fixture against
                    # the original v1.5 schema before trusted metadata is added.
                    validated_fixture = _validated_provider_answer(
                        {
                            "answer": (
                                "Deterministic v1.5 preview for the inactive "
                                "shadow-only staging canary; final approval remains required."
                            ),
                            "answer_type": "PROPOSAL",
                            "based_on": "PROPOSAL_DRAFT",
                            "parent_analysis_run_id": str(
                                conversation.parent_analysis_run_id
                            ),
                            "evidence_refs": [{
                                "evidence_id": canary_proposal["changes"][0][
                                    "evidence_refs"
                                ][0],
                            }],
                            "proposal": canary_proposal,
                        },
                        dict(prompt.output_schema_json),
                    )
                    answer = validated_fixture.model_copy(update={
                        "modules_consulted": modules,
                        "evidence_refs": refs,
                        "limitations": ["STAGING_FAKE_PROVIDER_RESPONSE"],
                    }).model_dump(mode="json")
                    await self._emit_tokens(
                        db, run, request, message, answer["answer"]
                    )
                    return {
                        "answer": answer,
                        "provider_transport_attempted": False,
                    }
                if based_on == "REFRESHED_READONLY_DATA":
                    answer_text = (
                        f"Resultado original congelado: {summary[:700]} "
                        "Consulta nova separada: foi lido somente o snapshot atual de market_regime. "
                        "A janela histórica de sete dias não foi materializada por esta tool; nenhuma "
                        "configuração foi alterada."
                    )
                else:
                    answer_text = (
                        f"Com base no resultado persistido da análise original: {summary[:900]} "
                        "A resposta está limitada às evidências vinculadas abaixo e não altera nenhuma configuração."
                    )
                answer = AnalysisChatOutput(
                    answer=answer_text[:5000],
                    answer_type="LIMITATION" if is_proposal else answer_type,
                    based_on="PROPOSAL_DRAFT" if is_proposal else based_on,
                    parent_analysis_run_id=conversation.parent_analysis_run_id,
                    modules_consulted=modules,
                    evidence_refs=refs,
                    new_data_queried=based_on == "REFRESHED_READONLY_DATA",
                    new_data_window=(
                        {
                            "requested_window": "7d",
                            "effective_coverage": "CURRENT_SNAPSHOT_ONLY",
                            "queried_at": _now().isoformat(),
                        }
                        if based_on == "REFRESHED_READONLY_DATA" else None
                    ),
                    limitations=[
                        "STAGING_FAKE_PROVIDER_RESPONSE",
                        *(["FAKE_PROVIDER_DOES_NOT_CREATE_EXECUTABLE_CHANGES"] if is_proposal else []),
                        *(["READONLY_REFRESH_CURRENT_SNAPSHOT_ONLY"]
                          if based_on == "REFRESHED_READONLY_DATA" else []),
                    ],
                    suggested_questions=[
                        "Quais evidências sustentam a principal conclusão?",
                        "Quais limitações materiais permanecem?",
                    ],
                ).model_dump(mode="json")
                await self._emit_tokens(db, run, request, message, answer["answer"])
                return {"answer": answer, "provider_transport_attempted": False}

            # NORMAL_ANALYSIS is dispatched by ``_handle_provider_node`` so
            # the HTTP transport can never run inside this DB transaction.
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_PHASED_DISPATCH_REQUIRED")
        if node_name == "validate_chat_output":
            AnalysisChatOutput.model_validate(state.get("answer"))
            return {}
        if node_name == "persist_message_result_usage":
            answer = AnalysisChatOutput.model_validate(state.get("answer"))
            message.tool_call_ids_json = list(state.get("tool_call_ids") or [])
            await self._persist_answer(db, run, request, message, conversation, answer)
            return {"result_json": answer.model_dump(mode="json")}
        if node_name == "update_conversation_summary_if_needed":
            config = await self._runtime_config(db, run.tenant_id)
            if not config.summary_enabled or int(conversation.message_count or 0) < config.summary_message_threshold:
                return {}
            rows = list((await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.tenant_id == run.tenant_id,
                AIAnalysisMessage.conversation_id == conversation.id,
                AIAnalysisMessage.sequence_number > int(conversation.summarized_through_sequence or 0),
                AIAnalysisMessage.status == "COMPLETED",
            ).order_by(AIAnalysisMessage.sequence_number).limit(20))).scalars().all())
            if not rows:
                return {}
            summary = "\n".join(
                f"{row.sequence_number}:{row.role}:{(row.content or '')[:500]}" for row in rows
            )[:6000]
            conversation.running_summary = summary
            conversation.summary_version = "analysis-chat-summary@1.0.0"
            conversation.summary_hash = _sha(summary)
            conversation.summarized_through_sequence = rows[-1].sequence_number
            return {"conversation_summary": summary}
        if node_name == "complete_message":
            return {"status": "COMPLETED", "terminal_reason": "ANALYSIS_CHAT_MESSAGE_COMPLETED"}
        return {}

    async def _prepare_normal_provider(
        self,
        db,
        run: AIGraphRun,
        request: AIRequestRecord,
        message: AIAnalysisMessage,
        conversation: AIAnalysisConversation,
        state: ScalpynGraphState,
    ) -> _ProviderInvocation:
        request_json = dict(request.request_json or {})
        budget_enforcement_enabled = (
            request_json.get("budget_enforcement_enabled") is not False
        )
        resolution = await db.get(AIModelResolutionRecord, request.model_resolution_id)
        prompt = await db.get(AIPromptVersion, request.prompt_version_id)
        parent_run = await db.get(AIGraphRun, conversation.parent_analysis_run_id)
        parent_result = await db.get(AIResultRecord, conversation.parent_result_id)
        if not all((resolution, prompt, parent_run, parent_result)):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_CANONICAL_LINEAGE_INVALID",
                "The chat provider was blocked because canonical lineage is incomplete",
            )
        if (
            resolution.tenant_id != run.tenant_id
            or parent_run.tenant_id != run.tenant_id
            or parent_result.tenant_id != run.tenant_id
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_TENANT_SCOPE_INVALID",
                "The chat provider was blocked by tenant scope validation",
            )

        try:
            approval_id = UUID(str(request_json["model_approval_id"]))
        except (KeyError, ValueError) as exc:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MODEL_APPROVAL_REQUIRED",
                "The chat provider requires an auditable per-turn approval",
            ) from exc
        approval = await db.get(AIModelApprovalRecord, approval_id)
        if (
            approval is None
            or approval.tenant_id != run.tenant_id
            or approval.provider != resolution.effective_provider
            or approval.model != resolution.effective_model
            or approval.scope != "ANALYSIS_CHAT_TURN"
            or approval.status != "APPROVED"
            or approval.expires_at <= _now()
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MODEL_APPROVAL_INVALID",
                "The chat provider was blocked because the per-turn approval is invalid or expired",
            )
        budget = (
            await db.execute(select(AIBudgetPolicyRecord).where(
                AIBudgetPolicyRecord.tenant_id == run.tenant_id,
                AIBudgetPolicyRecord.provider == resolution.effective_provider,
                AIBudgetPolicyRecord.model == resolution.effective_model,
                AIBudgetPolicyRecord.module == "analysis_chat",
                AIBudgetPolicyRecord.is_active.is_(True),
            ))
        ).scalar_one_or_none()
        expected_budget_policy = (
            "DENY" if budget_enforcement_enabled else "AUDIT_ONLY"
        )
        if budget is None or budget.null_limit_policy != expected_budget_policy:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_BUDGET_POLICY_INVALID",
                "The chat provider requires the configured chat budget policy",
            )
        if budget_enforcement_enabled and (
            budget.daily_token_limit is None or budget.monthly_token_limit is None
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_BOUNDED_BUDGET_REQUIRED",
                "The chat provider requires a bounded request, daily and monthly budget",
            )
        key = (
            await db.execute(select(AIProviderKey).where(
                AIProviderKey.user_id == run.tenant_id,
                AIProviderKey.provider == resolution.effective_provider,
                AIProviderKey.is_active.is_(True),
                AIProviderKey.is_validated.is_(True),
            ))
        ).scalar_one_or_none()
        if key is None or (
            budget_enforcement_enabled and key.monthly_token_limit is None
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_VALIDATED_PROVIDER_KEY_REQUIRED",
                "The chat provider requires an active validated provider key",
            )

        evidence_ids: list[UUID] = []
        for ref in state.get("selected_evidence_refs") or []:
            try:
                evidence_ids.append(UUID(str(ref.get("evidence_id"))))
            except (TypeError, ValueError):
                continue
        evidence_rows = list((await db.execute(select(AIToolEvidenceRecord).where(
            AIToolEvidenceRecord.tenant_id == run.tenant_id,
            AIToolEvidenceRecord.id.in_(evidence_ids),
        ))).scalars().all()) if evidence_ids else []
        by_id = {row.id: row for row in evidence_rows}
        ordered_evidence = [by_id[item] for item in evidence_ids if item in by_id]
        if not ordered_evidence:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_TYPED_EVIDENCE_REQUIRED",
                "The chat provider requires tenant-scoped typed evidence",
            )
        evidence_payload = [{
            "label": f"E{index + 1}",
            "evidence_id": str(row.id),
            "module": row.module_key,
            "tool": row.tool_name,
            "output": row.output_json,
        } for index, row in enumerate(ordered_evidence)]
        # Short enumerated labels (E1..E12), not raw UUIDs, are what the model
        # is instructed to cite in proposal.changes[].evidence_refs. A long
        # unstructured UUID invites hallucination; a small closed menu does
        # not. The backend translates label -> real UUID after the response
        # (see _translate_change_evidence_labels) and still revalidates the
        # translated UUID against the canonical evidence pool.
        evidence_labels = "\n".join(
            f"{item['label']}: {item['module']}.{item['tool']}"
            for item in evidence_payload
        )
        values = {
            "parent_analysis": json.dumps(
                parent_result.result_json,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ),
            "evidence_labels": evidence_labels,
            "evidence": json.dumps(
                evidence_payload,
                ensure_ascii=False,
                default=str,
                separators=(",", ":"),
            ),
            "conversation": json.dumps({
                "summary": state.get("conversation_summary"),
                "recent_messages": state.get("recent_messages") or [],
            }, ensure_ascii=False, default=str, separators=(",", ":")),
            "question": structured_block(
                TrustLabel.USER_INPUT, str(request_json.get("question") or "")
            ),
        }
        try:
            system_prompt = prompt.system_template.format_map(values)
            user_prompt = prompt.user_template.format_map(values)
        except KeyError as exc:
            raise ProviderBlockedError(
                f"ANALYSIS_CHAT_PROMPT_INPUT_MISSING_{exc.args[0]}",
                "The chat provider prompt contract is incomplete",
            ) from exc
        structured_output_reservation = 0
        if resolution.effective_provider == "anthropic":
            structured_output_reservation = len(json.dumps(
                {"output_config": anthropic_output_config(prompt.output_schema_json)},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")) + 512
        estimated_input_tokens = max(
            1,
            len((system_prompt + user_prompt).encode("utf-8"))
            + structured_output_reservation,
        )
        max_output_tokens = int(approval.max_output_tokens)
        max_input_tokens = int(budget.request_token_limit) - max_output_tokens
        if budget_enforcement_enabled and (
            max_input_tokens <= 0 or estimated_input_tokens > max_input_tokens
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_INPUT_RESERVATION_EXCEEDED",
                "The chat provider was blocked because the input reservation exceeds the request budget",
            )
        reserved_tokens = estimated_input_tokens + max_output_tokens
        now = _now()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        used_today = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == "analysis_chat",
            AIUsageRecord.created_at >= day_start,
        ))) or 0)
        used_month = int((await db.scalar(select(func.coalesce(
            func.sum(AIUsageRecord.tokens_input + AIUsageRecord.tokens_output), 0,
        )).where(
            AIUsageRecord.tenant_id == run.tenant_id,
            AIUsageRecord.provider == resolution.effective_provider,
            AIUsageRecord.model == resolution.effective_model,
            AIUsageRecord.module == "analysis_chat",
            AIUsageRecord.created_at >= month_start,
        ))) or 0)
        if (
            budget_enforcement_enabled
            and used_today + reserved_tokens > int(budget.daily_token_limit)
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_DAILY_TOKEN_BUDGET_EXCEEDED",
                "The chat provider was blocked by the daily token budget",
            )
        if (
            budget_enforcement_enabled
            and used_month + reserved_tokens > int(budget.monthly_token_limit)
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_MONTHLY_TOKEN_BUDGET_EXCEEDED",
                "The chat provider was blocked by the monthly token budget",
            )
        if (
            budget_enforcement_enabled
            and int(key.tokens_used_month or 0) + reserved_tokens
            > int(key.monthly_token_limit)
        ):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_PROVIDER_KEY_BUDGET_EXCEEDED",
                "The chat provider was blocked by the provider-key monthly budget",
            )
        million = Decimal("1000000")
        input_rate = Decimal(approval.input_cost_per_million)
        output_rate = Decimal(approval.output_cost_per_million)
        reserved_cost = (
            Decimal(estimated_input_tokens) * input_rate
            + Decimal(max_output_tokens) * output_rate
        ) / million
        if budget_enforcement_enabled and reserved_cost > Decimal(approval.max_cost_usd):
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_COST_APPROVAL_EXCEEDED",
                "The chat provider was blocked by the per-turn cost ceiling",
            )

        activated = await BudgetReservationAudit.activate_placeholder(
            db,
            tenant_id=run.tenant_id,
            ai_request_id=request.id,
            budget_policy_id=budget.id,
            model_approval_id=approval.id,
            provider=resolution.effective_provider,
            model=resolution.effective_model,
            module="analysis_chat",
            estimated_input_tokens=estimated_input_tokens,
            max_output_tokens=max_output_tokens,
            request_token_limit=int(budget.request_token_limit),
            daily_token_limit=int(budget.daily_token_limit or 0),
            monthly_token_limit=int(budget.monthly_token_limit or 0),
            reserved_tokens=reserved_tokens,
            reserved_cost_usd=reserved_cost,
        )
        if not activated["activated"]:
            raise ProviderBlockedError(
                "ANALYSIS_CHAT_BUDGET_RESERVATION_ALREADY_ACTIVATED_NO_RETRY",
                "The chat provider was not retried because this turn already has an activated reservation",
            )
        return _ProviderInvocation(
            tenant_id=run.tenant_id,
            request_id=request.id,
            message_id=message.id,
            parent_analysis_run_id=conversation.parent_analysis_run_id,
            provider_key_id=key.id,
            provider=resolution.effective_provider,
            model=resolution.effective_model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            api_key=decrypt_value(bytes(key.api_key_encrypted)),
            max_output_tokens=max_output_tokens,
            output_schema=dict(prompt.output_schema_json or {}),
            budget_enforcement_enabled=budget_enforcement_enabled,
            reserved_tokens=reserved_tokens,
            reserved_cost=reserved_cost,
            used_today=used_today,
            used_month=used_month,
            request_token_limit=int(budget.request_token_limit),
            daily_token_limit=(
                int(budget.daily_token_limit)
                if budget.daily_token_limit is not None else None
            ),
            monthly_token_limit=(
                int(budget.monthly_token_limit)
                if budget.monthly_token_limit is not None else None
            ),
            provider_key_monthly_token_limit=(
                int(key.monthly_token_limit)
                if key.monthly_token_limit is not None else None
            ),
            provider_key_tokens_used_month_before=int(key.tokens_used_month or 0),
            input_rate=input_rate,
            output_rate=output_rate,
            max_cost_usd=Decimal(approval.max_cost_usd),
            pricing_snapshot_version=approval.pricing_snapshot_hash,
            selected_evidence_refs=tuple(
                dict(item) for item in state.get("selected_evidence_refs") or []
            ),
            data_mode=str(state.get("data_mode") or "FROZEN_ANALYSIS_ONLY"),
        )

    async def _invoke_normal_provider(self, invocation: _ProviderInvocation):
        # Intentionally contains no DB/session parameter.  The reservation and
        # TRANSPORT_STARTED audit were committed by the prepare phase.
        return await SystemicLangGraphBridge.execute_json_provider(
            provider=invocation.provider,
            model=invocation.model,
            system_prompt=invocation.system_prompt,
            user_prompt=invocation.user_prompt,
            api_key=invocation.api_key,
            request_id=str(invocation.request_id),
            max_output_tokens=invocation.max_output_tokens,
            output_schema=invocation.output_schema,
        )

    async def _reconcile_provider_response(
        self,
        db,
        *,
        invocation: _ProviderInvocation,
        response,
    ) -> tuple[dict[str, int], str | None]:
        run = (
            await db.execute(select(AIGraphRun).where(
                AIGraphRun.id == self.graph_run_id,
                AIGraphRun.tenant_id == invocation.tenant_id,
            ).with_for_update())
        ).scalar_one_or_none()
        message = (
            await db.execute(select(AIAnalysisMessage).where(
                AIAnalysisMessage.id == invocation.message_id,
                AIAnalysisMessage.tenant_id == invocation.tenant_id,
                AIAnalysisMessage.ai_request_id == invocation.request_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if run is None or message is None:
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_RECONCILIATION_SCOPE_INVALID")

        actual_tokens = int(response.tokens_input) + int(response.tokens_output)
        actual_cost = (
            Decimal(response.tokens_input) * invocation.input_rate
            + Decimal(response.tokens_output) * invocation.output_rate
        ) / Decimal("1000000")
        await BudgetReservationAudit.reconcile(
            db,
            tenant_id=invocation.tenant_id,
            ai_request_id=invocation.request_id,
            reserved_tokens=invocation.reserved_tokens,
            actual_tokens=actual_tokens,
            actual_cost_usd=actual_cost,
            terminal_reason=str(
                response.terminal_error_code
                or (
                    "PROVIDER_RESPONSE_RECEIVED"
                    if invocation.budget_enforcement_enabled
                    else "AUDIT_ONLY_PROVIDER_RESPONSE_RECEIVED"
                )
            ),
        )
        usage_audit = await self._record_provider_usage(
            db,
            tenant_id=invocation.tenant_id,
            ai_request_id=invocation.request_id,
            provider=invocation.provider,
            model=invocation.model,
            tokens_input=int(response.tokens_input),
            tokens_output=int(response.tokens_output),
            estimated_cost=invocation.reserved_cost,
            actual_cost=actual_cost,
            pricing_snapshot_version=invocation.pricing_snapshot_version,
            provider_key_id=invocation.provider_key_id,
            provider_key_tokens_used_month_before=(
                invocation.provider_key_tokens_used_month_before
            ),
        )
        message.provider_transport_attempted = True
        terminal_status = (
            "CANCELLED" if message.status == "CANCELLED" else
            run.status if run.status in _TERMINAL_GRAPH_RUN_STATUSES else None
        )
        if terminal_status is not None:
            await self._attribute_terminal_provider_usage(
                db,
                tenant_id=invocation.tenant_id,
                message=message,
                tokens_input=int(response.tokens_input),
                tokens_output=int(response.tokens_output),
                actual_cost=actual_cost,
            )
        return usage_audit, terminal_status

    @staticmethod
    async def _attribute_terminal_provider_usage(
        db,
        *,
        tenant_id: UUID,
        message: AIAnalysisMessage,
        tokens_input: int,
        tokens_output: int,
        actual_cost: Decimal,
    ) -> None:
        """Attribute a billable response even when cancellation won the race."""
        existing = (
            message.tokens_input,
            message.tokens_output,
            message.cost_usd,
        )
        if any(value is not None for value in existing):
            if (
                all(value is not None for value in existing)
                and message.tokens_input == tokens_input
                and message.tokens_output == tokens_output
                and Decimal(message.cost_usd) == actual_cost
            ):
                return
            raise RuntimeError("ANALYSIS_CHAT_TERMINAL_USAGE_ATTRIBUTION_CONFLICT")

        conversation = (
            await db.execute(
                select(AIAnalysisConversation).where(
                    AIAnalysisConversation.id == message.conversation_id,
                    AIAnalysisConversation.tenant_id == tenant_id,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise RuntimeError("ANALYSIS_CHAT_TERMINAL_USAGE_CONVERSATION_MISSING")

        now = _now()
        message.tokens_input = tokens_input
        message.tokens_output = tokens_output
        message.cost_usd = actual_cost
        message.lock_version = int(message.lock_version or 0) + 1
        conversation.total_tokens_input = (
            int(conversation.total_tokens_input or 0) + tokens_input
        )
        conversation.total_tokens_output = (
            int(conversation.total_tokens_output or 0) + tokens_output
        )
        conversation.total_cost_usd = (
            Decimal(str(conversation.total_cost_usd or 0)) + actual_cost
        )
        conversation.updated_at = now
        conversation.lock_version = int(conversation.lock_version or 0) + 1

    @staticmethod
    def _build_provider_answer(invocation: _ProviderInvocation, response) -> AnalysisChatOutput:
        provider_answer = _validated_provider_answer(
            response.output,
            invocation.output_schema,
        )
        provider_answer = _normalize_provider_parent(
            provider_answer,
            invocation.parent_analysis_run_id,
        )
        provider_answer = provider_answer.model_copy(update={
            "proposal": _translate_change_evidence_labels(
                provider_answer.proposal,
                invocation.selected_evidence_refs,
            ),
        })
        refs = [dict(item) for item in invocation.selected_evidence_refs]
        modules = list(dict.fromkeys(
            str(item.get("module")) for item in refs if item.get("module")
        ))
        refreshed = invocation.data_mode == "ALLOW_READONLY_REFRESH"
        is_proposal = invocation.data_mode == "DRAFT_PROPOSAL"
        answer_type, based_on, proposal = _normalized_provider_mode(
            provider_answer,
            is_proposal=is_proposal,
            refreshed=refreshed,
        )
        return AnalysisChatOutput(
            answer=provider_answer.answer,
            answer_type=answer_type,
            based_on=based_on,
            parent_analysis_run_id=invocation.parent_analysis_run_id,
            modules_consulted=modules,
            evidence_refs=refs,
            new_data_queried=refreshed,
            new_data_window=provider_answer.new_data_window,
            proposal=proposal,
            warnings=provider_answer.warnings,
            limitations=provider_answer.limitations,
            suggested_questions=provider_answer.suggested_questions,
        )

    async def _record_provider_usage(
        self,
        db,
        *,
        tenant_id: UUID,
        ai_request_id: UUID,
        provider: str,
        model: str,
        tokens_input: int,
        tokens_output: int,
        estimated_cost: Decimal,
        actual_cost: Decimal,
        pricing_snapshot_version: str,
        provider_key_id: UUID,
        provider_key_tokens_used_month_before: int,
    ) -> dict[str, int]:
        existing = (
            await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == tenant_id,
                AIUsageRecord.ai_request_id == ai_request_id,
            ).with_for_update())
        ).scalar_one_or_none()
        key = (
            await db.execute(select(AIProviderKey).where(
                AIProviderKey.id == provider_key_id,
                AIProviderKey.user_id == tenant_id,
                AIProviderKey.provider == provider,
            ).with_for_update())
        ).scalar_one_or_none()
        if existing is None:
            db.add(AIUsageRecord(
                tenant_id=tenant_id,
                ai_request_id=ai_request_id,
                provider=provider,
                model=model,
                module="analysis_chat",
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                estimated_cost=estimated_cost,
                actual_cost=actual_cost,
                currency="USD",
                pricing_snapshot_version=pricing_snapshot_version,
            ))
            if key is not None:
                key.tokens_used_month = (
                    int(key.tokens_used_month or 0) + tokens_input + tokens_output
                )
                key.last_used_at = _now()
        elif (
            int(existing.tokens_input) != tokens_input
            or int(existing.tokens_output) != tokens_output
            or Decimal(existing.actual_cost) != actual_cost
        ):
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_USAGE_CONFLICT")
        await db.flush()
        provider_tokens_used_month = (
            int(key.tokens_used_month or 0)
            if key is not None
            else provider_key_tokens_used_month_before + tokens_input + tokens_output
        )
        return {"provider_tokens_used_month": provider_tokens_used_month}

    async def _runtime_config(self, db, tenant_id: UUID) -> AnalysisChatRuntimeConfig:
        record = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == tenant_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "ai_analysis_chat_runtime",
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1))
        ).scalar_one_or_none()
        return AnalysisChatRuntimeConfig.model_validate(record.config_json if record else {})

    async def _ensure_reservation(self, db, run, request) -> AIBudgetReservationRecord:
        existing = (
            await db.execute(select(AIBudgetReservationRecord).where(
                AIBudgetReservationRecord.ai_request_id == request.id
            ).with_for_update())
        ).scalar_one_or_none()
        if existing:
            return existing
        intent = str((request.request_json or {}).get("request_intent") or "NORMAL_ANALYSIS")
        provider = "fake" if intent == "FAKE_PROVIDER_CANARY" else "blocked"
        model = "fake-analysis-v1" if provider == "fake" else "pending-checkpoint"
        reservation = AIBudgetReservationRecord(
            id=uuid.uuid4(),
            tenant_id=run.tenant_id,
            ai_request_id=request.id,
            request_intent=intent,
            provider=provider,
            model=model,
            module="analysis_chat",
            status="RESERVED",
            estimated_input_tokens=0,
            max_output_tokens=0,
            request_token_limit=0,
            daily_token_limit=0,
            monthly_token_limit=0,
            reserved_tokens=0,
            reserved_cost_usd=Decimal("0"),
            provider_transport_attempted=False,
        )
        db.add(reservation)
        await db.flush()
        return reservation

    async def _persist_answer(self, db, run, request, message, conversation, answer: AnalysisChatOutput) -> None:
        existing = (
            await db.execute(select(AIResultRecord).where(
                AIResultRecord.tenant_id == run.tenant_id,
                AIResultRecord.ai_request_id == request.id,
            ))
        ).scalar_one_or_none()
        if existing is None:
            existing = AIResultRecord(
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                status="COMPLETED",
                result_json=answer.model_dump(mode="json"),
                terminal_reason="STAGING_FAKE" if request.request_json.get("request_intent") == "FAKE_PROVIDER_CANARY" else "ANALYSIS_CHAT",
                completed_at=_now(),
            )
            db.add(existing)
            await db.flush()
        usage = (
            await db.execute(select(AIUsageRecord).where(
                AIUsageRecord.tenant_id == run.tenant_id,
                AIUsageRecord.ai_request_id == request.id,
            ))
        ).scalar_one_or_none()
        is_fake = request.request_json.get("request_intent") == "FAKE_PROVIDER_CANARY"
        no_provider_required = request.request_kind == "CHILD_ANALYSIS"
        zero_cost_turn = is_fake or no_provider_required
        if usage is None and zero_cost_turn:
            usage = AIUsageRecord(
                tenant_id=run.tenant_id,
                ai_request_id=request.id,
                provider=message.effective_provider or "fake",
                model=message.effective_model or "fake-analysis-v1",
                module="analysis_chat",
                tokens_input=0,
                tokens_output=0,
                estimated_cost=Decimal("0"),
                actual_cost=Decimal("0"),
                currency="USD",
                pricing_snapshot_version=(
                    "ZERO_COST_FAKE_STAGING" if is_fake else "NO_PROVIDER_REQUIRED"
                ),
            )
            db.add(usage)
        elif usage is None:
            raise RuntimeError("ANALYSIS_CHAT_PROVIDER_USAGE_MISSING")
        reservation = await self._ensure_reservation(db, run, request)
        if zero_cost_turn:
            reservation.status = "RECONCILED"
            reservation.actual_tokens = 0
            reservation.actual_cost_usd = Decimal("0")
            reservation.released_tokens = 0
            reservation.provider_transport_attempted = False
            reservation.terminal_reason = (
                "ZERO_COST_FAKE_RECONCILED" if is_fake else "NO_PROVIDER_REQUIRED_RECONCILED"
            )
            reservation.reconciled_at = _now()
        elif reservation.status != "RECONCILED" or not reservation.provider_transport_attempted:
            raise RuntimeError("ANALYSIS_CHAT_BUDGET_RECONCILIATION_MISSING")

        document = answer.model_dump(mode="json")
        first_completion = message.ai_result_id is None
        message.status = "COMPLETED"
        message.content = answer.answer
        message.content_hash = _sha(answer.answer)
        message.answer_type = answer.answer_type
        message.ai_result_id = existing.id
        message.evidence_refs_json = document["evidence_refs"]
        message.modules_consulted_json = document["modules_consulted"]
        message.warnings_json = document["warnings"]
        message.limitations_json = document["limitations"]
        message.suggested_questions_json = document["suggested_questions"]
        message.new_data_queried = answer.new_data_queried
        message.provider_transport_attempted = not zero_cost_turn
        message.tokens_input = int(usage.tokens_input)
        message.tokens_output = int(usage.tokens_output)
        message.cost_usd = Decimal(usage.actual_cost)
        message.completed_at = _now()
        message.lock_version = int(message.lock_version or 0) + 1
        for ref in answer.evidence_refs:
            try:
                evidence_id = UUID(str(ref.get("evidence_id")))
            except (TypeError, ValueError):
                continue
            evidence = await db.get(AIToolEvidenceRecord, evidence_id)
            if evidence is None or evidence.tenant_id != run.tenant_id:
                raise RuntimeError("ANALYSIS_CHAT_EVIDENCE_SCOPE_INVALID")
            source_run_id = conversation.parent_analysis_run_id
            if evidence.ai_request_id == request.id:
                source_run_id = run.id
            await db.execute(insert(AIAnalysisMessageEvidence).values(
                id=uuid.uuid4(),
                message_id=message.id,
                tenant_id=run.tenant_id,
                evidence_id=evidence.id,
                source_run_id=source_run_id,
                tool_call_id=evidence.tool_call_audit_id,
                relation_type=str(ref.get("source") or "FROZEN_ANALYSIS")[:40],
            ).on_conflict_do_nothing(
                index_elements=[
                    AIAnalysisMessageEvidence.message_id,
                    AIAnalysisMessageEvidence.evidence_id,
                    AIAnalysisMessageEvidence.relation_type,
                ]
            ))
        if first_completion:
            conversation.total_tokens_input = (
                int(conversation.total_tokens_input or 0) + int(usage.tokens_input)
            )
            conversation.total_tokens_output = (
                int(conversation.total_tokens_output or 0) + int(usage.tokens_output)
            )
            conversation.total_cost_usd = (
                Decimal(str(conversation.total_cost_usd or 0))
                + Decimal(usage.actual_cost)
            )
        conversation.updated_at = _now()
        conversation.lock_version = int(conversation.lock_version or 0) + 1

    async def _emit_tokens(self, db, run, request, message, content: str) -> None:
        chunks = [content[index:index + 240] for index in range(0, len(content), 240)] or [""]
        for index, chunk in enumerate(chunks):
            await db.execute(insert(AIGraphEvent).values(
                tenant_id=run.tenant_id,
                graph_run_id=run.id,
                event_key=f"{run.id}:token:{index}",
                event_type="TOKEN",
                node_name="invoke_provider",
                status="STREAMING",
                payload={
                    "conversation_id": str(request.conversation_id),
                    "message_id": str(message.id),
                    "chunk": chunk,
                    "coalesced": True,
                },
            ).on_conflict_do_nothing(
                index_elements=[AIGraphEvent.graph_run_id, AIGraphEvent.event_key]
            ))

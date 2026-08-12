"""Human-confirmed, auditable Analysis Chat configuration changes.

The model can only propose a typed JSON Patch against an existing owned
resource.  It never receives a database/session/tool capable of writing.  The
backend snapshots, validates, approves and applies the change after the human
gate, with optimistic concurrency and a guarded rollback.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.profiles import _validate_profile_config
from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.copilot import CopilotActionPlan, CopilotAuditLog
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from ..schemas.futures_engine_config import FuturesEngineConfig
from ..schemas.spot_engine_config import SpotEngineConfig
from .config_service import config_service
from .profile_optimization_service import document_hash, validate_score_links


ACTION_TYPE = "ANALYSIS_CHAT_GOVERNED_CHANGE"
APPROVAL_TEXT = "UI_CONFIRM_GOVERNED_WRITE"
ROLLBACK_TEXT = "CONFIRMO ROLLBACK"

# These are user-editable operational configuration families.  Runtime gates,
# provider credentials, ML promotion, exchange/order state and secrets are
# intentionally absent.
ALLOWED_CONFIG_TYPES = frozenset({
    "block",
    "crypto_ev",
    "decision_log",
    "filters",
    "futures_engine",
    "indicators",
    "pipeline",
    "pool_config",
    "profile_intelligence",
    "risk",
    "score",
    "spot_engine",
    "strategy",
    "universe",
    "watchlist_performance_ranking",
})
PROFILE_ROOTS = frozenset({
    "default_timeframe",
    "filters",
    "scoring",
    "signals",
    "block_rules",
    "entry_triggers",
})
FORBIDDEN_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "database_url",
    "dsn",
    "jwt",
    "password",
    "provider_key",
    "secret",
    "token",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_pointer(path: str) -> list[str | int]:
    if not path.startswith("/") or path == "/":
        raise ValueError("JSON Patch path must identify a field below the root")
    parts: list[str | int] = []
    for raw in path[1:].split("/"):
        value = raw.replace("~1", "/").replace("~0", "~")
        if any(fragment in value.lower() for fragment in FORBIDDEN_KEY_FRAGMENTS):
            raise ValueError(f"Sensitive field is outside chat authority: {path}")
        parts.append(int(value) if value.isdigit() else value)
    return parts


def _read(document: Any, part: str | int, *, path: str) -> Any:
    if isinstance(part, int):
        if not isinstance(document, list) or part >= len(document):
            raise ValueError(f"List index does not exist at {path}")
        return document[part]
    if not isinstance(document, dict) or part not in document:
        raise ValueError(f"Field does not exist at {path}")
    return document[part]


def apply_typed_patch(
    document: dict[str, Any],
    changes: list[dict[str, Any]],
    *,
    allowed_roots: frozenset[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not changes:
        raise ValueError("At least one configuration change is required")
    if len(changes) > 100:
        raise ValueError("A governed change is limited to 100 patch operations")
    candidate = deepcopy(document)
    diff: list[dict[str, Any]] = []
    for change in changes:
        op = str(change.get("op") or "replace").lower()
        if op not in {"add", "replace", "remove"}:
            raise ValueError(f"Unsupported patch operation: {op}")
        path = str(change.get("path") or "")
        parts = _decode_pointer(path)
        root = parts[0]
        if not isinstance(root, str):
            raise ValueError(f"Root path must be a field name: {path}")
        if allowed_roots is not None and root not in allowed_roots:
            raise ValueError(f"Path outside resource allowlist: {path}")
        # Generic config updates may only modify an existing top-level family.
        # This prevents the model from inventing a new configuration contract.
        if allowed_roots is None and root not in document:
            raise ValueError(f"Unknown configuration root: {root}")

        parent: Any = candidate
        for index, part in enumerate(parts[:-1]):
            parent = _read(parent, part, path="/" + "/".join(map(str, parts[: index + 1])))
        leaf = parts[-1]
        old_exists = False
        old_value: Any = None
        if isinstance(leaf, int):
            if not isinstance(parent, list):
                raise ValueError(f"Expected a list at {path}")
            if op == "add":
                if leaf > len(parent):
                    raise ValueError(f"List add index is out of range at {path}")
                old_exists = leaf < len(parent)
                old_value = deepcopy(parent[leaf]) if old_exists else None
                parent.insert(leaf, deepcopy(change.get("value")))
            else:
                if leaf >= len(parent):
                    raise ValueError(f"List index does not exist at {path}")
                old_exists = True
                old_value = deepcopy(parent[leaf])
                if op == "remove":
                    parent.pop(leaf)
                else:
                    parent[leaf] = deepcopy(change.get("value"))
        else:
            if not isinstance(parent, dict):
                raise ValueError(f"Expected an object at {path}")
            old_exists = leaf in parent
            old_value = deepcopy(parent.get(leaf))
            if op in {"replace", "remove"} and not old_exists:
                raise ValueError(f"Field does not exist at {path}")
            if op == "remove":
                del parent[leaf]
            else:
                parent[leaf] = deepcopy(change.get("value"))
        if "old_value" in change and change.get("old_value") is not None:
            if old_value != change.get("old_value"):
                raise ValueError(f"Stale old_value at {path}")
        diff.append({
            "op": op,
            "path": path,
            "old_value": old_value if old_exists else None,
            "value": None if op == "remove" else deepcopy(change.get("value")),
            "reason": str(change.get("reason") or "Requested in Analysis Chat")[:2000],
            "evidence_refs": [str(item) for item in change.get("evidence_refs") or []],
        })
    return candidate, diff


def _validate_config_candidate(config_type: str, candidate: dict[str, Any]) -> dict[str, Any]:
    if config_type == "spot_engine":
        validated = SpotEngineConfig.from_config_json(candidate).model_dump()
        if validated["selling"]["never_sell_at_loss"] is not True:
            raise ValueError("Spot invariant requires selling.never_sell_at_loss=true")
        return candidate
    if config_type == "futures_engine":
        FuturesEngineConfig.from_config_json(candidate)
        return candidate
    return candidate


async def _runtime_allows_write(db: AsyncSession, user_id: UUID) -> bool:
    record = (
        await db.execute(select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id.is_(None),
            ConfigProfile.config_type == "ai_analysis_chat_runtime",
            ConfigProfile.is_active.is_(True),
        ).order_by(ConfigProfile.updated_at.desc()).limit(1))
    ).scalar_one_or_none()
    config = dict(record.config_json or {}) if record else {}
    return (
        config.get("governed_actions_enabled") is True
        and config.get("live_config_write_enabled") is True
    )


async def create_dry_run(
    db: AsyncSession,
    user_id: UUID,
    *,
    proposal: dict[str, Any],
    conversation_id: UUID,
    message_id: UUID,
    evidence_ids: set[str],
) -> dict[str, Any]:
    operation = str(proposal.get("operation_type") or "")
    target = dict(proposal.get("target") or {})
    changes = list(proposal.get("changes") or [])
    referenced = {
        str(ref)
        for change in changes
        for ref in change.get("evidence_refs") or []
    }
    if not referenced or not referenced.issubset(evidence_ids):
        raise ValueError("Every proposed change requires evidence from the parent analysis")

    if operation == "UPDATE_PROFILE_CONFIG":
        try:
            profile_id = UUID(str(target.get("profile_id")))
        except (TypeError, ValueError) as exc:
            raise ValueError("UPDATE_PROFILE_CONFIG requires a valid profile_id") from exc
        resource = (
            await db.execute(select(Profile).where(
                Profile.id == profile_id,
                Profile.user_id == user_id,
            ))
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Profile not found")
        expected_name = str(target.get("profile_name") or "").strip()
        if expected_name and expected_name != resource.name:
            raise ValueError("Target profile_name does not match the owned profile")
        before = deepcopy(resource.config or {})
        candidate, diff = apply_typed_patch(before, changes, allowed_roots=PROFILE_ROOTS)
        candidate = _validate_profile_config(candidate)
        score = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "score",
                ConfigProfile.is_active.is_(True),
            ).limit(1))
        ).scalar_one_or_none()
        if score is not None and (score.config_json or {}).get("scoring_rules") is not None:
            validate_score_links(candidate, score.config_json or {})
        target_type = "PROFILE"
        target_id = str(resource.id)
        target_label = resource.name
        state_hash = document_hash({
            "config": before,
            "profile_version": resource.profile_version,
            "updated_at": resource.updated_at,
        })
        payload = {
            "operation_type": operation,
            "profile_id": str(resource.id),
            "profile_name": resource.name,
            "source_document": before,
            "candidate_document": candidate,
        }
    elif operation == "UPDATE_CONFIG_PROFILE":
        config_type = str(target.get("config_type") or "").strip()
        if config_type not in ALLOWED_CONFIG_TYPES:
            raise ValueError(f"Configuration family is outside chat authority: {config_type}")
        pool_id = UUID(str(target["pool_id"])) if target.get("pool_id") else None
        resource = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.pool_id == pool_id,
                ConfigProfile.config_type == config_type,
                ConfigProfile.is_active.is_(True),
            ).order_by(ConfigProfile.updated_at.desc()).limit(1))
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Configuration profile not found")
        before = deepcopy(resource.config_json or {})
        candidate, diff = apply_typed_patch(before, changes)
        candidate = _validate_config_candidate(config_type, candidate)
        if config_type == "score" and candidate.get("scoring_rules") is not None:
            profiles = list((await db.execute(select(Profile).where(
                Profile.user_id == user_id,
            ))).scalars().all())
            for profile in profiles:
                validate_score_links(profile.config or {}, candidate)
        target_type = "CONFIG_PROFILE"
        target_id = str(resource.id)
        target_label = config_type
        state_hash = document_hash({
            "config": before,
            "updated_at": resource.updated_at,
        })
        payload = {
            "operation_type": operation,
            "config_profile_id": str(resource.id),
            "config_type": config_type,
            "pool_id": str(pool_id) if pool_id else None,
            "source_document": before,
            "candidate_document": candidate,
        }
    else:
        raise ValueError(f"Unsupported governed operation: {operation}")

    plan = CopilotActionPlan(
        user_id=user_id,
        action_type=ACTION_TYPE,
        target_type=target_type,
        target_id=target_id,
        objective=str(proposal.get("objective") or f"Update {target_label}")[:2000],
        evidence={
            "source": "ANALYSIS_CHAT",
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "evidence_ids": sorted(referenced),
        },
        proposed_diff=diff,
        execution_payload=payload,
        risk_assessment=str(proposal.get("risk") or "Operational configuration change")[:4000],
        rollback_plan={
            "action": "RESTORE_SNAPSHOT",
            "source_document": before,
            "source_document_hash": document_hash(before),
        },
        target_state_hash=state_hash,
        status="DRY_RUN",
    )
    db.add(plan)
    await db.flush()
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_DRY_RUN_CREATED",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={
            "conversation_id": str(conversation_id),
            "message_id": str(message_id),
            "operation_type": operation,
            "target_type": target_type,
            "target_id": target_id,
            "diff": diff,
        },
    ))
    await db.commit()
    await db.refresh(plan)
    return plan_to_dict(plan)


def plan_to_dict(plan: CopilotActionPlan) -> dict[str, Any]:
    return {
        "proposal_id": str(plan.id),
        "operation_type": (plan.execution_payload or {}).get("operation_type"),
        "target_type": plan.target_type,
        "target_id": plan.target_id,
        "target": {
            key: value for key, value in (plan.execution_payload or {}).items()
            if key in {"profile_id", "profile_name", "config_type", "pool_id"}
        },
        "objective": plan.objective,
        "risk": plan.risk_assessment,
        "changes": plan.proposed_diff or [],
        "status": plan.status,
        "requires_human_approval": plan.status == "DRY_RUN",
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "executed_at": plan.executed_at.isoformat() if plan.executed_at else None,
        "execution_result": plan.execution_result,
        "rollback_available": plan.status == "EXECUTED",
    }


async def get_plan(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    lock: bool = False,
) -> CopilotActionPlan:
    query = select(CopilotActionPlan).where(
        CopilotActionPlan.id == plan_id,
        CopilotActionPlan.user_id == user_id,
        CopilotActionPlan.action_type == ACTION_TYPE,
    )
    if lock:
        query = query.with_for_update()
    plan = (await db.execute(query)).scalar_one_or_none()
    if plan is None:
        raise LookupError("Governed change proposal not found")
    return plan


async def approve_and_execute(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    decision_id: str | None,
) -> dict[str, Any]:
    if not await _runtime_allows_write(db, user_id):
        raise ValueError("ANALYSIS_CHAT_LIVE_CONFIG_WRITE_DISABLED")
    plan = await get_plan(db, user_id, plan_id, lock=True)
    if plan.status == "EXECUTED":
        return plan_to_dict(plan)
    if plan.status != "DRY_RUN":
        raise ValueError(f"Proposal cannot execute from status {plan.status}")
    now = _now()
    plan.status = "APPROVED"
    plan.approved_at = now
    plan.approved_by = user_id
    plan.approval_text = APPROVAL_TEXT
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_APPROVED",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={
            "approval_method": APPROVAL_TEXT,
            "decision_id": decision_id,
            "approved_at": now.isoformat(),
        },
    ))

    payload = dict(plan.execution_payload or {})
    operation = payload.get("operation_type")
    candidate = deepcopy(payload.get("candidate_document") or {})
    if operation == "UPDATE_PROFILE_CONFIG":
        resource = (
            await db.execute(select(Profile).where(
                Profile.id == UUID(payload["profile_id"]),
                Profile.user_id == user_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Profile not found")
        current_hash = document_hash({
            "config": resource.config or {},
            "profile_version": resource.profile_version,
            "updated_at": resource.updated_at,
        })
        if current_hash != plan.target_state_hash:
            plan.status = "STALE"
            await db.commit()
            raise ValueError("Profile changed after preview; create a new proposal")
        candidate = _validate_profile_config(candidate)
        old_config = deepcopy(resource.config or {})
        old_version = resource.profile_version
        resource.config = candidate
        resource.profile_version = now
        resource.updated_at = now
        db.add(ProfileAuditLog(
            user_id=user_id,
            profile_id=resource.id,
            changed_by=user_id,
            change_source="analysis_chat_human_confirmed",
            change_description=f"Governed Analysis Chat proposal {plan.id}: {plan.objective}",
            previous_config=old_config,
            new_config=candidate,
            previous_profile_version=old_version,
            new_profile_version=now,
        ))
        result = {
            "status": "EXECUTED",
            "resource_type": "PROFILE",
            "resource_id": str(resource.id),
            "profile_name": resource.name,
            "new_document_hash": document_hash(candidate),
            "live_config_changed": True,
        }
        cache_type = None
    elif operation == "UPDATE_CONFIG_PROFILE":
        resource = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.id == UUID(payload["config_profile_id"]),
                ConfigProfile.user_id == user_id,
                ConfigProfile.is_active.is_(True),
            ).with_for_update())
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Configuration profile not found")
        current_hash = document_hash({
            "config": resource.config_json or {},
            "updated_at": resource.updated_at,
        })
        if current_hash != plan.target_state_hash:
            plan.status = "STALE"
            await db.commit()
            raise ValueError("Configuration changed after preview; create a new proposal")
        candidate = _validate_config_candidate(resource.config_type, candidate)
        old_config = deepcopy(resource.config_json or {})
        resource.config_json = candidate
        resource.updated_at = now
        db.add(ConfigAuditLog(
            config_id=resource.id,
            changed_by=user_id,
            previous_json=old_config,
            new_json=candidate,
            change_description=f"Governed Analysis Chat proposal {plan.id}: {plan.objective}",
        ))
        result = {
            "status": "EXECUTED",
            "resource_type": "CONFIG_PROFILE",
            "resource_id": str(resource.id),
            "config_type": resource.config_type,
            "new_document_hash": document_hash(candidate),
            "live_config_changed": True,
        }
        cache_type = resource.config_type
    else:
        raise ValueError(f"Unsupported governed operation: {operation}")

    plan.status = "EXECUTED"
    plan.executed_at = now
    plan.execution_result = result
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_EXECUTED",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={**result, "diff": plan.proposed_diff or []},
    ))
    await db.commit()
    if cache_type:
        await config_service.invalidate_cache(cache_type, user_id, resource.pool_id)
    await db.refresh(plan)
    return plan_to_dict(plan)


async def rollback(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    confirmation_text: str,
) -> dict[str, Any]:
    if " ".join(confirmation_text.strip().upper().split()) != ROLLBACK_TEXT:
        raise ValueError(f"Type exactly {ROLLBACK_TEXT}")
    plan = await get_plan(db, user_id, plan_id, lock=True)
    if plan.status != "EXECUTED":
        raise ValueError("Only an executed proposal can be rolled back")
    payload = dict(plan.execution_payload or {})
    result = dict(plan.execution_result or {})
    source = deepcopy((plan.rollback_plan or {}).get("source_document") or {})
    candidate_hash = str(result.get("new_document_hash") or "")
    now = _now()
    cache_type: str | None = None
    if payload.get("operation_type") == "UPDATE_PROFILE_CONFIG":
        resource = (
            await db.execute(select(Profile).where(
                Profile.id == UUID(payload["profile_id"]),
                Profile.user_id == user_id,
            ).with_for_update())
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Profile not found")
        if document_hash(resource.config or {}) != candidate_hash:
            raise ValueError("Profile changed after execution; rollback would overwrite newer work")
        previous = deepcopy(resource.config or {})
        previous_version = resource.profile_version
        resource.config = _validate_profile_config(source)
        resource.profile_version = now
        resource.updated_at = now
        db.add(ProfileAuditLog(
            user_id=user_id,
            profile_id=resource.id,
            changed_by=user_id,
            change_source="analysis_chat_human_confirmed_rollback",
            change_description=f"Rollback governed Analysis Chat proposal {plan.id}",
            previous_config=previous,
            new_config=source,
            previous_profile_version=previous_version,
            new_profile_version=now,
        ))
    elif payload.get("operation_type") == "UPDATE_CONFIG_PROFILE":
        resource = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.id == UUID(payload["config_profile_id"]),
                ConfigProfile.user_id == user_id,
                ConfigProfile.is_active.is_(True),
            ).with_for_update())
        ).scalar_one_or_none()
        if resource is None:
            raise LookupError("Configuration profile not found")
        if document_hash(resource.config_json or {}) != candidate_hash:
            raise ValueError("Configuration changed after execution; rollback would overwrite newer work")
        previous = deepcopy(resource.config_json or {})
        source = _validate_config_candidate(resource.config_type, source)
        resource.config_json = source
        resource.updated_at = now
        cache_type = resource.config_type
        db.add(ConfigAuditLog(
            config_id=resource.id,
            changed_by=user_id,
            previous_json=previous,
            new_json=source,
            change_description=f"Rollback governed Analysis Chat proposal {plan.id}",
        ))
    else:
        raise ValueError("Unsupported rollback operation")
    rollback_result = {
        "status": "ROLLED_BACK",
        "resource_id": plan.target_id,
        "restored_document_hash": document_hash(source),
        "rolled_back_at": now.isoformat(),
    }
    plan.status = "ROLLED_BACK"
    plan.execution_result = {**result, "rollback": rollback_result}
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_ROLLED_BACK",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload=rollback_result,
    ))
    await db.commit()
    if cache_type:
        await config_service.invalidate_cache(cache_type, user_id, resource.pool_id)
    await db.refresh(plan)
    return plan_to_dict(plan)

"""Guarded candidate-only profile optimization with immutable versions."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.profiles import _validate_profile_config
from ..copilot.action_service import action_to_dict, profile_state_hash
from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.copilot import CopilotActionPlan, CopilotAuditLog
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from .config_service import config_service
from .profile_versioning_v2 import create_candidate_profile_version


ALLOWED_ROOTS = {
    "default_timeframe",
    "filters",
    "scoring",
    "signals",
    "block_rules",
    "entry_triggers",
}
APPROVAL_TEXT = "CONFIRMO APLICAR"


def document_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _pointer_parts(path: str) -> list[str | int]:
    if not path.startswith("/"):
        raise ValueError(f"JSON Patch path must start with '/': {path}")
    raw = path[1:].split("/") if path != "/" else []
    parts: list[str | int] = []
    for value in raw:
        decoded = value.replace("~1", "/").replace("~0", "~")
        parts.append(int(decoded) if decoded.isdigit() else decoded)
    if not parts or parts[0] not in ALLOWED_ROOTS:
        raise ValueError(f"Path outside optimization allowlist: {path}")
    return parts


def _read(document: Any, parts: list[str | int]) -> Any:
    cursor = document
    for part in parts:
        if isinstance(part, int):
            if not isinstance(cursor, list) or part >= len(cursor):
                raise ValueError(f"List index does not exist: {part}")
        elif not isinstance(cursor, dict) or part not in cursor:
            raise ValueError(f"Field does not exist: {part}")
        cursor = cursor[part]
    return cursor


def apply_json_patch(config: dict[str, Any], changes: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate = deepcopy(config)
    normalized: list[dict[str, Any]] = []
    for change in changes:
        op = str(change.get("op", "replace")).lower()
        if op not in {"add", "replace", "remove"}:
            raise ValueError(f"Unsupported patch operation: {op}")
        path = str(change.get("path", ""))
        parts = _pointer_parts(path)
        parent = candidate
        for part in parts[:-1]:
            parent = _read(parent, [part])
        leaf = parts[-1]
        old_exists = False
        old_value = None
        if isinstance(leaf, int):
            if not isinstance(parent, list):
                raise ValueError(f"Expected list parent at {path}")
            if op == "add":
                if leaf > len(parent):
                    raise ValueError(f"List add index out of range at {path}")
                old_exists = leaf < len(parent)
                old_value = parent[leaf] if old_exists else None
                parent.insert(leaf, deepcopy(change.get("value")))
            else:
                if leaf >= len(parent):
                    raise ValueError(f"List index does not exist at {path}")
                old_exists = True
                old_value = parent[leaf]
                if op == "remove":
                    parent.pop(leaf)
                else:
                    parent[leaf] = deepcopy(change.get("value"))
        else:
            if not isinstance(parent, dict):
                raise ValueError(f"Expected object parent at {path}")
            old_exists = leaf in parent
            old_value = parent.get(leaf)
            if op in {"replace", "remove"} and not old_exists:
                raise ValueError(f"Field does not exist at {path}")
            if op == "remove":
                del parent[leaf]
            else:
                parent[leaf] = deepcopy(change.get("value"))
        if "old_value" in change and change.get("old_value") is not None and old_value != change.get("old_value"):
            raise ValueError(f"Stale old_value at {path}")
        normalized.append(
            {
                "op": op,
                "path": path,
                "old_value": old_value if old_exists else None,
                "value": None if op == "remove" else deepcopy(change.get("value")),
                "reason": change.get("reason") or "optimization recommendation",
                "evidence_refs": change.get("evidence_refs") or [],
            }
        )
    return candidate, normalized


def apply_score_matrix_patch(global_config: dict[str, Any], patch: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidate = deepcopy(global_config)
    rules = list(candidate.get("scoring_rules") or candidate.get("rules") or [])
    by_id: dict[str, dict[str, Any]] = {}
    for rule in rules:
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id:
            raise ValueError("Every global score rule must have an id")
        if rule_id in by_id:
            raise ValueError(f"Duplicate global score rule id: {rule_id}")
        by_id[rule_id] = deepcopy(rule)
    diff: list[dict[str, Any]] = []
    for rule in patch.get("upsert_rules") or []:
        if not isinstance(rule, dict):
            raise ValueError("score_matrix_patch.upsert_rules items must be objects")
        rule_id = str(rule.get("id") or "").strip()
        if not rule_id:
            raise ValueError("Upserted global score rule requires id")
        for field in ("indicator", "operator", "points", "category"):
            if field not in rule:
                raise ValueError(f"Global score rule {rule_id} missing {field}")
        old = by_id.get(rule_id)
        by_id[rule_id] = deepcopy(rule)
        diff.append({"op": "replace" if old else "add", "rule_id": rule_id, "old_value": old, "value": rule})
    for rule_id in patch.get("remove_rule_ids") or []:
        rule_id = str(rule_id)
        if rule_id not in by_id:
            raise ValueError(f"Cannot remove missing global score rule: {rule_id}")
        old = by_id.pop(rule_id)
        diff.append({"op": "remove", "rule_id": rule_id, "old_value": old, "value": None})
    candidate["scoring_rules"] = list(by_id.values())
    candidate.pop("rules", None)
    return candidate, diff


def _iter_conditions(config: dict[str, Any]):
    for section_name in ("filters", "signals", "entry_triggers"):
        section = config.get(section_name) or {}
        for condition in section.get("conditions") or []:
            if isinstance(condition, dict):
                yield section_name, condition
    for block in ((config.get("block_rules") or {}).get("blocks") or []):
        if not isinstance(block, dict):
            continue
        for condition in block.get("conditions") or []:
            if isinstance(condition, dict):
                yield "block_rules", condition


def validate_score_links(profile_config: dict[str, Any], global_score: dict[str, Any]) -> dict[str, Any]:
    rules = list(global_score.get("scoring_rules") or [])
    rule_ids = [str(rule.get("id") or "") for rule in rules]
    if len(rule_ids) != len(set(rule_ids)):
        raise ValueError("Global Score Engine contains duplicate rule IDs")
    known = set(rule_ids)
    scoring = profile_config.get("scoring") or {}
    selected = [str(value) for value in scoring.get("selected_rule_ids") or []]
    if len(selected) != len(set(selected)):
        raise ValueError("Profile scoring contains duplicate selected_rule_ids")
    missing = sorted(set(selected) - known)
    explicit: set[str] = set()
    uses_score_gate = False
    for _, condition in _iter_conditions(profile_config):
        field = str(condition.get("field") or condition.get("indicator") or "")
        if field == "score":
            uses_score_gate = True
        if condition.get("rule_id"):
            explicit.add(str(condition["rule_id"]))
    scoring_enabled = scoring.get("enabled", True) is not False
    if (scoring_enabled or uses_score_gate) and not selected:
        raise ValueError("scoring.selected_rule_ids is required for an enabled score or score gate")
    if missing:
        raise ValueError(f"Selected score rule IDs do not exist globally: {', '.join(missing)}")
    explicit_missing = sorted(explicit - known)
    if explicit_missing:
        raise ValueError(f"Condition rule_ids do not exist globally: {', '.join(explicit_missing)}")
    unselected = sorted(explicit - set(selected))
    if unselected:
        raise ValueError(f"Condition rule_ids must also be selected by the profile: {', '.join(unselected)}")
    resolved = [rule for rule in rules if str(rule.get("id")) in set(selected)]
    if len(resolved) != len(set(selected)):
        raise ValueError("Profile scoring association did not resolve exactly")
    return {"selected_rule_ids": selected, "resolved_rule_ids": [str(rule["id"]) for rule in resolved]}


async def _global_score_record(db: AsyncSession, user_id: UUID, *, lock: bool = False) -> ConfigProfile:
    query = select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.config_type == "score",
        ConfigProfile.is_active.is_(True),
    )
    if lock:
        query = query.with_for_update()
    record = (await db.execute(query)).scalars().first()
    if record is None:
        raise ValueError("Global Score Engine Configuration not found")
    return record


async def _profile_record(db: AsyncSession, user_id: UUID, profile_id: UUID, *, lock: bool = False) -> Profile:
    query = select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
    if lock:
        query = query.with_for_update()
    profile = (await db.execute(query)).scalar_one_or_none()
    if profile is None:
        raise LookupError("Profile not found")
    return profile


async def create_dry_run(
    db: AsyncSession,
    user_id: UUID,
    *,
    patch: dict[str, Any],
    source: str,
    source_id: str | None = None,
) -> dict[str, Any]:
    target = patch.get("target") or {}
    profile_id = UUID(str(target.get("profile_id")))
    profile = await _profile_record(db, user_id, profile_id)
    if str(target.get("profile_name") or "") != profile.name:
        raise ValueError("Target profile_name does not match the owned profile")
    expected_hash = target.get("expected_profile_config_hash")
    current_config_hash = document_hash(profile.config or {})
    if expected_hash and expected_hash != current_config_hash:
        raise ValueError("Target profile config hash is stale")
    expected_version = target.get("expected_profile_version")
    current_version = profile.profile_version.isoformat() if profile.profile_version else None
    if expected_version is not None and str(expected_version) != str(current_version):
        raise ValueError("Target profile_version is stale")

    score_record = await _global_score_record(db, user_id)
    current_score = deepcopy(score_record.config_json or {})
    expected_score_hash = (patch.get("score_matrix_patch") or {}).get("expected_config_hash")
    current_score_hash = document_hash(current_score)
    if expected_score_hash and expected_score_hash != current_score_hash:
        raise ValueError("Global Score Engine config hash is stale")

    candidate_config, profile_diff = apply_json_patch(profile.config or {}, patch.get("changes") or [])
    score_assignment = patch.get("score_assignment") or {}
    if "selected_rule_ids" in score_assignment:
        candidate_config.setdefault("scoring", {})["selected_rule_ids"] = [
            str(value) for value in score_assignment.get("selected_rule_ids") or []
        ]
    candidate_config = _validate_profile_config(candidate_config)
    candidate_score, score_diff = apply_score_matrix_patch(current_score, patch.get("score_matrix_patch") or {})

    all_profiles = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalars().all()
    references: dict[str, list[dict[str, str]]] = {}
    for candidate_profile in all_profiles:
        selected = ((candidate_profile.config or {}).get("scoring") or {}).get("selected_rule_ids") or []
        for rule_id in selected:
            references.setdefault(str(rule_id), []).append(
                {"profile_id": str(candidate_profile.id), "profile_name": candidate_profile.name}
            )
    for rule_id in (patch.get("score_matrix_patch") or {}).get("remove_rule_ids") or []:
        if references.get(str(rule_id)):
            raise ValueError(f"Cannot remove score rule {rule_id}; it is referenced by profiles")
    shared_impacts = []
    for change in score_diff:
        if change["op"] == "replace":
            affected = [item for item in references.get(change["rule_id"], []) if item["profile_id"] != str(profile.id)]
            if affected:
                shared_impacts.append({"rule_id": change["rule_id"], "profiles": affected})
    if shared_impacts:
        raise ValueError("Shared global score rules cannot be modified by a single-profile optimization; create a new rule ID")

    score_links = validate_score_links(candidate_config, candidate_score)
    constraints = patch.get("constraints") or {}
    for key in ("preserve_profile_id", "preserve_profile_name"):
        if constraints.get(key) is not True:
            raise ValueError(f"Optimization constraint must be true: {key}")
    if constraints.get("preserve_profile_version") is not False:
        raise ValueError("Optimization must create a new candidate profile version")
    if constraints.get("create_profile") is not False:
        raise ValueError("Optimization must explicitly set create_profile=false")

    evidence = deepcopy(patch.get("evidence") or {})
    evidence.update({"source": source, "source_id": source_id, "score_links": score_links})
    plan = CopilotActionPlan(
        user_id=user_id,
        action_type="OPTIMIZE_PROFILE_CANDIDATE",
        target_type="PROFILE_AND_SCORE_MATRIX",
        target_id=str(profile.id),
        objective=patch.get("objective") or f"Optimize profile {profile.name} in place",
        evidence=evidence,
        proposed_diff={"profile": profile_diff, "score_matrix": score_diff},
        execution_payload={
            "mode": "OPTIMIZE_PROFILE_CANDIDATE",
            "profile_id": str(profile.id),
            "expected_profile_id": str(profile.id),
            "expected_profile_name": profile.name,
            "expected_profile_version": current_version,
            "source_profile_config": deepcopy(profile.config or {}),
            "candidate_profile_config": candidate_config,
            "source_score_config": current_score,
            "candidate_score_config": candidate_score,
            "source_score_config_hash": current_score_hash,
            "candidate_score_config_hash": document_hash(candidate_score),
        },
        risk_assessment=patch.get("risk") or "In-place profile and Score Engine change; explicit approval required",
        rollback_plan={
            "action": "RESTORE_PROFILE_AND_SCORE_SNAPSHOTS",
            "profile_config": deepcopy(profile.config or {}),
            "score_config": current_score,
            "preserve_identity": True,
            "preserve_profile_version": False,
        },
        target_state_hash=profile_state_hash(profile),
        status="DRY_RUN",
    )
    db.add(plan)
    await db.flush()
    db.add(
        CopilotAuditLog(
            user_id=user_id,
            event_type="PROFILE_OPTIMIZATION_DRY_RUN_CREATED",
            actor_user_id=user_id,
            action_plan_id=plan.id,
            payload={
                "profile_id": str(profile.id),
                "profile_config_hash": current_config_hash,
                "score_config_hash": current_score_hash,
                "diff": plan.proposed_diff,
            },
        )
    )
    await db.commit()
    await db.refresh(plan)
    return optimization_to_dict(plan)


def optimization_to_dict(plan: CopilotActionPlan) -> dict[str, Any]:
    base = action_to_dict(plan)
    base["approval_required_text"] = APPROVAL_TEXT
    base["preserves_profile_identity"] = True
    base["preserves_profile_version"] = False
    base["creates_candidate_version"] = True
    return base


async def get_plan(db: AsyncSession, user_id: UUID, plan_id: UUID, *, lock: bool = False) -> CopilotActionPlan:
    query = select(CopilotActionPlan).where(
        CopilotActionPlan.id == plan_id,
        CopilotActionPlan.user_id == user_id,
        CopilotActionPlan.action_type == "OPTIMIZE_PROFILE_CANDIDATE",
    )
    if lock:
        query = query.with_for_update()
    plan = (await db.execute(query)).scalar_one_or_none()
    if plan is None:
        raise LookupError("Profile optimization plan not found")
    return plan


async def approve(db: AsyncSession, user_id: UUID, plan_id: UUID, confirmation_text: str) -> dict[str, Any]:
    if " ".join(confirmation_text.strip().upper().split()) != APPROVAL_TEXT:
        raise ValueError(f"Type exactly {APPROVAL_TEXT}")
    plan = await get_plan(db, user_id, plan_id, lock=True)
    if plan.status != "DRY_RUN":
        raise ValueError(f"Plan cannot be approved from status {plan.status}")
    now = datetime.now(timezone.utc)
    plan.status = "APPROVED"
    plan.approved_at = now
    plan.approved_by = user_id
    plan.approval_text = APPROVAL_TEXT
    db.add(
        CopilotAuditLog(
            user_id=user_id,
            event_type="PROFILE_OPTIMIZATION_APPROVED",
            actor_user_id=user_id,
            action_plan_id=plan.id,
            payload={"confirmation_text": APPROVAL_TEXT, "approved_at": now.isoformat()},
        )
    )
    await db.commit()
    await db.refresh(plan)
    return optimization_to_dict(plan)


async def execute(db: AsyncSession, user_id: UUID, plan_id: UUID) -> dict[str, Any]:
    plan = await get_plan(db, user_id, plan_id, lock=True)
    if plan.status != "APPROVED" or plan.approved_by != user_id:
        raise ValueError("Optimization plan is not approved by current user")
    payload = plan.execution_payload or {}
    profile = await _profile_record(db, user_id, UUID(payload["profile_id"]), lock=True)
    score_record = await _global_score_record(db, user_id, lock=True)
    if profile_state_hash(profile) != plan.target_state_hash:
        plan.status = "STALE"
        await db.commit()
        raise ValueError("Profile changed after DRY_RUN; create a new plan")
    if document_hash(score_record.config_json or {}) != payload["source_score_config_hash"]:
        plan.status = "STALE"
        await db.commit()
        raise ValueError("Score Engine changed after DRY_RUN; create a new plan")
    expected_version = payload.get("expected_profile_version")
    current_version = profile.profile_version.isoformat() if profile.profile_version else None
    if str(profile.id) != payload["expected_profile_id"] or profile.name != payload["expected_profile_name"] or current_version != expected_version:
        plan.status = "STALE"
        await db.commit()
        raise ValueError("Profile identity or version changed after DRY_RUN")

    candidate_config = _validate_profile_config(deepcopy(payload["candidate_profile_config"]))
    candidate_score = deepcopy(payload["candidate_score_config"])
    validate_score_links(candidate_config, candidate_score)
    old_profile_config = deepcopy(profile.config or {})
    candidate_version_id, candidate_score_version_id, created = await create_candidate_profile_version(
        db, profile_id=profile.id, config=candidate_config, score_config=candidate_score,
        change_set_id=plan.id, mutation_reason=f"systemic_ai_profile_optimization:{plan.id}",
    )
    db.add(
        ProfileAuditLog(
            user_id=user_id,
            profile_id=profile.id,
            changed_by=user_id,
            change_source="systemic_ai_profile_optimization_candidate",
            change_description=f"Candidate-only optimization action plan {plan.id}",
            previous_config=old_profile_config,
            new_config=candidate_config,
            previous_profile_version=profile.profile_version,
            new_profile_version=profile.profile_version,
        )
    )
    now = datetime.now(timezone.utc)
    result = {
        "status": "EXECUTED",
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "profile_version": current_version,
        "candidate_profile_version_id": str(candidate_version_id),
        "candidate_score_engine_version_id": str(candidate_score_version_id),
        "candidate_created": created,
        "profile_identity_preserved": True,
        "profile_version_preserved": False,
        "profile_config_hash": document_hash(candidate_config),
        "score_config_hash": document_hash(candidate_score),
        "live_authority_changed": False,
        "profile_created": False,
    }
    plan.status = "EXECUTED"
    plan.executed_at = now
    plan.execution_result = result
    db.add(
        CopilotAuditLog(
            user_id=user_id,
            event_type="PROFILE_OPTIMIZATION_EXECUTED",
            actor_user_id=user_id,
            action_plan_id=plan.id,
            payload={**result, "diff": plan.proposed_diff},
        )
    )
    await db.commit()
    await config_service.invalidate_cache("score", user_id)
    await db.refresh(plan)
    return optimization_to_dict(plan)


async def rollback(db: AsyncSession, user_id: UUID, plan_id: UUID, confirmation_text: str) -> dict[str, Any]:
    if " ".join(confirmation_text.strip().upper().split()) != "CONFIRMO ROLLBACK":
        raise ValueError("Type exactly CONFIRMO ROLLBACK")
    plan = await get_plan(db, user_id, plan_id, lock=True)
    if plan.status != "EXECUTED":
        raise ValueError("Only an executed optimization can be rolled back")
    payload = plan.execution_payload or {}
    profile = await _profile_record(db, user_id, UUID(payload["profile_id"]), lock=True)
    score_record = await _global_score_record(db, user_id, lock=True)
    current_version = profile.profile_version
    restored_profile = deepcopy((plan.rollback_plan or {})["profile_config"])
    restored_score = deepcopy((plan.rollback_plan or {})["score_config"])
    executed_candidate_id = UUID(str((plan.execution_result or {})["candidate_profile_version_id"]))
    rollback_version_id, rollback_score_version_id, created = await create_candidate_profile_version(
        db, profile_id=profile.id, config=restored_profile, score_config=restored_score,
        change_set_id=uuid.uuid5(uuid.NAMESPACE_URL, f"rollback:{plan.id}"),
        mutation_reason=f"systemic_ai_profile_optimization_rollback:{plan.id}",
        rollback_to_version_id=executed_candidate_id,
    )
    db.add(ProfileAuditLog(
        user_id=user_id, profile_id=profile.id, changed_by=user_id,
        change_source="shadow_trade_profile_optimization_rollback",
        change_description=f"Rollback action plan {plan.id}",
        previous_config=payload["candidate_profile_config"], new_config=restored_profile,
        previous_profile_version=current_version, new_profile_version=current_version,
    ))
    plan.status = "ROLLED_BACK"
    result = {
        "status": "ROLLED_BACK",
        "profile_id": str(profile.id),
        "profile_name": profile.name,
        "profile_version": current_version.isoformat() if current_version else None,
        "profile_identity_preserved": True,
        "profile_version_preserved": profile.profile_version == current_version,
        "rollback_candidate_profile_version_id": str(rollback_version_id),
        "rollback_candidate_score_engine_version_id": str(rollback_score_version_id),
        "candidate_created": created,
    }
    plan.execution_result = {**(plan.execution_result or {}), "rollback": result}
    db.add(CopilotAuditLog(
        user_id=user_id, event_type="PROFILE_OPTIMIZATION_ROLLED_BACK",
        actor_user_id=user_id, action_plan_id=plan.id, payload=result,
    ))
    await db.commit()
    await config_service.invalidate_cache("score", user_id)
    await db.refresh(plan)
    return optimization_to_dict(plan)

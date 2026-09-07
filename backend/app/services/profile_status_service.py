"""Governed status-only activation and deactivation for strategy profiles."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.pipeline_watchlist import PipelineWatchlist
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from .profile_config_validation import validate_profile_config
from .profile_execution_contract import load_profile_execution_snapshots
from .profile_indicator_contract import validate_profile_execution_structure
from .profile_runtime_config import canonical_profile_config_hash


ROLE_LEVEL = {
    "universe_filter": "POOL",
    "primary_filter": "L1",
    "score_engine": "L2",
    "acquisition_queue": "L3",
}
UPSTREAM_LEVELS = frozenset({"POOL", "L1", "L2"})
NON_EXECUTION_ROLES = frozenset({"universe_filter", "primary_filter", "score_engine"})


class ProfileStatusConflict(ValueError):
    def __init__(self, code: str, *, context: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.context = dict(context or {})

    @property
    def detail(self) -> dict[str, Any]:
        return {"code": self.code, **self.context}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def parse_expected_updated_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ProfileStatusConflict("EXPECTED_UPDATED_AT_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProfileStatusConflict("EXPECTED_UPDATED_AT_INVALID") from exc
    if parsed.tzinfo is None:
        raise ProfileStatusConflict("EXPECTED_UPDATED_AT_TIMEZONE_REQUIRED")
    return _as_utc(parsed)


def validate_status_payload(payload: Mapping[str, Any]) -> tuple[bool, str, datetime]:
    if not isinstance(payload.get("is_active"), bool):
        raise ProfileStatusConflict("PROFILE_STATUS_BOOLEAN_REQUIRED")
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise ProfileStatusConflict("PROFILE_STATUS_REASON_REQUIRED")
    return bool(payload["is_active"]), reason, parse_expected_updated_at(payload.get("expected_updated_at"))


async def _watchlist_impact(
    db: AsyncSession, *, profile_id: UUID, user_id: UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(PipelineWatchlist)
            .where(
                PipelineWatchlist.profile_id == profile_id,
                PipelineWatchlist.user_id == user_id,
            )
            .order_by(PipelineWatchlist.level, PipelineWatchlist.name, PipelineWatchlist.id)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "name": row.name,
            "level": str(row.level or "").upper(),
            "auto_refresh": bool(row.auto_refresh),
        }
        for row in rows
    ]


async def _validate_reactivation(
    db: AsyncSession,
    *,
    profile: Profile,
    user_id: UUID,
    watchlists: list[dict[str, Any]],
) -> dict[str, Any]:
    role = str(profile.profile_role or "").strip().lower()
    require_feature_identity = role not in NON_EXECUTION_ROLES
    try:
        validated = validate_profile_config(
            deepcopy(profile.config or {}),
            require_feature_identity=require_feature_identity,
        )
    except ValueError as exc:
        raise ProfileStatusConflict(
            "PROFILE_REACTIVATION_INVALID_CONFIG", context={"error": str(exc)}
        ) from exc

    structure_errors = validate_profile_execution_structure(
        validated, path="config", require_sections=True
    )
    if structure_errors:
        raise ProfileStatusConflict(
            "PROFILE_REACTIVATION_INVALID_CONFIG",
            context={"issues": structure_errors},
        )

    expected_level = ROLE_LEVEL.get(role)
    actual_levels = sorted({item["level"] for item in watchlists if item["level"]})
    if actual_levels and (expected_level is None or actual_levels != [expected_level]):
        raise ProfileStatusConflict(
            "PROFILE_REACTIVATION_WATCHLIST_LAYER_MISMATCH",
            context={"expected_level": expected_level, "actual_levels": actual_levels},
        )

    snapshots = await load_profile_execution_snapshots(
        db, [profile.id], user_id=user_id
    )
    snapshot = snapshots.get(profile.id)
    contract = snapshot.get("contract") if snapshot else None
    if not contract or contract.get("status") != "MATCH":
        raise ProfileStatusConflict(
            "PROFILE_REACTIVATION_CONTRACT_MISMATCH",
            context={"reason_codes": (contract or {}).get("reason_codes", ["PROFILE_VERSION_MISSING"])},
        )
    return contract


async def preview_profile_status(
    db: AsyncSession,
    *,
    profile_id: UUID,
    user_id: UUID,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    requested_active, _reason, expected_updated_at = validate_status_payload(payload)
    profile = (
        await db.execute(
            select(Profile).where(Profile.id == profile_id, Profile.user_id == user_id)
        )
    ).scalars().first()
    if profile is None:
        raise ProfileStatusConflict("PROFILE_NOT_FOUND")
    idempotent = bool(profile.is_active) == requested_active
    if (
        not idempotent
        and (profile.updated_at is None or _as_utc(profile.updated_at) != expected_updated_at)
    ):
        raise ProfileStatusConflict(
            "PROFILE_STATUS_STALE",
            context={"current_updated_at": profile.updated_at.isoformat() if profile.updated_at else None},
        )

    watchlists = await _watchlist_impact(db, profile_id=profile.id, user_id=user_id)
    blockers = []
    if not requested_active and not idempotent:
        blockers = [item for item in watchlists if item["level"] in UPSTREAM_LEVELS]

    contract = None
    if requested_active and not idempotent:
        contract = await _validate_reactivation(
            db, profile=profile, user_id=user_id, watchlists=watchlists
        )

    return {
        "status": "BLOCKED" if blockers else "READY",
        "allowed": not blockers,
        "idempotent": idempotent,
        "profile": {
            "id": str(profile.id),
            "name": profile.name,
            "profile_role": profile.profile_role,
            "profile_type": profile.profile_type,
            "is_active": bool(profile.is_active),
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        },
        "requested_is_active": requested_active,
        "watchlists": watchlists,
        "blockers": blockers,
        "open_trades_preserved": True,
        "execution_contract": ({
            "status": contract.get("status"),
            "profile_version_id": contract.get("profile_version_id"),
            "profile_projection_hash": contract.get("profile_projection_hash"),
        } if contract else None),
    }


async def apply_profile_status(
    db: AsyncSession,
    *,
    profile_id: UUID,
    user_id: UUID,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    requested_active, reason, expected_updated_at = validate_status_payload(payload)
    profile = (
        await db.execute(
            select(Profile)
            .where(Profile.id == profile_id, Profile.user_id == user_id)
            .with_for_update()
        )
    ).scalars().first()
    if profile is None:
        raise ProfileStatusConflict("PROFILE_NOT_FOUND")
    previous_active = bool(profile.is_active)
    if (
        previous_active != requested_active
        and (profile.updated_at is None or _as_utc(profile.updated_at) != expected_updated_at)
    ):
        raise ProfileStatusConflict(
            "PROFILE_STATUS_STALE",
            context={"current_updated_at": profile.updated_at.isoformat() if profile.updated_at else None},
        )

    watchlists = await _watchlist_impact(db, profile_id=profile.id, user_id=user_id)
    if previous_active == requested_active:
        return {
            "changed": False,
            "idempotent": True,
            "audit_id": None,
            "profile": profile,
            "watchlists": watchlists,
            "cleared_live_watchlists": [],
            "open_trades_preserved": True,
        }

    if not requested_active:
        blockers = [item for item in watchlists if item["level"] in UPSTREAM_LEVELS]
        if blockers:
            raise ProfileStatusConflict(
                "PROFILE_UPSTREAM_DEACTIVATION_BLOCKED",
                context={"watchlists": blockers},
            )
    else:
        await _validate_reactivation(
            db, profile=profile, user_id=user_id, watchlists=watchlists
        )

    config_hash_before = canonical_profile_config_hash(profile.config or {})
    profile_version_before = profile.profile_version
    profile.is_active = requested_active
    profile.updated_at = datetime.now(timezone.utc)

    cleared_live_watchlists: list[dict[str, Any]] = []
    if not requested_active:
        for watchlist in watchlists:
            if watchlist["level"] != "L3":
                continue
            assets = (
                await db.execute(
                    text("DELETE FROM pipeline_watchlist_assets WHERE watchlist_id = CAST(:id AS UUID) RETURNING id"),
                    {"id": watchlist["id"]},
                )
            ).fetchall()
            rejections = (
                await db.execute(
                    text("DELETE FROM pipeline_watchlist_rejections WHERE watchlist_id = CAST(:id AS UUID) RETURNING id"),
                    {"id": watchlist["id"]},
                )
            ).fetchall()
            cleared_live_watchlists.append(
                {**watchlist, "assets_removed": len(assets), "rejections_removed": len(rejections)}
            )

    audit = ProfileAuditLog(
        user_id=user_id,
        profile_id=profile.id,
        changed_by=user_id,
        change_source="profile_ui_status",
        change_description="profile operational status changed via dedicated endpoint",
        previous_config=None,
        new_config=None,
        previous_profile_version=profile_version_before,
        new_profile_version=profile_version_before,
        previous_is_active=previous_active,
        new_is_active=requested_active,
        status_reason=reason,
    )
    db.add(audit)
    await db.flush()

    if canonical_profile_config_hash(profile.config or {}) != config_hash_before:
        raise RuntimeError("PROFILE_STATUS_CONFIG_MUTATION_DETECTED")
    if profile.profile_version != profile_version_before:
        raise RuntimeError("PROFILE_STATUS_VERSION_MUTATION_DETECTED")

    await db.commit()
    await db.refresh(profile)
    return {
        "changed": True,
        "idempotent": False,
        "audit_id": str(audit.id),
        "profile": profile,
        "watchlists": watchlists,
        "cleared_live_watchlists": cleared_live_watchlists,
        "open_trades_preserved": True,
    }

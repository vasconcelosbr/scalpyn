"""Governed, atomic update of the existing Spot MTF L1/L2 profiles."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import logging
import re
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.pipeline_watchlist import PipelineWatchlist
from ..models.profile import Profile
from .config_service import config_service
from .profile_config_validation import validate_profile_config
from .profile_execution_contract import (
    EXECUTION_SECTIONS,
    activate_profile_config,
    load_profile_execution_snapshots,
    lock_profiles_for_update,
    restore_profile_config_version,
)
from .profile_indicator_contract import validate_profile_execution_structure
from .profile_runtime_config import canonical_hash, canonical_profile_config_hash
from .strategy_settings_service import strategy_settings_service


IMPORT_MODE = "UPDATE_EXISTING_MTF_AND_ACTIVATE_SHADOW"
WAIVER_IMPORT_MODE = "UPDATE_EXISTING_MTF_AND_ACTIVATE_SHADOW_WITH_WAIVER"
EXPECTED_NAMES = {"L1": "L1", "L2": "L2"}
EXPECTED_ROLES = {"L1": "primary_filter", "L2": "score_engine"}
EXPECTED_ORDERS = {"L1": "1", "L2": "2"}
EXPECTED_TIMEFRAMES = {"L1": "1h", "L2": "15m"}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
logger = logging.getLogger(__name__)


class MTFActivationConflict(ValueError):
    """A target changed since the governed JSON was generated."""


def _uuid(value: Any, path: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} must be a UUID") from exc


def _hash(value: Any, path: str) -> str:
    result = str(value or "").lower()
    if not _HASH_RE.fullmatch(result):
        raise ValueError(f"{path} must be a lowercase SHA-256 hash")
    return result


def _profile_document_to_config(
    document: Mapping[str, Any], layer: str, *, waiver: bool = False,
) -> dict[str, Any]:
    raw = document.get("config") if isinstance(document.get("config"), Mapping) else document
    config = {
        "default_timeframe": raw.get("default_timeframe"),
        "filters": deepcopy(raw.get("filters") or {"logic": "AND", "conditions": []}),
        "signals": deepcopy(raw.get("signals") or {"logic": "AND", "conditions": []}),
        "block_rules": deepcopy(raw.get("block_rules") or {"blocks": []}),
        "entry_triggers": deepcopy(
            raw.get("entry_triggers") or {"logic": "AND", "conditions": []}
        ),
        "scoring": deepcopy(raw.get("scoring") or {}),
        "mtf_semantics": deepcopy(raw.get("mtf_semantics") or {}),
        "source_identity": deepcopy(raw.get("source_identity") or {}),
        "calibration": deepcopy(raw.get("calibration") or {}),
        "mtf_layer": {
            "layer": layer,
            "activation_mode": "SHADOW",
            "operational_effect": False,
        },
    }
    if config["default_timeframe"] != EXPECTED_TIMEFRAMES[layer]:
        raise ValueError(f"{layer}_TIMEFRAME_MUST_BE_{EXPECTED_TIMEFRAMES[layer]}")
    source = config["source_identity"]
    if (
        source.get("candle_policy") != "CLOSED_ONLY"
        or not source.get("allowed_source_providers")
        or not source.get("provider_policy_id")
        or not source.get("allowed_capture_contract_versions")
        or source.get("validity_margin_seconds") is None
        or source.get("scheduler_group") != "structural"
        or not source.get("allowed_producer_versions")
        or not source.get("indicator_config_profile_id")
        or not source.get("indicator_config_hash")
    ):
        raise ValueError(f"{layer}_SOURCE_IDENTITY_INCOMPLETE")
    calibration = config["calibration"]
    if waiver:
        if (
            calibration.get("status") != "DRAFT_INSUFFICIENT_DATA"
            or calibration.get("method") != "WALK_FORWARD"
            or calibration.get("activation_authority") != "HUMAN_WAIVER"
            or calibration.get("reason") != "MIN_SAMPLES_NOT_MET"
            or calibration.get("thresholds_emitted") is not False
            or calibration.get("min_samples") is None
            or not calibration.get("run_id")
            or not calibration.get("policy_hash")
            or not calibration.get("dataset_hash")
        ):
            raise ValueError(f"{layer}_WAIVER_CALIBRATION_DISCLOSURE_INVALID")
    elif (
        calibration.get("status") != "PASSED"
        or calibration.get("method") != "WALK_FORWARD"
        or calibration.get("baseline_outperformed") is not True
        or calibration.get("worst_fold_drawdown_not_worse") is not True
        or calibration.get("min_samples") is None
    ):
        raise ValueError(f"{layer}_CALIBRATION_GATE_FAILED")
    structural_errors = validate_profile_execution_structure(
        config, path=f"profiles.{layer}.config", require_sections=True
    )
    if structural_errors:
        raise ValueError(
            "PROFILE_CONDITION_INVALID:"
            + json.dumps(structural_errors, sort_keys=True)
        )
    return validate_profile_config(config, require_feature_identity=True)


def parse_activation_document(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate static identity and economic material before any DB access."""
    import_mode = payload.get("import_mode")
    if import_mode not in {IMPORT_MODE, WAIVER_IMPORT_MODE}:
        raise ValueError(
            f"import_mode must be {IMPORT_MODE} or {WAIVER_IMPORT_MODE}"
        )
    waiver = import_mode == WAIVER_IMPORT_MODE
    if payload.get("update_indicators_only"):
        raise ValueError("MTF_ACTIVATION_MODE_INCOMPATIBLE_WITH_UPDATE_INDICATORS_ONLY")
    if payload.get("allow_create") is not False:
        raise ValueError("allow_create must be false")
    if payload.get("activation_mode") != "SHADOW":
        raise ValueError("activation_mode must be SHADOW")
    if payload.get("operational_effect") is not False:
        raise ValueError("operational_effect must be false")

    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, Mapping) or set(raw_profiles) != {"L1", "L2"}:
        raise ValueError("profiles must contain exactly L1 and L2")
    parsed_profiles: dict[str, Any] = {}
    profile_ids: set[UUID] = set()
    for layer in ("L1", "L2"):
        item = raw_profiles[layer]
        if not isinstance(item, Mapping):
            raise ValueError(f"profiles.{layer} must be an object")
        profile_id = _uuid(item.get("profile_id"), f"profiles.{layer}.profile_id")
        if profile_id in profile_ids:
            raise ValueError("L1 and L2 profile IDs must be distinct")
        profile_ids.add(profile_id)
        if item.get("name") != EXPECTED_NAMES[layer]:
            raise ValueError(f"profiles.{layer}.name must remain {EXPECTED_NAMES[layer]}")
        if item.get("profile_kind") != "MTF_LAYER":
            raise ValueError(f"profiles.{layer}.profile_kind must be MTF_LAYER")
        if item.get("layer") != layer:
            raise ValueError(f"profiles.{layer}.layer mismatch")
        if item.get("funnel_role") != EXPECTED_ROLES[layer]:
            raise ValueError(f"profiles.{layer}.funnel_role mismatch")
        parsed_profiles[layer] = {
            "profile_id": profile_id,
            "expected_profile_version_id": _uuid(
                item.get("expected_profile_version_id"),
                f"profiles.{layer}.expected_profile_version_id",
            ),
            "expected_profile_config_hash": _hash(
                item.get("expected_profile_config_hash"),
                f"profiles.{layer}.expected_profile_config_hash",
            ),
            "config": _profile_document_to_config(item, layer, waiver=waiver),
        }

    raw_watchlists = payload.get("watchlists")
    if not isinstance(raw_watchlists, Mapping) or set(raw_watchlists) != {"L1", "L2"}:
        raise ValueError("watchlists must contain exactly L1 and L2")
    watchlists = {layer: _uuid(raw_watchlists[layer], f"watchlists.{layer}") for layer in ("L1", "L2")}
    if watchlists["L1"] == watchlists["L2"]:
        raise ValueError("L1 and L2 watchlist IDs must be distinct")
    raw_expected_bindings = payload.get("expected_watchlist_bindings")
    if (
        not isinstance(raw_expected_bindings, Mapping)
        or set(raw_expected_bindings) != {"L1", "L2"}
    ):
        raise ValueError("expected_watchlist_bindings must contain L1 and L2")
    expected_watchlist_bindings = {
        layer: _uuid(
            raw_expected_bindings[layer],
            f"expected_watchlist_bindings.{layer}",
        )
        for layer in ("L1", "L2")
    }

    calibration = payload.get("calibration")
    if not isinstance(calibration, Mapping):
        raise ValueError("calibration is required")
    parsed_calibration = {
        "run_id": _uuid(calibration.get("run_id"), "calibration.run_id"),
        "policy_hash": _hash(calibration.get("policy_hash"), "calibration.policy_hash"),
        "dataset_hash": _hash(calibration.get("dataset_hash"), "calibration.dataset_hash"),
    }
    if waiver:
        raw_gate = payload.get("statistical_gate")
        if not isinstance(raw_gate, Mapping):
            raise ValueError("statistical_gate is required for waiver activation")
        required_gate = {
            "status": "WAIVED_FOR_SHADOW",
            "authorization_scope": "OBSERVATIONAL_ONLY",
            "calibration_not_passed_acknowledged": True,
            "thresholds_unvalidated_acknowledged": True,
            "operational_effect_false_acknowledged": True,
        }
        if any(raw_gate.get(key) != value for key, value in required_gate.items()):
            raise ValueError("MTF_WAIVER_ACKNOWLEDGEMENTS_REQUIRED")
        statistical_gate = deepcopy(dict(raw_gate))
    else:
        thresholds_hashes = calibration.get("thresholds_hashes")
        if (
            not isinstance(thresholds_hashes, Mapping)
            or set(thresholds_hashes) != {"L1", "L2"}
        ):
            raise ValueError("calibration.thresholds_hashes must contain L1 and L2")
        parsed_calibration["thresholds_hashes"] = {
            layer: _hash(
                thresholds_hashes[layer],
                f"calibration.thresholds_hashes.{layer}",
            )
            for layer in ("L1", "L2")
        }
        statistical_gate = None
    l3_source_identity = payload.get("l3_source_identity")
    if not isinstance(l3_source_identity, Mapping):
        raise ValueError("l3_source_identity is required")
    return {
        "profiles": parsed_profiles,
        "watchlists": watchlists,
        "expected_watchlist_bindings": expected_watchlist_bindings,
        "calibration": parsed_calibration,
        "import_mode": import_mode,
        "statistical_gate": statistical_gate,
        "l3_source_identity": deepcopy(dict(l3_source_identity)),
    }


def _profile_snapshot(profile: Profile, active: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(profile.id),
        "name": profile.name,
        "profile_role": profile.profile_role,
        "pipeline_order": profile.pipeline_order,
        "profile_type": profile.profile_type,
        "is_active": bool(profile.is_active),
        "is_shadow_only": bool(profile.is_shadow_only),
        "live_trading_enabled": bool(profile.live_trading_enabled),
        "config": deepcopy(profile.config or {}),
        "active_profile_version_id": active["contract"]["profile_version_id"],
        "profile_config_hash": active["contract"]["profile_projection_hash"],
    }


async def _load_prerequisites(
    db: AsyncSession, *, user_id: UUID, parsed: Mapping[str, Any], lock: bool
) -> dict[str, Any]:
    profile_ids = [parsed["profiles"][layer]["profile_id"] for layer in ("L1", "L2")]
    if lock:
        profiles = await lock_profiles_for_update(
            db, user_id=user_id, profile_ids=profile_ids
        )
    else:
        profile_rows = (await db.execute(select(Profile).where(
            Profile.user_id == user_id, Profile.id.in_(profile_ids)
        ))).scalars().all()
        profiles = {row.id: row for row in profile_rows}
    if set(profiles) != set(profile_ids):
        raise MTFActivationConflict("MTF_PROFILE_NOT_FOUND")
    snapshots = await load_profile_execution_snapshots(db, profile_ids, user_id=user_id)
    for layer in ("L1", "L2"):
        item = parsed["profiles"][layer]
        profile = profiles[item["profile_id"]]
        snapshot = snapshots.get(profile.id)
        if profile.name != EXPECTED_NAMES[layer]:
            raise MTFActivationConflict(f"{layer}_PROFILE_NAME_CHANGED")
        if not snapshot:
            raise MTFActivationConflict(f"{layer}_PROFILE_VERSION_MISSING")
        if str(snapshot["contract"]["profile_version_id"]) != str(item["expected_profile_version_id"]):
            raise MTFActivationConflict(f"{layer}_PROFILE_VERSION_CONFLICT")
        if snapshot["contract"]["profile_projection_hash"] != item["expected_profile_config_hash"]:
            raise MTFActivationConflict(f"{layer}_PROFILE_CONFIG_HASH_CONFLICT")

    run_lock = "FOR UPDATE" if lock else ""
    run = (await db.execute(text(f"""
        SELECT id, status, approved_policy_hash, dataset_hash, selected_profiles,
               failure_reason
          FROM mtf_calibration_runs
         WHERE id = CAST(:run_id AS UUID)
           AND user_id = CAST(:user_id AS UUID)
         {run_lock}
    """), {
        "run_id": str(parsed["calibration"]["run_id"]),
        "user_id": str(user_id),
    })).mappings().one_or_none()
    waiver = parsed["import_mode"] == WAIVER_IMPORT_MODE
    if run is None:
        raise MTFActivationConflict("MTF_CALIBRATION_RUN_NOT_FOUND")
    if waiver:
        if (
            run["status"] != "DRAFT_INSUFFICIENT_DATA"
            or run["failure_reason"] != "MIN_SAMPLES_NOT_MET"
        ):
            raise MTFActivationConflict("MTF_WAIVER_RUN_NOT_INSUFFICIENT_DATA")
    elif run["status"] != "PASSED":
        raise MTFActivationConflict("MTF_CALIBRATION_RUN_NOT_PASSED")
    if run["approved_policy_hash"] != parsed["calibration"]["policy_hash"]:
        raise MTFActivationConflict("MTF_CALIBRATION_POLICY_HASH_CONFLICT")
    if run["dataset_hash"] != parsed["calibration"]["dataset_hash"]:
        raise MTFActivationConflict("MTF_CALIBRATION_DATASET_HASH_CONFLICT")
    selected = dict(run["selected_profiles"] or {})
    if waiver:
        for layer in ("L1", "L2"):
            current_config = dict(
                profiles[parsed["profiles"][layer]["profile_id"]].config or {}
            )
            proposed_config = parsed["profiles"][layer]["config"]
            for section in (*EXECUTION_SECTIONS, "scoring"):
                if canonical_hash(current_config.get(section) or {}) != canonical_hash(
                    proposed_config.get(section) or {}
                ):
                    raise MTFActivationConflict(
                        f"{layer}_WAIVER_ECONOMIC_RULE_CHANGE_FORBIDDEN"
                    )
            disclosure = proposed_config.get("calibration") or {}
            if (
                str(disclosure.get("run_id")) != str(run["id"])
                or disclosure.get("policy_hash") != run["approved_policy_hash"]
                or disclosure.get("dataset_hash") != run["dataset_hash"]
            ):
                raise MTFActivationConflict(
                    f"{layer}_WAIVER_CALIBRATION_DISCLOSURE_CONFLICT"
                )
    else:
        if set(selected) != {"L1", "L2"}:
            raise MTFActivationConflict("MTF_CALIBRATION_PROFILE_SET_INVALID")
        for layer in ("L1", "L2"):
            selected_item = dict(selected[layer] or {})
            selected_payload = dict(selected_item.get("payload") or {})
            selected_config = _profile_document_to_config(selected_payload, layer)
            expected_thresholds_hash = parsed["calibration"]["thresholds_hashes"][layer]
            if selected_item.get("thresholds_hash") != expected_thresholds_hash:
                raise MTFActivationConflict(f"{layer}_THRESHOLDS_HASH_CONFLICT")
            if canonical_profile_config_hash(selected_config) != canonical_profile_config_hash(
                parsed["profiles"][layer]["config"]
            ):
                raise MTFActivationConflict(f"{layer}_PROFILE_NOT_EMITTED_BY_CALIBRATION")

    watchlist_query = select(PipelineWatchlist).where(
        PipelineWatchlist.user_id == user_id,
        PipelineWatchlist.id.in_(list(parsed["watchlists"].values())),
    )
    if lock:
        watchlist_query = watchlist_query.with_for_update()
    rows = (await db.execute(watchlist_query.order_by(PipelineWatchlist.id))).scalars().all()
    watchlists = {row.id: row for row in rows}
    if set(watchlists) != set(parsed["watchlists"].values()):
        raise MTFActivationConflict("MTF_WATCHLIST_NOT_FOUND")
    l1 = watchlists[parsed["watchlists"]["L1"]]
    l2 = watchlists[parsed["watchlists"]["L2"]]
    if l1.level.upper() != "L1" or l2.level.upper() != "L2":
        raise MTFActivationConflict("MTF_WATCHLIST_LAYER_MISMATCH")
    if l1.market_mode != "spot" or l2.market_mode != "spot":
        raise MTFActivationConflict("MTF_WATCHLIST_MARKET_MISMATCH")
    l1_source = None
    if l1.source_watchlist_id is not None:
        source_query = select(PipelineWatchlist).where(
            PipelineWatchlist.user_id == user_id,
            PipelineWatchlist.id == l1.source_watchlist_id,
        )
        if lock:
            source_query = source_query.with_for_update()
        l1_source = (await db.execute(source_query)).scalar_one_or_none()
    _validate_watchlist_chain(l1, l2, l1_source=l1_source)
    if l1.profile_id != parsed["profiles"]["L1"]["profile_id"]:
        raise MTFActivationConflict("MTF_L1_PROFILE_ASSOCIATION_CHANGED")
    for layer, row in (("L1", l1), ("L2", l2)):
        if row.profile_id != parsed["expected_watchlist_bindings"][layer]:
            raise MTFActivationConflict(f"MTF_{layer}_PROFILE_ASSOCIATION_CHANGED")

    spot_query = select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.config_type == "spot_engine",
        ConfigProfile.is_active.is_(True),
    )
    if lock:
        spot_query = spot_query.with_for_update()
    spot_config = (await db.execute(spot_query)).scalars().all()
    if len(spot_config) != 1:
        raise MTFActivationConflict("SPOT_ENGINE_CONFIG_CARDINALITY_INVALID")
    return {
        "profiles": profiles,
        "profile_snapshots": snapshots,
        "watchlists": {"L1": l1, "L2": l2},
        "run": run,
        "spot_config": spot_config[0],
    }


def _watchlist_snapshot(row: PipelineWatchlist) -> dict[str, Any]:
    return {
        "id": str(row.id), "name": row.name, "level": row.level,
        "profile_id": str(row.profile_id) if row.profile_id else None,
        "source_pool_id": str(row.source_pool_id) if row.source_pool_id else None,
        "source_watchlist_id": str(row.source_watchlist_id) if row.source_watchlist_id else None,
    }


def _authorized_statistical_gate(
    *, parsed: Mapping[str, Any], run: Mapping[str, Any], user_id: UUID,
) -> dict[str, Any] | None:
    if parsed["import_mode"] != WAIVER_IMPORT_MODE:
        return None
    request = dict(parsed["statistical_gate"] or {})
    material = {
        "status": "WAIVED_FOR_SHADOW",
        "run_status": str(run["status"]),
        "failure_reason": str(run["failure_reason"]),
        "calibration_run_id": str(run["id"]),
        "policy_hash": str(run["approved_policy_hash"]),
        "dataset_hash": str(run["dataset_hash"]),
        "authorization_scope": "OBSERVATIONAL_ONLY",
        "calibration_not_passed_acknowledged": request[
            "calibration_not_passed_acknowledged"
        ],
        "thresholds_unvalidated_acknowledged": request[
            "thresholds_unvalidated_acknowledged"
        ],
        "operational_effect_false_acknowledged": request[
            "operational_effect_false_acknowledged"
        ],
        "authorized_by": str(user_id),
        "authorized_at": datetime.now(timezone.utc).isoformat(),
    }
    return {**material, "authorization_hash": canonical_hash(material)}


def _validate_watchlist_chain(
    l1: PipelineWatchlist,
    l2: PipelineWatchlist,
    *,
    l1_source: PipelineWatchlist | None,
) -> None:
    """Accept both supported POOL origins while preserving POOL -> L1 -> L2."""

    if (l1.source_pool_id is None) == (l1.source_watchlist_id is None):
        raise MTFActivationConflict("MTF_L1_SOURCE_CHAIN_INVALID")
    if l1.source_watchlist_id is not None and (
        l1_source is None
        or str(l1_source.level or "").upper() != "POOL"
        or l1_source.market_mode != "spot"
    ):
        raise MTFActivationConflict("MTF_L1_SOURCE_WATCHLIST_INVALID")
    if l2.source_watchlist_id != l1.id or l2.source_pool_id is not None:
        raise MTFActivationConflict("MTF_L2_SOURCE_CHAIN_INVALID")


async def activate_existing_mtf_profiles(
    db: AsyncSession, *, user_id: UUID, payload: Mapping[str, Any], apply: bool
) -> dict[str, Any]:
    parsed = parse_activation_document(payload)
    state = await _load_prerequisites(db, user_id=user_id, parsed=parsed, lock=apply)
    statistical_gate = _authorized_statistical_gate(
        parsed=parsed, run=state["run"], user_id=user_id
    )
    before = {
        "profiles": {
            layer: _profile_snapshot(
                state["profiles"][parsed["profiles"][layer]["profile_id"]],
                state["profile_snapshots"][parsed["profiles"][layer]["profile_id"]],
            )
            for layer in ("L1", "L2")
        },
        "watchlists": {
            layer: _watchlist_snapshot(state["watchlists"][layer])
            for layer in ("L1", "L2")
        },
        "multilayer_contract": deepcopy(
            ((state["spot_config"].config_json or {}).get("scanner") or {}).get(
                "multilayer_contract"
            ) or {}
        ),
    }
    preview = {
        "profiles": {
            layer: {
                "profile_id": str(parsed["profiles"][layer]["profile_id"]),
                "name": EXPECTED_NAMES[layer],
                "profile_role": EXPECTED_ROLES[layer],
                "profile_type": "MTF_LAYER",
                "is_active": True,
                "is_shadow_only": True,
                "live_trading_enabled": False,
                "default_timeframe": EXPECTED_TIMEFRAMES[layer],
                "next_profile_config_hash": canonical_profile_config_hash(
                    parsed["profiles"][layer]["config"]
                ),
                "profile_version_id": "GENERATED_ON_APPLY",
            }
            for layer in ("L1", "L2")
        },
        "watchlists": {
            "L1": {"id": str(state["watchlists"]["L1"].id), "profile_id": str(parsed["profiles"]["L1"]["profile_id"])},
            "L2": {"id": str(state["watchlists"]["L2"].id), "profile_id": str(parsed["profiles"]["L2"]["profile_id"])},
        },
        "contract": {
            "enabled": True, "activation_mode": "SHADOW",
            "operational_effect": False,
            "decision_feature_contract_version": (
                "multilayer_decision_context_v4"
                if statistical_gate else "multilayer_decision_context_v3"
            ),
            "calibration_run_id": str(parsed["calibration"]["run_id"]),
            "statistical_gate": statistical_gate or {},
        },
    }
    if not apply:
        return {
            "status": "READY_TO_APPLY", "applied": False,
            "before": before, "after_preview": preview,
            "document_hash": canonical_hash(payload),
        }

    activations: dict[str, Any] = {}
    for layer in ("L1", "L2"):
        item = parsed["profiles"][layer]
        profile = state["profiles"][item["profile_id"]]
        profile.profile_role = EXPECTED_ROLES[layer]
        profile.pipeline_order = EXPECTED_ORDERS[layer]
        profile.profile_type = "MTF_LAYER"
        profile.is_active = True
        profile.is_shadow_only = True
        profile.live_trading_enabled = False
        activations[layer] = await activate_profile_config(
            db, profile=profile, config=item["config"], changed_by=user_id,
            change_source=(
                "mtf_governed_waiver_activation"
                if statistical_gate else "mtf_governed_activation"
            ),
            change_description=f"[{parsed['import_mode']}] update existing {layer}",
            require_feature_identity=True,
            version_idempotency_namespace=(
                f"mtf-activation:{parsed['import_mode']}:{parsed['calibration']['run_id']}"
            ),
        )

    state["watchlists"]["L1"].profile_id = parsed["profiles"]["L1"]["profile_id"]
    state["watchlists"]["L2"].profile_id = parsed["profiles"]["L2"]["profile_id"]
    await db.flush()
    contract_result = await strategy_settings_service.activate_multilayer_shadow(
        db, user_id,
        layer_profile_ids={layer: parsed["profiles"][layer]["profile_id"] for layer in ("L1", "L2")},
        calibration_run_id=parsed["calibration"]["run_id"],
        l3_source_identity=parsed["l3_source_identity"],
        statistical_gate=statistical_gate,
        apply=True, commit=False,
    )
    after = {
        "profiles": activations,
        "watchlists": {
            layer: _watchlist_snapshot(state["watchlists"][layer])
            for layer in ("L1", "L2")
        },
        "multilayer_contract": contract_result["multilayer_contract"],
    }
    audit_id = (await db.execute(text("""
        INSERT INTO mtf_profile_activation_audits (
          user_id, calibration_run_id, applied_by, import_mode, document_hash,
          before_snapshot, after_snapshot, status
        ) VALUES (
          CAST(:user_id AS UUID), CAST(:run_id AS UUID), CAST(:applied_by AS UUID),
          :import_mode, :document_hash, CAST(:before AS JSONB), CAST(:after AS JSONB),
          'APPLIED'
        ) RETURNING id
    """), {
        "user_id": str(user_id), "run_id": str(parsed["calibration"]["run_id"]),
        "applied_by": str(user_id), "import_mode": parsed["import_mode"],
        "document_hash": canonical_hash(payload),
        "before": json.dumps(before, default=str), "after": json.dumps(after, default=str),
    })).scalar_one()
    await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
    await db.commit()
    cache_invalidation_confirmed = True
    try:
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
    except Exception:
        cache_invalidation_confirmed = False
        logger.exception(
            "MTF activation committed but post-commit cache invalidation failed; "
            "stale workers remain fail-closed on the prior disabled contract"
        )
    return {
        "status": (
            "APPLIED" if cache_invalidation_confirmed
            else "APPLIED_CACHE_INVALIDATION_FAILED"
        ),
        "applied": True,
        "cache_invalidation_confirmed": cache_invalidation_confirmed,
        "audit_id": str(audit_id),
        "before": before, "after": after,
        "document_hash": canonical_hash(payload),
    }


async def rollback_existing_mtf_activation(
    db: AsyncSession, *, user_id: UUID, audit_id: UUID
) -> dict[str, Any]:
    """Restore the exact pre-activation bindings without deleting history."""

    audit = (await db.execute(text("""
        SELECT id, before_snapshot, after_snapshot, status
          FROM mtf_profile_activation_audits
         WHERE id = CAST(:audit_id AS UUID)
           AND user_id = CAST(:user_id AS UUID)
         FOR UPDATE
    """), {"audit_id": str(audit_id), "user_id": str(user_id)})).mappings().one_or_none()
    if audit is None:
        raise MTFActivationConflict("MTF_ACTIVATION_AUDIT_NOT_FOUND")
    if audit["status"] != "APPLIED":
        raise MTFActivationConflict("MTF_ACTIVATION_ALREADY_ROLLED_BACK")

    before = deepcopy(dict(audit["before_snapshot"] or {}))
    after = deepcopy(dict(audit["after_snapshot"] or {}))
    before_profiles = dict(before.get("profiles") or {})
    after_profiles = dict(after.get("profiles") or {})
    if set(before_profiles) != {"L1", "L2"} or set(after_profiles) != {"L1", "L2"}:
        raise MTFActivationConflict("MTF_ROLLBACK_SNAPSHOT_INVALID")

    profile_ids = [_uuid(before_profiles[layer].get("id"), f"before.{layer}.id") for layer in ("L1", "L2")]
    profiles = await lock_profiles_for_update(db, user_id=user_id, profile_ids=profile_ids)
    if set(profiles) != set(profile_ids):
        raise MTFActivationConflict("MTF_ROLLBACK_PROFILE_NOT_FOUND")
    current_snapshots = await load_profile_execution_snapshots(
        db, profile_ids, user_id=user_id
    )
    for layer, profile_id in zip(("L1", "L2"), profile_ids):
        current = current_snapshots.get(profile_id)
        expected = after_profiles[layer]
        if not current:
            raise MTFActivationConflict(f"{layer}_ROLLBACK_CURRENT_VERSION_MISSING")
        if (
            str(current["contract"]["profile_version_id"])
            != str(expected.get("profile_version_id"))
            or current["contract"]["profile_projection_hash"]
            != expected.get("profile_config_hash")
        ):
            raise MTFActivationConflict(f"{layer}_ROLLBACK_STATE_CHANGED")

    before_watchlists = dict(before.get("watchlists") or {})
    after_watchlists = dict(after.get("watchlists") or {})
    watchlist_ids = [_uuid(before_watchlists[layer].get("id"), f"before.watchlists.{layer}.id") for layer in ("L1", "L2")]
    rows = (await db.execute(
        select(PipelineWatchlist)
        .where(PipelineWatchlist.user_id == user_id, PipelineWatchlist.id.in_(watchlist_ids))
        .order_by(PipelineWatchlist.id)
        .with_for_update()
    )).scalars().all()
    watchlists = {row.id: row for row in rows}
    if set(watchlists) != set(watchlist_ids):
        raise MTFActivationConflict("MTF_ROLLBACK_WATCHLIST_NOT_FOUND")
    for layer, watchlist_id in zip(("L1", "L2"), watchlist_ids):
        expected_profile_id = after_watchlists[layer].get("profile_id")
        if str(watchlists[watchlist_id].profile_id) != str(expected_profile_id):
            raise MTFActivationConflict(f"{layer}_ROLLBACK_WATCHLIST_CHANGED")

    spot_rows = (await db.execute(
        select(ConfigProfile).where(
            ConfigProfile.user_id == user_id,
            ConfigProfile.pool_id.is_(None),
            ConfigProfile.config_type == "spot_engine",
            ConfigProfile.is_active.is_(True),
        ).with_for_update()
    )).scalars().all()
    if len(spot_rows) != 1:
        raise MTFActivationConflict("SPOT_ENGINE_CONFIG_CARDINALITY_INVALID")
    spot_config = spot_rows[0]
    current_config = deepcopy(dict(spot_config.config_json or {}))
    current_contract = deepcopy(
        ((current_config.get("scanner") or {}).get("multilayer_contract")) or {}
    )
    if canonical_hash(current_contract) != canonical_hash(
        after.get("multilayer_contract") or {}
    ):
        raise MTFActivationConflict("MTF_ROLLBACK_CONTRACT_CHANGED")

    restored_versions: dict[str, str] = {}
    for layer, profile_id in zip(("L1", "L2"), profile_ids):
        profile = profiles[profile_id]
        prior = before_profiles[layer]
        prior_version_id = _uuid(
            prior.get("active_profile_version_id"),
            f"before.profiles.{layer}.active_profile_version_id",
        )
        expected_current_version_id = _uuid(
            after_profiles[layer].get("profile_version_id"),
            f"after.profiles.{layer}.profile_version_id",
        )
        try:
            await restore_profile_config_version(
                db,
                profile=profile,
                prior_config=prior.get("config") or {},
                prior_profile_version_id=prior_version_id,
                expected_current_profile_version_id=expected_current_version_id,
                expected_current_profile_config_hash=str(
                    after_profiles[layer].get("profile_config_hash") or ""
                ),
                restored_is_shadow_only=bool(prior.get("is_shadow_only")),
                changed_by=user_id,
                change_description=(
                    f"[{IMPORT_MODE}] restore {layer} from activation audit {audit_id}"
                ),
            )
        except Exception as exc:
            raise MTFActivationConflict(
                f"{layer}_ROLLBACK_PRIOR_VERSION_INVALID:{exc}"
            ) from exc
        profile.name = prior["name"]
        profile.profile_role = prior.get("profile_role")
        profile.pipeline_order = prior.get("pipeline_order")
        profile.profile_type = prior.get("profile_type") or "STANDARD"
        profile.is_active = bool(prior.get("is_active"))
        profile.is_shadow_only = bool(prior.get("is_shadow_only"))
        profile.live_trading_enabled = bool(prior.get("live_trading_enabled"))
        restored_versions[layer] = str(prior_version_id)

    for layer, watchlist_id in zip(("L1", "L2"), watchlist_ids):
        previous_profile_id = before_watchlists[layer].get("profile_id")
        watchlists[watchlist_id].profile_id = (
            UUID(str(previous_profile_id)) if previous_profile_id else None
        )

    restored_config = deepcopy(current_config)
    scanner = deepcopy(dict(restored_config.get("scanner") or {}))
    scanner["multilayer_contract"] = deepcopy(
        before.get("multilayer_contract") or {}
    )
    restored_config["scanner"] = scanner
    spot_config.config_json = restored_config
    db.add(ConfigAuditLog(
        config_id=spot_config.id,
        changed_by=user_id,
        previous_json=current_config,
        new_json=restored_config,
        change_description=(
            f"[MTF_SPOT_ROLLBACK] restore activation audit {audit_id}; preserve history"
        ),
    ))
    await db.execute(text("""
        UPDATE mtf_profile_activation_audits
           SET status = 'ROLLED_BACK', rolled_back_by = CAST(:user_id AS UUID),
               rolled_back_at = clock_timestamp()
         WHERE id = CAST(:audit_id AS UUID) AND status = 'APPLIED'
    """), {"user_id": str(user_id), "audit_id": str(audit_id)})
    await db.flush()
    await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
    await db.commit()
    cache_invalidation_confirmed = True
    try:
        await config_service.invalidate_cache("spot_engine", user_id, None, strict=True)
    except Exception:
        cache_invalidation_confirmed = False
        logger.exception("MTF rollback committed but post-commit cache invalidation failed")
    return {
        "status": (
            "ROLLED_BACK" if cache_invalidation_confirmed
            else "ROLLED_BACK_CACHE_INVALIDATION_FAILED"
        ),
        "rolled_back": True,
        "cache_invalidation_confirmed": cache_invalidation_confirmed,
        "audit_id": str(audit_id),
        "restored_profile_versions": restored_versions,
        "watchlists": {
            layer: _watchlist_snapshot(watchlists[watchlist_id])
            for layer, watchlist_id in zip(("L1", "L2"), watchlist_ids)
        },
        "multilayer_contract": deepcopy(before.get("multilayer_contract") or {}),
    }

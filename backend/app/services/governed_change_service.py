"""Human-confirmed, auditable Analysis Chat configuration changes.

The model can only propose a typed JSON Patch against an existing owned
resource.  It never receives a database/session/tool capable of writing.  The
backend snapshots, validates, approves and applies the change after the human
gate, with optimistic concurrency and a guarded rollback.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..api.profiles import _validate_profile_config
from ..models.config_profile import ConfigAuditLog, ConfigProfile
from ..models.copilot import CopilotActionPlan, CopilotAuditLog
from ..models.profile import Profile
from ..models.profile_audit_log import ProfileAuditLog
from ..schemas.analysis_chat import AnalysisChatRuntimeConfig
from ..schemas.futures_engine_config import FuturesEngineConfig
from ..schemas.spot_engine_config import SpotEngineConfig
from ..ai_orchestration.recommendation_guard import (
    GuardDecision,
    RecommendationGuard,
    RecommendationValidation,
)
from .config_service import config_service
from .profile_optimization_service import document_hash, validate_score_links


ACTION_TYPE = "ANALYSIS_CHAT_GOVERNED_CHANGE"
APPROVAL_TEXT = "UI_CONFIRM_GOVERNED_WRITE"
ROLLBACK_TEXT = "CONFIRMO ROLLBACK"
CACHE_RECONCILIATION_MAX_ATTEMPTS = 6
CACHE_RECONCILIATION_BACKOFF_SECONDS = (30, 60, 120, 240, 480)

# Only configuration families with a complete deterministic candidate schema
# and registered policy-semantic validation may reach a governed preview.
# Runtime gates, provider credentials, ML promotion, exchange/order state and
# secrets are intentionally absent.
ALLOWED_CONFIG_TYPES = frozenset({
    "futures_engine",
    "risk",
    "score",
    "spot_engine",
    "strategy",
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
# score_engine.py's own docstring: "Reading self.weights (kept on the instance
# for back-compat but no longer drives scoring)". robust_indicators/score.py
# calculate_score_with_confidence() accepts a ``weights`` parameter "for API
# compatibility with the legacy engine" and immediately ``del weights`` --
# confirmed dead configuration at both the global score document and the
# profile scoring section. A governed change that edits it would look
# successful while having zero effect on real scoring. FIX-AC-GOV-002 Fase
# 5.4: block it until the underlying dead code is either removed or revived.
DEAD_CONFIG_PATH_ROOTS = ("/weights", "/scoring/weights")
ARRAY_IDENTITY_KEYS = frozenset({
    "field",
    "id",
    "indicator",
    "left",
    "name",
    "right",
    "rule_id",
    "type",
})
PRIMARY_ARRAY_IDENTITY_KEYS = frozenset({
    "field",
    "id",
    "indicator",
    "name",
    "rule_id",
})

# Candidate validation deliberately reads the same persisted policy families
# that govern the running system.  Aliases are retained only where the runtime
# already supports them; no value or threshold is synthesized here.
_CANDIDATE_POLICY_TYPES = frozenset({
    "futures_engine",
    "risk",
    "strategy",
    "strategies",
    "spot_engine",
    "score",
    "score_engine",
})
_POLICY_TYPE_PRECEDENCE = {
    "futures": ("futures_engine",),
    "risk": ("risk",),
    "strategy": ("strategies", "strategy"),
    "spot": ("spot_engine",),
    "score": ("score", "score_engine"),
}
_STRICT_CONFIG_PROFILE_TYPES = frozenset({
    "risk",
    "score",
    "spot_engine",
    "futures_engine",
    "strategy",
})
_PROFILE_TIMEFRAMES = frozenset({"1m", "3m", "5m", "15m", "1h"})
_PROFILE_LOGICS = frozenset({"AND", "OR"})
_PROFILE_OPERATORS = frozenset({
    ">",
    ">=",
    "<",
    "<=",
    "=",
    "==",
    "!=",
    "between",
    "in",
    "not_in",
    "contains",
    "is_true",
    "is_false",
})
_SCORE_OPERATORS = frozenset({
    "<", "<=", ">", ">=", "=", "==", "!=", "between",
    "is_true", "is_false", "ema9>ema50>ema200", "ema9>ema50", "ema9<ema50",
    "ema50>ema200", "di+>di-", "di->di+", ">prev+", ">prev",
})

_POLICY_SEMANTIC_VALIDATOR_VERSION = "governed-config-policy-v2"
_RISK_POLICY_REQUIRED_KEYS = frozenset({
    "take_profit_pct",
    "stop_loss_atr_multiplier",
    "trailing_stop_enabled",
    "max_positions",
    "daily_loss_limit_pct",
    "max_exposure_per_asset_pct",
    "circuit_breaker_consecutive_losses",
    "default_order_type",
    "max_slippage_pct",
    "capital_per_trade_pct",
    "max_capital_in_use_pct",
})
_RISK_POLICY_OPTIONAL_KEYS = frozenset({
    "circuit_breaker_pause_minutes",
    "trailing_stop_distance_pct",
})
_RISK_DOWNSTREAM_CAP_KEYS = (
    "capital_per_trade_pct",
    "max_capital_in_use_pct",
    "max_exposure_per_asset_pct",
    "max_positions",
    "daily_loss_limit_pct",
    "circuit_breaker_consecutive_losses",
    "circuit_breaker_pause_minutes",
    "max_slippage_pct",
    "stop_loss_atr_multiplier",
    "default_order_type",
)
_PROTECTED_RISK_CONCEPTS = frozenset({
    "allocation",
    "capital",
    "circuit_breaker",
    "daily_loss",
    "dca",
    "exposure",
    "leverage",
    "liquidation",
    "margin",
    "order",
    "orders",
    "position",
    "positions",
    "position_size",
    "quantity",
    "risk_per_trade",
    "sizing",
    "slippage",
    "sl",
    "stop",
    "stops",
    "stop_loss",
    "take_profit",
    "tp",
})
_PROVEN_PROFILE_RUNTIME_FIELDS = frozenset({
    # Literal, case-sensitive keys consumed from the ProfileEngine indicator
    # payload.  Policy parameter aliases must never be treated as runtime
    # fields: a missing literal key is skipped by RuleEngine.
    "adx",
    "rsi",
    "taker_ratio",
    "volume_spike",
})

# Field names and comparison direction are schema, not thresholds.  Every
# threshold used below is read from the active persisted strategy catalog.
_STRATEGY_PARAMETER_CONTRACTS: dict[str, dict[str, dict[str, str | None]]] = {
    "momentum_breakout": {
        "adx_min": {
            "concept": "adx",
            "comparison": "minimum",
            "runtime_basis": "PROFILE_SCORE_ADX_THRESHOLD",
        },
        "volume_spike_multiplier": {
            "concept": "volume_spike",
            "comparison": "minimum",
            "runtime_basis": "PROFILE_SCORE_VOLUME_SPIKE_THRESHOLD",
        },
        "lookback": {
            "concept": "lookback",
            "comparison": None,
            "runtime_basis": "NO_PROFILE_SCORE_RUNTIME_MAPPING",
        },
    },
    "mean_reversion": {
        "rsi_threshold": {
            "concept": "rsi",
            "comparison": None,
            "runtime_basis": "DIRECTION_NOT_PROVEN_FOR_PROFILE_SCORE_RUNTIME",
        },
        "bollinger_deviation": {
            "concept": "bollinger_deviation",
            "comparison": None,
            "runtime_basis": "NO_PROFILE_SCORE_RUNTIME_MAPPING",
        },
        "zscore_threshold": {
            "concept": "zscore",
            "comparison": None,
            "runtime_basis": "DIRECTION_NOT_PROVEN_FOR_PROFILE_SCORE_RUNTIME",
        },
    },
}


class GovernedChangePathError(ValueError):
    """The proposed JSON Patch path does not match the persisted document."""


class GovernedExecutionFenceError(ValueError):
    """A confirmed write failed its final, transaction-bound safety fence."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _decode_pointer(path: str) -> list[str | int]:
    if not path.startswith("/") or path == "/":
        raise GovernedChangePathError(
            "JSON Patch path must identify a field below the root"
        )
    parts: list[str | int] = []
    for raw in path[1:].split("/"):
        value = raw.replace("~1", "/").replace("~0", "~")
        if any(fragment in value.lower() for fragment in FORBIDDEN_KEY_FRAGMENTS):
            raise GovernedChangePathError(
                f"Sensitive field is outside chat authority: {path}"
            )
        parts.append(int(value) if value.isdigit() else value)
    return parts


def _read(document: Any, part: str | int, *, path: str) -> Any:
    if isinstance(part, int):
        if not isinstance(document, list) or part >= len(document):
            raise GovernedChangePathError(f"List index does not exist at {path}")
        return document[part]
    if not isinstance(document, dict) or part not in document:
        raise GovernedChangePathError(f"Field does not exist at {path}")
    return document[part]


def _pointer(parts: list[str | int]) -> str:
    return "/" + "/".join(
        str(part).replace("~", "~0").replace("/", "~1")
        for part in parts
    )


def _guard_matches(item: Any, identity: Any) -> bool:
    if isinstance(identity, dict):
        return isinstance(item, dict) and all(
            key in item and item[key] == value
            for key, value in identity.items()
        )
    return item == identity


def _stable_identity(item: Any, *, path: str) -> Any:
    if not isinstance(item, dict):
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        raise GovernedChangePathError(
            f"Appended array value lacks a stable scalar identity at {path}"
        )
    for key in ("id", "rule_id", "field", "indicator", "name"):
        if key in item and not isinstance(item[key], (dict, list)):
            return {key: item[key]}
    if (
        "left" in item
        and "right" in item
        and not isinstance(item["left"], (dict, list))
        and not isinstance(item["right"], (dict, list))
    ):
        return {"left": item["left"], "right": item["right"]}
    raise GovernedChangePathError(
        f"Appended array object lacks a stable identity at {path}"
    )


def _validate_guard_identity(identity: Any, *, path: str) -> None:
    if isinstance(identity, dict):
        if not identity or any(key not in ARRAY_IDENTITY_KEYS for key in identity):
            raise GovernedChangePathError(
                f"Array identity uses unsupported keys at {path}"
            )
        if not (
            PRIMARY_ARRAY_IDENTITY_KEYS.intersection(identity)
            or {"left", "right"}.issubset(identity)
        ):
            raise GovernedChangePathError(
                f"Array identity is not stable at {path}"
            )
        if any(isinstance(value, (dict, list)) for value in identity.values()):
            raise GovernedChangePathError(
                f"Array identity values must be scalar at {path}"
            )
        return
    if isinstance(identity, (str, int, float, bool)) or identity is None:
        return
    raise GovernedChangePathError(f"Array identity must be scalar or an object at {path}")


def _validate_array_guards(
    document: dict[str, Any],
    parts: list[str | int],
    *,
    op: str,
    guards: Any,
    path: str,
) -> None:
    if not isinstance(guards, list):
        raise GovernedChangePathError("array_guards must be a list")
    guard_map: dict[str, Any] = {}
    for raw_guard in guards:
        if not isinstance(raw_guard, dict) or set(raw_guard) != {"path", "identity"}:
            raise GovernedChangePathError(
                "Each array guard requires exactly path and identity"
            )
        guard_path = str(raw_guard.get("path") or "")
        if guard_path in guard_map:
            raise GovernedChangePathError(f"Duplicate array guard at {guard_path}")
        guard_map[guard_path] = raw_guard.get("identity")

    current: Any = document
    required_paths: set[str] = set()
    for index, part in enumerate(parts):
        partial = _pointer(parts[: index + 1])
        if isinstance(part, int):
            if not isinstance(current, list):
                raise GovernedChangePathError(f"Expected a list at {partial}")
            is_append = index == len(parts) - 1 and op == "add" and part == len(current)
            if is_append:
                continue
            if part >= len(current):
                raise GovernedChangePathError(f"List index does not exist at {partial}")
            required_paths.add(partial)
            if partial not in guard_map:
                raise GovernedChangePathError(
                    f"Array index requires an identity guard at {partial}"
                )
            identity = guard_map[partial]
            _validate_guard_identity(identity, path=partial)
            matches = [
                item_index
                for item_index, item in enumerate(current)
                if _guard_matches(item, identity)
            ]
            if matches != [part]:
                raise GovernedChangePathError(
                    f"Array identity is missing, ambiguous, or at another index at {partial}"
                )
            current = current[part]
            continue
        if index == len(parts) - 1:
            continue
        current = _read(current, part, path=partial)

    if set(guard_map) != required_paths:
        raise GovernedChangePathError(
            f"Array guards do not match the indexed path at {path}"
        )


def _assert_non_overlapping_paths(changes: list[dict[str, Any]]) -> None:
    decoded: list[tuple[str, list[str | int]]] = []
    for change in changes:
        path = str(change.get("path") or "")
        parts = _decode_pointer(path)
        for prior_path, prior in decoded:
            prefix = min(len(parts), len(prior))
            if parts[:prefix] == prior[:prefix]:
                raise GovernedChangePathError(
                    f"Overlapping governed change paths are not allowed: {prior_path} and {path}"
                )
        decoded.append((path, parts))


def _read_pointer(document: Any, path: str) -> Any:
    current = document
    parts = _decode_pointer(path)
    for index, part in enumerate(parts):
        current = _read(
            current,
            part,
            path=_pointer(parts[: index + 1]),
        )
    return current


def _assert_patch_survived_normalization(
    normalized_before: dict[str, Any],
    normalized_candidate: dict[str, Any],
    changes: list[dict[str, Any]],
) -> None:
    if normalized_before == normalized_candidate:
        raise GovernedChangePathError(
            "The governed proposal is a no-op after configuration validation"
        )
    for change in changes:
        path = str(change.get("path") or "")
        op = str(change.get("op") or "replace").lower()
        if op == "remove":
            try:
                _read_pointer(normalized_candidate, path)
            except GovernedChangePathError:
                continue
            raise GovernedChangePathError(
                f"Removed path survived configuration validation at {path}"
            )
        try:
            materialized_value = _read_pointer(normalized_candidate, path)
        except GovernedChangePathError as exc:
            raise GovernedChangePathError(
                f"Proposed path was discarded by configuration validation at {path}"
            ) from exc
        if materialized_value != change.get("value"):
            raise GovernedChangePathError(
                f"Proposed value changed during configuration validation at {path}"
            )


def _require_canonical_profile_source(
    source: dict[str, Any],
    normalized_source: dict[str, Any],
) -> None:
    """Prevent hidden whole-document changes during a governed patch."""
    if source != normalized_source:
        raise GovernedChangePathError(
            "Profile configuration requires a separate canonicalization preview"
        )


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
    _assert_non_overlapping_paths(changes)
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
            raise GovernedChangePathError(f"Root path must be a field name: {path}")
        if allowed_roots is not None and root not in allowed_roots:
            raise GovernedChangePathError(f"Path outside resource allowlist: {path}")
        # Generic config updates may only modify an existing top-level family.
        # This prevents the model from inventing a new configuration contract.
        if allowed_roots is None and root not in document:
            raise GovernedChangePathError(f"Unknown configuration root: {root}")
        _validate_array_guards(
            candidate,
            parts,
            op=op,
            guards=change.get("array_guards"),
            path=path,
        )
        if "old_value" not in change:
            raise GovernedChangePathError(f"old_value is required at {path}")
        if op == "remove" and change.get("value") is not None:
            raise GovernedChangePathError(f"Remove value must be null at {path}")

        parent: Any = candidate
        for index, part in enumerate(parts[:-1]):
            parent = _read(parent, part, path="/" + "/".join(map(str, parts[: index + 1])))
        leaf = parts[-1]
        old_exists = False
        old_value: Any = None
        if isinstance(leaf, int):
            if not isinstance(parent, list):
                raise GovernedChangePathError(f"Expected a list at {path}")
            if op == "add":
                if leaf != len(parent):
                    raise GovernedChangePathError(
                        f"List additions must append at the current array length at {path}"
                    )
                old_exists = False
                old_value = None
                new_value = deepcopy(change.get("value"))
                identity = _stable_identity(new_value, path=path)
                if any(_guard_matches(item, identity) for item in parent):
                    raise GovernedChangePathError(
                        f"Appended array identity already exists at {path}"
                    )
                parent.insert(leaf, new_value)
            else:
                if leaf >= len(parent):
                    raise GovernedChangePathError(
                        f"List index does not exist at {path}"
                    )
                old_exists = True
                old_value = deepcopy(parent[leaf])
                if op == "remove":
                    parent.pop(leaf)
                else:
                    new_value = deepcopy(change.get("value"))
                    identity = next(
                        guard["identity"]
                        for guard in change.get("array_guards") or []
                        if guard["path"] == path
                    )
                    if not _guard_matches(new_value, identity):
                        raise GovernedChangePathError(
                            f"Array element identity cannot change at {path}"
                        )
                    parent[leaf] = new_value
        else:
            if not isinstance(parent, dict):
                raise GovernedChangePathError(f"Expected an object at {path}")
            old_exists = leaf in parent
            old_value = deepcopy(parent.get(leaf))
            if op in {"replace", "remove"} and not old_exists:
                raise GovernedChangePathError(f"Field does not exist at {path}")
            if op == "add" and old_exists:
                raise GovernedChangePathError(f"Added field already exists at {path}")
            if op == "remove":
                del parent[leaf]
            else:
                parent[leaf] = deepcopy(change.get("value"))
        if any(isinstance(part, int) for part in parts[:-1]) and leaf in ARRAY_IDENTITY_KEYS:
            raise GovernedChangePathError(
                f"Array element identity fields cannot be edited in place at {path}"
            )
        expected_old_value = change.get("old_value")
        if op == "add":
            if expected_old_value is not None:
                raise GovernedChangePathError(f"Add old_value must be null at {path}")
        elif old_value != expected_old_value:
            raise GovernedChangePathError(f"Stale old_value at {path}")
        if op == "replace" and old_value == change.get("value"):
            raise GovernedChangePathError(f"Replacement is a no-op at {path}")
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
    if config_type == "risk":
        _validate_closed_risk_policy(candidate)
        return candidate
    if config_type == "strategy":
        _validate_closed_strategy_catalog(candidate)
        return candidate
    if config_type == "score":
        _validate_score_candidate(candidate)
        return candidate
    if config_type == "spot_engine":
        validated = SpotEngineConfig.from_config_json(deepcopy(candidate)).model_dump()
        if validated != candidate:
            raise ValueError(
                "Spot candidate must be the complete canonical document without unknown keys"
            )
        if validated["selling"]["never_sell_at_loss"] is not True:
            raise ValueError("Spot invariant requires selling.never_sell_at_loss=true")
        return candidate
    if config_type == "futures_engine":
        validated = FuturesEngineConfig.from_config_json(deepcopy(candidate)).model_dump()
        if validated != candidate:
            raise ValueError(
                "Futures candidate must be the complete canonical document without unknown keys"
            )
        return candidate
    raise ValueError(f"Configuration family has no governed candidate schema: {config_type}")


def _iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _apply_materialized_change(
    document: dict[str, Any],
    change: dict[str, Any],
    *,
    path: str | None = None,
) -> None:
    """Apply one internal, already-reviewed diff to its frozen source."""
    op = str(change.get("op") or "replace").lower()
    if op not in {"add", "replace", "remove"}:
        raise GovernedChangePathError("Governed plan diff has an unsupported operation")
    materialized_path = path if path is not None else str(change.get("path") or "")
    parts = _decode_pointer(materialized_path)
    parent: Any = document
    for index, part in enumerate(parts[:-1]):
        parent = _read(parent, part, path=_pointer(parts[: index + 1]))
    leaf = parts[-1]
    if isinstance(leaf, int):
        if not isinstance(parent, list):
            raise GovernedChangePathError(f"Expected a list at {materialized_path}")
        exists = leaf < len(parent)
        old_value = deepcopy(parent[leaf]) if exists else None
        if op == "add":
            if leaf != len(parent):
                raise GovernedChangePathError(
                    f"Materialized list addition is not an append at {materialized_path}"
                )
            parent.append(deepcopy(change.get("value")))
        elif not exists:
            raise GovernedChangePathError(
                f"Materialized list index does not exist at {materialized_path}"
            )
        elif op == "remove":
            parent.pop(leaf)
        else:
            parent[leaf] = deepcopy(change.get("value"))
    else:
        if not isinstance(parent, dict):
            raise GovernedChangePathError(f"Expected an object at {materialized_path}")
        exists = leaf in parent
        old_value = deepcopy(parent.get(leaf))
        if op == "add":
            if exists:
                raise GovernedChangePathError(
                    f"Materialized add already exists at {materialized_path}"
                )
            parent[leaf] = deepcopy(change.get("value"))
        elif not exists:
            raise GovernedChangePathError(
                f"Materialized field does not exist at {materialized_path}"
            )
        elif op == "remove":
            del parent[leaf]
        else:
            parent[leaf] = deepcopy(change.get("value"))
    if op == "add":
        if change.get("old_value") is not None:
            raise GovernedChangePathError(
                f"Materialized add old_value must be null at {materialized_path}"
            )
    elif old_value != change.get("old_value"):
        raise GovernedChangePathError(
            f"Materialized diff old_value does not match source at {materialized_path}"
        )


def _reconstruct_candidate_from_materialized_diff(
    plan: CopilotActionPlan,
) -> dict[str, Any]:
    """Rebuild the *entire* candidate from source plus persisted diff."""
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    source = payload.get("source_document")
    if not isinstance(source, dict):
        raise GovernedChangePathError("Governed plan lacks a source document")
    changes = list(plan.proposed_diff or [])
    if not changes:
        raise GovernedChangePathError("Governed plan has no materialized diff")
    reconstructed = deepcopy(source)
    if operation not in {"UPDATE_PROFILE_CONFIG_SET", "SET_PROFILE_ACTIVE_STATUS"}:
        _assert_non_overlapping_paths(changes)
        for change in changes:
            if not isinstance(change, dict):
                raise GovernedChangePathError("Governed plan diff must contain objects")
            _apply_materialized_change(reconstructed, change)
        return reconstructed

    rows = reconstructed.get("profiles")
    if not isinstance(rows, list) or not rows:
        raise GovernedChangePathError("Bulk governed source has no profile rows")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise GovernedChangePathError("Bulk governed source row is malformed")
        profile_id = str(row.get("profile_id") or "")
        if not profile_id or profile_id in by_id:
            raise GovernedChangePathError("Bulk governed source profile IDs are invalid")
        by_id[profile_id] = row
    scoped_paths: dict[str, list[dict[str, Any]]] = {}
    for change in changes:
        if not isinstance(change, dict):
            raise GovernedChangePathError("Governed plan diff must contain objects")
        parts = _decode_pointer(str(change.get("path") or ""))
        if len(parts) < 3 or parts[0] != "profiles":
            raise GovernedChangePathError("Bulk governed diff path is not canonical")
        profile_id = str(parts[1])
        if profile_id not in by_id:
            raise GovernedChangePathError("Bulk governed diff references an unknown profile")
        if change.get("profile_id") and str(change["profile_id"]) != profile_id:
            raise GovernedChangePathError("Bulk governed diff profile metadata is inconsistent")
        suffix = _pointer(parts[2:])
        local_change = {**change, "path": suffix}
        scoped_paths.setdefault(profile_id, []).append(local_change)
    for profile_id, local_changes in scoped_paths.items():
        _assert_non_overlapping_paths(local_changes)
        target = by_id[profile_id]
        if operation == "UPDATE_PROFILE_CONFIG_SET":
            target = target.get("config")
            if not isinstance(target, dict):
                raise GovernedChangePathError("Bulk governed profile config is malformed")
        for change in local_changes:
            _apply_materialized_change(target, change)
    return reconstructed


def _assert_materialized_diff_matches_candidate(plan: CopilotActionPlan) -> None:
    """Require exact deep equality, not only equality at changed paths."""
    payload = dict(plan.execution_payload or {})
    candidate = payload.get("candidate_document")
    if not isinstance(candidate, dict):
        raise GovernedChangePathError("Governed plan lacks a candidate document")
    reconstructed = _reconstruct_candidate_from_materialized_diff(plan)
    if reconstructed != candidate:
        raise GovernedChangePathError(
            "Governed candidate contains changes not reconstructed from the materialized diff"
        )


def _latest_global_policy_records(
    records: list[ConfigProfile],
) -> dict[str, ConfigProfile | None]:
    """Apply the runtime's already-established alias precedence, without values."""
    by_type: dict[str, ConfigProfile] = {}
    for record in records:
        if record.pool_id is None and record.config_type not in by_type:
            by_type[record.config_type] = record
    selected: dict[str, ConfigProfile | None] = {}
    for family, aliases in _POLICY_TYPE_PRECEDENCE.items():
        selected[family] = next(
            (by_type[alias] for alias in aliases if alias in by_type),
            None,
        )
    return selected


def _effective_policy_document(
    plan: CopilotActionPlan,
    record: ConfigProfile,
) -> tuple[dict[str, Any], bool]:
    payload = dict(plan.execution_payload or {})
    is_target = (
        payload.get("operation_type") == "UPDATE_CONFIG_PROFILE"
        and str(payload.get("config_profile_id") or "") == str(record.id)
    )
    document = (
        payload.get("candidate_document")
        if is_target
        else record.config_json
    )
    if not isinstance(document, dict):
        raise ValueError(f"Persisted {record.config_type} policy is not a JSON object")
    return deepcopy(document), is_target


def _profile_candidates_from_plan(plan: CopilotActionPlan) -> list[dict[str, Any]]:
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    candidate = payload.get("candidate_document")
    if operation == "UPDATE_PROFILE_CONFIG":
        if not isinstance(candidate, dict):
            raise ValueError("Profile candidate is not a JSON object")
        return [candidate]
    if operation == "UPDATE_PROFILE_CONFIG_SET":
        if not isinstance(candidate, dict):
            raise ValueError("Profile set candidate is not a JSON object")
        rows = candidate.get("profiles")
        if not isinstance(rows, list) or not rows:
            raise ValueError("Profile set candidate is empty")
        configs: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("config"), dict):
                raise ValueError("Profile set candidate contains a malformed row")
            configs.append(row["config"])
        return configs
    return []


def _require_exact_keys(value: Any, keys: frozenset[str], *, path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError(f"{path} must contain exactly {', '.join(sorted(keys))}")
    return value


def _require_known_keys(
    value: Any,
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    missing = required - set(value)
    if missing:
        raise ValueError(f"{path} is missing keys: {', '.join(sorted(missing))}")
    unknown = set(value) - required - optional
    if unknown:
        raise ValueError(f"{path} contains unknown keys: {', '.join(sorted(unknown))}")
    return value


def _require_logic(value: Any, *, path: str) -> str:
    if not isinstance(value, str) or value not in _PROFILE_LOGICS:
        raise ValueError(f"{path} must be AND or OR")
    return value


def _require_finite_number(value: Any, *, path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be numeric")
    if isinstance(value, float) and (value != value or value in {float("inf"), float("-inf")}):
        raise ValueError(f"{path} must be finite")


def _validate_condition(
    condition: Any,
    *,
    path: str,
    block: bool = False,
    allow_inferred_type: bool = False,
) -> None:
    if not isinstance(condition, dict):
        raise ValueError(f"{path} must be an object")
    allowed = {
        "id", "field", "operator", "value", "min", "max", "period", "timeframe",
        "required", "enabled", "rule_id", "type", "indicator", "left", "right",
        "name", "description", "source",
    }
    unknown = set(condition) - allowed
    if unknown:
        raise ValueError(f"{path} contains unknown keys: {', '.join(sorted(unknown))}")
    if "id" in condition and (
        not isinstance(condition["id"], str) or not condition["id"].strip()
    ):
        raise ValueError(f"{path}.id must be a non-empty string")
    if "rule_id" in condition and (
        not isinstance(condition["rule_id"], str) or not condition["rule_id"].strip()
    ):
        raise ValueError(f"{path}.rule_id must be a non-empty string")
    for key in ("name", "description", "source"):
        if key in condition and not isinstance(condition[key], str):
            raise ValueError(f"{path}.{key} must be a string")
    field = condition.get("indicator") if block else condition.get("field")
    if block:
        condition_type = condition.get("type")
        if condition_type is None and allow_inferred_type:
            if condition.get("left") is not None or condition.get("right") is not None:
                condition_type = "comparison"
            elif condition.get("operator") in {"is_true", "is_false"} or isinstance(
                condition.get("value"), bool
            ):
                condition_type = "boolean"
            else:
                condition_type = "threshold"
        if condition_type not in {"threshold", "boolean", "comparison"}:
            raise ValueError(f"{path}.type is unsupported")
        if condition_type == "comparison":
            forbidden = {"field", "indicator", "value", "min", "max"}.intersection(
                condition
            )
            if forbidden:
                raise ValueError(
                    f"{path} comparison can contain only left/right operands"
                )
            if not isinstance(condition.get("left"), str) or not isinstance(condition.get("right"), str):
                raise ValueError(f"{path} comparison requires left and right")
        else:
            forbidden = {"field", "left", "right"}.intersection(condition)
            if forbidden:
                raise ValueError(
                    f"{path} {condition_type} must use indicator only"
                )
            if not isinstance(field, str) or not field.strip():
                raise ValueError(f"{path} requires a non-empty indicator")
    else:
        forbidden = {"indicator", "left", "right"}.intersection(condition)
        if forbidden:
            raise ValueError(f"{path} must use field as its only lookup key")
        if not isinstance(field, str) or not field.strip():
            raise ValueError(f"{path} requires a non-empty field")
    operator = condition.get("operator")
    if not isinstance(operator, str) or operator not in _PROFILE_OPERATORS:
        raise ValueError(f"{path}.operator is unsupported")
    if condition.get("timeframe") is not None and condition["timeframe"] not in _PROFILE_TIMEFRAMES:
        raise ValueError(f"{path}.timeframe is unsupported")
    if condition.get("period") is not None:
        if isinstance(condition["period"], bool) or not isinstance(condition["period"], int) or condition["period"] <= 0:
            raise ValueError(f"{path}.period must be a positive integer")
    for key in ("required", "enabled"):
        if key in condition and not isinstance(condition[key], bool):
            raise ValueError(f"{path}.{key} must be boolean")
    if operator == "between":
        for key in ("min", "max"):
            if key not in condition:
                raise ValueError(f"{path}.{key} is required for between")
            _require_finite_number(condition[key], path=f"{path}.{key}")
        if condition["min"] > condition["max"]:
            raise ValueError(f"{path}.min cannot exceed max")
        if "value" in condition:
            raise ValueError(f"{path}.value is invalid for between")
    elif operator in {"is_true", "is_false"}:
        if any(key in condition for key in ("min", "max")):
            raise ValueError(f"{path} boolean operator cannot carry min/max")
        if "value" in condition:
            expected = operator == "is_true"
            if not isinstance(condition["value"], bool) or condition["value"] is not expected:
                raise ValueError(f"{path}.value must agree with the boolean operator")
    else:
        if "value" not in condition and not (
            block and condition_type == "comparison"
        ):
            raise ValueError(f"{path}.value is required")
        value = condition.get("value")
        if operator in {"in", "not_in"}:
            if not isinstance(value, list) or not value or any(
                isinstance(item, (dict, list)) for item in value
            ):
                raise ValueError(f"{path}.value must be a non-empty scalar array")
        elif operator == "contains" and value is None:
            raise ValueError(f"{path}.value is required for contains")
        elif isinstance(value, (dict, list)):
            raise ValueError(f"{path}.value has an invalid type")
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError(f"{path}.value has an invalid type")
        if operator in {">", ">=", "<", "<="} and not (
            block and condition_type == "comparison"
        ):
            _require_finite_number(value, path=f"{path}.value")
        if isinstance(value, float):
            _require_finite_number(value, path=f"{path}.value")


def _validate_weights(value: Any, *, path: str) -> None:
    weights = _require_exact_keys(
        value,
        frozenset({"liquidity", "market_structure", "momentum", "signal"}),
        path=path,
    )
    for key, weight in weights.items():
        _require_finite_number(weight, path=f"{path}.{key}")
        if weight < 0:
            raise ValueError(f"{path}.{key} cannot be negative")


def _validate_thresholds(value: Any, *, path: str) -> None:
    thresholds = _require_exact_keys(
        value,
        frozenset({"strong_buy", "buy", "neutral"}),
        path=path,
    )
    for key, threshold in thresholds.items():
        _require_finite_number(threshold, path=f"{path}.{key}")
        if not 0 <= threshold <= 100:
            raise ValueError(f"{path}.{key} must be between 0 and 100")
    if not thresholds["neutral"] <= thresholds["buy"] <= thresholds["strong_buy"]:
        raise ValueError(f"{path} must be ordered neutral <= buy <= strong_buy")


def _validate_score_rule(rule: Any, *, path: str) -> str:
    rule = _require_known_keys(
        rule,
        required=frozenset({"id", "indicator", "operator", "points", "category"}),
        optional=frozenset({
            "value", "min", "max", "name", "enabled", "description", "source",
            "period", "timeframe",
        }),
        path=path,
    )
    rule_id = rule["id"]
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise ValueError(f"{path}.id must be a non-empty string")
    if not isinstance(rule["indicator"], str) or not rule["indicator"].strip():
        raise ValueError(f"{path}.indicator must be a non-empty string")
    operator = rule["operator"]
    if operator not in _SCORE_OPERATORS:
        raise ValueError(f"{path}.operator is unsupported")
    _require_finite_number(rule["points"], path=f"{path}.points")
    if not isinstance(rule["category"], str) or not rule["category"].strip():
        raise ValueError(f"{path}.category must be a non-empty string")
    for key in ("name", "description", "source"):
        if key in rule and not isinstance(rule[key], str):
            raise ValueError(f"{path}.{key} must be a string")
    if "enabled" in rule and not isinstance(rule["enabled"], bool):
        raise ValueError(f"{path}.enabled must be boolean")
    if rule.get("timeframe") is not None and rule["timeframe"] not in _PROFILE_TIMEFRAMES:
        raise ValueError(f"{path}.timeframe is unsupported")
    if rule.get("period") is not None and (
        isinstance(rule["period"], bool)
        or not isinstance(rule["period"], int)
        or rule["period"] <= 0
    ):
        raise ValueError(f"{path}.period must be a positive integer")
    if operator == "between":
        for key in ("min", "max"):
            if key not in rule:
                raise ValueError(f"{path}.{key} is required for between")
            _require_finite_number(rule[key], path=f"{path}.{key}")
        if rule["min"] > rule["max"]:
            raise ValueError(f"{path}.min cannot exceed max")
        if rule.get("value") is not None:
            raise ValueError(f"{path}.value must be null or absent for between")
    elif operator in {"<", "<=", ">", ">=", "=", "==", "!="}:
        if (
            "value" not in rule
            or rule["value"] is None
            or isinstance(rule["value"], (dict, list))
        ):
            raise ValueError(f"{path}.value must be a scalar")
        if operator in {"<", "<=", ">", ">="}:
            _require_finite_number(rule["value"], path=f"{path}.value")
    elif any(key in rule for key in ("min", "max")):
        raise ValueError(f"{path} cannot carry min/max for {operator}")
    return rule_id


def _validate_embedded_scoring(value: Any, *, path: str) -> None:
    scoring = _require_exact_keys(
        value,
        frozenset({"enabled", "selected_rule_ids", "weights"}),
        path=path,
    )
    if not isinstance(scoring["enabled"], bool):
        raise ValueError(f"{path}.enabled must be boolean")
    selected = scoring["selected_rule_ids"]
    if not isinstance(selected, list) or any(
        not isinstance(item, str) or not item.strip() for item in selected
    ):
        raise ValueError(f"{path}.selected_rule_ids must contain non-empty strings")
    if len(selected) != len(set(selected)):
        raise ValueError(f"{path}.selected_rule_ids must be unique")
    _validate_weights(scoring["weights"], path=f"{path}.weights")


def _validate_flat_block(block: Any, *, path: str) -> None:
    if not isinstance(block, dict):
        raise ValueError(f"{path} must be an object")
    common = frozenset({
        "id", "name", "enabled", "reason", "description", "source", "rule_id",
        "timeframe", "period",
    })
    block_type = block.get("type")
    if block_type is None:
        if "condition" in block:
            block_type = "condition"
        elif "min" in block or "max" in block:
            block_type = "range"
        else:
            block_type = "threshold"
    if block_type == "threshold":
        block = _require_known_keys(
            block,
            required=frozenset({"indicator", "operator", "value"}),
            optional=common | frozenset({"type"}),
            path=path,
        )
        if block["operator"] not in {"<", "<=", ">", ">="}:
            raise ValueError(f"{path}.operator is unsupported for a flat threshold block")
        _require_finite_number(block["value"], path=f"{path}.value")
    elif block_type == "range":
        block = _require_known_keys(
            block,
            required=frozenset({"indicator", "min", "max"}),
            optional=common | frozenset({"type"}),
            path=path,
        )
        _require_finite_number(block["min"], path=f"{path}.min")
        _require_finite_number(block["max"], path=f"{path}.max")
        if block["min"] > block["max"]:
            raise ValueError(f"{path}.min cannot exceed max")
    elif block_type == "condition":
        block = _require_known_keys(
            block,
            required=frozenset({"condition"}),
            optional=common | frozenset({"type", "indicator"}),
            path=path,
        )
        if block["condition"] not in {"ema9<ema50", "ema9>ema50"}:
            raise ValueError(f"{path}.condition is unsupported by BlockEngine")
    else:
        raise ValueError(f"{path}.type is unsupported")
    if "id" in block and (not isinstance(block["id"], str) or not block["id"].strip()):
        raise ValueError(f"{path}.id must be a non-empty string")
    if "name" in block and (not isinstance(block["name"], str) or not block["name"].strip()):
        raise ValueError(f"{path}.name must be a non-empty string")
    if "enabled" in block and not isinstance(block["enabled"], bool):
        raise ValueError(f"{path}.enabled must be boolean")
    for key in ("reason", "description", "source", "rule_id"):
        if key in block and not isinstance(block[key], str):
            raise ValueError(f"{path}.{key} must be a string")
    if block_type in {"threshold", "range"} and (
        not isinstance(block.get("indicator"), str)
        or not block["indicator"].strip()
    ):
        raise ValueError(f"{path}.indicator must be a non-empty string")
    if "indicator" in block and not isinstance(block["indicator"], str):
        raise ValueError(f"{path}.indicator must be a string")
    if block.get("timeframe") is not None and block["timeframe"] not in _PROFILE_TIMEFRAMES:
        raise ValueError(f"{path}.timeframe is unsupported")
    if block.get("period") is not None and (
        isinstance(block["period"], bool)
        or not isinstance(block["period"], int)
        or block["period"] <= 0
    ):
        raise ValueError(f"{path}.period must be a positive integer")


def _validate_generated_profile_metadata(value: Any) -> None:
    path = "profile.metadata"
    metadata = _require_exact_keys(
        value,
        frozenset({
            "generated_by", "suggestion_id", "source_combination_id",
            "confidence_level", "confidence_score", "created_as",
            "live_trading_enabled", "is_shadow_only", "profile_family",
        }),
        path=path,
    )
    if metadata["generated_by"] != "profile_intelligence":
        raise ValueError(f"{path}.generated_by is unsupported")
    try:
        UUID(str(metadata["suggestion_id"]))
        if metadata["source_combination_id"] is not None:
            UUID(str(metadata["source_combination_id"]))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{path} contains an invalid generated lineage UUID") from exc
    for key in ("confidence_level", "profile_family"):
        if metadata[key] is not None and not isinstance(metadata[key], str):
            raise ValueError(f"{path}.{key} must be a string or null")
    _require_finite_number(metadata["confidence_score"], path=f"{path}.confidence_score")
    if metadata["created_as"] not in {"SHADOW_ONLY", "DRAFT"}:
        raise ValueError(f"{path}.created_as is unsupported")
    if metadata["live_trading_enabled"] is not False:
        raise ValueError(f"{path}.live_trading_enabled must remain false")
    if metadata["is_shadow_only"] is not True:
        raise ValueError(f"{path}.is_shadow_only must remain true")


def _validate_strict_profile_config(config: dict[str, Any]) -> None:
    """Validate canonical and known runtime Profile documents without coercion."""
    generated = isinstance(config, dict) and "metadata" in config
    if generated:
        roots = _require_known_keys(
            config,
            required=frozenset({
                "scoring", "signals", "block_rules", "entry_triggers", "metadata",
            }),
            optional=frozenset({"default_timeframe", "filters"}),
            path="profile",
        )
        _validate_generated_profile_metadata(roots["metadata"])
    else:
        roots = _require_exact_keys(config, PROFILE_ROOTS, path="profile")

    timeframe = roots.get("default_timeframe")
    if timeframe is not None and timeframe not in _PROFILE_TIMEFRAMES:
        raise ValueError("profile.default_timeframe is unsupported")
    if "filters" in roots:
        filters = _require_exact_keys(
            roots["filters"], frozenset({"logic", "conditions"}), path="profile.filters"
        )
        _require_logic(filters["logic"], path="profile.filters.logic")
        if not isinstance(filters["conditions"], list):
            raise ValueError("profile.filters.conditions must be an array")
        for index, condition in enumerate(filters["conditions"]):
            _validate_condition(condition, path=f"profile.filters.conditions[{index}]")

    signals = _require_known_keys(
        roots["signals"],
        required=frozenset({"logic", "conditions"}),
        optional=frozenset({"scoring"}),
        path="profile.signals",
    )
    _require_logic(signals["logic"], path="profile.signals.logic")
    if not isinstance(signals["conditions"], list):
        raise ValueError("profile.signals.conditions must be an array")
    for index, condition in enumerate(signals["conditions"]):
        _validate_condition(condition, path=f"profile.signals.conditions[{index}]")
    if "scoring" in signals:
        _validate_embedded_scoring(signals["scoring"], path="profile.signals.scoring")

    entry = _require_known_keys(
        roots["entry_triggers"],
        required=frozenset({"logic", "conditions"}),
        optional=frozenset({"logic_preview_text", "scoring"}),
        path="profile.entry_triggers",
    )
    _require_logic(entry["logic"], path="profile.entry_triggers.logic")
    if "logic_preview_text" in entry and not isinstance(entry["logic_preview_text"], str):
        raise ValueError("profile.entry_triggers.logic_preview_text must be a string")
    if not isinstance(entry["conditions"], list):
        raise ValueError("profile.entry_triggers.conditions must be an array")
    for index, condition in enumerate(entry["conditions"]):
        _validate_condition(
            condition,
            path=f"profile.entry_triggers.conditions[{index}]",
            block=True,
            allow_inferred_type=True,
        )
    if "scoring" in entry:
        _validate_embedded_scoring(entry["scoring"], path="profile.entry_triggers.scoring")

    blocks = _require_exact_keys(
        roots["block_rules"], frozenset({"blocks"}), path="profile.block_rules"
    )
    if not isinstance(blocks["blocks"], list):
        raise ValueError("profile.block_rules.blocks must be an array")
    for block_index, raw_block in enumerate(blocks["blocks"]):
        block_path = f"profile.block_rules.blocks[{block_index}]"
        if not isinstance(raw_block, dict):
            raise ValueError(f"{block_path} must be an object")
        if "conditions" not in raw_block:
            _validate_flat_block(raw_block, path=block_path)
            continue
        block = _require_known_keys(
            raw_block,
            required=frozenset({"name", "enabled", "logic", "conditions"}),
            optional=frozenset({
                "id", "reason", "description", "source", "rule_id", "timeframe", "period",
            }),
            path=block_path,
        )
        if "id" in block and (
            not isinstance(block["id"], str) or not block["id"].strip()
        ):
            raise ValueError(f"{block_path}.id must be a non-empty string")
        if not isinstance(block["name"], str) or not block["name"].strip():
            raise ValueError(f"{block_path}.name is required")
        if not isinstance(block["enabled"], bool):
            raise ValueError(f"{block_path}.enabled must be boolean")
        for key in ("reason", "description", "source", "rule_id"):
            if key in block and not isinstance(block[key], str):
                raise ValueError(f"{block_path}.{key} must be a string")
        _require_logic(block["logic"], path=f"{block_path}.logic")
        if block.get("timeframe") is not None and block["timeframe"] not in _PROFILE_TIMEFRAMES:
            raise ValueError(f"{block_path}.timeframe is unsupported")
        if block.get("period") is not None and (
            isinstance(block["period"], bool)
            or not isinstance(block["period"], int)
            or block["period"] <= 0
        ):
            raise ValueError(f"{block_path}.period must be a positive integer")
        if not isinstance(block["conditions"], list) or not block["conditions"]:
            raise ValueError(f"{block_path}.conditions must be a non-empty array")
        for condition_index, condition in enumerate(block["conditions"]):
            _validate_condition(
                condition,
                path=f"{block_path}.conditions[{condition_index}]",
                block=True,
            )

    scoring = roots["scoring"]
    if generated:
        scoring = _require_exact_keys(
            scoring,
            frozenset({
                "selected_rule_ids", "weights", "generated_rules", "source", "suggestion_id",
            }),
            path="profile.scoring",
        )
        if scoring["source"] != "profile_intelligence":
            raise ValueError("profile.scoring.source is unsupported")
        try:
            UUID(str(scoring["suggestion_id"]))
        except (TypeError, ValueError) as exc:
            raise ValueError("profile.scoring.suggestion_id is invalid") from exc
        if not isinstance(scoring["generated_rules"], list):
            raise ValueError("profile.scoring.generated_rules must be an array")
        generated_rule_ids: set[str] = set()
        for index, rule in enumerate(scoring["generated_rules"]):
            rule_id = _validate_score_rule(
                rule, path=f"profile.scoring.generated_rules[{index}]"
            )
            if rule_id in generated_rule_ids:
                raise ValueError("profile.scoring.generated_rules IDs must be unique")
            generated_rule_ids.add(rule_id)
    else:
        scoring = _require_exact_keys(
            scoring,
            frozenset({"enabled", "weights", "rules", "selected_rule_ids", "thresholds"}),
            path="profile.scoring",
        )
        if not isinstance(scoring["enabled"], bool):
            raise ValueError("profile.scoring.enabled must be boolean")
        if not isinstance(scoring["rules"], list):
            raise ValueError("profile.scoring.rules must be an array")
        profile_rule_ids: set[str] = set()
        for index, rule in enumerate(scoring["rules"]):
            rule_id = _validate_score_rule(
                rule, path=f"profile.scoring.rules[{index}]"
            )
            if rule_id in profile_rule_ids:
                raise ValueError("profile.scoring.rules IDs must be unique")
            profile_rule_ids.add(rule_id)
        _validate_thresholds(scoring["thresholds"], path="profile.scoring.thresholds")
    selected = scoring["selected_rule_ids"]
    if not isinstance(selected, list) or any(
        not isinstance(item, str) or not item.strip() for item in selected
    ):
        raise ValueError("profile.scoring.selected_rule_ids must contain non-empty strings")
    if len(selected) != len(set(selected)):
        raise ValueError("profile.scoring.selected_rule_ids must be unique")
    _validate_weights(scoring["weights"], path="profile.scoring.weights")


def _validate_score_candidate(candidate: dict[str, Any]) -> None:
    score = _require_exact_keys(
        candidate,
        frozenset({
            "weights", "scoring_rules", "thresholds",
            "auto_select_top_n", "auto_select_min_score",
        }),
        path="score",
    )
    _validate_weights(score["weights"], path="score.weights")
    _validate_thresholds(score["thresholds"], path="score.thresholds")
    top_n = score["auto_select_top_n"]
    if isinstance(top_n, bool) or not isinstance(top_n, int) or not 1 <= top_n <= 50:
        raise ValueError("score.auto_select_top_n must be an integer between 1 and 50")
    _require_finite_number(
        score["auto_select_min_score"], path="score.auto_select_min_score"
    )
    if not 0 <= score["auto_select_min_score"] <= 100:
        raise ValueError("score.auto_select_min_score must be between 0 and 100")
    rules = score["scoring_rules"]
    if not isinstance(rules, list) or not rules:
        raise ValueError("score.scoring_rules must be a non-empty array")
    seen: set[str] = set()
    for index, rule in enumerate(rules):
        rule_id = _validate_score_rule(rule, path=f"score.scoring_rules[{index}]")
        if rule_id in seen:
            raise ValueError("score scoring rule IDs must be unique")
        seen.add(rule_id)


def _target_profile_ids(plan: CopilotActionPlan) -> set[str]:
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    if operation == "UPDATE_PROFILE_CONFIG":
        return {str(payload.get("profile_id") or "")}
    if operation in {"UPDATE_PROFILE_CONFIG_SET", "SET_PROFILE_ACTIVE_STATUS"}:
        return {str(item) for item in payload.get("profile_ids") or []}
    return set()


def _profile_dependency_snapshots(
    plan: CopilotActionPlan,
    profiles: list[Profile],
) -> list[dict[str, Any]]:
    """Hash only Profile rows actually read by deterministic validation."""
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    if operation == "UPDATE_CONFIG_PROFILE" and payload.get("config_type") in {"score", "score_engine"}:
        relevant = profiles
    else:
        target_ids = _target_profile_ids(plan)
        relevant = [profile for profile in profiles if str(profile.id) in target_ids]
        if target_ids and {str(profile.id) for profile in relevant} != target_ids:
            raise ValueError("Target Profile dependency snapshot is incomplete")
    snapshots = []
    for profile in sorted(relevant, key=lambda item: str(item.id)):
        row = {
            "id": str(profile.id),
            "name": getattr(profile, "name", None),
            "profile_role": getattr(profile, "profile_role", None),
            "pipeline_label": getattr(profile, "pipeline_label", None),
            "generated_by": getattr(profile, "generated_by", None),
            "config": deepcopy(profile.config or {}),
            "is_active": bool(profile.is_active),
            "profile_version": _iso_or_none(profile.profile_version),
            "updated_at": _iso_or_none(profile.updated_at),
        }
        snapshots.append(row)
    return snapshots


def _plan_binding_payload(plan: CopilotActionPlan) -> dict[str, Any]:
    """Canonical immutable intent that an execution fence must re-prove."""
    evidence = deepcopy(getattr(plan, "evidence", None) or {})
    evidence.pop("candidate_validation", None)
    return {
        "action_type": getattr(plan, "action_type", None),
        "target_type": getattr(plan, "target_type", None),
        "target_id": getattr(plan, "target_id", None),
        "execution_payload": deepcopy(getattr(plan, "execution_payload", None) or {}),
        "proposed_diff": deepcopy(getattr(plan, "proposed_diff", None) or []),
        "target_state_hash": getattr(plan, "target_state_hash", None),
        "rollback_plan": deepcopy(getattr(plan, "rollback_plan", None) or {}),
        "objective": getattr(plan, "objective", None),
        "risk_assessment": getattr(plan, "risk_assessment", None),
        "base_evidence": evidence,
    }


def plan_binding_hash(plan: CopilotActionPlan) -> str:
    return document_hash(_plan_binding_payload(plan))


def _normalize_semantic_concept(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = "_".join(
        part for part in value.strip().lower().replace("-", "_").replace(".", "_").replace(" ", "_").split("_")
        if part
    )
    aliases = {
        "adx_min": "adx",
        "adx_min_threshold": "adx",
        "entry_adx_min": "adx",
        "volume_spike_multiplier": "volume_spike",
        "rsi_threshold": "rsi",
        "z_score": "zscore",
        "zscore_threshold": "zscore",
    }
    return aliases.get(normalized, normalized)


def _runtime_candidate_concept(value: Any) -> str:
    """Preserve the literal, case-sensitive ProfileEngine lookup key."""
    return value if isinstance(value, str) else ""


def _is_protected_risk_concept(value: Any) -> bool:
    concept = _normalize_semantic_concept(value)
    if not concept:
        return False
    return any(
        concept == protected
        or concept.startswith(f"{protected}_")
        or concept.endswith(f"_{protected}")
        or f"_{protected}_" in concept
        for protected in _PROTECTED_RISK_CONCEPTS
    )


def _validate_closed_risk_policy(policy: dict[str, Any]) -> dict[str, Any]:
    risk = _require_known_keys(
        policy,
        required=_RISK_POLICY_REQUIRED_KEYS,
        optional=_RISK_POLICY_OPTIONAL_KEYS,
        path="risk",
    )
    for key in (
        "take_profit_pct",
        "daily_loss_limit_pct",
        "max_exposure_per_asset_pct",
        "max_slippage_pct",
        "capital_per_trade_pct",
        "max_capital_in_use_pct",
    ):
        _require_finite_number(risk[key], path=f"risk.{key}")
        if not 0 <= risk[key] <= 100:
            raise ValueError(f"risk.{key} must be between 0 and 100")
    _require_finite_number(
        risk["stop_loss_atr_multiplier"],
        path="risk.stop_loss_atr_multiplier",
    )
    if risk["stop_loss_atr_multiplier"] <= 0:
        raise ValueError("risk.stop_loss_atr_multiplier must be positive")
    for key in ("max_positions", "circuit_breaker_consecutive_losses"):
        if isinstance(risk[key], bool) or not isinstance(risk[key], int) or risk[key] < 1:
            raise ValueError(f"risk.{key} must be a positive integer")
    if not isinstance(risk["trailing_stop_enabled"], bool):
        raise ValueError("risk.trailing_stop_enabled must be boolean")
    if "circuit_breaker_pause_minutes" in risk and (
        isinstance(risk["circuit_breaker_pause_minutes"], bool)
        or not isinstance(risk["circuit_breaker_pause_minutes"], int)
        or risk["circuit_breaker_pause_minutes"] < 1
    ):
        raise ValueError("risk.circuit_breaker_pause_minutes must be a positive integer")
    if risk["default_order_type"] not in {"limit", "market"}:
        raise ValueError("risk.default_order_type must be limit or market")
    if "trailing_stop_distance_pct" in risk:
        _require_finite_number(
            risk["trailing_stop_distance_pct"],
            path="risk.trailing_stop_distance_pct",
        )
        if not 0 < risk["trailing_stop_distance_pct"] <= 100:
            raise ValueError("risk.trailing_stop_distance_pct must be in (0, 100]")
    elif risk["trailing_stop_enabled"] is True:
        raise ValueError(
            "risk.trailing_stop_distance_pct is required when trailing stop is enabled"
        )
    return risk


def _validate_closed_strategy_catalog(
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    catalog = _require_exact_keys(
        policy,
        frozenset({"strategies"}),
        path="strategy",
    )
    rows = catalog["strategies"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("strategy.strategies must be a non-empty array")
    seen: set[str] = set()
    constraints: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        path = f"strategy.strategies[{index}]"
        row = _require_exact_keys(
            raw,
            frozenset({"id", "name", "enabled", "params"}),
            path=path,
        )
        strategy_id = row["id"]
        if not isinstance(strategy_id, str) or not strategy_id.strip():
            raise ValueError(f"{path}.id must be a non-empty string")
        if strategy_id in seen:
            raise ValueError(f"{path}.id is duplicated")
        seen.add(strategy_id)
        if strategy_id not in _STRATEGY_PARAMETER_CONTRACTS:
            raise ValueError(f"{path}.id has no registered semantic contract")
        if not isinstance(row["name"], str) or not row["name"].strip():
            raise ValueError(f"{path}.name must be a non-empty string")
        if not isinstance(row["enabled"], bool):
            raise ValueError(f"{path}.enabled must be boolean")
        parameter_contracts = _STRATEGY_PARAMETER_CONTRACTS[strategy_id]
        params = _require_exact_keys(
            row["params"],
            frozenset(parameter_contracts),
            path=f"{path}.params",
        )
        for parameter, contract in parameter_contracts.items():
            value = params[parameter]
            _require_finite_number(value, path=f"{path}.params.{parameter}")
            if parameter == "lookback":
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{path}.params.lookback must be a positive integer")
            elif parameter == "adx_min" and not 0 <= value <= 100:
                raise ValueError(f"{path}.params.adx_min must be between 0 and 100")
            elif parameter == "volume_spike_multiplier" and value <= 0:
                raise ValueError(f"{path}.params.volume_spike_multiplier must be positive")
            elif parameter == "rsi_threshold" and not 0 <= value <= 100:
                raise ValueError(f"{path}.params.rsi_threshold must be between 0 and 100")
            elif parameter == "bollinger_deviation" and value <= 0:
                raise ValueError(f"{path}.params.bollinger_deviation must be positive")
            if row["enabled"] is True:
                constraints.append({
                    "strategy_id": strategy_id,
                    "parameter": parameter,
                    "concept": contract["concept"],
                    "comparison": contract["comparison"],
                    "runtime_basis": contract["runtime_basis"],
                    "persisted_value": value,
                })
    return constraints


def _condition_assertions(
    value: Any,
    *,
    origin: str,
    mode: str = "allow",
    parent_enabled: bool = True,
    parent_logic: str | None = None,
    default_required: bool = False,
    block_logic: str | None = None,
    block_condition_count: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    if value.get("type") == "comparison":
        concepts = [
            raw
            for raw in (value.get("left"), value.get("right"))
            if isinstance(raw, str)
        ]
    else:
        # RuleEngine gives a literal ``field`` precedence over its ``indicator``
        # alias.  Preserve that single effective runtime lookup; never invent a
        # second assertion from an ignored key.
        raw_lookup = value.get("field")
        if not isinstance(raw_lookup, str):
            raw_lookup = value.get("indicator")
        concepts = [raw_lookup] if isinstance(raw_lookup, str) else []
    return [{
        "concept": _runtime_candidate_concept(raw_concept),
        "raw_concept": raw_concept,
        "condition_type": value.get("type"),
        "scalar_threshold_effective": value.get("type") != "comparison",
        "rule_id": value.get("id") or value.get("rule_id"),
        "operator": value.get("operator"),
        "value": deepcopy(value.get("value")),
        "min": deepcopy(value.get("min")),
        "max": deepcopy(value.get("max")),
        "period": deepcopy(value.get("period")),
        "timeframe": deepcopy(value.get("timeframe")),
        "enabled": parent_enabled and value.get("enabled", True) is not False,
        "required": value.get("required", default_required) is True,
        "required_basis": (
            "EXPLICIT_REQUIRED_TRUE"
            if value.get("required") is True
            else (
                "EXPLICIT_REQUIRED_FALSE"
                if value.get("required") is False
                else (
                    "FILTER_AND_MEMBERSHIP_IS_MANDATORY"
                    if default_required
                    else "RUNTIME_DEFAULT_REQUIRED_FALSE"
                )
            )
        ),
        "gate_logic": parent_logic,
        "mode": mode,
        "block_logic": block_logic,
        "block_condition_count": block_condition_count,
        "origin": origin,
    } for raw_concept in concepts]


def _condition_assertion(
    value: Any,
    **kwargs: Any,
) -> dict[str, Any] | None:
    assertions = _condition_assertions(value, **kwargs)
    return assertions[0] if assertions else None


def _condition_list_assertions(
    values: Any,
    *,
    origin: str,
    mode: str = "allow",
    parent_enabled: bool = True,
    parent_logic: str | None = None,
    default_required: bool = False,
    block_logic: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    assertions: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        assertions.extend(_condition_assertions(
            value,
            origin=f"{origin}[{index}]",
            mode=mode,
            parent_enabled=parent_enabled,
            parent_logic=parent_logic,
            default_required=default_required,
            block_logic=block_logic,
            block_condition_count=len(values) if mode == "block" else None,
        ))
    return assertions


def _score_rules_by_id(score_document: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not isinstance(score_document, dict):
        return {}
    rules = score_document.get("scoring_rules")
    if not isinstance(rules, list):
        return {}
    return {
        str(rule.get("id")): rule
        for rule in rules
        if isinstance(rule, dict) and isinstance(rule.get("id"), str)
    }


def _scoring_assertions(
    scoring: Any,
    score_document: dict[str, Any] | None,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    if not isinstance(scoring, dict):
        return []
    enabled = scoring.get("enabled", True) is not False
    assertions: list[dict[str, Any]] = []
    for key in ("rules", "generated_rules"):
        assertions.extend(_condition_list_assertions(
            scoring.get(key),
            origin=f"{origin}.{key}",
            mode="scoring",
            parent_enabled=enabled,
        ))
    rules_by_id = _score_rules_by_id(score_document)
    selected = scoring.get("selected_rule_ids")
    if isinstance(selected, list):
        for rule_id in selected:
            rule = rules_by_id.get(str(rule_id))
            assertion = _condition_assertion(
                rule,
                origin=f"{origin}.selected_rule_ids[{rule_id}]",
                mode="scoring",
                parent_enabled=enabled,
            )
            if assertion:
                assertions.append(assertion)
    return assertions


def _block_assertions(value: Any, *, origin: str) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    blocks = value.get("blocks")
    if not isinstance(blocks, list):
        return []
    assertions: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        block_origin = f"{origin}.blocks[{index}]"
        enabled = block.get("enabled", True) is not False
        if isinstance(block.get("conditions"), list):
            assertions.extend(_condition_list_assertions(
                block["conditions"],
                origin=f"{block_origin}.conditions",
                mode="block",
                parent_enabled=enabled,
                block_logic=str(block.get("logic") or ""),
            ))
            continue
        block_assertions = _condition_assertions(
            block,
            origin=block_origin,
            mode="block",
            parent_enabled=enabled,
            block_logic="OR",
            block_condition_count=1,
        )
        assertions.extend(block_assertions)
    return assertions


def _with_profile_runtime_gate_context(
    assertions: list[dict[str, Any]],
    config: dict[str, Any],
    root: str,
) -> list[dict[str, Any]]:
    entry = config.get("entry_triggers")
    entry_conditions = (
        entry.get("conditions")
        if isinstance(entry, dict) and isinstance(entry.get("conditions"), list)
        else []
    )
    signals_shadowed = root == "signals" and bool(entry_conditions)
    signals_operator_not_propagated = root == "signals" and any(
        assertion.get("operator") == "between"
        or assertion.get("condition_type") == "comparison"
        for assertion in assertions
    )
    default_timeframe = config.get("default_timeframe")
    return [{
        **assertion,
        "runtime_gate_effective": not (
            signals_shadowed or signals_operator_not_propagated
        ),
        "runtime_gate_basis": (
            "SIGNALS_SHADOWED_BY_ENTRY_TRIGGERS"
            if signals_shadowed
            else (
                "SIGNALS_BETWEEN_OR_COMPARISON_NOT_PROPAGATED"
                if signals_operator_not_propagated
                else "EFFECTIVE_PROFILE_GATE_SECTION"
            )
        ),
        "profile_default_timeframe": deepcopy(default_timeframe),
        "effective_timeframe": (
            assertion.get("timeframe")
            if assertion.get("timeframe") is not None
            else deepcopy(default_timeframe)
        ),
        "effective_timeframe_basis": (
            "CONDITION_EXPLICIT_TIMEFRAME"
            if assertion.get("timeframe") is not None
            else "PROFILE_DEFAULT_TIMEFRAME"
        ),
    } for assertion in assertions]


def _profile_root_assertions(
    config: dict[str, Any],
    parts: list[str | int],
    score_document: dict[str, Any] | None,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    if not parts or not isinstance(parts[0], str):
        return []
    root = parts[0]
    value = config.get(root)
    if root in {"filters", "signals", "entry_triggers"}:
        if not isinstance(value, dict):
            return []
        if len(parts) >= 3 and parts[1] == "conditions" and isinstance(parts[2], int):
            conditions = value.get("conditions")
            if not isinstance(conditions, list) or parts[2] >= len(conditions):
                return []
            assertions = _condition_assertions(
                conditions[parts[2]],
                origin=f"{origin}.{root}.conditions[{parts[2]}]",
                parent_logic=str(value.get("logic") or ""),
                default_required=root == "filters",
            )
            return _with_profile_runtime_gate_context(assertions, config, root)
        if len(parts) >= 2 and parts[1] == "scoring":
            if len(parts) >= 3 and parts[2] in {"weights"}:
                return []
            assertions = _scoring_assertions(
                value.get("scoring"),
                score_document,
                origin=f"{origin}.{root}.scoring",
            )
            return _with_profile_runtime_gate_context(assertions, config, root)
        assertions = _condition_list_assertions(
            value.get("conditions"),
            origin=f"{origin}.{root}.conditions",
            parent_logic=str(value.get("logic") or ""),
            default_required=root == "filters",
        )
        if root in {"signals", "entry_triggers"}:
            assertions.extend(_scoring_assertions(
                value.get("scoring"),
                score_document,
                origin=f"{origin}.{root}.scoring",
            ))
        return _with_profile_runtime_gate_context(assertions, config, root)
    if root == "block_rules":
        if (
            len(parts) >= 3
            and parts[1] == "blocks"
            and isinstance(parts[2], int)
            and isinstance(value, dict)
            and isinstance(value.get("blocks"), list)
            and parts[2] < len(value["blocks"])
        ):
            assertions = _block_assertions(
                {"blocks": [value["blocks"][parts[2]]]},
                origin=f"{origin}.block_rules",
            )
            return _with_profile_runtime_gate_context(
                assertions,
                config,
                root,
            )
        assertions = _block_assertions(value, origin=f"{origin}.block_rules")
        return _with_profile_runtime_gate_context(assertions, config, root)
    if root == "scoring":
        if not isinstance(value, dict):
            return []
        if len(parts) >= 2 and parts[1] in {"weights", "thresholds"}:
            return []
        if (
            len(parts) >= 3
            and parts[1] in {"rules", "generated_rules"}
            and isinstance(parts[2], int)
        ):
            rules = value.get(parts[1])
            if not isinstance(rules, list) or parts[2] >= len(rules):
                return []
            assertions = _condition_assertions(
                rules[parts[2]],
                origin=f"{origin}.scoring.{parts[1]}[{parts[2]}]",
                mode="scoring",
                parent_enabled=value.get("enabled", True) is not False,
            )
            return _with_profile_runtime_gate_context(assertions, config, root)
        assertions = _scoring_assertions(
            value,
            score_document,
            origin=f"{origin}.scoring",
        )
        return _with_profile_runtime_gate_context(assertions, config, root)
    return []


def _all_profile_assertions(
    config: dict[str, Any],
    score_document: dict[str, Any] | None,
    *,
    origin: str,
) -> list[dict[str, Any]]:
    assertions: list[dict[str, Any]] = []
    for root in ("filters", "signals", "entry_triggers", "block_rules", "scoring"):
        assertions.extend(_profile_root_assertions(
            config,
            [root],
            score_document,
            origin=origin,
        ))
    return assertions


def _bulk_profile_documents(document: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(document, dict) or not isinstance(document.get("profiles"), list):
        return {}
    return {
        str(row.get("profile_id")): row["config"]
        for row in document["profiles"]
        if isinstance(row, dict)
        and isinstance(row.get("profile_id"), (str, UUID))
        and isinstance(row.get("config"), dict)
    }


def _score_path_assertions(
    score_document: dict[str, Any],
    parts: list[str | int],
    *,
    origin: str,
) -> list[dict[str, Any]]:
    if not parts or parts[0] != "scoring_rules":
        return []
    rules = score_document.get("scoring_rules")
    if not isinstance(rules, list):
        return []
    if len(parts) >= 2 and isinstance(parts[1], int):
        if parts[1] >= len(rules):
            return []
        return _condition_assertions(
            rules[parts[1]],
            origin=f"{origin}.scoring_rules[{parts[1]}]",
            mode="scoring",
        )
    return _condition_list_assertions(
        rules,
        origin=f"{origin}.scoring_rules",
        mode="scoring",
    )


def _explicit_profile_strategy_binding(profile: Profile) -> dict[str, Any]:
    config = profile.config if isinstance(profile.config, dict) else {}
    metadata = config.get("metadata") if isinstance(config.get("metadata"), dict) else {}
    candidates = {
        "name": getattr(profile, "name", None),
        "pipeline_label": getattr(profile, "pipeline_label", None),
        "metadata.profile_family": metadata.get("profile_family"),
    }
    matches: dict[str, list[str]] = {}
    for source, raw in candidates.items():
        normalized = _normalize_semantic_concept(raw)
        if normalized in _STRATEGY_PARAMETER_CONTRACTS:
            matches.setdefault(normalized, []).append(source)
    return {
        "profile_id": str(profile.id),
        "name": getattr(profile, "name", None),
        "profile_role": getattr(profile, "profile_role", None),
        "pipeline_label": getattr(profile, "pipeline_label", None),
        "generated_by": getattr(profile, "generated_by", None),
        "metadata_profile_family": metadata.get("profile_family"),
        "strategy_ids": sorted(matches),
        "binding_sources": {
            strategy_id: sources
            for strategy_id, sources in sorted(matches.items())
        },
        "binding_basis": (
            "EXACT_PERSISTED_PROFILE_IDENTITY_MATCH"
            if matches
            else "NO_EXACT_PERSISTED_PROFILE_TO_STRATEGY_ID_MATCH"
        ),
    }


def _profile_selected_score_rule_ids(config: Any) -> set[str]:
    if not isinstance(config, dict):
        return set()
    selected: set[str] = set()
    for scoring in (
        config.get("scoring"),
        (config.get("signals") or {}).get("scoring")
        if isinstance(config.get("signals"), dict) else None,
        (config.get("entry_triggers") or {}).get("scoring")
        if isinstance(config.get("entry_triggers"), dict) else None,
    ):
        if isinstance(scoring, dict) and isinstance(scoring.get("selected_rule_ids"), list):
            selected.update(str(item) for item in scoring["selected_rule_ids"])
    filters = config.get("filters")
    if isinstance(filters, dict) and isinstance(filters.get("conditions"), list):
        selected.update(
            str(condition["rule_id"])
            for condition in filters["conditions"]
            if isinstance(condition, dict) and condition.get("rule_id")
        )
    return selected


def _assertions_for_materialized_change(
    assertions: list[dict[str, Any]],
    change: dict[str, Any],
    parts: list[str | int],
) -> list[dict[str, Any]]:
    """Bind every semantic assertion to the exact reviewed diff leaf.

    A whole-condition snapshot is useful evidence, but it is not proof that a
    structural edit preserved a hard floor.  Only a materialized replacement
    of ``value``/``min``/``max`` (or an explicitly modelled ``threshold``
    leaf) can be considered.  The active-floor validator narrows this further
    to value/min; max is admitted only for a proven monotonic subset.
    """
    leaf = parts[-1] if parts and isinstance(parts[-1], str) else None
    direct_threshold_change = (
        change.get("op") == "replace"
        and leaf in {"value", "min", "max", "threshold"}
    )
    return [{
        **assertion,
        "change_path": str(change.get("path") or ""),
        "change_op": change.get("op"),
        "changed_leaf": leaf,
        "direct_threshold_change": direct_threshold_change,
    } for assertion in assertions]


def _profile_score_semantic_scope(
    plan: CopilotActionPlan,
    profiles: list[Profile],
    score_document: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    source = payload.get("source_document")
    candidate = payload.get("candidate_document")
    source_assertions: list[dict[str, Any]] = []
    candidate_assertions: list[dict[str, Any]] = []
    changed_paths: list[str] = []
    materialized_change_scope: list[dict[str, Any]] = []
    source_bulk = _bulk_profile_documents(source)
    candidate_bulk = _bulk_profile_documents(candidate)
    profile_by_id = {str(profile.id): profile for profile in profiles}
    relevant_profile_ids: set[str] = set()

    for change in list(plan.proposed_diff or []):
        path = str(change.get("path") or "")
        changed_paths.append(path)
        parts = _decode_pointer(path)
        local_parts = parts
        source_for_change: list[dict[str, Any]] = []
        candidate_for_change: list[dict[str, Any]] = []
        if operation == "UPDATE_PROFILE_CONFIG":
            target_profile_id = str(payload.get("profile_id") or plan.target_id or "")
            if target_profile_id:
                relevant_profile_ids.add(target_profile_id)
            if isinstance(source, dict):
                source_for_change = _profile_root_assertions(
                    source, parts, score_document, origin="source.profile",
                )
            if isinstance(candidate, dict):
                candidate_for_change = _profile_root_assertions(
                    candidate, parts, score_document, origin="candidate.profile",
                )
        elif operation == "UPDATE_PROFILE_CONFIG_SET":
            if (
                len(parts) >= 3
                and parts[0] == "profiles"
            ):
                profile_id = str(parts[1])
                relevant_profile_ids.add(profile_id)
                local_parts = parts[2:]
                if profile_id in source_bulk:
                    source_for_change = _profile_root_assertions(
                        source_bulk[profile_id],
                        local_parts,
                        score_document,
                        origin=f"source.profile[{profile_id}]",
                    )
                if profile_id in candidate_bulk:
                    candidate_for_change = _profile_root_assertions(
                        candidate_bulk[profile_id],
                        local_parts,
                        score_document,
                        origin=f"candidate.profile[{profile_id}]",
                    )
        elif operation == "SET_PROFILE_ACTIVE_STATUS":
            if len(parts) == 3 and parts[0] == "profiles" and parts[2] == "is_active":
                profile_id = str(parts[1])
                relevant_profile_ids.add(profile_id)
                if change.get("value") is True and profile_id in profile_by_id:
                    assertions = _all_profile_assertions(
                        profile_by_id[profile_id].config or {},
                        score_document,
                        origin=f"candidate.activation[{profile_id}]",
                    )
                    source_for_change = assertions
                    candidate_for_change = assertions
        elif operation == "UPDATE_CONFIG_PROFILE" and payload.get("config_type") == "score":
            if isinstance(source, dict):
                source_for_change = _score_path_assertions(
                    source, parts, origin="source.score",
                )
            if isinstance(candidate, dict):
                candidate_for_change = _score_path_assertions(
                    candidate, parts, origin="candidate.score",
                )

        annotated_source = _assertions_for_materialized_change(
            source_for_change,
            change,
            local_parts,
        )
        annotated_candidate = _assertions_for_materialized_change(
            candidate_for_change,
            change,
            local_parts,
        )
        source_assertions.extend(annotated_source)
        candidate_assertions.extend(annotated_candidate)
        materialized_change_scope.append({
            "path": path,
            "op": change.get("op"),
            "changed_leaf": (
                local_parts[-1]
                if local_parts and isinstance(local_parts[-1], str)
                else None
            ),
            "old_value": deepcopy(change.get("old_value")),
            "value": deepcopy(change.get("value")),
            "candidate_assertion_count": len(annotated_candidate),
            "candidate_concepts": sorted({
                assertion["concept"]
                for assertion in annotated_candidate
                if assertion.get("concept")
            }),
            "direct_threshold_change": bool(
                annotated_candidate
                and all(
                    assertion.get("direct_threshold_change") is True
                    for assertion in annotated_candidate
                )
            ),
        })

    touched = sorted({
        assertion["concept"]
        for assertion in source_assertions + candidate_assertions
        if assertion.get("concept")
    })
    if operation == "UPDATE_CONFIG_PROFILE" and payload.get("config_type") == "score":
        touched_rule_ids = {
            str(assertion["rule_id"])
            for assertion in source_assertions + candidate_assertions
            if assertion.get("rule_id")
        }
        for profile in profiles:
            if not bool(getattr(profile, "is_active", False)):
                continue
            selected = _profile_selected_score_rule_ids(profile.config or {})
            # The runtime resolves the complete global rule set when a Profile
            # has no explicit selection or matching filter rule.
            if not selected or selected.intersection(touched_rule_ids):
                relevant_profile_ids.add(str(profile.id))
    bindings = [
        _explicit_profile_strategy_binding(profile_by_id[profile_id])
        for profile_id in sorted(relevant_profile_ids)
        if profile_id in profile_by_id
    ]
    return {
        "operation": operation,
        "changed_paths": changed_paths,
        "touched_concepts": touched,
        "source_assertions": source_assertions,
        "candidate_assertions": candidate_assertions,
        "materialized_change_scope": materialized_change_scope,
        "profile_strategy_bindings": bindings,
        "profile_strategy_bindings_hash": document_hash(bindings),
    }


def _validate_profile_score_risk_authority(
    plan: CopilotActionPlan,
    risk_policy: dict[str, Any],
    scope: dict[str, Any],
) -> dict[str, Any]:
    risk = _validate_closed_risk_policy(risk_policy)
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    changed_paths = list(scope.get("changed_paths") or [])
    for path in changed_paths:
        parts = _decode_pointer(path)
        if operation == "UPDATE_PROFILE_CONFIG":
            allowed = bool(parts and parts[0] in PROFILE_ROOTS)
        elif operation == "UPDATE_PROFILE_CONFIG_SET":
            allowed = (
                len(parts) >= 3
                and parts[0] == "profiles"
                and parts[2] in PROFILE_ROOTS
            )
        elif operation == "SET_PROFILE_ACTIVE_STATUS":
            allowed = len(parts) == 3 and parts[0] == "profiles" and parts[2] == "is_active"
        elif operation == "UPDATE_CONFIG_PROFILE" and payload.get("config_type") == "score":
            allowed = bool(parts and parts[0] in {
                "weights", "scoring_rules", "thresholds",
                "auto_select_top_n", "auto_select_min_score",
            })
        else:
            allowed = False
        if not allowed:
            raise ValueError(f"Governed path is outside Profile/Score authority: {path}")
    protected = sorted({
        assertion["concept"]
        for assertion in (
            list(scope.get("source_assertions") or [])
            + list(scope.get("candidate_assertions") or [])
        )
        if _is_protected_risk_concept(assertion.get("concept"))
    })
    if protected:
        raise ValueError(
            "Profile/Score diff references protected risk concepts: "
            + ", ".join(protected)
        )
    downstream_caps = {
        key: deepcopy(risk[key])
        for key in _RISK_DOWNSTREAM_CAP_KEYS
        if key in risk
    }
    return {
        "authority_scope": "PROFILE_SCORE_SIGNAL_ONLY",
        "changed_paths": changed_paths,
        "touched_concepts": list(scope.get("touched_concepts") or []),
        "protected_domains_checked": sorted(_PROTECTED_RISK_CONCEPTS),
        "downstream_caps": downstream_caps,
        "downstream_caps_hash": document_hash(downstream_caps),
    }


def _minimum_assertion_is_compatible(
    assertion: dict[str, Any],
    minimum: Any,
) -> tuple[bool, str]:
    _require_finite_number(minimum, path="strategy.persisted_minimum")
    if assertion.get("enabled") is not True:
        return False, "CANDIDATE_ASSERTION_DISABLED"
    operator = assertion.get("operator")
    mode = assertion.get("mode")
    if mode == "block":
        if (
            assertion.get("block_logic") == "AND"
            and (assertion.get("block_condition_count") or 0) > 1
        ):
            return False, "AND_BLOCK_DOES_NOT_INDEPENDENTLY_ENFORCE_MINIMUM"
        value = assertion.get("value")
        if operator not in {"<", "<="}:
            return False, "BLOCK_OPERATOR_DOES_NOT_ENFORCE_MINIMUM"
        try:
            _require_finite_number(value, path="candidate.block.value")
        except ValueError:
            return False, "CANDIDATE_THRESHOLD_IS_NOT_NUMERIC"
        return (
            (value >= minimum, "COMPATIBLE_BLOCK_MINIMUM" if value >= minimum else "BELOW_PERSISTED_MINIMUM")
        )
    if operator in {">", ">=", "=", "=="}:
        value = assertion.get("value")
        try:
            _require_finite_number(value, path="candidate.value")
        except ValueError:
            return False, "CANDIDATE_THRESHOLD_IS_NOT_NUMERIC"
        return (
            (value >= minimum, "COMPATIBLE_MINIMUM" if value >= minimum else "BELOW_PERSISTED_MINIMUM")
        )
    if operator == "between":
        lower = assertion.get("min")
        try:
            _require_finite_number(lower, path="candidate.min")
        except ValueError:
            return False, "CANDIDATE_LOWER_BOUND_IS_NOT_NUMERIC"
        return (
            (lower >= minimum, "COMPATIBLE_LOWER_BOUND" if lower >= minimum else "BELOW_PERSISTED_MINIMUM")
        )
    return False, "CANDIDATE_OPERATOR_DOES_NOT_ENFORCE_MINIMUM"


def _monotonic_eligibility_restriction(
    candidate: dict[str, Any],
    source_assertions: list[dict[str, Any]],
    materialized_change_scope: list[dict[str, Any]],
) -> tuple[bool, str, dict[str, Any] | None]:
    """Prove that one no-floor edit can only reduce runtime eligibility."""
    if len(materialized_change_scope) != 1:
        return False, "MONOTONIC_PROOF_REQUIRES_A_SINGLE_NUMERIC_REPLACE", None
    change = materialized_change_scope[0]
    if (
        change.get("op") != "replace"
        or change.get("changed_leaf") not in {"value", "min", "max"}
    ):
        return False, "MONOTONIC_PROOF_REQUIRES_A_SINGLE_NUMERIC_REPLACE", None
    try:
        _require_finite_number(change.get("old_value"), path="source.threshold")
        _require_finite_number(change.get("value"), path="candidate.threshold")
    except ValueError:
        return False, "MONOTONIC_PROOF_REQUIRES_A_SINGLE_NUMERIC_REPLACE", None

    matching_sources = [
        source
        for source in source_assertions
        if source.get("change_path") == candidate.get("change_path")
        and source.get("raw_concept") == candidate.get("raw_concept")
        and source.get("rule_id") == candidate.get("rule_id")
    ]
    if len(matching_sources) != 1:
        return False, "SOURCE_ASSERTION_IDENTITY_IS_NOT_UNIQUE", None
    source = matching_sources[0]
    identity_fields = (
        "concept",
        "raw_concept",
        "rule_id",
        "operator",
        "period",
        "timeframe",
        "effective_timeframe",
        "mode",
        "gate_logic",
        "required",
        "enabled",
        "runtime_gate_effective",
    )
    if any(source.get(field) != candidate.get(field) for field in identity_fields):
        return False, "SOURCE_CANDIDATE_ASSERTION_IDENTITY_OR_CONTEXT_CHANGED", source
    if candidate.get("raw_concept") not in _PROVEN_PROFILE_RUNTIME_FIELDS:
        return False, "LITERAL_RUNTIME_FIELD_IS_NOT_PROVEN", source
    if candidate.get("runtime_gate_effective") is not True:
        return False, str(
            candidate.get("runtime_gate_basis") or "RUNTIME_GATE_SECTION_NOT_EFFECTIVE"
        ), source
    if candidate.get("mode") != "allow":
        return False, "ADDITIVE_SCORING_OR_BLOCK_IS_NOT_A_HARD_GATE", source
    if candidate.get("gate_logic") != "AND":
        return False, "ASSERTION_IS_NOT_UNDER_MANDATORY_AND_LOGIC", source
    if candidate.get("required") is not True or source.get("required") is not True:
        return False, "ASSERTION_IS_OPTIONAL", source
    origin = str(candidate.get("origin") or "")
    if (
        ".signals." in origin or ".entry_triggers." in origin
    ) and candidate.get("required_basis") != "EXPLICIT_REQUIRED_TRUE":
        return False, "SIGNAL_OR_ENTRY_REQUIRES_EXPLICIT_REQUIRED_TRUE", source
    if candidate.get("enabled") is not True or source.get("enabled") is not True:
        return False, "ASSERTION_IS_DISABLED", source

    operator = candidate.get("operator")
    leaf = change.get("changed_leaf")
    if operator in {">", ">="} and leaf == "value":
        compatible = candidate.get("value") >= source.get("value")
    elif operator in {"<", "<="} and leaf == "value":
        compatible = candidate.get("value") <= source.get("value")
    elif operator == "between" and leaf == "min":
        compatible = candidate.get("min") >= source.get("min")
    elif operator == "between" and leaf == "max":
        compatible = candidate.get("max") <= source.get("max")
    else:
        return False, "OPERATOR_HAS_NO_PROVEN_MONOTONIC_SUBSET_RULE", source
    return (
        compatible,
        (
            "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
            if compatible
            else "CANDIDATE_LOOSENS_ELIGIBILITY"
        ),
        source,
    )


def _validate_profile_score_strategy_semantics(
    strategy_policy: dict[str, Any],
    scope: dict[str, Any],
) -> dict[str, Any]:
    constraints = _validate_closed_strategy_catalog(strategy_policy)
    touched = set(scope.get("touched_concepts") or [])
    source_assertions = list(scope.get("source_assertions") or [])
    candidate_assertions = list(scope.get("candidate_assertions") or [])
    bindings = list(scope.get("profile_strategy_bindings") or [])
    materialized_change_scope = list(scope.get("materialized_change_scope") or [])
    comparisons: list[dict[str, Any]] = []
    scoped_out: list[dict[str, Any]] = []
    vetoes: list[str] = []

    if scope.get("operation") == "SET_PROFILE_ACTIVE_STATUS":
        pure_deactivation = bool(materialized_change_scope) and all(
            change.get("op") == "replace"
            and change.get("changed_leaf") == "is_active"
            and change.get("old_value") is True
            and change.get("value") is False
            for change in materialized_change_scope
        )
        basis = (
            "PROFILE_DEACTIVATION_NOT_GLOBALLY_ENFORCED_BY_WATCHLIST_CONSUMERS"
            if pure_deactivation
            else "PROFILE_ACTIVATION_OR_MIXED_STATUS_CHANGE_NOT_PROVEN"
        )
        return {
            "touched_concepts": sorted(touched),
            "active_constraints": constraints,
            "comparisons": [],
            "scoped_out_constraints": [
                {**constraint, "scope_reason": basis}
                for constraint in constraints
            ],
            "unconstrained_touched_concepts": [],
            "materialized_change_scope": materialized_change_scope,
            "profile_strategy_bindings": bindings,
            "profile_strategy_bindings_hash": scope.get(
                "profile_strategy_bindings_hash"
            ),
            "binding_authority": "EVIDENCE_ONLY_NOT_AUTHORIZATION",
            "vetoes": [basis],
            "validation_mode": basis,
            "scope_basis": basis,
        }

    if not materialized_change_scope:
        vetoes.append("No materialized Profile/Score diff is available for semantic proof")
    for change_scope in materialized_change_scope:
        path = str(change_scope.get("path") or "")
        if not change_scope.get("candidate_assertion_count"):
            vetoes.append(
                f"Changed path {path} has no proven hard-floor semantic mapping"
            )
        elif change_scope.get("direct_threshold_change") is not True:
            vetoes.append(
                f"Changed path {path} is structural; only a direct threshold/value/min/max "
                "replacement can prove conservative floor compatibility"
            )

    floors_by_concept: dict[str, list[dict[str, Any]]] = {}
    unmapped_by_concept: dict[str, list[dict[str, Any]]] = {}
    for constraint in constraints:
        concept = str(constraint.get("concept") or "")
        if concept not in touched:
            scoped_out.append({
                **constraint,
                "scope_reason": (
                    "LOOKBACK_HAS_NO_PROVEN_PROFILE_SCORE_RUNTIME_MAPPING"
                    if constraint["parameter"] == "lookback"
                    else "CONCEPT_NOT_TOUCHED_BY_MATERIALIZED_DIFF"
                ),
            })
            continue
        if constraint.get("comparison") == "minimum":
            floors_by_concept.setdefault(concept, []).append(constraint)
        else:
            unmapped_by_concept.setdefault(concept, []).append(constraint)

    proven_concepts: set[str] = set()
    proof_modes: set[str] = set()
    for assertion in candidate_assertions:
        concept = str(assertion.get("concept") or "")
        assertion_vetoes: list[str] = []
        if assertion.get("direct_threshold_change") is not True:
            assertion_vetoes.append("NOT_A_DIRECT_THRESHOLD_VALUE_MIN_OR_MAX_REPLACEMENT")
        if assertion.get("mode") != "allow":
            assertion_vetoes.append("ADDITIVE_SCORING_OR_BLOCK_IS_NOT_A_HARD_FLOOR")
        if assertion.get("gate_logic") != "AND":
            assertion_vetoes.append("ASSERTION_IS_NOT_UNDER_MANDATORY_AND_LOGIC")
        if assertion.get("required") is not True:
            assertion_vetoes.append("ASSERTION_IS_OPTIONAL")
        if assertion.get("enabled") is not True:
            assertion_vetoes.append("ASSERTION_IS_DISABLED")
        if assertion.get("runtime_gate_effective") is not True:
            assertion_vetoes.append(str(
                assertion.get("runtime_gate_basis")
                or "RUNTIME_GATE_SECTION_NOT_EFFECTIVE"
            ))
        if assertion.get("scalar_threshold_effective") is not True:
            assertion_vetoes.append(
                "COMPARISON_SCALAR_THRESHOLD_NOT_CONSUMED"
            )

        applicable_floors = floors_by_concept.get(concept) or []
        unmapped_constraints = unmapped_by_concept.get(concept) or []
        if unmapped_constraints:
            assertion_vetoes.append("ACTIVE_PARAMETER_DIRECTION_OR_RUNTIME_MAPPING_NOT_PROVEN")

        effective_floor = None
        compatible = False
        compatibility_reason = "POLICY_SEMANTIC_PROOF_NOT_EVALUATED"
        source_assertion: dict[str, Any] | None = None
        proof_mode: str | None = None
        if applicable_floors:
            proof_mode = "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
            effective_floor = max(
                constraint["persisted_value"]
                for constraint in applicable_floors
            )
            if assertion.get("raw_concept") not in _PROVEN_PROFILE_RUNTIME_FIELDS:
                assertion_vetoes.append("LITERAL_RUNTIME_FIELD_IS_NOT_PROVEN")
            if assertion.get("changed_leaf") not in {"value", "min", "threshold"}:
                assertion_vetoes.append("ACTIVE_FLOOR_REQUIRES_VALUE_OR_MIN_REPLACEMENT")
            if not assertion_vetoes:
                compatible, compatibility_reason = _minimum_assertion_is_compatible(
                    assertion,
                    effective_floor,
                )
                if not compatible:
                    assertion_vetoes.append(compatibility_reason)
        elif not unmapped_constraints:
            proof_mode = "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
            compatible, compatibility_reason, source_assertion = (
                _monotonic_eligibility_restriction(
                    assertion,
                    source_assertions,
                    materialized_change_scope,
                )
            )
            if not compatible:
                assertion_vetoes.append(compatibility_reason)

        decision = "VETO" if assertion_vetoes else "PASS"
        if decision == "PASS":
            proven_concepts.add(concept)
            if proof_mode:
                proof_modes.add(proof_mode)
        else:
            vetoes.append(
                f"Candidate {concept or '<unknown>'} assertion at "
                f"{assertion.get('origin')} is not a proven conservative global floor: "
                + ", ".join(assertion_vetoes)
            )
        comparisons.append({
            "concept": concept,
            "candidate_assertion": deepcopy(assertion),
            "source_assertion": deepcopy(source_assertion),
            "active_floor_constraints": deepcopy(applicable_floors),
            "unmapped_active_constraints": deepcopy(unmapped_constraints),
            "effective_global_minimum": effective_floor,
            "floor_scope": (
                "GLOBAL_ALL_TIMEFRAMES_PERIODS"
                if applicable_floors
                else None
            ),
            "proof_mode": proof_mode,
            "decision": decision,
            "reason": (
                compatibility_reason
                if decision == "PASS"
                else ", ".join(assertion_vetoes)
            ),
        })

    unconstrained_touched = sorted(touched - proven_concepts)
    for concept in sorted(touched):
        if not any(
            assertion.get("concept") == concept
            for assertion in candidate_assertions
        ):
            vetoes.append(
                f"Candidate removes or bypasses the touched {concept} assertion"
            )
    if not vetoes and proof_modes == {
        "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
    }:
        scope_basis = "NO_ACTIVE_FLOOR_MONOTONIC_ELIGIBILITY_RESTRICTION"
    else:
        scope_basis = "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
    return {
        "touched_concepts": sorted(touched),
        "active_constraints": constraints,
        "comparisons": comparisons,
        "scoped_out_constraints": scoped_out,
        "unconstrained_touched_concepts": unconstrained_touched,
        "materialized_change_scope": materialized_change_scope,
        "profile_strategy_bindings": bindings,
        "profile_strategy_bindings_hash": scope.get("profile_strategy_bindings_hash"),
        "binding_authority": "EVIDENCE_ONLY_NOT_AUTHORIZATION",
        "vetoes": vetoes,
        "floor_scope": "GLOBAL_ALL_TIMEFRAMES_PERIODS",
        "validation_mode": scope_basis,
        "scope_basis": scope_basis,
    }


def _validate_spot_candidate_against_global_risk(
    candidate: dict[str, Any],
    risk_policy: dict[str, Any],
) -> None:
    """Apply only cross-document limits with identical runtime meaning.

    These fields are consumed with the same units by ``RiskEngine`` and
    ``SpotCapitalManager``.  No absent limit is defaulted: an incomplete risk
    policy cannot authorize a governed Spot mutation.
    """
    buying = candidate.get("buying")
    if not isinstance(buying, dict):
        raise ValueError("Spot candidate lacks its buying policy section")

    numeric_limits = (
        ("capital_per_trade_pct", buying.get("capital_per_trade_pct")),
        ("max_capital_in_use_pct", buying.get("max_capital_in_use_pct")),
        ("max_exposure_per_asset_pct", buying.get("max_exposure_per_asset_pct")),
        ("max_positions", buying.get("max_positions_total")),
    )
    for policy_key, candidate_value in numeric_limits:
        policy_value = risk_policy.get(policy_key)
        _require_finite_number(policy_value, path=f"risk.{policy_key}")
        _require_finite_number(candidate_value, path=f"spot.{policy_key}")
        if candidate_value > policy_value:
            raise ValueError(
                f"Spot {policy_key} exceeds the persisted global risk limit"
            )

    risk_order_type = risk_policy.get("default_order_type")
    if risk_order_type not in {"limit", "market"}:
        raise ValueError("risk.default_order_type is missing or invalid")
    if buying.get("order_type") != risk_order_type:
        raise ValueError(
            "Spot buying.order_type differs from persisted risk.default_order_type"
        )


def _policy_semantic_result(
    plan: CopilotActionPlan,
    effective: dict[str, tuple[ConfigProfile, dict[str, Any], bool]],
    profiles: list[Profile],
) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Return honest policy-semantic labels and their deterministic basis.

    ``PASS`` is emitted only after an executable candidate-vs-policy check.
    Resource separation is not policy approval: when the persisted policy does
    not expose a deterministic mapping for the candidate, ``NOT_PERFORMED`` is
    fail-closed and never grants write authority.
    """
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    config_type = str(payload.get("config_type") or "")

    is_profile_score_operation = (
        operation in {
            "UPDATE_PROFILE_CONFIG",
            "UPDATE_PROFILE_CONFIG_SET",
            "SET_PROFILE_ACTIVE_STATUS",
        }
        or (operation == "UPDATE_CONFIG_PROFILE" and config_type == "score")
    )
    if is_profile_score_operation:
        score_row = effective.get("score")
        score_document = score_row[1] if score_row else None
        try:
            scope = _profile_score_semantic_scope(plan, profiles, score_document)
        except (GovernedChangePathError, TypeError, ValueError) as exc:
            reason = f"PROFILE_SCORE_SEMANTIC_SCOPE_INVALID: {str(exc)[:500]}"
            statuses = {"risk": "VETO", "strategy": "VETO"}
            evidence = {
                family: {
                    "status": "VETO",
                    "basis": reason,
                    "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                }
                for family in ("risk", "strategy")
            }
            return statuses, evidence

        risk_row = effective.get("risk")
        if risk_row is None:
            risk_status = "NOT_PERFORMED"
            risk_evidence: dict[str, Any] = {
                "status": risk_status,
                "basis": "PERSISTED_RISK_POLICY_UNAVAILABLE",
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
            }
        else:
            risk_record, risk_document, _risk_is_target = risk_row
            try:
                authority = _validate_profile_score_risk_authority(
                    plan,
                    risk_document,
                    scope,
                )
            except (GovernedChangePathError, TypeError, ValueError) as exc:
                risk_status = "VETO"
                risk_evidence = {
                    "status": risk_status,
                    "basis": str(exc)[:500],
                    "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                    "policy_config_id": str(risk_record.id),
                    "policy_document_hash": document_hash(risk_document),
                }
            else:
                risk_status = "PASS"
                risk_evidence = {
                    "status": risk_status,
                    "basis": "PROFILE_SCORE_AUTHORITY_AND_DOWNSTREAM_RISK_CAPS_PROVEN",
                    "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                    "policy_config_id": str(risk_record.id),
                    "policy_document_hash": document_hash(risk_document),
                    **authority,
                }

        strategy_row = effective.get("strategy")
        if strategy_row is None:
            strategy_status = "NOT_PERFORMED"
            strategy_evidence: dict[str, Any] = {
                "status": strategy_status,
                "basis": "PERSISTED_STRATEGY_POLICY_UNAVAILABLE",
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
            }
        else:
            strategy_record, strategy_document, _strategy_is_target = strategy_row
            try:
                comparison_evidence = _validate_profile_score_strategy_semantics(
                    strategy_document,
                    scope,
                )
            except (GovernedChangePathError, TypeError, ValueError) as exc:
                strategy_status = "VETO"
                strategy_evidence = {
                    "status": strategy_status,
                    "basis": str(exc)[:500],
                    "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                    "policy_config_id": str(strategy_record.id),
                    "policy_document_hash": document_hash(strategy_document),
                    "touched_concepts": list(scope.get("touched_concepts") or []),
                }
            else:
                vetoes = list(comparison_evidence.get("vetoes") or [])
                strategy_status = "VETO" if vetoes else "PASS"
                strategy_evidence = {
                    "status": strategy_status,
                    "basis": (
                        "; ".join(vetoes[:8])
                        if vetoes
                        else str(
                            comparison_evidence.get("scope_basis")
                            or "CONSERVATIVE_GLOBAL_FLOOR_COMPATIBILITY"
                        )
                    ),
                    "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                    "policy_config_id": str(strategy_record.id),
                    "policy_document_hash": document_hash(strategy_document),
                    **comparison_evidence,
                }
        return (
            {"risk": risk_status, "strategy": strategy_status},
            {"risk": risk_evidence, "strategy": strategy_evidence},
        )

    if operation == "UPDATE_CONFIG_PROFILE" and config_type in {"risk", "strategy"}:
        risk_row = effective.get("risk")
        strategy_row = effective.get("strategy")
        statuses: dict[str, str] = {}
        evidence: dict[str, dict[str, Any]] = {}

        if risk_row is None:
            statuses["risk"] = "NOT_PERFORMED"
            evidence["risk"] = {
                "status": "NOT_PERFORMED",
                "basis": "PERSISTED_RISK_POLICY_UNAVAILABLE",
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
            }
        else:
            risk_record, risk_document, risk_is_target = risk_row
            try:
                _validate_closed_risk_policy(risk_document)
            except (TypeError, ValueError) as exc:
                statuses["risk"] = "VETO"
                basis = str(exc)[:500]
            else:
                statuses["risk"] = "PASS"
                basis = "RISK_POLICY_COMPLETE_SCHEMA_AND_FIELD_BOUNDS_PROVEN"
            evidence["risk"] = {
                "status": statuses["risk"],
                "basis": basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                "policy_config_id": str(risk_record.id),
                "policy_document_hash": document_hash(risk_document),
                "uses_candidate_document": risk_is_target,
            }

        if strategy_row is None:
            statuses["strategy"] = "NOT_PERFORMED"
            evidence["strategy"] = {
                "status": "NOT_PERFORMED",
                "basis": "PERSISTED_STRATEGY_POLICY_UNAVAILABLE",
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
            }
        else:
            strategy_record, strategy_document, strategy_is_target = strategy_row
            try:
                _validate_closed_strategy_catalog(strategy_document)
            except (TypeError, ValueError) as exc:
                statuses["strategy"] = "VETO"
                basis = str(exc)[:500]
            else:
                statuses["strategy"] = "PASS"
                basis = "STRATEGY_POLICY_COMPLETE_SCHEMA_AND_FIELD_BOUNDS_PROVEN"
            evidence["strategy"] = {
                "status": statuses["strategy"],
                "basis": basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                "policy_config_id": str(strategy_record.id),
                "policy_document_hash": document_hash(strategy_document),
                "uses_candidate_document": strategy_is_target,
            }
        return statuses, evidence

    if operation == "UPDATE_CONFIG_PROFILE" and config_type == "spot_engine":
        risk_row = effective.get("risk")
        if risk_row is None:
            risk_status = "NOT_PERFORMED"
            risk_basis = "PERSISTED_RISK_POLICY_UNAVAILABLE"
        else:
            try:
                _validate_spot_candidate_against_global_risk(
                    deepcopy(payload.get("candidate_document") or {}),
                    risk_row[1],
                )
            except (TypeError, ValueError) as exc:
                risk_status = "VETO"
                risk_basis = str(exc)[:500]
            else:
                risk_status = "PASS"
                risk_basis = "SPOT_CANDIDATE_WITHIN_PERSISTED_GLOBAL_RISK_LIMITS"
        try:
            _validate_config_candidate(
                "spot_engine", deepcopy(payload.get("candidate_document") or {})
            )
        except (TypeError, ValueError) as exc:
            strategy_status = "VETO"
            strategy_basis = str(exc)[:500]
        else:
            strategy_status = "PASS"
            strategy_basis = (
                "SPOT_ENGINE_EXECUTION_POLICY_SCHEMA_AND_INVARIANTS_PROVEN"
            )
        statuses = {"risk": risk_status, "strategy": strategy_status}
        evidence = {
            "risk": {
                "status": risk_status,
                "basis": risk_basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
            },
            "strategy": {
                "status": strategy_status,
                "basis": strategy_basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                "candidate_document_hash": document_hash(
                    payload.get("candidate_document") or {}
                ),
            },
        }
        return statuses, evidence

    if operation == "UPDATE_CONFIG_PROFILE" and config_type == "futures_engine":
        try:
            _validate_config_candidate(
                "futures_engine", deepcopy(payload.get("candidate_document") or {})
            )
        except (TypeError, ValueError) as exc:
            status = "VETO"
            risk_basis = strategy_basis = str(exc)[:500]
        else:
            status = "PASS"
            risk_basis = "FUTURES_ENGINE_RISK_SCHEMA_AND_FIELD_BOUNDS_PROVEN"
            strategy_basis = (
                "FUTURES_ENGINE_EXECUTION_AND_MANAGEMENT_SCHEMA_PROVEN"
            )
        candidate_hash = document_hash(payload.get("candidate_document") or {})
        statuses = {"risk": status, "strategy": status}
        evidence = {
            "risk": {
                "status": status,
                "basis": risk_basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                "candidate_document_hash": candidate_hash,
            },
            "strategy": {
                "status": status,
                "basis": strategy_basis,
                "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
                "candidate_document_hash": candidate_hash,
            },
        }
        return statuses, evidence

    statuses = {"risk": "NOT_PERFORMED", "strategy": "NOT_PERFORMED"}
    evidence = {
        "risk": {
            "status": "NOT_PERFORMED",
            "basis": "NO_REGISTERED_CANDIDATE_TO_RISK_POLICY_MAPPING",
            "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
        },
        "strategy": {
            "status": "NOT_PERFORMED",
            "basis": "NO_REGISTERED_CANDIDATE_TO_STRATEGY_POLICY_MAPPING",
            "validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
        },
    }
    return statuses, evidence


def _candidate_validation_result(
    plan: CopilotActionPlan,
    policy_records: list[ConfigProfile],
    profiles: list[Profile],
) -> dict[str, Any]:
    """Evaluate a governed candidate against real, persisted policy snapshots.

    The evaluator is intentionally deterministic: it performs schema,
    referential and invariant checks only.  It does not infer numeric limits,
    simulate outcomes, call a provider, or treat catalog-tool availability as
    authorization.
    """
    checks: list[dict[str, Any]] = []
    risk_vetoes: list[str] = []
    strategy_vetoes: list[str] = []
    warnings: list[str] = []

    def check(check_id: str, module: str, callback) -> None:
        try:
            callback()
        except (GovernedChangePathError, TypeError, ValueError) as exc:
            reason = f"{check_id}: {str(exc)[:500]}"
            checks.append({"check": check_id, "module": module, "decision": "VETO", "reason": reason})
            (risk_vetoes if module == "global_risk" else strategy_vetoes).append(reason)
        else:
            checks.append({"check": check_id, "module": module, "decision": "PASS"})

    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    target_config_type = str(payload.get("config_type") or "")
    selected = _latest_global_policy_records(policy_records)
    effective: dict[str, tuple[ConfigProfile, dict[str, Any], bool]] = {}
    policy_families = [
        ("risk", "global_risk"),
        ("strategy", "strategies"),
    ]
    if (
        operation in {
            "UPDATE_PROFILE_CONFIG",
            "UPDATE_PROFILE_CONFIG_SET",
            "SET_PROFILE_ACTIVE_STATUS",
        }
        or (operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "score")
    ):
        policy_families.append(("score", "strategies"))
    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "spot_engine":
        policy_families.append(("spot", "global_risk"))
    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "futures_engine":
        policy_families.append(("futures", "global_risk"))
    for family, module in policy_families:
        record = selected.get(family)
        if record is None:
            reason = f"{family.upper()}_POLICY_NOT_FOUND"
            checks.append({
                "check": f"{family.upper()}_POLICY_SNAPSHOT_PRESENT",
                "module": module,
                "decision": "VETO",
                "reason": reason,
            })
            (risk_vetoes if module == "global_risk" else strategy_vetoes).append(reason)
            continue
        try:
            document, is_target = _effective_policy_document(plan, record)
        except ValueError as exc:
            reason = f"{family.upper()}_POLICY_INVALID: {str(exc)[:500]}"
            checks.append({
                "check": f"{family.upper()}_POLICY_SNAPSHOT_PRESENT",
                "module": module,
                "decision": "VETO",
                "reason": reason,
            })
            (risk_vetoes if module == "global_risk" else strategy_vetoes).append(reason)
            continue
        effective[family] = (record, document, is_target)
        checks.append({
            "check": f"{family.upper()}_POLICY_SNAPSHOT_PRESENT",
            "module": module,
            "decision": "PASS",
            "reason": "Snapshot presence and hash only; no semantic policy approval",
        })

    check(
        "MATERIALIZED_DIFF_MATCHES_CANDIDATE",
        "strategies",
        lambda: _assert_materialized_diff_matches_candidate(plan),
    )

    target_config_id = str(payload.get("config_profile_id") or "")
    target_family = next(
        (
            family
            for family, aliases in _POLICY_TYPE_PRECEDENCE.items()
            if target_config_type in aliases
        ),
        None,
    )
    if operation == "UPDATE_CONFIG_PROFILE":
        module = (
            "global_risk"
            if target_config_type in {"spot_engine", "futures_engine", "risk"}
            else "strategies"
        )
        def _require_registered_global_target() -> None:
            if payload.get("pool_id") not in {None, ""}:
                raise ValueError("Governed ConfigProfile target must be global (pool_id=null)")
            if target_config_type not in _STRICT_CONFIG_PROFILE_TYPES:
                raise ValueError(
                    "ConfigProfile family has no registered governed candidate schema"
                )

        check(
            "CONFIG_PROFILE_REGISTERED_GLOBAL_SCHEMA",
            module,
            _require_registered_global_target,
        )

    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "risk":
        check(
            "RISK_CANDIDATE_SCHEMA_AND_FIELD_BOUNDS",
            "global_risk",
            lambda: _validate_config_candidate(
                "risk", deepcopy(payload.get("candidate_document") or {})
            ),
        )

    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "strategy":
        check(
            "STRATEGY_CANDIDATE_SCHEMA_AND_FIELD_BOUNDS",
            "strategies",
            lambda: _validate_config_candidate(
                "strategy", deepcopy(payload.get("candidate_document") or {})
            ),
        )
    if operation == "UPDATE_CONFIG_PROFILE" and target_family:
        module = (
            "global_risk"
            if target_family in {"risk", "spot", "futures"}
            else "strategies"
        )

        def _require_effective_target() -> None:
            selected_record = selected.get(target_family)
            if selected_record is None or str(selected_record.id) != target_config_id:
                raise ValueError(
                    "Target row is not the effective global policy under runtime precedence"
                )

        check("TARGET_IS_EFFECTIVE_POLICY", module, _require_effective_target)

    if (
        operation == "UPDATE_CONFIG_PROFILE"
        and target_config_type == "spot_engine"
        and effective.get("spot")
    ):
        spot_document = effective["spot"][1]

        def _validate_spot_invariant() -> None:
            SpotEngineConfig.from_config_json(spot_document)
            selling = spot_document.get("selling")
            if not isinstance(selling, dict) or selling.get("never_sell_at_loss") is not True:
                raise ValueError(
                    "Persisted spot policy must explicitly set selling.never_sell_at_loss=true"
                )

        check("SPOT_NEVER_SELL_AT_LOSS", "global_risk", _validate_spot_invariant)

    profile_candidates: list[dict[str, Any]] = []

    def _validate_profile_schemas() -> None:
        nonlocal profile_candidates
        profile_candidates = _profile_candidates_from_plan(plan)
        for candidate in profile_candidates:
            _validate_strict_profile_config(candidate)
        if operation == "SET_PROFILE_ACTIVE_STATUS":
            if not profiles:
                raise ValueError("Profile status target dependencies are missing")
            candidate_document = payload.get("candidate_document")
            rows = (
                candidate_document.get("profiles")
                if isinstance(candidate_document, dict)
                else None
            )
            if not isinstance(rows, list) or not rows:
                raise ValueError("Profile status candidate is malformed")
            status_by_id: dict[str, bool] = {}
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("Profile status candidate row is malformed")
                profile_id = str(row.get("profile_id") or "")
                is_active = row.get("is_active")
                if not profile_id or profile_id in status_by_id or not isinstance(is_active, bool):
                    raise ValueError("Profile status candidate rows are invalid")
                status_by_id[profile_id] = is_active
            for profile in profiles:
                profile_id = str(profile.id)
                if profile_id not in status_by_id:
                    raise ValueError("Profile status target dependencies are incomplete")
                # A legacy or malformed profile may always be disabled. It may
                # only be enabled after its complete runtime contract passes.
                if status_by_id[profile_id] is True:
                    profile_config = profile.config or {}
                    _validate_strict_profile_config(profile_config)
                    # Activated profiles participate in the effective Score
                    # referential-integrity check below.  Disabling a legacy
                    # malformed profile deliberately does not require its old
                    # document to become valid first.
                    profile_candidates.append(profile_config)

    check("PROFILE_CANDIDATE_SCHEMA", "strategies", _validate_profile_schemas)

    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "score":
        check(
            "SCORE_CANDIDATE_SCHEMA",
            "strategies",
            lambda: _validate_config_candidate(
                "score",
                deepcopy(payload.get("candidate_document") or {})
            ),
        )

    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "spot_engine":
        check(
            "SPOT_CANDIDATE_SCHEMA_AND_INVARIANTS",
            "global_risk",
            lambda: _validate_config_candidate(
                "spot_engine",
                deepcopy(payload.get("candidate_document") or {}),
            ),
        )

    if operation == "UPDATE_CONFIG_PROFILE" and target_config_type == "futures_engine":
        check(
            "FUTURES_CANDIDATE_SCHEMA_AND_FIELD_BOUNDS",
            "global_risk",
            lambda: _validate_config_candidate(
                "futures_engine",
                deepcopy(payload.get("candidate_document") or {}),
            ),
        )

    if effective.get("score"):
        score_document = effective["score"][1]

        def _validate_score_links() -> None:
            if not isinstance(score_document.get("scoring_rules"), list):
                raise ValueError("Effective score policy lacks a persisted scoring_rules array")
            configs = profile_candidates
            if operation == "UPDATE_CONFIG_PROFILE" and target_family == "score":
                configs = [profile.config or {} for profile in profiles]
            for profile_config in configs:
                validate_score_links(profile_config, score_document)

        check("PROFILE_SCORE_LINKS", "strategies", _validate_score_links)

    semantic_statuses, semantic_evidence = _policy_semantic_result(
        plan,
        effective,
        profiles,
    )
    for family, module in (("risk", "global_risk"), ("strategy", "strategies")):
        status = semantic_statuses[family]
        basis = semantic_evidence[family]["basis"]
        checks.append({
            "check": f"{family.upper()}_POLICY_SEMANTIC_VALIDATION",
            "module": module,
            "decision": status,
            "reason": basis,
        })
        if status in {"VETO", "NOT_PERFORMED"}:
            reason = f"{family.upper()}_POLICY_SEMANTIC_{status}: {basis}"
            (risk_vetoes if family == "risk" else strategy_vetoes).append(reason)

    risk_decision = (
        GuardDecision.VETO
        if risk_vetoes
        else GuardDecision.PASS
    )
    strategy_decision = (
        GuardDecision.INVARIANT_CONFLICT
        if strategy_vetoes
        else GuardDecision.PASS
    )
    guard = RecommendationGuard.require_candidate_allowed(
        RecommendationValidation(
            module="global_risk",
            decision=risk_decision,
            reasons=tuple(risk_vetoes),
        ),
        RecommendationValidation(
            module="strategies",
            decision=strategy_decision,
            reasons=tuple(strategy_vetoes),
        ),
    )

    snapshots = []
    for family in ("risk", "strategy", "spot", "score", "futures"):
        row = effective.get(family)
        if row is None:
            continue
        record, document, is_target = row
        snapshots.append({
            "family": family,
            "config_type": record.config_type,
            "config_id": str(record.id),
            "pool_id": str(record.pool_id) if record.pool_id else None,
            "updated_at": _iso_or_none(record.updated_at),
            "document_hash": document_hash(document),
            "uses_candidate_document": is_target,
        })
    policy_snapshot_hash = document_hash(snapshots)
    try:
        profile_dependency_snapshots = _profile_dependency_snapshots(plan, profiles)
    except (TypeError, ValueError) as exc:
        reason = f"PROFILE_DEPENDENCY_SNAPSHOT: {str(exc)[:500]}"
        checks.append({
            "check": "PROFILE_DEPENDENCY_SNAPSHOT",
            "module": "strategies",
            "decision": "VETO",
            "reason": reason,
        })
        strategy_vetoes.append(reason)
        profile_dependency_snapshots = []
        strategy_decision = GuardDecision.INVARIANT_CONFLICT
        guard = RecommendationGuard.require_candidate_allowed(
            RecommendationValidation(
                module="global_risk",
                decision=risk_decision,
                reasons=tuple(risk_vetoes),
            ),
            RecommendationValidation(
                module="strategies",
                decision=strategy_decision,
                reasons=tuple(strategy_vetoes),
            ),
        )
    else:
        checks.append({
            "check": "PROFILE_DEPENDENCY_SNAPSHOT",
            "module": "strategies",
            "decision": "PASS",
        })
    profile_dependency_snapshot_hash = document_hash(profile_dependency_snapshots)
    candidate = payload.get("candidate_document") or {}
    validation_scope = "CANDIDATE_SCHEMA_AND_PERSISTED_POLICY_SEMANTICS"
    terminal_reason = (
        "POLICY_SEMANTIC_GUARDS_PASS" if guard.allowed else guard.terminal_reason
    )
    return {
        "decision": "PASS" if guard.allowed else "VETO",
        "policy_semantic_validator_version": _POLICY_SEMANTIC_VALIDATOR_VERSION,
        "terminal_reason": terminal_reason,
        "operation_type": operation,
        "candidate_document_hash": document_hash(candidate),
        "diff_hash": document_hash(list(plan.proposed_diff or [])),
        "plan_binding_hash": plan_binding_hash(plan),
        "policy_snapshot_hash": policy_snapshot_hash,
        "policy_snapshots": snapshots,
        "profile_dependency_snapshot_hash": profile_dependency_snapshot_hash,
        "profile_dependency_snapshots": profile_dependency_snapshots,
        # These public labels describe semantic validation only.  They must
        # never say PASS when semantic validation was not actually performed.
        "risk_validation": semantic_statuses["risk"],
        "strategy_validation": semantic_statuses["strategy"],
        "deterministic_guard_validation": {
            "risk": risk_decision.value,
            "strategy": strategy_decision.value,
        },
        "validation_scope": validation_scope,
        "policy_semantic_validation": semantic_statuses,
        "policy_semantic_evidence": semantic_evidence,
        "checks": checks,
        "warnings": warnings,
        "validated": [
            "materialized diff against source and candidate",
            "known Profile runtime contract and generated lineage metadata when applicable",
            "global Score document schema, bounds and Profile rule links when applicable",
            "complete canonical Spot/Futures document schemas when directly targeted",
            "explicit Spot never-sell-at-loss invariant when Spot is targeted",
            "policy snapshot identity and document hashes",
            "persisted global Risk caps against a Spot candidate when Spot is targeted",
            "closed persisted Risk and Strategy schemas for Profile/Score writes",
            "limited Profile/Score authority outside sizing, exposure, order, stop and leverage",
            "all applicable active Strategy catalog constraints for touched Profile/Score concepts",
        ],
        "not_validated": [
            "any risk/strategy semantics marked NOT_PERFORMED in policy_semantic_validation",
            "provider or model judgment",
            "profitability, backtest or shadow outcome",
            "exchange state, order placement or live execution",
            "numeric limits absent from registered schemas or persisted policy JSON",
        ],
    }


async def _load_candidate_validation_context(
    db: AsyncSession,
    user_id: UUID,
    plan: CopilotActionPlan,
) -> tuple[list[ConfigProfile], list[Profile]]:
    """Read the candidate's governing configuration context directly from DB."""
    policy_records = list((await db.execute(select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.is_active.is_(True),
        ConfigProfile.config_type.in_(_CANDIDATE_POLICY_TYPES),
    ).order_by(
        ConfigProfile.config_type,
        ConfigProfile.updated_at.desc(),
        ConfigProfile.id,
    ))).scalars().all())
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    profile_query = select(Profile).where(Profile.user_id == user_id)
    if not (
        operation == "UPDATE_CONFIG_PROFILE"
        and payload.get("config_type") in {"score", "score_engine"}
    ):
        try:
            target_ids = [UUID(item) for item in _target_profile_ids(plan) if item]
        except ValueError as exc:
            raise ValueError("Governed plan contains an invalid Profile target") from exc
        if target_ids:
            profile_query = profile_query.where(Profile.id.in_(target_ids))
        else:
            # No Profile object participates in spot/futures/config validation.
            profile_query = profile_query.where(Profile.id.is_(None))
    profiles = list((await db.execute(
        profile_query.order_by(Profile.id)
    )).scalars().all())
    return policy_records, profiles


async def validate_candidate_for_second_gate(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
) -> dict[str, Any]:
    """Persist an auditable PASS/VETO without mutating operational config."""
    plan = await get_plan(db, user_id, plan_id)
    if plan.status != "DRY_RUN":
        raise ValueError(f"Candidate cannot validate from status {plan.status}")
    policy_records, profiles = await _load_candidate_validation_context(
        db,
        user_id,
        plan,
    )
    result = _candidate_validation_result(plan, policy_records, profiles)
    evidence = dict(plan.evidence or {})
    previous = evidence.get("candidate_validation")
    if previous == result:
        return result
    evidence["candidate_validation"] = result
    plan.evidence = evidence
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type=(
            "ANALYSIS_CHAT_CANDIDATE_VALIDATION_PASS"
            if result["decision"] == "PASS"
            else "ANALYSIS_CHAT_CANDIDATE_VALIDATION_VETO"
        ),
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload=result,
    ))
    await db.flush()
    return result


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
        config.get("enabled") is True
        and config.get("proposals_enabled") is True
        and config.get("governed_actions_enabled") is True
        and config.get("live_config_write_enabled") is True
    )


async def _lock_candidate_execution_context(
    db: AsyncSession,
    user_id: UUID,
    plan: CopilotActionPlan,
) -> tuple[list[ConfigProfile], list[Profile]]:
    """Take a transaction-wide fence over every row used by validation.

    A score change depends on the complete Profile set and effective policy
    aliases.  Row locks alone cannot prevent a concurrent insert from creating
    a phantom dependency, so the governed write takes brief table-level write
    fences.  Ordinary readers remain available while other configuration
    writers serialize behind this confirmation transaction.
    """
    await db.execute(text(
        "LOCK TABLE config_profiles, profiles IN SHARE ROW EXCLUSIVE MODE"
    ))
    policy_records = list((await db.execute(select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.is_active.is_(True),
        ConfigProfile.config_type.in_(_CANDIDATE_POLICY_TYPES),
    ).order_by(
        ConfigProfile.config_type,
        ConfigProfile.updated_at.desc(),
        ConfigProfile.id,
    ).with_for_update())).scalars().all())

    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    profile_query = select(Profile).where(Profile.user_id == user_id)
    if not (
        operation == "UPDATE_CONFIG_PROFILE"
        and payload.get("config_type") == "score"
    ):
        try:
            target_ids = [UUID(item) for item in _target_profile_ids(plan) if item]
        except ValueError as exc:
            raise ValueError("Governed plan contains an invalid Profile target") from exc
        if target_ids:
            profile_query = profile_query.where(Profile.id.in_(target_ids))
        else:
            profile_query = profile_query.where(Profile.id.is_(None))
    profiles = list((await db.execute(
        profile_query.order_by(Profile.id).with_for_update()
    )).scalars().all())
    return policy_records, profiles


def _require_persisted_candidate_pass(
    plan: CopilotActionPlan,
    current: dict[str, Any],
) -> None:
    def is_executable(validation: dict[str, Any]) -> bool:
        semantic = dict(validation.get("policy_semantic_validation") or {})
        deterministic = dict(validation.get("deterministic_guard_validation") or {})
        labels_are_honest = (
            validation.get("risk_validation") == semantic.get("risk")
            and validation.get("strategy_validation") == semantic.get("strategy")
        )
        fully_semantic_scope = (
            semantic == {"risk": "PASS", "strategy": "PASS"}
            and validation.get("terminal_reason")
            == "POLICY_SEMANTIC_GUARDS_PASS"
        )
        return (
            validation.get("decision") == "PASS"
            and validation.get("policy_semantic_validator_version")
            == _POLICY_SEMANTIC_VALIDATOR_VERSION
            and labels_are_honest
            and deterministic == {"risk": "PASS", "strategy": "PASS"}
            and fully_semantic_scope
        )

    stored = dict((plan.evidence or {}).get("candidate_validation") or {})
    if not is_executable(stored):
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CANDIDATE_VALIDATION_REQUIRED"
        )
    if stored.get("plan_binding_hash") != plan_binding_hash(plan):
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CANDIDATE_PLAN_BINDING_STALE"
        )
    if stored != current:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CANDIDATE_VALIDATION_STALE"
        )
    if not is_executable(current):
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CANDIDATE_VALIDATION_VETO"
        )


async def _execution_runtime_record(
    db: AsyncSession,
    user_id: UUID,
) -> tuple[ConfigProfile, AnalysisChatRuntimeConfig]:
    rows = list((await db.execute(select(ConfigProfile).where(
        ConfigProfile.user_id == user_id,
        ConfigProfile.pool_id.is_(None),
        ConfigProfile.config_type == "ai_analysis_chat_runtime",
        ConfigProfile.is_active.is_(True),
    ).order_by(ConfigProfile.updated_at.desc(), ConfigProfile.id).with_for_update()))
        .scalars().all())
    if len(rows) != 1:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_RUNTIME_CONFIG_AMBIGUOUS"
        )
    try:
        config = AnalysisChatRuntimeConfig.model_validate(rows[0].config_json or {})
    except ValueError as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_RUNTIME_CONFIG_INVALID"
        ) from exc
    if not (
        config.enabled
        and config.proposals_enabled
        and config.governed_actions_enabled
        and config.live_config_write_enabled
    ):
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_LIVE_CONFIG_WRITE_DISABLED"
        )
    return rows[0], config


def _execution_fence_hashes(
    plan: CopilotActionPlan,
    validation: dict[str, Any] | None,
    *,
    decision_id: str,
    runtime_record: ConfigProfile | None = None,
) -> dict[str, Any]:
    stored = (plan.evidence or {}).get("candidate_validation")
    selected = validation if isinstance(validation, dict) else stored
    return {
        "approval_decision_id": decision_id,
        "plan_binding_hash": plan_binding_hash(plan),
        "candidate_document_hash": (selected or {}).get("candidate_document_hash"),
        "diff_hash": (selected or {}).get("diff_hash"),
        "policy_snapshot_hash": (selected or {}).get("policy_snapshot_hash"),
        "profile_dependency_snapshot_hash": (selected or {}).get(
            "profile_dependency_snapshot_hash"
        ),
        "stored_candidate_validation_hash": (
            document_hash(stored) if isinstance(stored, dict) else None
        ),
        "current_candidate_validation_hash": (
            document_hash(validation) if isinstance(validation, dict) else None
        ),
        "runtime_config_id": str(runtime_record.id) if runtime_record else None,
        "runtime_config_hash": (
            document_hash({
                "config": deepcopy(runtime_record.config_json or {}),
                "updated_at": _iso_or_none(runtime_record.updated_at),
            })
            if runtime_record
            else None
        ),
    }


async def _mark_execution_blocked(
    db: AsyncSession,
    *,
    plan: CopilotActionPlan,
    user_id: UUID,
    decision_id: str,
    reason_code: str,
    validation: dict[str, Any] | None = None,
    runtime_record: ConfigProfile | None = None,
) -> None:
    fence = _execution_fence_hashes(
        plan,
        validation,
        decision_id=decision_id,
        runtime_record=runtime_record,
    )
    result = {
        "status": "BLOCKED",
        "reason_code": reason_code,
        "live_config_changed": False,
        **fence,
    }
    plan.status = "STALE"
    plan.execution_result = result
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_EXECUTION_BLOCKED",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload=result,
    ))
    # Transaction ownership belongs to the graph handler.  Flushing keeps the
    # STALE state and its audit event in the same transaction as the graph
    # node-completed event; committing here would close ``session.begin()``
    # before that event can be inserted.
    await db.flush()


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
    if not changes:
        raise ValueError("A governed change requires at least one proposed change")
    for change in changes:
        change_path = str(change.get("path") or "")
        if any(
            change_path == root or change_path.startswith(f"{root}/")
            for root in DEAD_CONFIG_PATH_ROOTS
        ):
            raise ValueError(
                f"scoring.weights is dead configuration (accepted for API "
                f"compatibility only, ignored by robust_indicators."
                f"calculate_score_with_confidence); governed changes may not "
                f"target {change_path}"
            )
    referenced: set[str] = set()
    for change in changes:
        change_references = {str(ref) for ref in change.get("evidence_refs") or []}
        if not change_references or not change_references.issubset(evidence_ids):
            raise ValueError("Every proposed change requires evidence from the parent analysis")
        referenced.update(change_references)

    if operation == "SET_PROFILE_ACTIVE_STATUS":
        raw_target_ids = list(target.get("profile_ids") or [])
        if not raw_target_ids:
            raise ValueError("SET_PROFILE_ACTIVE_STATUS requires profile_ids")
        try:
            target_ids = [UUID(str(item)) for item in raw_target_ids]
        except (TypeError, ValueError) as exc:
            raise ValueError("SET_PROFILE_ACTIVE_STATUS requires valid profile_ids") from exc
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("SET_PROFILE_ACTIVE_STATUS profile_ids must be unique")
        if len(target_ids) > 100:
            raise ValueError("A governed change is limited to 100 profiles")
        resources = list((await db.execute(select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(target_ids),
        ))).scalars().all())
        by_id = {resource.id: resource for resource in resources}
        if set(by_id) != set(target_ids):
            raise LookupError("One or more profiles were not found")

        requested: dict[UUID, dict[str, Any]] = {}
        for change in changes:
            try:
                profile_id = UUID(str(change.get("profile_id")))
            except (TypeError, ValueError) as exc:
                raise ValueError("Every profile status change requires profile_id") from exc
            if profile_id not in by_id or profile_id in requested:
                raise ValueError("Profile status changes must target each owned profile once")
            if change.get("op") != "replace" or change.get("path") != "/is_active":
                raise ValueError("Profile status changes may only replace /is_active")
            value = change.get("value")
            if not isinstance(value, bool):
                raise ValueError("Profile is_active must be a boolean")
            if change.get("array_guards") != []:
                raise ValueError("Profile status changes cannot include array guards")
            if "old_value" not in change or not isinstance(change.get("old_value"), bool):
                raise ValueError("Profile status changes require a boolean old_value")
            current_value = bool(by_id[profile_id].is_active)
            if change.get("old_value") != current_value:
                raise ValueError("Profile is_active changed after the proposed evidence")
            if value == current_value:
                raise ValueError("Profile status change is a no-op")
            expected_name = str(change.get("profile_name") or "").strip()
            if expected_name and expected_name != by_id[profile_id].name:
                raise ValueError("Target profile_name does not match the owned profile")
            requested[profile_id] = change
        if set(requested) != set(target_ids):
            raise ValueError("profile_ids must match the proposed status changes")

        ordered = [by_id[item] for item in sorted(target_ids, key=str)]
        before = {
            "profiles": [
                {
                    "profile_id": str(resource.id),
                    "profile_name": resource.name,
                    "is_active": bool(resource.is_active),
                }
                for resource in ordered
            ]
        }
        state_snapshot = {
            "profiles": [
                {
                    **item,
                    "updated_at": by_id[UUID(item["profile_id"])].updated_at,
                }
                for item in before["profiles"]
            ]
        }
        candidate = {
            "profiles": [
                {
                    "profile_id": str(resource.id),
                    "profile_name": resource.name,
                    "is_active": requested[resource.id]["value"],
                }
                for resource in ordered
            ]
        }
        diff = [
            {
                "op": "replace",
                "path": f"/profiles/{resource.id}/is_active",
                "old_value": bool(resource.is_active),
                "value": requested[resource.id]["value"],
                "reason": str(
                    requested[resource.id].get("reason")
                    or "Requested in Analysis Chat"
                )[:2000],
                "evidence_refs": [
                    str(item)
                    for item in requested[resource.id].get("evidence_refs") or []
                ],
                "profile_id": str(resource.id),
                "profile_name": resource.name,
            }
            for resource in ordered
        ]
        target_type = "PROFILE_SET"
        target_id = f"bulk:{document_hash([str(item) for item in target_ids])[:93]}"
        target_label = f"{len(ordered)} profiles"
        state_hash = document_hash(state_snapshot)
        payload = {
            "operation_type": operation,
            "profile_ids": [str(resource.id) for resource in ordered],
            "source_document": before,
            "candidate_document": candidate,
        }
    elif operation == "UPDATE_PROFILE_CONFIG_SET":
        raw_target_ids = list(target.get("profile_ids") or [])
        if not raw_target_ids:
            raise ValueError("UPDATE_PROFILE_CONFIG_SET requires profile_ids")
        try:
            target_ids = [UUID(str(item)) for item in raw_target_ids]
        except (TypeError, ValueError) as exc:
            raise ValueError("UPDATE_PROFILE_CONFIG_SET requires valid profile_ids") from exc
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("UPDATE_PROFILE_CONFIG_SET profile_ids must be unique")
        if len(target_ids) > 32:
            raise ValueError("A governed profile configuration set is limited to 32 profiles")
        resources = list((await db.execute(select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(target_ids),
        ))).scalars().all())
        by_id = {resource.id: resource for resource in resources}
        if set(by_id) != set(target_ids):
            raise LookupError("One or more profiles were not found")

        grouped: dict[UUID, list[dict[str, Any]]] = {item: [] for item in target_ids}
        for change in changes:
            try:
                profile_id = UUID(str(change.get("profile_id")))
            except (TypeError, ValueError) as exc:
                raise ValueError("Every profile configuration change requires profile_id") from exc
            if profile_id not in by_id:
                raise ValueError("Profile configuration change targets an unowned profile")
            expected_name = str(change.get("profile_name") or "").strip()
            if expected_name and expected_name != by_id[profile_id].name:
                raise ValueError("Target profile_name does not match the owned profile")
            grouped[profile_id].append(change)
        if any(not grouped[item] for item in target_ids):
            raise ValueError("Every target profile requires at least one configuration change")

        score = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.user_id == user_id,
                ConfigProfile.pool_id.is_(None),
                ConfigProfile.config_type == "score",
                ConfigProfile.is_active.is_(True),
            ).limit(1))
        ).scalar_one_or_none()
        ordered = [by_id[item] for item in sorted(target_ids, key=str)]
        before_rows: list[dict[str, Any]] = []
        candidate_rows: list[dict[str, Any]] = []
        diff = []
        for resource in ordered:
            source_config = deepcopy(resource.config or {})
            patched_config, profile_diff = apply_typed_patch(
                source_config,
                grouped[resource.id],
                allowed_roots=PROFILE_ROOTS,
            )
            normalized_source = _validate_profile_config(deepcopy(source_config))
            _require_canonical_profile_source(source_config, normalized_source)
            candidate_config = _validate_profile_config(patched_config)
            _assert_patch_survived_normalization(
                normalized_source,
                candidate_config,
                grouped[resource.id],
            )
            if score is not None and (score.config_json or {}).get("scoring_rules") is not None:
                validate_score_links(candidate_config, score.config_json or {})
            before_rows.append({
                "profile_id": str(resource.id),
                "profile_name": resource.name,
                "config": source_config,
            })
            candidate_rows.append({
                "profile_id": str(resource.id),
                "profile_name": resource.name,
                "config": candidate_config,
            })
            diff.extend({
                **item,
                "path": f"/profiles/{resource.id}{item['path']}",
                "profile_id": str(resource.id),
                "profile_name": resource.name,
            } for item in profile_diff)
        before = {"profiles": before_rows}
        candidate = {"profiles": candidate_rows}
        state_hash = document_hash({
            "profiles": [
                {
                    **item,
                    "profile_version": by_id[UUID(item["profile_id"])].profile_version,
                    "updated_at": by_id[UUID(item["profile_id"])].updated_at,
                }
                for item in before_rows
            ]
        })
        target_type = "PROFILE_SET"
        target_id = f"bulk-config:{document_hash([str(item) for item in target_ids])[:86]}"
        target_label = f"{len(ordered)} profiles"
        payload = {
            "operation_type": operation,
            "profile_ids": [str(resource.id) for resource in ordered],
            "source_document": before,
            "candidate_document": candidate,
        }
    elif operation == "UPDATE_PROFILE_CONFIG":
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
        patched_candidate, diff = apply_typed_patch(
            before,
            changes,
            allowed_roots=PROFILE_ROOTS,
        )
        normalized_before = _validate_profile_config(deepcopy(before))
        _require_canonical_profile_source(before, normalized_before)
        candidate = _validate_profile_config(patched_candidate)
        _assert_patch_survived_normalization(normalized_before, candidate, changes)
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
        if target.get("pool_id") not in {None, ""}:
            raise ValueError("Governed configuration changes require pool_id=null")
        pool_id = None
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
        patched_candidate, diff = apply_typed_patch(before, changes)
        normalized_before = _validate_config_candidate(config_type, deepcopy(before))
        candidate = _validate_config_candidate(config_type, patched_candidate)
        _assert_patch_survived_normalization(normalized_before, candidate, changes)
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
    await db.flush()
    return plan_to_dict(plan)


def plan_to_dict(plan: CopilotActionPlan) -> dict[str, Any]:
    candidate_validation = dict(
        (getattr(plan, "evidence", None) or {}).get("candidate_validation") or {}
    ) or None
    return {
        "proposal_id": str(plan.id),
        "operation_type": (plan.execution_payload or {}).get("operation_type"),
        "target_type": plan.target_type,
        "target_id": plan.target_id,
        "target": {
            key: value for key, value in (plan.execution_payload or {}).items()
            if key in {
                "profile_id", "profile_ids", "profile_name", "config_type", "pool_id"
            }
        },
        "objective": plan.objective,
        "risk": plan.risk_assessment,
        "changes": plan.proposed_diff or [],
        "status": plan.status,
        "requires_human_approval": plan.status == "DRY_RUN",
        "approved_at": plan.approved_at.isoformat() if plan.approved_at else None,
        "executed_at": plan.executed_at.isoformat() if plan.executed_at else None,
        "execution_result": plan.execution_result,
        "candidate_validation": candidate_validation,
        "validation_scope": (
            candidate_validation.get("validation_scope")
            if candidate_validation
            else None
        ),
        "policy_semantic_validation": (
            candidate_validation.get("policy_semantic_validation")
            if candidate_validation
            else None
        ),
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


async def _lock_execution_target(
    db: AsyncSession,
    user_id: UUID,
    plan: CopilotActionPlan,
    locked_profiles: list[Profile],
) -> tuple[list[Profile], ConfigProfile | None]:
    """Lock and re-prove the exact preview target before approval is recorded."""
    payload = dict(plan.execution_payload or {})
    operation = str(payload.get("operation_type") or "")
    resources: list[Profile] = []
    config_resource: ConfigProfile | None = None

    if operation in {"SET_PROFILE_ACTIVE_STATUS", "UPDATE_PROFILE_CONFIG_SET"}:
        try:
            profile_ids = [UUID(str(item)) for item in payload.get("profile_ids") or []]
        except (TypeError, ValueError) as exc:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_INVALID"
            ) from exc
        by_id = {profile.id: profile for profile in locked_profiles}
        resources = [by_id[item] for item in sorted(profile_ids, key=str) if item in by_id]
        if len(resources) != len(profile_ids) or len(set(profile_ids)) != len(profile_ids):
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_MISSING"
            )
        if operation == "SET_PROFILE_ACTIVE_STATUS":
            snapshot = {
                "profiles": [
                    {
                        "profile_id": str(resource.id),
                        "profile_name": resource.name,
                        "is_active": bool(resource.is_active),
                        "updated_at": resource.updated_at,
                    }
                    for resource in resources
                ]
            }
        else:
            snapshot = {
                "profiles": [
                    {
                        "profile_id": str(resource.id),
                        "profile_name": resource.name,
                        "config": deepcopy(resource.config or {}),
                        "profile_version": resource.profile_version,
                        "updated_at": resource.updated_at,
                    }
                    for resource in resources
                ]
            }
    elif operation == "UPDATE_PROFILE_CONFIG":
        try:
            profile_id = UUID(str(payload.get("profile_id")))
        except (TypeError, ValueError) as exc:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_INVALID"
            ) from exc
        resources = [item for item in locked_profiles if item.id == profile_id]
        if len(resources) != 1:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_MISSING"
            )
        resource = resources[0]
        snapshot = {
            "config": deepcopy(resource.config or {}),
            "profile_version": resource.profile_version,
            "updated_at": resource.updated_at,
        }
    elif operation == "UPDATE_CONFIG_PROFILE":
        try:
            config_id = UUID(str(payload.get("config_profile_id")))
        except (TypeError, ValueError) as exc:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_INVALID"
            ) from exc
        config_resource = (
            await db.execute(select(ConfigProfile).where(
                ConfigProfile.id == config_id,
                ConfigProfile.user_id == user_id,
                ConfigProfile.is_active.is_(True),
            ).with_for_update())
        ).scalar_one_or_none()
        if config_resource is None:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_MISSING"
            )
        expected_pool_id = str(payload.get("pool_id") or "")
        actual_pool_id = str(config_resource.pool_id or "")
        if (
            config_resource.config_type != payload.get("config_type")
            or actual_pool_id != expected_pool_id
        ):
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_MISMATCH"
            )
        snapshot = {
            "config": deepcopy(config_resource.config_json or {}),
            "updated_at": config_resource.updated_at,
        }
    else:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_EXECUTION_OPERATION_UNSUPPORTED"
        )

    if document_hash(snapshot) != plan.target_state_hash:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_EXECUTION_TARGET_STATE_STALE"
        )
    return resources, config_resource


async def approve_and_execute(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    decision_id: str | UUID | None,
) -> dict[str, Any]:
    try:
        persisted_decision_id = str(UUID(str(decision_id)))
    except (TypeError, ValueError) as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_REQUIRED"
        ) from exc

    plan = await get_plan(db, user_id, plan_id, lock=True)
    terminal_result = dict(plan.execution_result or {})
    if plan.status in {"EXECUTED", "STALE"} and terminal_result.get("status") in {
        "EXECUTED",
        "BLOCKED",
    }:
        prior_decision_id = str(
            terminal_result.get("approval_decision_id") or ""
        )
        if prior_decision_id != persisted_decision_id:
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_CONFLICT"
            )
        return plan_to_dict(plan)
    if plan.status != "DRY_RUN":
        raise ValueError(f"Proposal cannot execute from status {plan.status}")

    current_validation: dict[str, Any] | None = None
    runtime_record: ConfigProfile | None = None
    try:
        policy_records, locked_profiles = await _lock_candidate_execution_context(
            db,
            user_id,
            plan,
        )
        runtime_record, _runtime_config = await _execution_runtime_record(db, user_id)
        current_validation = _candidate_validation_result(
            plan,
            policy_records,
            locked_profiles,
        )
        _require_persisted_candidate_pass(plan, current_validation)
        resources, config_resource = await _lock_execution_target(
            db,
            user_id,
            plan,
            locked_profiles,
        )
    except GovernedExecutionFenceError as exc:
        await _mark_execution_blocked(
            db,
            plan=plan,
            user_id=user_id,
            decision_id=persisted_decision_id,
            reason_code=exc.reason_code,
            validation=current_validation,
            runtime_record=runtime_record,
        )
        return plan_to_dict(plan)

    now = _now()
    fence = _execution_fence_hashes(
        plan,
        current_validation,
        decision_id=persisted_decision_id,
        runtime_record=runtime_record,
    )
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
            "decision_id": persisted_decision_id,
            "approved_at": now.isoformat(),
            **fence,
        },
    ))

    payload = dict(plan.execution_payload or {})
    operation = payload.get("operation_type")
    candidate = deepcopy(payload.get("candidate_document") or {})
    if operation == "SET_PROFILE_ACTIVE_STATUS":
        candidate_rows = {
            UUID(str(item["profile_id"])): item
            for item in candidate.get("profiles") or []
        }
        for resource in resources:
            row = candidate_rows.get(resource.id)
            if row is None or not isinstance(row.get("is_active"), bool):
                raise ValueError("Bulk profile candidate is incomplete")
            resource.is_active = row["is_active"]
            resource.profile_version = now
            resource.updated_at = now
        result = {
            "status": "EXECUTED",
            "resource_type": "PROFILE_SET",
            "resource_ids": [str(resource.id) for resource in resources],
            "profile_count": len(resources),
            "new_document_hash": document_hash(candidate),
            "live_config_changed": True,
            "profiles_deleted": False,
            "target_state_hash_before": plan.target_state_hash,
            **fence,
            **_profile_cache_not_required(),
        }
        cache_type = None
    elif operation == "UPDATE_PROFILE_CONFIG_SET":
        candidate_rows = {
            UUID(str(item["profile_id"])): item
            for item in candidate.get("profiles") or []
        }
        score = _latest_global_policy_records(policy_records).get("score")
        for resource in resources:
            row = candidate_rows.get(resource.id)
            if row is None or not isinstance(row.get("config"), dict):
                raise ValueError("Bulk profile configuration candidate is incomplete")
            new_config = _validate_profile_config(deepcopy(row["config"]))
            if score is not None and (score.config_json or {}).get("scoring_rules") is not None:
                validate_score_links(new_config, score.config_json or {})
            old_config = deepcopy(resource.config or {})
            old_version = resource.profile_version
            resource.config = new_config
            resource.profile_version = now
            resource.updated_at = now
            db.add(ProfileAuditLog(
                user_id=user_id,
                profile_id=resource.id,
                changed_by=user_id,
                change_source="analysis_chat_human_confirmed_bulk",
                change_description=f"Governed Analysis Chat proposal {plan.id}: {plan.objective}",
                previous_config=old_config,
                new_config=new_config,
                previous_profile_version=old_version,
                new_profile_version=now,
            ))
        result = {
            "status": "EXECUTED",
            "resource_type": "PROFILE_SET",
            "resource_ids": [str(resource.id) for resource in resources],
            "profile_count": len(resources),
            "new_document_hash": document_hash(candidate),
            "live_config_changed": True,
            "profiles_deleted": False,
            "target_state_hash_before": plan.target_state_hash,
            **fence,
            **_profile_cache_not_required(),
        }
        cache_type = None
    elif operation == "UPDATE_PROFILE_CONFIG":
        resource = resources[0]
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
            "target_state_hash_before": plan.target_state_hash,
            **fence,
            **_profile_cache_not_required(),
        }
        cache_type = None
    elif operation == "UPDATE_CONFIG_PROFILE":
        if config_resource is None:  # defensive; the fence proved this target.
            raise GovernedExecutionFenceError(
                "ANALYSIS_CHAT_EXECUTION_TARGET_MISSING"
            )
        resource = config_resource
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
            "target_state_hash_before": plan.target_state_hash,
            **fence,
        }
        cache_type = resource.config_type
    else:
        raise ValueError(f"Unsupported governed operation: {operation}")

    if cache_type:
        # Cache invalidation is an external side effect and must not run before
        # the operational transaction commits.  The graph handler reconciles
        # this marker in a second transaction after its node event is durable.
        result.update(_pending_cache_reconciliation(now))

    plan.status = "EXECUTED"
    plan.executed_at = now
    plan.execution_result = result
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_EXECUTED",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={**result},
    ))
    await db.flush()
    return plan_to_dict(plan)


def _profile_cache_not_required() -> dict[str, Any]:
    """Explicitly distinguish Profile writes from ConfigProfile cache work."""
    return {
        "cache_invalidation_status": "NOT_REQUIRED",
        "cache_reconciliation_retry_state": "NOT_APPLICABLE",
        "cache_reconciliation_attempts": 0,
        "cache_reconciliation_max_attempts": 0,
        "cache_reconciliation_next_retry_at": None,
        "cache_reconciliation_dispatch_lease_until": None,
    }


def _pending_cache_reconciliation(now: datetime) -> dict[str, Any]:
    return {
        "cache_invalidation_status": "PENDING_AFTER_COMMIT",
        "cache_reconciliation_attempts": 0,
        "cache_reconciliation_max_attempts": CACHE_RECONCILIATION_MAX_ATTEMPTS,
        "cache_reconciliation_retry_state": "PENDING",
        "cache_reconciliation_next_retry_at": now.isoformat(),
        "cache_reconciliation_dispatch_lease_until": None,
    }


def _cache_reconciliation_datetime(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_STATE_INVALID"
        ) from exc
    if parsed.tzinfo is None:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_STATE_INVALID"
        )
    return parsed.astimezone(timezone.utc)


def _cache_reconciliation_attempts(value: dict[str, Any]) -> int:
    raw = value.get("cache_reconciliation_attempts", 0)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_STATE_INVALID"
        )
    return raw


def _cache_reconciliation_is_due(
    value: dict[str, Any],
    *,
    now: datetime,
    honor_dispatch_lease: bool,
) -> bool:
    attempts = _cache_reconciliation_attempts(value)
    if attempts >= CACHE_RECONCILIATION_MAX_ATTEMPTS:
        return False
    next_retry_at = _cache_reconciliation_datetime(
        value.get("cache_reconciliation_next_retry_at")
    )
    if next_retry_at is not None and next_retry_at > now:
        return False
    if honor_dispatch_lease:
        lease_until = _cache_reconciliation_datetime(
            value.get("cache_reconciliation_dispatch_lease_until")
        )
        if lease_until is not None and lease_until > now:
            return False
    return True


def _cache_reconciliation_result(
    value: dict[str, Any],
    *,
    succeeded: bool,
    now: datetime,
) -> dict[str, Any]:
    attempts = _cache_reconciliation_attempts(value) + 1
    common = {
        **value,
        "cache_reconciliation_attempts": attempts,
        "cache_reconciliation_max_attempts": CACHE_RECONCILIATION_MAX_ATTEMPTS,
        "cache_reconciliation_last_attempt_at": now.isoformat(),
        "cache_reconciliation_dispatch_lease_until": None,
    }
    if succeeded:
        return {
            **common,
            "cache_invalidation_status": "COMPLETED",
            "cache_reconciliation_retry_state": "COMPLETED",
            "cache_reconciliation_next_retry_at": None,
            "cache_reconciliation_completed_at": now.isoformat(),
        }
    if attempts >= CACHE_RECONCILIATION_MAX_ATTEMPTS:
        return {
            **common,
            "cache_invalidation_status": "RECONCILIATION_REQUIRED",
            "cache_reconciliation_retry_state": "EXHAUSTED",
            "cache_reconciliation_next_retry_at": None,
        }
    delay = CACHE_RECONCILIATION_BACKOFF_SECONDS[attempts - 1]
    return {
        **common,
        "cache_invalidation_status": "RECONCILIATION_REQUIRED",
        "cache_reconciliation_retry_state": "SCHEDULED",
        "cache_reconciliation_next_retry_at": (
            now + timedelta(seconds=delay)
        ).isoformat(),
    }


def _cache_reconciliation_payload(
    plan: CopilotActionPlan,
    kind: str,
) -> dict[str, Any] | None:
    result = dict(plan.execution_result or {})
    if kind == "EXECUTION" and plan.status == "EXECUTED":
        return result
    if kind == "ROLLBACK" and plan.status == "ROLLED_BACK":
        rollback_result = result.get("rollback")
        return dict(rollback_result) if isinstance(rollback_result, dict) else None
    return None


def cache_reconciliation_outcome(
    plan_dict: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    execution_result = dict(plan_dict.get("execution_result") or {})
    target = (
        execution_result
        if kind == "EXECUTION"
        else dict(execution_result.get("rollback") or {})
    )
    return {
        "status": target.get("cache_invalidation_status"),
        "retry_state": target.get("cache_reconciliation_retry_state"),
        "attempts": target.get("cache_reconciliation_attempts", 0),
        "max_attempts": target.get(
            "cache_reconciliation_max_attempts",
            CACHE_RECONCILIATION_MAX_ATTEMPTS,
        ),
        "next_retry_at": target.get("cache_reconciliation_next_retry_at"),
    }


async def claim_due_cache_reconciliations(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 50,
    lease_seconds: int = 120,
) -> list[dict[str, str]]:
    """Claim durable JSON outbox entries for the isolated AI worker."""
    claimed_at = now or _now()
    execution_status = CopilotActionPlan.execution_result[
        "cache_invalidation_status"
    ].astext
    execution_retry_state = CopilotActionPlan.execution_result[
        "cache_reconciliation_retry_state"
    ].astext
    rollback_status = CopilotActionPlan.execution_result["rollback"][
        "cache_invalidation_status"
    ].astext
    rollback_retry_state = CopilotActionPlan.execution_result["rollback"][
        "cache_reconciliation_retry_state"
    ].astext
    rows = list((await db.execute(select(CopilotActionPlan).where(
        CopilotActionPlan.action_type == ACTION_TYPE,
        or_(
            and_(
                CopilotActionPlan.status == "EXECUTED",
                execution_status.in_((
                    "PENDING_AFTER_COMMIT",
                    "RECONCILIATION_REQUIRED",
                )),
                or_(
                    execution_retry_state.is_(None),
                    execution_retry_state != "EXHAUSTED",
                ),
            ),
            and_(
                CopilotActionPlan.status == "ROLLED_BACK",
                rollback_status.in_((
                    "PENDING_AFTER_COMMIT",
                    "RECONCILIATION_REQUIRED",
                )),
                or_(
                    rollback_retry_state.is_(None),
                    rollback_retry_state != "EXHAUSTED",
                ),
            ),
        ),
    ).order_by(CopilotActionPlan.executed_at, CopilotActionPlan.id)
        .with_for_update(skip_locked=True)
        .limit(max(1, min(int(limit), 200))))).scalars().all())

    specs: list[dict[str, str]] = []
    lease_until = claimed_at + timedelta(seconds=max(30, int(lease_seconds)))
    for plan in rows:
        kind = "EXECUTION" if plan.status == "EXECUTED" else "ROLLBACK"
        target = _cache_reconciliation_payload(plan, kind)
        if target is None or target.get("resource_type") != "CONFIG_PROFILE":
            continue
        if not _cache_reconciliation_is_due(
            target,
            now=claimed_at,
            honor_dispatch_lease=True,
        ):
            continue
        target = {
            **target,
            "cache_reconciliation_retry_state": "DISPATCHED",
            "cache_reconciliation_dispatch_lease_until": lease_until.isoformat(),
        }
        result = dict(plan.execution_result or {})
        if kind == "EXECUTION":
            result = target
        else:
            result["rollback"] = target
        plan.execution_result = result
        specs.append({
            "user_id": str(plan.user_id),
            "plan_id": str(plan.id),
            "kind": kind,
        })
        db.add(CopilotAuditLog(
            user_id=plan.user_id,
            event_type="ANALYSIS_CHAT_CACHE_RECONCILIATION_DISPATCH_CLAIMED",
            actor_user_id=None,
            action_plan_id=plan.id,
            payload={
                "kind": kind,
                "cache_reconciliation_attempts": _cache_reconciliation_attempts(
                    target
                ),
                "cache_reconciliation_dispatch_lease_until": (
                    lease_until.isoformat()
                ),
            },
        ))
    await db.flush()
    return specs


async def reconcile_cache_outbox_attempt(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    kind: str,
) -> dict[str, Any]:
    """Run one idempotent cache attempt named by a claimed outbox item."""
    if kind == "EXECUTION":
        plan = await get_plan(db, user_id, plan_id)
        decision_id = (plan.execution_result or {}).get("approval_decision_id")
        return await reconcile_execution_cache(
            db,
            user_id,
            plan_id,
            decision_id=decision_id,
        )
    if kind == "ROLLBACK":
        return await reconcile_rollback_cache(db, user_id, plan_id)
    raise GovernedExecutionFenceError(
        "ANALYSIS_CHAT_CACHE_RECONCILIATION_KIND_INVALID"
    )


async def reconcile_execution_cache(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
    *,
    decision_id: str | UUID | None,
) -> dict[str, Any]:
    """Reconcile a committed ConfigProfile write with its runtime cache.

    This is deliberately a separate transaction from ``approve_and_execute``.
    Redis invalidation is idempotent; if it succeeds but this bookkeeping
    transaction later fails, a retry is safe.  Cache failure never changes the
    already-durable operational result from EXECUTED to FAILED.
    """
    try:
        persisted_decision_id = str(UUID(str(decision_id)))
    except (TypeError, ValueError) as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_REQUIRED"
        ) from exc

    plan = await get_plan(db, user_id, plan_id, lock=True)
    result = dict(plan.execution_result or {})
    if plan.status != "EXECUTED" or result.get("status") != "EXECUTED":
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_NOT_EXECUTED"
        )
    if result.get("approval_decision_id") != persisted_decision_id:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_GOVERNED_CHANGE_DECISION_CONFLICT"
        )

    cache_status = result.get("cache_invalidation_status")
    if cache_status == "COMPLETED" or result.get("resource_type") != "CONFIG_PROFILE":
        return plan_to_dict(plan)
    if cache_status not in {"PENDING_AFTER_COMMIT", "RECONCILIATION_REQUIRED"}:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_STATE_INVALID"
        )
    now = _now()
    if not _cache_reconciliation_is_due(
        result,
        now=now,
        honor_dispatch_lease=False,
    ):
        return plan_to_dict(plan)

    config_type = str(result.get("config_type") or "")
    if config_type not in ALLOWED_CONFIG_TYPES:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_TARGET_INVALID"
        )
    raw_pool_id = (plan.execution_payload or {}).get("pool_id")
    try:
        pool_id = UUID(str(raw_pool_id)) if raw_pool_id else None
    except (TypeError, ValueError) as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_CACHE_RECONCILIATION_TARGET_INVALID"
        ) from exc

    try:
        invalidated = await config_service.invalidate_cache(
            config_type,
            user_id,
            pool_id,
            strict=True,
        )
    except Exception:
        succeeded = False
        event_type = "ANALYSIS_CHAT_CACHE_INVALIDATION_RECONCILIATION_REQUIRED"
    else:
        succeeded = invalidated is True
        event_type = (
            "ANALYSIS_CHAT_CACHE_INVALIDATION_COMPLETED"
            if succeeded
            else "ANALYSIS_CHAT_CACHE_INVALIDATION_RECONCILIATION_REQUIRED"
        )

    result = _cache_reconciliation_result(
        result,
        succeeded=succeeded,
        now=now,
    )
    cache_status = result["cache_invalidation_status"]
    plan.execution_result = result
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type=event_type,
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={
            "approval_decision_id": persisted_decision_id,
            "config_type": config_type,
            "resource_id": result.get("resource_id"),
            "cache_invalidation_status": cache_status,
            "cache_reconciliation_attempts": result[
                "cache_reconciliation_attempts"
            ],
            "cache_reconciliation_retry_state": result[
                "cache_reconciliation_retry_state"
            ],
            "cache_reconciliation_next_retry_at": result[
                "cache_reconciliation_next_retry_at"
            ],
        },
    ))
    await db.flush()
    return plan_to_dict(plan)


async def reconcile_rollback_cache(
    db: AsyncSession,
    user_id: UUID,
    plan_id: UUID,
) -> dict[str, Any]:
    """Reconcile cache only after the rollback transaction is durable."""
    plan = await get_plan(db, user_id, plan_id, lock=True)
    result = dict(plan.execution_result or {})
    rollback_result = dict(result.get("rollback") or {})
    if plan.status != "ROLLED_BACK" or rollback_result.get("status") != "ROLLED_BACK":
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_ROLLBACK_CACHE_RECONCILIATION_NOT_ROLLED_BACK"
        )

    cache_status = rollback_result.get("cache_invalidation_status")
    if cache_status == "COMPLETED" or rollback_result.get("resource_type") != "CONFIG_PROFILE":
        return plan_to_dict(plan)
    if cache_status not in {"PENDING_AFTER_COMMIT", "RECONCILIATION_REQUIRED"}:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_ROLLBACK_CACHE_RECONCILIATION_STATE_INVALID"
        )
    now = _now()
    if not _cache_reconciliation_is_due(
        rollback_result,
        now=now,
        honor_dispatch_lease=False,
    ):
        return plan_to_dict(plan)

    config_type = str(rollback_result.get("config_type") or "")
    if config_type not in ALLOWED_CONFIG_TYPES:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_ROLLBACK_CACHE_RECONCILIATION_TARGET_INVALID"
        )
    raw_pool_id = rollback_result.get("pool_id")
    try:
        pool_id = UUID(str(raw_pool_id)) if raw_pool_id else None
    except (TypeError, ValueError) as exc:
        raise GovernedExecutionFenceError(
            "ANALYSIS_CHAT_ROLLBACK_CACHE_RECONCILIATION_TARGET_INVALID"
        ) from exc

    try:
        invalidated = await config_service.invalidate_cache(
            config_type,
            user_id,
            pool_id,
            strict=True,
        )
    except Exception:
        succeeded = False
        event_type = "ANALYSIS_CHAT_ROLLBACK_CACHE_INVALIDATION_RECONCILIATION_REQUIRED"
    else:
        succeeded = invalidated is True
        event_type = (
            "ANALYSIS_CHAT_ROLLBACK_CACHE_INVALIDATION_COMPLETED"
            if succeeded
            else "ANALYSIS_CHAT_ROLLBACK_CACHE_INVALIDATION_RECONCILIATION_REQUIRED"
        )

    rollback_result = _cache_reconciliation_result(
        rollback_result,
        succeeded=succeeded,
        now=now,
    )
    cache_status = rollback_result["cache_invalidation_status"]
    plan.execution_result = {**result, "rollback": rollback_result}
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type=event_type,
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload={
            "config_type": config_type,
            "resource_id": rollback_result.get("resource_id"),
            "cache_invalidation_status": cache_status,
            "cache_reconciliation_attempts": rollback_result[
                "cache_reconciliation_attempts"
            ],
            "cache_reconciliation_retry_state": rollback_result[
                "cache_reconciliation_retry_state"
            ],
            "cache_reconciliation_next_retry_at": rollback_result[
                "cache_reconciliation_next_retry_at"
            ],
        },
    ))
    await db.flush()
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
    if not source:
        raise ValueError("Rollback snapshot is missing or corrupted")
    candidate_hash = str(result.get("new_document_hash") or "")
    now = _now()
    cache_type: str | None = None
    if payload.get("operation_type") == "SET_PROFILE_ACTIVE_STATUS":
        profile_ids = [UUID(str(item)) for item in payload.get("profile_ids") or []]
        resources = list((await db.execute(select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(profile_ids),
        ).order_by(Profile.id).with_for_update())).scalars().all())
        if {resource.id for resource in resources} != set(profile_ids):
            raise LookupError("One or more profiles were not found")
        current = {
            "profiles": [
                {
                    "profile_id": str(resource.id),
                    "profile_name": resource.name,
                    "is_active": bool(resource.is_active),
                }
                for resource in resources
            ]
        }
        if document_hash(current) != candidate_hash:
            raise ValueError("A profile changed after execution; rollback would overwrite newer work")
        source_rows = {
            UUID(str(item["profile_id"])): item
            for item in source.get("profiles") or []
        }
        for resource in resources:
            row = source_rows.get(resource.id)
            if row is None or not isinstance(row.get("is_active"), bool):
                raise ValueError("Bulk profile rollback snapshot is incomplete")
            resource.is_active = row["is_active"]
            resource.profile_version = now
            resource.updated_at = now
    elif payload.get("operation_type") == "UPDATE_PROFILE_CONFIG_SET":
        profile_ids = [UUID(str(item)) for item in payload.get("profile_ids") or []]
        resources = list((await db.execute(select(Profile).where(
            Profile.user_id == user_id,
            Profile.id.in_(profile_ids),
        ).order_by(Profile.id).with_for_update())).scalars().all())
        if {resource.id for resource in resources} != set(profile_ids):
            raise LookupError("One or more profiles were not found")
        current = {
            "profiles": [
                {
                    "profile_id": str(resource.id),
                    "profile_name": resource.name,
                    "config": deepcopy(resource.config or {}),
                }
                for resource in resources
            ]
        }
        if document_hash(current) != candidate_hash:
            raise ValueError("A profile changed after execution; rollback would overwrite newer work")
        source_rows = {
            UUID(str(item["profile_id"])): item
            for item in source.get("profiles") or []
        }
        for resource in resources:
            row = source_rows.get(resource.id)
            if row is None or not isinstance(row.get("config"), dict):
                raise ValueError("Bulk profile configuration rollback snapshot is incomplete")
            restored = _validate_profile_config(deepcopy(row["config"]))
            previous = deepcopy(resource.config or {})
            previous_version = resource.profile_version
            resource.config = restored
            resource.profile_version = now
            resource.updated_at = now
            db.add(ProfileAuditLog(
                user_id=user_id,
                profile_id=resource.id,
                changed_by=user_id,
                change_source="analysis_chat_human_confirmed_bulk_rollback",
                change_description=f"Rollback governed Analysis Chat proposal {plan.id}",
                previous_config=previous,
                new_config=restored,
                previous_profile_version=previous_version,
                new_profile_version=now,
            ))
    elif payload.get("operation_type") == "UPDATE_PROFILE_CONFIG":
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
    if cache_type:
        rollback_result.update({
            "resource_type": "CONFIG_PROFILE",
            "config_type": cache_type,
            "pool_id": str(resource.pool_id) if resource.pool_id else None,
            **_pending_cache_reconciliation(now),
        })
    else:
        rollback_result.update(_profile_cache_not_required())
    plan.status = "ROLLED_BACK"
    plan.execution_result = {**result, "rollback": rollback_result}
    db.add(CopilotAuditLog(
        user_id=user_id,
        event_type="ANALYSIS_CHAT_CHANGE_ROLLED_BACK",
        actor_user_id=user_id,
        action_plan_id=plan.id,
        payload=rollback_result,
    ))
    await db.flush()
    return plan_to_dict(plan)

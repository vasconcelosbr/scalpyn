"""Server-authoritative point-in-time dataset and Spot MTF calibration."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from statistics import median
from typing import Any, Mapping
from uuid import UUID, uuid4

from sqlalchemy import text

from .mtf_walk_forward import (
    candidate_grid,
    chronological_folds,
    evaluate_candidate,
    evaluate_returns,
    expectancy_delta_confidence_interval,
    fit_candidate,
    require_calibration_config,
    select_candidate,
)
from .profile_runtime_config import canonical_hash


_TF_SECONDS = {"1h": 3600, "15m": 900}
_DATASET_CONTRACT = "mtf_point_in_time_dataset_v1"
_L2_DERIVED_FEATURES = {
    "extension_atr", "ema21_distance_atr", "breakout_distance_atr",
    "retest_distance_atr", "invalidation_distance_atr",
}


def derive_l2_geometry_features(values: Mapping[str, Any]) -> dict[str, float]:
    atr = float(values["atr"])
    if atr <= 0:
        raise ValueError("INDICATOR_VALUE_INVALID:atr")
    price = float(values["price"])
    ema21 = float(values["ema21"])
    bb_upper = float(values["bb_upper"])
    vwap = float(values["vwap"])
    return {
        "extension_atr": abs(price - ema21) / atr,
        "ema21_distance_atr": abs(price - ema21) / atr,
        "breakout_distance_atr": max(0.0, (price - bb_upper) / atr),
        "retest_distance_atr": abs(price - bb_upper) / atr,
        "invalidation_distance_atr": abs(price - min(ema21, vwap)) / atr,
    }


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _path(payload: Mapping[str, Any], path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _validated_features(
    payload: Mapping[str, Any], *, timeframe: str, decision_at: datetime,
    required_features: set[str], source_identity: Mapping[str, Any],
    enforce_fresh_at_decision: bool = True,
) -> tuple[dict[str, float | bool], str | None, datetime]:
    values: dict[str, float | bool] = {}
    config_hashes: set[str] = set()
    source_times: set[datetime] = set()
    allowed_capture_contracts = {
        str(value)
        for value in source_identity.get("allowed_capture_contract_versions") or []
    }
    allowed_providers = {
        str(value)
        for value in source_identity.get("allowed_source_providers") or []
    }
    provider_policy_id = str(source_identity.get("provider_policy_id") or "")
    scheduler_group = str(source_identity.get("scheduler_group") or "")
    expected_config_profile_id = str(
        source_identity.get("indicator_config_profile_id") or ""
    )
    expected_config_hash = str(source_identity.get("indicator_config_hash") or "")
    allowed_producer_versions = {
        str(value) for value in source_identity.get("allowed_producer_versions") or []
    }
    validity_margin = source_identity.get("validity_margin_seconds")
    if (
        not allowed_capture_contracts or not allowed_providers or not provider_policy_id
        or scheduler_group != "structural" or not expected_config_profile_id
        or not expected_config_hash or not allowed_producer_versions
    ):
        raise ValueError("SOURCE_IDENTITY_CONFIG_REQUIRED")
    if validity_margin is None:
        raise ValueError("VALIDITY_MARGIN_CONFIG_REQUIRED")
    for feature in sorted(required_features):
        envelope = payload.get(feature)
        if not isinstance(envelope, Mapping):
            raise ValueError(f"FEATURE_UNAVAILABLE:{feature}")
        material = dict(envelope)
        expected_hash = material.pop("envelope_hash", None)
        if not expected_hash or expected_hash != canonical_hash(material):
            raise ValueError(f"FEATURE_HASH_INVALID:{feature}")
        if material.get("timeframe") != timeframe or material.get("market_type") != "spot":
            raise ValueError(f"FEATURE_IDENTITY_INVALID:{feature}")
        if material.get("scheduler_group") != scheduler_group:
            raise ValueError(f"FEATURE_GROUP_INVALID:{feature}")
        if str(material.get("source_provider")) not in allowed_providers:
            raise ValueError(f"FEATURE_PROVIDER_INVALID:{feature}")
        if str(material.get("provider_policy_id")) != provider_policy_id:
            raise ValueError(f"FEATURE_PROVIDER_POLICY_INVALID:{feature}")
        if str(material.get("config_profile_id") or "") != expected_config_profile_id:
            raise ValueError(f"FEATURE_CONFIG_PROFILE_INVALID:{feature}")
        if str(material.get("config_hash") or "") != expected_config_hash:
            raise ValueError(f"FEATURE_CONFIG_HASH_INVALID:{feature}")
        if str(material.get("producer_version") or "") not in allowed_producer_versions:
            raise ValueError(f"FEATURE_PRODUCER_VERSION_INVALID:{feature}")
        if material.get("candle_closed") is not True or material.get("candle_policy") != "CLOSED_ONLY":
            raise ValueError(f"FEATURE_OPEN_CANDLE:{feature}")
        if str(material.get("capture_contract_version")) not in allowed_capture_contracts:
            raise ValueError(f"FEATURE_CAPTURE_CONTRACT_INVALID:{feature}")
        source_at = _utc(material.get("source_timestamp"))
        available_at = _utc(material.get("available_at"))
        if source_at.timestamp() + _TF_SECONDS[timeframe] > decision_at.timestamp():
            raise ValueError(f"FEATURE_OPEN_AT_DECISION:{feature}")
        if available_at > decision_at:
            raise ValueError(f"FEATURE_NOT_AVAILABLE_AT_DECISION:{feature}")
        if enforce_fresh_at_decision and decision_at > source_at + timedelta(
            seconds=_TF_SECONDS[timeframe] + int(validity_margin)
        ):
            raise ValueError(f"FEATURE_EXPIRED_AT_DECISION:{feature}")
        value = material.get("value")
        if isinstance(value, bool):
            values[feature] = value
        elif value is None or not math.isfinite(float(value)):
            raise ValueError(f"FEATURE_VALUE_INVALID:{feature}")
        else:
            values[feature] = float(value)
        config_hashes.add(str(material.get("config_hash") or ""))
        source_times.add(source_at)
    if "" in config_hashes or len(config_hashes) != 1 or len(source_times) != 1:
        raise ValueError("FEATURE_LINEAGE_CONFLICT")
    return values, next(iter(config_hashes)), next(iter(source_times))


async def load_approved_policy(db, *, user_id: UUID) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = (await db.execute(text("""
        SELECT id, config_json, updated_at
          FROM config_profiles
         WHERE user_id = CAST(:user_id AS UUID)
           AND pool_id IS NULL
           AND config_type = 'mtf_calibration'
           AND is_active IS TRUE
         ORDER BY updated_at DESC, id
    """), {"user_id": str(user_id)})).mappings().all()
    if len(rows) != 1:
        raise ValueError(f"CONFIG_REQUIRED:mtf_calibration_cardinality={len(rows)}")
    config = require_calibration_config(dict(rows[0]["config_json"] or {}))
    return config, {
        "config_profile_id": str(rows[0]["id"]),
        "config_hash": canonical_hash(config),
        "updated_at": rows[0]["updated_at"].isoformat(),
    }


async def audit_policy_availability(db, *, user_id: UUID) -> dict[str, Any]:
    """Bounded facts used to draft a policy; this never writes configuration."""
    shadow = (await db.execute(text("""
        SELECT count(*) AS completed_rows,
               min(entry_timestamp) AS first_entry_at,
               max(entry_timestamp) AS last_entry_at
          FROM shadow_trades
         WHERE user_id = CAST(:user_id AS UUID)
           AND status = 'COMPLETED' AND pnl_pct IS NOT NULL
           AND entry_timestamp IS NOT NULL AND exit_timestamp IS NOT NULL
    """), {"user_id": str(user_id)})).mappings().one()
    indicators = (await db.execute(text("""
        SELECT timeframe, count(*) AS snapshots,
               count(DISTINCT symbol) AS symbols,
               min(time) AS first_computed_at, max(time) AS last_computed_at,
               percentile_cont(0.5) WITHIN GROUP (
                 ORDER BY EXTRACT(EPOCH FROM (
                   time - CASE
                     WHEN indicators_json->'price'->>'source_timestamp'
                          ~ '^\\d{4}-\\d{2}-\\d{2}T'
                     THEN (indicators_json->'price'->>'source_timestamp')::timestamptz
                     ELSE NULL
                   END
                 ))
               ) FILTER (WHERE indicators_json ? 'price') AS median_latency_seconds
          FROM indicators
         WHERE market_type = 'spot' AND scheduler_group = 'structural'
           AND timeframe IN ('1h','15m')
           AND time > now() - interval '30 days'
         GROUP BY timeframe ORDER BY timeframe
    """))).mappings().all()
    return {
        "status": "PROPOSAL_INPUTS_ONLY",
        "requires_human_approval": True,
        "shadow_population": dict(shadow),
        "mtf_indicator_availability_30d": [dict(row) for row in indicators],
        "proposed_policy": None,
        "reason": "STATISTICAL_POLICY_VALUES_REQUIRE_EVIDENCE_REVIEW_AND_HUMAN_APPROVAL",
    }


async def approve_policy(
    db, *, user_id: UUID, payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Persist only an explicitly confirmed, fully validated policy."""
    if payload.get("approval_confirmed") is not True:
        raise ValueError("EXPLICIT_POLICY_APPROVAL_REQUIRED")
    candidate = deepcopy(dict(payload))
    candidate.pop("approval_confirmed", None)
    candidate["approval_status"] = "APPROVED"
    candidate["approved_by"] = str(user_id)
    candidate["approved_at"] = datetime.now(timezone.utc).isoformat()
    validated = require_calibration_config(candidate)
    config_hash = canonical_hash(validated)
    from ..models.config_profile import ConfigAuditLog, ConfigProfile

    current = (await db.execute(text("""
        SELECT id, config_json FROM config_profiles
         WHERE user_id = CAST(:user_id AS UUID) AND pool_id IS NULL
           AND config_type = 'mtf_calibration' AND is_active IS TRUE
         FOR UPDATE
    """), {"user_id": str(user_id)})).mappings().all()
    await db.execute(text("""
        UPDATE config_profiles SET is_active = FALSE, updated_at = clock_timestamp()
         WHERE user_id = CAST(:user_id AS UUID) AND pool_id IS NULL
           AND config_type = 'mtf_calibration' AND is_active IS TRUE
    """), {"user_id": str(user_id)})
    profile = ConfigProfile(
        user_id=user_id, pool_id=None, config_type="mtf_calibration",
        config_json=validated, is_active=True,
    )
    db.add(profile)
    await db.flush()
    db.add(ConfigAuditLog(
        config_id=profile.id, changed_by=user_id,
        previous_json=dict(current[0]["config_json"] or {}) if len(current) == 1 else None,
        new_json=validated,
        change_description="[MTF_CALIBRATION_POLICY] explicit human approval",
    ))
    await db.commit()
    return {
        "status": "APPROVED", "config_profile_id": str(profile.id),
        "config_hash": config_hash, "policy_version": validated["policy_version"],
        "approved_at": validated["approved_at"],
    }


async def get_run(db, *, user_id: UUID, run_id: UUID) -> dict[str, Any]:
    row = (await db.execute(text("""
        SELECT id, status, policy_version, approved_policy_hash,
               dataset_manifest, dataset_hash, results_json, selected_profiles,
               failure_reason, started_at, completed_at
          FROM mtf_calibration_runs
         WHERE id = CAST(:run_id AS UUID) AND user_id = CAST(:user_id AS UUID)
    """), {"run_id": str(run_id), "user_id": str(user_id)})).mappings().one_or_none()
    if row is None:
        raise ValueError("MTF_CALIBRATION_RUN_NOT_FOUND")
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in dict(row).items()
    }


async def build_point_in_time_dataset(
    db, *, user_id: UUID, policy: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    required_by_tf: dict[str, set[str]] = {
        "1h": {
            "adx", "atr_pct", "di_plus", "di_minus", "ema21", "ema50",
            "ema21_slope_pct", "ema50_slope_pct", "higher_highs_5",
            "higher_lows_5",
        },
        "15m": {
            "price", "atr", "ema21", "ema50", "vwap",
            "vwap_reclaim_bool", "bb_upper", "bb_lower", "di_plus",
            "di_minus", "higher_highs_5", "higher_lows_5", "adx",
            "volume_spike", "bb_width",
        },
    }
    for dimension in policy["candidate_dimensions"]:
        feature_name = str(dimension.get("feature") or "").split(".", 1)[-1]
        if feature_name and feature_name not in _L2_DERIVED_FEATURES:
            required_by_tf["1h" if dimension["layer"] == "L1" else "15m"].add(
                feature_name
            )
    requested = (
        int(policy["train_window_rows"])
        + int(policy["test_window_rows"]) * int(policy["fold_count"])
        + int(policy["embargo_rows"]) * int(policy["fold_count"])
        + int(policy["validation_holdout_rows"])
    )
    await db.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{int(policy['dataset_query_timeout_ms'])}ms"},
    )
    rows = (await db.execute(text("""
        WITH population AS (
          SELECT st.id, st.symbol, st.entry_timestamp AS decision_at,
                 st.exit_timestamp AS outcome_at, st.pnl_pct, st.pnl_usdt,
                 st.net_return_pct, st.fee_roundtrip_pct_applied,
                 st.amount_usdt,
                 st.exit_metrics_json, st.config_snapshot,
                 st.profile_id, st.profile_version_id, st.profile_config_hash
            FROM shadow_trades st
           WHERE st.user_id = CAST(:user_id AS UUID)
             AND st.status = 'COMPLETED'
             AND st.pnl_pct IS NOT NULL
             AND st.entry_timestamp IS NOT NULL
             AND st.exit_timestamp IS NOT NULL
           ORDER BY st.entry_timestamp DESC, st.id DESC
           LIMIT :requested
        )
        SELECT p.*,
               l1.indicators_json AS l1_indicators, l1.time AS l1_computed_at,
               l2.history AS l2_history,
               (pv.id IS NOT NULL) AS profile_lineage_verified
          FROM population p
          LEFT JOIN profile_versions pv
            ON pv.id = p.profile_version_id
           AND pv.profile_id = p.profile_id
           AND pv.config_hash = p.profile_config_hash
          LEFT JOIN LATERAL (
            SELECT i.time, i.indicators_json
              FROM indicators i
             WHERE i.symbol = p.symbol AND i.market_type = 'spot'
               AND i.timeframe = '1h' AND i.scheduler_group = 'structural'
               AND i.time <= p.decision_at
             ORDER BY i.time DESC LIMIT 1
          ) l1 ON TRUE
          LEFT JOIN LATERAL (
            SELECT jsonb_agg(
                     jsonb_build_object(
                       'computed_at', recent.time,
                       'indicators', recent.indicators_json
                     )
                     ORDER BY recent.source_timestamp
                   ) AS history
              FROM (
                SELECT DISTINCT ON (
                         i.indicators_json->'price'->>'source_timestamp'
                       )
                       i.time, i.indicators_json,
                       (i.indicators_json->'price'->>'source_timestamp')::timestamptz
                         AS source_timestamp
                  FROM indicators i
                 WHERE i.symbol = p.symbol AND i.market_type = 'spot'
                   AND i.timeframe = '15m' AND i.scheduler_group = 'structural'
                   AND i.time <= p.decision_at
                   AND i.indicators_json->'price'->>'source_timestamp'
                       ~ '^\\d{4}-\\d{2}-\\d{2}T'
                 ORDER BY
                   i.indicators_json->'price'->>'source_timestamp' DESC,
                   i.time DESC
                 LIMIT :l2_history_candles
              ) recent
          ) l2 ON TRUE
         ORDER BY p.decision_at, p.id
    """), {
        "user_id": str(user_id),
        "requested": requested,
        "l2_history_candles": int(policy["l2_history_candles"]),
    })).mappings().all()

    source_identity = {
        layer: dict((policy["profile_templates"][layer].get("source_identity") or {}))
        for layer in ("L1", "L2")
    }
    accepted: list[dict[str, Any]] = []
    discard_reasons: dict[str, int] = {}
    for source in rows:
        try:
            decision_at = _utc(source["decision_at"])
            if source.get("profile_lineage_verified") is not True:
                raise ValueError("PROFILE_LINEAGE_INVALID")
            if not isinstance(source.get("config_snapshot"), Mapping):
                raise ValueError("HISTORICAL_CONFIG_UNAVAILABLE")
            l1, l1_hash, _ = _validated_features(
                dict(source["l1_indicators"] or {}), timeframe="1h",
                decision_at=decision_at, required_features=required_by_tf["1h"],
                source_identity=source_identity["L1"],
            )
            raw_history = source.get("l2_history") or []
            if isinstance(raw_history, str):
                raw_history = json.loads(raw_history)
            if not isinstance(raw_history, list) or not raw_history:
                raise ValueError("L2_HISTORY_UNAVAILABLE")
            l2_history: list[dict[str, Any]] = []
            l2_hashes: set[str] = set()
            for item in raw_history:
                l2_values, l2_hash, candle_open_at = _validated_features(
                    dict((item or {}).get("indicators") or {}), timeframe="15m",
                    decision_at=decision_at,
                    required_features=required_by_tf["15m"],
                    source_identity=source_identity["L2"],
                    enforce_fresh_at_decision=False,
                )
                l2_history.append({
                    "candle_open_at": candle_open_at,
                    "values": l2_values,
                })
                l2_hashes.add(str(l2_hash))
            l2_history.sort(key=lambda item: item["candle_open_at"])
            if len(l2_hashes) != 1:
                raise ValueError("L2_HISTORY_CONFIG_CONFLICT")
            l2 = l2_history[-1]["values"]
            l2_hash = next(iter(l2_hashes))
            latest_l2_open = l2_history[-1]["candle_open_at"]
            if decision_at > latest_l2_open + timedelta(
                seconds=_TF_SECONDS["15m"]
                + int(source_identity["L2"]["validity_margin_seconds"])
            ):
                raise ValueError("L2_CURRENT_FEATURE_EXPIRED_AT_DECISION")
            source_payload = dict(source)
            raw_return = _path(source_payload, str(policy["return_field"]))
            raw_cost = _path(source_payload, str(policy["cost_field"]))
            if raw_return is None or raw_cost is None:
                raise ValueError("COST_OR_RETURN_UNAVAILABLE")
            net_return = float(raw_return) - float(raw_cost)
            if not math.isfinite(net_return):
                raise ValueError("NET_RETURN_INVALID")
            l2_derived = derive_l2_geometry_features(l2)
            features = {
                **{f"L1.{name}": value for name, value in l1.items()},
                **{f"L2.{name}": value for name, value in l2.items()},
                **{f"L2.{name}": value for name, value in l2_derived.items()},
            }
            accepted.append({
                "id": str(source["id"]), "symbol": source["symbol"],
                "decision_at": decision_at, "outcome_at": _utc(source["outcome_at"]),
                "net_return": net_return, "features": features,
                "l2_history": l2_history,
                "l1_config_hash": l1_hash, "l2_config_hash": l2_hash,
                "profile_version_id": str(source["profile_version_id"] or ""),
                "profile_config_hash": source["profile_config_hash"],
            })
        except (KeyError, TypeError, ValueError) as exc:
            reason = str(exc).split(":", 1)[0]
            discard_reasons[reason] = discard_reasons.get(reason, 0) + 1
    manifest_material = {
        "contract_version": _DATASET_CONTRACT,
        "sampling_unit": policy["sampling_unit"],
        "overlap_policy": policy["overlap_policy"],
        "capital_policy": policy["capital_policy"],
        "execution_policy": policy["execution_policy"],
        "policy_hash": canonical_hash(policy),
        "requested_rows": requested,
        "max_dataset_rows": int(policy["max_dataset_rows"]),
        "dataset_query_timeout_ms": int(policy["dataset_query_timeout_ms"]),
        "source_rows": len(rows),
        "accepted_rows": len(accepted),
        "discard_reasons": discard_reasons,
        "first_decision_at": accepted[0]["decision_at"].isoformat() if accepted else None,
        "last_decision_at": accepted[-1]["decision_at"].isoformat() if accepted else None,
        "row_hashes": [
            canonical_hash({
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in row.items()
            })
            for row in accepted
        ],
    }
    manifest = {**manifest_material, "dataset_hash": canonical_hash(manifest_material)}
    return accepted, manifest


def _profile_payloads(
    *, policy: Mapping[str, Any], fitted: Mapping[str, Any], run_id: str,
    policy_hash: str, results: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    by_layer = {"L1": [], "L2": []}
    for rule in fitted["rules"]:
        by_layer[rule["layer"]].append(dict(rule))
    for layer in ("L1", "L2"):
        payload = deepcopy(dict(policy["profile_templates"][layer]))
        payload["profile_kind"] = "MTF_LAYER"
        payload["layer"] = layer
        payload["activation_mode"] = "SHADOW"
        payload.setdefault("mtf_semantics", {})
        filters = payload.setdefault("filters", {"logic": "AND", "conditions": []})
        source_identity = dict(payload.get("source_identity") or {})
        timeframe = "1h" if layer == "L1" else "15m"
        timeframe_seconds = _TF_SECONDS[timeframe]
        source_provider = str(
            (source_identity.get("allowed_source_providers") or [""])[0]
        )
        for rule in by_layer[layer]:
            if str(rule.get("applies_to") or "BOTH") in {"FILTER", "BOTH"}:
                field = str(rule["feature"]).split(".", 1)[-1]
                filters.setdefault("conditions", []).append({
                    "field": field,
                    "operator": ">=" if rule["operator"] == "min" else "<=",
                    "value": rule["threshold"],
                    "timeframe": timeframe,
                    "source": "ohlcv",
                    "source_provider": source_provider,
                    "provider_policy_id": source_identity.get("provider_policy_id"),
                    "max_age_seconds": (
                        timeframe_seconds
                        + int(source_identity["validity_margin_seconds"])
                    ),
                    "candle_policy": "CLOSED_ONLY",
                    "required": True,
                })
            if rule.get("semantic_key"):
                payload["mtf_semantics"][rule["semantic_key"]] = rule["threshold"]
        thresholds = {
            str(rule.get("semantic_key") or rule.get("feature")): rule["threshold"]
            for rule in by_layer[layer]
        }
        payload["calibration"] = {
            "status": "PASSED", "method": "WALK_FORWARD",
            "run_id": run_id, "policy_hash": policy_hash,
            "min_samples": int(policy["min_samples"]),
            "baseline_outperformed": True,
            "worst_fold_drawdown_not_worse": True,
            "thresholds": thresholds,
            "thresholds_hash": canonical_hash(thresholds),
            "dataset_hash": results["dataset_hash"],
        }
        payloads[layer] = payload
    return payloads


def _l2_temporal_eligibility(
    candidate: Mapping[str, Any], row: Mapping[str, Any],
) -> bool:
    """Replay the calibrated L2 state machine on closed historical candles."""
    semantics = {
        str(rule["semantic_key"]): rule["threshold"]
        for rule in candidate.get("rules") or []
        if rule.get("layer") == "L2" and rule.get("semantic_key")
    }
    required = {
        "max_extension_atr", "pullback_max_distance_atr",
        "breakout_min_distance_atr", "retest_tolerance_atr",
        "invalidation_atr", "setup_valid_candles", "adx_impulse_min",
        "volume_relative_min", "bb_width_compression_max",
        "bb_width_expansion_min",
    }
    if not required.issubset(semantics):
        return False
    from .mtf_observation_service import advance_l2_setup_state

    previous: Mapping[str, Any] | None = None
    try:
        for item in row.get("l2_history") or []:
            previous = advance_l2_setup_state(
                values=item["values"],
                candle_open_at=item["candle_open_at"],
                semantics=semantics,
                previous=previous,
            )
    except (KeyError, TypeError, ValueError):
        return False
    return bool(previous) and previous.get("state") in {
        "PULLBACK_RECLAIM", "BREAKOUT_RETEST",
    }


def _draft_profile_payloads(
    *, policy: Mapping[str, Any], run_id: str, policy_hash: str,
    dataset_hash: str, status: str, reason: str | None,
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for layer in ("L1", "L2"):
        payload = deepcopy(dict(policy["profile_templates"][layer]))
        payload.update({
            "profile_kind": "MTF_LAYER", "layer": layer,
            "activation_mode": "DRAFT",
        })
        payload["calibration"] = {
            "status": status, "method": "WALK_FORWARD", "run_id": run_id,
            "policy_hash": policy_hash, "dataset_hash": dataset_hash,
            "min_samples": int(policy["min_samples"]),
            "thresholds_emitted": False, "reason": reason,
        }
        payloads[layer] = {"payload": payload, "thresholds_hash": None}
    return payloads


async def run_calibration(db, *, user_id: UUID) -> dict[str, Any]:
    policy, identity = await load_approved_policy(db, user_id=user_id)
    rows, manifest = await build_point_in_time_dataset(db, user_id=user_id, policy=policy)
    idempotency_key = canonical_hash({
        "user_id": str(user_id), "policy_hash": identity["config_hash"],
        "dataset_hash": manifest["dataset_hash"],
    })
    existing = (await db.execute(text("""
        SELECT id, status, dataset_manifest, results_json, selected_profiles,
               failure_reason, completed_at
          FROM mtf_calibration_runs WHERE idempotency_key = :key
    """), {"key": idempotency_key})).mappings().one_or_none()
    if existing and existing["status"] != "RUNNING":
        return {key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in dict(existing).items()}

    run_id = existing["id"] if existing else uuid4()
    if not existing:
        await db.execute(text("""
        INSERT INTO mtf_calibration_runs (
          id, user_id, config_profile_id, config_hash, policy_version,
          approved_policy_at, approved_policy_hash, idempotency_key, status,
          dataset_manifest, dataset_hash
        ) VALUES (
          :id, :user_id, :config_profile_id, :config_hash, :policy_version,
          :approved_policy_at, :approved_policy_hash, :idempotency_key, 'RUNNING',
          CAST(:manifest AS JSONB), :dataset_hash
        )
        """), {
            "id": str(run_id), "user_id": str(user_id),
            "config_profile_id": identity["config_profile_id"],
            "config_hash": identity["config_hash"], "policy_version": policy["policy_version"],
            "approved_policy_at": _utc(policy["approved_at"]),
            "approved_policy_hash": identity["config_hash"], "idempotency_key": idempotency_key,
            "manifest": json.dumps(manifest), "dataset_hash": manifest["dataset_hash"],
        })
        await db.commit()

    status = "DRAFT_INSUFFICIENT_DATA"
    failure_reason: str | None = None
    results: dict[str, Any] = {"dataset_hash": manifest["dataset_hash"]}
    selected_profiles: dict[str, Any] = {}
    try:
        if len(rows) < int(policy["min_samples"]):
            failure_reason = "MIN_SAMPLES_NOT_MET"
        else:
            holdout_size = int(policy["validation_holdout_rows"])
            holdout_start = len(rows) - holdout_size
            while holdout_start > 0 and (
                rows[holdout_start - 1]["decision_at"]
                == rows[holdout_start]["decision_at"]
            ):
                holdout_start -= 1
            holdout = rows[holdout_start:]
            holdout_boundary = holdout[0]["decision_at"]
            development = [
                row for row in rows[:holdout_start]
                if row.get("outcome_at") is None
                or row["outcome_at"] < holdout_boundary
            ]
            folds = chronological_folds(
                development,
                train_size=int(policy["train_window_rows"]),
                test_size=int(policy["test_window_rows"]),
                fold_count=int(policy["fold_count"]),
                embargo_rows=int(policy["embargo_rows"]),
            )
            if len(folds) != int(policy["fold_count"]):
                failure_reason = "FOLD_COUNT_NOT_MET"
            else:
                templates = candidate_grid(policy)
                baseline_folds = [evaluate_returns(test) for _, test in folds]
                candidate_folds: dict[str, list[Any]] = {item["id"]: [] for item in templates}
                invalid_candidates: set[str] = set()
                for train, test in folds:
                    for template in templates:
                        if template["id"] in invalid_candidates:
                            continue
                        fitted = fit_candidate(template, train)
                        candidate_result = evaluate_candidate(
                            fitted,
                            test,
                            temporal_eligibility=_l2_temporal_eligibility,
                        )
                        if candidate_result.samples < int(policy["min_test_samples_per_fold"]):
                            candidate_folds[template["id"]] = []
                            invalid_candidates.add(template["id"])
                            continue
                        candidate_folds[template["id"]].append(candidate_result)
                selected_id = select_candidate(candidate_folds, baseline_folds=baseline_folds)
                if selected_id is None:
                    status = "DRAFT_REJECTED"
                    failure_reason = "NO_CANDIDATE_OUTPERFORMED_BASELINE"
                else:
                    template = next(item for item in templates if item["id"] == selected_id)
                    fitted = fit_candidate(template, development)
                    baseline_holdout = evaluate_returns(holdout)
                    candidate_holdout = evaluate_candidate(
                        fitted,
                        holdout,
                        temporal_eligibility=_l2_temporal_eligibility,
                    )
                    confidence_interval = expectancy_delta_confidence_interval(
                        candidate_folds[selected_id], baseline_folds,
                        confidence_level=float(policy["confidence_level"]),
                    )
                    holdout_passed = (
                        candidate_holdout.samples >= int(policy["min_test_samples_per_fold"])
                        and candidate_holdout.net_expectancy > baseline_holdout.net_expectancy
                        and candidate_holdout.max_drawdown <= baseline_holdout.max_drawdown
                    )
                    results.update({
                        "selected_candidate_id": selected_id,
                        "fitted_candidate": fitted,
                        "baseline_folds": [row.__dict__ for row in baseline_folds],
                        "candidate_folds": [row.__dict__ for row in candidate_folds[selected_id]],
                        "baseline_holdout": baseline_holdout.__dict__,
                        "candidate_holdout": candidate_holdout.__dict__,
                        "oos_expectancy_delta_confidence_interval": {
                            "confidence_level": float(policy["confidence_level"]),
                            "lower": confidence_interval[0],
                            "upper": confidence_interval[1],
                        },
                        "holdout_passed": holdout_passed,
                    })
                    if holdout_passed:
                        status = "PASSED"
                        profiles = _profile_payloads(
                            policy=policy, fitted=fitted, run_id=str(run_id),
                            policy_hash=identity["config_hash"], results=results,
                        )
                        selected_profiles = {
                            layer: {
                                "payload": payload,
                                "thresholds_hash": payload["calibration"]["thresholds_hash"],
                            }
                            for layer, payload in profiles.items()
                        }
                    else:
                        status = "DRAFT_REJECTED"
                        failure_reason = "FINAL_HOLDOUT_GATE_FAILED"
    except Exception as exc:
        status = "FAILED"
        failure_reason = f"{type(exc).__name__}:{exc}"

    if status != "PASSED":
        selected_profiles = _draft_profile_payloads(
            policy=policy, run_id=str(run_id), policy_hash=identity["config_hash"],
            dataset_hash=manifest["dataset_hash"], status=status,
            reason=failure_reason,
        )

    await db.execute(text("""
        UPDATE mtf_calibration_runs
           SET status = :status, results_json = CAST(:results AS JSONB),
               selected_profiles = CAST(:profiles AS JSONB),
               failure_reason = :failure_reason, completed_at = clock_timestamp()
         WHERE id = CAST(:id AS UUID)
    """), {
        "id": str(run_id), "status": status,
        "results": json.dumps(results, default=str),
        "profiles": json.dumps(selected_profiles, default=str),
        "failure_reason": failure_reason,
    })
    await db.commit()
    return {
        "id": str(run_id), "status": status, "dataset_manifest": manifest,
        "results_json": results, "selected_profiles": selected_profiles,
        "failure_reason": failure_reason,
    }

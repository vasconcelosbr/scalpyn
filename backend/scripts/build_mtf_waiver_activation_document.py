"""Build a compare-and-swap MTF SHADOW document with an explicit human waiver.

The script is read-only with respect to PostgreSQL.  It never marks a failed
calibration as PASSED and never emits a document capable of affecting orders.
Current profile execution/scoring sections are copied byte-for-byte at the
JSON level so the waiver cannot smuggle in an economic rule change.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import AsyncSessionLocal  # noqa: E402
from app.models.profile import Profile  # noqa: E402
from app.services.mtf_profile_activation_service import (  # noqa: E402
    WAIVER_IMPORT_MODE,
    parse_activation_document,
)
from app.services.profile_execution_contract import (  # noqa: E402
    EXECUTION_SECTIONS,
    load_profile_execution_snapshots,
)
from app.services.profile_runtime_config import canonical_hash  # noqa: E402


_DERIVED_L2 = {
    "adx_impulse_min": ("adx", "low"),
    "volume_relative_min": ("volume_spike", "low"),
    "bb_width_compression_max": ("bb_width", "low"),
    "bb_width_expansion_min": ("bb_width", "high"),
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--l1-profile-id", type=UUID, required=True)
    parser.add_argument("--l2-profile-id", type=UUID, required=True)
    parser.add_argument("--l1-watchlist-id", type=UUID, required=True)
    parser.add_argument("--l2-watchlist-id", type=UUID, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SystemExit("JSON_OBJECT_REQUIRED")
    return payload


def _proposal_profiles(payload: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    profiles = payload.get("profiles")
    if not isinstance(profiles, list):
        raise SystemExit("PROPOSAL_PROFILES_REQUIRED")
    by_layer = {
        str(item.get("layer") or "").upper(): dict(item)
        for item in profiles
        if isinstance(item, Mapping)
        and str(item.get("layer") or "").upper() in {"L1", "L2"}
    }
    if set(by_layer) != {"L1", "L2"}:
        raise SystemExit("PROPOSAL_L1_L2_REQUIRED")
    return by_layer


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise SystemExit("MTF_SEMANTIC_DISTRIBUTION_EMPTY")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _validated_value(
    envelope: Any, *, feature: str, source_identity: Mapping[str, Any]
) -> tuple[float, str]:
    if not isinstance(envelope, Mapping):
        raise ValueError(f"FEATURE_UNAVAILABLE:{feature}")
    material = dict(envelope)
    envelope_hash = material.pop("envelope_hash", None)
    if not envelope_hash or envelope_hash != canonical_hash(material):
        raise ValueError(f"FEATURE_HASH_INVALID:{feature}")
    if (
        material.get("timeframe") != "15m"
        or material.get("market_type") != "spot"
        or material.get("scheduler_group") != source_identity.get("scheduler_group")
        or material.get("source_provider")
        not in set(source_identity.get("allowed_source_providers") or [])
        or material.get("provider_policy_id") != source_identity.get("provider_policy_id")
        or str(material.get("config_profile_id") or "")
        != str(source_identity.get("indicator_config_profile_id") or "")
        or material.get("config_hash") != source_identity.get("indicator_config_hash")
        or material.get("producer_version")
        not in set(source_identity.get("allowed_producer_versions") or [])
        or material.get("capture_contract_version")
        not in set(source_identity.get("allowed_capture_contract_versions") or [])
        or material.get("candle_policy") != "CLOSED_ONLY"
        or material.get("candle_closed") is not True
    ):
        raise ValueError(f"FEATURE_IDENTITY_INVALID:{feature}")
    try:
        value = float(material.get("value"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"FEATURE_VALUE_INVALID:{feature}") from exc
    if not math.isfinite(value):
        raise ValueError(f"FEATURE_VALUE_INVALID:{feature}")
    return value, str(envelope_hash)


def _validated_l3_envelope(
    envelope: Any, *, feature: str, scheduler_group: str
) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise ValueError(f"FEATURE_UNAVAILABLE:{feature}")
    material = dict(envelope)
    envelope_hash = material.pop("envelope_hash", None)
    if not envelope_hash or envelope_hash != canonical_hash(material):
        raise ValueError(f"FEATURE_HASH_INVALID:{feature}")
    if (
        material.get("timeframe") != "5m"
        or material.get("market_type") != "spot"
        or material.get("scheduler_group") != scheduler_group
        or material.get("candle_policy") != "CLOSED_ONLY"
        or material.get("candle_closed") is not True
        or not material.get("source_provider")
        or not material.get("provider_policy_id")
        or not material.get("producer_version")
        or not material.get("capture_contract_version")
    ):
        raise ValueError(f"FEATURE_IDENTITY_INVALID:{feature}")
    value = material.get("value")
    if not isinstance(value, bool):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"FEATURE_VALUE_INVALID:{feature}")
    return {**material, "value": value, "envelope_hash": str(envelope_hash)}


def _identity_policy(envelopes: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not envelopes:
        return {}
    singular = {}
    for output_key, envelope_key in (
        ("provider_policy_id", "provider_policy_id"),
        ("indicator_config_profile_id", "config_profile_id"),
        ("indicator_config_hash", "config_hash"),
    ):
        values = {str(item.get(envelope_key) or "") for item in envelopes}
        if len(values) != 1 or "" in values:
            raise SystemExit(f"MTF_L3_{output_key.upper()}_CONFLICT")
        singular[output_key] = next(iter(values))
    return {
        "allowed_source_providers": sorted({
            str(item["source_provider"]) for item in envelopes
        }),
        **singular,
        "timeframe": "5m",
        "candle_policy": "CLOSED_ONLY",
        "allowed_capture_contract_versions": sorted({
            str(item["capture_contract_version"]) for item in envelopes
        }),
        "allowed_producer_versions": sorted({
            str(item["producer_version"]) for item in envelopes
        }),
    }


async def _run(args: argparse.Namespace) -> None:
    proposal = _proposal_profiles(_load_json(args.proposal))
    profile_ids = {"L1": args.l1_profile_id, "L2": args.l2_profile_id}
    watchlist_ids = {"L1": args.l1_watchlist_id, "L2": args.l2_watchlist_id}
    async with AsyncSessionLocal() as db:
        run = (await db.execute(text("""
            SELECT id, status, failure_reason, approved_policy_hash, dataset_hash,
                   dataset_manifest
              FROM mtf_calibration_runs
             WHERE id = CAST(:run_id AS UUID)
               AND user_id = CAST(:user_id AS UUID)
        """), {"run_id": str(args.run_id), "user_id": str(args.user_id)})).mappings().one_or_none()
        if run is None:
            raise SystemExit("MTF_CALIBRATION_RUN_NOT_FOUND")
        if (
            run["status"] != "DRAFT_INSUFFICIENT_DATA"
            or run["failure_reason"] != "MIN_SAMPLES_NOT_MET"
        ):
            raise SystemExit("MTF_WAIVER_RUN_NOT_ELIGIBLE")
        policy_row = (await db.execute(text("""
            SELECT config_json
              FROM config_profiles
             WHERE user_id = CAST(:user_id AS UUID) AND pool_id IS NULL
               AND config_type = 'mtf_calibration' AND is_active IS TRUE
             ORDER BY updated_at DESC, id LIMIT 1
        """), {"user_id": str(args.user_id)})).mappings().one_or_none()
        policy = dict((policy_row or {}).get("config_json") or {})
        if (
            not policy
            or policy.get("approval_status") != "APPROVED"
            or canonical_hash(policy) != run["approved_policy_hash"]
        ):
            raise SystemExit("MTF_APPROVED_POLICY_HASH_CONFLICT")

        rows = (await db.execute(select(Profile).where(
            Profile.user_id == args.user_id,
            Profile.id.in_(list(profile_ids.values())),
        ))).scalars().all()
        profiles = {row.id: row for row in rows}
        if set(profiles) != set(profile_ids.values()):
            raise SystemExit("MTF_PROFILE_NOT_FOUND")
        snapshots = await load_profile_execution_snapshots(
            db, profile_ids.values(), user_id=args.user_id
        )
        if set(snapshots) != set(profile_ids.values()):
            raise SystemExit("MTF_PROFILE_VERSION_NOT_FOUND")

        watchlists = (await db.execute(text("""
            SELECT id, profile_id
              FROM pipeline_watchlists
             WHERE user_id = CAST(:user_id AS UUID)
               AND id IN (CAST(:l1 AS UUID), CAST(:l2 AS UUID))
        """), {
            "user_id": str(args.user_id),
            "l1": str(args.l1_watchlist_id),
            "l2": str(args.l2_watchlist_id),
        })).mappings().all()
        bindings = {row["id"]: row["profile_id"] for row in watchlists}
        if set(bindings) != set(watchlist_ids.values()):
            raise SystemExit("MTF_WATCHLIST_NOT_FOUND")

        spot_row = (await db.execute(text("""
            SELECT config_json
              FROM config_profiles
             WHERE user_id = CAST(:user_id AS UUID) AND pool_id IS NULL
               AND config_type = 'spot_engine' AND is_active IS TRUE
             ORDER BY updated_at DESC, id LIMIT 1
        """), {"user_id": str(args.user_id)})).mappings().one_or_none()
        scanner = (((spot_row or {}).get("config_json") or {}).get("scanner") or {})
        l3_rows = (await db.execute(text("""
            WITH active AS (
              SELECT DISTINCT pc.symbol
                FROM pool_coins pc
                JOIN pools p ON p.id = pc.pool_id
               WHERE p.user_id = CAST(:user_id AS UUID)
                 AND p.is_active IS TRUE AND p.market_type = 'spot'
                 AND pc.is_active IS TRUE AND pc.market_type = 'spot'
            ), requested(scheduler_group) AS (
              VALUES ('structural'), ('microstructure')
            )
            SELECT a.symbol, r.scheduler_group, latest.time,
                   latest.indicators_json
              FROM active a CROSS JOIN requested r
              LEFT JOIN LATERAL (
                SELECT i.time, i.indicators_json
                  FROM indicators i
                 WHERE i.symbol = a.symbol AND i.market_type = 'spot'
                   AND i.timeframe = '5m'
                   AND i.scheduler_group = r.scheduler_group
                 ORDER BY i.time DESC LIMIT 1
              ) latest ON TRUE
             ORDER BY a.symbol, r.scheduler_group
        """), {"user_id": str(args.user_id)})).mappings().all()
        if not l3_rows or any(row["indicators_json"] is None for row in l3_rows):
            raise SystemExit("MTF_L3_RUNTIME_ROWS_INCOMPLETE")
        required_by_group = {
            "structural": ["ema50", "adx", "macd_histogram"],
            "microstructure": ["price", "vwap"],
        }
        optional_by_group = {
            "microstructure": [
                "taker_ratio", "buy_pressure", "spread_pct",
                "orderbook_depth_usdt",
            ],
        }
        validated_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        symbols_by_group: dict[str, set[str]] = {
            "structural": set(), "microstructure": set(),
        }
        for row in l3_rows:
            group = str(row["scheduler_group"])
            symbol = str(row["symbol"])
            symbols_by_group[group].add(symbol)
            for feature in required_by_group[group] + optional_by_group.get(group, []):
                try:
                    validated_by_key[(symbol, group, feature)] = _validated_l3_envelope(
                        (row["indicators_json"] or {}).get(feature),
                        feature=feature,
                        scheduler_group=group,
                    )
                except (TypeError, ValueError):
                    if feature in required_by_group[group]:
                        raise SystemExit(
                            f"MTF_L3_REQUIRED_INDICATOR_INVALID:{symbol}:{group}:{feature}:"
                            + json.dumps(
                                {
                                    "envelope": (row["indicators_json"] or {}).get(feature),
                                    "available_keys": sorted((row["indicators_json"] or {}).keys()),
                                },
                                ensure_ascii=False,
                                default=str,
                            )
                        )
        for group, optional in optional_by_group.items():
            for feature in optional:
                if all(
                    (symbol, group, feature) in validated_by_key
                    for symbol in symbols_by_group[group]
                ):
                    required_by_group[group].append(feature)
        required_envelopes = [
            validated_by_key[(symbol, group, feature)]
            for group, features in required_by_group.items()
            for symbol in sorted(symbols_by_group[group])
            for feature in features
        ]
        ohlcv_features = {
            "ema50", "adx", "macd_histogram", "price", "vwap"
        }
        trade_flow_features = {"taker_ratio", "buy_pressure"}
        order_book_features = {"spread_pct", "orderbook_depth_usdt"}
        policies = {
            "ohlcv": _identity_policy([
                item for (symbol, group, feature), item in validated_by_key.items()
                if feature in ohlcv_features and feature in required_by_group[group]
            ]),
            "live_trade_flow": _identity_policy([
                item for (symbol, group, feature), item in validated_by_key.items()
                if feature in trade_flow_features and feature in required_by_group[group]
            ]),
            "live_order_book": _identity_policy([
                item for (symbol, group, feature), item in validated_by_key.items()
                if feature in order_book_features and feature in required_by_group[group]
            ]),
            "decision_context": {},
        }
        now = datetime.now(timezone.utc)
        measured_margin = max(
            0.0,
            max(
                (
                    datetime.fromisoformat(str(item["available_at"]).replace("Z", "+00:00"))
                    - datetime.fromisoformat(str(item["source_timestamp"]).replace("Z", "+00:00"))
                ).total_seconds() - 300
                for item in required_envelopes
            ),
        )
        scan_interval = int(scanner.get("scan_interval_seconds") or 0)
        if scan_interval <= 0:
            raise SystemExit("MTF_L3_SCAN_INTERVAL_CONFIG_REQUIRED")
        l3_margin = math.ceil(measured_margin) + scan_interval
        l3_source_identity = {
            "source_policies": policies,
            "required_indicators": sorted({
                feature for features in required_by_group.values() for feature in features
            }),
            "required_indicators_by_group": required_by_group,
            "validity_margin_seconds": l3_margin,
            "validity_evidence": {
                "captured_at": now.isoformat(),
                "formula": "ceil(max(available_at-source_timestamp-300)) + scanner.scan_interval_seconds",
                "measured_post_close_latency_seconds": measured_margin,
                "scanner_scan_interval_seconds": scan_interval,
                "active_symbols": len(symbols_by_group["structural"]),
                "required_rows": len(l3_rows),
                "identity_hash": canonical_hash(required_envelopes),
            },
        }

        templates = policy.get("profile_templates") or {}
        l2_source_identity = deepcopy((templates.get("L2") or {}).get("source_identity") or {})
        quantiles = sorted(float(value) for value in policy.get("candidate_quantiles") or [])
        if len(quantiles) < 2:
            raise SystemExit("MTF_CANDIDATE_QUANTILES_REQUIRED")
        distribution_rows = (await db.execute(text("""
            WITH latest AS (
              SELECT DISTINCT ON (i.symbol)
                     i.symbol, i.time, i.indicators_json
                FROM indicators i
                JOIN pool_coins p ON p.symbol = i.symbol
               WHERE p.is_active IS TRUE AND p.market_type = 'spot'
                 AND i.market_type = 'spot' AND i.timeframe = '15m'
                 AND i.scheduler_group = 'structural'
               ORDER BY i.symbol, i.time DESC
            )
            SELECT symbol, time, indicators_json FROM latest ORDER BY symbol
        """))).mappings().all()
        accepted: list[dict[str, Any]] = []
        discarded: dict[str, int] = {}
        for row in distribution_rows:
            values: dict[str, float] = {}
            hashes: dict[str, str] = {}
            try:
                for feature in {item[0] for item in _DERIVED_L2.values()}:
                    values[feature], hashes[feature] = _validated_value(
                        (row["indicators_json"] or {}).get(feature),
                        feature=feature,
                        source_identity=l2_source_identity,
                    )
            except ValueError as exc:
                code = str(exc).split(":", 1)[0]
                discarded[code] = discarded.get(code, 0) + 1
                continue
            accepted.append({
                "symbol": row["symbol"],
                "computed_at": row["time"].isoformat(),
                "values": values,
                "envelope_hashes": hashes,
            })
        if not accepted:
            raise SystemExit("MTF_L2_GOVERNED_DISTRIBUTION_EMPTY")
        low_q, high_q = quantiles[0], quantiles[-1]
        derived = {}
        for semantic, (feature, side) in _DERIVED_L2.items():
            q = low_q if side == "low" else high_q
            derived[semantic] = _quantile(
                [row["values"][feature] for row in accepted], q
            )
        evidence = {
            "contract_version": "mtf_provisional_semantics_distribution_v1",
            "timeframe": "15m",
            "market_type": "spot",
            "scheduler_group": "structural",
            "quantiles": {"low": low_q, "high": high_q},
            "accepted_rows": len(accepted),
            "discarded_rows": len(distribution_rows) - len(accepted),
            "discard_reasons": discarded,
            "population_hash": canonical_hash(accepted),
            "derived_semantics": derived,
        }

        document_profiles: dict[str, Any] = {}
        for layer in ("L1", "L2"):
            profile = profiles[profile_ids[layer]]
            current = deepcopy(dict(profile.config or {}))
            semantic = deepcopy(proposal[layer].get("mtf_semantics") or {})
            if layer == "L2":
                semantic.update(derived)
            source_identity = deepcopy((templates.get(layer) or {}).get("source_identity") or {})
            calibration = {
                "status": "DRAFT_INSUFFICIENT_DATA",
                "method": "WALK_FORWARD",
                "activation_authority": "HUMAN_WAIVER",
                "reason": "MIN_SAMPLES_NOT_MET",
                "thresholds_emitted": False,
                "min_samples": policy.get("min_samples"),
                "run_id": str(run["id"]),
                "policy_hash": run["approved_policy_hash"],
                "dataset_hash": run["dataset_hash"],
            }
            snapshot = snapshots[profile.id]["contract"]
            document_profiles[layer] = {
                "profile_id": str(profile.id),
                "expected_profile_version_id": snapshot["profile_version_id"],
                "expected_profile_config_hash": snapshot["profile_projection_hash"],
                "name": layer,
                "profile_kind": "MTF_LAYER",
                "layer": layer,
                "funnel_role": "primary_filter" if layer == "L1" else "score_engine",
                "default_timeframe": "1h" if layer == "L1" else "15m",
                **{section: deepcopy(current.get(section) or {}) for section in EXECUTION_SECTIONS},
                "scoring": deepcopy(current.get("scoring") or {}),
                "mtf_semantics": semantic,
                "source_identity": source_identity,
                "calibration": calibration,
                "activation_mode": "SHADOW",
                "is_shadow_only": True,
                "live_trading_enabled": False,
            }
        document = {
            "import_mode": WAIVER_IMPORT_MODE,
            "allow_create": False,
            "activation_mode": "SHADOW",
            "operational_effect": False,
            "profiles": document_profiles,
            "watchlists": {
                layer: str(watchlist_ids[layer]) for layer in ("L1", "L2")
            },
            "expected_watchlist_bindings": {
                layer: str(bindings[watchlist_ids[layer]]) for layer in ("L1", "L2")
            },
            "calibration": {
                "run_id": str(run["id"]),
                "policy_hash": run["approved_policy_hash"],
                "dataset_hash": run["dataset_hash"],
            },
            "statistical_gate": {
                "status": "WAIVED_FOR_SHADOW",
                "authorization_scope": "OBSERVATIONAL_ONLY",
                "calibration_not_passed_acknowledged": True,
                "thresholds_unvalidated_acknowledged": True,
                "operational_effect_false_acknowledged": True,
            },
            "l3_source_identity": l3_source_identity,
            "provisional_semantics_evidence": evidence,
            "_documentation": {
                "display_state": "SHADOW NÃO CALIBRADO",
                "calibration_run_status": run["status"],
                "calibration_failure_reason": run["failure_reason"],
                "dataset_manifest": deepcopy(run["dataset_manifest"] or {}),
                "no_profile_creation": True,
                "no_mtf_order_authority": True,
                "economic_rules_preserved_from_current_profiles": True,
            },
        }
        parse_activation_document(document)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": "EMITTED",
            "output": str(args.output),
            "document_hash": canonical_hash(document),
            "profiles": len(document_profiles),
            "current_distribution": evidence,
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(_run(_args()))

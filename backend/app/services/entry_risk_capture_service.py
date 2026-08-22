"""Idempotent post-commit capture and reconciliation for entry-risk data."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, Mapping

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .entry_risk_features import (
    LEGACY_FORMULA_VERSION,
    build_entry_risk_contract,
)
from .entry_risk_metrics import record_capture

logger = logging.getLogger(__name__)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _capture_one(db: AsyncSession, row: Mapping[str, Any]) -> dict[str, Any]:
    config_row = (await db.execute(text("""
        SELECT config_json
          FROM config_profiles
         WHERE user_id = :user_id
           AND pool_id IS NULL
           AND config_type = 'entry_risk_observation'
           AND is_active = true
         ORDER BY updated_at DESC
         LIMIT 1
    """), {"user_id": row["user_id"]})).scalar_one_or_none()
    capture_config = _mapping(config_row)
    if capture_config and (
        capture_config.get("momentum_operational") is not False
        or capture_config.get("exhaustion_operational") is not False
    ):
        return {
            "schema_version": "entry_risk_features_v1",
            "contract_status": {
                "status": "INVALID",
                "entry_risk_contract_valid": False,
                "entry_risk_eligible_for_training": False,
                "reason_codes": ["OPERATIONAL_CONFIG_FORBIDDEN"],
            },
        }
    if capture_config and capture_config.get("capture_enabled") is False:
        return {
            "schema_version": "entry_risk_features_v1",
            "contract_status": {
                "status": "NOT_AVAILABLE",
                "entry_risk_contract_valid": False,
                "entry_risk_eligible_for_training": False,
                "reason_codes": ["CAPTURE_DISABLED"],
            },
        }
    entry_at = row["entry_timestamp"]
    exchange = row.get("exchange")
    params = {
        "symbol": row["symbol"],
        "entry_at": entry_at,
        "exchange": exchange,
    }
    candles_result = await db.execute(text("""
        SELECT time, open, high, low, close, volume, quote_volume
          FROM ohlcv
         WHERE symbol = :symbol
           AND timeframe = '5m'
           AND market_type = 'spot'
           AND time + interval '5 minutes' <= :entry_at
           AND (
               CAST(:exchange AS text) IS NULL
               OR lower(exchange) = lower(CAST(:exchange AS text))
               OR (lower(CAST(:exchange AS text)) IN ('gate','gate.io') AND lower(exchange) IN ('gate','gate.io'))
           )
         ORDER BY time DESC
         LIMIT 50
    """), params)
    candle_rows = list(reversed(candles_result.mappings().all()))
    candles = pd.DataFrame(candle_rows)

    profile_row = None
    if row.get("profile_id"):
        profile_row = (await db.execute(text("""
            SELECT name, profile_type, config
              FROM profiles
             WHERE id = CAST(:profile_id AS uuid)
             LIMIT 1
        """), {"profile_id": str(row["profile_id"])})).mappings().first()
    profile_config = _mapping(profile_row["config"]) if profile_row else {}
    metadata = _mapping(profile_config.get("metadata"))
    profile_family = metadata.get("profile_family") or (
        profile_row["profile_type"] if profile_row else None
    )

    regime_row = (await db.execute(text("""
        SELECT regime, confidence, source, detected_at
          FROM regime_history
         WHERE detected_at <= :entry_at
         ORDER BY detected_at DESC
         LIMIT 1
    """), {"entry_at": entry_at})).mappings().first()
    regime = dict(regime_row) if regime_row else {}
    if isinstance(regime.get("detected_at"), datetime):
        regime["detected_at"] = regime["detected_at"].isoformat()

    pending = _mapping(row.get("entry_risk_features_json"))
    capture_input = _mapping(pending.get("capture_input"))
    feature_metadata = _mapping(capture_input.get("feature_metadata"))
    source_times = _mapping(row.get("feature_source_times"))
    for name, timestamp in source_times.items():
        feature_metadata.setdefault(name, {
            "timestamp": timestamp,
            "timeframe": None,
            "group": None,
            "stale": False,
        })
    stale_after = int(capture_config.get("source_stale_seconds") or 300)
    for meta in feature_metadata.values():
        if not isinstance(meta, dict):
            continue
        raw_timestamp = meta.get("timestamp") or meta.get("ts")
        if not raw_timestamp:
            continue
        try:
            source_at = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
            if source_at.tzinfo is None:
                source_at = source_at.replace(tzinfo=timezone.utc)
            meta["stale"] = bool(meta.get("stale")) or (
                (entry_at - source_at.astimezone(timezone.utc)).total_seconds() > stale_after
            )
        except ValueError:
            pass

    features = _mapping(row.get("features_snapshot"))
    contract = build_entry_risk_contract(
        candles=candles,
        features=features,
        feature_metadata=feature_metadata,
        symbol=row["symbol"],
        exchange=exchange,
        market_type="spot",
        entry_at=entry_at,
        decision_at=row.get("decision_created_at") or row.get("created_at"),
        profile_id=str(row["profile_id"]) if row.get("profile_id") else None,
        profile_name=row.get("profile_name") or (profile_row["name"] if profile_row else None),
        profile_family=profile_family,
        profile_version_id=(
            str(row["profile_version_id"]) if row.get("profile_version_id") else None
        ),
        regime=regime,
    )
    observed_legacy = features.get("entry_exhaustion_score")
    captured_legacy = (contract.get("legacy") or {}).get("entry_exhaustion_score")
    if observed_legacy is not None and captured_legacy is not None:
        delta = abs(float(observed_legacy) - float(captured_legacy))
        contract["legacy"]["snapshot_delta"] = delta
        if delta > 0.2:
            status = contract["contract_status"]
            status["reason_codes"] = sorted(set(status["reason_codes"]) | {"LEGACY_SNAPSHOT_MISMATCH"})
            if status["status"] == "VALID":
                status["status"] = "PARTIAL"
                status["entry_risk_contract_valid"] = False
    return contract


async def capture_pending_entry_risk(
    db: AsyncSession,
    *,
    limit: int = 100,
) -> dict[str, int]:
    rows = (await db.execute(text("""
        SELECT st.id, st.user_id, st.symbol, st.exchange, st.entry_timestamp,
               st.created_at, st.profile_id, st.profile_name, st.profile_version_id,
               st.features_snapshot, st.feature_source_times,
               st.entry_risk_features_json,
               dl.created_at AS decision_created_at
          FROM shadow_trades st
          LEFT JOIN decisions_log dl ON dl.id = st.decision_id
         WHERE st.entry_risk_capture_status IN ('PENDING','ERROR')
           AND st.entry_timestamp IS NOT NULL
         ORDER BY st.created_at
         LIMIT :limit
         FOR UPDATE OF st SKIP LOCKED
    """), {"limit": max(1, min(int(limit), 500))})).mappings().all()
    counts = {"processed": 0, "valid": 0, "partial": 0, "invalid": 0, "error": 0}
    for row in rows:
        started = time.perf_counter()
        try:
            async with db.begin_nested():
                contract = await _capture_one(db, row)
                status = str((contract.get("contract_status") or {}).get("status") or "INVALID")
                await db.execute(text("""
                    UPDATE shadow_trades
                       SET entry_risk_features_json = CAST(:payload AS jsonb),
                           entry_risk_capture_status = :status,
                           entry_risk_captured_at = :captured_at,
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid)
                """), {
                    "payload": json.dumps(contract, default=str, allow_nan=False),
                    "status": status,
                    "captured_at": datetime.now(timezone.utc),
                    "id": str(row["id"]),
                })
            counts[status.lower()] = counts.get(status.lower(), 0) + 1
            record_capture(contract, (time.perf_counter() - started) * 1000.0)
        except Exception as exc:
            counts["error"] += 1
            logger.exception(
                "entry_risk_capture_error trade_id=%s symbol=%s formula=%s",
                row["id"], row["symbol"], LEGACY_FORMULA_VERSION,
            )
            error_payload = {
                "schema_version": "entry_risk_features_v1",
                "contract_status": {
                    "status": "ERROR",
                    "entry_risk_contract_valid": False,
                    "entry_risk_eligible_for_training": False,
                    "reason_codes": [type(exc).__name__],
                },
            }
            async with db.begin_nested():
                await db.execute(text("""
                    UPDATE shadow_trades
                       SET entry_risk_features_json = CAST(:payload AS jsonb),
                           entry_risk_capture_status = 'ERROR',
                           entry_risk_captured_at = now(),
                           updated_at = now()
                     WHERE id = CAST(:id AS uuid)
                """), {
                    "payload": json.dumps(error_payload),
                    "id": str(row["id"]),
                })
        finally:
            counts["processed"] += 1
    pending_over_sla = (await db.execute(text("""
        SELECT count(*)
          FROM shadow_trades
         WHERE entry_risk_capture_status = 'PENDING'
           AND created_at < now() - interval '10 minutes'
    """))).scalar_one()
    counts["pending_over_sla"] = int(pending_over_sla or 0)
    if counts["pending_over_sla"]:
        logger.warning(
            "entry_risk_capture_sla_breach pending_over_10m=%d",
            counts["pending_over_sla"],
        )
    return counts


async def entry_risk_reconciliation_report(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(text("""
        SELECT entry_risk_capture_status AS status, count(*) AS count,
               min(created_at) AS oldest_created_at
          FROM shadow_trades
         GROUP BY entry_risk_capture_status
         ORDER BY entry_risk_capture_status
    """))).mappings().all()
    missing_hash = (await db.execute(text("""
        SELECT count(*)
          FROM shadow_trades
         WHERE entry_risk_capture_status IN ('VALID','PARTIAL')
           AND entry_risk_features_json #>> '{candle_window,candle_window_hash}' IS NULL
    """))).scalar_one()
    mismatches = (await db.execute(text("""
        SELECT count(*)
          FROM shadow_trades
         WHERE entry_risk_features_json #> '{contract_status,reason_codes}'
               ? 'LEGACY_SNAPSHOT_MISMATCH'
    """))).scalar_one()
    return {
        "schema_version": "entry_risk_reconciliation_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "by_status": [dict(row) for row in rows],
        "missing_candle_hash": int(missing_hash or 0),
        "legacy_snapshot_mismatch": int(mismatches or 0),
    }

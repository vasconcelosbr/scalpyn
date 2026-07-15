"""Shared dataset and policy contract for Profile Intelligence."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping
from uuid import UUID

from sqlalchemy import text

from ..ml.native_capture_governance import official_row_errors


DATASET_VERSION = "pi-native-point-in-time-v1"
LABEL_VERSION = "shadow_outcome-v1"
CAPTURE_CONTRACT_VERSION = "point-in-time-v1"
LABEL_CONTRACT_VERSION = "positive_net_return_v1"

OFFICIAL_CAPTURE_COLUMNS = """
    {alias}.decision_id,
    {alias}.features_captured_at,
    {alias}.feature_hash,
    {alias}.feature_extractor_version,
    {alias}.feature_schema_version,
    {alias}.capture_contract_version,
    {alias}.label_contract_version,
    {alias}.profile_version_id,
    {alias}.score_engine_version_id,
    {alias}.lineage_status,
    {alias}.eligible_for_training
"""


def native_capture_start_at() -> datetime:
    raw = (os.environ.get("NATIVE_CAPTURE_START_AT") or "").strip()
    if not raw:
        raise RuntimeError("missing_NATIVE_CAPTURE_START_AT")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("invalid_NATIVE_CAPTURE_START_AT") from exc


def official_where(alias: str = "st") -> str:
    return f"""
        {alias}.created_at >= :native_capture_start_at
        AND {alias}.capture_contract_version = '{CAPTURE_CONTRACT_VERSION}'
        AND {alias}.label_contract_version = '{LABEL_CONTRACT_VERSION}'
        AND {alias}.features_snapshot IS NOT NULL
        AND {alias}.features_snapshot != '{{}}'::jsonb
        AND {alias}.features_captured_at IS NOT NULL
        AND {alias}.feature_hash IS NOT NULL
        AND {alias}.feature_extractor_version IS NOT NULL
        AND {alias}.feature_schema_version IS NOT NULL
        AND {alias}.profile_version_id IS NOT NULL
        AND {alias}.score_engine_version_id IS NOT NULL
        AND {alias}.lineage_status = 'EXACT'
        AND {alias}.eligible_for_training IS TRUE
    """


def official_params() -> dict[str, Any]:
    return {"native_capture_start_at": native_capture_start_at()}


def _row_mapping(row: Any) -> Mapping[str, Any]:
    mapping = getattr(row, "_mapping", row)
    return mapping if isinstance(mapping, Mapping) else vars(row)


def filter_hash_valid_rows(rows: Iterable[Any]) -> tuple[list[Any], int]:
    """Recompute the native hash and keep only fully valid official rows."""
    start_at = native_capture_start_at()
    valid: list[Any] = []
    invalid = 0
    for row in rows:
        if official_row_errors(_row_mapping(row), start_at):
            invalid += 1
        else:
            valid.append(row)
    return valid, invalid


@dataclass(frozen=True)
class PIValidationPolicy:
    min_discovery_trades: int
    min_validation_trades: int
    min_validation_lift: float
    min_validation_winrate_delta: float
    max_single_symbol_share: float
    max_single_day_share: float
    min_distinct_symbols: int
    min_distinct_days: int
    min_assoc_support_validation: float
    min_assoc_confidence_validation: float
    min_validation_lift_retention: float


REQUIRED_POLICY_KEYS = {
    "analysis_sources",
    "indicator_winning_lift",
    "indicator_losing_winrate_ratio",
    "validation_min_discovery_trades",
    "validation_min_trades",
    "validation_min_lift",
    "validation_min_winrate_delta",
    "validation_max_single_symbol_share",
    "validation_max_single_day_share",
    "validation_min_distinct_symbols",
    "validation_min_distinct_days",
    "validation_min_assoc_support",
    "validation_min_assoc_confidence",
    "validation_min_lift_retention",
    "adjustment_min_profile_trades",
    "adjustment_max_win_rate",
    "adjustment_score_bump",
    "adjustment_score_cap",
}


async def load_pi_settings(
    db: Any,
    user_id: UUID,
    override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    row = (
        await db.execute(
            text("""
                SELECT config_json
                FROM config_profiles
                WHERE user_id = :uid
                  AND config_type = 'profile_intelligence'
                  AND is_active IS TRUE
                  AND pool_id IS NULL
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"uid": str(user_id)},
        )
    ).fetchone()
    settings = row.config_json if row else None
    if isinstance(settings, str):
        settings = json.loads(settings)
    settings = dict(settings or {})
    settings.update(dict(override or {}))
    missing = sorted(key for key in REQUIRED_POLICY_KEYS if settings.get(key) is None)
    if missing:
        raise RuntimeError(f"missing_profile_intelligence_settings:{','.join(missing)}")
    sources = settings.get("analysis_sources")
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("invalid_profile_intelligence_analysis_sources")
    forbidden = {"L3_REJECTED", "L3_SIMULATED"}.intersection(map(str, sources))
    if forbidden:
        raise RuntimeError(
            "profile_intelligence_analysis_sources_include_non_observational:"
            + ",".join(sorted(forbidden))
        )
    return settings


def validation_policy(settings: Mapping[str, Any]) -> PIValidationPolicy:
    return PIValidationPolicy(
        min_discovery_trades=int(settings["validation_min_discovery_trades"]),
        min_validation_trades=int(settings["validation_min_trades"]),
        min_validation_lift=float(settings["validation_min_lift"]),
        min_validation_winrate_delta=float(settings["validation_min_winrate_delta"]),
        max_single_symbol_share=float(settings["validation_max_single_symbol_share"]),
        max_single_day_share=float(settings["validation_max_single_day_share"]),
        min_distinct_symbols=int(settings["validation_min_distinct_symbols"]),
        min_distinct_days=int(settings["validation_min_distinct_days"]),
        min_assoc_support_validation=float(settings["validation_min_assoc_support"]),
        min_assoc_confidence_validation=float(settings["validation_min_assoc_confidence"]),
        min_validation_lift_retention=float(settings["validation_min_lift_retention"]),
    )

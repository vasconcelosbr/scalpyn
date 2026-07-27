"""Read-only, deterministic builder over immutable Profile Intelligence data."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Any, Iterable, Mapping
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .data_contract import (
    CanonicalObservation,
    canonical_hash,
    extract_indicators,
    extract_regime,
    finite_number,
    policy_key,
)


class DatasetContractError(ValueError):
    pass


@dataclass(frozen=True)
class BuiltDataset:
    observations: tuple[CanonicalObservation, ...]
    dataset_hash: str
    policy_hash: str
    window_from: datetime
    window_to: datetime
    manifest: Mapping[str, Any]


class BayesianDatasetBuilder:
    """Build a point-in-time dataset without writing to any source table."""

    async def build(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        profile_id: UUID,
        window_from: datetime,
        window_to: datetime,
        max_trades: int,
        profile_version_id: UUID | None = None,
        requested_policy_key: str | None = None,
        requested_indicators: Iterable[str] | None = None,
    ) -> BuiltDataset:
        if max_trades <= 0:
            raise DatasetContractError("max_trades must be positive")
        profile_version_value = (
            str(profile_version_id) if profile_version_id else None
        )
        policy_groups = (
            await db.execute(
                text(
                    """
                    SELECT
                        st.barrier_contract_version, st.tp_pct, st.sl_pct,
                        st.timeout_candles, st.direction, COUNT(*) AS row_count
                    FROM shadow_trades st
                    WHERE st.user_id = :user_id
                      AND st.profile_id = :profile_id
                      AND st.status = 'COMPLETED'
                      AND st.completed_at >= :window_from
                      AND st.completed_at < :window_to
                      AND (
                          CAST(:profile_version_id AS UUID) IS NULL
                          OR st.profile_version_id = CAST(:profile_version_id AS UUID)
                      )
                    GROUP BY
                        st.barrier_contract_version, st.tp_pct, st.sl_pct,
                        st.timeout_candles, st.direction
                    ORDER BY
                        row_count DESC,
                        st.barrier_contract_version NULLS LAST,
                        st.direction NULLS LAST,
                        st.timeout_candles NULLS LAST,
                        st.tp_pct NULLS LAST,
                        st.sl_pct NULLS LAST
                    """
                ),
                {
                    "user_id": str(user_id),
                    "profile_id": str(profile_id),
                    "profile_version_id": profile_version_value,
                    "window_from": window_from,
                    "window_to": window_to,
                },
            )
        ).mappings().all()
        if not policy_groups:
            raise DatasetContractError("no completed point-in-time observations")

        selected_group: Mapping[str, Any] | None = None
        if requested_policy_key:
            selected_group = next(
                (
                    group
                    for group in policy_groups
                    if policy_key(group) == requested_policy_key
                ),
                None,
            )
            if selected_group is None:
                raise DatasetContractError("requested operational policy not found")
        else:
            selected_group = policy_groups[0]
        selected_policy = policy_key(selected_group)

        rows = (
            await db.execute(
                text(
                    """
                    SELECT
                        st.id, st.event_id, st.profile_id, st.profile_version_id,
                        st.symbol, st.timeframe, st.entry_timestamp, st.completed_at,
                        st.outcome, st.pnl_pct, st.source, st.direction,
                        st.tp_pct, st.sl_pct, st.timeout_candles,
                        st.barrier_contract_version, st.features_snapshot,
                        st.exit_metrics_json
                    FROM shadow_trades st
                    WHERE st.user_id = :user_id
                      AND st.profile_id = :profile_id
                      AND st.status = 'COMPLETED'
                      AND st.completed_at >= :window_from
                      AND st.completed_at < :window_to
                      AND (
                          CAST(:profile_version_id AS UUID) IS NULL
                          OR st.profile_version_id = CAST(:profile_version_id AS UUID)
                      )
                      AND st.barrier_contract_version
                          IS NOT DISTINCT FROM :barrier_contract_version
                      AND st.tp_pct IS NOT DISTINCT FROM :tp_pct
                      AND st.sl_pct IS NOT DISTINCT FROM :sl_pct
                      AND st.timeout_candles IS NOT DISTINCT FROM :timeout_candles
                      AND st.direction IS NOT DISTINCT FROM :direction
                    ORDER BY st.completed_at ASC, st.id ASC
                    LIMIT :max_trades
                    """
                ),
                {
                    "user_id": str(user_id),
                    "profile_id": str(profile_id),
                    "profile_version_id": profile_version_value,
                    "window_from": window_from,
                    "window_to": window_to,
                    "barrier_contract_version": selected_group[
                        "barrier_contract_version"
                    ],
                    "tp_pct": selected_group["tp_pct"],
                    "sl_pct": selected_group["sl_pct"],
                    "timeout_candles": selected_group["timeout_candles"],
                    "direction": selected_group["direction"],
                    "max_trades": max_trades,
                },
            )
        ).mappings().all()
        if not rows:
            raise DatasetContractError("selected operational policy has no observations")

        observations: list[CanonicalObservation] = []
        duplicate_ids: list[str] = []
        invalid_rows: list[str] = []
        seen: set[str] = set()
        for row in rows:
            observation_id = str(row["event_id"] or row["id"])
            if observation_id in seen:
                duplicate_ids.append(observation_id)
                continue
            seen.add(observation_id)
            occurred_at = row["entry_timestamp"] or row["completed_at"]
            snapshot = row["features_snapshot"] or {}
            if not isinstance(snapshot, Mapping) or occurred_at is None:
                invalid_rows.append(str(row["id"]))
                continue
            outcome = str(row["outcome"] or "").upper()
            if outcome not in {"TP", "TP_HIT", "SL", "SL_HIT", "TIMEOUT"}:
                invalid_rows.append(str(row["id"]))
                continue
            exit_metrics = row["exit_metrics_json"] or {}
            net_pnl = (
                finite_number(exit_metrics.get("net_return_pct"))
                if isinstance(exit_metrics, Mapping)
                else None
            )
            if net_pnl is None:
                net_pnl = finite_number(row["pnl_pct"])
            observations.append(
                CanonicalObservation(
                    observation_id=observation_id,
                    profile_id=str(row["profile_id"]),
                    profile_version_id=(
                        str(row["profile_version_id"])
                        if row["profile_version_id"]
                        else None
                    ),
                    symbol=str(row["symbol"]),
                    timeframe=str(row["timeframe"]) if row["timeframe"] else None,
                    occurred_at=occurred_at,
                    outcome=outcome,
                    tp_hit=int(outcome in {"TP", "TP_HIT"}),
                    net_pnl_pct=net_pnl,
                    regime=extract_regime(snapshot),
                    policy_key=selected_policy,
                    indicators=extract_indicators(snapshot, requested_indicators),
                    source=str(row["source"]),
                )
            )
        if not observations:
            raise DatasetContractError("all observations failed the data contract")

        observations.sort(key=lambda item: (item.occurred_at, item.observation_id))
        counts_by_day = Counter(item.occurred_at.date().isoformat() for item in observations)
        counts_by_symbol = Counter(item.symbol for item in observations)
        counts_by_regime = Counter(item.regime or "UNKNOWN" for item in observations)
        counts_by_outcome = Counter(item.outcome for item in observations)
        counts_by_profile = Counter(item.profile_id for item in observations)
        manifest = {
            "contract_version": "profile_bayesian_dataset_v1",
            "source_tables": ["shadow_trades"],
            "entry_features_only": True,
            "observation_ids": [item.observation_id for item in observations],
            "inclusion": {
                "status": "COMPLETED",
                "profile_id": str(profile_id),
                "profile_version_id": str(profile_version_id) if profile_version_id else None,
                "window_from": window_from.isoformat(),
                "window_to": window_to.isoformat(),
                "policy_key": selected_policy,
                "policy_selection": (
                    "requested"
                    if requested_policy_key
                    else "largest_compatible_group"
                ),
            },
            "exclusion": {
                "duplicate_ids": duplicate_ids,
                "invalid_rows": invalid_rows,
                "incompatible_policy_rows": sum(
                    int(group["row_count"]) for group in policy_groups
                )
                - int(selected_group["row_count"]),
                "selected_policy_rows_before_limit": int(
                    selected_group["row_count"]
                ),
                "policy_group_count": len(policy_groups),
                "exit_features_excluded": True,
            },
            "counts": {
                "profile": dict(counts_by_profile),
                "symbol": dict(counts_by_symbol),
                "regime": dict(counts_by_regime),
                "outcome": dict(counts_by_outcome),
                "day": dict(counts_by_day),
                "operational_policy": {selected_policy: len(observations)},
            },
        }
        return BuiltDataset(
            observations=tuple(observations),
            dataset_hash=canonical_hash(observations),
            policy_hash=hashlib.sha256(
                json.dumps(
                    {"policy_key": selected_policy}, sort_keys=True
                ).encode("utf-8")
            ).hexdigest(),
            window_from=window_from,
            window_to=window_to,
            manifest=manifest,
        )

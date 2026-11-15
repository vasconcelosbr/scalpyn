"""Read-only, deterministic builder over immutable Profile Intelligence data."""

from __future__ import annotations

from collections import Counter
from bisect import bisect_right
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
    target_horizon_seconds,
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
        required_sources: Iterable[str] = (),
        required_lineage_statuses: Iterable[str] = (),
        required_barrier_modes: Iterable[str] = (),
        required_barrier_contract_versions: Iterable[str] = (),
        minimum_entry_at: datetime | None = None,
        require_eligible_for_training: bool = False,
        atr_bucket_edges_pct: Iterable[float] = (),
        selection_strategy: str = "oldest_contiguous",
    ) -> BuiltDataset:
        if max_trades <= 0:
            raise DatasetContractError("max_trades must be positive")
        if selection_strategy not in {
            "oldest_contiguous",
            "most_recent_contiguous",
        }:
            raise DatasetContractError("unsupported dataset selection strategy")
        required_sources = tuple(sorted(set(required_sources)))
        required_lineage_statuses = tuple(sorted(set(required_lineage_statuses)))
        required_barrier_modes = tuple(sorted(set(required_barrier_modes)))
        required_barrier_contract_versions = tuple(
            sorted(set(required_barrier_contract_versions))
        )
        requested_indicators = (
            tuple(requested_indicators)
            if requested_indicators is not None
            else None
        )
        atr_bucket_edges_pct = tuple(sorted(set(atr_bucket_edges_pct)))
        order_direction = (
            "DESC" if selection_strategy == "most_recent_contiguous" else "ASC"
        )
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
                          CAST(:minimum_entry_at AS TIMESTAMPTZ) IS NULL
                          OR st.entry_timestamp >= CAST(
                              :minimum_entry_at AS TIMESTAMPTZ
                          )
                      )
                      AND (
                          CAST(:profile_version_id AS UUID) IS NULL
                          OR st.profile_version_id = CAST(:profile_version_id AS UUID)
                      )
                      AND (
                          cardinality(CAST(:required_sources AS TEXT[])) = 0
                          OR st.source = ANY(CAST(:required_sources AS TEXT[]))
                      )
                      AND (
                          cardinality(CAST(:required_lineage_statuses AS TEXT[])) = 0
                          OR st.lineage_status = ANY(
                              CAST(:required_lineage_statuses AS TEXT[])
                          )
                      )
                      AND (
                          cardinality(CAST(:required_barrier_modes AS TEXT[])) = 0
                          OR st.barrier_mode = ANY(
                              CAST(:required_barrier_modes AS TEXT[])
                          )
                      )
                      AND (
                          cardinality(
                              CAST(:required_barrier_contract_versions AS TEXT[])
                          ) = 0
                          OR st.barrier_contract_version = ANY(
                              CAST(:required_barrier_contract_versions AS TEXT[])
                          )
                      )
                      AND (
                          CAST(:require_eligible_for_training AS BOOLEAN) = FALSE
                          OR st.eligible_for_training IS TRUE
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
                    "minimum_entry_at": minimum_entry_at,
                    "required_sources": list(required_sources),
                    "required_lineage_statuses": list(
                        required_lineage_statuses
                    ),
                    "required_barrier_modes": list(required_barrier_modes),
                    "required_barrier_contract_versions": list(
                        required_barrier_contract_versions
                    ),
                    "require_eligible_for_training": require_eligible_for_training,
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
                        st.exit_metrics_json, st.barrier_mode,
                        st.lineage_status, st.eligible_for_training,
                        st.atr_pct_at_entry, st.fee_roundtrip_pct_applied,
                        st.btc_change_1h_pct, st.market_data_confidence
                    FROM shadow_trades st
                    WHERE st.user_id = :user_id
                      AND st.profile_id = :profile_id
                      AND st.status = 'COMPLETED'
                      AND st.completed_at >= :window_from
                      AND st.completed_at < :window_to
                      AND (
                          CAST(:minimum_entry_at AS TIMESTAMPTZ) IS NULL
                          OR st.entry_timestamp >= CAST(
                              :minimum_entry_at AS TIMESTAMPTZ
                          )
                      )
                      AND (
                          CAST(:profile_version_id AS UUID) IS NULL
                          OR st.profile_version_id = CAST(:profile_version_id AS UUID)
                      )
                      AND (
                          cardinality(CAST(:required_sources AS TEXT[])) = 0
                          OR st.source = ANY(CAST(:required_sources AS TEXT[]))
                      )
                      AND (
                          cardinality(CAST(:required_lineage_statuses AS TEXT[])) = 0
                          OR st.lineage_status = ANY(
                              CAST(:required_lineage_statuses AS TEXT[])
                          )
                      )
                      AND (
                          cardinality(CAST(:required_barrier_modes AS TEXT[])) = 0
                          OR st.barrier_mode = ANY(
                              CAST(:required_barrier_modes AS TEXT[])
                          )
                      )
                      AND (
                          cardinality(
                              CAST(:required_barrier_contract_versions AS TEXT[])
                          ) = 0
                          OR st.barrier_contract_version = ANY(
                              CAST(:required_barrier_contract_versions AS TEXT[])
                          )
                      )
                      AND (
                          CAST(:require_eligible_for_training AS BOOLEAN) = FALSE
                          OR st.eligible_for_training IS TRUE
                      )
                      AND st.barrier_contract_version
                          IS NOT DISTINCT FROM :barrier_contract_version
                      AND st.tp_pct IS NOT DISTINCT FROM :tp_pct
                      AND st.sl_pct IS NOT DISTINCT FROM :sl_pct
                      AND st.timeout_candles IS NOT DISTINCT FROM :timeout_candles
                      AND st.direction IS NOT DISTINCT FROM :direction
                    ORDER BY st.completed_at {order_direction}, st.id {order_direction}
                    LIMIT :max_trades
                    """.format(order_direction=order_direction)
                ),
                {
                    "user_id": str(user_id),
                    "profile_id": str(profile_id),
                    "profile_version_id": profile_version_value,
                    "window_from": window_from,
                    "window_to": window_to,
                    "minimum_entry_at": minimum_entry_at,
                    "barrier_contract_version": selected_group[
                        "barrier_contract_version"
                    ],
                    "tp_pct": selected_group["tp_pct"],
                    "sl_pct": selected_group["sl_pct"],
                    "timeout_candles": selected_group["timeout_candles"],
                    "direction": selected_group["direction"],
                    "max_trades": max_trades,
                    "required_sources": list(required_sources),
                    "required_lineage_statuses": list(
                        required_lineage_statuses
                    ),
                    "required_barrier_modes": list(required_barrier_modes),
                    "required_barrier_contract_versions": list(
                        required_barrier_contract_versions
                    ),
                    "require_eligible_for_training": require_eligible_for_training,
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
            indicators = extract_indicators(snapshot, requested_indicators)
            atr_at_entry = finite_number(row.get("atr_pct_at_entry"))
            btc_change_1h = finite_number(row.get("btc_change_1h_pct"))
            market_confidence = finite_number(
                row.get("market_data_confidence")
            )
            requested = set(requested_indicators or ())
            # Dedicated immutable entry columns are authoritative controls.
            # They replace any less-specific snapshot representation.
            if requested_indicators is None or "atr_pct" in requested:
                indicators["atr_pct"] = atr_at_entry
            if (
                requested_indicators is None
                or "btc_change_1h_pct" in requested
            ):
                indicators["btc_change_1h_pct"] = btc_change_1h
            if (
                requested_indicators is None
                or "market_data_confidence" in requested
            ):
                indicators["market_data_confidence"] = market_confidence
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
                    indicators=indicators,
                    source=str(row["source"]),
                    barrier_mode=(
                        str(row.get("barrier_mode"))
                        if row.get("barrier_mode")
                        else None
                    ),
                    lineage_status=(
                        str(row.get("lineage_status"))
                        if row.get("lineage_status")
                        else None
                    ),
                    eligible_for_training=(
                        bool(row.get("eligible_for_training"))
                        if row.get("eligible_for_training") is not None
                        else None
                    ),
                    target_horizon_seconds=target_horizon_seconds(
                        str(row["timeframe"]) if row["timeframe"] else None,
                        row["timeout_candles"],
                    ),
                    atr_pct_at_entry=atr_at_entry,
                    fee_roundtrip_pct=finite_number(
                        row.get("fee_roundtrip_pct_applied")
                    ),
                    btc_change_1h_pct=btc_change_1h,
                    market_data_confidence=market_confidence,
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
        counts_by_source = Counter(item.source for item in observations)
        counts_by_lineage = Counter(
            item.lineage_status or "NULL" for item in observations
        )
        counts_by_barrier_mode = Counter(
            item.barrier_mode or "NULL" for item in observations
        )
        counts_by_eligibility = Counter(
            str(item.eligible_for_training).lower()
            if item.eligible_for_training is not None
            else "null"
            for item in observations
        )
        counts_by_atr_bucket: Counter[str] = Counter()
        for item in observations:
            if item.atr_pct_at_entry is None:
                counts_by_atr_bucket["MISSING"] += 1
                continue
            position = bisect_right(atr_bucket_edges_pct, item.atr_pct_at_entry)
            if not atr_bucket_edges_pct:
                label = "ALL_FINITE"
            elif position == 0:
                label = f"<={atr_bucket_edges_pct[0]:g}"
            elif position >= len(atr_bucket_edges_pct):
                label = f">{atr_bucket_edges_pct[-1]:g}"
            else:
                label = (
                    f"({atr_bucket_edges_pct[position - 1]:g},"
                    f"{atr_bucket_edges_pct[position]:g}]"
                )
            counts_by_atr_bucket[label] += 1
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
                "selection_strategy": selection_strategy,
                "required_sources": list(required_sources),
                "required_lineage_statuses": list(
                    required_lineage_statuses
                ),
                "required_barrier_modes": list(required_barrier_modes),
                "required_barrier_contract_versions": list(
                    required_barrier_contract_versions
                ),
                "minimum_entry_at": (
                    minimum_entry_at.isoformat() if minimum_entry_at else None
                ),
                "require_eligible_for_training": require_eligible_for_training,
                "atr_bucket_edges_pct": list(atr_bucket_edges_pct),
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
                "source": dict(counts_by_source),
                "lineage_status": dict(counts_by_lineage),
                "barrier_mode": dict(counts_by_barrier_mode),
                "eligible_for_training": dict(counts_by_eligibility),
                "atr_pct_at_entry_bucket": dict(counts_by_atr_bucket),
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
                    {
                        "policy_key": selected_policy,
                        "required_sources": list(required_sources),
                        "required_lineage_statuses": list(
                            required_lineage_statuses
                        ),
                        "required_barrier_modes": list(
                            required_barrier_modes
                        ),
                        "required_barrier_contract_versions": list(
                            required_barrier_contract_versions
                        ),
                        "minimum_entry_at": (
                            minimum_entry_at.isoformat()
                            if minimum_entry_at
                            else None
                        ),
                        "require_eligible_for_training": (
                            require_eligible_for_training
                        ),
                        "atr_bucket_edges_pct": list(
                            atr_bucket_edges_pct
                        ),
                        "selection_strategy": selection_strategy,
                    },
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            window_from=window_from,
            window_to=window_to,
            manifest=manifest,
        )

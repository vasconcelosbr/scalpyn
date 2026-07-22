"""Read-only reconciliation of historical ``shadow_trades`` rows.

Compares an isolated backup restore with a current database, restricted to
rows created on or before a caller-supplied cutoff.  The process performs no
DDL/DML and exits non-zero when an immutable value or primary key differs.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, AsyncIterator, Iterable
from uuid import UUID

import asyncpg


# Authoritative creation/native-capture fields.  The native subset mirrors
# prevent_shadow_native_capture_update() from migration 133; the remaining
# identity/economic snapshot fields are populated by the INSERT writers.
IMMUTABLE_COLUMNS = (
    "id", "decision_id", "user_id", "symbol", "strategy", "direction",
    "amount_usdt", "skip_reason", "source", "config_snapshot",
    "features_snapshot", "event_id", "snapshot_id", "exchange", "timeframe",
    "profile_version_id", "score_engine_version_id", "feature_schema_version",
    "feature_extractor_version", "capture_contract_version",
    "features_captured_at", "features_coverage", "oldest_indicator_age_s",
    "market_data_confidence", "feature_hash", "profile_config_hash",
    "score_engine_config_hash", "profile_id", "profile_version", "profile_name",
    "strategy_type", "rules_snapshot", "profile_status_at_entry", "ranking_id",
    "watchlist_id", "watchlist_name", "watchlist_level", "source_watchlist_id",
    "created_at",
)

# Fields intentionally allowed to mature through the monitor, analyzers,
# lineage backfill and ranking orchestrator.  Their signature is informative.
LIFECYCLE_COLUMNS = (
    "entry_price", "entry_timestamp", "tp_price", "sl_price", "tp_pct",
    "sl_pct", "timeout_candles", "exit_price", "exit_timestamp", "outcome",
    "pnl_pct", "pnl_usdt", "holding_seconds", "status", "features_snapshot_exit",
    "label_resolved_at", "lineage_status", "eligible_for_training",
    "last_processed_time", "updated_at", "completed_at", "btc_price_at_entry",
    "btc_change_1h_pct", "funding_rate_at_entry", "n_concurrent_signals",
    "min_price_post_entry", "max_price_post_entry", "max_drawdown_pct",
    "max_profit_pct", "mae_pct", "mfe_pct", "exit_metrics_json",
    "price_after_1h", "price_after_2h", "price_after_4h", "price_after_12h",
    "price_after_24h", "max_profit_after_timeout_pct",
    "max_drawdown_after_timeout_pct", "delayed_tp", "delayed_tp_hours",
    "timeout_post_analysis_done", "mae_at", "mfe_at", "barrier_touched",
    "barrier_touched_at", "intrabar_convention", "final_return_pct",
    "net_return_pct", "fee_roundtrip_pct_applied", "barrier_mode",
    "tp_pct_applied", "sl_pct_applied", "atr_pct_at_entry", "ttt_enabled",
    "ttt_tp_pct", "ttt_timeout_minutes", "ttt_outcome", "ttt_close_reason",
    "ttt_fast_win_bucket", "ttt_analysis_done", "elapsed_minutes",
    "time_to_tp_minutes", "profit_velocity", "profit_velocity_per_hour",
    "max_profit_first_15m", "max_profit_first_30m", "max_profit_first_60m",
    "candles_to_peak", "candles_to_first_positive", "final_priority_score",
    "ml_probability", "ml_model_id", "orchestrator_payload", "model_lane",
    "model_version", "threshold_used", "score_status", "gate_action",
    "reason_codes", "ml_gate_enabled", "lineage_confidence", "lineage_source",
    "lineage_resolved_at",
)


def canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, (datetime, date)):
        dt = value
        if isinstance(value, datetime):
            if value.tzinfo is None:
                dt = value.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            return dt.isoformat(timespec="microseconds").replace("+00:00", "Z")
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, float):
        return format(value, ".17g")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): canonical_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [canonical_value(item) for item in value]
    return str(value)


def canonical_row(row: dict[str, Any], columns: Iterable[str]) -> bytes:
    payload = [[column, canonical_value(row.get(column))] for column in columns]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def differing_columns(left: dict[str, Any], right: dict[str, Any], columns: Iterable[str]) -> list[str]:
    return [
        column for column in columns
        if canonical_value(left.get(column)) != canonical_value(right.get(column))
    ]


async def _schema_columns(conn: asyncpg.Connection) -> set[str]:
    rows = await conn.fetch("""
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = 'public' AND table_name = 'shadow_trades'
    """)
    return {row["column_name"] for row in rows}


async def _rows(
    conn: asyncpg.Connection,
    cutoff: datetime,
    columns: tuple[str, ...],
    batch_size: int,
) -> AsyncIterator[dict[str, Any]]:
    quoted = ", ".join(f'"{column}"' for column in columns)
    statement = await conn.prepare(
        f'SELECT {quoted} FROM public.shadow_trades '
        'WHERE created_at <= $1 ORDER BY id'
    )
    async for row in statement.cursor(cutoff, prefetch=batch_size):
        yield dict(row)


async def reconcile(args: argparse.Namespace) -> dict[str, Any]:
    baseline = await asyncpg.connect(args.baseline_url, command_timeout=args.command_timeout)
    current = await asyncpg.connect(args.current_url, command_timeout=args.command_timeout)
    try:
        baseline_tx = baseline.transaction(readonly=True, isolation="repeatable_read")
        current_tx = current.transaction(readonly=True, isolation="repeatable_read")
        await baseline_tx.start()
        await current_tx.start()
        try:
            baseline_schema, current_schema = await asyncio.gather(
                _schema_columns(baseline), _schema_columns(current),
            )
            requested = tuple(dict.fromkeys((*IMMUTABLE_COLUMNS, *LIFECYCLE_COLUMNS)))
            missing = sorted(set(requested) - baseline_schema | (set(requested) - current_schema))
            if missing:
                raise RuntimeError(f"shadow_trades columns missing in one database: {missing}")

            left_iter = _rows(baseline, args.cutoff, requested, args.batch_size).__aiter__()
            right_iter = _rows(current, args.cutoff, requested, args.batch_size).__aiter__()
            left = await anext(left_iter, None)
            right = await anext(right_iter, None)
            immutable_base = hashlib.sha256()
            immutable_current = hashlib.sha256()
            lifecycle_base = hashlib.sha256()
            lifecycle_current = hashlib.sha256()
            counts = {
                "baseline_rows": 0, "current_rows": 0, "matched_rows": 0,
                "missing_in_current": 0, "new_in_current_before_cutoff": 0,
                "immutable_diff_rows": 0, "lifecycle_diff_rows": 0,
            }
            lifecycle_by_column: dict[str, int] = {}
            samples: list[dict[str, Any]] = []

            while left is not None or right is not None:
                left_id = str(left["id"]) if left is not None else None
                right_id = str(right["id"]) if right is not None else None
                if right is None or (left is not None and left_id < right_id):
                    counts["baseline_rows"] += 1
                    counts["missing_in_current"] += 1
                    immutable_base.update(canonical_row(left, IMMUTABLE_COLUMNS) + b"\n")
                    lifecycle_base.update(canonical_row(left, LIFECYCLE_COLUMNS) + b"\n")
                    if len(samples) < args.sample_limit:
                        samples.append({"id": left_id, "kind": "missing_in_current"})
                    left = await anext(left_iter, None)
                    continue
                if left is None or right_id < left_id:
                    counts["current_rows"] += 1
                    counts["new_in_current_before_cutoff"] += 1
                    immutable_current.update(canonical_row(right, IMMUTABLE_COLUMNS) + b"\n")
                    lifecycle_current.update(canonical_row(right, LIFECYCLE_COLUMNS) + b"\n")
                    if len(samples) < args.sample_limit:
                        samples.append({"id": right_id, "kind": "new_in_current_before_cutoff"})
                    right = await anext(right_iter, None)
                    continue

                counts["baseline_rows"] += 1
                counts["current_rows"] += 1
                counts["matched_rows"] += 1
                immutable_base.update(canonical_row(left, IMMUTABLE_COLUMNS) + b"\n")
                immutable_current.update(canonical_row(right, IMMUTABLE_COLUMNS) + b"\n")
                lifecycle_base.update(canonical_row(left, LIFECYCLE_COLUMNS) + b"\n")
                lifecycle_current.update(canonical_row(right, LIFECYCLE_COLUMNS) + b"\n")
                immutable_diff = differing_columns(left, right, IMMUTABLE_COLUMNS)
                lifecycle_diff = differing_columns(left, right, LIFECYCLE_COLUMNS)
                if immutable_diff:
                    counts["immutable_diff_rows"] += 1
                if lifecycle_diff:
                    counts["lifecycle_diff_rows"] += 1
                    for column in lifecycle_diff:
                        lifecycle_by_column[column] = lifecycle_by_column.get(column, 0) + 1
                if (immutable_diff or lifecycle_diff) and len(samples) < args.sample_limit:
                    samples.append({
                        "id": left_id,
                        "kind": "column_diff",
                        "immutable_columns": immutable_diff,
                        "lifecycle_columns": lifecycle_diff,
                    })
                left = await anext(left_iter, None)
                right = await anext(right_iter, None)

            blocked = any((
                counts["missing_in_current"], counts["new_in_current_before_cutoff"],
                counts["immutable_diff_rows"],
            ))
            return {
                "status": "BLOCKED_IMMUTABLE_HISTORY_MUTATION" if blocked else "PASS",
                "cutoff": args.cutoff.isoformat().replace("+00:00", "Z"),
                "immutable_columns": list(IMMUTABLE_COLUMNS),
                "lifecycle_columns": list(LIFECYCLE_COLUMNS),
                **counts,
                "immutable_signature": {
                    "baseline": immutable_base.hexdigest(),
                    "current": immutable_current.hexdigest(),
                },
                "lifecycle_signature": {
                    "baseline": lifecycle_base.hexdigest(),
                    "current": lifecycle_current.hexdigest(),
                },
                "lifecycle_diff_by_column": dict(sorted(lifecycle_by_column.items())),
                "samples": samples,
            }
        finally:
            await baseline_tx.rollback()
            await current_tx.rollback()
    finally:
        await baseline.close()
        await current.close()


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("cutoff must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-url", required=True)
    parser.add_argument("--current-url", required=True)
    parser.add_argument("--cutoff", required=True, type=_utc)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--sample-limit", type=int, default=100)
    parser.add_argument("--command-timeout", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = asyncio.run(reconcile(args))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())

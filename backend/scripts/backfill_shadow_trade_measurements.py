"""Build immutable Shadow measurement revisions; dry-run is the default.

Examples:
    python backend/scripts/backfill_shadow_trade_measurements.py --trade-id UUID
    python backend/scripts/backfill_shadow_trade_measurements.py --report-run-id UUID
    python backend/scripts/backfill_shadow_trade_measurements.py --all

No write occurs unless ``--apply`` is supplied explicitly.  Existing revisions
are never updated; the unique input identity makes repeated runs idempotent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any
from types import SimpleNamespace
from uuid import UUID

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_HERE)
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import func, select  # noqa: E402


def _json(value: Any) -> str:
    return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "min": ordered[0],
        "p25": percentile(0.25),
        "median": statistics.median(ordered),
        "p75": percentile(0.75),
        "max": ordered[-1],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    from app.database import AsyncSessionLocal
    from app.models.shadow_trade import ShadowTrade
    from app.models.shadow_trade_analysis import ShadowTradeReportItem
    from app.services.shadow_trade_measurement_service import (
        build_measurement_revision,
        persist_measurement_revision,
    )

    async with AsyncSessionLocal() as db:
        if args.count_only:
            count_query = select(func.count(ShadowTrade.id)).where(
                ShadowTrade.status == "COMPLETED"
            )
            if args.report_run_id:
                count_query = count_query.join(
                    ShadowTradeReportItem,
                    ShadowTradeReportItem.shadow_trade_id == ShadowTrade.id,
                ).where(ShadowTradeReportItem.report_run_id == UUID(args.report_run_id))
            elif args.trade_id:
                count_query = count_query.where(ShadowTrade.id == UUID(args.trade_id))
            if args.created_from:
                count_query = count_query.where(
                    ShadowTrade.created_at >= _parse_datetime(args.created_from)
                )
            if args.created_to:
                count_query = count_query.where(
                    ShadowTrade.created_at < _parse_datetime(args.created_to)
                )
            return {
                "mode": "DRY_RUN_COUNT",
                "selected": int((await db.execute(count_query)).scalar_one()),
            }
        columns = (
            ShadowTrade.id,
            ShadowTrade.symbol,
            ShadowTrade.status,
            ShadowTrade.outcome,
            ShadowTrade.config_snapshot,
            ShadowTrade.entry_price,
            ShadowTrade.entry_timestamp,
            ShadowTrade.exit_price,
            ShadowTrade.exit_timestamp,
            ShadowTrade.mae_pct,
            ShadowTrade.mfe_pct,
            ShadowTrade.mae_at,
            ShadowTrade.mfe_at,
            ShadowTrade.pnl_pct,
            ShadowTrade.fee_roundtrip_pct_applied,
            ShadowTrade.net_return_pct,
            ShadowTrade.tp_pct,
            ShadowTrade.sl_pct,
            ShadowTrade.created_at,
        )
        query = select(*columns).where(ShadowTrade.status == "COMPLETED")
        if args.trade_id:
            query = query.where(ShadowTrade.id == UUID(args.trade_id))
        elif args.report_run_id:
            query = query.join(
                ShadowTradeReportItem,
                ShadowTradeReportItem.shadow_trade_id == ShadowTrade.id,
            ).where(ShadowTradeReportItem.report_run_id == UUID(args.report_run_id))
        if args.created_from:
            query = query.where(ShadowTrade.created_at >= _parse_datetime(args.created_from))
        if args.created_to:
            query = query.where(ShadowTrade.created_at < _parse_datetime(args.created_to))
        after_created_at = _parse_datetime(args.after_created_at) if args.after_created_at else None
        after_id = UUID(args.after_id) if args.after_id else None
        if (after_created_at is None) != (after_id is None):
            raise ValueError("after-created-at and after-id must be supplied together")

        counts: dict[str, int] = {}
        inserted = 0
        rows: list[dict[str, Any]] = []
        selected = 0
        batches = 0
        started = time.monotonic()
        last_created_at = after_created_at
        last_id = after_id
        while args.max_batches is None or batches < args.max_batches:
            page_query = query
            if last_created_at is not None and last_id is not None:
                page_query = page_query.where(
                    (ShadowTrade.created_at > last_created_at)
                    | ((ShadowTrade.created_at == last_created_at) & (ShadowTrade.id > last_id))
                )
            remaining = None if args.limit is None else args.limit - selected
            if remaining is not None and remaining <= 0:
                break
            page_size = min(args.batch_size, remaining) if remaining is not None else args.batch_size
            page_query = page_query.order_by(ShadowTrade.created_at, ShadowTrade.id).limit(page_size)
            trades = [
                SimpleNamespace(**dict(row))
                for row in (await db.execute(page_query)).mappings().all()
            ]
            if not trades:
                break
            batches += 1
            for trade in trades:
                config = trade.config_snapshot if isinstance(trade.config_snapshot, dict) else {}
                revision = await build_measurement_revision(
                    db,
                    trade,
                    timeframe_priority=(
                        args.timeframes
                        if args.timeframes is not None
                        else config.get("shadow_measurement_timeframe_priority")
                    ),
                    max_entry_lag_seconds=(
                        args.max_entry_lag_seconds
                        if args.max_entry_lag_seconds is not None
                        else config.get("shadow_entry_max_lag_seconds")
                    ),
                )
                status = str(revision["status"])
                counts[status] = counts.get(status, 0) + 1
                row_summary = {
                    "shadow_trade_id": str(trade.id),
                    "symbol": trade.symbol,
                    "outcome": trade.outcome,
                    "tp_pct": trade.tp_pct,
                    "sl_pct": trade.sl_pct,
                    "pnl_pct": trade.pnl_pct,
                    "status": status,
                    "source": revision["source"],
                    "timeframe": revision["timeframe"],
                    "input_hash": revision["input_hash"],
                    "legacy_mae_pct": revision["legacy_mae_pct"],
                    "legacy_mfe_pct": revision["legacy_mfe_pct"],
                    "mae_pct": revision["mae_pct"],
                    "mfe_pct": revision["mfe_pct"],
                    "mae_at": revision["mae_at"],
                    "mfe_at": revision["mfe_at"],
                    "unavailable_reason": revision["unavailable_reason"],
                }
                rows.append(row_summary)
                if args.apply and await persist_measurement_revision(db, revision):
                    inserted += 1
            selected += len(trades)
            last_created_at = trades[-1].created_at
            last_id = trades[-1].id
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
            if len(trades) < page_size:
                break
        tp_rows = [row for row in rows if row["outcome"] == "TP_HIT"]
        sl_rows = [row for row in rows if row["outcome"] == "SL_HIT"]
        legacy_mae_ratio = [
            abs(float(row["legacy_mae_pct"])) / float(row["sl_pct"])
            for row in rows
            if row["legacy_mae_pct"] is not None and row["sl_pct"]
        ]
        corrected_mae_ratio = [
            abs(float(row["mae_pct"])) / float(row["sl_pct"])
            for row in rows
            if row["mae_pct"] is not None and row["sl_pct"]
        ]
        legacy_mfe_ratio = [
            float(row["legacy_mfe_pct"]) / float(row["tp_pct"])
            for row in rows
            if row["legacy_mfe_pct"] is not None and row["tp_pct"]
        ]
        corrected_mfe_ratio = [
            float(row["mfe_pct"]) / float(row["tp_pct"])
            for row in rows
            if row["mfe_pct"] is not None and row["tp_pct"]
        ]
        summary = {
            "mode": "APPLY" if args.apply else "DRY_RUN",
            "selected": selected,
            "inserted": inserted,
            "batches": batches,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "resume_after": (
                {"created_at": last_created_at, "id": str(last_id)}
                if last_created_at is not None and last_id is not None
                else None
            ),
            "status_counts": counts,
            "outcome_counts": {
                outcome: sum(1 for row in rows if str(row["outcome"]) == outcome)
                for outcome in sorted({str(row["outcome"]) for row in rows})
            },
            "zero_regression": {
                "tp_legacy_mae_zero": sum(row["legacy_mae_pct"] == 0 for row in tp_rows),
                "tp_corrected_mae_zero": sum(row["mae_pct"] == 0 for row in tp_rows),
                "sl_legacy_mfe_zero": sum(row["legacy_mfe_pct"] == 0 for row in sl_rows),
                "sl_corrected_mfe_zero": sum(row["mfe_pct"] == 0 for row in sl_rows),
            },
            "ratio_quantiles": {
                "abs_mae_over_sl_legacy": _quantiles(legacy_mae_ratio),
                "abs_mae_over_sl_corrected": _quantiles(corrected_mae_ratio),
                "mfe_over_tp_legacy": _quantiles(legacy_mfe_ratio),
                "mfe_over_tp_corrected": _quantiles(corrected_mfe_ratio),
            },
            "entry_exit_classification": _entry_exit_classification(rows),
        }
        if not args.summary_only:
            summary["rows"] = rows
    return summary


def _parse_datetime(raw: str) -> datetime:
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _entry_exit_classification(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row["status"] != "READY" or row["mfe_pct"] is None:
            continue
        if float(row["mfe_pct"]) < 0.1:
            label = "NEVER_PROFITABLE_ENTRY"
        elif (
            row["outcome"] == "SL_HIT"
            and row["tp_pct"]
            and float(row["mfe_pct"]) >= 0.5 * float(row["tp_pct"])
        ):
            label = "PROFITABLE_THEN_REVERSED_EXIT"
        else:
            label = "INTERMEDIATE"
        r_value = (
            float(row["pnl_pct"]) / float(row["sl_pct"])
            if row["pnl_pct"] is not None and row["sl_pct"]
            else None
        )
        bucket = buckets.setdefault(label, {"n": 0, "r_values": []})
        bucket["n"] += 1
        if r_value is not None:
            bucket["r_values"].append(r_value)
    return {
        label: {
            "n": bucket["n"],
            "n_with_r": len(bucket["r_values"]),
            "avg_r": (
                sum(bucket["r_values"]) / len(bucket["r_values"])
                if bucket["r_values"]
                else None
            ),
        }
        for label, bucket in sorted(buckets.items())
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--trade-id")
    target.add_argument("--report-run-id")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--max-batches", type=int)
    parser.add_argument("--created-from")
    parser.add_argument("--created-to")
    parser.add_argument("--after-created-at")
    parser.add_argument("--after-id")
    parser.add_argument(
        "--timeframes",
        nargs="+",
        choices=["1m", "5m", "15m", "1h"],
        help="Explicit dry-run priority; otherwise use each immutable config snapshot.",
    )
    parser.add_argument("--max-entry-lag-seconds", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--count-only", action="store_true")
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 5000:
        parser.error("--batch-size must be between 1 and 5000")
    return args


if __name__ == "__main__":
    print(_json(asyncio.run(run(parse_args()))))

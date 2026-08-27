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
        query = query.order_by(ShadowTrade.created_at, ShadowTrade.id)
        if args.limit is not None:
            query = query.limit(args.limit)
        trades = [SimpleNamespace(**dict(row)) for row in (await db.execute(query)).mappings().all()]

        counts: dict[str, int] = {}
        inserted = 0
        rows: list[dict[str, Any]] = []
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
            rows.append(
                {
                    "shadow_trade_id": str(trade.id),
                    "symbol": trade.symbol,
                    "outcome": trade.outcome,
                    "tp_pct": trade.tp_pct,
                    "sl_pct": trade.sl_pct,
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
            )
            if args.apply and await persist_measurement_revision(db, revision):
                inserted += 1

        if args.apply:
            await db.commit()
        else:
            await db.rollback()
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
            "selected": len(trades),
            "inserted": inserted,
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
        }
        if not args.summary_only:
            summary["rows"] = rows
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--trade-id")
    target.add_argument("--report-run-id")
    target.add_argument("--all", action="store_true")
    parser.add_argument("--limit", type=int)
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
    return parser.parse_args()


if __name__ == "__main__":
    print(_json(asyncio.run(run(parse_args()))))

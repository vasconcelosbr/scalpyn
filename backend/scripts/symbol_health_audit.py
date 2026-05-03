"""Operator CLI for the symbol-ingestion audit (Task #194).

Usage::

    python -m scripts.symbol_health_audit               # full audit + repair
    python -m scripts.symbol_health_audit --dry-run     # only report
    python -m scripts.symbol_health_audit --no-approve  # skip pool_coins UPDATE
    python -m scripts.symbol_health_audit --json        # machine-readable

The CLI is a thin wrapper over :class:`SymbolHealthService` and
:class:`SymbolRemediator` so the same code path runs from the admin
endpoint and from the Celery beat task.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import List


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.symbol_health_audit",
        description="Audit and (optionally) repair the spot symbol ingestion pipeline.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify symbols and propose actions but do not execute any repair.",
    )
    p.add_argument(
        "--no-approve",
        action="store_true",
        help="Never set pool_coins.is_approved = TRUE (recompute + WS refresh still run).",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of the human-readable summary.",
    )
    p.add_argument(
        "--symbol",
        action="append",
        default=None,
        metavar="SYMBOL",
        help="Limit the audit to this symbol (may be repeated). Default: full pool universe.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=16,
        help="Per-symbol probe concurrency (default 16).",
    )
    return p


def _format_human(report_dict: dict, remediation_dict: dict) -> str:
    lines: List[str] = []
    lines.append(f"=== Symbol audit @ {report_dict['checked_at']} ===")
    lines.append(f"Total symbols audited: {report_dict['total']}")
    lines.append("Counts by status:")
    for status, n in sorted(report_dict["counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {status:<20} {n}")
    lines.append("")
    lines.append("=== Remediation ===")
    lines.append(f"dry_run={remediation_dict['dry_run']}")
    lines.append(f"refresh_subscriptions_requested={remediation_dict['refresh_subscriptions_requested']}")
    lines.append(f"recompute_enqueued={remediation_dict['recompute_enqueued']}")
    lines.append("Actions by type:")
    for action, n in sorted(
        remediation_dict["counts_by_action"].items(), key=lambda kv: -kv[1]
    ):
        lines.append(f"  {action:<32} {n}")
    return "\n".join(lines)


async def _run_async(args) -> int:
    # Imported lazily so ``--help`` does not require the FastAPI app graph.
    from app.services.symbol_health_service import (
        SymbolHealthService,
        build_etapa8_envelope,
    )
    from app.services.symbol_remediator import (
        GateSymbolValidator,
        SymbolRemediator,
    )

    health = SymbolHealthService(concurrency=args.concurrency)
    report = await health.audit(symbols=args.symbol)

    remediator = SymbolRemediator(
        validator=GateSymbolValidator(),
        approve_unknown=not args.no_approve,
        recompute_indicators=True,
    )
    rem = await remediator.remediate(report, dry_run=args.dry_run)

    # Etapa 8 envelope is the operator contract — same shape as the
    # admin endpoint so the CLI and HTTP responses are interchangeable.
    envelope = build_etapa8_envelope(report, rem)
    envelope["report"] = report.to_dict()
    envelope["remediation"] = rem.to_dict()
    if args.json:
        print(json.dumps(envelope, default=str, indent=2))
    else:
        print(_format_human(envelope["report"], envelope["remediation"]))
        print()
        print(
            f"Resumo: total={envelope['resumo']['total']} "
            f"corrigidos={envelope['resumo']['corrigidos']} "
            f"pendentes={envelope['resumo']['pendentes']} "
            f"system_healthy={envelope['system_healthy']}"
        )
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _build_parser().parse_args()
    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
"""Label Lab report — evaluate label-definition viability for both ML lanes
BEFORE training, and persist every evaluation to label_lab_runs for audit.

Part of the Profile Intelligence Adaptive Loop reformulation (audit
2026-06-24, Fase 5). Mirrors backend/scripts/backfill_model_promotion_gate.py:
read-only by default, --commit required to persist.

Evaluates the 2x2 grid that matters today:
  - label_version=is_win_fast_v1 (target_window_seconds=1800)
  - label_version=is_tp_4h_v1    (target_window_seconds=14400)
  against:
  - Lane 1 (L1_SPECTRUM): source = 'L1_SPECTRUM'
  - Lane 2 (L3_PROFILE strict): source = 'L3_LAB' OR (source = 'L3' AND
    profile_id IS NOT NULL) — see CLAUDE.md memory: L3 has 66.64% NULL
    profile_id, training without this filter produces a global/unknown model.

Usage:
    python run_label_lab_report.py            (dry-run, default)
    python run_label_lab_report.py --commit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from app.services.profile_intelligence_label_lab import evaluate_label_candidate  # noqa: E402

LABELS = [
    ("is_win_fast_v1", 1800),
    ("is_tp_4h_v1", 14400),
]

LANES = {
    "L1_SPECTRUM": """
        SELECT outcome, holding_seconds, source, profile_id
        FROM shadow_trades
        WHERE status = 'COMPLETED' AND source = 'L1_SPECTRUM'
    """,
    "L3_PROFILE_STRICT": """
        SELECT outcome, holding_seconds, source, profile_id
        FROM shadow_trades
        WHERE status = 'COMPLETED'
          AND (source = 'L3_LAB' OR (source = 'L3' AND profile_id IS NOT NULL))
    """,
}


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Defina DATABASE_PUBLIC_URL ou DATABASE_URL no ambiente.")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    elif dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return dsn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true", help="persist results to label_lab_runs")
    args = parser.parse_args()
    dry_run = not args.commit

    conn = psycopg2.connect(_get_dsn())
    try:
        for lane_name, lane_sql in LANES.items():
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(lane_sql)
                rows = [dict(r) for r in cur.fetchall()]
            print(f"\n=== Lane: {lane_name} (n_rows_fetched={len(rows)}) ===")

            for label_version, window_s in LABELS:
                result = evaluate_label_candidate(
                    rows, label_version=label_version, target_window_seconds=window_s,
                )
                print(
                    f"  {label_version:<16} window={window_s:>6}s -> "
                    f"status={result['status']:<22} "
                    f"samples={result['metrics']['total_samples']:>5} "
                    f"positive_rate={result['metrics']['positive_rate']} "
                    f"distinct_profiles={result['metrics']['distinct_profiles']} "
                    f"reasons={result['reasons']}"
                )

                if not dry_run:
                    with conn.cursor() as wcur:
                        wcur.execute(
                            """
                            INSERT INTO label_lab_runs (
                                label_version, target_window_seconds, source_filter,
                                status, reasons, thresholds, metrics, by_source, triggered_by
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                label_version, window_s, lane_name, result["status"],
                                json.dumps(result["reasons"]), json.dumps(result["thresholds"]),
                                json.dumps(result["metrics"]), json.dumps(result["by_source"]),
                                "run_label_lab_report.py",
                            ),
                        )

        if not dry_run:
            conn.commit()
            print("\nCOMMITADO: avaliações persistidas em label_lab_runs.")
        else:
            print("\nDRY-RUN: nenhuma escrita feita. Rode com --commit para persistir.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

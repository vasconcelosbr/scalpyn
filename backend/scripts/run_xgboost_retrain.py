"""Governed XGBoost retrain entrypoint for the L1 and L3 lanes.

Examples:
    python backend/scripts/run_xgboost_retrain.py --lane l1 --dry-run
    python backend/scripts/run_xgboost_retrain.py --lane l3
    python backend/scripts/run_xgboost_retrain.py --lane both --dry-run

Training persists candidates and promotion-gate evidence but never activates a
model automatically.
"""

from __future__ import annotations

import argparse
import runpy
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lane",
        choices=("l1", "l3", "both"),
        required=True,
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    lane_scripts = []
    if args.lane in {"l1", "both"}:
        lane_scripts.append(scripts_dir / "run_lgbm_retrain.py")
    if args.lane in {"l3", "both"}:
        lane_scripts.append(scripts_dir / "run_catboost_retrain.py")

    forwarded_args = ["--dry-run"] if args.dry_run else []
    for lane_script in lane_scripts:
        sys.argv = [str(lane_script), *forwarded_args]
        runpy.run_path(str(lane_script), run_name="__main__")


if __name__ == "__main__":
    main()

"""Emit the governed L1/L2 update JSON from a PASSED calibration run."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import AsyncSessionLocal  # noqa: E402
from app.services.mtf_profile_activation_service import (  # noqa: E402
    IMPORT_MODE,
    parse_activation_document,
)
from app.services.profile_execution_contract import (  # noqa: E402
    load_profile_execution_snapshots,
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    parser.add_argument("--l1-profile-id", type=UUID, required=True)
    parser.add_argument("--l2-profile-id", type=UUID, required=True)
    parser.add_argument("--l1-watchlist-id", type=UUID, required=True)
    parser.add_argument("--l2-watchlist-id", type=UUID, required=True)
    parser.add_argument("--l3-source-identity", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> None:
    l3_source_identity = json.loads(
        args.l3_source_identity.read_text(encoding="utf-8")
    )
    async with AsyncSessionLocal() as db:
        run = (await db.execute(text("""
            SELECT status, approved_policy_hash, dataset_hash, selected_profiles
              FROM mtf_calibration_runs
             WHERE id = CAST(:run_id AS UUID)
               AND user_id = CAST(:user_id AS UUID)
        """), {"run_id": str(args.run_id), "user_id": str(args.user_id)})).mappings().one_or_none()
        if run is None or run["status"] != "PASSED":
            raise SystemExit("MTF_CALIBRATION_RUN_NOT_PASSED")
        profile_ids = {"L1": args.l1_profile_id, "L2": args.l2_profile_id}
        snapshots = await load_profile_execution_snapshots(
            db, profile_ids.values(), user_id=args.user_id
        )
        if set(snapshots) != set(profile_ids.values()):
            raise SystemExit("MTF_PROFILE_NOT_FOUND")
        selected = dict(run["selected_profiles"] or {})
        watchlist_rows = (await db.execute(text("""
            SELECT id, profile_id
              FROM pipeline_watchlists
             WHERE user_id = CAST(:user_id AS UUID)
               AND id IN (CAST(:l1 AS UUID), CAST(:l2 AS UUID))
        """), {
            "user_id": str(args.user_id), "l1": str(args.l1_watchlist_id),
            "l2": str(args.l2_watchlist_id),
        })).mappings().all()
        watchlist_bindings = {row["id"]: row["profile_id"] for row in watchlist_rows}
        if set(watchlist_bindings) != {args.l1_watchlist_id, args.l2_watchlist_id}:
            raise SystemExit("MTF_WATCHLIST_NOT_FOUND")
        if any(value is None for value in watchlist_bindings.values()):
            raise SystemExit("MTF_WATCHLIST_PROFILE_REQUIRED")
        profiles: dict[str, object] = {}
        thresholds_hashes: dict[str, str] = {}
        for layer in ("L1", "L2"):
            emitted = dict(selected.get(layer) or {})
            document = dict(emitted.get("payload") or {})
            snapshot = snapshots[profile_ids[layer]]["contract"]
            profiles[layer] = {
                **document,
                "profile_id": str(profile_ids[layer]),
                "expected_profile_version_id": snapshot["profile_version_id"],
                "expected_profile_config_hash": snapshot["profile_projection_hash"],
                "name": layer,
                "profile_kind": "MTF_LAYER",
                "layer": layer,
                "funnel_role": (
                    "primary_filter" if layer == "L1" else "score_engine"
                ),
                "activation_mode": "SHADOW",
            }
            thresholds_hashes[layer] = str(emitted.get("thresholds_hash") or "")
        payload = {
            "import_mode": IMPORT_MODE,
            "allow_create": False,
            "activation_mode": "SHADOW",
            "operational_effect": False,
            "profiles": profiles,
            "watchlists": {
                "L1": str(args.l1_watchlist_id),
                "L2": str(args.l2_watchlist_id),
            },
            "expected_watchlist_bindings": {
                "L1": str(watchlist_bindings[args.l1_watchlist_id]),
                "L2": str(watchlist_bindings[args.l2_watchlist_id]),
            },
            "calibration": {
                "run_id": str(args.run_id),
                "policy_hash": run["approved_policy_hash"],
                "dataset_hash": run["dataset_hash"],
                "thresholds_hashes": thresholds_hashes,
            },
            "l3_source_identity": l3_source_identity,
        }
        parse_activation_document(payload)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            "status": "EMITTED", "output": str(args.output),
            "calibration_run_id": str(args.run_id),
        }))


if __name__ == "__main__":
    asyncio.run(_run(_args()))

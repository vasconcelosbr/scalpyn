"""Activate the clean L3 dataset boundary, the v2 label rules and the
missing-candle guard -- through ConfigService (validated + audited).

Every value is an explicit argument; nothing is defaulted in code. Default is a
DRY RUN that prints the current value, the proposed value and the validation
result of each key. ``--apply`` writes through ConfigService.update_config,
which records config_audit_log and invalidates the cache, then reads back.

  ml (config_type='ml'):
    ml_l3_dataset_valid_from      <- --valid-from
    ml_l3_label_certification     <- {version: l3_managed_cert_v2, --flow-evidence, --timeout-horizon}
  shadow_l3_exit_runtime:
    {version: shadow_l3_exit_runtime_v1, missing_candle_wait_seconds: --missing-candle-wait-seconds}

PowerShell (backend/, DATABASE_URL set):
  python -m scripts.configure_l3_label_v2 --valid-from 2026-10-19T18:50:00+00:00 `
      --flow-evidence REPLAY_FALLBACK --timeout-horizon POLICY_TIMEOUT --missing-candle-wait-seconds 600
  (repeat with --apply after reviewing the dry run)
"""
import argparse
import asyncio
import json
from uuid import UUID

from sqlalchemy import text


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    async with CeleryAsyncSessionLocal() as db:
        result = await execute(db, args)
    if result.get("read_back_ok") is False:
        raise SystemExit("read-back mismatch: inspect config_audit_log")


async def execute(db, args, service=None):
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
    from app.ml.l3_managed_exit import CERTIFICATION_KEY, CERTIFICATION_V2, certification_rules, definition
    from app.schemas.shadow_l3_exit_runtime import CONFIG_TYPE, VERSION, ShadowL3ExitRuntimeConfig
    from app.services.config_service import ConfigService

    frontier = parse_required_ml_dataset_valid_from({"ml_dataset_valid_from": args.valid_from}).isoformat()
    rules = certification_rules({"version": CERTIFICATION_V2, "flow_evidence": args.flow_evidence,
                                 "timeout_horizon": args.timeout_horizon})
    runtime = ShadowL3ExitRuntimeConfig.model_validate(
        {"version": VERSION, "missing_candle_wait_seconds": args.missing_candle_wait_seconds}).model_dump()
    if not args.apply:
        await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        await db.commit()
    owner = (await db.execute(text("""SELECT user_id::text, config_json FROM config_profiles
        WHERE config_type='ml' AND is_active = true AND pool_id IS NULL LIMIT 1"""))).one()
    user_id = UUID(args.user_id or owner[0])
    ml = dict(owner[1] if isinstance(owner[1], dict) else json.loads(owner[1]))
    if definition(ml) is None:
        raise SystemExit("ml_l3_managed_exit is not configured; the v2 rules have nothing to certify.")
    current_runtime = (await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND config_type=:type AND pool_id IS NULL AND is_active LIMIT 1"""),
        {"uid": user_id, "type": CONFIG_TYPE})).scalar_one_or_none()
    new_ml = {**ml, "ml_l3_dataset_valid_from": frontier, CERTIFICATION_KEY: rules}
    plan = {
        "mode": "APPLY" if args.apply else "DRY_RUN", "user_id": str(user_id),
        "ml.ml_l3_dataset_valid_from": {"current": ml.get("ml_l3_dataset_valid_from"), "proposed": frontier},
        f"ml.{CERTIFICATION_KEY}": {"current": ml.get(CERTIFICATION_KEY), "proposed": rules},
        f"{CONFIG_TYPE}": {"current": current_runtime, "proposed": runtime},
        "unchanged_ml_keys": len([k for k in ml if k not in ("ml_l3_dataset_valid_from", CERTIFICATION_KEY)]),
    }
    print(json.dumps(plan, indent=2, default=str), flush=True)
    if not args.apply:
        print("[configure] dry run only; re-run with --apply to write.", flush=True)
        return plan
    service = service or ConfigService()
    reason = "L3 clean dataset boundary + l3_managed_cert_v2 + missing-candle guard (AUD-ML-L3-010)"
    await service.update_config(db, "ml", user_id, new_ml, user_id, change_description=reason)
    await service.update_config(db, CONFIG_TYPE, user_id, runtime, user_id, change_description=reason)
    back_ml = (await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND config_type='ml' AND pool_id IS NULL AND is_active LIMIT 1"""), {"uid": user_id})).scalar_one()
    back_rt = (await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND config_type=:type AND pool_id IS NULL AND is_active LIMIT 1"""),
        {"uid": user_id, "type": CONFIG_TYPE})).scalar_one()
    ok = (back_ml.get(CERTIFICATION_KEY) == rules and back_ml.get("ml_l3_dataset_valid_from") == frontier
          and back_rt == runtime and {k: v for k, v in back_ml.items() if k in ml and k not in plan_keys()} ==
          {k: v for k, v in ml.items() if k not in plan_keys()})
    print(json.dumps({"read_back_ok": ok}), flush=True)
    return {**plan, "read_back_ok": ok}


def plan_keys():
    from app.ml.l3_managed_exit import CERTIFICATION_KEY
    return ("ml_l3_dataset_valid_from", CERTIFICATION_KEY)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--valid-from", required=True)
    parser.add_argument("--flow-evidence", required=True, choices=("REQUIRE_VALID", "REPLAY_FALLBACK"))
    parser.add_argument("--timeout-horizon", required=True, choices=("EXIT_AT", "POLICY_TIMEOUT"))
    parser.add_argument("--missing-candle-wait-seconds", required=True, type=int)
    parser.add_argument("--user-id")
    parser.add_argument("--apply", action="store_true")
    asyncio.run(run(parser.parse_args()))

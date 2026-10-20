"""Re-certify completed L3 managed shadows under the configured label rules.

Scope is deliberately narrow: shadows of the active managed-exit contract
whose entry is at or after ``ml_l3_dataset_valid_from`` and whose persisted
proof was stamped by a different certification version (typically v1 proofs
written between the clean boundary and the v2 activation). Older rows are
never touched -- they stay outside the training frontier anyway.

Default is a DRY RUN in a READ ONLY session: it prints old -> new validity per
outcome and writes nothing. ``--apply`` re-runs the same ``certify`` used by
finalize, stores the new proof in ``shadow_l3_exit_states.state.ml_label``
(the previous proof is kept in ``state.ml_label_superseded``) and updates only
the fields a managed proof owns (eligible_for_training, net_return_pct,
label_resolved_at). One transaction per batch, then a read-back check.

PowerShell (backend/, DATABASE_URL set):
  python -m scripts.recertify_l3_managed_labels              # dry run
  python -m scripts.recertify_l3_managed_labels --apply      # after review
"""
import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select, text


async def _targets(db, *, user_id, spec_hash, frontier, cert_version):
    from app.ml.l3_managed_exit import VERSION
    return (await db.execute(text("""
        SELECT st.id::text AS id
          FROM shadow_trades st JOIN shadow_l3_exit_states sx ON sx.shadow_id = st.id
         WHERE st.user_id = :uid AND st.source = 'L3' AND st.label_contract_version = :version
           AND st.config_snapshot->'l3_managed_ml'->'contract'->>'hash' = :hash
           AND st.entry_timestamp >= :frontier AND st.status = 'COMPLETED' AND st.outcome IS NOT NULL
           AND sx.state ? 'ml_label'
           AND (sx.state->'ml_label'->>'certification_version') IS DISTINCT FROM :cert
         ORDER BY st.entry_timestamp"""),
        {"uid": user_id, "version": VERSION, "hash": spec_hash, "frontier": frontier,
         "cert": cert_version})).scalars().all()


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    async with CeleryAsyncSessionLocal() as db:
        report = await execute(db, args)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    if args.apply and report["remaining_after"]:
        raise SystemExit(f"read-back: {report['remaining_after']} rows still carry another certification version")


async def execute(db, args):
    from app.ml.dataset_config import parse_required_ml_dataset_valid_from
    from app.ml.l3_managed_exit import (apply_proof, certify, configured_certification, definition,
                                        lane_config, load_certification_evidence)
    from app.models.shadow_trade import ShadowTrade
    now = datetime.now(timezone.utc)
    if not args.apply:
        await db.execute(text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"))
        await db.commit()
    owner = (await db.execute(text("""SELECT user_id::text, config_json FROM config_profiles
        WHERE config_type='ml' AND is_active = true AND pool_id IS NULL LIMIT 1"""))).one()
    user_id = args.user_id or owner[0]
    cfg = lane_config(owner[1] if isinstance(owner[1], dict) else json.loads(owner[1]))
    spec, rules = definition(cfg), configured_certification(cfg)
    if spec is None or rules is None:
        raise SystemExit("Configure ml_l3_managed_exit and ml_l3_label_certification first; nothing to do.")
    if cfg.get("ml_l3_dataset_valid_from") in (None, ""):
        raise SystemExit("Configure ml_l3_dataset_valid_from (the clean boundary) first.")
    frontier = parse_required_ml_dataset_valid_from({"ml_dataset_valid_from": cfg["ml_l3_dataset_valid_from"]})
    ids = await _targets(db, user_id=user_id, spec_hash=spec["hash"], frontier=frontier, cert_version=rules["version"])
    print(f"[recertify] mode={'APPLY' if args.apply else 'DRY_RUN'} rules={rules} frontier={frontier.isoformat()} "
          f"targets={len(ids)}", flush=True)
    transitions, changed = Counter(), 0
    for start in range(0, len(ids), args.batch_size):
        chunk = ids[start:start + args.batch_size]
        query = select(ShadowTrade).where(ShadowTrade.id.in_(chunk))
        if args.apply:
            query = query.with_for_update()
        shadows = (await db.execute(query)).scalars().all()
        for shadow in shadows:
            state = (await db.execute(text("SELECT state FROM shadow_l3_exit_states WHERE shadow_id=:id"
                                           + (" FOR UPDATE" if args.apply else "")), {"id": shadow.id})).scalar_one()
            old = (state or {}).get("ml_label") or {}
            decisions, boundary = await load_certification_evidence(db, shadow)
            proof = certify(shadow, decisions, boundary, checked_at=now, certification=rules)
            transitions[f"{shadow.outcome}|{'valid' if old.get('valid') else old.get('reason')}->"
                        f"{'valid' if proof['valid'] else proof['reason']}"] += 1
            if args.apply:
                apply_proof(shadow, proof)
                new_state = {**state, "ml_label": proof,
                             "ml_label_superseded": [*(state.get("ml_label_superseded") or []), old]}
                await db.execute(text("""UPDATE shadow_l3_exit_states SET state=CAST(:state AS JSONB),
                    checked_at=clock_timestamp() WHERE shadow_id=:id"""),
                    {"id": shadow.id, "state": json.dumps(new_state, default=str)})
                changed += 1
        await db.commit()
        print(f"[recertify] {min(start + args.batch_size, len(ids))}/{len(ids)}", flush=True)
    remaining = len(await _targets(db, user_id=user_id, spec_hash=spec["hash"], frontier=frontier,
                                   cert_version=rules["version"]))
    return {"mode": "APPLY" if args.apply else "DRY_RUN", "rules": rules, "frontier": frontier.isoformat(),
            "targets": len(ids), "updated": changed, "remaining_after": remaining,
            "transitions": dict(sorted(transitions.items()))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the new proofs (default: dry run)")
    parser.add_argument("--user-id")
    parser.add_argument("--batch-size", type=int, default=50)
    asyncio.run(run(parser.parse_args()))

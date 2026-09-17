"""S5 (2026-09-17 shadow-trade collapse fix): read-only v1-vs-candidate
replay comparison for ONE real L3 shadow, using its preserved evidence.

Never writes, never approves, never activates a policy. Answers exactly the
question S1 (v2 flow-window/candle-delay split) needs answered before any
tenant is switched: for this real shadow, does the candidate policy's flow
-evidence-quality verdict differ from what actually happened, and at which
candle, and along which dimension.

Usage:
  python -m scripts.shadow_l3_replay_compare --shadow-id UUID [--policy candidate.json] [--output report.json]

Without --policy, builds a candidate v2 policy from the shadow's OWN frozen
v1 policy (every field copied verbatim) plus flow_window_age_seconds set to
the same numeric limit v1 used for max_age_seconds -- the natural "what if
this exact threshold applied to the flow-window dimension alone" candidate.
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.schemas.shadow_l3_exit_policy import (
    ShadowL3ExitPolicyV2, VERSION_V2, validate_policy,
)
from app.services.shadow_l3_exit_service import build_evidence
from scripts.shadow_l3_replay import dated


def fetch_shadow(shadow_id):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(
        os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"],
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    )
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as q:
            q.execute(
                """SELECT id, symbol, entry_timestamp,
                          config_snapshot->'shadow_l3_exit_policy' AS frozen_policy
                     FROM shadow_trades
                    WHERE id=%s AND source='L3'
                      AND config_snapshot ? 'shadow_l3_exit_policy'""",
                (str(shadow_id),),
            )
            trade = q.fetchone()
            if trade is None:
                raise SystemExit(
                    f"shadow {shadow_id} not found, not source=L3, or has no "
                    "shadow_l3_exit_policy in its frozen config_snapshot"
                )
            q.execute(
                """SELECT evidence FROM shadow_l3_exit_decisions
                    WHERE shadow_id=%s ORDER BY candle_at""",
                (str(shadow_id),),
            )
            trade["decisions"] = [row["evidence"] for row in q.fetchall()]
            return trade
    finally:
        conn.close()


def default_candidate_v2(frozen_policy):
    base = dict(frozen_policy["config"])
    base["version"] = VERSION_V2
    base["flow_window_age_seconds"] = base.get("max_age_seconds")
    return ShadowL3ExitPolicyV2.model_validate(base)


def _post_warmup_first_invalid(rows, quality_key, entry_at, warmup_seconds):
    for row in rows:
        if row.get("skipped"):
            continue
        candle_at = datetime.fromisoformat(row["candle_at"])
        if (candle_at - entry_at).total_seconds() >= warmup_seconds and row[quality_key] != "VALID":
            return row["candle_at"]
    return None


def compare(trade, candidate_policy):
    """Recompute quality under ``candidate_policy`` for every persisted
    candle, diffing against the ORIGINAL evidence -- v1's actual recorded
    verdict, not a recomputation of it (S5: "comparação ... usando evidência
    preservada"). Mirrors l3_managed_exit.certify()'s own post-warmup rule
    (any non-VALID candle after warmup rejects the whole capture) to report
    whether that specific verdict would flip.
    """
    entry_at = trade["entry_timestamp"]
    price_history, structure_history = {}, {}
    rows = []
    for evidence in trade["decisions"]:
        candle = dated(evidence["candle"])
        at = datetime.fromisoformat(evidence["decision_at"])
        if evidence["structure_timeframe"] != candidate_policy.structure_timeframe:
            rows.append({"candle_at": candle["time"].isoformat(), "skipped": "STRUCTURE_TIMEFRAME_UNAVAILABLE"})
            continue
        if candidate_policy.warmup_seconds > evidence["replay_lookback_seconds"]:
            rows.append({"candle_at": candle["time"].isoformat(), "skipped": "INPUT_WINDOW_UNAVAILABLE"})
            continue
        for row in evidence["price_history"]:
            parsed = dated(row)
            price_history[parsed["time"]] = parsed
        for row in evidence["structure_history"]:
            parsed = dated(row)
            structure_history[parsed["time"]] = parsed
        candidate_evidence = build_evidence(
            [dated(b) for b in evidence["flow_buckets"]],
            [price_history[t] for t in sorted(price_history)],
            [structure_history[t] for t in sorted(structure_history)],
            candidate_policy, entry_at, candle["time"] + timedelta(minutes=1), at,
            evidence.get("flow_context"),
        )
        rows.append({
            "candle_at": candle["time"].isoformat(),
            "v1_quality": evidence.get("quality"),
            "v2_quality": candidate_evidence.get("quality"),
            "v1_data_age_seconds": evidence.get("data_age_seconds"),
            "v2_flow_window_age_seconds": candidate_evidence.get("flow_window_age_seconds"),
            "collection_lag_seconds": evidence.get("collection_lag_seconds"),
            "diverges": evidence.get("quality") != candidate_evidence.get("quality"),
        })
    warmup = candidate_policy.warmup_seconds
    v1_first_invalid = _post_warmup_first_invalid(rows, "v1_quality", entry_at, warmup)
    v2_first_invalid = _post_warmup_first_invalid(rows, "v2_quality", entry_at, warmup)
    return {
        "shadow_id": str(trade["id"]), "symbol": trade["symbol"],
        "v1_incomplete_flow_evidence_at": v1_first_invalid,
        "v2_incomplete_flow_evidence_at": v2_first_invalid,
        "verdict_flips": bool(v1_first_invalid) != bool(v2_first_invalid),
        "candidate_policy_hash": candidate_policy.digest(),
        "candidate_policy_version": candidate_policy.version,
        "candles": rows,
        "note": (
            "Read-only comparison against preserved evidence. Does not "
            "certify, approve, retrain, or activate any policy."
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--shadow-id", required=True)
    parser.add_argument("--policy")
    parser.add_argument("--output")
    args = parser.parse_args()
    trade = fetch_shadow(args.shadow_id)
    if args.policy:
        candidate = validate_policy(json.loads(Path(args.policy).read_text()))
    else:
        candidate = default_candidate_v2(trade["frozen_policy"])
    if candidate.missing_parameters():
        parser.error("Candidate parameters incomplete: " + ", ".join(candidate.missing_parameters()))
    report = compare(trade, candidate)
    text = json.dumps(report, default=str, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf8")
        print(f"Read-only report written to {args.output}; no approval or activation performed.")
    else:
        print(text)


if __name__ == "__main__":
    main()

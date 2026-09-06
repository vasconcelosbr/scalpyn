"""Read-only export/replay. Never approves a policy or updates a shadow.

Export: python -m scripts.shadow_l3_replay export --user-id UUID --output file.json
Replay: python -m scripts.shadow_l3_replay replay --input file.json --policy candidate.json
        --split-at ISO_TIMESTAMP --output report.json
"""
import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from app.schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy
from app.services.shadow_l3_exit_evaluator import advance
from app.services.shadow_l3_exit_service import build_evidence


def dated(row):
    return {k:datetime.fromisoformat(v) if isinstance(v,str) and k in
            ("time","first_at","last_at","available_at","ingested_at") else v for k,v in row.items()}


def replay(data, policy, split_at):
    results=[]
    for trade in data["trades"]:
        state={};price_history={};structure_history={};entry_at=datetime.fromisoformat(trade["entry_timestamp"])
        reasons=set()
        for record in trade["decisions"]:
            evidence=record["evidence"]
            candle=dated(evidence["candle"])
            at=datetime.fromisoformat(evidence["decision_at"])
            if evidence["structure_timeframe"]!=policy.structure_timeframe:
                reasons.add("STRUCTURE_TIMEFRAME_UNAVAILABLE");break
            if policy.warmup_seconds>evidence["replay_lookback_seconds"]:
                reasons.add("INPUT_WINDOW_UNAVAILABLE");break
            for row in evidence["price_history"]:
                parsed=dated(row);price_history[parsed["time"]]=parsed
            for row in evidence["structure_history"]:
                parsed=dated(row);structure_history[parsed["time"]]=parsed
            current=build_evidence([dated(b) for b in evidence["flow_buckets"]],
                                   [price_history[t] for t in sorted(price_history)],
                                   [structure_history[t] for t in sorted(structure_history)],policy,
                                   entry_at,candle["time"]+timedelta(minutes=1),at,evidence.get("flow_context"))
            if current["quality"]!="VALID":reasons.add(current["quality"])
            state=advance(state,candle,current,policy,entry=trade["entry_price"],tp=trade["tp_price"],
                          sl=trade["sl_price"],trailing=trade["trailing"],entry_at=entry_at,
                          timeout_candles=trade["timeout_candles"])
            if state.get("outcome"):break
        gross=(state["exit_price"]/trade["entry_price"]-1)*100 if state.get("outcome") else None
        fee=trade["fee_roundtrip_pct"]
        exit_at=datetime.fromisoformat(state["exit_at"]) if state.get("exit_at") else None
        split="VALIDATION" if entry_at>=split_at else "DEVELOPMENT"
        if entry_at<split_at and (exit_at is None or exit_at>=split_at):split="EXCLUDED_CROSSES_SPLIT"
        results.append({"id":trade["id"],"split":split,"state":state,"quality_reasons":sorted(reasons),
                        "baseline_gross_pct":trade["baseline_gross_pct"],"candidate_gross_pct":gross,
                        "fee_roundtrip_pct":fee,"candidate_net_pct":gross-fee if gross is not None and fee is not None else None})
    return {"decision":"REVIEW_REQUIRED","policy_hash":policy.digest(),"policy":policy.model_dump(),
            "split_at":split_at.isoformat(),"dataset_cutoff":data["cutoff"],"trades":results,
            "approval":None,"notes":"Prospective evidence only. Incomplete horizons are not wins or losses. No automatic promotion."}


def export(user_id):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn=psycopg2.connect(os.environ.get("DATABASE_PUBLIC_URL") or os.environ["DATABASE_URL"],
                          options="-c default_transaction_read_only=on -c statement_timeout=30000")
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as q:
            q.execute("SELECT now() cutoff");cutoff=q.fetchone()["cutoff"]
            q.execute("""SELECT st.id,st.entry_timestamp,st.entry_price,st.tp_price,st.sl_price,
                     st.timeout_candles,st.config_snapshot->'trailing' trailing,
                     (st.config_snapshot->>'ml_fee_roundtrip_pct')::float fee_roundtrip_pct,
                     st.pnl_pct baseline_gross_pct
                FROM shadow_trades st WHERE st.user_id=%s AND st.source='L3'
                AND st.config_snapshot ? 'shadow_l3_exit_policy' AND st.created_at<=%s
                ORDER BY st.entry_timestamp,st.id""",(user_id,cutoff))
            trades=q.fetchall()
            for trade in trades:
                q.execute("SELECT evidence,state FROM shadow_l3_exit_decisions WHERE shadow_id=%s AND available_at<=%s ORDER BY candle_at",(trade["id"],cutoff))
                trade["decisions"]=q.fetchall()
            return {"cutoff":cutoff,"trades":trades}
    finally:conn.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument("action",choices=["export","replay"])
    parser.add_argument("--user-id");parser.add_argument("--input");parser.add_argument("--policy")
    parser.add_argument("--split-at");parser.add_argument("--output",required=True)
    args=parser.parse_args()
    if args.action=="export":
        if not args.user_id:parser.error("--user-id required")
        from uuid import UUID
        result=export(str(UUID(args.user_id)))
    else:
        if not all((args.input,args.policy,args.split_at)):parser.error("--input, --policy and --split-at required")
        p=ShadowL3ExitPolicy.model_validate(json.loads(Path(args.policy).read_text()))
        if p.missing_parameters():parser.error("Candidate parameters incomplete")
        split=datetime.fromisoformat(args.split_at)
        if split.tzinfo is None:parser.error("--split-at must include timezone")
        result=replay(json.loads(Path(args.input).read_text()),p,split)
    Path(args.output).write_text(json.dumps(result,default=str,indent=2),encoding="utf8")
    print("Read-only report written; no approval or activation performed.")


if __name__=="__main__":main()

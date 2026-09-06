"""Persistence and point-in-time evidence for shadow-only continuation."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy, frozen_policy
from .shadow_l3_exit_evaluator import advance


async def load_frozen_policy(db, user_id):
    result = await db.execute(text("""
        SELECT config_json FROM config_profiles WHERE user_id=:uid
        AND pool_id IS NULL AND config_type='shadow_l3_exit_policy' AND is_active
    """), {"uid": user_id})
    config = result.scalar_one_or_none() or {}
    return frozen_policy(config)


async def attach_states(db, rows, user_id):
    ids = [r.id for r in rows if r.source == "L3" and (r.config_snapshot or {}).get("shadow_l3_exit_policy")]
    if not ids:
        return
    records = (await db.execute(text("""
        SELECT shadow_id,state,checked_at,policy_hash,policy FROM shadow_l3_exit_states
        WHERE user_id=:uid AND shadow_id=ANY(:ids)
    """), {"uid":user_id,"ids":ids})).mappings().all()
    states = {r["shadow_id"]:{**r["state"],"checked_at":r["checked_at"],"policy_hash":r["policy_hash"],
                            "mode":r["policy"]["config"]["mode"],"version":r["policy"]["version"]} for r in records}
    for row in rows:
        row.l3_exit = states.get(row.id)


def build_evidence(buckets, candles, structure, policy, entry_at, end, decision_at, flow_context=None):
    evidence = {"quality": "UNAVAILABLE", "decision_at": decision_at.isoformat(),
                "flow_buckets": buckets, "structure_lost": False}
    if policy.missing_parameters():
        evidence["quality"] = "PARAMETERS_REQUIRED"
        return evidence
    start = min(entry_at, end - timedelta(seconds=policy.warmup_seconds))
    rows = [b for b in buckets if start <= b["last_at"] < end]
    if not rows:
        return evidence
    context = flow_context or {}
    first_at = context.get("first_at", rows[0]["first_at"])
    first_at = datetime.fromisoformat(first_at) if isinstance(first_at,str) else first_at
    gaps = [max(0,(first_at - start).total_seconds()), context.get("max_gap",0),
            (end - rows[-1]["last_at"]).total_seconds()]
    gaps.extend(b["max_gap"] for b in rows)
    gaps.extend((b["first_at"] - a["last_at"]).total_seconds() for a, b in zip(rows, rows[1:]))
    warm = [b for b in rows if b["time"] >= end - timedelta(seconds=policy.warmup_seconds)]
    coverage = min(100., len(warm) * 60 / policy.warmup_seconds * 100)
    age = (decision_at - rows[-1]["last_at"]).total_seconds()
    evidence.update(coverage_pct=coverage, data_age_seconds=age, max_gap_seconds=max(gaps),
                    cvd=sum(b["entry_delta"] for b in rows)+context.get("cvd_before_window",0))
    if (max(gaps) > policy.max_gap_seconds or coverage < policy.min_coverage_pct
            or age > policy.max_age_seconds or (decision_at-end).total_seconds() > policy.alignment_seconds):
        evidence["quality"] = "INCOMPLETE_OR_STALE"
        return evidence
    flow = [b for b in rows if b["time"] >= end - timedelta(seconds=policy.flow_window_seconds)]
    cvd = [b for b in rows if b["time"] >= end - timedelta(seconds=policy.cvd_window_seconds)]
    buy, sell = sum(b["buy"] for b in flow), sum(b["sell"] for b in flow)
    volume = sum(b["buy"] + b["sell"] for b in cvd)
    if buy + sell <= 0 or volume <= 0 or len(candles) < policy.atr_period + 1:
        return evidence
    recent = candles[-max(policy.atr_period+1, policy.flow_window_seconds//60):]
    if any((b["time"]-a["time"]).total_seconds()!=60 for a,b in zip(recent,recent[1:])):
        evidence["quality"] = "PRICE_HISTORY_GAP"
        return evidence
    prices = [c for c in candles if c["time"] >= end - timedelta(seconds=policy.flow_window_seconds)]
    if not prices:
        return evidence
    tr = [max(c["high"]-c["low"], abs(c["high"]-p["close"]), abs(c["low"]-p["close"]))
          for p, c in zip(candles, candles[1:])]
    # A pivot is confirmed only after the right-hand bars have CLOSED and arrived.
    pivots = []
    left, right = policy.pivot_left, policy.pivot_right
    for i in range(left, len(structure)-right):
        pivot_window = structure[i-left:i+right+1]
        if any((b["time"]-a["time"]).total_seconds()!=int(policy.structure_timeframe[:-1])*60
               for a,b in zip(pivot_window,pivot_window[1:])):
            continue
        value = structure[i]["low"]
        if all(value < c["low"] for c in structure[i-left:i] + structure[i+1:i+right+1]):
            pivots.append(value)
    support = pivots[-1] if len(pivots) >= 2 and pivots[-1] > pivots[-2] else None
    evidence.update(quality="VALID", taker_ratio=buy/(buy+sell),
                    delta_normalized=(buy-sell)/(buy+sell),
                    cvd_slope_normalized=sum(b["buy"]-b["sell"] for b in cvd)/volume,
                    price_return_pct=(prices[-1]["close"]/prices[0]["open"]-1)*100,
                    atr=sum(tr[-policy.atr_period:])/policy.atr_period,
                    support_price=support,
                    structure_lost=support is not None and structure[-1]["close"] < support)
    return evidence


async def candle_evidence(db, shadow, candle, history, policy):
    end = candle["time"] + timedelta(minutes=1)
    decision_at = max(end, candle["ingested_at"])
    start = min(shadow.entry_timestamp, end - timedelta(seconds=policy.warmup_seconds or 0))
    data = (await db.execute(text("""
        WITH trades AS (
          SELECT *, extract(epoch FROM occurred_at-lag(occurred_at) OVER(ORDER BY occurred_at,trade_id)) gap
          FROM shadow_l3_flow_trades
          WHERE exchange=:exchange AND market_type='spot' AND symbol=:symbol
            AND occurred_at >= :start AND occurred_at < :end AND available_at <= :decision
        ) SELECT date_trunc('minute',occurred_at) time,
          min(occurred_at) first_at, max(occurred_at) last_at,
          COALESCE(max(gap),0) max_gap,
          COALESCE(sum(amount) FILTER(WHERE side='buy'),0) buy,
          COALESCE(sum(amount) FILTER(WHERE side='sell'),0) sell,
          sum(CASE WHEN occurred_at>=:entry THEN CASE WHEN side='buy' THEN amount ELSE -amount END ELSE 0 END) entry_delta,
          count(*) n, max(available_at) available_at
        FROM trades GROUP BY 1 ORDER BY 1
    """), {"exchange": shadow.exchange or "gate.io", "symbol": shadow.symbol,
            "start": start, "end": end, "entry": shadow.entry_timestamp, "decision": decision_at})).mappings().all()
    buckets = [{k:float(v) if k in ("buy","sell","entry_delta","max_gap") else v
                for k,v in row.items()} for row in data]
    structure = history
    if policy.structure_timeframe != "1m":
        minutes = int(policy.structure_timeframe[:-1])
        structure = (await db.execute(text("""
            SELECT time, open, high, low, close FROM ohlcv
            WHERE symbol=:symbol AND exchange=:exchange AND market_type='spot'
              AND timeframe=:tf AND is_closed IS TRUE
              AND time >= :start AND time + make_interval(mins=>:minutes) <= :end
              AND ingested_at <= :decision ORDER BY time
        """), {"symbol":shadow.symbol,"exchange":shadow.exchange or "gate.io", "tf":policy.structure_timeframe,
                "start":start,"end":end,"decision":decision_at,"minutes":minutes})).mappings().all()
        structure = [{k:float(v) if k in ("open","high","low","close") else v for k,v in r.items()} for r in structure]
    evidence = build_evidence(buckets, history, structure, policy, shadow.entry_timestamp, end, decision_at)
    keep_from = end-timedelta(seconds=policy.replay_lookback_seconds)
    evidence["candle"] = candle
    evidence["price_history"] = [c for c in history if c["time"]>=keep_from]
    evidence["structure_history"] = [c for c in structure if c["time"]>=keep_from]
    evidence["flow_buckets"] = [b for b in buckets if b["time"]>=keep_from]
    evidence["replay_lookback_seconds"] = policy.replay_lookback_seconds
    evidence["flow_context"] = {
        "first_at":buckets[0]["first_at"] if buckets else None,
        "cvd_before_window":sum(b["entry_delta"] for b in buckets if b["time"]<keep_from),
        "max_gap":max([0]+[b["max_gap"] for b in buckets]+[(b["first_at"]-a["last_at"]).total_seconds() for a,b in zip(buckets,buckets[1:])]),
    }
    evidence["structure_timeframe"] = policy.structure_timeframe
    evidence["candle_ingested_at"] = candle["ingested_at"]
    evidence["processed_at"] = datetime.now(timezone.utc).isoformat()
    evidence["collection_lag_seconds"] = (decision_at-end).total_seconds()
    return evidence


async def advance_shadow(db, shadow):
    snapshot = (shadow.config_snapshot or {}).get("shadow_l3_exit_policy")
    if shadow.source != "L3" or not snapshot or not shadow.entry_timestamp or not shadow.tp_price or not shadow.sl_price:
        return None
    policy = ShadowL3ExitPolicy.model_validate(snapshot["config"])
    if snapshot["hash"] != policy.digest():
        raise ValueError("Frozen shadow L3 policy hash mismatch")
    if policy.mode == "LEGACY":
        return None
    await db.execute(text("""
        INSERT INTO shadow_l3_exit_states(shadow_id,user_id,policy_hash,policy)
        VALUES(:id,:uid,:hash,CAST(:policy AS JSONB)) ON CONFLICT DO NOTHING
    """), {"id":shadow.id,"uid":shadow.user_id,"hash":snapshot["hash"],"policy":json.dumps(snapshot)})
    row = (await db.execute(text("SELECT state FROM shadow_l3_exit_states WHERE shadow_id=:id FOR UPDATE"), {"id":shadow.id})).scalar_one()
    state = row or {}
    if state.get("observation_complete") or (state.get("outcome") and policy.mode != "OBSERVE"):
        return state
    start = shadow.entry_timestamp.replace(second=0,microsecond=0)
    # Historical candles support warmup/ATR but are never evaluated before entry.
    lookback = max(policy.warmup_seconds or 0, (policy.atr_period or 0)*60,
                   ((policy.pivot_left or 0)+(policy.pivot_right or 0)+2)*int(policy.structure_timeframe[:-1])*60)
    data = (await db.execute(text("""
        SELECT time,open,high,low,close,ingested_at FROM ohlcv
        WHERE symbol=:symbol AND exchange=:exchange AND market_type='spot'
          AND timeframe='1m' AND is_closed IS TRUE AND ingested_at IS NOT NULL
          AND time >= :start AND time < date_trunc('minute',now())
        ORDER BY time
    """), {"symbol":shadow.symbol,"exchange":shadow.exchange or "gate.io",
            "start":start-timedelta(seconds=lookback)})).mappings().all()
    candles = [{k:float(v) if k in ("open","high","low","close") else v for k,v in r.items()} for r in data]
    evaluated = 0
    horizon = shadow.entry_timestamp + timedelta(seconds=policy.observation_horizon_seconds)
    for index, candle in enumerate(candles):
        cursor = state.get("observation_cursor") if policy.mode == "OBSERVE" else state.get("last_candle_at")
        if candle["time"] < start or cursor and candle["time"] <= datetime.fromisoformat(cursor):
            continue
        if policy.mode == "OBSERVE" and candle["time"] >= horizon:
            state["observation_complete"] = True
            break
        decision_at = max(candle["ingested_at"],candle["time"]+timedelta(minutes=1))
        history = [c for c in candles[:index+1] if c["ingested_at"] <= decision_at]
        evidence = await candle_evidence(db,shadow,candle,history,policy)
        state = advance(state,candle,evidence,policy,entry=float(shadow.entry_price),tp=float(shadow.tp_price),
                        sl=float(shadow.sl_price),trailing=(shadow.config_snapshot or {}).get("trailing") or {},
                        entry_at=shadow.entry_timestamp,timeout_candles=shadow.timeout_candles)
        if policy.mode == "OBSERVE":
            state["observation_cursor"] = candle["time"].isoformat()
        elif state.get("last_candle_at") != candle["time"].isoformat():
            continue
        evaluated += 1
        await db.execute(text("""
            INSERT INTO shadow_l3_exit_decisions(shadow_id,candle_at,available_at,policy_hash,evidence,state)
            VALUES(:id,:at,:available,:hash,CAST(:evidence AS JSONB),CAST(:state AS JSONB)) ON CONFLICT DO NOTHING
        """), {"id":shadow.id,"at":candle["time"],"available":decision_at,"hash":snapshot["hash"],
                "evidence":json.dumps(evidence,default=str),"state":json.dumps(state,default=str)})
        if (state.get("outcome") and policy.mode != "OBSERVE") or evaluated >= policy.max_candles_per_run:
            break
    await db.execute(text("UPDATE shadow_l3_exit_states SET state=CAST(:state AS JSONB),checked_at=clock_timestamp() WHERE shadow_id=:id"),
                     {"id":shadow.id,"state":json.dumps(state,default=str)})
    if policy.mode == "APPLY" and state.get("outcome") and shadow.status != "COMPLETED":
        from ..tasks.shadow_trade_monitor import _finalize_outcome
        price = state["exit_price"]
        _finalize_outcome(shadow,state["outcome"],price,datetime.fromisoformat(state["exit_at"]),float(shadow.entry_price),
                          closure_path="l3_continuation",exit_price_nominal=state.get("trigger_price"),
                          exit_price_observed=state.get("observed_price"),exit_price_semantics=state["semantics"])
        shadow.eligible_for_training = False
    return state

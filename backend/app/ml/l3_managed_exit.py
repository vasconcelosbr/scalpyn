"""Versioned, prospective L3 managed-exit target. Never repairs legacy rows."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

VERSION = "l3_managed_net_v1"
KEY = "ml_l3_managed_exit"
OUTCOMES = ("TP_HIT", "SL_HIT", "TIMEOUT", "TRAILING_STOP", "FLOW_STRUCTURE_EXIT")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def definition(config):
    raw = config.get(KEY)
    if raw is None:
        return None
    required = {"version", "policy_hash", "trailing_hash", "fee_roundtrip_pct", "slippage_roundtrip_pct", "max_holding_seconds", "barrier_contract_version"}
    if not isinstance(raw, dict) or set(raw) != required or raw.get("version") != VERSION:
        raise ValueError("invalid_l3_managed_exit_definition")
    for name in ("fee_roundtrip_pct", "slippage_roundtrip_pct", "max_holding_seconds"):
        value = raw[name]
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid_l3_managed_exit_" + name)
    if raw["max_holding_seconds"] <= 0 or int(raw["max_holding_seconds"]) != raw["max_holding_seconds"]:
        raise ValueError("invalid_l3_managed_exit_horizon")
    for name in ("policy_hash", "trailing_hash"):
        if not isinstance(raw[name], str) or len(raw[name]) != 64:
            raise ValueError("invalid_l3_managed_exit_" + name)
    if not raw["barrier_contract_version"]:
        raise ValueError("missing_l3_managed_exit_barrier")
    return {**deepcopy(raw), "hash": digest(raw)}


def lane_config(config):
    result = deepcopy(config)
    if definition(config):
        result.update(ml_label_version=VERSION, ml_label_objective="positive_net_return")
    return result


def freeze(snapshot, config, *, source, capture_valid):
    spec = definition(config)
    if source != "L3" or spec is None:
        return None
    from app.schemas.shadow_l3_exit_policy import validate_policy
    frozen = snapshot.get("shadow_l3_exit_policy") or {}
    policy = validate_policy(frozen.get("config") or {})
    compatible = (policy.mode == "APPLY" and policy.digest() == frozen.get("hash") == spec["policy_hash"]
                  and digest(snapshot.get("trailing") or {}) == spec["trailing_hash"]
                  and snapshot.get("barrier_contract_version") == spec["barrier_contract_version"]
                  and snapshot.get("ml_fee_roundtrip_pct") == spec["fee_roundtrip_pct"])
    return {"contract": spec, "capture_valid": bool(capture_valid and compatible),
            "capture_reason": None if compatible else "MANAGED_EXIT_CONFIG_MISMATCH"}


def at(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.tzinfo is None:
        raise ValueError("managed_exit_timestamp_required")
    return result


def certify(shadow, decisions, boundary_candle, *, checked_at):
    """Replay complete persisted evidence; a gap or ambiguous entry fails closed.

    Warmup is explicitly price-only; thereafter all flow evidence must be VALID.
    Holding beyond the frozen horizon is censored, never forced to close.
    """
    from app.schemas.shadow_l3_exit_policy import validate_policy
    from app.services.shadow_l3_exit_evaluator import advance
    snap = shadow.config_snapshot or {}
    capture = snap.get("l3_managed_ml") or {}
    proof = {"version": VERSION, "valid": False, "checked_at": checked_at.isoformat()}
    def reject(reason):
        return {**proof, "reason": reason}
    try:
        spec = capture.get("contract") or {}
        raw = {k: v for k, v in spec.items() if k != "hash"}
        if definition({KEY: raw}) != spec or shadow.label_contract_version != VERSION:
            return reject("LABEL_CONTRACT_MISMATCH")
        if not capture.get("capture_valid") or shadow.lineage_status != "EXACT":
            return reject("INVALID_CAPTURE")
        if freeze(snap, {KEY: raw}, source=shadow.source, capture_valid=True)["capture_valid"] is not True:
            return reject("FROZEN_POLICY_MISMATCH")
        entry_at, exit_at = at(shadow.entry_timestamp), at(shadow.exit_timestamp)
        if not 0 < (exit_at-entry_at).total_seconds() <= spec["max_holding_seconds"]:
            return reject("CENSORED_OR_INVALID_HORIZON")
        if shadow.outcome not in OUTCOMES or shadow.closure_path != "l3_continuation":
            return reject("UNSUPPORTED_CLOSURE")
        policy = validate_policy(snap["shadow_l3_exit_policy"]["config"])
        first = entry_at.replace(second=0, microsecond=0)
        if first != entry_at:
            if not boundary_candle or at(boundary_candle["time"]) != first:
                return reject("ENTRY_BOUNDARY_MISSING")
            if float(boundary_candle["high"]) >= float(shadow.tp_price) or float(boundary_candle["low"]) <= float(shadow.sl_price):
                return reject("ENTRY_BOUNDARY_AMBIGUOUS")
            first += timedelta(minutes=1)
        state, previous, hashes = {}, None, []
        for row in decisions:
            candle_at = at(row["candle_at"])
            if candle_at < first:
                continue
            if candle_at != (previous + timedelta(minutes=1) if previous else first):
                return reject("PRICE_HISTORY_GAP")
            if state.get("outcome"):
                return reject("DECISION_AFTER_TERMINAL")
            evidence = row["evidence"]
            candle = evidence.get("candle") or {}
            available = at(row["available_at"])
            if (row["policy_hash"] != spec["policy_hash"] or at(candle["time"]) != candle_at
                    or available != at(evidence["decision_at"]) or available > checked_at
                    or available < candle_at + timedelta(minutes=1)
                    or at(candle["ingested_at"]) > available):
                return reject("INVALID_DECISION_AVAILABILITY")
            if (candle_at-entry_at).total_seconds() >= policy.warmup_seconds and evidence.get("quality") != "VALID":
                return reject("INCOMPLETE_FLOW_EVIDENCE")
            state = advance(state, candle, evidence, policy, entry=float(shadow.entry_price),
                            tp=float(shadow.tp_price), sl=float(shadow.sl_price), trailing=snap.get("trailing") or {},
                            entry_at=entry_at, timeout_candles=shadow.timeout_candles)
            # Service-only cursor/entry-boundary annotations do not authorize exits.
            stored = {k:v for k,v in row["state"].items() if k not in ("entry_boundary_ambiguous_at", "ml_label")}
            if state != stored:
                return reject("REPLAY_STATE_MISMATCH")
            hashes.append(digest(row))
            previous = candle_at
        if (not state.get("outcome") or state["outcome"] != shadow.outcome
                or at(state["exit_at"]) != exit_at or state["semantics"] != shadow.exit_price_semantics
                or not math.isclose(float(state["exit_price"]), float(shadow.exit_price), rel_tol=1e-10)):
            return reject("EXIT_PROOF_MISMATCH")
        gross = (float(shadow.exit_price)/float(shadow.entry_price)-1)*100
        net = gross-spec["fee_roundtrip_pct"]-spec["slippage_roundtrip_pct"]
        if not math.isfinite(net) or not math.isclose(gross,float(shadow.pnl_pct),abs_tol=1e-8):
            return reject("RETURN_MISMATCH")
        return {**proof, "valid": True, "reason": None, "contract_hash": spec["hash"],
                "evidence_hash": digest(hashes), "decision_count": len(hashes),
                "gross_return_pct": gross, "net_return_pct": net, "label": int(net > 0),
                "fee_roundtrip_pct": spec["fee_roundtrip_pct"], "slippage_roundtrip_pct": spec["slippage_roundtrip_pct"],
                "label_available_at": checked_at.isoformat(), "exit_semantics": state["semantics"]}
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
        return reject("MANAGED_EXIT_EVIDENCE_INVALID")


async def finalize(db, shadow, state):
    from sqlalchemy import text
    if not (shadow.config_snapshot or {}).get("l3_managed_ml"):
        return state  # Legacy rows remain excluded, never re-labelled.
    decisions = (await db.execute(text("""SELECT candle_at,available_at,policy_hash,evidence,state
        FROM shadow_l3_exit_decisions WHERE shadow_id=:id ORDER BY candle_at"""), {"id":shadow.id})).mappings().all()
    boundary = (await db.execute(text("""SELECT time,high,low FROM ohlcv WHERE symbol=:symbol
        AND exchange=:exchange AND market_type='spot' AND timeframe='1m' AND is_closed IS TRUE
        AND time=date_trunc('minute',CAST(:entry AS timestamptz))"""),
        {"symbol":shadow.symbol,"exchange":"gate.io" if shadow.exchange in ("gate", "gateio", "gate.io") else shadow.exchange,
         "entry":shadow.entry_timestamp})).mappings().first()
    proof = certify(shadow, decisions, boundary, checked_at=datetime.now(timezone.utc))
    shadow.eligible_for_training = proof["valid"]
    if proof["valid"]:
        shadow.net_return_pct = proof["net_return_pct"]
        shadow.label_resolved_at = at(proof["label_available_at"])
    return {**state, "ml_label": proof}


async def inference_compatible(db, model_id, artifact_contract):
    """Check the selected model's tenant and the live economic identity each call."""
    from sqlalchemy import text
    from app.schemas.shadow_l3_exit_policy import frozen_policy
    from app.schemas.spot_engine_config import SpotEngineConfig
    from app.services.shadow_trade_service import _trailing_policy_from_spot_config
    rows = (await db.execute(text("""SELECT cp.config_type,cp.config_json FROM config_profiles cp
        JOIN ml_models m ON m.user_id=cp.user_id WHERE m.id=CAST(:id AS uuid)
        AND cp.pool_id IS NULL AND cp.is_active
        AND cp.config_type IN ('ml','shadow_l3_exit_policy','spot_engine')"""), {"id":model_id})).mappings().all()
    configs = {r['config_type']:r['config_json'] for r in rows}
    if len(configs) != len(rows) or 'ml' not in configs:
        return False
    spec = definition(configs['ml'])
    trained = ((artifact_contract or {}).get('label') or {}).get('managed_exit')
    if spec is None:
        return trained is None
    if trained != spec:
        return False
    policy = frozen_policy(configs.get('shadow_l3_exit_policy') or {})
    trailing = _trailing_policy_from_spot_config(SpotEngineConfig.model_validate(configs.get('spot_engine') or {}))
    return (policy['config']['mode'] == 'APPLY' and policy['hash'] == spec['policy_hash']
            and digest(trailing) == spec['trailing_hash'])

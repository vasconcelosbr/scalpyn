"""Versioned, prospective L3 managed-exit target. Never repairs legacy rows."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

VERSION = "l3_managed_net_v1"
KEY = "ml_l3_managed_exit"
OUTCOMES = ("TP_HIT", "SL_HIT", "TIMEOUT", "TRAILING_STOP", "FLOW_STRUCTURE_EXIT")

# Label-certification rules are versioned separately from the economic
# contract above: the same frozen exit policy/costs can be certified under
# different evidence rules. ``None`` keeps the v1 rules byte-for-byte (the
# production finalize path); any other rule set must be passed explicitly.
CERTIFICATION_KEY = "ml_l3_label_certification"
CERTIFICATION_V1 = "l3_managed_cert_v1"
CERTIFICATION_V2 = "l3_managed_cert_v2"
FLOW_EVIDENCE_RULES = ("REQUIRE_VALID", "REPLAY_FALLBACK")
TIMEOUT_HORIZON_RULES = ("EXIT_AT", "POLICY_TIMEOUT")


def certification_rules(raw):
    """Validate an explicit certification rule set (fail-closed, no defaults).

    flow_evidence:
      REQUIRE_VALID   -- v1: any post-warmup decision whose flow evidence is
                         not VALID rejects the whole label.
      REPLAY_FALLBACK -- v2: such decisions are still replayed; the frozen
                         policy already treats them as "no continuation"
                         (strong=weak=False), so the exit path stays
                         deterministic and REPLAY_STATE_MISMATCH still proves it.
    timeout_horizon:
      EXIT_AT         -- v1: exit_at - entry_at must be <= max_holding_seconds.
      POLICY_TIMEOUT  -- v2: a TIMEOUT emitted by the frozen policy is accepted
                         when timeout_candles*60 <= max_holding_seconds, even
                         though its DECISION_CLOSE exit is stamped after the
                         triggering candle closes. Every other outcome keeps
                         the EXIT_AT rule.
    """
    if raw is None:
        return None
    required = {"version", "flow_evidence", "timeout_horizon"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise ValueError("invalid_l3_label_certification")
    if raw["version"] not in (CERTIFICATION_V1, CERTIFICATION_V2):
        raise ValueError("invalid_l3_label_certification_version")
    if raw["flow_evidence"] not in FLOW_EVIDENCE_RULES:
        raise ValueError("invalid_l3_label_certification_flow_evidence")
    if raw["timeout_horizon"] not in TIMEOUT_HORIZON_RULES:
        raise ValueError("invalid_l3_label_certification_timeout_horizon")
    if raw["version"] == CERTIFICATION_V1 and (raw["flow_evidence"], raw["timeout_horizon"]) != ("REQUIRE_VALID", "EXIT_AT"):
        raise ValueError("invalid_l3_label_certification_v1_rules")
    return dict(raw)


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
    # int vs float carries no economic meaning here, but a raw JSON round-trip
    # (e.g. an unrelated settings-form save that clones the whole config
    # through JS, which has no int/float distinction) silently turns 0.0 into
    # 0. digest() hashes the literal JSON token, so that alone changes the
    # contract hash and orphans every previously captured row from the
    # training population. Normalize before hashing so contract identity
    # tracks economics, not incidental serialization.
    raw = {**raw, "fee_roundtrip_pct": float(raw["fee_roundtrip_pct"]),
           "slippage_roundtrip_pct": float(raw["slippage_roundtrip_pct"]),
           "max_holding_seconds": int(raw["max_holding_seconds"])}
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


def certify(shadow, decisions, boundary_candle, *, checked_at, certification=None):
    """Replay complete persisted evidence; a gap or ambiguous entry fails closed.

    Warmup is explicitly price-only. Under the v1 rules (``certification=None``)
    all later flow evidence must be VALID and holding beyond the frozen horizon
    is censored. ``certification`` (see certification_rules) relaxes exactly
    those two checks; every other replay/availability proof is unchanged.
    """
    from app.schemas.shadow_l3_exit_policy import validate_policy
    from app.services.shadow_l3_exit_evaluator import advance
    rules = certification_rules(certification)
    flow_rule = rules["flow_evidence"] if rules else "REQUIRE_VALID"
    timeout_rule = rules["timeout_horizon"] if rules else "EXIT_AT"
    snap = shadow.config_snapshot or {}
    capture = snap.get("l3_managed_ml") or {}
    proof = {"version": VERSION, "valid": False, "checked_at": checked_at.isoformat()}
    if rules:
        proof["certification_version"] = rules["version"]
    def reject(reason):
        return {**proof, "reason": reason}
    degraded_flow = 0
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
        held = (exit_at-entry_at).total_seconds()
        policy_timeout = (timeout_rule == "POLICY_TIMEOUT" and shadow.outcome == "TIMEOUT"
                          and shadow.timeout_candles is not None
                          and 0 < int(shadow.timeout_candles) * 60 <= spec["max_holding_seconds"])
        # A policy TIMEOUT is still proven by the replay below: the replayed
        # state must reach TIMEOUT at this exact exit_at from the frozen
        # timeout_candles, so this only removes the exit-stamp lag artefact.
        if not 0 < held or (held > spec["max_holding_seconds"] and not policy_timeout):
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
                if flow_rule == "REQUIRE_VALID":
                    return reject("INCOMPLETE_FLOW_EVIDENCE")
                degraded_flow += 1
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
        result = {**proof, "valid": True, "reason": None, "contract_hash": spec["hash"],
                  "evidence_hash": digest(hashes), "decision_count": len(hashes),
                  "gross_return_pct": gross, "net_return_pct": net, "label": int(net > 0),
                  "fee_roundtrip_pct": spec["fee_roundtrip_pct"], "slippage_roundtrip_pct": spec["slippage_roundtrip_pct"],
                  "label_available_at": checked_at.isoformat(), "exit_semantics": state["semantics"]}
        if rules:
            result.update(degraded_flow_decisions=degraded_flow, policy_timeout_accepted=bool(
                policy_timeout and held > spec["max_holding_seconds"]))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError):
        return reject("MANAGED_EXIT_EVIDENCE_INVALID")


async def load_certification_evidence(db, shadow, *, compact=False):
    """Persisted replay evidence for one shadow (read-only).

    ``compact`` drops the raw ``flow_buckets`` array, which neither the
    certifier nor the evaluator reads; it only changes ``evidence_hash``.
    """
    from sqlalchemy import text
    evidence_column = "evidence - 'flow_buckets' AS evidence" if compact else "evidence"
    decisions = (await db.execute(text(f"""SELECT candle_at,available_at,policy_hash,{evidence_column},state
        FROM shadow_l3_exit_decisions WHERE shadow_id=:id ORDER BY candle_at"""), {"id":shadow.id})).mappings().all()
    boundary = (await db.execute(text("""SELECT time,high,low FROM ohlcv WHERE symbol=:symbol
        AND exchange=:exchange AND market_type='spot' AND timeframe='1m' AND is_closed IS TRUE
        AND time=date_trunc('minute',CAST(:entry AS timestamptz))"""),
        {"symbol":shadow.symbol,"exchange":"gate.io" if shadow.exchange in ("gate", "gateio", "gate.io") else shadow.exchange,
         "entry":shadow.entry_timestamp})).mappings().first()
    return decisions, boundary


def configured_certification(config):
    """Active certification rules from config (None keeps the v1 rules)."""
    return certification_rules((config or {}).get(CERTIFICATION_KEY))


async def active_certification(db, user_id):
    """The tenant's configured rules, read at certification time (fail-closed)."""
    from sqlalchemy import text
    raw = (await db.execute(text("""SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND config_type='ml' AND pool_id IS NULL AND is_active
        ORDER BY updated_at DESC NULLS LAST LIMIT 1"""), {"uid": user_id})).scalar_one_or_none()
    if isinstance(raw, str):
        raw = json.loads(raw)
    return configured_certification(raw)


def apply_proof(shadow, proof):
    """The only shadow fields a managed proof owns."""
    shadow.eligible_for_training = proof["valid"]
    shadow.net_return_pct = proof["net_return_pct"] if proof["valid"] else None
    shadow.label_resolved_at = at(proof["label_available_at"]) if proof["valid"] else None


async def finalize(db, shadow, state):
    if not (shadow.config_snapshot or {}).get("l3_managed_ml"):
        return state  # Legacy rows remain excluded, never re-labelled.
    decisions, boundary = await load_certification_evidence(db, shadow)
    rules = await active_certification(db, shadow.user_id)
    proof = certify(shadow, decisions, boundary, checked_at=datetime.now(timezone.utc), certification=rules)
    apply_proof(shadow, proof)
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

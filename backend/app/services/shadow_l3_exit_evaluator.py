"""Pure, incremental and replayable post-TP evaluator. No IO or wall clock."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import floor

from ..schemas.shadow_l3_exit_policy import ShadowL3ExitPolicy


def _at(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def advance(state: dict, candle: dict, evidence: dict, policy: ShadowL3ExitPolicy,
            *, entry: float, tp: float, sl: float, trailing: dict,
            entry_at: datetime, timeout_candles: int | None = None) -> dict:
    """One CLOSED 1m candle. Floor active at open wins over all signals.

    Evidence contains only information available at decision_at. Missing or
    partial data never authorizes extension. A terminal state is immutable.
    """
    s = deepcopy(state)
    if s.get("outcome"):
        return s
    at = _at(candle["time"])
    if s.get("last_candle_at") and at <= _at(s["last_candle_at"]):
        return s
    if at < entry_at:
        # Partial entry candle cannot prove post-entry extrema or ordering.
        s["entry_boundary_ambiguous_at"] = at.isoformat()
        return s
    op, hi, lo, close = (float(candle[k]) for k in ("open", "high", "low", "close"))
    hwm = max(entry, float(s.get("high_water_mark", entry)))
    old_floor = s.get("floor_price")
    floor_schedule = s.get("floor_schedule")
    if floor_schedule is not None:
        old_floor = max([sl] + [f["price"] for f in floor_schedule if _at(f["available_at"]) <= at])
    protected = entry * (1 + max(float(trailing.get("min_profit_pct") or 0),
                                float(trailing.get("safety_margin_above_entry_pct") or 0)) / 100)
    pre_floor = None
    if trailing.get("enabled") and trailing.get("contract_version") == "shadow_hwm_trailing_v1":
        activation = trailing.get("activation_profit_pct")
        distance = trailing.get("hwm_trail_pct")
        if activation is not None and distance is not None and hwm >= entry * (1 + activation / 100):
            candidate = hwm * (1 - distance / 100)
            if not trailing.get("never_sell_at_loss") or candidate >= protected:
                pre_floor = candidate
    elif trailing.get("enabled") and trailing.get("contract_version") == "shadow_trailing_policy_v2":
        from .shadow_barrier_evaluator import _resolve_trailing_floor
        pre_floor = _resolve_trailing_floor(hwm, entry, trailing["policy"])
        if pre_floor is not None and trailing.get("never_sell_at_loss") and pre_floor < protected:
            pre_floor = None
    effective = max(sl, old_floor or sl, (pre_floor or sl) if not s.get("continuation") else sl)
    s.update(last_candle_at=at.isoformat(), last_evaluated_at=evidence.get("decision_at"),
             quality=evidence.get("quality", "UNAVAILABLE"), reason="WAITING_TP")

    def finish(reason, price, trigger, semantics):
        s.update(outcome=reason, exit_price=price, trigger_price=trigger,
                 observed_price=(op if semantics == "NEXT_OPEN_AFTER_SIGNAL" else lo if semantics == "PRIOR_FLOOR_TOUCH" else hi if semantics == "TP_FIRST_TOUCH_NOMINAL" else close),
                 exit_at=(evidence["decision_at"] if semantics in ("DECISION_CLOSE", "NEW_FLOOR_DECISION_CLOSE") else at.isoformat()),
                 reason=reason, state="CLOSED", semantics=semantics)
        return s

    if lo <= effective:
        return finish("TRAILING_STOP" if effective > sl else "SL_HIT",
                      min(op, effective), effective, "PRIOR_FLOOR_TOUCH")
    pending = s.get("pending_exit")
    if pending and at >= _at(pending["available_at"]):
        return finish(pending["outcome"],op,pending.get("trigger_price"),"NEXT_OPEN_AFTER_SIGNAL")

    valid = evidence.get("quality") == "VALID" and not policy.missing_parameters()
    if valid:
        strong = (evidence["taker_ratio"] >= policy.continuation_taker_min
                  and evidence["delta_normalized"] >= policy.continuation_delta_min
                  and evidence["cvd_slope_normalized"] >= policy.continuation_cvd_min
                  and evidence["price_return_pct"] >= policy.continuation_price_min_pct)
        weak = (evidence["taker_ratio"] <= policy.weakening_taker_max
                and evidence["delta_normalized"] <= policy.weakening_delta_max
                and evidence["cvd_slope_normalized"] <= policy.weakening_cvd_max)
    else:
        strong = weak = False
    # A missing candle resets persistence instead of bridging an unseen interval.
    prev = _at(state["last_candle_at"]) if state.get("last_candle_at") else None
    consecutive = prev is not None and (at - prev).total_seconds() == 60
    for key, yes in (("strong_seconds", strong), ("weak_seconds", weak)):
        s[key] = (int(s.get(key, 0)) if consecutive else 0) + 60 if yes else 0
    strong_confirmed = strong and s["strong_seconds"] >= policy.confirmation_seconds
    weak_confirmed = weak and s["weak_seconds"] >= policy.confirmation_seconds
    s["cvd"] = evidence.get("cvd")
    authorizations = s.get("authorizations", [])
    known = [a for a in authorizations if _at(a["available_at"]) <= at]
    latest = max(known, key=lambda a:_at(a["available_at"])) if known else None
    authorized_at_open = bool(latest and latest["strong"] and
                              (at-_at(latest["available_at"])).total_seconds() <= (policy.max_age_seconds or 0))
    # Keep the most recent known signal plus not-yet-available decisions.
    authorizations = ([latest] if latest else []) + [a for a in authorizations if _at(a["available_at"]) > at]
    authorizations.append({"available_at":evidence["decision_at"],"strong":bool(strong_confirmed)})
    s["authorizations"] = authorizations
    if not s.get("continuation"):
        if hi < tp:
            if timeout_candles and (at-entry_at).total_seconds() >= timeout_candles*60:
                return finish("TIMEOUT",close,None,"DECISION_CLOSE")
            s.update(high_water_mark=max(hwm, hi), floor_price=pre_floor, state="PRE_TP")
            return s
        if not authorized_at_open:
            return finish("TP_HIT", tp, tp, "TP_FIRST_TOUCH_NOMINAL")
        initial = max(effective, protected, tp-entry*policy.initial_buffer_pct/100)
        s.update(continuation=True, activated_at=at.isoformat(), state="CONTINUATION", floor_price=initial)
        floor_schedule = [{"price":effective,"available_at":at.isoformat()},
                          {"price":initial,"available_at":evidence["decision_at"]}]
        s["floor_schedule"] = floor_schedule
        if close <= initial:
            s["pending_exit"] = {"outcome":"TRAILING_STOP","available_at":evidence["decision_at"],"trigger_price":initial}

    if weak_confirmed and evidence.get("structure_lost"):
        s.update(state="EXIT_PENDING", reason="FLOW_STRUCTURE_EXIT",
                 pending_exit={"outcome":"FLOW_STRUCTURE_EXIT","available_at":evidence["decision_at"],
                               "trigger_price":evidence.get("support_price")})
        return s

    hwm = max(hwm, hi)
    s.update(high_water_mark=hwm, reason="DATA_DEGRADED" if not valid else "CONTINUATION")
    if not valid:
        return s  # Previous protection remains active; no synthetic signal.
    tp_pct = (tp / entry - 1) * 100
    initial_pct = tp_pct - policy.initial_buffer_pct
    peak_pct = (hwm / entry - 1) * 100
    steps = max(0, floor((peak_pct - tp_pct) / policy.step_trigger_pct))
    step_price = entry * (1 + (initial_pct + steps * policy.step_floor_pct) / 100)
    mult = policy.tight_atr_multiplier if weak_confirmed else policy.atr_multiplier
    candidate = max(effective, s.get("floor_price") or effective, protected, step_price, hwm - evidence["atr"] * mult)
    s.update(floor_price=candidate, state="TIGHTENED" if weak_confirmed else "CONTINUATION")
    schedule = [f for f in (floor_schedule or []) if _at(f["available_at"]) > at]
    schedule.append({"price":effective,"available_at":at.isoformat()})
    schedule.append({"price":candidate,"available_at":evidence["decision_at"]})
    s["floor_schedule"] = schedule
    if close <= candidate:
        # Signal exits fill at the first subsequent observable open, never
        # at the earlier candle close or at a newly raised historical floor.
        s.update(state="EXIT_PENDING", reason="NEW_FLOOR_BELOW_MARKET",
                 pending_exit={"outcome":"TRAILING_STOP","available_at":evidence["decision_at"],
                               "trigger_price":candidate})
    return s

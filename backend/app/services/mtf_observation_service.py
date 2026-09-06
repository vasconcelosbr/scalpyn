"""Fail-closed Spot MTF observation; never participates in order authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from typing import Any, Mapping

from sqlalchemy import text

from ..schemas.layer_context import (
    CandleIdentity,
    L1DecisionContextV3,
    L2DecisionContextV3,
    LayerVerdictRecord,
    MultilayerDecisionContextV3,
    MultilayerDecisionContextV4,
    ProfileIdentity,
)
from .indicators_provider import get_timeframe_indicators
from .multilayer_contract import require_shadow_multilayer_config
from .profile_engine import ProfileEngine
from .profile_runtime_config import canonical_hash, canonical_profile_config_hash

_TF_SECONDS = {"1h": 3600, "15m": 900, "5m": 300}
_L2_STATES = {
    "NONE", "PULLBACK_SEEN", "BREAKOUT_SEEN", "PULLBACK_RECLAIM",
    "BREAKOUT_RETEST", "INVALIDATED",
}


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _seal(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(payload))
    result.pop("context_hash", None)
    result["context_hash"] = canonical_hash(result)
    return result


def _finite(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def verify_context_hash(payload: Mapping[str, Any]) -> None:
    expected = payload.get("context_hash")
    material = dict(payload)
    material.pop("context_hash", None)
    if not expected or expected != canonical_hash(material):
        raise ValueError("MULTILAYER_CONTEXT_HASH_INVALID")


def _required_indicator_names(profile: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    sections = (
        ("filters", "conditions", "field"),
        ("signals", "conditions", "field"),
        ("entry_triggers", "conditions", "indicator"),
        ("block_rules", "blocks", "indicator"),
    )
    for section, list_key, name_key in sections:
        for condition in ((profile.get(section) or {}).get(list_key) or []):
            name = condition.get(name_key) or condition.get("field")
            if name:
                names.add(str(name))
    for rule in ((profile.get("scoring") or {}).get("rules") or []):
        name = rule.get("indicator") or rule.get("field")
        if name:
            names.add(str(name))
    return names


def _validate_indicator_identity(
    merged,
    *,
    required: set[str],
    timeframe: str,
    layer_config: Mapping[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], CandleIdentity, datetime]:
    policies = layer_config.get("source_policies") or {}
    policy = policies.get("ohlcv") or {}
    allowed = {str(item) for item in policy.get("allowed_source_providers") or []}
    allowed_capture = {
        str(item) for item in policy.get("allowed_capture_contract_versions") or []
    }
    policy_id = str(policy.get("provider_policy_id") or "")
    expected_group = str(policy.get("scheduler_group") or "")
    expected_config_profile_id = str(policy.get("indicator_config_profile_id") or "")
    expected_config_hash = str(policy.get("indicator_config_hash") or "")
    allowed_producer_versions = {
        str(item) for item in policy.get("allowed_producer_versions") or []
    }
    candidates_by_name = {
        str(candidate.get("indicator")): candidate
        for candidate in merged.candidates
        if candidate.get("timeframe") == timeframe
        and candidate.get("market_type") == "spot"
        and candidate.get("group") == expected_group
    }
    missing = sorted(name for name in required if name not in candidates_by_name)
    if missing:
        raise ValueError("INDICATOR_INPUTS_UNAVAILABLE:" + ",".join(missing))
    selected = [candidates_by_name[name] for name in sorted(required)]
    if not selected:
        raise ValueError("INDICATOR_INPUTS_UNAVAILABLE")
    if any(candidate.get("candle_closed") is not True for candidate in selected):
        raise ValueError("OPEN_CANDLE_REJECTED")
    if any(candidate.get("fallback_used") for candidate in selected):
        raise ValueError("FALLBACK_SOURCE_REJECTED")
    for candidate in selected:
        envelope = dict(candidate.get("envelope") or {})
        expected_hash = envelope.pop("envelope_hash", None)
        if not expected_hash or expected_hash != canonical_hash(envelope):
            raise ValueError("INDICATOR_ENVELOPE_HASH_INVALID")
    if any(str(candidate.get("source_provider")) not in allowed for candidate in selected):
        raise ValueError("SOURCE_PROVIDER_REJECTED")
    if any(str(candidate.get("provider_policy_id")) != policy_id for candidate in selected):
        raise ValueError("PROVIDER_POLICY_REJECTED")
    if any(str(candidate.get("config_hash") or "") != expected_config_hash for candidate in selected):
        raise ValueError("INDICATOR_CONFIG_HASH_REJECTED")
    if any(
        str((candidate.get("envelope") or {}).get("config_profile_id") or "")
        != expected_config_profile_id
        for candidate in selected
    ):
        raise ValueError("INDICATOR_CONFIG_PROFILE_REJECTED")
    if any(
        str(candidate.get("producer_version") or "") not in allowed_producer_versions
        for candidate in selected
    ):
        raise ValueError("INDICATOR_PRODUCER_VERSION_REJECTED")
    if not allowed_capture or any(
        str((candidate.get("envelope") or {}).get("capture_contract_version"))
        not in allowed_capture
        for candidate in selected
    ):
        raise ValueError("CAPTURE_CONTRACT_REJECTED")
    config_hashes = {candidate.get("config_hash") for candidate in selected}
    if None in config_hashes or len(config_hashes) != 1:
        raise ValueError("INDICATOR_CONFIG_IDENTITY_CONFLICT")
    source_times = {_utc(candidate.get("source_timestamp")) for candidate in selected}
    if len(source_times) != 1:
        raise ValueError("INDICATOR_CANDLE_IDENTITY_CONFLICT")
    source_timestamp = next(iter(source_times))
    available_times = {_utc(candidate.get("available_at")) for candidate in selected}
    if len(available_times) != 1:
        raise ValueError("INDICATOR_AVAILABILITY_IDENTITY_CONFLICT")
    if next(iter(available_times)) > now:
        raise ValueError("INDICATOR_NOT_YET_AVAILABLE")
    if source_timestamp + timedelta(seconds=_TF_SECONDS[timeframe]) > now:
        raise ValueError("OPEN_CANDLE_REJECTED")
    margin = layer_config.get("validity_margin_seconds")
    if margin is None:
        raise ValueError("VALIDITY_MARGIN_CONFIG_REQUIRED")
    expires_at = source_timestamp + timedelta(
        seconds=_TF_SECONDS[timeframe] + int(margin)
    )
    if now > expires_at:
        raise ValueError("CONTEXT_EXPIRED")
    values = {name: merged.values.get(name) for name in required}
    if any(value is None for value in values.values()):
        raise ValueError("INDICATOR_VALUE_UNAVAILABLE")
    invalid_values = sorted(name for name, value in values.items() if not _finite(value))
    if invalid_values:
        raise ValueError("INDICATOR_VALUE_NONFINITE:" + ",".join(invalid_values))
    candle = CandleIdentity(
        symbol=str(selected[0].get("symbol") or "UNKNOWN"),
        market_type="spot",
        timeframe=timeframe,
        source_timestamp=source_timestamp,
        closed=True,
        source_provider=str(selected[0].get("source_provider")),
        provider_policy_id=policy_id,
    )
    return values, candle, expires_at


def _profile_verdict(profile: Mapping[str, Any], *, symbol: str, timeframe: str, values: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    asset = {
        "symbol": symbol,
        "indicators": {},
        "_indicators_by_tf": {timeframe: values},
    }
    result = ProfileEngine(
        dict(profile), strict_timeframe_mode=True
    ).evaluate_asset(asset)
    if result.get("blocked") or result.get("passed_filter") is False:
        return "REJECT", result
    return "PASS", result


def _direction(values: Mapping[str, Any]) -> str:
    votes: list[int] = []
    if values.get("di_plus") is not None and values.get("di_minus") is not None:
        delta = float(values["di_plus"]) - float(values["di_minus"])
        votes.append(1 if delta > 0 else -1 if delta < 0 else 0)
    if values.get("ema21") is not None and values.get("ema50") is not None:
        delta = float(values["ema21"]) - float(values["ema50"])
        votes.append(1 if delta > 0 else -1 if delta < 0 else 0)
    if values.get("ema21_slope_pct") is not None and values.get("ema50_slope_pct") is not None:
        slow_slopes = (float(values["ema21_slope_pct"]), float(values["ema50_slope_pct"]))
        votes.append(
            1 if all(value > 0 for value in slow_slopes)
            else -1 if all(value < 0 for value in slow_slopes)
            else 0
        )
    if values.get("higher_highs_5") is True and values.get("higher_lows_5") is True:
        votes.append(1)
    elif values.get("higher_highs_5") is False and values.get("higher_lows_5") is False:
        votes.append(-1)
    return "UP" if sum(votes) > 0 else "DOWN" if sum(votes) < 0 else "NEUTRAL"


def build_l1_context(
    *, symbol: str, profile: Mapping[str, Any], profile_identity: ProfileIdentity,
    values: dict[str, Any], candle: CandleIdentity, expires_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    semantics = profile.get("mtf_semantics") or {}
    for key in ("adx_strong_min", "atr_pct_low", "atr_pct_high"):
        if semantics.get(key) is None:
            raise ValueError("CONFIG_REQUIRED:" + key)
    verdict, _ = _profile_verdict(profile, symbol=symbol, timeframe="1h", values=values)
    direction = _direction(values)
    adx = float(values["adx"])
    atr_pct = float(values["atr_pct"])
    structure = (
        "BULLISH" if values.get("higher_highs_5") and values.get("higher_lows_5")
        else "BEARISH" if values.get("higher_highs_5") is False and values.get("higher_lows_5") is False
        else "NEUTRAL"
    )
    payload = L1DecisionContextV3(
        direction=direction,
        strength=max(0.0, min(1.0, adx / 100.0)),
        regime="TREND" if adx >= float(semantics["adx_strong_min"]) else "RANGE",
        volatility=(
            "HIGH" if atr_pct >= float(semantics["atr_pct_high"])
            else "LOW" if atr_pct <= float(semantics["atr_pct_low"])
            else "NORMAL"
        ),
        structure=structure,
        validity="VALID",
        verdict=verdict,
        candle=candle,
        profile=profile_identity,
        computed_at=now,
        expires_at=expires_at,
        indicators_hash=canonical_hash(values),
        ema21_slope_pct=float(values["ema21_slope_pct"]),
        ema50_slope_pct=float(values["ema50_slope_pct"]),
    ).model_dump(mode="json")
    return _seal(payload)


def advance_l2_setup_state(
    *, values: Mapping[str, Any], candle_open_at: datetime,
    semantics: Mapping[str, Any], previous: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Advance the 15m setup automaton once per closed candle.

    A terminal setup is emitted only after its precursor was persisted on an
    earlier candle. Replaying the same material is idempotent; altered material
    for the same candle is rejected.
    """
    required = (
        "max_extension_atr", "pullback_max_distance_atr",
        "breakout_min_distance_atr", "retest_tolerance_atr",
        "invalidation_atr", "setup_valid_candles", "adx_impulse_min",
        "volume_relative_min", "bb_width_compression_max",
        "bb_width_expansion_min",
    )
    missing = [key for key in required if semantics.get(key) is None]
    if missing:
        raise ValueError("CONFIG_REQUIRED:" + ",".join(missing))
    if int(semantics["setup_valid_candles"]) <= 0:
        raise ValueError("CONFIG_INVALID:setup_valid_candles")

    candle_open_at = _utc(candle_open_at)
    indicators_hash = canonical_hash(dict(values))
    before = str((previous or {}).get("state") or "NONE")
    if before not in _L2_STATES:
        raise ValueError("L2_STATE_VERSION_UNKNOWN")
    if previous:
        prior_material = {
            "state": previous.get("state"),
            "state_before": (previous.get("state_payload") or {}).get("state_before"),
            "last_candle_open_at": _utc(previous.get("last_candle_open_at")).isoformat(),
            "last_indicators_hash": previous.get("last_indicators_hash"),
            "state_payload": dict(previous.get("state_payload") or {}),
        }
        if previous.get("state_hash") != canonical_hash(prior_material):
            raise ValueError("L2_STATE_HASH_INVALID")
    previous_open = (
        _utc(previous["last_candle_open_at"])
        if previous and previous.get("last_candle_open_at") else None
    )
    if previous_open and candle_open_at < previous_open:
        raise ValueError("L2_STATE_STALE_REPLAY")
    if previous_open and candle_open_at == previous_open:
        if previous.get("last_indicators_hash") != indicators_hash:
            raise ValueError("L2_STATE_REPLAY_CONFLICT")
        material = {
            "state": before,
            "state_before": str((previous.get("state_payload") or {}).get("state_before") or before),
            "last_candle_open_at": candle_open_at.isoformat(),
            "last_indicators_hash": indicators_hash,
            "state_payload": dict(previous.get("state_payload") or {}),
        }
        material["state_hash"] = canonical_hash(material)
        return material

    price = float(values["price"])
    atr = float(values["atr"])
    ema = float(values["ema21"])
    vwap = float(values["vwap"])
    upper = float(values["bb_upper"])
    lower = float(values["bb_lower"])
    if atr <= 0:
        raise ValueError("INDICATOR_VALUE_INVALID:atr")
    extension = abs(price - ema) / atr
    adx = float(values["adx"])
    volume_relative = float(values["volume_spike"])
    bb_width = float(values["bb_width"])
    impulse_ok = (
        adx >= float(semantics["adx_impulse_min"])
        and volume_relative >= float(semantics["volume_relative_min"])
    )
    expanding = bb_width >= float(semantics["bb_width_expansion_min"])
    direction = _direction(values)
    previous_payload = dict((previous or {}).get("state_payload") or {})
    remaining = int(previous_payload.get("remaining_candles") or 0)
    if previous_open:
        elapsed_seconds = (candle_open_at - previous_open).total_seconds()
        if elapsed_seconds <= 0 or elapsed_seconds % _TF_SECONDS["15m"] != 0:
            raise ValueError("L2_CANDLE_SEQUENCE_INVALID")
        remaining -= int(elapsed_seconds // _TF_SECONDS["15m"])

    invalidation = min(ema, vwap) - float(semantics["invalidation_atr"]) * atr
    state_after = "NONE"
    payload: dict[str, Any] = {
        "state_before": before,
        "remaining_candles": 0,
        "support": lower,
        "resistance": upper,
        "invalidation": invalidation,
        "extension_atr": extension,
    }
    if price < invalidation or extension > float(semantics["max_extension_atr"]):
        state_after = "INVALIDATED"
    elif before == "PULLBACK_SEEN" and remaining >= 0:
        anchor = float(previous_payload["anchor"])
        if (
            price >= anchor and bool(values.get("vwap_reclaim_bool"))
            and direction != "DOWN" and impulse_ok
        ):
            state_after = "PULLBACK_RECLAIM"
        else:
            state_after = "PULLBACK_SEEN" if remaining > 0 else "NONE"
            payload.update({"anchor": anchor, "remaining_candles": max(0, remaining)})
    elif before == "BREAKOUT_SEEN" and remaining >= 0:
        anchor = float(previous_payload["anchor"])
        tolerance = float(semantics["retest_tolerance_atr"]) * atr
        if anchor - tolerance <= price <= anchor + tolerance and direction != "DOWN":
            state_after = "BREAKOUT_RETEST"
        else:
            state_after = "BREAKOUT_SEEN" if remaining > 0 else "NONE"
            payload.update({"anchor": anchor, "remaining_candles": max(0, remaining)})
    elif (
        price <= ema
        and abs(price - ema) / atr <= float(semantics["pullback_max_distance_atr"])
        and direction != "DOWN"
    ):
        state_after = "PULLBACK_SEEN"
        payload.update({
            "anchor": ema,
            "remaining_candles": int(semantics["setup_valid_candles"]),
        })
    elif (
        price >= upper + float(semantics["breakout_min_distance_atr"]) * atr
        and direction == "UP" and impulse_ok and expanding
    ):
        state_after = "BREAKOUT_SEEN"
        payload.update({
            "anchor": upper,
            "remaining_candles": int(semantics["setup_valid_candles"]),
        })

    material = {
        "state": state_after,
        "state_before": before,
        "last_candle_open_at": candle_open_at.isoformat(),
        "last_indicators_hash": indicators_hash,
        "state_payload": payload,
    }
    material["state_hash"] = canonical_hash(material)
    return material


def build_l2_context(
    *, symbol: str, profile: Mapping[str, Any], profile_identity: ProfileIdentity,
    values: dict[str, Any], candle: CandleIdentity, expires_at: datetime,
    l1_context: Mapping[str, Any], now: datetime,
    state_transition: Mapping[str, Any],
) -> dict[str, Any]:
    verify_context_hash(l1_context)
    verdict, _ = _profile_verdict(profile, symbol=symbol, timeframe="15m", values=values)
    price = float(values["price"])
    atr = float(values["atr"])
    ema = float(values["ema21"])
    vwap = float(values["vwap"])
    extension = abs(price - ema) / atr if atr > 0 else None
    direction = _direction(values)
    setup = str(state_transition["state"])
    if setup in {"INVALIDATED", "NONE", "PULLBACK_SEEN", "BREAKOUT_SEEN"}:
        setup_for_contract = "INVALIDATED" if setup == "INVALIDATED" else "NONE"
    else:
        setup_for_contract = setup
    if setup == "INVALIDATED":
        verdict = "REJECT"
    elif setup not in {"PULLBACK_RECLAIM", "BREAKOUT_RETEST"}:
        verdict = "INSUFFICIENT_DATA"
    adx = float(values["adx"])
    volume_relative = float(values["volume_spike"])
    bb_width = float(values["bb_width"])
    semantics = profile.get("mtf_semantics") or {}
    payload = L2DecisionContextV3(
        local_direction=direction,
        setup_state=setup_for_contract,
        extension_atr=extension,
        support=float(state_transition["state_payload"]["support"]),
        resistance=float(state_transition["state_payload"]["resistance"]),
        invalidation=float(state_transition["state_payload"]["invalidation"]),
        validity="VALID",
        verdict=verdict,
        candle=candle,
        profile=profile_identity,
        l1_context_hash=str(l1_context["context_hash"]),
        computed_at=now,
        expires_at=expires_at,
        indicators_hash=canonical_hash(values),
        state_before=str(state_transition["state_before"]),
        state_after=setup,
        state_hash=str(state_transition["state_hash"]),
        adx=adx,
        volume_relative=volume_relative,
        bb_width=bb_width,
        volume_state=(
            "EXPANDED" if volume_relative >= float(semantics["volume_relative_min"])
            else "NORMAL"
        ),
        volatility_state=(
            "COMPRESSION" if bb_width <= float(semantics["bb_width_compression_max"])
            else "EXPANSION" if bb_width >= float(semantics["bb_width_expansion_min"])
            else "NORMAL"
        ),
    ).model_dump(mode="json")
    return _seal(payload)


def build_multilayer_context(
    *, l1: Mapping[str, Any], l2: Mapping[str, Any], l3_confirmation: Mapping[str, Any],
    canonical_score: float | None, calibration_run_id: str, now: datetime,
    statistical_gate: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    verify_context_hash(l1)
    verify_context_hash(l2)
    verify_context_hash(l3_confirmation)
    if l2.get("l1_context_hash") != l1.get("context_hash"):
        raise ValueError("L2_L1_CONTEXT_REPLAY_REJECTED")
    verdict_values = {
        "L1": str(l1["verdict"]),
        "L2": str(l2["verdict"]),
        "L3": str(l3_confirmation.get("verdict", "UNAVAILABLE")),
    }
    if "REJECT" in verdict_values.values():
        decision = "REJECT"
    elif any(value in {"UNAVAILABLE", "INSUFFICIENT_DATA"} for value in verdict_values.values()):
        decision = "WAIT"
    else:
        decision = "PASS"
    verdicts = {
        layer: LayerVerdictRecord(
            verdict=value,
            computed_at=now,
            contract_version=(
                str(l1["contract_version"]) if layer == "L1"
                else str(l2["contract_version"]) if layer == "L2"
                else str(l3_confirmation.get("contract_version") or "l3_confirmation_v2")
            ),
        )
        for layer, value in verdict_values.items()
    }
    payload_type = (
        MultilayerDecisionContextV4
        if statistical_gate else MultilayerDecisionContextV3
    )
    payload = payload_type(
        l1_snapshot=dict(l1),
        l1_context_hash=str(l1["context_hash"]),
        l2_snapshot=dict(l2),
        l2_context_hash=str(l2["context_hash"]),
        l3_confirmation=dict(l3_confirmation),
        canonical_score=canonical_score,
        verdicts=verdicts,
        observational_decision=decision,
        computed_at=now,
        calibration_run_id=calibration_run_id,
        **(
            {"statistical_gate": dict(statistical_gate)}
            if statistical_gate else {}
        ),
    ).model_dump(mode="json")
    return _seal(payload)


def build_l3_confirmation(
    *, legacy_decision: str, indicators_snapshot: Mapping[str, Any],
    gate_evaluation_hash: str | None, layer_config: Mapping[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Create a strict 5m confirmation from the actual persisted L3 inputs."""
    invalid: list[str] = []
    required_meta = {
        "timeframe", "source_group", "ts", "source_timestamp", "available_at",
        "source_provider", "provider_policy_id", "candle_closed", "config_hash",
        "producer_version", "envelope",
    }
    policies = (layer_config or {}).get("source_policies") or {}
    margin = (layer_config or {}).get("validity_margin_seconds")
    if not layer_config or margin is None:
        invalid.append("__layer_config__")
    required_by_group = (layer_config or {}).get("required_indicators_by_group") or {}
    required_inputs: list[tuple[str, str]] = []
    for group, names in required_by_group.items():
        for name in names or []:
            required_inputs.append((str(group), str(name)))
            item = indicators_snapshot.get(str(name))
            if not isinstance(item, Mapping) or item.get("source_group") != group:
                invalid.append(str(name))
    if not required_inputs:
        invalid.append("__required_indicators__")
    for expected_group, name in required_inputs:
        item = indicators_snapshot.get(name)
        if not isinstance(item, Mapping):
            invalid.append(name)
            continue
        if any(item.get(field) is None for field in required_meta):
            invalid.append(name)
            continue
        observed = set(item.get("observed_timeframes") or [])
        timeframe = item.get("timeframe")
        if item.get("source_group") == "microstructure" and name in {"taker_ratio", "taker_buy_volume", "taker_sell_volume", "volume_delta", "buy_pressure"}:
            source_kind = "live_trade_flow"
        elif item.get("source_group") == "microstructure" and name in {"spread_pct", "orderbook_depth_usdt", "bid_ask_imbalance", "orderbook_pressure"}:
            source_kind = "live_order_book"
        else:
            source_kind = "ohlcv"
        policy = policies.get(source_kind) or {}
        allowed = {str(value) for value in policy.get("allowed_source_providers") or []}
        policy_id = str(policy.get("provider_policy_id") or "")
        allowed_capture = {
            str(value) for value in policy.get("allowed_capture_contract_versions") or []
        }
        if item.get("timeframe_conflict") or item.get("stale"):
            invalid.append(name)
        elif observed != {"5m"}:
            invalid.append(name)
        elif timeframe != "5m":
            invalid.append(name)
        elif item.get("source_group") != expected_group:
            invalid.append(name)
        elif item.get("candle_closed") is not True:
            invalid.append(name)
        elif not allowed or str(item.get("source_provider")) not in allowed:
            invalid.append(name)
        elif not policy_id or str(item.get("provider_policy_id")) != policy_id:
            invalid.append(name)
        else:
            try:
                source_timestamp = _utc(item["source_timestamp"])
                available_at = _utc(item["available_at"])
                if available_at > now:
                    raise ValueError("future availability")
                if source_timestamp + timedelta(seconds=_TF_SECONDS["5m"]) > now:
                    raise ValueError("open candle")
                if now > source_timestamp + timedelta(
                    seconds=_TF_SECONDS["5m"] + int(margin)
                ):
                    raise ValueError("expired")
                envelope = dict(item["envelope"])
                expected_hash = envelope.pop("envelope_hash", None)
                if not expected_hash or expected_hash != canonical_hash(envelope):
                    raise ValueError("hash")
                if source_kind == "ohlcv" and (
                    not allowed_capture
                    or str(envelope.get("capture_contract_version")) not in allowed_capture
                ):
                    raise ValueError("capture contract")
            except (KeyError, TypeError, ValueError):
                invalid.append(name)
    verdict = "UNAVAILABLE" if invalid or not indicators_snapshot else (
        "PASS" if legacy_decision == "ALLOW" else "REJECT"
    )
    material = {
        "contract_version": "l3_confirmation_v2",
        "timeframe": "5m",
        "candle_policy": "CLOSED_ONLY",
        "verdict": verdict,
        "legacy_decision": legacy_decision,
        "gate_evaluation_hash": gate_evaluation_hash,
        "indicators_snapshot_hash": canonical_hash(indicators_snapshot),
        "reason_codes": (
            ["L3_TEMPORAL_IDENTITY_UNAVAILABLE"] if invalid
            else ["L3_INDICATORS_UNAVAILABLE"] if not indicators_snapshot
            else []
        ),
        "invalid_indicators": sorted(set(invalid)),
        "computed_at": now.isoformat(),
    }
    return _seal(material)


async def _load_profile(db, *, profile_id: str, expected_version_id: str, expected_hash: str) -> tuple[dict[str, Any], ProfileIdentity]:
    row = (await db.execute(text("""
        SELECT p.config, p.profile_type, p.is_shadow_only, p.live_trading_enabled,
               pv.id AS version_id, pv.config_hash AS version_hash
          FROM profiles p
          JOIN profile_versions pv ON pv.profile_id = p.id
         WHERE p.id = CAST(:profile_id AS UUID)
           AND pv.id = CAST(:version_id AS UUID)
           AND pv.status = 'SHADOW'
         LIMIT 1
    """), {"profile_id": profile_id, "version_id": expected_version_id})).mappings().one_or_none()
    if row is None:
        raise ValueError("PROFILE_VERSION_UNAVAILABLE")
    config = dict(row["config"] or {})
    actual_hash = canonical_profile_config_hash(config)
    if row["profile_type"] != "MTF_LAYER" or not row["is_shadow_only"] or row["live_trading_enabled"]:
        raise ValueError("MTF_PROFILE_AUTHORITY_INVALID")
    if actual_hash != expected_hash or str(row["version_hash"]) != expected_hash:
        raise ValueError("MTF_PROFILE_HASH_INVALID")
    return config, ProfileIdentity(
        profile_id=profile_id,
        profile_version_id=expected_version_id,
        profile_config_hash=expected_hash,
    )


async def _load_l2_state(
    db, *, user_id: Any, symbol: str, profile_version_id: str,
) -> dict[str, Any] | None:
    row = (await db.execute(text("""
        SELECT state, state_payload, last_candle_open_at,
               last_indicators_hash, state_hash
          FROM mtf_l2_setup_states
         WHERE user_id = CAST(:user_id AS UUID)
           AND symbol = :symbol
           AND profile_version_id = CAST(:profile_version_id AS UUID)
         FOR UPDATE
    """), {
        "user_id": str(user_id), "symbol": symbol,
        "profile_version_id": profile_version_id,
    })).mappings().one_or_none()
    return dict(row) if row else None


async def _persist_l2_state(
    db, *, user_id: Any, symbol: str, profile_version_id: str,
    transition: Mapping[str, Any],
) -> None:
    material = {
        "state": transition["state"],
        "state_before": transition["state_before"],
        "last_candle_open_at": transition["last_candle_open_at"],
        "last_indicators_hash": transition["last_indicators_hash"],
        "state_payload": transition["state_payload"],
    }
    if canonical_hash(material) != transition["state_hash"]:
        raise ValueError("L2_STATE_HASH_INVALID")
    await db.execute(text("""
        INSERT INTO mtf_l2_setup_states (
          user_id, symbol, profile_version_id, last_candle_open_at,
          last_indicators_hash, state, state_payload, state_hash
        ) VALUES (
          CAST(:user_id AS UUID), :symbol, CAST(:profile_version_id AS UUID),
          :last_candle_open_at, :last_indicators_hash, :state, CAST(:state_payload AS JSONB),
          :state_hash
        )
        ON CONFLICT (user_id, symbol, profile_version_id) DO UPDATE SET
          last_candle_open_at = EXCLUDED.last_candle_open_at,
          last_indicators_hash = EXCLUDED.last_indicators_hash,
          state = EXCLUDED.state,
          state_payload = EXCLUDED.state_payload,
          state_hash = EXCLUDED.state_hash,
          updated_at = clock_timestamp()
        WHERE mtf_l2_setup_states.last_candle_open_at <= EXCLUDED.last_candle_open_at
    """), {
        "user_id": str(user_id), "symbol": symbol,
        "profile_version_id": profile_version_id,
        "last_candle_open_at": _utc(transition["last_candle_open_at"]),
        "last_indicators_hash": transition["last_indicators_hash"],
        "state": transition["state"],
        "state_payload": json.dumps(transition["state_payload"]),
        "state_hash": transition["state_hash"],
    })


async def build_observations_for_assets(db, *, user_id: Any, assets: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Return per-symbol MTF contexts; failures are explicit WAIT envelopes."""
    row = (await db.execute(text("""
        SELECT config_json
          FROM config_profiles
         WHERE user_id = :user_id AND pool_id IS NULL
           AND config_type = 'spot_engine' AND is_active IS TRUE
         ORDER BY updated_at DESC LIMIT 1
    """), {"user_id": str(user_id)})).mappings().one_or_none()
    if row is None:
        return {}
    scanner = (row["config_json"] or {}).get("scanner") or {}
    try:
        contract = require_shadow_multilayer_config(scanner)
    except ValueError:
        return {}
    layers = contract["layers"]
    symbols = sorted({str(asset.get("symbol")) for asset in assets if asset.get("symbol")})
    now = datetime.now(timezone.utc)
    try:
        l1_profile, l1_identity = await _load_profile(
            db,
            profile_id=layers["L1"]["profile_id"],
            expected_version_id=layers["L1"]["profile_version_id"],
            expected_hash=layers["L1"]["profile_config_hash"],
        )
        l2_profile, l2_identity = await _load_profile(
            db,
            profile_id=layers["L2"]["profile_id"],
            expected_version_id=layers["L2"]["profile_version_id"],
            expected_hash=layers["L2"]["profile_config_hash"],
        )
        l1_rows = await get_timeframe_indicators(
            db, symbols, timeframe="1h", market_type="spot",
            groups=["structural"], now=now, include_stale=True,
        )
        l2_rows = await get_timeframe_indicators(
            db, symbols, timeframe="15m", market_type="spot",
            groups=["structural"], now=now, include_stale=True,
        )
    except Exception as exc:
        return {
            symbol: {
                "error": type(exc).__name__, "reason": str(exc),
                "observational_decision": "WAIT", "operational_effect": False,
            }
            for symbol in symbols
        }
    output: dict[str, dict[str, Any]] = {}
    for asset in assets:
        symbol = str(asset.get("symbol") or "")
        try:
            l1_required = _required_indicator_names(l1_profile) | {
                "adx", "atr_pct", "di_plus", "di_minus", "ema21", "ema50",
                "ema21_slope_pct", "ema50_slope_pct",
                "higher_highs_5", "higher_lows_5",
            }
            l1_values, l1_candle, l1_expiry = _validate_indicator_identity(
                l1_rows[symbol], required=l1_required,
                timeframe="1h", layer_config=layers["L1"], now=now,
            )
            l1_candle.symbol = symbol
            l1 = build_l1_context(
                symbol=symbol, profile=l1_profile, profile_identity=l1_identity,
                values=l1_values, candle=l1_candle, expires_at=l1_expiry, now=now,
            )
            l2_required = _required_indicator_names(l2_profile) | {
                "price", "atr", "ema21", "ema50", "vwap",
                "vwap_reclaim_bool", "bb_upper", "bb_lower",
                "di_plus", "di_minus", "higher_highs_5", "higher_lows_5",
                "adx", "volume_spike", "bb_width",
            }
            l2_values, l2_candle, l2_expiry = _validate_indicator_identity(
                l2_rows[symbol], required=l2_required,
                timeframe="15m", layer_config=layers["L2"], now=now,
            )
            l2_candle.symbol = symbol
            async with db.begin_nested():
                prior_state = await _load_l2_state(
                    db, user_id=user_id, symbol=symbol,
                    profile_version_id=l2_identity.profile_version_id,
                )
                transition = advance_l2_setup_state(
                    values=l2_values,
                    candle_open_at=l2_candle.source_timestamp,
                    semantics=l2_profile.get("mtf_semantics") or {},
                    previous=prior_state,
                )
                await _persist_l2_state(
                    db, user_id=user_id, symbol=symbol,
                    profile_version_id=l2_identity.profile_version_id,
                    transition=transition,
                )
            l2 = build_l2_context(
                symbol=symbol, profile=l2_profile, profile_identity=l2_identity,
                values=l2_values, candle=l2_candle, expires_at=l2_expiry,
                l1_context=l1, now=now, state_transition=transition,
            )
            l3 = {
                "contract_version": "l3_confirmation_v1",
                "timeframe": "5m",
                "verdict": "UNAVAILABLE",
                "reason_codes": ["L3_PENDING_CANONICAL_EVALUATION"],
            }
            output[symbol] = {
                "l1": l1, "l2": l2, "l3": l3,
                "l3_layer_config": dict(layers["L3"]),
                "calibration_run_id": str(contract.get("calibration_run_id") or ""),
                "statistical_gate": dict(contract.get("statistical_gate") or {}),
                "decision_context_version": contract.get(
                    "decision_feature_contract_version"
                ),
            }
        except Exception as exc:
            output[symbol] = {
                "error": type(exc).__name__,
                "reason": str(exc),
                "observational_decision": "WAIT",
                "operational_effect": False,
                "statistical_gate": dict(contract.get("statistical_gate") or {}),
                "decision_context_version": contract.get(
                    "decision_feature_contract_version"
                ),
            }
    return output

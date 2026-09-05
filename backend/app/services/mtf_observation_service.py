"""Fail-closed Spot MTF observation; never participates in order authority."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import text

from ..schemas.layer_context import (
    CandleIdentity,
    L1DecisionContextV2,
    L2DecisionContextV1,
    LayerVerdictRecord,
    MultilayerDecisionContextV2,
    ProfileIdentity,
)
from .indicators_provider import get_timeframe_indicators
from .multilayer_contract import require_shadow_multilayer_config
from .profile_engine import ProfileEngine
from .profile_runtime_config import canonical_hash, canonical_profile_config_hash

_TF_SECONDS = {"1h": 3600, "15m": 900, "5m": 300}


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
    policy_id = str(policy.get("provider_policy_id") or "")
    candidates_by_name = {
        str(candidate.get("indicator")): candidate
        for candidate in merged.candidates
        if candidate.get("timeframe") == timeframe
        and candidate.get("market_type") == "spot"
        and candidate.get("group") == "structural"
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
    config_hashes = {candidate.get("config_hash") for candidate in selected}
    if None in config_hashes or len(config_hashes) != 1:
        raise ValueError("INDICATOR_CONFIG_IDENTITY_CONFLICT")
    source_times = {_utc(candidate.get("source_timestamp")) for candidate in selected}
    if len(source_times) != 1:
        raise ValueError("INDICATOR_CANDLE_IDENTITY_CONFLICT")
    source_timestamp = next(iter(source_times))
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
    votes = []
    if values.get("di_plus") is not None and values.get("di_minus") is not None:
        votes.append(1 if float(values["di_plus"]) > float(values["di_minus"]) else -1)
    if values.get("ema21") is not None and values.get("ema50") is not None:
        votes.append(1 if float(values["ema21"]) > float(values["ema50"]) else -1)
    if values.get("higher_highs_5") is True or values.get("higher_lows_5") is True:
        votes.append(1)
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
        else "BEARISH" if direction == "DOWN" else "NEUTRAL"
    )
    payload = L1DecisionContextV2(
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
    ).model_dump(mode="json")
    return _seal(payload)


def build_l2_context(
    *, symbol: str, profile: Mapping[str, Any], profile_identity: ProfileIdentity,
    values: dict[str, Any], candle: CandleIdentity, expires_at: datetime,
    l1_context: Mapping[str, Any], now: datetime,
) -> dict[str, Any]:
    verify_context_hash(l1_context)
    semantics = profile.get("mtf_semantics") or {}
    if semantics.get("max_extension_atr") is None:
        raise ValueError("CONFIG_REQUIRED:max_extension_atr")
    verdict, _ = _profile_verdict(profile, symbol=symbol, timeframe="15m", values=values)
    price = float(values["price"])
    atr = float(values["atr"])
    ema = float(values["ema21"])
    vwap = float(values["vwap"])
    extension = abs(price - ema) / atr if atr > 0 else None
    direction = _direction(values)
    if extension is None or extension > float(semantics["max_extension_atr"]):
        setup = "INVALIDATED"
        verdict = "REJECT"
    elif bool(values.get("vwap_reclaim_bool")) and price >= ema:
        setup = "PULLBACK_RECLAIM"
    elif price >= float(values["bb_upper"]) and direction == "UP":
        setup = "BREAKOUT_RETEST"
    else:
        setup = "NONE"
    payload = L2DecisionContextV1(
        local_direction=direction,
        setup_state=setup,
        extension_atr=extension,
        support=float(values["bb_lower"]),
        resistance=float(values["bb_upper"]),
        invalidation=min(ema, vwap),
        validity="VALID",
        verdict=verdict,
        candle=candle,
        profile=profile_identity,
        l1_context_hash=str(l1_context["context_hash"]),
        computed_at=now,
        expires_at=expires_at,
        indicators_hash=canonical_hash(values),
    ).model_dump(mode="json")
    return _seal(payload)


def build_multilayer_context(
    *, l1: Mapping[str, Any], l2: Mapping[str, Any], l3_confirmation: Mapping[str, Any],
    canonical_score: float | None, now: datetime,
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
                else "l3_confirmation_v1"
            ),
        )
        for layer, value in verdict_values.items()
    }
    payload = MultilayerDecisionContextV2(
        l1_snapshot=dict(l1),
        l1_context_hash=str(l1["context_hash"]),
        l2_snapshot=dict(l2),
        l2_context_hash=str(l2["context_hash"]),
        l3_confirmation=dict(l3_confirmation),
        canonical_score=canonical_score,
        verdicts=verdicts,
        observational_decision=decision,
        computed_at=now,
    ).model_dump(mode="json")
    return _seal(payload)


def build_l3_confirmation(
    *, legacy_decision: str, indicators_snapshot: Mapping[str, Any],
    gate_evaluation_hash: str | None, now: datetime,
) -> dict[str, Any]:
    """Create a strict 5m confirmation from the actual persisted L3 inputs."""
    invalid = []
    for name, item in indicators_snapshot.items():
        if not isinstance(item, Mapping):
            invalid.append(str(name))
            continue
        observed = set(item.get("observed_timeframes") or [])
        timeframe = item.get("timeframe")
        if item.get("timeframe_conflict") or item.get("stale"):
            invalid.append(str(name))
        elif observed and observed != {"5m"}:
            invalid.append(str(name))
        elif timeframe and timeframe != "5m":
            invalid.append(str(name))
    verdict = "UNAVAILABLE" if invalid or not indicators_snapshot else (
        "PASS" if legacy_decision == "ALLOW" else "REJECT"
    )
    material = {
        "contract_version": "l3_confirmation_v1",
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
        "invalid_indicators": sorted(invalid),
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
    symbols = sorted({str(asset.get("symbol")) for asset in assets if asset.get("symbol")})
    now = datetime.now(timezone.utc)
    l1_rows = await get_timeframe_indicators(
        db, symbols, timeframe="1h", market_type="spot",
        groups=["structural"], now=now, include_stale=True,
    )
    l2_rows = await get_timeframe_indicators(
        db, symbols, timeframe="15m", market_type="spot",
        groups=["structural"], now=now, include_stale=True,
    )
    output: dict[str, dict[str, Any]] = {}
    for asset in assets:
        symbol = str(asset.get("symbol") or "")
        try:
            l1_required = _required_indicator_names(l1_profile) | {
                "adx", "atr_pct", "di_plus", "di_minus", "ema21", "ema50",
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
            }
            l2_values, l2_candle, l2_expiry = _validate_indicator_identity(
                l2_rows[symbol], required=l2_required,
                timeframe="15m", layer_config=layers["L2"], now=now,
            )
            l2_candle.symbol = symbol
            l2 = build_l2_context(
                symbol=symbol, profile=l2_profile, profile_identity=l2_identity,
                values=l2_values, candle=l2_candle, expires_at=l2_expiry,
                l1_context=l1, now=now,
            )
            l3 = {
                "contract_version": "l3_confirmation_v1",
                "timeframe": "5m",
                "verdict": "UNAVAILABLE",
                "reason_codes": ["L3_PENDING_CANONICAL_EVALUATION"],
            }
            output[symbol] = {"l1": l1, "l2": l2, "l3": l3}
        except Exception as exc:
            output[symbol] = {
                "error": type(exc).__name__,
                "reason": str(exc),
                "observational_decision": "WAIT",
                "operational_effect": False,
            }
    return output

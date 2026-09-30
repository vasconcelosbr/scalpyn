"""Resolve governed block inputs independently, without cross-timeframe fallback."""
from typing import Any, Mapping, Optional


def pinned_ohlcv_scheduler_group(runtime_policy: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The OHLCV ``scheduler_group`` the L3 v3 resolver pins for this profile.

    Same gate as ``materialize_runtime_profile_contract``: only an enabled,
    allowlisted resolver pins a group; otherwise None (no pin).
    """
    resolver = (runtime_policy or {}).get("l3_v3_provenance_resolver") or {}
    profile_id = (runtime_policy or {}).get("profile_id")
    if not resolver.get("enabled") or profile_id is None:
        return None
    if str(profile_id) not in {str(item) for item in resolver.get("profile_allowlist") or []}:
        return None
    return ((resolver.get("source_policies") or {}).get("ohlcv") or {}).get("scheduler_group")


def in_scheduler_group(candidate: dict, group: Optional[str]) -> bool:
    """Whether an OHLCV candidate survives a pinned ``scheduler_group``.

    The pin picks one of the two scheduled cadences that write the same
    series (compute_5m vs compute_structural_5m). A request-bound series
    (e.g. RSI 6 from closed 1m candles) has a single producer and no rival
    cadence, so the pin does not apply to it.
    """
    if not group or candidate.get("request_bound"):
        return True
    return (candidate.get("scheduler_group") or candidate.get("group")) == group


def block_condition_data(condition: dict, asset: dict, *, scheduler_group: Optional[str] = None) -> dict:
    # Legacy rules keep their existing compatibility path. Editor-authored
    # identities explicitly declare both source and timeframe.
    references = (
        list((condition.get("resolved_operands") or {}).values())
        if condition.get("type") == "comparison" else [condition]
    )
    data = {**(asset.get("indicators") or {}), **asset}
    candidates = list(getattr(asset.get("_merged_indicators"), "candidates", []) or [])
    candidates.extend(asset.get("_block_ohlcv_candidates") or [])
    for reference in references:
        if reference.get("source") != "ohlcv" or not reference.get("timeframe"):
            continue
        indicator = reference.get("indicator") or reference.get("field")
        if not indicator:
            continue
        timeframe = reference["timeframe"]
        matches = [c for c in candidates if c.get("indicator") == indicator
                   and c.get("timeframe") == timeframe]
        for key in ("source_provider", "provider_policy_id", "period"):
            if reference.get(key) is not None:
                matches = [c for c in matches if c.get(key) == reference[key]]
        # Read the cadence the L3 contract pins, or the contract looks for a
        # value from the other cadence and rejects (FEATURE_IDENTITY_NOT_AVAILABLE).
        group = reference.get("scheduler_group") or scheduler_group
        matches = [c for c in matches if in_scheduler_group(c, group)]
        # Candidate metadata wins over a flat map, including a missing value.
        if matches:
            selected = max(matches, key=lambda c: str(c.get("computed_at") or c.get("source_timestamp") or ""))
            value: Any = selected.get("actual")
            if (selected.get("stale") or selected.get("candle_closed") is False
                    or (reference.get("max_age_seconds") is not None
                        and (selected.get("age_seconds") is None
                             or selected["age_seconds"] > reference["max_age_seconds"]))):
                value = None
        elif not candidates:
            value = (asset.get("_indicators_by_tf") or {}).get(timeframe, {}).get(indicator)
        else:
            value = None
        data[indicator] = value
    return data


async def prepare_block_candle_inputs(db, assets: list[dict], profile: dict) -> list[dict]:
    """Fetch declared block candle series before both rejection and final gates.

    PROFILE_BLOCK_EXACT_TIMEFRAME: independent of legacy flat-map feature flags.
    Copies keep one profile's requested inputs out of shared upstream assets.
    """
    from .indicators_provider import get_timeframe_indicators, get_closed_block_rsi6

    requested_by_timeframe: dict[str, set[str]] = {}
    for block in (profile.get("block_rules") or {}).get("blocks") or []:
        if block.get("enabled", True) is False:
            continue
        for condition in block.get("conditions") or []:
            references = (list((condition.get("resolved_operands") or {}).values())
                          if condition.get("type") == "comparison" else [condition])
            for reference in references:
                if reference.get("source") == "ohlcv" and reference.get("timeframe"):
                    requested_by_timeframe.setdefault(reference["timeframe"], set()).add(
                        reference.get("indicator") or reference.get("field"))
    timeframes = set(requested_by_timeframe)
    if not timeframes or not assets or db is None:
        return assets
    prepared = [{**a, "_indicators_by_tf": {tf: dict(values) for tf, values in (a.get("_indicators_by_tf") or {}).items()},
                 "_block_ohlcv_candidates": list(a.get("_block_ohlcv_candidates") or []),
                 "_block_candle_timeframes_loaded": list(a.get("_block_candle_timeframes_loaded") or [])}
                for a in assets]
    for timeframe in sorted(timeframes):
        for market_type in ("spot", "futures"):
            pending = [a for a in prepared if timeframe not in a["_block_candle_timeframes_loaded"]
                       and ("futures" if a.get("is_futures") else "spot") == market_type]
            if not pending:
                continue
            # RSI 6 on spot M1 is request-bound: there is no scheduled M1
            # indicator series. Do not scan its empty historical table first.
            direct_rsi = (timeframe == "1m" and market_type == "spot"
                          and requested_by_timeframe[timeframe] == {"rsi_6"})
            exact = {} if direct_rsi else await get_timeframe_indicators(
                db, [a["symbol"] for a in pending], timeframe=timeframe, market_type=market_type,
            )
            missing_rsi = [a["symbol"] for a in pending if not any(
                c.get("indicator") == "rsi_6" for c in getattr(exact.get(a["symbol"]), "candidates", [])
            )] if timeframe == "1m" and market_type == "spot" else []
            rsi_candidates = await get_closed_block_rsi6(db, missing_rsi) if missing_rsi else {}
            for asset in pending:
                merged = exact.get(asset["symbol"])
                asset["_block_candle_timeframes_loaded"].append(timeframe)
                if merged is not None:
                    asset["_indicators_by_tf"][timeframe] = merged.as_flat_dict()
                    asset["_block_ohlcv_candidates"].extend(merged.candidates)
                if asset["symbol"] in rsi_candidates:
                    candidate = rsi_candidates[asset["symbol"]]
                    asset["_block_ohlcv_candidates"].append(candidate)
                    asset["_indicators_by_tf"].setdefault(timeframe, {})["rsi_6"] = candidate["actual"]
    return prepared

"""Pump-only identity and frozen research contracts; no inferred listing epochs."""
from datetime import datetime,timezone
from .pump_opportunity_engine import canonical_hash,number,utc

FEATURE_SPEC={"version":"pump_numeric_features_v1","availability":"captured_by_decision_no_future_producer_timestamp",
    "fields":{"rsi":"0_100","adx":"0_100","ema9":"quote_per_base","ema21":"quote_per_base",
        "price":"gate_best_ask_quote_per_base","spread_pct":"percent_of_mid",
        "estimated_slippage_buy_pct":"buy_vwap_minus_mid_percent_of_mid_no_fees",
        "delta_norm":"normalized_signed_flow","buy_persistence":"fraction_0_1","cvd_slope":"producer_flow_slope",
        "rvol_strict":"volume_ratio","price_progress_atr":"atr_units","breakout_hold_ratio":"fraction_0_1",
        "price_extension_atr":"atr_units","upper_wick_ratio":"fraction_0_1","ask_depth_usdt_1pct":"USDT"}}

# Optional context features (2026-10-07). Kept OUT of FEATURE_SPEC on purpose:
# every stored observation manifest carries canonical_hash(FEATURE_SPEC), so
# editing it would orphan the whole history. These cells are written by the
# Pump Score v1 cycle (structure 5m, Gate perp contract_stats, universe regime
# and USDT capital tide) and exist only in observations captured after each
# producer shipped; absent values enter the model as missing (NaN), never as a
# guess, and never gate row eligibility. None of them depends on the ML itself.
CONTEXT_FEATURE_SPEC={"version":"pump_context_features_v1","availability":"captured_by_decision_optional_missing_as_nan",
    "fields":{"v1_progress_atr":"atr5m_units","v1_rs_atr":"atr5m_units_vs_reference","v1_extension_atr":"atr5m_units",
        "v1_efficiency_short":"fraction_0_1","v1_efficiency_long":"fraction_0_1","v1_consistency":"fraction_0_1",
        "v1_higher_lows":"fraction_0_1","v1_concentration":"fraction_0_1","v1_wick":"fraction_0_1",
        "v1_rvol_5m":"volume_ratio","v1_volume_spike_max":"volume_ratio","v1_compression_ratio":"range_ratio",
        "v1_progress_1m_atr":"atr5m_units",
        "perp_perp_flow_norm":"normalized_signed_flow","perp_oi_change_pct":"percent","perp_funding_rate":"rate_per_period",
        "perp_short_liq_oi_bps":"bps_of_open_interest",
        "ctx_breadth":"fraction_0_1_universe_progress_positive","ctx_ref_progress_atr":"atr5m_units_reference",
        "ctx_ref_ret_pct":"percent_reference","ctx_capital_ratio":"net_over_gross_usdt_taker_flow",
        "ctx_capital_z":"zscore_vs_ew_history",
        # 2026-10-07: derived from closed 5m ohlcv at the decision (training: recomputed
        # point-in-time in memory; live: same function in the cycle). Previous 15 min
        # beta-adjusted return vs the universe (Gate 5m 02-07/10: P(next above | prev
        # above) 0.4774 vs 0.5225 | prev below, n=72,493 pairs) and the 24h beta itself.
        "rel_prev15_resid":"pp_beta_adjusted_vs_universe_median_previous_window",
        "beta_24h":"ols_slope_vs_universe_median_5m_returns"}}

def gate_listing_record(pair,captured_at):
    """Only provider-declared nonzero trading starts certify this epoch.

    Gate pair names alone do not distinguish relistings. Zero/absent starts
    remain unverified; no historical observation is retroactively certified.
    """
    evidence={k:pair.get(k) for k in ("id","base","quote","buy_start","sell_start")}
    starts=[pair.get(k) for k in ("buy_start","sell_start")]
    valid=pair.get("id")==f"{pair.get('base')}_{pair.get('quote')}" and pair.get("quote")=="USDT"
    valid=valid and pair.get("trade_status")=="tradable" and all(type(v) is int and 0<=v<=utc(captured_at).timestamp() for v in starts) and max(starts)>0
    if not valid:return {"certified":False,"reason":"provider_listing_epoch_unavailable","evidence":evidence}
    return {"certified":True,"source":"gate_spot_currency_pairs_v4","captured_at":utc(captured_at).isoformat(),
        "listing_id":f"gate_spot_epoch_v1:{pair['id']}:{int(starts[0])}:{int(starts[1])}",
        "evidence":evidence,"evidence_hash":canonical_hash(evidence)}

def validate_listing(record,symbol):
    if not record or not record.get("certified"):return False
    try:
        if utc(record.get("captured_at"))>datetime.now(timezone.utc):return False
    except (TypeError,ValueError):return False
    evidence=record.get("evidence") or {}
    try:expected=gate_listing_record({**evidence,"trade_status":"tradable"},record.get("captured_at"))
    except (ValueError,TypeError):return False
    return bool(expected.get("certified") and evidence.get("id")==symbol
        and expected["listing_id"]==record.get("listing_id") and expected["evidence_hash"]==record.get("evidence_hash")
        and record.get("source")==expected["source"])

def validate_manifest(spec):
    if "cost_policy" not in spec:raise ValueError("Declare known costs or explicit null")
    if not spec.get("producer_config_hash"):raise ValueError("Frozen producer configuration hash required")
    if spec.get("label_spec_hash")!=canonical_hash(spec.get("label_spec")):raise ValueError("Frozen label specification mismatch")
    from .pump_opportunity_engine import config
    if not isinstance(spec.get("label_spec"),dict) or config({"labels":spec["label_spec"]})["labels"]!=spec["label_spec"]:
        raise ValueError("Fully expanded validated label specification required")
    if spec.get("reference_policy")!="gate_best_ask_v1":raise ValueError("Frozen best ask reference required")
    features=spec.get("features",[])
    if not features or len(features)!=len(set(features)) or any(f not in FEATURE_SPEC["fields"] for f in features):
        raise ValueError("Pump feature names must be unique and defined by the frozen dictionary")
    context=spec.get("context_features",[])
    if not isinstance(context,list) or len(context)!=len(set(context)) or set(context)&set(features) \
            or any(f not in CONTEXT_FEATURE_SPEC["fields"] for f in context):
        raise ValueError("Pump context features must be unique and defined by the context dictionary")
    if context and spec.get("context_feature_spec_hash")!=canonical_hash(CONTEXT_FEATURE_SPEC):
        raise ValueError("Pump context dictionary mismatch")
    if spec.get("feature_spec_hash")!=canonical_hash(FEATURE_SPEC):raise ValueError("Pump feature dictionary mismatch")
    cuts=spec.get("boundaries",[])
    if len(cuts)!=3 or not all(utc(a)<utc(b) for a,b in zip(cuts,cuts[1:])):raise ValueError("Strict temporal cuts required")
    if spec.get("embargo_seconds",0)<120*60:raise ValueError("Embargo must cover the maximum 120-minute horizon")
    for key in ("min_episodes","min_days","min_instruments","max_rows","max_threads"):
        if type(spec.get(key)) is not int or spec[key]<=0:raise ValueError(f"Positive integer manifest required: {key}")
    if not number(spec.get("decision_threshold")) or not 0<spec["decision_threshold"]<1:raise ValueError("Explicit probability selection threshold required")
    costs=spec.get("cost_policy")
    if costs is not None and (not number(costs.get("roundtrip_pct")) or costs["roundtrip_pct"]<0):raise ValueError("Invalid cost policy")
    if spec.get("cost_policy_hash")!=canonical_hash(costs):raise ValueError("Frozen cost policy hash mismatch")
    if not isinstance(spec.get("support_criteria"),dict) or not spec["support_criteria"]:raise ValueError("Explicit support and review criteria required")
    return {"version":"pump_temporal_manifest_v1","frozen_at":datetime.now(timezone.utc).isoformat(),
        "manifest_hash":canonical_hash(spec),"feature_spec":FEATURE_SPEC,"spec":spec,"auto_promotion":False,"applied_delta":0}

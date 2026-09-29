"""Pump Monitor — pure logic: config, row assembly, score, colours, alerts, hysteresis.

OBSERVATION ONLY. Nothing produced here authorises an entry; the score is a
v0 HYPOTHESIS whose weights have not been calibrated (see ``score.status``).

Every threshold, weight, window, polarity and colour band comes from the
versioned ``pump_monitor`` config. ``DEFAULT_CONFIG`` is only the seed that
the operator publishes (and can change through ``PUT /api/pump-monitor/config``).
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from . import flow_metrics as fm

SCORE_STATUS = "HYPOTHESIS_NOT_VALIDATED"

# Seed configuration. Numbers marked "hipótese" are uncalibrated starting points.
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "universe_pool_id": None,
    "cycle_seconds": 30,
    "symbol_timeout_seconds": 8,
    "concurrency": 8,
    "flow": {
        "bucket_seconds": 60,
        "volume_unit": "quote",
        "min_coverage_pct": 80,
        "persistence_buckets": 10,
        "progress_window_minutes": 15,
        "bucket_lookback_seconds": 300,
        "settle_seconds": 3,
        "retention_days": 2,
    },
    "cvd": {"window_minutes": 60, "slope_buckets": 15},
    "book": {"limit": 100, "bands_pct": [0.5, 1, 2], "imbalance_band_pct": 1, "max_age_seconds": 60},
    "slippage": {
        "reference_notional_usdt": 1000,
        "notional_source": "spot_engine.shadow.amount_usdt (default escolhido pelo operador em 2026-09-29)",
        "amber_pct": 0.1,
        "red_pct": 0.2,
    },
    "price": {
        "extension_reference": "vwap",
        "breakout_level_key": "recent_high_1h_level",
        "breakout_max_age_minutes": 60,
        "breakout_tolerance_pct": 0.0,
        "wick_timeframe": "5m",
    },
    "score": {
        "version": "pump_monitor_score_v0",
        "status": SCORE_STATUS,
        "min_confidence": 0.5,
        # value mapped linearly: lo → 0, hi → 1 (clamped). Hipótese.
        "components": {
            "delta_norm": {"group": "flow", "weight": 1.0, "lo": 0.0, "hi": 0.5},
            "buy_persistence": {"group": "flow", "weight": 1.0, "lo": 0.5, "hi": 0.9},
            "cvd_slope": {"group": "flow", "weight": 1.0, "lo": 0.0, "hi": 0.5},
            "rvol_strict": {"group": "flow", "weight": 1.0, "lo": 1.0, "hi": 4.0},
            "price_progress_atr": {"group": "price", "weight": 1.0, "lo": 0.0, "hi": 2.0},
            "price_change_5m_pct": {"group": "price", "weight": 0.5, "lo": 0.0, "hi": 3.0},
            "price_change_15m_pct": {"group": "price", "weight": 0.5, "lo": 0.0, "hi": 5.0},
            "breakout_hold_ratio": {"group": "price", "weight": 0.5, "lo": 0.5, "hi": 1.0},
        },
        "penalties": {
            "spread_pct": {"weight": 0.5, "lo": 0.1, "hi": 0.5},
            "estimated_slippage_buy_pct": {"weight": 1.0, "lo": 0.2, "hi": 0.6},
            "upper_wick_ratio": {"weight": 0.5, "lo": 0.4, "hi": 0.8},
            "price_extension_atr": {"weight": 0.5, "lo": 3.0, "hi": 6.0},
        },
    },
    "filters": {"only_rising": {"price_progress_atr_min": 0.25, "delta_norm_min": 0.0}},
    "alerts": {
        "effort_no_progress": {"enabled": True, "rvol_strict_min": 2.0,
                               "buy_persistence_min": 0.7, "abs_price_progress_atr_max": 0.25},
        "breakout_failure": {"enabled": True, "breakout_hold_ratio_max": 0.5, "flow_change_max": 0.0},
        "sell_absorption": {"enabled": True, "persistence_buckets": 5,
                            "sell_persistence_min": 0.8, "price_progress_atr_min": 0.0},
    },
    "display": {"top_n": 10, "top_n_options": [10, 20, 50, 0], "stale_after_cycles": 2},
    # Redis always holds the full latest cycle; the table keeps a sample.
    "snapshots": {"retention_hours": 48, "every_n_cycles": 10},
    # Defaults for pools with pump_monitor_sync_enabled (overridable per pool
    # via overrides.pump_monitor_<key>). min_score is on the 0–100 score scale.
    "sync_defaults": {"min_score": 50.0,
                      "exit_consecutive_cycles": 3, "min_hold_seconds": 300,
                      "only_rising": True, "min_scored_fraction": 0.5},
    # polarity ∈ higher_better | lower_better | neutral | categorical.
    # Without "bands" an indicator is shown without colour (never invented).
    "indicators": {
        "pump_monitor_score": {"group": "scores", "polarity": "higher_better"},
        "delta_norm": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "window_delta_norm": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "cvd_60m": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "cvd_slope": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "buy_persistence": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.5}},
        "volume_acceleration": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "flow_change": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "rvol_strict": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 1.0}},
        "volume_spike": {"group": "flow", "polarity": "neutral"},
        "taker_ratio": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.5}},
        "volume_delta": {"group": "flow", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "obv": {"group": "flow", "polarity": "neutral"},
        "spread_pct": {"group": "liquidity", "polarity": "lower_better"},
        "orderbook_depth_usdt": {"group": "liquidity", "polarity": "higher_better"},
        "bid_ask_imbalance": {"group": "liquidity", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "bid_depth_usdt_0_5pct": {"group": "liquidity", "polarity": "higher_better"},
        "ask_depth_usdt_0_5pct": {"group": "liquidity", "polarity": "neutral"},
        "bid_depth_usdt_1pct": {"group": "liquidity", "polarity": "higher_better"},
        "ask_depth_usdt_1pct": {"group": "liquidity", "polarity": "neutral"},
        "bid_depth_usdt_2pct": {"group": "liquidity", "polarity": "higher_better"},
        "ask_depth_usdt_2pct": {"group": "liquidity", "polarity": "neutral"},
        "depth_imbalance_1pct": {"group": "liquidity", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "estimated_slippage_buy_pct": {"group": "liquidity", "polarity": "lower_better",
                                       "bands": {"type": "threshold", "from": "slippage"}},
        "estimated_slippage_sell_pct": {"group": "liquidity", "polarity": "lower_better",
                                        "bands": {"type": "threshold", "from": "slippage"}},
        "volume_24h_usdt": {"group": "liquidity", "polarity": "neutral"},
        "price": {"group": "price", "polarity": "neutral"},
        "price_change_1m_pct": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "price_change_5m_pct": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "price_change_15m_pct": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "price_progress_atr": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "price_extension_atr": {"group": "price", "polarity": "neutral"},
        "breakout_distance_atr": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "breakout_hold_ratio": {"group": "price", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.5}},
        "upper_wick_ratio": {"group": "price", "polarity": "lower_better"},
        "vwap_distance_pct": {"group": "price", "polarity": "neutral"},
        "bb_upper_distance_pct": {"group": "price", "polarity": "neutral"},
        "recent_high_1h_distance_pct": {"group": "price", "polarity": "neutral"},
        "rsi": {"group": "momentum", "polarity": "neutral"},
        "macd_histogram": {"group": "momentum", "polarity": "higher_better", "bands": {"type": "sign", "center": 0.0}},
        "stoch_k": {"group": "momentum", "polarity": "neutral"},
        "adx": {"group": "trend", "polarity": "neutral"},
        "di_trend": {"group": "trend", "polarity": "categorical", "bands": {"type": "map", "map": {"true": "green", "false": "red"}}},
        "psar_trend": {"group": "trend", "polarity": "categorical", "bands": {"type": "map", "map": {"bullish": "green", "bearish": "red"}}},
        "ema_full_alignment": {"group": "trend", "polarity": "categorical", "bands": {"type": "map", "map": {"true": "green", "false": "red"}}},
        "atr_pct": {"group": "trend", "polarity": "neutral"},
        "bb_width": {"group": "trend", "polarity": "neutral"},
        "score": {"group": "scores", "polarity": "neutral"},
        "liquidity_score": {"group": "scores", "polarity": "neutral"},
        "momentum_score": {"group": "scores", "polarity": "neutral"},
    },
}

# Indicators copied from the governed indicator snapshot (not recomputed here).
SNAPSHOT_KEYS = (
    "price", "volume_24h_usdt", "spread_pct", "orderbook_depth_usdt", "bid_ask_imbalance",
    "taker_ratio", "volume_delta", "volume_spike", "rvol_strict", "obv",
    "price_change_1m_pct", "price_change_5m_pct", "price_change_15m_pct",
    "vwap_distance_pct", "bb_upper_distance_pct", "recent_high_1h_distance_pct",
    "rsi", "macd_histogram", "stoch_k", "adx", "di_trend", "psar_trend",
    "ema_full_alignment", "atr_pct", "bb_width",
    # From alpha_scores (score engine), injected by the service.
    "score", "liquidity_score", "momentum_score",
)


# ── Config ───────────────────────────────────────────────────────────────────

def _merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            out[key] = _merge(base.get(key), value) if key in base else deepcopy(value)
        return out
    return deepcopy(override)


def config_body(config: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in (config or {}).items() if k != "_meta"}


def config_hash(config: Dict[str, Any]) -> str:
    from .profile_runtime_config import canonical_hash
    return canonical_hash(config_body(config))


def effective_config(stored: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Stored config over the seed; ``_meta`` carries version + hash."""
    body = _merge(DEFAULT_CONFIG, config_body(stored or {}))
    meta = dict((stored or {}).get("_meta") or {})
    meta.setdefault("version", 0)
    meta["config_hash"] = config_hash(body)
    return {**body, "_meta": meta}


def next_config_version(previous: Optional[Dict[str, Any]], requested: Dict[str, Any],
                        *, changed_by: str, now_iso: str) -> Dict[str, Any]:
    # Partial updates land on the current version, not on the seed.
    current = _merge(DEFAULT_CONFIG, config_body(previous or {}))
    body = config_body(_merge(current, config_body(requested)))
    validate_config(body)
    version = int(((previous or {}).get("_meta") or {}).get("version") or 0) + 1
    return {**body, "_meta": {"version": version, "config_hash": config_hash(body),
                              "updated_at": now_iso, "updated_by": changed_by}}


def validate_config(body: Dict[str, Any]) -> None:
    errors: List[str] = []
    if int(body.get("cycle_seconds", 0)) < 10:
        errors.append("cycle_seconds must be >= 10")
    if int(body["flow"]["bucket_seconds"]) != 60:
        errors.append("flow.bucket_seconds is fixed at 60 (1-minute buckets)")
    if body["flow"]["volume_unit"] not in ("quote", "base"):
        errors.append("flow.volume_unit must be quote|base")
    if not 0 <= float(body["flow"]["min_coverage_pct"]) <= 100:
        errors.append("flow.min_coverage_pct must be within [0, 100]")
    for key in ("persistence_buckets", "progress_window_minutes"):
        if int(body["flow"][key]) < 1:
            errors.append(f"flow.{key} must be >= 1")
    if int(body["cvd"]["window_minutes"]) < 2 or int(body["cvd"]["slope_buckets"]) < 2:
        errors.append("cvd windows must be >= 2")
    slip = body["slippage"]
    if float(slip["reference_notional_usdt"]) <= 0:
        errors.append("slippage.reference_notional_usdt must be > 0")
    if not 0 <= float(slip["amber_pct"]) <= float(slip["red_pct"]):
        errors.append("slippage bands must satisfy 0 <= amber_pct <= red_pct")
    for name, spec in {**body["score"]["components"], **body["score"]["penalties"]}.items():
        if float(spec["hi"]) == float(spec["lo"]) or float(spec["weight"]) < 0:
            errors.append(f"score.{name}: hi must differ from lo and weight must be >= 0")
    sync_int = body["display"]["top_n"]
    if int(sync_int) < 0:
        errors.append("display.top_n must be >= 0")
    if errors:
        raise ValueError("; ".join(errors))


# ── Colours ──────────────────────────────────────────────────────────────────

def color_state(name: str, value: Any, config: Dict[str, Any]) -> Optional[str]:
    """green / amber / red / gray(null) / None (no colour defined)."""
    spec = (config.get("indicators") or {}).get(name) or {}
    if value is None:
        return "gray"
    polarity = spec.get("polarity")
    bands = spec.get("bands")
    if not bands or polarity in (None, "neutral"):
        return None
    kind = bands.get("type")
    if kind == "map":
        return (bands.get("map") or {}).get(str(value).lower())
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if kind == "sign":
        center = float(bands.get("center", 0.0))
        weak = float(bands.get("weak_abs", 0.0))
        if polarity == "lower_better":
            v, center = -v, -center
        if v > center + weak:
            return "green"
        if v < center - weak:
            return "red"
        return "amber"
    if kind == "threshold":
        source = config.get(bands["from"]) if bands.get("from") else bands
        amber, red = float(source["amber_pct"]), float(source["red_pct"])
        if polarity == "lower_better":
            return "green" if v <= amber else "amber" if v <= red else "red"
        return "green" if v >= red else "amber" if v >= amber else "red"
    return None


# ── Breakout state (level frozen at the moment of the break) ─────────────────

def advance_breakout(state: Optional[Dict[str, Any]], *, price: Any, level: Any,
                     minute_ms: int, config: Dict[str, Any]) -> Dict[str, Any]:
    """Track one breakout episode per symbol.

    A breakout starts when the price trades above the level observed in the
    *previous* cycle; that level is frozen for the whole episode. The
    episode expires after ``price.breakout_max_age_minutes``.
    """
    state = dict(state or {})
    max_age_ms = int(config["price"]["breakout_max_age_minutes"]) * 60_000
    if state.get("breakout_at_ms") is not None and minute_ms - int(state["breakout_at_ms"]) > max_age_ms:
        state.pop("breakout_level", None)
        state.pop("breakout_at_ms", None)
    previous_level = state.get("last_level")
    if (state.get("breakout_at_ms") is None and previous_level is not None
            and price is not None and float(price) > float(previous_level)):
        state["breakout_level"] = float(previous_level)
        state["breakout_at_ms"] = int(minute_ms)
    if level is not None:
        state["last_level"] = float(level)
    return state


# ── Row assembly ─────────────────────────────────────────────────────────────

def _cell(value: Any, *, reason: Optional[str] = None, source: Optional[str] = None,
          status: Optional[str] = None, **extra) -> Dict[str, Any]:
    return {"value": value, "reason": reason if value is None else None,
            "source": source, "status": status or ("NO_DATA" if value is None else "VALID"), **extra}


def _metric_cell(metric: Dict[str, Any], source: str) -> Dict[str, Any]:
    extra = {k: metric[k] for k in ("coverage_pct", "partial_buckets", "missing_buckets") if k in metric}
    return _cell(metric["value"], reason=metric.get("reason"), source=metric.get("source") or source, **extra)


def build_row(
    symbol: str,
    *,
    snapshot: Dict[str, Any],
    snapshot_meta: Dict[str, Dict[str, Any]],
    buckets: Dict[int, Dict[str, Any]],
    book: Optional[Dict[str, Any]],
    candle: Optional[Dict[str, Any]],
    breakout_state: Dict[str, Any],
    last_minute_ms: int,
    now_ms: int,
    config: Dict[str, Any],
) -> Dict[str, Any]:
    """One monitor row. ``last_minute_ms`` is the start of the last closed minute."""
    flow_cfg, unit = config["flow"], config["flow"]["volume_unit"]
    min_cov = float(flow_cfg["min_coverage_pct"])
    cells: Dict[str, Dict[str, Any]] = {}

    for key in SNAPSHOT_KEYS:
        meta = snapshot_meta.get(key) or {}
        value = snapshot.get(key)
        reason = None if value is not None else ("stale" if meta.get("stale") else "not_in_snapshot")
        cells[key] = _cell(value, reason=reason, source=meta.get("source"),
                           status=meta.get("indicator_status"),
                           age_seconds=meta.get("age_seconds"))

    # Flow (single source: flow_metrics)
    last = buckets.get(last_minute_ms)
    prev = buckets.get(last_minute_ms - 60_000)
    cvd_win = fm.window(buckets, end_ms=last_minute_ms, count=int(config["cvd"]["window_minutes"]))
    slope_win = cvd_win[-int(config["cvd"]["slope_buckets"]):]
    pers_win = fm.window(buckets, end_ms=last_minute_ms, count=int(flow_cfg["persistence_buckets"]))
    prog_win = fm.window(buckets, end_ms=last_minute_ms, count=int(flow_cfg["progress_window_minutes"]))
    source = next((b.get("source") for b in [last, prev] if b and b.get("source")), None)
    cells["delta_norm"] = _metric_cell(fm.delta_norm(last, unit), source)
    cells["window_delta_norm"] = _metric_cell(fm.window_delta_norm(prog_win, unit, min_cov), source)
    cells["cvd_60m"] = _metric_cell(fm.cvd(cvd_win, unit, min_cov), source)
    cells["cvd_slope"] = _metric_cell(fm.cvd_slope(slope_win, unit, min_cov), source)
    cells["buy_persistence"] = _metric_cell(fm.buy_persistence(pers_win, unit, min_cov), source)
    cells["volume_acceleration"] = _metric_cell(fm.volume_acceleration(prev, last, unit), source)
    cells["flow_change"] = _metric_cell(fm.flow_change(prev, last, unit), source)

    # Price normalised by ATR
    atr = snapshot.get("atr")
    trade_price = last.get("close_price") if last and not last.get("partial") else None
    price = trade_price if trade_price is not None else snapshot.get("price")
    price_source = "flow_bucket_close" if trade_price is not None else (snapshot_meta.get("price") or {}).get("source")
    reference_key = config["price"]["extension_reference"]
    start_close, end_close = fm.window_close_prices(prog_win)
    prog_cov = fm.coverage_pct(prog_win)
    progress = (fm.price_progress_atr(start_close, end_close, atr) if prog_cov >= min_cov
                else fm.null("insufficient_coverage"))
    cells["price_extension_atr"] = _cell(**_unpack(fm.price_extension_atr(price, snapshot.get(reference_key), atr)),
                                         source=f"{price_source}/{reference_key}")
    cells["price_progress_atr"] = _cell(**_unpack(progress), source="flow_bucket_close", coverage_pct=prog_cov)

    level = breakout_state.get("breakout_level")
    started = breakout_state.get("breakout_at_ms")
    if level is not None and started is not None:
        count = int((last_minute_ms - int(started)) // 60_000) + 1
        hold_win = fm.window(buckets, end_ms=last_minute_ms, count=count) if count > 0 else []
        hold = fm.breakout_hold_ratio(hold_win, level, float(config["price"]["breakout_tolerance_pct"]), min_cov)
    else:
        hold = fm.null("no_active_breakout")
    cells["breakout_distance_atr"] = _cell(**_unpack(fm.breakout_distance_atr(price, level, atr)),
                                           source="frozen_breakout_level", breakout_level=level,
                                           breakout_at_ms=started)
    cells["breakout_hold_ratio"] = _metric_cell(hold, "flow_bucket_close")
    if candle:
        wick = fm.upper_wick_ratio(candle.get("open"), candle.get("high"), candle.get("low"), candle.get("close"))
        cells["upper_wick_ratio"] = _cell(**_unpack(wick), source=f"ohlcv_{config['price']['wick_timeframe']}_closed",
                                          candle_time=candle.get("time"))
    else:
        cells["upper_wick_ratio"] = _cell(None, reason="candle_unavailable")

    # Executable liquidity
    cells.update(liquidity_cells(book, now_ms, config))

    row = {"symbol": symbol, "indicators": cells}
    row.update(score_row(cells, config))
    row["alerts_active"] = evaluate_alerts(cells, buckets, last_minute_ms, config)
    for name, cell in cells.items():
        cell["color_state"] = color_state(name, cell["value"], config)
    row["only_rising"] = is_rising(cells, config)
    ages = [c.get("age_seconds") for c in cells.values() if isinstance(c.get("age_seconds"), (int, float))]
    row["data_age_seconds"] = round(max(ages), 1) if ages else None
    row["coverage"] = {k: cells[k].get("coverage_pct") for k in ("cvd_60m", "buy_persistence", "price_progress_atr")}
    return row


def _unpack(metric: Dict[str, Any]) -> Dict[str, Any]:
    return {"value": metric["value"], "reason": metric.get("reason")}


def liquidity_cells(book: Optional[Dict[str, Any]], now_ms: int, config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    keys = ["depth_imbalance_1pct", "estimated_slippage_buy_pct", "estimated_slippage_sell_pct"]
    bands = [float(b) for b in config["book"]["bands_pct"]]
    for band in bands:
        label = _band_label(band)
        keys += [f"bid_depth_usdt_{label}pct", f"ask_depth_usdt_{label}pct"]
    if not book:
        return {k: _cell(None, reason="book_unavailable", source="gate_rest_order_book") for k in keys}
    age = book.get("age_seconds")
    if age is None or age > float(config["book"]["max_age_seconds"]):
        return {k: _cell(None, reason="book_stale", source="gate_rest_order_book", age_seconds=age) for k in keys}
    bids, asks = book.get("bids") or [], book.get("asks") or []
    mid = fm.book_mid(bids, asks)
    limit = int(config["book"]["limit"])
    complete = {"bid": len(bids) < limit, "ask": len(asks) < limit}
    source = "gate_rest_order_book"
    out: Dict[str, Dict[str, Any]] = {}
    depths: Dict[float, tuple] = {}
    for band in bands:
        label = _band_label(band)
        bid = fm.band_depth_quote(bids, mid, band, "bid", complete["bid"])
        ask = fm.band_depth_quote(asks, mid, band, "ask", complete["ask"])
        depths[band] = (bid, ask)
        out[f"bid_depth_usdt_{label}pct"] = _cell(**_unpack(bid), source=source, age_seconds=age)
        out[f"ask_depth_usdt_{label}pct"] = _cell(**_unpack(ask), source=source, age_seconds=age)
    imb_band = float(config["book"]["imbalance_band_pct"])
    bid, ask = depths.get(imb_band) or (fm.band_depth_quote(bids, mid, imb_band, "bid", complete["bid"]),
                                         fm.band_depth_quote(asks, mid, imb_band, "ask", complete["ask"]))
    out["depth_imbalance_1pct"] = _cell(**_unpack(fm.depth_imbalance(bid, ask)), source=source, age_seconds=age)
    notional = float(config["slippage"]["reference_notional_usdt"])
    out["estimated_slippage_buy_pct"] = _cell(**_unpack(fm.estimated_slippage_pct(asks, mid, notional, "buy")),
                                              source=source, age_seconds=age, notional_usdt=notional)
    out["estimated_slippage_sell_pct"] = _cell(**_unpack(fm.estimated_slippage_pct(bids, mid, notional, "sell")),
                                               source=source, age_seconds=age, notional_usdt=notional)
    return out


def _band_label(band: float) -> str:
    return str(band).rstrip("0").rstrip(".").replace(".", "_")


# ── Score v0 (hypothesis) ────────────────────────────────────────────────────

def _normalise(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def score_row(cells: Dict[str, Dict[str, Any]], config: Dict[str, Any]) -> Dict[str, Any]:
    """Σ(w·n) − Σ(p·m), divided by the weight of the available components.

    Missing components are excluded (never scored as zero) and reduce
    ``score_confidence``; below ``min_confidence`` the score is null.
    """
    spec = config["score"]
    components: Dict[str, Dict[str, Any]] = {}
    total_weight = sum(float(c["weight"]) for c in spec["components"].values())
    available = gained = lost = 0.0
    for name, c in spec["components"].items():
        value = (cells.get(name) or {}).get("value")
        if value is None:
            components[name] = {"value": None, "weight": c["weight"], "contribution": None,
                                "reason": (cells.get(name) or {}).get("reason")}
            continue
        n = _normalise(float(value), float(c["lo"]), float(c["hi"]))
        available += float(c["weight"])
        gained += float(c["weight"]) * n
        components[name] = {"value": value, "normalized": round(n, 4), "weight": c["weight"],
                            "contribution": round(float(c["weight"]) * n, 4)}
    for name, p in spec["penalties"].items():
        value = (cells.get(name) or {}).get("value")
        if value is None:
            components[f"penalty:{name}"] = {"value": None, "weight": p["weight"], "contribution": None,
                                             "reason": (cells.get(name) or {}).get("reason")}
            continue
        m = _normalise(float(value), float(p["lo"]), float(p["hi"]))
        lost += float(p["weight"]) * m
        components[f"penalty:{name}"] = {"value": value, "normalized": round(m, 4), "weight": p["weight"],
                                         "contribution": round(-float(p["weight"]) * m, 4)}
    confidence = round(available / total_weight, 4) if total_weight else 0.0
    if available <= 0 or confidence < float(spec["min_confidence"]):
        score = None
    else:
        score = round(100.0 * max(0.0, min(1.0, (gained - lost) / available)), 2)
    cells["pump_monitor_score"] = _cell(score, reason=None if score is not None else "low_confidence",
                                        source=spec["version"])
    return {"pump_monitor_score": score, "score_components": components,
            "score_version": spec["version"], "score_status": spec["status"], "score_confidence": confidence}


def is_rising(cells: Dict[str, Dict[str, Any]], config: Dict[str, Any]) -> bool:
    rule = config["filters"]["only_rising"]
    progress = (cells.get("price_progress_atr") or {}).get("value")
    delta = (cells.get("delta_norm") or {}).get("value")
    if progress is None or delta is None:
        return False
    return progress > float(rule["price_progress_atr_min"]) and delta > float(rule["delta_norm_min"])


# ── Composite alerts (context, never proof) ──────────────────────────────────

def evaluate_alerts(cells: Dict[str, Dict[str, Any]], buckets: Dict[int, Dict[str, Any]],
                    last_minute_ms: int, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    rules = config["alerts"]
    v = {k: (cells.get(k) or {}).get("value") for k in
         ("rvol_strict", "buy_persistence", "price_progress_atr", "breakout_hold_ratio", "flow_change")}
    active: List[Dict[str, Any]] = []

    rule = rules["effort_no_progress"]
    if (rule.get("enabled") and None not in (v["rvol_strict"], v["buy_persistence"], v["price_progress_atr"])
            and v["rvol_strict"] >= rule["rvol_strict_min"]
            and v["buy_persistence"] >= rule["buy_persistence_min"]
            and abs(v["price_progress_atr"]) <= rule["abs_price_progress_atr_max"]):
        active.append({"type": "effort_no_progress", "inputs": {k: v[k] for k in
                       ("rvol_strict", "buy_persistence", "price_progress_atr")}})

    rule = rules["breakout_failure"]
    if (rule.get("enabled") and None not in (v["breakout_hold_ratio"], v["flow_change"])
            and v["breakout_hold_ratio"] < rule["breakout_hold_ratio_max"]
            and v["flow_change"] < rule["flow_change_max"]):
        active.append({"type": "breakout_failure", "inputs": {
            "breakout_hold_ratio": v["breakout_hold_ratio"], "flow_change": v["flow_change"],
            "breakout_level": (cells.get("breakout_distance_atr") or {}).get("breakout_level")}})

    rule = rules["sell_absorption"]
    unit = config["flow"]["volume_unit"]
    win = fm.window(buckets, end_ms=last_minute_ms, count=int(rule["persistence_buckets"]))
    sell = fm.buy_persistence(win, unit, float(config["flow"]["min_coverage_pct"]), side="sell")
    if (rule.get("enabled") and sell["value"] is not None and v["price_progress_atr"] is not None
            and sell["value"] >= rule["sell_persistence_min"]
            and v["price_progress_atr"] >= rule["price_progress_atr_min"]):
        active.append({"type": "sell_absorption", "inputs": {
            "sell_persistence": sell["value"], "price_progress_atr": v["price_progress_atr"]}})
    return active


def new_alerts(previous_types: Iterable[str], active: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Only transitions (inactive → active) are logged, not every cycle."""
    previous = set(previous_types or [])
    return [a for a in active if a["type"] not in previous]


# ── REALTIME membership with hysteresis ──────────────────────────────────────

def advance_membership(state: Optional[Dict[str, Any]], scores: Dict[str, float], *, now_ms: int,
                       min_score: float, exit_consecutive_cycles: int,
                       min_hold_seconds: int) -> Dict[str, Any]:
    """Anti-flapping membership by minimum Pump Score (no size cap).

    Enter: score >= min_score.
    Exit: score < min_score (or unscored/ineligible) for
    ``exit_consecutive_cycles`` consecutive cycles AND held for at least
    ``min_hold_seconds``.
    """
    if not 0 <= min_score <= 100:
        raise ValueError("min_score must be within [0, 100]")
    members = {s: dict(m) for s, m in ((state or {}).get("members") or {}).items()}
    for symbol in list(members):
        score = scores.get(symbol)
        member = members[symbol]
        member["out_count"] = 0 if (score is not None and score >= min_score) else int(member.get("out_count", 0)) + 1
        held = (now_ms - int(member["entered_at_ms"])) / 1000.0
        if member["out_count"] >= exit_consecutive_cycles and held >= min_hold_seconds:
            del members[symbol]
    for symbol, score in scores.items():
        if symbol not in members and score >= min_score:
            members[symbol] = {"entered_at_ms": int(now_ms), "out_count": 0}
    return {"members": members}

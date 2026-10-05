"""Pump Score v1 — trend radar for stable 10–15 min up-moves (pure logic).

OBSERVATION ONLY, HYPOTHESIS_NOT_VALIDATED. Runs in parallel with v0 every
cycle; ``engines.active`` in the ``pump_monitor`` config selects which engine
drives the displayed Pump Score and the REALTIME pool sync. Rollback is a
config change (``engines.active = "v0"``) that applies on the next cycle.

Design (each layer has one job):
  1. Structure on CLOSED 5m candles (trend shape, ATR(5m) as the only unit).
  2. Regime: BTC 5m progress + universe breadth; relative strength vs BTC.
  3. Direction gates (AND, missing input = fail, never zero): a falling,
     stretched, choppy or micro-pump asset cannot score.
  4. Strength score (only when every gate passes): geometric mean of
     flow / price+RS / trend quality / participation blocks, so a weak
     block cannot be compensated by a strong one.
  5. Stability: EMA smoothing + hysteresis state machine stepped once per
     closed minute; hard invalidation exits immediately, soft weakening
     needs ``exit_cycles`` AND ``min_hold_minutes``.

Every threshold below is a seed hypothesis read from config (ZERO HARDCODE).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

VERSION = "pump_score_v1"
STATUS = "HYPOTHESIS_NOT_VALIDATED"
ENGINES = ("v0", "v1")
LISTED_STATES = ("candidato", "ativo", "enfraquecendo")
MEMBER_STATES = ("ativo", "enfraquecendo")
# A stall with buying (absorção) or a pause is soft: it may be acceptance before the next leg.
HARD_CONDITIONS = ("caindo", "esticado", "sem_dados")

DEFAULT_V1: Dict[str, Any] = {
    "version": VERSION,
    "status": STATUS,
    "structure": {
        "timeframe": "5m",
        "lookback_candles": 48,        # 4h of 5m candles
        "max_age_seconds": 600,        # last closed candle older than this → stale
        "atr_period": 14,
        "short_candles": 6,            # 30 min
        "long_candles": 12,            # 60 min
        "progress_candles": 3,         # 15 min
        "rvol_baseline_candles": 20,
        "rvol_recent_candles": 3,      # participation = mean of the last 3 closed candles vs baseline
        "vwap_candles": 12,            # rolling 60-min VWAP: the extension reference for a 10–15 min radar
        "compression_base_candles": 12,
    },
    "regime": {
        "enabled": True,
        "reference_symbol": "BTC_USDT",
        "progress_min_atr": 0.0,
        "breadth_min": 0.5,
        "beta": 1.0,                   # first version: rs = ret − ret_btc
        "rs_min_neutral_atr": 0.0,
        "rs_min_unfavorable_atr": 0.5,
    },
    "gates": {
        "progress_atr_min": 0.25,
        "window_delta_norm_min": -0.1,  # flow must not be net selling (tiny noise tolerated)
        "cvd_slope_min": 0.0,
        "extension_atr_min": 0.0,      # price above the rolling 60-min VWAP
        "extension_atr_max": 6.0,      # vs 60-min VWAP: a steady ~1 ATR/candle hour sits ~5.5 ATR above it
        "wick_max": 0.5,
        "efficiency_min": 0.3,
        "concentration_max": 0.6,      # one 5m candle cannot carry > 60 % of the 30-min upward movement
        "volume_spike_max": 4.0,       # one 5m candle volume vs its 20-candle baseline
        "slippage_buy_max_pct": 0.2,
        "require_ask_depth_1pct": True,
        "fast_exit_progress_1m_atr": -0.25,
    },
    # Absolute normalisation lo→0, hi→1; used while the per-asset statistics warm up
    # (or always, when normalization.mode = "absolute").
    "factors": {
        "cvd_slope": {"lo": 0.0, "hi": 0.5},
        "window_delta_norm": {"lo": 0.0, "hi": 0.5},
        "progress_atr": {"lo": 0.25, "hi": 2.0},
        "rs_atr": {"lo": -0.5, "hi": 1.5},
        "rvol_5m": {"lo": 0.0, "hi": 1.5},
    },
    "normalization": {"mode": "asset_adaptive", "halflife_minutes": 1440, "min_observations": 240},
    # Participation is context, not a veto: stable trends often run on ordinary volume.
    "blocks": {"flow": 1.0, "price": 1.0, "quality": 1.0, "participation": 0.25, "compression": 0.0},
    # A block at exactly 0 would zero the geometric mean; the floor keeps it heavily penalised but finite.
    "block_floor": 0.05,
    "penalties": {
        "extension_atr": {"weight": 0.5, "lo": 3.0, "hi": 6.0},
        "slippage_buy_pct": {"weight": 0.5, "lo": 0.1, "hi": 0.2},
    },
    "stability": {
        "ema_alpha": 0.2,
        "enter_score": 50.0,
        "stay_score": 35.0,
        "enter_cycles": 3,
        "exit_cycles": 3,
        "min_hold_minutes": 15,
        "cooldown_minutes": 10,
    },
}

_TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000}


def _null(reason: str) -> Dict[str, Any]:
    return {"value": None, "reason": reason}


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def validate(spec: Dict[str, Any], errors: List[str]) -> None:
    s = spec["structure"]
    if s["timeframe"] not in _TF_MS:
        errors.append(f"score_v1.structure.timeframe must be one of {sorted(_TF_MS)}")
    need = max(int(s["atr_period"]) + 1, int(s["long_candles"]) + 1,
               int(s["rvol_baseline_candles"]) + int(s["rvol_recent_candles"]),
               int(s["vwap_candles"]),
               int(s["long_candles"]) + int(s["compression_base_candles"]))
    if int(s["lookback_candles"]) < need:
        errors.append(f"score_v1.structure.lookback_candles must be >= {need}")
    if not 0 <= float(spec.get("block_floor", 0.0)) < 1:
        errors.append("score_v1.block_floor must be within [0, 1)")
    for key in ("short_candles", "progress_candles", "atr_period", "rvol_recent_candles", "vwap_candles"):
        if int(s[key]) < 1:
            errors.append(f"score_v1.structure.{key} must be >= 1")
    for name, f in spec["factors"].items():
        if float(f["hi"]) <= float(f["lo"]):
            errors.append(f"score_v1.factors.{name}: hi must be > lo")
    for name, p in spec["penalties"].items():
        if float(p["hi"]) <= float(p["lo"]) or not 0 <= float(p["weight"]) <= 1:
            errors.append(f"score_v1.penalties.{name}: hi > lo and 0 <= weight <= 1")
    if any(float(w) < 0 for w in spec["blocks"].values()) or sum(float(w) for w in spec["blocks"].values()) <= 0:
        errors.append("score_v1.blocks: weights must be >= 0 with a positive sum")
    st = spec["stability"]
    if not 0 < float(st["ema_alpha"]) <= 1:
        errors.append("score_v1.stability.ema_alpha must be within (0, 1]")
    if not 0 <= float(st["stay_score"]) <= float(st["enter_score"]) <= 100:
        errors.append("score_v1.stability: 0 <= stay_score <= enter_score <= 100")
    for key in ("enter_cycles", "exit_cycles"):
        if int(st[key]) < 1:
            errors.append(f"score_v1.stability.{key} must be >= 1")
    norm = spec["normalization"]
    if norm["mode"] not in ("asset_adaptive", "absolute"):
        errors.append("score_v1.normalization.mode must be asset_adaptive|absolute")
    if float(norm["halflife_minutes"]) <= 0 or int(norm["min_observations"]) < 2:
        errors.append("score_v1.normalization: halflife_minutes > 0 and min_observations >= 2")


# ── 1. Structure on closed candles ───────────────────────────────────────────

def structure_metrics(candles: Sequence[Dict[str, Any]], spec: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    """Trend shape from CLOSED candles sorted by time ascending.

    ``candles`` items: ``time_ms, open, high, low, close, volume``. Returns a
    dict of values (``None`` with ``reason`` when not computable).
    """
    s = spec["structure"]
    tf_ms = _TF_MS[s["timeframe"]]
    keys = ("atr", "atr_pct", "progress_atr", "ret_pct", "efficiency_short", "efficiency_long",
            "consistency", "higher_lows", "concentration", "wick", "rvol_5m", "volume_spike_max",
            "compression_ratio", "vwap", "extension_atr", "close", "candle_close_ms")
    rows = [c for c in candles if all(_finite(c.get(k)) is not None for k in ("open", "high", "low", "close"))]
    atr_n, short, long_, prog = (int(s["atr_period"]), int(s["short_candles"]),
                                 int(s["long_candles"]), int(s["progress_candles"]))
    base_n, rv_n = int(s["compression_base_candles"]), int(s["rvol_baseline_candles"])
    if not rows:
        return {"reason": "no_closed_candles", **{k: None for k in keys}}
    last_close_ms = int(rows[-1]["time_ms"]) + tf_ms
    if now_ms - last_close_ms > int(s["max_age_seconds"]) * 1000:
        return {"reason": "stale_candles", **{k: None for k in keys}, "candle_close_ms": last_close_ms}
    if len(rows) < max(atr_n + 1, long_ + 1, prog + 1):
        return {"reason": "insufficient_candles", **{k: None for k in keys}, "candle_close_ms": last_close_ms}

    o = [float(c["open"]) for c in rows]
    h = [float(c["high"]) for c in rows]
    lo = [float(c["low"]) for c in rows]
    c = [float(c["close"]) for c in rows]
    tr = [max(h[i] - lo[i], abs(h[i] - c[i - 1]), abs(lo[i] - c[i - 1])) for i in range(1, len(c))]
    atr = sum(tr[-atr_n:]) / atr_n
    out: Dict[str, Any] = {k: None for k in keys}
    out.update(reason=None, close=c[-1], candle_close_ms=last_close_ms)
    if atr <= 0 or c[-1] <= 0:
        out["reason"] = "atr_zero"
        return out
    out["atr"] = atr
    out["atr_pct"] = 100.0 * atr / c[-1]
    out["progress_atr"] = (c[-1] - c[-1 - prog]) / atr
    out["ret_pct"] = 100.0 * (c[-1] / c[-1 - prog] - 1.0) if c[-1 - prog] > 0 else None

    def efficiency(n: int) -> Optional[float]:
        path = sum(abs(c[i] - c[i - 1]) for i in range(len(c) - n, len(c)))
        return abs(c[-1] - c[-1 - n]) / path if path > 0 else None

    out["efficiency_short"] = efficiency(short)
    out["efficiency_long"] = efficiency(long_)
    out["consistency"] = sum(1 for i in range(len(c) - short, len(c)) if c[i] > o[i]) / short
    out["higher_lows"] = sum(1 for i in range(len(c) - short, len(c)) if lo[i] > lo[i - 1]) / short
    # Share of the gross upward movement carried by the single biggest up-candle (30 min).
    # A steady staircase spreads it (~1/6); a micro pump puts most of it in one candle.
    # (Dividing by the NET move exploded on noisy paths: median > 1 in production.)
    ups = [c[i] - c[i - 1] for i in range(len(c) - short, len(c)) if c[i] > c[i - 1]]
    out["concentration"] = max(ups) / sum(ups) if ups else None
    rng = h[-1] - lo[-1]
    out["wick"] = (h[-1] - max(o[-1], c[-1])) / rng if rng > 0 else None

    vol = [_finite(r.get("volume")) for r in rows]
    recent_n = int(s["rvol_recent_candles"])

    def ratio(i: int, n: int = 1) -> Optional[float]:
        """Mean volume of the ``n`` candles ending at ``i`` vs the ``rv_n`` candles before them."""
        window, base = vol[i - n + 1:i + 1], vol[i - n + 1 - rv_n:i - n + 1]
        if len(window) < n or len(base) < rv_n or any(v is None for v in window + base):
            return None
        mean = sum(base) / rv_n
        return (sum(window) / n) / mean if mean > 0 else None

    out["rvol_5m"] = ratio(len(vol) - 1, recent_n)
    spikes = [r for r in (ratio(i) for i in range(len(vol) - short, len(vol))) if r is not None]
    out["volume_spike_max"] = max(spikes) if spikes else None

    vw_n = int(s["vwap_candles"])
    pv = [((h[i] + lo[i] + c[i]) / 3.0, vol[i]) for i in range(len(c) - vw_n, len(c))]
    if all(v is not None for _, v in pv) and sum(v for _, v in pv) > 0:
        out["vwap"] = sum(p * v for p, v in pv) / sum(v for _, v in pv)
        out["extension_atr"] = (c[-1] - out["vwap"]) / atr

    if len(c) >= long_ + base_n:
        ranges = [h[i] - lo[i] for i in range(len(c))]
        base = ranges[-(long_ + base_n):-long_]
        ref = sum(ranges) / len(ranges)
        out["compression_ratio"] = (sum(base) / len(base)) / ref if ref > 0 else None
    return out


# ── 2. Regime and relative strength ──────────────────────────────────────────

def regime(reference: Optional[Dict[str, Any]], universe_progress: Sequence[Optional[float]],
           spec: Dict[str, Any]) -> Dict[str, Any]:
    r = spec["regime"]
    values = [v for v in universe_progress if v is not None]
    breadth = sum(1 for v in values if v > 0) / len(values) if values else None
    ref_progress = (reference or {}).get("progress_atr")
    out = {"reference_symbol": r["reference_symbol"], "reference_progress_atr": ref_progress,
           "reference_ret_pct": (reference or {}).get("ret_pct"), "breadth": breadth,
           "universe_counted": len(values)}
    if not r.get("enabled"):
        return {**out, "state": "desligado"}
    if ref_progress is None or breadth is None:
        return {**out, "state": "desconhecido"}
    up = ref_progress > float(r["progress_min_atr"])
    wide = breadth >= float(r["breadth_min"])
    state = "favoravel" if up and wide else "desfavoravel" if not up and not wide else "neutro"
    return {**out, "state": state}


def relative_strength_atr(ret_pct: Optional[float], ref_ret_pct: Optional[float], atr_pct: Optional[float],
                          beta: float) -> Optional[float]:
    if None in (ret_pct, ref_ret_pct, atr_pct) or not atr_pct:
        return None
    return (ret_pct - beta * ref_ret_pct) / atr_pct


# ── 3. Direction gates and condition ─────────────────────────────────────────

def _gate(name: str, value: Optional[float], ok: Optional[bool]) -> Dict[str, Any]:
    return {"gate": name, "value": value, "result": ok, "reason": None if ok is not None else "missing_input"}


def evaluate_gates(v: Dict[str, Any], regime_state: str, spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    g, r = spec["gates"], spec["regime"]

    def cmp(value, fn):
        return None if value is None else bool(fn(value))

    gates = [
        _gate("progress_5m", v.get("progress_atr"), cmp(v.get("progress_atr"), lambda x: x > float(g["progress_atr_min"]))),
        _gate("window_flow", v.get("window_delta_norm"),
              cmp(v.get("window_delta_norm"), lambda x: x > float(g["window_delta_norm_min"]))),
        _gate("cvd_slope", v.get("cvd_slope"), cmp(v.get("cvd_slope"), lambda x: x > float(g["cvd_slope_min"]))),
        _gate("above_vwap", v.get("extension_atr"),
              cmp(v.get("extension_atr"), lambda x: x > float(g["extension_atr_min"]))),
        _gate("not_stretched", v.get("extension_atr"),
              cmp(v.get("extension_atr"), lambda x: x < float(g["extension_atr_max"]))),
        _gate("no_upper_wick", v.get("wick"), cmp(v.get("wick"), lambda x: x < float(g["wick_max"]))),
        _gate("efficiency", v.get("efficiency_short"),
              cmp(v.get("efficiency_short"), lambda x: x >= float(g["efficiency_min"]))),
        _gate("not_concentrated", v.get("concentration"),
              cmp(v.get("concentration"), lambda x: x <= float(g["concentration_max"]))),
        _gate("no_volume_spike", v.get("volume_spike_max"),
              cmp(v.get("volume_spike_max"), lambda x: x <= float(g["volume_spike_max"]))),
        _gate("executable", v.get("slippage_buy_pct"),
              cmp(v.get("slippage_buy_pct"), lambda x: x <= float(g["slippage_buy_max_pct"]))),
    ]
    if g.get("require_ask_depth_1pct"):
        depth, slippage = v.get("ask_depth_1pct"), v.get("slippage_buy_pct")
        # The 1 % band is null when the 100-level book does not even reach 1 % from mid,
        # i.e. on the DEEPEST books (BTC, XRP). A measured buy slippage for the reference
        # notional already proves the ask side absorbs the order, so it satisfies the gate.
        ok = (depth > 0) if depth is not None else (True if slippage is not None else None)
        gates.append(_gate("ask_depth_1pct", depth, ok))
    rs = v.get("rs_atr")
    if regime_state in ("desligado", "favoravel"):
        regime_ok: Optional[bool] = True
    elif regime_state == "neutro":
        regime_ok = cmp(rs, lambda x: x > float(r["rs_min_neutral_atr"]))
    elif regime_state == "desfavoravel":
        regime_ok = cmp(rs, lambda x: x > float(r["rs_min_unfavorable_atr"]))
    else:
        regime_ok = None
    gates.append(_gate(f"regime:{regime_state}", rs, regime_ok))
    return gates


def classify(v: Dict[str, Any], gates: List[Dict[str, Any]], spec: Dict[str, Any]) -> str:
    """Instant condition of the asset. Only ``subindo`` may score."""
    g = spec["gates"]
    progress, ext, wick = v.get("progress_atr"), v.get("extension_atr"), v.get("wick")
    flow_up = (v.get("window_delta_norm") or 0) > 0 and (v.get("cvd_slope") or 0) > 0
    if progress is None:
        return "sem_dados"
    if (ext is not None and ext >= float(g["extension_atr_max"])) or (wick is not None and wick >= float(g["wick_max"])):
        return "esticado"
    if progress < -float(g["progress_atr_min"]) or (ext is not None and ext <= float(g["extension_atr_min"]) and progress <= 0):
        return "caindo"
    if progress <= 0 and flow_up:
        return "absorcao"  # buying absorbed above VWAP: a stall, not (yet) a fall
    if all(x["result"] is True for x in gates):
        return "subindo"
    return "neutro"


# ── 4. Strength score (non-compensatory) ─────────────────────────────────────

def _absolute(value: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def normalise(name: str, value: Optional[float], stats: Optional[Dict[str, Any]], spec: Dict[str, Any]) -> Dict[str, Any]:
    """Per-asset percentile (EW mean/var, normal approximation) or absolute lo/hi during warm-up."""
    if value is None:
        return {"value": None, "normalized": None, "method": None}
    f = spec["factors"][name]
    norm = spec["normalization"]
    if (norm["mode"] == "asset_adaptive" and stats and int(stats.get("n", 0)) >= int(norm["min_observations"])
            and float(stats.get("var", 0.0)) > 0):
        z = (value - float(stats["mean"])) / math.sqrt(float(stats["var"]))
        return {"value": value, "normalized": 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))), "method": "asset_percentile"}
    return {"value": value, "normalized": _absolute(value, float(f["lo"]), float(f["hi"])), "method": "absolute"}


def update_stats(stats: Optional[Dict[str, Any]], value: Optional[float], spec: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Exponentially weighted mean/variance, one update per closed minute."""
    if value is None:
        return stats
    alpha = 1.0 - 0.5 ** (1.0 / float(spec["normalization"]["halflife_minutes"]))
    if not stats or int(stats.get("n", 0)) == 0:
        return {"mean": value, "var": 0.0, "n": 1}
    mean, var = float(stats["mean"]), float(stats["var"])
    diff = value - mean
    incr = alpha * diff
    return {"mean": mean + incr, "var": (1.0 - alpha) * (var + diff * incr), "n": int(stats["n"]) + 1}


def strength_score(v: Dict[str, Any], stats: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    n = {k: normalise(k, v.get(k), (stats or {}).get(k), spec)
         for k in ("cvd_slope", "window_delta_norm", "progress_atr", "rs_atr", "rvol_5m")}

    def mean(values):
        values = [x for x in values if x is not None]
        return sum(values) / len(values) if values else None

    blocks = {
        "flow": mean([n["cvd_slope"]["normalized"], n["window_delta_norm"]["normalized"]]),
        "price": (None if n["progress_atr"]["normalized"] is None or n["rs_atr"]["normalized"] is None
                  else 0.5 * n["progress_atr"]["normalized"] + 0.5 * n["rs_atr"]["normalized"]),
        "quality": mean([v.get("efficiency_short"), v.get("consistency"), v.get("higher_lows")]),
        "participation": n["rvol_5m"]["normalized"],
        "compression": (None if v.get("compression_ratio") is None
                        else max(0.0, min(1.0, 1.0 - float(v["compression_ratio"])))),
    }
    weights = {k: float(w) for k, w in spec["blocks"].items() if float(w) > 0}
    missing = [k for k in weights if blocks.get(k) is None]
    penalties = {}
    for name, p in spec["penalties"].items():
        value = v.get(name)
        penalties[name] = None if value is None else float(p["weight"]) * _absolute(value, float(p["lo"]), float(p["hi"]))
    ledger = {"factors": n, "blocks": blocks, "weights": weights, "penalties": penalties, "missing_blocks": missing}
    if missing:
        return {"score": None, "reason": "missing_block", "ledger": ledger}
    eps = 1e-9
    floor = max(float(spec.get("block_floor", 0.0)), eps)
    geo = math.exp(sum(w * math.log(max(blocks[k], floor)) for k, w in weights.items()) / sum(weights.values()))
    score = 100.0 * geo
    for value in penalties.values():
        if value is not None:
            score *= (1.0 - value)
    return {"score": round(max(0.0, min(100.0, score)), 2), "reason": None, "ledger": ledger}


# ── 5. Stability (hysteresis, stepped once per closed minute) ────────────────

def step_state(prev: Optional[Dict[str, Any]], *, condition: str, gates_ok: bool, raw_score: Optional[float],
               fast_exit: bool, minute_ms: int, spec: Dict[str, Any]) -> Dict[str, Any]:
    st = spec["stability"]
    s = dict(prev or {"state": "fora", "score_s": None, "in_count": 0, "out_count": 0,
                      "entered_ms": None, "exited_ms": None})
    if s.get("last_step_ms") == minute_ms:
        return s
    alpha = float(st["ema_alpha"])
    prior = s.get("score_s")
    passing = gates_ok and raw_score is not None
    if s.get("state", "fora") not in MEMBER_STATES and not passing:
        # not listed and not passing: no memory, so a later entry starts from its own score
        s["score_s"] = None
    else:
        current = raw_score if passing else 0.0
        s["score_s"] = round(current if prior is None else alpha * current + (1 - alpha) * float(prior), 2)
    s["last_step_ms"] = minute_ms
    hard = condition in HARD_CONDITIONS or fast_exit
    state = s.get("state", "fora")

    def leave():
        s.update(state="fora", in_count=0, out_count=0, exited_ms=minute_ms, entered_ms=None)

    if state in MEMBER_STATES:
        held_min = (minute_ms - int(s.get("entered_ms") or minute_ms)) / 60_000
        if hard:
            leave()
            s["exit_reason"] = f"hard:{'fast_exit_1m' if fast_exit else condition}"
        elif gates_ok and (s["score_s"] or 0.0) >= float(st["stay_score"]):
            s.update(state="ativo", out_count=0)
        else:
            s["out_count"] = int(s.get("out_count", 0)) + 1
            s["state"] = "enfraquecendo"
            if s["out_count"] >= int(st["exit_cycles"]) and held_min >= float(st["min_hold_minutes"]):
                leave()
                s["exit_reason"] = "soft:weakening"
        return s

    cooling = s.get("exited_ms") is not None and (minute_ms - int(s["exited_ms"])) < int(st["cooldown_minutes"]) * 60_000
    if not cooling and gates_ok and not hard and s["score_s"] is not None and s["score_s"] >= float(st["enter_score"]):
        s["in_count"] = int(s.get("in_count", 0)) + 1
        if s["in_count"] >= int(st["enter_cycles"]):
            s.update(state="ativo", entered_ms=minute_ms, out_count=0, exit_reason=None)
        else:
            s["state"] = "candidato"
    else:
        s.update(state="fora", in_count=0)
    return s


# ── Orchestration for one cycle (pure) ───────────────────────────────────────

def evaluate_universe(rows: List[Dict[str, Any]], structures: Dict[str, Dict[str, Any]],
                      reference: Optional[Dict[str, Any]], state: Dict[str, Any], *,
                      minute_ms: int, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate v1 for every row, mutate ``state`` (per-symbol stats + stability).

    Returns ``{"regime": ..., "results": {symbol: result}}``. Rows are only read.
    """
    reg = regime(reference, [(structures.get(r["symbol"]) or {}).get("progress_atr") for r in rows], spec)
    stats_all = state.setdefault("stats", {})
    stab_all = state.setdefault("stability", {})
    stepped = state.get("last_step_ms") != minute_ms
    results: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"]
        cells = row.get("indicators") or {}
        val = lambda k: _finite((cells.get(k) or {}).get("value"))
        st = structures.get(symbol) or {"reason": "no_structure"}
        source = row.get("opportunity_source_values") or {}
        atr = st.get("atr")
        atr_snapshot = _finite(source.get("atr"))
        # v0 1m progress is in the snapshot ATR unit; rescale it to ATR(5m) for the fast exit.
        to_5m = (lambda x: None if None in (x, atr_snapshot, atr) or not atr else x * atr_snapshot / atr)
        extension = st.get("extension_atr")  # vs rolling 60-min VWAP of closed 5m candles
        v = {
            "progress_atr": st.get("progress_atr"), "ret_pct": st.get("ret_pct"), "atr_pct": st.get("atr_pct"),
            "efficiency_short": st.get("efficiency_short"), "efficiency_long": st.get("efficiency_long"),
            "consistency": st.get("consistency"), "higher_lows": st.get("higher_lows"),
            "concentration": st.get("concentration"), "wick": st.get("wick"), "rvol_5m": st.get("rvol_5m"),
            "volume_spike_max": st.get("volume_spike_max"), "compression_ratio": st.get("compression_ratio"),
            "window_delta_norm": val("window_delta_norm"), "cvd_slope": val("cvd_slope"),
            "progress_1m_atr": to_5m(val("price_progress_atr")),
            "extension_atr": extension, "slippage_buy_pct": val("estimated_slippage_buy_pct"),
            "ask_depth_1pct": val("ask_depth_usdt_1pct"),
        }
        v["rs_atr"] = relative_strength_atr(v["ret_pct"], reg.get("reference_ret_pct"), v["atr_pct"],
                                            float(spec["regime"]["beta"]))
        gates = evaluate_gates(v, reg["state"], spec)
        condition = classify(v, gates, spec)
        gates_ok = condition == "subindo"
        stats = stats_all.get(symbol) or {}
        strength = strength_score(v, stats, spec) if gates_ok else {"score": None, "reason": f"condition:{condition}",
                                                                      "ledger": None}
        fast_exit = (v["progress_1m_atr"] is not None
                     and v["progress_1m_atr"] < float(spec["gates"]["fast_exit_progress_1m_atr"]))
        stab = step_state(stab_all.get(symbol), condition=condition, gates_ok=gates_ok,
                          raw_score=strength["score"], fast_exit=fast_exit, minute_ms=minute_ms, spec=spec)
        stab_all[symbol] = stab
        if stepped:
            stats_all[symbol] = {k: update_stats(stats.get(k), v.get(k), spec)
                                 for k in ("cvd_slope", "window_delta_norm", "progress_atr", "rs_atr", "rvol_5m")}
        listed = stab["state"] in LISTED_STATES
        results[symbol] = {
            "condition": condition, "state": stab["state"], "raw_score": strength["score"],
            "score": stab["score_s"] if listed else None,
            "reason": None if listed else f"{stab['state']}:{condition}",
            "gates": gates, "values": v, "structure_reason": st.get("reason"),
            "strength": strength.get("ledger"), "stability": stab, "fast_exit": fast_exit,
        }
    known = set(results)
    for symbol in list(stab_all):
        if symbol not in known:  # left the universe: drop its state, never keep a stale signal
            stab_all.pop(symbol, None)
    state["last_step_ms"] = minute_ms
    return {"regime": reg, "results": results}


def members(results: Dict[str, Dict[str, Any]]) -> List[str]:
    return sorted(s for s, r in results.items() if r["state"] in MEMBER_STATES)


def display_components(result: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Tooltip ledger in the v0 ``score_components`` shape (value → contribution)."""
    out: Dict[str, Dict[str, Any]] = {
        "estado": {"value": result["state"], "contribution": None},
        "condição": {"value": result["condition"], "contribution": None},
        "score bruto": {"value": result["raw_score"], "contribution": None},
    }
    for g in result["gates"]:
        out[f"gate:{g['gate']}"] = {"value": g["value"], "contribution": g["result"],
                                    "reason": g["reason"]}
    ledger = result.get("strength") or {}
    for name, value in (ledger.get("blocks") or {}).items():
        out[f"bloco:{name}"] = {"value": value, "contribution": (ledger.get("weights") or {}).get(name)}
    for name, value in (ledger.get("penalties") or {}).items():
        out[f"penalidade:{name}"] = {"value": value, "contribution": None}
    return out

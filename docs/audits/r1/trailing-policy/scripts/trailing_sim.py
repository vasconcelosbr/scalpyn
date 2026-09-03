"""Generalized trailing-policy simulator (P1 fixed / P2 stepped / P3 proportional),
built on the same CLOSED_ONLY / SL_FIRST / first-touch discipline as
shadow_barrier_evaluator.evaluate_closed_candles.
"""
from datetime import datetime


def floor_fixed(hwm, entry_price, activation_pct, trail_pct):
    if activation_pct <= 0 or trail_pct <= 0:
        return None
    if hwm < entry_price * (1 + activation_pct / 100):
        return None
    return hwm * (1 - trail_pct / 100)


def floor_proportional(hwm, entry_price, k):
    """Retain (1-k) of the peak PROFIT (not of the raw peak price).
    Reference check: peak=1% profit, k=0.30 -> floor=0.7% profit;
    peak=4% profit, k=0.30 -> floor=2.8% profit."""
    return entry_price + (hwm - entry_price) * (1 - k)


def floor_stepped(hwm, entry_price, steps, base):
    """steps: sorted list of (peak_pct, floor_pct), floors are ABSOLUTE profit
    levels (entry_price * (1 + floor_pct/100)), flat within a tier.
    base: None (no trailing below first step) or (activation_pct, trail_pct)
    fixed-style trailing used only below the first step's peak threshold."""
    hwm_pct = (hwm / entry_price - 1) * 100
    applicable = [s for s in steps if hwm_pct >= s[0]]
    if applicable:
        floor_pct = max(s[1] for s in applicable)
        return entry_price * (1 + floor_pct / 100)
    if base is not None:
        return floor_fixed(hwm, entry_price, base[0], base[1])
    return None


def simulate_policy(
    candles,
    *,
    entry_price: float,
    entry_timestamp: datetime,
    tp_price: float,
    sl_price: float,
    timeout_candles: int,
    floor_fn,
    never_sell_at_loss: bool,
    protected_profit_pct: float,
):
    """floor_fn(hwm, entry_price) -> trailing floor price or None."""
    ordered = sorted(candles, key=lambda c: c["time"])
    entry_bucket = entry_timestamp.replace(second=0, microsecond=0)
    entry_boundary_partial = entry_timestamp != entry_bucket
    hwm = entry_price
    result = {
        "status": "PENDING",
        "outcome": None,
        "barrier_touched_at": None,
        "exit_price_nominal": None,
        "high_water_mark": entry_price,
    }
    for index, candle in enumerate(ordered, start=1):
        candle_at = candle["time"]
        high = candle["high"]
        low = candle["low"]

        trailing_stop = floor_fn(hwm, entry_price)
        protected = entry_price * (1 + protected_profit_pct / 100)
        if trailing_stop is not None and never_sell_at_loss and trailing_stop < protected:
            trailing_stop = None

        sl_hit = low <= sl_price
        tp_hit = high >= tp_price
        trailing_hit = (
            trailing_stop is not None
            and trailing_stop > sl_price
            and low <= trailing_stop
        )
        entry_candle = candle_at == entry_bucket
        if entry_boundary_partial and entry_candle and (trailing_hit or sl_hit or tp_hit):
            result.update({"status": "UNRESOLVED", "outcome": None, "barrier_touched_at": candle_at})
            return result
        if trailing_hit:
            result.update({
                "status": "OUTCOME", "outcome": "TRAILING_STOP",
                "barrier_touched_at": candle_at, "exit_price_nominal": trailing_stop,
                "high_water_mark": hwm,
            })
            return result
        if sl_hit:
            result.update({
                "status": "OUTCOME", "outcome": "SL_HIT",
                "barrier_touched_at": candle_at, "exit_price_nominal": sl_price,
                "high_water_mark": hwm,
            })
            return result
        if tp_hit:
            result.update({
                "status": "OUTCOME", "outcome": "TP_HIT",
                "barrier_touched_at": candle_at, "exit_price_nominal": tp_price,
                "high_water_mark": hwm,
            })
            return result

        hwm = max(hwm, high)
        if timeout_candles and index >= timeout_candles:
            close = candle.get("close", candle.get("open"))
            result.update({
                "status": "OUTCOME", "outcome": "TIMEOUT",
                "barrier_touched_at": None, "exit_price_nominal": float(close),
                "high_water_mark": hwm,
            })
            return result

    result["high_water_mark"] = hwm
    return result

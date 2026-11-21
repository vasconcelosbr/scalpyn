"""MTF_BTC_TIMEFRAME_V1: frozen evidence; classification remains owned by trend()."""
from .mtf_chart_engine import TF_SECONDS, iso, trend


def frozen_frame(frame, tf, cutoff, cfg, *, self_context=False, error=None):
    candles = frame.get("candles", [])
    points = frame.get("pivots", [])
    last = candles[-1] if candles else None
    stale = bool(last and cutoff - (last["time"] + TF_SECONDS[tf]) >
                 cfg.stale_tf_multiplier * TF_SECONDS[tf])
    # Preserve the original BTC self-context (it is exempt from its own veto).
    state = trend(points) if self_context or (last and not stale and not error) else None
    used = sorted([p for kind in ("H", "L") for p in
                   [p for p in points if p["kind"] == kind][-2:]],
                  key=lambda p: (p["time"], p["kind"]))
    return {
        "trend": state, "reason": error or ("NO_CLOSED_CANDLES" if not last else
                                          "DATA_STALE" if stale else None),
        "pivots_used": used,
        "reference_candle": None if last is None else {
            "open_at": iso(last["time"]), "close_at": iso(last["time"] + TF_SECONDS[tf]),
            "close": last["close"],
        },
        "window_start": iso(candles[0]["time"]) if candles else None,
        "candle_count": len(candles),
    }


async def load_btc_context(symbol, frames, cutoff, indicators, cfg, market_candles, prepared):
    readings = {}
    for tf in ("4h", "1h"):
        error = None
        if symbol == "BTC_USDT":
            frame = frames[tf]
        else:
            try:
                # Shared market cache and configured limit also serve BTC watchlist reads.
                raw = await market_candles("BTC_USDT", tf, cfg)
                frame = await prepared(raw, tf, cutoff, indicators, {"atr"}, cfg)
            except ValueError as exc:
                frame, error = {}, str(exc)
            except Exception as exc:
                # An unavailable observational timeframe must not break the active veto.
                if tf == cfg.btc_trend_timeframe:
                    raise
                frame, error = {}, type(exc).__name__
        readings[tf] = frozen_frame(frame, tf, cutoff, cfg,
                                   self_context=symbol == "BTC_USDT", error=error)
    return {
        "version": "mtf_btc_context_v1", "symbol": "BTC_USDT", "source": "gate.io",
        "market_type": "spot", "evaluated_at": iso(cutoff),
        "active_timeframe": cfg.btc_trend_timeframe,
        "veto_enabled": cfg.veto_filters.btc_downtrend,
        "exempt": symbol == "BTC_USDT",
        "pivot_left": cfg.pivot_left, "pivot_right": cfg.pivot_right,
        "candle_limits": {tf: cfg.candle_limits[tf] for tf in ("4h", "1h")},
        "frames": readings,
    }

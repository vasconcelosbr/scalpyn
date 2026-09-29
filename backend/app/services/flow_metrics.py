"""Single source of truth for Pump Monitor flow, price-normalised and book measures.

Every function here is pure (no I/O) so the live Pump Monitor and the
``pump_radar_research`` backtest compute identical values from identical
inputs. Nothing in the front-end or in another service may re-implement
these formulas.

Null contract: absence of data is never converted to zero. Every metric
returns ``{"value": <number|None>, "reason": <str|None>}``; ``value`` is
``None`` exactly when ``reason`` explains why (partial bucket, insufficient
coverage, zero denominator, ...).

Units
-----
* Buckets carry buy/sell volume in base asset AND in quote (USDT). Quote is
  ``None`` when any trade in the bucket lacked a price (legacy buffer
  members written before the price field existed).
* ``volume_unit`` selects which of the two the flow metrics use.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

Metric = Dict[str, Any]


def ok(value: float, digits: int = 6) -> Metric:
    return {"value": round(float(value), digits), "reason": None}


def null(reason: str) -> Metric:
    return {"value": None, "reason": reason}


# ── Trade bucketing ──────────────────────────────────────────────────────────

def bucket_trades(
    trades: Iterable[Dict[str, Any]],
    *,
    bucket_seconds: int = 60,
) -> Dict[int, Dict[str, Any]]:
    """Aggregate trades into non-overlapping buckets keyed by bucket start (ms).

    ``trades`` items: ``{"trade_id", "side", "amount", "price", "ts_ms"}``.
    Duplicates (same ``trade_id``) are counted once; trades without an id
    are deduplicated by their full (ts, side, amount, price) identity.
    Only ``side in {"buy", "sell"}`` and ``amount >= 0`` are accepted.
    """
    size_ms = int(bucket_seconds) * 1000
    seen: set = set()
    ordered = sorted(
        (t for t in trades if t.get("ts_ms") is not None),
        key=lambda t: (float(t["ts_ms"]), str(t.get("trade_id") or "")),
    )
    buckets: Dict[int, Dict[str, Any]] = {}
    for trade in ordered:
        side = trade.get("side")
        try:
            amount = float(trade.get("amount"))
            ts_ms = float(trade["ts_ms"])
        except (TypeError, ValueError):
            continue
        if side not in ("buy", "sell") or amount < 0:
            continue
        trade_id = trade.get("trade_id")
        identity = ("id", str(trade_id)) if trade_id is not None else (
            "raw", ts_ms, side, amount, trade.get("price"))
        if identity in seen:
            continue
        seen.add(identity)
        price = trade.get("price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None

        start = int(ts_ms // size_ms) * size_ms
        b = buckets.get(start)
        if b is None:
            b = buckets[start] = {
                "bucket_start_ms": start,
                "buy_base": 0.0, "sell_base": 0.0,
                "buy_quote": 0.0, "sell_quote": 0.0,
                "quote_complete": True,
                "trade_count": 0,
                "first_trade_id": None, "last_trade_id": None,
                "open_price": None, "high_price": None,
                "low_price": None, "close_price": None,
            }
        b[f"{side}_base"] += amount
        if price is None:
            b["quote_complete"] = False
        else:
            b[f"{side}_quote"] += amount * price
            if b["open_price"] is None:
                b["open_price"] = price
            b["high_price"] = price if b["high_price"] is None else max(b["high_price"], price)
            b["low_price"] = price if b["low_price"] is None else min(b["low_price"], price)
            b["close_price"] = price
        b["trade_count"] += 1
        if trade_id is not None:
            if b["first_trade_id"] is None:
                b["first_trade_id"] = str(trade_id)
            b["last_trade_id"] = str(trade_id)
    for b in buckets.values():
        if not b.pop("quote_complete"):
            b["buy_quote"] = None
            b["sell_quote"] = None
    return buckets


def empty_bucket(start_ms: int) -> Dict[str, Any]:
    """A covered minute with no trades: real zero flow, not missing data."""
    return {
        "bucket_start_ms": int(start_ms),
        "buy_base": 0.0, "sell_base": 0.0, "buy_quote": 0.0, "sell_quote": 0.0,
        "trade_count": 0, "first_trade_id": None, "last_trade_id": None,
        "open_price": None, "high_price": None, "low_price": None, "close_price": None,
    }


def materialize_buckets(
    traded: Dict[int, Dict[str, Any]],
    minutes: Iterable[int],
    *,
    covered_from_ms: Optional[float],
    source: str,
    gap_reason: Optional[str] = None,
    alive_slots: Optional[set] = None,
    slot_seconds: int = 10,
    bucket_seconds: int = 60,
) -> List[Dict[str, Any]]:
    """Turn traded buckets into one row per closed minute with coverage flags.

    A minute is complete only when the source is known to hold every trade
    of it: it starts at/after ``covered_from_ms`` and, for the WebSocket
    path (``alive_slots`` given), the stream delivered frames in every
    ``slot_seconds`` slot of that minute. A complete minute without trades
    is a real zero-flow bucket; anything else is ``partial`` with a reason.
    """
    rows = []
    for start in minutes:
        start = int(start)
        reason = None
        if covered_from_ms is None or start < covered_from_ms:
            reason = gap_reason or "not_covered"
        elif alive_slots is not None:
            first = start // 1000
            slots = range(first, first + bucket_seconds, slot_seconds)
            if not all(s in alive_slots for s in slots):
                reason = "ws_gap"
        bucket = traded.get(start)
        if bucket is None:
            bucket = empty_bucket(start) if reason is None else {
                **empty_bucket(start),
                "buy_base": None, "sell_base": None, "buy_quote": None, "sell_quote": None,
                "trade_count": None,
            }
        rows.append({**bucket, "partial": reason is not None, "gap_reason": reason, "source": source})
    return rows


# ── Flow metrics ─────────────────────────────────────────────────────────────

def _sides(bucket: Dict[str, Any], unit: str) -> tuple[Optional[float], Optional[float]]:
    b = bucket.get(f"buy_{unit}")
    s = bucket.get(f"sell_{unit}")
    if b is None or s is None:
        return None, None
    return float(b), float(s)


def _usable(bucket: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the reason a bucket cannot be used, or None when it is usable."""
    if bucket is None:
        return "missing_bucket"
    if bucket.get("partial"):
        return str(bucket.get("gap_reason") or "partial_bucket")
    return None


def delta_norm(bucket: Optional[Dict[str, Any]], unit: str = "quote") -> Metric:
    """(B − S) / (B + S) of one bucket. Range [-1, 1]."""
    reason = _usable(bucket)
    if reason:
        return null(reason)
    b, s = _sides(bucket, unit)
    if b is None:
        return null(f"{unit}_unavailable")
    total = b + s
    if total <= 0:
        return null("no_trades")
    return ok((b - s) / total)


def window(
    buckets: Dict[int, Dict[str, Any]],
    *,
    end_ms: int,
    count: int,
    bucket_seconds: int = 60,
) -> List[Optional[Dict[str, Any]]]:
    """The ``count`` consecutive buckets ending at (and including) ``end_ms``.

    Missing minutes are returned as ``None`` — never synthesised — so the
    callers' coverage checks see them as gaps.
    """
    size = int(bucket_seconds) * 1000
    first = int(end_ms) - (int(count) - 1) * size
    return [buckets.get(first + i * size) for i in range(int(count))]


def coverage_pct(win: Sequence[Optional[Dict[str, Any]]]) -> float:
    if not win:
        return 0.0
    good = sum(1 for b in win if _usable(b) is None)
    return round(good / len(win) * 100.0, 2)


def _meta(win, source_default="ws") -> Dict[str, Any]:
    sources = sorted({str(b.get("source") or source_default) for b in win if b})
    return {
        "coverage_pct": coverage_pct(win),
        "partial_buckets": sum(1 for b in win if b is not None and b.get("partial")),
        "missing_buckets": sum(1 for b in win if b is None),
        "source": "+".join(sources) if sources else None,
    }


def cvd(win, unit: str = "quote", min_coverage_pct: float = 80.0) -> Metric:
    """Σ(B − S) over non-overlapping usable buckets of the window."""
    meta = _meta(win)
    if meta["coverage_pct"] < min_coverage_pct:
        return {**null("insufficient_coverage"), **meta}
    total = 0.0
    for bucket in win:
        if _usable(bucket) is not None:
            continue
        b, s = _sides(bucket, unit)
        if b is None:
            return {**null(f"{unit}_unavailable"), **meta}
        total += b - s
    return {**ok(total, 8), **meta}


def cvd_slope(win, unit: str = "quote", min_coverage_pct: float = 80.0) -> Metric:
    """OLS slope of the running CVD over the window, divided by mean bucket volume.

    Dimensionless: "CVD gained per bucket, in units of an average bucket's
    traded volume". Gaps keep their time position (x is the bucket index)
    and do not add to the running sum.
    """
    meta = _meta(win)
    if meta["coverage_pct"] < min_coverage_pct:
        return {**null("insufficient_coverage"), **meta}
    xs, ys, volumes = [], [], []
    running = 0.0
    for index, bucket in enumerate(win):
        if _usable(bucket) is not None:
            continue
        b, s = _sides(bucket, unit)
        if b is None:
            return {**null(f"{unit}_unavailable"), **meta}
        running += b - s
        xs.append(float(index))
        ys.append(running)
        volumes.append(b + s)
    if len(xs) < 2:
        return {**null("insufficient_points"), **meta}
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x == 0:
        return {**null("insufficient_points"), **meta}
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    mean_volume = sum(volumes) / len(volumes)
    if mean_volume <= 0:
        return {**null("no_trades"), **meta}
    return {**ok(slope / mean_volume), **meta}


def buy_persistence(win, unit: str = "quote", min_coverage_pct: float = 80.0,
                    side: str = "buy") -> Metric:
    """Buckets with B > S divided by K (= len(window)). Gaps are not wins.

    ``side="sell"`` counts buckets with S > B instead (sell persistence).
    """
    meta = _meta(win)
    if meta["coverage_pct"] < min_coverage_pct:
        return {**null("insufficient_coverage"), **meta}
    wins = 0
    for bucket in win:
        if _usable(bucket) is not None:
            continue
        b, s = _sides(bucket, unit)
        if b is None:
            return {**null(f"{unit}_unavailable"), **meta}
        if (b > s) if side == "buy" else (s > b):
            wins += 1
    return {**ok(wins / len(win)), **meta}


def volume_acceleration(prev_bucket, last_bucket, unit: str = "quote") -> Metric:
    """vol_t / vol_{t-1} − 1 for two consecutive usable buckets."""
    for bucket in (prev_bucket, last_bucket):
        reason = _usable(bucket)
        if reason:
            return null(reason)
    pb, ps = _sides(prev_bucket, unit)
    lb, ls = _sides(last_bucket, unit)
    if pb is None or lb is None:
        return null(f"{unit}_unavailable")
    previous = pb + ps
    if previous <= 0:
        return null("zero_denominator")
    return ok((lb + ls) / previous - 1.0)


def flow_change(prev_bucket, last_bucket, unit: str = "quote") -> Metric:
    """delta_norm_t − delta_norm_{t-1}."""
    previous = delta_norm(prev_bucket, unit)
    current = delta_norm(last_bucket, unit)
    if previous["value"] is None:
        return null(previous["reason"])
    if current["value"] is None:
        return null(current["reason"])
    return ok(current["value"] - previous["value"])


def window_delta_norm(win, unit: str = "quote", min_coverage_pct: float = 80.0) -> Metric:
    """(ΣB − ΣS) / (ΣB + ΣS) over the usable buckets of a window."""
    meta = _meta(win)
    if meta["coverage_pct"] < min_coverage_pct:
        return {**null("insufficient_coverage"), **meta}
    buy = sell = 0.0
    for bucket in win:
        if _usable(bucket) is not None:
            continue
        b, s = _sides(bucket, unit)
        if b is None:
            return {**null(f"{unit}_unavailable"), **meta}
        buy += b
        sell += s
    if buy + sell <= 0:
        return {**null("no_trades"), **meta}
    return {**ok((buy - sell) / (buy + sell)), **meta}


def window_close_prices(win) -> tuple[Optional[float], Optional[float]]:
    """First and last traded close inside the window's usable buckets."""
    closes = [b["close_price"] for b in win
              if _usable(b) is None and b.get("close_price") is not None]
    if not closes:
        return None, None
    return float(closes[0]), float(closes[-1])


# ── Price measures normalised by ATR ─────────────────────────────────────────

def _atr_guard(atr: Any) -> Optional[str]:
    if atr is None:
        return "atr_unavailable"
    try:
        atr = float(atr)
    except (TypeError, ValueError):
        return "atr_unavailable"
    if atr <= 0:
        return "atr_zero"
    return None


def price_extension_atr(price: Any, reference: Any, atr: Any) -> Metric:
    """(price − reference) / ATR."""
    reason = _atr_guard(atr)
    if reason:
        return null(reason)
    if price is None or reference is None:
        return null("price_or_reference_unavailable")
    return ok((float(price) - float(reference)) / float(atr))


def price_progress_atr(close_start: Any, close_end: Any, atr: Any) -> Metric:
    """(close_end − close_start) / ATR over the flow window."""
    reason = _atr_guard(atr)
    if reason:
        return null(reason)
    if close_start is None or close_end is None:
        return null("window_prices_unavailable")
    return ok((float(close_end) - float(close_start)) / float(atr))


def breakout_distance_atr(price: Any, level: Any, atr: Any) -> Metric:
    """(price − frozen breakout level) / ATR."""
    reason = _atr_guard(atr)
    if reason:
        return null(reason)
    if price is None or level is None:
        return null("level_unavailable")
    return ok((float(price) - float(level)) / float(atr))


def breakout_hold_ratio(win, level: Any, tolerance_pct: float = 0.0,
                        min_coverage_pct: float = 80.0) -> Metric:
    """Usable buckets closing at/above the frozen level / expected buckets."""
    if level is None:
        return null("no_active_breakout")
    if not win:
        return null("breakout_just_started")
    meta = _meta(win)
    if meta["coverage_pct"] < min_coverage_pct:
        return {**null("insufficient_coverage"), **meta}
    floor = float(level) * (1.0 - float(tolerance_pct) / 100.0)
    held = sum(1 for b in win
               if _usable(b) is None and b.get("close_price") is not None
               and float(b["close_price"]) >= floor)
    return {**ok(held / len(win)), **meta}


def upper_wick_ratio(open_: Any, high: Any, low: Any, close: Any) -> Metric:
    """(high − max(open, close)) / (high − low). Undefined when high == low."""
    try:
        o, h, l, c = (float(v) for v in (open_, high, low, close))
    except (TypeError, ValueError):
        return null("candle_unavailable")
    span = h - l
    if span <= 0:
        return null("zero_range")
    return ok((h - max(o, c)) / span)


# ── Order book: depth bands and simulated slippage ───────────────────────────

def _levels(side: Sequence) -> List[tuple[float, float]]:
    out = []
    for level in side or []:
        try:
            price, qty = float(level[0]), float(level[1])
        except (TypeError, ValueError, IndexError):
            continue
        if price > 0 and qty >= 0:
            out.append((price, qty))
    return out


def book_mid(bids: Sequence, asks: Sequence) -> Optional[float]:
    b, a = _levels(bids), _levels(asks)
    if not b or not a:
        return None
    return (b[0][0] + a[0][0]) / 2.0


def band_depth_quote(levels: Sequence, mid: float, band_pct: float, side: str,
                     book_complete: bool = False) -> Metric:
    """Σ price × qty of the levels within ``band_pct`` of mid on one side.

    When the returned book ends inside the band and may have been truncated
    by the request limit, the true depth is unknown, so the result is null —
    never a lower bound disguised as the real value. ``book_complete`` (the
    exchange returned fewer levels than requested) means the whole side was
    received, so the sum is exact.
    """
    parsed = _levels(levels)
    if not parsed or mid is None or mid <= 0:
        return null("book_unavailable")
    if side == "bid":
        edge = mid * (1.0 - band_pct / 100.0)
        inside = [(p, q) for p, q in parsed if p >= edge]
        truncated = parsed[-1][0] >= edge
    else:
        edge = mid * (1.0 + band_pct / 100.0)
        inside = [(p, q) for p, q in parsed if p <= edge]
        truncated = parsed[-1][0] <= edge
    if truncated and not book_complete:
        return null("insufficient_depth")
    return ok(sum(p * q for p, q in inside), 8)


def depth_imbalance(bid_depth: Metric, ask_depth: Metric) -> Metric:
    for m in (bid_depth, ask_depth):
        if m["value"] is None:
            return null(m["reason"])
    total = bid_depth["value"] + ask_depth["value"]
    if total <= 0:
        return null("zero_denominator")
    return ok((bid_depth["value"] - ask_depth["value"]) / total)


def estimated_slippage_pct(levels: Sequence, mid: Optional[float],
                           notional_quote: float, side: str) -> Metric:
    """Walk the book until ``notional_quote`` is filled.

    buy consumes asks, sell consumes bids. Slippage is the distance between
    the execution VWAP and mid, relative to mid, in percent, always >= 0 for
    a normal book. Fees are not included.
    """
    parsed = _levels(levels)
    if not parsed or mid is None or mid <= 0:
        return null("book_unavailable")
    if notional_quote is None or float(notional_quote) <= 0:
        return null("notional_not_configured")
    remaining = float(notional_quote)
    spent = base = 0.0
    for price, qty in parsed:
        take_quote = min(remaining, price * qty)
        spent += take_quote
        base += take_quote / price
        remaining -= take_quote
        if remaining <= 1e-12:
            break
    if remaining > 1e-9 or base <= 0:
        return null("insufficient_depth")
    vwap = spent / base
    if side == "buy":
        return ok((vwap - mid) / mid * 100.0)
    return ok((mid - vwap) / mid * 100.0)

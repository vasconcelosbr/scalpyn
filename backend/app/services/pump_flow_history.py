"""Long-lived 5-minute flow and perpetual history (Pump Monitor v1.16).

``flow_buckets_1m`` keeps only ``flow.retention_days`` (2) and the perpetual stats
live only in Redis, so no model could ever learn from order flow or derivatives
beyond a couple of days. Every cycle this module upserts:

- ``pump_flow_5m``: spot taker flow per symbol per 5-minute bucket (open time),
  aggregated from the 1-minute buckets of the last two closed 5-minute windows
  (the second pass picks up late minute buckets). ``minutes`` = 1-minute buckets
  found, ``partial_minutes`` = of those, flagged partial. Nothing is invented for
  missing minutes.
- ``pump_perp_stats_5m``: Gate ``contract_stats`` rows exactly as fetched by the
  cycle (``stat_time`` = Gate ``time``). Kept apart from spot flow on purpose: when
  these become features, each source is aligned explicitly to the decision time
  (only rows with ``stat_time`` <= decision − interval), never by guessing.

Collection only: no model reads these tables yet. Failures never touch the cycle.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

FLOW_5M_SQL = """
    INSERT INTO pump_flow_5m (symbol, bucket_start, buy_quote, sell_quote, trade_count,
                              minutes, partial_minutes, computed_at)
    SELECT symbol,
           to_timestamp(floor(extract(epoch FROM bucket_start) / CAST(:step AS integer)) * CAST(:step AS integer)) AS b,
           sum(buy_quote), sum(sell_quote), sum(trade_count),
           count(*), count(*) FILTER (WHERE partial), now()
      FROM flow_buckets_1m
     WHERE symbol = ANY(CAST(:s AS text[])) AND bucket_start >= :lo AND bucket_start < :hi
     GROUP BY symbol, b
    ON CONFLICT (symbol, bucket_start) DO UPDATE SET
        buy_quote = EXCLUDED.buy_quote, sell_quote = EXCLUDED.sell_quote,
        trade_count = EXCLUDED.trade_count, minutes = EXCLUDED.minutes,
        partial_minutes = EXCLUDED.partial_minutes, computed_at = now()
"""

PERP_SQL = """
    INSERT INTO pump_perp_stats_5m (symbol, stat_time, stat_interval, long_taker_size, short_taker_size,
        open_interest_usd, short_liq_usd, long_liq_usd, last_funding_rate, lsr_taker, mark_price, computed_at)
    SELECT r.s, to_timestamp(r.t), r.i, r.lts, r.sts, r.oi, r.sl, r.ll, r.f, r.lsr, r.mp, now()
      FROM jsonb_to_recordset(CAST(:batch AS jsonb))
        AS r(s text, t bigint, i text, lts double precision, sts double precision, oi double precision,
             sl double precision, ll double precision, f double precision, lsr double precision,
             mp double precision)
    ON CONFLICT (symbol, stat_time, stat_interval) DO UPDATE SET
        long_taker_size = EXCLUDED.long_taker_size, short_taker_size = EXCLUDED.short_taker_size,
        open_interest_usd = EXCLUDED.open_interest_usd, short_liq_usd = EXCLUDED.short_liq_usd,
        long_liq_usd = EXCLUDED.long_liq_usd, last_funding_rate = EXCLUDED.last_funding_rate,
        lsr_taker = EXCLUDED.lsr_taker, mark_price = EXCLUDED.mark_price, computed_at = now()
"""

_PERP_FIELDS = (("lts", "long_taker_size"), ("sts", "short_taker_size"), ("oi", "open_interest_usd"),
                ("sl", "short_liq_usd"), ("ll", "long_liq_usd"), ("f", "last_funding_rate"),
                ("lsr", "lsr_taker"), ("mp", "mark_price"))


def _num(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def flow_window(now_ms: int, step_seconds: int) -> Dict[str, datetime]:
    """Last two CLOSED buckets: [slot − 2·step, slot)."""
    slot = (int(now_ms) // 1000 // step_seconds) * step_seconds
    return {"lo": datetime.fromtimestamp(slot - 2 * step_seconds, timezone.utc),
            "hi": datetime.fromtimestamp(slot, timezone.utc)}


def perp_rows(derivatives: Optional[Dict[str, Optional[List[Dict[str, Any]]]]], interval: str) -> List[Dict[str, Any]]:
    """Batch for ``PERP_SQL``: symbols without a perpetual ([]) or a failed fetch (None) add nothing."""
    out = []
    for symbol, rows in sorted((derivatives or {}).items()):
        for r in rows or []:
            t = _num(r.get("time"))
            if t is None:
                continue
            out.append({"s": symbol, "t": int(t), "i": interval,
                        **{k: _num(r.get(src)) for k, src in _PERP_FIELDS}})
    return out


async def write(db, symbols: List[str], derivatives: Optional[Dict[str, Any]], now_ms: int,
                cfg: Dict[str, Any], interval: str, rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    import json
    from sqlalchemy import text
    step = int(cfg["step_seconds"])
    written = {"flow": 0, "perp": 0, "book": 0}
    if symbols:
        res = await db.execute(text(FLOW_5M_SQL), {"s": sorted(symbols), "step": step, **flow_window(now_ms, step)})
        written["flow"] = res.rowcount or 0
    batch = perp_rows(derivatives, interval)
    if batch:
        res = await db.execute(text(PERP_SQL), {"batch": json.dumps(batch, allow_nan=False)})
        written["perp"] = res.rowcount or 0
    book = book_rows(rows, now_ms, step)
    if book:
        res = await db.execute(text(BOOK_SQL), {"batch": json.dumps(book, allow_nan=False)})
        written["book"] = res.rowcount or 0
    return written


# v1.18: order-book state per 5-minute bucket (the LAST cycle snapshot inside the bucket;
# ``observed_at`` = cycle time, ``computed_at`` = receipt). Kept for liquidity / execution
# features; pump_research_minute holds these only for its 30-day retention.
BOOK_FIELDS = (("sp", "spread_pct"), ("bd", "bid_depth_usdt_1pct"), ("ad", "ask_depth_usdt_1pct"),
               ("im", "depth_imbalance_1pct"), ("sb", "estimated_slippage_buy_pct"),
               ("ss", "estimated_slippage_sell_pct"))

BOOK_SQL = """
    INSERT INTO pump_book_5m (symbol, bucket_start, observed_at, spread_pct, bid_depth_1pct_usdt,
        ask_depth_1pct_usdt, depth_imbalance_1pct, slippage_buy_pct, slippage_sell_pct, computed_at)
    SELECT r.s, to_timestamp(r.b), to_timestamp(r.o), r.sp, r.bd, r.ad, r.im, r.sb, r.ss, now()
      FROM jsonb_to_recordset(CAST(:batch AS jsonb))
        AS r(s text, b bigint, o double precision, sp double precision, bd double precision,
             ad double precision, im double precision, sb double precision, ss double precision)
    ON CONFLICT (symbol, bucket_start) DO UPDATE SET
        observed_at = EXCLUDED.observed_at, spread_pct = EXCLUDED.spread_pct,
        bid_depth_1pct_usdt = EXCLUDED.bid_depth_1pct_usdt, ask_depth_1pct_usdt = EXCLUDED.ask_depth_1pct_usdt,
        depth_imbalance_1pct = EXCLUDED.depth_imbalance_1pct, slippage_buy_pct = EXCLUDED.slippage_buy_pct,
        slippage_sell_pct = EXCLUDED.slippage_sell_pct, computed_at = now()
     WHERE pump_book_5m.observed_at <= EXCLUDED.observed_at
"""


def book_rows(rows: Optional[List[Dict[str, Any]]], now_ms: int, step_seconds: int) -> List[Dict[str, Any]]:
    """Batch for ``BOOK_SQL`` from the cycle rows (``indicators[field].value``); the bucket is
    the one OPEN at ``now_ms``, so its final value is the last snapshot before it closes.
    Assets with no book value at all add nothing; individual missing fields stay NULL."""
    now_s = int(now_ms) / 1000.0
    bucket = (int(now_s) // step_seconds) * step_seconds
    out = []
    for row in rows or []:
        cells = row.get("indicators") or {}
        vals = {k: _num((cells.get(src) or {}).get("value")) for k, src in BOOK_FIELDS}
        if all(v is None for v in vals.values()):
            continue
        out.append({"s": row["symbol"], "b": bucket, "o": now_s, **vals})
    return out


COVERAGE_SQL = """
    SELECT 'flow' AS source, count(DISTINCT symbol) AS symbols, count(*) AS rows,
           min(bucket_start) AS first, max(bucket_start) AS last FROM pump_flow_5m
    UNION ALL
    SELECT 'perp', count(DISTINCT symbol), count(*), min(stat_time), max(stat_time) FROM pump_perp_stats_5m
    UNION ALL
    SELECT 'book', count(DISTINCT symbol), count(*), min(bucket_start), max(bucket_start) FROM pump_book_5m
"""


def retention_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=max(1, int(days)))

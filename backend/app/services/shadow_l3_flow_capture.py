"""Durable handoff of subscribed public spot trades. Never handles orders."""
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

WATCHED = "shadow_l3_flow:watched"
STREAM = "shadow_l3_flow:pending"


def normalize_trade(trade, available_at):
    from ..exchange_adapters.gate_adapter import GateAdapter
    try:
        symbol = GateAdapter._normalize_symbol(trade.get("currency_pair") or trade.get("s"))
        amount = Decimal(str(trade["amount"]))
        raw_time = trade.get("create_time_ms")
        ts = float(raw_time) / 1000 if raw_time is not None else float(trade["create_time"])
        at = datetime.fromtimestamp(ts, timezone.utc)
        if not amount.is_finite() or amount <= 0 or trade.get("id") is None or trade.get("side") not in ("buy", "sell") or at > available_at:
            return None
        return dict(exchange="gate.io", market_type="spot", symbol=symbol,
                    trade_id=str(trade["id"]), occurred_at=at.isoformat(),
                    available_at=available_at.isoformat(), side=trade["side"], amount=str(amount))
    except (KeyError, ValueError, TypeError, OverflowError, InvalidOperation):
        return None


async def enqueue_trades(redis, trades):
    now = datetime.now(timezone.utc)
    watched = {x.decode() if isinstance(x, bytes) else x for x in await redis.smembers(WATCHED)}
    if not watched:
        return
    async with redis.pipeline(transaction=False) as pipe:
        for trade in trades:
            row = normalize_trade(trade, now)
            if row and row["symbol"] in watched:
                # No lossy MAXLEN. Removed only after the DB transaction commits.
                pipe.xadd(STREAM, {"payload": json.dumps(row)})
        await pipe.execute()


async def drain(db, redis, count=2000):
    from sqlalchemy import text
    messages = await redis.xrange(STREAM, count=count)
    ids = []
    rows = []
    for key, payload in messages:
        data = json.loads(payload.get(b"payload") or payload.get("payload"))
        data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
        data["available_at"] = datetime.fromisoformat(data["available_at"])
        data["amount"] = Decimal(data["amount"])
        rows.append(data)
        ids.append(key)
    if rows:
        await db.execute(text("""
            INSERT INTO shadow_l3_flow_trades
                (exchange,market_type,symbol,trade_id,occurred_at,available_at,side,amount)
            VALUES (:exchange,:market_type,:symbol,:trade_id,:occurred_at,:available_at,:side,:amount)
            ON CONFLICT DO NOTHING
        """), rows)
    await db.commit()
    if ids:
        await redis.xdel(STREAM, *ids)
    return len(ids)

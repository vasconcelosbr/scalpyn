"""Executable spot entry estimates; candle/metadata prices are references only.

This module never places orders. Gate's returned book and its original clocks
are frozen together; a cache hit cannot refresh either clock.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import math

VERSION = "gate_spot_entry_quote_v1"
# Gate API protocol limit, not a trading/risk threshold.
GATE_BOOK_LIMIT = 100


class EntryQuoteUnavailable(ValueError):
    pass


def _positive(value):
    try:
        number = Decimal(str(value))
        if number.is_finite() and number > 0:
            return number
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise EntryQuoteUnavailable("ENTRY_QUOTE_INVALID_NUMBER")


def _utc(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    raise EntryQuoteUnavailable("ENTRY_QUOTE_TIMESTAMP_MISSING")


def estimate_entry(book, *, symbol, amount_usdt, max_age_seconds, now):
    """Price the entire quote budget against asks, or refuse the estimate."""
    try:
        max_age = float(max_age_seconds)
    except (TypeError, ValueError):
        raise EntryQuoteUnavailable("ENTRY_QUOTE_AGE_UNCONFIGURED") from None
    if not math.isfinite(max_age) or max_age <= 0:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_AGE_UNCONFIGURED")
    if not isinstance(book, dict):
        raise EntryQuoteUnavailable("ENTRY_QUOTE_BOOK_UNAVAILABLE")
    observed = _utc(book.get("_observed_at"))
    requested = _utc(book.get("_requested_at"))
    try:
        updated = datetime.fromtimestamp(float(book["update"]) / 1000, timezone.utc)
        generated = datetime.fromtimestamp(float(book["current"]) / 1000, timezone.utc)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        raise EntryQuoteUnavailable("ENTRY_QUOTE_TIMESTAMP_MISSING") from None
    if updated > generated or requested > observed or observed > now:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_FUTURE_TIMESTAMP")
    # Gate and the host have independent clocks. Keep raw exchange times and
    # anchor their elapsed age at the ORIGINAL request, never at a cache hit.
    # Including the complete round trip is conservative about network delay.
    # Comparing a Gate timestamp directly with host UTC rejects valid books
    # when the exchange clock is ahead (observed in the live UNI read).
    source_at = requested - (generated - updated)
    age = (now - source_at).total_seconds()
    if age > max_age:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_STALE")
    budget = _positive(amount_usdt)
    try:
        asks = [(_positive(p), _positive(q)) for p, q in book["asks"]]
        bids = [(_positive(p), _positive(q)) for p, q in book["bids"]]
    except (KeyError, TypeError, ValueError):
        raise EntryQuoteUnavailable("ENTRY_QUOTE_INVALID_BOOK") from None
    if not asks or not bids:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_EMPTY_BOOK")
    asks.sort()
    best_bid = max(p for p, _ in bids)
    if best_bid >= asks[0][0]:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_CROSSED_BOOK")
    remaining, quantity = budget, Decimal(0)
    consumed = []
    for price, size in asks:
        cost = min(remaining, price * size)
        taken = cost / price
        quantity += taken
        remaining -= cost
        consumed.append([str(price), str(taken)])
        if remaining == 0:
            break
    if remaining > 0:
        raise EntryQuoteUnavailable("ENTRY_QUOTE_INSUFFICIENT_DEPTH")
    return {
        "contract_version": VERSION, "source": "gate_spot_order_book",
        "exchange": "gate.io", "market_type": "spot", "symbol": symbol,
        "side": "buy", "value": float(budget / quantity),
        "amount_usdt": str(budget), "estimated_quantity": str(quantity),
        "source_at": source_at.isoformat(), "response_at": generated.isoformat(),
        "exchange_source_at": updated.isoformat(),
        "clock_semantics": "EXCHANGE_AGE_PLUS_REQUEST_ROUNDTRIP",
        "exchange_clock_offset_seconds": (generated - observed).total_seconds(),
        "requested_at": requested.isoformat(),
        "available_at": observed.isoformat(), "entry_at": now.isoformat(),
        "age_seconds": age,
        "best_bid": str(best_bid), "best_ask": str(asks[0][0]),
        "consumed_asks": consumed, "semantics": "ASK_DEPTH_VWAP_ESTIMATE",
        "realized": False,
    }


async def capture_entry_quote(*, symbol, amount_usdt, max_age_seconds):
    from .market_data_service import MarketDataService
    book = await MarketDataService().fetch_raw_orderbook(symbol, GATE_BOOK_LIMIT)
    return estimate_entry(book, symbol=symbol, amount_usdt=amount_usdt,
                          max_age_seconds=max_age_seconds, now=datetime.now(timezone.utc))


def require_current_quote(quote, *, now, max_age_seconds):
    """Recheck after DB/lock work, immediately before persisting the entry."""
    source_at = _utc(quote.get("source_at"))
    age = (now - source_at).total_seconds()
    if age < 0 or age > float(max_age_seconds):
        raise EntryQuoteUnavailable("ENTRY_QUOTE_EXPIRED_BEFORE_INSERT")

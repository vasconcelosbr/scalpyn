"""Trade Monitor Service — Module 3.

Monitors all open ``trade_tracking`` rows and closes them when a
TP / SL / timeout condition is met.

Responsibilities
----------------
* Fetch up to 200 open trades per run.
* Batch-fetch current prices from the Gate.io public ticker API
  (single HTTP request, cached per symbol for the duration of the run).
* Apply per-trade exit logic (long / short × TP / SL / timeout).
* Write ``exit_price``, ``exit_time``, ``outcome``, ``pnl_pct``, and
  ``holding_seconds`` to ``trade_tracking``.
* Mirror ``outcome``, ``pnl_pct``, and ``holding_seconds`` to the
  matching ``decisions_log`` row (if one exists).

Invariants
----------
* Does NOT open trades.
* Does NOT modify pipeline_scan, score_engine, block_engine, execute_buy,
  indicators, or the Celery execution flow.
* Does NOT use the authenticated Gate.io API (that is reconciliation).
* Price cache is local to each run — no cross-run stale prices.
* Each trade is processed in a SAVEPOINT so one failure never aborts the
  rest of the batch.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade_tracking import TradeTracking
from ..models.backoffice import DecisionLog

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

# Batch size: maximum open trades processed per run.
_BATCH_SIZE: int = 200

# Default trade timeout in seconds (24 h).  A trade that has been open
# longer than this is closed with outcome = "timeout" regardless of price.
# The value is intentionally conservative; shorter timeouts should be
# encoded as stop_price levels by the decision layer.
_DEFAULT_TIMEOUT_SECONDS: int = 86_400

# Gate.io public tickers endpoint — no authentication required.
_GATE_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"

# HTTP timeout for the ticker batch request.
_HTTP_TIMEOUT: float = 10.0

# Retry attempts for the price fetch.
_PRICE_FETCH_RETRIES: int = 2


# ── Price fetching ────────────────────────────────────────────────────────────


async def _fetch_price_map(symbols: set[str]) -> dict[str, float]:
    """Return {symbol: last_price} for all requested symbols.

    Executes a single call to the Gate.io public tickers endpoint and
    filters the response to the set of symbols we need.  ``symbol`` is
    expected in ``BTC_USDT`` (underscore) format as stored in
    ``trade_tracking.symbol``.

    Returns an empty dict on failure so callers can skip gracefully.
    """
    if not symbols:
        return {}

    price_map: dict[str, float] = {}
    last_exc: Exception | None = None

    for attempt in range(1, _PRICE_FETCH_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(_GATE_TICKERS_URL)
                resp.raise_for_status()
                tickers: list[dict[str, Any]] = resp.json()

            for ticker in tickers:
                pair: str = ticker.get("currency_pair", "")
                if pair not in symbols:
                    continue
                last = ticker.get("last")
                if last is not None:
                    try:
                        price_map[pair] = float(last)
                    except (TypeError, ValueError):
                        pass

            logger.debug(
                "[TradeMonitor] price fetch attempt %d/%d — %d/%d symbols resolved",
                attempt,
                _PRICE_FETCH_RETRIES,
                len(price_map),
                len(symbols),
            )
            return price_map

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[TradeMonitor] price fetch attempt %d/%d failed: %s",
                attempt,
                _PRICE_FETCH_RETRIES,
                exc,
            )

    logger.error("[TradeMonitor] all price fetch attempts failed: %s", last_exc)
    return price_map


# ── Exit condition logic ──────────────────────────────────────────────────────


def _check_exit_conditions(
    trade: TradeTracking,
    price: float,
    now: datetime,
    timeout_seconds: int,
) -> str | None:
    """Return 'tp', 'sl', 'timeout', or None (still open).

    Timeout takes priority so we don't misclassify a stale trade as TP/SL.
    """
    entry_time: datetime | None = trade.entry_time
    if entry_time is not None:
        # Normalise to UTC-aware for comparison.
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        elapsed = (now - entry_time).total_seconds()
        if elapsed > timeout_seconds:
            return "timeout"

    target: float | None = None
    try:
        if trade.target_price is not None:
            target = float(trade.target_price)
    except (TypeError, ValueError):
        pass

    stop: float | None = None
    try:
        if trade.stop_price is not None:
            stop = float(trade.stop_price)
    except (TypeError, ValueError):
        pass

    side: str = (trade.position_side or "long").lower()

    if side == "long":
        if target is not None and price >= target:
            return "tp"
        if stop is not None and price <= stop:
            return "sl"
    else:  # short
        if target is not None and price <= target:
            return "tp"
        if stop is not None and price >= stop:
            return "sl"

    return None


def _calc_pnl_pct(entry_price: float, exit_price: float, position_side: str) -> float:
    """Return percentage P&L (positive = profit)."""
    if entry_price <= 0:
        return 0.0
    if position_side.lower() == "long":
        return round(((exit_price - entry_price) / entry_price) * 100, 4)
    else:
        return round(((entry_price - exit_price) / entry_price) * 100, 4)


# ── Core service ──────────────────────────────────────────────────────────────


class TradeMonitorService:
    """Scan open trades and close those that meet TP / SL / timeout criteria."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def run(self, timeout_seconds: int = _DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
        """Run one monitoring cycle.

        Returns a summary dict for logging.
        """
        summary: dict[str, int] = {
            "open_trades": 0,
            "no_price": 0,
            "closed_tp": 0,
            "closed_sl": 0,
            "closed_timeout": 0,
            "errors": 0,
        }

        # ── 1. Load open trades ───────────────────────────────────────────────
        result = await self.session.execute(
            select(TradeTracking)
            .where(TradeTracking.status == "open")
            .limit(_BATCH_SIZE)
        )
        trades: list[TradeTracking] = list(result.scalars().all())
        summary["open_trades"] = len(trades)

        if not trades:
            logger.debug("[TradeMonitor] no open trades found")
            return summary

        logger.info("[TradeMonitor] scanning %d open trade(s)", len(trades))

        # ── 2. Batch-fetch prices (single HTTP call, cached per symbol) ───────
        symbols: set[str] = {t.symbol for t in trades}
        price_map = await _fetch_price_map(symbols)

        now = datetime.now(timezone.utc)

        # ── 3. Evaluate and close ─────────────────────────────────────────────
        for trade in trades:
            try:
                price: float | None = price_map.get(trade.symbol)

                # Determine outcome — timeout can trigger even without price.
                if price is None:
                    # Check timeout-only path; can't evaluate TP/SL without price.
                    outcome = None
                    if trade.entry_time is not None:
                        entry_time = trade.entry_time
                        if entry_time.tzinfo is None:
                            entry_time = entry_time.replace(tzinfo=timezone.utc)
                        if (now - entry_time).total_seconds() > timeout_seconds:
                            outcome = "timeout"

                    if outcome is None:
                        summary["no_price"] += 1
                        logger.debug(
                            "[TradeMonitor] no price for %s — skipping", trade.symbol
                        )
                        continue

                    # Timeout without price: use entry_price as exit_price proxy.
                    exit_price = float(trade.entry_price)
                else:
                    outcome = _check_exit_conditions(trade, price, now, timeout_seconds)
                    if outcome is None:
                        continue
                    exit_price = price

                await self._close_trade(trade, exit_price, outcome, now)

                summary[f"closed_{outcome}"] += 1

            except Exception as exc:
                summary["errors"] += 1
                logger.exception(
                    "[TradeMonitor] error processing trade %s (%s): %s",
                    trade.id,
                    trade.symbol,
                    exc,
                )

        logger.info(
            "[TradeMonitor] cycle complete — %s",
            summary,
        )
        return summary

    async def _close_trade(
        self,
        trade: TradeTracking,
        exit_price: float,
        outcome: str,
        now: datetime,
    ) -> None:
        """Write exit data to trade_tracking and mirror to decisions_log.

        Each close is wrapped in a SAVEPOINT so a DB failure here does not
        abort the outer transaction (which is still processing other trades).
        """
        entry_price = float(trade.entry_price)
        entry_time: datetime | None = trade.entry_time
        if entry_time is not None and entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        pnl_pct = _calc_pnl_pct(entry_price, exit_price, trade.position_side or "long")
        holding_secs: int | None = (
            int((now - entry_time).total_seconds()) if entry_time is not None else None
        )

        async with self.session.begin_nested():
            # ── Update trade_tracking ─────────────────────────────────────────
            await self.session.execute(
                update(TradeTracking)
                .where(TradeTracking.id == trade.id)
                .values(
                    status="closed",
                    exit_price=exit_price,
                    exit_time=now,
                    outcome=outcome,
                    pnl_pct=pnl_pct,
                    holding_seconds=holding_secs,
                )
            )

            # ── Mirror outcome to decisions_log (if linked) ───────────────────
            if trade.decision_id is not None:
                await self.session.execute(
                    update(DecisionLog)
                    .where(DecisionLog.id == trade.decision_id)
                    .values(
                        outcome=outcome,
                        pnl_pct=pnl_pct,
                        holding_seconds=holding_secs,
                    )
                )

        logger.info(
            "[TradeMonitor] closed trade %s symbol=%s outcome=%s pnl_pct=%.4f",
            trade.id,
            trade.symbol,
            outcome,
            pnl_pct,
        )

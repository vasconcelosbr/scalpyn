"""Trade Monitor Service — Module 3.

Monitors all open ``trade_tracking`` rows and closes them when a
TP / SL / timeout condition is met.

Responsibilities
----------------
* Fetch up to 200 open trades per run, locking rows with
  ``FOR UPDATE SKIP LOCKED`` to prevent double-closing when multiple
  Celery workers run concurrently.
* Batch-fetch current prices from the Gate.io public ticker API
  (single HTTP request per cycle — no per-symbol calls).
* Apply per-trade exit logic (long / short × TP / SL / timeout).
* Write ``exit_price``, ``exit_price_source``, ``exit_time``, ``outcome``,
  ``pnl_pct``, and ``holding_seconds`` to ``trade_tracking``.
* Mirror ``outcome``, ``pnl_pct``, and ``holding_seconds`` to the
  matching ``decisions_log`` row (if one exists).

exit_price_source values
------------------------
* ``'market'``   — Gate.io public ticker; estimated close price used by
                   the monitor for simulated and real trades alike until
                   actual fill reconciliation is implemented.
* ``'exchange'`` — reserved for future: actual fill price confirmed via
                   the authenticated Gate.io API.

Invariants
----------
* Does NOT open trades.
* Does NOT modify pipeline_scan, score_engine, block_engine, execute_buy,
  indicators, or the Celery execution flow.
* Does NOT use the authenticated Gate.io API (that is reconciliation).
* Price map is built once per run from a single HTTP call — no redundant
  per-symbol requests.
* Each trade is processed in a SAVEPOINT so one failure never aborts the
  rest of the batch.
* ``FOR UPDATE SKIP LOCKED`` on the batch SELECT prevents race conditions
  when two monitor workers overlap — each worker acquires a non-overlapping
  subset of open trades so the same row is never closed twice.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models.trade_tracking import TradeTracking
from ..models.backoffice import DecisionLog
from .order_flow_service import get_order_flow_data

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

# Batch size: maximum open trades processed per run.
_BATCH_SIZE: int = 200

# Default trade timeout in seconds (24 h).  Overridden at runtime via
# settings.TRADE_MONITOR_TIMEOUT_SECONDS which reads from the
# TRADE_MONITOR_TIMEOUT_SECONDS environment variable.
_DEFAULT_TIMEOUT_SECONDS: int = 86_400

# Gate.io public tickers endpoints — no authentication required.
_GATE_SPOT_TICKERS_URL = "https://api.gateio.ws/api/v4/spot/tickers"
_GATE_FUTURES_TICKERS_URL = "https://api.gateio.ws/api/v4/futures/usdt/tickers"

# HTTP timeout for the ticker batch request.
_HTTP_TIMEOUT: float = 10.0

# Retry attempts for the price fetch.
_PRICE_FETCH_RETRIES: int = 2

# Source label written to exit_price_source for ticker-based closes.
_PRICE_SOURCE_MARKET = "market"


# ── Price fetching ────────────────────────────────────────────────────────────


async def _fetch_tickers(url: str, pair_key: str) -> dict[str, float]:
    """Fetch a Gate.io tickers endpoint and return {symbol: last_price}.

    ``pair_key`` is the JSON field that contains the symbol/contract name:
    * ``'currency_pair'`` for spot tickers
    * ``'contract'`` for futures/usdt tickers

    Returns an empty dict on failure so callers can skip gracefully.
    """
    price_map: dict[str, float] = {}
    last_exc: Exception | None = None

    for attempt in range(1, _PRICE_FETCH_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                tickers: list[dict[str, Any]] = resp.json()

            for ticker in tickers:
                pair: str = ticker.get(pair_key, "")
                if not pair:
                    continue
                last = ticker.get("last")
                if last is not None:
                    try:
                        price_map[pair] = float(last)
                    except (TypeError, ValueError):
                        pass

            logger.debug(
                "[TradeMonitor] %s attempt %d/%d — %d symbols loaded",
                url,
                attempt,
                _PRICE_FETCH_RETRIES,
                len(price_map),
            )
            return price_map

        except Exception as exc:
            last_exc = exc
            logger.warning(
                "[TradeMonitor] %s attempt %d/%d failed: %s",
                url,
                attempt,
                _PRICE_FETCH_RETRIES,
                exc,
            )

    logger.error("[TradeMonitor] all fetch attempts for %s failed: %s", url, last_exc)
    return price_map


async def _fetch_price_maps(
    spot_symbols: set[str],
    futures_symbols: set[str],
) -> tuple[dict[str, float], dict[str, float]]:
    """Fetch spot and futures price maps concurrently.

    Only calls the respective endpoint when there are symbols of that type
    so that an all-spot workload never hits the futures API and vice versa.
    Returns ``(spot_price_map, futures_price_map)``; each map is an empty
    dict if the endpoint is skipped or fails, allowing graceful degradation.
    """
    if not spot_symbols and not futures_symbols:
        return {}, {}

    if spot_symbols and futures_symbols:
        spot_map, futures_map = await asyncio.gather(
            _fetch_tickers(_GATE_SPOT_TICKERS_URL, "currency_pair"),
            _fetch_tickers(_GATE_FUTURES_TICKERS_URL, "contract"),
        )
        return spot_map, futures_map

    if spot_symbols:
        return await _fetch_tickers(_GATE_SPOT_TICKERS_URL, "currency_pair"), {}

    return {}, await _fetch_tickers(_GATE_FUTURES_TICKERS_URL, "contract")


# ── Trade helpers ─────────────────────────────────────────────────────────────


def _is_futures(trade: TradeTracking) -> bool:
    """Return True when *trade* belongs to the futures market."""
    return (trade.market_type or "spot") == "futures"


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

        ``timeout_seconds`` — maximum holding time before a trade is closed
        with outcome = 'timeout'.  Defaults to ``_DEFAULT_TIMEOUT_SECONDS``
        (24 h) but should be overridden via ``settings.TRADE_MONITOR_TIMEOUT_SECONDS``
        so operators can tune it without a code deploy.

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

        # ── 1. Load open trades — FOR UPDATE SKIP LOCKED prevents two workers
        #       from processing the same row simultaneously.
        result = await self.session.execute(
            select(TradeTracking)
            .where(TradeTracking.status == "open")
            .with_for_update(skip_locked=True)
            .limit(_BATCH_SIZE)
        )
        trades: list[TradeTracking] = list(result.scalars().all())
        summary["open_trades"] = len(trades)

        if not trades:
            logger.debug("[TradeMonitor] no open trades found")
            return summary

        logger.info("[TradeMonitor] scanning %d open trade(s)", len(trades))

        # ── 2. Batch-fetch prices — one HTTP call per market type ─────────────
        spot_symbols: set[str] = set()
        futures_symbols: set[str] = set()
        for t in trades:
            if _is_futures(t):
                futures_symbols.add(t.symbol)
            else:
                spot_symbols.add(t.symbol)
        spot_price_map, futures_price_map = await _fetch_price_maps(spot_symbols, futures_symbols)

        now = datetime.now(timezone.utc)

        # ── 3. Evaluate and close ─────────────────────────────────────────────
        for trade in trades:
            try:
                is_futures = _is_futures(trade)
                # Select the correct price map based on the trade's market type.
                price: float | None = futures_price_map.get(trade.symbol) if is_futures else spot_price_map.get(trade.symbol)

                # Determine outcome — timeout can trigger even without price.
                if price is None:
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

                    # Timeout without price: use entry_price as the best available
                    # proxy (P&L will be 0 — better than skipping the close).
                    exit_price = float(trade.entry_price)
                else:
                    outcome = _check_exit_conditions(trade, price, now, timeout_seconds)
                    if outcome is None:
                        # ── Dynamic exit via order flow (additive, post-TP/SL) ──
                        # TP/SL/timeout always win — only reached when the legacy
                        # check returned None. Master switch defaults False so the
                        # path is dark until the operator opts in.
                        if settings.TRADE_MONITOR_EXIT_FLOW_ENABLED:
                            should_exit, reason = await self._check_exit_signals(trade, price)
                            if should_exit:
                                logger.info(
                                    "[TradeMonitor][%s] dynamic flow exit reason=%s price=%s",
                                    trade.symbol, reason, price,
                                )
                                flow_outcome = f"flow_{reason}"
                                await self._close_trade(
                                    trade, price, _PRICE_SOURCE_MARKET, flow_outcome, now,
                                )
                                summary["closed_flow"] = summary.get("closed_flow", 0) + 1
                        continue
                    exit_price = price

                await self._close_trade(trade, exit_price, _PRICE_SOURCE_MARKET, outcome, now)

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
        exit_price_source: str,
        outcome: str,
        now: datetime,
    ) -> None:
        """Write exit data to trade_tracking and mirror to decisions_log.

        ``exit_price_source`` labels the authority for the exit price:
        ``'market'`` (ticker estimate) or ``'exchange'`` (confirmed fill).

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
                    exit_price_source=exit_price_source,
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
            "[TradeMonitor] closed trade %s symbol=%s outcome=%s pnl_pct=%.4f source=%s",
            trade.id,
            trade.symbol,
            outcome,
            pnl_pct,
            exit_price_source,
        )

    # ── Dynamic exit via order flow (additive — TP/SL always wins) ───────────
    #
    # Why thresholds are read from ``settings`` (env vars) and NOT from
    # ``ConfigService``: ``ConfigService.get_config`` requires ``user_id``
    # (NOT NULL UUID) and ``TradeTracking`` rows have no ``user_id``/``pool_id``
    # column. Adding those would require a migration (out of scope for this
    # additive change). The env-var path mirrors the existing
    # ``TRADE_MONITOR_TIMEOUT_SECONDS`` pattern in this same service — same
    # "Zero Hardcode" guarantee (operators tune via env without a code deploy).
    #
    # Why ``order_flow_service`` is imported as a function and not injected:
    # ``get_order_flow_data`` is a module-level coroutine, not a class.
    # No DI surface to add to ``TradeMonitorService.__init__``.
    #
    # Side semantics:
    #   * LONG  → exit when sellers dominate (taker_ratio low, volume_delta
    #             very negative).
    #   * SHORT → exit when buyers dominate  (taker_ratio high, volume_delta
    #             very positive). Symmetric mirror of the LONG branch.
    async def _check_exit_signals(
        self,
        trade: TradeTracking,
        current_price: float,
    ) -> tuple[bool, str]:
        """Evaluate dynamic exit signals from live order flow.

        Returns ``(should_exit, reason)``. ``reason`` is a SHORT 2-char tag
        (``tb``/``ve``/``tu``/``vs``) so the final ``outcome=f"flow_{reason}"``
        (7 chars) fits the ``String(20)`` column on ``trade_tracking.outcome``
        and ``decisions_log.outcome``. Numeric diagnostics (ratio, dominance)
        are emitted via ``logger.info`` at the signal site, NOT embedded in
        the outcome string.

        Never raises: any failure is logged and returns ``(False, "flow_error")``
        so the legacy TP/SL/timeout path is never affected.
        """
        try:
            window  = int(settings.TRADE_MONITOR_EXIT_FLOW_WINDOW_SECONDS)
            max_age = int(settings.TRADE_MONITOR_EXIT_FLOW_MAX_AGE_SECONDS)
            bear_threshold  = float(settings.TRADE_MONITOR_EXIT_TAKER_BEAR_THRESHOLD)
            spike_threshold = float(settings.TRADE_MONITOR_EXIT_VOLUME_SPIKE_THRESHOLD)

            market_type = (trade.market_type or "spot").lower()
            side = (trade.position_side or "long").lower()

            flow = await get_order_flow_data(
                symbol=trade.symbol,
                window_seconds=window,
                market_type=market_type,
            )

            # ``get_order_flow_data`` always returns a dict; the guard is a
            # defensive belt-and-braces in case the contract changes.
            if not flow:
                return False, "flow_unavailable"

            taker_ratio = flow.get("taker_ratio")
            if taker_ratio is None:
                # Empty buffer + REST fallback empty → no signal.
                return False, "flow_empty"

            age = flow.get("data_age_seconds")
            if age is not None and age > max_age:
                return False, f"stale_{age:.0f}s"

            taker_buy  = flow.get("taker_buy_volume") or 0.0
            taker_sell = flow.get("taker_sell_volume") or 0.0
            volume_delta = flow.get("volume_delta") or 0.0
            total_vol = taker_buy + taker_sell

            # Volume-exhaustion dominance threshold derived from spike multiplier:
            # spike=2.0 → dominance > 0.5 (one side > 50% net of total).
            try:
                dominance_floor = 1.0 - (1.0 / spike_threshold)
            except ZeroDivisionError:
                dominance_floor = 0.5

            # Reason tags are intentionally short (<= 5 chars) so the final
            # outcome ``flow_<reason>`` fits the ``String(20)`` column on
            # ``trade_tracking.outcome`` and ``decisions_log.outcome``.
            # Numeric diagnostics (ratio, dominance) go into the INFO log
            # at the call-site, not into the outcome string.
            #   tb = taker bear        (LONG : sellers dominate taker flow)
            #   ve = volume exhaustion (LONG : net delta strongly negative)
            #   tu = taker bull/up     (SHORT: buyers  dominate taker flow)
            #   vs = volume squeeze    (SHORT: net delta strongly positive)
            if side == "long":
                if taker_ratio < bear_threshold:
                    logger.info(
                        "[TradeMonitor][%s] flow_tb signal taker_ratio=%.3f < %.3f",
                        trade.symbol, taker_ratio, bear_threshold,
                    )
                    return True, "tb"
                if total_vol > 0 and volume_delta < 0:
                    sell_dominance = abs(volume_delta) / total_vol
                    if sell_dominance > dominance_floor:
                        logger.info(
                            "[TradeMonitor][%s] flow_ve signal sell_dominance=%.2f > %.2f",
                            trade.symbol, sell_dominance, dominance_floor,
                        )
                        return True, "ve"
            else:  # short
                bull_threshold = 1.0 - bear_threshold
                if taker_ratio > bull_threshold:
                    logger.info(
                        "[TradeMonitor][%s] flow_tu signal taker_ratio=%.3f > %.3f",
                        trade.symbol, taker_ratio, bull_threshold,
                    )
                    return True, "tu"
                if total_vol > 0 and volume_delta > 0:
                    buy_dominance = abs(volume_delta) / total_vol
                    if buy_dominance > dominance_floor:
                        logger.info(
                            "[TradeMonitor][%s] flow_vs signal buy_dominance=%.2f > %.2f",
                            trade.symbol, buy_dominance, dominance_floor,
                        )
                        return True, "vs"

            return False, "flow_ok"

        except Exception as exc:
            logger.warning(
                "[TradeMonitor][%s] _check_exit_signals error: %r — ignoring",
                trade.symbol, exc,
            )
            return False, "flow_error"


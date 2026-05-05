"""Trade Reconciliation Service — Module 2.

Queries the Gate.io API (spot and futures) to detect real trade fills,
matches them against open ``trade_tracking`` rows (converting simulated entries
to real ones), and creates new ``trade_tracking`` rows for trades that
originated outside Scalpyn.

Invariants
----------
* Does NOT modify ``pipeline_scan``, ``score_engine``, ``block_engine``,
  ``execute_buy``, indicators, or the Celery execution flow.
* Gate.io is treated as the source of truth.
* Original ``entry_price`` (decision/signal price) is NEVER overwritten;
  the real fill price is stored in ``real_entry_price`` for slippage analysis.
* ``decisions_log.metrics`` JSONB is immutable — execution metadata is written
  to dedicated columns (``trade_executed``, ``execution_type``,
  ``execution_entry_price``, ``execution_entry_time``).
* The ``reconciled_gate_trades`` dedup table guarantees idempotency: a Gate
  fill is processed at most once even if the task fires multiple times within
  the same window.
* Each reconciliation run is scoped to a single active ExchangeConnection;
  multiple connections (multiple users) are processed in the same Celery task
  invocation but independently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.backoffice import DecisionLog
from ..models.trade_tracking import TradeTracking
from ..utils.encryption import decrypt

logger = logging.getLogger(__name__)

# How far back (in days) to fetch fills on each run.
_LOOKBACK_DAYS = 7
# Max fills per API call.
_FETCH_LIMIT = 100
# Maximum seconds between a Gate fill timestamp and a trade_tracking.entry_time
# to consider them the same trade.
_MATCH_WINDOW_S = 60
# Maximum relative price deviation (0.5 %) to accept a match.
_PRICE_TOLERANCE = 0.005


class TradeReconciliationService:
    """Reconcile Gate.io trade fills against local trade_tracking records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── public entry-point ────────────────────────────────────────────────────

    async def run(self) -> dict[str, Any]:
        """Process all active exchange connections and reconcile their fills.

        Returns a summary dict for logging.
        """
        connections = await self._get_active_connections()
        if not connections:
            logger.debug("[Reconciler] No active exchange connections found.")
            return {"connections": 0, "converted": 0, "external": 0, "skipped": 0, "errors": 0}

        total: dict[str, int] = {
            "connections": len(connections),
            "converted": 0,
            "external": 0,
            "skipped": 0,
            "errors": 0,
        }

        for conn in connections:
            try:
                adapter = self._build_adapter(conn)
            except Exception as exc:
                logger.warning(
                    "[Reconciler] Cannot build adapter for connection %s: %s",
                    conn.id,
                    exc,
                )
                total["errors"] += 1
                continue

            result = await self._reconcile_connection(adapter, conn.user_id)
            for key in ("converted", "external", "skipped", "errors"):
                total[key] += result.get(key, 0)

        logger.info(
            "[Reconciler] Complete: connections=%d converted=%d external=%d "
            "skipped=%d errors=%d",
            total["connections"],
            total["converted"],
            total["external"],
            total["skipped"],
            total["errors"],
        )
        return total

    # ── per-connection reconciliation ─────────────────────────────────────────

    async def _reconcile_connection(
        self, adapter: GateAdapter, user_id: Any
    ) -> dict[str, int]:
        stats: dict[str, int] = {"converted": 0, "external": 0, "skipped": 0, "errors": 0}

        raw_spot = await self._safe_fetch_spot(adapter)
        raw_futures = await self._safe_fetch_futures(adapter)

        spot_normalized = [self._normalize_spot(t) for t in raw_spot]
        futures_normalized = [self._normalize_futures(t) for t in raw_futures]

        all_trades = [t for t in spot_normalized + futures_normalized if t is not None]

        for trade in all_trades:
            try:
                outcome = await self._process_trade(trade, user_id)
                stats[outcome] = stats.get(outcome, 0) + 1
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    "[Reconciler] Error processing external_id=%s market=%s: %s",
                    trade.get("external_id"),
                    trade.get("market_type"),
                    exc,
                    exc_info=True,
                )

        return stats

    # ── trade processing ──────────────────────────────────────────────────────

    async def _process_trade(
        self, trade: dict[str, Any], user_id: Any
    ) -> str:
        """Process one normalized trade. Returns outcome key: skipped/converted/external."""
        external_id = trade["external_id"]
        market_type = trade["market_type"]

        if await self._already_processed(external_id, market_type):
            return "skipped"

        match = await self._find_matching_trade_tracking(trade, user_id)

        if match:
            await self._convert_to_real(match, trade)
            await self._update_decision_log(match, trade)
            await self._mark_processed(external_id, market_type, match.id)
            return "converted"
        else:
            new_tt = await self._create_external_trade(trade)
            await self._mark_processed(external_id, market_type, new_tt.id if new_tt else None)
            return "external"

    # ── Gate fetch helpers ────────────────────────────────────────────────────

    async def _safe_fetch_spot(self, adapter: GateAdapter) -> list[dict]:
        try:
            return await adapter.get_my_spot_trades(days=_LOOKBACK_DAYS, limit=_FETCH_LIMIT)
        except Exception as exc:
            logger.warning("[Reconciler] Spot trades fetch failed: %s", exc)
            return []

    async def _safe_fetch_futures(self, adapter: GateAdapter) -> list[dict]:
        try:
            return await adapter.get_my_futures_trades(days=_LOOKBACK_DAYS, limit=_FETCH_LIMIT)
        except Exception as exc:
            logger.warning("[Reconciler] Futures trades fetch failed: %s", exc)
            return []

    # ── normalization ─────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_spot(raw: dict) -> dict[str, Any] | None:
        """Normalize a /spot/my_trades entry into the internal trade dict.

        Only BUY fills are processed.  In the Scalpyn spot engine, sells are
        used to *close* existing long positions — they are not modelled as
        opening short positions.  Returning None for sell fills prevents the
        reconciler from creating spurious short trade_tracking rows that would
        never match an open trade and would be created as external trades with
        wrong position_side.
        """
        try:
            ts_raw = raw.get("create_time") or raw.get("create_time_ms")
            ts = _parse_timestamp(ts_raw)
            if ts is None:
                return None
            price = float(raw["price"])
            size = float(raw["amount"])
            if price <= 0 or size <= 0:
                return None
            side = str(raw.get("side", "buy")).lower()
            # Skip sell fills: spot sells close long positions, they are not shorts.
            if side == "sell":
                return None
            return {
                "external_id": str(raw["id"]),
                "symbol": str(raw["currency_pair"]),
                "price": price,
                "size": size,
                "timestamp": ts,
                "side": side,
                "position_side": "long",
                "market_type": "spot",
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("[Reconciler] Spot normalization failed: %s — %s", raw, exc)
            return None

    @staticmethod
    def _normalize_futures(raw: dict) -> dict[str, Any] | None:
        """Normalize a /futures/usdt/my_trades entry into the internal trade dict.

        For futures, position direction is determined by the sign of ``size``:
          * size > 0 → long fill (opening/adding a long position)
          * size < 0 → short fill (opening/adding a short position)

        We do NOT use buy/sell labels here because in hedge-mode or reduce-only
        orders, the buy/sell direction does not reliably indicate whether the
        resulting position is long or short.
        """
        try:
            ts_raw = raw.get("create_time") or raw.get("create_time_ms")
            ts = _parse_timestamp(ts_raw)
            if ts is None:
                return None
            price = float(raw["price"])
            size_raw = float(raw["size"])
            if price <= 0 or size_raw == 0:
                return None
            # Derive position_side directly from the size sign (Gate.io spec).
            position_side = "long" if size_raw > 0 else "short"
            return {
                "external_id": str(raw["id"]),
                "symbol": str(raw["contract"]),
                "price": price,
                "size": abs(size_raw),
                "timestamp": ts,
                "side": "buy" if size_raw > 0 else "sell",
                "position_side": position_side,
                "market_type": "futures",
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("[Reconciler] Futures normalization failed: %s — %s", raw, exc)
            return None

    # ── deduplication ─────────────────────────────────────────────────────────

    async def _already_processed(self, external_id: str, market_type: str) -> bool:
        result = await self.session.execute(
            text("""
                SELECT 1 FROM reconciled_gate_trades
                WHERE external_id = :eid AND market_type = :mt
                LIMIT 1
            """),
            {"eid": external_id, "mt": market_type},
        )
        return result.fetchone() is not None

    async def _mark_processed(
        self,
        external_id: str,
        market_type: str,
        trade_tracking_id: Any,
    ) -> None:
        await self.session.execute(
            text("""
                INSERT INTO reconciled_gate_trades
                    (external_id, market_type, trade_tracking_id)
                VALUES
                    (:eid, :mt, :ttid)
                ON CONFLICT (external_id, market_type) DO NOTHING
            """),
            {
                "eid": external_id,
                "mt": market_type,
                "ttid": str(trade_tracking_id) if trade_tracking_id else None,
            },
        )

    # ── trade_tracking matching ───────────────────────────────────────────────

    async def _find_matching_trade_tracking(
        self, trade: dict[str, Any], user_id: Any
    ) -> TradeTracking | None:
        """Find an open simulated trade_tracking row close in time to *trade*.

        Matching criteria (all must be satisfied):
        * same symbol
        * same market_type
        * same position_side (long/short) — prevents cross-direction false matches
        * status = 'open'
        * is_simulated = TRUE
        * external_id IS NULL (not yet reconciled)
        * |entry_time − trade.timestamp| ≤ _MATCH_WINDOW_S seconds
        * |entry_price − trade.price| / entry_price < _PRICE_TOLERANCE (0.5 %)
        * user context: decision linked to the same user (or no decision)
        """
        ts: datetime = trade["timestamp"]

        stmt = (
            select(TradeTracking)
            .outerjoin(DecisionLog, TradeTracking.decision_id == DecisionLog.id)
            .where(
                TradeTracking.symbol == trade["symbol"],
                TradeTracking.market_type == trade["market_type"],
                TradeTracking.position_side == trade["position_side"],
                TradeTracking.status == "open",
                TradeTracking.is_simulated == True,  # noqa: E712
                TradeTracking.external_id.is_(None),
                func.abs(
                    func.extract(
                        "epoch",
                        TradeTracking.entry_time - ts,
                    )
                ) <= _MATCH_WINDOW_S,
                func.abs(TradeTracking.entry_price - trade["price"])
                / TradeTracking.entry_price
                < _PRICE_TOLERANCE,
                or_(
                    DecisionLog.user_id == str(user_id) if user_id is not None else DecisionLog.user_id.is_(None),
                    TradeTracking.decision_id.is_(None),
                ),
            )
            .order_by(TradeTracking.entry_time.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    # ── conversion / creation ─────────────────────────────────────────────────

    async def _convert_to_real(
        self, tt: TradeTracking, trade: dict[str, Any]
    ) -> None:
        """Convert a simulated trade_tracking row to real using Gate fill data.

        The original ``entry_price`` (from the decision/signal) is preserved
        so that slippage can be calculated as ``real_entry_price − entry_price``.
        Only ``real_entry_price``, ``is_simulated``, and ``external_id`` are updated.
        """
        await self.session.execute(
            update(TradeTracking)
            .where(TradeTracking.id == tt.id)
            .values(
                is_simulated=False,
                real_entry_price=trade["price"],
                external_id=trade["external_id"],
            )
        )
        logger.info(
            "[Reconciler] Converted simulated → real | id=%s symbol=%s "
            "decision_price=%.8g real_price=%.8g slippage_pct=%.4f",
            tt.id,
            trade["symbol"],
            float(tt.entry_price),
            trade["price"],
            (trade["price"] - float(tt.entry_price)) / float(tt.entry_price) * 100,
        )

    async def _update_decision_log(
        self, tt: TradeTracking, trade: dict[str, Any]
    ) -> None:
        """Write execution metadata to dedicated decisions_log columns.

        The ``metrics`` JSONB column is intentionally left untouched — it is
        written once by pipeline_scan and must remain immutable for auditability.
        """
        if tt.decision_id is None:
            return
        await self.session.execute(
            update(DecisionLog)
            .where(DecisionLog.id == tt.decision_id)
            .values(
                trade_executed=True,
                execution_type=trade["market_type"],
                execution_entry_price=float(trade["price"]),
                execution_entry_time=trade["timestamp"],
            )
        )

    async def _create_external_trade(
        self, trade: dict[str, Any]
    ) -> TradeTracking | None:
        """Create a new trade_tracking row for a Gate fill with no local match."""
        tt = TradeTracking(
            decision_id=None,
            symbol=trade["symbol"],
            market_type=trade["market_type"],
            position_side=trade["position_side"],
            is_simulated=False,
            entry_price=trade["price"],
            real_entry_price=trade["price"],
            entry_time=trade["timestamp"],
            external_id=trade["external_id"],
            status="open",
        )
        self.session.add(tt)
        await self.session.flush()  # populate tt.id before returning
        logger.info(
            "[Reconciler] Created external trade | symbol=%s market=%s side=%s "
            "price=%.8g external_id=%s",
            trade["symbol"],
            trade["market_type"],
            trade["position_side"],
            trade["price"],
            trade["external_id"],
        )
        return tt

    # ── credential helpers ────────────────────────────────────────────────────

    async def _get_active_connections(self) -> list[ExchangeConnection]:
        result = await self.session.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    def _build_adapter(conn: ExchangeConnection) -> GateAdapter:
        raw_key = (
            bytes(conn.api_key_encrypted)
            if isinstance(conn.api_key_encrypted, memoryview)
            else conn.api_key_encrypted
        )
        raw_secret = (
            bytes(conn.api_secret_encrypted)
            if isinstance(conn.api_secret_encrypted, memoryview)
            else conn.api_secret_encrypted
        )
        api_key = decrypt(raw_key).strip()
        api_secret = decrypt(raw_secret).strip()
        if not api_key or not api_secret:
            raise ValueError("Empty credentials after decryption")
        return GateAdapter(api_key, api_secret)


# ── module-level helpers ──────────────────────────────────────────────────────


def _parse_timestamp(value: Any) -> datetime | None:
    """Convert a Gate.io unix timestamp (seconds or milliseconds) to a UTC datetime."""
    if value is None:
        return None
    try:
        ts = float(value)
        # Gate sometimes returns milliseconds for *_ms fields
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


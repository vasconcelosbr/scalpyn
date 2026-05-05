"""Trade Reconciliation Service — Module 2.

Queries the Gate.io API (spot and futures) to detect real trade fills,
matches them against open ``trade_tracking`` rows (converting simulated entries
to real ones), and creates new ``trade_tracking`` rows for trades that
originated outside Scalpyn.

Invariants
----------
* Does NOT modify ``pipeline_scan``, ``score_engine``, ``block_engine``,
  ``execute_buy``, indicators, or the Celery execution flow.
* Gate.io is treated as the source of truth — it can overwrite a simulated
  entry_price with the real fill price.
* The ``reconciled_gate_trades`` dedup table guarantees idempotency: a Gate
  fill is processed at most once even if the task fires multiple times within
  the same window.
* Each reconciliation run is scoped to a single active ExchangeConnection;
  multiple connections (multiple users) are processed in the same Celery task
  invocation but independently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
        futures_normalized = [self._normalize_futures(t) for t in raw_futures if t]

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
        """Normalize a /spot/my_trades entry into the internal trade dict."""
        try:
            ts_raw = raw.get("create_time") or raw.get("create_time_ms")
            ts = _parse_timestamp(ts_raw)
            if ts is None:
                return None
            price = float(raw["price"])
            size = float(raw["amount"])
            if price <= 0 or size <= 0:
                return None
            return {
                "external_id": str(raw["id"]),
                "symbol": str(raw["currency_pair"]),
                "price": price,
                "size": size,
                "timestamp": ts,
                "side": str(raw.get("side", "buy")).lower(),
                "market_type": "spot",
            }
        except (KeyError, TypeError, ValueError) as exc:
            logger.debug("[Reconciler] Spot normalization failed: %s — %s", raw, exc)
            return None

    @staticmethod
    def _normalize_futures(raw: dict) -> dict[str, Any] | None:
        """Normalize a /futures/usdt/my_trades entry into the internal trade dict."""
        try:
            ts_raw = raw.get("create_time") or raw.get("create_time_ms")
            ts = _parse_timestamp(ts_raw)
            if ts is None:
                return None
            price = float(raw["price"])
            size_raw = float(raw["size"])
            if price <= 0 or size_raw == 0:
                return None
            side = "buy" if size_raw > 0 else "sell"
            return {
                "external_id": str(raw["id"]),
                "symbol": str(raw["contract"]),
                "price": price,
                "size": abs(size_raw),
                "timestamp": ts,
                "side": side,
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

        Matching criteria:
        * same symbol
        * status = 'open'
        * is_simulated = TRUE
        * |entry_time − trade.timestamp| ≤ _MATCH_WINDOW_S
        * user context: decision linked to the same user (or no decision)
        """
        result = await self.session.execute(
            text("""
                SELECT tt.*
                FROM trade_tracking tt
                LEFT JOIN decisions_log dl ON tt.decision_id = dl.id
                WHERE tt.symbol      = :symbol
                  AND tt.market_type = :market_type
                  AND tt.status      = 'open'
                  AND tt.is_simulated = TRUE
                  AND tt.external_id IS NULL
                  AND ABS(EXTRACT(EPOCH FROM (tt.entry_time - :ts))) <= :window
                  AND (dl.user_id = :uid OR tt.decision_id IS NULL)
                ORDER BY tt.entry_time DESC
                LIMIT 1
            """),
            {
                "symbol": trade["symbol"],
                "market_type": trade["market_type"],
                "ts": trade["timestamp"],
                "window": _MATCH_WINDOW_S,
                "uid": str(user_id) if user_id else None,
            },
        )
        row = result.fetchone()
        if row is None:
            return None
        # Re-fetch as ORM object so we get the mapped model.
        result2 = await self.session.execute(
            select(TradeTracking).where(TradeTracking.id == row[0])
        )
        return result2.scalars().first()

    # ── conversion / creation ─────────────────────────────────────────────────

    async def _convert_to_real(
        self, tt: TradeTracking, trade: dict[str, Any]
    ) -> None:
        """Convert a simulated trade_tracking row to real using Gate fill data."""
        await self.session.execute(
            update(TradeTracking)
            .where(TradeTracking.id == tt.id)
            .values(
                is_simulated=False,
                entry_price=trade["price"],
                entry_time=trade["timestamp"],
                external_id=trade["external_id"],
            )
        )
        logger.info(
            "[Reconciler] Converted simulated → real | id=%s symbol=%s price=%.8g",
            tt.id,
            trade["symbol"],
            trade["price"],
        )

    async def _update_decision_log(
        self, tt: TradeTracking, trade: dict[str, Any]
    ) -> None:
        """Update the linked decisions_log row to reflect the real execution."""
        if tt.decision_id is None:
            return
        await self.session.execute(
            text("""
                UPDATE decisions_log
                SET
                    metrics = COALESCE(metrics, '{}'::jsonb) ||
                              jsonb_build_object(
                                  'trade_executed',  true,
                                  'execution_type',  :market_type,
                                  'entry_price',     :price,
                                  'entry_time',      :ts,
                                  'simulation',      false
                              )
                WHERE id = :decision_id
            """),
            {
                "market_type": trade["market_type"],
                "price": float(trade["price"]),
                "ts": trade["timestamp"].isoformat(),
                "decision_id": tt.decision_id,
            },
        )

    async def _create_external_trade(
        self, trade: dict[str, Any]
    ) -> TradeTracking | None:
        """Create a new trade_tracking row for a Gate fill with no local match."""
        position_side = _infer_side(trade["side"])
        tt = TradeTracking(
            decision_id=None,
            symbol=trade["symbol"],
            market_type=trade["market_type"],
            position_side=position_side,
            is_simulated=False,
            entry_price=trade["price"],
            entry_time=trade["timestamp"],
            external_id=trade["external_id"],
            status="open",
        )
        self.session.add(tt)
        await self.session.flush()  # populate tt.id before returning
        logger.info(
            "[Reconciler] Created external trade | symbol=%s market=%s side=%s price=%.8g external_id=%s",
            trade["symbol"],
            trade["market_type"],
            position_side,
            trade["price"],
            trade["external_id"],
        )
        return tt

    # ── credential helpers ────────────────────────────────────────────────────

    async def _get_active_connections(self) -> list[ExchangeConnection]:
        result = await self.session.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.is_active == True,  # noqa: E712
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


def _infer_side(side: str) -> str:
    """Map Gate.io trade side ('buy'/'sell') to position_side ('long'/'short')."""
    return "long" if side.lower() == "buy" else "short"

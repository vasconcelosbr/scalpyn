"""Executions Sync Service — backfill Gate.io fills into ``exchange_executions`` (Task #257).

Idempotent UPSERT keyed by ``(exchange, market_type, trade_id)``. WebSocket
streaming via ``spot.usertrades`` / ``futures.usertrades`` is intentionally
deferred to a follow-up task (only the REST backfill + 5-min reconciliation
loop is delivered here — both safely keep the dashboard consistent within a
few minutes of any new fill).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..exchange_adapters.gate_adapter import GateAdapter
from ..models.exchange_connection import ExchangeConnection
from ..models.exchange_execution import ExchangeExecution
from ..utils.encryption import decrypt

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # Gate uses seconds for create_time and ms for create_time_ms.
    if f > 1_000_000_000_000:
        f = f / 1000.0
    return datetime.fromtimestamp(f, tz=timezone.utc)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").replace("/", "_").upper()


class ExecutionsSyncService:
    """REST-driven backfill + reconciliation of Gate.io fills."""

    async def _get_gate_adapter(
        self, db: AsyncSession, user_id: UUID
    ) -> Optional[GateAdapter]:
        result = await db.execute(
            select(ExchangeConnection).where(
                ExchangeConnection.user_id == user_id,
                ExchangeConnection.is_active == True,  # noqa: E712
            )
        )
        conn = result.scalars().first()
        if not conn:
            return None
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
            return None
        return GateAdapter(api_key, api_secret)

    # ── normalisation ───────────────────────────────────────────────────────
    @staticmethod
    def _normalize_spot(row: Dict[str, Any], user_id: UUID) -> Optional[Dict[str, Any]]:
        trade_id = str(row.get("id") or "").strip()
        if not trade_id:
            return None
        executed_at = (
            _parse_ts(row.get("create_time_ms"))
            or _parse_ts(row.get("create_time"))
        )
        if executed_at is None:
            return None
        qty = _safe_float(row.get("amount"))
        price = _safe_float(row.get("price"))
        return {
            "user_id": user_id,
            "exchange": "gate",
            "market_type": "spot",
            "trade_id": trade_id,
            "order_id": str(row.get("order_id") or "") or None,
            "symbol": _normalize_symbol(row.get("currency_pair")),
            "side": str(row.get("side") or "").lower(),
            "role": str(row.get("role") or "").lower() or None,
            "price": price,
            "quantity": qty,
            "quote_quantity": qty * price if qty and price else None,
            "fee": _safe_float(row.get("fee")) if row.get("fee") is not None else None,
            "fee_currency": str(row.get("fee_currency") or "") or None,
            "executed_at": executed_at,
            "raw_payload": row,
        }

    @staticmethod
    def _normalize_futures(row: Dict[str, Any], user_id: UUID) -> Optional[Dict[str, Any]]:
        trade_id = str(row.get("id") or "").strip()
        if not trade_id:
            return None
        executed_at = (
            _parse_ts(row.get("create_time_ms"))
            or _parse_ts(row.get("create_time"))
        )
        if executed_at is None:
            return None
        size = _safe_float(row.get("size"))
        price = _safe_float(row.get("price"))
        # In Gate futures, ``size`` carries direction (positive = long fill,
        # negative = short fill). We store quantity as absolute and flag side.
        side = "buy" if size > 0 else "sell"
        qty = abs(size)
        return {
            "user_id": user_id,
            "exchange": "gate",
            "market_type": "futures",
            "trade_id": trade_id,
            "order_id": str(row.get("order_id") or "") or None,
            "symbol": _normalize_symbol(row.get("contract")),
            "side": side,
            "role": str(row.get("role") or "").lower() or None,
            "price": price,
            "quantity": qty,
            "quote_quantity": qty * price if qty and price else None,
            "fee": _safe_float(row.get("fee")) if row.get("fee") is not None else None,
            "fee_currency": "USDT",
            "executed_at": executed_at,
            "raw_payload": row,
        }

    # ── upsert ──────────────────────────────────────────────────────────────
    async def _upsert_batch(
        self, db: AsyncSession, rows: List[Dict[str, Any]]
    ) -> int:
        if not rows:
            return 0
        # Strip the JSONB raw_payload to a JSON-safe dict (asyncpg + JSONB
        # accepts dicts directly, but we json.loads(json.dumps) defensively
        # to drop any non-serialisable Decimals from the exchange response).
        for r in rows:
            if r.get("raw_payload") is not None:
                try:
                    r["raw_payload"] = json.loads(json.dumps(r["raw_payload"], default=str))
                except Exception:
                    r["raw_payload"] = None

        stmt = pg_insert(ExchangeExecution).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["exchange", "market_type", "trade_id"]
        )
        result = await db.execute(stmt)
        # rowcount is reliable for ON CONFLICT DO NOTHING in asyncpg.
        return int(result.rowcount or 0)

    # ── pagination helpers ──────────────────────────────────────────────────
    async def _paginate_spot(
        self,
        adapter,
        user_id: UUID,
        days: int,
        page_size: int,
        max_pages: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Walk /spot/my_trades using Gate.io's native ``page`` cursor.

        Strategy:
        1. Pin the ``from``/``to`` window once at the start so paging is
           stable even if new fills land mid-loop.
        2. Walk ``page=1, 2, ...`` until the API returns a short page
           (< page_size rows), an empty page, or we hit ``max_pages``.
        3. Dedup by ``trade_id`` (defence-in-depth — duplicates would
           also be filtered by the ON CONFLICT DO NOTHING UPSERT).
        """
        from_ts = int((datetime.now(timezone.utc)
                       - timedelta(days=days)).timestamp())
        to_ts = int(datetime.now(timezone.utc).timestamp())

        all_norm: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        pages_walked = 0
        raw_total = 0
        for page in range(1, max_pages + 1):
            pages_walked = page
            raw = await adapter.get_my_spot_trades(
                limit=page_size, page=page,
                from_ts=from_ts, to_ts=to_ts,
            )
            if not raw:
                break
            raw_total += len(raw)
            for x in raw:
                n = self._normalize_spot(x, user_id)
                if not n or n["trade_id"] in seen_ids:
                    continue
                seen_ids.add(n["trade_id"])
                all_norm.append(n)
            if len(raw) < page_size:
                break  # last page (proven exhaustion).
        telemetry = {
            "pages_walked": pages_walked,
            "raw_rows": raw_total,
            "normalized_rows": len(all_norm),
            "exhausted": pages_walked < max_pages,
        }
        return all_norm, telemetry

    async def _paginate_futures(
        self,
        adapter,
        user_id: UUID,
        days: int,
        page_size: int,
        max_pages: int,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Walk /futures/usdt/my_trades using ``last_id`` cursor.

        Gate.io's futures endpoint returns rows sorted DESC by id; passing
        ``last_id=<smallest id from previous page>`` returns rows strictly
        older than that id. This is a true monotonic cursor — there is no
        risk of silent gap.
        """
        from_ts = int((datetime.now(timezone.utc)
                       - timedelta(days=days)).timestamp())
        to_ts = int(datetime.now(timezone.utc).timestamp())

        all_norm: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        last_id: Optional[str] = None
        pages_walked = 0
        raw_total = 0
        for page in range(1, max_pages + 1):
            pages_walked = page
            raw = await adapter.get_my_futures_trades(
                limit=page_size,
                from_ts=from_ts, to_ts=to_ts,
                last_id=last_id,
            )
            if not raw:
                break
            raw_total += len(raw)
            min_id_in_page: Optional[str] = None
            for x in raw:
                n = self._normalize_futures(x, user_id)
                if not n:
                    continue
                if n["trade_id"] not in seen_ids:
                    seen_ids.add(n["trade_id"])
                    all_norm.append(n)
                # Track smallest numeric id for cursor advancement.
                try:
                    candidate = str(x.get("id"))
                    if candidate and (min_id_in_page is None
                                      or int(candidate) < int(min_id_in_page)):
                        min_id_in_page = candidate
                except (ValueError, TypeError):
                    continue
            if len(raw) < page_size or not min_id_in_page or min_id_in_page == last_id:
                break
            last_id = min_id_in_page
        telemetry = {
            "pages_walked": pages_walked,
            "raw_rows": raw_total,
            "normalized_rows": len(all_norm),
            "exhausted": pages_walked < max_pages,
        }
        return all_norm, telemetry

    # ── public api ──────────────────────────────────────────────────────────
    async def backfill_user(
        self,
        db: AsyncSession,
        user_id: UUID,
        days: int = 90,
        markets: Optional[List[str]] = None,
        page_size: int = 1000,
        max_pages: int = 25,
    ) -> Dict[str, Any]:
        """Fetch up to ``days`` of fills for a user and UPSERT them.

        Pagination walks Gate.io's ``my_trades`` endpoints until a short
        page is returned or ``max_pages`` is hit (safety cap: at 1000
        rows/page that's 25k fills/market — well above realistic volumes).
        """
        markets = markets or ["spot", "futures"]
        adapter = await self._get_gate_adapter(db, user_id)
        if not adapter:
            return {
                "success": False,
                "error": "No active Gate.io connection found.",
            }

        summary: Dict[str, Any] = {
            "success": True, "imported": {}, "fetched": {}, "telemetry": {},
        }
        try:
            if "spot" in markets:
                rows, tele = await self._paginate_spot(
                    adapter=adapter, user_id=user_id, days=days,
                    page_size=page_size, max_pages=max_pages,
                )
                summary["fetched"]["spot"] = len(rows)
                summary["telemetry"]["spot"] = tele
                imported = 0
                for i in range(0, len(rows), 500):
                    imported += await self._upsert_batch(db, rows[i:i + 500])
                summary["imported"]["spot"] = imported
                if not tele["exhausted"]:
                    logger.warning(
                        "Spot backfill hit max_pages cap (%d pages × %d rows). "
                        "Some historical fills may be missing — re-run with "
                        "smaller days window or higher max_pages.",
                        tele["pages_walked"], page_size,
                    )
            if "futures" in markets:
                rows, tele = await self._paginate_futures(
                    adapter=adapter, user_id=user_id, days=days,
                    page_size=page_size, max_pages=max_pages,
                )
                summary["fetched"]["futures"] = len(rows)
                summary["telemetry"]["futures"] = tele
                imported = 0
                for i in range(0, len(rows), 500):
                    imported += await self._upsert_batch(db, rows[i:i + 500])
                summary["imported"]["futures"] = imported
                if not tele["exhausted"]:
                    logger.warning(
                        "Futures backfill hit max_pages cap (%d pages × %d rows). "
                        "Some historical fills may be missing.",
                        tele["pages_walked"], page_size,
                    )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.exception("Executions backfill failed: %s", exc)
            return {"success": False, "error": str(exc)}

        return summary

    async def latest_execution_at(
        self, db: AsyncSession, user_id: UUID, market_type: str
    ) -> Optional[datetime]:
        row = await db.execute(text(
            "SELECT MAX(executed_at) FROM exchange_executions "
            "WHERE user_id = :uid AND market_type = :mt"
        ), {"uid": str(user_id), "mt": market_type})
        v = row.scalar_one_or_none()
        return v


executions_sync_service = ExecutionsSyncService()

"""Decision Audit Service — append-only writer for ``trade_decisions``.

Records *every* pipeline decision (APPROVED / REJECTED / BLOCKED /
SKIPPED) at any stage (L1 / L2 / L3 / EXECUTION) into the audit table.

Invariants
----------
* **Never raises**: any DB / serialization error is logged with full
  ``trace_id`` context but swallowed. The audit log is observability,
  not a control path — it MUST NOT be able to abort a trading decision.
* **No commit**: the caller owns the transaction boundary. This service
  only issues an INSERT; the surrounding ``async with session.begin():``
  (or explicit ``await db.commit()``) is the caller's responsibility.
* **Fire-and-forget shape**: returns ``None``. Callers that need to know
  whether the row landed should query by ``trace_id`` afterwards.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_INSERT_SQL = text("""
    INSERT INTO trade_decisions (
        trace_id, user_id, pool_id, symbol, market_type, exchange,
        status, stage, reason, blocking_rule,
        rule_details, rules_matched, rules_failed, rules_skipped,
        score_breakdown, indicators_snapshot, latency_ms, trade_id
    ) VALUES (
        :trace_id, :user_id, :pool_id, :symbol, :market_type, :exchange,
        :status, :stage, :reason, :blocking_rule,
        CAST(:rule_details AS JSONB),
        CAST(:rules_matched AS JSONB),
        CAST(:rules_failed AS JSONB),
        CAST(:rules_skipped AS JSONB),
        CAST(:score_breakdown AS JSONB),
        CAST(:indicators_snapshot AS JSONB),
        CAST(:latency_ms AS JSONB),
        :trade_id
    )
""")


def _to_jsonb(value: Any) -> Optional[str]:
    """Serialize a Python value to a JSON string for the JSONB cast.

    asyncpg accepts a Python dict/list directly for JSONB, but going
    through ``CAST(... AS JSONB)`` with a JSON string is the most
    portable path and works identically under SQLAlchemy's text() bind
    parameter substitution. Returns ``None`` for ``None`` so the column
    stays NULL.
    """
    if value is None:
        return None
    import json
    try:
        return json.dumps(value, default=str, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        # Defensive: never let a bad payload abort the audit write.
        logger.warning(
            "decision_audit: failed to serialize JSONB payload (%s); "
            "storing NULL", exc,
        )
        return None


async def record_decision(
    db: AsyncSession,
    trace_id: str,
    user_id: str,
    pool_id: str,
    symbol: str,
    market_type: str,
    exchange: Optional[str],
    status: Literal["APPROVED", "REJECTED", "BLOCKED", "SKIPPED"],
    stage: Literal["L1", "L2", "L3", "EXECUTION"],
    reason: Optional[str] = None,
    blocking_rule: Optional[str] = None,
    rule_details: Optional[dict] = None,
    rules_matched: Optional[list] = None,
    rules_failed: Optional[list] = None,
    rules_skipped: Optional[list] = None,
    score_breakdown: Optional[dict] = None,
    indicators_snapshot: Optional[dict] = None,
    latency_ms: Optional[dict] = None,
    trade_id: Optional[str] = None,
) -> None:
    """Insert one audit row into ``trade_decisions``.

    See module docstring for invariants. The function never raises and
    never commits — it issues a single INSERT and returns.
    """
    try:
        await db.execute(
            _INSERT_SQL,
            {
                "trace_id": trace_id,
                "user_id": user_id,
                "pool_id": pool_id,
                "symbol": symbol,
                "market_type": market_type,
                "exchange": exchange,
                "status": status,
                "stage": stage,
                "reason": reason,
                "blocking_rule": blocking_rule,
                "rule_details": _to_jsonb(rule_details),
                "rules_matched": _to_jsonb(rules_matched),
                "rules_failed": _to_jsonb(rules_failed),
                "rules_skipped": _to_jsonb(rules_skipped),
                "score_breakdown": _to_jsonb(score_breakdown),
                "indicators_snapshot": _to_jsonb(indicators_snapshot),
                "latency_ms": _to_jsonb(latency_ms),
                "trade_id": trade_id,
            },
        )
    except Exception:
        # Never propagate: the audit log is observability, not a gate.
        # The full traceback is preserved with the trace_id for
        # post-mortem correlation.
        logger.exception(
            "decision_audit: failed to record decision "
            "(trace_id=%s symbol=%s stage=%s status=%s)",
            trace_id, symbol, stage, status,
        )

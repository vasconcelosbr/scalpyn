"""Persistence queue message types.

Each message is an immutable payload describing one idempotent UPSERT.
Workers dequeue messages and execute them inside a short-lived UnitOfWork
(one transaction per message).  Messages MUST be self-contained — workers
never call back into producers, never perform external I/O, and never share
state across messages.

Idempotency contract
--------------------
Every message corresponds to an UPSERT keyed on a natural primary key
(time+symbol+timeframe for ohlcv/indicators, symbol for market_metadata,
gate trade_id for reconciled fills).  Workers may re-execute the same
message after a transient failure with no observable side effect.

Categories
----------
``category`` controls backpressure policy when the queue is full
(see ``persistence.queue.PersistenceQueue.put``):

* ``ingest``    — high-frequency tick/orderbook data; drop oldest on overflow.
* ``compute``   — best-effort periodic writes (e.g. cache warmups);
                  block producer with timeout, drop on timeout.
* ``scheduler`` — periodic indicator/score writes that MUST land every cycle;
                  block producer indefinitely (no drop).
* ``critical``  — decisions/trades/reconciliation; block producer indefinitely.

The distinction between ``compute`` and ``scheduler`` exists because the
recurring scheduler writes are the system's primary data source — a
silent drop here means a user sees stale indicators on the dashboard
without any error surfacing.  ``compute`` is reserved for derived /
recoverable writes that the next cycle would naturally re-emit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

Category = str  # one of: "ingest", "compute", "scheduler", "critical"


@dataclass(frozen=True)
class _BaseMessage:
    category: Category
    enqueued_at: float  # time.monotonic()

    @property
    def kind(self) -> str:
        return type(self).__name__


@dataclass(frozen=True)
class OhlcvCandle(_BaseMessage):
    symbol: str
    exchange: str
    timeframe: str
    market_type: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float


@dataclass(frozen=True)
class OhlcvBatch(_BaseMessage):
    """Bulk variant — many candles for a single (symbol, timeframe).

    Workers UPSERT all rows inside one transaction so the cost per insert
    drops dramatically; falls back to per-row insert if any row fails.
    """
    symbol: str
    exchange: str
    timeframe: str
    market_type: str
    rows: tuple[dict, ...]  # tuple for hashability/immutability


@dataclass(frozen=True)
class MarketMetadataUpsert(_BaseMessage):
    symbol: str
    last_updated: datetime
    price: Optional[float] = None
    price_change_24h: Optional[float] = None
    volume_24h: Optional[float] = None
    spread_pct: Optional[float] = None
    orderbook_depth_usdt: Optional[float] = None


@dataclass(frozen=True)
class IndicatorsUpsert(_BaseMessage):
    """Insert or update an indicators row.

    ``mode`` controls the ON CONFLICT clause:
      * ``"upsert"``        — DO UPDATE SET indicators_json + scheduler_group
      * ``"insert_only"``   — DO NOTHING (used by 5m microstructure path
                              where each (time, symbol, timeframe) tuple is
                              expected to be unique by cadence).
    """
    symbol: str
    timeframe: str
    market_type: str
    scheduler_group: str
    time: datetime
    payload_json: str  # already serialised by producer (envelope-wrapped)
    mode: str = "upsert"  # "upsert" | "insert_only"


@dataclass(frozen=True)
class ReconciledTradeUpsert(_BaseMessage):
    """Persist a Gate fill into reconciled_gate_trades + side-effects.

    The ``side_effects`` payload is an opaque dict the worker passes
    through to ``persistence.repositories.persist_reconciled_trade`` —
    avoids leaking the reconciliation business logic into the message
    type. Producer is responsible for ensuring the side-effects dict is
    JSON-serialisable and idempotent.
    """
    connection_id: int
    gate_trade_id: str
    symbol: str
    market_type: str
    side_effects: dict[str, Any] = field(default_factory=dict)

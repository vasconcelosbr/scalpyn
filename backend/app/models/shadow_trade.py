"""Shadow Portfolio — registro de promoções L3 que não viraram trade real.

Uma linha em ``shadow_trades`` representa uma promoção
``decisions_log.decision='ALLOW' AND decisions_log.direction='SPOT'``
que ``execute_buy.py`` barrou por gate de capital/risco e que o sistema
acompanha como trade simulado de U$1000 USDT (configurável via
``SHADOW_TRADE_AMOUNT_USDT``) até atingir TP, SL ou timeout.

Vocabulário canônico (Task #292): ``decisions_log.direction`` usa
``'LONG' | 'SHORT' | 'NEUTRAL' | 'SPOT'`` (uppercase). Shadow é
**spot-only** hoje (sem leverage, long-only) — só promove ``'SPOT'``.
Habilitar Shadow para futures requer helper separado.

Os dados desta tabela NUNCA contaminam P&L real, win rate real ou
capital em uso. Eventualmente são replicados em ``trade_simulations``
com ``is_simulated=TRUE`` e ``source='SHADOW'`` para alimentar o
dataset de ML (Fase 3).
"""

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid

from ..database import Base


class ShadowTrade(Base):
    __tablename__ = "shadow_trades"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id = Column(
        BigInteger,
        ForeignKey("decisions_log.id"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    symbol = Column(String(20), nullable=False, index=True)
    strategy = Column(String(50), nullable=True)
    direction = Column(String(10), nullable=True)

    # No Python-side default — value is supplied by the writer (Fase 2:
    # ``ShadowTradeService.create_from_skip`` reads ``SHADOW_TRADE_AMOUNT_USDT``
    # env, default 1000.0). DDL has ``DEFAULT 1000.0`` only as a safety net
    # for ad-hoc INSERTs; runtime callers must always set this explicitly.
    amount_usdt = Column(Float, nullable=False)

    entry_price = Column(Float, nullable=True)
    entry_timestamp = Column(DateTime(timezone=True), nullable=True)
    tp_price = Column(Float, nullable=True)
    sl_price = Column(Float, nullable=True)
    tp_pct = Column(Float, nullable=True)
    sl_pct = Column(Float, nullable=True)
    timeout_candles = Column(Integer, nullable=True)

    exit_price = Column(Float, nullable=True)
    exit_timestamp = Column(DateTime(timezone=True), nullable=True)
    outcome = Column(String(20), nullable=True)
    pnl_pct = Column(Float, nullable=True)
    pnl_usdt = Column(Float, nullable=True)
    holding_seconds = Column(Integer, nullable=True)

    status = Column(String(20), nullable=False, default="PENDING", index=True)
    skip_reason = Column(String(50), nullable=True)

    config_snapshot = Column(JSONB, nullable=True)
    features_snapshot = Column(JSONB, nullable=True)
    # Snapshot dos indicadores no momento em que TP/SL/TIMEOUT foi
    # atingido (preenchido pelo ``shadow_trade_monitor`` no fechamento).
    # Mesmo formato FLAT do entry (ver ``_build_features_snapshot``).
    features_snapshot_exit = Column(JSONB, nullable=True)

    last_processed_time = Column(DateTime(timezone=True), nullable=True)

    # Index on created_at is declared by migration 046 as DESC
    # (``ix_shadow_trades_created_at``). Do NOT set ``index=True`` here —
    # SQLAlchemy would emit a separate ASC index on metadata.create_all.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # ── Market-context columns (migration 052, ML Fase 6) ────────────────
    # Preenchidos pelo monitor (`shadow_trade_monitor._enrich_market_context`)
    # após resolver a entrada — additive, não afeta TP/SL/timeout. Backfill
    # offline via `scripts/backfill_shadow_trade_context.py`.
    # Todos nullable: shadows criados antes da migration ou com dados de
    # contexto indisponíveis ficam em NULL (o XGBoost trata como missing).
    btc_price_at_entry = Column(Numeric(18, 8), nullable=True)
    btc_change_1h_pct = Column(Numeric(8, 4), nullable=True)
    funding_rate_at_entry = Column(Numeric(10, 6), nullable=True)
    n_concurrent_signals = Column(Integer, nullable=True)

    decision = relationship("DecisionLog", foreign_keys=[decision_id])

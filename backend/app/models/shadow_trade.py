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
    Boolean,
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
        # Nullable since migration 057 (Task #303): shadows criadas a partir
        # do snapshot vivo da L3 (``pipeline_watchlist_assets``) não têm uma
        # DecisionLog correspondente porque o gate
        # ``pipeline_scan._should_log_decision`` só grava transições.
        nullable=True,
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

    # Origem da promoção — 'L3' (gate canônico POOL→L1→L2→L3) ou
    # 'L3_REJECTED' (bloqueado na L3, shadow criado para dados ML).
    # Default 'L3' garante back-compat com linhas pré-migration 060.
    # Histórico: 'ARROW' existia para watchlist custom (Task #321, removida).
    source = Column(String(20), nullable=False, default="L3", index=True)

    config_snapshot = Column(JSONB, nullable=True)
    features_snapshot = Column(JSONB, nullable=True)
    # Snapshot dos indicadores no momento em que TP/SL/TIMEOUT foi
    # atingido (preenchido pelo ``shadow_trade_monitor`` no fechamento).
    # Mesmo formato FLAT do entry (ver ``_build_features_snapshot``).
    features_snapshot_exit = Column(JSONB, nullable=True)

    # Canonical immutable lineage (migrations 131/133). Historical rows may
    # remain unresolved, but every new native capture must populate this set.
    event_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    snapshot_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    exchange = Column(String(32), nullable=True)
    timeframe = Column(String(16), nullable=True)
    profile_version_id = Column(UUID(as_uuid=True), nullable=True)
    score_engine_version_id = Column(UUID(as_uuid=True), nullable=True)
    feature_schema_version = Column(String(80), nullable=True)
    feature_extractor_version = Column(String(80), nullable=True)
    capture_contract_version = Column(String(80), nullable=True)
    label_contract_version = Column(String(80), nullable=True)
    barrier_contract_version = Column(String(80), nullable=True)
    features_captured_at = Column(DateTime(timezone=True), nullable=True)
    label_resolved_at = Column(DateTime(timezone=True), nullable=True)
    features_coverage = Column(Numeric(7, 6), nullable=True)
    oldest_indicator_age_s = Column(Integer, nullable=True)
    market_data_confidence = Column(Numeric(7, 6), nullable=True)
    feature_hash = Column(String(64), nullable=True)
    profile_config_hash = Column(String(64), nullable=True)
    score_engine_config_hash = Column(String(64), nullable=True)
    lineage_status = Column(String(32), nullable=True)
    eligible_for_training = Column(Boolean, nullable=False, default=False)

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

    # ── MAE/MFE Tracking (migration 062, Fase Quant 1) ───────────────────
    # Rastreamento contínuo de trajetória do trade durante RUNNING.
    # min/max_price_post_entry: atualizados candle-a-candle pelo monitor
    # usando candle.low / candle.high (nunca só o close).
    # mae_pct / mfe_pct / max_drawdown_pct / max_profit_pct: calculados no
    # encerramento. NÃO usados em inferência do XGBoost nesta fase.
    # Todos nullable: back-compat com trades criados antes desta migration.
    min_price_post_entry = Column(Float, nullable=True)
    max_price_post_entry = Column(Float, nullable=True)
    max_drawdown_pct = Column(Float, nullable=True)   # == mae_pct (alias)
    max_profit_pct = Column(Float, nullable=True)     # == mfe_pct (alias)
    mae_pct = Column(Float, nullable=True)             # MAE %: (min-entry)/entry*100
    mfe_pct = Column(Float, nullable=True)             # MFE %: (max-entry)/entry*100

    # ── Exit Metrics Snapshot (migration 062, Fase Quant 2) ──────────────
    # Snapshot rico no encerramento: indicadores + PnL + MAE/MFE.
    # Preenchido por _capture_exit_features após o trade atingir outcome.
    # Complementa features_snapshot_exit (flat indicators) com contexto
    # quantitativo completo para análises offline.
    exit_metrics_json = Column(JSONB, nullable=True)

    # ── Timeout Post-Analysis (migration 063, Fase Quant — Timeout) ──────
    # Monitoramento passivo pós-timeout: rastreia o preço em horizontes
    # fixos após exit_timestamp para calcular Timeout Recovery Rate,
    # Delayed TP Time, e MFE/MAE adicionais. Puramente observacional —
    # nunca reabre o trade nem altera outcome/pnl. Todos nullable:
    # back-compat com trades antes da migration.
    price_after_1h = Column(Float, nullable=True)
    price_after_2h = Column(Float, nullable=True)
    price_after_4h = Column(Float, nullable=True)
    price_after_12h = Column(Float, nullable=True)
    price_after_24h = Column(Float, nullable=True)
    # Excursão pós-timeout (high-water / low-water nas 24h seguintes).
    max_profit_after_timeout_pct = Column(Float, nullable=True)
    max_drawdown_after_timeout_pct = Column(Float, nullable=True)
    # Delayed TP: teria atingido tp_price dentro das 24h pós-timeout?
    delayed_tp = Column(Boolean, nullable=True)
    delayed_tp_hours = Column(Float, nullable=True)
    # Flag de controle: TRUE quando o analyzer terminou de processar.
    timeout_post_analysis_done = Column(Boolean, nullable=True, default=False)

    # ── Shadow Instrumentation (migration 071, Fases 1/2/3) ──────────────
    # Fase 1 — MAE/MFE timestamps e barreira intrabar.
    mae_at = Column(DateTime(timezone=True), nullable=True)
    mfe_at = Column(DateTime(timezone=True), nullable=True)
    # 'TP' | 'SL' | 'BOTH_SAME_CANDLE' | 'NONE' (timeout)
    barrier_touched = Column(String(20), nullable=True)
    barrier_touched_at = Column(DateTime(timezone=True), nullable=True)
    # Convenção aplicada quando TP e SL tocam no mesmo candle.
    intrabar_convention = Column(String(20), nullable=True)
    # Retorno no close do candle de timeout (com sinal); NULL para TP/SL.
    final_return_pct = Column(Float, nullable=True)
    # Fase 2 — Labels líquidos de fees.
    # Fee lido de config_profiles (config_snapshot["ml_fee_roundtrip_pct"]).
    net_return_pct = Column(Float, nullable=True)
    fee_roundtrip_pct_applied = Column(Float, nullable=True)
    # Fase 3 — Barreiras volatility-adjusted (registro; modo FIXED agora).
    # 'FIXED' | 'ATR_ADAPTIVE' — vigente na abertura do trade.
    barrier_mode = Column(String(20), nullable=True)
    tp_pct_applied = Column(Float, nullable=True)
    sl_pct_applied = Column(Float, nullable=True)
    # ATR% no momento da entrada — preenchido pelo monitor na primeira
    # resolução de entry_price; vira feature mesmo em modo FIXED.
    atr_pct_at_entry = Column(Float, nullable=True)

    # ── TTT (Time-To-Target) Policy — migration 065 ───────────────────────
    # Camada de labeling de ML para eficiência temporal. Classifica trades
    # como FAST_WIN (atingiu ttt_tp_pct dentro de ttt_timeout_minutes) ou
    # TIMEOUT. NÃO altera outcome/pnl/TP/SL. Sem lookahead bias: campos
    # TTT são preenchidos post-trade (analytics + ML label only).
    #
    # Snapshot da política (gravado na criação — imutável por shadow):
    ttt_enabled = Column(Boolean, nullable=True, default=False)
    ttt_tp_pct = Column(Float, nullable=True)           # ex: 1.0 (%)
    ttt_timeout_minutes = Column(Integer, nullable=True) # ex: 180 (3h)
    #
    # Label ML — resultado do post-analysis:
    #   'FAST_WIN' | 'TIMEOUT'
    ttt_outcome = Column(String(20), nullable=True)
    #   'TP_HIT_IN_WINDOW' | 'HARD_TIMEOUT'
    ttt_close_reason = Column(String(30), nullable=True)
    #   'WIN_0_15M' | 'WIN_15_30M' | 'WIN_30_60M' | 'WIN_60_180M'
    ttt_fast_win_bucket = Column(String(20), nullable=True)
    # TRUE = análise concluída; idempotência do ttt_analyzer.
    ttt_analysis_done = Column(Boolean, nullable=True, default=False)
    #
    # Métricas temporais (preenchidas ao fechar + pelo ttt_analyzer):
    elapsed_minutes = Column(Float, nullable=True)            # duração total em min
    time_to_tp_minutes = Column(Float, nullable=True)         # min até atingir ttt_tp_pct
    profit_velocity = Column(Float, nullable=True)            # max_profit_pct / elapsed_min
    profit_velocity_per_hour = Column(Float, nullable=True)   # normalizado por hora
    #
    # Lucro máximo por janela temporal (inline monitor ou ttt_analyzer):
    max_profit_first_15m = Column(Float, nullable=True)
    max_profit_first_30m = Column(Float, nullable=True)
    max_profit_first_60m = Column(Float, nullable=True)
    #
    # Contadores de candles (ANALYTICS ONLY — nunca feature de entrada ML):
    candles_to_peak = Column(Integer, nullable=True)
    candles_to_first_positive = Column(Integer, nullable=True)

    # ── Strategy Lab columns (migration 077) ─────────────────────────────────
    # Populated only for Strategy Lab shadows created via create_strategy_lab_shadows/
    # create_strategy_lab_rejected_shadows. Null for all pre-migration shadows.
    # profile_id FK uses ON DELETE SET NULL to preserve data if profile deleted.
    profile_id = Column(
        UUID(as_uuid=True),
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    profile_version = Column(DateTime(timezone=True), nullable=True)
    profile_name = Column(String(255), nullable=True)
    strategy_type = Column(String(64), nullable=True)
    rules_snapshot = Column(JSONB, nullable=True)
    profile_status_at_entry = Column(String(32), nullable=True)
    final_priority_score = Column(Float, nullable=True)
    ml_probability = Column(Float, nullable=True)
    ml_model_id = Column(UUID(as_uuid=True), nullable=True)
    orchestrator_payload = Column(JSONB, nullable=True)
    model_lane = Column(String, nullable=True)
    ranking_id = Column(UUID(as_uuid=True), ForeignKey("ml_opportunity_rankings.id", use_alter=True, name="fk_shadow_trades_ranking_id"), nullable=True)
    model_version = Column(String, nullable=True)
    threshold_used = Column(Float, nullable=True)
    score_status = Column(String, nullable=True)
    gate_action = Column(String, nullable=True)
    reason_codes = Column(JSONB, nullable=True)
    ml_gate_enabled = Column(Boolean, nullable=False, default=False)

    # ── Watchlist Lineage (migration 103) ─────────────────────────────────────
    # Snapshot da watchlist que originou a promoção — preenchido inline pelo
    # pipeline_scan para todos os novos trades. Linhas históricas: NULL até
    # backfill via shadow_lineage_backfill (L3_LAB resolvível via profile_id
    # JOIN; L3 canônico → lineage_confidence='LEGACY_UNKNOWN').
    watchlist_id = Column(UUID(as_uuid=True), nullable=True)
    watchlist_name = Column(String(150), nullable=True)
    watchlist_level = Column(String(10), nullable=True)
    source_watchlist_id = Column(UUID(as_uuid=True), nullable=True)
    lineage_confidence = Column(String(30), nullable=True)
    lineage_source = Column(String(50), nullable=True)
    lineage_resolved_at = Column(DateTime(timezone=True), nullable=True)

    decision = relationship("DecisionLog", foreign_keys=[decision_id])

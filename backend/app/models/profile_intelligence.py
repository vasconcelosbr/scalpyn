"""Profile Intelligence Engine models — runs, indicator stats, rule combinations,
suggestions, and audit log."""

from sqlalchemy import Column, String, Integer, Boolean, Numeric, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.types import TIMESTAMP
import uuid
from datetime import datetime, timezone
from ..database import Base


class ProfileIntelligenceRun(Base):
    __tablename__ = "profile_intelligence_runs"
    __table_args__ = (
        Index("idx_pi_runs_user_run_at", "user_id", "run_at"),
        Index("idx_pi_runs_user_status", "user_id", "status", "run_at"),
    )

    id                          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                     = Column(UUID(as_uuid=True), nullable=False)
    run_at                      = Column(TIMESTAMP(timezone=True), nullable=False,
                                         default=lambda: datetime.now(timezone.utc))
    lookback_days               = Column(Integer, nullable=False)
    min_closed_trades           = Column(Integer, nullable=False, default=30)
    discovery_start_at          = Column(TIMESTAMP(timezone=True), nullable=True)
    discovery_end_at            = Column(TIMESTAMP(timezone=True), nullable=True)
    validation_start_at         = Column(TIMESTAMP(timezone=True), nullable=True)
    validation_end_at           = Column(TIMESTAMP(timezone=True), nullable=True)
    profiles_analyzed           = Column(JSONB, nullable=True)
    total_profiles              = Column(Integer, default=0)
    total_shadow_trades         = Column(Integer, default=0)
    total_closed_trades         = Column(Integer, default=0)
    total_opportunity_snapshots = Column(Integer, default=0)
    base_win_rate               = Column(Numeric, nullable=True)
    base_avg_pnl_pct            = Column(Numeric, nullable=True)
    base_tp_30m_rate            = Column(Numeric, nullable=True)
    status                      = Column(String(30), default="running")
    engine_version              = Column(String(30), nullable=True)
    settings_json               = Column(JSONB, nullable=True)
    notes                       = Column(Text, nullable=True)
    error_message               = Column(Text, nullable=True)
    created_at                  = Column(TIMESTAMP(timezone=True), nullable=False,
                                         default=lambda: datetime.now(timezone.utc))
    updated_at                  = Column(TIMESTAMP(timezone=True), nullable=False,
                                         default=lambda: datetime.now(timezone.utc))


class ProfileIndicatorStats(Base):
    __tablename__ = "profile_indicator_stats"
    __table_args__ = (
        Index("idx_pi_ind_stats_run", "user_id", "run_id"),
        Index("idx_pi_ind_stats_role", "user_id", "role_detected", "confidence_score"),
        Index("idx_pi_ind_stats_bucket", "indicator", "bucket_label"),
    )

    id                         = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                    = Column(UUID(as_uuid=True), nullable=False)
    run_id                     = Column(UUID(as_uuid=True),
                                        ForeignKey("profile_intelligence_runs.id", ondelete="CASCADE"),
                                        nullable=False)
    indicator                  = Column(String(60), nullable=False)
    operator                   = Column(String(10), nullable=True)
    range_min                  = Column(Numeric, nullable=True)
    range_max                  = Column(Numeric, nullable=True)
    value_text                 = Column(String(60), nullable=True)
    bucket_label               = Column(String(100), nullable=False)
    total_cases                = Column(Integer, default=0)
    wins                       = Column(Integer, default=0)
    losses                     = Column(Integer, default=0)
    timeouts                   = Column(Integer, default=0)
    win_rate                   = Column(Numeric, nullable=True)
    loss_rate                  = Column(Numeric, nullable=True)
    avg_pnl_pct                = Column(Numeric, nullable=True)
    avg_holding_seconds        = Column(Numeric, nullable=True)
    avg_winner_holding_seconds = Column(Numeric, nullable=True)
    avg_mae_pct                = Column(Numeric, nullable=True)
    avg_mfe_pct                = Column(Numeric, nullable=True)
    tp_15m_rate                = Column(Numeric, nullable=True)
    tp_30m_rate                = Column(Numeric, nullable=True)
    tp_60m_rate                = Column(Numeric, nullable=True)
    lift_vs_base               = Column(Numeric, nullable=True)
    pnl_lift_vs_base           = Column(Numeric, nullable=True)
    winner_presence_pct        = Column(Numeric, nullable=True)
    loser_presence_pct         = Column(Numeric, nullable=True)
    confidence_score           = Column(Numeric, nullable=True)
    confidence_level           = Column(String(20), nullable=True)
    role_detected              = Column(String(30), nullable=True)
    source_profiles            = Column(JSONB, nullable=True)
    evidence_json              = Column(JSONB, nullable=True)
    created_at                 = Column(TIMESTAMP(timezone=True), nullable=False,
                                        default=lambda: datetime.now(timezone.utc))


class ProfileRuleCombination(Base):
    __tablename__ = "profile_rule_combinations"
    __table_args__ = (
        Index("idx_pi_comb_run", "user_id", "run_id"),
        Index("idx_pi_comb_score", "user_id", "champion_score"),
        Index("idx_pi_comb_conf_score", "user_id", "confidence_level", "champion_score"),
        Index("uq_pi_comb_hash", "user_id", "run_id", "combination_hash", unique=True),
    )

    id                                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                            = Column(UUID(as_uuid=True), nullable=False)
    run_id                             = Column(UUID(as_uuid=True),
                                                ForeignKey("profile_intelligence_runs.id", ondelete="CASCADE"),
                                                nullable=False)
    combination_hash                   = Column(String(64), nullable=False)
    combination_type                   = Column(String(30), nullable=False)
    setup_family                       = Column(String(30), nullable=True)
    suggested_name                     = Column(String(120), nullable=True)
    rules_json                         = Column(JSONB, nullable=False, default=list)
    signals_json                       = Column(JSONB, nullable=True)
    scoring_rules_json                 = Column(JSONB, nullable=True)
    block_rules_json                   = Column(JSONB, nullable=True)
    required_master_scoring_rules_json = Column(JSONB, nullable=True)
    source_profiles                    = Column(JSONB, nullable=True)
    total_cases                        = Column(Integer, default=0)
    wins                               = Column(Integer, default=0)
    losses                             = Column(Integer, default=0)
    timeouts                           = Column(Integer, default=0)
    win_rate                           = Column(Numeric, nullable=True)
    loss_rate                          = Column(Numeric, nullable=True)
    avg_pnl_pct                        = Column(Numeric, nullable=True)
    avg_holding_seconds                = Column(Numeric, nullable=True)
    avg_winner_holding_seconds         = Column(Numeric, nullable=True)
    avg_mae_pct                        = Column(Numeric, nullable=True)
    avg_mfe_pct                        = Column(Numeric, nullable=True)
    tp_15m_rate                        = Column(Numeric, nullable=True)
    tp_30m_rate                        = Column(Numeric, nullable=True)
    tp_60m_rate                        = Column(Numeric, nullable=True)
    lift_vs_base                       = Column(Numeric, nullable=True)
    support                            = Column(Numeric, nullable=True)
    confidence                         = Column(Numeric, nullable=True)
    rule_lift                          = Column(Numeric, nullable=True)
    leverage                           = Column(Numeric, nullable=True)
    conviction                         = Column(Numeric, nullable=True)
    champion_score                     = Column(Numeric, nullable=True)
    confidence_level                   = Column(String(20), nullable=True)
    discovery_metrics_json             = Column(JSONB, nullable=True)
    validation_metrics_json            = Column(JSONB, nullable=True)
    degradation_pct                    = Column(Numeric, nullable=True)
    overfit_risk                       = Column(Boolean, default=False)
    is_tested_live_shadow              = Column(Boolean, default=False)
    status                             = Column(String(30), default="discovered")
    created_at                         = Column(TIMESTAMP(timezone=True), nullable=False,
                                                default=lambda: datetime.now(timezone.utc))


class ProfileSuggestion(Base):
    __tablename__ = "profile_suggestions"
    __table_args__ = (
        Index("idx_pi_sugg_status", "user_id", "status", "created_at"),
        Index("idx_pi_sugg_score", "user_id", "confidence_score"),
    )

    id                                 = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                            = Column(UUID(as_uuid=True), nullable=False)
    run_id                             = Column(UUID(as_uuid=True),
                                                ForeignKey("profile_intelligence_runs.id", ondelete="CASCADE"),
                                                nullable=False)
    source_combination_id              = Column(UUID(as_uuid=True),
                                                ForeignKey("profile_rule_combinations.id", ondelete="SET NULL"),
                                                nullable=True)
    suggested_profile_name             = Column(String(255), nullable=False)
    suggested_profile_description      = Column(Text, nullable=True)
    suggested_profile_family           = Column(String(30), nullable=True)
    source_profiles                    = Column(JSONB, nullable=True)
    suggested_config_json              = Column(JSONB, nullable=False, default=dict)
    suggested_signals_json             = Column(JSONB, nullable=True)
    suggested_scoring_json             = Column(JSONB, nullable=True)
    suggested_block_rules_json         = Column(JSONB, nullable=True)
    required_master_scoring_rules_json = Column(JSONB, nullable=True)
    evidence_summary_json              = Column(JSONB, nullable=True)
    quantitative_explanation           = Column(Text, nullable=True)
    ai_explanation                     = Column(Text, nullable=True)
    risk_notes                         = Column(Text, nullable=True)
    confidence_score                   = Column(Numeric, nullable=True)
    confidence_level                   = Column(String(20), nullable=True)
    status                             = Column(String(30), default="pending_user_approval")
    created_profile_id                 = Column(UUID(as_uuid=True), nullable=True)
    created_at                         = Column(TIMESTAMP(timezone=True), nullable=False,
                                                default=lambda: datetime.now(timezone.utc))
    updated_at                         = Column(TIMESTAMP(timezone=True), nullable=False,
                                                default=lambda: datetime.now(timezone.utc))


class ProfileIntelligenceAuditLog(Base):
    __tablename__ = "profile_intelligence_audit_log"
    __table_args__ = (
        Index("idx_pi_audit_user", "user_id", "created_at"),
        Index("idx_pi_audit_run", "run_id"),
        Index("idx_pi_audit_sugg", "suggestion_id"),
    )

    id                = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id           = Column(UUID(as_uuid=True), nullable=False)
    run_id            = Column(UUID(as_uuid=True), nullable=True)
    suggestion_id     = Column(UUID(as_uuid=True), nullable=True)
    combination_id    = Column(UUID(as_uuid=True), nullable=True)
    event_type        = Column(String(60), nullable=False)
    event_description = Column(Text, nullable=True)
    payload_json      = Column(JSONB, nullable=True)
    result_json       = Column(JSONB, nullable=True)
    model_provider    = Column(String(30), nullable=True)
    model_name        = Column(String(60), nullable=True)
    prompt_text       = Column(Text, nullable=True)
    response_text     = Column(Text, nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc))

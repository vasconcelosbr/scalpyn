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
    trigger_source              = Column(String(20), nullable=True)  # 'manual', 'beat', 'api'
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
    source_profile_ids         = Column(JSONB, nullable=True)
    validation_status          = Column(String(40), nullable=False, default="exploratory_only")
    actionability_status       = Column(String(40), nullable=False, default="exploratory_only")
    target_section             = Column(String(80), nullable=True)
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
    source_profile_ids                 = Column(JSONB, nullable=True)
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
    source_type                        = Column(String(50), nullable=True)
    source_model_type                  = Column(String(30), nullable=True)
    source_model_id                    = Column(UUID(as_uuid=True), nullable=True)
    source_run_id                      = Column(UUID(as_uuid=True), nullable=True)
    profile_id                         = Column(UUID(as_uuid=True),
                                                ForeignKey("profiles.id", ondelete="SET NULL"),
                                                nullable=True)
    profile_name                       = Column(String(255), nullable=True)
    suggested_profile_name             = Column(String(255), nullable=False)
    suggested_profile_description      = Column(Text, nullable=True)
    suggested_profile_family           = Column(String(30), nullable=True)
    source_profiles                    = Column(JSONB, nullable=True)
    source_profile_ids                 = Column(JSONB, nullable=True)
    target_section                     = Column(String(80), nullable=True)
    target_field                       = Column(String(120), nullable=True)
    current_value                      = Column(JSONB, nullable=True)
    proposed_value                     = Column(JSONB, nullable=True)
    diff_json                          = Column(JSONB, nullable=True)
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
    confidence                         = Column(Numeric, nullable=True)
    lift                               = Column(Numeric, nullable=True)
    evidence_count                     = Column(Integer, nullable=True)
    expected_impact                    = Column(JSONB, nullable=True)
    risk_level                         = Column(String(20), nullable=True)
    validation_status                  = Column(String(40), nullable=True)
    actionability_status               = Column(String(40), nullable=True)
    blocked_reason                     = Column(Text, nullable=True)
    status                             = Column(String(30), default="pending")
    created_profile_id                 = Column(UUID(as_uuid=True), nullable=True)
    applied_at                         = Column(TIMESTAMP(timezone=True), nullable=True)
    reverted_at                        = Column(TIMESTAMP(timezone=True), nullable=True)
    reason                             = Column(Text, nullable=True)
    rollback_payload                   = Column(JSONB, nullable=True)
    dataset_version                    = Column(String(80), nullable=True)
    feature_schema_version             = Column(String(80), nullable=True)
    label_version                      = Column(String(80), nullable=True)
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
    before_json       = Column(JSONB, nullable=True)
    after_json        = Column(JSONB, nullable=True)
    diff_json         = Column(JSONB, nullable=True)
    actor_user_id     = Column(UUID(as_uuid=True), nullable=True)
    profile_name      = Column(String(200), nullable=True)
    source_run_id     = Column(UUID(as_uuid=True), nullable=True)
    created_at        = Column(TIMESTAMP(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc))


class MLModelRegistry(Base):
    __tablename__ = "ml_model_registry"
    __table_args__ = (
        Index("idx_ml_registry_scope", "profile_id", "market_regime", "strategy_skill"),
        Index("idx_ml_registry_status", "status", "model_type", "created_at"),
    )

    model_id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_ml_model_id     = Column(UUID(as_uuid=True), nullable=True)
    model_type             = Column(String(30), nullable=False)
    model_version          = Column(String(80), nullable=False)
    profile_id             = Column(UUID(as_uuid=True),
                                    ForeignKey("profiles.id", ondelete="SET NULL"),
                                    nullable=True)
    profile_name           = Column(String(255), nullable=True)
    strategy_skill         = Column(String(80), nullable=False, default="win_fast")
    market_regime          = Column(String(80), nullable=False, default="all")
    dataset_version        = Column(String(80), nullable=True)
    feature_schema_version = Column(String(80), nullable=True)
    label_version          = Column(String(80), nullable=True)
    train_start            = Column(TIMESTAMP(timezone=True), nullable=True)
    train_end              = Column(TIMESTAMP(timezone=True), nullable=True)
    validation_start       = Column(TIMESTAMP(timezone=True), nullable=True)
    validation_end         = Column(TIMESTAMP(timezone=True), nullable=True)
    test_start             = Column(TIMESTAMP(timezone=True), nullable=True)
    test_end               = Column(TIMESTAMP(timezone=True), nullable=True)
    metrics_json           = Column(JSONB, nullable=False, default=dict)
    threshold              = Column(Numeric, nullable=True)
    status                 = Column(String(30), nullable=False, default="candidate")
    promoted_at            = Column(TIMESTAMP(timezone=True), nullable=True)
    promoted_by            = Column(UUID(as_uuid=True), nullable=True)
    rejection_reason       = Column(Text, nullable=True)
    artifact_path          = Column(Text, nullable=True)
    created_at             = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc))


class ProductionChampionControl(Base):
    __tablename__ = "production_champion_control"
    __table_args__ = (
        Index(
            "uq_production_champion_scope",
            "profile_id",
            "market_regime",
            "strategy_skill",
            unique=True,
        ),
    )

    id                   = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    profile_id           = Column(UUID(as_uuid=True),
                                  ForeignKey("profiles.id", ondelete="CASCADE"),
                                  nullable=False)
    market_regime        = Column(String(80), nullable=False, default="all")
    strategy_skill       = Column(String(80), nullable=False, default="win_fast")
    active_model_id      = Column(UUID(as_uuid=True),
                                  ForeignKey("ml_model_registry.model_id", ondelete="RESTRICT"),
                                  nullable=False)
    active_model_type    = Column(String(30), nullable=False)
    active_threshold     = Column(Numeric, nullable=False)
    activated_at         = Column(TIMESTAMP(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))
    activated_by         = Column(UUID(as_uuid=True), nullable=True)
    previous_model_id    = Column(UUID(as_uuid=True), nullable=True)
    rollback_available   = Column(Boolean, nullable=False, default=True)


class AlgorithmForwardValidation(Base):
    __tablename__ = "algorithm_forward_validations"
    __table_args__ = (
        Index("idx_forward_validation_suggestion", "suggestion_id", "stage"),
        Index("idx_forward_validation_model", "model_id", "stage"),
    )

    id                    = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    suggestion_id         = Column(UUID(as_uuid=True),
                                   ForeignKey("profile_suggestions.id", ondelete="CASCADE"),
                                   nullable=True)
    model_id              = Column(UUID(as_uuid=True),
                                   ForeignKey("ml_model_registry.model_id", ondelete="CASCADE"),
                                   nullable=True)
    profile_id            = Column(UUID(as_uuid=True),
                                   ForeignKey("profiles.id", ondelete="CASCADE"),
                                   nullable=False)
    stage                 = Column(String(40), nullable=False, default="discovery")
    validation_status     = Column(String(40), nullable=False, default="exploratory_only")
    metrics_json          = Column(JSONB, nullable=False, default=dict)
    human_approved_by     = Column(UUID(as_uuid=True), nullable=True)
    human_approved_at     = Column(TIMESTAMP(timezone=True), nullable=True)
    rollback_payload      = Column(JSONB, nullable=True)
    blocked_reason        = Column(Text, nullable=True)
    created_at            = Column(TIMESTAMP(timezone=True), nullable=False,
                                   default=lambda: datetime.now(timezone.utc))
    updated_at            = Column(TIMESTAMP(timezone=True), nullable=False,
                                   default=lambda: datetime.now(timezone.utc))


class AutopilotAutonomyPolicy(Base):
    __tablename__ = "autopilot_autonomy_policies"

    id                     = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id                = Column(UUID(as_uuid=True), nullable=False, unique=True)
    maximum_level          = Column(Integer, nullable=False, default=2)
    impact_limit_json      = Column(JSONB, nullable=False, default=dict)
    cooldown_seconds       = Column(Integer, nullable=False, default=0)
    max_changes_per_day    = Column(Integer, nullable=False, default=0)
    risk_budget_json       = Column(JSONB, nullable=False, default=dict)
    post_change_monitoring = Column(Boolean, nullable=False, default=True)
    auto_rollback_enabled  = Column(Boolean, nullable=False, default=False)
    updated_by             = Column(UUID(as_uuid=True), nullable=True)
    created_at             = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc))
    updated_at             = Column(TIMESTAMP(timezone=True), nullable=False,
                                    default=lambda: datetime.now(timezone.utc))

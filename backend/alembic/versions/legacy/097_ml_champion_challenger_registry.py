"""Add governed ML model and production champion registries.

Revision ID: 097_ml_champion_registry
Revises: 096_pi_suggestion_registry
Create Date: 2026-06-20
"""

from alembic import op
import sqlalchemy as sa


revision = "097_ml_champion_registry"
down_revision = "096_pi_suggestion_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        CREATE TABLE ml_model_registry (
            model_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_ml_model_id UUID,
            model_type VARCHAR(30) NOT NULL,
            model_version VARCHAR(80) NOT NULL,
            profile_id UUID REFERENCES profiles(id) ON DELETE SET NULL,
            profile_name VARCHAR(255),
            strategy_skill VARCHAR(80) NOT NULL DEFAULT 'win_fast',
            market_regime VARCHAR(80) NOT NULL DEFAULT 'all',
            dataset_version VARCHAR(80),
            feature_schema_version VARCHAR(80),
            label_version VARCHAR(80),
            train_start TIMESTAMPTZ,
            train_end TIMESTAMPTZ,
            validation_start TIMESTAMPTZ,
            validation_end TIMESTAMPTZ,
            test_start TIMESTAMPTZ,
            test_end TIMESTAMPTZ,
            metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            threshold NUMERIC,
            status VARCHAR(30) NOT NULL DEFAULT 'candidate',
            promoted_at TIMESTAMPTZ,
            promoted_by UUID,
            rejection_reason TEXT,
            artifact_path TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT ck_ml_registry_model_type
                CHECK (model_type IN ('xgboost', 'lightgbm', 'catboost')),
            CONSTRAINT ck_ml_registry_status
                CHECK (status IN ('candidate', 'challenger', 'champion', 'rejected', 'archived'))
        )
    """))
    op.execute(sa.text("""
        CREATE INDEX idx_ml_registry_scope
        ON ml_model_registry(profile_id, market_regime, strategy_skill)
    """))
    op.execute(sa.text("""
        CREATE INDEX idx_ml_registry_status
        ON ml_model_registry(status, model_type, created_at DESC)
    """))
    op.execute(sa.text("""
        CREATE UNIQUE INDEX uq_ml_registry_one_champion_scope
        ON ml_model_registry (
            COALESCE(profile_id, '00000000-0000-0000-0000-000000000000'::uuid),
            market_regime,
            strategy_skill
        )
        WHERE status = 'champion'
    """))

    op.execute(sa.text("""
        CREATE TABLE production_champion_control (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            profile_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
            market_regime VARCHAR(80) NOT NULL DEFAULT 'all',
            strategy_skill VARCHAR(80) NOT NULL DEFAULT 'win_fast',
            active_model_id UUID NOT NULL REFERENCES ml_model_registry(model_id) ON DELETE RESTRICT,
            active_model_type VARCHAR(30) NOT NULL,
            active_threshold NUMERIC NOT NULL,
            activated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            activated_by UUID,
            previous_model_id UUID,
            rollback_available BOOLEAN NOT NULL DEFAULT TRUE,
            CONSTRAINT ck_production_champion_type
                CHECK (active_model_type IN ('xgboost', 'lightgbm', 'catboost')),
            CONSTRAINT uq_production_champion_scope
                UNIQUE (profile_id, market_regime, strategy_skill)
        )
    """))

    # Preserve the existing active XGBoost rows as current champions. This is
    # registry preparation only; it does not change inference or thresholds.
    op.execute(sa.text("""
        INSERT INTO ml_model_registry (
            model_id,
            source_ml_model_id,
            model_type,
            model_version,
            profile_id,
            profile_name,
            dataset_version,
            feature_schema_version,
            train_start,
            train_end,
            metrics_json,
            threshold,
            status,
            promoted_at,
            artifact_path,
            created_at
        )
        SELECT
            m.id,
            m.id,
            'xgboost',
            m.version,
            m.profile_id,
            p.name,
            m.dataset_hash,
            m.feature_schema_version,
            m.train_from,
            m.train_to,
            jsonb_build_object(
                'precision', m.precision_score,
                'recall', m.recall_score,
                'f1', m.f1_score,
                'roc_auc', m.roc_auc,
                'false_positive_rate', m.false_positive_rate,
                'ev_score', m.ev_score
            ),
            m.decision_threshold,
            CASE
                WHEN m.status = 'active' AND m.scope_rank = 1 THEN 'champion'
                ELSE 'archived'
            END,
            m.activated_at,
            m.model_path,
            m.created_at
        FROM (
            SELECT
                source.*,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        COALESCE(
                            source.profile_id,
                            '00000000-0000-0000-0000-000000000000'::uuid
                        )
                    ORDER BY
                        source.activated_at DESC NULLS LAST,
                        source.created_at DESC
                ) AS scope_rank
            FROM ml_models source
        ) m
        LEFT JOIN profiles p ON p.id = m.profile_id
        ON CONFLICT (model_id) DO NOTHING
    """))
    op.execute(sa.text("""
        INSERT INTO production_champion_control (
            profile_id,
            market_regime,
            strategy_skill,
            active_model_id,
            active_model_type,
            active_threshold,
            activated_at,
            rollback_available
        )
        SELECT
            r.profile_id,
            r.market_regime,
            r.strategy_skill,
            r.model_id,
            r.model_type,
            r.threshold,
            COALESCE(r.promoted_at, r.created_at),
            TRUE
        FROM ml_model_registry r
        WHERE r.status = 'champion'
          AND r.profile_id IS NOT NULL
          AND r.threshold IS NOT NULL
        ON CONFLICT (profile_id, market_regime, strategy_skill) DO NOTHING
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS production_champion_control"))
    op.execute(sa.text("DROP TABLE IF EXISTS ml_model_registry"))

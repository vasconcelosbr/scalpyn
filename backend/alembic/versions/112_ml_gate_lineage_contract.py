"""Add ML Gate lineage contract fields.

Revision ID: 112_ml_gate_lineage
Revises: 111_ml_gate_audit_payload
Create Date: 2026-06-25

This migration is additive. It gives decisions_log, ml_opportunity_rankings
and shadow_trades first-class columns for the canary audit contract without
rewriting historical JSON payloads.
"""

from alembic import op
import sqlalchemy as sa


revision = "112_ml_gate_lineage"
down_revision = "111_ml_gate_audit_payload"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            ADD COLUMN IF NOT EXISTS ranking_id UUID NULL,
            ADD COLUMN IF NOT EXISTS model_id UUID NULL,
            ADD COLUMN IF NOT EXISTS model_version VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS model_lane VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS probability DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS threshold_used DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS score_status VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS gate_action VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS reason_codes JSONB NULL,
            ADD COLUMN IF NOT EXISTS orchestrator_payload JSONB NULL,
            ADD COLUMN IF NOT EXISTS ml_gate_enabled BOOLEAN NOT NULL DEFAULT FALSE
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint
                 WHERE conname = 'fk_decisions_log_ranking_id'
            ) THEN
                ALTER TABLE decisions_log
                ADD CONSTRAINT fk_decisions_log_ranking_id
                FOREIGN KEY (ranking_id) REFERENCES ml_opportunity_rankings(id);
            END IF;
        END
        $$;
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_decisions_log_ranking_id
        ON decisions_log (ranking_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_decisions_log_model_id
        ON decisions_log (model_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_decisions_log_ml_audit
        ON decisions_log (created_at DESC, model_lane, score_status)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_decisions_log_orchestrator_payload
        ON decisions_log USING GIN (orchestrator_payload)
        WHERE orchestrator_payload IS NOT NULL
    """))

    op.execute(sa.text("""
        ALTER TABLE ml_opportunity_rankings
            ADD COLUMN IF NOT EXISTS threshold_used DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS gate_action VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS used_by_gate BOOLEAN NOT NULL DEFAULT FALSE,
            ADD COLUMN IF NOT EXISTS rank_percentile DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS l1_ranker_mode VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS selected_by_l1_ranker BOOLEAN NULL,
            ADD COLUMN IF NOT EXISTS reason_codes JSONB NULL,
            ADD COLUMN IF NOT EXISTS orchestrator_payload JSONB NULL
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_model_id
        ON ml_opportunity_rankings (model_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_audit
        ON ml_opportunity_rankings (ranked_at DESC, model_lane, score_status)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_ml_opportunity_rankings_orch_payload
        ON ml_opportunity_rankings USING GIN (orchestrator_payload)
        WHERE orchestrator_payload IS NOT NULL
    """))

    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS model_version VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS threshold_used DOUBLE PRECISION NULL,
            ADD COLUMN IF NOT EXISTS score_status VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS gate_action VARCHAR NULL,
            ADD COLUMN IF NOT EXISTS reason_codes JSONB NULL,
            ADD COLUMN IF NOT EXISTS ml_gate_enabled BOOLEAN NOT NULL DEFAULT FALSE
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_ranking_id
        ON shadow_trades (ranking_id)
    """))
    op.execute(sa.text("""
        CREATE INDEX IF NOT EXISTS ix_shadow_trades_ml_audit
        ON shadow_trades (created_at DESC, model_lane, score_status)
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_ml_audit"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_shadow_trades_ranking_id"))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS ml_gate_enabled,
            DROP COLUMN IF EXISTS reason_codes,
            DROP COLUMN IF EXISTS gate_action,
            DROP COLUMN IF EXISTS score_status,
            DROP COLUMN IF EXISTS threshold_used,
            DROP COLUMN IF EXISTS model_version
    """))

    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_opportunity_rankings_orch_payload"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_opportunity_rankings_audit"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_ml_opportunity_rankings_model_id"))
    op.execute(sa.text("""
        ALTER TABLE ml_opportunity_rankings
            DROP COLUMN IF EXISTS orchestrator_payload,
            DROP COLUMN IF EXISTS reason_codes,
            DROP COLUMN IF EXISTS selected_by_l1_ranker,
            DROP COLUMN IF EXISTS l1_ranker_mode,
            DROP COLUMN IF EXISTS rank_percentile,
            DROP COLUMN IF EXISTS used_by_gate,
            DROP COLUMN IF EXISTS gate_action,
            DROP COLUMN IF EXISTS threshold_used
    """))

    op.execute(sa.text("DROP INDEX IF EXISTS ix_decisions_log_orchestrator_payload"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_decisions_log_ml_audit"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_decisions_log_model_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS ix_decisions_log_ranking_id"))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
        DROP CONSTRAINT IF EXISTS fk_decisions_log_ranking_id
    """))
    op.execute(sa.text("""
        ALTER TABLE decisions_log
            DROP COLUMN IF EXISTS ml_gate_enabled,
            DROP COLUMN IF EXISTS orchestrator_payload,
            DROP COLUMN IF EXISTS reason_codes,
            DROP COLUMN IF EXISTS gate_action,
            DROP COLUMN IF EXISTS score_status,
            DROP COLUMN IF EXISTS threshold_used,
            DROP COLUMN IF EXISTS probability,
            DROP COLUMN IF EXISTS model_lane,
            DROP COLUMN IF EXISTS model_version,
            DROP COLUMN IF EXISTS model_id,
            DROP COLUMN IF EXISTS ranking_id
    """))

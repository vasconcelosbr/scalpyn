"""ML audit tables: label/feature/dataset contracts, training dataset, threshold curve, gate results

Revision ID: c002rastreat
Revises: c001v52activ
Create Date: 2026-06-30

Fase 5 of ML correction plan — creates governance/audit tables that enable
post-hoc reconstruction of: what label was trained, what features were used,
which dataset was used, and the full threshold curve (not just the final scalar).

Tables:
  ml_label_contracts     — defines label formulas (is_tp_4h_v1, etc.)
  ml_feature_contracts   — defines feature schemas by hash
  ml_dataset_contracts   — links label + feature contracts with source_filter
  ml_training_dataset    — records dataset stats per model training run
  ml_threshold_curve     — full precision/recall/threshold curve per model
  ml_promotion_gate_results — gate evaluation history (currently in metrics_json JSONB)
  ml_model_predictions   — renamed from ml_predictions; new table with richer schema

Note: ml_predictions table already exists with a simpler schema and is kept.
ml_model_predictions is a new richer table for future use.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c002rastreat"
down_revision = "c001v52activ"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_label_contracts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("sql_expression", sa.Text),
        sa.Column("target_window_seconds", sa.Integer),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("name", "version", name="uq_label_contract_name_version"),
    )

    op.create_table(
        "ml_feature_contracts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("feature_columns_hash", sa.String(64)),
        sa.Column("feature_count", sa.Integer),
        sa.Column("feature_columns_json", postgresql.JSONB),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("schema_version", name="uq_feature_contract_schema_version"),
    )

    op.create_table(
        "ml_dataset_contracts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("label_contract_id", sa.String(32)),
        sa.Column("feature_contract_id", sa.String(32)),
        sa.Column("source_filter", sa.String(128)),
        sa.Column("model_lane", sa.String(32)),
        sa.Column("description", sa.Text),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ml_training_dataset",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True)),
        sa.Column("dataset_contract_id", sa.String(32)),
        sa.Column("source_filter", sa.String(128)),
        sa.Column("n_samples", sa.Integer),
        sa.Column("n_positive", sa.Integer),
        sa.Column("n_negative", sa.Integer),
        sa.Column("positive_rate", sa.Float),
        sa.Column("cutoff_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("train_from", sa.TIMESTAMP(timezone=True)),
        sa.Column("train_to", sa.TIMESTAMP(timezone=True)),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ml_threshold_curve",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("precision_score", sa.Float),
        sa.Column("recall_score", sa.Float),
        sa.Column("fpr", sa.Float),
        sa.Column("f1_score", sa.Float),
        sa.Column("n_positive", sa.Integer),
        sa.Column("n_negative", sa.Integer),
        sa.Column("is_selected", sa.Boolean, default=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ml_promotion_gate_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gate_version", sa.String(32)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reasons_json", postgresql.JSONB),
        sa.Column("input_json", postgresql.JSONB),
        sa.Column("evaluated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "ml_model_predictions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_lane", sa.String(32)),
        sa.Column("model_version", sa.String(32)),
        sa.Column("decision_id", sa.BigInteger),
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True)),
        sa.Column("symbol", sa.String(32)),
        sa.Column("profile_id", postgresql.UUID(as_uuid=True)),
        sa.Column("win_fast_probability", sa.Float),
        sa.Column("threshold_used", sa.Float),
        sa.Column("model_approved", sa.Boolean),
        sa.Column("p_l1_win", sa.Float),
        sa.Column("p_l3_profile_win", sa.Float),
        sa.Column("features_snapshot", postgresql.JSONB),
        sa.Column("score_status", sa.String(16)),
        sa.Column("scored_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )

    # Seed known label contracts from training history
    op.execute("""
        INSERT INTO ml_label_contracts (id, name, version, description, sql_expression, target_window_seconds)
        VALUES
        ('is_win_fast_v1_30m', 'is_win_fast_v1', '1.0',
         'Fast TP within 30min — original label (ttt buckets 0-15m and 15-30m)',
         'ttt_fast_win_bucket IN (''WIN_0_15M'',''WIN_15_30M'') AND ttt_analysis_done = TRUE',
         1800),
        ('is_tp_4h_v1_30m', 'is_tp_4h_v1', '1.0',
         'Fast TP <= 30min — label for v52 training (TRAIN_CUTOFF 2026-06-25T19:45)',
         'ttt_fast_win_bucket IN (''WIN_0_15M'',''WIN_15_30M'') AND ttt_analysis_done = TRUE',
         1800)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_table("ml_model_predictions")
    op.drop_table("ml_promotion_gate_results")
    op.drop_table("ml_threshold_curve")
    op.drop_table("ml_training_dataset")
    op.drop_table("ml_dataset_contracts")
    op.drop_table("ml_feature_contracts")
    op.drop_table("ml_label_contracts")

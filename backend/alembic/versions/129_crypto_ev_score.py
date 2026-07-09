"""Crypto EV operational score snapshots.

Revision ID: 129_crypto_ev_score
Revises: 128_shadow_force_close
Create Date: 2026-07-08
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "129_crypto_ev_score"
down_revision = "128_shadow_force_close"
branch_labels = None
depends_on = None


_DEFAULT_CONFIG_SQL = """
{
  "enabled": true,
  "window_hours": 168,
  "shrinkage_k": 30,
  "min_trades_for_state": 15,
  "max_unreplayable_ratio": 0.20,
  "fee_roundtrip_pct_source": "ml_fee_roundtrip_pct",
  "atr_buckets": [
    {"name": "LOW", "atr_pct_max": 1.0},
    {"name": "MID", "atr_pct_max": 2.0},
    {"name": "HIGH", "atr_pct_max": null}
  ],
  "score_normalization": {
    "method": "linear_clamp",
    "ev_at_score_0": -0.010,
    "ev_at_score_100": 0.010
  },
  "states": {
    "favorable_enter": 65,
    "favorable_exit": 60,
    "risky_enter": 40,
    "risky_exit": 45,
    "avoid_enter": 25,
    "avoid_exit": 30
  },
  "views": {"operational_view": "executable"},
  "ml_component": {
    "user_enabled": false,
    "weight_pct": 0,
    "health_gate": {
      "require_status": "promoted",
      "min_oos_auc": 0.62,
      "min_clean_days": 15,
      "require_canary_passed": true
    }
  },
  "recalibration": {
    "auto_recompute_priors": true,
    "prior_refresh_hours": 24
  },
  "task": {"interval_seconds": 900}
}
"""


def upgrade() -> None:
    op.create_table(
        "crypto_ev_l3_replay_flags",
        sa.Column("shadow_trade_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("would_pass_l3", sa.Boolean(), nullable=True),
        sa.Column("replay_status", sa.Text(), nullable=False),
        sa.Column("l3_config_version", sa.Text(), nullable=False),
        sa.Column("replay_reason", sa.Text(), nullable=False),
        sa.Column("replay_details", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "replay_status IN ('PASSED','FAILED','UNREPLAYABLE')",
            name="ck_crypto_ev_l3_replay_status",
        ),
        sa.CheckConstraint(
            "(replay_status = 'PASSED' AND would_pass_l3 IS true) OR "
            "(replay_status = 'FAILED' AND would_pass_l3 IS false) OR "
            "(replay_status = 'UNREPLAYABLE' AND would_pass_l3 IS NULL)",
            name="ck_crypto_ev_l3_replay_status_bool",
        ),
        sa.ForeignKeyConstraint(["shadow_trade_id"], ["shadow_trades.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("shadow_trade_id"),
    )
    op.create_index(
        "idx_crypto_ev_l3_replay_flags_pass",
        "crypto_ev_l3_replay_flags",
        ["would_pass_l3", "computed_at"],
    )

    op.create_table(
        "crypto_ev_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("view", sa.Text(), nullable=False),
        sa.Column("window_hours", sa.Integer(), nullable=False),
        sa.Column("n_trades", sa.Integer(), nullable=False),
        sa.Column("n_excluded_no_pnl", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_excluded_unreplayable", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("ev_symbol", sa.Numeric(), nullable=True),
        sa.Column("ev_prior", sa.Numeric(), nullable=False),
        sa.Column("atr_bucket", sa.Text(), nullable=False),
        sa.Column("shrinkage_k", sa.Integer(), nullable=False),
        sa.Column("w", sa.Numeric(), nullable=False),
        sa.Column("ev_shrunk", sa.Numeric(), nullable=False),
        sa.Column("score", sa.Numeric(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ml_component_applied", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("ml_component_value", sa.Numeric(), nullable=True),
        sa.Column("ml_model_version", sa.Text(), nullable=True),
        sa.Column("config_version", sa.Text(), nullable=False),
        sa.Column("l3_config_version", sa.Text(), nullable=True),
        sa.Column("audit_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint("view IN ('executable','spectrum')", name="ck_crypto_ev_snapshots_view"),
        sa.CheckConstraint("state IN ('FAVORABLE','NEUTRAL','RISKY','AVOID','INSUFFICIENT_DATA')", name="ck_crypto_ev_snapshots_state"),
        sa.PrimaryKeyConstraint("id", "computed_at"),
    )
    op.execute(
        "CREATE INDEX idx_crypto_ev_snapshots_symbol_current "
        "ON crypto_ev_snapshots (symbol, computed_at DESC)"
    )
    op.execute(
        "CREATE INDEX idx_crypto_ev_snapshots_view_current "
        "ON crypto_ev_snapshots (view, symbol, computed_at DESC)"
    )

    op.execute(
        """
        CREATE OR REPLACE VIEW crypto_ev_current AS
        SELECT DISTINCT ON (symbol, view)
               id, computed_at, symbol, view, window_hours, n_trades,
               n_excluded_no_pnl, n_excluded_unreplayable, ev_symbol, ev_prior, atr_bucket,
               shrinkage_k, w, ev_shrunk, score, state,
               ml_component_applied, ml_component_value, ml_model_version,
               config_version, l3_config_version, audit_json
          FROM crypto_ev_snapshots
         ORDER BY symbol, view, computed_at DESC, id DESC
        """
    )

    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO config_profiles (id, user_id, pool_id, config_type, config_json, is_active, created_at, updated_at)
            SELECT gen_random_uuid(), u.id, NULL, 'crypto_ev', CAST(:cfg AS jsonb), true, now(), now()
              FROM users u
             WHERE NOT EXISTS (
                   SELECT 1 FROM config_profiles cp
                    WHERE cp.user_id = u.id
                      AND cp.pool_id IS NULL
                      AND cp.config_type = 'crypto_ev'
             )
            """
        ),
        {"cfg": _DEFAULT_CONFIG_SQL},
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS crypto_ev_current")
    op.drop_index("idx_crypto_ev_snapshots_view_current", table_name="crypto_ev_snapshots")
    op.drop_index("idx_crypto_ev_snapshots_symbol_current", table_name="crypto_ev_snapshots")
    op.drop_table("crypto_ev_snapshots")
    op.drop_index("idx_crypto_ev_l3_replay_flags_pass", table_name="crypto_ev_l3_replay_flags")
    op.drop_table("crypto_ev_l3_replay_flags")
    op.execute("DELETE FROM config_profiles WHERE config_type = 'crypto_ev'")

"""B.1 (2026-09-03): allow the 3600s (1h) late-anchor delay in
ohlcv_settlement_latency_samples.

ohlcv_shadow is immutable (CHECK is_closed IS TRUE + ON CONFLICT DO
NOTHING): a Gate revision arriving after the sampler's previous 300s
ceiling would freeze the wrong value forever, undetected. This widens the
CHECK constraint so the sampler (research_ohlcv_service.
SETTLEMENT_LATENCY_DELAYS_SECONDS) can record a 1h ground-truth read
against which the 10-300s samples get compared.

Revision ID: 213_settlement_latency_late_anchor
Revises: 212_shadow_monitor_unstick
"""

from alembic import op
import sqlalchemy as sa


revision = "213_settlement_latency_late_anchor"
down_revision = "212_shadow_monitor_unstick"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE ohlcv_settlement_latency_samples "
        "DROP CONSTRAINT IF EXISTS ck_ohlcv_settlement_latency_delay"
    ))
    op.execute(sa.text(
        "ALTER TABLE ohlcv_settlement_latency_samples "
        "ADD CONSTRAINT ck_ohlcv_settlement_latency_delay "
        "CHECK (delay_target_seconds IN (10, 30, 60, 120, 300, 3600))"
    ))


def downgrade() -> None:
    op.execute(sa.text(
        "ALTER TABLE ohlcv_settlement_latency_samples "
        "DROP CONSTRAINT IF EXISTS ck_ohlcv_settlement_latency_delay"
    ))
    op.execute(sa.text(
        "ALTER TABLE ohlcv_settlement_latency_samples "
        "ADD CONSTRAINT ck_ohlcv_settlement_latency_delay "
        "CHECK (delay_target_seconds IN (10, 30, 60, 120, 300))"
    ))

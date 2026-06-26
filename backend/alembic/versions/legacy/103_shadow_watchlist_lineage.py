"""Add watchlist lineage columns to shadow_trades for ML traceability.

Revision ID: 103_shadow_watchlist_lineage
Revises: 102_suggestion_hash
Create Date: 2026-06-22

Contexto:
  shadow_trades atualmente não registra de qual watchlist a promoção veio.
  Esse gap impede atribuição correta de trades ao perfil/watchlist no dataset
  de treinamento do CatBoost (Lane 2), causando distribuição shift entre
  treino/teste quando novos perfis PI surgem.

  Colunas adicionadas:
    watchlist_id          UUID NULL   — PK da pipeline_watchlist origem
    watchlist_name        VARCHAR(150) NULL — snapshot do nome no momento da criação
    watchlist_level       VARCHAR(10) NULL  — L1 / L2 / L3 / custom
    source_watchlist_id   UUID NULL   — pipeline_watchlists.source_watchlist_id (self-ref)
    lineage_confidence    VARCHAR(30) NULL  — EXACT / JOIN_PROFILE_UNIQUE / AMBIGUOUS_PROFILE /
                                             UNRESOLVED / LEGACY_UNKNOWN
    lineage_source        VARCHAR(50) NULL  — pipeline_scan / backfill / etc.
    lineage_resolved_at   TIMESTAMPTZ NULL  — quando a lineage foi resolvida

  Todos nullable: back-compat com linhas históricas; backfill separado via
  shadow_lineage_backfill.py preenche o que for possível.

  asyncpg: cada op.execute() contém exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as pgUUID


revision = "103_shadow_watchlist_lineage"
down_revision = "102_suggestion_hash"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_trades",
        sa.Column("watchlist_id", pgUUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("watchlist_name", sa.String(150), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("watchlist_level", sa.String(10), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("source_watchlist_id", pgUUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("lineage_confidence", sa.String(30), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("lineage_source", sa.String(50), nullable=True),
    )
    op.add_column(
        "shadow_trades",
        sa.Column("lineage_resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_shadow_trades_watchlist_id "
        "ON shadow_trades (watchlist_id) WHERE watchlist_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_shadow_trades_watchlist_level "
        "ON shadow_trades (watchlist_level, created_at DESC) WHERE watchlist_level IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_shadow_trades_profile_watchlist "
        "ON shadow_trades (profile_id, watchlist_id) "
        "WHERE profile_id IS NOT NULL AND watchlist_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_shadow_trades_lineage_confidence "
        "ON shadow_trades (lineage_confidence) WHERE lineage_confidence IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_shadow_trades_lineage_confidence")
    op.execute("DROP INDEX IF EXISTS ix_shadow_trades_profile_watchlist")
    op.execute("DROP INDEX IF EXISTS ix_shadow_trades_watchlist_level")
    op.execute("DROP INDEX IF EXISTS ix_shadow_trades_watchlist_id")
    op.drop_column("shadow_trades", "lineage_resolved_at")
    op.drop_column("shadow_trades", "lineage_source")
    op.drop_column("shadow_trades", "lineage_confidence")
    op.drop_column("shadow_trades", "source_watchlist_id")
    op.drop_column("shadow_trades", "watchlist_level")
    op.drop_column("shadow_trades", "watchlist_name")
    op.drop_column("shadow_trades", "watchlist_id")

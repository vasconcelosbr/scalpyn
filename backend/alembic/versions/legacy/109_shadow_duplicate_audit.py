"""Create shadow_trade_duplicate_audit + shadow_trades.superseded_by_id.

Revision ID: 109_shadow_dup_audit
Revises: 108_suggestion_feedback
Create Date: 2026-06-24

Contexto (Profile Intelligence Adaptive Loop reformulation, audit 2026-06-24,
item 4 do checklist pos-VALIDACAO_GERAL):

  VALIDACAO_GERAL_PROFILE_INTELLIGENCE_ADAPTIVE_LOOP.md (Fase 14) confirmou
  com query real que o comentario em shadow_trade_service.py afirmando
  idempotencia via "UNIQUE INDEX (migration 047)" sobre decision_id e falso —
  nao existe nenhum indice UNICO sobre essa coluna, apenas um indice comum
  (ix_shadow_trades_decision_id). 38 grupos de decision_id duplicados foram
  encontrados, varios com outcomes conflitantes (mesma decisao registrada
  como TP_HIT em uma linha e SL_HIT em outra) — risco real de contaminacao
  de dataset de treino de ML.

  Esta migration e SOMENTE estrutura — nao apaga nada, nao resolve nada
  ainda. Adiciona:

    shadow_trades.superseded_by_id UUID NULL
      Marcacao NAO-DESTRUTIVA. NULL = linha canonica (ou sem duplicata).
      Apontando para outro shadow_trades.id = esta linha foi superada por
      aquela (mesmo decision_id, escolhida como nao-canonica pelo
      resolver). A linha em si NUNCA e deletada.

    shadow_trade_duplicate_audit
      Uma linha por GRUPO de decision_id duplicado processado, gravando
      todos os ids envolvidos, qual foi escolhido canonico, os outcomes de
      cada um, e se houve conflito (distinct_outcomes_count > 1).

  O indice UNICO parcial que de fato previne NOVOS duplicados
  (CREATE UNIQUE INDEX ... ON shadow_trades(decision_id) WHERE
  superseded_by_id IS NULL) só pode ser criado DEPOIS que
  backend/scripts/fix_shadow_trade_duplicate_decision_id.py --commit rodar
  e marcar os 38 grupos historicos — caso contrario a criacao do indice
  falharia (ja existem linhas duplicadas com superseded_by_id NULL). Esse
  indice fica para uma migration 110 separada, executada apos o backfill.

  asyncpg: cada op.execute() contem exatamente um statement.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision = "109_shadow_dup_audit"
down_revision = "108_suggestion_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "shadow_trades",
        sa.Column("superseded_by_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_shadow_trades_superseded_by_id",
        "shadow_trades", "shadow_trades",
        ["superseded_by_id"], ["id"],
    )
    op.execute(
        "COMMENT ON COLUMN shadow_trades.superseded_by_id IS "
        "'Non-destructive duplicate marking. NULL = canonical row. "
        "Points to the shadow_trades.id chosen as canonical for the same "
        "decision_id. The row itself is NEVER deleted.'"
    )

    op.create_table(
        "shadow_trade_duplicate_audit",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("decision_id", sa.BigInteger, nullable=False),
        sa.Column("member_ids", JSONB, nullable=False),
        sa.Column("canonical_id", UUID(as_uuid=True), nullable=False),
        sa.Column("superseded_ids", JSONB, nullable=False),
        sa.Column("outcomes", JSONB, nullable=False),
        sa.Column("distinct_outcomes_count", sa.Integer, nullable=False),
        sa.Column("conflict", sa.Boolean, nullable=False),
        sa.Column("resolution_reason", sa.String, nullable=False),
        sa.Column("triggered_by", sa.String, nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_shadow_trade_duplicate_audit_decision_id",
        "shadow_trade_duplicate_audit", ["decision_id"],
    )
    op.execute(
        "COMMENT ON TABLE shadow_trade_duplicate_audit IS "
        "'One row per duplicate decision_id group resolved by "
        "shadow_trade_duplicate_resolver.py. Read-only audit trail — never "
        "mutated after insert, never deletes the underlying shadow_trades rows.'"
    )


def downgrade() -> None:
    op.drop_index("ix_shadow_trade_duplicate_audit_decision_id", table_name="shadow_trade_duplicate_audit")
    op.drop_table("shadow_trade_duplicate_audit")
    op.drop_constraint("fk_shadow_trades_superseded_by_id", "shadow_trades", type_="foreignkey")
    op.drop_column("shadow_trades", "superseded_by_id")

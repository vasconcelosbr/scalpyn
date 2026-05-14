"""Add market-context columns to shadow_trades for ML training.

Revision ID: 052_shadow_market_ctx
Revises: 051_shadow_exit_snap
Create Date: 2026-05-14

Contexto
--------
Até esta migration o ``shadow_trades`` carregava só o snapshot técnico
do próprio símbolo (``features_snapshot`` na entrada e
``features_snapshot_exit`` na saída). Faltavam 4 campos de
**contexto de mercado** que o XGBoost precisa pra contextualizar a
decisão:

* ``btc_price_at_entry``    — preço do BTC_USDT no instante da entrada
* ``btc_change_1h_pct``     — variação % do BTC na última hora
* ``funding_rate_at_entry`` — funding rate do ativo (NULL se spot puro)
* ``n_concurrent_signals``  — quantos ALLOW dispararam no mesmo minuto

Fix
---
Adiciona as 4 colunas como nullable. Backfill é feito offline pelo
script ``backend/scripts/backfill_shadow_trade_context.py``; novos
shadows são enriquecidos pelo ``shadow_trade_monitor`` após resolver a
entrada (additive — não toca o fluxo de TP/SL/timeout).

Rule N/N+1
----------
Coluna fora de ``_critical_schema.py`` — não bloqueia startup. Pode
entrar em ``CRITICAL_COLUMNS`` no próximo deploy se a leitura virar
crítica para algum path quente (não é hoje: só ML offline lê).
"""

from alembic import op


revision = "052_shadow_market_ctx"
down_revision = "051_shadow_exit_snap"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE shadow_trades
            ADD COLUMN IF NOT EXISTS btc_price_at_entry    NUMERIC(18,8),
            ADD COLUMN IF NOT EXISTS btc_change_1h_pct     NUMERIC(8,4),
            ADD COLUMN IF NOT EXISTS funding_rate_at_entry NUMERIC(10,6),
            ADD COLUMN IF NOT EXISTS n_concurrent_signals  INTEGER
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE shadow_trades
            DROP COLUMN IF EXISTS btc_price_at_entry,
            DROP COLUMN IF EXISTS btc_change_1h_pct,
            DROP COLUMN IF EXISTS funding_rate_at_entry,
            DROP COLUMN IF EXISTS n_concurrent_signals
        """
    )

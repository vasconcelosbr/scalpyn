"""Backfill shadow_trades.direction lowercase 'long' → canonical 'SPOT'.

Revision ID: 053_shadow_dir_canon
Revises: 052_shadow_market_ctx
Create Date: 2026-05-15

Contexto
--------
Task #292 estabeleceu o vocabulário canônico de ``direction`` como
``UPPERCASE`` ``{'LONG', 'SHORT', 'NEUTRAL', 'SPOT'}`` em
``decisions_log``. ``shadow_trade_service._create_from_decision``,
porém, gravava o literal lowercase ``'long'`` em
``shadow_trades.direction`` (resquício do shape antigo). Resultado:
107 trades com ``direction = 'long'`` em produção, fora do
vocabulário canônico.

Fix
---
* O produtor (``_create_from_decision``) passou a herdar
  ``decision.direction`` (com fallback ``'SPOT'``).
* Esta migration faz o backfill dos registros existentes:
  ``UPDATE shadow_trades SET direction = 'SPOT' WHERE direction = 'long'``.

Reversibilidade
---------------
``downgrade`` reverte para ``'long'`` (estritamente o conjunto que esta
migration tocou — usa marker via ``WHERE direction = 'SPOT'``). Como
novos shadows passam a gravar ``'SPOT'`` diretamente, a reversão é
aproximada (mistura legado + novos), mas mantém compat com qualquer
consumidor antigo que ainda esperasse lowercase.
"""

from __future__ import annotations

from alembic import op


revision = "053_shadow_dir_canon"
down_revision = "052_shadow_market_ctx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE shadow_trades
           SET direction = 'SPOT'
         WHERE direction = 'long'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE shadow_trades
           SET direction = 'long'
         WHERE direction = 'SPOT'
        """
    )

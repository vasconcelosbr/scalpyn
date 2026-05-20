"""Add trade_tracking.exit_metrics_json — symmetric exit snapshot.

Revision ID: 059_tt_exit_metrics_json
Revises: 058_ts_entry_safety_net
Create Date: 2026-05-20

Contexto
--------
Task #316 (PHASE A da Task #315 / runbook
``exit-metrics-symmetric-capture.md``). Hoje só ``shadow_trades`` tem
captura simétrica de indicadores entrada↔saída (coluna
``features_snapshot_exit`` adicionada em 051). ``trade_tracking`` (trades
reais via ``TradeMonitorService._close_trade``) grava apenas
``exit_price/exit_time/outcome/pnl_pct/holding_seconds`` — sem nenhum
snapshot de indicadores na saída. XGBoost e UI pós-trade ficam cegos
para deterioração/fortalecimento durante o holding.

Fix
---
* Coluna ``exit_metrics_json JSONB NULL`` (nullable — trades ainda em
  aberto e linhas históricas pré-deploy ficam em NULL).
* Preenchida por ``TradeMonitorService._close_trade`` via novo helper
  ``app.services.exit_metrics.build_exit_snapshot`` no formato **flat**
  ``{key: scalar}`` — mesmo contrato exigido pelo
  ``DatasetBuilder.extract_features`` (gotcha Task #290: nested quebra
  com ``TypeError: float(dict)``).

Rule N/N+1
----------
Coluna NÃO entra em ``_critical_schema.CRITICAL_COLUMNS`` neste deploy.
Promoção fica para deploy N+1 após a Fase E do runbook (14 dias com
paridade > 99%).

ID curto obrigatório (gotcha 2026-05-15): ``alembic_version.version_num``
é ``VARCHAR(32)``. Este ID tem 23 chars — dentro do limite.
"""

from alembic import op


revision = "059_tt_exit_metrics_json"
down_revision = "058_ts_entry_safety_net"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trade_tracking "
        "ADD COLUMN IF NOT EXISTS exit_metrics_json JSONB NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE trade_tracking DROP COLUMN IF EXISTS exit_metrics_json"
    )

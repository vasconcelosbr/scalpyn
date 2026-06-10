"""Shadow Instrumentation — Fase 1/2/3: labels e métricas de qualidade.

Revision ID: 071_shadow_instrumentation
Revises: 070_ml_models_blob
Create Date: 2026-06-09

Adiciona 12 colunas novas (todas nullable, additive-only) em
``shadow_trades`` e espelho idêntico em ``trade_simulations``.

Colunas adicionadas:

  Fase 1 — Instrumentação:
    mae_at / mfe_at          TIMESTAMPTZ — candle onde ocorreu MAE/MFE
    barrier_touched          VARCHAR(20) — 'TP'|'SL'|'BOTH_SAME_CANDLE'|'NONE'
    barrier_touched_at       TIMESTAMPTZ — primeiro toque de barreira
    intrabar_convention      VARCHAR(20) — convenção aplicada ('SL_FIRST')
    final_return_pct         DOUBLE PRECISION — retorno no close do TIMEOUT

  Fase 2 — Labels líquidos de fees:
    net_return_pct           DOUBLE PRECISION — retorno líquido do fee round-trip
    fee_roundtrip_pct_applied DOUBLE PRECISION — snapshot do fee usado

  Fase 3 — Barreiras volatility-adjusted (registro, modo FIXED agora):
    barrier_mode             VARCHAR(20) — 'FIXED'|'ATR_ADAPTIVE'
    tp_pct_applied           DOUBLE PRECISION — barreira TP% efetiva na abertura
    sl_pct_applied           DOUBLE PRECISION — barreira SL% efetiva na abertura
    atr_pct_at_entry         DOUBLE PRECISION — ATR% do ativo no momento da entrada

Invariante: toda coluna nova em shadow_trades é espelhada em
trade_simulations na mesma migração.
"""

from alembic import op
import sqlalchemy as sa

revision = "071_shadow_instrumentation"
down_revision = "070_ml_models_blob"
branch_labels = None
depends_on = None

_SHADOW_DDL = """
    ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS mae_at                    TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS mfe_at                    TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS barrier_touched           VARCHAR(20),
        ADD COLUMN IF NOT EXISTS barrier_touched_at        TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS intrabar_convention       VARCHAR(20),
        ADD COLUMN IF NOT EXISTS final_return_pct          DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS net_return_pct            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS fee_roundtrip_pct_applied DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS barrier_mode              VARCHAR(20),
        ADD COLUMN IF NOT EXISTS tp_pct_applied            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS sl_pct_applied            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS atr_pct_at_entry          DOUBLE PRECISION
"""

_SIM_DDL = """
    ALTER TABLE trade_simulations
        ADD COLUMN IF NOT EXISTS mae_at                    TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS mfe_at                    TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS barrier_touched           VARCHAR(20),
        ADD COLUMN IF NOT EXISTS barrier_touched_at        TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS intrabar_convention       VARCHAR(20),
        ADD COLUMN IF NOT EXISTS final_return_pct          DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS net_return_pct            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS fee_roundtrip_pct_applied DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS barrier_mode              VARCHAR(20),
        ADD COLUMN IF NOT EXISTS tp_pct_applied            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS sl_pct_applied            DOUBLE PRECISION,
        ADD COLUMN IF NOT EXISTS atr_pct_at_entry          DOUBLE PRECISION
"""

_SHADOW_DROP = """
    ALTER TABLE shadow_trades
        DROP COLUMN IF EXISTS mae_at,
        DROP COLUMN IF EXISTS mfe_at,
        DROP COLUMN IF EXISTS barrier_touched,
        DROP COLUMN IF EXISTS barrier_touched_at,
        DROP COLUMN IF EXISTS intrabar_convention,
        DROP COLUMN IF EXISTS final_return_pct,
        DROP COLUMN IF EXISTS net_return_pct,
        DROP COLUMN IF EXISTS fee_roundtrip_pct_applied,
        DROP COLUMN IF EXISTS barrier_mode,
        DROP COLUMN IF EXISTS tp_pct_applied,
        DROP COLUMN IF EXISTS sl_pct_applied,
        DROP COLUMN IF EXISTS atr_pct_at_entry
"""

_SIM_DROP = """
    ALTER TABLE trade_simulations
        DROP COLUMN IF EXISTS mae_at,
        DROP COLUMN IF EXISTS mfe_at,
        DROP COLUMN IF EXISTS barrier_touched,
        DROP COLUMN IF EXISTS barrier_touched_at,
        DROP COLUMN IF EXISTS intrabar_convention,
        DROP COLUMN IF EXISTS final_return_pct,
        DROP COLUMN IF EXISTS net_return_pct,
        DROP COLUMN IF EXISTS fee_roundtrip_pct_applied,
        DROP COLUMN IF EXISTS barrier_mode,
        DROP COLUMN IF EXISTS tp_pct_applied,
        DROP COLUMN IF EXISTS sl_pct_applied,
        DROP COLUMN IF EXISTS atr_pct_at_entry
"""


def upgrade() -> None:
    op.execute(sa.text(_SHADOW_DDL))
    op.execute(sa.text(_SIM_DDL))


def downgrade() -> None:
    op.execute(sa.text(_SHADOW_DROP))
    op.execute(sa.text(_SIM_DROP))

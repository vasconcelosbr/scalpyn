"""R6 additive schema for multilayer verdicts and the indicator registry.

Revision ID: 215_r6_multilayer_contracts
Revises: 214_ohlcv_canonical_cutover
"""

from __future__ import annotations

import json
from pathlib import Path

from alembic import op
import sqlalchemy as sa


revision = "215_r6_multilayer_contracts"
down_revision = "214_ohlcv_canonical_cutover"
branch_labels = None
depends_on = None


REGISTRY_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "contracts"
    / "r6_indicator_registry_v1.json"
)


def _registry_document() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def upgrade() -> None:
    # Nullable, default-free columns preserve historical semantics.  NULL means
    # "captured before R6"; NONE is reserved for new R6-evaluated events.
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS rejected_by_layer TEXT NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS rejected_by_rule TEXT NULL
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        ADD COLUMN IF NOT EXISTS layer_verdicts JSONB NULL
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'ck_shadow_rejected_by_layer_r6'
               AND conrelid = 'shadow_trades'::regclass
          ) THEN
            ALTER TABLE shadow_trades
              ADD CONSTRAINT ck_shadow_rejected_by_layer_r6
              CHECK (
                rejected_by_layer IS NULL
                OR rejected_by_layer IN ('L1', 'L2', 'L3', 'NONE')
              ) NOT VALID;
          END IF;
        END $$
    """))
    op.execute(sa.text("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'ck_shadow_layer_verdicts_object_r6'
               AND conrelid = 'shadow_trades'::regclass
          ) THEN
            ALTER TABLE shadow_trades
              ADD CONSTRAINT ck_shadow_layer_verdicts_object_r6
              CHECK (
                layer_verdicts IS NULL OR jsonb_typeof(layer_verdicts) = 'object'
              ) NOT VALID;
          END IF;
        END $$
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        VALIDATE CONSTRAINT ck_shadow_rejected_by_layer_r6
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        VALIDATE CONSTRAINT ck_shadow_layer_verdicts_object_r6
    """))

    op.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS indicator_registry (
          indicator_id TEXT PRIMARY KEY,
          alias_of TEXT NULL,
          phenomenon TEXT NOT NULL,
          owning_layer TEXT NOT NULL,
          timeframe TEXT NOT NULL,
          producer TEXT NULL,
          source_family TEXT NOT NULL,
          is_blocking BOOLEAN NOT NULL,
          composed_inputs JSONB NOT NULL,
          contract_version TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CONSTRAINT ck_indicator_registry_owner_r6
            CHECK (owning_layer IN ('L1', 'L2', 'L3')),
          CONSTRAINT ck_indicator_registry_inputs_r6
            CHECK (jsonb_typeof(composed_inputs) = 'array')
        )
    """))

    document = _registry_document()
    for row in document["indicators"]:
        params = {
            **row,
            "composed_inputs": json.dumps(row["composed_inputs"]),
            "contract_version": document["contract_version"],
        }
        op.execute(
            sa.text("""
                INSERT INTO indicator_registry
                    (indicator_id, alias_of, phenomenon, owning_layer, timeframe,
                     producer, source_family, is_blocking, composed_inputs,
                     contract_version)
                VALUES
                    (:indicator_id, :alias_of, :phenomenon, :owning_layer,
                     :timeframe, :producer, :source_family, :is_blocking,
                     CAST(:composed_inputs AS jsonb), :contract_version)
                ON CONFLICT (indicator_id) DO UPDATE SET
                    alias_of = EXCLUDED.alias_of,
                    phenomenon = EXCLUDED.phenomenon,
                    owning_layer = EXCLUDED.owning_layer,
                    timeframe = EXCLUDED.timeframe,
                    producer = EXCLUDED.producer,
                    source_family = EXCLUDED.source_family,
                    is_blocking = EXCLUDED.is_blocking,
                    composed_inputs = EXCLUDED.composed_inputs,
                    contract_version = EXCLUDED.contract_version
            """).bindparams(**params)
        )
    op.execute(sa.text("""
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conname = 'fk_indicator_registry_alias_r6'
               AND conrelid = 'indicator_registry'::regclass
          ) THEN
            ALTER TABLE indicator_registry
              ADD CONSTRAINT fk_indicator_registry_alias_r6
              FOREIGN KEY (alias_of) REFERENCES indicator_registry(indicator_id);
          END IF;
        END $$
    """))


def downgrade() -> None:
    op.execute(sa.text("DROP TABLE IF EXISTS indicator_registry"))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        DROP CONSTRAINT IF EXISTS ck_shadow_layer_verdicts_object_r6
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        DROP CONSTRAINT IF EXISTS ck_shadow_rejected_by_layer_r6
    """))
    op.execute(sa.text("""
        ALTER TABLE shadow_trades
        DROP COLUMN IF EXISTS layer_verdicts,
        DROP COLUMN IF EXISTS rejected_by_rule,
        DROP COLUMN IF EXISTS rejected_by_layer
    """))

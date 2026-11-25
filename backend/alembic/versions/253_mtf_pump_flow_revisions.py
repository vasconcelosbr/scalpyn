"""Deferred, immutable re-reading of the MTF Pump flow context.

An MTF evaluation is frozen ~25 s after the 5m close, usually before the Pump cycle
publishes the last closed minute, so its flow context reads ``unavailable``
(``delayed_window``). The evaluation stays untouched; ONE later reading per
evaluation is stored here with provenance (original confirmation, capture time,
revision time and delay). Rows are immutable: inserts use ON CONFLICT DO NOTHING
and a trigger rejects UPDATE.

The schedule lives in config (``mtf_analysis.pump_flow.revision``), outside the flow
config_hash and the engine identity. Existing configs receive the preset block only
when it fits under their own ``max_evaluation_delay_seconds``.

Revision ID: 253_mtf_pump_flow_revisions
Revises: 252_mtf_signal_replay
"""
import json

from alembic import op
import sqlalchemy as sa

revision = '253_mtf_pump_flow_revisions'
down_revision = '252_mtf_signal_replay'
branch_labels = None
depends_on = None

# Same values as schemas.mtf_chart.pump_flow_preset()['revision'] (editable hypothesis).
REVISION = {'enabled': True, 'delay_seconds': 120, 'batch_limit': 200}


def upgrade():
    op.execute(sa.text('''CREATE TABLE IF NOT EXISTS mtf_pump_flow_revisions (
        evaluation_id uuid PRIMARY KEY REFERENCES mtf_chart_evaluations(id),
        user_id uuid NOT NULL REFERENCES users(id),
        symbol text NOT NULL,
        cutoff timestamptz NOT NULL,
        original_captured_at timestamptz,
        revised_at timestamptz NOT NULL,
        delay_seconds integer NOT NULL CHECK (delay_seconds >= 0),
        original_confirmation text NOT NULL,
        confirmation text NOT NULL,
        config_hash text NOT NULL,
        context jsonb NOT NULL,
        CHECK (context->>'operational_effect' = 'false'))'''))
    op.execute(sa.text('CREATE INDEX IF NOT EXISTS ix_mtf_pump_flow_revisions_user ON mtf_pump_flow_revisions(user_id, cutoff DESC)'))
    # The minute-by-minute candidate scan filters by owner and recency only; the existing
    # (user_id, watchlist_id, symbol, observed_at) index does not serve that range.
    op.execute(sa.text('CREATE INDEX IF NOT EXISTS ix_mtf_chart_evaluations_user_observed ON mtf_chart_evaluations(user_id, observed_at DESC)'))
    op.execute(sa.text('''CREATE OR REPLACE FUNCTION mtf_pump_flow_revisions_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mtf_pump_flow_revisions rows are immutable (deferred readings)';
        END;
        $$ LANGUAGE plpgsql'''))
    op.execute(sa.text('DROP TRIGGER IF EXISTS mtf_pump_flow_revisions_no_update ON mtf_pump_flow_revisions'))
    op.execute(sa.text('''CREATE TRIGGER mtf_pump_flow_revisions_no_update BEFORE UPDATE ON mtf_pump_flow_revisions
        FOR EACH ROW EXECUTE FUNCTION mtf_pump_flow_revisions_immutable()'''))
    op.execute(sa.text('''UPDATE config_profiles
        SET config_json = jsonb_set(config_json, '{pump_flow,revision}', CAST(:revision AS jsonb)), updated_at = now()
        WHERE config_type = 'mtf_analysis'
          AND jsonb_typeof(config_json->'pump_flow') = 'object'
          AND NOT (config_json->'pump_flow' ? 'revision')
          AND (config_json->'pump_flow'->>'max_evaluation_delay_seconds')::int > :delay''').bindparams(
        revision=json.dumps(REVISION), delay=REVISION['delay_seconds']))


def downgrade():
    op.execute(sa.text("UPDATE config_profiles SET config_json = config_json #- '{pump_flow,revision}' "
                       "WHERE config_type = 'mtf_analysis' AND config_json->'pump_flow' ? 'revision'"))
    op.execute(sa.text('DROP TABLE IF EXISTS mtf_pump_flow_revisions'))
    op.execute(sa.text('DROP INDEX IF EXISTS ix_mtf_chart_evaluations_user_observed'))
    op.execute(sa.text('DROP FUNCTION IF EXISTS mtf_pump_flow_revisions_immutable()'))

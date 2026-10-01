"""Pump Monitor research dataset: per-minute rows and offline labels.

Additive only: two new tables, range-partitioned by day on ``ts`` with a
DEFAULT partition so a late partition job never loses a write. Daily
partitions are created ahead and dropped after the configured retention by
``app.tasks.pump_research.maintain``. No existing table is altered.

Revision ID: 230_pump_research_dataset
Revises: 229_pump_monitor
"""
from alembic import op
import sqlalchemy as sa

revision = "230_pump_research_dataset"
down_revision = "229_pump_monitor"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(sa.text("""
        CREATE TABLE pump_research_minute (
            symbol             varchar(40)       NOT NULL,
            ts                 timestamptz       NOT NULL,
            cycle_at           timestamptz       NOT NULL,
            config_version     integer           NOT NULL,
            config_hash        varchar(64)       NOT NULL,
            value_keys_hash    varchar(64)       NOT NULL,
            price              double precision,
            mid                double precision,
            best_bid           double precision,
            best_ask           double precision,
            bucket_open        double precision,
            bucket_high        double precision,
            bucket_low         double precision,
            bucket_close       double precision,
            bucket_partial     boolean,
            spread_pct         double precision,
            slippage_buy_pct   double precision,
            slippage_sell_pct  double precision,
            insufficient_depth_buy  boolean NOT NULL DEFAULT false,
            insufficient_depth_sell boolean NOT NULL DEFAULT false,
            score              double precision,
            score_pre_veto     double precision,
            score_confidence   double precision,
            exhaustion_flag    boolean,
            exhaustion_triggers text[],
            alerts             text[],
            pool_member        boolean NOT NULL DEFAULT false,
            data_age_seconds   double precision,
            vals               real[]            NOT NULL,
            contributions      real[],
            categorical        jsonb,
            null_reasons       jsonb,
            PRIMARY KEY (symbol, ts)
        ) PARTITION BY RANGE (ts)
    """))
    op.execute(sa.text("CREATE TABLE pump_research_minute_default PARTITION OF pump_research_minute DEFAULT"))
    op.execute(sa.text("CREATE INDEX ix_pump_research_minute_ts ON pump_research_minute (ts)"))

    # Key order of ``vals`` / ``contributions`` per hash, so the narrow arrays stay decodable.
    op.execute(sa.text("""
        CREATE TABLE pump_research_value_keys (
            value_keys_hash   varchar(64) PRIMARY KEY,
            value_keys        text[]      NOT NULL,
            contribution_keys text[]      NOT NULL,
            first_seen_at     timestamptz NOT NULL DEFAULT now()
        )
    """))

    op.execute(sa.text("""
        CREATE TABLE pump_research_labels (
            symbol             varchar(40)  NOT NULL,
            ts                 timestamptz  NOT NULL,
            label_set_version  varchar(40)  NOT NULL,
            labeled_at         timestamptz  NOT NULL,
            label_config_hash  varchar(64)  NOT NULL,
            price_t            double precision,
            cost_pct           double precision,
            fee_roundtrip_pct  double precision,
            returns            jsonb        NOT NULL,
            path               jsonb        NOT NULL,
            barriers           jsonb        NOT NULL,
            reason             varchar(40),
            PRIMARY KEY (symbol, ts, label_set_version)
        ) PARTITION BY RANGE (ts)
    """))
    op.execute(sa.text("CREATE TABLE pump_research_labels_default PARTITION OF pump_research_labels DEFAULT"))
    op.execute(sa.text("CREATE INDEX ix_pump_research_labels_ts ON pump_research_labels (ts)"))

    # Day partitions for today and the next days exist before the first write;
    # the maintenance task keeps creating them ahead from then on.
    op.execute(sa.text("""
        DO $$
        DECLARE d date;
        BEGIN
          FOR d IN SELECT generate_series((now() AT TIME ZONE 'UTC')::date, (now() AT TIME ZONE 'UTC')::date + 3, interval '1 day')::date LOOP
            EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF pump_research_minute FOR VALUES FROM (%L) TO (%L)',
                           'pump_research_minute_' || to_char(d, 'YYYYMMDD'), (d::timestamp AT TIME ZONE 'UTC'), ((d + 1)::timestamp AT TIME ZONE 'UTC'));
            EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF pump_research_labels FOR VALUES FROM (%L) TO (%L)',
                           'pump_research_labels_' || to_char(d, 'YYYYMMDD'), (d::timestamp AT TIME ZONE 'UTC'), ((d + 1)::timestamp AT TIME ZONE 'UTC'));
          END LOOP;
        END $$
    """))


def downgrade():
    op.execute(sa.text("DROP TABLE IF EXISTS pump_research_labels"))
    op.execute(sa.text("DROP TABLE IF EXISTS pump_research_value_keys"))
    op.execute(sa.text("DROP TABLE IF EXISTS pump_research_minute"))

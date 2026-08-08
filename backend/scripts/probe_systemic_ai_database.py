"""Emit a secret-free, read-only database capability snapshot."""

from __future__ import annotations

import json
import os

import psycopg2
from psycopg2.extensions import make_dsn


def main() -> None:
    database_url = os.environ.get("DATABASE_PUBLIC_URL")
    if not database_url and os.environ.get("RAILWAY_TCP_PROXY_DOMAIN"):
        database_url = make_dsn(
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            host=os.environ["RAILWAY_TCP_PROXY_DOMAIN"],
            port=os.environ["RAILWAY_TCP_PROXY_PORT"],
            sslmode="require",
        )
    if not database_url:
        database_url = os.environ["DATABASE_URL"]
    connection = psycopg2.connect(database_url)
    connection.set_session(readonly=True, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    current_database(),
                    current_user,
                    current_setting('server_version'),
                    EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'pgcrypto'
                    ),
                    EXISTS (
                        SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements'
                    ),
                    to_regclass('public.alembic_version')::text
                """
            )
            database, user, version, pgcrypto, pg_stat_statements, alembic_table = cursor.fetchone()
            alembic_head = None
            if alembic_table:
                cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
                alembic_head = [row[0] for row in cursor.fetchall()]
            expected_tables = (
                "ai_prompt_versions", "ai_model_aliases", "ai_model_resolutions",
                "ai_configuration_bundles", "ai_dataset_snapshots", "ai_requests",
                "ai_jobs", "ai_results", "ai_usage_records", "ai_budget_policies",
                "ai_tool_call_audits", "decision_hypotheses", "ai_change_sets",
                "regeneration_runs", "experiment_links", "decision_memory",
                "context_fingerprints", "mutation_fingerprints", "causal_evidence_refs",
            )
            cursor.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                ORDER BY table_name
                """,
                (list(expected_tables),),
            )
            existing_systemic_tables = [row[0] for row in cursor.fetchall()]
            cursor.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name IN (
                    'shadow_trade_analysis_jobs', 'profile_ai_reviews',
                    'profile_suggestions', 'shadow_trades'
                  )
                  AND column_name IN (
                    'tenant_id', 'ai_request_id', 'heartbeat_at', 'lease_owner',
                    'lease_expires_at', 'attempt', 'max_attempts', 'retry_after',
                    'terminal_reason', 'last_error_code',
                    'last_error_safe_message', 'configuration_bundle_id'
                  )
                ORDER BY table_name, column_name
                """
            )
            existing_bridge_columns = [f"{row[0]}.{row[1]}" for row in cursor.fetchall()]
        print(
            json.dumps(
                {
                    "database": database,
                    "database_user": user,
                    "server_version": version,
                    "pgcrypto_installed": pgcrypto,
                    "pg_stat_statements_installed": pg_stat_statements,
                    "alembic_table": alembic_table,
                    "alembic_head": alembic_head,
                    "existing_systemic_tables": existing_systemic_tables,
                    "existing_bridge_columns": existing_bridge_columns,
                },
                sort_keys=True,
            )
        )
    finally:
        connection.close()


if __name__ == "__main__":
    main()

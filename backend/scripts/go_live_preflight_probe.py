"""Secret-free, read-only go-live database preflight.

The probe never selects encrypted key material, password hashes, MFA secrets,
or raw email addresses. It is safe to run against staging or production.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import psycopg2
from psycopg2.extensions import make_dsn


def _database_url() -> str:
    public_url = os.environ.get("DATABASE_PUBLIC_URL")
    if public_url and ".railway.internal" not in public_url:
        return public_url
    if os.environ.get("RAILWAY_TCP_PROXY_DOMAIN"):
        return make_dsn(
            dbname=os.environ["PGDATABASE"],
            user=os.environ["PGUSER"],
            password=os.environ["PGPASSWORD"],
            host=os.environ["RAILWAY_TCP_PROXY_DOMAIN"],
            port=os.environ["RAILWAY_TCP_PROXY_PORT"],
            sslmode="require",
        )
    return os.environ["DATABASE_URL"]


def _json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return str(value)
    return value


def main() -> None:
    connection = psycopg2.connect(_database_url())
    connection.set_session(readonly=True, autocommit=True)
    output: dict[str, Any] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, column_name
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                 ORDER BY table_name, ordinal_position
                """
            )
            schema: dict[str, set[str]] = {}
            for table_name, column_name in cursor.fetchall():
                schema.setdefault(table_name, set()).add(column_name)

            def exists(table: str, *columns: str) -> bool:
                return table in schema and set(columns).issubset(schema[table])

            cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
            output["alembic_heads"] = [row[0] for row in cursor.fetchall()]

            if exists("users", "id", "email", "is_active"):
                cursor.execute(
                    """
                    SELECT u.id::text, u.email, u.is_active,
                           (SELECT count(*) FROM ai_requests r WHERE r.tenant_id = u.id),
                           (SELECT count(*) FROM ai_provider_keys k WHERE k.user_id = u.id),
                           (SELECT count(*) FROM profiles p WHERE p.user_id = u.id)
                      FROM users u
                     WHERE EXISTS (SELECT 1 FROM ai_requests r WHERE r.tenant_id = u.id)
                        OR EXISTS (SELECT 1 FROM ai_provider_keys k WHERE k.user_id = u.id)
                        OR EXISTS (SELECT 1 FROM profiles p WHERE p.user_id = u.id)
                     ORDER BY 4 DESC, 5 DESC, u.id
                    """
                )
                output["tenant_candidates"] = [
                    {
                        "tenant_id": row[0],
                        "email_sha256": hashlib.sha256(row[1].lower().encode()).hexdigest(),
                        "is_active": bool(row[2]),
                        "ai_request_count": row[3],
                        "provider_key_count": row[4],
                        "profile_count": row[5],
                    }
                    for row in cursor.fetchall()
                ]

            if exists(
                "ai_provider_keys",
                "user_id",
                "provider",
                "is_active",
                "is_validated",
                "test_status",
                "last_tested_at",
            ):
                cursor.execute(
                    """
                    SELECT user_id::text, provider,
                           count(*)::int,
                           count(*) FILTER (WHERE is_active)::int,
                           count(*) FILTER (WHERE is_active AND is_validated)::int,
                           array_agg(DISTINCT coalesce(test_status, 'NULL') ORDER BY coalesce(test_status, 'NULL')),
                           max(last_tested_at)
                      FROM ai_provider_keys
                     GROUP BY user_id, provider
                     ORDER BY user_id, provider
                    """
                )
                output["provider_key_metadata"] = [
                    {
                        "tenant_id": row[0],
                        "provider": row[1],
                        "total": row[2],
                        "active": row[3],
                        "active_validated": row[4],
                        "test_statuses": row[5],
                        "last_tested_at": _json_value(row[6]),
                        "key_material_selected": False,
                    }
                    for row in cursor.fetchall()
                ]

            if exists("ai_model_approvals", "tenant_id", "provider", "model", "status", "expires_at"):
                cursor.execute(
                    """
                    SELECT tenant_id::text, provider, model, status,
                           max_cost_usd, input_cost_per_million,
                           output_cost_per_million, max_output_tokens,
                           pricing_source_url, pricing_observed_at,
                           pricing_snapshot_hash, expires_at
                      FROM ai_model_approvals
                     ORDER BY approved_at DESC, id
                    """
                )
                output["model_approvals"] = [
                    {
                        "tenant_id": row[0],
                        "provider": row[1],
                        "model": row[2],
                        "status": row[3],
                        "max_cost_usd": _json_value(row[4]),
                        "input_cost_per_million": _json_value(row[5]),
                        "output_cost_per_million": _json_value(row[6]),
                        "max_output_tokens": row[7],
                        "pricing_source_url": row[8],
                        "pricing_observed_at": _json_value(row[9]),
                        "pricing_snapshot_hash": row[10],
                        "expires_at": _json_value(row[11]),
                    }
                    for row in cursor.fetchall()
                ]

            if exists("ai_budget_policies", "tenant_id", "provider", "is_active"):
                cursor.execute(
                    """
                    SELECT tenant_id::text, provider, model, module,
                           daily_token_limit, monthly_token_limit,
                           request_token_limit, null_limit_policy, is_active
                      FROM ai_budget_policies
                     ORDER BY tenant_id, provider, model NULLS FIRST, module NULLS FIRST
                    """
                )
                output["budget_policies"] = [
                    {
                        "tenant_id": row[0],
                        "provider": row[1],
                        "model": row[2],
                        "module": row[3],
                        "daily_token_limit": row[4],
                        "monthly_token_limit": row[5],
                        "request_token_limit": row[6],
                        "null_limit_policy": row[7],
                        "is_active": bool(row[8]),
                    }
                    for row in cursor.fetchall()
                ]

            bridge_tables = (
                "profile_suggestions",
                "shadow_trade_analysis_jobs",
                "profile_ai_reviews",
            )
            bridge_rows: dict[str, Any] = {}
            for table in bridge_tables:
                if not exists(table, "status"):
                    continue
                tenant_expr = "count(*) FILTER (WHERE tenant_id IS NOT NULL)" if "tenant_id" in schema[table] else "0"
                request_expr = "count(*) FILTER (WHERE ai_request_id IS NOT NULL)" if "ai_request_id" in schema[table] else "0"
                cursor.execute(
                    f"""
                    SELECT status, count(*)::int,
                           ({tenant_expr})::int,
                           ({request_expr})::int
                      FROM {table}
                     GROUP BY status
                     ORDER BY status
                    """
                )
                bridge_rows[table] = [
                    {
                        "status": row[0],
                        "count": row[1],
                        "tenant_linked": row[2],
                        "ai_request_linked": row[3],
                    }
                    for row in cursor.fetchall()
                ]
            output["legacy_bridge_rows"] = bridge_rows

            if exists("ai_requests", "tenant_id", "origin_module", "authority"):
                cursor.execute(
                    """
                    SELECT origin_module, authority, count(*)::int
                      FROM ai_requests
                     GROUP BY origin_module, authority
                     ORDER BY origin_module, authority
                    """
                )
                output["ai_requests_by_origin"] = [
                    {"origin_module": row[0], "authority": row[1], "count": row[2]}
                    for row in cursor.fetchall()
                ]

            if exists("ai_graph_runs", "status", "authority"):
                cursor.execute(
                    """
                    SELECT status, authority, count(*)::int
                      FROM ai_graph_runs
                     GROUP BY status, authority
                     ORDER BY status, authority
                    """
                )
                output["graph_runs"] = [
                    {"status": row[0], "authority": row[1], "count": row[2]}
                    for row in cursor.fetchall()
                ]

            if exists("ai_usage_records", "provider", "tokens_input", "tokens_output", "actual_cost"):
                cursor.execute(
                    """
                    SELECT provider, model, count(*)::int,
                           coalesce(sum(tokens_input), 0)::bigint,
                           coalesce(sum(tokens_output), 0)::bigint,
                           coalesce(sum(actual_cost), 0)::text
                      FROM ai_usage_records
                     GROUP BY provider, model
                     ORDER BY provider, model
                    """
                )
                output["usage_by_provider"] = [
                    {
                        "provider": row[0],
                        "model": row[1],
                        "records": row[2],
                        "tokens_input": row[3],
                        "tokens_output": row[4],
                        "actual_cost": row[5],
                    }
                    for row in cursor.fetchall()
                ]

            if exists("orders", "id", "created_at"):
                cursor.execute("SELECT count(*)::int, max(created_at) FROM orders")
                row = cursor.fetchone()
                output["orders"] = {"count": row[0], "latest_created_at": _json_value(row[1])}

            if exists("profiles", "is_active", "live_trading_enabled", "auto_pilot_enabled"):
                cursor.execute(
                    """
                    SELECT count(*)::int,
                           count(*) FILTER (WHERE is_active)::int,
                           count(*) FILTER (WHERE live_trading_enabled)::int,
                           count(*) FILTER (WHERE auto_pilot_enabled)::int
                      FROM profiles
                    """
                )
                row = cursor.fetchone()
                output["profiles"] = {
                    "total": row[0],
                    "active": row[1],
                    "live_trading_enabled": row[2],
                    "auto_pilot_enabled": row[3],
                }

        print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    finally:
        connection.close()


if __name__ == "__main__":
    main()

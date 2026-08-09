"""Trace non-fake AI usage in a secret-free, read-only database session.

The report deliberately excludes raw prompts, questions, provider key material,
result payload values, user emails, and authentication data. JSON payloads are
represented only by their top-level key names.
"""

from __future__ import annotations

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


def _value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "as_tuple"):
        return str(value)
    return value


def main() -> None:
    connection = psycopg2.connect(_database_url())
    connection.set_session(readonly=True, autocommit=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT table_name, array_agg(column_name ORDER BY ordinal_position)
                  FROM information_schema.columns
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
                 GROUP BY table_name
                 ORDER BY table_name
                """,
                ([
                    "ai_usage_records", "ai_requests", "ai_model_resolutions",
                    "ai_results", "ai_jobs", "ai_graph_runs", "ai_dataset_snapshots",
                ],),
            )
            schema = {table: columns for table, columns in cursor.fetchall()}
            required = {
                "ai_usage_records", "ai_requests", "ai_model_resolutions",
                "ai_results", "ai_dataset_snapshots",
            }
            if not required.issubset(schema):
                raise RuntimeError(f"required tables missing: {sorted(required - set(schema))}")

            cursor.execute(
                """
                SELECT
                    u.id::text,
                    u.tenant_id::text,
                    u.ai_request_id::text,
                    u.provider,
                    u.model,
                    u.module,
                    u.tokens_input,
                    u.tokens_output,
                    u.estimated_cost::text,
                    u.actual_cost::text,
                    u.currency,
                    u.pricing_snapshot_version,
                    u.created_at,
                    r.origin_module,
                    r.origin_view,
                    r.analysis_mode,
                    r.authority,
                    r.correlation_id,
                    r.created_at,
                    ARRAY(SELECT jsonb_object_keys(r.request_json) ORDER BY 1),
                    m.requested_provider,
                    m.requested_model,
                    m.configured_provider,
                    m.configured_model,
                    m.effective_provider,
                    m.effective_model,
                    m.capabilities,
                    m.resolution_policy_version,
                    m.resolution_reason,
                    m.resolved_at,
                    d.contract_version,
                    d.origin_module,
                    d.source_tables,
                    d.source_labels,
                    d.event_identity_contract,
                    d.outcome_contract,
                    d.time_anchor,
                    d.window_start,
                    d.window_end,
                    d.row_count,
                    d.quality_status,
                    d.quality_findings,
                    result.status,
                    result.terminal_reason,
                    result.completed_at,
                    ARRAY(SELECT jsonb_object_keys(result.result_json) ORDER BY 1),
                    job.status,
                    job.attempt,
                    job.max_attempts,
                    job.terminal_reason,
                    job.last_error_code,
                    graph.id::text,
                    graph.status,
                    graph.current_node,
                    graph.authority,
                    graph.terminal_reason,
                    graph.last_error_code,
                    graph.created_at,
                    graph.completed_at,
                    r.request_json ->> 'question' =
                        'Validate the staging persistence path without external calls or live effects.',
                    result.result_json -> 'analysis' ->> 'verdict' = 'STAGING_SYNTHETIC_ANALYSIS_ONLY',
                    result.result_json @> '{"warnings":["No external provider was called"]}'::jsonb,
                    result.result_json @> '{"limitations":["Synthetic staging evidence only"]}'::jsonb
                  FROM ai_usage_records u
                  JOIN ai_requests r ON r.id = u.ai_request_id AND r.tenant_id = u.tenant_id
                  JOIN ai_model_resolutions m ON m.id = r.model_resolution_id AND m.tenant_id = r.tenant_id
                  JOIN ai_dataset_snapshots d ON d.id = r.dataset_snapshot_id AND d.tenant_id = r.tenant_id
                  LEFT JOIN ai_results result ON result.ai_request_id = r.id AND result.tenant_id = r.tenant_id
                  LEFT JOIN ai_jobs job ON job.ai_request_id = r.id AND job.tenant_id = r.tenant_id
                  LEFT JOIN ai_graph_runs graph ON graph.ai_request_id = r.id AND graph.tenant_id = r.tenant_id
                 WHERE u.provider <> 'fake'
                 ORDER BY u.created_at, u.id
                """
            )
            records = []
            for row in cursor.fetchall():
                records.append(
                    {
                        "usage_id": row[0],
                        "tenant_id": row[1],
                        "ai_request_id": row[2],
                        "provider": row[3],
                        "model": row[4],
                        "module": row[5],
                        "tokens_input": row[6],
                        "tokens_output": row[7],
                        "estimated_cost": row[8],
                        "actual_cost": row[9],
                        "currency": row[10],
                        "pricing_snapshot_version": row[11],
                        "usage_created_at": _value(row[12]),
                        "request": {
                            "origin_module": row[13],
                            "origin_view": row[14],
                            "analysis_mode": row[15],
                            "authority": row[16],
                            "correlation_id": row[17],
                            "created_at": _value(row[18]),
                            "payload_keys_only": row[19],
                        },
                        "model_resolution": {
                            "requested_provider": row[20],
                            "requested_model": row[21],
                            "configured_provider": row[22],
                            "configured_model": row[23],
                            "effective_provider": row[24],
                            "effective_model": row[25],
                            "capabilities": row[26],
                            "policy_version": row[27],
                            "reason": row[28],
                            "resolved_at": _value(row[29]),
                        },
                        "dataset": {
                            "contract_version": row[30],
                            "origin_module": row[31],
                            "source_tables": row[32],
                            "source_labels": row[33],
                            "event_identity_contract": row[34],
                            "outcome_contract": row[35],
                            "time_anchor": row[36],
                            "window_start": _value(row[37]),
                            "window_end": _value(row[38]),
                            "row_count": row[39],
                            "quality_status": row[40],
                            "quality_findings": row[41],
                        },
                        "result": {
                            "status": row[42],
                            "terminal_reason": row[43],
                            "completed_at": _value(row[44]),
                            "payload_keys_only": row[45],
                        },
                        "job": {
                            "status": row[46],
                            "attempt": row[47],
                            "max_attempts": row[48],
                            "terminal_reason": row[49],
                            "last_error_code": row[50],
                        },
                        "graph_run": {
                            "id": row[51],
                            "status": row[52],
                            "current_node": row[53],
                            "authority": row[54],
                            "terminal_reason": row[55],
                            "last_error_code": row[56],
                            "created_at": _value(row[57]),
                            "completed_at": _value(row[58]),
                        },
                        "legacy_fake_adapter_fingerprint": {
                            "known_synthetic_question_matches": row[59],
                            "synthetic_verdict_matches": row[60],
                            "fixed_fake_token_counts_match": row[6] == 17 and row[7] == 11,
                            "synthetic_correlation_prefix_matches": str(row[17]).startswith(
                                "systemic-ai-staging-canary-"
                            ),
                            "unpriced_staging_marker_matches": row[11] == "UNPRICED_STAGING_V1",
                            "no_external_provider_warning_matches": row[61],
                            "synthetic_only_limitation_matches": row[62],
                            "strong_code_path_match": (
                                row[59] is True
                                and row[60] is True
                                and row[6] == 17
                                and row[7] == 11
                                and str(row[17]).startswith("systemic-ai-staging-canary-")
                                and row[11] == "UNPRICED_STAGING_V1"
                            ),
                        },
                    }
                )
            print(json.dumps({"schema_columns": schema, "non_fake_usage": records}, sort_keys=True, separators=(",", ":")))
    finally:
        connection.close()


if __name__ == "__main__":
    main()

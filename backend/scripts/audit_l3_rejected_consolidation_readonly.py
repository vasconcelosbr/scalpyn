"""Read-only evidence probe for the L3 rejected-shadow consolidation rollout."""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import text

from app.database import CeleryAsyncSessionLocal


async def _rows(db, sql: str) -> list[dict]:
    result = await db.execute(text(sql))
    return [dict(row) for row in result.mappings().all()]


async def main() -> None:
    evidence: dict[str, list[dict]] = {}
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text("SET statement_timeout = '20s'"))
        evidence["alembic_version"] = await _rows(
            db, "SELECT version_num FROM alembic_version"
        )
        evidence["shadow_columns"] = await _rows(
            db,
            """
            SELECT column_name, data_type, is_nullable
              FROM information_schema.columns
             WHERE table_schema = 'public'
               AND table_name = 'shadow_trades'
               AND column_name IN (
                   'user_id', 'symbol', 'direction', 'source', 'status',
                   'l3_consolidation_enforced', 'config_snapshot'
               )
             ORDER BY ordinal_position
            """,
        )
        evidence["l3_indexes"] = await _rows(
            db,
            """
            SELECT indexname, indexdef
              FROM pg_indexes
             WHERE schemaname = 'public'
               AND tablename = 'shadow_trades'
               AND (
                   indexname ILIKE '%l3%'
                   OR indexdef ILIKE '%l3_consolidation%'
               )
             ORDER BY indexname
            """,
        )
        evidence["active_rejected"] = await _rows(
            db,
            """
            SELECT COUNT(*)::int AS active_total,
                   COUNT(*) FILTER (
                       WHERE l3_consolidation_enforced IS TRUE
                   )::int AS enforced_total,
                   COUNT(*) FILTER (
                       WHERE COALESCE(l3_consolidation_enforced, FALSE) IS FALSE
                   )::int AS legacy_total
              FROM shadow_trades
             WHERE source = 'L3_REJECTED'
               AND status IN ('PENDING', 'RUNNING')
            """,
        )
        evidence["active_rejected_duplicate_groups"] = await _rows(
            db,
            """
            SELECT COUNT(*)::int AS duplicate_groups,
                   COALESCE(SUM(n - 1), 0)::int AS excess_rows
              FROM (
                    SELECT user_id, symbol, direction, COUNT(*) AS n
                      FROM shadow_trades
                     WHERE source = 'L3_REJECTED'
                       AND status IN ('PENDING', 'RUNNING')
                     GROUP BY user_id, symbol, direction
                    HAVING COUNT(*) > 1
                   ) AS duplicate_keys
            """,
        )
        evidence["active_rejected_top_groups"] = await _rows(
            db,
            """
            SELECT symbol,
                   direction,
                   COUNT(*)::int AS active_rows,
                   COUNT(DISTINCT profile_id)::int AS profile_count
              FROM shadow_trades
             WHERE source = 'L3_REJECTED'
               AND status IN ('PENDING', 'RUNNING')
             GROUP BY symbol, direction
            HAVING COUNT(*) > 1
             ORDER BY active_rows DESC, symbol
             LIMIT 20
            """,
        )
        evidence["recent_rejected_24h"] = await _rows(
            db,
            """
            SELECT COUNT(*)::int AS total,
                   COUNT(DISTINCT (user_id, symbol, direction))::int
                       AS distinct_owner_keys
              FROM shadow_trades
             WHERE source = 'L3_REJECTED'
               AND created_at >= NOW() - INTERVAL '24 hours'
            """,
        )
        evidence["spot_engine_scanner"] = await _rows(
            db,
            """
            SELECT id::text,
                   user_id::text,
                   config_json -> 'scanner' AS scanner,
                   updated_at
              FROM config_profiles
             WHERE config_type = 'spot_engine'
               AND is_active IS TRUE
             ORDER BY updated_at DESC
            """,
        )
        evidence["rejected_capture_rate_limit"] = await _rows(
            db,
            """
            SELECT user_id::text,
                   config_json -> 'shadow_capture_l3_rejected_max_per_hour'
                       AS max_per_hour
              FROM config_profiles
             WHERE config_type = 'ml'
               AND is_active IS TRUE
             ORDER BY updated_at DESC
            """,
        )
        evidence["active_l3_profile_hashes"] = await _rows(
            db,
            """
            SELECT DISTINCT p.id::text,
                   p.name,
                   p.profile_version,
                   md5(p.config::text) AS config_md5,
                   pv.id::text AS champion_version_id,
                   pv.config_hash
              FROM profiles AS p
              JOIN pipeline_watchlists AS pw
                ON pw.profile_id = p.id
               AND pw.level = 'L3'
               AND pw.auto_refresh IS TRUE
              LEFT JOIN profile_versions AS pv
                ON pv.profile_id = p.id
               AND pv.status = 'CHAMPION'
             WHERE p.is_active IS TRUE
             ORDER BY p.name, p.id::text
            """,
        )
    print(json.dumps(evidence, default=str, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

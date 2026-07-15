"""Read-only production verification for Calibration Evolution v2."""

from __future__ import annotations

import asyncio
import json
import os

import asyncpg


async def main() -> None:
    connection = await asyncpg.connect(os.environ["DATABASE_PUBLIC_URL"])
    try:
        output = {
            "revision": await connection.fetchval("SELECT version_num FROM alembic_version"),
            "tables": await connection.fetchval("""
                SELECT count(*)
                  FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name = ANY($1::text[])
            """, [
                "calibration_recommendations", "calibration_proposals",
                "calibration_state_events", "calibration_results",
                "profile_version_ev_scores", "crypto_profile_ev_scores",
            ]),
            "rows": dict(await connection.fetchrow("""
                SELECT
                  (SELECT count(*) FROM calibration_recommendations) AS recommendations,
                  (SELECT count(*) FROM calibration_proposals) AS proposals,
                  (SELECT count(*) FROM calibration_state_events) AS events,
                  (SELECT count(*) FROM profile_version_ev_scores) AS profile_ev,
                  (SELECT count(*) FROM crypto_profile_ev_scores) AS crypto_ev
            """)),
            "flags_enabled_profiles": dict(await connection.fetchrow("""
                SELECT
                  count(*) FILTER (WHERE COALESCE((config_json->>'calibration_evidence_registry_v1')::boolean, false)) AS evidence,
                  count(*) FILTER (WHERE COALESCE((config_json->>'calibration_orchestrator_v1')::boolean, false)) AS orchestrator,
                  count(*) FILTER (WHERE COALESCE((config_json->>'autopilot_calibration_v1')::boolean, false)) AS autopilot,
                  count(*) FILTER (WHERE COALESCE((config_json->>'ev_score_v2')::boolean, false)) AS ev_score
                  FROM config_profiles WHERE config_type = 'ml'
            """)),
        }
        print(json.dumps(output, default=str, sort_keys=True))
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())

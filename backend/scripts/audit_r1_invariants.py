"""R1 cutover -- durable invariant fingerprints, with the query and the
predicate/temporal cutoff that define each set recorded alongside the value.

Why this script exists: two prior R1 audit reports cited fingerprint values
(97b7d30f95e76321d65794b809dddd1d, f5abc9b4386175d62c22ab5b5e492a80) whose
producing query was never persisted. Three reconstruction attempts (config_hash
column, md5(config_json::text), md5(rules||weights||thresholds)) all failed to
reproduce them. That is what made the loss unrecoverable, not the loss itself
-- the rule this script enforces is: a fingerprint is only ever reported next
to the literal query, its predicate/cutoff, and the run timestamp. Never the
number alone.

Read-only. Never mutates config_profiles, score_engine_versions, or
shadow_trades. Rerun this script to get fresh, reproducible values -- the two
old hashes above are not, and are not attempted here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


INVARIANTS: list[dict[str, str]] = [
    {
        "name": "active_profiles",
        "predicate": "config_profiles WHERE is_active = true",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(config_json::text), ','
                       ORDER BY id
                   )) AS fingerprint
              FROM config_profiles
             WHERE is_active = true
        """,
    },
    {
        "name": "governed_score_engine_versions",
        "predicate": "score_engine_versions WHERE status = 'BASELINE' "
                      "(the only status value present as of this run -- "
                      "if a new status is introduced, this predicate must "
                      "be revisited, not silently widened)",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || config_hash, ','
                       ORDER BY id
                   )) AS fingerprint
              FROM score_engine_versions
             WHERE status = 'BASELINE'
        """,
    },
]


def _terminal_shadow_trades_invariant(cutoff_iso: str) -> dict[str, str]:
    return {
        "name": "terminal_shadow_trades",
        "predicate": (
            "shadow_trades WHERE status = 'COMPLETED' AND "
            f"label_resolved_at < '{cutoff_iso}' -- the temporal cutoff IS "
            "the run timestamp below; rerunning with this same literal "
            "cutoff must reproduce this exact value forever, since a "
            "COMPLETED trade's label_resolved_at never changes retroactively"
        ),
        "sql": f"""
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || outcome || ':'
                           || label_resolved_at::text, ','
                       ORDER BY id
                   )) AS fingerprint
              FROM shadow_trades
             WHERE status = 'COMPLETED'
               AND label_resolved_at < '{cutoff_iso}'
        """,
    }


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, indent=2)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    run_at = datetime.now(timezone.utc)
    run_at_iso = run_at.isoformat()

    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True, autocommit=True)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    results = []
    all_invariants = list(INVARIANTS) + [_terminal_shadow_trades_invariant(run_at_iso)]
    for invariant in all_invariants:
        cursor.execute(invariant["sql"])
        row = cursor.fetchone()
        results.append({
            "name": invariant["name"],
            "predicate": invariant["predicate"],
            "query": invariant["sql"].strip(),
            "value": dict(row),
            "computed_at": run_at_iso,
        })

    print(_json({"computed_at": run_at_iso, "invariants": results}))

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()

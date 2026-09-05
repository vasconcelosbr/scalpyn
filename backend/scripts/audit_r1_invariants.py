"""Reproducible, read-only invariants for the R1 OHLCV canonical cut.

Every invariant records its query, predicate, population and computation time.
The terminal Shadow population is bounded by ``completed_at`` -- the database
write time of the terminal transition -- rather than ``label_resolved_at``.
The latter is an event timestamp and may precede a later terminal write, which
would allow rows to enter an already-audited historical set retroactively.

Use ``--cutoff`` to rerun the exact same terminal population. A changed hash
for that fixed population is then evidence that protected terminal fields were
mutated; normal trades completed after the cutoff cannot join the set.

Read-only. The script never mutates configuration, profiles or trades.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg2
from psycopg2.extras import RealDictCursor


STATIC_INVARIANTS: list[dict[str, str]] = [
    {
        "name": "active_config_profiles",
        "predicate": "config_profiles WHERE is_active = true",
        "population": "Runtime configuration records, not strategy profiles",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(jsonb_build_object(
                           'config_type', config_type,
                           'config_json', config_json
                       )::text), ',' ORDER BY id
                   )) AS fingerprint
              FROM config_profiles
             WHERE is_active = true
        """,
    },
    {
        "name": "baseline_score_engine_versions",
        "predicate": "score_engine_versions WHERE status = 'BASELINE'",
        "population": "Governed score-engine baselines",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(jsonb_build_object(
                           'config_hash', config_hash,
                           'rules', rules,
                           'weights', weights,
                           'thresholds', thresholds,
                           'selected_rule_ids', selected_rule_ids,
                           'status', status
                       )::text), ',' ORDER BY id
                   )) AS fingerprint
              FROM score_engine_versions
             WHERE status = 'BASELINE'
        """,
    },
    {
        "name": "active_strategy_profiles",
        "predicate": "profiles WHERE is_active = true",
        "population": "Active strategy profiles and their economic/runtime configuration",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(jsonb_build_object(
                           'name', name,
                           'config', config,
                           'profile_role', profile_role,
                           'pipeline_order', pipeline_order,
                           'auto_pilot_enabled', auto_pilot_enabled,
                           'auto_pilot_config', auto_pilot_config,
                           'profile_type', profile_type,
                           'profile_version', profile_version,
                           'is_shadow_only', is_shadow_only,
                           'live_trading_enabled', live_trading_enabled
                       )::text), ',' ORDER BY id
                   )) AS fingerprint
              FROM profiles
             WHERE is_active = true
        """,
    },
    {
        "name": "governed_profile_versions",
        "predicate": "profile_versions WHERE "
                     "(status = 'CHAMPION' AND is_active = true) OR status = 'SHADOW'",
        "population": "Active CHAMPION and governed SHADOW strategy-profile versions",
        "sql": """
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(jsonb_build_object(
                           'profile_id', profile_id,
                           'version_number', version_number,
                           'config', config,
                           'is_active', is_active,
                           'parent_version_id', parent_version_id,
                           'config_hash', config_hash,
                           'score_engine_version_id', score_engine_version_id,
                           'status', status,
                           'activated_at', activated_at,
                           'deactivated_at', deactivated_at
                       )::text), ',' ORDER BY id
                   )) AS fingerprint
              FROM profile_versions
             WHERE (status = 'CHAMPION' AND is_active = true)
                OR status = 'SHADOW'
        """,
    },
]

# Backwards-compatible import name used by lightweight audit tooling/tests.
INVARIANTS = STATIC_INVARIANTS


def _terminal_shadow_trades_invariant(cutoff_iso: str) -> dict[str, str]:
    return {
        "name": "terminal_shadow_trades",
        "predicate": (
            "shadow_trades WHERE status = 'COMPLETED' AND "
            f"completed_at < '{cutoff_iso}'"
        ),
        "population": (
            "Trades whose terminal database transition completed before the cutoff; "
            "the hash covers identity, entry/barrier and realized-result fields"
        ),
        "sql": f"""
            SELECT count(*) AS n,
                   md5(string_agg(
                       id::text || ':' || md5(jsonb_build_object(
                           'symbol', symbol,
                           'direction', direction,
                           'source', source,
                           'amount_usdt', amount_usdt,
                           'entry_price', entry_price,
                           'entry_timestamp', entry_timestamp,
                           'tp_price', tp_price,
                           'sl_price', sl_price,
                           'tp_pct', tp_pct,
                           'sl_pct', sl_pct,
                           'timeout_candles', timeout_candles,
                           'exit_price', exit_price,
                           'exit_price_nominal', exit_price_nominal,
                           'exit_price_observed', exit_price_observed,
                           'exit_price_semantics', exit_price_semantics,
                           'exit_timestamp', exit_timestamp,
                           'outcome', outcome,
                           'pnl_pct', pnl_pct,
                           'pnl_usdt', pnl_usdt,
                           'holding_seconds', holding_seconds,
                           'label_resolved_at', label_resolved_at,
                           'completed_at', completed_at,
                           'label_contract_version', label_contract_version,
                           'barrier_contract_version', barrier_contract_version
                       )::text), ',' ORDER BY id
                   )) AS fingerprint
              FROM shadow_trades
             WHERE status = 'COMPLETED'
               AND completed_at < '{cutoff_iso}'
        """,
    }


def _parse_cutoff(value: str, *, not_after: datetime | None = None) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--cutoff must include an explicit timezone")
    parsed = parsed.astimezone(timezone.utc)
    if not_after is not None and parsed > not_after:
        raise ValueError("--cutoff cannot be later than the audit run")
    return parsed


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False, sort_keys=True, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cutoff",
        required=True,
        help="Fixed timezone-aware terminal cutoff from a settled historical instant",
    )
    args = parser.parse_args()

    database_url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_PUBLIC_URL or DATABASE_URL is required")

    run_at = datetime.now(timezone.utc)
    cutoff = _parse_cutoff(args.cutoff, not_after=run_at)
    run_at_iso = run_at.isoformat()
    cutoff_iso = cutoff.isoformat()

    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True, autocommit=True)
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    results = []
    all_invariants = list(STATIC_INVARIANTS) + [
        _terminal_shadow_trades_invariant(cutoff_iso)
    ]
    for invariant in all_invariants:
        cursor.execute(invariant["sql"])
        row = cursor.fetchone()
        results.append({
            "name": invariant["name"],
            "population": invariant["population"],
            "predicate": invariant["predicate"],
            "query": invariant["sql"].strip(),
            "value": dict(row),
            "computed_at": run_at_iso,
            "terminal_cutoff": cutoff_iso if invariant["name"] == "terminal_shadow_trades" else None,
        })

    print(_json({
        "computed_at": run_at_iso,
        "terminal_cutoff": cutoff_iso,
        "invariants": results,
    }))

    cursor.close()
    conn.close()


if __name__ == "__main__":
    main()

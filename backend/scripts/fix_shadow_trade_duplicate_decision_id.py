#!/usr/bin/env python
"""Fix decision_id duplicates in shadow_trades — audit + non-destructive
marking, NEVER DELETE.

Part of the Profile Intelligence Adaptive Loop reformulation (audit
2026-06-24, item 4 of the post-VALIDACAO_GERAL punch list). For each
decision_id with more than one shadow_trades row:
  1. Picks a canonical row (earliest created_at — see
     shadow_trade_duplicate_resolver.resolve_duplicate_group).
  2. Records one audit row in shadow_trade_duplicate_audit with every
     member id, the chosen canonical, the outcomes of each row, and whether
     they conflict.
  3. Sets shadow_trades.superseded_by_id on every non-canonical row to the
     canonical row's id. The row itself is never touched otherwise — no
     UPDATE of outcome/status/any other column, no DELETE.

After this script runs --commit and confirms 0 unmarked duplicate groups
remain, migration 110 (not yet created) can safely add the partial unique
index `ON shadow_trades(decision_id) WHERE superseded_by_id IS NULL` to
prevent new duplicates going forward.

Usage:
    python fix_shadow_trade_duplicate_decision_id.py            (dry-run)
    python fix_shadow_trade_duplicate_decision_id.py --commit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg2
import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/
from app.services.shadow_trade_duplicate_resolver import resolve_duplicate_group  # noqa: E402


def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("Defina DATABASE_PUBLIC_URL ou DATABASE_URL no ambiente.")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = "postgresql://" + dsn[len("postgresql+asyncpg://"):]
    elif dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return dsn


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    dry_run = not args.commit

    conn = psycopg2.connect(_get_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT decision_id FROM shadow_trades "
                "WHERE decision_id IS NOT NULL AND superseded_by_id IS NULL "
                "GROUP BY decision_id HAVING COUNT(*) > 1 ORDER BY decision_id"
            )
            dup_decision_ids = [r[0] for r in cur.fetchall()]

        print(f"Grupos de decision_id duplicados (ainda não marcados): {len(dup_decision_ids)}\n")

        n_conflicts = 0
        for decision_id in dup_decision_ids:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, created_at, outcome FROM shadow_trades "
                    "WHERE decision_id = %s ORDER BY created_at",
                    (decision_id,),
                )
                rows = [dict(r) for r in cur.fetchall()]

            result = resolve_duplicate_group(rows)
            marker = "  <<< OUTCOMES CONFLITANTES" if result["conflict"] else ""
            if result["conflict"]:
                n_conflicts += 1
            print(
                f"  decision_id={decision_id} canonical={result['canonical_id']} "
                f"superseded={result['superseded_ids']} outcomes={result['outcomes']}{marker}"
            )

            if not dry_run:
                with conn.cursor() as wcur:
                    wcur.execute(
                        """
                        INSERT INTO shadow_trade_duplicate_audit (
                            decision_id, member_ids, canonical_id, superseded_ids,
                            outcomes, distinct_outcomes_count, conflict, resolution_reason, triggered_by
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            decision_id,
                            json.dumps([r["id"] for r in rows], default=str),
                            result["canonical_id"],
                            json.dumps(result["superseded_ids"]),
                            json.dumps(result["outcomes"]),
                            result["distinct_outcomes_count"],
                            result["conflict"],
                            result["resolution_reason"],
                            "fix_shadow_trade_duplicate_decision_id.py",
                        ),
                    )
                    for superseded_id in result["superseded_ids"]:
                        wcur.execute(
                            "UPDATE shadow_trades SET superseded_by_id = %s WHERE id = %s",
                            (result["canonical_id"], superseded_id),
                        )

        if not dry_run:
            conn.commit()
            print(f"\nCOMMITADO: {len(dup_decision_ids)} grupo(s) marcados em shadow_trade_duplicate_audit.")
        else:
            print("\nDRY-RUN: nenhuma escrita feita. Rode com --commit para persistir.")

        print(f"\nResumo: {len(dup_decision_ids)} grupos, {n_conflicts} com outcomes conflitantes.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

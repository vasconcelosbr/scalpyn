#!/usr/bin/env python
"""Profile Intelligence Feedback Engine — annotate profile_suggestions with
real Shadow performance evidence (Fase 11, audit 2026-06-24).

Never promotes a suggestion (exploratory_only -> applied stays 100% human,
via POST /suggestions/{id}/create-profile). Only writes
shadow_feedback_status / shadow_feedback_json so a human reviewer has
evidence instead of nothing.

Usage:
    python run_profile_suggestion_feedback.py            (dry-run, default)
    python run_profile_suggestion_feedback.py --commit
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
from app.services.profile_suggestion_feedback_engine import (  # noqa: E402
    evaluate_suggestion_feedback,
    resolve_profile_id_for_suggestion,
    no_profile_linked_result,
)


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
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, status, profile_id, created_profile_id FROM profile_suggestions"
            )
            suggestions = [dict(r) for r in cur.fetchall()]

        print(f"Encontradas {len(suggestions)} suggestion(s). dry_run={dry_run}\n")

        status_counts: dict = {}
        for s in suggestions:
            pid = resolve_profile_id_for_suggestion(s)
            if pid is None:
                result = no_profile_linked_result()
            else:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute(
                        "SELECT outcome FROM shadow_trades "
                        "WHERE profile_id = %s AND status = 'COMPLETED'",
                        (pid,),
                    )
                    rows = [dict(r) for r in cur.fetchall()]
                result = evaluate_suggestion_feedback(rows)

            status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1
            print(
                f"  suggestion={s['id']} status={s['status']:<16} "
                f"profile_id={pid} -> feedback={result['status']:<22} "
                f"trades={result['metrics']['trades']} win_rate={result['metrics']['win_rate']}"
            )

            if not dry_run:
                with conn.cursor() as wcur:
                    wcur.execute(
                        "UPDATE profile_suggestions SET shadow_feedback_status = %s, "
                        "shadow_feedback_json = %s WHERE id = %s",
                        (result["status"], json.dumps(result), s["id"]),
                    )

        if not dry_run:
            conn.commit()
            print(f"\nCOMMITADO: {len(suggestions)} suggestion(s) anotadas.")
        else:
            print("\nDRY-RUN: nenhuma escrita feita. Rode com --commit para persistir.")

        print(f"\nResumo: {status_counts}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()

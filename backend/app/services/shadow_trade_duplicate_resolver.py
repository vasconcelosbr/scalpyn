"""Resolve decision_id duplicate groups in shadow_trades — audit + safe
marking, never DELETE (Profile Intelligence Adaptive Loop reformulation,
audit 2026-06-24, item 4 of the post-audit punch list).

Confirmed by VALIDACAO_GERAL_PROFILE_INTELLIGENCE_ADAPTIVE_LOOP.md (Fase 14):
the docstring of _create_from_decision claims idempotency via "ON CONFLICT
(decision_id) DO NOTHING (UNIQUE INDEX migration 047)" but no such unique
index exists in production — only a plain (non-unique) btree index. 38
decision_id groups are duplicated today, several with conflicting outcomes
(the same decision recorded as both TP_HIT and SL_HIT in different rows).

This module only decides WHICH row is canonical. It never deletes anything
and never guesses which outcome is "correct" when they conflict — a
conflict is recorded as a fact (conflict=True), not silently resolved.
"""

from __future__ import annotations

from typing import Any, Dict, List


def resolve_duplicate_group(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """rows: shadow_trades-shaped dicts with at least {id, created_at, outcome}
    for every row sharing the same decision_id (the caller's GROUP BY ...
    HAVING COUNT(*) > 1 result, expanded back to row level).

    Canonical = earliest created_at (tie-broken by id string) — the first
    row created for that decision is treated as authoritative; later rows
    are marked superseded, never deleted. If the rows disagree on outcome,
    that is recorded as conflict=True for human/ML-pipeline review — this
    function does not attempt to decide which outcome is "true".
    """
    if not rows:
        raise ValueError("resolve_duplicate_group requires at least one row")

    sorted_rows = sorted(rows, key=lambda r: (r["created_at"], str(r["id"])))
    canonical = sorted_rows[0]
    superseded = sorted_rows[1:]

    outcomes = {str(r["id"]): r.get("outcome") for r in rows}
    distinct_outcomes = {r.get("outcome") for r in rows if r.get("outcome") is not None}

    return {
        "canonical_id": str(canonical["id"]),
        "superseded_ids": [str(r["id"]) for r in superseded],
        "outcomes": outcomes,
        "distinct_outcomes_count": len(distinct_outcomes),
        "conflict": len(distinct_outcomes) > 1,
        "resolution_reason": "earliest_created_at",
    }

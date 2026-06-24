"""Profile Intelligence Feedback Engine — profile_suggestions (audit
2026-06-24, Fase 11 da reformulacao Adaptive Loop).

Lives inside Profile Intelligence (NOT a new external "Auto-Calibrator"
module — see absolute rule). Generalizes the per-profile shadow-performance
aggregation pattern already used by
profile_intelligence_autopilot_service.py's _shadow_metrics() / _review_shadow
(candidate state machine) and applies it to profile_suggestions, which today
have NO feedback loop at all: exploratory_only -> applied is 100% manual,
with zero automatic connection to how the profile actually performs in
Shadow.

This module ONLY computes evidence and a recommendation. It NEVER flips
status from exploratory_only to applied — that stays gated by the existing
human-only POST /suggestions/{id}/create-profile endpoint
(backend/app/api/profile_intelligence.py:825,
ProfileCreateService.create_from_suggestion). Writing the recommendation
gives the human reviewer real evidence instead of nothing.

Confirmed during implementation (production query, 2026-06-24): all 99
status='exploratory_only' rows have profile_id IS NULL AND
created_profile_id IS NULL AND source_profile_ids IS NULL/empty, AND their
source combinations (profile_rule_combinations) ALSO have empty
source_profile_ids — a structural gap that predates migration 096, not
something a join can backfill. NO_PROFILE_LINKED records this honestly
instead of guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

WIN_OUTCOME = "TP_HIT"
LOSS_OUTCOME = "SL_HIT"

PROMOTE_CANDIDATE = "PROMOTE_CANDIDATE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
POOR_PERFORMANCE = "POOR_PERFORMANCE"
NO_PROFILE_LINKED = "NO_PROFILE_LINKED"

# Mirrors config_profiles config_type='profile_intelligence' defaults
# (min_closed_trades=30, min_win_rate=0.45) for consistency with the rest of
# Profile Intelligence's viability checks — not an arbitrary new number.
DEFAULT_MIN_CLOSED_TRADES = 30
DEFAULT_MIN_WIN_RATE = 0.45


def resolve_profile_id_for_suggestion(suggestion_row: Dict[str, Any]) -> Optional[str]:
    """Prefer the profile actually created from this suggestion
    (created_profile_id) over the profile it was originally proposed for
    (profile_id) — if applied, the created profile is what's really running
    in Shadow. Returns None (NOT a guess) when neither is set."""
    return suggestion_row.get("created_profile_id") or suggestion_row.get("profile_id")


def evaluate_suggestion_feedback(
    rows: Iterable[Dict[str, Any]],
    *,
    min_closed_trades: int = DEFAULT_MIN_CLOSED_TRADES,
    min_win_rate: float = DEFAULT_MIN_WIN_RATE,
) -> Dict[str, Any]:
    """rows: shadow_trades-shaped dicts with at least {"outcome"} for the
    resolved profile_id, status already filtered by the caller (e.g.
    status='COMPLETED'), exactly like promotion_gate.py / label_lab's
    contract of trusting the caller's query.
    """
    trades = 0
    wins = 0
    for row in rows:
        outcome = row.get("outcome")
        if outcome == WIN_OUTCOME:
            trades += 1
            wins += 1
        elif outcome == LOSS_OUTCOME:
            trades += 1
        # TIMEOUT / None: not countable as a definitive win/loss — excluded,
        # not guessed (same convention as profile_intelligence_label_lab.py).

    win_rate = (wins / trades) if trades else None
    reasons = []

    if trades < min_closed_trades:
        status = INSUFFICIENT_EVIDENCE
        reasons.append(f"insufficient_closed_trades:{trades}<{min_closed_trades}")
    elif win_rate is not None and win_rate < min_win_rate:
        status = POOR_PERFORMANCE
        reasons.append(f"win_rate_below_threshold:{win_rate:.4f}<{min_win_rate}")
    else:
        status = PROMOTE_CANDIDATE

    return {
        "status": status,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "thresholds": {
            "min_closed_trades": min_closed_trades,
            "min_win_rate": min_win_rate,
        },
        "metrics": {
            "trades": trades,
            "wins": wins,
            "win_rate": win_rate,
        },
    }


def no_profile_linked_result() -> Dict[str, Any]:
    """Explicit, honest result for suggestions with no resolvable profile_id
    — recorded via reason_code, never silently skipped or guessed."""
    return {
        "status": NO_PROFILE_LINKED,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "reasons": ["no_profile_id_or_created_profile_id_set"],
        "thresholds": {},
        "metrics": {"trades": 0, "wins": 0, "win_rate": None},
    }

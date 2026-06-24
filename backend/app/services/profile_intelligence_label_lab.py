"""Label Lab — Profile Intelligence Adaptive Loop (audit 2026-06-24, Fase 5).

Pure-logic evaluator that answers one question BEFORE any model is trained:
"is this label definition (target_window_seconds + outcome rule) even viable
to learn from, given the data we have right now?"

Context: v41/v42 (is_tp_4h_v1, target_window_seconds=14400) were trained and
promoted to 'candidate' WITHOUT this check — test AUC collapsed to 0.497 and
0.422 (worse than random), discovered only after a full training run.
ml_models.metrics_json.promotion_gate now rejects them post-hoc (Fase 2),
but Label Lab's job is to catch an unviable label BEFORE the next training
run wastes a cycle on it.

Lives inside Profile Intelligence (NOT a new external module — see absolute
rule against creating an "Auto-Calibrator").  Pure function, no DB I/O: the
caller (profile_intelligence_label_lab_service / API endpoint) fetches rows
from shadow_trades and passes them in, exactly like promotion_gate.py.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

WIN_OUTCOME = "TP_HIT"
LOSS_OUTCOME = "SL_HIT"

VIABLE = "VIABLE"
INSUFFICIENT_SAMPLES = "INSUFFICIENT_SAMPLES"
DEGENERATE_CLASS_BALANCE = "DEGENERATE_CLASS_BALANCE"

# Rule #13 analogue for labels: never bypassable, mirrors promotion_gate.py's
# ABSOLUTE_MIN_TEST_AUC pattern — an explicit numeric floor, not a vibe.
DEFAULT_MIN_TOTAL_SAMPLES = 500
DEFAULT_MIN_POSITIVE_RATE = 0.05
DEFAULT_MAX_POSITIVE_RATE = 0.95
DEFAULT_MIN_DISTINCT_PROFILES = 1


def _is_win(row: Dict[str, Any], target_window_seconds: int) -> Optional[bool]:
    """True/False for a closed trade, None when the row can't be labeled
    (outcome missing or not yet closed) — never silently assumed."""
    outcome = row.get("outcome")
    holding_seconds = row.get("holding_seconds")
    if outcome == WIN_OUTCOME:
        if holding_seconds is None:
            return None
        return holding_seconds <= target_window_seconds
    if outcome == LOSS_OUTCOME:
        return False
    return None


def evaluate_label_candidate(
    rows: Iterable[Dict[str, Any]],
    *,
    label_version: str,
    target_window_seconds: int,
    source_filter: Optional[Iterable[str]] = None,
    min_total_samples: int = DEFAULT_MIN_TOTAL_SAMPLES,
    min_positive_rate: float = DEFAULT_MIN_POSITIVE_RATE,
    max_positive_rate: float = DEFAULT_MAX_POSITIVE_RATE,
) -> Dict[str, Any]:
    """Evaluate whether a label definition is viable to train on.

    rows: shadow_trades-shaped dicts with at least
          {"outcome", "holding_seconds", "source", "profile_id"}.
          Pass ONLY already-closed trades (status='CLOSED' or equivalent) —
          this function does not filter by status itself, matching
          promotion_gate.py's contract of trusting the caller's query.

    Returns a dict with the same shape family as evaluate_promotion_gate():
        {"status", "evaluated_at", "reasons": [...], "thresholds": {...},
         "metrics": {...}, "by_source": {...}}
    """
    _source_filter = set(source_filter) if source_filter else None

    labeled_rows = []
    skipped_unlabelable = 0
    by_source_counts: Counter = Counter()
    profile_ids = set()

    for row in rows:
        if _source_filter is not None and row.get("source") not in _source_filter:
            continue
        label = _is_win(row, target_window_seconds)
        if label is None:
            skipped_unlabelable += 1
            continue
        labeled_rows.append(label)
        by_source_counts[row.get("source")] += 1
        if row.get("profile_id"):
            profile_ids.add(row.get("profile_id"))

    total_samples = len(labeled_rows)
    positive_count = sum(1 for v in labeled_rows if v)
    positive_rate = (positive_count / total_samples) if total_samples else None

    reasons = []

    if total_samples < min_total_samples:
        reasons.append(
            f"insufficient_samples:{total_samples}<{min_total_samples}"
        )

    if positive_rate is not None and (
        positive_rate < min_positive_rate or positive_rate > max_positive_rate
    ):
        reasons.append(
            f"degenerate_class_balance:positive_rate={positive_rate:.4f} "
            f"outside [{min_positive_rate},{max_positive_rate}]"
        )
    elif positive_rate is None:
        reasons.append("degenerate_class_balance:no_labeled_samples")

    if total_samples < min_total_samples:
        status = INSUFFICIENT_SAMPLES
    elif reasons:
        status = DEGENERATE_CLASS_BALANCE
    else:
        status = VIABLE

    return {
        "status": status,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "reasons": reasons,
        "label_version": label_version,
        "target_window_seconds": target_window_seconds,
        "thresholds": {
            "min_total_samples": min_total_samples,
            "min_positive_rate": min_positive_rate,
            "max_positive_rate": max_positive_rate,
        },
        "metrics": {
            "total_samples": total_samples,
            "positive_count": positive_count,
            "positive_rate": positive_rate,
            "skipped_unlabelable": skipped_unlabelable,
            "distinct_profiles": len(profile_ids),
        },
        "by_source": dict(by_source_counts),
    }


def is_viable(result: Dict[str, Any]) -> bool:
    return result.get("status") == VIABLE

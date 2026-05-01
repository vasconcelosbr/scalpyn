"""Pre-flight safety guard for Phase 2 rollout raises.

Before bumping ``USE_ROBUST_INDICATORS_PERCENT`` from one tier to the
next (10 → 50 → 100) ops should call ``check_safe_to_raise`` to verify
the last 30 minutes of shadow snapshots are healthy enough to widen
exposure. The guard fails when:

  * Divergence rate (>10% bucket / total) exceeds the threshold.
  * Rejection rate (rejected snapshots / total) exceeds the threshold.
  * Average global confidence falls below the threshold.
  * No snapshots at all were produced in the window (shadow mode off).

``FORCE_ROLLOUT_RAISE=1`` (or ``settings.FORCE_ROLLOUT_RAISE``)
bypasses the guard for emergency rollbacks. The override is recorded
in the result payload so admin endpoints can surface it.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings

logger = logging.getLogger(__name__)


# Thresholds — kept module-level so tests can monkey-patch them.
DIVERGENCE_RATE_MAX = 0.05      # ≥5% of snapshots in the >10% bucket → block
REJECTION_RATE_MAX = 0.30       # ≥30% rejected → block
MIN_GLOBAL_CONFIDENCE = 0.60    # average <0.60 → block
WINDOW_MINUTES = 30
MIN_SNAPSHOTS_REQUIRED = 1


@dataclass
class PreflightResult:
    """Outcome of a pre-flight rollout-raise check."""

    safe: bool
    reasons: list[str]
    forced: bool
    metrics: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "safe": self.safe,
            "reasons": list(self.reasons),
            "forced": self.forced,
            "metrics": dict(self.metrics),
        }


def _force_override() -> bool:
    if bool(getattr(settings, "FORCE_ROLLOUT_RAISE", False)):
        return True
    raw = os.environ.get("FORCE_ROLLOUT_RAISE", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


async def collect_window_metrics(
    db: AsyncSession,
    *,
    window_minutes: int = WINDOW_MINUTES,
) -> Dict[str, Any]:
    """Aggregate ``indicator_snapshots`` over the last ``window_minutes``.

    Returns a dict with ``total``, ``rejected``, ``divergent_high``,
    ``avg_confidence``, ``divergence_rate``, ``rejection_rate``. On any
    DB error returns empty/zero metrics — callers must treat that as
    "unsafe" rather than "safe" so a broken DB never silently authorises
    a rollout raise.
    """
    try:
        row = (await db.execute(
            text(
                """
                SELECT
                    COUNT(*)                                                AS total,
                    SUM(CASE WHEN divergence_bucket = '>10%' THEN 1 ELSE 0 END) AS divergent_high,
                    SUM(CASE WHEN rejection_reason IS NOT NULL THEN 1 ELSE 0 END) AS rejected,
                    AVG(global_confidence)                                  AS avg_confidence
                FROM indicator_snapshots
                WHERE timestamp > now() - (:wm || ' minutes')::interval
                """
            ),
            {"wm": str(int(window_minutes))},
        )).fetchone()
    except Exception as exc:
        logger.warning("[preflight] window aggregate failed: %s", exc)
        return {
            "total": 0,
            "rejected": 0,
            "divergent_high": 0,
            "avg_confidence": 0.0,
            "divergence_rate": 0.0,
            "rejection_rate": 0.0,
            "window_minutes": window_minutes,
            "error": f"{type(exc).__name__}: {exc}",
        }

    total = int(row.total or 0)
    rejected = int(row.rejected or 0)
    divergent_high = int(row.divergent_high or 0)
    avg_conf = float(row.avg_confidence) if row.avg_confidence is not None else 0.0
    div_rate = (divergent_high / total) if total else 0.0
    rej_rate = (rejected / total) if total else 0.0
    return {
        "total": total,
        "rejected": rejected,
        "divergent_high": divergent_high,
        "avg_confidence": round(avg_conf, 4),
        "divergence_rate": round(div_rate, 4),
        "rejection_rate": round(rej_rate, 4),
        "window_minutes": window_minutes,
    }


async def check_safe_to_raise(
    db: AsyncSession,
    target_percent: Optional[int] = None,
    *,
    window_minutes: int = WINDOW_MINUTES,
) -> PreflightResult:
    """Decide whether it is safe to raise the rollout to ``target_percent``.

    The guard always evaluates the metrics — even when ``forced=True`` —
    so the admin response can surface the unsafe reasons the operator
    chose to override.
    """
    metrics = await collect_window_metrics(db, window_minutes=window_minutes)
    reasons: list[str] = []

    if "error" in metrics:
        reasons.append(f"db_error:{metrics['error']}")
    elif metrics["total"] < MIN_SNAPSHOTS_REQUIRED:
        reasons.append(
            f"no_snapshots_in_last_{window_minutes}m (count={metrics['total']})"
        )
    else:
        if metrics["divergence_rate"] > DIVERGENCE_RATE_MAX:
            reasons.append(
                f"divergence_rate {metrics['divergence_rate']:.3f} > "
                f"max {DIVERGENCE_RATE_MAX:.3f}"
            )
        if metrics["rejection_rate"] > REJECTION_RATE_MAX:
            reasons.append(
                f"rejection_rate {metrics['rejection_rate']:.3f} > "
                f"max {REJECTION_RATE_MAX:.3f}"
            )
        if metrics["avg_confidence"] < MIN_GLOBAL_CONFIDENCE:
            reasons.append(
                f"avg_confidence {metrics['avg_confidence']:.3f} < "
                f"min {MIN_GLOBAL_CONFIDENCE:.3f}"
            )

    safe_natural = not reasons
    forced = _force_override()
    safe = safe_natural or forced

    if target_percent is not None:
        metrics["target_percent"] = int(target_percent)

    return PreflightResult(
        safe=safe,
        reasons=reasons,
        forced=forced and not safe_natural,
        metrics=metrics,
    )


__all__ = [
    "DIVERGENCE_RATE_MAX",
    "MIN_GLOBAL_CONFIDENCE",
    "MIN_SNAPSHOTS_REQUIRED",
    "PreflightResult",
    "REJECTION_RATE_MAX",
    "WINDOW_MINUTES",
    "check_safe_to_raise",
    "collect_window_metrics",
]

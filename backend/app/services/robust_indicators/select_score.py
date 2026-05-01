"""Authoritative-score selection for Phase 2 rollout.

For symbols bucketed into the robust pipeline we want to use the new
confidence-weighted score everywhere a "score read point" exists:

  * ``pipeline_scan`` — when persisting ``alpha_score`` to
    ``pipeline_watchlist_assets``.
  * ``pipeline_rejections`` — when including a score in the trace.
  * ``evaluate_signals`` — when reading ``alpha_score`` from
    ``alpha_scores`` to decide whether a signal triggers.
  * Futures entry gate — when reading ``confidence_score`` to decide
    whether the gate opens.

This module exposes a single, side-effect-free helper:

    select_authoritative_score(
        symbol, indicators, *, legacy_score, score_config,
        percent=None,
    ) -> SelectScoreResult

It returns the score to use, the engine tag (``"robust"`` or
``"legacy"``), and — when robust failed silently and we fell back to
legacy — bumps the ``robust_silent_fallback_total`` counter.

Selection NEVER raises into the caller; on any internal failure it
returns the legacy score with ``engine_tag="legacy"`` and the
silent-fallback counter is incremented.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .bucketing import should_use_robust
from .compute import envelope_indicators
from .metrics import increment_silent_fallback
from .score import ScoreResult, calculate_score_with_confidence

logger = logging.getLogger(__name__)


@dataclass
class SelectScoreResult:
    """Outcome of an authoritative-score selection."""

    score: Optional[float]
    engine_tag: str  # "robust" | "legacy"
    bucketed: bool
    fell_back: bool = False
    fallback_reason: Optional[str] = None
    robust_score: Optional[ScoreResult] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "engine_tag": self.engine_tag,
            "bucketed": self.bucketed,
            "fell_back": self.fell_back,
            "fallback_reason": self.fallback_reason,
            "robust_score": (
                self.robust_score.to_dict() if self.robust_score is not None else None
            ),
        }


def _coerce_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def compute_robust_score(
    symbol: str,
    indicators: Mapping[str, Any],
    *,
    score_config: Optional[Mapping[str, Any]] = None,
    flow_source_hint: Optional[str] = None,
) -> Optional[ScoreResult]:
    """Wrap ``indicators`` into envelopes and compute a robust score.

    Returns ``None`` when no envelopes could be produced (so the caller
    knows to fall back to the legacy score and bump the silent-fallback
    counter).
    """
    if not symbol or not indicators:
        return None
    try:
        now = datetime.now(timezone.utc)
        envelopes = envelope_indicators(
            symbol,
            dict(indicators),
            timestamp=now,
            flow_source_hint=flow_source_hint or indicators.get("taker_source"),
        )
        if not envelopes:
            return None
        rules = []
        threshold = 65.0
        if score_config:
            rules = score_config.get("scoring_rules") or score_config.get("rules") or []
            thresholds = score_config.get("thresholds") or {}
            try:
                threshold = float(thresholds.get("buy", threshold))
            except (TypeError, ValueError):
                pass
        return calculate_score_with_confidence(
            envelopes, rules, can_trade_threshold=threshold,
        )
    except Exception as exc:
        logger.debug(
            "[robust_indicators] compute_robust_score failed for %s: %s",
            symbol, exc,
        )
        return None


def select_authoritative_score(
    symbol: str,
    indicators: Optional[Mapping[str, Any]],
    *,
    legacy_score: Any,
    score_config: Optional[Mapping[str, Any]] = None,
    percent: Optional[int] = None,
    flow_source_hint: Optional[str] = None,
) -> SelectScoreResult:
    """Return the authoritative score + engine tag for ``symbol``.

    Bucketing is computed via :func:`should_use_robust`. When the symbol
    is bucketed but the robust score cannot be produced (missing
    indicators, validation gate failure, exception) the legacy score is
    returned and ``robust_silent_fallback_total`` is incremented with
    the appropriate reason label.
    """
    legacy = _coerce_float(legacy_score)
    bucketed = should_use_robust(symbol, percent=percent)

    if not bucketed:
        return SelectScoreResult(
            score=legacy,
            engine_tag="legacy",
            bucketed=False,
        )

    if not indicators:
        increment_silent_fallback("missing_indicators")
        return SelectScoreResult(
            score=legacy,
            engine_tag="legacy",
            bucketed=True,
            fell_back=True,
            fallback_reason="missing_indicators",
        )

    robust = compute_robust_score(
        symbol,
        indicators,
        score_config=score_config,
        flow_source_hint=flow_source_hint,
    )

    if robust is None:
        increment_silent_fallback("compute_failed")
        return SelectScoreResult(
            score=legacy,
            engine_tag="legacy",
            bucketed=True,
            fell_back=True,
            fallback_reason="compute_failed",
        )

    if robust.rejected:
        # Rejected scores are still authoritative — record them as the
        # robust outcome so we don't paper over critical-gate failures
        # with a stale legacy number. The score itself is 0.0 in that
        # case, which correctly suppresses the symbol downstream.
        return SelectScoreResult(
            score=float(robust.score),
            engine_tag="robust",
            bucketed=True,
            fell_back=False,
            fallback_reason=None,
            robust_score=robust,
        )

    return SelectScoreResult(
        score=float(robust.score),
        engine_tag="robust",
        bucketed=True,
        fell_back=False,
        fallback_reason=None,
        robust_score=robust,
    )


__all__ = [
    "SelectScoreResult",
    "compute_robust_score",
    "select_authoritative_score",
]

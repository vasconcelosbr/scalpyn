"""Authoritative-score selection (Phase 3 — robust default, legacy on standby).

The robust confidence-weighted score is the only authority outside the
``LEGACY_PIPELINE_ROLLBACK`` emergency flag. The selector is invoked at
every "score read point" in the pipeline:

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

The result tells the caller which score to use and which engine
produced it. The contract:

  * ``engine_tag="robust"``, ``score=<float>`` — usable robust score.
  * ``engine_tag="robust"``, ``score=None`` — robust-tagged sentinel
    (rejection): the engine could not produce a value (missing
    indicators, compute exception, empty symbol). Callers MUST treat
    this as a non-trade signal — the legacy score is **never**
    substituted outside rollback.
  * ``engine_tag="legacy"``, ``score=<float>`` — only emitted while
    ``LEGACY_PIPELINE_ROLLBACK`` is active (``fell_back=True``,
    ``fallback_reason="legacy_rollback"``).

Selection NEVER raises into the caller. The
``robust_silent_fallback_total`` counter is bumped for every robust
failure (with the matching reason) and for every rollback override
so ops can see both signals on the admin endpoint and Prometheus.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .bucketing import is_legacy_rollback_active, should_use_robust
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

    Returns ``None`` when no envelopes could be produced. Callers
    treat ``None`` as a Phase 3 sentinel (engine declined to produce
    a value) and bump the ``robust_silent_fallback_total`` counter
    with the matching reason — outside ``LEGACY_PIPELINE_ROLLBACK``
    the legacy score is **never** substituted.
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

    Phase 3 contract: the robust engine is the **formal default and the
    only authority** in normal operation. The selector returns a
    ``"legacy"``-tagged result in exactly one case — the operator has
    flipped ``LEGACY_PIPELINE_ROLLBACK=true`` for an emergency standby
    revert. When it does, every symbol receives the legacy score
    regardless of bucketing or percent, the result is tagged
    ``engine_tag="legacy"``, ``fell_back=True``,
    ``fallback_reason="legacy_rollback"``, and
    ``robust_silent_fallback_total{reason="legacy_rollback"}`` is
    incremented so the admin status endpoint and Prometheus both
    surface the override.

    Outside rollback, every result is tagged ``engine_tag="robust"``.
    Robust failures (missing indicators, compute exception) surface as
    a robust-tagged sentinel — ``score=None``, ``fell_back=False``,
    ``fallback_reason`` kept purely for telemetry. The
    ``robust_silent_fallback_total`` counter is still bumped with the
    matching reason so ops can see how often the robust engine cannot
    produce a value, but the legacy score is **never** returned in
    place of the missing robust score outside rollback. Callers that
    need a numeric score must treat ``score is None`` as a rejection
    (the consumer in ``pipeline_scan._apply_robust_authoritative_scoring``
    explicitly does this).

    Critical-gate / confidence-gate rejections from the robust engine
    are also robust-tagged (``score=0.0`` from the engine, ``rejected=True``
    on the underlying ``robust_score``).
    """
    legacy = _coerce_float(legacy_score)

    if is_legacy_rollback_active():
        # Emergency revert — every symbol resolves to legacy. We bump
        # the silent-fallback counter with a dedicated reason so the
        # admin endpoint and Prometheus surface the override.
        increment_silent_fallback("legacy_rollback")
        return SelectScoreResult(
            score=legacy,
            engine_tag="legacy",
            bucketed=False,
            fell_back=True,
            fallback_reason="legacy_rollback",
        )

    bucketed = should_use_robust(symbol, percent=percent)

    if not bucketed:
        # ``should_use_robust`` only returns False for an empty symbol
        # outside rollback (Phase 3). We still tag the result as
        # ``robust`` because the legacy engine is on standby and must
        # not be re-introduced as a routine fallback. The score is
        # ``None`` so the caller treats this as a non-trade signal.
        return SelectScoreResult(
            score=None,
            engine_tag="robust",
            bucketed=False,
            fell_back=False,
            fallback_reason="empty_symbol",
        )

    if not indicators:
        # Robust failure outside rollback — surface as a robust-tagged
        # sentinel. Counter still bumped for telemetry.
        increment_silent_fallback("missing_indicators")
        return SelectScoreResult(
            score=None,
            engine_tag="robust",
            bucketed=True,
            fell_back=False,
            fallback_reason="missing_indicators",
        )

    robust = compute_robust_score(
        symbol,
        indicators,
        score_config=score_config,
        flow_source_hint=flow_source_hint,
    )

    if robust is None:
        # Robust failure outside rollback — surface as a robust-tagged
        # sentinel. Counter still bumped for telemetry.
        increment_silent_fallback("compute_failed")
        return SelectScoreResult(
            score=None,
            engine_tag="robust",
            bucketed=True,
            fell_back=False,
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

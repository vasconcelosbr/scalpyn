"""Deterministic per-symbol bucketing for the Phase 2 gradual rollout.

The robust indicator pipeline is rolled out in three steps (10% → 50% →
100%) controlled by ``USE_ROBUST_INDICATORS_PERCENT``. A symbol is
"in the rollout bucket" when

    int(sha1(symbol).hexdigest(), 16) % 100 < percent

This is fully deterministic across processes and across restarts: the
same symbol always lands in the same bucket for a given percent, which
keeps user-visible scoring stable instead of flapping between engines.

A symbol-level override is provided so ops can opportunistically force
or exclude a symbol without changing the global percent.
"""

from __future__ import annotations

import hashlib
import os
from typing import Iterable, Optional, Set

from ...config import settings


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _bucket_index(symbol: str) -> int:
    """Stable 0-99 bucket for ``symbol``. Uppercase + sha1 → modulo 100."""
    norm = _normalize_symbol(symbol)
    if not norm:
        return 0
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return int(digest, 16) % 100


def _read_overrides(env_var: str) -> Set[str]:
    raw = os.environ.get(env_var, "") or ""
    out: Set[str] = set()
    for token in raw.split(","):
        norm = _normalize_symbol(token)
        if norm:
            out.add(norm)
    return out


def get_rollout_percent(percent: Optional[int] = None) -> int:
    """Return the configured rollout percentage clamped to ``[0, 100]``.

    Resolution order:
      1. Explicit ``percent`` argument (used by tests / preflight).
      2. ``settings.USE_ROBUST_INDICATORS_PERCENT``.
      3. ``USE_ROBUST_INDICATORS_PERCENT`` env var fallback.
    """
    if percent is None:
        percent = getattr(settings, "USE_ROBUST_INDICATORS_PERCENT", 0)
        if percent in (None, 0):
            raw = os.environ.get("USE_ROBUST_INDICATORS_PERCENT")
            if raw is not None and str(raw).strip() != "":
                try:
                    percent = int(str(raw).strip())
                except (TypeError, ValueError):
                    percent = 0
    try:
        pct = int(percent or 0)
    except (TypeError, ValueError):
        pct = 0
    return max(0, min(100, pct))


def should_use_robust(symbol: str, percent: Optional[int] = None) -> bool:
    """Return True iff ``symbol`` is bucketed into the robust pipeline.

    The deterministic bucket index is the same across every process so
    long as ``percent`` matches; this guarantees a symbol cannot flap
    between engines on consecutive scans.
    """
    pct = get_rollout_percent(percent)
    norm = _normalize_symbol(symbol)
    if not norm:
        return False
    forced = _read_overrides("ROBUST_FORCE_SYMBOLS")
    if norm in forced:
        return True
    excluded = _read_overrides("ROBUST_EXCLUDE_SYMBOLS")
    if norm in excluded:
        return False
    if pct <= 0:
        return False
    if pct >= 100:
        return True
    return _bucket_index(norm) < pct


def bucketed_symbols(
    symbols: Iterable[str],
    percent: Optional[int] = None,
) -> Set[str]:
    """Return the subset of ``symbols`` that lands in the robust bucket."""
    pct = get_rollout_percent(percent)
    return {
        _normalize_symbol(s) for s in symbols
        if should_use_robust(s, percent=pct)
    }


__all__ = [
    "bucketed_symbols",
    "get_rollout_percent",
    "should_use_robust",
]

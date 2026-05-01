"""Bucketing helpers for the robust indicator pipeline.

Phase 3 (deprecation) makes the robust engine the formal default for
every symbol. ``should_use_robust`` is now a one-line wrapper around
``is_legacy_rollback_active``: every symbol uses the robust engine
unless the operator has flipped ``LEGACY_PIPELINE_ROLLBACK=true`` for
an emergency standby revert.

The historic Phase 2 per-symbol bucket math —

    int(sha1(symbol).hexdigest(), 16) % 100 < percent

— is preserved as ``is_symbol_in_robust_bucket`` so admin diagnostics
and tests can still reason about how many symbols *would* land in the
robust bucket at any given percent. Bucket math is no longer consulted
on the hot path.

A symbol-level override (``ROBUST_FORCE_SYMBOLS`` /
``ROBUST_EXCLUDE_SYMBOLS``) is still honoured by the diagnostic
helper. The rollback flag overrides everything.
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


def is_legacy_rollback_active() -> bool:
    """Return True iff the emergency legacy rollback flag is set.

    Reads ``settings.LEGACY_PIPELINE_ROLLBACK`` first, then falls back
    to the ``LEGACY_PIPELINE_ROLLBACK`` env var so ops can flip the
    rollback at the container boundary without code changes.
    """
    if bool(getattr(settings, "LEGACY_PIPELINE_ROLLBACK", False)):
        return True
    raw = os.environ.get("LEGACY_PIPELINE_ROLLBACK", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def get_rollout_percent(percent: Optional[int] = None) -> int:
    """Return the configured rollout percentage clamped to ``[0, 100]``.

    Phase 3: the default in ``Settings`` is 100, so this normally returns
    100 unless the operator explicitly downshifts. Resolution order is
    unchanged so the diagnostic helpers keep working:

      1. Explicit ``percent`` argument (used by tests / diagnostics).
      2. ``settings.USE_ROBUST_INDICATORS_PERCENT``.
      3. ``USE_ROBUST_INDICATORS_PERCENT`` env var fallback.
    """
    if percent is None:
        percent = getattr(settings, "USE_ROBUST_INDICATORS_PERCENT", 100)
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


def is_symbol_in_robust_bucket(
    symbol: str,
    percent: Optional[int] = None,
) -> bool:
    """Diagnostic-only: does ``symbol`` land in the robust rollout bucket?

    This preserves the Phase 2 per-symbol bucketing math so the admin
    endpoint can still answer "how many symbols *would* use the robust
    engine at percent=N?" and so the rollout-distribution test suite can
    keep asserting determinism + monotonicity.

    Hot-path code MUST NOT use this helper. Use :func:`should_use_robust`
    instead — that's the one that honours the Phase 3 rollback flag.
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


def should_use_robust(symbol: str, percent: Optional[int] = None) -> bool:
    """Return True iff ``symbol`` should use the robust score engine.

    Phase 3: this is a thin wrapper. Every symbol uses the robust engine
    unless ``LEGACY_PIPELINE_ROLLBACK`` is set, in which case the legacy
    engine takes over for *every* symbol. The ``percent`` argument is
    accepted for backwards-compatibility with diagnostic callers but is
    ignored on the hot path — see :func:`is_symbol_in_robust_bucket`
    for the bucket math.
    """
    if is_legacy_rollback_active():
        return False
    if not _normalize_symbol(symbol):
        return False
    return True


def bucketed_symbols(
    symbols: Iterable[str],
    percent: Optional[int] = None,
) -> Set[str]:
    """Return the subset of ``symbols`` that lands in the robust bucket.

    Used by admin diagnostics to show how many symbols *would* fall into
    the robust bucket at the given ``percent``. This intentionally uses
    the diagnostic bucket math (not :func:`should_use_robust`) so the
    answer reflects the historic rollout shape rather than the Phase 3
    "always robust unless rollback" flag.
    """
    pct = get_rollout_percent(percent)
    return {
        _normalize_symbol(s) for s in symbols
        if is_symbol_in_robust_bucket(s, percent=pct)
    }


__all__ = [
    "bucketed_symbols",
    "get_rollout_percent",
    "is_legacy_rollback_active",
    "is_symbol_in_robust_bucket",
    "should_use_robust",
]

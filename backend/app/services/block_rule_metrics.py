"""Prometheus counters for Block Rule outcomes (Task #253).

Block rules use *negative-polarity* vocabulary in the trace contract:
``outcome="TRIPPED"`` means the configured (bad) condition matched and
the asset was rejected; ``outcome="OK"`` means the condition did not
match and the asset is free; ``outcome="SKIPPED"`` means the underlying
data was missing/invalid so the block could not be evaluated.

We expose a single counter keyed by ``{rule, outcome}`` so dashboards
can chart trip rate per block over time and operators can spot a block
that suddenly trips on every symbol (almost always a tuning bug, not a
real signal). The ``rule`` label carries the block's display name —
the same string that surfaces in the UI as the block-rule indicator.

Cardinality is bounded by the number of configured blocks across all
profiles — small (single digits per profile, < 50 in practice). The
``outcome`` label is closed-set (3 values).

Counter name
------------
``scalpyn_block_rule_tripped_total{rule, outcome}``

The ``_tripped`` suffix matches the dominant operator vocabulary
established in Task #253 (UI shows "TRIPPED"). No prior counter for
block rules exists in the codebase, so there is nothing to alias —
this is a fresh first-class metric.

Like the other metric modules in ``app/services``, this one degrades
to no-ops when ``prometheus_client`` is not installed (tests / dev
shells). Importers can always call :func:`record_block_outcome` without
guarding the optional dependency.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter  # type: ignore[import-untyped]
    _PROM_OK = True
except Exception as exc:  # pragma: no cover — optional dep
    Counter = None  # type: ignore[assignment]
    _PROM_OK = False
    logger.debug("prometheus_client unavailable: %s — block-rule metrics disabled", exc)


_BLOCK_OUTCOME: Optional["Counter"] = None


_VALID_OUTCOMES = frozenset({"OK", "TRIPPED", "SKIPPED"})


def _init() -> None:
    global _BLOCK_OUTCOME
    if not _PROM_OK or _BLOCK_OUTCOME is not None:
        return
    _BLOCK_OUTCOME = Counter(
        "scalpyn_block_rule_tripped_total",
        "Block-rule evaluations grouped by outcome (Task #253).",
        ["rule", "outcome"],
    )


def record_block_outcome(rule: str, outcome: str) -> None:
    """Increment the block-outcome counter.

    No-op when prometheus is unavailable, when ``outcome`` is outside
    the closed set OK/TRIPPED/SKIPPED, or when ``rule`` is empty.
    """
    if not rule or outcome not in _VALID_OUTCOMES:
        return
    _init()
    if _BLOCK_OUTCOME is None:
        return
    try:
        _BLOCK_OUTCOME.labels(rule=rule, outcome=outcome).inc()
    except Exception as exc:  # pragma: no cover
        logger.debug("block outcome counter inc failed (%s/%s): %s", rule, outcome, exc)

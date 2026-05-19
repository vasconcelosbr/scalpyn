"""Single-source-of-truth indicator read path for all decision engines.

ARCHITECTURAL CONSTRAINT (Task #215):
    This module is the ONLY sanctioned way to read indicator data for
    decision-making. Every consumer (pipeline_scan, evaluate_signals,
    execute_buy, and any future task) MUST go through
    :func:`get_merged_indicators`.

    A direct ``SELECT … FROM indicators`` against ``indicators_json`` is
    a regression and reintroduces the partial-row bug:

      * The ``indicators`` table is partitioned by ``scheduler_group``.
        - structural    → 15 min cadence, writes RSI/MACD/ADX/EMA/Bollinger
        - microstructure → 5 min cadence, writes taker_ratio/spread/VWAP/volume
      * Each row carries a *partial* envelope. A naive
        ``SELECT DISTINCT ON (symbol) ... ORDER BY time DESC`` returns
        a microstructure-only row in ~67–87% of execution-cycle calls,
        making RSI/MACD physically absent from the consumer's view even
        though they exist in the DB and render correctly in the UI.

The provider wraps :func:`fetch_merged_indicators` (the dual-group merge),
exposes the shared completeness guard, and emits sampled
``indicators_used`` telemetry so collection gaps surface in logs.
"""

from __future__ import annotations

import logging
import os
import random
from typing import Any, Dict, Iterable, List, Optional

from .indicator_validity import unwrap_envelope_value
from ..utils.indicator_merge import (
    MergedIndicators,
    fetch_merged_indicators,
)


logger = logging.getLogger(__name__)


# ── Required-core completeness rule ──────────────────────────────────────────
# These keys are the canonical OUTPUT names of the structural scheduler's
# RSI / ADX / MACD calculations.
#
# * ``rsi`` and ``adx`` are written directly by ``feature_engine``.
# * ``macd_histogram`` is the actionable momentum field consumed by
#   ``robust_indicators/asset_score.py:60`` and the entry-trigger /
#   block-rule engines. The raw ``macd`` line value is also written but
#   is not what downstream rules read; ``macd_histogram`` is the
#   authoritative key for "MACD presence" in decision logic.
#
# Renaming any of these requires updating, in order:
#   1. ``feature_engine._calc_macd`` (writer side)
#   2. ``structural_scheduler_service`` (cadence / payload shape)
#   3. ``indicator_validity._PLAUSIBILITY_RULES`` (validity rules)
#   4. all decision engines (consumer side)
REQUIRED_CORE_INDICATORS: tuple[str, ...] = ("adx", "rsi", "macd_histogram")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Default 1% sample. Set to ``100`` during incident response to capture
# every consumer cycle; ``0`` disables the log entirely.
INDICATORS_USED_LOG_SAMPLE_PCT: int = _env_int("INDICATORS_USED_LOG_SAMPLE_PCT", 1)


def is_complete(indicators: Dict[str, Any]) -> tuple[bool, list[str]]:
    """Return ``(is_complete, missing_keys)`` per :data:`REQUIRED_CORE_INDICATORS`.

    ``indicators`` is the flat dict produced by
    :meth:`MergedIndicators.as_flat_dict` (or any equivalent flat indicator
    dict). Envelope unwrapping is applied defensively for callers that
    pass raw ``indicators_json`` payloads.
    """
    missing: list[str] = []
    for key in REQUIRED_CORE_INDICATORS:
        if unwrap_envelope_value(indicators.get(key)) is None:
            missing.append(key)
    return (not missing), missing


def filter_incomplete_assets(assets: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split a list of pipeline-asset dicts into ``(complete, incomplete)``.

    Asset shape: ``{"symbol": ..., "indicators": {...}, ...}``. Used by
    pipeline_scan; preserved here so all three consumers route through
    one implementation.
    """
    complete: list[dict] = []
    incomplete: list[dict] = []

    for asset in assets:
        ok, missing = is_complete(asset.get("indicators") or {})
        if ok:
            complete.append(asset)
        else:
            incomplete.append(asset)
            logger.warning(
                "[IndicatorsProvider] QUARANTINED %s — core indicators null: %s "
                "(asset will not advance until indicators are fully computed)",
                asset.get("symbol", "?"),
                missing,
            )

    if incomplete:
        logger.info(
            "[IndicatorsProvider] Core indicator guard: %d/%d assets quarantined "
            "(required=%s). Sample: %s",
            len(incomplete),
            len(assets),
            list(REQUIRED_CORE_INDICATORS),
            [a.get("symbol") for a in incomplete[:10]],
        )

    return complete, incomplete


async def get_merged_indicators(
    db,
    symbols: List[str],
    *,
    now=None,
    include_stale: bool = False,
) -> Dict[str, MergedIndicators]:
    """Single-source-of-truth indicator fetch for decision engines.

    Wraps :func:`fetch_merged_indicators` so every consumer (pipeline_scan,
    evaluate_signals, execute_buy) goes through the same code path and
    the same telemetry. Returns ``Dict[symbol, MergedIndicators]`` —
    symbols with no indicator rows are absent from the dict.
    """
    merged = await fetch_merged_indicators(
        db, symbols, now=now, include_stale=include_stale
    )
    _emit_sampled_telemetry(merged)
    return merged


def _emit_sampled_telemetry(merged: Dict[str, MergedIndicators]) -> None:
    """Emit a sampled INFO log per symbol for indicator key/source observability.

    Sample rate controlled by env var ``INDICATORS_USED_LOG_SAMPLE_PCT``
    (default 1). Setting it to ``100`` enables full-trace logging during
    incident response; ``0`` disables.
    """
    pct = INDICATORS_USED_LOG_SAMPLE_PCT
    if pct <= 0 or not merged:
        return

    for symbol, mi in merged.items():
        if pct < 100 and random.random() * 100.0 >= pct:
            continue

        keys = sorted(mi.values.keys())
        src_hist: Dict[str, int] = {}
        for k in keys:
            grp = (mi.meta.get(k) or {}).get("group") or "unknown"
            src_hist[grp] = src_hist.get(grp, 0) + 1

        logger.info(
            "indicators_used | symbol=%s | n=%d | source_groups=%s | keys=%s",
            symbol,
            len(keys),
            src_hist,
            keys,
        )


async def build_full_flat_snapshot(
    db,
    symbol: str,
    *,
    include_stale: bool = True,
) -> Dict[str, Any]:
    """Single-source-of-truth flat ``{key: scalar}`` snapshot for ML capture.

    Task #306 — canonical helper used to populate
    ``shadow_trades.features_snapshot_exit`` (and any future ML-feature
    capture point on the shadow / trade-simulation paths). Contract:

    * Reads ALL merged indicators for ``symbol`` via the same SSoT path
      used by the decision engines (:func:`get_merged_indicators`), so
      the exit snapshot has the **same key set** as the entry snapshot
      that ``decisions_log.metrics["indicators_snapshot"]`` captured.
    * Returns a flat ``{key: scalar}`` dict where every value is a
      scalar (int / float / bool / None). Non-scalar values (dict /
      list) are filtered out defensively — required by
      :class:`DatasetBuilder` (Task #290 gotcha: ``float({"value": …})``
      raises ``TypeError`` and contaminates the dataset).
    * Returns an empty dict when no merged indicators exist for the
      symbol (caller decides how to surface that to the UI). Never
      raises — exceptions propagate from the caller's try/except
      because the capture is best-effort (must not abort the shadow
      close / TP/SL/timeout invariant).

    ``include_stale=True`` (default) so the exit snapshot reflects
    "whatever the system saw" at close time, even if a microstructure
    refresh was momentarily missing. Stale flag is preserved in
    ``merged.meta`` (not used here — flat output is values-only).
    """
    merged_map = await get_merged_indicators(
        db, [symbol], include_stale=include_stale
    )
    merged = merged_map.get(symbol)
    if merged is None:
        return {}
    flat: Dict[str, Any] = {}
    for key, value in merged.values.items():
        # Defensive: never emit dict/list — would break DatasetBuilder.
        if isinstance(value, (dict, list)):
            continue
        flat[key] = value
    return flat


def build_indicators_snapshot(
    merged: MergedIndicators,
    keys: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build a compact ``{key: {value, source_group, ts, stale}}`` snapshot.

    Persisted into ``decisions_log.metrics["indicators_snapshot"]`` so a
    "decision vs DB" investigation can compare the exact payload that
    was used against the table state at decision time.

    By default snapshots ONLY :data:`REQUIRED_CORE_INDICATORS` so the
    JSONB column stays small (3 keys × ~4 fields = ~12 entries per
    decision). Callers that consumed additional indicators (e.g. score
    components, block-rule inputs) should pass an explicit ``keys``
    iterable derived from what the decision actually read — that scopes
    the snapshot to "exactly what was consumed", not "everything that
    happened to be merged".
    """
    if keys is None:
        keys_to_dump = set(REQUIRED_CORE_INDICATORS)
    else:
        # Always include required-core keys so the snapshot is self-evident
        # about the completeness state of the decision.
        keys_to_dump = set(keys) | set(REQUIRED_CORE_INDICATORS)

    snapshot: Dict[str, Dict[str, Any]] = {}
    for key in sorted(keys_to_dump):
        meta = merged.meta.get(key) or {}
        ts = meta.get("timestamp")
        snapshot[key] = {
            "value": merged.values.get(key),
            "source_group": meta.get("group"),
            "ts": ts.isoformat() if ts is not None else None,
            "stale": meta.get("stale", False),
        }
    return snapshot

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
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .indicator_constants import REQUIRED_CORE_INDICATORS  # noqa: F401 — re-exported
from .indicator_validity import unwrap_envelope_value
from .profile_runtime_config import canonical_hash
from ..utils.indicator_merge import (
    MergedIndicators,
    fetch_merged_indicators,
    fetch_timeframe_indicators,
)


logger = logging.getLogger(__name__)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _derived_snapshot_envelope(
    merged: MergedIndicators, *, key: str, meta: Dict[str, Any], value: Any,
) -> Dict[str, Any] | None:
    dependency_times = meta.get("dependency_source_times") or {}
    if not dependency_times:
        return None
    dependencies = []
    for dependency, expected_at in dependency_times.items():
        matches = [
            candidate for candidate in merged.candidates
            if candidate.get("indicator") == dependency
            and _iso(candidate.get("source_timestamp")) == _iso(expected_at)
            and isinstance(candidate.get("envelope"), dict)
        ]
        if not matches:
            return None
        selected = max(matches, key=lambda item: str(item.get("computed_at") or ""))
        material = dict(selected.get("envelope") or {})
        expected_hash = material.pop("envelope_hash", None)
        if not expected_hash or expected_hash != canonical_hash(material):
            return None
        dependencies.append(selected)
    identity_fields = (
        "timeframe", "market_type", "source_provider", "provider_policy_id",
        "candle_policy", "candle_closed", "config_hash", "capture_contract_version",
    )
    identities = {
        tuple((candidate.get("envelope") or {}).get(field) for field in identity_fields)
        for candidate in dependencies
    }
    source_times = {_iso(candidate.get("source_timestamp")) for candidate in dependencies}
    if len(identities) != 1 or len(source_times) != 1:
        return None
    identity = dict(zip(identity_fields, next(iter(identities))))
    dependency_hashes = {
        str(candidate["indicator"]): (candidate.get("envelope") or {}).get("envelope_hash")
        for candidate in dependencies
    }
    if any(not item for item in dependency_hashes.values()):
        return None
    envelope = {
        "value": value, "status": "available", "source": "derived",
        "confidence": min(
            float((candidate.get("envelope") or {}).get("confidence") or 0)
            for candidate in dependencies
        ),
        **identity,
        "scheduler_group": meta.get("group"),
        "source_timestamp": next(iter(source_times)),
        "computed_at": max(_iso(candidate.get("computed_at")) or "" for candidate in dependencies),
        "available_at": max(_iso(candidate.get("available_at")) or "" for candidate in dependencies),
        "producer_version": "indicator_merge_derived_v1",
        "dependency_hashes": dependency_hashes,
    }
    envelope["envelope_hash"] = canonical_hash(envelope)
    return envelope


# ── Required-core completeness rule ──────────────────────────────────────────
# Imported from indicator_constants.py (single source of truth).
# See that module's docstring for the full rationale and rename procedure.
# Re-exported here so existing importers (``from .indicators_provider import
# REQUIRED_CORE_INDICATORS``) continue to work without modification.


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


async def get_timeframe_indicators(
    db,
    symbols: List[str],
    *,
    timeframe: str,
    market_type: str = "spot",
    groups: Optional[List[str]] = None,
    now=None,
    include_stale: bool = False,
) -> Dict[str, MergedIndicators]:
    """Governed exact-identity provider used by the observational MTF path."""
    merged = await fetch_timeframe_indicators(
        db,
        symbols,
        timeframe=timeframe,
        market_type=market_type,
        groups=groups,
        now=now,
        include_stale=include_stale,
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
        candidates = [
            candidate for candidate in merged.candidates
            if candidate.get("indicator") == key
            and candidate.get("group") == meta.get("group")
            and candidate.get("timeframe") == meta.get("timeframe")
            and candidate.get("actual") == merged.values.get(key)
        ]
        winner = max(
            candidates,
            key=lambda item: str(item.get("source_timestamp") or ""),
            default={},
        )
        envelope = dict(winner.get("envelope") or {})
        if not envelope:
            envelope = _derived_snapshot_envelope(
                merged, key=key, meta=meta, value=merged.values.get(key)
            ) or {}
            if envelope:
                winner = {
                    "source_timestamp": envelope.get("source_timestamp"),
                    "available_at": envelope.get("available_at"),
                    "source_provider": envelope.get("source_provider"),
                    "provider_policy_id": envelope.get("provider_policy_id"),
                    "candle_closed": envelope.get("candle_closed"),
                    "config_hash": envelope.get("config_hash"),
                    "producer_version": envelope.get("producer_version"),
                }
        snapshot[key] = {
            "value": merged.values.get(key),
            "source_group": meta.get("group"),
            "ts": ts.isoformat() if ts is not None else None,
            "timeframe": meta.get("timeframe") or envelope.get("timeframe"),
            "observed_timeframes": (
                meta.get("observed_timeframes")
                or ([envelope.get("timeframe")] if envelope.get("timeframe") else [])
            ),
            "timeframe_conflict": bool(meta.get("timeframe_conflict", False)),
            "stale": meta.get("stale", False),
            "oldest_source_at": (
                meta["oldest_source_at"].isoformat()
                if meta.get("oldest_source_at") is not None else None
            ),
            "newest_source_at": (
                meta["newest_source_at"].isoformat()
                if meta.get("newest_source_at") is not None else None
            ),
            "dependency_source_times": {
                dependency: value.isoformat() if value is not None else None
                for dependency, value in (meta.get("dependency_source_times") or {}).items()
            },
            "source_timestamp": (
                _iso(winner.get("source_timestamp")) if winner else None
            ),
            "available_at": _iso(winner.get("available_at")) if winner else None,
            "source_provider": winner.get("source_provider"),
            "provider_policy_id": winner.get("provider_policy_id"),
            "candle_closed": winner.get("candle_closed"),
            "config_hash": winner.get("config_hash"),
            "producer_version": winner.get("producer_version"),
            "envelope": envelope or None,
        }
    return snapshot


def build_grouped_indicators_snapshot(
    merged: MergedIndicators,
    *,
    required_by_group: Dict[str, Iterable[str]],
    timeframe: str,
    market_type: str = "spot",
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Resolve MTF inputs by their full temporal identity.

    The legacy snapshot above deliberately keeps its historical flat
    latest-winner semantics.  MTF cannot use that projection because the same
    indicator name may legitimately exist in more than one scheduler group.
    This additive snapshot therefore selects from ``merged.candidates`` by
    ``(indicator, timeframe, group, market_type)`` and never falls back to the
    flat winner.
    """

    grouped: Dict[str, Dict[str, Dict[str, Any]]] = {}
    matches_by_identity: Dict[tuple[str, str], list[dict[str, Any]]] = {}
    source_sets: list[set[str]] = []
    for group, names in required_by_group.items():
        group_name = str(group)
        for raw_name in names:
            name = str(raw_name)
            matches = [
                candidate
                for candidate in merged.candidates
                if str(candidate.get("indicator")) == name
                and str(candidate.get("timeframe")) == timeframe
                and str(candidate.get("group")) == group_name
                and str(candidate.get("market_type")) == market_type
            ]
            matches_by_identity[(group_name, name)] = matches
            if matches:
                source_sets.append({
                    _iso(item.get("source_timestamp"))
                    for item in matches
                    if item.get("source_timestamp") is not None
                })

    common_source_timestamp: str | None = None
    if source_sets and len(source_sets) == len(matches_by_identity):
        common = set.intersection(*source_sets)
        if common:
            common_source_timestamp = max(common)

    for group, names in required_by_group.items():
        group_name = str(group)
        grouped[group_name] = {}
        for raw_name in names:
            name = str(raw_name)
            matches = matches_by_identity.get((group_name, name), [])
            if not matches:
                continue
            if common_source_timestamp is not None:
                matches = [
                    item for item in matches
                    if _iso(item.get("source_timestamp")) == common_source_timestamp
                ]
            winner = max(
                matches,
                key=lambda item: (
                    str(item.get("source_timestamp") or ""),
                    str(item.get("available_at") or ""),
                    str(item.get("computed_at") or ""),
                ),
            )
            envelope = dict(winner.get("envelope") or {})
            grouped[group_name][name] = {
                "value": winner.get("actual"),
                "source_group": group_name,
                "ts": _iso(winner.get("computed_at")),
                "timeframe": timeframe,
                "observed_timeframes": [timeframe],
                "timeframe_conflict": False,
                "stale": bool(winner.get("stale", False)),
                "source_timestamp": _iso(winner.get("source_timestamp")),
                "available_at": _iso(winner.get("available_at")),
                "source_provider": winner.get("source_provider"),
                "provider_policy_id": winner.get("provider_policy_id"),
                "candle_closed": winner.get("candle_closed"),
                "config_profile_id": winner.get("config_profile_id"),
                "config_hash": winner.get("config_hash"),
                "producer_version": winner.get("producer_version"),
                "fallback_used": bool(winner.get("fallback_used", False)),
                "envelope": envelope or None,
            }
    return grouped

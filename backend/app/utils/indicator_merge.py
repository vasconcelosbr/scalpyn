"""Shared utility for merging dual-scheduler indicator rows.

Implements deterministic merge from structural + microstructure indicator groups.

Merge contract (applied per indicator key):
  1. Absolute staleness filter: rows older than group stale limit are absent.
       structural   → STRUCTURAL_STALE_SECONDS (env STRUCTURAL_STALE_SECONDS,  default 1800)
       microstructure → MICROSTRUCTURE_STALE_SECONDS (env MICRO_STALE_SECONDS, default 600)
  2. Inter-group drift check: if |micro_ts - struct_ts| > INDICATOR_MAX_DRIFT_SECONDS,
     the OLDER group is additionally treated as absent (dropped).
  3. For keys present in only one remaining group — use that group's value.
  4. For keys present in both remaining groups — microstructure is preferred
     (its faster cadence means it is always at least as fresh as structural).
  5. Hybrid indicators (ema9_gt_ema50, ema_full_alignment) are computed
     post-merge if both contributing EMA values are present.

Environment overrides:
  INDICATOR_MAX_DRIFT_SECONDS  — inter-group drift cap (default 900)
  STRUCTURAL_STALE_SECONDS     — structural stale limit  (default 1800)
  MICRO_STALE_SECONDS          — microstructure stale limit (default 600)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Runtime-configurable limits
INDICATOR_MAX_DRIFT_SECONDS: float = _env_float("INDICATOR_MAX_DRIFT_SECONDS", 900.0)
STRUCTURAL_STALE_SECONDS: float = _env_float("STRUCTURAL_STALE_SECONDS", 1800.0)
MICROSTRUCTURE_STALE_SECONDS: float = _env_float("MICRO_STALE_SECONDS", 600.0)

# For legacy "combined" rows treat same as structural
_GROUP_STALE: Dict[str, float] = {
    "structural": STRUCTURAL_STALE_SECONDS,
    "microstructure": MICROSTRUCTURE_STALE_SECONDS,
    "combined": STRUCTURAL_STALE_SECONDS,
}


def _ensure_utc(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


class MergedIndicators:
    """Container for a symbol's merged indicator data with per-key metadata.

    attributes:
        values   — {key: scalar} usable for scoring (non-stale values only).
        meta     — {key: {group, age_seconds, timestamp, stale}} per key.
                   Includes keys from stale groups (stale=True) so the API
                   can show staleness signals without using stale values for
                   scoring.
    """

    def __init__(self) -> None:
        self.values: Dict[str, Any] = {}
        # {key: {group, age_seconds, timestamp, stale}}
        self.meta: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def as_flat_dict(self) -> Dict[str, Any]:
        """Flat {key: value} dict — non-stale values only, safe for scoring."""
        return dict(self.values)

    def as_enriched_dict(self) -> Dict[str, Dict[str, Any]]:
        """Enriched {key: {value, source_group, timestamp, stale}} for API responses.

        Includes stale keys with value=None and stale=True so that the UI can
        show debugging metadata while the score engine uses as_flat_dict() for
        actual filter evaluation.
        """
        enriched: Dict[str, Dict[str, Any]] = {}
        for k, m in self.meta.items():
            enriched[k] = {
                "value": self.values.get(k),  # None for stale keys
                "source_group": m.get("group"),
                "timestamp": m.get("timestamp").isoformat()
                if m.get("timestamp") is not None else None,
                "stale": m.get("stale", False),
                "age_seconds": m.get("age_seconds"),
            }
        return enriched


def merge_indicator_rows(
    rows: List[Tuple[str, Optional[datetime], Dict[str, Any]]],
    now: Optional[datetime] = None,
) -> MergedIndicators:
    """Merge a list of (group, timestamp, indicators_dict) tuples for ONE symbol.

    Args:
        rows: Each entry is (scheduler_group, row_timestamp, indicators_dict).
        now:  Current UTC time used to compute ages.  Defaults to utcnow().

    Returns:
        MergedIndicators with merged values + per-key metadata.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    else:
        now = _ensure_utc(now)
    assert now is not None

    # ── Step 1: Absolute staleness filter ────────────────────────────────────
    # Stale rows are excluded from scoring values but tracked in `stale_rows`
    # so their metadata can be added to result.meta with stale=True.
    live: List[Tuple[str, Optional[datetime], Dict[str, Any], float]] = []
    stale_rows: List[Tuple[str, Optional[datetime], Dict[str, Any], float]] = []
    for grp, ts, ind_json in rows:
        ts_utc = _ensure_utc(ts)
        stale_limit = _GROUP_STALE.get(grp, STRUCTURAL_STALE_SECONDS)
        if ts_utc is not None:
            age = (now - ts_utc).total_seconds()
            if age > stale_limit:
                logger.debug(
                    "[merge] group=%s ts=%s age=%.0fs > stale_limit=%.0fs — stale",
                    grp, ts_utc.isoformat(), age, stale_limit,
                )
                stale_rows.append((grp, ts_utc, ind_json or {}, age))
                continue
            live.append((grp, ts_utc, ind_json or {}, age))
        else:
            live.append((grp, None, ind_json or {}, float("inf")))

    # ── Step 2: Inter-group drift cap ─────────────────────────────────────────
    # Pick latest timestamp per group from live rows.
    ts_by_group: Dict[str, Optional[datetime]] = {}
    for grp, ts_utc, _, _age in live:
        existing = ts_by_group.get(grp)
        if existing is None or (ts_utc is not None and ts_utc > existing):
            ts_by_group[grp] = ts_utc

    struct_ts = ts_by_group.get("structural") or ts_by_group.get("combined")
    micro_ts = ts_by_group.get("microstructure")

    max_drift = _env_float("INDICATOR_MAX_DRIFT_SECONDS", INDICATOR_MAX_DRIFT_SECONDS)

    # If drift > max_drift, drop the OLDER group from live
    if struct_ts is not None and micro_ts is not None:
        drift = abs((micro_ts - struct_ts).total_seconds())
        if drift > max_drift:
            if struct_ts < micro_ts:
                # Structural is older — treat as absent for shared keys
                stale_groups = {"structural", "combined"}
                logger.debug(
                    "[merge] drift=%.0fs > %.0fs — structural group absent",
                    drift, max_drift,
                )
            else:
                # Microstructure is older — treat as absent
                stale_groups = {"microstructure"}
                logger.debug(
                    "[merge] drift=%.0fs > %.0fs — microstructure group absent",
                    drift, max_drift,
                )
            # Track drift-dropped rows as stale (same as absolute staleness)
            for entry in live:
                if entry[0] in stale_groups:
                    stale_rows.append(entry)
            live = [(g, t, d, a) for g, t, d, a in live if g not in stale_groups]

    # ── Step 3: Per-key latest-timestamp-wins merge ───────────────────────────
    # For each indicator key:
    #   - If present in only one group: use that group's value.
    #   - If present in multiple groups: use the value from the group with the
    #     newest timestamp.  Microstructure is the tiebreak if timestamps are
    #     equal (its faster cadence means it is at least as fresh as structural).
    result = MergedIndicators()

    for grp, ts_utc, ind_json, age in live:
        for k, v in ind_json.items():
            if not isinstance(v, (int, float, bool, str)):
                continue

            existing_meta = result.meta.get(k)
            if existing_meta is None:
                # First entry for this key
                result.values[k] = v
                result.meta[k] = {
                    "group": grp,
                    "age_seconds": age,
                    "timestamp": ts_utc,
                }
                continue

            existing_ts: Optional[datetime] = existing_meta.get("timestamp")

            # Determine whether this entry should overwrite the existing one
            should_overwrite = False
            if ts_utc is None and existing_ts is None:
                # Neither has a timestamp — prefer microstructure (tiebreak)
                should_overwrite = grp == "microstructure"
            elif ts_utc is None:
                # Existing has a timestamp, this one doesn't — keep existing
                should_overwrite = False
            elif existing_ts is None:
                # This entry has a timestamp, existing doesn't — use this
                should_overwrite = True
            elif ts_utc > existing_ts:
                # This entry is newer — use it
                should_overwrite = True
            elif ts_utc == existing_ts:
                # Equal timestamps — microstructure is the tiebreak
                should_overwrite = grp == "microstructure"
            # else: existing is newer — keep existing (should_overwrite stays False)

            if should_overwrite:
                result.values[k] = v
                result.meta[k] = {
                    "group": grp,
                    "age_seconds": age,
                    "timestamp": ts_utc,
                }

    # ── Step 4: Post-merge hybrid indicators ──────────────────────────────────
    # ema9_gt_ema50: EMA9 (microstructure) vs EMA50 (structural)
    if "ema9" in result.values and "ema50" in result.values:
        result.values["ema9_gt_ema50"] = result.values["ema9"] > result.values["ema50"]
        ema9_age = (result.meta.get("ema9") or {}).get("age_seconds")
        ema50_age = (result.meta.get("ema50") or {}).get("age_seconds")
        ages = [a for a in (ema9_age, ema50_age) if a is not None]
        result.meta["ema9_gt_ema50"] = {
            "group": "structural",  # hybrid classified as structural
            "age_seconds": max(ages) if ages else None,
            "timestamp": None,
        }

    # ema_full_alignment: EMA9 > EMA50 > EMA200
    if "ema9" in result.values and "ema50" in result.values and "ema200" in result.values:
        result.values["ema_full_alignment"] = (
            result.values["ema9"] > result.values["ema50"] > result.values["ema200"]
        )
        ema9_age = (result.meta.get("ema9") or {}).get("age_seconds")
        ema50_age = (result.meta.get("ema50") or {}).get("age_seconds")
        ema200_age = (result.meta.get("ema200") or {}).get("age_seconds")
        ages = [a for a in (ema9_age, ema50_age, ema200_age) if a is not None]
        result.meta["ema_full_alignment"] = {
            "group": "structural",
            "age_seconds": max(ages) if ages else None,
            "timestamp": None,
            "stale": False,
        }

    # ── Step 5: Add stale metadata (debugging/observability) ─────────────────
    # Stale rows are excluded from `values` (not used for scoring), but their
    # keys are registered in `meta` with stale=True so that API responses can
    # surface staleness information to the UI without using stale values in
    # filter/scoring logic.  Non-stale keys that already exist in meta are not
    # overwritten (a fresh value always wins the metadata slot).
    for grp, ts_utc, ind_json, age in stale_rows:
        for k, v in ind_json.items():
            if not isinstance(v, (int, float, bool, str)):
                continue
            if k not in result.meta:
                # Key has no live entry — record as stale (value omitted)
                result.meta[k] = {
                    "group": grp,
                    "age_seconds": age,
                    "timestamp": ts_utc,
                    "stale": True,
                }
            # If a live entry exists for this key, don't overwrite with stale meta.

    # Ensure stale=False on all live meta entries (default for non-stale keys)
    for k, m in result.meta.items():
        if "stale" not in m:
            m["stale"] = False

    return result


async def fetch_merged_indicators(
    db,
    symbols: List[str],
    now: Optional[datetime] = None,
) -> Dict[str, MergedIndicators]:
    """Fetch and merge indicator rows for all given symbols from the DB.

    Returns:
        Dict mapping symbol → MergedIndicators.
        Symbols with no indicator rows are absent from the dict.
    """
    from sqlalchemy import text

    if not symbols:
        return {}

    if now is None:
        now = datetime.now(timezone.utc)

    merged: Dict[str, MergedIndicators] = {}

    # ── Try dual-scheduler query (requires scheduler_group column) ────────────
    try:
        dual_rows = (await db.execute(text("""
            SELECT DISTINCT ON (symbol, scheduler_group)
                symbol, scheduler_group, time, indicators_json
            FROM   indicators
            WHERE  symbol = ANY(:syms)
            ORDER  BY symbol, scheduler_group, time DESC
        """), {"syms": symbols})).fetchall()

        from collections import defaultdict
        by_sym: Dict[str, List] = defaultdict(list)
        for r in dual_rows:
            by_sym[r.symbol].append((
                r.scheduler_group or "combined",
                r.time,
                r.indicators_json or {},
            ))

        for sym, row_list in by_sym.items():
            merged[sym] = merge_indicator_rows(row_list, now=now)

        # Legacy fallback for symbols still missing
        missing = [s for s in symbols if s not in merged]
        if missing:
            _add_legacy_rows(merged, await _fetch_legacy(db, missing), now)

        return merged

    except Exception:
        # scheduler_group column does not exist — fall back to legacy
        pass

    legacy = await _fetch_legacy(db, symbols)
    _add_legacy_rows(merged, legacy, now)
    return merged


async def _fetch_legacy(db, symbols: List[str]) -> List:
    """Fetch multiple recent indicator rows per symbol for legacy merge.

    When the scheduler_group column is absent, each scheduler may have
    inserted separate rows (structural vs microstructure) that lack the
    group tag.  Fetching the last N rows (up to 3, within 2 h) and merging
    them by timestamp gives a more complete combined indicator set than
    taking only the single latest row.
    """
    from sqlalchemy import text
    if not symbols:
        return []
    try:
        rows = (await db.execute(text("""
            SELECT symbol, time, indicators_json
            FROM   indicators
            WHERE  symbol = ANY(:syms)
              AND  time > now() - interval '2 hours'
            ORDER  BY symbol, time DESC
        """), {"syms": symbols})).fetchall()
        return list(rows)
    except Exception as exc:
        logger.warning("[merge] legacy indicator fetch failed: %s", exc)
        return []


def _add_legacy_rows(
    merged: Dict[str, MergedIndicators],
    legacy_rows: List,
    now: datetime,
) -> None:
    """Merge multiple legacy rows per symbol into MergedIndicators.

    Groups rows by symbol and merges them as "combined" rows (all get the
    same group tag so they compete on timestamp alone — newest per key wins).
    """
    from collections import defaultdict
    by_sym: Dict[str, List] = defaultdict(list)
    for r in legacy_rows:
        if r.symbol not in merged:
            ts = _ensure_utc(r.time)
            by_sym[r.symbol].append(("combined", ts, r.indicators_json or {}))

    for sym, row_list in by_sym.items():
        if sym not in merged:
            merged[sym] = merge_indicator_rows(row_list, now=now)

"""Shared utility for merging dual-scheduler indicator rows.

Implements deterministic per-key merge from structural + microstructure
indicator groups.  Rules (applied per indicator key):
  1. For keys present in only one group — use that group's value.
  2. For keys present in both groups — use the value from the row with the
     most recent timestamp (latest timestamp wins).
  3. Staleness: if a group's row is older than STALE_SECONDS for that group,
     treat it as absent.
  4. Inter-group drift cap: if |micro_ts - struct_ts| > MAX_DRIFT_SECONDS,
     prefer the more recent group's value for shared keys.
  5. Hybrid indicators (ema9_gt_ema50, ema_full_alignment) are computed
     post-merge if both contributing EMA values are available.

Returns a merged indicator dict plus per-key metadata:
  {indicator_key: {value, group, age_seconds, timestamp}}
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# A group's row is considered stale if it is older than this.
STRUCTURAL_STALE_SECONDS: float = 1800.0   # 30 min (2× 15-min cadence)
MICROSTRUCTURE_STALE_SECONDS: float = 600.0  # 10 min (2× 5-min cadence)

# If the drift between structural and microstructure timestamps exceeds this
# threshold, shared keys use the more recent group.
MAX_DRIFT_SECONDS: float = 900.0  # 15 min

_GROUP_STALE_LIMITS: Dict[str, float] = {
    "structural": STRUCTURAL_STALE_SECONDS,
    "microstructure": MICROSTRUCTURE_STALE_SECONDS,
    "combined": STRUCTURAL_STALE_SECONDS,  # legacy rows treated as structural
}


def _ensure_utc(ts: Optional[datetime]) -> Optional[datetime]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


class MergedIndicators:
    """Container for a symbol's merged indicator data."""

    def __init__(self) -> None:
        self.values: Dict[str, Any] = {}
        # Per-key metadata: {key: {group, age_seconds, timestamp}}
        self.meta: Dict[str, Dict[str, Any]] = {}

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self.values

    def as_flat_dict(self) -> Dict[str, Any]:
        return dict(self.values)


def merge_indicator_rows(
    rows: List[Tuple[str, Optional[datetime], Dict[str, Any]]],
    now: Optional[datetime] = None,
) -> MergedIndicators:
    """Merge a list of (group, timestamp, indicators_json) tuples for ONE symbol.

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

    # ── 1. Filter stale rows ─────────────────────────────────────────────────
    live_rows: List[Tuple[str, Optional[datetime], Dict[str, Any]]] = []
    for grp, ts, ind_json in rows:
        ts_utc = _ensure_utc(ts)
        stale_limit = _GROUP_STALE_LIMITS.get(grp, STRUCTURAL_STALE_SECONDS)
        if ts_utc is not None:
            age = (now - ts_utc).total_seconds()
            if age > stale_limit:
                logger.debug(
                    "[merge] group=%s ts=%s age=%.0fs > stale_limit=%.0fs — skipping",
                    grp, ts_utc.isoformat(), age, stale_limit,
                )
                continue
        live_rows.append((grp, ts_utc, ind_json or {}))

    # ── 2. Collect per-group latest timestamps for drift check ────────────────
    ts_by_group: Dict[str, Optional[datetime]] = {}
    for grp, ts_utc, _ in live_rows:
        existing = ts_by_group.get(grp)
        if existing is None or (ts_utc is not None and ts_utc > existing):
            ts_by_group[grp] = ts_utc

    struct_ts = ts_by_group.get("structural") or ts_by_group.get("combined")
    micro_ts = ts_by_group.get("microstructure")

    # Compute inter-group drift
    inter_group_drift: Optional[float] = None
    if struct_ts is not None and micro_ts is not None:
        inter_group_drift = abs((micro_ts - struct_ts).total_seconds())

    # ── 3. Per-key merge with latest-timestamp-wins ───────────────────────────
    # Sort rows so most-recent rows are processed last (their values win).
    def _row_sort_key(t: Tuple[str, Optional[datetime], Dict]) -> float:
        ts = t[1]
        if ts is None:
            return 0.0
        return ts.timestamp()

    live_rows_sorted = sorted(live_rows, key=_row_sort_key)

    result = MergedIndicators()

    for grp, ts_utc, ind_json in live_rows_sorted:
        age = (now - ts_utc).total_seconds() if ts_utc is not None else None
        for k, v in ind_json.items():
            if not isinstance(v, (int, float, bool)):
                continue
            # Drift guard: if inter-group drift > MAX_DRIFT_SECONDS and this key
            # already has a value from the other group, prefer whichever has the
            # more recent timestamp (handled by sort order — last write wins).
            # The sort guarantees more-recent rows overwrite older ones, which
            # is exactly the "latest timestamp wins" rule.
            result.values[k] = v
            result.meta[k] = {
                "group": grp,
                "age_seconds": age,
                "timestamp": ts_utc,
            }

    # ── 4. Hybrid indicators: compute post-merge if components available ───────
    # ema9_gt_ema50: EMA9 (microstructure) vs EMA50 (structural)
    if "ema9" in result.values and "ema50" in result.values:
        result.values["ema9_gt_ema50"] = result.values["ema9"] > result.values["ema50"]
        # Age is the older of the two components (worst-case freshness)
        ema9_age = (result.meta.get("ema9") or {}).get("age_seconds")
        ema50_age = (result.meta.get("ema50") or {}).get("age_seconds")
        hybrid_age = max(a for a in [ema9_age, ema50_age] if a is not None) if any(
            a is not None for a in [ema9_age, ema50_age]
        ) else None
        result.meta["ema9_gt_ema50"] = {
            "group": "structural",  # classified as structural hybrid
            "age_seconds": hybrid_age,
            "timestamp": None,
        }

    # ema_full_alignment: EMA9 > EMA50 > EMA200
    if "ema9" in result.values and "ema50" in result.values and "ema200" in result.values:
        result.values["ema_full_alignment"] = (
            result.values["ema9"] > result.values["ema50"] > result.values["ema200"]
        )
        result.meta["ema_full_alignment"] = {
            "group": "structural",  # hybrid — anchored on structural EMA50/200
            "age_seconds": hybrid_age if "hybrid_age" in dir() else None,
            "timestamp": None,
        }

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

        # Group rows by symbol
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

        # Fallback for symbols with no dual rows
        missing = [s for s in symbols if s not in merged]
        if missing:
            _add_legacy_rows(merged, await _fetch_legacy(db, missing), now)

        return merged

    except Exception:
        # scheduler_group column does not exist yet — fall back to legacy query
        pass

    # ── Legacy query (single latest row per symbol) ───────────────────────────
    legacy = await _fetch_legacy(db, symbols)
    _add_legacy_rows(merged, legacy, now)
    return merged


async def _fetch_legacy(db, symbols: List[str]) -> List:
    from sqlalchemy import text
    if not symbols:
        return []
    try:
        rows = (await db.execute(text("""
            SELECT DISTINCT ON (symbol) symbol, time, indicators_json
            FROM   indicators
            WHERE  symbol = ANY(:syms)
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
    for r in legacy_rows:
        if r.symbol in merged:
            continue
        ts = _ensure_utc(r.time)
        age = (now - ts).total_seconds() if ts is not None else None
        rows = [("combined", ts, r.indicators_json or {})]
        merged[r.symbol] = merge_indicator_rows(rows, now=now)

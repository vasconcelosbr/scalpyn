"""Point-in-time backfill for L1_SPECTRUM directional ML features.

This is an operational script, not an Alembic migration. It updates only
``shadow_trades.features_snapshot`` and leaves outcomes, prices, TP/SL and labels
untouched.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.feature_engine import FeatureEngine  # noqa: E402


DIRECTIONAL_FEATURES = [
    "rsi_slope_3",
    "rsi_slope_5",
    "macd_hist_slope_3",
    "macd_hist_slope_5",
    "adx_slope_3",
    "vwap_reclaim_bool",
    "higher_highs_5",
    "higher_lows_5",
    "ema21_ema50_distance_pct",
    "di_plus_minus_diff",
]

FORMULA_VERSION = "directional_backfill_v1"
FEATURE_SCHEMA_VERSION = "directional_v1"
DEFAULT_RAILWAY = r"C:\Users\ricar\.railway\bin\railway.exe"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _db_url() -> str:
    env_url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL")
    if env_url:
        return env_url
    railway = os.getenv("RAILWAY_BIN", DEFAULT_RAILWAY)
    proc = subprocess.run(
        [railway, "variables", "--service", "Postgres", "--environment", "production", "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    data = json.loads(proc.stdout)
    url = data.get("DATABASE_PUBLIC_URL") or data.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_PUBLIC_URL/DATABASE_URL not found")
    return url


def _coverage_sql(days: int, source: str) -> str:
    feature_counts = ",\n".join(
        f"COUNT(*) FILTER (WHERE features_snapshot ? '{feature}') AS {feature}"
        for feature in DIRECTIONAL_FEATURES
    )
    return f"""
        SELECT COUNT(*) AS total,
               COUNT(DISTINCT symbol) AS symbols,
               MIN(created_at) AS min_created,
               MAX(created_at) AS max_created,
               {feature_counts}
        FROM shadow_trades
        WHERE source = :source
          AND created_at >= NOW() - (:days || ' days')::interval
          AND features_snapshot IS NOT NULL
          AND features_snapshot::text <> '{{}}'
    """


def _coverage(engine, days: int, source: str) -> dict[str, Any]:
    with engine.connect() as conn:
        row = conn.execute(text(_coverage_sql(days, source)), {"days": days, "source": source}).mappings().one()
    data = dict(row)
    total = int(data.get("total") or 0)
    features = {}
    for feature in DIRECTIONAL_FEATURES:
        count = int(data.get(feature) or 0)
        features[feature] = {
            "count": count,
            "coverage_pct": (count / total * 100.0) if total else None,
            "null_rate_pct": (100.0 - (count / total * 100.0)) if total else None,
        }
    return {
        "total": total,
        "symbols": int(data.get("symbols") or 0),
        "min_created": _jsonable(data.get("min_created")),
        "max_created": _jsonable(data.get("max_created")),
        "features": features,
    }


def _load_targets(engine, days: int, source: str, limit: int | None) -> list[dict[str, Any]]:
    missing_clause = " OR ".join(
        f"NOT (features_snapshot ? '{feature}') OR features_snapshot->>'{feature}' IS NULL"
        for feature in DIRECTIONAL_FEATURES
    )
    limit_clause = "LIMIT :limit" if limit else ""
    params: dict[str, Any] = {"days": days, "source": source}
    if limit:
        params["limit"] = limit
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"""
                SELECT id, symbol, created_at, entry_timestamp, features_snapshot
                FROM shadow_trades
                WHERE source = :source
                  AND created_at >= NOW() - (:days || ' days')::interval
                  AND features_snapshot IS NOT NULL
                  AND features_snapshot::text <> '{{}}'
                  AND ({missing_clause})
                ORDER BY created_at ASC
                {limit_clause}
                """
            ),
            params,
        ).mappings().all()
    return [dict(row) for row in rows]


def _load_closed_candles(engine, symbol: str, snapshot_time: datetime, timeframe: str, exchange: str, limit: int) -> pd.DataFrame:
    minutes = int(timeframe.rstrip("m")) if timeframe.endswith("m") else 5
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT time, open, high, low, close, volume, quote_volume
                FROM ohlcv
                WHERE symbol = :symbol
                  AND exchange = :exchange
                  AND timeframe = :timeframe
                  AND time <= (:snapshot_time - (:minutes || ' minutes')::interval)
                ORDER BY time DESC
                LIMIT :limit
                """
            ),
            {
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "snapshot_time": snapshot_time,
                "minutes": minutes,
                "limit": limit,
            },
        ).mappings().all()
    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        return df
    df = df.sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _load_candles_by_symbol(
    engine,
    targets: list[dict[str, Any]],
    timeframe: str,
    exchange: str,
    candle_limit: int,
) -> dict[str, pd.DataFrame]:
    if not targets:
        return {}
    minutes = int(timeframe.rstrip("m")) if timeframe.endswith("m") else 5
    start = min((row.get("entry_timestamp") or row.get("created_at")) for row in targets)
    end = max((row.get("entry_timestamp") or row.get("created_at")) for row in targets)
    start = start - timedelta(minutes=minutes * (candle_limit + 2))
    symbols = sorted({row["symbol"] for row in targets})
    candles_by_symbol: dict[str, pd.DataFrame] = {}
    with engine.connect() as conn:
        for symbol in symbols:
            rows = conn.execute(
                text(
                    """
                    SELECT time, open, high, low, close, volume, quote_volume
                    FROM ohlcv
                    WHERE symbol = :symbol
                      AND exchange = :exchange
                      AND timeframe = :timeframe
                      AND time >= :start
                      AND time <= :end
                    ORDER BY time ASC
                    """
                ),
                {
                    "symbol": symbol,
                    "exchange": exchange,
                    "timeframe": timeframe,
                    "start": start,
                    "end": end,
                },
            ).mappings().all()
            df = pd.DataFrame([dict(row) for row in rows])
            if not df.empty:
                for col in ["open", "high", "low", "close", "volume", "quote_volume"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            candles_by_symbol[symbol] = df
    return candles_by_symbol


def _build_patch(
    engine,
    row: dict[str, Any],
    feature_engine: FeatureEngine,
    *,
    timeframe: str,
    exchange: str,
    candle_limit: int,
    force: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = dict(row.get("features_snapshot") or {})
    snapshot_time = row.get("entry_timestamp") or row.get("created_at")
    candles = _load_closed_candles(engine, row["symbol"], snapshot_time, timeframe, exchange, candle_limit)
    return _build_patch_from_candles(
        row,
        feature_engine,
        candles,
        timeframe=timeframe,
        exchange=exchange,
        force=force,
    )


def _build_patch_from_candles(
    row: dict[str, Any],
    feature_engine: FeatureEngine,
    candles: pd.DataFrame,
    *,
    timeframe: str,
    exchange: str,
    force: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = dict(row.get("features_snapshot") or {})
    snapshot_time = row.get("entry_timestamp") or row.get("created_at")
    audit = {
        "id": str(row["id"]),
        "symbol": row["symbol"],
        "snapshot_time": _jsonable(snapshot_time),
        "candles": int(len(candles)),
        "candle_cutoff": None,
        "filled": [],
        "kept_null": [],
        "skipped_existing": [],
        "future_violation": False,
    }
    if candles.empty:
        audit["kept_null"] = list(DIRECTIONAL_FEATURES)
        return {}, audit

    candle_cutoff = candles["time"].iloc[-1]
    audit["candle_cutoff"] = _jsonable(candle_cutoff)
    if candle_cutoff > snapshot_time - pd.Timedelta(minutes=int(timeframe.rstrip("m"))):
        audit["future_violation"] = True
        audit["kept_null"] = list(DIRECTIONAL_FEATURES)
        return {}, audit

    computed = feature_engine._calc_directional_features(candles)
    patch: dict[str, Any] = {}
    source_timestamps: dict[str, Any] = {}
    for feature in DIRECTIONAL_FEATURES:
        existing = snapshot.get(feature)
        if existing is not None and not force:
            audit["skipped_existing"].append(feature)
            continue
        value = computed.get(feature)
        if value is None:
            audit["kept_null"].append(feature)
            continue
        patch[feature] = value
        source_timestamps[feature] = _jsonable(candle_cutoff)
        audit["filled"].append(feature)

    if patch:
        patch["_directional_backfill"] = {
            "feature_backfill_version": FORMULA_VERSION,
            "feature_schema_version": FEATURE_SCHEMA_VERSION,
            "backfilled_at": datetime.now(timezone.utc).isoformat(),
            "timeframe": timeframe,
            "exchange": exchange,
            "candle_cutoff": _jsonable(candle_cutoff),
            "source_timestamps": source_timestamps,
            "formulas": {
                "rsi_slope_3": "(rsi[t] - rsi[t-3]) / 3",
                "rsi_slope_5": "(rsi[t] - rsi[t-5]) / 5",
                "macd_hist_slope_3": "((macd_hist[t] - macd_hist[t-3]) / close[t]) * 100",
                "macd_hist_slope_5": "((macd_hist[t] - macd_hist[t-5]) / close[t]) * 100",
                "ema21_ema50_distance_pct": "((ema21 - ema50) / ema50) * 100",
                "di_plus_minus_diff": "di_plus - di_minus",
                "adx_slope_3": "(adx[t] - adx[t-3]) / 3",
                "vwap_reclaim_bool": "vwap_distance_pct[t-1] < 0 and vwap_distance_pct[t] >= 0",
                "higher_highs_5": "last 5 closed highs strictly ascending",
                "higher_lows_5": "last 5 closed lows strictly ascending",
            },
        }
    return patch, audit


def _closed_window_from_cache(
    candles_by_symbol: dict[str, pd.DataFrame],
    symbol: str,
    snapshot_time: datetime,
    timeframe: str,
    candle_limit: int,
) -> pd.DataFrame:
    minutes = int(timeframe.rstrip("m")) if timeframe.endswith("m") else 5
    candles = candles_by_symbol.get(symbol)
    if candles is None or candles.empty:
        return pd.DataFrame()
    cutoff = snapshot_time - pd.Timedelta(minutes=minutes)
    window = candles[candles["time"] <= cutoff].tail(candle_limit)
    return window.reset_index(drop=True)


def _update_batch(engine, updates: list[tuple[str, dict[str, Any]]]) -> None:
    with engine.begin() as conn:
        for trade_id, patch in updates:
            conn.execute(
                text(
                    """
                    UPDATE shadow_trades
                    SET features_snapshot = COALESCE(features_snapshot, '{}'::jsonb) || CAST(:patch AS jsonb),
                        updated_at = NOW()
                    WHERE id = :id
                    """
                ),
                {"id": trade_id, "patch": json.dumps(patch, default=_jsonable)},
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--source", default="L1_SPECTRUM")
    parser.add_argument("--timeframe", default="5m")
    parser.add_argument("--exchange", default="gate.io")
    parser.add_argument("--candle-limit", type=int, default=120)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force-recompute", action="store_true")
    args = parser.parse_args()

    engine = create_engine(_db_url(), pool_pre_ping=True)
    before = _coverage(engine, args.days, args.source)
    targets = _load_targets(engine, args.days, args.source, args.limit)
    candles_by_symbol = _load_candles_by_symbol(
        engine,
        targets,
        args.timeframe,
        args.exchange,
        args.candle_limit,
    )
    feature_engine = FeatureEngine({})
    updates: list[tuple[str, dict[str, Any]]] = []
    audits: list[dict[str, Any]] = []
    feature_fill_counts = {feature: 0 for feature in DIRECTIONAL_FEATURES}

    for row in targets:
        snapshot_time = row.get("entry_timestamp") or row.get("created_at")
        candles = _closed_window_from_cache(
            candles_by_symbol,
            row["symbol"],
            snapshot_time,
            args.timeframe,
            args.candle_limit,
        )
        patch, audit = _build_patch_from_candles(
            row,
            feature_engine,
            candles,
            timeframe=args.timeframe,
            exchange=args.exchange,
            force=args.force_recompute,
        )
        audits.append(audit)
        if audit["future_violation"]:
            continue
        if patch:
            updates.append((str(row["id"]), patch))
            for feature in DIRECTIONAL_FEATURES:
                if feature in patch:
                    feature_fill_counts[feature] += 1
        if args.apply and len(updates) >= args.batch_size:
            _update_batch(engine, updates)
            updates.clear()

    if args.apply and updates:
        _update_batch(engine, updates)
        updates.clear()

    after = _coverage(engine, args.days, args.source) if args.apply else None
    report = {
        "mode": "apply" if args.apply else "dry_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "args": vars(args),
        "coverage_before": before,
        "targets": len(targets),
        "rows_with_any_patch": sum(1 for audit in audits if audit["filled"]),
        "feature_fill_counts": feature_fill_counts,
        "future_violations": sum(1 for audit in audits if audit["future_violation"]),
        "coverage_after": after,
        "sample_audit": audits[:20],
    }
    out_dir = ROOT / ".codex_tmp"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / (
        "directional_backfill_apply_report.json"
        if args.apply
        else "directional_backfill_dry_run_report.json"
    )
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")
    print(json.dumps({"report": str(out_path), **report}, ensure_ascii=False, indent=2, default=_jsonable))


if __name__ == "__main__":
    main()

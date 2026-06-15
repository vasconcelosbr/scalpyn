"""
Backfill Script — Recompute features_snapshot for existing shadow_trades.

Problem: Sprint 2 changed RSI/ADX from SMA (Cutler's) to Wilder's EMA.
All existing shadow_trades have features_snapshot with OLD RSI/ADX values.
The ML model is re-trained on these old values but inference now uses
Wilder's EMA -> train-inference skew -> metrics degradation.

Solution: For each shadow_trade, fetch the OHLCV candles from around
the entry timestamp, re-run FeatureEngine with the corrected formulas,
and update features_snapshot in-place.

Runs in the same environment as ml_trainer/job.py (Cloud Run Job / Railway).

Usage:
    # Set DB_URL env var, then:
    python backfill_features.py

    # Dry run (no DB writes):
    python backfill_features.py --dry-run

    # Limit to recent shadows:
    python backfill_features.py --days 30

    # Process specific source:
    python backfill_features.py --source L1_SPECTRUM
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# ── Path setup (same as ml_trainer/job.py) ──────────────────────────────────
sys.path.insert(0, "/app")  # Cloud Run container
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "scalpyn", "backend"))

import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("backfill_features")

# ── Config ──────────────────────────────────────────────────────────────────
DB_URL = os.environ.get("DB_URL") or os.environ.get("DATABASE_URL", "")
if not DB_URL:
    logger.error("Neither DB_URL nor DATABASE_URL is set.")
    sys.exit(1)

# Normalise to sync psycopg2 URL
if DB_URL.startswith("postgresql+asyncpg://"):
    DB_URL = DB_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
elif DB_URL.startswith("postgres://"):
    DB_URL = DB_URL.replace("postgres://", "postgresql://", 1)

OHLCV_LOOKBACK_CANDLES = 200  # ~200 candles before entry for RSI(14)/ADX(14)
BATCH_SIZE = 50
OHLCV_TIMEFRAME = "5m"

# Keys affected by the SMA -> Wilder's EMA change (Sprint 2 P0-08)
# + Bollinger Bands (Sprint 4 P3-11 ddof=0)
# + VWAP (Sprint 4 P1-13 daily reset)
RECOMPUTED_KEYS = {
    "rsi", "rsi_7", "rsi_21", "rsi_50",           # RSI all periods
    "adx", "di_plus", "di_minus", "dx",            # ADX components
    "atr",                                          # ATR (also EMA now)
    "bb_upper", "bb_lower", "bb_width", "bb_middle",  # Bollinger ddof=0
    "vwap", "vwap_distance",                        # VWAP daily reset
}


def fetch_ohlcv(engine, symbol: str, before_ts, n_candles: int = OHLCV_LOOKBACK_CANDLES) -> pd.DataFrame:
    """Fetch OHLCV candles for a symbol before a given timestamp."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT time, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = :symbol
                  AND timeframe = :tf
                  AND time <= :before_ts
                ORDER BY time DESC
                LIMIT :n
            """),
            {"symbol": symbol, "tf": OHLCV_TIMEFRAME, "before_ts": before_ts, "n": n_candles},
        )
        rows = result.fetchall()

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(
        [{"time": r.time, "open": float(r.open), "high": float(r.high),
          "low": float(r.low), "close": float(r.close),
          "volume": float(r.volume)} for r in rows]
    )
    df.sort_values("time", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def recompute_indicators(df: pd.DataFrame, config: Dict[str, Any]) -> Dict[str, Any]:
    """Run FeatureEngine on OHLCV DataFrame and return flat indicators dict."""
    from app.services.feature_engine import FeatureEngine

    if df.empty or len(df) < 30:
        return {}

    engine = FeatureEngine(config)
    try:
        indicators = engine.calculate(df)
    except Exception as e:
        logger.warning("FeatureEngine.calculate failed: %s", e)
        return {}
    return indicators


def get_default_indicators_config(engine) -> Dict[str, Any]:
    """Get indicators config from existing profiles or use defaults."""
    try:
        with engine.connect() as conn:
            result = conn.execute(
                text("SELECT config FROM config_profiles WHERE config IS NOT NULL LIMIT 1")
            )
            row = result.fetchone()
            if row and row.config:
                cfg = row.config if isinstance(row.config, dict) else json.loads(row.config)
                ind_cfg = cfg.get("indicators", {})
                if ind_cfg:
                    logger.info("Loaded indicators config from profile: %s", list(ind_cfg.keys()))
                    return ind_cfg
    except Exception as e:
        logger.warning("Could not load indicators config: %s", e)

    # Fallback defaults
    return {
        "rsi": {"enabled": True, "period": 14},
        "macd": {"enabled": True, "fast": 12, "slow": 26, "signal": 9},
        "adx": {"enabled": True, "period": 14},
        "ema": {"enabled": True, "periods": [9, 21, 50]},
        "bb": {"enabled": True, "period": 20, "std_dev": 2},
        "atr": {"enabled": True, "period": 14},
        "stochastic": {"enabled": True, "period": 14},
        "vwap": {"enabled": True},
        "volume_sma": {"enabled": True, "period": 20},
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backfill features_snapshot with corrected RSI/ADX (Wilder's EMA)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Log changes without writing to DB")
    parser.add_argument("--days", type=int, default=30, help="Process shadows from last N days (default: 30)")
    parser.add_argument("--source", type=str, default=None, help="Filter by source (e.g., L1_SPECTRUM)")
    args = parser.parse_args()

    engine = create_engine(DB_URL, pool_size=5, max_overflow=5, connect_args={"connect_timeout": 10})

    logger.info("=" * 70)
    logger.info("BACKFILL FEATURES_SNAPSHOT — Wilder's EMA Migration")
    logger.info("=" * 70)
    logger.info("Dry run: %s | Days: %s | Source: %s", args.dry_run, args.days, args.source or "ALL")

    # Get indicators config
    indicators_config = get_default_indicators_config(engine)

    # Build WHERE clause
    where_parts = [
        "features_snapshot IS NOT NULL",
        "features_snapshot::text <> '{}'",
    ]
    params: Dict[str, Any] = {}
    if args.days:
        where_parts.append("created_at >= NOW() - INTERVAL :days")
        params["days"] = f"{args.days} days"
    if args.source:
        where_parts.append("source = :source")
        params["source"] = args.source

    where_clause = " AND ".join(where_parts)

    # Count
    with engine.connect() as conn:
        total_count = conn.execute(
            text(f"SELECT COUNT(*) FROM shadow_trades WHERE {where_clause}"), params
        ).scalar()

    logger.info("Total shadow_trades to process: %d", total_count)
    if total_count == 0:
        logger.info("Nothing to process. Exiting.")
        return

    # Stats
    stats = {"updated": 0, "skipped": 0, "errors": 0, "no_ohlcv": 0}
    t0 = time.time()
    offset = 0
    batch_num = 0

    while offset < total_count:
        batch_num += 1

        # Fetch batch
        with engine.connect() as conn:
            result = conn.execute(
                text(f"""
                    SELECT id, symbol, source, created_at, features_snapshot
                    FROM shadow_trades
                    WHERE {where_clause}
                    ORDER BY created_at ASC
                    LIMIT :limit OFFSET :offset
                """),
                {**params, "limit": BATCH_SIZE, "offset": offset},
            )
            rows = result.fetchall()

        if not rows:
            break

        # Process each shadow in this batch
        updates = []  # Collect (id, new_snapshot) for batch update

        for r in rows:
            try:
                shadow_id = r.id
                symbol = r.symbol
                entry_ts = r.created_at
                old_snapshot = r.features_snapshot
                if isinstance(old_snapshot, str):
                    old_snapshot = json.loads(old_snapshot)
                if not isinstance(old_snapshot, dict):
                    old_snapshot = {}

                # Fetch OHLCV
                df = fetch_ohlcv(engine, symbol, entry_ts)
                if df.empty or len(df) < 30:
                    stats["no_ohlcv"] += 1
                    continue

                # Recompute indicators
                new_indicators = recompute_indicators(df, indicators_config)
                if not new_indicators:
                    stats["skipped"] += 1
                    continue

                # Merge: only overwrite affected keys
                merged = dict(old_snapshot)
                changed = False
                diffs = {}
                for key in RECOMPUTED_KEYS:
                    if key in new_indicators:
                        old_val = old_snapshot.get(key)
                        new_val = new_indicators[key]
                        # Compare with tolerance for float rounding
                        if old_val is None and new_val is None:
                            continue
                        if old_val is not None and new_val is not None:
                            try:
                                if abs(float(old_val) - float(new_val)) < 1e-6:
                                    continue
                            except (TypeError, ValueError):
                                pass
                        merged[key] = new_val
                        changed = True
                        diffs[key] = {"old": old_val, "new": new_val}

                if not changed:
                    stats["skipped"] += 1
                    continue

                if args.dry_run:
                    logger.info(
                        "DRY RUN | shadow=%s sym=%s diffs=%s",
                        shadow_id, symbol,
                        {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv)
                             for kk, vv in v.items()} for k, v in list(diffs.items())[:3]},
                    )
                else:
                    updates.append((str(shadow_id), json.dumps(merged, default=str)))

                stats["updated"] += 1

            except Exception as e:
                logger.error("Error shadow=%s: %s", r.id, e, exc_info=True)
                stats["errors"] += 1

        # Batch write
        if updates and not args.dry_run:
            with engine.begin() as conn:
                for sid, snap_json in updates:
                    conn.execute(
                        text("UPDATE shadow_trades SET features_snapshot = CAST(:snap AS JSONB) WHERE id = :sid"),
                        {"sid": sid, "snap": snap_json},
                    )

        offset += BATCH_SIZE
        elapsed = time.time() - t0
        rate = offset / elapsed if elapsed > 0 else 0
        logger.info(
            "Batch %d | offset=%d/%d | updated=%d skipped=%d errs=%d no_ohlcv=%d | %.1f/s",
            batch_num, min(offset, total_count), total_count,
            stats["updated"], stats["skipped"], stats["errors"], stats["no_ohlcv"], rate,
        )

    elapsed = time.time() - t0
    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE in %.1fs", elapsed)
    logger.info("Updated: %d | Skipped: %d | No OHLCV: %d | Errors: %d",
                stats["updated"], stats["skipped"], stats["no_ohlcv"], stats["errors"])
    logger.info("=" * 70)

    if not args.dry_run and stats["updated"] > 0:
        logger.info("")
        logger.info(">>> Next step: re-train the model <<<")
        logger.info("    Cloud Run: trigger ml-trainer job")
        logger.info("    API: POST /api/ml/train")

    engine.dispose()


if __name__ == "__main__":
    main()

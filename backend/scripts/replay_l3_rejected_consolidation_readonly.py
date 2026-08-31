"""Dry-run the canonical rejected-profile ranking over the latest legacy event.

This probe performs SELECTs only.  It deliberately imports the same
``rank_candidates`` function used by approved L3 consolidation so the ordering
comparison is executable evidence rather than a second implementation.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timezone
from typing import Any

from sqlalchemy import text

from app.database import CeleryAsyncSessionLocal
from app.services.l3_rejected_trade_consolidation import RejectedL3Candidate
from app.services.l3_trade_consolidation import rank_candidates


def _number(snapshot: Any, key: str) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    value = snapshot.get(key)
    if isinstance(value, dict):
        value = value.get("value")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _thresholds(profile_config: Any, scanner: Any) -> tuple[float, float]:
    configured = (
        ((profile_config or {}).get("scoring") or {}).get("thresholds") or {}
        if isinstance(profile_config, dict)
        else {}
    )
    scanner = scanner if isinstance(scanner, dict) else {}
    buy = configured.get("buy", configured.get("buy_threshold"))
    strong = configured.get("strong_buy", configured.get("strong_buy_threshold"))
    return (
        float(buy if buy is not None else scanner.get("buy_threshold_score")),
        float(strong if strong is not None else scanner.get("strong_buy_threshold")),
    )


async def main() -> None:
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text("SET statement_timeout = '20s'"))
        event = (
            await db.execute(
                text(
                    """
                    SELECT user_id,
                           symbol,
                           direction,
                           date_bin(
                               INTERVAL '5 minutes', created_at,
                               TIMESTAMPTZ '2000-01-01 00:00:00+00'
                           ) AS candle_open,
                           COUNT(*)::int AS candidate_rows,
                           COUNT(DISTINCT profile_id)::int AS candidate_profiles
                      FROM shadow_trades
                     WHERE source = 'L3_REJECTED'
                       AND profile_id IS NOT NULL
                       AND created_at >= NOW() - INTERVAL '24 hours'
                     GROUP BY user_id, symbol, direction, candle_open
                    HAVING COUNT(DISTINCT profile_id) > 1
                     ORDER BY candle_open DESC, candidate_profiles DESC, symbol
                     LIMIT 1
                    """
                )
            )
        ).mappings().first()
        if event is None:
            print(json.dumps({"status": "NO_RECENT_MULTI_PROFILE_EVENT"}))
            return

        scanner = (
            await db.execute(
                text(
                    """
                    SELECT config_json -> 'scanner'
                      FROM config_profiles
                     WHERE user_id = :user_id
                       AND config_type = 'spot_engine'
                       AND is_active IS TRUE
                     ORDER BY updated_at DESC
                     LIMIT 1
                    """
                ),
                {"user_id": event["user_id"]},
            )
        ).scalar_one()
        rows = (
            await db.execute(
                text(
                    """
                    SELECT st.id::text AS shadow_trade_id,
                           st.profile_id,
                           st.profile_name,
                           st.profile_version,
                           st.config_snapshot,
                           st.features_snapshot,
                           st.watchlist_id::text AS watchlist_id,
                           st.watchlist_name,
                           p.config AS profile_config,
                           pv.id AS profile_version_id
                      FROM shadow_trades AS st
                      JOIN profiles AS p ON p.id = st.profile_id
                      LEFT JOIN profile_versions AS pv
                        ON pv.profile_id = st.profile_id
                       AND pv.status = 'CHAMPION'
                     WHERE st.user_id = :user_id
                       AND st.source = 'L3_REJECTED'
                       AND st.symbol = :symbol
                       AND st.direction IS NOT DISTINCT FROM :direction
                       AND st.created_at >= :candle_open
                       AND st.created_at < :candle_open + INTERVAL '5 minutes'
                     ORDER BY st.created_at, st.id
                    """
                ),
                {
                    "user_id": event["user_id"],
                    "symbol": event["symbol"],
                    "direction": event["direction"],
                    "candle_open": event["candle_open"],
                },
            )
        ).mappings().all()

    candidates: list[RejectedL3Candidate] = []
    source_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        buy, strong = _thresholds(row["profile_config"], scanner)
        config_snapshot = row["config_snapshot"] or {}
        features = row["features_snapshot"] or {}
        score = config_snapshot.get("l3_score")
        candidate = RejectedL3Candidate(
            user_id=event["user_id"],
            symbol=event["symbol"],
            direction=event["direction"] or "SPOT",
            timeframe="5m",
            candle_open_timestamp=event["candle_open"].astimezone(timezone.utc),
            observed_at=event["candle_open"].astimezone(timezone.utc),
            profile_id=row["profile_id"],
            profile_name=row["profile_name"],
            profile_version=row["profile_version"],
            profile_version_id=row["profile_version_id"],
            decision_score=float(score or 0),
            buy_threshold=buy,
            strong_buy_threshold=strong,
            decision={
                "reasons": config_snapshot.get("l3_reasons") or [],
                "metrics": {},
            },
            market_structure_score=_number(features, "market_structure_score"),
            momentum_score=_number(features, "momentum_score"),
            liquidity_score=_number(features, "liquidity_score"),
            signal_score=_number(features, "signal_score"),
            watchlist_id=row["watchlist_id"],
            watchlist_name=row["watchlist_name"],
        )
        candidates.append(candidate)
        source_rows[str(row["profile_id"])] = dict(row)

    ranked = rank_candidates(candidates)
    output = {
        "status": "DRY_RUN",
        "writes": 0,
        "event": {
            "symbol": event["symbol"],
            "direction": event["direction"],
            "candle_open": event["candle_open"],
            "candidate_rows": event["candidate_rows"],
            "candidate_profiles": event["candidate_profiles"],
        },
        "approved_ranking_function_reused": True,
        "ranking_function": (
            "app.services.l3_trade_consolidation.rank_candidates"
        ),
        "winner": {
            "profile_id": str(ranked[0].profile_id),
            "profile_name": ranked[0].profile_name,
            "shadow_trade_id": source_rows[str(ranked[0].profile_id)][
                "shadow_trade_id"
            ],
        },
        "ranking": [
            {
                "rank": rank,
                "profile_id": str(candidate.profile_id),
                "profile_name": candidate.profile_name,
                "normalized_score_margin": candidate.normalized_score_margin,
                "decision_score": candidate.decision_score,
                "market_structure_score": candidate.market_structure_score,
                "momentum_score": candidate.momentum_score,
                "liquidity_score": candidate.liquidity_score,
                "signal_score": candidate.signal_score,
            }
            for rank, candidate in enumerate(ranked, start=1)
        ],
    }
    print(json.dumps(output, default=str, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())

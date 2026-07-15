"""Contextual EV materialization by Profile Version and crypto asset.

This is descriptive intelligence, not a model that predicts whether every
trade will win. It uses only resolved shadow outcomes and immutable lineage.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .config_service import config_service
from .crypto_ev_config import default_crypto_ev_config


def normalized_score(ev: float, score_config: dict[str, Any]) -> float:
    low = float(score_config["ev_at_score_0"])
    high = float(score_config["ev_at_score_100"])
    if high <= low:
        raise ValueError("invalid_ev_score_normalization")
    return max(0.0, min(100.0, (ev - low) * 100.0 / (high - low)))


class EVScoreV2Service:
    async def refresh(
        self,
        db: AsyncSession,
        *,
        user_id: UUID,
        window_from: Any,
        window_to: Any,
    ) -> dict[str, Any]:
        if window_from >= window_to:
            raise ValueError("invalid_window")
        ml_config = await config_service.get_config(db, "ml", user_id)
        if ml_config.get("ml_fee_roundtrip_pct") is None:
            raise ValueError("missing_ml_fee_roundtrip_pct")
        fee_pct = float(ml_config["ml_fee_roundtrip_pct"])
        crypto_config = {
            **default_crypto_ev_config(),
            **(await config_service.get_config(db, "crypto_ev", user_id) or {}),
        }
        min_n = int(crypto_config["min_trades_for_state"])
        shrinkage_k = float(crypto_config["shrinkage_k"])
        score_config = crypto_config["score_normalization"]

        rows = (await db.execute(text("""
            WITH base AS (
                SELECT st.profile_id, st.profile_version_id,
                       COALESCE(st.timeframe, 'UNSPECIFIED') AS timeframe,
                       st.symbol,
                       COALESCE(st.net_return_pct, st.pnl_pct - :fee_pct) AS net_ev,
                       COALESCE(st.snapshot_id, st.event_id, st.id) AS independent_id,
                       COALESCE(st.label_resolved_at, st.completed_at, st.exit_timestamp) AS resolved_at,
                       st.max_drawdown_pct
                  FROM shadow_trades st
                  JOIN profiles p ON p.id = st.profile_id
                 WHERE p.user_id = :user_id
                   AND st.status = 'COMPLETED'
                   AND st.pnl_pct IS NOT NULL
                   AND st.profile_id IS NOT NULL
                   AND st.profile_version_id IS NOT NULL
                   AND COALESCE(st.label_resolved_at, st.completed_at, st.exit_timestamp) >= :window_from
                   AND COALESCE(st.label_resolved_at, st.completed_at, st.exit_timestamp) < :window_to
            ), daily AS (
                SELECT profile_id, profile_version_id, timeframe, symbol,
                       date_trunc('day', resolved_at) AS day, avg(net_ev) AS day_ev
                  FROM base GROUP BY 1,2,3,4,5
            ), stability AS (
                SELECT profile_id, profile_version_id, timeframe, symbol,
                       avg(CASE WHEN day_ev > 0 THEN 1.0 ELSE 0.0 END) AS stability
                  FROM daily GROUP BY 1,2,3,4
            )
            SELECT b.profile_id, b.profile_version_id, b.timeframe, b.symbol,
                   count(*)::int AS raw_n,
                   count(DISTINCT b.independent_id)::int AS effective_n,
                   avg(b.net_ev) AS net_ev,
                   stddev_samp(b.net_ev) AS stddev,
                   avg(CASE WHEN b.net_ev > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
                   avg(abs(b.max_drawdown_pct)) FILTER (WHERE b.max_drawdown_pct IS NOT NULL) AS drawdown,
                   s.stability
              FROM base b
              JOIN stability s USING (profile_id, profile_version_id, timeframe, symbol)
             GROUP BY b.profile_id, b.profile_version_id, b.timeframe, b.symbol, s.stability
        """), {
            "user_id": str(user_id), "fee_pct": fee_pct,
            "window_from": window_from, "window_to": window_to,
        })).mappings().all()

        by_profile: dict[tuple[Any, Any, str], list[dict[str, Any]]] = {}
        crypto_written = 0
        for row in rows:
            item = dict(row)
            n = int(item["effective_n"])
            ev = float(item["net_ev"])
            stddev = float(item["stddev"] or 0.0)
            margin = 1.96 * stddev / (n ** 0.5) if n > 1 else None
            confidence = n / (n + shrinkage_k)
            expected_ev = ev * confidence
            score = normalized_score(expected_ev, score_config)
            status = "OBSERVED" if n >= min_n else "INSUFFICIENT_DATA"
            audit = {
                "formula": "expected_ev=mean(net_return)*n/(n+shrinkage_k)",
                "fee_roundtrip_pct": fee_pct,
                "min_trades_for_state": min_n,
                "shrinkage_k": shrinkage_k,
                "ci95_available": margin is not None,
                "lineage_filter": "profile_id and profile_version_id required",
            }
            await db.execute(text("""
                INSERT INTO crypto_profile_ev_scores (
                    id, profile_id, profile_version_id, symbol, timeframe,
                    window_from, window_to, raw_n, effective_n, expected_ev,
                    realized_ev, confidence, score, status, audit_json
                ) VALUES (
                    :id, :profile_id, :profile_version_id, :symbol, :timeframe,
                    :window_from, :window_to, :raw_n, :effective_n, :expected_ev,
                    :realized_ev, :confidence, :score, :status, CAST(:audit AS JSONB)
                ) ON CONFLICT (profile_version_id, symbol, timeframe, window_from, window_to)
                DO UPDATE SET raw_n=EXCLUDED.raw_n, effective_n=EXCLUDED.effective_n,
                    expected_ev=EXCLUDED.expected_ev, realized_ev=EXCLUDED.realized_ev,
                    confidence=EXCLUDED.confidence, score=EXCLUDED.score,
                    status=EXCLUDED.status, audit_json=EXCLUDED.audit_json, computed_at=now()
            """), {
                "id": str(uuid4()), "profile_id": str(item["profile_id"]),
                "profile_version_id": str(item["profile_version_id"]),
                "symbol": item["symbol"], "timeframe": item["timeframe"],
                "window_from": window_from, "window_to": window_to,
                "raw_n": int(item["raw_n"]), "effective_n": n,
                "expected_ev": expected_ev, "realized_ev": ev,
                "confidence": confidence, "score": score, "status": status,
                "audit": json.dumps(audit),
            })
            crypto_written += 1
            by_profile.setdefault(
                (item["profile_id"], item["profile_version_id"], item["timeframe"]), []
            ).append({**item, "n": n})

        profile_written = 0
        for (profile_id, version_id, timeframe), group in by_profile.items():
            raw_n = sum(int(item["raw_n"]) for item in group)
            effective_n = sum(item["n"] for item in group)
            ev = sum(float(item["net_ev"]) * item["n"] for item in group) / effective_n
            win_rate = sum(float(item["win_rate"]) * item["n"] for item in group) / effective_n
            stability = sum(float(item["stability"]) * item["n"] for item in group) / effective_n
            drawdowns = [float(item["drawdown"]) for item in group if item["drawdown"] is not None]
            drawdown = sum(drawdowns) / len(drawdowns) if drawdowns else None
            # CI is computed from symbol-level sufficient statistics; this is
            # explicitly recorded so it is not confused with a pooled raw CI.
            variances = [float(item["stddev"] or 0.0) ** 2 for item in group]
            pooled_se = (sum(variances) / len(variances) / effective_n) ** 0.5
            margin = 1.96 * pooled_se if effective_n > 1 else None
            status = "OBSERVED" if effective_n >= min_n else "INSUFFICIENT_DATA"
            audit = {
                "formula": "profile_ev=effective_n_weighted_mean(symbol_ev)",
                "ci_method": "symbol_level_pooled_variance",
                "fee_roundtrip_pct": fee_pct,
                "min_trades_for_state": min_n,
                "symbols": sorted(item["symbol"] for item in group),
            }
            await db.execute(text("""
                INSERT INTO profile_version_ev_scores (
                    id, profile_id, profile_version_id, timeframe, window_from,
                    window_to, raw_n, effective_n, net_ev, ci95_lower, ci95_upper,
                    win_rate, drawdown, stability, score, status, audit_json
                ) VALUES (
                    :id, :profile_id, :profile_version_id, :timeframe, :window_from,
                    :window_to, :raw_n, :effective_n, :net_ev, :ci95_lower, :ci95_upper,
                    :win_rate, :drawdown, :stability, :score, :status, CAST(:audit AS JSONB)
                ) ON CONFLICT (profile_version_id, timeframe, window_from, window_to)
                DO UPDATE SET raw_n=EXCLUDED.raw_n, effective_n=EXCLUDED.effective_n,
                    net_ev=EXCLUDED.net_ev, ci95_lower=EXCLUDED.ci95_lower,
                    ci95_upper=EXCLUDED.ci95_upper, win_rate=EXCLUDED.win_rate,
                    drawdown=EXCLUDED.drawdown, stability=EXCLUDED.stability,
                    score=EXCLUDED.score, status=EXCLUDED.status,
                    audit_json=EXCLUDED.audit_json, computed_at=now()
            """), {
                "id": str(uuid4()), "profile_id": str(profile_id),
                "profile_version_id": str(version_id), "timeframe": timeframe,
                "window_from": window_from, "window_to": window_to,
                "raw_n": raw_n, "effective_n": effective_n, "net_ev": ev,
                "ci95_lower": ev - margin if margin is not None else None,
                "ci95_upper": ev + margin if margin is not None else None,
                "win_rate": win_rate, "drawdown": drawdown, "stability": stability,
                "score": normalized_score(ev, score_config), "status": status,
                "audit": json.dumps(audit),
            })
            profile_written += 1
        return {
            "profile_version_scores_written": profile_written,
            "contextual_crypto_scores_written": crypto_written,
            "source_rows": sum(int(dict(row)["raw_n"]) for row in rows),
            "window_from": window_from.isoformat(),
            "window_to": window_to.isoformat(),
        }


ev_score_v2_service = EVScoreV2Service()

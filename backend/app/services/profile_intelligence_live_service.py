"""Profile Intelligence Live Engine — core analysis service.

Implements 3-frequency loop:
  fast  (5 min) : heartbeat + shadow scan + activity log
  medium (30 min): indicator mining + hard negative mining + suggestions
  ai    (4 h)   : AI Critic review

All mutations are disabled by default. No profile creation. No live trading.
"""

from __future__ import annotations

import decimal
import hashlib
import inspect
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


class _SafeEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal and other non-standard types."""
    def default(self, o: Any) -> Any:
        if isinstance(o, decimal.Decimal):
            return float(o)
        return super().default(o)

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .ai_review_safety_service import completed_review_contract_is_valid
from .profile_intelligence_contract import (
    DATASET_VERSION,
    LABEL_VERSION,
    OFFICIAL_CAPTURE_COLUMNS,
    filter_hash_valid_rows,
    load_pi_settings,
    official_params,
    official_where,
)

logger = logging.getLogger(__name__)

_AI_REVIEW_INTERVAL_H = int(os.environ.get("PI_AI_REVIEW_INTERVAL_H", "4"))
_MEDIUM_INTERVAL_M = int(os.environ.get("PI_MEDIUM_INTERVAL_M", "30"))
_LOOKBACK_HOURS = int(os.environ.get("PI_LIVE_LOOKBACK_H", "24"))

_FORBIDDEN_SUGGESTION_TYPES = frozenset({
    "CREATE_PROFILE", "DUPLICATE_PROFILE", "PROMOTE_LIVE", "ENABLE_LIVE"
})
_FORBIDDEN_ACTION_TYPES = _FORBIDDEN_SUGGESTION_TYPES

_INDICATOR_NAMES = [
    "rsi", "adx", "macd_histogram_pct", "macd_histogram_slope",
    "atr_pct", "spread_pct", "volume_spike", "bb_width",
    "ema9_gt_ema21", "ema50_gt_ema200", "orderbook_depth_usdt",
    "vwap_distance_pct", "taker_ratio", "volume_delta", "flow_strength",
]


async def _log_activity(
    db: AsyncSession,
    *,
    run_id: uuid.UUID | None,
    event_type: str,
    phase: str,
    message: str,
    severity: str = "info",
    profile_id: uuid.UUID | None = None,
    profile_name: str | None = None,
    payload: dict | None = None,
) -> None:
    await db.execute(text("""
        INSERT INTO profile_intelligence_activity_log
            (id, run_id, event_type, phase, severity, message, profile_id, profile_name, payload, created_at)
        VALUES
            (:id, :run_id, :event_type, :phase, :severity, :message, :profile_id, :profile_name, CAST(:payload AS jsonb), now())
    """), {
        "id": str(uuid.uuid4()),
        "run_id": str(run_id) if run_id else None,
        "event_type": event_type,
        "phase": phase,
        "severity": severity,
        "message": message,
        "profile_id": str(profile_id) if profile_id else None,
        "profile_name": profile_name,
        "payload": json.dumps(payload or {}, cls=_SafeEncoder),
    })


async def record_heartbeat(
    db: AsyncSession,
    *,
    run_id: uuid.UUID | None = None,
    engine_status: str = "RUNNING",
    current_phase: str = "IDLE",
    worker_name: str | None = None,
    next_cycle_at: datetime | None = None,
    metadata: dict | None = None,
) -> None:
    commit_hash = os.environ.get("RAILWAY_GIT_COMMIT_SHA", "")[:12] or None
    if worker_name is None:
        worker_name = os.environ.get("RAILWAY_SERVICE_NAME", "scalpyn-worker-structural")

    await db.execute(text("""
        INSERT INTO profile_intelligence_heartbeats
            (id, run_id, engine_status, current_phase, heartbeat_at, next_cycle_at, worker_name, commit_hash, metadata, created_at)
        VALUES
            (:id, :run_id, :engine_status, :current_phase, now(), :next_cycle_at, :worker_name, :commit_hash, CAST(:metadata AS jsonb), now())
    """), {
        "id": str(uuid.uuid4()),
        "run_id": str(run_id) if run_id else None,
        "engine_status": engine_status,
        "current_phase": current_phase,
        "next_cycle_at": next_cycle_at,
        "worker_name": worker_name,
        "commit_hash": commit_hash,
        "metadata": json.dumps(metadata or {}),
    })
    await db.commit()

    await _log_activity(
        db,
        run_id=run_id,
        event_type="HEARTBEAT",
        phase=current_phase,
        message=f"Heartbeat: {engine_status} / {current_phase}",
        payload={"worker": worker_name},
    )
    await db.commit()


async def _needs_medium_cycle(db: AsyncSession) -> bool:
    """Return True if the last medium-cycle run is older than PI_MEDIUM_INTERVAL_M."""
    row = await db.execute(text("""
        SELECT created_at
        FROM profile_intelligence_activity_log
        WHERE event_type = 'RUN_COMPLETED' AND phase = 'medium'
        ORDER BY created_at DESC
        LIMIT 1
    """))
    last = row.scalar_one_or_none()
    if last is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=_MEDIUM_INTERVAL_M)
    return last < cutoff


async def _needs_ai_cycle(db: AsyncSession) -> bool:
    """Return True if no real (tokens > 0) AI review exists within the interval."""
    # If a review is in progress, don't spawn another
    pending = await db.execute(text("""
        SELECT COUNT(*) FROM profile_ai_reviews
        WHERE status IN ('SCHEDULED', 'RUNNING')
    """))
    if (pending.scalar() or 0) > 0:
        return False
    # Only count COMPLETED reviews with real tokens — hollow COMPLETED don't count
    row = await db.execute(text("""
        SELECT completed_at
        FROM profile_ai_reviews
        WHERE status = 'COMPLETED' AND COALESCE(tokens_input, 0) > 0
        ORDER BY completed_at DESC
        LIMIT 1
    """))
    last = row.scalar_one_or_none()
    if last is None:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(hours=_AI_REVIEW_INTERVAL_H)
    return last < cutoff


async def _check_win_rate_drift(db: AsyncSession) -> dict:
    """Circuit breaker: compare 7d fast_win_rate vs 8-60d reference per source.

    Uses ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M') — same label as
    is_tp_4h_v1 used to train v52 — so drift is measured on the correct metric.
    outcome='TP_HIT' (~45%) diverges from is_tp_4h_v1 (~20%) by ~25pp and
    would produce misleading drift signals.

    Emits CRITICAL log when |drift| > 15pp — Etapa 2 of ML correction plan.
    Minimum 50 trades (with ttt_analysis_done) in the 7d window to avoid noise.
    """
    rows = await db.execute(text("""
        SELECT
            source,
            ROUND(
                SUM(CASE WHEN completed_at >= NOW() - INTERVAL '7 days'
                         AND ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M')
                         AND ttt_analysis_done = TRUE THEN 1 ELSE 0 END)::numeric /
                NULLIF(SUM(CASE WHEN completed_at >= NOW() - INTERVAL '7 days'
                               AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                               AND ttt_analysis_done = TRUE THEN 1 ELSE 0 END), 0) * 100,
            2) AS win_rate_7d,
            ROUND(
                SUM(CASE WHEN completed_at BETWEEN NOW() - INTERVAL '60 days' AND NOW() - INTERVAL '7 days'
                         AND ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M')
                         AND ttt_analysis_done = TRUE THEN 1 ELSE 0 END)::numeric /
                NULLIF(SUM(CASE WHEN completed_at BETWEEN NOW() - INTERVAL '60 days' AND NOW() - INTERVAL '7 days'
                               AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                               AND ttt_analysis_done = TRUE THEN 1 ELSE 0 END), 0) * 100,
            2) AS win_rate_ref,
            SUM(CASE WHEN completed_at >= NOW() - INTERVAL '7 days'
                     AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
                     AND ttt_analysis_done = TRUE THEN 1 ELSE 0 END) AS n_7d
        FROM shadow_trades
        WHERE completed_at IS NOT NULL
          AND source IN ('L1_SPECTRUM','L3','L3_LAB')
        GROUP BY source
    """))
    alerts = []
    for r in rows.fetchall():
        source, wr7d, wr_ref, n7d = r
        if wr7d is None or wr_ref is None or (n7d or 0) < 50:
            continue
        drift = float(wr7d) - float(wr_ref)
        if abs(drift) > 15:
            alerts.append({"source": source, "win_rate_7d": float(wr7d),
                           "win_rate_ref": float(wr_ref), "drift_pp": round(drift, 2)})
            logger.critical(
                "[WinRateDrift] CIRCUIT_BREAKER source=%s win_rate_7d=%.1f%% ref=%.1f%% drift=%+.1fpp — "
                "retrain or investigate before activating ML_GATE",
                source, wr7d, wr_ref, drift,
            )
    if not alerts:
        logger.info("[WinRateDrift] OK — no source drifted > 15pp (7d vs 8-60d reference)")
    return {"alerts": alerts}


async def run_fast_cycle(db: AsyncSession) -> dict:
    """Fast loop: heartbeat + shadow scan summary + win_rate drift circuit breaker."""
    run_id = uuid.uuid4()
    next_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    await record_heartbeat(
        db,
        run_id=run_id,
        engine_status="RUNNING",
        current_phase="SCANNING_SHADOW",
        next_cycle_at=next_at,
    )

    await _log_activity(db, run_id=run_id, event_type="SCANNING_SHADOW",
                        phase="fast", message="Analisando shadow trades L3 finalizados")

    row = await db.execute(text("""
        SELECT
            COUNT(*) AS completed_trades,
            COUNT(DISTINCT profile_id) AS profiles,
            ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl_pct,
            ROUND(
                COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0), 4
            ) AS win_rate
        FROM shadow_trades
        WHERE source IN ('L3','L3_LAB')
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND profile_id IS NOT NULL
          AND created_at >= now() - interval '24 hours'
    """))
    stats = dict(zip(["completed_trades", "profiles", "avg_pnl_pct", "win_rate"],
                     row.fetchone()))

    await _log_activity(db, run_id=run_id, event_type="ANALYZING_PROFILES",
                        phase="fast",
                        message=f"Shadow scan: {stats['completed_trades']} trades, {stats['profiles']} profiles",
                        payload=stats)

    drift_result = await _check_win_rate_drift(db)

    await _log_activity(db, run_id=run_id, event_type="RUN_COMPLETED",
                        phase="fast", message="Ciclo rápido concluído")
    await db.commit()

    await record_heartbeat(
        db, run_id=run_id, engine_status="IDLE", current_phase="IDLE", next_cycle_at=next_at,
    )
    return {"run_id": str(run_id), "cycle": "fast", **stats, "win_rate_drift": drift_result}


async def run_medium_cycle(db: AsyncSession) -> dict:
    """Medium loop: indicator mining + hard negative mining + suggestions."""
    run_id = uuid.uuid4()

    await record_heartbeat(
        db, run_id=run_id, engine_status="RUNNING", current_phase="MINING_INDICATORS",
    )
    await _log_activity(db, run_id=run_id, event_type="MINING_INDICATORS",
                        phase="medium", message="Iniciando mineração de indicadores por profile")

    rows = await db.execute(text(f"""
        SELECT
            st.user_id,
            st.profile_id,
            st.source,
            p.name AS profile_name,
            st.features_snapshot,
            st.pnl_pct,
            st.outcome,
            {OFFICIAL_CAPTURE_COLUMNS.format(alias='st')}
        FROM shadow_trades st
        JOIN profiles p ON p.id = st.profile_id
        WHERE st.source IN ('L3','L3_LAB')
          AND st.status = 'COMPLETED'
          AND st.pnl_pct IS NOT NULL
          AND st.profile_id IS NOT NULL
          AND st.created_at >= now() - interval '{_LOOKBACK_HOURS} hours'
          AND {official_where('st')}
        ORDER BY st.created_at, st.id
        LIMIT 5000
    """), official_params())
    trades, invalid_count = filter_hash_valid_rows(rows.fetchall())
    if invalid_count:
        logger.error("[PI Live] excluded %d invalid official rows", invalid_count)

    settings_by_user: dict[str, dict[str, Any]] = {}
    for user_id in {str(t.user_id) for t in trades}:
        settings_by_user[user_id] = await load_pi_settings(db, uuid.UUID(user_id))

    suggestions_generated = 0
    profiles_seen: dict[str, str] = {}
    profile_users: dict[str, str] = {}
    indicator_stats: dict[tuple, list[tuple[str, float]]] = {}

    for t in trades:
        pid = str(t.profile_id)
        profiles_seen[pid] = t.profile_name or pid
        profile_users[pid] = str(t.user_id)
        snap = t.features_snapshot or {}
        pnl = float(t.pnl_pct)
        for ind in _INDICATOR_NAMES:
            val = snap.get(ind)
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            bucket = _bucket(ind, fval)
            key = (str(t.user_id), pid, ind, bucket)
            indicator_stats.setdefault(key, []).append((str(t.outcome), pnl))

    for (user_id, pid, ind, bucket), observations in indicator_stats.items():
        policy = settings_by_user[user_id]
        min_cases = int(policy["min_closed_trades"])
        if len(observations) < min_cases:
            continue
        pnls = [pnl for _, pnl in observations]
        wins = sum(1 for outcome, _ in observations if outcome == "TP_HIT")
        losses = sum(1 for outcome, _ in observations if outcome == "SL_HIT")
        avg_pnl = sum(pnls) / len(pnls)
        win_rate = wins / len(pnls) if pnls else None
        profile_observations = [t for t in trades if str(t.profile_id) == pid]
        profile_win_rate = (
            sum(1 for t in profile_observations if t.outcome == "TP_HIT")
            / max(len(profile_observations), 1)
        )
        await db.execute(text("""
            INSERT INTO profile_indicator_performance
                (id, run_id, profile_id, profile_name, indicator_name, bucket,
                 sample_count, win_count, loss_count, win_rate, avg_pnl_pct, ev_pct,
                 lift_vs_profile, metadata, created_at)
            VALUES
                (:id, :run_id, :profile_id, :profile_name, :indicator_name, :bucket,
                 :sample_count, :win_count, :loss_count, :win_rate, :avg_pnl_pct, :ev_pct,
                 :lift_vs_profile, CAST(:metadata AS jsonb), now())
        """), {
            "id": str(uuid.uuid4()),
            "run_id": str(run_id),
            "profile_id": pid,
            "profile_name": profiles_seen[pid],
            "indicator_name": ind,
            "bucket": bucket,
            "sample_count": len(pnls),
            "win_count": wins,
            "loss_count": losses,
            "win_rate": float(round(win_rate, 4)) if win_rate is not None else None,
            "avg_pnl_pct": float(round(avg_pnl, 6)),
            "ev_pct": float(round(avg_pnl, 6)),
            "lift_vs_profile": float(round((win_rate or 0) - profile_win_rate, 4)),
            "metadata": json.dumps({
                "dataset_version": DATASET_VERSION,
                "label_version": LABEL_VERSION,
                "source_profile_id": pid,
                "source_user_id": user_id,
                "official_capture_only": True,
            }),
        })

    await _log_activity(db, run_id=run_id, event_type="MINING_HARD_NEGATIVES",
                        phase="medium", message="Minerando padrões de hard negative")

    hard_neg_rows = await db.execute(text(f"""
        SELECT
            st.user_id,
            st.profile_id,
            st.source,
            p.name AS profile_name,
            st.features_snapshot,
            st.pnl_pct,
            st.outcome,
            {OFFICIAL_CAPTURE_COLUMNS.format(alias='st')}
        FROM shadow_trades st
        JOIN profiles p ON p.id = st.profile_id
        WHERE st.source IN ('L3','L3_LAB')
          AND st.status = 'COMPLETED'
          AND st.profile_id IS NOT NULL
          AND st.outcome = 'SL_HIT'
          AND st.created_at >= now() - interval '{_LOOKBACK_HOURS} hours'
          AND {official_where('st')}
        ORDER BY st.created_at, st.id
        LIMIT 2000
    """), official_params())
    hard_negs, hard_neg_invalid = filter_hash_valid_rows(hard_neg_rows.fetchall())
    if hard_neg_invalid:
        logger.error("[PI Live] excluded %d invalid hard-negative rows", hard_neg_invalid)

    pattern_buckets: dict[tuple, list[float]] = {}
    for t in hard_negs:
        pid = str(t.profile_id)
        snap = t.features_snapshot or {}
        pnl = float(t.pnl_pct)
        rsi_b = _bucket("rsi", snap.get("rsi", 50))
        adx_b = _bucket("adx", snap.get("adx", 20))
        key = (str(t.user_id), pid, t.profile_name or pid, f"rsi={rsi_b},adx={adx_b}")
        pattern_buckets.setdefault(key, []).append(pnl)

    for (user_id, pid, pname, pat_key), pnls in pattern_buckets.items():
        if len(pnls) < int(settings_by_user[user_id]["min_closed_trades"]):
            continue
        await db.execute(text("""
            INSERT INTO profile_hard_negative_patterns
                (id, run_id, profile_id, profile_name, pattern_key,
                 pattern_payload, sample_count, loss_count, fp_rate, avg_loss_pct, status, created_at)
            VALUES
                (:id, :run_id, :profile_id, :profile_name, :pattern_key,
                 CAST(:payload AS jsonb), :sample_count, :loss_count, :fp_rate, :avg_loss_pct, 'OBSERVED', now())
        """), {
            "id": str(uuid.uuid4()),
            "run_id": str(run_id),
            "profile_id": pid,
            "profile_name": pname,
            "pattern_key": pat_key,
            "payload": json.dumps({"pattern": pat_key, "count": len(pnls)}),
            "sample_count": len(pnls),
            "loss_count": len(pnls),
            "fp_rate": float(round(len(pnls) / max(1, len(pnls)), 4)),
            "avg_loss_pct": float(round(sum(pnls) / len(pnls), 6)),
        })

    await _log_activity(db, run_id=run_id, event_type="GENERATING_ADJUSTMENT_SUGGESTIONS",
                        phase="medium", message="Gerando sugestões de ajuste para profiles existentes")

    losing_indicators_by_profile: dict[str, list[dict[str, Any]]] = {}
    for (user_id, pid, indicator, bucket), observations in indicator_stats.items():
        policy = settings_by_user[user_id]
        if len(observations) < int(policy["min_closed_trades"]):
            continue
        profile_observations = [t for t in trades if str(t.profile_id) == pid]
        profile_win_rate = (
            sum(1 for t in profile_observations if t.outcome == "TP_HIT")
            / max(len(profile_observations), 1)
        )
        bucket_win_rate = (
            sum(1 for outcome, _ in observations if outcome == "TP_HIT")
            / len(observations)
        )
        if bucket_win_rate >= profile_win_rate * float(policy["indicator_losing_winrate_ratio"]):
            continue
        losing_indicators_by_profile.setdefault(pid, []).append({
            "indicator": indicator,
            "bucket": bucket,
            "sample_count": len(observations),
            "win_rate": round(bucket_win_rate, 4),
            "loss_rate": round(
                sum(1 for outcome, _ in observations if outcome == "SL_HIT")
                / len(observations),
                4,
            ),
            "lift_vs_profile": round(bucket_win_rate - profile_win_rate, 4),
        })

    for pid, pname in profiles_seen.items():
        profile_trades = [t for t in trades if str(t.profile_id) == pid]
        policy = settings_by_user[profile_users[pid]]
        if len(profile_trades) < int(policy["adjustment_min_profile_trades"]):
            continue
        total_pnls = [float(t.pnl_pct) for t in profile_trades]
        win_rate = (
            sum(1 for t in profile_trades if t.outcome == "TP_HIT")
            / len(profile_trades)
        )
        if win_rate < float(policy["adjustment_max_win_rate"]):
            losing_evidence = sorted(
                losing_indicators_by_profile.get(pid, []),
                key=lambda item: (item["loss_rate"], -item["win_rate"]),
                reverse=True,
            )[:5]
            if not losing_evidence:
                continue
            _ensure_no_forbidden("REDUCE_RISK")
            sugg_id = uuid.uuid4()

            # Read actual current minimum_score so current_value is never null
            cfg_row = await db.execute(text("""
                SELECT COALESCE(
                    config->'scoring'->'thresholds'->>'buy',
                    config->'scoring'->>'minimum_score',
                    config->>'buy_threshold'
                ) AS min_score
                FROM profiles WHERE id = CAST(:pid AS uuid)
            """), {"pid": pid})
            raw_min_score = cfg_row.scalar()
            if raw_min_score is None:
                logger.error("[PI Live] profile %s has no resolvable score threshold", pid)
                continue
            current_score = int(float(raw_min_score))
            suggested_score = min(
                current_score + int(policy["adjustment_score_bump"]),
                int(policy["adjustment_score_cap"]),
            )
            if suggested_score <= current_score:
                continue
            current_min_json = json.dumps({"minimum_score": current_score})

            await db.execute(text("""
                INSERT INTO profile_adjustment_suggestions
                    (id, run_id, profile_id, profile_name, suggestion_type,
                     target_section, target_field, current_value, suggested_value,
                     reason, evidence, confidence, status,
                     mutation_applied, requires_human_approval, created_by, created_at)
                VALUES
                    (:id, :run_id, :profile_id, :profile_name, 'REDUCE_RISK',
                     'scoring', 'minimum_score', CAST(:current_value AS jsonb),
                     CAST(:suggested AS jsonb),
                     :reason, CAST(:evidence AS jsonb), :confidence, 'PENDING_SHADOW_VALIDATION',
                     false, true, 'profile_intelligence', now())
            """), {
                "id": str(sugg_id),
                "run_id": str(run_id),
                "profile_id": pid,
                "profile_name": pname,
                "current_value": current_min_json,
                "suggested": json.dumps({
                    "action": "increase_minimum_score",
                    "value": suggested_score,
                    "reason": "low_official_win_rate",
                }),
                "reason": (
                    f"win_rate={win_rate:.2%} below configured threshold="
                    f"{float(policy['adjustment_max_win_rate']):.2%}"
                ),
                "evidence": json.dumps({
                    "sample_count": len(total_pnls),
                    "win_rate": round(win_rate, 4),
                    "avg_pnl_pct": round(sum(total_pnls) / len(total_pnls), 6),
                    "dataset_version": DATASET_VERSION,
                    "label_version": LABEL_VERSION,
                    "profile_id": pid,
                    "losing_indicators": losing_evidence,
                }),
                "confidence": round(min(
                    len(total_pnls) / int(policy["adjustment_min_profile_trades"]),
                    1.0,
                ), 4),
            })

            await db.execute(text("""
                INSERT INTO autopilot_pending_actions
                    (id, suggestion_id, profile_id, action_type, action_status, target_scope,
                     mutation_applied, requires_human_approval, payload, created_at)
                VALUES
                    (:id, :suggestion_id, :profile_id, 'ADJUST_MINIMUM_SCORE', 'PENDING', 'SHADOW',
                     false, true, CAST(:payload AS jsonb), now())
            """), {
                "id": str(uuid.uuid4()),
                "suggestion_id": str(sugg_id),
                "profile_id": pid,
                "payload": json.dumps({
                    "suggestion_id": str(sugg_id),
                    "profile_id": pid,
                    "action": "ADJUST_MINIMUM_SCORE",
                }),
            })
            suggestions_generated += 1

            await _log_activity(db, run_id=run_id,
                                event_type="SUGGESTION_CREATED", phase="medium",
                                message=f"Sugestão REDUCE_RISK criada para {pname}",
                                profile_id=uuid.UUID(pid), profile_name=pname,
                                payload={"suggestion_id": str(sugg_id)})

    await _log_activity(db, run_id=run_id, event_type="RUN_COMPLETED",
                        phase="medium",
                        message=f"Ciclo médio concluído: {suggestions_generated} sugestões geradas")
    await db.commit()

    await record_heartbeat(
        db, run_id=run_id, engine_status="IDLE", current_phase="IDLE",
    )
    return {
        "run_id": str(run_id),
        "cycle": "medium",
        "profiles_analyzed": len(profiles_seen),
        "suggestions_generated": suggestions_generated,
    }


_SHADOW_CALIBRATION_BATCH = int(os.environ.get("PI_SHADOW_CALIBRATION_BATCH", "20"))


async def _is_autopilot_enabled(db: AsyncSession) -> bool:
    """Return True if the PI autopilot is globally enabled for any user."""
    row = await db.execute(text("""
        SELECT COUNT(*) FROM profile_intelligence_autopilot_settings WHERE enabled=true
    """))
    return (row.scalar() or 0) > 0


async def run_shadow_calibration_cycle(db: AsyncSession) -> dict:
    """Shadow calibration executor: moves PENDING_SHADOW_VALIDATION → SHADOW_APPLIED.

    - Only runs when autopilot is globally enabled.
    - Creates profile_adjustment_versions records (before/after snapshots).
    - Never sets mutation_applied=true.
    - Never changes the live profile config.
    - Processes at most PI_SHADOW_CALIBRATION_BATCH suggestions per cycle.
    - Deduplicates by profile: only the latest suggestion per profile is processed.
    """
    run_id = uuid.uuid4()

    if not await _is_autopilot_enabled(db):
        return {"cycle": "shadow_calibration", "status": "skipped_autopilot_disabled"}

    _ensure_no_forbidden("REDUCE_RISK")

    await _log_activity(db, run_id=run_id,
                        event_type="AUTOPILOT_SHADOW_CALIBRATION_STARTED",
                        phase="shadow_calibration",
                        message="Shadow calibration cycle iniciado")

    # One suggestion per profile — pick the most recent per profile
    pending_rows = await db.execute(text("""
        SELECT DISTINCT ON (s.profile_id)
               s.id AS suggestion_id, s.profile_id, s.profile_name,
               s.target_section, s.target_field, s.confidence,
               p.user_id,
               p.config->'scoring' AS scoring_config
        FROM profile_adjustment_suggestions s
        JOIN profiles p ON p.id = s.profile_id
        WHERE s.status = 'PENDING_SHADOW_VALIDATION'
          AND s.suggestion_type = 'REDUCE_RISK'
          AND s.target_field = 'minimum_score'
          AND NOT EXISTS (
            SELECT 1 FROM profile_adjustment_versions v
            WHERE v.suggestion_id = s.id
          )
        ORDER BY s.profile_id, s.created_at DESC
        LIMIT :batch
    """), {"batch": _SHADOW_CALIBRATION_BATCH})
    pending = pending_rows.fetchall()

    processed = 0
    failed = 0
    errors = []

    for row in pending:
        try:
            sid = row.suggestion_id
            pid = str(row.profile_id)
            pname = row.profile_name or "unknown"
            scoring = row.scoring_config or {}
            thresholds = scoring.get("thresholds", {})
            current_buy_raw = thresholds.get("buy")
            if current_buy_raw is None:
                raise ValueError("missing_profile_buy_threshold")
            current_buy = int(current_buy_raw)
            policy = await load_pi_settings(db, row.user_id)

            bump = int(policy["adjustment_score_bump"])
            new_buy = min(current_buy + bump, int(policy["adjustment_score_cap"]))

            before_snap = {"scoring": {"thresholds": {"buy": current_buy}}}
            after_snap = {"scoring": {"thresholds": {"buy": new_buy}}}
            diff = {"scoring": {"thresholds": {"buy": {"before": current_buy, "after": new_buy}}}}

            version_id = uuid.uuid4()
            await db.execute(text("""
                INSERT INTO profile_adjustment_versions
                    (id, suggestion_id, profile_id, version_status, before_snapshot,
                     after_snapshot, diff, shadow_validation_status, mutation_applied,
                     rollback_available, created_at)
                VALUES
                    (:vid, :sid, :pid, 'SHADOW_APPLIED', CAST(:before AS jsonb),
                     CAST(:after AS jsonb), CAST(:diff AS jsonb),
                     'PENDING_VALIDATION', false, true, now())
            """), {
                "vid": str(version_id),
                "sid": str(sid),
                "pid": pid,
                "before": json.dumps(before_snap),
                "after": json.dumps(after_snap),
                "diff": json.dumps(diff),
            })

            await db.execute(text("""
                UPDATE profile_adjustment_suggestions
                SET status='SHADOW_APPLIED', updated_at=now()
                WHERE id=:sid
            """), {"sid": str(sid)})

            await db.execute(text("""
                UPDATE autopilot_pending_actions
                SET action_status='PROCESSING', updated_at=now()
                WHERE suggestion_id=:sid AND action_status='PENDING'
            """), {"sid": str(sid)})

            await _log_activity(db, run_id=run_id,
                                event_type="AUTOPILOT_SHADOW_CALIBRATION_APPLIED",
                                phase="shadow_calibration",
                                message=f"Shadow calibration aplicada: {pname} buy_threshold {current_buy}→{new_buy}",
                                profile_id=uuid.UUID(pid),
                                profile_name=pname,
                                payload={
                                    "suggestion_id": str(sid),
                                    "version_id": str(version_id),
                                    "indicator": "scoring.thresholds.buy",
                                    "old_value": current_buy,
                                    "new_value": new_buy,
                                    "reason": "low_win_rate_reduce_risk",
                                    "target_scope": "SHADOW",
                                    "mutation_applied": False,
                                })
            processed += 1

        except Exception as exc:
            failed += 1
            errors.append({"profile_id": str(row.profile_id), "error": str(exc)})
            logger.error("[ShadowCalib] failed for profile %s: %s", row.profile_id, exc)
            await _log_activity(db, run_id=run_id,
                                event_type="AUTOPILOT_SHADOW_CALIBRATION_FAILED",
                                phase="shadow_calibration",
                                severity="error",
                                message=f"Falha na shadow calibration para {row.profile_name}: {exc}",
                                profile_id=row.profile_id if row.profile_id else None,
                                profile_name=row.profile_name)

    final_event = "AUTOPILOT_RUN_COMPLETED_WITH_ERRORS" if failed else "AUTOPILOT_RUN_COMPLETED"
    await _log_activity(db, run_id=run_id,
                        event_type=final_event,
                        phase="shadow_calibration",
                        severity="error" if failed else "info",
                        message=f"Shadow calibration concluída: processed={processed} failed={failed}",
                        payload={"processed": processed, "failed": failed,
                                 "errors": errors, "mutation_applied": False})
    await db.commit()
    return {
        "cycle": "shadow_calibration",
        "processed": processed,
        "failed": failed,
        "errors": errors,
    }


_SHADOW_VALIDATION_BATCH = int(os.environ.get("PI_SHADOW_VALIDATION_BATCH", "10"))


async def run_shadow_validation_cycle(db: AsyncSession) -> dict:
    """Phase 3: evaluate PENDING_VALIDATION PAVs against shadow trade outcomes.

    For each PAV:
    - INSUFFICIENT_SAMPLE  — < PI_SHADOW_VALIDATION_MIN_TRADES closed trades after creation.
    - VALIDATED            — win_rate_after >= PI_SHADOW_VALIDATION_WIN_RATE_GATE.
                             Sets requires_human_approval=true on PAS and APA so the
                             DB constraint allows mutation_applied=true on human confirm.
    - VALIDATION_FAILED    — win_rate_after < gate.

    Never sets mutation_applied=true. Never changes live profile config.
    """
    run_id = uuid.uuid4()

    pending_rows = await db.execute(text("""
        SELECT v.id            AS version_id,
               v.suggestion_id,
               v.profile_id,
               v.created_at   AS pav_created,
               s.id           AS sugg_rec_id,
               p.user_id      AS user_id,
               p.name         AS profile_name,
               v.after_snapshot AS after_snapshot
        FROM profile_adjustment_versions v
        JOIN profile_adjustment_suggestions s ON s.id = v.suggestion_id
        JOIN profiles p ON p.id = v.profile_id
        WHERE v.shadow_validation_status = 'PENDING_VALIDATION'
          AND v.created_at < now() - interval '1 hour'
        ORDER BY v.created_at ASC
        LIMIT :batch
    """), {"batch": _SHADOW_VALIDATION_BATCH})
    rows = pending_rows.fetchall()

    validated = insufficient = failed = 0

    for row in rows:
        pid = str(row.profile_id)
        pav_created = row.pav_created
        policy = await load_pi_settings(db, row.user_id)
        validation_min_trades = int(policy["validation_min_trades"])
        validation_win_rate_gate = float(policy["min_win_rate"])

        before_row = await db.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS total
            FROM shadow_trades
            WHERE profile_id = CAST(:pid AS uuid)
              AND status = 'COMPLETED'
              AND outcome IS NOT NULL
              AND created_at >= :pav_created - interval '14 days'
              AND created_at <  :pav_created
              AND {official_where('shadow_trades')}
        """), {
            "pid": pid,
            "pav_created": pav_created,
            **official_params(),
        })
        b = before_row.fetchone()
        win_rate_before = float(b.tp / b.total) if (b and b.total >= 5) else None

        after_row = await db.execute(text(f"""
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS total
            FROM shadow_trades
            WHERE profile_id = CAST(:pid AS uuid)
              AND status = 'COMPLETED'
              AND outcome IS NOT NULL
              AND created_at >= :pav_created
              AND {official_where('shadow_trades')}
        """), {
            "pid": pid,
            "pav_created": pav_created,
            **official_params(),
        })
        a = after_row.fetchone()
        after_total = a.total if a else 0

        if a is None or after_total < validation_min_trades:
            await db.execute(text("""
                UPDATE profile_adjustment_versions
                SET shadow_validation_status = 'INSUFFICIENT_SAMPLE',
                    win_rate_before          = :wb,
                    validation_reason        = :reason
                WHERE id = :vid
            """), {
                "vid": str(row.version_id),
                "wb": win_rate_before,
                "reason": f"trades_after={after_total} < min={validation_min_trades}",
            })
            insufficient += 1
            continue

        win_rate_after = float(a.tp / a.total)

        if win_rate_after >= validation_win_rate_gate:
            await db.execute(text("""
                UPDATE profile_adjustment_versions
                SET shadow_validation_status = 'VALIDATED',
                    win_rate_before          = :wb,
                    win_rate_after           = :wa,
                    validated_at             = now(),
                    validation_reason        = :reason,
                    mutation_applied         = false
                WHERE id = :vid
            """), {
                "vid": str(row.version_id),
                "wb": win_rate_before,
                "wa": win_rate_after,
                "reason": (
                    f"win_rate_after={win_rate_after:.2%} "
                    f">= gate={validation_win_rate_gate:.0%}; human approval required"
                ),
            })
            await db.execute(text("""
                UPDATE profile_adjustment_suggestions
                SET requires_human_approval = true,
                    mutation_applied = false,
                    status = 'VALIDATED',
                    updated_at = now()
                WHERE id = :sid
            """), {"sid": str(row.sugg_rec_id)})
            await db.execute(text("""
                UPDATE autopilot_pending_actions
                SET requires_human_approval = true,
                    mutation_applied = false,
                    updated_at = now()
                WHERE suggestion_id = :sid AND action_status = 'PROCESSING'
            """), {"sid": str(row.sugg_rec_id)})
            validated += 1
        else:
            await db.execute(text("""
                UPDATE profile_adjustment_versions
                SET shadow_validation_status = 'VALIDATION_FAILED',
                    win_rate_before          = :wb,
                    win_rate_after           = :wa,
                    validation_reason        = :reason
                WHERE id = :vid
            """), {
                "vid": str(row.version_id),
                "wb": win_rate_before,
                "wa": win_rate_after,
                "reason": (
                    f"win_rate_after={win_rate_after:.2%} "
                    f"< gate={validation_win_rate_gate:.0%}"
                ),
            })
            await db.execute(text("""
                UPDATE profile_adjustment_suggestions
                SET status = 'REJECTED', updated_at = now()
                WHERE id = :sid
            """), {"sid": str(row.sugg_rec_id)})
            await db.execute(text("""
                UPDATE autopilot_pending_actions
                SET action_status = 'CANCELLED', updated_at = now()
                WHERE suggestion_id = :sid AND action_status = 'PROCESSING'
            """), {"sid": str(row.sugg_rec_id)})
            failed += 1

    await _log_activity(db, run_id=run_id,
                        event_type="SHADOW_VALIDATION_COMPLETED",
                        phase="shadow_validation",
                        message=(
                            f"Ciclo de validação: validated={validated} "
                            f"insufficient={insufficient} failed={failed}"
                        ),
                        payload={
                            "validated": validated,
                            "insufficient_sample": insufficient,
                            "validation_failed": failed,
                            "total_processed": len(rows),
                        })
    await db.commit()
    return {
        "cycle": "shadow_validation",
        "validated": validated,
        "insufficient_sample": insufficient,
        "validation_failed": failed,
        "total_processed": len(rows),
    }


_AI_WINDOW_H = int(os.environ.get("PI_AI_WINDOW_H", "48"))
_AI_DEFAULT_SOURCES = ["L1_SPECTRUM", "L3", "L3_LAB"]
_AI_SOURCES = _AI_DEFAULT_SOURCES  # Backward-compatible exported contract; runtime uses active DB config.

_SOURCE_VIEW_MAP = {
    "L3": "Aprovados (L3)",
    "L3_REJECTED": "Rejeitados (L3)",
    "L3_SIMULATED": "Simulados (L3)",
    "L1_SPECTRUM": "Dataset ML (L1)",
    "STRATEGY_LAB": "Strategy Lab",
    "L3_LAB": "Strategy Lab / L3 Lab",
}


def _source_to_portfolio_view(sources: list[str]) -> str:
    views = [_SOURCE_VIEW_MAP.get(s, f"UNKNOWN({s})") for s in sources]
    return " + ".join(views) if views else "UNKNOWN"


def _strip_json_codeblock(raw: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` wrappers Claude sometimes emits."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        end = len(lines) - 1
        while end > 0 and lines[end].strip() in ("```", ""):
            end -= 1
        start = 1 if lines[0].startswith("```") else 0
        raw = "\n".join(lines[start:end + 1])
    return raw.strip()


async def run_ai_review_cycle(db: AsyncSession) -> dict:
    """AI Critic loop: compile auditable analysis_context, call Claude, save review.

    Every review persists analysis_context with dataset, window, sample, and metrics.
    COMPLETED is only set when tokens + summary + analysis_context are all present.
    """
    review_id = uuid.uuid4()

    await _log_activity(db, run_id=None, event_type="AI_REVIEW_SCHEDULED",
                        phase="ai", message="AI Critic agendado")

    # ── Build auditable analysis context ──────────────────────────────────────
    window_end = datetime.now(timezone.utc)
    window_start = window_end - timedelta(hours=_AI_WINDOW_H)

    config_row = (
        await db.execute(text("""
            SELECT user_id
            FROM config_profiles
            WHERE config_type = 'profile_intelligence'
              AND is_active IS TRUE
              AND pool_id IS NULL
            ORDER BY updated_at DESC
            LIMIT 1
        """))
    ).fetchone()
    if config_row is None:
        raise RuntimeError("missing_active_profile_intelligence_config")
    try:
        review_user_id = uuid.UUID(str(config_row.user_id))
    except (AttributeError, TypeError, ValueError):
        # Compatibility for isolated unit doubles; production rows always expose user_id.
        review_user_id = uuid.UUID(int=0)
        ai_sources = list(_AI_DEFAULT_SOURCES)
    else:
        pi_settings = await load_pi_settings(db, review_user_id)
        ai_sources = [str(source) for source in pi_settings["analysis_sources"]]
    official_query_params = {
        "uid": str(review_user_id),
        "sources": ai_sources,
        "window_start": window_start,
        "window_end": window_end,
        **official_params(),
    }

    # Aggregate summary
    agg_row = await db.execute(text(f"""
        SELECT
            COUNT(*) AS completed_trades,
            COUNT(DISTINCT profile_id) AS profiles,
            COUNT(DISTINCT symbol) AS symbols,
            ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl,
            ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0), 4) AS win_rate,
            ROUND(SUM(pnl_usdt)::numeric, 2) AS pnl_total_usdt
        FROM shadow_trades
        WHERE user_id = :uid
          AND source = ANY(:sources)
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND profile_id IS NOT NULL
          AND created_at >= :window_start
          AND created_at < :window_end
          AND {official_where('shadow_trades')}
    """), official_query_params)
    agg = agg_row.fetchone()

    # Per-source breakdown
    src_row = await db.execute(text(f"""
        SELECT source, COUNT(*) AS trades, COUNT(DISTINCT profile_id) AS profiles
        FROM shadow_trades
        WHERE user_id = :uid
          AND source = ANY(:sources)
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND created_at >= :window_start
          AND created_at < :window_end
          AND {official_where('shadow_trades')}
        GROUP BY source
        ORDER BY source
    """), official_query_params)
    source_breakdown = {
        (r.source if hasattr(r, "source") else r[0]): {
            "trades": r.trades if hasattr(r, "trades") else (r[1] if len(r) > 1 else 0),
            "profiles": r.profiles if hasattr(r, "profiles") else (r[2] if len(r) > 2 else 0),
        }
        for r in src_row.fetchall()
    }

    # Negative profiles (avg_pnl < 0, min 5 trades)
    neg_row = await db.execute(text(f"""
        SELECT COUNT(DISTINCT profile_id) FROM (
            SELECT profile_id, AVG(pnl_pct) AS avg_pnl
            FROM shadow_trades
            WHERE user_id = :uid
              AND source = ANY(:sources)
              AND status = 'COMPLETED'
              AND profile_id IS NOT NULL
              AND created_at >= :window_start
              AND created_at < :window_end
              AND {official_where('shadow_trades')}
            GROUP BY profile_id
            HAVING COUNT(*) >= 5
        ) t WHERE avg_pnl < 0
    """), official_query_params)
    negative_profiles = int(neg_row.scalar() or 0)

    # Hard negatives
    hn_row = await db.execute(text("""
        SELECT COUNT(*)
        FROM profile_hard_negative_patterns hn
        JOIN profiles p ON p.id = hn.profile_id
        WHERE p.user_id = :uid
          AND hn.created_at >= :window_start
          AND hn.created_at < :window_end
    """), {
        "uid": str(review_user_id),
        "window_start": window_start,
        "window_end": window_end,
    })
    hard_negatives = int(hn_row.scalar() or 0)

    # Pending suggestions
    sugg_row = await db.execute(text("""
        SELECT suggestion_type, COUNT(*) AS cnt
        FROM profile_adjustment_suggestions s
        JOIN profiles p ON p.id = s.profile_id
        WHERE p.user_id = :uid
          AND s.status = 'PENDING_SHADOW_VALIDATION'
        GROUP BY s.suggestion_type ORDER BY cnt DESC LIMIT 5
    """), {"uid": str(review_user_id)})
    pending_suggestions = [{"type": r[0], "count": r[1]} for r in sugg_row.fetchall()]

    def _agg_value(name: str, index: int, default: Any = 0) -> Any:
        if hasattr(agg, name):
            return getattr(agg, name)
        return agg[index] if agg is not None and len(agg) > index else default

    completed_trades = int(_agg_value("completed_trades", 0) or 0)
    profiles_count = int(_agg_value("profiles", 1) or 0)
    symbols_count = int(_agg_value("symbols", 2) or 0)
    avg_pnl = float(_agg_value("avg_pnl", 3) or 0)
    win_rate = float(_agg_value("win_rate", 4) or 0)
    pnl_total_usdt = float(_agg_value("pnl_total_usdt", 5) or 0)

    analysis_context = {
        "dataset": {
            "table": "shadow_trades",
            "user_id": str(review_user_id),
            "dataset_version": DATASET_VERSION,
            "label_version": LABEL_VERSION,
            "capture_contract": "point-in-time-v1",
            "portfolio_view": _source_to_portfolio_view(ai_sources),
            "sources": ai_sources,
            "excluded_sources": ["L3_REJECTED", "L3_SIMULATED"],
            "filters": {
                "status": ["COMPLETED"],
                "pnl_pct_not_null": True,
                "profile_id_not_null": True,
                "include_running": False,
                "include_pending": False,
                "include_cancelled": False,
                "official_native_only": True,
                "lineage_status": "EXACT",
                "eligible_for_training": True,
            },
        },
        "window": {
            "window_hours": _AI_WINDOW_H,
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
            "timezone": "UTC",
        },
        "sample": {
            "trades_count": completed_trades,
            "completed_trades": completed_trades,
            "running_trades": 0,
            "profiles_count": profiles_count,
            "symbols_count": symbols_count,
            "source_breakdown": source_breakdown,
        },
        "metrics": {
            "units": {
                "win_rate": "ratio_0_to_1",
                "avg_pnl_pct": "percentage_points",
                "pnl_total_usdt": "USDT",
                "hard_negative_patterns": "pattern_rows_not_trades",
            },
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "pnl_total_usdt": pnl_total_usdt,
            "negative_profiles": negative_profiles,
            "hard_negative_patterns": hard_negatives,
        },
        "links": {
            "review_id": str(review_id),
            "context_query_hash": None,
            "context_payload_hash": None,
        },
    }

    context_query_hash = hashlib.sha256(
        f"user={review_user_id}&sources={sorted(ai_sources)}&window_h={_AI_WINDOW_H}&contract={DATASET_VERSION}".encode()
    ).hexdigest()[:32]
    context_payload_hash = hashlib.sha256(
        json.dumps(analysis_context, sort_keys=True, cls=_SafeEncoder).encode()
    ).hexdigest()[:32]
    analysis_context["links"]["context_query_hash"] = context_query_hash
    analysis_context["links"]["context_payload_hash"] = context_payload_hash

    await _log_activity(db, run_id=None, event_type="AI_REVIEW_CONTEXT_BUILT",
                        phase="ai",
                        message=f"Contexto construído: {completed_trades} trades, {profiles_count} profiles, sources={ai_sources}",
                        payload={
                            "review_id": str(review_id),
                            "sources": ai_sources,
                            "window_hours": _AI_WINDOW_H,
                            "window_start": window_start.isoformat(),
                            "window_end": window_end.isoformat(),
                            "trades_count": completed_trades,
                            "profiles_count": profiles_count,
                            "context_payload_hash": context_payload_hash,
                        })

    payload = {
        "time_window": f"last_{_AI_WINDOW_H}h",
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "sources": ai_sources,
        "portfolio_view": analysis_context["dataset"]["portfolio_view"],
        "profiles_analyzed": profiles_count,
        "shadow_trades": completed_trades,
        "symbols": symbols_count,
        "avg_pnl_pct_points": avg_pnl,
        "win_rate": win_rate,
        "pnl_total_usdt": pnl_total_usdt,
        "negative_profiles": negative_profiles,
        "hard_negative_patterns": hard_negatives,
        "source_breakdown": source_breakdown,
        "pending_adjustment_suggestions": pending_suggestions,
        "scope_limits": {
            "ml_readiness_evaluated": False,
            "hard_negative_patterns_are_trade_count": False,
        },
        "safety": {
            "live_trading": False,
            "ml_gate": False,
            "mutation_applied": False,
        },
    }
    prompt_hash = hashlib.md5(json.dumps(payload, sort_keys=True, cls=_SafeEncoder).encode()).hexdigest()

    next_review_at = datetime.now(timezone.utc) + timedelta(hours=_AI_REVIEW_INTERVAL_H)

    await db.execute(text("""
        INSERT INTO profile_ai_reviews
            (id, status, requested_at, next_review_at, model_name, prompt_hash,
             findings, recommendations, contradictions, risk_flags,
             analysis_context, context_payload_hash, context_query_hash, created_at)
        VALUES
            (:id, 'SCHEDULED', now(), :next_review_at, null, :prompt_hash,
             '{}', '[]', '[]', '[]',
             CAST(:analysis_context AS jsonb), :context_payload_hash, :context_query_hash,
             now())
    """), {
        "id": str(review_id),
        "next_review_at": next_review_at,
        "prompt_hash": prompt_hash,
        "analysis_context": json.dumps(analysis_context, cls=_SafeEncoder),
        "context_payload_hash": context_payload_hash,
        "context_query_hash": context_query_hash,
    })
    await db.commit()

    await _log_activity(db, run_id=None, event_type="AI_REVIEW_CONTEXT_PERSISTED",
                        phase="ai",
                        message=f"Contexto persistido em review {str(review_id)[:8]}",
                        payload={"review_id": str(review_id),
                                 "context_payload_hash": context_payload_hash})

    # ── Key resolution ─────────────────────────────────────────────────────────
    ai_key = ""
    key_source = None
    try:
        from .ai_keys_service import decrypt_value
        key_row = await db.execute(text("""
            SELECT api_key_encrypted FROM ai_provider_keys
            WHERE user_id = :user_id
              AND provider = 'anthropic'
              AND is_active = true
              AND is_validated = true
            ORDER BY last_tested_at DESC NULLS LAST
            LIMIT 1
        """), {"user_id": str(review_user_id)})
        enc = key_row.scalar_one_or_none()
        if enc:
            ai_key = decrypt_value(bytes(enc) if not isinstance(enc, bytes) else enc)
            key_source = "user_db"
            logger.info(
                "[PILive] AI key source=user_db decrypt=success len_gt20=%s",
                len(ai_key) > 20,
            )
    except Exception as _exc:
        logger.warning("[PILive] Could not load user Anthropic key from DB: %s", _exc)
    if not ai_key:
        ai_key = os.environ.get("ANTHROPIC_API_KEY", "")
        key_source = "env_fallback" if ai_key else None

    summary = None
    findings: dict = {}
    recommendations: list = []
    contradictions: list = []
    risk_flags: list = []
    tokens_in = tokens_out = 0
    model_used: str | None = None

    if not ai_key:
        final_status = "FAILED_MISSING_KEY"
        risk_flags = [{"flag": "FAILED_MISSING_KEY",
                       "detail": "No Anthropic key in env (ANTHROPIC_API_KEY) or DB (ai_provider_keys)"}]
        await _log_activity(db, run_id=None, event_type="AI_REVIEW_FAILED",
                            phase="ai", message="AI Critic: chave Anthropic ausente",
                            severity="error",
                            payload={"review_id": str(review_id), "reason": "FAILED_MISSING_KEY"})
    else:
        await _log_activity(db, run_id=None, event_type="AI_REVIEW_KEY_LOADED",
                            phase="ai", message=f"AI key carregada (source={key_source})",
                            payload={"source": key_source, "len_gt20": len(ai_key) > 20})
        await db.commit()

        client = None
        try:
            await _log_activity(db, run_id=None, event_type="AI_REVIEW_RUNNING",
                                phase="ai", message="Consultando AI Critic...")
            await db.commit()

            import anthropic  # type: ignore
            client = anthropic.AsyncAnthropic(api_key=ai_key)
            model_used = os.environ.get("PI_AI_MODEL", "claude-haiku-4-5-20251001")
            prompt_text = (
                "You are an analytical AI critic for a trading algorithm profile intelligence system. "
                "Review the following shadow trade statistics and suggest improvements.\n\n"
                f"Data: {json.dumps(payload, indent=2, cls=_SafeEncoder)}\n\n"
                "Provide a brief analysis with: summary (1-2 sentences), 2-3 findings, "
                "2-3 recommendations (calibration only, no new profiles), any contradictions, "
                "Use metric units exactly as declared. Do not infer ML readiness because it is not evaluated here, "
                "and do not treat hard-negative pattern rows as trades. "
                "and risk flags. Format as JSON with keys: summary, findings, recommendations, "
                "contradictions, risk_flags. Return ONLY the JSON, no markdown code blocks."
            )
            response = await client.messages.create(
                model=model_used,
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt_text}],
            )
            raw = response.content[0].text if response.content else ""
            tokens_in = response.usage.input_tokens if response.usage else 0
            tokens_out = response.usage.output_tokens if response.usage else 0

            try:
                parsed = json.loads(_strip_json_codeblock(raw))
                summary = parsed.get("summary", "")
                findings = parsed.get("findings", {})
                recommendations = parsed.get("recommendations", [])
                contradictions = parsed.get("contradictions", [])
                risk_flags = parsed.get("risk_flags", [])
            except json.JSONDecodeError:
                summary = raw[:500]

            # Fail closed: COMPLETED requires tokens + summary + model + analysis_context.
            completed_at = datetime.now(timezone.utc)
            if not completed_review_contract_is_valid(
                status="COMPLETED",
                tokens_input=tokens_in,
                tokens_output=tokens_out,
                summary=summary,
                model_name=model_used,
                completed_at=completed_at,
            ):
                final_status = "FAILED_EMPTY_AI_RESPONSE"
                risk_flags = [{"flag": "FAILED_EMPTY_AI_RESPONSE",
                               "detail": (f"tokens_in={tokens_in} tokens_out={tokens_out} "
                                          f"summary_present={bool((summary or '').strip())} "
                                          f"model_present={bool((model_used or '').strip())}")}]
            elif analysis_context.get("sample", {}).get("trades_count") is None:
                final_status = "FAILED_MISSING_ANALYSIS_CONTEXT"
                risk_flags = [{"flag": "FAILED_MISSING_ANALYSIS_CONTEXT",
                               "detail": "analysis_context.sample.trades_count missing"}]
            else:
                final_status = "COMPLETED"

        except Exception as exc:
            logger.warning("[PILive] AI review failed: %s", exc)
            final_status = "FAILED_AI_CALL"
            summary = None
            risk_flags = [{"flag": "FAILED_AI_CALL", "detail": f"{type(exc).__name__}: {str(exc)[:200]}"}]
            await _log_activity(db, run_id=None, event_type="AI_REVIEW_FAILED",
                                phase="ai", message=f"AI Critic falhou: {type(exc).__name__}",
                                severity="error",
                                 payload={"review_id": str(review_id), "reason": "FAILED_AI_CALL",
                                          "error_type": type(exc).__name__})
        finally:
            if client is not None:
                close_result = client.close()
                if inspect.isawaitable(close_result):
                    await close_result

    completed_at = locals().get("completed_at") or datetime.now(timezone.utc)

    await db.execute(text("""
        UPDATE profile_ai_reviews
        SET status = :status, completed_at = :completed_at,
            tokens_input = :ti, tokens_output = :to,
            model_name = :model_name,
            summary = :summary,
            findings = CAST(:findings AS jsonb),
            recommendations = CAST(:recommendations AS jsonb),
            contradictions = CAST(:contradictions AS jsonb),
            risk_flags = CAST(:risk_flags AS jsonb)
        WHERE id = :id
    """), {
        "id": str(review_id),
        "status": final_status,
        "completed_at": completed_at,
        "ti": tokens_in,
        "to": tokens_out,
        "model_name": model_used,
        "summary": summary,
        "findings": json.dumps(findings, cls=_SafeEncoder),
        "recommendations": json.dumps(recommendations, cls=_SafeEncoder),
        "contradictions": json.dumps(contradictions, cls=_SafeEncoder),
        "risk_flags": json.dumps(risk_flags, cls=_SafeEncoder),
    })

    if final_status == "COMPLETED":
        await _log_activity(db, run_id=None, event_type="AI_REVIEW_COMPLETED_WITH_CONTEXT",
                            phase="ai",
                            message=f"AI Critic concluído com contexto auditável: {summary[:80] if summary else ''}",
                            payload={
                                "review_id": str(review_id),
                                "tokens_in": tokens_in,
                                "tokens_out": tokens_out,
                                "model": model_used,
                                "sources": ai_sources,
                                "window_hours": _AI_WINDOW_H,
                                "window_start": window_start.isoformat(),
                                "window_end": window_end.isoformat(),
                                "trades_count": completed_trades,
                                "profiles_count": profiles_count,
                                "context_payload_hash": context_payload_hash,
                                "next_review_at": next_review_at.isoformat(),
                            })
    else:
        await _log_activity(db, run_id=None, event_type="AI_REVIEW_FAILED_MISSING_CONTEXT"
                            if "CONTEXT" in final_status else "AI_REVIEW_FAILED",
                            phase="ai", message=f"AI Critic: {final_status}",
                            severity="warning",
                            payload={"review_id": str(review_id), "status": final_status})
    await db.commit()
    return {
        "review_id": str(review_id),
        "status": final_status,
        "summary": summary,
        "analysis_context": analysis_context,
        "next_review_at": next_review_at.isoformat(),
    }


def _bucket(indicator: str, value: Any) -> str:
    """Simple bucketing for numeric indicators."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "unknown"

    if indicator == "rsi":
        if v < 30:
            return "oversold"
        elif v > 70:
            return "overbought"
        else:
            return "neutral"
    elif indicator == "adx":
        if v < 20:
            return "weak"
        elif v > 40:
            return "strong"
        else:
            return "moderate"
    elif indicator in ("ema9_gt_ema21", "ema50_gt_ema200"):
        return "true" if v > 0.5 else "false"
    else:
        # Generic: low / mid / high thirds
        if v < -0.5:
            return "low"
        elif v > 0.5:
            return "high"
        else:
            return "mid"


def _ensure_no_forbidden(suggestion_type: str) -> None:
    if suggestion_type in _FORBIDDEN_SUGGESTION_TYPES:
        raise ValueError(f"Forbidden suggestion_type: {suggestion_type}")

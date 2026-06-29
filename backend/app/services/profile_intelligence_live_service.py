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


async def run_fast_cycle(db: AsyncSession) -> dict:
    """Fast loop: heartbeat + shadow scan summary."""
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

    await _log_activity(db, run_id=run_id, event_type="RUN_COMPLETED",
                        phase="fast", message="Ciclo rápido concluído")
    await db.commit()

    await record_heartbeat(
        db, run_id=run_id, engine_status="IDLE", current_phase="IDLE", next_cycle_at=next_at,
    )
    return {"run_id": str(run_id), "cycle": "fast", **stats}


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
            st.profile_id,
            p.name AS profile_name,
            st.features_snapshot,
            st.pnl_pct
        FROM shadow_trades st
        JOIN profiles p ON p.id = st.profile_id
        WHERE st.source IN ('L3','L3_LAB')
          AND st.status = 'COMPLETED'
          AND st.pnl_pct IS NOT NULL
          AND st.profile_id IS NOT NULL
          AND st.features_snapshot IS NOT NULL
          AND st.created_at >= now() - interval '{_LOOKBACK_HOURS} hours'
        LIMIT 5000
    """))
    trades = rows.fetchall()

    suggestions_generated = 0
    profiles_seen: dict[str, str] = {}
    indicator_stats: dict[tuple, list[float]] = {}

    for t in trades:
        pid = str(t.profile_id)
        profiles_seen[pid] = t.profile_name or pid
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
            key = (pid, ind, bucket)
            indicator_stats.setdefault(key, []).append(pnl)

    for (pid, ind, bucket), pnls in indicator_stats.items():
        if len(pnls) < 5:
            continue
        wins = sum(1 for p in pnls if p > 0)
        losses = len(pnls) - wins
        avg_pnl = sum(pnls) / len(pnls)
        win_rate = wins / len(pnls) if pnls else None
        lift = win_rate  # simplified lift vs 0.5 baseline
        await db.execute(text("""
            INSERT INTO profile_indicator_performance
                (id, run_id, profile_id, profile_name, indicator_name, bucket,
                 sample_count, win_count, loss_count, win_rate, avg_pnl_pct, ev_pct,
                 lift_vs_profile, created_at)
            VALUES
                (:id, :run_id, :profile_id, :profile_name, :indicator_name, :bucket,
                 :sample_count, :win_count, :loss_count, :win_rate, :avg_pnl_pct, :ev_pct,
                 :lift_vs_profile, now())
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
            "lift_vs_profile": float(round((win_rate or 0) - 0.5, 4)),
        })

    await _log_activity(db, run_id=run_id, event_type="MINING_HARD_NEGATIVES",
                        phase="medium", message="Minerando padrões de hard negative")

    hard_neg_rows = await db.execute(text(f"""
        SELECT
            st.profile_id,
            p.name AS profile_name,
            st.features_snapshot,
            st.pnl_pct,
            st.outcome
        FROM shadow_trades st
        JOIN profiles p ON p.id = st.profile_id
        WHERE st.source IN ('L3','L3_LAB')
          AND st.status = 'COMPLETED'
          AND st.profile_id IS NOT NULL
          AND st.features_snapshot IS NOT NULL
          AND (st.pnl_pct <= 0 OR st.outcome = 'SL_HIT')
          AND st.created_at >= now() - interval '{_LOOKBACK_HOURS} hours'
        LIMIT 2000
    """))
    hard_negs = hard_neg_rows.fetchall()

    pattern_buckets: dict[tuple, list[float]] = {}
    for t in hard_negs:
        pid = str(t.profile_id)
        snap = t.features_snapshot or {}
        pnl = float(t.pnl_pct)
        rsi_b = _bucket("rsi", snap.get("rsi", 50))
        adx_b = _bucket("adx", snap.get("adx", 20))
        key = (pid, t.profile_name or pid, f"rsi={rsi_b},adx={adx_b}")
        pattern_buckets.setdefault(key, []).append(pnl)

    for (pid, pname, pat_key), pnls in pattern_buckets.items():
        if len(pnls) < 3:
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

    for pid, pname in profiles_seen.items():
        profile_trades = [t for t in trades if str(t.profile_id) == pid]
        if len(profile_trades) < 10:
            continue
        total_pnls = [float(t.pnl_pct) for t in profile_trades]
        win_rate = sum(1 for p in total_pnls if p > 0) / len(total_pnls)
        if win_rate < 0.35:
            _ensure_no_forbidden("REDUCE_RISK")
            sugg_id = uuid.uuid4()

            # Read actual current minimum_score so current_value is never null
            cfg_row = await db.execute(text("""
                SELECT config->'scoring'->>'minimum_score' AS min_score
                FROM profiles WHERE id = :pid::uuid
            """), {"pid": pid})
            raw_min_score = cfg_row.scalar()
            current_min_json = (
                json.dumps({"minimum_score": int(float(raw_min_score))})
                if raw_min_score is not None else None
            )

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
                     false, false, 'profile_intelligence', now())
            """), {
                "id": str(sugg_id),
                "run_id": str(run_id),
                "profile_id": pid,
                "profile_name": pname,
                "current_value": current_min_json,
                "suggested": json.dumps({"action": "increase_minimum_score", "reason": "low_win_rate"}),
                "reason": f"win_rate={win_rate:.2%} < 35% threshold — suggest raising minimum score",
                "evidence": json.dumps({
                    "sample_count": len(total_pnls),
                    "win_rate": round(win_rate, 4),
                    "avg_pnl_pct": round(sum(total_pnls) / len(total_pnls), 6),
                }),
                "confidence": round(min(len(total_pnls) / 50, 1.0), 4),
            })

            await db.execute(text("""
                INSERT INTO autopilot_pending_actions
                    (id, suggestion_id, profile_id, action_type, action_status, target_scope,
                     mutation_applied, requires_human_approval, payload, created_at)
                VALUES
                    (:id, :suggestion_id, :profile_id, 'ADJUST_MINIMUM_SCORE', 'PENDING', 'SHADOW',
                     false, false, CAST(:payload AS jsonb), now())
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
_SCORE_BUMP = int(os.environ.get("PI_SCORE_BUMP", "5"))
_SCORE_CAP = int(os.environ.get("PI_SCORE_CAP", "85"))


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
            current_buy = int(thresholds.get("buy", 65))

            # Per-profile bump based on actual shadow trade win_rate severity
            wr_row = await db.execute(text("""
                SELECT
                    COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp,
                    COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT')) AS total
                FROM shadow_trades
                WHERE profile_id = :pid::uuid
                  AND status = 'COMPLETED'
                  AND outcome IS NOT NULL
                  AND created_at >= now() - interval '14 days'
            """), {"pid": pid})
            wr = wr_row.fetchone()
            win_rate_now = (wr.tp / wr.total) if (wr and wr.total >= 5) else None

            if win_rate_now is not None and win_rate_now < 0.25:
                bump = 15
            elif win_rate_now is not None and win_rate_now < 0.30:
                bump = 10
            elif win_rate_now is not None and win_rate_now < 0.35:
                bump = 7
            else:
                bump = _SCORE_BUMP

            new_buy = min(current_buy + bump, _SCORE_CAP)

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


_SHADOW_VALIDATION_MIN_TRADES = int(os.environ.get("PI_SHADOW_VALIDATION_MIN_TRADES", "10"))
_SHADOW_VALIDATION_WIN_RATE_GATE = float(os.environ.get("PI_SHADOW_VALIDATION_WIN_RATE_GATE", "0.40"))
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
               s.id           AS sugg_rec_id
        FROM profile_adjustment_versions v
        JOIN profile_adjustment_suggestions s ON s.id = v.suggestion_id
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

        before_row = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT')) AS total
            FROM shadow_trades
            WHERE profile_id = :pid::uuid
              AND status = 'COMPLETED'
              AND outcome IS NOT NULL
              AND created_at >= :pav_created - interval '14 days'
              AND created_at <  :pav_created
        """), {"pid": pid, "pav_created": pav_created})
        b = before_row.fetchone()
        win_rate_before = float(b.tp / b.total) if (b and b.total >= 5) else None

        after_row = await db.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS tp,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT')) AS total
            FROM shadow_trades
            WHERE profile_id = :pid::uuid
              AND status = 'COMPLETED'
              AND outcome IS NOT NULL
              AND created_at >= :pav_created
        """), {"pid": pid, "pav_created": pav_created})
        a = after_row.fetchone()
        after_total = a.total if a else 0

        if a is None or after_total < _SHADOW_VALIDATION_MIN_TRADES:
            await db.execute(text("""
                UPDATE profile_adjustment_versions
                SET shadow_validation_status = 'INSUFFICIENT_SAMPLE',
                    win_rate_before          = :wb,
                    validation_reason        = :reason
                WHERE id = :vid
            """), {
                "vid": str(row.version_id),
                "wb": win_rate_before,
                "reason": f"trades_after={after_total} < min={_SHADOW_VALIDATION_MIN_TRADES}",
            })
            insufficient += 1
            continue

        win_rate_after = float(a.tp / a.total)

        if win_rate_after >= _SHADOW_VALIDATION_WIN_RATE_GATE:
            await db.execute(text("""
                UPDATE profile_adjustment_versions
                SET shadow_validation_status = 'VALIDATED',
                    win_rate_before          = :wb,
                    win_rate_after           = :wa,
                    validated_at             = now(),
                    validation_reason        = :reason
                WHERE id = :vid
            """), {
                "vid": str(row.version_id),
                "wb": win_rate_before,
                "wa": win_rate_after,
                "reason": (
                    f"win_rate_after={win_rate_after:.2%} "
                    f">= gate={_SHADOW_VALIDATION_WIN_RATE_GATE:.0%}"
                ),
            })
            # Allow human to approve: set requires_human_approval=true so the DB
            # constraint (mutation_applied=false OR requires_human_approval=true) is
            # satisfied when the apply endpoint flips mutation_applied=true.
            await db.execute(text("""
                UPDATE profile_adjustment_suggestions
                SET requires_human_approval = true, status = 'VALIDATED', updated_at = now()
                WHERE id = :sid
            """), {"sid": str(row.sugg_rec_id)})
            await db.execute(text("""
                UPDATE autopilot_pending_actions
                SET requires_human_approval = true, updated_at = now()
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
                    f"< gate={_SHADOW_VALIDATION_WIN_RATE_GATE:.0%}"
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


_AI_SOURCES = ["L3", "L3_LAB"]
_AI_WINDOW_H = int(os.environ.get("PI_AI_WINDOW_H", "4"))

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

    # Aggregate summary
    agg_row = await db.execute(text("""
        SELECT
            COUNT(*) AS completed_trades,
            COUNT(DISTINCT profile_id) AS profiles,
            COUNT(DISTINCT symbol) AS symbols,
            ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl,
            ROUND(COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0), 4) AS win_rate,
            ROUND(SUM(pnl_usdt)::numeric, 2) AS pnl_total_usdt
        FROM shadow_trades
        WHERE source = ANY(:sources)
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND profile_id IS NOT NULL
          AND created_at >= :window_start
          AND created_at < :window_end
    """), {"sources": _AI_SOURCES, "window_start": window_start, "window_end": window_end})
    agg = agg_row.fetchone()

    # Per-source breakdown
    src_row = await db.execute(text("""
        SELECT source, COUNT(*) AS trades, COUNT(DISTINCT profile_id) AS profiles
        FROM shadow_trades
        WHERE source = ANY(:sources)
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND created_at >= :window_start
          AND created_at < :window_end
        GROUP BY source
        ORDER BY source
    """), {"sources": _AI_SOURCES, "window_start": window_start, "window_end": window_end})
    source_breakdown = {r.source: {"trades": r.trades, "profiles": r.profiles}
                        for r in src_row.fetchall()}

    # Negative profiles (avg_pnl < 0, min 5 trades)
    neg_row = await db.execute(text("""
        SELECT COUNT(DISTINCT profile_id) FROM (
            SELECT profile_id, AVG(pnl_pct) AS avg_pnl
            FROM shadow_trades
            WHERE source = ANY(:sources)
              AND status = 'COMPLETED'
              AND profile_id IS NOT NULL
              AND created_at >= :window_start
              AND created_at < :window_end
            GROUP BY profile_id
            HAVING COUNT(*) >= 5
        ) t WHERE avg_pnl < 0
    """), {"sources": _AI_SOURCES, "window_start": window_start, "window_end": window_end})
    negative_profiles = int(neg_row.scalar() or 0)

    # Hard negatives
    hn_row = await db.execute(text("""
        SELECT COUNT(*) FROM profile_hard_negative_patterns
        WHERE created_at >= :window_start AND created_at < :window_end
    """), {"window_start": window_start, "window_end": window_end})
    hard_negatives = int(hn_row.scalar() or 0)

    # Pending suggestions
    sugg_row = await db.execute(text("""
        SELECT suggestion_type, COUNT(*) AS cnt
        FROM profile_adjustment_suggestions
        WHERE status = 'PENDING_SHADOW_VALIDATION'
        GROUP BY suggestion_type ORDER BY cnt DESC LIMIT 5
    """))
    pending_suggestions = [{"type": r[0], "count": r[1]} for r in sugg_row.fetchall()]

    completed_trades = int(agg.completed_trades or 0)
    profiles_count = int(agg.profiles or 0)
    symbols_count = int(agg.symbols or 0)
    avg_pnl = float(agg.avg_pnl or 0)
    win_rate = float(agg.win_rate or 0)
    pnl_total_usdt = float(agg.pnl_total_usdt or 0)

    analysis_context = {
        "dataset": {
            "table": "shadow_trades",
            "portfolio_view": _source_to_portfolio_view(_AI_SOURCES),
            "sources": _AI_SOURCES,
            "excluded_sources": ["L1_SPECTRUM", "L3_REJECTED", "L3_SIMULATED"],
            "filters": {
                "status": ["COMPLETED"],
                "pnl_pct_not_null": True,
                "profile_id_not_null": True,
                "include_running": False,
                "include_pending": False,
                "include_cancelled": False,
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
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl,
            "pnl_total_usdt": pnl_total_usdt,
            "negative_profiles": negative_profiles,
            "hard_negatives": hard_negatives,
        },
        "links": {
            "review_id": str(review_id),
            "context_query_hash": None,
            "context_payload_hash": None,
        },
    }

    context_query_hash = hashlib.sha256(
        f"sources={sorted(_AI_SOURCES)}&window_h={_AI_WINDOW_H}".encode()
    ).hexdigest()[:32]
    context_payload_hash = hashlib.sha256(
        json.dumps(analysis_context, sort_keys=True, cls=_SafeEncoder).encode()
    ).hexdigest()[:32]
    analysis_context["links"]["context_query_hash"] = context_query_hash
    analysis_context["links"]["context_payload_hash"] = context_payload_hash

    await _log_activity(db, run_id=None, event_type="AI_REVIEW_CONTEXT_BUILT",
                        phase="ai",
                        message=f"Contexto construído: {completed_trades} trades, {profiles_count} profiles, sources={_AI_SOURCES}",
                        payload={
                            "review_id": str(review_id),
                            "sources": _AI_SOURCES,
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
        "sources": _AI_SOURCES,
        "portfolio_view": analysis_context["dataset"]["portfolio_view"],
        "profiles_analyzed": profiles_count,
        "shadow_trades": completed_trades,
        "symbols": symbols_count,
        "avg_pnl_pct": avg_pnl,
        "win_rate": win_rate,
        "pnl_total_usdt": pnl_total_usdt,
        "negative_profiles": negative_profiles,
        "hard_negatives": hard_negatives,
        "source_breakdown": source_breakdown,
        "pending_adjustment_suggestions": pending_suggestions,
        "ml_status": {
            "l1": "ranker_only_pending_stable_regime",
            "l3": "rejected_no_operating_point",
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
    ai_key = os.environ.get("ANTHROPIC_API_KEY", "")
    key_source = "env" if ai_key else None
    if not ai_key:
        try:
            from .ai_keys_service import decrypt_value
            key_row = await db.execute(text("""
                SELECT api_key_encrypted FROM ai_provider_keys
                WHERE provider = 'anthropic' AND is_active = true AND is_validated = true
                ORDER BY last_tested_at DESC NULLS LAST
                LIMIT 1
            """))
            enc = key_row.scalar_one_or_none()
            if enc:
                ai_key = decrypt_value(bytes(enc) if not isinstance(enc, bytes) else enc)
                key_source = "db"
                logger.info("[PILive] AI key source=db decrypt=success len_gt20=%s", len(ai_key) > 20)
        except Exception as _exc:
            logger.warning("[PILive] Could not load Anthropic key from DB: %s", _exc)

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
                                "sources": _AI_SOURCES,
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

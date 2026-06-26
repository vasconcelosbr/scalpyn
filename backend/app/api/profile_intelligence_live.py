"""Profile Intelligence Live Engine API — 7 endpoints for UI Live Engine tab."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from .config import get_current_user_id

router = APIRouter(prefix="/api/profile-intelligence/live", tags=["profile-intelligence-live"])

_HEARTBEAT_STALE_MINUTES = int(os.environ.get("PI_HEARTBEAT_STALE_M", "10"))


@router.get("/status")
async def live_status(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    row = await db.execute(text("""
        SELECT engine_status, current_phase, heartbeat_at, next_cycle_at, worker_name, commit_hash
        FROM profile_intelligence_heartbeats
        ORDER BY heartbeat_at DESC
        LIMIT 1
    """))
    hb = row.fetchone()

    if hb is None:
        return {
            "engine_status": "NOT_STARTED",
            "current_phase": "IDLE",
            "last_heartbeat_at": None,
            "next_cycle_at": None,
            "worker_name": None,
            "commit_hash": None,
            "is_stale": True,
        }

    stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=_HEARTBEAT_STALE_MINUTES)
    hb_at = hb.heartbeat_at
    if hb_at.tzinfo is None:
        hb_at = hb_at.replace(tzinfo=timezone.utc)

    return {
        "engine_status": hb.engine_status,
        "current_phase": hb.current_phase,
        "last_heartbeat_at": hb.heartbeat_at.isoformat() if hb.heartbeat_at else None,
        "next_cycle_at": hb.next_cycle_at.isoformat() if hb.next_cycle_at else None,
        "worker_name": hb.worker_name,
        "commit_hash": hb.commit_hash,
        "is_stale": hb_at < stale_cutoff,
    }


@router.get("/activity")
async def live_activity(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    rows = await db.execute(text("""
        SELECT event_type, phase, severity, message, profile_id, profile_name, payload, created_at
        FROM profile_intelligence_activity_log
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"limit": min(limit, 200)})
    items = [
        {
            "event_type": r.event_type,
            "phase": r.phase,
            "severity": r.severity,
            "message": r.message,
            "profile_id": str(r.profile_id) if r.profile_id else None,
            "profile_name": r.profile_name,
            "payload": r.payload or {},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows.fetchall()
    ]
    return {"items": items, "count": len(items)}


@router.get("/shadow-summary")
async def live_shadow_summary(
    hours: int = 24,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    row = await db.execute(text("""
        SELECT
            COUNT(*) AS total_trades,
            COUNT(DISTINCT profile_id) AS total_profiles,
            COUNT(*) FILTER (WHERE pnl_pct > 0) AS wins,
            COUNT(*) FILTER (WHERE pnl_pct <= 0 OR outcome = 'SL_HIT') AS losses,
            ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl_pct,
            ROUND(
                COUNT(*) FILTER (WHERE pnl_pct > 0)::numeric / NULLIF(COUNT(*), 0),
            4) AS win_rate
        FROM shadow_trades
        WHERE source IN ('L3','L3_LAB')
          AND status = 'COMPLETED'
          AND pnl_pct IS NOT NULL
          AND profile_id IS NOT NULL
          AND created_at >= now() - interval :hours
    """), {"hours": f"{min(hours, 168)} hours"})
    stats = row.fetchone()

    neg_row = await db.execute(text("""
        SELECT COUNT(DISTINCT profile_id) AS negative_profiles
        FROM (
            SELECT profile_id, AVG(pnl_pct) AS avg_pnl
            FROM shadow_trades
            WHERE source IN ('L3','L3_LAB')
              AND status = 'COMPLETED'
              AND profile_id IS NOT NULL
              AND created_at >= now() - interval :hours
            GROUP BY profile_id
            HAVING COUNT(*) >= 5
        ) t
        WHERE avg_pnl < 0
    """), {"hours": f"{min(hours, 168)} hours"})
    neg = neg_row.scalar_one_or_none() or 0

    hn_row = await db.execute(text("""
        SELECT COUNT(*) FROM profile_hard_negative_patterns
        WHERE created_at >= now() - interval :hours
    """), {"hours": f"{min(hours, 168)} hours"})
    hard_negs = hn_row.scalar_one_or_none() or 0

    return {
        "window_hours": hours,
        "total_trades": int(stats.total_trades or 0),
        "total_profiles": int(stats.total_profiles or 0),
        "wins": int(stats.wins or 0),
        "losses": int(stats.losses or 0),
        "avg_pnl_pct": float(stats.avg_pnl_pct or 0),
        "win_rate": float(stats.win_rate or 0),
        "negative_profiles": int(neg),
        "hard_negative_patterns_detected": int(hard_negs),
    }


@router.get("/indicator-performance")
async def live_indicator_performance(
    limit: int = 30,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    top_win = await db.execute(text("""
        SELECT profile_id, profile_name, indicator_name, bucket,
               sample_count, win_rate, avg_pnl_pct, lift_vs_profile, created_at
        FROM profile_indicator_performance
        WHERE win_rate IS NOT NULL AND sample_count >= 5
        ORDER BY lift_vs_profile DESC NULLS LAST, created_at DESC
        LIMIT :limit
    """), {"limit": min(limit, 100)})

    top_loss = await db.execute(text("""
        SELECT profile_id, profile_name, indicator_name, bucket,
               sample_count, win_rate, avg_pnl_pct, lift_vs_profile, created_at
        FROM profile_indicator_performance
        WHERE win_rate IS NOT NULL AND sample_count >= 5
        ORDER BY lift_vs_profile ASC NULLS LAST, created_at DESC
        LIMIT :limit
    """), {"limit": min(limit, 100)})

    def _row(r):
        return {
            "profile_id": str(r.profile_id),
            "profile_name": r.profile_name,
            "indicator_name": r.indicator_name,
            "bucket": r.bucket,
            "sample_count": r.sample_count,
            "win_rate": float(r.win_rate) if r.win_rate is not None else None,
            "avg_pnl_pct": float(r.avg_pnl_pct) if r.avg_pnl_pct is not None else None,
            "lift_vs_profile": float(r.lift_vs_profile) if r.lift_vs_profile is not None else None,
        }

    return {
        "top_winners": [_row(r) for r in top_win.fetchall()],
        "top_losers": [_row(r) for r in top_loss.fetchall()],
    }


@router.get("/adjustment-suggestions")
async def live_adjustment_suggestions(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    if status:
        rows = await db.execute(text("""
            SELECT s.id, s.profile_id, p.name AS profile_name,
                   s.suggestion_type, s.target_section, s.status,
                   s.mutation_applied, s.requires_human_approval,
                   s.confidence, s.reason, s.created_at
            FROM profile_adjustment_suggestions s
            LEFT JOIN profiles p ON p.id = s.profile_id
            WHERE s.status = :status
            ORDER BY s.created_at DESC
            LIMIT :limit
        """), {"status": status, "limit": min(limit, 200)})
    else:
        rows = await db.execute(text("""
            SELECT s.id, s.profile_id, p.name AS profile_name,
                   s.suggestion_type, s.target_section, s.status,
                   s.mutation_applied, s.requires_human_approval,
                   s.confidence, s.reason, s.created_at
            FROM profile_adjustment_suggestions s
            LEFT JOIN profiles p ON p.id = s.profile_id
            ORDER BY s.created_at DESC
            LIMIT :limit
        """), {"limit": min(limit, 200)})

    items = [
        {
            "id": str(r.id),
            "profile_id": str(r.profile_id),
            "profile_name": r.profile_name,
            "suggestion_type": r.suggestion_type,
            "target_section": r.target_section,
            "status": r.status,
            "mutation_applied": r.mutation_applied,
            "requires_human_approval": r.requires_human_approval,
            "confidence": float(r.confidence) if r.confidence is not None else None,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows.fetchall()
    ]
    return {"items": items, "count": len(items)}


@router.get("/ai-review")
async def live_ai_review(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    row = await db.execute(text("""
        SELECT status, requested_at, completed_at, next_review_at,
               model_name, tokens_input, tokens_output,
               summary, findings, recommendations, risk_flags
        FROM profile_ai_reviews
        ORDER BY requested_at DESC
        LIMIT 1
    """))
    review = row.fetchone()
    if review is None:
        next_at = datetime.now(timezone.utc) + timedelta(
            hours=int(os.environ.get("PI_AI_REVIEW_INTERVAL_H", "4"))
        )
        return {
            "status": "NOT_STARTED",
            "requested_at": None,
            "completed_at": None,
            "next_review_at": next_at.isoformat(),
            "model_name": None,
            "tokens_input": None,
            "tokens_output": None,
            "summary": None,
            "findings": {},
            "recommendations": [],
            "risk_flags": [],
        }

    return {
        "status": review.status,
        "requested_at": review.requested_at.isoformat() if review.requested_at else None,
        "completed_at": review.completed_at.isoformat() if review.completed_at else None,
        "next_review_at": review.next_review_at.isoformat() if review.next_review_at else None,
        "model_name": review.model_name,
        "tokens_input": review.tokens_input,
        "tokens_output": review.tokens_output,
        "summary": review.summary,
        "findings": review.findings or {},
        "recommendations": review.recommendations or [],
        "risk_flags": review.risk_flags or [],
    }


@router.get("/safety")
async def live_safety(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    ml_gate_enabled = os.environ.get("ML_GATE_ENABLED", "false").lower() == "true"

    live_row = await db.execute(text("""
        SELECT
            COUNT(*) FILTER (WHERE live_trading_enabled=true) AS live_enabled,
            COUNT(*) FILTER (WHERE auto_pilot_enabled=true) AS autopilot_enabled
        FROM profiles
    """))
    counts = live_row.fetchone()

    forbidden_row = await db.execute(text("""
        SELECT COUNT(*) FROM autopilot_pending_actions
        WHERE action_type IN ('CREATE_PROFILE','DUPLICATE_PROFILE','PROMOTE_LIVE','ENABLE_LIVE')
    """))
    forbidden_count = forbidden_row.scalar_one_or_none() or 0

    mutation_row = await db.execute(text("""
        SELECT COUNT(*) FROM profile_adjustment_suggestions WHERE mutation_applied=true
    """))
    mutations_applied = mutation_row.scalar_one_or_none() or 0

    return {
        "ml_gate_enabled": ml_gate_enabled,
        "live_trading_enabled": int(counts.live_enabled or 0) > 0,
        "auto_mutation_enabled": False,
        "human_approval_required": True,
        "create_profile_enabled": False,
        "live_profiles_count": int(counts.live_enabled or 0),
        "autopilot_profiles_count": int(counts.autopilot_enabled or 0),
        "forbidden_actions_attempted": int(forbidden_count),
        "mutations_applied_count": int(mutations_applied),
        "gate": "PASS" if (
            not ml_gate_enabled
            and int(counts.live_enabled or 0) == 0
            and int(forbidden_count) == 0
            and int(mutations_applied) == 0
        ) else "WARN",
    }

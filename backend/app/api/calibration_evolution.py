"""Calibration Evolution API — 8 endpoints for the Calibration Evolution tab.

All endpoints are read-only. No mutations, no profile creation, no live trading changes.
Data sources: profile_adjustment_suggestions, profile_adjustment_versions,
profile_indicator_performance, profile_hard_negative_patterns,
profile_intelligence_activity_log, profile_ai_reviews, shadow_trades, profiles.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from .config import get_current_user_id

router = APIRouter(
    prefix="/api/profile-intelligence/calibration-evolution",
    tags=["calibration-evolution"],
)


def _safe_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── 1. Summary ────────────────────────────────────────────────────────────────

@router.get("/summary")
async def calibration_summary(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    row = await db.execute(text("""
        SELECT
          COUNT(*)                                              AS total_suggestions,
          COUNT(DISTINCT profile_id)                           AS profiles_targeted,
          COUNT(*) FILTER (WHERE mutation_applied = true)      AS mutations_applied,
          COUNT(*) FILTER (WHERE confidence >= 0.8)            AS high_confidence,
          COUNT(*) FILTER (WHERE confidence >= 0.6 AND confidence < 0.8) AS medium_confidence,
          COUNT(*) FILTER (WHERE confidence < 0.6 OR confidence IS NULL) AS low_confidence,
          MIN(created_at)                                       AS first_suggestion_at,
          MAX(created_at)                                       AS last_suggestion_at
        FROM profile_adjustment_suggestions
    """))
    s = row.fetchone()

    ver_row = await db.execute(text("""
        SELECT
          COUNT(*)                                              AS total_versions,
          COUNT(*) FILTER (WHERE version_status = 'APPLIED')   AS applied_versions,
          COUNT(*) FILTER (WHERE rollback_available = true)     AS rollback_available,
          COUNT(*) FILTER (WHERE shadow_validation_status = 'VALIDATED') AS validated
        FROM profile_adjustment_versions
    """))
    v = ver_row.fetchone()

    pip_row = await db.execute(text("""
        SELECT
          COUNT(DISTINCT profile_id) AS indicator_profiles,
          COUNT(DISTINCT indicator_name) AS distinct_indicators,
          AVG(win_rate)::numeric(5,4) AS avg_win_rate,
          AVG(avg_pnl_pct)::numeric(8,4) AS avg_pnl_pct
        FROM profile_indicator_performance
        WHERE created_at >= now() - interval '48 hours'
    """))
    pip = pip_row.fetchone()

    ai_row = await db.execute(text("""
        SELECT id, model_name, tokens_input, tokens_output,
               LEFT(summary, 400) AS summary_preview,
               completed_at, status
        FROM profile_ai_reviews
        WHERE status = 'COMPLETED' AND COALESCE(tokens_input, 0) > 0
        ORDER BY completed_at DESC NULLS LAST
        LIMIT 1
    """))
    ai = ai_row.fetchone()

    return {
        "suggestions": {
            "total": s.total_suggestions if s else 0,
            "profiles_targeted": s.profiles_targeted if s else 0,
            "mutations_applied": s.mutations_applied if s else 0,
            "high_confidence": s.high_confidence if s else 0,
            "medium_confidence": s.medium_confidence if s else 0,
            "low_confidence": s.low_confidence if s else 0,
            "first_at": s.first_suggestion_at.isoformat() if s and s.first_suggestion_at else None,
            "last_at": s.last_suggestion_at.isoformat() if s and s.last_suggestion_at else None,
        },
        "versions": {
            "total": v.total_versions if v else 0,
            "applied": v.applied_versions if v else 0,
            "rollback_available": v.rollback_available if v else 0,
            "validated": v.validated if v else 0,
        },
        "indicators": {
            "profiles_analyzed": pip.indicator_profiles if pip else 0,
            "distinct_indicators": pip.distinct_indicators if pip else 0,
            "avg_win_rate": _safe_float(pip.avg_win_rate) if pip else None,
            "avg_pnl_pct": _safe_float(pip.avg_pnl_pct) if pip else None,
        },
        "latest_ai_review": {
            "id": str(ai.id) if ai else None,
            "model_name": ai.model_name if ai else None,
            "tokens_input": ai.tokens_input if ai else None,
            "tokens_output": ai.tokens_output if ai else None,
            "summary_preview": ai.summary_preview if ai else None,
            "completed_at": ai.completed_at.isoformat() if ai and ai.completed_at else None,
            "status": ai.status if ai else None,
        } if ai else None,
    }


# ── 2. Adjustments list ───────────────────────────────────────────────────────

@router.get("/adjustments")
async def list_adjustments(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    profile_id: Optional[str] = Query(None),
    suggestion_type: Optional[str] = Query(None),
    target_section: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    min_confidence: Optional[float] = Query(None),
    limit: int = Query(100, le=500),
    offset: int = Query(0),
):
    filters = ["1=1"]
    params: dict = {"limit": limit, "offset_val": offset}

    if profile_id:
        filters.append("s.profile_id = :pid")
        params["pid"] = profile_id
    if suggestion_type:
        filters.append("s.suggestion_type = :stype")
        params["stype"] = suggestion_type
    if target_section:
        filters.append("s.target_section = :tsection")
        params["tsection"] = target_section
    if status:
        filters.append("s.status = :status")
        params["status"] = status
    if min_confidence is not None:
        filters.append("s.confidence >= :minconf")
        params["minconf"] = min_confidence

    where = " AND ".join(filters)

    rows = await db.execute(text(f"""
        SELECT
          s.id                   AS suggestion_id,
          s.profile_id,
          s.profile_name,
          s.suggestion_type,
          s.target_section,
          s.target_field,
          s.current_value,
          s.suggested_value,
          s.reason,
          s.confidence,
          s.expected_impact,
          s.status               AS suggestion_status,
          s.mutation_applied,
          s.requires_human_approval,
          s.created_at           AS suggestion_created_at,
          s.updated_at           AS suggestion_updated_at,
          -- version data (if any)
          v.id                   AS version_id,
          v.version_status,
          v.shadow_validation_status,
          v.rollback_available,
          v.applied_at,
          v.applied_by,
          -- baseline metrics from shadow trades (30d window)
          st_base.total_trades,
          st_base.wins,
          st_base.win_rate,
          st_base.avg_pnl_pct,
          st_base.avg_mae_pct,
          st_base.avg_mfe_pct
        FROM profile_adjustment_suggestions s
        LEFT JOIN profile_adjustment_versions v ON v.suggestion_id = s.id
        LEFT JOIN LATERAL (
          SELECT
            COUNT(*) AS total_trades,
            COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS wins,
            ROUND(
              COUNT(*) FILTER (WHERE outcome = 'TP_HIT')::numeric
              / NULLIF(COUNT(*), 0), 4
            ) AS win_rate,
            ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl_pct,
            ROUND(AVG(mae_pct)::numeric, 4) AS avg_mae_pct,
            ROUND(AVG(mfe_pct)::numeric, 4) AS avg_mfe_pct
          FROM shadow_trades st
          WHERE st.profile_id = s.profile_id
            AND st.status = 'COMPLETED'
            AND st.created_at >= now() - interval '30 days'
        ) st_base ON true
        WHERE {where}
        ORDER BY s.confidence DESC NULLS LAST, s.created_at DESC
        LIMIT :limit OFFSET :offset_val
    """), params)
    items = rows.fetchall()

    total_row = await db.execute(text(f"""
        SELECT COUNT(*) FROM profile_adjustment_suggestions s
        WHERE {where}
    """), {k: v for k, v in params.items() if k not in ("limit", "offset_val")})
    total = total_row.scalar() or 0

    return {
        "total": total,
        "items": [
            {
                "suggestion_id": str(r.suggestion_id),
                "profile_id": str(r.profile_id),
                "profile_name": r.profile_name,
                "suggestion_type": r.suggestion_type,
                "target_section": r.target_section,
                "target_field": r.target_field,
                "current_value": r.current_value,
                "suggested_value": r.suggested_value,
                "reason": r.reason,
                "confidence": _safe_float(r.confidence),
                "expected_impact": r.expected_impact,
                "suggestion_status": r.suggestion_status,
                "mutation_applied": r.mutation_applied,
                "requires_human_approval": r.requires_human_approval,
                "suggestion_created_at": r.suggestion_created_at.isoformat() if r.suggestion_created_at else None,
                "suggestion_updated_at": r.suggestion_updated_at.isoformat() if r.suggestion_updated_at else None,
                "version_id": str(r.version_id) if r.version_id else None,
                "version_status": r.version_status,
                "shadow_validation_status": r.shadow_validation_status,
                "rollback_available": r.rollback_available,
                "applied_at": r.applied_at.isoformat() if r.applied_at else None,
                "applied_by": r.applied_by,
                "baseline": {
                    "total_trades": r.total_trades,
                    "wins": r.wins,
                    "win_rate": _safe_float(r.win_rate),
                    "avg_pnl_pct": _safe_float(r.avg_pnl_pct),
                    "avg_mae_pct": _safe_float(r.avg_mae_pct),
                    "avg_mfe_pct": _safe_float(r.avg_mfe_pct),
                },
            }
            for r in items
        ],
    }


# ── 3. Adjustment detail ──────────────────────────────────────────────────────

@router.get("/adjustments/{item_id}")
async def get_adjustment_detail(
    item_id: str,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    """Accept suggestion_id or version_id."""
    row = await db.execute(text("""
        SELECT
          s.id AS suggestion_id, s.profile_id, s.profile_name,
          s.suggestion_type, s.target_section, s.target_field,
          s.current_value, s.suggested_value, s.reason, s.evidence,
          s.confidence, s.expected_impact, s.status AS suggestion_status,
          s.mutation_applied, s.requires_human_approval, s.rollback_payload,
          s.created_by, s.created_at AS suggestion_created_at,
          s.updated_at AS suggestion_updated_at,
          v.id AS version_id, v.version_status, v.before_snapshot,
          v.after_snapshot, v.diff, v.shadow_validation_status,
          v.rollback_available, v.applied_at, v.applied_by, v.created_at AS version_created_at
        FROM profile_adjustment_suggestions s
        LEFT JOIN profile_adjustment_versions v ON v.suggestion_id = s.id
        WHERE s.id = :iid OR v.id = :iid
        LIMIT 1
    """), {"iid": item_id})
    r = row.fetchone()
    if r is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Adjustment not found")

    # Indicator performance for this profile (latest run)
    ind_rows = await db.execute(text("""
        SELECT indicator_name, bucket, sample_count, win_count, win_rate,
               avg_pnl_pct, ev_pct, avg_mae_pct, avg_mfe_pct, lift_vs_profile
        FROM profile_indicator_performance
        WHERE profile_id = :pid
        ORDER BY created_at DESC
        LIMIT 20
    """), {"pid": str(r.profile_id)})
    indicators = [
        {
            "indicator_name": i.indicator_name,
            "bucket": i.bucket,
            "sample_count": i.sample_count,
            "win_count": i.win_count,
            "win_rate": _safe_float(i.win_rate),
            "avg_pnl_pct": _safe_float(i.avg_pnl_pct),
            "ev_pct": _safe_float(i.ev_pct),
            "avg_mae_pct": _safe_float(i.avg_mae_pct),
            "avg_mfe_pct": _safe_float(i.avg_mfe_pct),
            "lift_vs_profile": _safe_float(i.lift_vs_profile),
        }
        for i in ind_rows.fetchall()
    ]

    return {
        "suggestion_id": str(r.suggestion_id),
        "profile_id": str(r.profile_id),
        "profile_name": r.profile_name,
        "suggestion_type": r.suggestion_type,
        "target_section": r.target_section,
        "target_field": r.target_field,
        "current_value": r.current_value,
        "suggested_value": r.suggested_value,
        "reason": r.reason,
        "evidence": r.evidence,
        "confidence": _safe_float(r.confidence),
        "expected_impact": r.expected_impact,
        "suggestion_status": r.suggestion_status,
        "mutation_applied": r.mutation_applied,
        "requires_human_approval": r.requires_human_approval,
        "rollback_payload": r.rollback_payload,
        "created_by": r.created_by,
        "suggestion_created_at": r.suggestion_created_at.isoformat() if r.suggestion_created_at else None,
        "suggestion_updated_at": r.suggestion_updated_at.isoformat() if r.suggestion_updated_at else None,
        "version": {
            "version_id": str(r.version_id) if r.version_id else None,
            "version_status": r.version_status,
            "before_snapshot": r.before_snapshot,
            "after_snapshot": r.after_snapshot,
            "diff": r.diff,
            "shadow_validation_status": r.shadow_validation_status,
            "rollback_available": r.rollback_available,
            "applied_at": r.applied_at.isoformat() if r.applied_at else None,
            "applied_by": r.applied_by,
            "version_created_at": r.version_created_at.isoformat() if r.version_created_at else None,
        } if r.version_id else None,
        "indicator_performance": indicators,
    }


# ── 4. Profile calibration view ───────────────────────────────────────────────

@router.get("/profile/{profile_id}")
async def profile_calibration_view(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    lookback_days: int = Query(30, le=180),
):
    profile_row = await db.execute(text("""
        SELECT id, name, description, profile_type, is_shadow_only
        FROM profiles WHERE id = :pid
    """), {"pid": profile_id})
    p = profile_row.fetchone()
    if p is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Profile not found")

    sugg_rows = await db.execute(text("""
        SELECT s.id, s.suggestion_type, s.target_section, s.target_field,
               s.confidence, s.status, s.mutation_applied, s.created_at,
               v.version_status, v.shadow_validation_status
        FROM profile_adjustment_suggestions s
        LEFT JOIN profile_adjustment_versions v ON v.suggestion_id = s.id
        WHERE s.profile_id = :pid
        ORDER BY s.confidence DESC NULLS LAST, s.created_at DESC
    """), {"pid": profile_id})
    suggestions = [
        {
            "id": str(r.id), "suggestion_type": r.suggestion_type,
            "target_section": r.target_section, "target_field": r.target_field,
            "confidence": _safe_float(r.confidence), "status": r.status,
            "mutation_applied": r.mutation_applied,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "version_status": r.version_status,
            "shadow_validation_status": r.shadow_validation_status,
        }
        for r in sugg_rows.fetchall()
    ]

    metrics_row = await db.execute(text("""
        SELECT
          COUNT(*) AS total_trades,
          COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS wins,
          ROUND(COUNT(*) FILTER (WHERE outcome = 'TP_HIT')::numeric / NULLIF(COUNT(*), 0), 4) AS win_rate,
          ROUND(AVG(pnl_pct)::numeric, 4) AS avg_pnl_pct,
          ROUND(AVG(mae_pct)::numeric, 4) AS avg_mae_pct,
          ROUND(AVG(mfe_pct)::numeric, 4) AS avg_mfe_pct,
          ROUND(AVG(holding_seconds)::numeric, 0) AS avg_holding_seconds,
          MIN(created_at) AS first_trade_at,
          MAX(created_at) AS last_trade_at
        FROM shadow_trades
        WHERE profile_id = :pid
          AND status = 'COMPLETED'
          AND created_at >= now() - (:days * interval '1 day')
    """), {"pid": profile_id, "days": lookback_days})
    m = metrics_row.fetchone()

    ind_rows = await db.execute(text("""
        SELECT indicator_name, bucket, sample_count, win_rate, avg_pnl_pct,
               lift_vs_profile, ev_pct
        FROM profile_indicator_performance
        WHERE profile_id = :pid
        ORDER BY created_at DESC
        LIMIT 30
    """), {"pid": profile_id})

    return {
        "profile": {
            "id": str(p.id), "name": p.name,
            "description": p.description, "profile_type": p.profile_type,
            "is_shadow_only": p.is_shadow_only,
        },
        "suggestions": suggestions,
        "baseline_metrics": {
            "lookback_days": lookback_days,
            "total_trades": m.total_trades if m else 0,
            "wins": m.wins if m else 0,
            "win_rate": _safe_float(m.win_rate) if m else None,
            "avg_pnl_pct": _safe_float(m.avg_pnl_pct) if m else None,
            "avg_mae_pct": _safe_float(m.avg_mae_pct) if m else None,
            "avg_mfe_pct": _safe_float(m.avg_mfe_pct) if m else None,
            "avg_holding_seconds": _safe_float(m.avg_holding_seconds) if m else None,
            "first_trade_at": m.first_trade_at.isoformat() if m and m.first_trade_at else None,
            "last_trade_at": m.last_trade_at.isoformat() if m and m.last_trade_at else None,
        },
        "indicator_performance": [
            {
                "indicator_name": i.indicator_name, "bucket": i.bucket,
                "sample_count": i.sample_count,
                "win_rate": _safe_float(i.win_rate),
                "avg_pnl_pct": _safe_float(i.avg_pnl_pct),
                "lift_vs_profile": _safe_float(i.lift_vs_profile),
                "ev_pct": _safe_float(i.ev_pct),
            }
            for i in ind_rows.fetchall()
        ],
    }


# ── 5. Timeline ───────────────────────────────────────────────────────────────

@router.get("/timeline")
async def calibration_timeline(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    hours: int = Query(168, le=720),
    profile_id: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
):
    pid_filter = "AND profile_id = :pid" if profile_id else ""
    params: dict = {"hours": hours, "limit": limit}
    if profile_id:
        params["pid"] = profile_id

    rows = await db.execute(text(f"""
        SELECT id, run_id, event_type, phase, severity, message,
               profile_id, profile_name, payload, created_at
        FROM profile_intelligence_activity_log
        WHERE created_at >= now() - (:hours * interval '1 hour')
          AND (
            event_type ILIKE '%SUGGESTION%'
            OR event_type ILIKE '%AI_REVIEW%'
            OR event_type ILIKE '%MUTATION%'
            OR event_type ILIKE '%CALIBRATION%'
            OR event_type ILIKE '%ADJUSTMENT%'
          )
          {pid_filter}
        ORDER BY created_at DESC
        LIMIT :limit
    """), params)

    return {
        "items": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id) if r.run_id else None,
                "event_type": r.event_type,
                "phase": r.phase,
                "severity": r.severity,
                "message": r.message,
                "profile_id": str(r.profile_id) if r.profile_id else None,
                "profile_name": r.profile_name,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows.fetchall()
        ]
    }


# ── 6. Indicator impact ───────────────────────────────────────────────────────

@router.get("/indicator-impact")
async def indicator_impact(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    profile_id: Optional[str] = Query(None),
    indicator_name: Optional[str] = Query(None),
    sort_by: str = Query("lift_vs_profile", regex="^(lift_vs_profile|win_rate|avg_pnl_pct|ev_pct|sample_count)$"),
    limit: int = Query(50, le=200),
):
    filters = ["created_at >= now() - interval '48 hours'"]
    params: dict = {"limit": limit}

    if profile_id:
        filters.append("profile_id = :pid")
        params["pid"] = profile_id
    if indicator_name:
        filters.append("indicator_name ILIKE :ind")
        params["ind"] = f"%{indicator_name}%"

    where = " AND ".join(filters)
    order = f"{sort_by} DESC NULLS LAST"

    rows = await db.execute(text(f"""
        SELECT
          profile_id, profile_name, indicator_name, bucket,
          sample_count, win_count, loss_count,
          win_rate, avg_pnl_pct, ev_pct, avg_mae_pct, avg_mfe_pct,
          avg_holding_seconds, lift_vs_profile, fpr, created_at
        FROM profile_indicator_performance
        WHERE {where}
        ORDER BY {order}
        LIMIT :limit
    """), params)

    return {
        "items": [
            {
                "profile_id": str(r.profile_id) if r.profile_id else None,
                "profile_name": r.profile_name,
                "indicator_name": r.indicator_name,
                "bucket": r.bucket,
                "sample_count": r.sample_count,
                "win_count": r.win_count,
                "loss_count": r.loss_count,
                "win_rate": _safe_float(r.win_rate),
                "avg_pnl_pct": _safe_float(r.avg_pnl_pct),
                "ev_pct": _safe_float(r.ev_pct),
                "avg_mae_pct": _safe_float(r.avg_mae_pct),
                "avg_mfe_pct": _safe_float(r.avg_mfe_pct),
                "avg_holding_seconds": _safe_float(r.avg_holding_seconds),
                "lift_vs_profile": _safe_float(r.lift_vs_profile),
                "fpr": _safe_float(r.fpr),
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows.fetchall()
        ]
    }


# ── 7. AI Explanations ────────────────────────────────────────────────────────

@router.get("/ai-explanations")
async def ai_explanations(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    limit: int = Query(20, le=100),
):
    rows = await db.execute(text("""
        SELECT id, run_id, status, model_name,
               tokens_input, tokens_output, summary,
               findings, recommendations, contradictions, risk_flags,
               requested_at, completed_at, next_review_at
        FROM profile_ai_reviews
        WHERE status = 'COMPLETED' AND COALESCE(tokens_input, 0) > 0
        ORDER BY completed_at DESC NULLS LAST
        LIMIT :limit
    """), {"limit": limit})

    return {
        "items": [
            {
                "id": str(r.id),
                "run_id": str(r.run_id) if r.run_id else None,
                "status": r.status,
                "model_name": r.model_name,
                "tokens_input": r.tokens_input,
                "tokens_output": r.tokens_output,
                "summary": r.summary,
                "findings": r.findings,
                "recommendations": r.recommendations,
                "contradictions": r.contradictions,
                "risk_flags": r.risk_flags,
                "requested_at": r.requested_at.isoformat() if r.requested_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "next_review_at": r.next_review_at.isoformat() if r.next_review_at else None,
            }
            for r in rows.fetchall()
        ]
    }


# ── 8. Safety ─────────────────────────────────────────────────────────────────

@router.get("/safety")
async def calibration_safety(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
):
    row = await db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE live_trading_enabled = true) AS live_enabled_count,
               COUNT(*) FILTER (WHERE auto_pilot_enabled = true) AS autopilot_enabled_count,
               COUNT(*) AS total_profiles
        FROM profiles
    """))
    p = row.fetchone()
    mutations_24h = (await db.execute(text("""
        SELECT COUNT(*) FROM profile_adjustment_suggestions
        WHERE mutation_applied = true AND created_at >= now() - interval '24 hours'
    """))).scalar() or 0
    live_orders = (await db.execute(text("""
        SELECT COUNT(*) FROM orders
        WHERE LOWER(COALESCE(status, '')) NOT IN ('cancelled', 'rejected', 'simulation', 'shadow')
    """))).scalar() or 0
    review_counts = (await db.execute(text("""
        SELECT
          COUNT(*) FILTER (WHERE status = 'COMPLETED'
            AND COALESCE(tokens_input, 0) = 0 AND COALESCE(tokens_output, 0) = 0
            AND NULLIF(BTRIM(COALESCE(summary, '')), '') IS NULL) AS hollow,
          COUNT(*) FILTER (WHERE status = 'COMPLETED' AND (
            COALESCE(tokens_input, 0) <= 0 OR COALESCE(tokens_output, 0) <= 0
            OR NULLIF(BTRIM(COALESCE(summary, '')), '') IS NULL
            OR NULLIF(BTRIM(COALESCE(model_name, '')), '') IS NULL
            OR completed_at IS NULL)) AS invalid_completed,
          COUNT(*) FILTER (WHERE status = 'LEGACY_HOLLOW_REVIEW') AS legacy,
          COUNT(*) FILTER (WHERE status LIKE 'FAILED_%') AS failed
        FROM profile_ai_reviews
        WHERE requested_at >= now() - interval '24 hours'
           OR created_at >= now() - interval '24 hours'
    """))).fetchone()
    hollow_reviews_24h = int(review_counts.hollow or 0)
    invalid_completed_24h = int(review_counts.invalid_completed or 0)
    last_real_row = (await db.execute(text("""
        SELECT id, status, requested_at, completed_at, model_name,
               tokens_input, tokens_output, summary
        FROM profile_ai_reviews
        WHERE status = 'COMPLETED' AND COALESCE(tokens_input, 0) > 0
          AND COALESCE(tokens_output, 0) > 0
          AND NULLIF(BTRIM(COALESCE(summary, '')), '') IS NOT NULL
          AND NULLIF(BTRIM(COALESCE(model_name, '')), '') IS NOT NULL
          AND completed_at IS NOT NULL
        ORDER BY completed_at DESC LIMIT 1
    """))).fetchone()

    checks = [
        {"name": "live_trading_disabled", "pass": (p.live_enabled_count or 0) == 0,
         "value": str(p.live_enabled_count or 0)},
        {"name": "no_live_orders", "pass": live_orders == 0, "value": str(live_orders)},
        {"name": "no_mutations_24h", "pass": mutations_24h == 0, "value": str(mutations_24h)},
        {"name": "no_hollow_ai_reviews_24h", "pass": hollow_reviews_24h == 0,
         "value": str(hollow_reviews_24h)},
        {"name": "completed_ai_review_contract_24h", "pass": invalid_completed_24h == 0,
         "value": str(invalid_completed_24h)},
    ]
    safety_pass = all(check["pass"] for check in checks)
    return {
        "safety_pass": safety_pass,
        "safety_status": "PASS" if safety_pass else "FAIL",
        "checks": checks,
        "no_hollow_ai_reviews_24h": hollow_reviews_24h == 0,
        "hollow_ai_reviews_24h": hollow_reviews_24h,
        "invalid_completed_ai_reviews_24h": invalid_completed_24h,
        "legacy_hollow_reviews_24h": int(review_counts.legacy or 0),
        "failed_ai_reviews_24h": int(review_counts.failed or 0),
        "last_real_ai_review": ({
            "id": str(last_real_row.id), "status": last_real_row.status,
            "requested_at": last_real_row.requested_at.isoformat(),
            "completed_at": last_real_row.completed_at.isoformat(),
            "model_name": last_real_row.model_name,
            "tokens_input": last_real_row.tokens_input,
            "tokens_output": last_real_row.tokens_output,
            "summary": last_real_row.summary,
        } if last_real_row else None),
        "profile_counts": {
            "total": p.total_profiles if p else 0,
            "live_enabled": p.live_enabled_count if p else 0,
            "autopilot_enabled": p.autopilot_enabled_count if p else 0,
        },
    }

@router.get("/export")
async def export_adjustments(
    db: AsyncSession = Depends(get_db),
    _uid: str = Depends(get_current_user_id),
    fmt: str = Query("csv", regex="^(csv|json)$"),
    profile_id: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
):
    params: dict = {"limit": limit}
    pid_filter = "AND s.profile_id = :pid" if profile_id else ""
    if profile_id:
        params["pid"] = profile_id

    rows = await db.execute(text(f"""
        SELECT
          s.id AS suggestion_id, s.profile_id, s.profile_name,
          s.suggestion_type, s.target_section, s.target_field,
          s.confidence, s.status, s.mutation_applied,
          s.reason, s.created_at
        FROM profile_adjustment_suggestions s
        WHERE 1=1 {pid_filter}
        ORDER BY s.confidence DESC NULLS LAST, s.created_at DESC
        LIMIT :limit
    """), params)
    items = rows.fetchall()

    if fmt == "json":
        data = [
            {
                "suggestion_id": str(r.suggestion_id),
                "profile_id": str(r.profile_id),
                "profile_name": r.profile_name,
                "suggestion_type": r.suggestion_type,
                "target_section": r.target_section,
                "target_field": r.target_field,
                "confidence": _safe_float(r.confidence),
                "status": r.status,
                "mutation_applied": r.mutation_applied,
                "reason": r.reason,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in items
        ]
        content = json.dumps(data, indent=2, ensure_ascii=False)
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=calibration_adjustments.json"},
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "suggestion_id", "profile_id", "profile_name", "suggestion_type",
        "target_section", "target_field", "confidence", "status",
        "mutation_applied", "reason", "created_at",
    ])
    for r in items:
        writer.writerow([
            str(r.suggestion_id), str(r.profile_id), r.profile_name,
            r.suggestion_type, r.target_section, r.target_field,
            _safe_float(r.confidence), r.status, r.mutation_applied,
            r.reason,
            r.created_at.isoformat() if r.created_at else None,
        ])
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.read().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=calibration_adjustments.csv"},
    )

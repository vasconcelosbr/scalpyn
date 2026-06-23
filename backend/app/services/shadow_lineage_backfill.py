"""Shadow Lineage Backfill — popula colunas de lineage em shadow_trades históricos.

Estratégia:
  L3_LAB: profile_id JOIN pipeline_watchlists → confidence='JOIN_PROFILE_UNIQUE'
           se 1 watchlist por profile, 'AMBIGUOUS_PROFILE' se > 1.
  L3 canônico (profile_id IS NULL): confidence='LEGACY_UNKNOWN' — não há dados
           suficientes para resolver sem watchlist_id no shadow original.

Modos:
  dry_run=True  → retorna preview sem escrever nada (padrão)
  dry_run=False → executa UPDATE (máx `limit` rows por chamada, idempotente)

Idempotência: filtra WHERE lineage_confidence IS NULL para não sobrescrever
linhas já preenchidas (inclusive as criadas inline pelo pipeline_scan com
lineage_confidence='EXACT').
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


async def preview_lineage_backfill(
    db: Any,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Retorna contagens do que seria preenchido sem escrever nada."""
    return await _run_backfill(db, dry_run=True, limit=None, user_id=user_id)


async def apply_lineage_backfill(
    db: Any,
    limit: int = 5000,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Executa UPDATE nas linhas elegíveis (máx `limit` por chamada)."""
    return await _run_backfill(db, dry_run=False, limit=limit, user_id=user_id)


async def _run_backfill(
    db: Any,
    dry_run: bool,
    limit: Optional[int],
    user_id: Optional[str],
) -> Dict[str, Any]:
    from sqlalchemy import text

    _uid_filter = "AND st.user_id = :uid" if user_id else ""
    _uid_params: Dict[str, Any] = {"uid": user_id} if user_id else {}

    # ── Count candidates ──────────────────────────────────────────────────
    count_sql = text(f"""
        SELECT
            COUNT(*) FILTER (
                WHERE st.source = 'L3_LAB'
                  AND st.profile_id IS NOT NULL
            ) AS l3_lab_resolvable,
            COUNT(*) FILTER (
                WHERE st.source IN ('L3', 'L3_REJECTED', 'L3_SIMULATED')
                  AND st.profile_id IS NULL
            ) AS l3_legacy_unknown,
            COUNT(*) FILTER (
                WHERE st.source = 'L3'
                  AND st.profile_id IS NOT NULL
            ) AS l3_with_profile,
            COUNT(*) AS total_unresolved
        FROM shadow_trades st
        WHERE st.lineage_confidence IS NULL
        {_uid_filter}
    """)
    row = (await db.execute(count_sql, _uid_params)).fetchone()
    l3_lab_resolvable = row.l3_lab_resolvable if row else 0
    l3_legacy_unknown = row.l3_legacy_unknown if row else 0
    l3_with_profile = row.l3_with_profile if row else 0
    total_unresolved = row.total_unresolved if row else 0

    result: Dict[str, Any] = {
        "dry_run": dry_run,
        "total_unresolved": total_unresolved,
        "l3_lab_resolvable": l3_lab_resolvable,
        "l3_with_profile": l3_with_profile,
        "l3_legacy_unknown": l3_legacy_unknown,
        "updated_l3_lab": 0,
        "updated_l3_legacy": 0,
        "errors": [],
    }

    if dry_run:
        return result

    _resolved_at = datetime.now(timezone.utc)

    # ── L3_LAB: resolve via profile_id JOIN ───────────────────────────────
    # For each distinct profile_id in shadow_trades, count matching watchlists.
    # If exactly 1 → EXACT (stored as JOIN_PROFILE_UNIQUE); if > 1 → AMBIGUOUS_PROFILE.
    try:
        _limit_clause = f"LIMIT {int(limit)}" if limit else ""
        lab_sql = text(f"""
            WITH profile_wl AS (
                SELECT
                    pw.profile_id,
                    MIN(pw.id::text) AS watchlist_id,
                    MIN(pw.name) AS watchlist_name,
                    MIN(pw.level) AS watchlist_level,
                    MIN(pw.source_watchlist_id::text) AS source_watchlist_id,
                    COUNT(*) AS wl_count
                FROM pipeline_watchlists pw
                WHERE pw.profile_id IS NOT NULL
                GROUP BY pw.profile_id
            )
            UPDATE shadow_trades st
            SET
                watchlist_id = CASE
                    WHEN pwl.wl_count = 1 THEN CAST(pwl.watchlist_id AS UUID)
                    ELSE NULL
                END,
                watchlist_name = CASE
                    WHEN pwl.wl_count = 1 THEN pwl.watchlist_name
                    ELSE NULL
                END,
                watchlist_level = CASE
                    WHEN pwl.wl_count = 1 THEN pwl.watchlist_level
                    ELSE NULL
                END,
                source_watchlist_id = CASE
                    WHEN pwl.wl_count = 1 AND pwl.source_watchlist_id IS NOT NULL
                        THEN CAST(pwl.source_watchlist_id AS UUID)
                    ELSE NULL
                END,
                lineage_confidence = CASE
                    WHEN pwl.wl_count = 1 THEN 'JOIN_PROFILE_UNIQUE'
                    ELSE 'AMBIGUOUS_PROFILE'
                END,
                lineage_source = 'backfill',
                lineage_resolved_at = :resolved_at
            FROM profile_wl pwl
            WHERE st.profile_id = pwl.profile_id
              AND st.source = 'L3_LAB'
              AND st.lineage_confidence IS NULL
              {_uid_filter.replace('st.user_id', 'st.user_id')}
            {_limit_clause}
        """)
        lab_res = await db.execute(lab_sql, {**_uid_params, "resolved_at": _resolved_at})
        result["updated_l3_lab"] = lab_res.rowcount or 0
    except Exception as exc:
        logger.exception("[lineage-backfill] L3_LAB update failed: %s", exc)
        result["errors"].append(f"l3_lab: {exc}")

    # ── L3 canonical (profile_id IS NULL): mark LEGACY_UNKNOWN ───────────
    try:
        legacy_sql = text(f"""
            UPDATE shadow_trades st
            SET
                lineage_confidence = 'LEGACY_UNKNOWN',
                lineage_source = 'backfill',
                lineage_resolved_at = :resolved_at
            WHERE st.lineage_confidence IS NULL
              AND st.source IN ('L3', 'L3_REJECTED', 'L3_SIMULATED')
              AND st.profile_id IS NULL
              {_uid_filter}
            {f"LIMIT {int(limit)}" if limit else ""}
        """)
        leg_res = await db.execute(legacy_sql, {**_uid_params, "resolved_at": _resolved_at})
        result["updated_l3_legacy"] = leg_res.rowcount or 0
    except Exception as exc:
        logger.exception("[lineage-backfill] L3_LEGACY update failed: %s", exc)
        result["errors"].append(f"l3_legacy: {exc}")

    logger.info(
        "[lineage-backfill] done: l3_lab=%d l3_legacy=%d dry_run=%s",
        result["updated_l3_lab"], result["updated_l3_legacy"], dry_run,
    )
    return result


async def get_lineage_coverage(db: Any, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Retorna estatísticas de cobertura de lineage para o dashboard."""
    from sqlalchemy import text

    _uid_filter = "WHERE st.user_id = :uid" if user_id else ""
    _uid_params: Dict[str, Any] = {"uid": user_id} if user_id else {}

    coverage_sql = text(f"""
        SELECT
            COUNT(*) AS total,
            COUNT(lineage_confidence) AS with_lineage,
            COUNT(*) FILTER (WHERE lineage_confidence = 'EXACT') AS exact_count,
            COUNT(*) FILTER (WHERE lineage_confidence = 'JOIN_PROFILE_UNIQUE') AS join_unique_count,
            COUNT(*) FILTER (WHERE lineage_confidence = 'AMBIGUOUS_PROFILE') AS ambiguous_count,
            COUNT(*) FILTER (WHERE lineage_confidence = 'LEGACY_UNKNOWN') AS legacy_unknown_count,
            COUNT(*) FILTER (WHERE lineage_confidence = 'UNRESOLVED') AS unresolved_count,
            COUNT(*) FILTER (WHERE source = 'L3' AND lineage_confidence IS NOT NULL) AS l3_covered,
            COUNT(*) FILTER (WHERE source = 'L3') AS l3_total,
            COUNT(*) FILTER (WHERE source = 'L3_LAB' AND lineage_confidence IS NOT NULL) AS l3_lab_covered,
            COUNT(*) FILTER (WHERE source = 'L3_LAB') AS l3_lab_total,
            COUNT(*) FILTER (WHERE source = 'L1_SPECTRUM' AND lineage_confidence IS NOT NULL) AS l1_covered,
            COUNT(*) FILTER (WHERE source = 'L1_SPECTRUM') AS l1_total
        FROM shadow_trades st
        {_uid_filter}
    """)
    row = (await db.execute(coverage_sql, _uid_params)).fetchone()
    if row is None:
        return {"total": 0, "with_lineage": 0, "coverage_pct": 0.0}

    total = row.total or 0
    with_lineage = row.with_lineage or 0
    coverage_pct = round(100.0 * with_lineage / total, 1) if total > 0 else 0.0

    return {
        "total": total,
        "with_lineage": with_lineage,
        "coverage_pct": coverage_pct,
        "by_confidence": {
            "EXACT": row.exact_count or 0,
            "JOIN_PROFILE_UNIQUE": row.join_unique_count or 0,
            "AMBIGUOUS_PROFILE": row.ambiguous_count or 0,
            "LEGACY_UNKNOWN": row.legacy_unknown_count or 0,
            "UNRESOLVED": row.unresolved_count or 0,
        },
        "by_source": {
            "L3": {
                "covered": row.l3_covered or 0,
                "total": row.l3_total or 0,
                "pct": round(100.0 * (row.l3_covered or 0) / row.l3_total, 1) if row.l3_total else 0.0,
            },
            "L3_LAB": {
                "covered": row.l3_lab_covered or 0,
                "total": row.l3_lab_total or 0,
                "pct": round(100.0 * (row.l3_lab_covered or 0) / row.l3_lab_total, 1) if row.l3_lab_total else 0.0,
            },
            "L1_SPECTRUM": {
                "covered": row.l1_covered or 0,
                "total": row.l1_total or 0,
                "pct": round(100.0 * (row.l1_covered or 0) / row.l1_total, 1) if row.l1_total else 0.0,
            },
        },
    }

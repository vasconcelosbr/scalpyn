"""
Profile Intelligence Service — main orchestrator for the PI Engine.

Coordinates:
1. ProfilePerformanceAnalyzer   — per-profile shadow trade metrics → profile_metrics
2. IndicatorLiftAnalyzer        — per-bucket lift statistics → profile_indicator_stats
3. RuleContributionAnalyzer     — per-rule win/loss attribution → rule_contribution
4. CounterfactualCombinationMiner — seed rule sets evaluated → profile_rule_combinations
5. DynamicCombinationGenerator  — indicator-bucket combinations → profile_rule_combinations
6. AssociationRulesEngine       — (optional, lazy import)
7. OptunaProfileSearchService   — (optional, lazy import)
8. ProfileSuggestionService     — top combinations → profile_suggestions
9. ProfileAIExplanationService  — (optional, lazy import)
"""
import hashlib
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile_intelligence import ProfileIntelligenceRun
from ..models.profile_metrics import ProfileMetrics
from ..models.rule_contribution import RuleContribution
from .profile_intelligence_audit_service import log_pi_event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ProfilePerformanceAnalyzer
# ---------------------------------------------------------------------------

class ProfilePerformanceAnalyzer:
    """Aggregates per-profile shadow trade performance metrics."""

    async def analyze(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        lookback_days: int,
        min_closed_trades: int,
        profile_ids: Optional[list] = None,
    ) -> List[dict]:
        """
        Load shadow trades grouped by profile, compute metrics, upsert into
        profile_metrics, and return sorted list by win_rate DESC.
        """
        logger.info(
            "[PerfAnalyzer] Analyzing profiles for user=%s lookback=%d days",
            user_id, lookback_days,
        )

        # asyncpg does NOT support INTERVAL ':days days' with parameter substitution
        # so we use Python f-string for the interval part only; uid stays as a parameter.
        sql = text(f"""
            SELECT
                profile_id,
                profile_name,
                source,
                COUNT(*) AS total_trades,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS closed_trades,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS wins,
                COUNT(*) FILTER (WHERE outcome = 'SL_HIT') AS losses,
                COUNT(*) FILTER (WHERE outcome = 'TIMEOUT') AS timeouts,
                AVG(pnl_pct) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS avg_pnl_pct,
                SUM(pnl_pct) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS pnl_total_pct,
                AVG(holding_seconds) AS avg_holding_seconds,
                AVG(holding_seconds) FILTER (WHERE outcome = 'TP_HIT') AS avg_winner_holding_seconds,
                AVG(mae_pct) AS avg_mae_pct,
                AVG(mfe_pct) AS avg_mfe_pct,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND holding_seconds <= 900) AS tp_15m,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND holding_seconds <= 1800) AS tp_30m,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND holding_seconds <= 3600) AS tp_60m
            FROM shadow_trades
            WHERE user_id = :uid
              AND created_at >= NOW() - INTERVAL '{lookback_days} days'
              AND profile_id IS NOT NULL
            GROUP BY profile_id, profile_name, source
        """)

        rows = (await db.execute(sql, {"uid": str(user_id)})).fetchall()

        if not rows:
            logger.info("[PerfAnalyzer] No profile shadow trades found.")
            return []

        results = []
        for row in rows:
            if profile_ids and str(row.profile_id) not in [str(p) for p in profile_ids]:
                continue

            closed = int(row.closed_trades or 0)
            wins = int(row.wins or 0)
            losses = int(row.losses or 0)
            timeouts = int(row.timeouts or 0)
            total = int(row.total_trades or 0)
            open_trades = total - closed

            denom = max(closed, 1)
            win_rate = wins / denom
            loss_rate = losses / denom

            tp_15m = int(row.tp_15m or 0)
            tp_30m = int(row.tp_30m or 0)
            tp_60m = int(row.tp_60m or 0)
            tp_15m_rate = tp_15m / denom
            tp_30m_rate = tp_30m / denom
            tp_60m_rate = tp_60m / denom

            avg_pnl_pct = float(row.avg_pnl_pct or 0.0)
            pnl_total_pct = float(row.pnl_total_pct or 0.0)
            avg_holding = float(row.avg_holding_seconds or 0.0)
            avg_winner_holding = float(row.avg_winner_holding_seconds or 0.0)
            avg_mae_pct = float(row.avg_mae_pct or 0.0)
            avg_mfe_pct = float(row.avg_mfe_pct or 0.0)

            if closed < 30:
                confidence_level = "LOW"
            elif closed < 100:
                confidence_level = "MEDIUM"
            else:
                confidence_level = "HIGH"

            p = {
                "profile_id": row.profile_id,
                "profile_name": row.profile_name,
                "source": row.source,
                "total_trades": total,
                "closed_trades": closed,
                "open_trades": open_trades,
                "wins": wins,
                "losses": losses,
                "timeouts": timeouts,
                "win_rate": win_rate,
                "loss_rate": loss_rate,
                "pnl_total_pct": pnl_total_pct,
                "avg_pnl_pct": avg_pnl_pct,
                "avg_holding_seconds": avg_holding,
                "avg_winner_holding_seconds": avg_winner_holding,
                "avg_mae_pct": avg_mae_pct,
                "avg_mfe_pct": avg_mfe_pct,
                "tp_15m_rate": tp_15m_rate,
                "tp_30m_rate": tp_30m_rate,
                "tp_60m_rate": tp_60m_rate,
                "confidence_level": confidence_level,
            }
            results.append(p)

            # Upsert into profile_metrics
            # Check if a recent row exists for this profile
            existing = (
                await db.execute(
                    text("""
                        SELECT id FROM profile_metrics
                        WHERE user_id = :uid AND profile_id = :pid
                        ORDER BY calculated_at DESC
                        LIMIT 1
                    """),
                    {"uid": str(user_id), "pid": str(row.profile_id)},
                )
            ).fetchone()

            now_utc = datetime.now(timezone.utc)
            lookback_start = now_utc - timedelta(days=lookback_days)

            if existing:
                await db.execute(
                    text("""
                        UPDATE profile_metrics SET
                            profile_name = :profile_name,
                            source = :source,
                            period_start = :period_start,
                            period_end = :period_end,
                            total_trades = :total_trades,
                            closed_trades = :closed_trades,
                            open_trades = :open_trades,
                            wins = :wins,
                            losses = :losses,
                            timeouts = :timeouts,
                            win_rate = :win_rate,
                            pnl_total_pct = :pnl_total_pct,
                            avg_pnl_pct = :avg_pnl_pct,
                            avg_holding_seconds = :avg_holding_seconds,
                            avg_winner_holding_seconds = :avg_winner_holding_seconds,
                            avg_mae_pct = :avg_mae_pct,
                            avg_mfe_pct = :avg_mfe_pct,
                            tp_15m_rate = :tp_15m_rate,
                            tp_30m_rate = :tp_30m_rate,
                            tp_60m_rate = :tp_60m_rate,
                            confidence_level = :confidence_level,
                            calculated_at = :calculated_at
                        WHERE id = :id
                    """),
                    {
                        "id": existing.id,
                        "profile_name": row.profile_name,
                        "source": row.source,
                        "period_start": lookback_start,
                        "period_end": now_utc,
                        "total_trades": total,
                        "closed_trades": closed,
                        "open_trades": open_trades,
                        "wins": wins,
                        "losses": losses,
                        "timeouts": timeouts,
                        "win_rate": win_rate,
                        "pnl_total_pct": pnl_total_pct,
                        "avg_pnl_pct": avg_pnl_pct,
                        "avg_holding_seconds": avg_holding,
                        "avg_winner_holding_seconds": avg_winner_holding,
                        "avg_mae_pct": avg_mae_pct,
                        "avg_mfe_pct": avg_mfe_pct,
                        "tp_15m_rate": tp_15m_rate,
                        "tp_30m_rate": tp_30m_rate,
                        "tp_60m_rate": tp_60m_rate,
                        "confidence_level": confidence_level,
                        "calculated_at": now_utc,
                    },
                )
            else:
                pm = ProfileMetrics(
                    user_id=user_id,
                    profile_id=row.profile_id,
                    profile_name=row.profile_name,
                    source=row.source,
                    period_start=lookback_start,
                    period_end=now_utc,
                    total_trades=total,
                    closed_trades=closed,
                    open_trades=open_trades,
                    wins=wins,
                    losses=losses,
                    timeouts=timeouts,
                    win_rate=win_rate,
                    pnl_total_pct=pnl_total_pct,
                    avg_pnl_pct=avg_pnl_pct,
                    avg_holding_seconds=avg_holding,
                    avg_winner_holding_seconds=avg_winner_holding,
                    avg_mae_pct=avg_mae_pct,
                    avg_mfe_pct=avg_mfe_pct,
                    tp_15m_rate=tp_15m_rate,
                    tp_30m_rate=tp_30m_rate,
                    tp_60m_rate=tp_60m_rate,
                    confidence_level=confidence_level,
                )
                db.add(pm)

        await db.flush()

        results.sort(key=lambda x: x["win_rate"], reverse=True)
        logger.info("[PerfAnalyzer] Analyzed %d profiles.", len(results))
        return results


# ---------------------------------------------------------------------------
# RuleContributionAnalyzer
# ---------------------------------------------------------------------------

class RuleContributionAnalyzer:
    """Builds per-rule win/loss attribution from rules_snapshot in closed shadow trades."""

    async def analyze(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        lookback_days: int,
    ) -> List[dict]:
        logger.info(
            "[RuleContrib] Analyzing rule contributions for user=%s lookback=%d",
            user_id, lookback_days,
        )

        # Load trades in batches of 10,000
        batch_size = 10_000
        offset = 0
        all_trades: List[dict] = []

        while True:
            batch_sql = text(f"""
                SELECT
                    profile_id,
                    outcome,
                    pnl_pct,
                    mae_pct,
                    mfe_pct,
                    rules_snapshot
                FROM shadow_trades
                WHERE user_id = :uid
                  AND created_at >= NOW() - INTERVAL '{lookback_days} days'
                  AND profile_id IS NOT NULL
                  AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
                  AND rules_snapshot IS NOT NULL
                ORDER BY created_at
                LIMIT {batch_size} OFFSET {offset}
            """)
            batch = (await db.execute(batch_sql, {"uid": str(user_id)})).fetchall()
            if not batch:
                break
            for row in batch:
                rules_snapshot = row.rules_snapshot
                if isinstance(rules_snapshot, str):
                    try:
                        rules_snapshot = json.loads(rules_snapshot)
                    except Exception:
                        rules_snapshot = {}
                if not isinstance(rules_snapshot, dict):
                    rules_snapshot = {}
                all_trades.append({
                    "profile_id": row.profile_id,
                    "outcome": row.outcome or "",
                    "is_win": (row.outcome or "") == "TP_HIT",
                    "is_loss": (row.outcome or "") == "SL_HIT",
                    "pnl_pct": float(row.pnl_pct or 0.0),
                    "mae_pct": float(row.mae_pct or 0.0),
                    "mfe_pct": float(row.mfe_pct or 0.0),
                    "rules_snapshot": rules_snapshot,
                })
            if len(batch) < batch_size:
                break
            offset += batch_size

        if not all_trades:
            logger.info("[RuleContrib] No trades with rules_snapshot found.")
            return []

        logger.info("[RuleContrib] Processing %d trades for rule contribution.", len(all_trades))

        # Aggregate by rule_hash
        rule_buckets: Dict[str, dict] = {}

        for trade in all_trades:
            rules_snapshot = trade["rules_snapshot"]
            extracted = _extract_rules_from_snapshot(rules_snapshot)
            for rule_info in extracted:
                rule_hash = _build_rule_hash(
                    rule_info["rule_type"],
                    rule_info["indicator"],
                    rule_info["operator"],
                    rule_info["value_text"],
                    trade["profile_id"],
                )
                if rule_hash not in rule_buckets:
                    rule_buckets[rule_hash] = {
                        "rule_hash": rule_hash,
                        "rule_type": rule_info["rule_type"],
                        "indicator": rule_info["indicator"],
                        "operator": rule_info["operator"],
                        "value_text": rule_info["value_text"],
                        "profile_id": trade["profile_id"],
                        "total": 0, "wins": 0, "losses": 0,
                        "pnl_sum": 0.0, "mae_sum": 0.0, "mfe_sum": 0.0,
                    }
                rb = rule_buckets[rule_hash]
                rb["total"] += 1
                if trade["is_win"]:
                    rb["wins"] += 1
                elif trade["is_loss"]:
                    rb["losses"] += 1
                rb["pnl_sum"] += trade["pnl_pct"]
                rb["mae_sum"] += trade["mae_pct"]
                rb["mfe_sum"] += trade["mfe_pct"]

        results = []
        now_utc = datetime.now(timezone.utc)

        for rule_hash, rb in rule_buckets.items():
            total = rb["total"]
            wins = rb["wins"]
            losses = rb["losses"]
            win_rate = wins / max(total, 1)
            avg_pnl_pct = rb["pnl_sum"] / total if total > 0 else 0.0
            avg_mae_pct = rb["mae_sum"] / total if total > 0 else 0.0
            avg_mfe_pct = rb["mfe_sum"] / total if total > 0 else 0.0

            rc = RuleContribution(
                user_id=user_id,
                profile_id=rb["profile_id"],
                rule_hash=rule_hash,
                rule_type=rb["rule_type"],
                indicator=rb["indicator"],
                operator=rb["operator"],
                value_text=rb["value_text"],
                total_cases=total,
                wins=wins,
                losses=losses,
                win_rate=win_rate,
                avg_pnl_pct=avg_pnl_pct,
                avg_mae_pct=avg_mae_pct,
                avg_mfe_pct=avg_mfe_pct,
                calculated_at=now_utc,
            )
            db.add(rc)
            results.append({
                "rule_hash": rule_hash,
                "indicator": rb["indicator"],
                "win_rate": win_rate,
                "total_cases": total,
            })

        if results:
            await db.flush()

        logger.info("[RuleContrib] Saved %d rule contribution rows.", len(results))
        return results


def _extract_rules_from_snapshot(snapshot: dict) -> list:
    """Extract individual rules from a rules_snapshot JSONB dict."""
    rules = []
    if not isinstance(snapshot, dict):
        return rules

    # Common snapshot shapes: flat list, dict with keys signals/blocks/entry_triggers
    for section_key in ("signals", "block_rules", "entry_triggers", "rules"):
        section = snapshot.get(section_key, [])
        if isinstance(section, dict):
            section = section.get("conditions", section.get("blocks", []))
        if isinstance(section, list):
            for rule in section:
                if isinstance(rule, dict):
                    indicator = rule.get("indicator") or rule.get("field", "")
                    operator = rule.get("operator", "")
                    value = rule.get("value", "")
                    if indicator:
                        rules.append({
                            "rule_type": section_key,
                            "indicator": str(indicator),
                            "operator": str(operator),
                            "value_text": str(value),
                        })

    # Fallback: flat list of rules at top level
    if not rules and isinstance(snapshot, list):
        for rule in snapshot:
            if isinstance(rule, dict):
                indicator = rule.get("indicator") or rule.get("field", "")
                operator = rule.get("operator", "")
                value = rule.get("value", "")
                if indicator:
                    rules.append({
                        "rule_type": "rule",
                        "indicator": str(indicator),
                        "operator": str(operator),
                        "value_text": str(value),
                    })

    return rules


def _build_rule_hash(rule_type: str, indicator: str, operator: str, value_text: str, profile_id) -> str:
    raw = f"{rule_type}|{indicator}|{operator}|{value_text}|{profile_id}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------

class ProfileIntelligenceService:
    """
    Full PI Engine orchestrator. Call run() to execute all analyzers and
    return the run_id for polling / UI display.
    """

    ENGINE_VERSION = "2B.1"

    async def run(
        self,
        db: AsyncSession,
        user_id: UUID,
        lookback_days: int = 60,
        min_closed_trades: int = 30,
        include_counterfactual: bool = True,
        include_dynamic_combinations: bool = True,
        include_association_rules: bool = False,
        include_optuna: bool = False,
        include_ai_explanation: bool = False,
        profiles_filter: Optional[list] = None,
        max_combinations: int = 500,
        settings_override: Optional[dict] = None,
        run_id: Optional[UUID] = None,
        trigger_source: Optional[str] = None,
    ) -> UUID:
        """
        Runs the full PI Engine. Returns the run_id.
        If run_id is provided (API path), updates the pre-created run record.
        If run_id is None (Celery path), creates a new run record.
        Each sub-service is wrapped in try/except so a single failure
        does not abort the whole run.
        """
        now_utc = datetime.now(timezone.utc)

        # ------------------------------------------------------------------
        # 1. Create or adopt PIRun row with status='running'
        # ------------------------------------------------------------------
        if run_id is not None:
            # API path: a "queued" run was pre-created — update it in-place
            await db.execute(
                text("""
                    UPDATE profile_intelligence_runs SET
                        status = 'running',
                        run_at = :now,
                        updated_at = :now
                    WHERE id = :run_id AND user_id = :uid
                """),
                {"now": now_utc, "run_id": str(run_id), "uid": str(user_id)},
            )
            await db.flush()
        else:
            # Celery path: create a fresh run record
            pi_run = ProfileIntelligenceRun(
                user_id=user_id,
                run_at=now_utc,
                lookback_days=lookback_days,
                min_closed_trades=min_closed_trades,
                status="running",
                trigger_source=trigger_source,
                engine_version=self.ENGINE_VERSION,
                settings_json=settings_override,
            )
            db.add(pi_run)
            await db.flush()
            run_id = pi_run.id

        logger.info("[PIEngine] Run started: user=%s run_id=%s", user_id, run_id)

        await log_pi_event(
            db, user_id, "run_started",
            event_description=f"PI Engine run started (lookback={lookback_days}d)",
            run_id=run_id,
            payload_json={
                "lookback_days": lookback_days,
                "min_closed_trades": min_closed_trades,
                "engine_version": self.ENGINE_VERSION,
            },
        )

        # ------------------------------------------------------------------
        # 2. Compute base metrics
        # ------------------------------------------------------------------
        error_message = None
        try:
            base_metrics = await self.get_base_metrics(db, user_id, lookback_days)
        except Exception as exc:
            logger.error("[PIEngine] get_base_metrics failed: %s", exc)
            base_metrics = {
                "base_win_rate": 0.0,
                "base_avg_pnl_pct": 0.0,
                "base_tp_30m_rate": 0.0,
                "total_shadow_trades": 0,
                "total_closed_trades": 0,
            }
            error_message = f"base_metrics failed: {exc}"

        total_shadow_trades = base_metrics.get("total_shadow_trades", 0)
        total_closed_trades = base_metrics.get("total_closed_trades", 0)
        base_win_rate = base_metrics.get("base_win_rate", 0.0)
        base_avg_pnl_pct = base_metrics.get("base_avg_pnl_pct", 0.0)
        base_tp_30m_rate = base_metrics.get("base_tp_30m_rate", 0.0)

        # Count opportunity snapshots for this user in the lookback window
        try:
            opp_count = (await db.execute(
                text(f"""
                    SELECT COUNT(*) FROM opportunity_snapshots
                    WHERE user_id = :uid
                      AND created_at >= NOW() - INTERVAL '{lookback_days} days'
                """),
                {"uid": str(user_id)},
            )).scalar()
            total_opp_snapshots = int(opp_count or 0)
        except Exception as _opp_exc:
            logger.warning("[PIEngine] opportunity_snapshots count failed: %s", _opp_exc)
            total_opp_snapshots = 0

        # Update run with base metrics
        await db.execute(
            text("""
                UPDATE profile_intelligence_runs SET
                    total_shadow_trades = :total_shadow_trades,
                    total_closed_trades = :total_closed_trades,
                    total_opportunity_snapshots = :total_opp,
                    base_win_rate = :base_win_rate,
                    base_avg_pnl_pct = :base_avg_pnl_pct,
                    base_tp_30m_rate = :base_tp_30m_rate,
                    updated_at = :now
                WHERE id = :run_id
            """),
            {
                "total_shadow_trades": total_shadow_trades,
                "total_closed_trades": total_closed_trades,
                "total_opp": total_opp_snapshots,
                "base_win_rate": base_win_rate,
                "base_avg_pnl_pct": base_avg_pnl_pct,
                "base_tp_30m_rate": base_tp_30m_rate,
                "now": datetime.now(timezone.utc),
                "run_id": str(run_id),
            },
        )
        await db.flush()

        # ------------------------------------------------------------------
        # 3. Determine discovery/validation time windows (70%/30% split)
        # ------------------------------------------------------------------
        lookback_start = now_utc - timedelta(days=lookback_days)

        # Clamp discovery start to earliest available feature data so that a long
        # lookback_days doesn't produce an empty discovery window when features
        # haven't been collected for the full period yet.
        earliest_features = (
            await db.execute(
                text("""
                    SELECT MIN(created_at)
                    FROM shadow_trades
                    WHERE user_id = :uid
                      AND features_snapshot IS NOT NULL
                      AND features_snapshot != '{}'::jsonb
                """),
                {"uid": str(user_id)},
            )
        ).scalar()

        if earliest_features is not None and earliest_features > lookback_start:
            logger.info(
                "[PIEngine] Clamping discovery_start from %s to %s (earliest features)",
                lookback_start.date(), earliest_features.date(),
            )
            effective_start = earliest_features
        else:
            effective_start = lookback_start

        effective_span_seconds = (now_utc - effective_start).total_seconds()
        discovery_span_seconds = int(effective_span_seconds * 0.70)

        discovery_start = effective_start
        discovery_end = effective_start + timedelta(seconds=discovery_span_seconds)
        validation_start = discovery_end + timedelta(microseconds=1)
        validation_end = now_utc

        # Persist time windows onto the run row
        await db.execute(
            text("""
                UPDATE profile_intelligence_runs SET
                    discovery_start_at = :ds,
                    discovery_end_at = :de,
                    validation_start_at = :vs,
                    validation_end_at = :ve,
                    updated_at = :now
                WHERE id = :run_id
            """),
            {
                "ds": discovery_start,
                "de": discovery_end,
                "vs": validation_start,
                "ve": validation_end,
                "now": datetime.now(timezone.utc),
                "run_id": str(run_id),
            },
        )
        await db.flush()

        # ------------------------------------------------------------------
        # 4. ProfilePerformanceAnalyzer
        # ------------------------------------------------------------------
        profile_results = []
        try:
            analyzer = ProfilePerformanceAnalyzer()
            profile_results = await analyzer.analyze(
                db, user_id, run_id, lookback_days, min_closed_trades,
                profile_ids=profiles_filter,
            )
            await log_pi_event(
                db, user_id, "perf_analyzer_completed",
                run_id=run_id,
                result_json={"profiles_analyzed": len(profile_results)},
            )
        except Exception as exc:
            logger.error("[PIEngine] ProfilePerformanceAnalyzer failed: %s", exc)
            error_message = (error_message or "") + f" perf_analyzer: {exc}"
            await log_pi_event(
                db, user_id, "perf_analyzer_error",
                run_id=run_id,
                result_json={"error": str(exc)},
            )

        # Update total_profiles on run
        if profile_results:
            await db.execute(
                text("""
                    UPDATE profile_intelligence_runs SET
                        total_profiles = :n, updated_at = :now
                    WHERE id = :run_id
                """),
                {"n": len(profile_results), "now": datetime.now(timezone.utc), "run_id": str(run_id)},
            )
            await db.flush()

        # ------------------------------------------------------------------
        # 5. IndicatorLiftAnalyzer
        # ------------------------------------------------------------------
        indicator_stats = []
        try:
            from .indicator_lift_service import IndicatorLiftAnalyzer
            lift_analyzer = IndicatorLiftAnalyzer()
            indicator_stats = await lift_analyzer.analyze(
                db=db,
                user_id=user_id,
                run_id=run_id,
                lookback_days=lookback_days,
                min_closed_trades=min_closed_trades,
                base_win_rate=base_win_rate,
                base_avg_pnl_pct=base_avg_pnl_pct,
                discovery_start=discovery_start,
                discovery_end=discovery_end,
            )
            await log_pi_event(
                db, user_id, "indicator_lift_completed",
                run_id=run_id,
                result_json={"buckets_computed": len(indicator_stats)},
            )
        except Exception as exc:
            logger.error("[PIEngine] IndicatorLiftAnalyzer failed: %s", exc)
            error_message = (error_message or "") + f" indicator_lift: {exc}"
            await log_pi_event(
                db, user_id, "indicator_lift_error",
                run_id=run_id,
                result_json={"error": str(exc)},
            )

        # ------------------------------------------------------------------
        # 6. RuleContributionAnalyzer
        # ------------------------------------------------------------------
        try:
            rule_analyzer = RuleContributionAnalyzer()
            rule_results = await rule_analyzer.analyze(db, user_id, run_id, lookback_days)
            await log_pi_event(
                db, user_id, "rule_contribution_completed",
                run_id=run_id,
                result_json={"rules_computed": len(rule_results)},
            )
        except Exception as exc:
            logger.error("[PIEngine] RuleContributionAnalyzer failed: %s", exc)
            error_message = (error_message or "") + f" rule_contrib: {exc}"
            await log_pi_event(
                db, user_id, "rule_contribution_error",
                run_id=run_id,
                result_json={"error": str(exc)},
            )

        # ------------------------------------------------------------------
        # 7. CounterfactualCombinationMiner (optional)
        # ------------------------------------------------------------------
        seed_results = []
        if include_counterfactual:
            try:
                from .counterfactual_combination_service import CounterfactualCombinationMiner
                miner = CounterfactualCombinationMiner()
                seed_results = await miner.mine_seeds(
                    db=db,
                    user_id=user_id,
                    run_id=run_id,
                    lookback_days=lookback_days,
                    base_metrics=base_metrics,
                    discovery_start=discovery_start,
                    discovery_end=discovery_end,
                    validation_start=validation_start,
                    validation_end=validation_end,
                )
                await log_pi_event(
                    db, user_id, "counterfactual_seeds_completed",
                    run_id=run_id,
                    result_json={"seeds_mined": len(seed_results)},
                )
            except Exception as exc:
                logger.error("[PIEngine] CounterfactualCombinationMiner failed: %s", exc)
                error_message = (error_message or "") + f" cf_miner: {exc}"
                await log_pi_event(
                    db, user_id, "counterfactual_seeds_error",
                    run_id=run_id,
                    result_json={"error": str(exc)},
                )

        # ------------------------------------------------------------------
        # 8. DynamicCombinationGenerator (optional)
        # ------------------------------------------------------------------
        if include_dynamic_combinations and indicator_stats:
            try:
                from .counterfactual_combination_service import DynamicCombinationGenerator
                dyn_gen = DynamicCombinationGenerator()
                dyn_results = await dyn_gen.generate(
                    db=db,
                    user_id=user_id,
                    run_id=run_id,
                    base_metrics=base_metrics,
                    indicator_stats=indicator_stats,
                    discovery_start=discovery_start,
                    discovery_end=discovery_end,
                    validation_start=validation_start,
                    validation_end=validation_end,
                    max_combinations=max_combinations,
                )
                await log_pi_event(
                    db, user_id, "dynamic_combinations_completed",
                    run_id=run_id,
                    result_json={"combinations_generated": len(dyn_results)},
                )
            except Exception as exc:
                logger.error("[PIEngine] DynamicCombinationGenerator failed: %s", exc)
                error_message = (error_message or "") + f" dyn_comb: {exc}"
                await log_pi_event(
                    db, user_id, "dynamic_combinations_error",
                    run_id=run_id,
                    result_json={"error": str(exc)},
                )

        # ------------------------------------------------------------------
        # 9. AssociationRulesEngine (optional)
        # Each optional phase runs in a SAVEPOINT so a failure here cannot
        # abort the outer transaction and kill subsequent phases.
        # ------------------------------------------------------------------
        if include_association_rules:
            try:
                async with db.begin_nested():
                    from .association_rules_service import AssociationRulesEngine
                    assoc_engine = AssociationRulesEngine()
                    await assoc_engine.run(
                        db=db,
                        user_id=user_id,
                        run_id=run_id,
                        lookback_days=lookback_days,
                        base_metrics=base_metrics,
                        discovery_start=discovery_start,
                        discovery_end=discovery_end,
                        validation_start=validation_start,
                        validation_end=validation_end,
                    )
                await log_pi_event(
                    db, user_id, "association_rules_completed",
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("[PIEngine] AssociationRulesEngine skipped/failed: %s", exc)
                try:
                    await log_pi_event(
                        db, user_id, "association_rules_error",
                        run_id=run_id,
                        result_json={"error": str(exc)},
                    )
                except Exception:
                    pass

        # ------------------------------------------------------------------
        # 10. OptunaProfileSearchService (optional)
        # ------------------------------------------------------------------
        if include_optuna and total_closed_trades >= min_closed_trades:
            try:
                async with db.begin_nested():
                    from .optuna_profile_search_service import OptunaProfileSearchService
                    optuna_svc = OptunaProfileSearchService()
                    await optuna_svc.search(
                        db=db,
                        user_id=user_id,
                        run_id=run_id,
                        lookback_days=lookback_days,
                        base_metrics=base_metrics,
                        discovery_start=discovery_start,
                        discovery_end=discovery_end,
                        validation_start=validation_start,
                        validation_end=validation_end,
                    )
                await log_pi_event(
                    db, user_id, "optuna_completed",
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("[PIEngine] OptunaProfileSearchService skipped/failed: %s", exc)
                try:
                    await log_pi_event(
                        db, user_id, "optuna_error",
                        run_id=run_id,
                        result_json={"error": str(exc)},
                    )
                except Exception:
                    pass

        # ------------------------------------------------------------------
        # 11. ProfileSuggestionService
        # ------------------------------------------------------------------
        suggestions = []
        try:
            from .profile_suggestion_service import ProfileSuggestionService
            sugg_svc = ProfileSuggestionService()
            suggestions = await sugg_svc.generate_suggestions(
                db=db,
                user_id=user_id,
                run_id=run_id,
                base_metrics=base_metrics,
            )
            await log_pi_event(
                db, user_id, "suggestions_completed",
                run_id=run_id,
                result_json={"suggestions_generated": len(suggestions)},
            )
        except Exception as exc:
            logger.error("[PIEngine] ProfileSuggestionService failed: %s", exc)
            error_message = (error_message or "") + f" suggestions: {exc}"
            await log_pi_event(
                db, user_id, "suggestions_error",
                run_id=run_id,
                result_json={"error": str(exc)},
            )

        # ------------------------------------------------------------------
        # 12. ProfileAIExplanationService (optional)
        # ------------------------------------------------------------------
        if include_ai_explanation and suggestions:
            try:
                from .profile_ai_explanation_service import ProfileAIExplanationService
                ai_svc = ProfileAIExplanationService()
                for sugg_dict in suggestions:
                    await ai_svc.explain_suggestion(
                        db=db,
                        user_id=user_id,
                        suggestion_id=sugg_dict["id"],
                        run_id=run_id,
                    )
                await log_pi_event(
                    db, user_id, "ai_explanation_completed",
                    run_id=run_id,
                )
            except Exception as exc:
                logger.warning("[PIEngine] ProfileAIExplanationService skipped/failed: %s", exc)
                await log_pi_event(
                    db, user_id, "ai_explanation_error",
                    run_id=run_id,
                    result_json={"error": str(exc)},
                )

        # ------------------------------------------------------------------
        # 13. Update PIRun with final counts + status
        # ------------------------------------------------------------------
        final_status = "completed" if not error_message else "completed_with_errors"

        await db.execute(
            text("""
                UPDATE profile_intelligence_runs SET
                    status = :status,
                    error_message = :error_message,
                    updated_at = :now
                WHERE id = :run_id
            """),
            {
                "status": final_status,
                "error_message": error_message,
                "now": datetime.now(timezone.utc),
                "run_id": str(run_id),
            },
        )
        await db.flush()

        await log_pi_event(
            db, user_id, "run_finished",
            event_description=f"PI Engine run finished with status={final_status}",
            run_id=run_id,
            result_json={
                "status": final_status,
                "profiles_analyzed": len(profile_results),
                "indicator_buckets": len(indicator_stats),
                "seed_combinations": len(seed_results),
                "suggestions": len(suggestions),
            },
        )

        logger.info(
            "[PIEngine] Run complete: run_id=%s status=%s profiles=%d suggestions=%d",
            run_id, final_status, len(profile_results), len(suggestions),
        )
        await db.commit()
        return run_id

    async def get_base_metrics(
        self,
        db: AsyncSession,
        user_id: UUID,
        lookback_days: int,
    ) -> dict:
        """
        Compute base win_rate, avg_pnl_pct, tp_30m_rate from ALL closed shadow
        trades for this user in the lookback window.
        """
        sql = text(f"""
            SELECT
                COUNT(*) AS total_shadow_trades,
                COUNT(*) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS total_closed_trades,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT') AS total_wins,
                AVG(pnl_pct) FILTER (WHERE outcome IN ('TP_HIT','SL_HIT','TIMEOUT')) AS avg_pnl_pct,
                COUNT(*) FILTER (WHERE outcome = 'TP_HIT' AND holding_seconds <= 1800) AS tp_30m_wins
            FROM shadow_trades
            WHERE user_id = :uid
              AND created_at >= NOW() - INTERVAL '{lookback_days} days'
        """)
        row = (await db.execute(sql, {"uid": str(user_id)})).fetchone()

        total_shadow = int(row.total_shadow_trades or 0)
        total_closed = int(row.total_closed_trades or 0)
        total_wins = int(row.total_wins or 0)
        avg_pnl_pct = float(row.avg_pnl_pct or 0.0)
        tp_30m_wins = int(row.tp_30m_wins or 0)

        denom = max(total_closed, 1)
        base_win_rate = total_wins / denom
        base_tp_30m_rate = tp_30m_wins / denom

        return {
            "total_shadow_trades": total_shadow,
            "total_closed_trades": total_closed,
            "total_wins": total_wins,
            "base_win_rate": base_win_rate,
            "base_avg_pnl_pct": avg_pnl_pct,
            "base_tp_30m_rate": base_tp_30m_rate,
        }

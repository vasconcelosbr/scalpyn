"""
Profile Suggestion Service — generates actionable profile suggestions from
top-scoring rule combinations discovered by the PI Engine.

Also exports two module-level utility functions used by sibling services:
  - calculate_champion_score(metrics, base_metrics, settings=None)
  - detect_overfit_risk(discovery_metrics, validation_metrics, total_cases, n_rules)
"""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.profile_intelligence import ProfileRuleCombination, ProfileSuggestion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Champion score calculation
# ---------------------------------------------------------------------------

def calculate_champion_score(metrics, base_metrics, settings=None) -> float:
    """
    Compute champion score (0-100) from metrics and base_metrics.

    Parameters
    ----------
    metrics : object or dict-like with attributes:
        win_rate, tp_30m_rate, avg_pnl_pct, avg_mae_pct,
        total_cases, degradation_pct (optional)
    base_metrics : object or dict-like with attribute base_win_rate
    settings : optional dict of overrides (not used currently)

    Returns
    -------
    float : champion_score in [0, 100] before penalties
    """

    def _get(obj, key, default=0.0):
        if isinstance(obj, dict):
            return obj.get(key, default) or default
        return getattr(obj, key, default) or default

    win_rate = _get(metrics, "win_rate", 0.0)
    tp30m_rate = _get(metrics, "tp_30m_rate", 0.0)
    avg_pnl_pct = _get(metrics, "avg_pnl_pct", 0.0)
    avg_mae_pct = _get(metrics, "avg_mae_pct", 0.0)
    total_cases = _get(metrics, "total_cases", 0)
    degradation_pct = _get(metrics, "degradation_pct", None)
    overfit_risk = _get(metrics, "overfit_risk", False)

    base_win_rate = _get(base_metrics, "base_win_rate", 0.01)
    safe_base_wr = max(float(base_win_rate), 0.01)

    # Win rate component (weight 30%)
    win_rate_score = min(100.0, (float(win_rate) / safe_base_wr) * 40.0)

    # TP 30m component (weight 20%)
    tp30m_score = min(100.0, float(tp30m_rate) * 100.0)

    # Avg PnL component (weight 15%)
    avg_pnl_score = min(100.0, max(0.0, float(avg_pnl_pct) * 50.0 + 50.0))

    # MAE component (weight 15%) — mae is negative, so higher (less negative) is better
    mae_score = min(100.0, max(0.0, 100.0 + float(avg_mae_pct) * 50.0))

    # Support component (weight 10%)
    support_score = min(100.0, (float(total_cases) / 100.0) * 100.0)

    # Stability component (weight 10%)
    if degradation_pct is None:
        stability_score = 0.0
    else:
        stability_score = min(100.0, max(0.0, 100.0 - abs(float(degradation_pct)) * 2.0))

    champion_score = (
        0.30 * win_rate_score
        + 0.20 * tp30m_score
        + 0.15 * avg_pnl_score
        + 0.15 * mae_score
        + 0.10 * support_score
        + 0.10 * stability_score
    )

    # Penalties
    if float(total_cases) < 30:
        champion_score *= 0.5
    if overfit_risk:
        champion_score *= 0.7

    return round(champion_score, 4)


# ---------------------------------------------------------------------------
# Overfit risk detection
# ---------------------------------------------------------------------------

def detect_overfit_risk(
    discovery_metrics: dict,
    validation_metrics: dict,
    total_cases: int,
    n_rules: int,
) -> bool:
    """
    Return True if any overfit risk heuristic fires.

    Heuristics:
    1. degradation_pct > 30
    2. total_cases < 20
    3. discovery win_rate > 0.70 with total_cases < 50
    4. n_rules > 6
    5. discovery win_rate > validation win_rate * 1.3
    """
    d_wr = float(discovery_metrics.get("win_rate", 0.0) or 0.0)
    v_wr = float((validation_metrics or {}).get("win_rate", d_wr) or d_wr)
    total = int(total_cases or 0)
    n_r = int(n_rules or 0)

    # Compute degradation_pct from discovery/validation win rates
    if d_wr > 0 and v_wr < d_wr:
        degradation_pct = ((d_wr - v_wr) / d_wr) * 100.0
    else:
        degradation_pct = 0.0

    if degradation_pct > 30:
        return True
    if total < 20:
        return True
    if d_wr > 0.70 and total < 50:
        return True
    if n_r > 6:
        return True
    if d_wr > 0 and v_wr > 0 and d_wr > v_wr * 1.3:
        return True

    return False


# ---------------------------------------------------------------------------
# Standard block rules for every suggestion
# ---------------------------------------------------------------------------

STANDARD_BLOCK_RULES = [
    {"indicator": "rsi", "operator": ">", "value": 78},
    {"indicator": "zscore", "operator": ">", "value": 2.5},
    {"indicator": "vwap_distance_pct", "operator": ">", "value": 3.0},
    {"indicator": "spread_pct", "operator": ">", "value": 0.30},
    {"indicator": "orderbook_depth_usdt", "operator": "<", "value": 10000},
    {"indicator": "volume_delta", "operator": "<", "value": -20},
    {"indicator": "taker_ratio", "operator": "<", "value": 0.35},
    {"indicator": "atr_pct", "operator": ">", "value": 5.0},
    {"indicator": "atr_pct", "operator": "<", "value": 0.30},
]


# ---------------------------------------------------------------------------
# Suggestion generator
# ---------------------------------------------------------------------------

class ProfileSuggestionService:
    """Generates ProfileSuggestion rows from top-scoring rule combinations."""

    async def generate_suggestions(
        self,
        db: AsyncSession,
        user_id: UUID,
        run_id: UUID,
        base_metrics: dict,
        min_champion_score: float = 40.0,
        max_suggestions: int = 10,
    ) -> List[dict]:
        logger.info(
            "[SuggestionSvc] Generating suggestions for user=%s run=%s", user_id, run_id
        )

        # ------------------------------------------------------------------
        # Load top qualifying combinations
        # ------------------------------------------------------------------
        rows = (
            await db.execute(
                text("""
                    SELECT
                        id,
                        combination_type,
                        setup_family,
                        suggested_name,
                        rules_json,
                        total_cases,
                        wins,
                        losses,
                        timeouts,
                        win_rate,
                        loss_rate,
                        avg_pnl_pct,
                        avg_holding_seconds,
                        avg_winner_holding_seconds,
                        avg_mae_pct,
                        avg_mfe_pct,
                        tp_15m_rate,
                        tp_30m_rate,
                        tp_60m_rate,
                        lift_vs_base,
                        champion_score,
                        confidence_level,
                        degradation_pct,
                        overfit_risk,
                        discovery_metrics_json,
                        validation_metrics_json
                    FROM profile_rule_combinations
                    WHERE user_id = :uid
                      AND run_id = :run_id
                      AND champion_score >= :min_score
                      AND confidence_level IN ('MEDIUM', 'HIGH')
                      AND overfit_risk = FALSE
                    ORDER BY champion_score DESC
                    LIMIT :limit
                """),
                {
                    "uid": str(user_id),
                    "run_id": str(run_id),
                    "min_score": min_champion_score,
                    "limit": max_suggestions * 2,  # fetch extra, dedup by name
                },
            )
        ).fetchall()

        if not rows:
            logger.info("[SuggestionSvc] No qualifying combinations found.")
            return []

        suggestions = []
        seen_names = set()

        for row in rows[:max_suggestions * 2]:
            if len(suggestions) >= max_suggestions:
                break

            comb_name = row.suggested_name or f"combination_{row.id}"
            if comb_name in seen_names:
                continue
            seen_names.add(comb_name)

            try:
                rules_json = row.rules_json
                if isinstance(rules_json, str):
                    import json as _json
                    rules_json = _json.loads(rules_json)
                if not isinstance(rules_json, list):
                    rules_json = []

                disc_metrics = row.discovery_metrics_json or {}
                val_metrics = row.validation_metrics_json or {}
                if isinstance(disc_metrics, str):
                    import json as _json
                    disc_metrics = _json.loads(disc_metrics)
                if isinstance(val_metrics, str):
                    import json as _json
                    val_metrics = _json.loads(val_metrics)

                win_rate = float(row.win_rate or 0.0)
                avg_pnl_pct = float(row.avg_pnl_pct or 0.0)
                avg_mae_pct = float(row.avg_mae_pct or 0.0)
                tp_30m_rate = float(row.tp_30m_rate or 0.0)
                lift_vs_base = float(row.lift_vs_base or 1.0)
                total_cases = int(row.total_cases or 0)
                confidence_level = row.confidence_level or "LOW"
                champion_score = float(row.champion_score or 0.0)
                degradation_pct = float(row.degradation_pct or 0.0)

                base_win_rate = float(base_metrics.get("base_win_rate", 0.0) or 0.0)

                d_win_rate = float(disc_metrics.get("win_rate", win_rate) or win_rate)
                v_win_rate = float(val_metrics.get("win_rate", win_rate) or win_rate)

                # ------------------------------------------------------------------
                # Build suggested_config_json
                # ------------------------------------------------------------------
                signal_conditions = _rules_to_conditions(rules_json)

                suggested_config_json = {
                    "signals": {
                        "logic": "AND",
                        "conditions": signal_conditions,
                    },
                    "entry_triggers": {
                        "logic": "AND",
                        "conditions": [],
                    },
                    "scoring": {
                        "selected_rule_ids": [],
                        "weights": {
                            "liquidity": 25,
                            "momentum": 30,
                            "market_structure": 25,
                            "signal": 20,
                        },
                        "generated_rules": [],
                    },
                    "block_rules": {
                        "blocks": STANDARD_BLOCK_RULES,
                    },
                }

                # ------------------------------------------------------------------
                # Quantitative explanation
                # ------------------------------------------------------------------
                quantitative_explanation = (
                    f"Combinação {comb_name}: {total_cases} trades, "
                    f"{win_rate:.0%} win rate (base: {base_win_rate:.0%}), "
                    f"lift={lift_vs_base:.2f}x, tp_30m_rate={tp_30m_rate:.0%}, "
                    f"avg_pnl={avg_pnl_pct:.3f}%, avg_mae={avg_mae_pct:.3f}%. "
                    f"Confidence: {confidence_level}. "
                    f"Discovery: {d_win_rate:.0%} / Validation: {v_win_rate:.0%}."
                )

                # ------------------------------------------------------------------
                # Evidence summary
                # ------------------------------------------------------------------
                evidence_summary_json = {
                    "total_cases": total_cases,
                    "wins": int(row.wins or 0),
                    "losses": int(row.losses or 0),
                    "timeouts": int(row.timeouts or 0),
                    "win_rate": win_rate,
                    "lift_vs_base": lift_vs_base,
                    "tp_30m_rate": tp_30m_rate,
                    "avg_pnl_pct": avg_pnl_pct,
                    "avg_mae_pct": avg_mae_pct,
                    "champion_score": champion_score,
                    "confidence_level": confidence_level,
                    "discovery": disc_metrics,
                    "validation": val_metrics,
                    "degradation_pct": degradation_pct,
                }

                # ------------------------------------------------------------------
                # Risk notes
                # ------------------------------------------------------------------
                risk_notes_parts = []
                if degradation_pct > 20:
                    risk_notes_parts.append(
                        f"Degradação de {degradation_pct:.1f}% entre descoberta e validação."
                    )
                if total_cases < 50:
                    risk_notes_parts.append(
                        f"Baixo suporte: apenas {total_cases} trades. Aguardar mais dados antes de ativar."
                    )
                if len(rules_json) > 6:
                    risk_notes_parts.append(
                        f"Combinação com {len(rules_json)} regras — risco elevado de overfitting."
                    )
                risk_notes = " ".join(risk_notes_parts) if risk_notes_parts else None

                sugg = ProfileSuggestion(
                    user_id=user_id,
                    run_id=run_id,
                    source_combination_id=row.id,
                    suggested_profile_name=comb_name[:255],
                    suggested_profile_description=quantitative_explanation,
                    suggested_profile_family=row.setup_family,
                    suggested_config_json=suggested_config_json,
                    suggested_signals_json={"logic": "AND", "conditions": signal_conditions},
                    suggested_scoring_json=suggested_config_json["scoring"],
                    suggested_block_rules_json={"blocks": STANDARD_BLOCK_RULES},
                    evidence_summary_json=evidence_summary_json,
                    quantitative_explanation=quantitative_explanation,
                    risk_notes=risk_notes,
                    confidence_score=champion_score,
                    confidence_level=confidence_level,
                    status="pending_user_approval",
                )
                db.add(sugg)
                await db.flush()

                suggestions.append({
                    "id": sugg.id,
                    "name": comb_name,
                    "family": row.setup_family,
                    "champion_score": champion_score,
                    "confidence_level": confidence_level,
                    "win_rate": win_rate,
                    "lift_vs_base": lift_vs_base,
                    "total_cases": total_cases,
                    "quantitative_explanation": quantitative_explanation,
                    "risk_notes": risk_notes,
                    "rules": rules_json,
                })

            except Exception as exc:
                logger.warning(
                    "[SuggestionSvc] Failed to create suggestion for %s: %s",
                    row.suggested_name, exc,
                )
                continue

        logger.info(
            "[SuggestionSvc] Created %d suggestions for run=%s", len(suggestions), run_id
        )
        return suggestions


# ---------------------------------------------------------------------------
# Helper: rules → conditions
# ---------------------------------------------------------------------------

def _rules_to_conditions(rules: list) -> list:
    """Convert rules_json list to signals.conditions format (field instead of indicator)."""
    conditions = []
    for rule in rules:
        indicator = rule.get("indicator") or rule.get("field")
        if not indicator:
            continue
        conditions.append({
            "field": indicator,
            "operator": rule.get("operator", ">="),
            "value": rule.get("value"),
        })
    return conditions

"""
Backtest Skills Engine — Comparative backtesting between current rigid rules
and the new Market Skills Engine.

Uses historical shadow_trades to replay decisions:
  1. For each shadow_trade with known outcome (TP_HIT/SL_HIT):
     a. Reconstruct the regime at entry time
     b. Evaluate with CURRENT RULES (legacy block/filter/trigger system)
     c. Evaluate with MARKET SKILLS (regime → skill → weighted scoring)
     d. Compare: would the decision have been different?
  2. Compute comparative metrics:
     - Win Rate, Profit Factor, Sharpe, Max Drawdown
     - False rejections (profitable trades rejected by current rules)
     - Trades captured by context (approved only by skills)

Author: Market Skills Engine v1
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ── Backtest Result Structures ────────────────────────────────────────────────

@dataclass
class StrategyMetrics:
    """Metrics for a single strategy."""
    name: str
    total_trades: int = 0
    approved_trades: int = 0
    rejected_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown: float = 0.0
    avg_hold_time_minutes: float = 0.0
    false_rejections: int = 0          # profitable trades that were rejected
    correct_rejections: int = 0        # losing trades that were correctly rejected
    false_approvals: int = 0           # losing trades that were approved
    correct_approvals: int = 0         # profitable trades that were correctly approved

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "total_trades": self.total_trades,
            "approved_trades": self.approved_trades,
            "rejected_trades": self.rejected_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate * 100, 2),
            "total_pnl_pct": round(self.total_pnl, 4),
            "avg_pnl_pct": round(self.avg_pnl, 4),
            "profit_factor": round(self.profit_factor, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "sortino_ratio": round(self.sortino_ratio, 3),
            "max_drawdown_pct": round(self.max_drawdown, 4),
            "avg_hold_time_minutes": round(self.avg_hold_time_minutes, 1),
            "false_rejections": self.false_rejections,
            "correct_rejections": self.correct_rejections,
            "false_approvals": self.false_approvals,
            "correct_approvals": self.correct_approvals,
        }


@dataclass
class BacktestComparison:
    """Comparison between two strategies."""
    strategy_a: StrategyMetrics
    strategy_b: StrategyMetrics
    opportunity_increase_pct: float = 0.0
    false_veto_reduction_pct: float = 0.0
    net_pnl_impact_pct: float = 0.0
    drawdown_impact_pct: float = 0.0
    recommendation: str = ""
    details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_a": self.strategy_a.to_dict(),
            "strategy_b": self.strategy_b.to_dict(),
            "comparison": {
                "opportunity_increase_pct": round(self.opportunity_increase_pct, 2),
                "false_veto_reduction_pct": round(self.false_veto_reduction_pct, 2),
                "net_pnl_impact_pct": round(self.net_pnl_impact_pct, 4),
                "drawdown_impact_pct": round(self.drawdown_impact_pct, 4),
            },
            "recommendation": self.recommendation,
            "n_details": len(self.details),
        }


# ── Backtest Engine ──────────────────────────────────────────────────────────

class SkillBacktester:
    """
    Backtests shadow_trades comparing current rules vs Market Skills Engine.

    For each historical shadow_trade:
    1. Loads the features_snapshot (indicators at time of entry)
    2. Determines the regime at that time
    3. Evaluates with current rules (would it have been approved?)
    4. Evaluates with skills (would the skill have approved it?)
    5. Records the outcome for comparison
    """

    async def run_comparison(
        self,
        db: AsyncSession,
        user_id: str,
        days: int = 30,
        limit: int = 500,
    ) -> BacktestComparison:
        """Run full backtest comparison."""
        logger.info(
            "[Backtest] Starting comparison for user=%s days=%d limit=%d",
            user_id, days, limit,
        )

        # Load shadow trades with known outcomes
        trades = await self._load_shadow_trades(db, days, limit)
        if not trades:
            return BacktestComparison(
                strategy_a=StrategyMetrics(name="current_rules"),
                strategy_b=StrategyMetrics(name="market_skills"),
                recommendation="Dados insuficientes para backtest.",
            )

        logger.info("[Backtest] Loaded %d shadow trades for analysis", len(trades))

        # Load user's skills
        from .skill_profiles import load_user_skills
        skills = await load_user_skills(db, user_id)

        # Process each trade
        current_results: List[Dict[str, Any]] = []
        skills_results: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []

        from .market_regime_engine import MarketRegimeEngine
        from .skill_selector import SkillSelector
        from .decision_explainer import DecisionExplainer

        regime_engine = MarketRegimeEngine()
        selector = SkillSelector(mode="ai_adaptive")
        explainer = DecisionExplainer()

        for trade in trades:
            features = trade.get("features_snapshot") or {}
            outcome = trade.get("outcome", "")
            pnl = float(trade.get("pnl_pct") or 0)
            is_win = outcome == "TP_HIT"

            # Skip trades without features
            if not features:
                continue

            # 1. Determine regime from features
            symbol = trade.get("symbol", "UNKNOWN")
            asset_regime = regime_engine.detect_asset_regime(symbol, features)

            # 2. Evaluate with current rules (simplified: was it approved?)
            # The trade EXISTS in shadow_trades, so it was seen by the pipeline.
            # We check if it would pass current entry triggers
            current_approved = self._evaluate_current_rules(features)

            # 3. Evaluate with skills
            selection = await selector.select(asset_regime, db, user_id)
            skill = selection.selected_skill
            explanation = explainer.evaluate(
                symbol=symbol,
                indicators=features,
                skill=skill,
                regime_signal=asset_regime,
                skill_selection=selection,
            )
            skills_approved = explanation.decision in ("BUY", "STRONG_BUY")

            # 4. Record results
            current_results.append({
                "approved": current_approved,
                "pnl": pnl,
                "is_win": is_win,
            })
            skills_results.append({
                "approved": skills_approved,
                "pnl": pnl,
                "is_win": is_win,
                "skill_key": skill.skill_key,
                "regime": asset_regime.regime.value,
                "score": explanation.total_score,
            })

            # Record detail for interesting cases
            if current_approved != skills_approved:
                details.append({
                    "symbol": symbol,
                    "outcome": outcome,
                    "pnl_pct": round(pnl, 4),
                    "regime": asset_regime.regime.value,
                    "skill": skill.skill_key,
                    "skill_score": round(explanation.total_score, 1),
                    "current_approved": current_approved,
                    "skills_approved": skills_approved,
                    "is_win": is_win,
                    "classification": self._classify_difference(
                        current_approved, skills_approved, is_win,
                    ),
                })

        # 5. Compute metrics
        strategy_a = self._compute_metrics("current_rules", current_results)
        strategy_b = self._compute_metrics("market_skills", skills_results)

        # 6. Compute comparison
        comparison = self._compute_comparison(strategy_a, strategy_b, details)

        logger.info(
            "[Backtest] Complete: current WR=%.1f%% vs skills WR=%.1f%%, "
            "opportunity_increase=%.1f%%",
            strategy_a.win_rate * 100,
            strategy_b.win_rate * 100,
            comparison.opportunity_increase_pct,
        )

        return comparison

    async def _load_shadow_trades(
        self,
        db: AsyncSession,
        days: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Load shadow trades with known outcomes and features."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        try:
            result = await db.execute(text("""
                SELECT
                    symbol, outcome, pnl_pct, net_return_pct,
                    features_snapshot, source, created_at,
                    EXTRACT(EPOCH FROM (COALESCE(closed_at, created_at) - created_at)) / 60.0 AS hold_minutes
                FROM shadow_trades
                WHERE outcome IN ('TP_HIT', 'SL_HIT')
                  AND pnl_pct IS NOT NULL
                  AND features_snapshot IS NOT NULL
                  AND created_at >= :cutoff
                ORDER BY created_at DESC
                LIMIT :lim
            """), {"cutoff": cutoff, "lim": limit})

            rows = result.mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:
            logger.error("[Backtest] Failed to load shadow trades: %s", exc)
            return []

    def _evaluate_current_rules(self, features: Dict[str, Any]) -> bool:
        """
        Simplified evaluation of current rigid rules.
        Returns True if the trade would pass current L3 entry triggers.
        """
        # Current rules (from the user's analysis):
        # Entry Trigger: RSI < 45 (FAIL for most assets)
        # Block: RSI > 65, ADX < 12, MACD < 0, Volume < 800000, ATR% > 8
        rsi = _safe_float(features.get("rsi"))
        adx = _safe_float(features.get("adx"))
        macd = _safe_float(features.get("macd_value") or features.get("macd"))
        volume = _safe_float(features.get("volume_24h"))
        atr_pct = _safe_float(features.get("atr_pct"))

        # Entry trigger: RSI < 45 (required)
        if rsi is not None and rsi >= 45:
            return False  # Most rejections come from here

        # Block rules
        if rsi is not None and rsi > 65:
            return False
        if adx is not None and adx < 12:
            return False
        if macd is not None and macd < 0:
            return False
        if volume is not None and volume < 800000:
            return False
        if atr_pct is not None and atr_pct > 8:
            return False

        return True

    @staticmethod
    def _classify_difference(
        current_approved: bool,
        skills_approved: bool,
        is_win: bool,
    ) -> str:
        """Classify the difference between strategies."""
        if not current_approved and skills_approved and is_win:
            return "FALSE_REJECTION_FIXED"  # Skills captured a winner that current rejected
        if not current_approved and skills_approved and not is_win:
            return "RISK_TAKEN"  # Skills approved a loser that current rejected
        if current_approved and not skills_approved and is_win:
            return "OPPORTUNITY_LOST"  # Skills rejected a winner that current approved
        if current_approved and not skills_approved and not is_win:
            return "RISK_AVOIDED"  # Skills rejected a loser that current approved
        return "AGREE"

    def _compute_metrics(
        self,
        name: str,
        results: List[Dict[str, Any]],
    ) -> StrategyMetrics:
        """Compute strategy metrics from results."""
        metrics = StrategyMetrics(name=name)
        metrics.total_trades = len(results)

        approved = [r for r in results if r["approved"]]
        rejected = [r for r in results if not r["approved"]]

        metrics.approved_trades = len(approved)
        metrics.rejected_trades = len(rejected)

        if not approved:
            return metrics

        pnls = [r["pnl"] for r in approved]
        metrics.wins = sum(1 for r in approved if r["is_win"])
        metrics.losses = len(approved) - metrics.wins
        metrics.win_rate = metrics.wins / len(approved) if approved else 0

        metrics.total_pnl = sum(pnls)
        metrics.avg_pnl = metrics.total_pnl / len(pnls) if pnls else 0

        # Profit Factor
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        metrics.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        # Sharpe Ratio (annualized, assuming daily returns)
        if len(pnls) > 1:
            avg = sum(pnls) / len(pnls)
            variance = sum((p - avg) ** 2 for p in pnls) / (len(pnls) - 1)
            std = math.sqrt(variance) if variance > 0 else 0.001
            metrics.sharpe_ratio = (avg / std) * math.sqrt(252) if std > 0 else 0

            # Sortino (only downside deviation)
            downside = [p for p in pnls if p < 0]
            if downside:
                down_var = sum(p ** 2 for p in downside) / len(downside)
                down_std = math.sqrt(down_var)
                metrics.sortino_ratio = (avg / down_std) * math.sqrt(252) if down_std > 0 else 0

        # Max Drawdown
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        metrics.max_drawdown = max_dd

        # False rejections / correct rejections
        for r in rejected:
            if r["is_win"]:
                metrics.false_rejections += 1
            else:
                metrics.correct_rejections += 1

        for r in approved:
            if r["is_win"]:
                metrics.correct_approvals += 1
            else:
                metrics.false_approvals += 1

        return metrics

    def _compute_comparison(
        self,
        strategy_a: StrategyMetrics,
        strategy_b: StrategyMetrics,
        details: List[Dict[str, Any]],
    ) -> BacktestComparison:
        """Compute comparison between strategies."""
        # Opportunity increase
        a_approved = strategy_a.approved_trades or 1
        b_approved = strategy_b.approved_trades or 1
        opp_increase = ((b_approved - a_approved) / a_approved) * 100

        # False veto reduction
        a_false_rej = strategy_a.false_rejections or 1
        b_false_rej = strategy_b.false_rejections or 1
        veto_reduction = ((a_false_rej - b_false_rej) / a_false_rej) * 100

        # PnL impact
        pnl_impact = strategy_b.total_pnl - strategy_a.total_pnl

        # Drawdown impact
        dd_impact = strategy_b.max_drawdown - strategy_a.max_drawdown

        # Recommendation
        recommendation = self._generate_recommendation(
            strategy_a, strategy_b, opp_increase, pnl_impact,
        )

        return BacktestComparison(
            strategy_a=strategy_a,
            strategy_b=strategy_b,
            opportunity_increase_pct=opp_increase,
            false_veto_reduction_pct=veto_reduction,
            net_pnl_impact_pct=pnl_impact,
            drawdown_impact_pct=dd_impact,
            recommendation=recommendation,
            details=details[:50],  # cap at 50 details
        )

    @staticmethod
    def _generate_recommendation(
        a: StrategyMetrics,
        b: StrategyMetrics,
        opp_increase: float,
        pnl_impact: float,
    ) -> str:
        """Generate human-readable recommendation."""
        parts = []

        if b.win_rate > a.win_rate:
            parts.append(
                f"Market Skills tem WIN RATE superior ({b.win_rate*100:.1f}% vs {a.win_rate*100:.1f}%)."
            )
        elif b.win_rate < a.win_rate:
            parts.append(
                f"Regras atuais têm WIN RATE superior ({a.win_rate*100:.1f}% vs {b.win_rate*100:.1f}%)."
            )

        if opp_increase > 20:
            parts.append(
                f"Market Skills captura {opp_increase:.0f}% mais oportunidades válidas."
            )

        if b.false_rejections < a.false_rejections:
            diff = a.false_rejections - b.false_rejections
            parts.append(
                f"Market Skills evita {diff} rejeições falsas (trades lucrativos que seriam perdidos)."
            )

        if pnl_impact > 0:
            parts.append(
                f"Impacto positivo no PnL: +{pnl_impact:.3f}%."
            )
        elif pnl_impact < 0:
            parts.append(
                f"Impacto negativo no PnL: {pnl_impact:.3f}% (mais risco aceito)."
            )

        if b.profit_factor > a.profit_factor:
            parts.append("Profit Factor melhor com Market Skills.")

        if not parts:
            return "Resultados similares entre as estratégias."

        # Final recommendation
        score = 0
        if b.win_rate > a.win_rate:
            score += 1
        if pnl_impact > 0:
            score += 2
        if b.false_rejections < a.false_rejections:
            score += 1
        if b.profit_factor > a.profit_factor:
            score += 1

        if score >= 3:
            parts.append("✅ RECOMENDAÇÃO: Ativar Market Skills Engine em produção.")
        elif score >= 1:
            parts.append("⚠️ RECOMENDAÇÃO: Ativar em modo conservador (AI Adaptive).")
        else:
            parts.append("❌ RECOMENDAÇÃO: Manter regras atuais até mais dados.")

        return " ".join(parts)


# ── Helper ────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f  # NaN check
    except (TypeError, ValueError):
        return None


# ── Convenience ───────────────────────────────────────────────────────────────

async def run_skills_backtest(
    db: AsyncSession,
    user_id: str,
    days: int = 30,
    limit: int = 500,
) -> Dict[str, Any]:
    """Convenience function to run backtest and return dict."""
    backtester = SkillBacktester()
    result = await backtester.run_comparison(db, user_id, days, limit)
    return result.to_dict()

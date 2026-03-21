"""Futures Risk Engine — position sizing, leverage calculation, SL/TP placement.

Principle: Leverage is CALCULATED, not chosen.
  risk_dollars = capital × risk_pct / 100
  position_size = risk_dollars / stop_distance
  leverage = position_value / margin_allocated

All thresholds from ExecutionFuturesConfig and ScoringFuturesConfig (zero hardcode).
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..schemas.futures_engine_config import (
    ExecutionFuturesConfig,
    ScoringFuturesConfig,
    RiskFuturesConfig,
)
from ..engines.futures_anti_liq import FuturesAntiLiq, AntiLiqDesignResult

logger = logging.getLogger(__name__)


@dataclass
class RiskParameters:
    """Complete risk parameters for a futures trade."""
    entry_price: float
    stop_loss: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    position_size_contracts: int    # Gate.io contracts (can be fractional for some contracts)
    position_value_usdt: float
    leverage: float
    risk_dollars: float
    risk_pct: float
    rr_tp1: float
    rr_tp2: float
    rr_tp3: float
    stop_distance_pct: float
    estimated_liq_price: float
    stop_to_liq_pct: float
    classification: str             # "institutional_grade" | "strong" | "valid"
    details: Dict[str, Any]


@dataclass
class ScoreClassification:
    label: str             # "institutional_grade" | "strong" | "valid" | "reject"
    size_multiplier: float
    max_leverage_pct: float   # fraction of tier max (0-1)


class FuturesRiskEngine:
    """
    Calculates all risk parameters for a futures trade given:
      - account capital
      - entry, direction
      - score classification
      - macro size modifier
      - key levels and ATR (for SL/TP placement)
    """

    def __init__(
        self,
        exec_cfg: ExecutionFuturesConfig,
        score_cfg: ScoringFuturesConfig,
        risk_cfg: RiskFuturesConfig,
        anti_liq: FuturesAntiLiq,
    ):
        self.exec_cfg  = exec_cfg
        self.score_cfg = score_cfg
        self.risk_cfg  = risk_cfg
        self.anti_liq  = anti_liq

    def classify_score(self, total_score: float) -> ScoreClassification:
        cfg = self.score_cfg
        if total_score >= cfg.conviction_threshold:
            return ScoreClassification(
                label="institutional_grade",
                size_multiplier=cfg.size_multipliers.get("institutional_grade", 1.5),
                max_leverage_pct=cfg.leverage_tiers.get("high", 1.0),
            )
        elif total_score >= cfg.strong_threshold:
            return ScoreClassification(
                label="strong",
                size_multiplier=cfg.size_multipliers.get("strong", 1.0),
                max_leverage_pct=cfg.leverage_tiers.get("normal", 0.7),
            )
        elif total_score >= cfg.valid_threshold:
            return ScoreClassification(
                label="valid",
                size_multiplier=cfg.size_multipliers.get("valid", 0.6),
                max_leverage_pct=cfg.leverage_tiers.get("conservative", 0.4),
            )
        else:
            return ScoreClassification(label="reject", size_multiplier=0.0, max_leverage_pct=0.0)

    def get_max_leverage(self, classification: str) -> float:
        """Returns the leverage cap for a given score classification."""
        lev = self.exec_cfg.leverage
        caps = {
            "institutional_grade": lev.max_leverage_institutional,
            "strong":              lev.max_leverage_strong,
            "valid":               lev.max_leverage_valid,
        }
        return caps.get(classification, lev.max_leverage_valid)

    def calculate_stop_loss(
        self,
        direction: str,
        entry_price: float,
        atr: float,
        swing_lows: List[float] = None,
        swing_highs: List[float] = None,
    ) -> float:
        """
        Hierarchy: structure → ATR fallback.
        Returns stop_loss price.
        """
        sl_cfg = self.exec_cfg.stop_loss
        min_dist = entry_price * sl_cfg.min_stop_distance_pct / 100
        max_dist = entry_price * sl_cfg.max_stop_distance_pct / 100
        atr_stop = atr * sl_cfg.atr_stop_multiplier

        for method in sl_cfg.method_priority:
            if method == "structure":
                if direction == "long" and swing_lows:
                    # Last swing low below entry
                    candidates = [l for l in sorted(swing_lows) if l < entry_price]
                    if candidates:
                        sl = candidates[-1] * 0.999   # just below swing low
                        dist = entry_price - sl
                        if min_dist <= dist <= max_dist:
                            return round(sl, 8)
                elif direction == "short" and swing_highs:
                    candidates = [h for h in sorted(swing_highs, reverse=True) if h > entry_price]
                    if candidates:
                        sl = candidates[-1] * 1.001
                        dist = sl - entry_price
                        if min_dist <= dist <= max_dist:
                            return round(sl, 8)
            elif method == "atr":
                if direction == "long":
                    sl = entry_price - atr_stop
                else:
                    sl = entry_price + atr_stop
                dist = abs(entry_price - sl)
                if dist <= max_dist:
                    return round(sl, 8)

        # Final fallback: ATR regardless of max_dist
        if direction == "long":
            return round(entry_price - atr_stop, 8)
        else:
            return round(entry_price + atr_stop, 8)

    def calculate_take_profits(
        self,
        direction: str,
        entry_price: float,
        stop_loss: float,
        vol_regime: str = "NORMAL",
    ) -> Tuple[float, float, float]:
        """
        Returns (tp1, tp2, tp3) prices based on R:R multiples.
        Adjusts multipliers for volatility regime.
        """
        tp_cfg  = self.exec_cfg.take_profit
        risk    = abs(entry_price - stop_loss)

        # Regime adjustments
        if vol_regime == "SQUEEZE":
            multiplier = tp_cfg.squeeze_tp_multiplier
        elif vol_regime == "EXPANDING":
            multiplier = tp_cfg.expanding_tp_multiplier
        else:
            multiplier = 1.0

        rr1 = tp_cfg.rr_tp1 * multiplier
        rr2 = tp_cfg.rr_tp2 * multiplier
        rr3 = tp_cfg.rr_tp3 * multiplier

        if direction == "long":
            tp1 = entry_price + risk * rr1
            tp2 = entry_price + risk * rr2
            tp3 = entry_price + risk * rr3
        else:
            tp1 = entry_price - risk * rr1
            tp2 = entry_price - risk * rr2
            tp3 = entry_price - risk * rr3

        return round(tp1, 8), round(tp2, 8), round(tp3, 8)

    def calculate_position(
        self,
        capital_usdt: float,
        entry_price: float,
        stop_loss: float,
        direction: str,
        total_score: float,
        macro_size_modifier: float,
        contract_quanto_multiplier: float = 0.0001,   # from Gate contract info
        maintenance_rate: float = 0.005,
        vol_regime: str = "NORMAL",
    ) -> Optional[RiskParameters]:
        """
        Full position calculation: size → leverage → anti-liq validation → TP levels.
        Returns None if trade cannot be made safely.
        """
        classification = self.classify_score(total_score)
        if classification.label == "reject":
            logger.info("Score %.1f rejected (< min_score_to_trade=%.1f)", total_score, self.score_cfg.min_score_to_trade)
            return None

        # ── 1. Determine risk% ────────────────────────────────────────────────
        risk_pct = self.risk_cfg.max_risk_per_trade_pct
        if classification.label == "institutional_grade":
            risk_pct = self.risk_cfg.max_risk_per_trade_conviction_pct
        risk_pct *= classification.size_multiplier * macro_size_modifier

        # ── 2. Risk in dollars ────────────────────────────────────────────────
        risk_dollars = capital_usdt * risk_pct / 100

        # ── 3. Stop distance ─────────────────────────────────────────────────
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            logger.warning("Stop distance is zero — cannot size position")
            return None

        stop_distance_pct = stop_distance / entry_price * 100

        sl_cfg = self.exec_cfg.stop_loss
        if stop_distance_pct > sl_cfg.max_stop_distance_pct:
            logger.info(
                "Stop too wide: %.2f%% > max %.2f%% — trade rejected",
                stop_distance_pct, sl_cfg.max_stop_distance_pct,
            )
            return None
        if stop_distance_pct < sl_cfg.min_stop_distance_pct:
            logger.info("Stop too tight: %.4f%% — trade rejected", stop_distance_pct)
            return None

        # ── 4. Position size in contracts ────────────────────────────────────
        # Gate.io USDT-margined: 1 contract = quanto_multiplier × index_price (in USDT)
        # position_value = contracts × entry × quanto
        # risk = contracts × stop_distance × quanto
        contracts = risk_dollars / (stop_distance * contract_quanto_multiplier)
        contracts = max(1, round(contracts))
        position_value = contracts * entry_price * contract_quanto_multiplier

        # ── 5. Cap by max_capital_per_trade ───────────────────────────────────
        max_value = capital_usdt * self.exec_cfg.max_capital_per_trade_pct / 100
        if position_value > max_value:
            contracts = max(1, round(max_value / (entry_price * contract_quanto_multiplier)))
            position_value = contracts * entry_price * contract_quanto_multiplier

        # ── 6. Calculate required leverage ───────────────────────────────────
        margin_budget = capital_usdt * self.exec_cfg.max_capital_deployed_pct / 100
        required_leverage = position_value / margin_budget if margin_budget > 0 else 999

        max_leverage = self.get_max_leverage(classification.label)
        leverage = min(required_leverage, max_leverage)
        leverage = max(1.0, round(leverage, 1))

        # Recalculate position to fit leverage cap
        if required_leverage > max_leverage:
            position_value = margin_budget * max_leverage
            contracts = max(1, round(position_value / (entry_price * contract_quanto_multiplier)))
            position_value = contracts * entry_price * contract_quanto_multiplier
            actual_risk = contracts * stop_distance * contract_quanto_multiplier
            risk_pct    = actual_risk / capital_usdt * 100
            risk_dollars = actual_risk

        # ── 7. Anti-liquidation design validation ─────────────────────────────
        anti_result = self.anti_liq.validate_design(
            entry_price, stop_loss, leverage, direction, maintenance_rate=maintenance_rate
        )
        if not anti_result.safe:
            logger.info("Anti-liq L1 rejected: %s", anti_result.rejection_reason)
            return None
        if anti_result.adjusted_leverage is not None:
            leverage = anti_result.adjusted_leverage
            # Recalculate position_value with lower leverage
            position_value = margin_budget * leverage
            contracts = max(1, round(position_value / (entry_price * contract_quanto_multiplier)))
            position_value = contracts * entry_price * contract_quanto_multiplier

        # ── 8. Take profits ───────────────────────────────────────────────────
        tp1, tp2, tp3 = self.calculate_take_profits(direction, entry_price, stop_loss, vol_regime)

        risk   = abs(entry_price - stop_loss)
        rr_tp1 = abs(tp1 - entry_price) / risk if risk > 0 else 0
        rr_tp2 = abs(tp2 - entry_price) / risk if risk > 0 else 0
        rr_tp3 = abs(tp3 - entry_price) / risk if risk > 0 else 0

        return RiskParameters(
            entry_price=entry_price,
            stop_loss=round(stop_loss, 8),
            tp1_price=tp1,
            tp2_price=tp2,
            tp3_price=tp3,
            position_size_contracts=contracts,
            position_value_usdt=round(position_value, 2),
            leverage=leverage,
            risk_dollars=round(risk_dollars, 2),
            risk_pct=round(risk_pct, 3),
            rr_tp1=round(rr_tp1, 2),
            rr_tp2=round(rr_tp2, 2),
            rr_tp3=round(rr_tp3, 2),
            stop_distance_pct=round(stop_distance_pct, 3),
            estimated_liq_price=anti_result.estimated_liq_price,
            stop_to_liq_pct=anti_result.stop_to_liq_pct,
            classification=classification.label,
            details={
                "macro_size_modifier": macro_size_modifier,
                "quanto_multiplier":   contract_quanto_multiplier,
                "margin_budget":       round(margin_budget, 2),
            },
        )

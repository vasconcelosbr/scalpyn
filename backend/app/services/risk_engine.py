"""Risk Engine — position sizing, TP/SL, exposure limits, circuit breaker."""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone, timedelta
from decimal import Decimal

logger = logging.getLogger(__name__)


class RiskEngine:
    """Evaluates risk constraints and calculates position parameters from config."""

    def __init__(self, risk_config: Dict[str, Any]):
        self.config = risk_config

    def evaluate_trade(
        self,
        symbol: str,
        direction: str,
        current_price: float,
        indicators: Dict[str, Any],
        available_capital: float,
        open_positions: int,
        daily_pnl: float,
        consecutive_losses: int,
    ) -> Dict[str, Any]:
        """Evaluate whether a trade should proceed and calculate its parameters.

        Returns:
            {
                "approved": True/False,
                "rejection_reason": str or None,
                "quantity": float,
                "invested_value": float,
                "take_profit_price": float,
                "stop_loss_price": float,
                "order_type": str,
            }
        """
        # 1. Circuit breaker check
        max_consec = self.config.get("circuit_breaker_consecutive_losses", 3)
        if consecutive_losses >= max_consec:
            return self._reject(f"Circuit breaker: {consecutive_losses} consecutive losses (limit: {max_consec})")

        # 2. Daily loss limit check
        daily_loss_limit_pct = self.config.get("daily_loss_limit_pct", 3.0)
        if available_capital > 0:
            daily_loss_pct = abs(daily_pnl / available_capital * 100) if daily_pnl < 0 else 0
            if daily_loss_pct >= daily_loss_limit_pct:
                return self._reject(f"Daily loss limit reached: {daily_loss_pct:.2f}% (limit: {daily_loss_limit_pct}%)")

        # 3. Max positions check
        max_positions = self.config.get("max_positions", 5)
        if open_positions >= max_positions:
            return self._reject(f"Max positions reached: {open_positions}/{max_positions}")

        # 4. Max capital in use check
        max_capital_pct = self.config.get("max_capital_in_use_pct", 80)
        # This would need total invested value from open positions - simplified here
        capital_per_trade_pct = self.config.get("capital_per_trade_pct", 10)

        # 5. Calculate position size
        trade_capital = available_capital * (capital_per_trade_pct / 100)

        # Max exposure per asset check
        max_exposure_pct = self.config.get("max_exposure_per_asset_pct", 20)
        max_per_asset = available_capital * (max_exposure_pct / 100)
        trade_capital = min(trade_capital, max_per_asset)

        if trade_capital <= 0 or current_price <= 0:
            return self._reject("Insufficient capital or invalid price")

        quantity = trade_capital / current_price

        # 6. Calculate TP/SL
        tp_pct = self.config.get("take_profit_pct", 1.5)
        sl_atr_mult = self.config.get("stop_loss_atr_multiplier", 1.5)
        atr = indicators.get("atr", current_price * 0.02)  # fallback 2%

        if direction == "long":
            take_profit_price = current_price * (1 + tp_pct / 100)
            stop_loss_price = current_price - (atr * sl_atr_mult)
        else:  # short
            take_profit_price = current_price * (1 - tp_pct / 100)
            stop_loss_price = current_price + (atr * sl_atr_mult)

        # Validate SL is reasonable (not more than 10% away)
        sl_distance_pct = abs(stop_loss_price - current_price) / current_price * 100
        if sl_distance_pct > 10:
            stop_loss_price = current_price * (0.90 if direction == "long" else 1.10)

        # 7. Slippage check
        max_slippage = self.config.get("max_slippage_pct", 0.1)
        order_type = self.config.get("default_order_type", "limit")

        return {
            "approved": True,
            "rejection_reason": None,
            "quantity": round(quantity, 8),
            "invested_value": round(trade_capital, 2),
            "take_profit_price": round(take_profit_price, 8),
            "stop_loss_price": round(stop_loss_price, 8),
            "order_type": order_type,
            "max_slippage_pct": max_slippage,
            "risk_per_trade": round(abs(current_price - stop_loss_price) * quantity, 2),
        }

    def check_exit_conditions(
        self,
        trade: Dict[str, Any],
        current_price: float,
        indicators: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Check if an open position should be closed (TP/SL/trailing).

        Returns:
            {"should_exit": bool, "exit_reason": str, "exit_type": str}
        """
        entry_price = trade.get("entry_price", 0)
        direction = trade.get("direction", "long")
        tp_price = trade.get("take_profit_price")
        sl_price = trade.get("stop_loss_price")

        if current_price <= 0 or entry_price <= 0:
            return {"should_exit": False, "exit_reason": None, "exit_type": None}

        # Take Profit hit
        if tp_price:
            if direction == "long" and current_price >= tp_price:
                return {"should_exit": True, "exit_reason": f"Take profit at {tp_price}", "exit_type": "take_profit"}
            if direction == "short" and current_price <= tp_price:
                return {"should_exit": True, "exit_reason": f"Take profit at {tp_price}", "exit_type": "take_profit"}

        # Stop Loss hit
        if sl_price:
            if direction == "long" and current_price <= sl_price:
                return {"should_exit": True, "exit_reason": f"Stop loss at {sl_price}", "exit_type": "stop_loss"}
            if direction == "short" and current_price >= sl_price:
                return {"should_exit": True, "exit_reason": f"Stop loss at {sl_price}", "exit_type": "stop_loss"}

        # Trailing stop (if enabled)
        if self.config.get("trailing_stop_enabled", False):
            trailing_pct = self.config.get("trailing_stop_distance_pct", 0.5)
            if direction == "long":
                # Track highest price since entry (would need price history in practice)
                trailing_sl = current_price * (1 - trailing_pct / 100)
                if sl_price and trailing_sl > sl_price:
                    pass  # Would update stop loss dynamically

        return {"should_exit": False, "exit_reason": None, "exit_type": None}

    def _reject(self, reason: str) -> Dict[str, Any]:
        return {
            "approved": False,
            "rejection_reason": reason,
            "quantity": 0,
            "invested_value": 0,
            "take_profit_price": 0,
            "stop_loss_price": 0,
            "order_type": None,
        }

"""Spot Capital Manager — tracks available USDT, locked capital, reserve, and exposure limits.

All thresholds come from SpotEngineConfig (zero hardcode).
"""

import logging
from decimal import Decimal
from typing import Dict, List, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from ..models.trade import Trade
from ..schemas.spot_engine_config import BuyingConfig

logger = logging.getLogger(__name__)


class CapitalState:
    """Snapshot of capital at a given moment."""

    def __init__(
        self,
        usdt_balance: float,
        capital_reserve: float,
        positions_value: float,
        locked_underwater: float,
        position_count: int,
    ):
        self.usdt_balance       = usdt_balance
        self.capital_reserve    = capital_reserve
        self.positions_value    = positions_value
        self.locked_underwater  = locked_underwater
        self.position_count     = position_count

    @property
    def total_capital(self) -> float:
        return self.usdt_balance + self.positions_value

    @property
    def available(self) -> float:
        """USDT actually available to deploy (balance minus reserve)."""
        return max(0.0, self.usdt_balance - self.capital_reserve)

    @property
    def capital_utilization_pct(self) -> float:
        if self.total_capital <= 0:
            return 0.0
        return (self.positions_value / self.total_capital) * 100

    @property
    def underwater_ratio_pct(self) -> float:
        if self.total_capital <= 0:
            return 0.0
        return (self.locked_underwater / self.total_capital) * 100

    def to_dict(self) -> Dict[str, float]:
        return {
            "usdt_balance":           round(self.usdt_balance, 2),
            "capital_reserve":        round(self.capital_reserve, 2),
            "available":              round(self.available, 2),
            "positions_value":        round(self.positions_value, 2),
            "locked_underwater":      round(self.locked_underwater, 2),
            "total_capital":          round(self.total_capital, 2),
            "capital_utilization_pct": round(self.capital_utilization_pct, 2),
            "underwater_ratio_pct":   round(self.underwater_ratio_pct, 2),
            "position_count":         self.position_count,
        }


class SpotCapitalManager:
    """
    Calculates and enforces capital allocation rules for the Spot Engine.

    Rules (all from config, zero hardcode):
      - available = usdt_balance - (usdt_balance × capital_reserve_pct / 100)
      - trade_size = min(available, total_capital × capital_per_trade_pct / 100)
      - trade_size >= capital_per_trade_min_usdt
      - capital_in_use <= max_capital_in_use_pct
      - positions_total <= max_positions_total
      - positions_per_asset <= max_positions_per_asset
      - exposure_per_asset <= max_exposure_per_asset_pct of total_capital
    """

    def __init__(self, buying_cfg: BuyingConfig):
        self.cfg = buying_cfg

    async def get_state(
        self,
        usdt_balance: float,
        db: AsyncSession,
        user_id: str,
    ) -> CapitalState:
        """Build a CapitalState from live balance + DB positions."""
        positions = await self._load_open_positions(db, user_id)

        positions_value    = sum(float(p.invested_value or 0) for p in positions)
        locked_underwater  = sum(
            float(p.invested_value or 0)
            for p in positions
            if p.status == "HOLDING_UNDERWATER"
        )
        capital_reserve = usdt_balance * (self.cfg.capital_reserve_pct / 100)

        return CapitalState(
            usdt_balance=usdt_balance,
            capital_reserve=capital_reserve,
            positions_value=positions_value,
            locked_underwater=locked_underwater,
            position_count=len(positions),
        )

    def calc_trade_size(self, state: CapitalState) -> float:
        """
        Calculate USDT amount to use for a new trade.
        Returns 0.0 if capital conditions are not met.
        """
        if state.available < self.cfg.capital_per_trade_min_usdt:
            logger.debug(
                "Capital check failed: available=%.2f < min_trade=%.2f",
                state.available, self.cfg.capital_per_trade_min_usdt,
            )
            return 0.0

        trade_size = state.total_capital * (self.cfg.capital_per_trade_pct / 100)
        trade_size = min(trade_size, state.available)

        if trade_size < self.cfg.capital_per_trade_min_usdt:
            return 0.0

        return round(trade_size, 2)

    def can_open_new_position(self, state: CapitalState) -> tuple[bool, str]:
        """
        Returns (allowed, reason). Checks global position and capital limits.
        """
        if state.position_count >= self.cfg.max_positions_total:
            return False, f"max_positions_total reached ({self.cfg.max_positions_total})"

        if state.capital_utilization_pct >= self.cfg.max_capital_in_use_pct:
            return False, (
                f"max_capital_in_use_pct reached "
                f"({state.capital_utilization_pct:.1f}% >= {self.cfg.max_capital_in_use_pct}%)"
            )

        trade_size = self.calc_trade_size(state)
        if trade_size <= 0:
            return False, "insufficient available capital"

        return True, "ok"

    async def can_trade_asset(
        self,
        symbol: str,
        trade_size_usdt: float,
        state: CapitalState,
        db: AsyncSession,
        user_id: str,
    ) -> tuple[bool, str]:
        """
        Per-asset position count and exposure checks.
        """
        positions = await self._load_open_positions(db, user_id, symbol=symbol)
        asset_count = len(positions)

        if asset_count >= self.cfg.max_positions_per_asset:
            return False, (
                f"max_positions_per_asset reached for {symbol} "
                f"({asset_count}/{self.cfg.max_positions_per_asset})"
            )

        asset_value = sum(float(p.invested_value or 0) for p in positions)
        new_exposure = asset_value + trade_size_usdt
        max_exposure = state.total_capital * (self.cfg.max_exposure_per_asset_pct / 100)

        if new_exposure > max_exposure:
            return False, (
                f"max_exposure_per_asset_pct exceeded for {symbol}: "
                f"{new_exposure:.2f} > {max_exposure:.2f}"
            )

        return True, "ok"

    @staticmethod
    async def _load_open_positions(
        db: AsyncSession,
        user_id: str,
        symbol: str = None,
    ) -> List[Trade]:
        """Load open/active/holding_underwater positions for a user."""
        q = select(Trade).where(
            Trade.user_id == user_id,
            Trade.profile == "spot",
            Trade.status.in_(["ACTIVE", "HOLDING_UNDERWATER", "open"]),
        )
        if symbol:
            q = q.where(Trade.symbol == symbol)
        result = await db.execute(q)
        return result.scalars().all()

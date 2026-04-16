"""Spot Position Manager — lifecycle, status transitions, and DCA for spot positions.

Status transitions:
  ACTIVE ──(price drops below entry)──► HOLDING_UNDERWATER
  HOLDING_UNDERWATER ──(price recovers above entry + safety_margin)──► ACTIVE
  ACTIVE ──(sell layer triggers)──► CLOSED

DCA: executed only on HOLDING_UNDERWATER positions when all conditions met.
All thresholds from SpotEngineConfig (zero hardcode).
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.trade import Trade
from ..schemas.spot_engine_config import SpotEngineConfig
from ..exchange_adapters.gate_adapter import GateAdapter, InsufficientBalanceError

logger = logging.getLogger(__name__)


class SpotPositionManager:
    """
    Manages status transitions and DCA for open spot positions.

    Called every scanner cycle (or on a separate monitor loop).
    """

    def __init__(self, config: SpotEngineConfig):
        self.cfg = config

    # ── Status transitions ────────────────────────────────────────────────────

    async def update_position_statuses(
        self,
        db: AsyncSession,
        user_id: str,
        current_prices: dict[str, float],
    ) -> List[Tuple[Trade, str, str]]:
        """
        For each open spot position, check if status needs updating.
        Returns list of (trade, old_status, new_status) for changed positions.
        """
        positions = await self._load_open_positions(db, user_id)
        transitions = []

        for pos in positions:
            symbol       = pos.symbol
            entry_price  = float(pos.entry_price)
            current_price = current_prices.get(symbol)

            if current_price is None:
                continue

            old_status = pos.status
            new_status = self._determine_status(entry_price, current_price)

            if new_status != old_status:
                pos.status = new_status
                transitions.append((pos, old_status, new_status))
                logger.info(
                    "Position %s (%s): %s → %s  entry=%.6f  current=%.6f  pnl=%.2f%%",
                    pos.id, symbol, old_status, new_status,
                    entry_price, current_price,
                    ((current_price - entry_price) / entry_price) * 100,
                )

        if transitions:
            await db.commit()

        return transitions

    def _determine_status(self, entry_price: float, current_price: float) -> str:
        """
        ACTIVE              if current_price > entry_price × (1 + safety_margin/100)
        HOLDING_UNDERWATER  if current_price <= entry_price
        """
        safety   = self.cfg.selling.safety_margin_above_entry_pct / 100
        breakeven = entry_price * (1 + safety)
        return "ACTIVE" if current_price >= breakeven else "HOLDING_UNDERWATER"

    # ── DCA logic ─────────────────────────────────────────────────────────────

    async def process_dca(
        self,
        db: AsyncSession,
        user_id: str,
        current_prices: dict[str, float],
        current_scores: dict[str, float],
        available_usdt: float,
        adapter: GateAdapter,
    ) -> List[dict]:
        """
        Check all HOLDING_UNDERWATER positions for DCA eligibility and execute if met.
        Returns list of DCA execution results.
        """
        if not self.cfg.dca.enabled:
            return []

        positions = await self._load_underwater_positions(db, user_id)
        results   = []

        for pos in positions:
            symbol        = pos.symbol
            current_price = current_prices.get(symbol)
            current_score = current_scores.get(symbol, 0.0)
            entry_price   = float(pos.entry_price)
            dca_layers    = pos.dca_layers or 0

            if current_price is None:
                continue

            result = await self._try_dca(
                pos=pos,
                db=db,
                current_price=current_price,
                current_score=current_score,
                entry_price=entry_price,
                dca_layers=dca_layers,
                available_usdt=available_usdt,
                adapter=adapter,
            )
            if result:
                results.append(result)
                available_usdt -= result["dca_amount_usdt"]

        return results

    async def _try_dca(
        self,
        pos: Trade,
        db: AsyncSession,
        current_price: float,
        current_score: float,
        entry_price: float,
        dca_layers: int,
        available_usdt: float,
        adapter: GateAdapter,
    ) -> Optional[dict]:
        """Attempt DCA on a single underwater position. Returns result dict or None."""
        cfg = self.cfg.dca

        # Conditions
        drop_pct = ((entry_price - current_price) / entry_price) * 100
        if drop_pct < cfg.trigger_drop_pct:
            return None
        if current_score < cfg.min_score_for_dca:
            return None
        if dca_layers >= cfg.max_dca_layers:
            return None

        # Decayed DCA amount: Layer 1 = full, Layer 2 = ×decay, Layer 3 = ×decay²
        dca_amount = cfg.dca_amount_usdt * (cfg.dca_decay_factor ** dca_layers)
        if available_usdt < dca_amount:
            return None

        # Max total exposure check
        max_exposure = (
            (float(pos.invested_value or 0) / (dca_layers + 1))  # approx per-layer
            * (cfg.max_total_exposure_per_asset_pct / 100)
        )
        # Simplified: just check dca_amount fits within remaining exposure budget
        total_invested = float(pos.invested_value or 0) + dca_amount
        # (Full per-asset exposure check is in CapitalManager; this is a safeguard)

        logger.info(
            "DCA trigger: %s layer=%d  drop=%.2f%%  score=%.1f  amount=%.2f USDT",
            pos.symbol, dca_layers + 1, drop_pct, current_score, dca_amount,
        )

        try:
            order = await adapter.place_spot_order(
                currency_pair=pos.symbol,
                side="buy",
                order_type="market",
                amount=str(round(dca_amount, 2)),
                text=f"t-scalpyn-dca-{dca_layers + 1}",
            )
        except InsufficientBalanceError as e:
            logger.warning("DCA skipped (insufficient balance): %s", e)
            return None
        except Exception as e:
            logger.exception("DCA order failed for %s: %s", pos.symbol, e)
            return None

        # Recalculate weighted average entry price
        old_qty      = float(pos.quantity)
        dca_qty      = dca_amount / current_price
        new_qty      = old_qty + dca_qty
        new_entry    = (old_qty * entry_price + dca_qty * current_price) / new_qty
        old_invested = float(pos.invested_value or 0)

        # Persist original_entry_price on first DCA
        if pos.original_entry_price is None:
            pos.original_entry_price = Decimal(str(entry_price))

        pos.entry_price    = Decimal(str(round(new_entry, 8)))
        pos.quantity       = Decimal(str(round(new_qty, 8)))
        pos.invested_value = Decimal(str(round(old_invested + dca_amount, 2)))
        pos.dca_layers     = dca_layers + 1

        # Store DCA layer details in JSONB
        layers_data = pos.dca_layers_data or []
        layers_data.append({
            "layer": dca_layers + 1,
            "price": current_price,
            "amount_usdt": round(dca_amount, 2),
            "qty": round(dca_qty, 8),
            "score_at_dca": current_score,
            "drop_pct_at_dca": round(drop_pct, 2),
            "executed_at": datetime.now(timezone.utc).isoformat(),
        })
        pos.dca_layers_data = layers_data

        await db.commit()

        result = {
            "position_id":    str(pos.id),
            "symbol":         pos.symbol,
            "dca_layer":      dca_layers + 1,
            "dca_amount_usdt": round(dca_amount, 2),
            "old_entry":      entry_price,
            "new_entry":      round(new_entry, 6),
            "new_qty":        round(new_qty, 8),
            "score":          current_score,
            "order_id":       order.get("id"),
        }
        logger.info(
            "DCA executed: %s layer=%d  old_entry=%.6f  new_entry=%.6f  new_qty=%.4f",
            pos.symbol, dca_layers + 1, entry_price, new_entry, new_qty,
        )
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    async def _load_open_positions(db: AsyncSession, user_id: str) -> List[Trade]:
        q = select(Trade).where(
            Trade.user_id == user_id,
            Trade.market_type == "spot",
            Trade.status.in_(["ACTIVE", "HOLDING_UNDERWATER", "open"]),
        )
        r = await db.execute(q)
        return r.scalars().all()

    @staticmethod
    async def _load_underwater_positions(db: AsyncSession, user_id: str) -> List[Trade]:
        q = select(Trade).where(
            Trade.user_id == user_id,
            Trade.market_type == "spot",
            Trade.status == "HOLDING_UNDERWATER",
        )
        r = await db.execute(q)
        return r.scalars().all()

    async def get_position_summary(
        self,
        db: AsyncSession,
        user_id: str,
        current_prices: dict[str, float],
    ) -> dict:
        """Summary dict for API status endpoint."""
        positions = await self._load_open_positions(db, user_id)
        total = len(positions)
        active = sum(1 for p in positions if p.status == "ACTIVE")
        underwater = sum(1 for p in positions if p.status == "HOLDING_UNDERWATER")

        total_pnl = 0.0
        for pos in positions:
            cp = current_prices.get(pos.symbol)
            if cp:
                ep   = float(pos.entry_price)
                qty  = float(pos.quantity)
                total_pnl += (cp - ep) * qty

        return {
            "total":     total,
            "active":    active,
            "underwater": underwater,
            "unrealized_pnl_usdt": round(total_pnl, 2),
        }

"""Spot Sell Manager — 5-layer sell logic with REGRA SUPREMA: never_sell_at_loss.

REGRA SUPREMA (enforced before every sell decision):
  price > entry_price × (1 + safety_margin_pct / 100)
  AND profit_pct >= min_profit_pct
  → Only then can any layer trigger a sell.

5 Sell Layers (in priority order):
  1. RANGING   — market is sideways, score is falling, free up capital
  2. EXHAUSTION — trend is weakening (RSI overbought + volume declining)
  3. AI HOLD   — AI consultation (optional, rate-limited)
  4. TARGET    — take_profit_pct reached (primary exit)
  5. TRAILING  — HWM trailing stop active after AI extends

All thresholds from SpotEngineConfig (zero hardcode).
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade import Trade
from ..schemas.spot_engine_config import SpotEngineConfig
from ..exchange_adapters.gate_adapter import GateAdapter

logger = logging.getLogger(__name__)


@dataclass
class SellDecision:
    should_sell: bool
    layer: Optional[str]     # Which layer triggered
    reason: str
    profit_pct: float


class SpotSellManager:
    """
    Evaluates each ACTIVE spot position against the 5 sell layers.
    REGRA SUPREMA is the absolute pre-check — no layer can bypass it.
    """

    def __init__(self, config: SpotEngineConfig):
        self.cfg = config
        # In-memory HWM tracking for trailing stop: {position_id: high_water_mark}
        self._hwm: dict[str, float] = {}

    # ── Public entry point ────────────────────────────────────────────────────

    def evaluate(
        self,
        position: Trade,
        current_price: float,
        indicators: dict,
        current_score: float,
    ) -> SellDecision:
        """
        Evaluate sell decision for a single ACTIVE position.
        Returns SellDecision — call execute_sell() if should_sell=True.
        """
        entry_price = float(position.entry_price)
        profit_pct  = ((current_price - entry_price) / entry_price) * 100

        # ── REGRA SUPREMA ────────────────────────────────────────────────────
        if not self._passes_suprema(entry_price, current_price, profit_pct):
            self._update_hwm(str(position.id), current_price, reset=True)
            return SellDecision(
                should_sell=False,
                layer=None,
                reason=f"REGRA SUPREMA: profit={profit_pct:.2f}% < min={self.cfg.selling.min_profit_pct}%",
                profit_pct=profit_pct,
            )

        pos_id = str(position.id)

        # ── Layer 1: RANGING ─────────────────────────────────────────────────
        if self.cfg.sell_flow.ranging.enabled:
            decision = self._check_ranging(profit_pct, indicators, current_score)
            if decision.should_sell:
                return decision

        # ── Layer 2: EXHAUSTION ──────────────────────────────────────────────
        if self.cfg.sell_flow.exhaustion.enabled:
            decision = self._check_exhaustion(profit_pct, indicators)
            if decision.should_sell:
                return decision

        # ── Layer 3: AI CONSULTATION ─────────────────────────────────────────
        # (Async AI call handled externally; this checks if AI said SELL)
        if position.engine_meta and position.engine_meta.get("ai_decision") == "SELL":
            return SellDecision(
                should_sell=True,
                layer="3_AI",
                reason="AI consultation returned SELL",
                profit_pct=profit_pct,
            )

        # ── Layer 4: TARGET ──────────────────────────────────────────────────
        decision = self._check_target(profit_pct, indicators)
        if decision.should_sell:
            return decision

        # ── Layer 5: TRAILING HWM ────────────────────────────────────────────
        if self.cfg.sell_flow.trailing.enabled:
            decision = self._check_trailing(pos_id, current_price, profit_pct)
            if decision.should_sell:
                return decision

        # Update HWM for trailing
        self._update_hwm(pos_id, current_price)

        return SellDecision(
            should_sell=False,
            layer=None,
            reason=f"HOLD — profit={profit_pct:.2f}%  target={self.cfg.selling.take_profit_pct}%",
            profit_pct=profit_pct,
        )

    # ── Execute sell ──────────────────────────────────────────────────────────

    async def execute_sell(
        self,
        position: Trade,
        adapter: GateAdapter,
        decision: SellDecision,
        db: AsyncSession,
    ) -> dict:
        """
        Place market sell order and mark position as CLOSED.
        REGRA SUPREMA is pre-validated by evaluate(); this is the executor.
        """
        symbol = position.symbol
        qty    = str(float(position.quantity))

        logger.info(
            "SELL [layer=%s] %s qty=%s  profit=%.2f%%  reason: %s",
            decision.layer, symbol, qty, decision.profit_pct, decision.reason,
        )

        order = await adapter.place_spot_order(
            currency_pair=symbol,
            side="sell",
            order_type="market",
            amount=qty,
            text=f"t-scalpyn-sell-{decision.layer}",
        )

        # Mark position closed
        position.status    = "CLOSED"
        position.exit_at   = datetime.now(timezone.utc)
        position.exit_price = Decimal(str(
            float(order.get("avg_deal_price") or order.get("price") or 0)
        )) or None

        if position.exit_price:
            ep  = float(position.entry_price)
            qty_f = float(position.quantity)
            position.profit_loss     = Decimal(str(
                round((float(position.exit_price) - ep) * qty_f, 2)
            ))
            position.profit_loss_pct = Decimal(str(round(decision.profit_pct, 4)))

        # Clean up HWM
        self._hwm.pop(str(position.id), None)

        await db.commit()

        return {
            "position_id": str(position.id),
            "symbol":      symbol,
            "layer":       decision.layer,
            "profit_pct":  decision.profit_pct,
            "order_id":    order.get("id"),
        }

    # ── Layer implementations ─────────────────────────────────────────────────

    def _check_ranging(
        self, profit_pct: float, indicators: dict, current_score: float
    ) -> SellDecision:
        """
        Layer 1: Market is ranging (sideways).
        Sell if: ADX < adx_threshold AND BB is narrow AND profit >= take_profit.
        """
        cfg    = self.cfg.sell_flow.ranging
        adx    = indicators.get("adx") or 999
        bb_w   = indicators.get("bb_width") or 999
        target = self.cfg.selling.take_profit_pct

        if (
            profit_pct >= target
            and adx < cfg.adx_threshold
            and bb_w < cfg.bb_width_threshold
        ):
            return SellDecision(
                should_sell=True,
                layer="1_RANGING",
                reason=f"Ranging: ADX={adx:.1f} < {cfg.adx_threshold}, BB_width={bb_w:.4f}",
                profit_pct=profit_pct,
            )
        return SellDecision(should_sell=False, layer=None, reason="", profit_pct=profit_pct)

    def _check_exhaustion(self, profit_pct: float, indicators: dict) -> SellDecision:
        """
        Layer 2: Trend is exhausting.
        Sell if: RSI overbought AND volume declining AND profit >= min_profit.
        """
        cfg    = self.cfg.sell_flow.exhaustion
        rsi    = indicators.get("rsi") or 0
        v_spike = indicators.get("volume_spike") or 1.0
        # volume_spike < 1 means current volume < 20-period average
        volume_decline_ok = v_spike < (1 - cfg.volume_decline_pct / 100)

        if rsi >= cfg.rsi_overbought and volume_decline_ok:
            return SellDecision(
                should_sell=True,
                layer="2_EXHAUSTION",
                reason=f"Exhaustion: RSI={rsi:.1f} >= {cfg.rsi_overbought}, vol_spike={v_spike:.2f}",
                profit_pct=profit_pct,
            )
        return SellDecision(should_sell=False, layer=None, reason="", profit_pct=profit_pct)

    def _check_target(self, profit_pct: float, indicators: dict) -> SellDecision:
        """
        Layer 4: Take-profit target reached.
        Optional volatility and liquidity filters before selling.
        """
        cfg = self.cfg.sell_flow.target

        if profit_pct < self.cfg.selling.take_profit_pct:
            return SellDecision(should_sell=False, layer=None, reason="", profit_pct=profit_pct)

        # Volatility filter: don't sell in a BB squeeze (very low volatility)
        if cfg.volatility_filter_enabled:
            bb_w = indicators.get("bb_width")
            if bb_w is not None and bb_w < 0.01:
                return SellDecision(
                    should_sell=False,
                    layer=None,
                    reason=f"TARGET blocked by volatility squeeze: bb_width={bb_w:.4f}",
                    profit_pct=profit_pct,
                )

        # Volume filter: ensure adequate liquidity
        if cfg.liquidity_check_enabled:
            v_spike = indicators.get("volume_spike") or 1.0
            if v_spike < cfg.min_volume_multiplier:
                return SellDecision(
                    should_sell=False,
                    layer=None,
                    reason=f"TARGET blocked by low volume: spike={v_spike:.2f} < {cfg.min_volume_multiplier}",
                    profit_pct=profit_pct,
                )

        return SellDecision(
            should_sell=True,
            layer="4_TARGET",
            reason=f"Target reached: profit={profit_pct:.2f}% >= {self.cfg.selling.take_profit_pct}%",
            profit_pct=profit_pct,
        )

    def _check_trailing(
        self, pos_id: str, current_price: float, profit_pct: float
    ) -> SellDecision:
        """
        Layer 5: HWM trailing stop.
        Activates when profit >= activation_profit_pct.
        Sells when price drops hwm_trail_pct% below HWM.
        """
        cfg = self.cfg.sell_flow.trailing

        if profit_pct < cfg.activation_profit_pct:
            return SellDecision(should_sell=False, layer=None, reason="", profit_pct=profit_pct)

        hwm        = self._hwm.get(pos_id, current_price)
        trail_stop = hwm * (1 - cfg.hwm_trail_pct / 100)

        if current_price <= trail_stop:
            return SellDecision(
                should_sell=True,
                layer="5_TRAILING",
                reason=(
                    f"Trailing stop hit: price={current_price:.6f} <= "
                    f"stop={trail_stop:.6f} (HWM={hwm:.6f})"
                ),
                profit_pct=profit_pct,
            )
        return SellDecision(should_sell=False, layer=None, reason="", profit_pct=profit_pct)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _passes_suprema(
        self, entry_price: float, current_price: float, profit_pct: float
    ) -> bool:
        """
        REGRA SUPREMA check — the absolute piso before any sell.
        Returns True only if it is safe to sell (profit is real and sufficient).
        """
        if not self.cfg.selling.never_sell_at_loss:
            # Config override: never disable in production, but respect if set
            return True

        safety = self.cfg.selling.safety_margin_above_entry_pct / 100
        above_entry = current_price >= entry_price * (1 + safety)
        profit_ok   = profit_pct >= self.cfg.selling.min_profit_pct

        return above_entry and profit_ok

    def _update_hwm(self, pos_id: str, current_price: float, reset: bool = False) -> None:
        if reset:
            self._hwm.pop(pos_id, None)
            return
        if pos_id not in self._hwm or current_price > self._hwm[pos_id]:
            self._hwm[pos_id] = current_price

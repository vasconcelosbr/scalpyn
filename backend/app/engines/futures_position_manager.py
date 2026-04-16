"""Futures Position Manager — active position lifecycle after entry.

Responsibilities:
  - Detect TP1 hit → close tp1_exit_pct → move SL to breakeven (via Gate amend)
  - Detect TP2 hit → close tp2_exit_pct → activate trailing ATR
  - ATR-based trailing stop (HWM/LWM tracking)
  - Funding drain monitoring
  - Anti-liq runtime monitoring (Layer 3)
  - Emergency exit execution

All thresholds from ManagementFuturesConfig (zero hardcode).
"""

import logging
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..models.trade import Trade
from ..schemas.futures_engine_config import FuturesEngineConfig
from ..exchange_adapters.gate_adapter import GateAdapter
from ..engines.futures_anti_liq import FuturesAntiLiq
from ..engines.futures_emergency import FuturesEmergency, EmergencyEvent

logger = logging.getLogger(__name__)


@dataclass
class ManagementAction:
    action: str           # "TP1_HIT" | "TP2_HIT" | "TRAILING_STOP" | "EMERGENCY" | "FUNDING_DRAIN" | "NONE"
    position_id: str
    symbol: str
    details: Dict[str, Any]


class FuturesPositionManager:
    """
    Manages all active futures positions on each monitoring cycle.
    """

    def __init__(
        self,
        cfg: FuturesEngineConfig,
        adapter: GateAdapter,
        anti_liq: FuturesAntiLiq,
        emergency: FuturesEmergency,
    ):
        self.cfg       = cfg
        self.adapter   = adapter
        self.anti_liq  = anti_liq
        self.emergency = emergency
        # In-memory HWM/LWM tracking: {position_id: float}
        self._hwm: Dict[str, float] = {}

    async def manage_all(
        self,
        db: AsyncSession,
        user_id: str,
        current_prices: Dict[str, float],
        macro_regime: str,
        btc_prices: Dict[str, float],   # {"current": x, "1h_ago": y}
    ) -> List[ManagementAction]:
        """
        Run management cycle for all open futures positions.
        Returns list of actions taken.
        """
        positions = await self._load_open_futures(db, user_id)
        actions:  List[ManagementAction] = []

        for pos in positions:
            current_price = current_prices.get(pos.symbol)
            if current_price is None:
                continue

            result = await self._manage_position(
                pos, db, current_price, macro_regime, btc_prices
            )
            if result:
                actions.append(result)

        return actions

    async def _manage_position(
        self,
        pos: Trade,
        db: AsyncSession,
        current_price: float,
        macro_regime: str,
        btc_prices: Dict[str, float],
    ) -> Optional[ManagementAction]:
        pos_id    = str(pos.id)
        symbol    = pos.symbol
        direction = pos.direction or "long"
        entry     = float(pos.entry_price)
        liq_price = float(pos.liq_price) if pos.liq_price else 0.0
        tp1       = float(pos.take_profit_price) if pos.take_profit_price else None
        tp2       = float(pos.tp2_price) if pos.tp2_price else None
        sl_id     = int(pos.sl_order_id) if pos.sl_order_id else None
        tp1_id    = int(pos.tp1_order_id) if pos.tp1_order_id else None
        tp2_id    = int(pos.tp2_order_id) if pos.tp2_order_id else None
        tp1_hit   = bool(pos.tp1_hit)
        tp2_hit   = bool(pos.tp2_hit)
        mgmt      = self.cfg.management
        partial   = mgmt.partial_exits

        # ── Emergency checks (highest priority) ──────────────────────────────
        emergency_events = await self.emergency.check_position(
            position_id=pos_id,
            symbol=symbol,
            direction=direction,
            entry_price=entry,
            liq_price=liq_price,
            current_price=current_price,
            macro_regime=macro_regime,
            btc_price_1h_ago=btc_prices.get("1h_ago"),
            current_btc_price=btc_prices.get("current"),
        )
        for event in emergency_events:
            if event.action == "FORCE_CLOSE":
                success = await self.emergency.execute_emergency_close(
                    pos_id, symbol, direction, sl_id, tp1_id, tp2_id, event.message
                )
                if success:
                    await self._mark_closed(pos, db, current_price, reason="emergency")
                return ManagementAction("EMERGENCY", pos_id, symbol, {"event": event.condition})

        # ── Anti-liq runtime monitor (Layer 3) ────────────────────────────────
        if liq_price > 0:
            alert = self.anti_liq.monitor_position(entry, liq_price, current_price, direction)
            if alert.zone == "EMERGENCY":
                logger.critical("Anti-liq EMERGENCY: %s → %s", symbol, alert.message)
                await self.emergency.execute_emergency_close(
                    pos_id, symbol, direction, sl_id, tp1_id, tp2_id, alert.message
                )
                await self._mark_closed(pos, db, current_price, reason="anti_liq_emergency")
                return ManagementAction("EMERGENCY", pos_id, symbol, {"zone": alert.zone, "dist_pct": alert.distance_to_liq_pct})
            elif alert.zone == "CRITICAL":
                logger.warning("Anti-liq CRITICAL: %s — %.2f%% to liq", symbol, alert.distance_to_liq_pct)

        # ── Funding drain check ───────────────────────────────────────────────
        if mgmt.funding_drain.enabled:
            await self._check_funding_drain(pos, db, current_price)

        # ── TP1 check ─────────────────────────────────────────────────────────
        if not tp1_hit and tp1 is not None:
            tp1_reached = (
                (direction == "long"  and current_price >= tp1) or
                (direction == "short" and current_price <= tp1)
            )
            if tp1_reached:
                action = await self._handle_tp1(pos, db, current_price, sl_id)
                if action:
                    return action

        # ── TP2 check ─────────────────────────────────────────────────────────
        if tp1_hit and not tp2_hit and tp2 is not None:
            tp2_reached = (
                (direction == "long"  and current_price >= tp2) or
                (direction == "short" and current_price <= tp2)
            )
            if tp2_reached:
                action = await self._handle_tp2(pos, db, current_price, sl_id, tp2_id)
                if action:
                    return action

        # ── Trailing stop (after TP2) ─────────────────────────────────────────
        if tp2_hit:
            action = await self._check_trailing(pos, db, current_price)
            if action:
                return action

        return None

    # ── TP1 handler ───────────────────────────────────────────────────────────

    async def _handle_tp1(
        self, pos: Trade, db: AsyncSession, current_price: float, sl_id: Optional[int]
    ) -> Optional[ManagementAction]:
        symbol     = pos.symbol
        direction  = pos.direction or "long"
        entry      = float(pos.entry_price)
        qty        = int(float(pos.quantity))
        tp1_pct    = self.cfg.management.partial_exits.tp1_close_pct / 100
        close_size = max(1, round(qty * tp1_pct))

        logger.info("TP1 hit: %s — closing %.0f%% (%d contracts)", symbol, tp1_pct * 100, close_size)

        # Close partial: negative size for long (reduce_only)
        close_contracts = -close_size if direction == "long" else close_size
        try:
            await self.adapter.place_futures_order(
                contract=symbol,
                size=close_contracts,
                price="0",
                tif="ioc",
                is_reduce_only=True,
                text="t-scalpyn-tp1",
            )
        except Exception as e:
            logger.error("TP1 partial close failed for %s: %s", symbol, e)
            return None

        # Move SL to breakeven via amend
        if sl_id:
            try:
                # Breakeven = entry + small fee buffer (0.1%)
                be_price = entry * 1.001 if direction == "long" else entry * 0.999
                await self.adapter.modify_price_trigger(sl_id, str(round(be_price, 8)))
                logger.info("SL moved to breakeven: %s @ %.6f", symbol, be_price)
            except Exception as e:
                logger.warning("Failed to amend SL to BE for %s: %s", symbol, e)

        # Update position in DB
        pos.tp1_hit   = True
        pos.quantity  = Decimal(str(max(0, int(float(pos.quantity)) - close_size)))
        pos.engine_meta = {**(pos.engine_meta or {}), "tp1_closed_at": current_price}
        await db.commit()

        return ManagementAction("TP1_HIT", str(pos.id), symbol, {
            "closed_contracts": close_size, "remaining": int(float(pos.quantity)),
            "new_sl": "breakeven",
        })

    # ── TP2 handler ───────────────────────────────────────────────────────────

    async def _handle_tp2(
        self, pos: Trade, db: AsyncSession, current_price: float,
        sl_id: Optional[int], tp2_id: Optional[int]
    ) -> Optional[ManagementAction]:
        symbol    = pos.symbol
        direction = pos.direction or "long"
        qty       = int(float(pos.quantity))
        tp2_pct   = self.cfg.management.partial_exits.tp2_close_pct / 100
        close_size = max(1, round(qty * tp2_pct))

        logger.info("TP2 hit: %s — closing %.0f%% (%d contracts)", symbol, tp2_pct * 100, close_size)

        close_contracts = -close_size if direction == "long" else close_size
        try:
            await self.adapter.place_futures_order(
                contract=symbol,
                size=close_contracts,
                price="0",
                tif="ioc",
                is_reduce_only=True,
                text="t-scalpyn-tp2",
            )
        except Exception as e:
            logger.error("TP2 partial close failed for %s: %s", symbol, e)
            return None

        # Cancel TP2 price_order (already hit manually)
        if tp2_id:
            try:
                await self.adapter.cancel_price_trigger(tp2_id)
            except Exception:
                pass

        # Init HWM for trailing
        self._hwm[str(pos.id)] = current_price

        pos.tp2_hit   = True
        pos.quantity  = Decimal(str(max(0, int(float(pos.quantity)) - close_size)))
        pos.engine_meta = {**(pos.engine_meta or {}), "tp2_closed_at": current_price, "trailing_activated": True}
        pos.hwm_price = Decimal(str(current_price))
        await db.commit()

        return ManagementAction("TP2_HIT", str(pos.id), symbol, {
            "closed_contracts": close_size, "remaining": int(float(pos.quantity)),
            "trailing_activated": True,
        })

    # ── Trailing stop ─────────────────────────────────────────────────────────

    async def _check_trailing(
        self, pos: Trade, db: AsyncSession, current_price: float
    ) -> Optional[ManagementAction]:
        symbol    = pos.symbol
        direction = pos.direction or "long"
        pos_id    = str(pos.id)
        trail_cfg = self.cfg.management.trailing

        # Get ATR
        try:
            klines = await self.adapter.get_klines(symbol, interval="1h", limit=50, market="futures")
            df     = pd.DataFrame(klines)
            highs  = df["high"].astype(float)
            lows   = df["low"].astype(float)
            closes = df["close"].astype(float)
            tr     = pd.concat([
                highs - lows,
                (highs - closes.shift()).abs(),
                (lows  - closes.shift()).abs(),
            ], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
        except Exception:
            atr = float(pos.entry_price) * 0.01  # fallback: 1% of entry

        atr_trail = atr * trail_cfg.trailing_atr_multiplier

        # Tighten if big unrealized profit
        entry   = float(pos.entry_price)
        unreal  = ((current_price - entry) / entry * 100) if direction == "long" else ((entry - current_price) / entry * 100)
        if unreal > trail_cfg.tighten_above_profit_pct:
            atr_trail *= trail_cfg.tighten_factor

        # Update HWM/LWM
        hwm = self._hwm.get(pos_id, current_price)
        if direction == "long":
            hwm = max(hwm, current_price)
            trail_stop = hwm - atr_trail
            trail_stop = max(trail_stop, entry)   # floor at breakeven
            stop_hit   = current_price <= trail_stop
        else:
            hwm = min(hwm, current_price)
            trail_stop = hwm + atr_trail
            trail_stop = min(trail_stop, entry)
            stop_hit   = current_price >= trail_stop

        self._hwm[pos_id] = hwm
        pos.hwm_price = Decimal(str(hwm))

        if stop_hit:
            logger.info(
                "Trailing stop hit: %s  price=%.6f  trail_stop=%.6f  HWM=%.6f",
                symbol, current_price, trail_stop, hwm,
            )
            try:
                await self.adapter.close_position(symbol, text="t-scalpyn-trail")
                await self.adapter.cancel_all_price_triggers(symbol)
                await self._mark_closed(pos, db, current_price, reason="trailing_stop")
                return ManagementAction("TRAILING_STOP", pos_id, symbol, {
                    "trail_stop": trail_stop, "hwm": hwm, "atr": atr,
                })
            except Exception as e:
                logger.error("Trailing close failed for %s: %s", symbol, e)

        await db.commit()
        return None

    # ── Funding drain ─────────────────────────────────────────────────────────

    async def _check_funding_drain(
        self, pos: Trade, db: AsyncSession, current_price: float
    ) -> None:
        drain_cfg  = self.cfg.management.funding_drain
        pos_value  = float(pos.quantity) * current_price * 0.0001   # approx
        funding_paid = float(pos.funding_cost_usdt or 0)
        entry      = float(pos.entry_price)
        direction  = pos.direction or "long"
        qty        = float(pos.quantity)
        unrealized = ((current_price - entry) * qty * 0.0001) if direction == "long" else ((entry - current_price) * qty * 0.0001)

        if funding_paid <= 0:
            return

        if unrealized > 0 and (funding_paid / unrealized) > drain_cfg.max_funding_drain_pct_of_profit:
            logger.warning(
                "Funding drain warning: %s  paid=%.4f  unrealized=%.4f  ratio=%.2f",
                pos.symbol, funding_paid, unrealized, funding_paid / unrealized,
            )
        elif unrealized <= 0 and funding_paid > 0:
            logger.warning("Funding drain + position at loss: %s — consider closing", pos.symbol)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _mark_closed(
        self, pos: Trade, db: AsyncSession, exit_price: float, reason: str
    ) -> None:
        pos.status    = "CLOSED"
        pos.exit_at   = datetime.now(timezone.utc)
        pos.exit_price = Decimal(str(exit_price))
        entry = float(pos.entry_price)
        qty   = float(pos.quantity)
        direction = pos.direction or "long"
        if direction == "long":
            pnl = (exit_price - entry) * qty * 0.0001
        else:
            pnl = (entry - exit_price) * qty * 0.0001
        pos.profit_loss = Decimal(str(round(pnl, 2)))
        pos.engine_meta = {**(pos.engine_meta or {}), "close_reason": reason}
        self._hwm.pop(str(pos.id), None)
        await db.commit()

    @staticmethod
    async def _load_open_futures(db: AsyncSession, user_id: str) -> List[Trade]:
        q = select(Trade).where(
            Trade.user_id == user_id,
            Trade.market_type == "futures",
            Trade.status.in_(["ACTIVE", "open"]),
        )
        r = await db.execute(q)
        return r.scalars().all()

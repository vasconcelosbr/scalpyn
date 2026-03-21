"""Futures Emergency Engine — detects and responds to emergency conditions.

Conditions (all thresholds from ManagementFuturesConfig):
  1. Macro regime shift → STRONG_RISK_OFF (for long positions)
  2. BTC flash crash > btc_emergency_threshold_1h_pct (for alt positions)
  3. Funding rate explosion > funding_emergency
  4. Exchange latency / connectivity issues
  5. Liquidation approaching (delegated to FuturesAntiLiq Layer 3)

Actions:
  - FORCE_CLOSE: market close the position immediately
  - ALERT: notify without closing (latency, funding warning)
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..schemas.futures_engine_config import ManagementFuturesConfig
from ..exchange_adapters.gate_adapter import GateAdapter

logger = logging.getLogger(__name__)


@dataclass
class EmergencyEvent:
    condition: str        # e.g. "macro_shift", "btc_crash", "funding_explosion"
    position_id: str
    symbol: str
    direction: str
    action: str           # "FORCE_CLOSE" | "ALERT"
    message: str
    details: Dict[str, Any]


class FuturesEmergency:
    """
    Monitors all open futures positions for emergency conditions.
    Called on every monitoring cycle.
    """

    def __init__(self, cfg: ManagementFuturesConfig, adapter: GateAdapter):
        self.cfg     = cfg
        self.adapter = adapter
        self._last_api_call: float = 0.0

    async def check_position(
        self,
        position_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        liq_price: float,
        current_price: float,
        macro_regime: str,
        btc_price_1h_ago: Optional[float] = None,
        current_btc_price: Optional[float] = None,
    ) -> List[EmergencyEvent]:
        """
        Run all emergency checks for a single position.
        Returns list of EmergencyEvent (may be empty if all clear).
        """
        events: List[EmergencyEvent] = []
        ecfg = self.cfg.emergency

        # ── 1. Macro regime shift ─────────────────────────────────────────────
        if ecfg.macro_shift_exit and direction == "long":
            if macro_regime in ("STRONG_RISK_OFF",):
                events.append(EmergencyEvent(
                    condition="macro_shift",
                    position_id=position_id,
                    symbol=symbol,
                    direction=direction,
                    action="FORCE_CLOSE",
                    message=f"Macro regime shifted to {macro_regime} — closing long.",
                    details={"macro_regime": macro_regime},
                ))

        # ── 2. BTC flash crash ────────────────────────────────────────────────
        if symbol not in ("BTC_USDT", "BTCUSDT"):
            if btc_price_1h_ago and current_btc_price and btc_price_1h_ago > 0:
                btc_change_1h = abs(current_btc_price - btc_price_1h_ago) / btc_price_1h_ago * 100
                if btc_change_1h >= ecfg.btc_emergency_threshold_1h_pct:
                    events.append(EmergencyEvent(
                        condition="btc_crash",
                        position_id=position_id,
                        symbol=symbol,
                        direction=direction,
                        action="FORCE_CLOSE",
                        message=f"BTC moved {btc_change_1h:.1f}% in 1h — closing alt position.",
                        details={
                            "btc_1h_change_pct": round(btc_change_1h, 2),
                            "threshold":         ecfg.btc_emergency_threshold_1h_pct,
                        },
                    ))

        # ── 3. Funding rate explosion ─────────────────────────────────────────
        try:
            info    = await self.adapter.get_contract_info(symbol)
            funding = float(info.get("funding_rate", 0) or 0)

            if direction == "long" and funding >= ecfg.funding_emergency:
                events.append(EmergencyEvent(
                    condition="funding_explosion",
                    position_id=position_id,
                    symbol=symbol,
                    direction=direction,
                    action="FORCE_CLOSE",
                    message=f"Funding rate {funding:.4%} >= emergency threshold {ecfg.funding_emergency:.4%}.",
                    details={"funding_rate": funding, "threshold": ecfg.funding_emergency},
                ))
        except Exception as e:
            logger.debug("Emergency: funding check failed for %s: %s", symbol, e)

        return events

    async def execute_emergency_close(
        self,
        position_id: str,
        symbol: str,
        direction: str,
        sl_order_id: Optional[int],
        tp1_order_id: Optional[int],
        tp2_order_id: Optional[int],
        reason: str,
    ) -> bool:
        """
        Force-close a position:
          1. Cancel all open price triggers (SL/TP)
          2. Close position with market order
        Returns True if successful.
        """
        logger.critical(
            "EMERGENCY CLOSE: %s (%s) — %s", symbol, position_id, reason
        )

        # Cancel all triggers first
        try:
            await self.adapter.cancel_all_price_triggers(symbol)
        except Exception as e:
            logger.error("Emergency: failed to cancel triggers for %s: %s", symbol, e)

        # Close position
        try:
            await self.adapter.close_position(
                contract=symbol,
                text=f"t-scalpyn-emergency",
            )
            logger.critical("EMERGENCY CLOSE executed: %s", symbol)
            return True
        except Exception as e:
            logger.critical(
                "EMERGENCY CLOSE FAILED for %s: %s — MANUAL INTERVENTION REQUIRED", symbol, e
            )
            return False

    async def check_exchange_latency(self) -> Optional[EmergencyEvent]:
        """
        Ping Gate.io and measure latency.
        Returns an ALERT event if latency > max_exchange_latency_ms.
        """
        start = time.monotonic()
        try:
            await self.adapter.get_spot_balance()
            latency_ms = (time.monotonic() - start) * 1000
            if latency_ms > self.cfg.emergency.max_exchange_latency_ms:
                return EmergencyEvent(
                    condition="exchange_latency",
                    position_id="all",
                    symbol="all",
                    direction="all",
                    action="ALERT",
                    message=f"Exchange latency {latency_ms:.0f}ms > {self.cfg.emergency.max_exchange_latency_ms}ms.",
                    details={"latency_ms": round(latency_ms, 1)},
                )
        except Exception as e:
            return EmergencyEvent(
                condition="exchange_connectivity",
                position_id="all",
                symbol="all",
                direction="all",
                action="ALERT",
                message=f"Exchange connectivity error: {e}",
                details={"error": str(e)},
            )
        return None

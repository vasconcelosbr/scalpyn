"""Futures Anti-Liquidation Engine — 3 layers of protection.

Layer 1 (DESIGN): Pre-trade design validation
  - stop_to_liq distance check: stop must be placed BEFORE liquidation price
  - minimum buffer between stop and liq: liq_price_safety_margin_pct
  - Recalculate leverage until safe or reject trade

Layer 2 (PRE-TRADE): Buffer validation
  - After leverage is set, verify liq_price from Gate.io
  - Gate returns actual liq_price (accounts for fees, funding accrued)
  - Must be >= min_liquidation_distance_pct from entry

Layer 3 (RUNTIME): Continuous monitoring
  - Alert zone (8%): warn user, prepare to exit
  - Critical zone (5%): reduce position or tighten stop
  - Emergency zone (3%): force close immediately
  All thresholds from config.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from ..schemas.futures_engine_config import ExecutionFuturesConfig, ManagementFuturesConfig

logger = logging.getLogger(__name__)


@dataclass
class AntiLiqDesignResult:
    """Result of Layer 1 pre-trade design validation."""
    safe: bool
    adjusted_leverage: Optional[float]   # None if safe without adjustment
    estimated_liq_price: float
    stop_to_liq_pct: float               # % distance from stop to liq
    rejection_reason: Optional[str]
    details: Dict[str, Any]


@dataclass
class AntiLiqRuntimeAlert:
    """Result of Layer 3 runtime monitoring for a single position."""
    distance_to_liq_pct: float
    zone: str    # "SAFE" | "ALERT" | "CRITICAL" | "EMERGENCY"
    action: str  # "MONITOR" | "WARN" | "REDUCE" | "FORCE_CLOSE"
    message: str


class FuturesAntiLiq:
    """
    Anti-liquidation engine implementing the 3-layer framework.
    Thresholds come from ExecutionFuturesConfig and ManagementFuturesConfig.
    """

    def __init__(self, exec_cfg: ExecutionFuturesConfig, mgmt_cfg: ManagementFuturesConfig):
        self.exec_cfg = exec_cfg
        self.mgmt_cfg = mgmt_cfg

    # ── Layer 1: Design validation ────────────────────────────────────────────

    def validate_design(
        self,
        entry_price: float,
        stop_loss: float,
        leverage: float,
        direction: str,        # "long" | "short"
        margin_mode: str = "isolated",
        maintenance_rate: float = 0.005,
    ) -> AntiLiqDesignResult:
        """
        Layer 1: Validate that stop_loss is positioned safely BEFORE liq_price.

        Args:
            entry_price:      planned entry
            stop_loss:        planned stop loss
            leverage:         calculated leverage
            direction:        "long" or "short"
            maintenance_rate: from Gate contract info (default 0.5%)
        """
        liq_price       = self._calc_liq_price(entry_price, leverage, direction, maintenance_rate)
        stop_to_liq_pct = self._stop_to_liq_pct(stop_loss, liq_price, direction, entry_price)
        min_buffer      = self.exec_cfg.leverage.min_liquidation_distance_from_stop_pct

        if stop_to_liq_pct >= min_buffer:
            return AntiLiqDesignResult(
                safe=True,
                adjusted_leverage=None,
                estimated_liq_price=round(liq_price, 8),
                stop_to_liq_pct=round(stop_to_liq_pct, 2),
                rejection_reason=None,
                details={"entry": entry_price, "stop": stop_loss, "leverage": leverage},
            )

        # Try to find a safe leverage by reducing it
        for trial_lev in range(int(leverage) - 1, 0, -1):
            trial_liq = self._calc_liq_price(entry_price, float(trial_lev), direction, maintenance_rate)
            trial_buf = self._stop_to_liq_pct(stop_loss, trial_liq, direction, entry_price)
            if trial_buf >= min_buffer:
                logger.info(
                    "Anti-liq L1: adjusted leverage %s→%s (stop_to_liq=%.2f%%)",
                    leverage, trial_lev, trial_buf,
                )
                return AntiLiqDesignResult(
                    safe=True,
                    adjusted_leverage=float(trial_lev),
                    estimated_liq_price=round(trial_liq, 8),
                    stop_to_liq_pct=round(trial_buf, 2),
                    rejection_reason=None,
                    details={"entry": entry_price, "stop": stop_loss, "original_leverage": leverage},
                )

        # Cannot make safe even at 1x
        return AntiLiqDesignResult(
            safe=False,
            adjusted_leverage=None,
            estimated_liq_price=round(liq_price, 8),
            stop_to_liq_pct=round(stop_to_liq_pct, 2),
            rejection_reason=(
                f"Cannot satisfy stop_to_liq >= {min_buffer}% at any leverage. "
                f"Stop too close to entry. Widen stop or reduce leverage cap."
            ),
            details={"entry": entry_price, "stop": stop_loss, "leverage": leverage},
        )

    # ── Layer 2: Pre-trade validation (uses Gate-returned liq_price) ──────────

    def validate_pretrade(
        self,
        entry_price: float,
        actual_liq_price: float,   # from Gate.io after set_leverage
        direction: str,
    ) -> Tuple[bool, str]:
        """
        Layer 2: After Gate.io confirms the position, verify actual liq_price
        is far enough from entry.

        Returns (safe, reason).
        """
        min_dist = self.exec_cfg.leverage.min_liquidation_distance_from_stop_pct
        distance = self._distance_to_liq_pct(entry_price, actual_liq_price, direction)

        if distance >= min_dist:
            return True, f"Pre-trade liq check OK: {distance:.2f}% from entry"

        return False, (
            f"Pre-trade liq check FAILED: liq_price={actual_liq_price:.6f} only "
            f"{distance:.2f}% from entry (min={min_dist}%). Aborting trade."
        )

    # ── Layer 3: Runtime monitoring ───────────────────────────────────────────

    def monitor_position(
        self,
        entry_price: float,
        liq_price: float,
        current_price: float,
        direction: str,
    ) -> AntiLiqRuntimeAlert:
        """
        Layer 3: Called on every monitoring cycle for open positions.
        Returns the appropriate alert zone and recommended action.
        """
        distance_pct = self._distance_to_liq_pct(current_price, liq_price, direction)
        emergency_zone = self.mgmt_cfg.emergency.emergency_liq_distance_pct

        # Derive alert and critical thresholds relative to emergency
        critical_zone = emergency_zone + 2.0    # e.g. 5% emergency → 7% critical
        alert_zone    = emergency_zone + 5.0    # e.g. 5% emergency → 10% alert

        if distance_pct <= emergency_zone:
            return AntiLiqRuntimeAlert(
                distance_to_liq_pct=round(distance_pct, 2),
                zone="EMERGENCY",
                action="FORCE_CLOSE",
                message=(
                    f"EMERGENCY: Liquidation {distance_pct:.2f}% away "
                    f"(threshold={emergency_zone}%). FORCE CLOSING."
                ),
            )
        elif distance_pct <= critical_zone:
            return AntiLiqRuntimeAlert(
                distance_to_liq_pct=round(distance_pct, 2),
                zone="CRITICAL",
                action="REDUCE",
                message=(
                    f"CRITICAL: Liquidation {distance_pct:.2f}% away. "
                    "Reducing position or tightening stop."
                ),
            )
        elif distance_pct <= alert_zone:
            return AntiLiqRuntimeAlert(
                distance_to_liq_pct=round(distance_pct, 2),
                zone="ALERT",
                action="WARN",
                message=(
                    f"ALERT: Liquidation {distance_pct:.2f}% away. "
                    "Monitor closely."
                ),
            )
        else:
            return AntiLiqRuntimeAlert(
                distance_to_liq_pct=round(distance_pct, 2),
                zone="SAFE",
                action="MONITOR",
                message=f"Safe. Liquidation {distance_pct:.2f}% away.",
            )

    # ── Math helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _calc_liq_price(
        entry: float,
        leverage: float,
        direction: str,
        maintenance_rate: float = 0.005,
    ) -> float:
        """
        Estimate liquidation price for isolated margin.
        Formula (simplified): liq = entry × (1 ∓ 1/leverage + maintenance_rate)
        """
        if leverage <= 0:
            return 0.0
        if direction == "long":
            return entry * (1 - 1 / leverage + maintenance_rate)
        else:
            return entry * (1 + 1 / leverage - maintenance_rate)

    @staticmethod
    def _distance_to_liq_pct(current: float, liq: float, direction: str) -> float:
        """% distance from current price to liquidation price."""
        if liq <= 0 or current <= 0:
            return 100.0
        if direction == "long":
            return ((current - liq) / current * 100) if current > liq else 0.0
        else:
            return ((liq - current) / current * 100) if liq > current else 0.0

    @staticmethod
    def _stop_to_liq_pct(stop: float, liq: float, direction: str, entry: float) -> float:
        """% buffer between stop_loss and liq_price (relative to entry)."""
        if entry <= 0:
            return 0.0
        if direction == "long":
            return ((stop - liq) / entry * 100) if stop > liq else 0.0
        else:
            return ((liq - stop) / entry * 100) if liq > stop else 0.0

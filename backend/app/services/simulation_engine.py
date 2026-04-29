"""Simulation Engine — Core logic for simulating trade outcomes."""

import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


class SimulationEngine:
    """Core engine for simulating trade outcomes from historical OHLCV data."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize simulation engine with configuration.

        Args:
            config: Dictionary with simulation parameters:
                - entry_mode: How to determine entry price (default: "next_candle_open")
                - tp_pct: Take profit percentage (e.g., 0.012 for 1.2%)
                - sl_pct: Stop loss percentage (e.g., -0.008 for -0.8%)
                - timeout_candles: Max candles to simulate before timeout
        """
        self.config = config
        self.entry_mode = config.get("entry_mode", "next_candle_open")
        self.tp_pct = float(config.get("tp_pct", 0.012))
        self.sl_pct = float(config.get("sl_pct", -0.008))
        self.timeout_candles = int(config.get("timeout_candles", 10))

        logger.info(
            "SimulationEngine initialized: tp_pct=%.4f, sl_pct=%.4f, timeout=%d",
            self.tp_pct, self.sl_pct, self.timeout_candles
        )

    def calculate_entry_price(
        self,
        decision_timestamp: datetime,
        next_candles: List[Dict[str, Any]],
    ) -> Optional[float]:
        """
        Calculate entry price based on entry mode.

        Args:
            decision_timestamp: When the decision was made
            next_candles: List of candles after decision (sorted by time ASC)

        Returns:
            Entry price or None if no valid entry
        """
        if not next_candles:
            return None

        if self.entry_mode == "next_candle_open":
            # Entry at open of next candle after decision
            return float(next_candles[0]["open"])

        # Fallback to first candle open
        return float(next_candles[0]["open"])

    def calculate_targets(
        self,
        entry_price: float,
        direction: str,
    ) -> Tuple[float, float]:
        """
        Calculate TP and SL prices based on direction.

        Args:
            entry_price: Entry price
            direction: Trade direction (LONG/SHORT/SPOT)

        Returns:
            Tuple of (tp_price, sl_price)
        """
        if direction in ("LONG", "SPOT"):
            tp_price = entry_price * (1 + self.tp_pct)
            sl_price = entry_price * (1 + self.sl_pct)
        else:  # SHORT
            tp_price = entry_price * (1 - self.tp_pct)
            sl_price = entry_price * (1 - self.sl_pct)

        return tp_price, sl_price

    def check_gap(
        self,
        candles: List[Dict[str, Any]],
        timeframe: str,
    ) -> bool:
        """
        Check if there are gaps in the candle data.

        Args:
            candles: List of candles to check
            timeframe: Timeframe string (e.g., "1h", "5m")

        Returns:
            True if there are gaps, False otherwise
        """
        if len(candles) < 2:
            return False

        expected_delta = self._get_timeframe_delta(timeframe)

        for i in range(1, len(candles)):
            prev_time = candles[i - 1]["time"]
            curr_time = candles[i]["time"]

            # Ensure both are timezone-aware
            if prev_time.tzinfo is None:
                prev_time = prev_time.replace(tzinfo=timezone.utc)
            if curr_time.tzinfo is None:
                curr_time = curr_time.replace(tzinfo=timezone.utc)

            delta = curr_time - prev_time

            # Allow small tolerance (1 second)
            if delta > expected_delta + timedelta(seconds=1):
                logger.warning(
                    "Gap detected: expected %s, got %s between %s and %s",
                    expected_delta, delta, prev_time, curr_time
                )
                return True

        return False

    def simulate_trade(
        self,
        entry_price: float,
        entry_timestamp: datetime,
        direction: str,
        candles: List[Dict[str, Any]],
        timeframe: str,
    ) -> Dict[str, Any]:
        """
        Simulate a trade through candles.

        Args:
            entry_price: Entry price
            entry_timestamp: Entry timestamp
            direction: Trade direction (LONG/SHORT/SPOT)
            candles: List of candles after entry (sorted by time ASC)
            timeframe: Timeframe string

        Returns:
            Dictionary with simulation result:
                - result: WIN | LOSS | TIMEOUT
                - exit_price: Exit price
                - exit_timestamp: Exit timestamp
                - time_to_result: Seconds to result
        """
        # Check for gaps
        if self.check_gap(candles, timeframe):
            return {
                "result": "INVALID",
                "reason": "gap_detected",
            }

        # Calculate targets
        tp_price, sl_price = self.calculate_targets(entry_price, direction)

        # Limit candles to timeout
        sim_candles = candles[:self.timeout_candles]

        # Iterate through candles
        for candle in sim_candles:
            candle_time = candle["time"]
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)

            high = float(candle["high"])
            low = float(candle["low"])

            if direction in ("LONG", "SPOT"):
                # Check TP first (optimistic)
                if high >= tp_price:
                    time_delta = (candle_time - entry_timestamp).total_seconds()
                    return {
                        "result": "WIN",
                        "exit_price": tp_price,
                        "exit_timestamp": candle_time,
                        "time_to_result": int(time_delta),
                    }
                # Check SL
                if low <= sl_price:
                    time_delta = (candle_time - entry_timestamp).total_seconds()
                    return {
                        "result": "LOSS",
                        "exit_price": sl_price,
                        "exit_timestamp": candle_time,
                        "time_to_result": int(time_delta),
                    }
            else:  # SHORT
                # Check TP first (price goes down)
                if low <= tp_price:
                    time_delta = (candle_time - entry_timestamp).total_seconds()
                    return {
                        "result": "WIN",
                        "exit_price": tp_price,
                        "exit_timestamp": candle_time,
                        "time_to_result": int(time_delta),
                    }
                # Check SL (price goes up)
                if high >= sl_price:
                    time_delta = (candle_time - entry_timestamp).total_seconds()
                    return {
                        "result": "LOSS",
                        "exit_price": sl_price,
                        "exit_timestamp": candle_time,
                        "time_to_result": int(time_delta),
                    }

        # Timeout - neither TP nor SL hit
        if sim_candles:
            last_candle = sim_candles[-1]
            last_time = last_candle["time"]
            if last_time.tzinfo is None:
                last_time = last_time.replace(tzinfo=timezone.utc)

            time_delta = (last_time - entry_timestamp).total_seconds()
            return {
                "result": "TIMEOUT",
                "exit_price": float(last_candle["close"]),
                "exit_timestamp": last_time,
                "time_to_result": int(time_delta),
            }

        # No candles available
        return {
            "result": "INVALID",
            "reason": "no_candles",
        }

    @staticmethod
    def _get_timeframe_delta(timeframe: str) -> timedelta:
        """Convert timeframe string to timedelta."""
        timeframe_map = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }
        return timeframe_map.get(timeframe, timedelta(hours=1))

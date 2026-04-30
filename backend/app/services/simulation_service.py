"""Simulation Service — Orchestrates trade simulation process."""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.backoffice import DecisionLog
from ..repositories.simulation_repository import SimulationRepository
from .simulation_engine import SimulationEngine

logger = logging.getLogger(__name__)


class SimulationService:
    """Service for orchestrating trade simulations."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repository = SimulationRepository(session)

    async def get_simulation_config(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get simulation configuration from config_profiles or use defaults.

        Args:
            user_id: Optional user ID to get user-specific config

        Returns:
            Configuration dictionary
        """
        # Try to get from database
        if user_id:
            try:
                result = await self.session.execute(
                    text("""
                        SELECT config_json
                        FROM config_profiles
                        WHERE config_type = 'ai_settings'
                          AND user_id = :user_id
                        LIMIT 1
                    """),
                    {"user_id": user_id}
                )
                row = result.fetchone()
                if row and row.config_json:
                    return row.config_json
            except Exception as e:
                logger.warning("Failed to get config from DB: %s", e)

        # Default config
        return {
            "entry_mode": "next_candle_open",
            "tp_pct": 0.012,
            "sl_pct": -0.008,
            "timeout_candles": 10,
        }

    async def fetch_candles_after_timestamp(
        self,
        symbol: str,
        timestamp: datetime,
        timeframe: str,
        limit: int,
        exchange: str = "gate",
    ) -> List[Dict[str, Any]]:
        """
        Fetch OHLCV candles after a given timestamp.

        Args:
            symbol: Trading symbol
            timestamp: Start timestamp
            timeframe: Timeframe (e.g., "1h", "5m")
            limit: Number of candles to fetch
            exchange: Exchange name

        Returns:
            List of candle dictionaries
        """
        result = await self.session.execute(
            text("""
                SELECT time, open, high, low, close, volume, quote_volume
                FROM ohlcv
                WHERE symbol = :symbol
                  AND exchange = :exchange
                  AND timeframe = :timeframe
                  AND time > :timestamp
                ORDER BY time ASC
                LIMIT :limit
            """),
            {
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "timestamp": timestamp,
                "limit": limit,
            }
        )

        candles = []
        for row in result.fetchall():
            candles.append({
                "time": row.time,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
                "quote_volume": float(row.quote_volume),
            })

        return candles

    async def simulate_decision(
        self,
        decision: DecisionLog,
        config: Dict[str, Any],
        exchange: str = "gate",
    ) -> List[Dict[str, Any]]:
        """
        Simulate trade outcomes for a single decision.

        Args:
            decision: Decision log entry
            config: Simulation configuration
            exchange: Exchange name

        Returns:
            List of simulation result dictionaries (one per direction)
        """
        engine = SimulationEngine(config)

        # Determine timeframe (default to 1h)
        timeframe = decision.timeframe or "1h"

        # Fetch candles after decision timestamp
        # Need at least timeout_candles + 1 (for entry)
        required_candles = engine.timeout_candles + 1

        candles = await self.fetch_candles_after_timestamp(
            symbol=decision.symbol,
            timestamp=decision.created_at,
            timeframe=timeframe,
            limit=required_candles,
            exchange=exchange,
        )

        if not candles:
            logger.warning(
                "[Simulation] SKIP: No candles found | symbol=%s | after=%s",
                decision.symbol, decision.created_at
            )
            return []

        if len(candles) < 2:
            logger.warning(
                "[Simulation] SKIP: Insufficient candles | symbol=%s | got=%d | need=2",
                decision.symbol, len(candles)
            )
            return []

        # Calculate entry price (first candle open)
        entry_price = engine.calculate_entry_price(decision.created_at, candles)
        if not entry_price:
            logger.warning(
                "[Simulation] SKIP: Failed to calculate entry price | symbol=%s",
                decision.symbol
            )
            return []

        # Entry timestamp is the first candle time
        entry_timestamp = candles[0]["time"]
        if entry_timestamp.tzinfo is None:
            entry_timestamp = entry_timestamp.replace(tzinfo=timezone.utc)

        # Simulation candles (after entry)
        sim_candles = candles[1:]  # Skip the entry candle

        # Determine directions to simulate
        # Extract from decision metrics if available
        futures_mode = False
        if decision.metrics:
            futures_mode = decision.metrics.get("futures_mode", False)

        directions = ["SPOT"]
        if futures_mode:
            directions = ["LONG", "SHORT"]

        results = []
        for direction in directions:
            # Run simulation
            outcome = engine.simulate_trade(
                entry_price=entry_price,
                entry_timestamp=entry_timestamp,
                direction=direction,
                candles=sim_candles,
                timeframe=timeframe,
            )

            # Skip invalid results
            if outcome.get("result") == "INVALID":
                logger.info(
                    "[Simulation] SKIP: Invalid simulation | symbol=%s | direction=%s | reason=%s",
                    decision.symbol, direction, outcome.get("reason", "unknown")
                )
                continue

            # Calculate TP/SL
            tp_price, sl_price = engine.calculate_targets(entry_price, direction)

            # Build result record
            record = {
                "symbol": decision.symbol,
                "timestamp_entry": entry_timestamp,
                "entry_price": entry_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "exit_price": outcome.get("exit_price"),
                "exit_timestamp": outcome.get("exit_timestamp"),
                "result": outcome["result"],
                "time_to_result": outcome.get("time_to_result"),
                "direction": direction,
                "decision_type": decision.decision,
                "decision_id": decision.id,
                "features_snapshot": decision.metrics,  # Store decision metrics as features
                "config_snapshot": config,
            }

            results.append(record)

        return results

    async def run_simulation_batch(
        self,
        limit: int = 100,
        skip_existing: bool = True,
        user_id: Optional[str] = None,
        exchange: str = "gate",
    ) -> Dict[str, Any]:
        """
        Run simulation on a batch of decisions.

        Args:
            limit: Maximum number of decisions to process
            skip_existing: Skip decisions that already have simulations
            user_id: Optional user ID filter
            exchange: Exchange name

        Returns:
            Summary statistics
        """
        # CRITICAL: Validate OHLCV data availability before processing batch
        ohlcv_check = await self.session.execute(text("""
            SELECT COUNT(DISTINCT symbol) as symbol_count,
                   MAX(time) as latest_time,
                   COUNT(*) as total_candles
            FROM ohlcv
            WHERE exchange = :exchange
              AND timeframe = '1h'
              AND time >= NOW() - INTERVAL '24 hours'
        """), {"exchange": exchange})

        ohlcv_row = ohlcv_check.fetchone()

        if not ohlcv_row or not ohlcv_row.total_candles:
            error_msg = f"OHLCV validation FAILED: No recent candle data found for exchange={exchange}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        if ohlcv_row.total_candles < 100:
            error_msg = (
                f"OHLCV validation FAILED: Insufficient candle data "
                f"(found {ohlcv_row.total_candles} candles, need at least 100)"
            )
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        logger.info(
            "[Simulation] OHLCV validation PASSED: %d symbols, %d candles, latest=%s",
            ohlcv_row.symbol_count or 0,
            ohlcv_row.total_candles,
            ohlcv_row.latest_time
        )

        # Get config
        config = await self.get_simulation_config(user_id)

        # Fetch decisions
        query = select(DecisionLog).order_by(DecisionLog.created_at.desc()).limit(limit)
        if user_id:
            query = query.where(DecisionLog.user_id == user_id)

        result = await self.session.execute(query)
        decisions = result.scalars().all()

        logger.info("Processing %d decisions for simulation", len(decisions))

        processed = 0
        skipped = 0
        simulated = 0
        errors = 0
        skipped_no_candles = 0
        skipped_invalid = 0

        all_records = []

        for decision in decisions:
            try:
                # Check if already simulated (if skip_existing)
                if skip_existing:
                    existing = await self.session.execute(
                        text("""
                            SELECT COUNT(*) as cnt
                            FROM trade_simulations
                            WHERE decision_id = :decision_id
                        """),
                        {"decision_id": decision.id}
                    )
                    row = existing.fetchone()
                    if row and row.cnt > 0:
                        skipped += 1
                        continue

                # Run simulation
                records = await self.simulate_decision(decision, config, exchange)

                if records:
                    all_records.extend(records)
                    simulated += len(records)
                    logger.debug(
                        "[Simulation] SUCCESS | decision_id=%s | symbol=%s | records=%d",
                        decision.id, decision.symbol, len(records)
                    )
                else:
                    skipped_no_candles += 1
                    logger.debug(
                        "[Simulation] SKIP | decision_id=%s | symbol=%s | reason=no_candles",
                        decision.id, decision.symbol
                    )

                processed += 1

                # Log progress every 10 decisions
                if processed % 10 == 0:
                    logger.info(
                        "[Simulation] Progress: %d/%d decisions processed | simulated=%d | skipped=%d",
                        processed, len(decisions), simulated, skipped + skipped_no_candles
                    )

            except Exception as e:
                logger.error(
                    "[Simulation] ERROR | decision_id=%s | symbol=%s | error=%s",
                    decision.id, decision.symbol, str(e), exc_info=True
                )
                errors += 1

        # Calculate skip rate for alerting
        total_attempts = processed
        total_skipped = skipped_no_candles + skipped_invalid
        skip_rate = (total_skipped / total_attempts * 100) if total_attempts > 0 else 0

        # Alert if skip rate is excessive
        if skip_rate > 50 and total_attempts > 10:
            logger.warning(
                "[Simulation] HIGH SKIP RATE: %.1f%% (%d/%d) — check OHLCV data quality",
                skip_rate, total_skipped, total_attempts
            )

        # Bulk insert results
        if all_records:
            inserted = await self.repository.bulk_insert_simulations(all_records)
            logger.info("[Simulation] Bulk insert complete: %d records", inserted)

        # Final summary
        logger.info(
            "[Simulation] Batch complete | decisions=%d | processed=%d | "
            "simulated=%d | skipped_existing=%d | skipped_no_data=%d | errors=%d",
            len(decisions), processed, simulated, skipped, skipped_no_candles, errors
        )

        return {
            "total_decisions": len(decisions),
            "processed": processed,
            "skipped": skipped,
            "simulated": simulated,
            "errors": errors,
            "records_inserted": len(all_records),
            "skipped_no_candles": skipped_no_candles,
            "skip_rate": round(skip_rate, 2),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """Get simulation statistics."""
        return await self.repository.get_simulation_stats()

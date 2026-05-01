"""Simulation Repository — Database access layer for trade simulation operations."""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import text, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.trade_simulation import TradeSimulation

logger = logging.getLogger(__name__)


class SimulationRepository:
    """Repository for trade simulation data operations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_insert_simulations(
        self,
        records: List[Dict[str, Any]],
        batch_size: int = 500,
    ) -> int:
        """
        Bulk insert simulation records with ON CONFLICT DO NOTHING for idempotency.

        Commit responsibility belongs to the caller's transaction block.
        Do NOT call ``await session.commit()`` inside this method — the caller
        must wrap the call in ``async with session.begin():`` or
        ``run_db_task(...)`` so the transaction lifecycle is managed uniformly.

        Args:
            records: List of dicts with simulation data
            batch_size: Number of records per insert batch

        Returns:
            Total number of records processed
        """
        if not records:
            return 0

        total = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]

            # Build VALUES clause for bulk insert
            values_clause = ", ".join([
                f"(:symbol{j}, :timestamp_entry{j}, :entry_price{j}, "
                f":tp_price{j}, :sl_price{j}, :exit_price{j}, :exit_timestamp{j}, "
                f":result{j}, :time_to_result{j}, :direction{j}, :decision_type{j}, "
                f":decision_id{j}, :features_snapshot{j}, :config_snapshot{j})"
                for j in range(len(batch))
            ])

            # Flatten parameters
            params = {}
            for j, record in enumerate(batch):
                params[f"symbol{j}"] = record["symbol"]
                params[f"timestamp_entry{j}"] = record["timestamp_entry"]
                params[f"entry_price{j}"] = float(record["entry_price"])
                params[f"tp_price{j}"] = float(record["tp_price"])
                params[f"sl_price{j}"] = float(record["sl_price"])
                params[f"exit_price{j}"] = float(record["exit_price"]) if record.get("exit_price") else None
                params[f"exit_timestamp{j}"] = record.get("exit_timestamp")
                params[f"result{j}"] = record["result"]
                params[f"time_to_result{j}"] = record.get("time_to_result")
                params[f"direction{j}"] = record["direction"]
                params[f"decision_type{j}"] = record["decision_type"]
                params[f"decision_id{j}"] = record.get("decision_id")
                params[f"features_snapshot{j}"] = record.get("features_snapshot")
                params[f"config_snapshot{j}"] = record.get("config_snapshot")

            query = text(f"""
                INSERT INTO trade_simulations (
                    symbol, timestamp_entry, entry_price, tp_price, sl_price,
                    exit_price, exit_timestamp, result, time_to_result, direction,
                    decision_type, decision_id, features_snapshot, config_snapshot
                )
                VALUES {values_clause}
                ON CONFLICT (symbol, timestamp_entry, direction) DO NOTHING
            """)

            await self.session.execute(query, params)
            total += len(batch)

        return total

    async def get_existing_simulations(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        direction: Optional[str] = None,
    ) -> List[datetime]:
        """
        Get list of timestamps that already have simulations.

        Args:
            symbol: Trading symbol
            start_time: Start of time range
            end_time: End of time range
            direction: Optional direction filter (LONG/SHORT/SPOT)

        Returns:
            List of timestamps that already have simulations
        """
        query = select(TradeSimulation.timestamp_entry).where(
            TradeSimulation.symbol == symbol,
            TradeSimulation.timestamp_entry >= start_time,
            TradeSimulation.timestamp_entry <= end_time,
        )

        if direction:
            query = query.where(TradeSimulation.direction == direction)

        result = await self.session.execute(query)
        return [row[0] for row in result.fetchall()]

    async def count_simulations(
        self,
        symbol: Optional[str] = None,
        result_filter: Optional[str] = None,
        direction: Optional[str] = None,
    ) -> int:
        """
        Count simulations with optional filters.

        Args:
            symbol: Optional symbol filter
            result_filter: Optional result filter (WIN/LOSS/TIMEOUT)
            direction: Optional direction filter

        Returns:
            Count of matching simulations
        """
        query = select(TradeSimulation)

        if symbol:
            query = query.where(TradeSimulation.symbol == symbol)
        if result_filter:
            query = query.where(TradeSimulation.result == result_filter)
        if direction:
            query = query.where(TradeSimulation.direction == direction)

        result = await self.session.execute(
            text(f"SELECT COUNT(*) FROM ({query}) AS subquery")
        )
        row = result.fetchone()
        return row[0] if row else 0

    async def get_simulation_stats(self) -> Dict[str, Any]:
        """
        Get overall simulation statistics.

        Returns:
            Dictionary with simulation statistics
        """
        result = await self.session.execute(text("""
            SELECT
                COUNT(*) as total,
                COUNT(*) FILTER (WHERE result = 'WIN') as wins,
                COUNT(*) FILTER (WHERE result = 'LOSS') as losses,
                COUNT(*) FILTER (WHERE result = 'TIMEOUT') as timeouts,
                COUNT(*) FILTER (WHERE direction = 'LONG') as long_trades,
                COUNT(*) FILTER (WHERE direction = 'SHORT') as short_trades,
                COUNT(*) FILTER (WHERE direction = 'SPOT') as spot_trades,
                AVG(time_to_result) as avg_time_to_result,
                COUNT(DISTINCT symbol) as unique_symbols
            FROM trade_simulations
        """))

        row = result.fetchone()
        if not row:
            return {}

        total = row.total or 0
        wins = row.wins or 0
        losses = row.losses or 0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "timeouts": row.timeouts or 0,
            "win_rate": round((wins / total) * 100, 2) if total > 0 else 0.0,
            "loss_rate": round((losses / total) * 100, 2) if total > 0 else 0.0,
            "long_trades": row.long_trades or 0,
            "short_trades": row.short_trades or 0,
            "spot_trades": row.spot_trades or 0,
            "avg_time_to_result_seconds": round(float(row.avg_time_to_result or 0), 2),
            "unique_symbols": row.unique_symbols or 0,
        }

    async def delete_simulations_for_symbol(self, symbol: str) -> int:
        """
        Delete all simulations for a specific symbol.

        Commit responsibility belongs to the caller's transaction block.
        Do NOT call ``await session.commit()`` inside this method — the caller
        must wrap the call in ``async with session.begin():`` or
        ``run_db_task(...)`` so the transaction lifecycle is managed uniformly.

        Args:
            symbol: Trading symbol

        Returns:
            Number of deleted records
        """
        result = await self.session.execute(
            text("DELETE FROM trade_simulations WHERE symbol = :symbol"),
            {"symbol": symbol}
        )
        return result.rowcount

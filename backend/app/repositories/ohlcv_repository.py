"""OHLCV Repository — Database access layer for OHLCV operations."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class OHLCVRepository:
    """Repository for OHLCV data operations with bulk insert support."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_insert_ohlcv(
        self,
        records: List[Dict[str, Any]],
        batch_size: int = 1000,
    ) -> int:
        """
        Bulk insert OHLCV records with ON CONFLICT DO NOTHING for idempotency.

        Args:
            records: List of dicts with keys: time, symbol, exchange, timeframe,
                     open, high, low, close, volume, quote_volume
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
                f"(:time{j}, :symbol{j}, :exchange{j}, :timeframe{j}, "
                f":open{j}, :high{j}, :low{j}, :close{j}, :volume{j}, :quote_volume{j})"
                for j in range(len(batch))
            ])

            # Flatten parameters
            params = {}
            for j, record in enumerate(batch):
                params[f"time{j}"] = record["time"]
                params[f"symbol{j}"] = record["symbol"]
                params[f"exchange{j}"] = record["exchange"]
                params[f"timeframe{j}"] = record["timeframe"]
                params[f"open{j}"] = float(record["open"])
                params[f"high{j}"] = float(record["high"])
                params[f"low{j}"] = float(record["low"])
                params[f"close{j}"] = float(record["close"])
                params[f"volume{j}"] = float(record["volume"])
                params[f"quote_volume{j}"] = float(record["quote_volume"])

            query = text(f"""
                INSERT INTO ohlcv (time, symbol, exchange, timeframe, open, high, low, close, volume, quote_volume)
                VALUES {values_clause}
                ON CONFLICT (time, symbol, exchange, timeframe) DO NOTHING
            """)

            await self.session.execute(query, params)
            total += len(batch)

        await self.session.commit()
        return total

    async def get_earliest_timestamp(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> Optional[datetime]:
        """
        Get the earliest timestamp for a symbol/exchange/timeframe combination.

        Returns:
            datetime if data exists, None otherwise
        """
        result = await self.session.execute(
            text("""
                SELECT MIN(time) as earliest
                FROM ohlcv
                WHERE symbol = :symbol
                  AND exchange = :exchange
                  AND timeframe = :timeframe
            """),
            {"symbol": symbol, "exchange": exchange, "timeframe": timeframe}
        )
        row = result.fetchone()
        return row.earliest if row else None

    async def get_latest_timestamp(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> Optional[datetime]:
        """
        Get the latest timestamp for a symbol/exchange/timeframe combination.

        Returns:
            datetime if data exists, None otherwise
        """
        result = await self.session.execute(
            text("""
                SELECT MAX(time) as latest
                FROM ohlcv
                WHERE symbol = :symbol
                  AND exchange = :exchange
                  AND timeframe = :timeframe
            """),
            {"symbol": symbol, "exchange": exchange, "timeframe": timeframe}
        )
        row = result.fetchone()
        return row.latest if row else None

    async def count_records(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
    ) -> int:
        """
        Count total records for a symbol/exchange/timeframe combination.

        Returns:
            Number of records
        """
        result = await self.session.execute(
            text("""
                SELECT COUNT(*) as cnt
                FROM ohlcv
                WHERE symbol = :symbol
                  AND exchange = :exchange
                  AND timeframe = :timeframe
            """),
            {"symbol": symbol, "exchange": exchange, "timeframe": timeframe}
        )
        row = result.fetchone()
        return row.cnt if row else 0

    async def get_data_gaps(
        self,
        symbol: str,
        exchange: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
    ) -> List[tuple[datetime, datetime]]:
        """
        Identify time gaps in OHLCV data between start_time and end_time.

        Returns:
            List of (gap_start, gap_end) tuples representing missing periods
        """
        interval_map = {
            "1m": "1 minute",
            "5m": "5 minutes",
            "15m": "15 minutes",
            "1h": "1 hour",
            "4h": "4 hours",
            "1d": "1 day",
        }
        interval = interval_map.get(timeframe, "1 hour")

        result = await self.session.execute(
            text(f"""
                WITH expected_times AS (
                    SELECT generate_series(
                        :start_time::timestamptz,
                        :end_time::timestamptz,
                        interval '{interval}'
                    ) AS expected_time
                ),
                gaps AS (
                    SELECT et.expected_time
                    FROM expected_times et
                    LEFT JOIN ohlcv o ON
                        o.time = et.expected_time
                        AND o.symbol = :symbol
                        AND o.exchange = :exchange
                        AND o.timeframe = :timeframe
                    WHERE o.time IS NULL
                )
                SELECT expected_time FROM gaps ORDER BY expected_time
            """),
            {
                "symbol": symbol,
                "exchange": exchange,
                "timeframe": timeframe,
                "start_time": start_time,
                "end_time": end_time,
            }
        )

        # Group consecutive gaps
        gaps = []
        gap_start = None
        prev_time = None

        for row in result:
            current_time = row.expected_time
            if gap_start is None:
                gap_start = current_time
            elif prev_time and (current_time - prev_time).total_seconds() > self._get_interval_seconds(timeframe):
                # Gap in consecutive missing times
                gaps.append((gap_start, prev_time))
                gap_start = current_time
            prev_time = current_time

        if gap_start and prev_time:
            gaps.append((gap_start, prev_time))

        return gaps

    @staticmethod
    def _get_interval_seconds(timeframe: str) -> int:
        """Convert timeframe string to seconds."""
        interval_map = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
        }
        return interval_map.get(timeframe, 3600)

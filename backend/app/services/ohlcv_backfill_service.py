"""OHLCV Backfill Service — Production-ready historical data backfill."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories.ohlcv_repository import OHLCVRepository
from ..utils.gate_market_data import parse_gate_spot_candle

logger = logging.getLogger(__name__)

GATE_SPOT_URL = "https://api.gateio.ws/api/v4/spot/candlesticks"


class OHLCVBackfillService:
    """
    Production-ready OHLCV backfill service with:
    - Chunk-based backfill strategy (backwards in time)
    - Async HTTP with rate limit handling
    - Data validation
    - Idempotency (safe to re-run)
    - Parallel processing with semaphore
    """

    def __init__(
        self,
        session: AsyncSession,
        exchange: str = "gate.io",
        max_concurrent: int = 5,
        rate_limit_delay: float = 0.5,
    ):
        self.session = session
        self.repository = OHLCVRepository(session)
        self.exchange = exchange
        self.max_concurrent = max_concurrent
        self.rate_limit_delay = rate_limit_delay
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def backfill_symbol(
        self,
        symbol: str,
        timeframe: str,
        days: int = 180,
        chunk_size: int = 1000,
    ) -> Dict[str, Any]:
        """
        Backfill historical OHLCV data for a single symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC_USDT")
            timeframe: Candle interval ("1h", "5m", etc.)
            days: Number of days to backfill
            chunk_size: Number of candles per API request

        Returns:
            Dict with stats: symbol, fetched, inserted, errors, duration
        """
        start_time = datetime.now(timezone.utc)
        logger.info(f"[BACKFILL] Starting {symbol} {timeframe} - {days} days")

        try:
            # Calculate time range
            end_time = datetime.now(timezone.utc)
            start_backfill = end_time - timedelta(days=days)

            # Check existing data
            earliest = await self.repository.get_earliest_timestamp(symbol, self.exchange, timeframe)
            if earliest and earliest <= start_backfill:
                logger.info(
                    f"[BACKFILL] {symbol} {timeframe} already has data from {earliest} "
                    f"(target: {start_backfill}) - skipping"
                )
                return {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "fetched": 0,
                    "inserted": 0,
                    "skipped": True,
                    "earliest_existing": earliest,
                }

            # Fetch data in chunks (backwards in time)
            all_records = []
            current_end = end_time
            fetch_errors = 0
            max_retries = 3

            while current_end > start_backfill and fetch_errors < max_retries:
                async with self._semaphore:
                    try:
                        candles = await self._fetch_candles_with_retry(
                            symbol=symbol,
                            timeframe=timeframe,
                            limit=chunk_size,
                            to_timestamp=int(current_end.timestamp()),
                        )

                        if not candles:
                            logger.warning(f"[BACKFILL] {symbol} {timeframe} - no data returned")
                            break

                        # Parse and validate candles
                        parsed = []
                        for candle in candles:
                            try:
                                normalized = parse_gate_spot_candle(candle)
                                if self._validate_candle(normalized):
                                    parsed.append({
                                        "time": normalized["time"],
                                        "symbol": symbol,
                                        "exchange": self.exchange,
                                        "timeframe": timeframe,
                                        "open": normalized["open"],
                                        "high": normalized["high"],
                                        "low": normalized["low"],
                                        "close": normalized["close"],
                                        "volume": normalized["volume"],
                                        "quote_volume": normalized["quote_volume"],
                                    })
                            except Exception as e:
                                logger.warning(f"[BACKFILL] Failed to parse candle: {e}")
                                continue

                        if parsed:
                            all_records.extend(parsed)
                            oldest_time = min(r["time"] for r in parsed)
                            logger.debug(
                                f"[BACKFILL] {symbol} {timeframe} - fetched {len(parsed)} candles, "
                                f"oldest: {oldest_time}"
                            )
                            current_end = oldest_time - timedelta(seconds=1)
                        else:
                            break

                        # Rate limiting
                        await asyncio.sleep(self.rate_limit_delay)

                    except Exception as e:
                        fetch_errors += 1
                        logger.error(f"[BACKFILL] {symbol} {timeframe} fetch error #{fetch_errors}: {e}")
                        if fetch_errors >= max_retries:
                            break
                        await asyncio.sleep(2 ** fetch_errors)  # Exponential backoff

            # Bulk insert all records
            inserted = 0
            if all_records:
                inserted = await self.repository.bulk_insert_ohlcv(all_records, batch_size=1000)
                logger.info(
                    f"[BACKFILL] {symbol} {timeframe} - processed {len(all_records)} records, "
                    f"inserted {inserted}"
                )

            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "fetched": len(all_records),
                "inserted": inserted,
                "errors": fetch_errors,
                "duration_seconds": duration,
            }

        except Exception as e:
            logger.error(f"[BACKFILL] {symbol} {timeframe} failed: {e}", exc_info=True)
            duration = (datetime.now(timezone.utc) - start_time).total_seconds()
            return {
                "symbol": symbol,
                "timeframe": timeframe,
                "fetched": 0,
                "inserted": 0,
                "errors": 1,
                "error_message": str(e),
                "duration_seconds": duration,
            }

    async def backfill_multiple_symbols(
        self,
        symbols: List[str],
        timeframe: str,
        days: int = 180,
        max_parallel: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Backfill multiple symbols in parallel with controlled concurrency.

        Args:
            symbols: List of trading pair symbols
            timeframe: Candle interval
            days: Number of days to backfill
            max_parallel: Maximum number of symbols to process in parallel

        Returns:
            List of results for each symbol
        """
        logger.info(
            f"[BACKFILL] Starting batch backfill for {len(symbols)} symbols, "
            f"timeframe: {timeframe}, days: {days}, max_parallel: {max_parallel}"
        )

        semaphore = asyncio.Semaphore(max_parallel)

        async def backfill_with_semaphore(symbol: str) -> Dict[str, Any]:
            async with semaphore:
                return await self.backfill_symbol(symbol, timeframe, days)

        tasks = [backfill_with_semaphore(symbol) for symbol in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[BACKFILL] {symbols[i]} failed with exception: {result}")
                processed.append({
                    "symbol": symbols[i],
                    "timeframe": timeframe,
                    "fetched": 0,
                    "inserted": 0,
                    "errors": 1,
                    "error_message": str(result),
                })
            else:
                processed.append(result)

        # Summary stats
        total_fetched = sum(r.get("fetched", 0) for r in processed)
        total_inserted = sum(r.get("inserted", 0) for r in processed)
        total_errors = sum(r.get("errors", 0) for r in processed)
        logger.info(
            f"[BACKFILL] Batch complete - symbols: {len(symbols)}, "
            f"fetched: {total_fetched}, inserted: {total_inserted}, errors: {total_errors}"
        )

        return processed

    async def _fetch_candles_with_retry(
        self,
        symbol: str,
        timeframe: str,
        limit: int,
        to_timestamp: Optional[int] = None,
        max_retries: int = 3,
    ) -> List[List[Any]]:
        """
        Fetch candles from Gate.io API with exponential backoff retry.

        Args:
            symbol: Trading pair (e.g., "BTC_USDT")
            timeframe: Interval (e.g., "1h", "5m")
            limit: Number of candles to fetch
            to_timestamp: End timestamp (Unix seconds)
            max_retries: Maximum number of retry attempts

        Returns:
            List of raw candle arrays from Gate.io API
        """
        params = {
            "currency_pair": symbol,
            "interval": timeframe,
            "limit": limit,
        }
        if to_timestamp:
            params["to"] = to_timestamp

        last_exception = None
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(GATE_SPOT_URL, params=params)

                    # Rate limit handling
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", 5))
                        logger.warning(
                            f"[BACKFILL] Rate limited for {symbol}, "
                            f"retry after {retry_after}s (attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    candles = response.json()

                    if not isinstance(candles, list):
                        logger.warning(f"[BACKFILL] Unexpected response format for {symbol}")
                        return []

                    return candles

            except httpx.HTTPStatusError as e:
                last_exception = e
                if e.response.status_code >= 500:
                    # Server error - retry with backoff
                    wait_time = 2 ** attempt
                    logger.warning(
                        f"[BACKFILL] Server error for {symbol} (attempt {attempt + 1}/{max_retries}), "
                        f"retrying in {wait_time}s"
                    )
                    await asyncio.sleep(wait_time)
                else:
                    # Client error - don't retry
                    logger.error(f"[BACKFILL] Client error for {symbol}: {e}")
                    break

            except Exception as e:
                last_exception = e
                wait_time = 2 ** attempt
                logger.warning(
                    f"[BACKFILL] Request error for {symbol} (attempt {attempt + 1}/{max_retries}): {e}, "
                    f"retrying in {wait_time}s"
                )
                await asyncio.sleep(wait_time)

        logger.error(f"[BACKFILL] Failed to fetch {symbol} after {max_retries} attempts: {last_exception}")
        return []

    @staticmethod
    def _validate_candle(candle: Dict[str, Any]) -> bool:
        """
        Validate parsed candle data.

        Args:
            candle: Parsed candle dict with OHLCV fields

        Returns:
            True if valid, False otherwise
        """
        try:
            # Check required fields
            required = ["time", "open", "high", "low", "close", "volume", "quote_volume"]
            if not all(field in candle for field in required):
                return False

            # Validate OHLC relationships
            o, h, l, c = candle["open"], candle["high"], candle["low"], candle["close"]
            if not (l <= o <= h and l <= c <= h and l <= h):
                logger.warning(f"[BACKFILL] Invalid OHLC: O={o}, H={h}, L={l}, C={c}")
                return False

            # Validate positive values
            if any(candle[field] < 0 for field in ["open", "high", "low", "close", "volume", "quote_volume"]):
                return False

            # Validate timestamp
            if not isinstance(candle["time"], datetime):
                return False

            return True

        except (KeyError, TypeError, ValueError) as e:
            logger.warning(f"[BACKFILL] Validation error: {e}")
            return False

    async def get_backfill_status(
        self,
        symbols: List[str],
        timeframe: str,
        target_days: int = 180,
    ) -> Dict[str, Any]:
        """
        Get backfill status for multiple symbols.

        Args:
            symbols: List of trading pairs
            timeframe: Candle interval
            target_days: Target number of days to have

        Returns:
            Dict with status information per symbol
        """
        target_start = datetime.now(timezone.utc) - timedelta(days=target_days)
        status = {}

        for symbol in symbols:
            earliest = await self.repository.get_earliest_timestamp(symbol, self.exchange, timeframe)
            latest = await self.repository.get_latest_timestamp(symbol, self.exchange, timeframe)
            count = await self.repository.count_records(symbol, self.exchange, timeframe)

            status[symbol] = {
                "earliest": earliest,
                "latest": latest,
                "count": count,
                "needs_backfill": earliest is None or earliest > target_start,
                "days_available": (latest - earliest).days if (earliest and latest) else 0,
            }

        return status

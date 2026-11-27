"""One owner for governed Spot H1 candles and indicators."""
from sqlalchemy import text


async def canonical_mtf_active(db) -> bool:
    return bool(await db.scalar(text("""
        SELECT EXISTS (
            SELECT 1 FROM ohlcv_capture_contracts
             WHERE capture_contract_version = 'spot_mtf_closed_ohlcv_v2'
               AND mode = 'CANONICAL' AND canonical_read_enabled IS TRUE
               AND valid_from <= clock_timestamp()
        )
    """)))

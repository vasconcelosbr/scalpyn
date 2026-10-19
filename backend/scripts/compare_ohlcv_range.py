"""Explicit, read-only historical comparison; no periodic full-history scans.

python -m scripts.compare_ohlcv_range --start <UTC ISO> --end <UTC ISO> --timeframe 1m
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from app.schemas.ohlcv_comparison import OHLCVComparisonConfig
from app.services.ohlcv_comparison import compare_window
from app.services.research_ohlcv_service import SHADOW_STATE_TIMEFRAMES, STATE_CAPTURE_CONTRACT_VERSION


async def run(args):
    from app.database import CeleryAsyncSessionLocal
    start, end = datetime.fromisoformat(args.start), datetime.fromisoformat(args.end)
    if start.tzinfo is None or end.tzinfo is None or not start < end:
        raise ValueError('Use explicit timezones and start < end')
    async with CeleryAsyncSessionLocal() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        raw = (await db.execute(text("""SELECT p.config_json FROM config_profiles p
            JOIN ohlcv_comparison_owner o ON o.user_id=p.user_id
            WHERE p.config_type='ohlcv_comparison' AND p.pool_id IS NULL"""))).scalar_one()
        cfg = OHLCVComparisonConfig.model_validate(raw)
        valid_from = (await db.execute(text("""SELECT valid_from FROM ohlcv_capture_contracts
            WHERE capture_contract_version=:contract AND mode='SHADOW' AND canonical_read_enabled IS FALSE"""),
            {'contract':STATE_CAPTURE_CONTRACT_VERSION})).scalar_one()
    if start < valid_from or end > datetime.now(timezone.utc)-timedelta(seconds=cfg.settlement_delay_seconds):
        raise ValueError('Requested range is outside the contract or settlement cutoff')
    while start < end:
        stop = min(start+timedelta(seconds=cfg.window_seconds),end)
        async with CeleryAsyncSessionLocal() as db:
            await db.execute(text('SET TRANSACTION READ ONLY'))
            result = await compare_window(db,cfg,args.timeframe,STATE_CAPTURE_CONTRACT_VERSION,start,stop)
            print(json.dumps({'start':start,'end':stop,'summary':result}, default=str))
        start = stop


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--timeframe', choices=SHADOW_STATE_TIMEFRAMES, required=True)
    asyncio.run(run(parser.parse_args()))

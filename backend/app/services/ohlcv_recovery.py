"""Manifest-bounded closed-price recovery. Never dispatches decisions or labels.

Transactions serialize the recovery workload only (not canonical collectors).
Prices, the contiguous cursor and the receipt commit together. Arrival timestamps
are assigned by PostgreSQL at insertion, never copied from candle open time.
"""
import asyncio
import hashlib
import json
import math
from datetime import timedelta
from uuid import uuid4
import httpx
from sqlalchemy import text
from ..schemas.ohlcv_recovery import (
    OHLCVRecoveryConfig, RecoveryManifest, require_recovery_owner,
)
from .research_ohlcv_service import fetch_gate_closed_candles, _normalize_state_db_record


async def load_config(db, user_id):
    await require_recovery_owner(db, user_id)
    raw = (await db.execute(text("""
        SELECT config_json FROM config_profiles
        WHERE user_id=:uid AND pool_id IS NULL AND config_type='ohlcv_recovery'
    """), {"uid": user_id})).scalar_one_or_none()
    return OHLCVRecoveryConfig.model_validate(raw)


async def timeouts(db, cfg):
    for name, value in (("statement_timeout", cfg.statement_timeout_ms),
                        ("lock_timeout", cfg.lock_timeout_ms)):
        await db.execute(text("SELECT set_config(:name,:value,true)"),
                         {"name": name, "value": str(value)})


async def validate_scope(db, manifest, cfg):
    if (manifest.end - manifest.start).total_seconds() / manifest.seconds > cfg.max_manifest_candles:
        raise ValueError("Manifest exceeds max_manifest_candles")
    row = (await db.execute(text("""
        SELECT capture_contract_version, finalization_delay_seconds, valid_from,
               mode, closed_table, clock_timestamp() AS observed_at
        FROM ohlcv_capture_contracts WHERE timeframes ? :tf
          AND mode IN ('SHADOW','CANONICAL') AND valid_from<=clock_timestamp()
        ORDER BY valid_from DESC LIMIT 1
    """), {"tf": manifest.timeframe})).mappings().one_or_none()
    if (not row or row['mode'] != 'CANONICAL' or row['closed_table'] != 'ohlcv'
            or row['capture_contract_version'] != manifest.capture_contract_version
            or row['finalization_delay_seconds'] != manifest.finalization_delay_seconds):
        raise ValueError("Manifest does not match the active canonical capture contract")
    if manifest.start < row['valid_from']:
        raise ValueError("Historical scope predates this capture contract")
    if manifest.end + timedelta(seconds=manifest.finalization_delay_seconds) > row['observed_at']:
        raise ValueError("Manifest includes unsettled candles")


async def missing_times(db, manifest, start=None, end=None):
    return list((await db.execute(text("""
        SELECT slot FROM generate_series(CAST(:start AS timestamptz),
            CAST(:end AS timestamptz)-make_interval(secs=>:seconds),
            make_interval(secs=>:seconds)) AS slot
        WHERE NOT EXISTS (SELECT 1 FROM ohlcv o WHERE o.time=slot
            AND o.symbol=:symbol AND o.exchange=:exchange AND o.market_type=:market
            AND o.timeframe=:tf AND o.is_closed IS TRUE
            AND o.open>0 AND o.high>0 AND o.low>0 AND o.close>0)
        ORDER BY slot
    """), {"start": start or manifest.start, "end": end or manifest.end,
           "seconds": manifest.seconds, "symbol": manifest.symbol,
           "exchange": manifest.exchange, "market": manifest.market_type,
           "tf": manifest.timeframe})).scalars())


def gap_ranges(times, seconds):
    ranges = []
    step = timedelta(seconds=seconds)
    for at in times:
        if ranges and ranges[-1]['end'] == at:
            ranges[-1]['end'] = at + step
        else:
            ranges.append({'start': at, 'end': at + step})
    return ranges


async def plan(db, user_id, manifest):
    """SELECT-only preview; no exchange calls, reservation, or registration."""
    cfg = await load_config(db, user_id)
    await timeouts(db, cfg)
    await validate_scope(db, manifest, cfg)
    missing = await missing_times(db, manifest)
    return {"manifest": manifest.model_dump(mode='json'), "sha256": manifest.digest(),
            "missing_candles": len(missing), "gaps": gap_ranges(missing, manifest.seconds),
            "source_recoverability": "UNKNOWN_UNTIL_REQUESTED",
            "writes": False}


async def submit(db, user_id, manifest, expected_hash):
    if expected_hash != manifest.digest():
        raise ValueError("Reviewed manifest hash does not match")
    cfg = await load_config(db, user_id)
    if not cfg.enabled:
        raise ValueError("Recovery is disabled")
    preview = await plan(db, user_id, manifest)
    first = preview['gaps'][0]['start'] if preview['gaps'] else manifest.end
    job_id = uuid4()
    await db.execute(text("""
        INSERT INTO ohlcv_recovery_jobs(id,manifest_hash,manifest,submitted_by,next_time,status)
        VALUES(:id,:hash,CAST(:manifest AS jsonb),:uid,:next,:status)
        ON CONFLICT(manifest_hash) DO NOTHING
    """), {'id': job_id, 'hash': expected_hash, 'manifest': manifest.model_dump_json(),
           'uid': user_id, 'next': first, 'status': 'READY' if preview['gaps'] else 'COMPLETE'})
    existing = (await db.execute(text(
        "SELECT id FROM ohlcv_recovery_jobs WHERE manifest_hash=:hash"
    ), {'hash': expected_hash})).scalar_one()
    await db.commit()
    return str(existing)


async def status(db, user_id, job_id):
    await require_recovery_owner(db, user_id)
    job = (await db.execute(text(
        "SELECT * FROM ohlcv_recovery_jobs WHERE id=:id AND submitted_by=:uid"
    ), {'id': job_id, 'uid': user_id})).mappings().one()
    manifest = RecoveryManifest.model_validate(job['manifest'])
    cfg = await load_config(db, user_id)
    await timeouts(db, cfg)
    # A decreased budget must not accidentally cause an unbounded status query.
    if (manifest.end-manifest.start).total_seconds()/manifest.seconds > cfg.max_manifest_candles:
        raise ValueError("Manifest exceeds current inspection budget")
    missing = await missing_times(db, manifest)
    return {**dict(job), 'missing_candles': len(missing),
            'gaps': gap_ranges(missing, manifest.seconds)}


async def run_page(db, job_id, *, client=None):
    """One bounded request. Lock contention/backoff return without HTTP or writes.

    A task crash rolls back prices/cursor/receipt, so redelivery is safe. A
    singleton row serializes and paces ALL recovery jobs across worker processes.
    No Celery autoretry: errors are receipts with an explicit next_attempt_at.
    """
    job = (await db.execute(text(
        "SELECT * FROM ohlcv_recovery_jobs WHERE id=:id FOR UPDATE SKIP LOCKED"
    ), {'id': job_id})).mappings().one_or_none()
    if not job:
        return {'status': 'BUSY_OR_NOT_FOUND'}
    cfg = await load_config(db, job['submitted_by'])
    if not cfg.enabled:
        return {'status': 'DISABLED'}
    await timeouts(db, cfg)
    owner = (await db.execute(text("""
        SELECT next_request_at,clock_timestamp() AS now FROM ohlcv_recovery_owner
        WHERE user_id=:uid FOR UPDATE SKIP LOCKED
    """), {'uid': job['submitted_by']})).mappings().one_or_none()
    if not owner:
        return {'status': 'BUSY'}
    if job['status'] in ('COMPLETE', 'EXHAUSTED'):
        return {'status': job['status']}
    now = owner['now']
    ready_at = max(owner['next_request_at'], job['next_attempt_at'])
    if now < ready_at:
        return {'status': 'WAIT', 'next_attempt_at': ready_at}
    manifest = RecoveryManifest.model_validate(job['manifest'])
    if manifest.digest() != job['manifest_hash']:
        raise ValueError("Stored manifest hash mismatch")
    await validate_scope(db, manifest, cfg)
    missing = await missing_times(db, manifest)
    if not missing:
        await db.execute(text("UPDATE ohlcv_recovery_jobs SET status='COMPLETE',next_time=:end WHERE id=:id"),
                         {'id': job_id, 'end': manifest.end})
        await db.commit()
        return {'status': 'COMPLETE'}
    start = missing[0]
    end = min(manifest.end, start + timedelta(seconds=cfg.page_candles * manifest.seconds))
    receipt = {'outcome': 'PARTIAL', 'inserted': 0, 'decision_dispatches': 0,
               'provenance': 'HISTORICAL_RECOVERY_LATE', 'response_sha256': None}
    retry_delay = cfg.retry_delay_seconds
    try:
        async def fetch(http):
            return await fetch_gate_closed_candles(
                http, symbol=manifest.symbol, timeframe=manifest.timeframe,
                points=int((end-start).total_seconds()/manifest.seconds),
                to_timestamp=int(end.timestamp())-manifest.seconds,
                finalization_delay_seconds=manifest.finalization_delay_seconds)
        if client is None:
            async with httpx.AsyncClient(timeout=cfg.http_timeout_seconds) as http:
                batch = await asyncio.wait_for(fetch(http), timeout=cfg.http_timeout_seconds)
        else:
            batch = await asyncio.wait_for(fetch(client), timeout=cfg.http_timeout_seconds)
        records = []
        for record in batch.records:
            if (record['symbol'] != manifest.symbol or record['timeframe'] != manifest.timeframe
                    or record['exchange'] != manifest.exchange or record['market_type'] != 'spot'
                    or record['time'].timestamp() % manifest.seconds
                    or not start <= record['time'] < end):
                raise ValueError('Exchange response outside requested identity/range')
            if (any(not math.isfinite(record[field]) or record[field] <= 0
                    for field in ('open','high','low','close'))
                    or any(not math.isfinite(record[field]) or record[field] < 0
                           for field in ('volume','quote_volume'))
                    or record['high'] < max(record['open'],record['close'],record['low'])
                    or record['low'] > min(record['open'],record['close'])):
                raise ValueError('Invalid exchange price')
            records.append(_normalize_state_db_record(record))
        receipt.update({'response_observed_at': batch.observed_at,
                        'received_closed': len(records), 'rejected_open': batch.rejected_open_candles,
                        'response_sha256': hashlib.sha256(json.dumps(records, default=str,
                            sort_keys=True).encode()).hexdigest()})
    except (httpx.HTTPError, TimeoutError, RuntimeError, ValueError, TypeError, IndexError, OverflowError) as exc:
        # Never persist raw HTTP errors/URLs (may contain credentials).
        receipt['error_code'] = type(exc).__name__
        if isinstance(exc, httpx.HTTPStatusError):
            receipt['http_status'] = exc.response.status_code
        if str(exc) in ('Exchange response outside requested identity/range', 'Invalid exchange price'):
            receipt['error_code'] = str(exc)
        if str(exc).startswith('GATE_RATE_LIMITED retry_after='):
            try:
                retry_delay = max(retry_delay, math.ceil(float(str(exc).split('=', 1)[1])))
            except ValueError:
                pass
            receipt['error_code'] = 'GATE_RATE_LIMITED'
        receipt['outcome'] = 'ERROR'
        records = []
    # DB errors escape: the entire transaction must roll back, not commit a
    # checkpoint following an incomplete insert. Never touch ohlcv_live here.
    for record in records:
        result = await db.execute(text("""
            INSERT INTO ohlcv(time,symbol,exchange,timeframe,market_type,
                open,high,low,close,volume,quote_volume,is_closed,capture_contract_version,ingested_at)
            VALUES(:time,:symbol,:exchange,:timeframe,:market_type,
                :open,:high,:low,:close,:volume,:quote_volume,true,:contract,clock_timestamp())
            ON CONFLICT(time,symbol,exchange,timeframe) DO NOTHING
        """), {**record, 'contract': manifest.capture_contract_version})
        receipt['inserted'] += max(result.rowcount, 0)
    remaining = await missing_times(db, manifest)
    next_time = remaining[0] if remaining else manifest.end
    attempts = job['gap_attempts'] + 1 if next_time == start == job['next_time'] else (1 if next_time == start else 0)
    state = ('COMPLETE' if not remaining else
             'EXHAUSTED' if attempts >= cfg.max_attempts_per_gap else 'PARTIAL')
    if state == 'COMPLETE':
        receipt['outcome'] = 'COMPLETE'
    receipt.update({'remaining_candles': len(remaining), 'next_time': next_time,
                    'remaining_page_gaps': gap_ranges([t for t in remaining if t < end], manifest.seconds)})
    await db.execute(text("""
        INSERT INTO ohlcv_recovery_attempts(job_id,request_start,request_end,config_json,receipt)
        VALUES(:id,:start,:end,CAST(:cfg AS jsonb),CAST(:receipt AS jsonb))
    """), {'id': job_id, 'start': start, 'end': end, 'cfg': cfg.model_dump_json(),
           'receipt': json.dumps(receipt, default=str)})
    # Pacing starts AFTER the response, including 429 retry-after, across jobs.
    await db.execute(text("""
        UPDATE ohlcv_recovery_owner SET next_request_at=clock_timestamp()+make_interval(secs=>:delay)
        WHERE user_id=:uid
    """), {'uid': job['submitted_by'], 'delay': max(cfg.request_spacing_seconds,
                retry_delay if receipt.get('error_code') == 'GATE_RATE_LIMITED' else 0)})
    await db.execute(text("""
        UPDATE ohlcv_recovery_jobs SET next_time=:next,status=:state,gap_attempts=:attempts,
            next_attempt_at=clock_timestamp()+make_interval(secs=>:delay) WHERE id=:id
    """), {'id': job_id, 'next': next_time, 'state': state, 'attempts': attempts,
           'delay': retry_delay if next_time == start else cfg.request_spacing_seconds})
    await db.commit()
    return {'status': state, **receipt}

"""Bounded, restartable comparisons. Never writes prices or decision evidence."""
from datetime import datetime, timedelta, timezone
import json
from sqlalchemy import text
from ..schemas.ohlcv_comparison import OHLCVComparisonConfig
from .research_ohlcv_service import SHADOW_STATE_TIMEFRAMES, STATE_CAPTURE_CONTRACT_VERSION


WINDOW_SQL = text("""
WITH shadow_rows AS MATERIALIZED (
    SELECT s.* FROM ohlcv_shadow s
    WHERE s.timeframe=:timeframe AND s.capture_contract_version=:contract
      AND s.time >= :start AND s.time < :end
    ORDER BY s.time,s.symbol,s.exchange,s.market_type LIMIT :row_limit
), compared AS (
    SELECT s.time, o.time IS NOT NULL AS canonical_present,
           o.open IS NOT DISTINCT FROM s.open
           AND o.high IS NOT DISTINCT FROM s.high
           AND o.low IS NOT DISTINCT FROM s.low
           AND o.close IS NOT DISTINCT FROM s.close
           AND o.volume IS NOT DISTINCT FROM s.volume
           AND o.quote_volume IS NOT DISTINCT FROM s.quote_volume AS exact
    FROM shadow_rows s LEFT JOIN ohlcv o
      ON o.time=s.time AND o.symbol=s.symbol AND o.exchange=s.exchange
     AND o.timeframe=s.timeframe AND o.market_type=s.market_type
)
SELECT count(*)::bigint AS shadow_rows,
       count(*) FILTER (WHERE canonical_present)::bigint AS compared_rows,
       count(*) FILTER (WHERE canonical_present AND exact)::bigint AS exact_rows,
       count(*) FILTER (WHERE canonical_present AND NOT exact)::bigint AS divergent_rows,
       count(*) FILTER (WHERE NOT canonical_present)::bigint AS missing_canonical_rows
FROM compared
""")


async def compare_window(db, cfg, timeframe, contract, start, end):
    """A full-history operator replay uses this same bounded primitive.

    Results describe the source as observed, not a final proof of completeness.
    Late source arrivals require an explicit replay of the affected interval.
    """
    await db.execute(text("SELECT set_config('statement_timeout',:v,true)"),
                     {"v": str(cfg.statement_timeout_ms)})
    await db.execute(text("SELECT set_config('lock_timeout',:v,true)"),
                     {"v": str(cfg.lock_timeout_ms)})
    summary = dict((await db.execute(WINDOW_SQL, {
        "timeframe": timeframe, "contract": contract, "start": start, "end": end,
        "row_limit": cfg.max_shadow_rows + 1,
    })).mappings().one())
    if summary["shadow_rows"] > cfg.max_shadow_rows:
        raise ValueError("comparison_window_row_budget_exceeded: reduce window_seconds")
    return summary


async def run_incremental(session_factory, *, now=None):
    now = now or datetime.now(timezone.utc)
    async with session_factory() as db:
        raw = (await db.execute(text("""
            SELECT p.config_json FROM config_profiles p
            JOIN ohlcv_comparison_owner o ON o.user_id=p.user_id
            WHERE p.config_type='ohlcv_comparison' AND p.pool_id IS NULL
        """))).scalar_one_or_none()
    if raw is None:
        return [{"status": "disabled", "reason": "operator_config_missing"}]
    cfg = OHLCVComparisonConfig.model_validate(raw)
    if not cfg.enabled:
        return [{"status": "disabled", "reason": "operator_config_disabled"}]
    cutoff = now - timedelta(seconds=cfg.settlement_delay_seconds)
    results = []
    # One window per timeframe per round prevents starvation of slower frames.
    for _ in range(cfg.max_rounds_per_run):
        progressed = False
        for timeframe in SHADOW_STATE_TIMEFRAMES:
            async with session_factory() as db:
                async with db.begin():
                    # Nonblocking per-contract/timeframe lock; also protects first insert.
                    locked = (await db.execute(text(
                        "SELECT pg_try_advisory_xact_lock(hashtextextended(:key,0))"
                    ), {"key": f"ohlcv_comparison:{STATE_CAPTURE_CONTRACT_VERSION}:{timeframe}"})).scalar()
                    if not locked:
                        continue
                    valid_from = (await db.execute(text("""
                        SELECT valid_from FROM ohlcv_capture_contracts
                        WHERE capture_contract_version=:contract AND mode='SHADOW'
                          AND canonical_read_enabled IS FALSE
                    """), {"contract": STATE_CAPTURE_CONTRACT_VERSION})).scalar_one_or_none()
                    if valid_from is None:
                        continue
                    params = {"timeframe": timeframe, "contract": STATE_CAPTURE_CONTRACT_VERSION}
                    cursor = (await db.execute(text("""SELECT next_time FROM ohlcv_comparison_cursors
                        WHERE timeframe=:timeframe AND capture_contract_version=:contract"""), params)).scalar_one_or_none()
                    start = cursor or valid_from
                    end = min(start + timedelta(seconds=cfg.window_seconds), cutoff)
                    if end <= start:
                        continue
                    summary = await compare_window(db, cfg, timeframe, STATE_CAPTURE_CONTRACT_VERSION, start, end)
                    await db.execute(text("""INSERT INTO ohlcv_comparison_windows
                        (timeframe,capture_contract_version,window_start,window_end,observed_at,config_json,summary)
                        VALUES(:timeframe,:contract,:start,:end,:now,CAST(:config AS jsonb),CAST(:summary AS jsonb))"""),
                        {**params, "start": start, "end": end,
                         "config": cfg.model_dump_json(), "summary": json.dumps(summary),
                         "now": datetime.now(timezone.utc)})
                    await db.execute(text("""INSERT INTO ohlcv_comparison_cursors(timeframe,capture_contract_version,next_time)
                        VALUES(:timeframe,:contract,:end)
                        ON CONFLICT(timeframe,capture_contract_version) DO UPDATE
                        SET next_time=excluded.next_time,updated_at=clock_timestamp()"""), {**params, "end": end})
                    results.append({"timeframe": timeframe, "start": start, "end": end, **summary})
                    progressed = True
        if not progressed:
            break
    return results

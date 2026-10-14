"""Small, recoverable batches on an immutable allowlist of operational tables.

Neither a config nor a request can name a different table. With
archive_before_delete, source deletion and the gzip archive commit together and
errors roll both back; without it, only the content hash of each batch is kept.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections import Counter
from datetime import timedelta
from uuid import uuid4

from ..schemas.storage_retention import StorageRetentionConfig

TABLES = (
    "ohlcv_state_ingestion_observations",
    "ohlcv_settlement_latency_samples",
    "pump_opportunity_label_queue",
    "pump_opportunity_observations",
    "pump_opportunity_price_paths",
)
# Pump collection had no retention and filled its storage budget (2026-10-14).
# Labels and queue rows go with their observation, unarchived, so these tables
# only run when archive_before_delete is off.
PUMP_COLLECTION_TABLES = TABLES[3:]
OBSERVATION_CHILDREN = ("pump_opportunity_labels", "pump_opportunity_label_queue")


def active_tables(cfg):
    return TABLES if not cfg.archive_before_delete else TABLES[:3]


def selection_sql(table):
    if table == TABLES[0]:
        # Keep EXISTS correlated (OFFSET 0) so LIMIT uses the ordered index
        # instead of a global semijoin/sort over the historical telemetry.
        # rejected_from_closed_rows counts normal open/unsettled candles routed
        # to ohlcv_live. It is not an ingestion error; status/error_code are.
        return """WITH fresh AS MATERIALIZED (
            SELECT symbol,timeframe,capture_contract_version
            FROM ohlcv_state_ingestion_observations WHERE observed_at >= $1
            ORDER BY observed_at DESC LIMIT $4
        ) SELECT t.ctid::text tid,to_jsonb(t)::text data FROM ohlcv_state_ingestion_observations t
        WHERE t.observed_at < $1 AND t.status='ok' AND t.error_code IS NULL
          AND EXISTS (SELECT 1 FROM fresh f WHERE f.symbol=t.symbol AND f.timeframe=t.timeframe
                      AND f.capture_contract_version=t.capture_contract_version OFFSET 0)
          AND NOT EXISTS (SELECT 1 FROM storage_retention_holds h WHERE h.table_name=$5
              AND (h.symbol IS NULL OR h.symbol=t.symbol)
              AND t.observed_at BETWEEN h.starts_at AND h.ends_at OFFSET 0)
          AND $3::uuid IS NOT NULL
        ORDER BY t.observed_at DESC,t.symbol DESC,t.timeframe DESC,t.capture_contract_version DESC
        LIMIT $2 FOR UPDATE OF t SKIP LOCKED"""
    if table == TABLES[1]:
        return """SELECT t.ctid::text tid,to_jsonb(t)::text data FROM ohlcv_settlement_latency_samples t
        WHERE t.observed_at < $1 AND t.found AND t.is_closed
          AND EXISTS (SELECT 1 FROM ohlcv_settlement_latency_samples f
              WHERE f.symbol=t.symbol AND f.timeframe=t.timeframe AND f.observed_at >= $1
                AND f.candle_open_time >= $1 LIMIT 1 OFFSET 0)
          AND NOT EXISTS (SELECT 1 FROM storage_retention_holds h WHERE h.table_name=$5
              AND (h.symbol IS NULL OR h.symbol=t.symbol)
              AND t.observed_at BETWEEN h.starts_at AND h.ends_at OFFSET 0)
          AND $3::uuid IS NOT NULL AND $4::integer > 0
        ORDER BY t.symbol,t.timeframe,t.candle_open_time,t.delay_target_seconds
        LIMIT $2 FOR UPDATE OF t SKIP LOCKED"""
    if table == TABLES[2]:
        return """SELECT t.ctid::text tid,to_jsonb(t)::text data FROM pump_opportunity_label_queue t
        WHERE t.user_id=$3 AND t.completed_at < $1 AND t.resource_block IS NULL
          AND EXISTS (SELECT 1 FROM pump_opportunity_labels l
              WHERE l.observation_id=t.observation_id AND l.label_spec_hash=t.label_spec_hash
                AND l.horizon_minutes=t.horizon_minutes OFFSET 0)
          AND NOT EXISTS (SELECT 1 FROM storage_retention_holds h WHERE h.table_name=$5
              AND t.completed_at BETWEEN h.starts_at AND h.ends_at OFFSET 0)
          AND $4::integer > 0
        ORDER BY t.completed_at,t.observation_id,t.label_spec_hash,t.horizon_minutes
        LIMIT $2 FOR UPDATE OF t SKIP LOCKED"""
    # Pump tables are never archived, so they select only their identity: reading
    # the whole row would detoast payloads the slow volume cannot afford.
    if table == TABLES[3]:
        # Observations scored by a model are model evidence and stay.
        return """SELECT t.ctid::text tid,
            json_build_object('observation_id',t.observation_id,'slot_at',t.slot_at)::text data
        FROM pump_opportunity_observations t
        WHERE t.user_id=$3 AND t.slot_at < $1
          AND NOT EXISTS (SELECT 1 FROM pump_ml_predictions p WHERE p.observation_id=t.observation_id OFFSET 0)
          AND NOT EXISTS (SELECT 1 FROM storage_retention_holds h WHERE h.table_name=$5
              AND t.slot_at BETWEEN h.starts_at AND h.ends_at OFFSET 0)
          AND $4::integer > 0
        ORDER BY t.user_id,t.slot_at DESC,t.observation_id
        LIMIT $2 FOR UPDATE OF t SKIP LOCKED"""
    if table == TABLES[4]:
        return """SELECT t.ctid::text tid,
            json_build_object('instrument_id',t.instrument_id,'bucket_start',t.bucket_start,
                              'content_hash',t.content_hash)::text data
        FROM pump_opportunity_price_paths t
        WHERE t.user_id=$3 AND t.bucket_start < $1
          AND NOT EXISTS (SELECT 1 FROM storage_retention_holds h WHERE h.table_name=$5
              AND t.bucket_start BETWEEN h.starts_at AND h.ends_at OFFSET 0)
          AND $4::integer > 0
        ORDER BY t.user_id,t.instrument_id,t.bucket_start
        LIMIT $2 FOR UPDATE OF t SKIP LOCKED"""
    raise ValueError("table_not_allowlisted")


def pack_archive(rows):
    # Keep PostgreSQL's original numeric literals, never round-trip through float.
    raw = ("[" + ",".join(r["data"] for r in rows) + "]").encode()
    archive = gzip.compress(raw, mtime=0)
    if gzip.decompress(archive) != raw:
        raise ValueError("archive_verification_failed")
    return archive, hashlib.sha256(raw).hexdigest(), len(raw)


def policy_hash(cfg):
    contract = {"config": cfg.model_dump(exclude={"mode", "enabled"}),
                "queries": [selection_sql(table) for table in TABLES]}
    return hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()


async def process_batch(conn, owner, run_id, table, cutoff, cfg):
    sql = selection_sql(table)
    async with conn.transaction():
        await conn.execute("SELECT set_config('statement_timeout',$1,true)", str(cfg.statement_timeout_ms))
        await conn.execute("SELECT set_config('lock_timeout',$1,true)", str(cfg.lock_timeout_ms))
        await conn.execute("SELECT set_config('temp_file_limit',$1,true)", f"{cfg.max_temp_file_mb}MB")
        # No newly placed hold may race with a batch's selection/deletion.
        await conn.execute("LOCK TABLE storage_retention_holds IN SHARE MODE")
        before = await conn.fetchval("SELECT pg_total_relation_size($1::regclass)", table)
        rows = await conn.fetch(sql, cutoff, cfg.batch_rows, owner, cfg.fresh_sample_rows, table)
        if cfg.archive_before_delete:
            archive, digest, raw_bytes = pack_archive(rows)
        else:
            # No archive by owner decision; the content hash still records what was deleted.
            raw = ("[" + ",".join(r["data"] for r in rows) + "]").encode()
            archive, digest, raw_bytes = None, hashlib.sha256(raw).hexdigest(), len(raw)
        deleted = 0
        children = {}
        if cfg.mode == "execute" and rows:
            if archive is not None:
                restored = await conn.fetch(
                    f"SELECT to_jsonb(r)::text data FROM jsonb_populate_recordset(NULL::{table},$1::jsonb) r",
                    gzip.decompress(archive).decode())
                if Counter(r["data"] for r in restored) != Counter(r["data"] for r in rows):
                    raise RuntimeError("archive_restore_mismatch")
            if table == TABLES[3]:
                ids = [json.loads(r["data"])["observation_id"] for r in rows]
                for child in OBSERVATION_CHILDREN:
                    result = await conn.execute(f"DELETE FROM {child} WHERE observation_id = ANY($1::uuid[])", ids)
                    children[child] = int(result.split()[-1])
            result = await conn.execute(
                f"DELETE FROM {table} WHERE ctid IN (SELECT unnest($1::text[])::tid)", [r["tid"] for r in rows])
            deleted = int(result.split()[-1])
            if deleted != len(rows):
                raise RuntimeError("deleted_count_mismatch")
        after = await conn.fetchval("SELECT pg_total_relation_size($1::regclass)", table)
        daily_counts = Counter(next((str(d[k]) for k in ("observed_at", "completed_at", "slot_at", "bucket_start")
                                     if d.get(k)), "")[:10] for d in (json.loads(r["data"]) for r in rows))
        summary = {"mode": cfg.mode, "source_bytes_before": before, "source_bytes_after": after,
                   "daily_counts": dict(daily_counts), "restore_verified": bool(deleted) and archive is not None,
                   "raw_archive_bytes": raw_bytes, "compressed_archive_bytes": len(archive) if archive else 0,
                   "query_sha256": hashlib.sha256(sql.encode()).hexdigest(),
                   "archived": archive is not None, "archive_verified": archive is not None,
                   "children_deleted": children}
        await conn.execute("""INSERT INTO storage_retention_batches
            (batch_id,run_id,table_name,cutoff,candidate_count,deleted_count,archive_sha256,archive_gzip,summary)
            VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)""", uuid4(), run_id, table, cutoff,
            len(rows), deleted, digest, archive if deleted else None, json.dumps(summary))
    return {"table": table, "candidates": len(rows), "deleted": deleted, **summary}


async def run_retention(conn, owner, *, force=False, preview=False):
    """Only provisioned operators with one active global policy can run retention."""
    import asyncpg
    locked = []
    run_id = None
    started = time.monotonic()
    try:
        for key in ("storage_retention", f"pump_ml:{owner}", f"pump_ml:candle:{owner}",
                    f"pump_ml:candle_ablation:{owner}", f"pump_ml:taker_flow_ablation:{owner}"):
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtextextended($1,0))", key):
                return {"status": "busy"}
            locked.append(key)
        policies = await conn.fetch("""SELECT DISTINCT ON (c.user_id) c.user_id,c.config_json
            FROM config_profiles c JOIN storage_retention_owners o USING(user_id)
            WHERE c.config_type='storage_retention' AND c.pool_id IS NULL AND c.is_active IS NOT FALSE
            ORDER BY c.user_id,c.updated_at DESC NULLS LAST""")
        active = [(r["user_id"], StorageRetentionConfig.model_validate(json.loads(r["config_json"])))
                  for r in policies]
        enabled = [(u, c) for u, c in active if c.enabled]
        if len(enabled) != 1 or enabled[0][0] != owner:
            return {"status": "disabled_or_conflicting_policies"}
        cfg = enabled[0][1]
        valid_indexes = await conn.fetchval("""SELECT count(*) FROM pg_index WHERE indisvalid
            AND indexrelid IN (to_regclass('ix_retention_ingestion_ok'),to_regclass('ix_retention_completed_labels'))""")
        if valid_indexes != 2:
            return {"status": "indexes_required"}
        if not force and await conn.fetchval("""SELECT EXISTS(SELECT 1 FROM storage_retention_runs
                WHERE user_id=$1 AND started_at>=now()-make_interval(secs=>$2))""", owner, cfg.cadence_seconds):
            return {"status": "not_due"}
        if preview:
            cfg = cfg.model_copy(update={"mode": "dry_run"})
        eligibility_hash = policy_hash(cfg)
        if cfg.mode == "execute" and not await conn.fetchval("""SELECT EXISTS(
            SELECT 1 FROM storage_retention_runs WHERE user_id=$1 AND status='completed'
            AND config_json->>'mode'='dry_run' AND summary->>'policy_hash'=$2)""", owner, eligibility_hash):
            return {"status": "dry_run_required"}
        frozen = cfg.model_dump_json()
        digest = hashlib.sha256(frozen.encode()).hexdigest()
        now = await conn.fetchval("SELECT now()")
        run_id = uuid4()
        await conn.execute("""INSERT INTO storage_retention_runs(run_id,user_id,status,config_hash,config_json)
            VALUES($1,$2,'running',$3,$4::jsonb)""", run_id, owner, digest, frozen)
        wal_start = await conn.fetchval("SELECT pg_current_wal_lsn()::text")
        batches = []
        status = "completed"
        # Round-robin prevents telemetry from starving queue cleanup.
        exhausted = set()
        tables = active_tables(cfg)
        for _ in range(cfg.max_batches_per_run):
            for table in tables:
                if table in exhausted:
                    continue
                if len(batches) >= cfg.max_batches_per_run:
                    status = "batch_budget"; break
                if time.monotonic() - started >= cfg.max_runtime_seconds:
                    status = "runtime_budget"; break
                wal_bytes = await conn.fetchval("SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),$1::text::pg_lsn)", wal_start)
                if wal_bytes >= cfg.max_wal_bytes_per_run:
                    status = "wal_budget"; break
                days = (cfg.completed_label_queue_retention_days if table == TABLES[2]
                        else cfg.pump_collection_retention_days if table in PUMP_COLLECTION_TABLES
                        else cfg.telemetry_retention_days)
                try:
                    result = await process_batch(conn, owner, run_id, table, now-timedelta(days=days), cfg)
                except asyncpg.QueryCanceledError:
                    # A slow pump batch (its transaction already rolled back) must
                    # not abort telemetry cleanup; it is retried next run.
                    if table not in PUMP_COLLECTION_TABLES:
                        raise
                    batches.append({"table": table, "candidates": 0, "deleted": 0, "error": "statement_timeout"})
                    exhausted.add(table)
                    continue
                batches.append(result)
                if result["candidates"] < cfg.batch_rows or cfg.mode == "dry_run":
                    exhausted.add(table)
            if status != "completed" or len(exhausted) == len(tables):
                break
        if status == "completed" and len(exhausted) < len(tables):
            status = "batch_budget"
        result = {"status": status, "run_id": str(run_id), "mode": cfg.mode,
                  "policy_hash": eligibility_hash,
                  "batches": batches, "deleted": sum(b["deleted"] for b in batches)}
        await conn.execute("UPDATE storage_retention_runs SET status=$2,finished_at=now(),summary=$3::jsonb WHERE run_id=$1",
                           run_id, status, json.dumps(result))
        return result
    except Exception as exc:
        if run_id:
            await conn.execute("UPDATE storage_retention_runs SET status='failed',finished_at=now(),summary=$2::jsonb WHERE run_id=$1",
                               run_id, json.dumps({"error_type": type(exc).__name__}))
        raise
    finally:
        for key in reversed(locked):
            await conn.execute("SELECT pg_advisory_unlock(hashtextextended($1,0))", key)


async def scheduled_run(owner=None, *, force=False, preview=False):
    import asyncpg
    from uuid import UUID
    from ..config import settings
    url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url, command_timeout=30)
    try:
        owners = await conn.fetch("SELECT user_id FROM storage_retention_owners ORDER BY authorized_at")
        return [await run_retention(conn, r["user_id"], force=force, preview=preview)
                for r in owners if owner is None or r["user_id"] == UUID(str(owner))]
    finally:
        await conn.close()

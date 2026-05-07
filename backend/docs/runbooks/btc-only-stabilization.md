# Runbook — BTC-only pipeline stabilization (Task #234)

## Context

In May 2026 production was reduced to a BTC-only pool (single symbol with
`pool_coins.is_active=true`). The reduced surface area exposed several
amplification effects that were previously masked by parallelism across
~30 symbols. This runbook captures the six hotfixes shipped under
Task #234 and the operator playbook that pairs with each.

## Symptom → fix matrix

| Symptom | Hotfix | Verification |
|---|---|---|
| `compute_scores` errors with `column "scoring_version" of relation "alpha_scores" does not exist` | Per-INSERT schema-drift guard `_is_scoring_version_drift` + legacy-INSERT fallback in `backend/app/tasks/compute_scores.py` | Logs show `[compute_scores] SCHEMA DRIFT` exactly once; `SELECT COUNT(*) FROM alpha_scores WHERE time > NOW()-INTERVAL '15min'` returns rows |
| Celery worker burns retries when sim batch finds zero recent candles | `simulation_service.run_simulation_batch` returns `{"status":"skipped","reason":"no_recent_candles"\|"insufficient_candles"}` instead of raising | Logs show `[SIM-SKIP] reason=...`; metric `simulation_skipped_total{reason="..."}` increases |
| Three schedulers contend for the same `BACKGROUND_SCHEDULER_CONCURRENCY` budget | Dedicated env vars `STRUCTURAL_SCHEDULER_CONCURRENCY`, `MICROSTRUCTURE_SCHEDULER_CONCURRENCY`, `PIPELINE_SCHEDULER_CONCURRENCY` (each falls back to the legacy global) | Boot logs show the resolved value per scheduler; `lsof -p $(pidof uvicorn)` shows DB connection count below pool budget |
| `/ws/decisions` returned 503 on broker hiccup, killed the front-end panel for the session | Try/except wrapper sends a `{"event":"degraded","status":"degraded"}` frame and KEEPS the socket open with a 10-15 s heartbeat loop; cumulative time tracked in `ws_degraded_seconds{endpoint="/ws/decisions"}` | UI shows yellow "degraded" badge instead of blank panel; backend logs `[WS /ws/decisions] entering degraded mode`; `ws_degraded_active{endpoint="/ws/decisions"} > 0` while degraded |
| OHLCV feed silence not visible until the `ingestion_stale` alert fired ~10 min later | Five structured logs `[OHLCV-RX\|PERSIST\|LATEST\|STALE\|COMMIT]` and three Prometheus metrics in `tasks/collect_market_data.py` | `grep '\[OHLCV-' celery-worker.log` shows one block per symbol per cycle; metric `ohlcv_staleness_seconds{symbol="BTC_USDT"}` < 1800 |
| Structural scheduler suspected to deadlock under BTC-only contention | Audit findings documented in `structural-scheduler-locking.md`; `SET LOCAL lock_timeout='3s'` already in place | See companion runbook |

## Production schema repair (the `scoring_version` drift)

The structural fix is migration `028_alpha_scores_confidence_weighting.py`,
which adds `alpha_scores.scoring_version` (NULLable, server_default `'v1'`)
plus the supporting index. It is part of the Alembic head and is applied at
Cloud Run startup by `start.sh` (`alembic upgrade head` with retry/timeout —
see `replit.md > Architecture decisions > Schema Bootstrap Robustness`). The
runtime fallback in `compute_scores` exists only as defense-in-depth for the
window between detecting drift and the next deploy completing the upgrade —
follow-up Task #235 closes the loop by promoting the column to
`CRITICAL_COLUMNS` once production is verified at head.


The drift handler in `compute_scores` is a band-aid. Permanent fix:

```sql
-- One-shot repair (Cloud SQL):
SELECT column_name FROM information_schema.columns
 WHERE table_name='alpha_scores' AND column_name='scoring_version';
-- If empty:
ALTER TABLE alpha_scores ADD COLUMN scoring_version VARCHAR(16) DEFAULT 'v1';
UPDATE alpha_scores SET scoring_version='v1' WHERE scoring_version IS NULL;
```

After repair, deploy N+1 with `("alpha_scores","scoring_version")` added to
`CRITICAL_COLUMNS` in `backend/app/_critical_schema.py` so future drift
fails the boot gate. **Do not add to `CRITICAL_COLUMNS` in the same deploy
as the column repair** — see the N+1 rule in `replit.md` and the Alembic
guardrails skill.

## Concurrency tuning (BTC-only defaults)

With one symbol, all three schedulers are I/O-bound on a single Gate.io
HTTP request per cycle. Defaults are safe but can be lowered to free up
DB connections for API handlers:

```bash
STRUCTURAL_SCHEDULER_CONCURRENCY=1
MICROSTRUCTURE_SCHEDULER_CONCURRENCY=1   # microstructure scheduler
PIPELINE_SCHEDULER_CONCURRENCY=1
```

Bumping any of these above 4 risks `QueuePool limit of size 5 reached`
cascades — see the gotchas section in `replit.md`.

## Alert wiring (Grafana)

Add the following panels to the `Centro Operacional` dashboard:

* **OHLCV freshness** — `ohlcv_staleness_seconds`, threshold 1800s.
* **Simulation skips** — `rate(simulation_skipped_total[15m])`, alert
  if any reason is non-zero for >30 minutes.
* **Decision WS degrades** — `count_over_time({log_pattern="[WS /ws/decisions]
  degraded"}[10m])`, alert on > 5.

## Rollback

Each item is independent and can be reverted without coordination:

* Sim skip → revert `simulation_service.py` to raise.
* Per-scheduler env vars → unset; fallback to global is automatic.
* `/ws/decisions` degrade mode → revert to bare `try/except WebSocketDisconnect`.
* OHLCV instrumentation → drop logs/metrics; no schema or behavior impact.
* `scoring_version` fallback → only reachable when prod schema is broken;
  revert after the column is repaired.

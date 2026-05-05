# Celery queue topology (Task #216)

## TL;DR

Three Celery queues, deployed as separate Cloud Run services. A slow
indicator compute on the structural worker can never starve a force-close
decision on the execution worker, because they consume from physically
different queues with isolated worker pools.

| Queue            | Cadence    | Workers (Cloud Run service) | Tasks                                                                                                                 |
| ---------------- | ---------- | --------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `microstructure` | 5 minutes  | `scalpyn-worker-micro`      | `collect_5m`, `compute_5m`, `pipeline_scan.scan`                                                                      |
| `structural`     | hourly+    | `scalpyn-worker-structural` | `collect_all`, `compute`, `score`, `discover`, `fetch_market_caps`, `macro_regime.update`, `simulation.*`, `daily_summary.send`, `robust_alerts.evaluate`, `symbol_health_audit.*`, `ohlcv_backfill.*` |
| `execution`      | sub-minute | `scalpyn-worker-execution`  | `evaluate_signals.evaluate`, `execute_buy.execute_buy_cycle`, `anti_liq_monitor.monitor`                              |

The single `scalpyn-beat` service runs Celery beat (the scheduler) — it
does **not** consume any queue. The single `scalpyn-api` service runs
FastAPI + uvicorn and never embeds a Celery worker.

## Why three queues?

Before the split, every task landed on a single `celery` queue and a
single worker pool drained it. Two real-world incidents motivated the
split:

1. A 1-hour `compute_indicators.compute` slowdown to ~5 minutes per
   universe sweep blocked the 60-second `evaluate_signals.evaluate`
   tick for the entire duration. Late buys, missed force-close
   triggers, anti_liq monitoring delayed by hundreds of seconds.
2. A burst of `simulation.run_trade_simulation` work piled 30k items
   into the queue. Beat's `pipeline_scan` ticks were FIFO behind the
   sims and arrived 12 minutes late, repeatedly.

With three queues the trading critical path runs on its own workers
that only consume `execution`. A pile-up on `structural` cannot delay
it.

## Cost guards

Every task carries `time_limit`, `soft_time_limit`, `rate_limit`, and
`max_retries=3` (with bounded backoff) via
`celery_app.conf.task_annotations` in `backend/app/tasks/celery_app.py`.
Defaults per queue:

| Queue            | `time_limit` | `soft_time_limit` | Default `rate_limit` |
| ---------------- | -----------: | ----------------: | -------------------- |
| `microstructure` |        180 s |             150 s | `12/m`               |
| `structural`     |        600 s |             540 s | `2/m`                |
| `execution`      |        120 s |             100 s | `4/m`                |

Anti-liquidation force-close (`anti_liq_monitor.monitor`) overrides
`max_retries=0` because a duplicated close attempt is dangerous.

## Mandatory dedup wrapper

Every Celery enqueue from inside `app/tasks/` MUST go through
`app.tasks.task_dispatch.enqueue()`. Direct `celery_app.send_task()` and
`<task>.apply_async()` are forbidden in `app/tasks/` and the lint test
`backend/tests/test_celery_routing_invariants.py` fails the build if it
finds either pattern.

The wrapper does `SET NX EX <ttl_seconds>` against Redis. If the lock
is already held, the duplicate is dropped and an INFO line is logged
(`DEDUP_SKIP task=… key=…`). The lock is released by the
`task_postrun` signal so legitimate retries are not blocked. If Redis
is unreachable, we fail open (enqueue anyway) and surface the failure
in `/api/system/celery-status`.

## Per-queue depth + age + hysteresis alert

`/api/system/celery-status` reports per-queue:

```json
{
  "queues": {
    "microstructure": {"depth": 12, "oldest_age_s": 0.4, "alert_state": "ok"},
    "structural":     {"depth": 7,  "oldest_age_s": 1.1, "alert_state": "ok"},
    "execution":      {"depth": 0,  "oldest_age_s": null, "alert_state": "ok"}
  }
}
```

Hysteresis to avoid alert flapping:

* `depth >= 10_000` and state was `ok` → log `CRITICAL`, set state to
  `alerted`, do not re-alert until depth drops below 8_000.
* `depth < 8_000` and state was `alerted` → reset state to `ok`. Next
  crossing of 10_000 will alert again.

The 10_000 / 8_000 numbers were sized against the
`simulation.run_trade_simulation` burst that defined the worst-case
queue length the system had to absorb without firing during normal
operation.

## Cloud Run multi-service deployment

The `cloudbuild.yaml` in this repo deploys the **api** service. The
worker / beat services share the same image (built once) and are
deployed by separate Cloud Build configs that override
`WORKER_QUEUES`, `RUN_BEAT`, and `--min-instances`. Concretely:

| Service                       | `--min-instances` | `WORKER_QUEUES`                            | `RUN_BEAT` | Notes                                                |
| ----------------------------- | ----------------: | ------------------------------------------ | ---------- | ---------------------------------------------------- |
| `scalpyn` (api)               |                 1 | _(unused — api does not run a worker)_     | n/a        | Set `WORKER_QUEUES=` empty + skip celery in start.sh |
| `scalpyn-beat`                |                 1 | _(unused — beat does not consume tasks)_   | `1`        | Single beat instance globally — never run two.       |
| `scalpyn-worker-micro`        |                 1 | `microstructure`                           | `0`        |                                                      |
| `scalpyn-worker-structural`   |                 1 | `structural`                               | `0`        |                                                      |
| `scalpyn-worker-execution`    |                 1 | `execution`                                | `0`        | Smallest CPU footprint, lowest latency budget.       |

In dev / Replit we keep a single container with
`WORKER_QUEUES=microstructure,structural,execution` and `RUN_BEAT=1`
so one image runs the whole pipeline.

## Operator playbook — queue is alerting

1. Open `/api/system/celery-status` and note which queue is in
   `alerted`.
2. Check `oldest_age_s` for that queue. If it is climbing linearly
   the workers are dead/unscheduled — check Cloud Run service logs for
   the matching worker service (`scalpyn-worker-{micro,structural,execution}`).
3. If workers are alive but `oldest_age_s` is stable, the workers are
   running but undersized — bump `--max-instances` for the matching
   service.
4. To clear a non-recoverable backlog, the operator runbook
   sanctions `redis-cli -u "$REDIS_URL" DEL <queue_name>`. Do this only
   after confirming the queue tasks are non-critical (sims, audits) or
   have already been satisfied by a later cycle.

## Architectural invariants enforced at lint level

`backend/tests/test_celery_routing_invariants.py` asserts:

1. `get_merged_indicators` is the only sanctioned read path for
   indicators inside the four decision tasks (`evaluate_signals`,
   `execute_buy`, `pipeline_scan`, `compute_scores`).
2. Each consumer asserts `is_complete()` before scoring/decision.
3. No raw `send_task()` / `apply_async()` inside `app/tasks/` outside
   `task_dispatch.py`.
4. Every registered task name appears in `celery_app.conf.task_routes`.
5. Pool universe queries always include `is_approved = true`.

Adding a new task without updating `TASK_ROUTES` (and ideally
`TASK_ANNOTATIONS`) will fail the lint test.

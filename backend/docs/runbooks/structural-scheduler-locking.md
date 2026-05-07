# Runbook — Structural scheduler locking audit (Task #234)

## Scope

Audit of `backend/app/services/structural_scheduler_service.py` for deadlock
risk under the BTC-only pool, plus the operator playbook for the lock-related
warnings the scheduler now emits.

## Locking surface

The scheduler writes to two tables per symbol per cycle. Both writes happen
inside a single outer transaction opened by `run_db_task` (`async with
session.begin()`), with each write isolated in its own SAVEPOINT.

| Write | SQL | Lock | Timeout |
|---|---|---|---|
| `_persist_indicators` | `INSERT INTO indicators (...) ON CONFLICT (time,symbol,timeframe) DO UPDATE` | Row lock on `(time,symbol,timeframe)` PK | `lock_timeout='3s'` |
| `_refresh_market_metadata` | `INSERT INTO market_metadata (...) ON CONFLICT (symbol) DO UPDATE` | Row lock on `(symbol)` PK | `lock_timeout='0'` (wait) |

`SET LOCAL lock_timeout='3s'` is set before the indicators write and reset to
`'0'` before the metadata write — see `_refresh_one_symbol` in
`backend/app/services/structural_scheduler_service.py`.

## Concurrent writers

| Writer | Table | Same-row contention with structural? |
|---|---|---|
| `microstructure_scheduler_service` | `indicators` | **YES** — both write `(time,symbol,'1h')` if cycles align. Different `scheduler_group` values are folded into the same row by `ON CONFLICT DO UPDATE`. |
| `tasks/compute_indicators` (Celery) | `indicators` | YES — historical legacy path; idle when both schedulers are enabled, but capable of waking up via beat. |
| `tasks/collect_market_data` | `market_metadata` | YES — long NullPool transaction holds row locks for several seconds. The `lock_timeout='0'` reset above is precisely so structural waits instead of aborting. |
| Anything | `ohlcv` | No — structural never writes to `ohlcv`. |

## Deadlock-cycle analysis

A deadlock requires two transactions to acquire the same two locks in
opposite orders. The structural scheduler always acquires in the order
`indicators → market_metadata` (per-symbol, single SAVEPOINT each). The
microstructure scheduler writes `indicators` only — no second resource —
so it cannot complete the cycle. `collect_market_data` writes `ohlcv`
then `market_metadata` — disjoint from indicators.

**Conclusion:** the topology cannot deadlock. Lock-timeout `cancelling
statement due to lock timeout` errors observed in production correspond
to *contention*, not *cycles*. The 3 s timeout intentionally aborts the
indicators write so the scheduler abandons that symbol and moves on
rather than blocking the worker pool.

## Why BTC-only amplifies the symptom

With a single symbol, structural and microstructure schedulers run their
entire cycle in a few hundred ms. When their wall-clock cycles overlap
(15-min vs 5-min cadence) they hit the same `(time,symbol,'1h')` row in
the same second; the second writer waits up to 3 s and then aborts. With
30 symbols this was statistically rare; with 1 symbol it happens on every
overlapping tick.

## Observability

Existing logs already cover both classes:

* `[STRUCT-SCHED] indicators insert failed for BTC_USDT: ... lock timeout` →
  contention; treat as benign if rate < 1/min.
* `[STRUCT-SCHED] market_metadata upsert failed for BTC_USDT: ...` →
  WARNING level when `"lock timeout"` is in the message, ERROR otherwise.

Alert thresholds:

* Lock-timeout WARNs > 10/min → reduce `STRUCTURAL_SCHEDULER_CONCURRENCY`
  or stagger `MICROSTRUCTURE_SCHEDULER_INTERVAL_SECONDS`.
* Lock-timeout WARNs > 100/min → switch the affected scheduler to the
  persistence queue (`USE_PERSISTENCE_QUEUE=1`); workers there serialize
  per-table so contention disappears.

## Mitigations evaluated

| Mitigation | Verdict |
|---|---|
| `SELECT ... FOR UPDATE` ordering | Rejected — adds round-trip; current `ON CONFLICT DO UPDATE` is already atomic. |
| Advisory lock keyed on `(symbol,timeframe)` | Rejected — moves contention from row to advisory level; same wait/abort outcome. |
| Stagger schedulers by N seconds | Adopted as operator knob (cron offsets via `*_SCHEDULER_INTERVAL_SECONDS`). |
| Persistence queue (Task #226) | **Recommended in production.** Single-writer-per-table eliminates the contention class entirely. Opt-in via `USE_PERSISTENCE_QUEUE=1`; structural/microstructure already enqueue when the flag is set. |

## TL;DR

* No deadlock cycle is reachable from the current code paths.
* The 3 s `lock_timeout` already aborts cleanly under contention.
* For sustained contention, enable the persistence queue rather than
  tuning timeouts further.

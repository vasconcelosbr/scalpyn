# MTF observer discarded before execution

## Cause and evidence

The continuous Shadow monitor shared `structural_compute` with MTF observations. Its Celery rate bucket could retain the only prefetched message before sending it to the pool. An idle pool therefore did not imply an available consumer. Tasks behind the retained monitor expired in Redis and were discarded as soon as consumed.

Runtime inspection [query/config]:

```json
{"shadow_rate_limit":"12/h","prefetch":1}
{"active":[],"reserved":[],"scheduled":[],"prefetch_count":1,"max-concurrency":1}
{"task":"app.tasks.shadow_trade_monitor.run","id":"b3a920ca-2468-4834-87da-62c56f1ba8f2","expires":"2026-11-26T12:17:43.165055+00:00"}
```

The last entry was still in the broker's unacknowledged set. Celery's rate-bucket requests are absent from `inspect.active`, `reserved` and ETA `scheduled`. Runtime source inspection confirmed `_limit_task` keeps the request in a token bucket and schedules a timer before pool dispatch.

Effective beat configuration [query/config]:

```json
{"shadow_trade_monitor":{"schedule":300.0,"options":{"queue":"structural_compute","expires":900.0}},"mtf_chart_after_close":{"schedule":"*/5 minutes","options":{"queue":"structural_compute","expires":240}}}
```

One concrete receive/execution pair [query: worker logs]:

```text
2026-11-26 08:59:16.447-03:00 Task app.tasks.shadow_trade_monitor.run[60ff6fce-9149-425d-a8dc-190a9cd8b4ad] received
2026-11-26 09:04:14.671-03:00 Task app.tasks.shadow_trade_monitor.run[60ff6fce-9149-425d-a8dc-190a9cd8b4ad] succeeded in 3.3417858965694904s
2026-11-26 09:04:14.677-03:00 Discarding revoked task: app.tasks.mtf_chart.observe[4d8cddaa-1616-4dff-8a44-e68738ffd387]
```

Task-success duration excludes the preceding rate wait. Ranking only execution durations understated this Shadow monitor's effect on observation delivery. MTF's own expensive calculation remains a separate optimization concern.

## Correction

Set the continuous monitor's effective annotation `rate_limit` to `None`. Preserve the existing beat interval, expiry, bounded execution and acknowledgement policy. This removes the idle token wait; it does not disable rate limits globally, purge queues, change trading profiles, or lengthen MTF freshness.

Add `[MTF-TASK]` lifecycle logs for observe, replay and continuous Shadow monitor. Publication attempt, receive, start, finish and discard share task IDs. Discards include Celery's explicit `expired` flag. Missing publication timestamps on older messages remain unknown. Logs exclude arguments and market payloads.

## Validation and release

The regression reproduces a depleted legacy bucket retaining the request without pool dispatch, then checks the actual annotated monitor has no bucket. Lifecycle tests distinguish broker wait, receive-to-start wait, execution and expiry and tolerate pre-instrumentation messages.

Deploy the verified main commit to compute and beat: compute activates the annotation; beat supplies publication timestamps. Check natural beat-generated observe starts, successful completions and advancing `mtf_chart_evaluations.observed_at`. A successful provider deployment alone is insufficient. Observe subsequent cycles to detect remaining computation backlog. Do not interpret unrelated history-worker writes or chart HTTP updates as observe recovery.

## Evidence ledger

| Quantity | Provenance | Literal |
|---|---|---|
| Legacy monitor limit | [query/config: annotated task] | `"12/h"` |
| Consumer capacity | [query: worker stats] | `"prefetch_count":1,"max-concurrency":1` |
| Beat monitor interval and expiry | [query/config: beat] | `"schedule":300.0,"expires":900.0` |
| Observation expiry | [query/config: beat] | `"expires":240` |
| Monitor execution duration | [query: task log] | `3.3417858965694904s` |

Timestamps and identifiers above are literal excerpts, not inferred timings or throughput estimates.

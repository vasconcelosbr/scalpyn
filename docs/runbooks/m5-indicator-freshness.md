# M5 indicator freshness isolation

The canonical candle collector published new closed candles while M5 indicator tasks waited behind the MTF observer on `structural_compute`. Production queue logs showed `queue_wait_s=133.18` [query], while the computation itself completed in `6.218487087637186s` [query]. Polling also retained the beat process startup offset.

The closed-candle publishers now route to `indicator_5m`, consumed by a dedicated worker. MTF and Shadow retain their existing queue. M5 canonical polling runs on wall-clock minute boundaries; the database capture contract still governs finalization. No profile configuration, TTL, candle timestamps, or execution gates change.

Deploy a dedicated consumer before updating producers. Use the backend Dockerfile, `SERVICE_MODE=worker`, `WORKER_QUEUES=indicator_5m`, `CELERY_CONCURRENCY=1`, `RUN_BEAT=0`, `RECOVERY_OBSERVATION_ONLY=1`, `SKIP_ALEMBIC=1`, and disable the combined/microstructure/structural/pipeline schedulers. Inherit the existing compute service database, Redis, encryption and canonical-indicator-config references. Do not attach a public domain. Then publish the canonical collector, metadata worker, beat and API from the same clean `origin/main` commit.

Validation must follow real candle boundaries: verify source close time, ingestion, computation, queue wait and authenticated profile-status results. Compare the entire profile hash/version before and after. Verify observation-only on every trading-capable service and retain the no-network exchange-write guard. Real source settlement delays remain data pending rather than profile rejection; never extend validity or restamp source time to hide them.

Evidence ledger:

| Reported | Origin | Literal output |
| --- | --- | --- |
| Queue wait | [query] compute worker runtime log | `queue_wait_s=133.18` |
| Compute duration | [query] same task runtime log | `succeeded in 6.218487087637186s` |


The production cycle also exposed an independent audit-refresh issue: stale operational order flow returned before evaluating otherwise healthy candle rules. Continue the configured-profile audit in that case, persist the flow diagnostic under `operational_data`, and force the operational decision to `BLOCK` both before and after gate promotion. The canonical authorization envelope retains that blocked final decision, so neither public execution nor Shadow capture is granted. The profile display can still classify its own current RSI predicate. Capture, TTL and profile configuration are unchanged.

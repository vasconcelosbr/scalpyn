# M5 collection and Shadow recovery

The recovery beat previously ran market metadata, research candles, Pump Monitor
and the isolated MTF chart observer. It did not collect operational M5 candles or
schedule the L3 scanner. A valid M5 rule could therefore be saved without its RSI
being available for evaluation.

`RECOVERY_M5_SHADOW_ENABLED=1` adds only the existing M5 collection chain, pipeline
scanner and Shadow trade monitor. It requires `RECOVERY_OBSERVATION_ONLY=1`.
Cadences and task routing are reused from the canonical Celery configuration.
Execution, training, promotion, optimization and Bayesian submission schedules
remain outside the allowlist. All consuming workers must run the canonical
release and retain recovery observation mode before enabling this schedule.

The Gate adapter also rejects every signed non-read HTTP method (and every
request explicitly marked as a write) in recovery observation mode, before
signing, rate limiting or making a network call. This covers background tasks as
well as the existing HTTP recovery middleware. Public market-data reads remain
available. Enable `ENABLE_GATE_WS=1` only on the API collector; existing Redis
leader election selects a single ingestion owner.

Missing Shadow configuration must be persisted through ConfigService, using the
existing typed simulation defaults and preserving any already configured values.
Do not change profile rules, score thresholds, or model gates to obtain an ALLOW.
Validate the frozen decision contract and outbox-linked Shadow row; a chart or
an approved display-only calculation is not evidence of Shadow submission.

Validation: `python -m pytest tests/test_recovery_m5_shadow.py
tests/test_mtf_recovery_gate.py tests/test_celery_routing_invariants.py -q`.

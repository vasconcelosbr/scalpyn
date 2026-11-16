# M5 collection and Shadow recovery

The recovery beat previously ran market metadata, research candles, Pump Monitor
and the isolated MTF chart observer. It did not collect operational M5 candles or
schedule the L3 scanner. A valid M5 rule could therefore be saved without its RSI
being available for evaluation.

`RECOVERY_M5_SHADOW_ENABLED=1` adds only the existing M5 collection chain, pipeline
scanner and Shadow trade monitor. It requires `RECOVERY_OBSERVATION_ONLY=1`.
Cadences and task routing are reused from the canonical Celery configuration.
The state collectors own canonical closed candles and live-candle separation;
the legacy M5 collection chain only refreshes metadata and dispatches compute.
Both state collectors (M5 and the M1 microstructure dependency) must be consumed
by the canonical OHLCV worker. Bootstrap history through the same active capture
contract, preserving exchange close status and actual ingestion timestamps.
Execution, training, promotion, optimization and Bayesian submission schedules
remain outside the allowlist. All consuming workers must run the canonical
release and retain recovery observation mode before enabling this schedule.

The Gate adapter also rejects every signed non-read HTTP method (and every
request explicitly marked as a write) in recovery observation mode, before
signing, rate limiting or making a network call. This covers background tasks as
well as the existing HTTP recovery middleware. Public market-data reads remain
available. Enable `ENABLE_GATE_WS=1` only on the API collector; existing Redis
leader election selects a single ingestion owner.

The shared indicator table has no tenant dimension. With multiple active
indicator configurations, explicitly select its producer configuration using
`CANONICAL_INDICATOR_CONFIG_PROFILE_ID` on compute consumers. The selector refers
to the existing database record, so parameters remain GUI-editable. Missing,
inactive or malformed selections fail closed; there is no latest-user fallback.
Without a selector the original exact-singleton validation remains in effect.
Consumers retain the selected configuration identity and hash in provenance.

M5 producers record both the Gate interval opening and the candle close. Feature
freshness uses the close time, as the closed-H4 provider already does. Using the
opening incorrectly exhausted the profile TTL during the candle itself and the
capture settlement delay. Recomputing an old candle never renews its source time.
Existing profile TTLs and veto thresholds remain unchanged; old envelopes are
not rewritten. New producer versions identify this timestamp correction.

Missing Shadow configuration must be persisted through ConfigService, using the
existing typed simulation defaults and preserving any already configured values.
Do not change profile rules, score thresholds, or model gates to obtain an ALLOW.
Validate the frozen decision contract and outbox-linked Shadow row; a chart or
an approved display-only calculation is not evidence of Shadow submission.

Validation: `python -m pytest tests/test_recovery_m5_shadow.py
tests/test_mtf_recovery_gate.py tests/test_celery_routing_invariants.py -q`.

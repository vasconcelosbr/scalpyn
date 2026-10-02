# Pump descriptive intelligence

This view is observational. No trading admission, exits, Shadow datasets, ML gates or live score change. Existing owner storage budget stays at its DB value (5,000,000,000 decimal bytes in production); no migration, purge or provider resource change.

The previous 5,000-row mixed-contract baseline is replaced by **separate compatibility cohorts**: score configuration, label specification/version, feature specification, legacy producer, costs, reference policy and selected horizon. Each row includes observation/episode/instrument/day support and its actual period. These counts are not independent-trade validation.

Both gross targets (0.6%, 0.8%) have independent hits/misses/unknown denominators, coverage-complete denominators, censored first touches, exact time support, bounded interval support and per-target pre-touch drawdown/MAE support. Never interpolate bar intervals, turn gaps into negatives, invent missing V3 pre-touch measurements, or use whole-horizon MAE as pre-touch MAE. A known observed hit does not imply complete coverage. No confidence interval or predictive probability is validated.

Reversible **presentation/resource defaults**, merged through existing Pump config, not trading thresholds:

```json
{"intelligence":{"sample_limit":500,"window_hours":24,"cache_seconds":60,"read_timeout_ms":2000,"max_read_bytes":2000000,"score_edges":[0,10,20,30,40]}}
```

Sample is the latest bounded candidates within the window, including low scores, sorted on the existing owner/slot index. This is **not total history**, not necessarily every candidate in the window, and not a fixed calendar-period report. Latest observations may still await labels; empty metrics stay unknown. The UI exposes the limit, actual size, truncation, period and selected contract. Score edges are exploratory half-open point bands, not admission changes.

Read metadata is selected in a materialized bounded CTE before payload extraction; label lookups are indexed lateral reads, not a full table aggregate. Each source JSON document is decompressed once. A SQL cumulative-byte prefix bounds transfer before rows reach the API; Python retains only the required fields. The byte budget can return fewer than 500 observations and this truncation is explicit. An individually oversized first record fails instead of inventing empty statistics. A 60-second, single-flight, per-process sample cache is bounded to 60 keys; conditions reuse the same sample. Failed refresh retains a previous cache as explicitly stale and throttles retries. Cold failure returns an error; no fabricated empty success. No database cache tables/jobs/resources are added. Cache is process-local, so different API processes may each read once per minute; it is not a distributed global refresh guarantee.

Active Intelligence refreshes every 60s and has its own refresh button, response/computation/capture/label timestamps, errors and staleness. Contract/horizon/applied exploration survive refresh and tab navigation. Request sequence guards reject old responses. If a selected contract leaves the sample, the UI says so rather than silently switching or pooling it. HTTP responses and fetches use no-store; this differs from the explicit server sample cache.

Training ledger/gates are real reads, with no re-training. Failed temporal-cohort runs contain a reason but no class counts; the UI does not infer missing counts. Daily job enabled is not model approved; inference/delta/Pool remain disabled.

Validation: focused tests cover per-target denominators, gaps/pending, interval/censorship, compatible cohorts, score bands, bounded configuration, cache deduplication/stale fallback and latest-request/freshness logic. Production UI proof requires an authenticated session; build/HTTP401 do not replace it.

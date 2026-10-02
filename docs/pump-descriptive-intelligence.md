# Pump descriptive intelligence

This view is observational. No trading admission, exits, Shadow datasets, ML gates or live score change. Existing owner storage budget stays at its DB value (5,000,000,000 decimal bytes in production); no migration, purge or provider resource change.

The previous 5,000-row mixed-contract baseline is replaced by **separate compatibility cohorts**: score configuration, label specification/version, feature specification, legacy producer, costs, reference policy and selected horizon. Each row includes observation/episode/instrument/day support and its actual period. These counts are not independent-trade validation.

Both gross targets (0.6%, 0.8%) have independent hits/misses/unknown denominators, coverage-complete denominators, censored first touches, exact time support, bounded interval support and per-target pre-touch drawdown/MAE support. Never interpolate bar intervals, turn gaps into negatives, invent missing V3 pre-touch measurements, or use whole-horizon MAE as pre-touch MAE. A known observed hit does not imply complete coverage. No confidence interval or predictive probability is validated.

Reversible **presentation/resource defaults**, merged through existing Pump config, not trading thresholds:

```json
{"intelligence":{"sample_limit":50,"temporal_buckets":10,"window_hours":24,"cache_seconds":60,"read_timeout_ms":2000,"max_read_bytes":2000000,"score_edges":[0,10,20,30,40]}}
```

Sample is at most 5 recent candidates in each of 10 evenly spaced time buckets over the 24-hour window, including low scores, using the existing owner/slot index. Empty buckets are not fabricated. Sampling is temporal exploratory support, not a population-weighted historical rate. This includes mature results as well as recent pending captures without selecting winners. This is **not total history**, not necessarily every candidate in the window, and not a fixed calendar-period report. Latest observations may still await labels; empty metrics stay unknown. The UI exposes the limit, actual size, truncation, period and selected contract. Score edges are exploratory half-open point bands, not admission changes.

Each time bucket uses a bounded lateral index read with matching slot/observation order before payload extraction; label lookups are indexed lateral reads, not a full table aggregate. Each source JSON document is decompressed once. A SQL cumulative-byte prefix bounds transfer before rows reach the API; Python retains only the required fields. The byte budget can return fewer than 50 observations and this truncation is explicit. An individually oversized first record fails instead of inventing empty statistics. A 60-second, single-flight, per-process sample cache is bounded to 60 keys; conditions reuse the same sample. Failed refresh retains a previous cache as explicitly stale and throttles retries. Cold failure returns an error; no fabricated empty success. No database cache tables/jobs/resources are added. Cache is process-local, so different API processes may each read once per minute; it is not a distributed global refresh guarantee.

Intelligence is a stable historical snapshot, changed on explicit operator refresh. The first request computes if no process cache exists; subsequent reads and AND filters reuse that snapshot even after the 60-second minimum-refresh interval. Only `refresh=true` may recalculate after that interval. Response fields distinguish consultation (`as_of`), analysis (`computed_at`), `recalculated` and `refresh_requested`. An older historical period is not marked stale merely because time passes. Failed refresh preserves the previous snapshot with an explicit warning. The cache is process-local; a provider restart may require an initial calculation. Radar retains its 60-second polling.

Contract/horizon/applied exploration survive manual refresh and tab navigation. Request guards reject older responses. The initial contract prefers V4 with known support, never by hit rate. HTTP responses and browser fetches use no-store, distinct from the explicit historical analysis cache.

Training ledger/gates are real reads, with no re-training. Failed temporal-cohort runs contain a reason but no class counts; the UI does not infer missing counts. Daily job enabled is not model approved; inference/delta/Pool remain disabled.

Validation: focused tests cover per-target denominators, gaps/pending, interval/censorship, compatible cohorts, score bands, bounded configuration, cache deduplication/stale fallback and latest-request/freshness logic. Production UI proof requires an authenticated session; build/HTTP401 do not replace it.

Cold-read validation under shared DB load required reducing the default to 50 candidates; the 2-second query and 2MB transfer budgets remain unchanged. Scope is explicit and recent candidates may all await labels, especially with existing label backlog. This is not full-history intelligence.

Authenticated QA found that a latest-only sample excluded mature history. Temporal buckets fix this without a full historical scan. Initial selection prefers V4 with known outcomes by support count, never by hit rate. User-selected cohorts stay selected. N=0 in the bounded sample is not absence of evidence in the entire history.

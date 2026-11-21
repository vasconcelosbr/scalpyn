# Pump Monitor: strong green arrows API

`GET /api/integrations/pump-monitor/strong-rising/assets`

Authenticate with `X-Scalpyn-Service-Key`. This dedicated key only reads this
export; it is not a login token and cannot authorize trading/configuration APIs.
There are no query parameters. The configured owner's entire monitored pool is
examined, without TOP-N or the independent `only_rising` filter.

Selection copies the final displayed direction: `direction=long` AND
`strength=forte`, including existing ML confirmation/demotion. No calculation,
collection, order, historical write, profile change or backfill is performed.

| Response field | Meaning |
| --- | --- |
| pool_id | Owner's configured monitored pool |
| generated_at | UTC cycle timestamp |
| data_age_seconds | Age at request time |
| stale | Age exceeds existing display.stale_after_cycles × cycle_seconds |
| config_version / config_hash | Configuration of that cycle |
| total_assets / matched_assets | Monitored rows / matching rows |
| rows[].symbol | Pair, sorted alphabetically |
| rows[].direction | Final direction, categorical strength and reason |
| rows[].pump_monitor_score | Displayed indicator cell, nullable; not the legacy v0 score |
| notice | Observation, not an entry signal |

`forte` is categorical, not a return forecast or a probability. Null scores remain
null. A successful empty result means the full valid cycle has no strong-long
rows. A stale but otherwise valid cycle remains readable, explicitly flagged.

## Availability and errors

- 401: missing, duplicate or invalid key. No JWT fallback.
- 404: export disabled (empty key hash).
- 405: method other than GET.
- 422: query parameters are not supported.
- 503: `complete_cycle_unavailable`, `incomplete_cycle`,
  `pool_changed_waiting_for_cycle`, `config_changed_waiting_for_cycle`,
  `universe_pool_not_configured`, `monitored_pool_unavailable`, or sanitized
  `pump_export_unavailable` for an infrastructure failure.

The Redis full-cycle envelope is the source. The compact sampled historical
fallback lacks the direction contract and is never presented as a valid empty
result. Partial cycles, duplicate symbols and missing directions are unavailable.
Pool ownership and current config identity are verified before exporting.
Responses use `Cache-Control: private, no-store`. Database access is in a
dedicated read-only transaction; Redis is only read.

## Credential provisioning and rotation

API-service settings (separate from Radar credentials):

- `PUMP_READONLY_USER_ID`: owner's UUID.
- `PUMP_READONLY_KEY_SHA256`: SHA-256 of an independently generated random key.

Generate the key with Python `secrets.token_urlsafe(32)`. Keep the plaintext in
the consumer's secret store; only the digest goes to Railway. Never commit the
plaintext, place it in a URL, or log headers. Rotate by replacing the digest and
deploying the API; revoke by emptying the digest and deploying the API. The
credential follows this owner's configured monitored pool.

Example (environment variable supplied by your secret store):

```bash
curl --fail-with-body \
  'https://scalpyn-api-production.up.railway.app/api/integrations/pump-monitor/strong-rising/assets' \
  -H "X-Scalpyn-Service-Key: $SCALPYN_PUMP_READ_KEY"
```

Consumer polling can follow the existing Pump Monitor cycle cadence. Check
`generated_at` and `stale` before using the result. Do not interpret unavailable
data as an empty market selection.

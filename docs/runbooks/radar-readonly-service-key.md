# Radar persisted-only export

## Contract and scope

`GET /api/integrations/radar/watchlists/{watchlist_id}/assets` accepts only
`X-Scalpyn-Service-Key`. JWT is neither required nor accepted as a fallback.
Existing Watchlist, Pool, JWT and trading handlers are unchanged.

The response version is `radar_watchlist_v1`. Its `assets` contain persisted
active rows (`level_direction IS NULL OR level_direction = 'up'`), ordered by
symbol and ID, without a result limit. `membership_basis=persisted_active_rows`
means this is NOT a fresh eligibility evaluation, trading authorization, or
promise of parity with the UI's parent-chain filtering. The consumer must never
use presence in this export as permission to trade.

Each asset carries stored price/volume/score, entered/refreshed timestamps and
an unchanged `analysis_snapshot`. Separately, `indicator_readings` contains the
latest persisted row for every scheduler-group/timeframe in the watchlist's
market type. Each reading exposes its table, group, timeframe, timestamp, age
and untouched JSON values/envelopes. No group/timeframe is merged. `selection=latest_per_group_timeframe` describes the latest-row selection; older rows within each identity are not a historical export.
`timestamp` is the database `indicators.time`; it is not asserted to be candle
close, ingestion time, or decision time. Negative ages are preserved to expose
clock anomalies. `freshness=not_assessed` deliberately introduces no threshold.

Null remains null; no fallback prices, derived hybrid indicators, score/ML
recomputations, exchange requests, background tasks or refreshes are performed.
An empty snapshot returns an empty array. Missing indicator rows return an empty
`indicator_readings` array. `queried_at` is the database transaction time, NOT a
freshness assertion. Radar must check source timestamps using its own configured
policy and treat missing/stale data as unavailable, never as no opportunities.

All reads run in a dedicated session with autoflush disabled, in a PostgreSQL
REPEATABLE READ, READ ONLY transaction, and end with rollback. Responses from
the GET handler, including errors, use `Cache-Control: private, no-store` and
`X-Scalpyn-Read-Mode: persisted-only`. Audit logs contain only the service name,
watchlist UUID (or `invalid`) and response status; no credentials or payloads.

## Configuration and provisioning

Backend variables (all default to empty):

- `RADAR_READONLY_KEY_SHA256`: SHA-256 hex digest; empty disables the endpoint.
- `RADAR_READONLY_USER_ID`: owner UUID, verified from the persisted watchlist.
- `RADAR_READONLY_WATCHLIST_IDS`: comma-separated UUID allowlist, no empty entries.

Provision only on the API service, not workers. Configure the consumer with
`SCALPYN_SERVICE_KEY` and the new full URL. Generate a cryptographically random
key (Python `secrets.token_urlsafe(32)` or equivalent) directly into a secret
manager / protected process memory. Hash it in memory with SHA-256. Transfer
using Railway's secret-input mechanism (`variable set NAME --stdin`), capturing
and suppressing command output. Never put keys into command arguments, source,
terminal output, examples, screenshots, tickets or PRs. Use sealed storage
where supported. Do not replace an existing sealed key merely because it cannot
be read back. Do not use a user JWT or copy JWT_SECRET into the Radar.

Disabled returns 404. Missing/empty/incorrect/duplicate service header returns
401 when enabled. Unlisted IDs and owner mismatches return 404. Invalid service
configuration returns a generic 500. Backend/query failure returns generic 503,
not an empty successful snapshot. Methods other than GET are not registered.

## Release and verification

1. Review isolated diff and tests. Integrate into canonical main and run
   `scripts/verify_deploy_source.ps1` before production deployment.
2. Deploy with the Radar variables absent. Observe terminal SUCCESS; verify the
   new route is disabled and existing JWT routing works unchanged.
3. Confirm the watchlist owner through a read-only query. Configure only that
   owner/allowlist; supply the consumer secret through the protected channel.
4. Wait for the updated processes to be healthy. Check the new GET through the
   public Vercel proxy with the service header, wrong/missing key and disallowed
   ID. Verify cache headers and compare response fields with persisted rows.
   Allow normal concurrent updates when comparing separate transactions; exact
   parity is checked within one repeatable-read snapshot before release.
5. Validate a normal authenticated JWT request using the existing user session;
   do not mint tokens using JWT_SECRET for release proof. Confirm no route or
   dependency was replaced and no credential appears in logs.
6. Configure Radar's daily reader and verify its successful receipt. Do not
   enable a retry loop that calls the interactive refresh endpoint on failure.

Rotate by generating a new random key, updating the backend hash and consumer
secret, restarting/redeploying the affected processes and verifying both that
the old key is rejected and the new key succeeds. A short consumer outage is
expected; no overlapping-key mechanism is provided. Revoke by clearing the
backend hash and rolling updated API processes, then confirm old-key rejection.
Environment edits alone do not prove revocation on running replicas. Disabling
this route does not change logins or the interactive Watchlist endpoint.

## Tests

Run from `backend`:

```text
python -m pytest tests/test_radar_readonly.py tests/test_radar_readonly_postgres.py tests/test_pool_tradable_auth.py tests/test_watchlist_pool_monitoring.py tests/test_watchlist_rejected_transaction_recovery.py tests/test_watchlist_block_timeframes.py -q
```

Set `RADAR_TEST_DATABASE_URL` only to disposable localhost PostgreSQL to enable
the integration test. It creates a uniquely named schema, checks write rejection
and repeatable-read consistency, and removes only that schema. The integration
test refuses non-local database hosts. Never point it at production.

## Initial review / remaining risks

Baseline: `1fa291994f197fcce47251b041d9395a783866ca`, matching the API's successful
Railway deployment `c32b5d31-f054-40c7-bac0-a5d8991e76a4` at implementation start.
At this revision the existing handler is `backend/app/api/watchlists.py:2638`;
refresh at `:2690`, background scheduling at `:2711`, score UPDATE at `:2835`
and market-metadata INSERT at `:3001` remain outside this integration.
The older local checkout previously inspected was not the production baseline.

`backend/app/config.py:9` and `:10` retain preexisting JWT/encryption defaults.
No startup rejection of those specific defaults was found. The production
service lists JWT_SECRET and ENCRYPTION_KEY as configured; values were not
inspected for this audit. Presence alone does not establish strength.
`backend/app/main.py:63` calls the separate AI_KEYS_ENCRYPTION_KEY check; that
does not validate the two preceding settings. Remediation is outside this PR.

The key has no built-in expiration or per-key rate limit. Scope is limited by
owner/allowlist, and revocation requires rolling the API processes. The read
transaction prevents writes but shares the API connection pool and can consume
resources. This is a bounded daily integration, not a public bulk data API.
No guarantee of fresh values or equality with the interactive UI is made.

The legacy `test_watchlist_l1_pipeline.py` requires a running local HTTP service
and performs registration/refresh operations. It cannot be used against
production and was not available in the local test environment. Focused
in-process Watchlist/auth regressions and the disposable PostgreSQL test provide
the relevant isolated checks; do not describe them as a full-suite pass.

## Implementation validation evidence

Focused command above, with the disposable PostgreSQL integration enabled:

```text
83 passed, 4 warnings in 11.68s
```

Warnings came from existing default JWT key length and deprecated FastAPI
`regex` parameters. No existing authentication setting was changed to hide them.

Read-only production source comparison using the new service inside a single
repeatable-read transaction (before deployment; not proof of a live HTTP route):

```json
{"readonly_source_parity":"PASS","asset_count":33,"readings":165,"response_bytes":1865820,"elapsed_seconds":17.963}
```

The comparison asserted row count, symbol order, stored scores and unchanged
analysis snapshots against independent SELECTs within the same transaction.
The database integration fixture separately asserts indicator identity selection,
market isolation, source values, concurrency consistency, and rejected writes.
These counts describe that captured snapshot only, not a permanent pool size.

Evidence ledger:

| Reported value | Origin | Literal output |
|---|---|---|
| Focused suite [query] | pytest | `83 passed, 4 warnings in 11.68s` |
| Export comparison [query] | read-only validation script | JSON above, copied verbatim |

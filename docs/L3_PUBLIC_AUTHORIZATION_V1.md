# L3 Consolidado: authorization and Shadow contract

`GET /api/watchlists/l3-consolidated/assets` retains its existing fields and
`LIVE_L3_CANDIDATES` semantic, but membership alone is no longer sufficient for
publication. All candidates must pass the same persisted authorization projection
used by the individual Spot L3 approved list.

Publication requires the latest decision for the user, profile and symbol to be
ALLOW, a valid ALLOW v3 contract with matching content hash/profile version/
watchlist lineage, and a matching transactional outbox event requesting Shadow.
An earlier ALLOW cannot mask a later BLOCK, missing contract or suppression.
Historical records are not backfilled or replayed into new trades.

## Additive response fields

- `decision_id`: canonical decision identifier.
- `authorization_id`: immutable authorization contract hash.
- `authorization_status`: ALLOW for published candidates.
- `evaluated_at`, `expires_at`: UTC timestamps. Expiration is the earliest
  remaining lifetime of the features actually evaluated, including comparison
  operands. Missing provenance or expiration information fails closed.
- `shadow_status`: PENDING, RETRY or STARTED. PENDING is a durable request, not
  proof of an executed Shadow. A terminal suppression removes that contribution.
- `shadow_reason`: processing result or retry failure, when available.

The consumer must reject expired authorizations and deduplicate authorization_id
before ordering. Deduplicating a hash only prevents reuse of that authorization;
the consumer must retain its own symbol/position/cooldown policy across distinct
authorizations. Persist the complete consumed response with each external order.
The API never places an external real order. Existing consumer code is outside
this repository; adding these fields does not update that integration.

## Producer and worker behavior

Explicit on-demand Spot L3 refresh uses the scanner's canonical evaluator once. Decision,
contract, outbox and membership projection commit together. Failure rolls back
the transaction. The scanner uses the same decision/outbox persistence boundary.
New L3 requests with invalid or expired contracts are not dispatched for capture.
Outbox workers retain their idempotency and active-position/consolidation locks;
new expired requests terminate explicitly as AUTHORIZATION_EXPIRED. No synthetic
price, historical purchase or retroactive Shadow is created.

Spot L3 GET endpoints only read persisted authorization. They do not trigger an
inline or background refresh when a list is empty or stale. Scheduled scans and
explicit refresh requests own production; refreshing the UI cannot produce a
decision or compete for its profile write lock.

The public winner and Shadow consolidation share the existing ranking function.
Pending batch membership can change before consolidation completes; STARTED is
the confirmed processing state. Read-side presentation never recalculates the
approved indicators from a different live data snapshot.

## Verification

Regression coverage: `backend/tests/test_l3_public_authorization.py`, existing
authorization/outbox/consolidation suites, and frontend watchlist tests.
Production acceptance requires authenticated endpoint/UI checks, current
profile/runtime parity and observation of a natural valid capture. Empty lists
and successful deploys alone do not prove positive end-to-end Shadow creation.

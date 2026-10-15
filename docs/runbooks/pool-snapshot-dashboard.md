# POOL Archive dashboard

Separate Railway web service and volume. The daily collector remains a cron job;
no scheduler, trading rule, original API or JWT contract in Scalpyn changes.
The dashboard has no Scalpyn database, exchange credentials, service read key or JWT
signing secret. It reads only its own replica. Opening, searching and exporting do
not call the Scalpyn asset endpoint or trigger a collection.

## Services and data flow

1. Existing collector calls the persisted-only endpoint with its existing key.
2. Its exact compressed JSON snapshots remain in the original SQLite volume.
3. Optional `delivery.py` reports start/end and replicates missing dates to the
   dashboard over HTTPS, with a distinct ingestion credential. Dashboard receives
   only the SHA-256 hash of that credential. Neither key reaches browser code.
4. Dashboard verifies contract, watchlist/pool IDs, day, size and checksum before
   inserting an immutable daily snapshot into its own SQLite volume.
5. Conflicting content for an archived day is rejected. Identical retries are safe.
   Read endpoints and JSON export use SQLite read-only connections.

The initial manual collector run syncs the existing archive, including old dates,
without fetching Scalpyn again when today's snapshot already exists.
A dashboard outage does not prevent local collection. Delivery failure makes the
job fail visibly while preserving the successfully saved original. Later runs
retry missing dates. The UI detects missing daily snapshots after the configured
grace period, and marks a running report with no timely completion as unconfirmed.

## Deployment configuration

Dashboard root: `/integrations`; Dockerfile: `pool-dashboard/Dockerfile`.
Watch patterns: `/integrations/pool-dashboard/**`, `/integrations/radar-collector/collector.py`.
Start: Dockerfile CMD, or `uvicorn app:app --host 0.0.0.0 --port 8080 --no-access-log`.
One replica, dedicated volume `/data`, public HTTPS Railway domain, health `/health`.
No cron schedule on the dashboard. Keep the collector's existing daily schedule.

Nonsecret dashboard variables:

```json
{
  "PORT":"8080",
  "SNAPSHOT_DIR":"/data",
  "PUBLIC_ORIGIN":"https://<dashboard-domain>",
  "SCALPYN_AUTH_ORIGIN":"https://scalpyn.vercel.app",
  "DASHBOARD_OWNER_ID":"8080110c-ee9d-4a2b-a53f-6bef86dd8867",
  "SCALPYN_WATCHLIST_ID":"68292ef9-c711-453e-be7c-46c1de10e9d4",
  "SCALPYN_POOL_ID":"2f5c6c1a-5ee6-4756-b039-7d5695f54508",
  "SESSION_TTL_SECONDS":"900",
  "COLLECTION_HOUR_UTC":"9",
  "COLLECTION_GRACE_SECONDS":"1800"
}
```

`INGEST_KEY_SHA256`: generated key hash, configured through captured CLI stdin.
Collector additions: `DASHBOARD_INGEST_URL` (HTTPS origin),
`DASHBOARD_INGEST_KEY` (generated secret, only in the collector).
Configure both before starting synchronization. Do not put keys in Git, terminal
arguments, logs, documentation, URLs, screenshots or chat.

## Authentication and UI

The dashboard login uses the unchanged Scalpyn `/api/auth/login`, then `/api/auth/me`
and checks the verified identity against `DASHBOARD_OWNER_ID`. Only the POOL owner
may access data. Password and issued JWTs are discarded; an opaque in-memory session
ID is returned in a Secure, HttpOnly, SameSite=Strict, host-only cookie. Sessions expire
and are cleared on deployment/restart. No JWT refresh or trading API proxy exists.
Login/logout enforce the configured Origin; login is rate limited. No credentials
are in localStorage or URL parameters. Access logs are disabled. Responses use
no-store, restrictive CSP, frame denial, HSTS and no-referrer.

UI: capture history/date selection, asset search, persisted price/change/volume/cap/score,
available source timestamps, separate decision snapshot and indicator groups,
exact original JSON download, collection reports and next scheduled time.
No write, trade, refresh-pipeline or manual-collection control is exposed.
The dashboard calls data historical and freshness unassessed; collection time must
never be represented as proof of indicator freshness. Missing values remain missing.

## Acceptance and recovery

Run both suites:

```sh
python -m unittest discover -s integrations/pool-dashboard -v
python -m unittest discover -s integrations/radar-collector -v
node --check integrations/pool-dashboard/static/app.js
```

Verify canonical source, terminal deployments, health, anonymous rejection, no-store,
initial sync checksums, owner login in public UI, date selection, search, detail view,
download and narrow viewport overflow. Confirm original POOL remains available.
Never mark authenticated UI proof complete based only on a healthy endpoint.

To disable replication, remove `DASHBOARD_INGEST_URL` from the collector and redeploy
that service; the original archive and daily collection continue. To revoke dashboard
ingestion rotate/clear its hash. To revoke browsing sessions restart the dashboard
or clear owner config; sessions should also be explicitly invalidated when changing
owner. Preserve both volumes. No automatic retention deletion or independent backup
is configured in this change. Original Scalpyn secret risks remain out of scope.

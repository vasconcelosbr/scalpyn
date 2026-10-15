# Daily POOL snapshot collector

An independent Railway cron service, `radar-pool-collector`, in the Scalpyn project.
Source: `integrations/radar-collector`, with no imports from the backend, no database,
Redis, exchange or JWT credentials and no inbound public domain. Its only remote
operation is GET on the dedicated persisted-only export.

## Configuration

Railway root directory: `/integrations/radar-collector`; config path:
`/integrations/radar-collector/railway.json`. Source is canonical `main`.
Watch pattern: `/integrations/radar-collector/**`.
Dedicated persistent volume mounted at `/data`; no shared trading volume.
Restart policy NEVER: the job exits when complete. Railway schedule is UTC:

```json
{"cronSchedule":"0 9 * * *","restartPolicyType":"NEVER"}
```

This schedules daily at 06:00 America/Sao_Paulo (UTC-03). Railway may start a few
minutes late. Deploys/manual runs can also collect, but a second run on the same
local date verifies the existing archive without fetching or overwriting it.

Non-secret environment:

```json
{
  "SCALPYN_RADAR_URL": "https://scalpyn.vercel.app/api/integrations/radar/watchlists/68292ef9-c711-453e-be7c-46c1de10e9d4/assets",
  "SCALPYN_WATCHLIST_ID": "68292ef9-c711-453e-be7c-46c1de10e9d4",
  "SCALPYN_POOL_ID": "2f5c6c1a-5ee6-4756-b039-7d5695f54508",
  "SNAPSHOT_DIR": "/data",
  "SNAPSHOT_TIMEZONE": "America/Sao_Paulo",
  "HTTP_TIMEOUT_SECONDS": "90",
  "MAX_RESPONSE_BYTES": "16777216"
}
```

`SCALPYN_SERVICE_KEY` is a Railway secret variable on this service only. Generate a
cryptographically random key outside Git. Transfer through CLI stdin, with output
captured and redacted. Never put the value in command arguments, logs, screenshots,
documentation or chat. The API gets only its SHA-256 hash, plus the single approved
watchlist and verified owner, following [the API runbook](radar-readonly-service-key.md).
No other service gets the key. Never fall back to JWT.

## Archive and meaning

`/data/snapshots.sqlite3` holds one row per watchlist/local day, with capture time,
SHA-256 of the exact HTTP JSON bytes, and gzip-compressed original bytes. SQLite
FULL synchronous transactions serialize concurrent runs; failed fetches and writes
leave previous snapshots intact. Every success includes a checksum read-back.
No retention deletion is enabled. Monitor volume capacity; storage failure exits
nonzero instead of silently discarding old history. A volume is persistent storage,
not an independent backup; use Railway volume backups for disaster recovery.

An archived snapshot is NOT a current trading signal. Original decision snapshots,
latest indicator groups, nulls and all timestamps remain unchanged. Old and empty
snapshots are valid historical evidence. `freshness=not_assessed` and
`usable_for_trading=false` are explicit collector log metadata. Downstream analysis
must establish freshness separately; failures or unproven freshness mean unavailable.
This service has no trading or analysis actions.

Failures log only `collection_unavailable` and exit nonzero. The collector rejects
redirects (avoids forwarding the credential), wrong identity/version/count, missing
read-mode/no-store headers, oversized bodies and missing persistent mounts. It does
not truncate, refresh, enrich, mutate Scalpyn or substitute defaults for missing data.

## Read/export a snapshot

Access the Railway volume through authorized Railway tooling, or download a volume
backup and work on a copy. From the collector image (or Python with the script and
the archive locally), export exact JSON without contacting the API:

```sh
SCALPYN_WATCHLIST_ID=68292ef9-c711-453e-be7c-46c1de10e9d4 \
SNAPSHOT_DIR=/data python collector.py --export-day YYYY-MM-DD --output /tmp/pool.json
```

Output must not already exist. Export checks the stored SHA-256. Never publish the
archive or JSON publicly. A stopped cron container may not accept Railway SSH;
use a volume backup rather than altering the service into a permanent web server.

## Verification and operation

Run `python -m unittest discover -s integrations/radar-collector -v` locally.
Before activation, run canonical source guard, confirm owner via read-only query,
and apply API hash/owner/allowlist with a targeted API redeploy. Verify public valid
key, incorrect/missing key, disallowed watchlist, JWT route isolation and no-store.
Observe deployment terminal state and runtime logs, then verify a saved snapshot
and a second execution with the same checksum and `already_saved_verified`.
Do not equate a successful image build with a collected/persisted snapshot.

To pause collection remove the cron schedule; to revoke access clear the API hash
and restart API processes, then verify the former key is rejected. Retain the volume
and snapshots. For rotation update both sides securely and verify a real request.
Preexisting Scalpyn secret-configuration risks remain documented in the API runbook
and are not changed by this collector.

References: [Railway cron jobs](https://docs.railway.com/cron-jobs),
[persistent volumes](https://docs.railway.com/volumes).

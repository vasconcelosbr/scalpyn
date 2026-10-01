# Pump capture recovery

After the 2026-10-01 PR252 rollout, capture/label success stopped between
21:51 and 22:03 UTC. PostgreSQL logs show cancelled observation INSERTs;
one read-only activity sample found no blockers. This does not establish a
single root cause. The queue subsequently resumed without intervention.

Capture now acquires a nonblocking, transaction-scoped `pump_capture:<owner>`
advisory lock, separate from the label lock. A busy owner returns `busy` and
does not write. Each bounded capture submits one price-path INSERT and one
observation INSERT instead of one round trip per asset. Immutable conflict
keys, references, raw-point limits, storage cap and atomic horizon enqueue
remain unchanged. Duplicate counts include both prior-slot skips and insert
conflicts. Capture and label failures are isolated independently, so due
labels can drain when a new capture fails. Logs name the failing stage and
elapsed time without payloads or credentials.

The existing 4-second operation deadlines, 3-second statement deadline and
all resource caps remain unchanged. No scheduling, ML, Shadow, trading,
market-cap or exit-engine settings change. Deploying this patch does not
activate the prepared V4 label contract.

Validation on G5: 178 focused tests passed. Real PostgreSQL TEMP fixture:
100 observations and 100 paths in 2.2400443999795243 seconds; retry wrote zero
and reported 100 duplicates, with 606 total horizon pairs including the
initial one-asset fixture. Outer transaction rollback; zero production
writes. G5 evidence: `pump-capture-batch-temp2.log` and
`pump-capture-atomic-temp.log` in the execution workspace. The original G5
helper was local. Maintainers can reproduce the capture atomicity, retry and
owner-lock checks with the checked-in fixture, from `backend`:
`python scripts/validate_pump_capture_temp.py --temp-only`, using an existing
authorized `DATABASE_URL`. It requires the explicit TEMP-only flag, checks
temporary table resolution, creates no credentials and writes no production
tables. The fixture tests rollback before commit, lost acknowledgement after
commit, immutable retry, independent owners and lock release after rollback.
The cycle regression test also makes ingest raise and checks labels run
exactly once while ranking and support contracts remain intact.
The combined capture plus V4 stress fixture first passed capture idempotency
but later exceeded the existing label deadline; this is retained as failed
evidence, not reported as a full integration pass.

After rollout, require fresh captures and label progress under the unchanged
budgets plus six consecutive read-only stability checks before activating
V4. Rollback uses the normal protected PR/deployment path to the prior main
commit; no schema rollback or historical relabel is needed for this patch.

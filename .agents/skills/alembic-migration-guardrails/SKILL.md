---
name: alembic-migration-guardrails
description: Author and review Alembic migrations for the Scalpyn backend without breaking the revision graph or the Cloud Run cold start. Use whenever creating, editing, merging, renaming, or reviewing any file under `backend/alembic/versions/`, when editing `backend/app/_critical_schema.py::CRITICAL_COLUMNS`, or when debugging an "alembic upgrade head" failure or a Cloud Run deploy that fails with "container failed to start and listen on the port defined provided by the PORT=8080 environment variable within the allocated timeout" (revision rollback, "Can't locate revision identified by …", multiple heads, KeyError in `_revision_map`, exit-1 from `validate_critical_schema`).
---

# Alembic Migration Guardrails (Scalpyn backend)

Scalpyn's Cloud Run container runs `alembic upgrade head` inside `backend/start.sh` on every cold start. If that command exits non-zero, the container exits, Cloud Run rolls the revision back, and the deploy is reported as failed. There is no second chance and the GCP build log is truncated to ~64 KB inside the GitHub Check, so the real error is often invisible from the PR. **A broken Alembic graph = a broken deploy.** Treat the rules below as deploy-blocking.

This skill captures every failure mode that has actually shipped to `main` in this repo (Task #155 alone produced four consecutive failed deploys from three independent broken edges) and the exact local checks that would have caught each one in under two seconds.

## The five invariants

Every migration file under `backend/alembic/versions/` MUST satisfy all of these. Verify all five before committing.

1. **`revision` is the file's true id.** The string assigned to `revision = "..."` is the only id alembic knows. The filename, the docstring header, and the `down_revision` of the next migration must all match it character-for-character. Filenames in this repo are descriptive (`026_decisions_log_direction_event_type.py`) but their `revision` strings are sometimes abbreviated (`"026_dl_direction_event_type"`). Never infer the id from the filename — open the file and read the `revision = "..."` line.

2. **`down_revision` points at a real id.** Open the previous migration file and copy its `revision` string verbatim. Do not paraphrase, do not abbreviate, do not use the filename, do not use a numeric prefix alone. `down_revision = "027"` is wrong if the real id is `"027_indicator_snapshots"`.

3. **Single head after your change.** `alembic heads` must return exactly one revision id. Two heads means the next person who tries to deploy will hit `Multiple head revisions are present`.

4. **Mergepoints use a tuple.** When two migrations legitimately share an ancestor (parallel branches that need to converge), the merge migration's `down_revision` is a tuple of both branch heads, e.g. `down_revision = ("028", "028_robust_engine_tag")`. Do not pick one and drop the other.

5. **UUID-defaulted tables `CREATE EXTENSION IF NOT EXISTS pgcrypto` first.** `gen_random_uuid()` lives in the `pgcrypto` extension on Postgres < 13 and is built-in on >= 13. The Cloud SQL instance's exact version is not guaranteed across cold starts, so any migration that creates a table with `DEFAULT gen_random_uuid()` MUST execute `op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))` before the `CREATE TABLE`. The statement is idempotent and a no-op on modern Postgres.

## Mandatory pre-commit check

Run this from the repo root before every commit that touches `backend/alembic/versions/**`. It takes < 2 seconds and needs no database.

```bash
cd backend && alembic heads && alembic history | head -20
```

Expected output:

- `alembic heads` prints exactly **one** line ending in `(head)`.
- `alembic history` walks cleanly from the newest revision back to the oldest with no `UserWarning: Revision ... is not present` and no `KeyError` traceback.

If either check fails, **do not commit**. The error message names the offending revision id — open the file with that id and reconcile against invariant #1 or #2.

## Quick id audit (when in doubt)

To see every revision/down_revision pair in one screen:

```bash
cd backend/alembic/versions && for f in *.py; do
  rev=$(grep -E "^revision[[:space:]]*=" "$f" | head -1)
  down=$(grep -E "^down_revision[[:space:]]*=" "$f" | head -1)
  printf "%-55s  %-50s  %s\n" "$f" "$rev" "$down"
done
```

Every `down_revision` value (or each element of a tuple) must appear as a `revision` value in the column to its left.

## Authoring checklist (copy into your PR description)

- [ ] Opened the previous migration file and copied its `revision` string verbatim into my new `down_revision`.
- [ ] My new file's `revision = "..."` string matches the filename's descriptive prefix and is unique across the directory.
- [ ] The docstring header `Revises: ...` line matches my `down_revision` exactly (alembic ignores it but humans read it).
- [ ] If I created a table with `DEFAULT gen_random_uuid()`, I added `op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))` before the `CREATE TABLE`.
- [ ] I ran `alembic heads` locally and it returned a single head.
- [ ] I ran `alembic history | head` locally and it printed no warnings or tracebacks.
- [ ] If I introduced a parallel branch, the merge migration's `down_revision` is a tuple containing both branch heads.
- [ ] If this PR adds a new entry to `backend/app/_critical_schema.py::CRITICAL_COLUMNS`, **the migration that creates that column was already merged AND deployed AND verified live** in Cloud SQL — see invariant #7 (rollout order). If not, split into two PRs.
- [ ] If this migration touches a **hot hypertable** (`ohlcv`, `indicators`, `decisions_log`, `trades`, `pipeline_watchlist_assets`), I applied the idempotent DDL manually in prod Cloud SQL **before** pushing — see invariant #8. Without this, Cloud Run cold start contests locks with the previous revision's Celery / collector and the deploy times out.

## Known failure modes (from past production incidents)

- **Wrong column name in a JSONB-plumbing migration (Task #158).** Migration `029_strip_candle_fallback.py` shipped with `UPDATE config_profiles SET config = ...`, but the real column on that table is `config_json` (see `backend/app/models/config_profile.py`). PostgreSQL aborts the statement with `column "config" does not exist`, alembic exits 1, `start.sh` retries 3× and then stamps head, but the migration never actually applies and Cloud Run rolls the revision back. Pure `alembic heads` / `alembic history` will NOT catch this — they only verify the graph, not the SQL inside `upgrade()`. The only check that catches it is running `alembic upgrade head` against a real dev DB (or against a fresh local Postgres) before pushing. **Make this part of your loop for any migration that touches data, not just schema.**

## Sixth invariant (data migrations)

6. **Every column / table name you write inside `op.execute(...)` matches the live schema.** Open the relevant ORM model in `backend/app/models/` and copy the `Column(...)` name verbatim — do not infer it from the docstring, the API field, or memory. Then prove the migration runs with `cd backend && alembic upgrade head` against a real DB. Graph-level checks (`alembic heads`, `alembic history`) do not parse SQL; they will green-light a migration that references a column that does not exist.

## Known fragile spots in this codebase

- `backend/alembic/versions/023_taker_ratio_scale_v2.py` calls `sa.inspect(bind)` inside `upgrade()`, which only works against a real DB connection. `alembic upgrade head --sql` (offline mode) will fail at this revision with `NoInspectionAvailable: ... MockConnection`. This is expected — use the online `alembic upgrade head` against a real dev DB to fully exercise the chain. Do not try to "fix" 023 by removing the inspector; it gates DDL on table presence intentionally.
- Filenames with numeric collisions (`028_alpha_scores_confidence_weighting.py` and `028_robust_engine_tag.py`) are allowed only because their `revision` strings differ (`"028"` vs `"028_robust_engine_tag"`). When converging them, the merge migration MUST use a tuple `down_revision`. See `029_strip_candle_fallback.py` for the canonical example.
- The deploy is owned by Google Cloud Build trigger `rmgpgab-scalpyn-us-central1-vasconcelosbr-scalpyn--majye` in GCP project `clickrate-477217`, driven by `cloudbuild.yaml` at the repo root. The `.github/workflows/deploy*.yml` files are intentionally skipped/no-op — do not "fix" them by adding GCP secrets unless you also delete the Cloud Build trigger, or you will end up with two competing deploy paths.

## Seventh invariant (CRITICAL_COLUMNS rollout order)

7. **Never add a `(table, column)` pair to `backend/app/_critical_schema.py` in the same deploy that introduces the migration which creates that column.** `start.sh` runs `validate_critical_schema` (= `python3 -m scripts.check_critical_schema`) **on every cold start**, both inside the stamp-head fallback AND after a successful `alembic upgrade head`, and `exit 1`s if any pair is missing. With `--min-instances=1` (`cloudbuild.yaml:54`) the previous revision keeps Celery beat alive holding locks on the target table; the new migration's `ALTER TABLE` either contests the lock and fails fast (asyncpg `lock_timeout=10s` from `alembic/env.py`), or in worst-case rolls back via the stamp-head fallback. Either way, if the column was added to `CRITICAL_COLUMNS` in the same commit, the post-stamp probe sees it missing, exits 1, and Cloud Run rolls the revision back. **The user-visible symptom is identical to a port-bind timeout: `"The user-provided container failed to start and listen on the port defined provided by the PORT=8080 environment variable within the allocated timeout."`** This is a generic Cloud Run message that covers any process that exits before binding `0.0.0.0:$PORT` — *do not* assume it means "boot is too slow"; the `exit 1` from `validate_critical_schema` produces it too.

   Safe rollout order is two deploys, never one:

   1. **Deploy N** ships the migration only. Wait for the build to go green and confirm in Cloud SQL that the column is present:
      ```sql
      SELECT column_name FROM information_schema.columns
       WHERE table_name = '<table>' AND column_name = '<column>';
      ```
   2. **Deploy N+1** adds the `(table, column)` entry to `_critical_schema.py`. Now the boot probe gates against a column that already exists, so it always passes.

   The same invariant applies in reverse for column drops: **first** remove the entry from `_critical_schema.py` and deploy, **then** ship the migration that drops the column. Otherwise the post-upgrade probe will flag a column it's still asking for.

## Eighth invariant (DDL on hot hypertables)

8. **Any `ALTER TABLE` / `CREATE INDEX` against a hypertable with continuous writes from the previous-revision Celery (`ohlcv`, `indicators`, `decisions_log`, `trades`) is a deploy-time lock-contention risk and MUST be applied to prod manually before the migration ships.** With `cloudbuild.yaml`'s `--min-instances=1`, the old revision keeps Celery beat (and the OHLCV collector — see `backend/app/tasks/collect_market_data.py`) writing to these tables for the entire duration of the new revision's `start.sh`. The new revision's `alembic upgrade head` will contest the lock, hit `lock_timeout=10s` (`alembic/env.py`), retry 3× per `start.sh`, then fall through to `stamp head`. Even if everything succeeds, total wall-clock is up to ~185 s of alembic alone, which often pushes the cold-start past the 240 s startup probe — the same generic "container failed to start and listen on PORT=8080" symptom from invariant #7, but with a different root cause.

   The hot-table list is small. As of revision 034, treat these tables as hot:

   - `ohlcv` (collector writes every 1m / 5m / 15m / 1h)
   - `indicators` (structural + microstructure schedulers write every 5m / 15m)
   - `decisions_log` (every pipeline scan)
   - `trades` (live trade ingestion)
   - `pipeline_watchlist_assets` (every pipeline scan)

   For any DDL on these tables, the safe rollout is **always**:

   1. Apply the DDL manually in Cloud SQL **before** the merge / push. Use the idempotent form your migration uses (`ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`). For `CREATE INDEX` on `ohlcv` specifically, prefer `CREATE INDEX CONCURRENTLY` from `psql` (it cannot run inside a Cloud SQL Studio transaction) so live writes are not blocked.
   2. Push / merge — the Cloud Run cold start hits `IF NOT EXISTS` no-ops and finishes in seconds.
   3. Alembic still records the revision in `alembic_version` because the migration ran successfully (every statement was a no-op, but `op.execute(...)` returned 0 rows with no error).

   **Cold tables** (`pools`, `pipeline_watchlists`, `watchlist_profiles`, `users`, config / lookup tables) are safe to migrate via the normal Cloud Run path — there is no concurrent writer to contest the lock.

## Cold-start budget on Cloud Run

`cloudbuild.yaml` sets `--timeout-startup 240` (the Cloud Run startup probe deadline). Everything between container start and `uvicorn` binding `0.0.0.0:$PORT` must fit inside that window. The current `start.sh` budget, in worst-case lock-contention conditions, is:

| step | wall-clock |
|---|---|
| `alembic upgrade head` (3 attempts × 50s + 5s+10s backoff) | up to **185 s** |
| `alembic stamp head` fallback (only if upgrade failed) | up to **30 s** |
| `validate_critical_schema` (asyncpg connect + 1 query) | ~**5 s** |
| Celery worker spawn (loads full app graph) | ~**5–10 s** |
| Celery beat spawn | ~**5–10 s** |
| `uvicorn` boot + FastAPI lifespan (5 schedulers + DB pool warmup + Gate WS leader bootstrap) | ~**10–20 s** |

The happy path lands well under 60 s; the failure path can blow past 240 s and trip the same generic port-bind error. The startup probe deadline lives in `cloudbuild.yaml` as `--timeout-startup` and was raised from 240 s to **540 s** (Cloud Run's max for startup probes) on 2026-05-02 after migrations 033/034 repeatedly timed out even with DDL pre-applied. Do not lower it back without a specific reason — the cost is zero (the probe completes as soon as `0.0.0.0:$PORT` binds, regardless of the deadline) and the headroom prevents flaky deploys when lock contention or scheduler bootstrap is slower than expected.

When debugging, prefer the Cloud Run **revision** logs over the build log — the revision logs contain the `start.sh` echo lines (`==> [migrations] attempt 1/3 …`, `==> [schema] Critical schema OK.`, etc.) that pinpoint which stage stalled. To find them in the GCP console: Cloud Run → service `scalpyn` → Revisions tab → click the failed revision (e.g. `scalpyn-00365-xyz`) → Logs tab. Filter by `severity>=DEFAULT` and look for the most recent `==>` line before silence; that is where the boot stalled.

## When `alembic upgrade head` fails on Cloud Run

Symptom: container starts, `start.sh` logs `Can't locate revision identified by '<id>'` (or `KeyError: '<id>'` in `_revision_map`), retries 3×, falls back to `alembic stamp head` which also fails, exits 1, Cloud Run rolls back the revision, GitHub check goes red.

Diagnosis (no GCP access required):

1. Pull the offending commit locally.
2. Run the pre-commit check from above. The same KeyError reproduces in <2s.
3. The error names the missing id. `grep -nE "^revision[[:space:]]*=" backend/alembic/versions/*.py` and find the file whose real id was meant to be referenced. Reconcile per invariant #2.
4. Re-run `alembic heads` and `alembic history` until both are clean.
5. Commit, push, watch Cloud Build go green.

Do not edit `start.sh`, `cloudbuild.yaml`, or `Dockerfile` to "work around" a graph break. The graph break is the bug.

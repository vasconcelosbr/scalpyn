# ENCRYPTION_KEY rotation (MultiFernet)

## Context

`backend/app/utils/encryption.py` uses `MultiFernet` (Task #268). The
`ENCRYPTION_KEY` environment variable accepts **either** a single key
**or** a comma-separated list of keys.

- The **first** key in the CSV is used to `encrypt()` new payloads.
- **All** keys are tried, in order, when `decrypt()` runs.

This unblocks recovery when the Cloud Run `ENCRYPTION_KEY` no longer
matches the key that was used to write the rows currently stored in
`exchange_credentials` (symptom: HTTP 503 on `/api/trades/open`,
`/api/performance/sync`, spot/futures engine, reconciliation, WS leader
— with `cryptography.exceptions.InvalidSignature` /
`InvalidToken` in the `scalpyn` API logs).

## Recovery procedure (Cloud Run service `scalpyn`)

1. **Confirm symptom.** In Cloud Logging for `scalpyn`, filter for
   `InvalidSignature` or `InvalidToken`. If present on `decrypt()`
   callsites, continue.

2. **Identify the previous key.** Look up the `ENCRYPTION_KEY` value
   that was active in Cloud Run when the affected `exchange_credentials`
   rows were written (check Secret Manager versions, deploy history,
   or the operator who last rotated). Call it `OLD_KEY`. Call the
   currently-configured key `CURRENT_KEY`.

3. **Validate token shape (optional but recommended).** In Cloud SQL
   `clickrate-477217:scalpyn`:
   ```sql
   SELECT id, user_id, LEFT(api_key_encrypted::text, 10) AS key_prefix,
          LENGTH(api_key_encrypted) AS key_len, created_at, updated_at
   FROM exchange_credentials ORDER BY updated_at DESC LIMIT 10;
   ```
   Expect `key_len > 100` and base64-decoded payload prefixed with
   `gAAAAA` (Fernet v1 header). If not, the rows are not Fernet tokens
   and rotation will not help — open a re-registration task instead.

4. **Update `ENCRYPTION_KEY` to CSV.** In Cloud Run for `scalpyn`,
   set:
   ```
   ENCRYPTION_KEY=CURRENT_KEY,OLD_KEY
   ```
   `CURRENT_KEY` stays first so any new write keeps using it; `OLD_KEY`
   is appended so existing rows decrypt successfully.

   If managed via Secret Manager: create a new version of the secret
   with the CSV value, then point the Cloud Run env binding at the new
   version and roll out a new revision.

5. **Probe the encryption health endpoint (Task #275).** Public,
   read-only, no auth required:
   ```
   curl -s https://scalpyn-…run.app/api/health/encryption | jq
   ```
   Expected fields:
   ```json
   {
     "ok": true,
     "scanned": 7,
     "decryptable": 7,
     "indecryptable": 0,
     "legacy_rows": 6,
     "current_key_id": "ab12…",
     "known_key_ids": ["ab12…", "cd34…"],
     "by_key_id": {"ab12…": 1, "cd34…": 6},
     "rotation_complete": false
   }
   ```
   - `legacy_rows > 0` confirms there are rows still encrypted under
     `OLD_KEY` (id `cd34…`) that can now be migrated.
   - `indecryptable > 0` after step 4 means the rotation also missed
     yet another historical key — investigate before proceeding.

6. **Smoke test.** With a real user that has Gate.io credentials
   cadastradas, exercise:
   - `POST /api/trades/sync?all_history=true` (was the original
     failure mode — should now succeed end-to-end; if a row is still
     bad, the API now returns **422** with a re-registration hint
     instead of a generic 500).
   - `GET /api/trades/open`
   - `POST /api/performance/sync`
   - Spot engine start/stop
   - Futures engine start/stop
   - Reconciliation cycle (check Celery logs)
   - WS leader (if `ENABLE_GATE_WS=1`)

   Confirm no new `InvalidSignature` / `InvalidToken` entries appear
   in `scalpyn` logs after the rollout.

7. **Mass rewrap (Task #275).** Migrate every legacy row to the
   current key in one shot. The endpoint is bearer-token gated using
   the same `ADMIN_DIAGNOSTICS_TOKEN` env var as
   `/api/admin/symbol-health` (returns 404 when unset, 401 when the
   token is wrong, 200 otherwise):
   ```
   curl -sX POST \
     -H "Authorization: Bearer $ADMIN_DIAGNOSTICS_TOKEN" \
     https://scalpyn-…run.app/api/admin/encryption/rewrap | jq
   ```
   Expected response:
   ```json
   {
     "ok": true,
     "scanned": 7,
     "rewrapped": 6,
     "already_current": 1,
     "failed": 0,
     "current_key_id": "ab12…"
   }
   ```
   - `failed > 0` lists up to 50 row ids in `failed_row_ids`. Those
     rows could not be decrypted under any configured key — instruct
     the affected user(s) to re-cadastrar API keys via Settings → API
     Keys.
   - The endpoint is idempotent: re-running it after success reports
     `rewrapped=0, already_current=N`.

8. **Drop the legacy key.** Probe `/api/health/encryption` once more
   and confirm `rotation_complete: true`. Then update Cloud Run
   `ENCRYPTION_KEY` back to a single value (`CURRENT_KEY` only) and
   roll out a new revision. The CSV form is safe to leave in place
   indefinitely if you prefer to keep `OLD_KEY` as a safety net.

## Notes

- A single-key value (no commas) keeps working unchanged. Backward
  compatible.
- Whitespace around commas is stripped: `"a , b"` is parsed as
  `["a", "b"]`.
- An empty / whitespace-only value raises `ValueError` at first use —
  the API will fail loudly rather than silently returning empty
  strings.
- `AI_KEYS_ENCRYPTION_KEY` (used by `ai_keys_service.py`) is a
  separate variable and is NOT affected by this change.

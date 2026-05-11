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

5. **Smoke test.** With a real user that has Gate.io credentials
   cadastradas, exercise:
   - `GET /api/trades/open`
   - `POST /api/performance/sync`
   - Spot engine start/stop
   - Futures engine start/stop
   - Reconciliation cycle (check Celery logs)
   - WS leader (if `ENABLE_GATE_WS=1`)

   Confirm no new `InvalidSignature` / `InvalidToken` entries appear
   in `scalpyn` logs after the rollout.

6. **Decision point — keep CSV or re-encrypt.** The CSV configuration
   is safe to leave in place indefinitely (it only adds fallback
   decrypt attempts). To fully retire `OLD_KEY`, run the mass
   re-encryption follow-up (Task #269) and then remove `OLD_KEY` from
   the CSV in a subsequent rollout.

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

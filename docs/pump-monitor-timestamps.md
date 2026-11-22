# Classic Pump Monitor observation times

The symbol cell always shows membership and last strong-long detection times,
including when a saved column preset is loaded. Dates are displayed in
`America/Sao_Paulo`; API timestamps include their UTC offset. The continuity
radar and scoring/execution rules are unchanged.

## Meaning

- **No monitor desde**: first cycle observing the current membership episode in
  the eligible monitored universe, before TOP, sorting and `only_rising` filters.
- **Última força alta**: last observed transition to the final displayed
  `direction=long, strength=forte`, including ML confirmation/demotion. It stays
  fixed while strong and remains visible, muted, after strength is lost.
- A newly arriving asset already strong gets both timestamps from that cycle.
  Confirmed removal and re-entry starts a new episode. Missing market-cap
  metadata, collection failures and absent/`sem_dados` direction do not create
  a removal or a false strength transition. Short-strong is not strong-long.
- At bootstrap, existing assets show **Registro iniciado em**. Their earlier
  membership/strong-entry timestamps are unknown, not reconstructed from sampled
  history. An initially strong asset needs an observed loss and return before
  its first strong-entry timestamp can be recorded.
- These are detection times, not exchange tick timestamps. An unobserved change
  between cycles cannot be reconstructed.

## Storage and API

`pump_monitor_timing_cycles` serializes accepted cycles per user, including pool
changes. `pump_monitor_asset_timing` stores the current/latest episode per user,
pool and symbol. A transaction updates state and the cycle checkpoint together;
duplicate and older cycles cannot replace newer observations. This storage is
independent of expiring Redis state and sampled-history retention.
Redis publishes the envelope and its matching state atomically and refuses an
older envelope, even if its worker finished after a newer database transaction.

`GET /api/pump-monitor/assets` adds optional row fields:

| Field | Contract |
| --- | --- |
| `monitor_entered_at` | Offset-aware ISO timestamp, null for unknown historical entry |
| `last_strong_long_at` | Offset-aware ISO timestamp, null until a proven strong entry |
| `tracking_started_at` | First recorded cycle of this episode |
| `timing_initial_baseline` | True when membership predates reliable recording |
| `timing_status` | `available` or `unavailable` |

The same fields are preserved in compact snapshots. API reads never write timing
state. Old cached envelopes and storage failures display **Indisponível**, never
the browser/request time. A timing write failure is logged under `[PUMP-TIMING]`
and does not stop the existing monitor cycle.

## Release and verification

Apply the additive `251_pump_monitor_timing` migration before updating the Pump
worker/API and frontend. Verify a single Alembic head and run the production
schema audit before any push involving the migration. Follow the repository's
canonical-source deployment guard for a separately authorized production release.
Do not backfill historical events from partial snapshots. The integration test
applies/reapplies/reverts this migration in an isolated local PostgreSQL schema.

Run `python -m pytest tests/test_pump_monitor_timing.py` from `backend` with
`PUMP_TIMING_TEST_DATABASE_URL` pointing at an isolated local PostgreSQL database.
The database test covers restart, isolation, membership removal/re-entry, missing
metadata, duplicate/out-of-order cycles and concurrent transactions. CI provisions
a disposable PostgreSQL service for this test. Run the Pump monitor/direction/
universe regressions too.

From `frontend`, run `node --import tsx --test lib/pump-monitor-timing.test.ts`,
TypeScript, ESLint for the changed files and the production build. Visually verify
the classic table using new entries, baseline rows, strong/demoted states, saved
presets, reload, horizontal scrolling and a narrow viewport.

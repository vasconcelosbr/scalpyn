# Robust Indicators — Phase 3 (Deprecation, Legacy on Standby)

## TL;DR

Phase 3 makes the robust engine the formal default everywhere.

| Setting / flag                  | Phase 2 default | Phase 3 default | Notes                                         |
| ------------------------------- | --------------- | --------------- | --------------------------------------------- |
| `USE_ROBUST_INDICATORS_PERCENT` | `0`             | **`100`**       | Diagnostic; the hot path no longer reads it.  |
| `LEGACY_PIPELINE_ROLLBACK`      | _did not exist_ | **`False`**     | The single emergency revert switch.           |
| `FORCE_ROLLOUT_RAISE`           | `False`         | `False`         | Unchanged. Bypasses the pre-flight guard.     |

The hot path (`select_authoritative_score`) now resolves to the robust
engine for every symbol. The Phase 2 per-symbol bucketing math
(`int(sha1(symbol).hexdigest(), 16) % 100 < percent`) is preserved as
`is_symbol_in_robust_bucket` for admin diagnostics and tests, but is
no longer consulted during scoring. The only way to revive the legacy
engine in production is to set `LEGACY_PIPELINE_ROLLBACK=true`.

> Phase 4 (full deletion of the legacy code path) is downstream. It
> intentionally is **not** part of this task — the legacy engine must
> remain on standby for at least one full 7-day observation window
> before deletion.

## Code map of the change

| File                                                       | Change                                                                                          |
| ---------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `backend/app/config.py`                                    | Default `USE_ROBUST_INDICATORS_PERCENT=100`; new `LEGACY_PIPELINE_ROLLBACK: bool = False`.       |
| `backend/app/services/robust_indicators/bucketing.py`      | `should_use_robust` is now a thin wrapper; bucket math lives in `is_symbol_in_robust_bucket`.   |
| `backend/app/services/robust_indicators/select_score.py`   | Short-circuits to legacy when `is_legacy_rollback_active()` is true; bumps `legacy_rollback`.   |
| `backend/app/services/robust_indicators/__init__.py`       | Re-exports `is_symbol_in_robust_bucket`, `is_legacy_rollback_active`.                           |
| `backend/app/api/admin_robust_indicators.py`               | Adds `phase: "deprecation"`, `rollback_active`, 7-day rolling stability window, `alert` block.  |
| `backend/app/tasks/robust_alerts.py`                       | Adds `check_legacy_rollback_standby` Celery task — pages ops if rollback ACTIVE >24h.           |
| `backend/app/tasks/celery_app.py`                          | New beat entry `robust_indicator_legacy_rollback_check` (hourly).                               |
| `backend/tests/test_phase2_rollout.py`                     | Migrated bucket tests to `is_symbol_in_robust_bucket`; new asserts for Phase 3 wrapper.         |
| `backend/tests/test_phase3_deprecation.py`                 | New: defaults, rollback flag parsing, hot-path short-circuit, hourly standby check semantics.   |

## Operator contract

* The robust engine is **always** authoritative outside rollback.
  There is no longer a per-symbol bucket — every score read goes
  through `calculate_score_with_confidence` unless the rollback is
  active.
* **Exclusivity:** `LEGACY_PIPELINE_ROLLBACK` is the **only** path
  that resurrects the legacy engine in production. When the rollback
  is OFF, `select_authoritative_score` never returns
  `engine_tag="legacy"` — robust failures (missing indicators,
  compute exceptions, empty symbol) surface as a robust-tagged
  sentinel: `engine_tag="robust"`, `score=None`, `fell_back=False`.
  The `fallback_reason` field is preserved for telemetry only and the
  `robust_silent_fallback_total` counter is still bumped so ops can
  see how often the robust engine cannot produce a value.
* The downstream consumer
  (`pipeline_scan._apply_robust_authoritative_scoring`) treats the
  sentinel as a rejection — it zeroes out the asset's score columns
  (`_score`, `score`, `alpha_score`, plus `confidence_score` /
  `score_long` / `score_short` for futures) so a pre-existing legacy
  numeric value is never persisted under the `robust` engine tag.
* The silent-fallback counter contract is unchanged for the in-engine
  failure modes. Phase 3 adds one new reason — `legacy_rollback` —
  that is incremented exactly once per `select_authoritative_score`
  call when the rollback is active.
* The admin status endpoint
  (`GET /api/admin/robust-indicators/status`) carries an `alert` block
  whenever (a) the rollback is active, or (b) the 7-day rolling
  stability window drifts back into the Phase 2 pre-flight unsafe
  zone (rejection rate, divergence rate, or average confidence). The
  day-level check is **fail-loud**: if the 7-day aggregate stays
  inside the bounds but **any single day** in the window is unsafe,
  `summary.alert` still flips and the offending day-level reasons
  are surfaced in `summary.alert_reasons`. `summary.unsafe_day_count`
  reports how many days drifted.
* The rollout step in `pipeline_scan` is also **fail-closed** under
  Phase 3: if `_apply_robust_authoritative_scoring` itself raises,
  every asset score column is zeroed out (and tagged `engine_tag="robust"`)
  so a pre-existing legacy numeric value cannot survive the failure
  and end up persisted with a `robust` tag. The exception is logged
  at `ERROR` and a `rollout_step_failed` reason is added to
  `robust_silent_fallback_total`. The only exception is when the
  operator has armed `LEGACY_PIPELINE_ROLLBACK` — in that case the
  failure is still logged at `ERROR` but the legacy scores are
  retained as the requested emergency fallback.

## Emergency rollback runbook

1. **Decide.** The rollback is a blast-radius operation — every score
   read in production will be served by the legacy engine. Confirm
   the incident requires it (e.g. a confirmed regression in the
   robust engine producing wrong scores at scale).

2. **Set the flag.** Set `LEGACY_PIPELINE_ROLLBACK=true` on the
   backend service environment. The flag is read at every score
   selection — there is no need to restart workers, but a restart
   does no harm and clears any in-flight state.

3. **Verify.** Hit `GET /api/admin/robust-indicators/status` and
   confirm:
   * `rollback_active: true`
   * `alert.kind: "legacy_rollback_active"` with severity
     `critical`.
   * `silent_fallbacks.by_reason.legacy_rollback` is incrementing.

4. **Land the fix.** While the rollback is active, no robust-engine
   work runs through the hot path. Investigate, ship the fix, and
   then **unset** the flag. The hourly standby check will continue
   paging ops every 6 hours until the flag is cleared.

5. **Confirm cleanup.** After unsetting the flag, hit
   `/status` again and confirm `rollback_active: false`. The
   Redis-backed first-seen marker is cleared on the next standby
   check (within one hour).

## Hourly standby alert

`app.tasks.robust_alerts.check_legacy_rollback_standby` runs hourly
and persists the first-seen timestamp of an active rollback in Redis
under `robust_alerts:legacy_rollback:first_seen`. If the rollback
stays ACTIVE for more than 24h the task pages the ops Slack webhook
(`ROBUST_ALERTS_OPS_WEBHOOK_URL`). Re-pages are rate-limited to one
alert every 6 hours so the channel doesn't spam during a long
incident.

The Redis key is cleared on the next tick after the rollback is
unset, so a future standby event will start its own 24h clock.

### Production gating

The standby check is **production-gated** so a staging or local
runbook drill doesn't wake ops. The task resolves the deployment
environment from the first non-empty value of (in order):

1. `ROBUST_ALERTS_ENVIRONMENT`  *(explicit override)*
2. `APP_ENV`
3. `ENVIRONMENT`
4. `ENV`

If none are set, the task **defaults to `production`** so existing
production deployments keep paging without configuration changes.

Recommended deployment hygiene:

- **Production**: leave the variables unset *or* set
  `ROBUST_ALERTS_ENVIRONMENT=production` for explicitness.
- **Staging / dev / preview**: set
  `ROBUST_ALERTS_ENVIRONMENT=staging` (or `dev`/`preview`/`test`)
  so any rollback flag flipped during a fire-drill bookkeeps
  cleanly without paging ops. The task still records `rollback_active`
  and `age_seconds` in the report; only the slack page is suppressed
  (response carries `skipped: "non_production"`).
- **Staging fire-drill**: set
  `ROBUST_ALERTS_FORCE_STANDBY=true` to re-enable paging in a
  non-prod environment for a single drill, then unset it.

## 7-day observation checklist

Before proposing Phase 4 (legacy deletion), confirm via the admin
status endpoint that the 7-day rolling window is healthy:

- [ ] `seven_day_trend.summary.total ≥ 1000` snapshots — enough data
      to be statistically meaningful.
- [ ] `seven_day_trend.summary.rejection_rate ≤ 0.20` (matches the
      Phase 2 pre-flight threshold).
- [ ] `seven_day_trend.summary.divergence_rate ≤ 0.10`.
- [ ] `seven_day_trend.summary.avg_confidence ≥ 0.65`.
- [ ] No individual day in `seven_day_trend.days` carries
      `unsafe: true`.
- [ ] `silent_fallbacks.by_reason.legacy_rollback == 0` at the end of
      the window — i.e. the rollback was not used.
- [ ] `silent_fallbacks.by_reason.compute_failed` trend is flat or
      decreasing — no slow regression in the engine.
- [ ] No `legacy_rollback_standby` Slack alerts fired during the
      window.

When all checkboxes are green for two consecutive 7-day windows the
team can open the Phase 4 (legacy deletion) ticket with confidence.

## What Phase 3 does **not** do

* It does **not** delete the legacy `score_engine` /
  `futures_pipeline_scorer` code paths. They are still callable and
  still tested.
* It does **not** drop the `engine_tag` columns or stop tagging
  rows — Phase 4 will re-evaluate that once the legacy engine is
  removed.
* It does **not** remove the Phase 2 admin endpoints — the pre-flight
  guard endpoint is still useful as a forward-looking sanity check
  for any future rollout.

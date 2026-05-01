# Robust Indicators — Phase 2 Rollout Playbook

This playbook documents the gradual rollout of the robust indicator
pipeline (Task #144) from shadow-only (Phase 1) to authoritative scoring
across all symbols. Operators read this end-to-end before bumping the
rollout percentage.

## TL;DR — three knobs, one ramp

| Env var | Default | Purpose |
| --- | --- | --- |
| `USE_ROBUST_INDICATORS` | `false` | Phase 1 — also runs the robust pipeline in shadow mode and persists snapshots to `indicator_snapshots`. Required as a precondition for Phase 2. |
| `USE_ROBUST_INDICATORS_PERCENT` | `0` | Phase 2 — percentage of symbols (0–100) bucketed into the robust pipeline as the **authoritative** score source. Recommended ramp: `10 → 50 → 100`. |
| `FORCE_ROLLOUT_RAISE` | `false` | Bypass the pre-flight safety guard for emergency raises (still evaluates and reports unsafe reasons; only changes `safe` from false to true). |

Optional symbol-level overrides:

| Env var | Purpose |
| --- | --- |
| `ROBUST_FORCE_SYMBOLS=BTC_USDT,ETH_USDT` | Force-bucket the listed symbols regardless of `USE_ROBUST_INDICATORS_PERCENT`. |
| `ROBUST_EXCLUDE_SYMBOLS=PEPE_USDT` | Hard-exclude the listed symbols from the rollout bucket. |

## How bucketing works

Selection is **deterministic per symbol** so a symbol cannot flap
between engines on consecutive scans:

```python
in_bucket = int(sha1(symbol.upper()).hexdigest(), 16) % 100 < percent
```

Properties:

- A symbol bucketed at `percent=N` is also bucketed at every `percent ≥ N`.
- Across a large symbol pool the bucketed fraction converges to `percent/100`
  (verified in `tests/test_phase2_rollout.py::test_bucketing_distribution_matches_percent`).
- The bucket index is purely a function of the symbol and is stable
  across processes and restarts.

## Score read points

When a symbol is bucketed, the **robust** confidence-weighted score
becomes authoritative at every read point. Failures fall back silently
to the legacy score and bump the `robust_silent_fallback_total`
Prometheus counter (with reason label).

| Read point | Source | Behaviour for bucketed symbols |
| --- | --- | --- |
| `pipeline_scan` (`_apply_robust_authoritative_scoring`) | `assets[i]['_score']` / `alpha_score` | Replaced with robust score. `engine_tag` set to `"robust"` (or `"legacy"` on fallback). |
| `pipeline_rejections` | `evaluate_rejections` | Each rejection row carries the source asset's `engine_tag` so the rejection log records which engine produced the rejected score. |
| `evaluate_signals` (Celery) | `alpha_scores` row | Rebuilt at read time from `indicators_json` via `select_authoritative_score`. |
| Futures entry gate (`pipeline_watchlist_assets.confidence_score`) | Persisted by `_upsert_assets` | Robust score scaled into `confidence_score`; `score_long` / `score_short` scaled proportionally to preserve direction. |
| UI score breakdown | `/api/pipeline/{wl_id}/assets` → `engine_tag` | Renders a `ROBUST` / `LEGACY` badge in the score column. |

## Persisted columns (Migration 028)

`pipeline_watchlist_assets` and `pipeline_watchlist_rejections` each
gain a nullable `engine_tag VARCHAR(16)` column. Values are
`"robust"`, `"legacy"`, or `NULL` (rows persisted before the migration).
The init_db bootstrap mirrors the migration so fresh containers without
migration history still get the column.

## Pre-flight safety guard

Before raising `USE_ROBUST_INDICATORS_PERCENT` to the next tier, run
the pre-flight check. The guard inspects the last 30 minutes of
`indicator_snapshots` and blocks the raise when **any** of the
following thresholds are exceeded (admin endpoint
`POST /api/admin/robust-indicators/preflight`):

| Metric | Default threshold | Why |
| --- | --- | --- |
| `divergence_rate` (snapshots in `>10%` bucket / total) | `≤ 0.05` | Robust vs legacy disagree on more than 5 % of symbols → likely correctness regression. |
| `rejection_rate` (rejected snapshots / total) | `≤ 0.30` | Critical-gate or confidence-gate rejection storm → robust pipeline is starving. |
| `avg_confidence` | `≥ 0.60` | Average envelope confidence too low → upstream data quality regression. |
| `total` (snapshot count in window) | `≥ 1` | Without snapshots in the window we cannot verify safety, so we block by default (must run with `USE_ROBUST_INDICATORS=true`). |

Setting `FORCE_ROLLOUT_RAISE=true` flips `safe` from `false` to
`true` while keeping the unsafe reasons in the response payload — the
operator's audit trail of what they chose to override.

## Standard rollout procedure (10 → 50 → 100)

1. **Confirm Phase 1 is healthy.** Verify `USE_ROBUST_INDICATORS=true`
   has been on for at least 24 h and that `indicator_snapshots` is
   actively written. `GET /api/admin/robust-indicators/status` returns
   `shadow_window.total > 0` and `preflight.safe == true`.
2. **Raise to 10 %.** Set `USE_ROBUST_INDICATORS_PERCENT=10` and
   redeploy / restart workers. The pipeline_scan log emits one
   `rollout — bucketed=… robust_used=… fallbacks=… legacy=…` line per
   scan. The Prometheus counter `robust_silent_fallback_total{reason}`
   should stay near zero.
3. **Soak for 24 h.** Watch the silent-fallback counter, the
   divergence rate (`robust_vs_legacy_divergence_total{bucket=">10%"}`)
   and user-visible scores. If anything looks off, drop back to `0`.
4. **Raise to 50 %.** Run the pre-flight check first. If it returns
   `safe=true`, bump the env var. Soak for 24 h.
5. **Raise to 100 %.** Same procedure. Soak for 48 h.
6. **Phase 3 — Deprecation.** Once 100 % has soaked stably, follow the
   downstream "Phase 3: Deprecation (Legacy on Standby)" task to remove
   the legacy code path.

## Rollback

The rollout has two rollback levers:

1. **Drop the percent.** Setting `USE_ROBUST_INDICATORS_PERCENT=0`
   reverts every symbol to legacy on the next scan; no DB change is
   required and no asset rows need backfilling — `engine_tag` simply
   reads `"legacy"` going forward and old `"robust"` rows are
   harmless.
2. **Symbol-scoped exclude.** If only one symbol is misbehaving, add
   it to `ROBUST_EXCLUDE_SYMBOLS` instead of dropping the global
   percent. The exclude env var wins over `USE_ROBUST_INDICATORS_PERCENT`.

## Admin endpoints

- `GET /api/admin/robust-indicators/status[?target_percent=50]`
  returns `{ rollout, bucketing, shadow_window, thresholds, preflight,
  silent_fallbacks }`.
- `POST /api/admin/robust-indicators/preflight` body
  `{ "target_percent": 50 }` runs the pre-flight guard and returns
  the same `preflight` payload.

Both require `users.role == "admin"`.

## Metrics summary

| Metric | Type | Labels | Source |
| --- | --- | --- | --- |
| `robust_silent_fallback_total` | Counter | `reason` (`missing_indicators`, `compute_failed`) | `select_authoritative_score` fallback path. |
| `robust_vs_legacy_divergence_total` | Counter | `bucket` (`<1%`, `1-5%`, `5-10%`, `>10%`) | Phase 1 shadow scan (unchanged). |
| `score_rejection_total` | Counter | `reason` | Robust score-engine critical/confidence gate failures. |

## Files of interest

- `backend/app/services/robust_indicators/bucketing.py` — sha1 % 100 logic + overrides.
- `backend/app/services/robust_indicators/select_score.py` — score selection + silent-fallback wiring.
- `backend/app/services/robust_indicators/preflight.py` — guard logic and thresholds.
- `backend/app/api/admin_robust_indicators.py` — `/api/admin/robust-indicators/*` routes.
- `backend/alembic/versions/028_robust_engine_tag.py` — `engine_tag` column migration.
- `backend/tests/test_phase2_rollout.py` — bucketing, score-selection and preflight tests.
- `frontend/components/watchlist/PipelineAssetTable.tsx` — `ROBUST` / `LEGACY` badge.

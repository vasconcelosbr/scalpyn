# Scalpyn — Institutional-Grade Crypto Trading Platform

## Architecture
- **Frontend**: Next.js 16 (App Router) + TypeScript + TailwindCSS + shadcn/ui — runs on port 5000
- **Backend**: FastAPI (Python 3.12) + SQLAlchemy 2.0 + Alembic — runs on port 8000
- **DB**: PostgreSQL (Replit managed) — TimescaleDB extension not available on Replit (handled gracefully)
- **Tasks**: Celery + Redis (Redis defaults to localhost:6379) — **plus** two in-process asyncio schedulers launched from the FastAPI lifespan, so the DB stays warm even when no Celery worker is configured (e.g. local Replit dev):
  - `app/services/scheduler_service.py` — refreshes OHLCV / indicators / market_metadata for every watchlist symbol on a fixed interval (default 30 min, env: `BACKGROUND_SCHEDULER_INTERVAL_SECONDS`, `BACKGROUND_SCHEDULER_CONCURRENCY`, `SKIP_BACKGROUND_SCHEDULER`).
  - `app/services/pipeline_scheduler_service.py` — runs the full pipeline scan (POOL → L1 → L2 → L3) by invoking `_run_pipeline_scan()` so `pipeline_watchlist_assets.refreshed_at`, `pipeline_watchlist_rejections` and `pipeline_watchlist.last_scanned_at` stay populated (default 600 s, env: `PIPELINE_SCHEDULER_INTERVAL_SECONDS`, `PIPELINE_SCHEDULER_FIRST_RUN_DELAY_SECONDS` default 60 s, `SKIP_PIPELINE_SCHEDULER`). The on-read fallback `_auto_refresh_watchlist_assets_if_needed` also fires when `last_scanned_at` is NULL or older than `PIPELINE_SCAN_STALE_SECONDS` (default 900 s) so a fresh DB or one whose scheduler missed cycles never serves an empty rejections snapshot.
- **Exchange**: Gate.io API v4

## Project Structure
```
frontend/     — Next.js app (App Router, port 5000)
backend/      — FastAPI app (port 8000)
  app/
    main.py   — FastAPI app factory, CORS, router mounting
    config.py — Settings (DATABASE_URL auto-converted to asyncpg)
    api/      — Route handlers
    models/   — SQLAlchemy ORM models
    schemas/  — Pydantic schemas
    services/ — Business logic
    engines/  — Trading engines
    tasks/    — Celery tasks
docs/         — Architecture docs and specs
```

## Workflows
- **Start application** — `cd frontend && npm run dev` (port 5000, webview)
- **Backend API** — `cd backend && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` (port 8000, console)

## Environment Variables
Required secrets (set in Replit Secrets):
- `DATABASE_URL` — PostgreSQL connection (auto-converted from postgresql:// to postgresql+asyncpg://)
- `JWT_SECRET` — JWT signing key (generate: openssl rand -hex 32)
- `ENCRYPTION_KEY` — AES key for encrypting API credentials (generate: openssl rand -hex 16)
- `REDIS_URL` — Redis connection (optional, defaults to redis://localhost:6379/0)
- `BACKEND_URL` — Backend URL for frontend proxy (defaults to http://localhost:8000)
- `PROMETHEUS_BEARER_TOKEN` — bearer token required to scrape `GET /metrics`. When unset the endpoint returns 404 (hidden); when set the request must carry `Authorization: Bearer <token>` or it returns 401. See `docs/grafana/README.md` and `backend/app/api/metrics.py`.

## API Proxy
The frontend proxies all `/api/*` requests to the FastAPI backend via `frontend/app/api/[...path]/route.ts`. This keeps the backend URL server-side only.

## Trade Sync (Exchange Import)
- `POST /api/trades/sync?days=90` — imports closed spot orders from Gate.io into the trades table
- Uses FIFO matching to pair buy/sell orders per symbol and calculate P&L
- Deduplicates via `trades.exchange_order_id` (unique index, nullable)
- `trades.source` column: `"scalpyn"` (engine-initiated) vs `"exchange_import"` (synced)
- Frontend: "Import from Gate" button on the Dashboard page triggers the sync

## Key Notes
- TimescaleDB hypertable warnings on startup are expected (Replit PostgreSQL lacks this extension). The app falls back to regular PostgreSQL tables.
- The DATABASE_URL validator in `backend/app/config.py` automatically converts `postgresql://` to `postgresql+asyncpg://` (required by asyncpg).
- CORS allows all `*.replit.app`, `*.replit.dev`, and `*.repl.co` domains.

## Robust Indicators — Phase 4 cleanup (steady state)
- The shadow → dual-write → robust-authoritative rollout is complete. Phase 4 removed all rollout flags, the dual-write columns, candle-derived approximation flags, divergence metrics, and the admin status endpoint. Robust scoring is the single execution path. `ScoreEngine` was reduced to a thin adapter: `compute_score()` delegates to `app.services.robust_indicators.compute_asset_score`, the legacy 4-bucket weighted-total math (`_evaluate_category_rules` + per-bucket components) was deleted, and the legacy response shape is preserved so the API/UI/`profile_engine`/`spot_scanner` keep working without changes. The rule pass/fail primitives (`_evaluate_rule`, `_get_matched_rules`, `get_full_breakdown`, `_classify`) are kept as pure observability for the drilldown panel.
- Slimmed `backend/app/services/robust_indicators/` to: envelope, validation, score, compute, metrics, snapshot. Removed: `select_score`, `bucketing`, `shadow`, `preflight`. Public exports trimmed accordingly.
- Pipeline: `pipeline_scan._apply_robust_authoritative_scoring` (now async) runs `envelope_indicators` + `validate_indicator_integrity` + `calculate_score_with_confidence` per symbol, persists the per-symbol envelope to `indicator_snapshots` via `persist_snapshot`, and emits `set_indicator_confidence` / `set_indicator_staleness` / `increment_rejection`. On failure it fails closed (zeroes scores, tags `engine_tag='robust'`). The legacy `_tag_futures_scores` wrapper around `app.scoring.futures_pipeline_scorer.score_futures` was removed — futures direction tag (`LONG` / `SHORT` / `NEUTRAL`) and the LONG/SHORT score split are derived from the robust direction bias.
- `pipeline_scan._apply_level_filter` / `_evaluate_l3_signals` / `_evaluate_l3_decisions` no longer instantiate `ScoreEngine` or call `merge_score_config`. They install a small `_RobustScoreShim` on `ProfileEngine.score_engine` that returns the asset's pre-computed robust score (`asset["_score"]`). L2 / L3 gating, signal/entry decisions and persisted `_score` therefore all read the robust value — the legacy 4-bucket math is fully off the hot path.
- New shared helper `app.services.robust_indicators.compute_asset_score(symbol, indicators, rules, *, is_futures)` returns `{score, score_confidence, global_confidence, score_long, score_short, confidence_score, futures_direction, ...}` for a single asset. Used by `compute_scores`, the watchlists API on-demand futures rescore (`api/watchlists.py`), and the pipeline_watchlists drilldown panel (`api/pipeline_watchlists.py`). The legacy `futures_pipeline_scorer.score_futures` is no longer called from any runtime path.
- `compute_scores` task no longer uses `ScoreEngine` math: it asks the robust engine for a confidence-weighted score per symbol and writes `alpha_scores` rows with `scoring_version='v1'` and `components_json={engine: 'robust', score_confidence, global_confidence, matched_rules}`. The bucket sub-score columns are written as NULL because the robust engine works at the indicator level, not the legacy four-bucket model. The level-transition detector reuses the per-symbol scores via an external `cached_scores` dict (SQLAlchemy `Row` objects are immutable).
- Volume-flow indicators (`volume_delta`, `taker_ratio`) in `feature_engine` now return `None` when no real order-flow source is present — no candle approximation.
- Migration `029_strip_candle_fallback.py` merges branch heads `028` + `028_robust_engine_tag` and JSONB-strips `allow_candle_fallback`, `dual_write_mode`, `confidence_weighting` from existing `config_profiles`. The `alpha_score_v2`, `confidence_metrics`, `scoring_version`, and `divergence_bucket` columns remain nullable for forward compatibility.
- Operational alerts (`backend/app/tasks/robust_alerts.py`) keep staleness / low-confidence / rejection-rate, dropped divergence + the hourly `legacy_rollback_standby` beat.
- Canonical architecture doc: `backend/docs/robust_indicators.md`. Phase-by-phase notes deleted. Tests: deleted `test_phase3_deprecation`, `test_phase2_rollout`, `test_dual_write_scoring`, `test_confidence_weighted_scoring`; trimmed shadow / divergence cases from `test_robust_indicators.py`.
- `indicator_snapshots` (alembic 027) records `{indicators_json, score, score_confidence, can_trade, validation_errors, ...}` per scan; the legacy `divergence_bucket` column is retained as nullable but no longer written.
- `/metrics` endpoint exposes Prometheus counters/histograms (`indicator_computation_duration_seconds`, `indicator_confidence`, `indicator_staleness_seconds`, `score_rejection_total`, `exchange_request_latency_seconds`, `exchange_request_errors_total`); the divergence counter was removed with the rest of the dual-write surface. The two `exchange_request_*` series are emitted from `_request` / `_public_get` chokepoints in `backend/app/exchange_adapters/{binance_adapter.py,gate_adapter.py}` (label `exchange` ∈ `{binance, gate}`; error `kind` ∈ `{http, transport}`) and feed the Exchange-Status panel + the error-rate alert in the Grafana dashboard below.
- Celery beat `app.tasks.robust_alerts.evaluate` (every 90 s) inspects recent snapshots and fires Slack alerts to the single ops webhook for stale data, low confidence, or high rejection rate (rate-limited to 1 alert per condition per 15 min via Redis with in-process fallback). Divergence + standby beats are gone.
- Tests in `backend/tests/test_robust_indicators.py` cover envelope wrapping, all validation rules, both score-engine gates, and the Phase 4 removed-symbol contract. Full design notes: `backend/docs/robust_indicators.md`.

## Watchlist Trace Asset (task #69)
- `pipeline_rejections.build_trace_asset(symbol, indicators, meta, alpha_score)` is the SINGLE source for building the asset dict consumed by `build_asset_evaluation_trace` and `_passes_profile_filters`.
- Merge contract:
  - `indicators_json` is the **sole source of truth** for indicator values; a non-None indicator value is NEVER shadowed by a None coming from `market_metadata`.
  - `market_metadata` complements only `current_price`, `price_change_24h`, `volume_24h`, `market_cap` (always present in the dict, possibly None — DB-write paths depend on these keys existing).
  - `spread_pct` and `orderbook_depth_usdt` are hybrid: indicators win when present, meta is the fallback.
  - Variant aliases (`bollinger_width` ↔ `bb_width`, `volume_24h_usdt` ↔ `volume_24h`, `price_change_24h_pct` ↔ `price_change_24h`, `spread_percent` ↔ `spread_pct`, `atr_percent` ↔ `atr_pct`, `orderbook_depth` ↔ `orderbook_depth_usdt`, `price` ↔ `current_price`) are auto-populated in both directions so legacy field naming never produces a false "SEM DADOS / aguardando coleta".
- Both call-sites in `backend/app/api/watchlists.py` (`_resolve_watchlist_pipeline` ~line 1490 and `get_watchlist_assets` ~line 1957) MUST go through this helper. Regression locked in by `backend/tests/test_build_trace_asset.py`.

## Trace SKIPPED Reasons (task #71)
The evaluation trace distinguishes three causes of `status="SKIPPED"` via the `reason` field — the frontend (`EvaluationTraceBreakdown.classifySkip` in `frontend/components/watchlist/EvaluationTraceBreakdown.tsx`) renders each one differently so traders can tell them apart at a glance:
- `"cascade_short_circuit"` → block/filter not evaluated because an earlier block already triggered the rejection. Renders as **PULADO** (cinza neutro), value `—`, expected `bloco anterior já rejeitou`. Set by `_skipped_block_rule` / `_skipped_filter` in `pipeline_rejections.py` when iterating `block_rules[index+1:]` after a FAIL.
- `"indicator_not_available"` → indicator missing from the payload (None/NaN). Renders as **SEM DADOS** (amarelo), value `aguardando coleta`. Emitted by `indicator_validity.is_valid` and propagated through `rule_engine.evaluate_condition_status`.
- `"indicator_invalid_value"` → indicator present but implausible (e.g. `taker_ratio` outside `[0, 1]`, `rsi` outside `[0, 100]`). Renders as **VALOR INVÁLIDO** (laranja) with the actual number shown for diagnostics. Plausibility predicates live in `_PLAUSIBILITY_RULES` (`backend/app/services/indicator_validity.py`).

### Taker Ratio canonical scale (#82, 2026-04-27)
`taker_ratio` is **always** `taker_buy_volume / (taker_buy_volume + taker_sell_volume)` — range `[0, 1]`, equilibrium `0.5`. Both `feature_engine`, `market_data_service`, `order_flow_service` and `layer_order_flow.safe_taker_ratio` write this exact formula; downstream evaluators (`futures_pipeline_scorer`, `blocking_rules`, `indicator_validity`, `score_engine`) all assume this scale. The legacy `buy/sell` definition is retired. The alias field `buy_pressure` carries the same value (kept for backward compatibility with existing profiles and the UI).

Profile thresholds saved on the legacy scale are auto-converted by alembic migration `023_taker_ratio_scale_v2` using the monotonic mapping `new = old / (old + 1)` (e.g. `1.04 → 0.5098`, `1.5 → 0.6`); migrated rows carry an idempotency marker `_taker_ratio_scale_v2: true` in `profiles.config`. The same migration also nulls out any persisted `indicators_json->'taker_ratio'` outside `[0, 1]` so the Rejected tab stops rendering legacy garbage like `328000000000`.
Tests: `backend/tests/test_pipeline_rejected_snapshot.py::test_cascade_skipped_blocks_carry_short_circuit_reason`, `test_filter_cascade_emits_short_circuit_reason_for_remaining_filters`, `test_taker_ratio_above_plausibility_bound_is_invalid_value`.

## Rejected Tab Trace Recompute on Read (task #76)
The Rejected tab endpoint (`_get_watchlist_rejections_payload` in `backend/app/api/watchlists.py`) **always recomputes** `evaluation_trace` on read using current `indicators_json` + `market_metadata`, instead of returning the trace stored in `pipeline_watchlist_rejections.evaluation_trace`. This guarantees backend semantics changes (new SKIPPED `reason` labels, plausibility bounds, etc.) appear in the UI immediately, without waiting for the 30-min scheduler to repopulate the snapshot column.
- Helper: `pipeline_rejections.recompute_rejection_trace(symbol, profile_config, indicators, meta, stored_trace, selected_filter_conditions)` — pure function. Falls back to `stored_trace` when `profile_config` is missing, when there is no `indicators` row for the symbol (collector gap, delisted asset, fresh listing — `meta`-only is NOT enough since most rules are indicator-based and would mass-downgrade to SEM DADOS), or when the trace builder raises. The endpoint also emits a discreet `logger.warning` when this fallback fires so persistent gaps surface in logs without spamming on every poll.
- The DB column `pipeline_watchlist_rejections.evaluation_trace` is intentionally **NOT** rewritten on read. The scheduler still owns that column; recompute-on-read is purely a presentation-layer override.
- Selected filter conditions follow the same `select_profile_filter_conditions` logic used by the live Approved trace (`get_watchlist_assets`), so both tabs stay consistent.
- Tests: `test_recompute_rejection_trace_uses_current_indicators_for_cascade_label`, `test_recompute_rejection_trace_falls_back_when_indicators_missing`, `test_recompute_rejection_trace_falls_back_when_only_meta_is_present`, `test_recompute_rejection_trace_falls_back_when_profile_config_missing` in `test_pipeline_rejected_snapshot.py`.

## Grafana Monitoring (task #166)
- Production-grade dashboard ships in `docs/grafana/`:
  - `scalpyn-trading-engine.json` — **8 panel-group rows** + 14 visual panels (Status Geral · Data Quality Gauges · Confidence ao longo do tempo · Exchange Status · Score por símbolo · Critical Indicators · Rejection Reasons · Alertas A1–A4 reference row), Grafana 10 schema, dark theme. **All four alerts (A1 Confidence < 0.6, A2 NO_DATA > 25%, A3 Rejection rate > 50%, A4 Exchange error > 10%) are embedded inside the dashboard JSON** in Grafana 10 unified-alerting schema (`alert.data` array of query + `__expr__` reduce + threshold nodes, `alert.condition` referencing the threshold).
  - `alert-rules.yaml` — companion provisioning file with the same four unified-alerting rules and matching UIDs, for repeatable production deploys (templated datasource UIDs — see README §5).
  - `queries.md` — per-panel verbatim PromQL/SQL + threshold table.
  - `README.md` — Prometheus scrape config, `grafana_ro` Postgres role, datasource setup, import workflow, smoke test, and the **22-connection Cloud SQL ceiling** caveat (`db-pool-budget.md`).
- Two datasources required: `${prometheus}` (scrapes `/metrics`) and `${postgres}` (reads `indicator_snapshots` + `decisions_log` only via the `grafana_ro` role).
- All PromQL aggregates with `sum by (…)` to handle the per-process counters that come from `WEB_CONCURRENCY=2` uvicorn workers — never paste a raw `rate()` series.
- All SQL touching `indicator_snapshots.score` uses `WHERE score IS NOT NULL` and `NULLIF(..., 0)` denominators, since the column is `NUMERIC(10,2) NULL` and is blank during early pipeline runs.
- `/metrics` is currently unauthenticated — the README recommends Cloud Run ingress allow-list / VPC connector instead of in-app auth (see follow-up).

## Real-time order flow ingestion — Gate Spot WS + Redis buffer (task #171)
- **What changed**: `taker_ratio` / `volume_delta` / `taker_buy_volume` / `taker_sell_volume` are no longer fed by REST polling against `GET /spot/trades`. The Gate Spot WebSocket now subscribes to `spot.trades` and every trade is persisted to a per-symbol Redis sorted set; `order_flow_service.get_order_flow_data` reads that buffer first and falls back to the old REST path only when the buffer is empty.
- **Buffer contract**: Redis key = `trades_buffer:{GateAdapter._normalize_symbol(symbol)}` (e.g. `trades_buffer:BTC_USDT`). Score = trade `create_time_ms`. Member = `{"s":<side>,"a":<amount>,"t":<ts_ms>}|<trade_id>`. TTL = `TRADE_BUFFER_TTL_SECONDS=360s` (≥ the 300s window consumed by `compute_indicators`). Capped per symbol via `ZREMRANGEBYRANK` (env `TRADES_BUFFER_MAX_PER_SYMBOL`, default 5000).
- **Source tags in envelopes**: `taker_source = "gate_trades_ws"` when the value comes from the WS buffer, `"gate_trades"`/`"gate_io_trades"` when it comes from REST. The score engine treats source as opaque metadata — no strict equality checks anywhere in `scoring/` — so the new tag passes through `layer_order_flow`, `safe_taker_ratio`, blocking rules, and validity gates without any extra config. **Robust-source coercion** (`services/robust_indicators/compute.py::_coerce_source_string`) explicitly maps both `"gate_trades"` and `"gate_trades_ws"` to `DataSource.GATE_TRADES` so envelope confidence weighting (`DataSource.GATE_TRADES → 1.00` in `envelope.py`) doesn't degrade to `UNKNOWN` when the WS buffer feeds the merge.
- **Environment variables**:
  - `REDIS_URL` (existing) — singleton client lives in `services/redis_client.py` (`decode_responses=False`, 3s connect timeout). Both the WS handler and the REST-fallback reader share this one client; never call `redis.asyncio.from_url(...)` in a hot path.
  - `ENABLE_GATE_WS=1` — feature flag. **Default off**; flip to `1` in production to start the WS in the FastAPI lifespan. Setting it back to `0` is the rollback switch (system goes back to REST polling instantly on the next deploy/restart).
  - `TRADES_BUFFER_MAX_PER_SYMBOL` — per-symbol entry cap; min 100, default 5000.
- **Multi-instance leader election + automatic failover** (`services/gate_ws_leader.py`): every replica runs a `_GateWSSupervisor` task that loops between two states. As **candidate** it tries `SET gate_ws:leader <instance_id> NX EX 30` every `CANDIDATE_POLL_INTERVAL_SECONDS` (5 s). As **leader** it opens the WebSocket, registers handlers, and runs a background renewal task that extends the TTL every 10 s via a Lua check-and-set (so a stalled ex-leader can't extend a peer's lock). The renewal task fires `_on_lost` the first time the Lua script returns `0`; the supervisor then tears the WS down, releases the lock if it still owns it, and immediately drops back into candidate mode. **Recovery target ≤ 35 s** (TTL 30 s + ~5 s poll). Other replicas stay as readers consuming the same Redis buffer the leader populates; lifespan shutdown awaits `supervisor.stop()` which drains the leader-mode `finally` (release lock + stop WS) before exiting.
- **Redis-unavailable bootstrap**: when `ENABLE_GATE_WS=1` but Redis is unreachable at startup, `start_gate_ws_with_leader_election` does **not** disable the WS for the process lifetime. A background task retries `get_async_redis()` every `REDIS_BOOTSTRAP_RETRY_SECONDS` (30 s) and spawns the supervisor as soon as Redis comes back. The lifespan shutdown coroutine drains this bootstrap task first, then stops the supervisor if one was created.
- **Spot-only WS task spawn**: `GateWSClient.start()` spawns a market task only when its symbol list is non-empty, so the spot-only Task #171 rollout doesn't open an idle futures socket per replica.
- **Test dependencies are not shipped**: `fakeredis` and `lupa` live in `backend/requirements-dev.txt` (which pulls `requirements.txt` first), not in `requirements.txt`. The production `Dockerfile` copies `requirements.txt` only, so the Cloud Run image stays free of test-only packages. Local devs install both via `pip install -r requirements-dev.txt`.
- **Merge fix in `compute_indicators`** (1h + 5m paths): `_merge_order_flow_into_results()` now preserves a previously-computed valid `taker_ratio` / `buy_pressure` / `volume_delta` / `taker_buy_volume` / `taker_sell_volume` when the new fetch comes back `None`. `taker_source` and `taker_window` are still updated unconditionally (they're metadata about the most recent fetch attempt). The window passed to `get_order_flow_data` is now **300s** in both paths — aligned to the 360s buffer TTL.
- **Prometheus metrics** (in `services/robust_indicators/metrics.py`, exposed via `/metrics` with bearer token):
  - `gate_ws_connected{market="spot|futures",instance="..."}` — Gauge 0/1 for **transport health** (WS handshake done + subscriptions about to be sent). Set to 1 immediately after `_send_auth` returns *regardless of auth result* (public channels keep streaming on auth failure), reset to 0 in the connection-loop `finally`. **Do not alert on this gauge for auth failures** — use `gate_ws_auth_ok` instead.
  - `gate_ws_auth_ok{market="spot|futures",instance="..."}` — Gauge 0/1 for **auth-specific health**. Set to 1 when Gate replied `result.status == "success"` to the login frame, *or* when no credentials are configured (the unauthenticated mode used by the order-flow ingestion path). Set to 0 on timeout/error. Pages here are real auth regressions.
  - `gate_trades_received_total{symbol="..."}` — Counter incremented once per trade after a successful pipeline write.
  - `gate_last_trade_timestamp_seconds{symbol="..."}` — Gauge (epoch seconds) of the most recent trade processed for that symbol.
- **Validation queries**:
  - SQL — confirm the new source is reaching the snapshot table:
    ```sql
    SELECT symbol,
           indicators_json->'taker_ratio'->>'value'  AS taker_ratio,
           indicators_json->'taker_ratio'->>'source' AS source,
           indicators_json->'taker_ratio'->>'status' AS status
    FROM indicators
    ORDER BY time DESC
    LIMIT 10;
    ```
  - Prometheus — feed health:
    ```promql
    gate_ws_connected{market="spot"}
    rate(gate_trades_received_total[5m])
    time() - gate_last_trade_timestamp_seconds
    ```
- **Out of scope (open follow-ups)**: futures.trades coverage; an L1 in-process buffer in front of Redis; unifying `microstructure_scheduler_service.fetch_orderbook_metrics`'s independent taker source with the WS buffer (spec line 81).

## Schema Bootstrap (Production)
- **Single source of truth**: Alembic migrations in `backend/alembic/versions/`. New schema changes MUST land as a migration, never only in `init_db.py`.
- **Cloud Run boot order** (`backend/start.sh`): `alembic upgrade head` is the ONLY schema gate. Three retries with backoff, time-boxed at 180s per attempt. `exit 1` on persistent failure causes Cloud Run to roll back to the previous revision automatically. Then Celery + uvicorn start.
- **Lock contention defense** (`backend/alembic/env.py`): every migration runs with `SET lock_timeout = '10s'` and `SET statement_timeout = '60s'`. During deploy the previous revision is still serving (Celery beat holds shared locks); without these timeouts an `ALTER TABLE` would block forever and blow past the ~240s Cloud Run startup probe window — that's exactly what broke the Task #44 deploy.
- **`init_db.py`** is a dev-only convenience for fresh local DBs. It runs from the FastAPI lifespan when `SKIP_LIFESPAN_INIT_DB` is unset; production exports `SKIP_LIFESPAN_INIT_DB=1` so it never touches the prod DB. Migration `021_init_db_parity_catchall.py` mirrors its DDL 1:1.
- **Health probe**: `GET /api/health/schema` queries `information_schema.columns` for the critical column list and returns 503 with `{ missing: [...] }` if any are absent. Use this — not `/api/health` — to verify a deploy succeeded. Canonical post-deploy check before testing the UI.
- **Adding new columns**: write an Alembic migration AND bump the critical column list in `backend/app/main.py::health_check_schema` so production drift is detected proactively.

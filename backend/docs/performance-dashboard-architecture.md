# Performance Dashboard — Architecture (Task #257)

> **Status**: v1 — REST backfill + FIFO engine + read-side API + UI shipped.
> WS streaming, automated 5-min reconciliation Celery task, materialized
> views, and Grafana alerting are tracked as follow-ups.

## 1. Why a new layer

Before Task #257, "what trades happened on the exchange" had three sources of
truth that disagreed by design:

| Source                | Origin                              | Coverage           | PnL source                  |
|-----------------------|-------------------------------------|--------------------|-----------------------------|
| `trades`              | `trade_sync_service` over `/spot/orders` | spot only, closed orders, FIFO-collapsed | computed in Python from order avg prices |
| `trade_tracking`      | Decision Log Enricher → live decisions  | what we *decided* to do (incl. simulated) | reconciler writes `real_entry_price` later |
| `portfolio_service`   | `/spot/accounts` + `/futures/usdt/positions` | live snapshot, no history | balance × ticker, no fills |

Numbers diverged across `/dashboard`, `/dashboard/performance` (operational),
and `/trading-desk/history`. None of them surfaced **fills** (the row-level
truth Gate.io exposes via `/spot/my_trades` and `/futures/usdt/my_trades`).

## 2. New model

```
                  ┌─────────────────────────────────────┐
   Gate.io REST  │   GET /spot/my_trades                │
                  │   GET /futures/usdt/my_trades        │
                  └──────────────────┬──────────────────┘
                                     │  (executions_sync_service)
                                     ▼
                       ┌──────────────────────────┐
                       │  exchange_executions     │   one row per fill
                       │  PK (id)                 │   UNIQUE (exchange,
                       │                          │           market_type,
                       │                          │           trade_id)
                       └──────────┬───────────────┘
                                  │  (position_lifecycle_service — FIFO)
                                  ▼
                       ┌──────────────────────────┐
                       │  position_lifecycle      │   one row per logical
                       │                          │   trade or partial-close
                       └──────────┬───────────────┘
                                  │  (performance_service — pure SQL)
                                  ▼
                       ┌──────────────────────────┐
                       │  GET /api/performance/*  │   summary, equity,
                       │                          │   distribution, by-asset,
                       │                          │   executions, fills
                       └──────────┬───────────────┘
                                  ▼
                       ┌──────────────────────────┐
                       │  /dashboard/performance  │
                       └──────────────────────────┘
```

`exchange_executions` is the **immutable, append-only** ledger of raw fills.
`position_lifecycle` is a **derived projection** — the FIFO engine truncates
and rebuilds the user's rows on every `POST /api/performance/sync` (or
`POST /api/performance/rebuild`). Replay is therefore one button-press.

## 3. FIFO matching algorithm

For each `(user_id, exchange, symbol, market_type)`:

1. Order all executions chronologically.
2. Maintain a FIFO queue of OPEN lots.
3. **Spot**: `side='buy'` opens long lots. `side='sell'` consumes lots from
   the front of the queue.
4. **Futures**: The first fill defines the position direction. Same-side
   fills add to the position; opposite-side fills consume lots.
5. Each closing fill produces ONE `position_lifecycle` row per lot it
   touches — partial closes therefore generate multiple lifecycle rows.
6. Fees are rateably distributed by `qty` proportion.
7. Realised PnL: `long → (avg_exit - avg_entry) * qty - fees`,
   `short → (avg_entry - avg_exit) * qty - fees`.
8. ROI = `pnl / invested`. `pnl_pct = pnl / invested * 100`.
9. Edge case: a closing fill with no matching open lot (e.g. entry leg older
   than backfill window) emits a row tagged `data_quality='DRIFT'` so the
   dashboard surfaces the gap rather than swallowing it silently.

Open positions (lots remaining at the end) are written with
`status='open'`, `closed_at=NULL`, `pnl_usdt=NULL`. Their `invested_usdt`
and `qty` reflect the *unfilled* portion of the queue.

## 4. Sync strategy

**REST backfill** (delivered): `POST /api/performance/sync?days=90` pulls
the last N days from both `/spot/my_trades` and `/futures/usdt/my_trades`,
UPSERTs into `exchange_executions` with `ON CONFLICT DO NOTHING` keyed by
`(exchange, market_type, trade_id)`, then triggers the FIFO rebuild.

**Reconciliation loop** (deferred — follow-up): a Celery beat task
`reconcile_executions` running every 5 min on the `structural` queue
(`acks_late=False`, idempotent) re-pulls a short rolling window
(e.g. 60 min) and re-runs the rebuild. Catches drift if the WebSocket
listener is added later.

**WebSocket private channels** `spot.usertrades` / `futures.usertrades`
(deferred — follow-up): would push fills with sub-second latency; reuses
the existing `gate_ws_leader` Redis election so only one container opens
the socket. The 5-min reconciler is the safety net for any dropped event.

## 5. Read API

All under `/api/performance/*` — pure SQL against `position_lifecycle`,
no Celery/Redis inspect calls (per Task #225 rule):

| Endpoint                              | Purpose                                      |
|---------------------------------------|----------------------------------------------|
| `GET /summary`                        | Flash cards (capital / pnl / stats / risk)   |
| `GET /equity`                         | Daily PnL, cumulative, drawdown series       |
| `GET /distribution`                   | Wins/losses, spot/futures, longs/shorts, heatmap by DOW×hour |
| `GET /by-asset`                       | Per-symbol aggregation (trades, win%, ROI)   |
| `GET /executions`                     | Paginated, filterable lifecycle table        |
| `GET /executions/{id}/fills`          | Lazy fill breakdown for one lifecycle row    |
| `POST /sync`                          | REST backfill + FIFO rebuild                 |
| `POST /rebuild`                       | Re-run FIFO from existing executions         |

All time filters accept presets `1D / 7D / 30D / MTD / YTD / ALL` plus
explicit `from` / `to` ISO datetimes (UTC).

## 6. Migration & co-existence

* Migration `044_executions_lifecycle` creates both tables. Per the
  N/N+1 rule, neither is added to `_critical_schema.py` in this deploy —
  follow-up "Promote 044 columns to CRITICAL_COLUMNS" handles that.
* The legacy operational dashboard (queues, Celery, ingestion freshness)
  is preserved at `/dashboard/operations` (file copied verbatim from the
  former `/dashboard/performance/page.tsx`). The new institutional
  dashboard takes the prime URL `/dashboard/performance`.
* `trades` and `trade_tracking` remain untouched. `/trading-desk/history`
  is intentionally **not** rewritten in this slice (follow-up: redirect
  308 → `/dashboard/performance#history`). Numbers in `/dashboard`
  overview are not yet rewired to the new endpoints — also a follow-up,
  to keep this PR's blast radius small.

## 7. Edge cases / known limitations

1. **Backfill window is finite.** Default 90 days. Closing fills whose
   opening leg is older than the window emit `data_quality='DRIFT'`.
2. **Fees in non-USDT** (GT discount, BNB style) are stored as-is in
   `fee` + `fee_currency` but USDT-denominated PnL only nets the USDT
   portion (see follow-up "Convert non-USDT fees to USDT").
3. **Liquidations** in futures are treated as ordinary opposite-side
   fills (which is what `my_trades` returns). The lifecycle row will
   show the realised PnL correctly but no liquidation tag.
4. **Internal transfers and airdrops** never appear in `my_trades` and
   therefore are invisible to this layer. Equity-curve users that want
   "real wallet equity" need to subtract `spot.account_book` inflows —
   tracked as a follow-up.
5. **Slippage estimate** is `NULL` in v1. To compute it we need OHLCV at
   1-second resolution which we don't ingest yet.
6. **WebSocket gap > 10 min** would be silent in v1 (no automated 5-min
   reconciler yet). Operators must trigger `POST /sync` manually until
   the reconciler ships.

## 8. Replay runbook

```bash
# Replay one user without re-fetching the exchange:
curl -X POST -H "Authorization: Bearer $JWT" \
  https://api.scalpyn.app/api/performance/rebuild

# Full re-fetch + replay (last 90d):
curl -X POST -H "Authorization: Bearer $JWT" \
  "https://api.scalpyn.app/api/performance/sync?days=90&markets=spot,futures"
```

The `/sync` call always rebuilds `position_lifecycle` after the UPSERT, so
running it twice is safe. `position_lifecycle` is `DELETE`-then-`INSERT`
inside one transaction, so rollback is automatic on failure.

## 9. Follow-ups (not in this delivery)

* WebSocket listener on `spot.usertrades` / `futures.usertrades`.
* Celery beat task `reconcile_executions` (5 min) on the `structural` queue.
* Promote `exchange_executions` and `position_lifecycle` columns to
  `_critical_schema.py` (deploy N+1).
* Rewire `/dashboard` overview KPIs to `/api/performance/summary`.
* 308-redirect `/trading-desk/history` → `/dashboard/performance#history`.
* Snapshot test of `/api/performance/summary` payload (skill #6).
* Prometheus metrics `scalpyn_executions_synced_total`,
  `scalpyn_position_lifecycle_lag_seconds`, `scalpyn_performance_data_quality`.
* Convert non-USDT fees to USDT for accurate fee totals.
* Slippage estimate via 1s OHLCV.

# Runbook: Pool Execution Gate (`is_tradable`)

**Owner:** Trading-engine on-call
**Introduced by:** Task #232 (May 2026)
**Migration:** `043_pool_coins_is_tradable`

## Why this exists

Before Task #232 the column `pool_coins.is_approved` gated four disjoint
domains simultaneously:

1. Whether the collector ingests candles for the symbol.
2. Whether the indicator engine computes microstructure for it.
3. Whether the L1/L2/L3 funnel admits it as a candidate.
4. Whether `evaluate_signals` and `execute_buy` may place a live order.

Bullets 1-3 are **ingestion-domain** and naturally align with the
operator's "I added this symbol to the pool" decision. Only bullet 4 is
an **execution-domain** decision and needs a separate audit trail.

## The split

| Column         | Default | Owner               | Read by                                                                                                |
|----------------|---------|---------------------|--------------------------------------------------------------------------------------------------------|
| `is_active`    | `true`  | Operator (UI/CLI)   | Collector, indicators, scoring, pipeline_scan funnel entry, WS subscription resolver                   |
| `is_tradable`  | `false` | Operator (UI only)  | `evaluate_signals`, `execute_buy` — and **only** them (enforced by `test_pool_queries_filter_*`)        |
| `is_approved`  | `false` | Legacy / SQL ad-hoc | Kept for one rolling-deploy cycle. A trigger mirrors `is_approved → is_tradable` for legacy SQL paths. |

## Operator workflows

### Pause ingestion completely

```sql
UPDATE pool_coins SET is_active = false, is_tradable = false WHERE symbol = 'XYZ_USDT';
```

The collector and indicator scheduler stop emitting work for the symbol
on the next cycle (≤ 15 s). Existing OHLCV rows are untouched.

### Authorise live trading for an already-active symbol

Use the UI (`/pools/<id>` → Assets table → "Tradable" toggle) or:

```bash
curl -X POST -H "Content-Type: application/json" \
     -H "Authorization: Bearer $JWT" \
     -d '{"is_tradable": true}' \
     "$API/api/pools/<pool_id>/coins/<symbol>/tradable"
```

The endpoint refuses to enable `is_tradable` while `is_active = false`
(returns HTTP 400) — promote ingestion first.

### Revoke trading without disrupting ingestion

```sql
UPDATE pool_coins SET is_tradable = false WHERE symbol = 'XYZ_USDT';
```

`evaluate_signals` and `execute_buy` will skip the symbol on the next
cycle while the rest of the pipeline keeps producing indicators and
scores.

## Alerts you may see

| Alert code         | Meaning                                                                                                      | Action                                                                       |
|--------------------|--------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------|
| `pool_starved`     | `pool_coins` has zero rows with `is_active = true`. Ingestion is legitimately idle — **not** a failure.      | Add at least one symbol via the UI or `POST /api/pools/<id>/coins`.          |
| `ingestion_stale`  | `is_active > 0` but no OHLCV row in the last 20 min.                                                         | Standard ingestion outage. Check `collect_5m` workflow / Gate.io REST.       |

`pool_starved` is `severity="info"` — paging is suppressed.

## Lint guard rails

`backend/tests/test_celery_routing_invariants.py` now contains two
parametrised tests instead of one:

* `test_pool_queries_filter_is_active` (ingestion files): forbid
  `is_tradable` and require `is_active = true`.
* `test_pool_queries_filter_execution_gate` (execution files): require
  both `is_active = true` and `is_tradable = true`.

Adding a new task that reads `pool_coins` requires adding it to one of
the two tuples (`_INGESTION_DECISION_FILES` / `_EXECUTION_DECISION_FILES`)
at the top of that test file.

## Critical-schema rule

`is_tradable` is **not** in `app/_critical_schema.py` in this deploy
(the standard "N+1" rule). It will be added in a follow-up after one
full deploy cycle without incident, so a partially-applied migration
does not turn the entire backend into a 503 on cold start.

## Rollback

```bash
cd backend && alembic downgrade 042_trade_monitor_price_source
```

Drops the column, the trigger, the partial index. Once the migration is
reverted, immediately revert the Task #232 application code as well —
`evaluate_signals` and `execute_buy` would otherwise issue
`column "is_tradable" does not exist` and never trade.

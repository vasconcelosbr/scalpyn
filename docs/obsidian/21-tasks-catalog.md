---
tags: [area, worker, celery, catalog]
aliases: [Tasks Catalog, Catálogo de Tasks]
---

# 21 — Catálogo de Tasks Celery

Todas as tasks vivem em `backend/app/tasks/`. Roteamento e cost guards
em `tasks/celery_app.py` (ver [[20-celery-topology]]).

Voltar ao [[00-INDEX]].

## Fila `microstructure` (5 min)

| Task | Arquivo | Função |
|------|---------|--------|
| `app.tasks.collect_market_data.collect_5m` | `collect_market_data.py` | Coleta OHLCV 5m da Gate.io para todos `pool_coins.is_active` |
| `app.tasks.compute_indicators.compute_5m` | `compute_indicators.py` | Calcula indicadores rápidos (VWAP, taker_ratio, volume_delta, spread, depth) |

Encadeamento típico: `collect_5m` (beat) → `compute_5m` (chain).

## Fila `structural` (15 min – 1 h)

| Task | Arquivo | Função |
|------|---------|--------|
| `app.tasks.collect_market_data.collect_all` | `collect_market_data.py` | OHLCV 1h + ticker (universo Gate USDT) |
| `app.tasks.compute_indicators.compute` | `compute_indicators.py` | RSI, MACD, ADX, EMA, Bollinger, PSAR, Z-score, OBV, Stochastic |
| `app.tasks.compute_scores.score` | `compute_scores.py` | Aplica regras determinísticas → `alpha_scores` ([[13-scoring-ml]]) |
| `app.tasks.pipeline_scan.scan` | `pipeline_scan.py` | Walk L1→L2→L3 do funil; popula `pipeline_watchlist_*` |
| `app.tasks.auto_discover_assets.discover` | `auto_discover_assets.py` | Discovery automático de candidatos para a pool |
| `app.tasks.fetch_market_caps.fetch_market_caps` | `fetch_market_caps.py` | CoinMarketCap → `market_metadata.market_cap` |
| `app.tasks.macro_regime_update.update` | `macro_regime_update.py` | Regime macro (BTC dominance) |
| `app.tasks.symbol_health_audit.monitor_only` / `.run_repair` | `symbol_health_audit.py` | Saúde por símbolo |
| `app.tasks.simulation.run_simulation_batch` / `.run_trade_simulation` / `.get_simulation_stats` | `simulation.py` | Sim/back-test |
| `app.tasks.robust_alerts.evaluate` | `robust_alerts.py` | Alertas (staleness, low-confidence, rejection-rate) |
| `app.tasks.daily_summary.send` | `daily_summary.py` | Sumário diário (20:00 UTC) |
| `app.tasks.ohlcv_backfill.backfill` / `.get_status` | `ohlcv_backfill.py` | Backfill histórico |
| `app.tasks.decision_log_enricher.enrich` | `decision_log_enricher.py` | Enriquece `decisions_log` → `trade_tracking` |
| `app.tasks.trade_reconciliation.reconcile` | `trade_reconciliation.py` | Bate ordens reais vs expectativa |

## Fila `execution`

| Task | Arquivo | Função |
|------|---------|--------|
| `app.tasks.evaluate_signals.evaluate` | `evaluate_signals.py` | Avalia signals → grava decisão |
| `app.tasks.execute_buy.execute_buy_cycle` | `execute_buy.py` | Coloca ordens reais via [[15-exchange-integration]] |
| `app.tasks.anti_liq_monitor.monitor` | `anti_liq_monitor.py` | Anti-liquidação futures |
| `app.tasks.trade_monitor.monitor` | `trade_monitor.py` | Fecha trades por TP/SL/timeout (cada 10s) |
| `app.tasks.orphan_tx_watchdog.kill_orphans` | `orphan_tx_watchdog.py` | Mata Postgres TXs órfãs (> 15 min idle in transaction) |

## Padrão `_run_async`

Todo task Celery declarado aqui é função síncrona. Internamente usa
um helper `_run_async(coro)` que faz `asyncio.new_event_loop()` +
`loop.run_until_complete(coro)`. Por isso as sessions do DB usam o
engine `NullPool` (`run_db_task(fn, celery=True)`, ver
[[14-models-database]] §Sessions).

## Lint invariants

`backend/tests/test_celery_routing_invariants.py` falha vermelho se:
1. Task lê indicadores fora do `get_merged_indicators` ([[11-services]]).
2. Consumidor não chama `is_complete()` antes de scoring/decisão.
3. Existir `send_task` ou `apply_async` em `app/tasks/` fora de
   `task_dispatch.py`.
4. Task registrada não estiver em `task_routes`.
5. Query de pool filtrar pela coluna errada (`is_active` vs `is_tradable`).

## Áreas relacionadas

[[11-services]] · [[12-engines]] · [[13-scoring-ml]] ·
[[14-models-database]] · [[20-celery-topology]] · [[50-data-flow]]

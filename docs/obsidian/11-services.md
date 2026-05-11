---
tags: [area, backend, service]
aliases: [Services, Camada de Serviços]
---

# 11 — Services

Camada de regras de negócio que orquestra o que models/repositories
expõem em SQL bruto. Tudo em `backend/app/services/`.

Voltar ao [[00-INDEX]].

## Famílias de services

### Indicadores
- `indicators_provider.py` — **única porta de entrada** para leitura de
  indicadores em decisões. `get_merged_indicators()` mescla os grupos
  `structural` e `microstructure` por símbolo. Direct DB query é proibido
  (ver `replit.md` §Architecture decisions).
- `indicator_validator.py` / `indicator_validity.py` / `indicator_envelope.py` /
  `indicator_classifier.py` — validação, envelope, classificação por origem.
- `feature_engine.py` — cálculos brutos (RSI, MACD, ADX, EMA, Bollinger…).
- `robust_indicators/` — engine "robusta" alternativa
  (`compute.py`, `score.py`, `snapshot.py`, `validation.py`).

### Schedulers in-process
Rodam dentro do `lifespan` da API ([[10-backend-api]]) **e/ou** chamados
por tasks Celery ([[20-celery-topology]]).
- `structural_scheduler_service.py` — 15 min, OHLCV 1h, indicadores lentos.
- `microstructure_scheduler_service.py` — 5 min, OHLCV 5m + WS data.
- `scheduler_service.py` — combinado (legacy, opt-in via
  `ENABLE_COMBINED_SCHEDULER`).
- `pipeline_scheduler_service.py` — refresh do funil L1→L2→L3.

### Persistence queue (Task #226)
Pasta `backend/app/services/persistence/`. Fila asyncio bounded + 4
workers que consomem mensagens UPSERT idempotentes em transação curta
(`run_uow`).
- `queue.py` — `PersistenceQueue` singleton com 3 categorias:
  `critical` (block), `compute` (block w/ timeout), `ingest` (drop-oldest).
- `worker.py` — workers long-running, healthcheck `GET /api/system/persistence`.
- `messages.py` / `repositories.py` / `uow.py` / `metrics.py`.
- Opt-in via `USE_PERSISTENCE_QUEUE=1`.

### Operational snapshot (Task #225)
- `operational_snapshot.py` — singleton com 6 refreshers em background
  (ingestion 10s, celery 15s, redis 15s, db 30s, score 60s, latency 60s)
  + 1 alert engine (5s). Alimenta `/api/dashboard/overview`. Detalhes em
  [[42-observability]].

### Dados de mercado e pool
- `market_data_service.py` — adaptador OHLCV.
- `pool_service.py` / `pool_selection.py` — gestão da pool de símbolos
  (gates `is_active` vs `is_tradable`, ver [[12-engines]]).
- `order_flow_service.py` — taker_ratio, volume_delta, reads do buffer
  Redis populado pelo Gate WS ([[15-exchange-integration]]).
- `ohlcv_backfill_service.py` — backfill histórico.
- `coinmarketcap_service.py` — market caps.

### Trading e execução
- `signal_engine.py` / `rule_engine.py` / `block_engine.py` /
  `filter_engine.py` — motores que vivem entre os engines de trading
  ([[12-engines]]) e a API.
- `execution_engine.py` / `executions_sync_service.py` — execução de
  ordens e sincronização com a corretora.
- `trade_monitor_service.py` — fechamento por TP/SL/timeout.
- `trade_reconciliation_service.py` — bate ordens reais vs expectativa.
- `trade_sync_service.py` — import de trades fechados.
- `decision_log_enricher_service.py` — enriquece `decisions_log`.
- `position_lifecycle_service.py` — alimenta a tabela `position_lifecycle`
  consumida em `/api/performance/*`.
- `risk_engine.py` / `block_rule_metrics.py` / `execution_gate_metrics.py`.

### Scoring
- `score_engine.py` — orquestra cálculo determinístico (ver [[13-scoring-ml]]).
- `simulation_engine.py` / `simulation_service.py` / `simulation_metrics.py` —
  back-test/sim.

### Real-time
- `gate_ws_leader.py` — leader election Redis para o WS Gate.io.
- `realtime_bridge.py` — pub/sub Redis → WebSocket browser
  (`start_decision_event_subscriber`).
- `ws_metrics.py` — Prometheus do WS.

### Operacional / observabilidade
- `symbol_health_service.py` / `symbol_remediator.py` — saúde dos símbolos.
- `notification_service.py` — webhooks/Slack.
- `orphan_tx_metrics.py` / `ohlcv_metrics.py` — Prom counters.
- `analytics_service.py` / `performance_service.py` / `portfolio_service.py`.
- `resilient_data_service.py` — wrappers c/ retry.

### Auth / config / IA
- `ai_keys_service.py` — vault de chaves de provedores IA.
- `config_service.py` / `preset_ia_service.py` / `profile_engine.py`.
- `seed_service.py` — seeds dev.

## Padrão de transação

Todo write-path passa por `run_db_task(fn, celery=...)` declarado em
`database.py:346`. Nunca `await session.commit()` manual dentro do
callback. Detalhes em [[14-models-database]] §Sessions.

## Áreas relacionadas

[[10-backend-api]] · [[12-engines]] · [[13-scoring-ml]] ·
[[14-models-database]] · [[15-exchange-integration]] · [[20-celery-topology]] ·
[[42-observability]]

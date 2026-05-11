---
tags: [area, infra, observability, prometheus, grafana]
aliases: [Observability, Snapshot, Alerts]
---

# 42 — Observabilidade (Snapshot, Métricas, Alertas)

Voltar ao [[00-INDEX]].

## `OperationalSnapshotService` (Task #225)

`backend/app/services/operational_snapshot.py`. Singleton iniciado no
`lifespan` da API ([[10-backend-api]]) com 6 refreshers em background +
1 alert engine.

### Famílias de snapshot

| Snapshot | Intervalo (s) | Probe |
|----------|---------------|-------|
| `ingestion` | 10 | `MAX(ohlcv.time)` + count distinct symbols + `pool_coins.is_active` |
| `celery` | 15 | `inspect.active()` + `inspect.registered()` (2 broadcasts; ver gotcha) |
| `redis` | 15 | `PING` + `INFO` + `LLEN` por fila |
| `db` | 30 | `SELECT 1` + pool stats |
| `score` | 60 | `MAX(alpha_scores.created_at)` + counts |
| `latency` | 60 | ingestion / decision / processing |
| `alerts` | 5 | re-derivação a partir das snapshots acima |

Cada snapshot só flipa para `degraded` / `critical` após **3 falhas
consecutivas** (`FAIL_TOLERANCE=3`). Probe bem-sucedido zera o streak.

### Endpoints

| Endpoint | Uso |
|----------|-----|
| `GET /api/dashboard/overview` | O(1) — agrega todas as snapshots + alertas |
| `GET /api/dashboard/celery` | snapshot Celery |
| `GET /api/dashboard/redis` | snapshot Redis |
| `GET /api/dashboard/db-health` | snapshot DB |
| `GET /api/dashboard/score-engine` | snapshot score |
| `GET /api/dashboard/pipeline-latency` | 3 latências |
| `GET /api/dashboard/ingestion` | snapshot ingestion |
| `GET /api/dashboard/alerts` | alertas correntes |
| `GET /api/dashboard/events` | ring buffer (alert / worker / redis) |
| `GET /api/system/celery-status` | bypass do snapshot (debug) |
| `GET /api/system/persistence` | health da [[11-services]] §persistence queue |
| `GET /api/health/schema` | drift de [[14-models-database]] §`_critical_schema` |

### Alertas conhecidos

| Code | Severity | Origem |
|------|----------|--------|
| `pool_starved` | info | `active_pool_count == 0` (ingestão **não** dispara `ingestion_stale` neste caso, ver gotcha) |
| `ingestion_stale` | critical | `delay_seconds > 1200` com pool ativo |
| `worker_offline_60s` | critical | `inspect()` retornou 0 workers por 60s |
| `redis_down` | critical | `PING` falhou após 3 strikes |
| `queue_backlog_500` | warn | `LLEN` > 500 em qualquer fila |
| `db_unhealthy` | critical | `SELECT 1` falhou |
| `no_decisions` | warn | `decisions_log` parou de crescer |
| `low_confidence` | info | médias de confidence abaixo do threshold |

## Gotchas críticos

### `pool_starved` ≠ `ingestion_stale`
Pool vazia → snapshot suprime `ingestion_stale` e emite `pool_starved`
(severity `info`). Operador já sabe que pool vazia = zero candles.
**Não "corrigir"** re-habilitando o stale alert.

### Probe Celery: 2 budgets distintos
- `CELERY_INSPECT_TIMEOUT_S` (default `2.0`) — passado a cada
  `inspect(timeout=...)`.
- `CELERY_INSPECT_BUDGET_S` (default `8.0`) — wrap `asyncio.wait_for`
  ao redor do probe inteiro.

**Não unificar.** Em maio/2026 estavam iguais (2s) e Redis Labs
us-central1 + workers ocupados → snapshot reportava `Workers: 0`
quando havia 5 vivos.

### Sem probes inline em handlers HTTP
`/api/dashboard/*` lê **só** do cache do snapshot. Adicionar
`celery_app.control.inspect()` ou `redis.info()` direto em handler
trava o response no dependency mais lento (5+ s).

## Prometheus `/metrics`

Endpoint `app/api/metrics.py`. Bearer-token gated por
`PROMETHEUS_BEARER_TOKEN`. Returna 404 se a env não existe, 401 sem
header válido.

Famílias de métricas:
- `scalpyn_persistence_*` — fila persistence ([[11-services]])
- `scalpyn_orphan_tx_*` — watchdog ([[14-models-database]])
- `scalpyn_ohlcv_*` — coleta ([[15-exchange-integration]])
- `scalpyn_ws_*` — Gate WebSocket
- `scalpyn_block_rule_*`, `scalpyn_execution_gate_*`, `scalpyn_robust_*`,
  `scalpyn_simulation_*` — engines/scoring

## Grafana

Dashboard + alert rules em `docs/grafana/`:
- `scalpyn-trading-engine.json` — dashboard principal
- `alert-rules.yaml` — regras
- `queries.md` — PromQL útil
- `README.md` — setup (Prometheus scrape config c/ `bearer_token_file`)

## Ring-buffer de eventos

`OperationalSnapshotService` mantém 3 deques de até 100 eventos
(`_alert_history`, `_worker_events`, `_redis_degradations`) expostos em
`GET /api/dashboard/events`. Categorias: `alert`, `worker`, `redis`.

## Áreas relacionadas

[[10-backend-api]] · [[11-services]] · [[14-models-database]] ·
[[20-celery-topology]] · [[40-infra-cloudrun]]

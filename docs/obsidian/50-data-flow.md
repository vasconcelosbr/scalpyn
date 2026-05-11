---
tags: [flow, sequence, cross-area]
aliases: [Data Flow, Fluxos]
---

# 50 — Fluxos Cross-Área

Diagramas sequenciais dos 3 fluxos mais críticos do Scalpyn. Cada passo
faz wiki-link para a área que o implementa.

Voltar ao [[00-INDEX]].

## (a) Ingestão → Indicadores → Score → Decisão

```mermaid
sequenceDiagram
    autonumber
    participant Beat as scalpyn-beat
    participant WMicro as scalpyn-worker-micro
    participant WStruct as scalpyn-worker-structural
    participant WExec as scalpyn-worker-execution
    participant Gate as Gate.io REST/WS
    participant DB as Postgres
    participant Redis

    Beat->>WMicro: collect_5m (beat, 300s)
    WMicro->>Gate: GET /api/v4/spot/candlesticks (5m)
    Gate-->>WMicro: OHLCV 5m
    WMicro->>DB: UPSERT ohlcv (símbolos ordenados!)
    WMicro->>WMicro: chain → compute_5m
    WMicro->>Redis: LRANGE order_flow:{sym}<br/>(populado pelo Gate WS)
    WMicro->>DB: UPSERT indicators<br/>(scheduler_group=microstructure)

    Beat->>WStruct: collect_all (beat, 60s)
    WStruct->>Gate: OHLCV 1h + tickers (universo Gate USDT)
    WStruct->>DB: UPSERT ohlcv + market_metadata
    WStruct->>WStruct: chain → compute (TA pesado)
    WStruct->>DB: UPSERT indicators<br/>(scheduler_group=structural)
    WStruct->>WStruct: chain → score
    WStruct->>DB: get_merged_indicators
    WStruct->>WStruct: aplica regras determinísticas
    WStruct->>DB: INSERT alpha_scores

    Beat->>WStruct: pipeline_scan.scan (300s)
    WStruct->>DB: walk L1 → L2 → L3
    WStruct->>DB: UPSERT pipeline_watchlist_assets

    Note over Beat,WExec: evaluate_signals NÃO está no beat:<br/>é disparada por pipeline_scan / API / chain
    WExec->>DB: get_merged_indicators (is_complete check)
    WExec->>DB: INSERT decisions_log
    WExec-->>Redis: PUBLISH decision_event
```

Áreas envolvidas: [[15-exchange-integration]] · [[21-tasks-catalog]] ·
[[11-services]] (indicators_provider) · [[13-scoring-ml]] ·
[[12-engines]]

## (b) Decisão → Execução → Reconciliação

```mermaid
sequenceDiagram
    autonumber
    participant WExec as scalpyn-worker-execution
    participant Gate as Gate.io REST
    participant DB as Postgres
    participant WStruct as scalpyn-worker-structural
    participant Mon as trade_monitor (10s)
    participant Browser

    WExec->>DB: SELECT decisions_log WHERE event=ALLOW
    WExec->>WExec: gate is_tradable + risk checks
    WExec->>Gate: POST /spot/orders
    Gate-->>WExec: order_id + status
    WExec->>DB: INSERT trades + trade_tracking(status=open)
    WExec-->>Browser: WebSocket (via realtime_bridge)

    loop cada 60s (structural)
        WStruct->>Gate: GET /spot/my_trades (fills)
        WStruct->>DB: trade_reconciliation: bate fill vs trade
    end

    loop cada 10s (execution)
        Mon->>DB: SELECT trade_tracking(status=open)
        Mon->>Gate: GET ticker p/ cada símbolo
        alt TP/SL atingido OU timeout
            Mon->>Gate: POST /spot/orders (sell)
            Mon->>DB: UPDATE trade_tracking<br/>(exit_price, outcome, exit_price_source)
        end
    end

    WStruct->>DB: decision_log_enricher.enrich
    Note over DB: position_lifecycle alimentada<br/>(consumida em /api/performance/*)
```

Áreas envolvidas: [[12-engines]] · [[15-exchange-integration]] ·
[[21-tasks-catalog]] · [[14-models-database]] · [[13-scoring-ml]]
(dataset ML)

## (c) Cloud Run boot (gate de schema)

```mermaid
sequenceDiagram
    autonumber
    participant CR as Cloud Run<br/>startup probe
    participant Sh as start.sh
    participant BG as subshell
    participant Uvi as uvicorn
    participant Mid as _SchemaReadinessGate
    participant DB as Cloud SQL

    CR->>Sh: exec /app/start.sh
    Sh->>Sh: echo CONTAINER ENTRY (stderr+stdout)
    alt K_SERVICE = scalpyn (API) e ASYNC_MIGRATIONS=1
        Sh->>BG: spawn subshell em background
        BG->>DB: alembic upgrade head (3x retry x 90s)
        BG->>DB: validate_critical_schema
        Sh->>Uvi: exec uvicorn :8080
        Uvi-->>CR: bind :8080 IMEDIATO ✓ probe ok
        CR->>Mid: GET /api/dashboard/overview
        alt /tmp/.migrations_done existe
            Mid->>Uvi: pass-through
            Uvi-->>CR: 200
        else schema gate ainda rodando
            Mid-->>CR: 503 Retry-After=10
        end
        BG-->>BG: touch /tmp/.migrations_done
        BG-->>Sh: (watchdog poll a cada 5s, 15min budget)
        opt schema gate falhou
            BG-->>Sh: touch /tmp/.migrations_failed
            Sh->>Uvi: SIGTERM
            Uvi-->>CR: container exit
            CR->>CR: rollback p/ revisão anterior
        end
    else worker / beat (síncrono)
        Sh->>DB: alembic upgrade head (síncrono)
        Sh->>DB: validate_critical_schema (síncrono)
        Sh->>Sh: exec celery worker --queues=$WORKER_QUEUES<br/>--hostname=celery@${K_SERVICE}-uuid
    end
```

Áreas envolvidas: [[40-infra-cloudrun]] · [[10-backend-api]] ·
[[14-models-database]] · [[20-celery-topology]]

## Áreas relacionadas

[[00-INDEX]] · [[10-backend-api]] · [[11-services]] · [[12-engines]] ·
[[13-scoring-ml]] · [[14-models-database]] · [[15-exchange-integration]] ·
[[20-celery-topology]] · [[21-tasks-catalog]] · [[40-infra-cloudrun]] ·
[[41-deploy-cloudbuild]] · [[42-observability]]

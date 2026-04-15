# Scalpyn - PRD

## Problem Statement
`https://scalpyn.vercel.app/watchlist` – Na página watchlist (Pipeline view), a lista de cripto ativos filtrados pelo profile mostra indicadores técnicos (RSI, ADX, BB Width, MACD Hist, EMA 9, EMA 50, EMA 200, EMA Alignment) para apenas alguns ativos. A maioria exibe "–" em todas as colunas de indicadores.

## Architecture
- **Frontend**: Next.js → Vercel (auto-deploy via GitHub main branch)
- **Backend**: FastAPI + Celery + TimescaleDB/PostgreSQL → Cloud Run
- **Exchange**: Gate.io (OHLCV via public API)
- **Task Queue**: Celery + Redis (beat scheduler: tasks a cada 5 min)

## Core Data Flow
1. `collect_5m` Celery task (5 min) → coleta OHLCV 5m de até 500 símbolos (universe top-100 + todos pool coins)
2. `compute_5m` Celery task (encadeado) → calcula indicadores técnicos para símbolos com OHLCV recente (últimas 2h)
3. `pipeline_scan` → filtra ativos pelo profile em 3 níveis (L1/L2/L3)
4. `GET /api/watchlists/{id}/assets` → retorna ativos com indicadores da tabela `indicators`

## Root Cause Identified
- Tabela `indicators` vazia ou sem dados para a maioria dos símbolos
- Causas possíveis: tasks Celery recém-iniciadas, rate-limit/timeout na Gate.io, symbols novos no pool sem histórico

## Fixes Implemented (Feb 2026)

### 1. On-demand indicator computation (`backend/app/api/watchlists.py`)
- Função `_compute_indicators_on_demand()` adicionada
- Quando `GET /api/watchlists/{id}/assets` detecta símbolos sem indicadores na DB:
  - Busca OHLCV 1h (200 candles) diretamente da Gate.io via `market_data_service.fetch_ohlcv`
  - Calcula indicadores usando `FeatureEngine`
  - Cacheia na tabela `indicators` com timeframe='1h'
  - Retorna indicadores calculados na mesma request
- Execução paralela com `asyncio.gather` + `Semaphore(8)` para limitar carga na API
- Cap de 40 símbolos por request para manter tempo de resposta aceitável (~3-5s first load)

### 2. Expand collect_5m universe (`backend/app/tasks/collect_market_data.py`)
- Cap aumentado de 200 → 500 símbolos para cobrir pools com muitos ativos

## Bug Fix #2: Market Cap filter não exclui ativos com market_cap baixo (Feb 2026)

### Problema
A watchlist POOL associada ao Profile Pool (com filtro Market Cap >= 5,000,000)
exibia ativos com market cap muito abaixo do limite (ex: $1.5M, $666K, $23K).

### Causa Raiz
**Dupla falha:**

1. **`pipeline_scan.py` `_fetch_market_data()`**: `market_cap` e `volume_24h` eram
   definidos como `None` quando NULL no banco (em vez de `0.0`). Com `None`, o
   `ProfileEngine._apply_filters` usava avaliação "lenient" e **saltava** a condição
   em vez de falhar → asset passava mesmo sem market_cap conhecido.

2. **`profile_engine.py` `_apply_filters()`**: Avaliação lenient aplicada indiscriminadamente
   a TODOS os campos, incluindo campos de mercado (market_cap, volume_24h) que DEVEM
   ser estritamente avaliados.

### Fixes
- **`backend/app/tasks/pipeline_scan.py`**: `market_cap` e `volume_24h` agora defaultam
  para `0.0` quando NULL → filtro `>= 5M` avalia `0 >= 5M = False` → asset excluído
- **`backend/app/services/profile_engine.py`**: `_apply_filters` agora aplica enforcement
  ESTRITO para campos meta (`volume_24h`, `market_cap`, `price`, `change_24h`):
  se o campo é None, a condição FALHA (não é saltada). Campos indicadores (RSI, ADX, etc.)
  continuam lenient.
- **`backend/app/tasks/pipeline_scan.py`**: Adicionado alias `atr_percent → atr_pct`
  no asset dict para corrigir mismatch entre nome usado na GUI e nome armazenado
  pelo feature engine.

- P0: On-demand indicators (DONE)
- P1: Monitoring/alertas quando tasks Celery falham silenciosamente
- P2: Cache TTL para on-demand indicators (re-compute se > 30min antigo)
- P3: Indicadores 5m opcionais por symbol na watchlist view

## Next Tasks
- Validar que indicadores aparecem para todos os símbolos em produção após deploy
- Monitorar logs de "[Pipeline] On-demand indicators computed" em Cloud Run

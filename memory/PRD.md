# Scalpyn - PRD

## Problem Statement
Sistema de análise de criptoativos via Gate.io com pipeline de filtros L1/L2/L3 e scoring alpha.

## Architecture
- **Frontend**: Next.js → Vercel (auto-deploy via GitHub main branch)
- **Backend**: FastAPI + Celery + TimescaleDB/PostgreSQL → Cloud Run
- **Exchange**: Gate.io (OHLCV, Tickers, Orderbook via API pública)
- **Task Queue**: Celery + Redis (beat: collect_5m → compute_5m → pipeline_scan a cada 5 min)

## Data Flow
1. `collect_all` (60s) → Tickers → market_metadata (price, volume, spread_pct de bid/ask)
2. `collect_5m` (5min) → OHLCV 100 candles + Orderbook top-10 → ohlcv + market_metadata
3. `compute_5m` (encadeado) → RSI/ADX/EMA/DI/BB/MACD → indicators table
4. `pipeline_scan` (5min) → market_metadata + indicators → filtros profile → pipeline_watchlist_assets
5. `fetch_market_caps` (30min) → Gate.io currencies → market_cap em market_metadata + pwa
6. `GET /api/watchlists/{id}/assets` → lê pwa + on-demand OHLCV/indicators/scoring se necessário

## Bug Fixes Implemented

### Fix #1 – Indicadores ausentes (Feb 2026)
`_compute_indicators_on_demand()` em watchlists.py — busca OHLCV e computa indicators quando ausentes.

### Fix #2 – Filtro Market Cap não aplicado (Feb 2026 - v1)
profile_engine.py `_STRICT_META` + pipeline_scan defaults 0.0.

### Fix #2b – L1 vazia após fix anterior (Feb 2026 - v2)
**CAUSA**: SQL em `_fetch_market_data` incluía colunas `spread_pct`/`orderbook_depth_usdt` que podem não existir → query falha → []
**FIX**: TRY/EXCEPT com query fallback sem as colunas novas
**FIX**: Revertido `market_cap = None` (não 0.0) + COALESCE de market_metadata JOIN pwa
para obter o melhor valor disponível de market_cap/volume_24h

### Fix #3 – L2/L3 com assets stale (Fix "down assets") (Feb 2026)
**CAUSA**: Pipeline scan de L2 buscava TODOS assets do L1 incluindo `level_direction = 'down'`
**FIX**: `WHERE (level_direction IS NULL OR level_direction = 'up')` na query do upstream

### Fix #4 – Alpha Score = 0.0 (Feb 2026)
**CAUSA**: `if a.alpha_score` era falsy para 0.0 → retornava None
**FIX A**: `if a.alpha_score is not None`
**FIX B**: On-demand scoring em `get_watchlist_assets` — após calcular indicators, computa
alpha score com ScoreEngine usando profile config. Override do score 0/None com valor real.
Cache do score calculado em pipeline_watchlist_assets para próximas requests.

### Fix #5 – Alpha Score "–" (0 falsy) (Feb 2026)
`_asset_to_dict`: suporta `override_score` para usar score calculado on-demand.

### Fix Filtros (Feb 2026)
- `_passes_profile_filters` em watchlists.py: suporte operator `between` (min/max)
- `!=` operator adicionado

## Features Implementadas

### Liquidez Real
- `spread_pct` e `orderbook_depth_usdt` em market_metadata (colunas novas)
- `fetch_orderbook_metrics()` em market_data_service
- `collect_5m` busca orderbook por símbolo
- `collect_all` calcula spread de tickers
- Campo `di_trend = di_plus > di_minus` no pipeline_scan
- Operadores `di+>di-` / `di->di+` no ScoreEngine

### GUI ConditionBuilder
- Operator `entre` (between) com inputs Min/Max → ex: RSI entre 45 e 60
- Grupo "Liquidez Real" com Spread % e Profundidade Book
- Campo `di_trend` = "DI+ > DI- (Alta)" boolean
- 6 grupos organizados

## Filters Recomendados pelo Usuário
**Pool**: `spread_pct <= 1`, `orderbook_depth_usdt >= 5000`
**L1**: `spread_pct <= 0.8`, `orderbook_depth_usdt >= 10000`

### Fix #6 – UnboundLocalError `ind_rows` em pipeline_scan (Feb 2026) — P0
**CAUSA**: `ind_rows` e `score_rows` estavam dentro do bloco `except` do fallback de
liquidez. Quando a query principal (com `spread_pct`/`orderbook_depth_usdt`) **sucedia**,
o `except` nunca executava e `ind_rows` ficava sem valor → `UnboundLocalError` no Cloud Run.
**FIX**: Separado em dois blocos try/except independentes:
  1. try/except para `meta_rows` (fallback de colunas de liquidez)
  2. try/except para `ind_rows` + `score_rows` (sempre executa)

## Prioritized Backlog
- P1: Scoring rules melhores configuradas pelo usuário nos profiles
- P2: Coluna Alpha com cores (verde/amarelo/vermelho por threshold)
- P2: Colunas Spread % e Depth visíveis na tabela watchlist
- P3: Monitoramento de falhas no pipeline Celery

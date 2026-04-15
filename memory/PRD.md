# Scalpyn - PRD

## Problem Statement
Sistema de análise de criptoativos via Gate.io com pipeline de filtros L1/L2/L3 e scoring.

## Architecture
- **Frontend**: Next.js → Vercel (auto-deploy via GitHub main branch)
- **Backend**: FastAPI + Celery + TimescaleDB/PostgreSQL → Cloud Run
- **Exchange**: Gate.io (OHLCV, Tickers, Orderbook via API pública)
- **Task Queue**: Celery + Redis (beat: collect_5m → compute_5m → pipeline_scan a cada 5 min)

## Bug Fixes Implemented

### Fix #1 – Indicadores ausentes na watchlist (Feb 2026)
**`backend/app/api/watchlists.py`**: `_compute_indicators_on_demand()` adicionada — busca OHLCV 1h diretamente da Gate.io quando indicadores ausentes na DB, calcula via FeatureEngine, cacheia na tabela `indicators`.
**`backend/app/tasks/collect_market_data.py`**: Cap 200→500 símbolos no collect_5m.

### Fix #2 – Filtro Market Cap não aplicado (Feb 2026)
**`backend/app/tasks/pipeline_scan.py`**: `market_cap` e `volume_24h` defaultam para `0.0` (não None) → filtros tipo `>= 5M` avaliam `0 >= 5M = False` → asset excluído.
**`backend/app/services/profile_engine.py`**: _STRICT_META adicionado → enforcement estrito para campos meta (market_cap, volume_24h, price, change_24h, spread_pct, orderbook_depth_usdt).

### Fix #3 – Alpha Score sempre "–" (Feb 2026)
**`backend/app/api/watchlists.py`** `_asset_to_dict`: `if a.alpha_score` → `if a.alpha_score is not None` (0.0 era falsy → None).

## Feature Implementations (Feb 2026)

### Novos Indicadores de Liquidez
- **`backend/app/services/market_data_service.py`**: `fetch_orderbook_metrics(symbol)` → `{spread_pct, orderbook_depth_usdt}` via Gate.io `/spot/order_book`; `compute_spread_from_ticker()` → spread_pct de bid/ask dos tickers (sem custo extra de API)
- **`backend/app/tasks/collect_market_data.py`**: `collect_all` agora armazena `spread_pct` de tickers; `collect_5m` busca orderbook por símbolo e armazena `orderbook_depth_usdt`
- **`backend/app/init_db.py`**: Colunas `spread_pct DECIMAL(10,4)` e `orderbook_depth_usdt DECIMAL(20,2)` adicionadas à `market_metadata`
- **`backend/app/tasks/pipeline_scan.py`**: `spread_pct` e `orderbook_depth_usdt` incluídos no asset dict; `di_trend = di_plus > di_minus` como campo derivado

### Correção DI Directional Index
- **`backend/app/services/score_engine.py`**: Operadores `di+>di-` e `di->di+` adicionados para comparação real de tendência
- **`backend/app/tasks/pipeline_scan.py`**: Campo `di_trend` (boolean) calculado automaticamente = `di_plus > di_minus`

### RSI Between (Range)
- **`backend/app/services/rule_engine.py`**: Já suportava `between` com `{min, max}`
- **`backend/app/api/watchlists.py`** `_passes_profile_filters`: Adicionado suporte a `between`

### GUI ConditionBuilder
- **`frontend/components/profiles/ConditionBuilder.tsx`**: 
  - Interface `Condition` com `min?`/`max?` para operator `between`
  - Operador `entre` (between) com dois inputs (Min / Max) → RSI entre 45 e 60
  - Grupo "Liquidez Real" com `Spread %` e `Profundidade Book (USDT)`
  - Campo `di_trend` = "DI+ > DI- (Alta)" como boolean
  - Fix: campo boolean agora force operator `==`
  - Grupos organizados: Preco e Volume | Liquidez Real | Momentum | Tendencia e Estrutura | EMA e Alinhamento | Scores

## Filters Recommended by User
**Pool (universo)**:
- `spread_pct <= 1`
- `orderbook_depth_usdt >= 5000`

**L1 (qualidade)**:
- `spread_pct <= 0.8`
- `orderbook_depth_usdt >= 10000`

## Data Flow
1. `collect_all` (60s) → Tickers → market_metadata (price, volume, spread_pct)
2. `collect_5m` (5min) → OHLCV + Orderbook → ohlcv table + market_metadata (orderbook_depth)
3. `compute_5m` (encadeado) → calcula RSI/ADX/EMA/DI/BB → indicators table
4. `pipeline_scan` (5min) → lê indicators + market_metadata → aplica filtros → armazena em pipeline_watchlist_assets

## Prioritized Backlog
- P0: Todos os fixes acima (DONE)
- P1: Scoring rules melhores (DEFAULT_SCORE só tem 3 regras simples)
- P2: UI de scoring rules com suporte a `di+>di-` operador e `between`
- P3: Monitoramento de falhas no pipeline Celery
- P3: Coluna `Spread %` e `Depth` visíveis na watchlist table

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

### Fix #7 – Score Engine usa DEFAULT_SCORE em vez do /settings/score (Feb 2026) — P1
**CAUSA**: `compute_scores.py`, `pipeline_scan.py` e `watchlists.py` usavam `DEFAULT_SCORE`
hardcoded para calcular Alpha Score, ignorando as regras configuradas pelo usuário em `/settings/score`.
**FIX**:
  - `watchlists.py`: on-demand scoring agora carrega `config_service.get_config(db, "score", user_id)`
    e usa `ScoreEngine(global_score_config)` diretamente (em vez de `ProfileEngine._process_single_asset`)
  - `pipeline_scan.py`: `_apply_level_filter` recebe `score_config` e sobrescreve
    `engine.score_engine` com o config global do usuário
  - `compute_scores.py`: busca user_id do primeiro pipeline watchlist e carrega o config do DB

### Fix #8 – Operador `between` falhava para `target_value = None` (Feb 2026) — P1
**CAUSA**: `score_engine._evaluate_rule` retornava `False` prematuramente quando `target_value is None`,
impedindo o handler `between` de ser alcançado (o `between` usa `min`/`max`, não `value`).
**FIX**: Mover verificação de `target_value is None` para depois do handler `between`.

## GUI Score Engine (/settings/score) — Melhorias (Feb 2026)
- Adicionado `macd_histogram` à lista de indicadores
- Corrigido input de value=0 (era convertido para null por `|| null`)
- Operador `between` com campos Min/Max (para RSI e outros intervalos)
- Total de pontos exibido no cabeçalho "Scoring Rules" (ex: "45 / 100 pts")
- Novos indicadores: di_plus, di_minus, di_trend, spread_pct, orderbook_depth_usdt, bb_width, stoch_k, stoch_d, vwap_distance_pct

### Fix #9 – L3 sempre vazia (Feb 2026) — P0
**CAUSAS**:
  1. `_evaluate_l3_signals` (Celery): Se o perfil não tem `signals.conditions`, `SignalEngine.evaluate`
     retorna `signal=False` para todos os ativos → L3 vazia permanentemente
  2. `_resolve_and_persist` (Refresh Now): `require_signal and signal < 50` usava `alpha_scores.signal_score`
     pré-computado — o DEFAULT_SCORE só gera até 30 pts em categoria signal (abaixo do threshold 50) →
     L3 sempre vazia no refresh manual
**FIX**:
  - `_evaluate_l3_signals`: se perfil sem signal conditions → fallback para modo scoring
    (retorna todos ativos filtrados, ordenados por score)
  - `_resolve_and_persist` (L3): substituído checagem de `signal_score` pré-computado por
    avaliação live via `SignalEngine` com os indicadores reais. Se perfil sem signal conditions →
    todos os ativos acima de `min_score` passam

### Fix #10 – Falha arquitetural na análise em camadas (Feb 2026) — P0

**3 bugs causando L3 mostrar ativos que deveriam ser filtrados pelo Profile Pool:**

**Bug A — Cascade indevido em `_get_base_symbols`**:
  Quando qualquer nível pai tinha 0 ativos ativos, o sistema subia até os pool_coins crus
  (1259 símbolos), pulando todos os filtros. Fix: quando o pai já foi populado antes mas
  está vazio, retorna `[]` em vez de escalar para pool_coins.

**Bug B — `_passes_profile_filters` muito restritivo**:
  Campos de indicadores ausentes (atr_pct, rsi, etc.) → None → `results.append(False)`
  → todos os ativos excluídos do Pool manual. Fix: apenas campos meta (`market_cap`,
  `volume_24h`, `price_change_24h`, `spread_pct`, `orderbook_depth_usdt`) são STRICT
  (None = FAIL); campos de indicadores são LENIENT (None = skip).

**Bug C — Signal conditions sob chave errada**:
  Código buscava `profile.config.signals.conditions` mas frontend salva sob
  `entry_triggers`. Fix: checks `entry_triggers` primeiro, depois `signals`,
  em `ProfileEngine.__init__`, `_evaluate_l3_signals`, e `_resolve_and_persist`.

**Fix adicional**: assets_out em `_resolve_and_persist` agora inclui dados de indicadores
  do `ind_map`, permitindo que condições de filtro de indicadores sejam avaliadas
  corretamente. Alias `change_24h` ↔ `price_change_24h` adicionado.

## Prioritized Backlog
- P1: Scoring rules melhores configuradas pelo usuário nos profiles
- P2: Coluna Alpha com cores (verde/amarelo/vermelho por threshold)
- P2: Colunas Spread % e Depth visíveis na tabela watchlist
- P3: Monitoramento de falhas no pipeline Celery

## Fix #12 — Score Breakdown Table (Pipeline Watchlist) — Apr 2026

### O que foi implementado:

**Backend:**
- `score_engine.py`: Adicionados `_IND_LABELS`, `_IND_CATEGORY` (module-level) e método `get_full_breakdown(indicators)` → retorna lista detalhada por regra: indicator, label, actual_value, passed, points_awarded, points_possible, condition_text, category
- `pipeline_watchlists.py`:
  - Boolean indicators agora incluídos em `ind_map` (ema9_gt_ema50, etc.)
  - Score engine carregado e `score_rules` computado por ativo no GET `/api/watchlists/{id}/assets`

**Frontend:**
- Novo componente `/components/watchlist/PipelineAssetTable.tsx`:
  - Tabela fixa: Symbol | Score (barra animada) | RSI | Vol Spike | Taker Ratio | ADX | MACD Hist | EMA Trend | Status | Weakness
  - `IndicatorCell`: valor + ícone ✅/❌/⚬ com tooltip da regra
  - `StatusBadge`: STRONG/GOOD/MIXED/WEAK por score
  - `Weakness`: lista dos indicadores com mais pontos perdidos
  - Alertas visuais: DIV (divergência MACD), ADX (breakout potencial)
  - Accordion drilldown por ativo: todas as regras agrupadas por categoria com pts awarded/possible
- `page.tsx`: WatchlistRow atualizado para usar PipelineAssetTable

--- (Feb 2026) — P0

**3 erros no Cloud Run (710 + 568 + 82 ocorrências)**:

**Bug A — "Future attached to a different loop" (asyncpg) — 710x**:
`pipeline_scan.py:369` + demais tasks Celery
**CAUSA**: Celery tasks usam `asyncio.new_event_loop()` a cada execução. O `AsyncSessionLocal` compartilhado usa engine com connection pool do asyncpg vinculado ao loop anterior → erro ao reutilizar conexões em novo loop.
**FIX**: Criado `CeleryAsyncSessionLocal` com `NullPool` em `database.py`. Todos os 13 arquivos de tasks Celery atualizados para usar `CeleryAsyncSessionLocal`.

**Bug B — "Event loop is closed" — 568x**:
**CAUSA**: Consequência direta do Bug A — tasks tentam usar conexões do pool após o loop fechar.
**FIX**: Mesma correção do Bug A (NullPool não reutiliza conexões entre loops).

**Bug C — AttributeError: 'Profile' object has no attribute 'config_json' — 82x**:
`execute_buy.py:195` em `_execute_buy_cycle_async`
**CAUSA**: `Profile` model tem campo `config` (não `config_json`). `ConfigProfile` model tem `config_json` — confusão entre os dois models.
**FIX**: `prof.config_json` → `prof.config` em execute_buy.py linha 195-196.

**Fix adicional — L3 usa DEFAULT_WEIGHTS em vez do /settings/score**:
`_evaluate_l3_signals` não recebia `score_config` → scores calculados com DEFAULT_WEIGHTS → assets podiam falhar o `min_score` gate do L3.
**FIX**: `_evaluate_l3_signals` agora aceita e aplica `score_config` (mesmo padrão do `_apply_level_filter` para L1/L2).

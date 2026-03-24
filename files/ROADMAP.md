# Scalpyn — Implementation Roadmap

## Branch Strategy

```
main
 └─ develop
     ├─ feature/docs-architecture          ← FASE 0 (estes docs)
     ├─ feature/gate-adapter               ← FASE 1
     ├─ feature/spot-engine                ← FASE 2
     ├─ feature/futures-engine             ← FASE 3
     ├─ feature/trading-desk-ui            ← FASE 4
     └─ feature/websocket-realtime         ← FASE 5
```

## FASE 0 — Docs (este PR)

```
Branch: feature/docs-architecture
Ação: Commitar os 5 documentos de arquitetura

docs/
├── architecture/
│   ├── 01-score-driven-framework.md      # Spot + Futures, sem grid, score-driven
│   ├── 02-futures-leveraged-framework.md # Framework futuros com anti-liquidação
│   ├── 03-trading-desk-navigation.md     # Sidebar, pages, components, routes
│   └── 04-sell-flow-improvements.md      # 10 melhorias no fluxo de venda
├── api-integration/
│   └── gate-io-v4-mapping.md             # Mapeamento completo Gate API → App
└── implementation/
    └── ROADMAP.md                        # Este arquivo
```

Commit: `docs: add architecture specs for score-driven engine, futures framework, and Gate.io API mapping`

---

## FASE 1 — Gate.io Exchange Adapter (Backend)

```
Branch: feature/gate-adapter
Dependência: nenhuma
Estimativa: 3-5 dias
```

### Arquivos a criar/modificar:

```
backend/
├── app/
│   ├── adapters/
│   │   ├── base.py                    # Abstract Exchange Adapter (já existe?)
│   │   └── gate_adapter.py            # ★ NOVO — Gate.io v4 implementation
│   ├── services/
│   │   └── exchange_service.py        # Registrar Gate adapter
│   └── config/
│       └── exchange_gate.py           # Config schema para Gate
```

### Tasks:

- [ ] `gate_adapter.py` — Implementar abstract interface para Gate.io
  - [ ] `get_spot_balance()` → `GET /spot/accounts`
  - [ ] `get_futures_balance()` → `GET /futures/usdt/accounts`
  - [ ] `get_futures_position()` → `GET /futures/usdt/positions/{contract}`
  - [ ] `get_contract_info()` → `GET /futures/usdt/contracts/{contract}`
  - [ ] `place_spot_order()` → `POST /spot/orders`
  - [ ] `place_futures_order()` → `POST /futures/usdt/orders`
  - [ ] `set_leverage()` → `POST /futures/usdt/positions/{contract}/leverage`
  - [ ] `create_price_trigger()` → `POST /futures/usdt/price_orders`
  - [ ] `modify_price_trigger()` → `PUT /futures/usdt/price_orders/amend/{id}`
  - [ ] `cancel_price_trigger()` → `DELETE /futures/usdt/price_orders/{id}`
  - [ ] `close_position()` → `POST /futures/usdt/orders` (is_close=true)
  - [ ] `get_funding_rate()` → `GET /futures/usdt/contracts/{contract}`
  - [ ] `get_contract_stats()` → `GET /futures/usdt/contract_stats`
  - [ ] `get_klines()` → `GET /spot/candlesticks` ou `/futures/usdt/candlesticks`
  - [ ] `get_orderbook()` → `GET /spot/order_book` ou `/futures/usdt/order_book`
  - [ ] `get_tickers()` → `GET /spot/tickers` ou `/futures/usdt/tickers`
  - [ ] `transfer_between_accounts()` → `POST /wallet/transfers`
- [ ] Auth: HMAC-SHA512 signature (Gate.io v4 auth scheme)
- [ ] Error handling: mapear Gate error labels para Scalpyn errors
- [ ] Rate limiting: implementar throttle (200 req/s orders, 400 req/s reads)
- [ ] Tests: mock responses, test cada método

### Commit convention:
```
feat(adapter): implement Gate.io v4 spot order placement
feat(adapter): add Gate.io futures leverage and position management
feat(adapter): add Gate.io price-triggered orders (TP/SL)
test(adapter): add Gate.io adapter unit tests
```

---

## FASE 2 — Spot Engine (Backend)

```
Branch: feature/spot-engine
Dependência: FASE 1 (Gate adapter)
Estimativa: 5-7 dias
```

### Arquivos a criar/modificar:

```
backend/
├── app/
│   ├── engines/
│   │   ├── spot_scanner.py             # ★ NOVO — Scanner loop (score → buy)
│   │   ├── spot_sell_manager.py        # ★ NOVO — 5 camadas de venda
│   │   ├── spot_position_manager.py    # ★ NOVO — HOLDING_UNDERWATER, DCA
│   │   └── spot_capital_manager.py     # ★ NOVO — Capital allocation
│   ├── models/
│   │   └── position.py                 # ★ MODIFICAR — Adicionar campos spot
│   ├── schemas/
│   │   └── spot_engine_config.py       # ★ NOVO — Pydantic schema
│   └── api/
│       └── routes/
│           ├── spot_engine.py          # ★ NOVO — API endpoints
│           └── config.py              # MODIFICAR — Adicionar spot_engine config
```

### Tasks:

- [ ] DB migration: adicionar campos `profile`, `dca_layers`, `original_entry_price`, `status=HOLDING_UNDERWATER` à tabela positions
- [ ] `spot_scanner.py` — Loop principal
  - [ ] Cada ciclo: calcular score para universo → rankear → filtrar → comprar top N
  - [ ] Capital check antes de cada compra
  - [ ] Cooldown per symbol e global
  - [ ] Macro filter (bloquear compras em risk_off)
- [ ] `spot_sell_manager.py` — 5 camadas adaptadas
  - [ ] REGRA SUPREMA: `never_sell_at_loss = True` (inviolável)
  - [ ] Ranging → Exhaustion → AI → Target → Trailing
  - [ ] Volatility filter (bloquear venda em squeeze)
  - [ ] Market structure check (HTF)
- [ ] `spot_position_manager.py`
  - [ ] Status transitions: ACTIVE ↔ HOLDING_UNDERWATER
  - [ ] DCA logic (opcional, config-driven)
  - [ ] Alertas para posições underwater
  - [ ] Opportunity cost tracking
- [ ] `spot_capital_manager.py`
  - [ ] available_capital = balance - reserve - locked
  - [ ] Exposure per asset check
  - [ ] Max positions check
- [ ] Config schema: `spot_engine` config_type completo
- [ ] API endpoints: start/pause engine, get status
- [ ] Celery task: `spot_scan_cycle` periodic task

---

## FASE 3 — Futures Engine (Backend)

```
Branch: feature/futures-engine
Dependência: FASE 1 (Gate adapter) + FASE 2 parcial (shared models)
Estimativa: 7-10 dias
```

### Arquivos a criar/modificar:

```
backend/
├── app/
│   ├── engines/
│   │   ├── futures_scanner.py          # ★ NOVO — 5-Layer scoring → trade
│   │   ├── futures_risk_engine.py      # ★ NOVO — Position sizing, leverage calc
│   │   ├── futures_position_manager.py # ★ NOVO — TP/SL/trailing management
│   │   ├── futures_anti_liq.py         # ★ NOVO — Anti-liquidation 3 camadas
│   │   ├── futures_macro_gate.py       # ★ NOVO — Macro regime filter
│   │   └── futures_emergency.py        # ★ NOVO — Emergency exits
│   ├── scoring/
│   │   ├── layer_liquidity.py          # ★ NOVO — L1 scoring
│   │   ├── layer_structure.py          # ★ NOVO — L2 scoring
│   │   ├── layer_momentum.py           # ★ NOVO — L3 scoring
│   │   ├── layer_volatility.py         # ★ NOVO — L4 scoring
│   │   └── layer_order_flow.py         # ★ NOVO — L5 scoring
│   ├── models/
│   │   └── position.py                 # MODIFICAR — campos futures
│   ├── schemas/
│   │   └── futures_engine_config.py    # ★ NOVO — Pydantic schema
│   └── api/
│       └── routes/
│           ├── futures_engine.py       # ★ NOVO — API endpoints
│           └── macro.py               # ★ NOVO — Macro regime API
```

### Tasks:

- [ ] DB migration: campos futures na tabela positions (leverage, liq_price, stop_loss, tp1/2/3, funding_cost)
- [ ] 5-Layer Scoring Engine
  - [ ] L1 Liquidity (volume, spread, book depth, relative vol)
  - [ ] L2 Structure (HH/HL, trend, key levels, MTF)
  - [ ] L3 Momentum (RSI, MACD, EMA, VWAP, divergences)
  - [ ] L4 Volatility (ATR, BB, squeeze detection, compression)
  - [ ] L5 Order Flow (taker ratio, funding, OI, liquidations)
- [ ] `futures_risk_engine.py`
  - [ ] Position sizing: risk% → size → leverage (calculada)
  - [ ] Stop loss placement (structure → liquidity → ATR)
  - [ ] Take profit levels (R:R based)
  - [ ] Score → size multiplier mapping
- [ ] `futures_anti_liq.py`
  - [ ] Camada 1: Validar stop_to_liq distance antes de trade
  - [ ] Camada 2: Runtime monitor (alert/critical/emergency zones)
  - [ ] Camada 3: Force close se critical zone
- [ ] `futures_position_manager.py`
  - [ ] TP1 hit → close parcial + move SL to BE (via Gate API)
  - [ ] TP2 hit → close parcial + activate trailing
  - [ ] Trailing ATR-based (internal, not exchange)
  - [ ] Funding drain monitor
- [ ] `futures_macro_gate.py`
  - [ ] BTC trend + DXY + Funding + Liquidations + VIX scoring
  - [ ] Regime classification → direction/size modifiers
  - [ ] Pre-event calendar buffer
- [ ] `futures_emergency.py`
  - [ ] Macro regime shift → exit
  - [ ] BTC flash crash → exit alts
  - [ ] Funding explosion → exit
  - [ ] Exchange latency → alert
- [ ] Gate.io integration
  - [ ] Set leverage antes de cada trade
  - [ ] Criar SL + TP como price_orders separados
  - [ ] Mover SL via amend quando TP1 hit
  - [ ] Cancel remaining triggers quando posição fecha
- [ ] Celery tasks: `futures_scan_cycle`, `anti_liq_monitor`, `macro_regime_update`

---

## FASE 4 — Trading Desk UI (Frontend)

```
Branch: feature/trading-desk-ui
Dependência: FASE 2 + 3 (backend APIs)
Estimativa: 7-10 dias
```

### Arquivos a criar:

```
frontend/app/trading-desk/
├── layout.tsx
├── spot/
│   ├── page.tsx                        # Spot Trading config page
│   └── components/
│       ├── SpotEngineStatus.tsx
│       ├── SpotBuyingConfig.tsx
│       ├── SpotSellingConfig.tsx
│       ├── SpotHoldingConfig.tsx
│       ├── SpotDCAConfig.tsx
│       ├── SpotMacroFilter.tsx
│       └── SpotTrailingConfig.tsx
├── futures/
│   ├── page.tsx                        # Futures Trading config page
│   └── components/
│       ├── FuturesEngineStatus.tsx
│       ├── FuturesScoringConfig.tsx
│       ├── FuturesLeverageAntiLiq.tsx
│       ├── FuturesSizingConfig.tsx
│       ├── FuturesStopTakeProfit.tsx
│       ├── FuturesTrailingConfig.tsx
│       ├── FuturesGuards.tsx
│       ├── FuturesMacroGate.tsx
│       └── FuturesLossLimits.tsx
├── positions/
│   ├── page.tsx
│   └── components/
│       ├── PositionsOverview.tsx
│       ├── SpotPositionsTable.tsx
│       ├── FuturesPositionsTable.tsx
│       ├── UnderwaterSummary.tsx
│       └── LiquidationMonitor.tsx
└── history/
    ├── page.tsx
    └── components/
        ├── TradeHistoryTable.tsx
        └── PnLSummaryCards.tsx
```

### Tasks:

- [ ] Update Sidebar nav (adicionar TRADING DESK section, remover Pools)
- [ ] Shared components: `EngineStatusBar`, `ConfigSection`, `SliderWithValue`, `RiskPreviewPanel`
- [ ] Spot Trading page (7 seções colapsáveis)
- [ ] Futures Trading page (9 seções colapsáveis)
- [ ] Positions page (overview cards + tabelas spot/futures + underwater summary)
- [ ] History page (tabela unificada + summary cards + export)
- [ ] Hooks: `useTradingConfig()`, `useEngineStatus()`, `usePositions()`, `useMacroRegime()`
- [ ] WebSocket integration para P&L live nas posições

---

## FASE 5 — WebSocket Real-time (Backend + Frontend)

```
Branch: feature/websocket-realtime
Dependência: FASE 1 + 4
Estimativa: 3-5 dias
```

### Tasks:

- [ ] Gate.io WebSocket client (subscribe to channels)
- [ ] `futures.positions` → update P&L live no Scalpyn
- [ ] `futures.orders` → detect fill/cancel events
- [ ] `futures.autoorders` → detect TP/SL trigger events
- [ ] `futures.liquidates` → emergency alert
- [ ] `spot.orders` → detect spot fills
- [ ] Forward to frontend via Scalpyn WebSocket/SSE
- [ ] Reconnection logic com exponential backoff

---

## Prioridade de Execução

```
Semana 1-2:  FASE 0 (docs) + FASE 1 (Gate adapter)
Semana 2-3:  FASE 2 (Spot engine)
Semana 3-5:  FASE 3 (Futures engine)
Semana 5-6:  FASE 4 (Trading Desk UI)
Semana 6-7:  FASE 5 (WebSocket) + Integration testing
Semana 7-8:  Paper trading mode → stress testing → live
```

---

## Como Usar com Claude Code

```bash
# 1. Clonar e entrar no repo
cd ~/scalpyn
git checkout develop

# 2. Criar branch para docs
git checkout -b feature/docs-architecture

# 3. Copiar docs (os arquivos deste pacote)
cp -r docs/ .

# 4. Commit e push
git add docs/
git commit -m "docs: add architecture specs for score-driven engine and Gate.io API mapping"
git push origin feature/docs-architecture

# 5. Criar PR no GitHub
# 6. Merge para develop

# 7. Começar FASE 1
git checkout develop
git pull
git checkout -b feature/gate-adapter

# 8. Usar Claude Code para implementar
# Claude Code pode ler os docs como contexto e gerar o código alinhado
```

### Prompt Sugerido para Claude Code (FASE 1):

```
Leia os docs em docs/api-integration/gate-io-v4-mapping.md e
docs/architecture/02-futures-leveraged-framework.md.

Implemente o Gate.io v4 exchange adapter em
backend/app/adapters/gate_adapter.py seguindo o abstract base
class existente. Use a lib gate-api do PyPI. Implemente todos
os métodos listados na FASE 1 do ROADMAP.md.
```

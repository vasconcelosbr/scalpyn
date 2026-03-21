# Scalpyn — Gate.io API v4 Integration: Mapeamento Completo

## API Base

```
REST:       https://api.gateio.ws/api/v4
Futures:    https://fx-api.gateio.ws/api/v4     (alternativo, futures only)
TestNet:    https://api-testnet.gateapi.io/api/v4
WebSocket:  wss://fx-ws.gateio.ws/v4/ws/{settle}
SDK:        pip install gate-api
Settle:     "usdt" (USDT-margined) ou "btc" (coin-margined)
```

---

## Mapeamento: Endpoint API → Função no App → Seção na GUI

Somente endpoints relevantes ao fluxo do Scalpyn (spot score-driven + futures alavancado). Funcionalidades ignoradas: lending, earn, dual investment, flash swap, P2P, OTC, subaccounts admin, rebates, options, delivery futures, multi-collateral loans.

---

## 1. CONFIGURAÇÃO DA CONTA (Exchange Card — Settings → Exchanges)

| Função App | API Endpoint | Método | Seção GUI |
|-----------|-------------|--------|-----------|
| Testar conexão | `GET /futures/{settle}/accounts` | GET | Exchange Card → "Test Connection" |
| Verificar saldo spot | `GET /spot/accounts` | GET | Engine Status Bar (saldo USDT) |
| Verificar saldo futures | `GET /futures/{settle}/accounts` | GET | Engine Status Bar (margem disponível) |
| Verificar fees | `GET /wallet/fee` | GET | Info display (maker/taker fees) |
| Fees por contrato | `GET /futures/{settle}/fee` | GET | Info display por contrato |
| Transferir spot→futures | `POST /wallet/transfers` | POST | Botão "Transfer to Futures" |
| Transferir futures→spot | `POST /wallet/transfers` | POST | Botão "Transfer to Spot" |

### Transfer entre contas

```python
# Spot → Futures
POST /wallet/transfers
{
  "currency": "USDT",
  "from": "spot",
  "to": "futures",
  "amount": "1000"
}

# Futures → Spot
POST /wallet/transfers
{
  "currency": "USDT",
  "from": "futures",
  "to": "spot",
  "amount": "500"
}
```

**GUI:** Botão em cada Engine Status Bar para transferir fundos entre contas. Modal com campo de amount + direção.

---

## 2. MARKET DATA (Shared — Scanner, Watchlist, Indicators)

| Função App | API Endpoint | Método | Seção GUI |
|-----------|-------------|--------|-----------|
| Listar contratos futuros | `GET /futures/{settle}/contracts` | GET | Universe config (quais contratos operar) |
| Info de contrato (leverage_min/max, fees, limits) | `GET /futures/{settle}/contracts/{contract}` | GET | Validação de limites |
| Listar pares spot | `GET /spot/currency_pairs` | GET | Universe config (quais pares spot) |
| Ticker (preço, volume 24h, high/low) | `GET /spot/tickers` ou `GET /futures/{settle}/tickers` | GET | Watchlist, Scanner |
| Order book (profundidade) | `GET /spot/order_book` ou `GET /futures/{settle}/order_book` | GET | L1 Liquidity Score |
| K-lines (OHLCV) | `GET /spot/candlesticks` ou `GET /futures/{settle}/candlesticks` | GET | Feature Engine (RSI, MACD, EMA, ATR, BB) |
| Trades recentes | `GET /spot/trades` ou `GET /futures/{settle}/trades` | GET | L5 Order Flow (taker analysis) |
| Funding rate atual | `GET /futures/{settle}/contracts/{contract}` → `funding_rate` | GET | L5 Order Flow, Funding Guard |
| Funding rate histórico | `GET /futures/{settle}/funding_rate` | GET | Funding trend analysis |
| Estatísticas (OI, liquidações, long/short ratio) | `GET /futures/{settle}/contract_stats` | GET | L5 Order Flow, OI Guard |
| Liquidation history (mercado) | `GET /futures/{settle}/liq_orders` | GET | Liquidation heatmap proxy |
| Risk limit tiers (por contrato) | `GET /futures/{settle}/risk_limit_tiers` | GET | Anti-liquidation calculation |

### Dados Críticos do Contrato (para GUI de limites)

```python
GET /futures/usdt/contracts/BTC_USDT

Response inclui:
{
  "name": "BTC_USDT",
  "leverage_min": "1",          # ← Min leverage configurável
  "leverage_max": "100",        # ← Max leverage permitido pela exchange
  "order_size_min": 1,          # ← Min contracts por ordem
  "order_size_max": 1000000,    # ← Max contracts por ordem
  "maintenance_rate": "0.005",  # ← Taxa de manutenção (para calc liquidação)
  "mark_price": "67543.21",     # ← Mark price atual
  "funding_rate": "0.000125",   # ← Funding rate atual
  "funding_interval": 28800,    # ← Intervalo funding (8h em segundos)
  "maker_fee_rate": "-0.00025", # ← Maker fee (rebate)
  "taker_fee_rate": "0.00075",  # ← Taker fee
  "quanto_multiplier": "0.0001",# ← Multiplicador do contrato
  "mark_type": "index",
  "funding_cap_ratio": "0.003", # ← Funding rate cap
  "market_order_slip_ratio": "0.05" # ← Max slippage para market orders
}
```

**GUI Impact:** Quando o usuário seleciona um contrato, esses dados populam automaticamente os limites na GUI (max leverage, min order size, fees preview).

---

## 3. SPOT TRADING (Trading Desk → Spot Trading)

| Função App | API Endpoint | Método | Parâmetros-Chave |
|-----------|-------------|--------|-----------------|
| Comprar (market order) | `POST /spot/orders` | POST | `side: "buy"`, `type: "market"`, `amount` |
| Comprar (limit order) | `POST /spot/orders` | POST | `side: "buy"`, `type: "limit"`, `price`, `amount` |
| Vender (market order) | `POST /spot/orders` | POST | `side: "sell"`, `type: "market"`, `amount` |
| Vender (limit order) | `POST /spot/orders` | POST | `side: "sell"`, `type: "limit"`, `price`, `amount` |
| Batch orders (até 10) | `POST /spot/batch_orders` | POST | Array de orders |
| Cancelar ordem | `DELETE /spot/orders/{order_id}` | DELETE | `order_id`, `currency_pair` |
| Cancelar todas do par | `DELETE /spot/orders` | DELETE | `currency_pair` |
| Listar ordens abertas | `GET /spot/open_orders` | GET | — |
| Histórico de ordens | `GET /spot/orders` | GET | `currency_pair`, `status` |
| Meus trades | `GET /spot/my_trades` | GET | `currency_pair` |
| Set TP/SL (spot trigger) | `POST /spot/price_orders` | POST | Trigger price + order params |

### Spot Buy Command (Score-Driven)

```python
# Market order — usado quando scanner detecta oportunidade
POST /spot/orders
{
  "currency_pair": "SOL_USDT",
  "side": "buy",
  "type": "market",
  "amount": "100",           # Em USDT (quote currency)
  "time_in_force": "ioc",   # Market order = IOC
  "text": "t-scalpyn-spot"  # Identificador Scalpyn
}

# Limit order — com timeout
POST /spot/orders
{
  "currency_pair": "SOL_USDT",
  "side": "buy",
  "type": "limit",
  "price": "148.50",
  "amount": "0.673",        # Em SOL (base currency)
  "time_in_force": "gtc",
  "text": "t-scalpyn-spot"
}
```

### Spot Sell Command (quando target atingido)

```python
POST /spot/orders
{
  "currency_pair": "SOL_USDT",
  "side": "sell",
  "type": "market",
  "amount": "0.673",        # Quantidade de SOL a vender
  "time_in_force": "ioc",
  "text": "t-scalpyn-sell"
}
```

### Spot Price-Triggered Order (para TP automático)

```python
# Criar trigger de TP no momento da compra
POST /spot/price_orders
{
  "market": "SOL_USDT",
  "trigger": {
    "price": "155.00",        # Preço trigger
    "rule": ">=",             # Quando preço >= trigger
    "expiration": 2592000     # 30 dias em segundos
  },
  "put": {
    "side": "sell",
    "type": "market",
    "amount": "0.673",
    "time_in_force": "ioc",
    "text": "t-scalpyn-tp"
  }
}
```

---

## 4. FUTURES TRADING (Trading Desk → Futures Trading)

### 4A. Position Mode & Leverage Setup

| Função App | API Endpoint | Método | Quando |
|-----------|-------------|--------|--------|
| Set position mode (single/dual/split) | `POST /futures/{settle}/set_position_mode` | POST | Ao conectar exchange |
| Set leverage (isolated) | `POST /futures/{settle}/positions/{contract}/leverage` | POST | Antes de abrir posição |
| Set leverage (cross) | `POST /futures/{settle}/positions/{contract}/leverage` | POST | `leverage=0`, `cross_leverage_limit=N` |
| Set leverage (split mode) | `POST /futures/{settle}/positions/{contract}/set_leverage` | POST | Por modo específico |
| Switch margin mode | `POST /futures/{settle}/positions/{contract}/cross_mode` | POST | Isolated ↔ Cross |
| Get leverage info | `GET /futures/{settle}/get_leverage/{contract}` | GET | Verificar antes de operar |
| Update risk limit | `POST /futures/{settle}/positions/{contract}/risk_limit` | POST | Ajustar max leverage |

```python
# ═══ DEFINIR POSITION MODE ═══
# Recomendado: "single" para Scalpyn (simples)
POST /futures/usdt/set_position_mode
{
  "position_mode": "single"    # "single", "dual", "dual_plus"
}

# ═══ DEFINIR LEVERAGE (ISOLATED MARGIN) ═══
# Chamado antes de cada trade, com leverage calculada
POST /futures/usdt/positions/BTC_USDT/leverage?leverage=5

# Parâmetros:
#   leverage: "5"  (1-200, depende do contrato)
#   Para isolated: leverage > 0
#   Para cross: leverage = 0, cross_leverage_limit = N

# ═══ DEFINIR LEVERAGE (CROSS MARGIN) ═══
POST /futures/usdt/positions/BTC_USDT/leverage?leverage=0&cross_leverage_limit=10

# ═══ SWITCH ENTRE ISOLATED E CROSS ═══
POST /futures/usdt/positions/BTC_USDT/cross_mode
{
  "pos_margin_mode": "isolated"   # ou "cross"
}
```

**GUI Mapping:**

```
Trading Desk → Futures → Leverage & Anti-Liquidation

  Margin Mode     ● Isolated  ○ Cross
  Position Mode   ● Single    ○ Dual    ○ Split

  LEVERAGE (calculated, not chosen):
  O slider mostra o range 1x—{leverage_max do contrato}
  Mas o valor é CALCULADO pelo risk engine
  User só vê o resultado: "Calculated leverage: 5.2x"

  API calls sequence:
  1. GET contract info → obter leverage_max
  2. Risk engine calcula leverage necessária
  3. POST set leverage com valor calculado
  4. POST order
```

### 4B. Abrir Posição (Futures Order)

| Função App | API Endpoint | Método | Parâmetros-Chave |
|-----------|-------------|--------|-----------------|
| Abrir LONG | `POST /futures/{settle}/orders` | POST | `size: +N` (positivo = buy/long) |
| Abrir SHORT | `POST /futures/{settle}/orders` | POST | `size: -N` (negativo = sell/short) |
| Market order | `POST /futures/{settle}/orders` | POST | `price: "0"`, `tif: "ioc"` |
| Limit order | `POST /futures/{settle}/orders` | POST | `price: "67500"`, `tif: "gtc"` |
| Reduce-only (fechar posição) | `POST /futures/{settle}/orders` | POST | `is_reduce_only: true` |
| Close position | `POST /futures/{settle}/orders` | POST | `is_close: true`, `size: 0` |
| Batch orders (até 10) | `POST /futures/{settle}/batch_orders` | POST | Array |

```python
# ═══ ABRIR LONG (Market Order) ═══
POST /futures/usdt/orders
{
  "contract": "BTC_USDT",
  "size": "10",              # Positivo = LONG (buy)
  "price": "0",              # 0 = market order
  "tif": "ioc",              # IOC para market
  "text": "t-scalpyn-long",
  "is_reduce_only": false,
  "is_close": false
}

# ═══ ABRIR SHORT (Market Order) ═══
POST /futures/usdt/orders
{
  "contract": "SOL_USDT",
  "size": "-50",             # Negativo = SHORT (sell)
  "price": "0",
  "tif": "ioc",
  "text": "t-scalpyn-short"
}

# ═══ ABRIR LONG (Limit Order) ═══
POST /futures/usdt/orders
{
  "contract": "ETH_USDT",
  "size": "5",
  "price": "3280.50",       # Preço limit
  "tif": "gtc",             # Good till cancel
  "text": "t-scalpyn-entry"
}

# ═══ FECHAR POSIÇÃO INTEIRA ═══
POST /futures/usdt/orders
{
  "contract": "BTC_USDT",
  "size": "0",               # 0 = fechar tudo
  "price": "0",
  "tif": "ioc",
  "is_close": true,          # Flag de close
  "text": "t-scalpyn-close"
}

# ═══ FECHAR PARCIAL (TP1: 35% da posição) ═══
POST /futures/usdt/orders
{
  "contract": "BTC_USDT",
  "size": "-4",              # Negativo para fechar long parcial
  "price": "0",
  "tif": "ioc",
  "is_reduce_only": true,    # Só reduz, não inverte
  "text": "t-scalpyn-tp1"
}
```

### 4C. Stop Loss & Take Profit (Price-Triggered Orders)

| Função App | API Endpoint | Método | Tipo |
|-----------|-------------|--------|------|
| Criar SL | `POST /futures/{settle}/price_orders` | POST | Trigger → market sell |
| Criar TP | `POST /futures/{settle}/price_orders` | POST | Trigger → market sell |
| Criar TP+SL juntos | 2× `POST /futures/{settle}/price_orders` | POST | Uma para cada |
| Listar ordens trigger | `GET /futures/{settle}/price_orders` | GET | Status "open" |
| Cancelar trigger | `DELETE /futures/{settle}/price_orders/{order_id}` | DELETE | — |
| Modificar trigger | `PUT /futures/{settle}/price_orders/amend/{order_id}` | PUT | Update price/size |
| Cancelar todas triggers | `DELETE /futures/{settle}/price_orders` | DELETE | Por contrato |

```python
# ═══ STOP LOSS (para posição LONG) ═══
POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "BTC_USDT",
    "size": "0",             # 0 = fechar posição inteira
    "price": "0",            # 0 = market order quando triggar
    "tif": "ioc",
    "is_close": true,
    "text": "t-scalpyn-sl"
  },
  "trigger": {
    "strategy_type": 0,      # 0 = by mark price / last price
    "price_type": 0,         # 0 = last price, 1 = mark price
    "price": "64500.00",     # Preço de trigger do SL
    "rule": 2,               # 1 = price >= trigger, 2 = price <= trigger
    "expiration": 604800     # 7 dias em segundos (0 = sem expiração)
  }
}

# ═══ TAKE PROFIT 1 — Parcial (para posição LONG) ═══
POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "BTC_USDT",
    "size": "-4",            # Fechar 4 contracts (parcial)
    "price": "0",
    "tif": "ioc",
    "is_reduce_only": true,
    "text": "t-scalpyn-tp1"
  },
  "trigger": {
    "strategy_type": 0,
    "price_type": 0,
    "price": "69000.00",     # TP1 price
    "rule": 1,               # >= trigger (preço subiu)
    "expiration": 604800
  }
}

# ═══ TAKE PROFIT 2 — Parcial (para posição LONG) ═══
POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "BTC_USDT",
    "size": "-3",
    "price": "0",
    "tif": "ioc",
    "is_reduce_only": true,
    "text": "t-scalpyn-tp2"
  },
  "trigger": {
    "strategy_type": 0,
    "price_type": 0,
    "price": "71500.00",
    "rule": 1,
    "expiration": 604800
  }
}

# ═══ STOP LOSS (para posição SHORT) ═══
POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "SOL_USDT",
    "size": "0",
    "price": "0",
    "tif": "ioc",
    "is_close": true,
    "text": "t-scalpyn-sl-short"
  },
  "trigger": {
    "strategy_type": 0,
    "price_type": 1,         # Mark price (mais seguro contra manipulação)
    "price": "162.00",       # SL acima do entry (para short)
    "rule": 1,               # >= trigger (preço subiu = loss para short)
    "expiration": 604800
  }
}

# ═══ MODIFICAR TRIGGER ORDER (mover SL para breakeven) ═══
PUT /futures/usdt/price_orders/amend/{order_id}
{
  "order_id": 12345,
  "trigger_price": "67800.00",    # Novo preço (breakeven)
  "price": "0",                   # Manter market order
  "size": "0"                     # Manter close all
}
```

### 4D. Trailing Stop Orders

| Função App | API Endpoint | Método |
|-----------|-------------|--------|
| Criar trail order | `POST /futures/{settle}/autoorder/v1/trail/create` | POST |
| Listar trail orders | `GET /futures/{settle}/autoorder/v1/trail/list` | GET |
| Detalhes trail order | `GET /futures/{settle}/autoorder/v1/trail/detail` | GET |
| Atualizar trail order | `POST /futures/{settle}/autoorder/v1/trail/update` | POST |
| Parar trail order | `POST /futures/{settle}/autoorder/v1/trail/stop` | POST |
| Parar todas trail | `POST /futures/{settle}/autoorder/v1/trail/stop_all` | POST |
| Histórico mudanças | `GET /futures/{settle}/autoorder/v1/trail/change_log` | GET |

**Nota:** A Gate.io suporta trailing stop orders nativo via API. O Scalpyn pode usar isso OU implementar o trailing internamente (mais controle). Recomendação: **usar o trailing da Gate como backup, mas gerenciar internamente** para o ATR-based trailing dinâmico que a Gate não suporta.

### 4E. Position Management

| Função App | API Endpoint | Método | Quando |
|-----------|-------------|--------|--------|
| Listar posições abertas | `GET /futures/{settle}/positions` | GET | Dashboard, Positions page |
| Posição específica | `GET /futures/{settle}/positions/{contract}` | GET | Detalhe da posição |
| Posições históricas | `GET /futures/{settle}/position_close` | GET | Trade History |
| Atualizar margem | `POST /futures/{settle}/positions/{contract}/margin` | POST | Add/remove margin |
| Atualizar leverage | `POST /futures/{settle}/positions/{contract}/leverage` | POST | Rebalancear |
| Histórico de liquidações | `GET /futures/{settle}/liquidates` | GET | Post-mortem, analytics |
| Histórico trades pessoais | `GET /futures/{settle}/my_trades` | GET | Trade History |
| Account book (ledger) | `GET /futures/{settle}/account_book` | GET | P&L detalhado, funding costs |

```python
# ═══ POSIÇÃO RESPONSE (dados para GUI) ═══
GET /futures/usdt/positions/BTC_USDT

{
  "contract": "BTC_USDT",
  "size": "10",                   # Contracts abertos (+ long, - short)
  "leverage": "5",                # Leverage atual (0 = cross)
  "cross_leverage_limit": "0",    # Cross leverage (se cross mode)
  "entry_price": "67543.21",      # Preço médio de entrada
  "mark_price": "68102.55",       # Mark price atual
  "liq_price": "61234.56",        # ← PREÇO DE LIQUIDAÇÃO (CRÍTICO)
  "margin": "1350.86",            # Margem alocada
  "value": "6810.25",             # Valor da posição
  "unrealised_pnl": "55.93",      # P&L não realizado
  "realised_pnl": "-2.15",        # P&L realizado (fees, funding)
  "leverage_max": "100",          # Max leverage para este risk level
  "maintenance_rate": "0.005",    # Taxa de manutenção
  "risk_limit": "100",            # Risk limit atual
  "adl_ranking": 3,               # Auto-deleverage ranking (1-5)
  "pending_orders": 2             # Ordens pendentes
}
```

---

## 5. SEQUÊNCIA COMPLETA: Trade Lifecycle (Futures)

```python
# ═══════════════════════════════════════════════════════
# LIFECYCLE DE UM TRADE FUTURES NO SCALPYN
# ═══════════════════════════════════════════════════════

# STEP 1: Verificar saldo e margem disponível
balance = GET /futures/usdt/accounts
available_margin = balance["available"]

# STEP 2: Obter info do contrato
contract = GET /futures/usdt/contracts/BTC_USDT
max_leverage = contract["leverage_max"]   # ex: 100
min_size = contract["order_size_min"]     # ex: 1
funding_rate = contract["funding_rate"]   # ex: 0.000125

# STEP 3: Risk engine calcula (internamente no Scalpyn)
entry_price = 67500
stop_loss = 66000        # Structure-based
risk_dollars = capital * 0.01  # 1% risk
stop_distance = entry_price - stop_loss  # 1500
position_contracts = risk_dollars / (stop_distance * quanto_multiplier)
position_value = position_contracts * entry_price * quanto_multiplier
required_leverage = position_value / available_margin_for_trade

# STEP 4: Validar anti-liquidação
liq_distance = abs(stop_loss - estimated_liq_price) / entry_price
if liq_distance < min_stop_to_liq_distance:
    # Reduzir leverage e recalcular
    ...

# STEP 5: Set leverage
POST /futures/usdt/positions/BTC_USDT/leverage?leverage={calculated}

# STEP 6: Abrir posição
order = POST /futures/usdt/orders
{
  "contract": "BTC_USDT",
  "size": str(position_contracts),  # Positivo = LONG
  "price": "0",                     # Market
  "tif": "ioc",
  "text": "t-scalpyn-{position_id}"
}

# STEP 7: Verificar fill e obter entry real
filled_order = GET /futures/usdt/orders/{order.id}
actual_entry = filled_order["fill_price"]

# STEP 8: Colocar Stop Loss na exchange
sl_order = POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "BTC_USDT",
    "size": "0", "price": "0", "tif": "ioc",
    "is_close": true, "text": "t-scalpyn-sl"
  },
  "trigger": {
    "strategy_type": 0, "price_type": 1,  # Mark price
    "price": str(stop_loss),
    "rule": 2,  # <= (preço cai para SL)
    "expiration": 604800
  }
}

# STEP 9: Colocar TP1 na exchange
tp1_order = POST /futures/usdt/price_orders
{
  "initial": {
    "contract": "BTC_USDT",
    "size": str(-tp1_contracts),  # Parcial close
    "price": "0", "tif": "ioc",
    "is_reduce_only": true, "text": "t-scalpyn-tp1"
  },
  "trigger": {
    "strategy_type": 0, "price_type": 0,
    "price": str(tp1_price),
    "rule": 1,  # >= (preço sobe para TP)
    "expiration": 604800
  }
}

# STEP 10: Monitorar via WebSocket
# Subscribe: futures.positions, futures.orders, futures.autoorders

# STEP 11: Quando TP1 hit → mover SL para breakeven
PUT /futures/usdt/price_orders/amend/{sl_order.id}
{
  "trigger_price": str(actual_entry + safety),  # BE + margem
  "order_id": sl_order.id
}

# STEP 12: Colocar TP2 + ativar trailing interno
# (trailing ATR-based gerenciado pelo Scalpyn, não pela exchange)

# STEP 13: Quando trailing stop atingido → close restante
POST /futures/usdt/orders
{
  "contract": "BTC_USDT",
  "size": "0", "price": "0", "tif": "ioc",
  "is_close": true, "text": "t-scalpyn-trail"
}

# STEP 14: Cancelar price orders restantes
DELETE /futures/usdt/price_orders?contract=BTC_USDT

# STEP 15: Registrar no DB
# P&L, funding costs (do account_book), duração, etc.
```

---

## 6. WEBSOCKET CHANNELS (Real-time Monitoring)

| Channel | Uso no App | Autenticação |
|---------|-----------|-------------|
| `futures.tickers` | Preços live no scanner/watchlist | Não |
| `futures.candlesticks` | Feature Engine (indicadores) | Não |
| `futures.order_book` | L1 Liquidity (depth live) | Não |
| `futures.trades` | L5 Order Flow (taker ratio live) | Não |
| `futures.positions` | Position monitor (P&L live, liq_price) | Sim |
| `futures.orders` | Order fill notifications | Sim |
| `futures.autoorders` | TP/SL trigger notifications | Sim |
| `futures.liquidates` | Liquidation alerts | Sim |
| `futures.reduce_risk_limits` | Risk limit changes | Sim |
| `spot.tickers` | Preços spot live | Não |
| `spot.candlesticks` | Indicadores spot | Não |
| `spot.orders` | Spot order fill notifications | Sim |

---

## 7. GUI: Controles que Mapeiam para API

### Seção: Leverage & Anti-Liquidation

```
┌─ Leverage & Anti-Liquidation ───────────────────────────┐
│                                                          │
│  Margin Mode        ● Isolated  ○ Cross                 │
│  → API: POST /futures/{s}/positions/{c}/cross_mode      │
│                                                          │
│  Position Mode      ● Single  ○ Dual                    │
│  → API: POST /futures/{s}/set_position_mode             │
│                                                          │
│  LEVERAGE CAPS (por score tier):                         │
│  90+ Institutional   [=====●=======] 10x                │
│  80-89 Strong        [====●========] 7x                 │
│  70-79 Valid         [==●==========] 4x                 │
│  Risk-Off regime     [=●===========] 3x                 │
│  → NOTA: o valor final é CALCULADO. Estes são MÁXIMOS.  │
│  → API: POST /futures/{s}/positions/{c}/leverage         │
│                                                          │
│  Trigger Price Type  ○ Last Price  ● Mark Price         │
│  → API: price_type em price_orders (0=last, 1=mark)     │
│                                                          │
│  EXCHANGE LIMITS (read-only, from contract info):        │
│  Max leverage (BTC_USDT): 100x                           │
│  Min order size: 1 contract                              │
│  Maintenance rate: 0.5%                                  │
│  → API: GET /futures/{s}/contracts/{c}                   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

### Seção: Stop Loss & Take Profit

```
┌─ Stop Loss & Take Profit ───────────────────────────────┐
│                                                          │
│  SL Method:  Structure → Liquidity → ATR (calculated)   │
│  → Scalpyn calcula, não input do user                    │
│  → API: POST /futures/{s}/price_orders (trigger rule=2)  │
│                                                          │
│  SL Price Type      ○ Last Price  ● Mark Price          │
│  → API: trigger.price_type = 1 (mark price recomendado) │
│                                                          │
│  TP1 R:R            [=====●=======] 1.5x                │
│  TP1 Close %        [=======●=====] 35%                 │
│  → API: POST /futures/{s}/price_orders (trigger rule=1)  │
│  → size = -(position_size × tp1_close_pct)              │
│                                                          │
│  TP2 R:R            [========●====] 2.5x                │
│  TP2 Close %        [=========●===] 50%                 │
│  → API: POST /futures/{s}/price_orders                   │
│                                                          │
│  TP3 Method         ○ Limit  ● Trailing (internal)      │
│  → Se trailing: gerenciado pelo Scalpyn, não pela Gate   │
│  → Close via POST /futures/{s}/orders (market, reduce)   │
│                                                          │
│  Move SL to BE at   ● TP1  ○ TP2  ○ Never              │
│  → API: PUT /futures/{s}/price_orders/amend/{id}         │
│  → Atualiza trigger_price do SL existente               │
│                                                          │
│  SL Expiration      [  7 dias  ] (604800s)              │
│  → API: trigger.expiration                               │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

---

## 8. RATE LIMITS — Relevantes

| Tipo | Limite | Nota |
|------|--------|------|
| Futures orders | 200 req/s (com preenchimento) | Rate limit por fill ratio |
| Futures read (positions, orders) | 200 req/s | Standard |
| Price orders (trigger) | 100 req/s | Menos agressivo |
| Spot orders | 200 req/s | Standard |
| Spot read | 400 req/s | Mais generoso |
| WebSocket messages | Sem limite de envio | Throttled pelo servidor |
| Batch orders | 10 orders por request | 1 request = 1 rate limit hit |

**Recomendação para Scalpyn:**
- Usar batch orders quando possível (10 ordens = 1 hit de rate limit)
- Scanner: throttle a 1 request/segundo por endpoint
- WebSocket para dados real-time (sem rate limit para reads)
- Price orders: máximo ~60-80 por minuto para ser safe

---

## 9. ERROR HANDLING

```python
# Erros comuns e como tratar

errors_map = {
    "INVALID_KEY":          "API key inválida → checar Exchange Card",
    "POSITION_NOT_FOUND":   "Posição não existe → já foi fechada?",
    "ORDER_NOT_FOUND":      "Ordem não encontrada → já cancelada?",
    "BALANCE_NOT_ENOUGH":   "Saldo insuficiente → bloquear trade",
    "TOO_MANY_ORDERS":      "Limite de ordens → cancelar antigas",
    "RISK_LIMIT_EXCEEDED":  "Risk limit → reduzir size ou leverage",
    "LEVERAGE_TOO_HIGH":    "Leverage > max permitido → reduzir",
    "ORDER_SIZE_TOO_SMALL": "Size < min → ajustar para min",
    "ORDER_SIZE_TOO_LARGE": "Size > max → dividir em múltiplas",
    "INVALID_PRICE":        "Preço fora do range permitido",
    "FUTURES_ACCOUNT_NOT_FOUND": "Conta futures não ativada"
}
```

---

## 10. CONFIG SCALPYN: Exchange Adapter (Gate.io)

```json
{
  "config_type": "exchange_gate",
  "config_json": {
    "api_base_url": "https://api.gateio.ws/api/v4",
    "futures_base_url": "https://fx-api.gateio.ws/api/v4",
    "ws_url": "wss://fx-ws.gateio.ws/v4/ws/usdt",
    "settle": "usdt",
    "position_mode": "single",
    "default_margin_mode": "isolated",
    "default_tif": "gtc",
    "trigger_price_type": 1,
    "trigger_expiration_seconds": 604800,
    "use_exchange_trailing": false,
    "batch_order_max": 10,
    "scan_throttle_ms": 1000,
    "order_tag_prefix": "t-scalpyn",
    "ws_channels": {
      "tickers": true,
      "candlesticks": true,
      "order_book": true,
      "trades": true,
      "positions": true,
      "orders": true,
      "autoorders": true,
      "liquidates": true
    }
  }
}
```

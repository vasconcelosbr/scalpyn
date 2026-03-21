# Scalpyn — Trading Desk: Estrutura de Navegação e Páginas

## Nova Sidebar — Navegação Completa

```
OVERVIEW
  ◻ Dashboard              (icon: LayoutDashboard)    /
  ◻ Watchlist              (icon: Eye)                /watchlist

TRADING DESK                                          ← NOVA SEÇÃO
  ◻ Spot Trading           (icon: Wallet)             /trading-desk/spot
  ◻ Futures Trading        (icon: TrendingUp)         /trading-desk/futures
  ◻ Positions              (icon: BarChart3)           /trading-desk/positions
  ◻ Trade History          (icon: History)             /trading-desk/history

ANALYTICS
  ◻ Reports                (icon: FileText)           /reports
  ◻ Performance            (icon: LineChart)           /analytics

CONFIGURATION
  ◻ General                (icon: Settings)           /settings/general
  ◻ Indicators             (icon: Activity)           /settings/indicators
  ◻ Score Engine           (icon: Target)             /settings/score
  ◻ Signal Rules           (icon: Zap)                /settings/signals
  ◻ Block Rules            (icon: ShieldOff)          /settings/blocks
  ◻ Risk Management        (icon: Shield)             /settings/risk
  ◻ Strategies             (icon: Brain)              /settings/strategies
  ◻ Exchanges              (icon: Repeat)             /settings/exchanges
  ◻ Notifications          (icon: Bell)               /settings/notifications
```

### Mudanças vs. Estrutura Anterior

```diff
  OVERVIEW
    Dashboard
    Watchlist

- TRADING
-   Trades & P&L
-   Reports
-   Pools
+ TRADING DESK
+   Spot Trading          ← NOVO (config + controle do engine spot)
+   Futures Trading       ← NOVO (config + controle do engine futures)
+   Positions             ← Refatorado (antes era tab dentro de Trades)
+   Trade History         ← Refatorado

+ ANALYTICS
+   Reports               ← Movido para grupo próprio
+   Performance           ← Antes era "Analytics"

  CONFIGURATION
    (mantém tudo igual — settings são compartilhados)

- Pools                   ← REMOVIDO (sem grid = sem pools)
```

**Por que remover Pools?** O modelo antigo de pools era atrelado a grids (grupo de moedas com config própria). No modelo score-driven sem grid, cada trade é independente. A configuração por perfil (spot/futures) substitui pools. Se no futuro quiser agrupamentos, podem ser reintroduzidos como "Portfolios" ou "Strategies Groups" — mas com semântica diferente.

---

## Rotas — Directory Structure

```
frontend/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                          # Dashboard
│   ├── login/page.tsx
│   ├── register/page.tsx
│   ├── watchlist/page.tsx
│   │
│   ├── trading-desk/                     ← NOVA ÁREA
│   │   ├── layout.tsx                    # Layout compartilhado do Trading Desk
│   │   ├── spot/
│   │   │   ├── page.tsx                  # Spot Trading (config + engine control)
│   │   │   └── components/
│   │   │       ├── SpotEngineStatus.tsx
│   │   │       ├── SpotBuyingConfig.tsx
│   │   │       ├── SpotSellingConfig.tsx
│   │   │       ├── SpotHoldingConfig.tsx
│   │   │       ├── SpotDCAConfig.tsx
│   │   │       ├── SpotMacroFilter.tsx
│   │   │       └── SpotScannerControl.tsx
│   │   ├── futures/
│   │   │   ├── page.tsx                  # Futures Trading (config + engine control)
│   │   │   └── components/
│   │   │       ├── FuturesEngineStatus.tsx
│   │   │       ├── FuturesScoringConfig.tsx
│   │   │       ├── FuturesLeverageConfig.tsx
│   │   │       ├── FuturesAntiLiqConfig.tsx
│   │   │       ├── FuturesSizingConfig.tsx
│   │   │       ├── FuturesTakeProfitConfig.tsx
│   │   │       ├── FuturesTrailingConfig.tsx
│   │   │       ├── FuturesFundingGuard.tsx
│   │   │       ├── FuturesEmergencyConfig.tsx
│   │   │       ├── FuturesMacroGate.tsx
│   │   │       ├── FuturesLossLimits.tsx
│   │   │       └── FuturesScannerControl.tsx
│   │   ├── positions/
│   │   │   ├── page.tsx                  # All open positions (spot + futures)
│   │   │   └── components/
│   │   │       ├── PositionsTable.tsx
│   │   │       ├── SpotPositionRow.tsx
│   │   │       ├── FuturesPositionRow.tsx
│   │   │       ├── UnderwaterPanel.tsx
│   │   │       ├── LiquidationMonitor.tsx
│   │   │       └── CapitalAllocation.tsx
│   │   └── history/
│   │       ├── page.tsx                  # Trade history (spot + futures)
│   │       └── components/
│   │           ├── TradeHistoryTable.tsx
│   │           ├── TradeDetailModal.tsx
│   │           └── PnLSummary.tsx
│   │
│   ├── reports/page.tsx
│   ├── analytics/page.tsx
│   │
│   ├── settings/
│   │   ├── general/page.tsx
│   │   ├── indicators/page.tsx
│   │   ├── score/page.tsx
│   │   ├── signals/page.tsx
│   │   ├── blocks/page.tsx
│   │   ├── risk/page.tsx
│   │   ├── strategies/page.tsx
│   │   ├── exchanges/page.tsx
│   │   ├── notifications/page.tsx
│   │   └── users/page.tsx
│   │
│   └── api/
```

---

## Especificação de Páginas — Trading Desk

### Spot Trading (`/trading-desk/spot`)

A página central de configuração e controle do engine spot. Tudo que configura como o bot compra, vende e gerencia posições spot.

```
┌──────────────────────────────────────────────────────────────────┐
│  SPOT TRADING                                                    │
│                                                                  │
│  ┌─ Engine Status Bar ─────────────────────────────────────────┐ │
│  │  ● RUNNING    Scanner: 30s cycle    Mode: LIVE              │ │
│  │  Positions: 12 active, 3 underwater                         │ │
│  │  Capital: $8,200 free / $12,000 total (68% deployed)        │ │
│  │  [  ⏸ Pause Engine  ]  [  📊 View Positions  ]             │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Scanner & Buying ──────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Scan Interval         [====●========] 30s                  │ │
│  │  Buy Score Threshold   [========●====] 75                   │ │
│  │  Strong Buy Score      [=========●===] 85                   │ │
│  │  Max Opps per Scan     [==●==========] 3                    │ │
│  │                                                              │ │
│  │  CAPITAL ALLOCATION                                          │ │
│  │  Per Trade             [======●======] 10%  ($1,200)        │ │
│  │  Min per Trade         [  $20   ]                           │ │
│  │  Max per Trade         [  $500  ]                           │ │
│  │  Capital Reserve       [===●=========] 10%  ($1,200)        │ │
│  │  Max Capital in Use    [==========●==] 80%  ($9,600)        │ │
│  │                                                              │ │
│  │  POSITION LIMITS                                             │ │
│  │  Max Positions Total   [  20  ]                             │ │
│  │  Max per Asset         [   5  ]                             │ │
│  │  Max Exposure/Asset    [======●======] 25%                  │ │
│  │                                                              │ │
│  │  Order Type  ○ Market  ● Limit (timeout: 120s)              │ │
│  │  Max Slippage          [===●=========] 0.15%                │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Sell Rules ────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  ██ CORE RULE: Never sell at loss ██    ☑ Enabled (locked)  │ │
│  │                                                              │ │
│  │  Take Profit Target    [=====●=======] 1.5%                 │ │
│  │  Min Profit to Sell    [==●==========] 0.5%                 │ │
│  │  Safety Margin         [=●===========] 0.3%                 │ │
│  │                                                              │ │
│  │  SELL FLOW LAYERS                                            │ │
│  │  ☑ Ranging Detection (lateralizado → vende se lucro ≥ TP)  │ │
│  │  ☑ Exhaustion Detection (tendência morrendo)                │ │
│  │  ☑ AI Opportunity (consulta IA para EXTEND/SELL)            │ │
│  │  ☑ Target Hit (lucro ≥ take profit)                         │ │
│  │  ☑ AI Trailing (HWM trailing stop após AI Hold)            │ │
│  │                                                              │ │
│  │  AI MODEL              [ google/gemini-2.5-flash     ▼ ]   │ │
│  │  AI Rate Limit         [  60s  ] per position               │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Holding & Recovery ────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Underwater positions are held indefinitely until             │ │
│  │  min profit target is reached. No exceptions.                │ │
│  │                                                              │ │
│  │  Alert after underwater    [  24h  ]                        │ │
│  │  Repeat alert every        [  12h  ]                        │ │
│  │  ☑ Show opportunity cost estimate                           │ │
│  │  ☑ Show recovery % needed                                   │ │
│  │                                                              │ │
│  │  DCA (Dollar Cost Average)                                   │ │
│  │  ☐ Enable DCA ──────────────────────────────────            │ │
│  │  │  Trigger after drop      [=====●=====] 5.0%             │ │
│  │  │  Min score for DCA       [=======●===] 70               │ │
│  │  │  Max DCA layers          [  3  ]                         │ │
│  │  │  Base amount             [  $50  ]                       │ │
│  │  │  Decay factor            [=======●===] 0.7              │ │
│  │  │  Max total exposure      [=======●===] 30%              │ │
│  │  │  ☑ Require macro ≠ risk_off                             │ │
│  │  └──────────────────────────────────────────────            │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Macro Filter ──────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  ☑ Enable Macro Filter                                      │ │
│  │  ☑ Block buys on Strong Risk-Off                            │ │
│  │  Reduce buys on Risk-Off    [=======●===] 50%              │ │
│  │  ☑ BTC Correlation Guard                                    │ │
│  │  BTC dump threshold (1h)    [======●====] -3.0%            │ │
│  │  BTC guard action           ○ Reduce targets  ● Alert only  │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Trailing Stop Config ──────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Method  ○ Fixed margins  ● ATR-based                       │ │
│  │  ATR Period               [  14  ]                          │ │
│  │  ATR Multiplier           [=====●=======] 1.0x             │ │
│  │  Margin Floor             [==●==========] 0.4%             │ │
│  │  Margin Ceiling           [=========●===] 2.0%             │ │
│  │  Tighten above profit     [=======●=====] 5.0%             │ │
│  │  Tighten factor           [=======●=====] 0.7              │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  [ Reset to Defaults ]                          [ Save Changes ] │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Futures Trading (`/trading-desk/futures`)

Mesma estrutura de page, mas com seções específicas de futuros.

```
┌──────────────────────────────────────────────────────────────────┐
│  FUTURES TRADING                                                 │
│                                                                  │
│  ┌─ Engine Status Bar ─────────────────────────────────────────┐ │
│  │  ● RUNNING    Scanner: 30s    Mode: LIVE    Macro: RISK_ON  │ │
│  │  Positions: 3 open (2L 1S)    P&L today: +$340 (+1.4%)     │ │
│  │  Margin: $4,200 used / $10,000 total (42%)                  │ │
│  │  Daily loss: $0 / $300 limit (0%)                           │ │
│  │  [  ⏸ Pause Engine  ]  [  📊 View Positions  ]             │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Scanner & 5-Layer Scoring ─────────────────────────────────┐ │
│  │                                                              │ │
│  │  Scan Interval         [====●========] 30s                  │ │
│  │  Min Total Score       [========●====] 70                   │ │
│  │  Min Layer Score       [==●==========] 8                    │ │
│  │  L1 Hard Reject Below  [==●==========] 10                   │ │
│  │  Max Opps per Scan     [=●===========] 2                    │ │
│  │                                                              │ │
│  │  DIRECTION                                                   │ │
│  │  ☑ Allow Long    ☑ Allow Short                              │ │
│  │  Direction source  [ L2 Market Structure  ▼ ]               │ │
│  │  ☑ Macro overrides direction                                │ │
│  │  ☐ Allow hedge (long+short same asset)                      │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Position Sizing ───────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Method: Risk-Based (size = risk$ / stop distance)          │ │
│  │                                                              │ │
│  │  Risk per Trade (base)  [===●=========] 1.0%  ($100)       │ │
│  │  Risk (conviction 90+)  [=====●=======] 2.0%  ($200)       │ │
│  │                                                              │ │
│  │  SCORE → SIZE MULTIPLIER                                     │ │
│  │  ┌────────────────┬────────────┐                            │ │
│  │  │ 90+ (Instit.)  │  × 1.5     │                           │ │
│  │  │ 80-89 (Strong) │  × 1.0     │                           │ │
│  │  │ 70-79 (Valid)  │  × 0.6     │                           │ │
│  │  └────────────────┴────────────┘                            │ │
│  │                                                              │ │
│  │  Max Capital Deployed  [========●====] 60%                  │ │
│  │  Max Positions Total   [  5  ]                              │ │
│  │  Max per Asset         [  2  ]                              │ │
│  │  Max Correlated        [  2  ] (threshold: 0.7)            │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Leverage & Anti-Liquidation ───────────────────────────────┐ │
│  │                                                              │ │
│  │  ██ LEVERAGE IS CALCULATED, NEVER CHOSEN ██                 │ │
│  │  (leverage = position value / allocated margin)              │ │
│  │                                                              │ │
│  │  MAX LEVERAGE CAPS                                           │ │
│  │  ┌────────────────┬────────────┐                            │ │
│  │  │ 90+ (Instit.)  │  10x max   │                           │ │
│  │  │ 80-89 (Strong) │   7x max   │                           │ │
│  │  │ 70-79 (Valid)  │   4x max   │                           │ │
│  │  │ Risk-Off macro │   3x max   │                           │ │
│  │  └────────────────┴────────────┘                            │ │
│  │                                                              │ │
│  │  ANTI-LIQUIDATION PROTECTION                                │ │
│  │  ┌──────────────────────────────────────────────────────┐   │ │
│  │  │                                                      │   │ │
│  │  │  ─── TP Zone ──────────────────── (+10%)            │   │ │
│  │  │  ─── Entry ────────────────────── ($100)            │   │ │
│  │  │  ─── Stop Loss ───────────────── (-3%)              │   │ │
│  │  │  ░░░ Buffer Zone ░░░░░░░░░░░░░░░                    │   │ │
│  │  │  ─── Alert Zone ──────────────── (-8%)   ⚠         │   │ │
│  │  │  ─── Critical Zone ───────────── (-5%)   🔴        │   │ │
│  │  │  ─── LIQUIDATION ─────────────── (-10%)  ☠         │   │ │
│  │  │                                                      │   │ │
│  │  │  Min Stop↔Liq distance:    3.0%                     │   │ │
│  │  │  Safety buffer:            3.0%                      │   │ │
│  │  │  If math doesn't fit → NO TRADE                      │   │ │
│  │  │                                                      │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  │                                                              │ │
│  │  Min Stop-to-Liq distance  [======●=====] 3.0%             │ │
│  │  Liq Safety Buffer         [======●=====] 3.0%             │ │
│  │  Alert Zone distance       [=========●==] 8.0%             │ │
│  │  Critical Zone distance    [=======●====] 5.0%             │ │
│  │  Force close at            [======●=====] 3.0%             │ │
│  │  ☑ Force close on critical                                  │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Stop Loss & Take Profit ───────────────────────────────────┐ │
│  │                                                              │ │
│  │  STOP LOSS                                                   │ │
│  │  Method priority   [ Structure → Liquidity → ATR  ▼ ]      │ │
│  │  ATR multiplier       [=====●=======] 1.5x                 │ │
│  │  Max stop distance    [=========●===] 5.0%                  │ │
│  │  Min stop distance    [●============] 0.3%                  │ │
│  │  Move to breakeven at  ○ TP1 ● TP2  ○ Never                │ │
│  │                                                              │ │
│  │  TAKE PROFIT                                                 │ │
│  │  TP1 (R:R)            [=====●=======] 1.5x                 │ │
│  │  TP1 close %          [=======●=====] 35%                  │ │
│  │  TP2 (R:R)            [========●====] 2.5x                 │ │
│  │  TP2 close %          [=========●===] 50%                  │ │
│  │  TP3 (R:R)            [==========●==] 4.0x                 │ │
│  │  TP3 method            ○ Limit  ● Trailing                  │ │
│  │                                                              │ │
│  │  VOLATILITY ADJUSTMENT                                       │ │
│  │  Squeeze TP multiplier  [========●===] 1.3x                 │ │
│  │  Expanding TP multiplier [======●====] 0.85x                │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Trailing Stop ─────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Activate after         ○ TP1  ● TP2  ○ TP3                │ │
│  │  Method                 ○ Fixed  ● ATR-based                │ │
│  │  ATR Multiplier         [=====●=======] 1.0x               │ │
│  │  Floor                  ● Breakeven  ○ Entry  ○ Custom %   │ │
│  │  Tighten above profit   [========●====] 5.0%               │ │
│  │  Tighten factor         [=======●=====] 0.7                │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Guards (Funding, OI, Emergency) ───────────────────────────┐ │
│  │                                                              │ │
│  │  FUNDING RATE GUARD                                          │ │
│  │  ☑ Enabled                                                  │ │
│  │  Max funding for Long   [======●=====] 0.03                 │ │
│  │  Min funding for Short  [======●=====] -0.03                │ │
│  │  Extreme funding        [========●===] 0.05                 │ │
│  │  Size reduction %       [======●=====] 30%                  │ │
│  │  Max drain % of profit  [======●=====] 25%                  │ │
│  │                                                              │ │
│  │  OPEN INTEREST GUARD                                         │ │
│  │  ☑ Enabled                                                  │ │
│  │  Extreme OI percentile  [==========●=] 95th                 │ │
│  │  Size reduction %       [======●=====] 30%                  │ │
│  │  Stop tighten %         [=====●======] 20%                  │ │
│  │                                                              │ │
│  │  EMERGENCY EXITS                                             │ │
│  │  ☑ Exit on macro shift to Strong Risk-Off                   │ │
│  │  BTC crash threshold    [======●=====] -4.0% (1h)          │ │
│  │  Funding emergency rate [=========●==] 0.08                 │ │
│  │  Exchange max latency   [======●=====] 5000ms              │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Macro Gate ────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  ☑ Enable Macro Gate (REQUIRED for futures)                 │ │
│  │  Update interval        [======●=====] 30min               │ │
│  │                                                              │ │
│  │  REGIME WEIGHTS (must sum to 100)                            │ │
│  │  BTC Trend       [=======●===] 30   ╔══════════════════╗   │ │
│  │  DXY Direction   [=====●=====] 20   ║▓▓▓▓▓▓░░░░░░░░░░░║   │ │
│  │  Funding Market  [====●======] 15   ║ 30  20  15 15 10 ║   │ │
│  │  Liquidation Pr. [====●======] 15   ╚══════════════════╝   │ │
│  │  Stablecoin Flow [===●=======] 10                           │ │
│  │  VIX             [===●=======] 10                           │ │
│  │                                                              │ │
│  │  THRESHOLDS                                                  │ │
│  │  Strong Risk-On >       [  75  ]                            │ │
│  │  Risk-On >              [  55  ]                            │ │
│  │  Neutral >              [  40  ]                            │ │
│  │  Risk-Off >             [  25  ]                            │ │
│  │  (Below 25 = Strong Risk-Off)                               │ │
│  │                                                              │ │
│  │  Neutral size reduction [======●=====] 25%                  │ │
│  │  Pre-event buffer       [======●=====] 4h                   │ │
│  │  Pre-event size cut     [========●===] 50%                  │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Loss Limits & Circuit Breaker ─────────────────────────────┐ │
│  │                                                              │ │
│  │  Daily Loss Limit       [======●=====] 3.0%   ($300)       │ │
│  │  Weekly Loss Limit      [========●===] 5.0%   ($500)       │ │
│  │  Weekly loss → size cut [========●===] 50%                  │ │
│  │  Circuit Breaker after  [  3  ] consecutive losses          │ │
│  │  Pause duration         [  60  ] minutes                    │ │
│  │                                                              │ │
│  │  ┌──────────────────────────────────────────────────────┐   │ │
│  │  │  RISK PREVIEW (real-time calculation)                │   │ │
│  │  │                                                      │   │ │
│  │  │  Capital:           $10,000                          │   │ │
│  │  │  Max risk/trade:    $100 (1%) — $200 conviction      │   │ │
│  │  │  Max daily loss:    $300 (3 trades at max risk)      │   │ │
│  │  │  Max weekly loss:   $500 (stops trading)             │   │ │
│  │  │  Max margin in use: $6,000 (60%)                     │   │ │
│  │  │  Max positions:     5 (max 2 correlated)             │   │ │
│  │  │                                                      │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ Strategies ────────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  ☑ Momentum Breakout     min: 70  lev: normal    [Edit]    │ │
│  │  ☑ Mean Reversion        min: 80  lev: conserv.  [Edit]    │ │
│  │  ☑ Liquidity Sweep       min: 75  lev: normal    [Edit]    │ │
│  │  ☑ Vol. Compression      min: 75  lev: high      [Edit]    │ │
│  │  ☐ Funding Exploitation  min: 70  lev: conserv.  [Edit]    │ │
│  │  ☑ Structure Shift       min: 75  lev: normal    [Edit]    │ │
│  │                                                              │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  [ Reset to Defaults ]                          [ Save Changes ] │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Positions (`/trading-desk/positions`)

Visão consolidada de todas as posições abertas, separadas por profile.

```
┌──────────────────────────────────────────────────────────────────┐
│  POSITIONS                                                       │
│                                                                  │
│  Filter: [ All ▼ ]  [ All Assets ▼ ]  [ All Status ▼ ]         │
│                                                                  │
│  ┌─ Capital Overview ──────────────────────────────────────────┐ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │ │
│  │  │ TOTAL    │ │ SPOT     │ │ FUTURES  │ │ FREE     │       │ │
│  │  │ $12,000  │ │ $6,400   │ │ $4,200   │ │ $1,400   │       │ │
│  │  │ 12 pos.  │ │ 9 pos.   │ │ 3 pos.   │ │ 11.7%    │       │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ SPOT POSITIONS ────────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Symbol   Entry     Current   P&L %    P&L $   Status  Age │ │
│  │  ─────────────────────────────────────────────────────────  │ │
│  │  SOL    $142.30   $148.50   +4.35%  +$27.84  ACTIVE   2h  │ │
│  │  ETH    $3,201    $3,340    +4.34%  +$13.90  ACTIVE   5h  │ │
│  │  DOGE   $0.1820   $0.1755  -3.57%   -$3.25  🔴 UNDER 3d  │ │
│  │  BTC    $67,200   $65,100  -3.12%  -$18.75  🔴 UNDER 7d  │ │
│  │  ...                                                        │ │
│  │                                                              │ │
│  │  ┌─ Underwater Summary ─────────────────────────────────┐   │ │
│  │  │  3 underwater positions · $1,450 locked (12%)        │   │ │
│  │  │  Worst: BTC -3.12% (needs +3.73% to target)         │   │ │
│  │  │  Avg time underwater: 4.3 days                       │   │ │
│  │  │  Est. opportunity cost: ~$12/day                     │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  ┌─ FUTURES POSITIONS ─────────────────────────────────────────┐ │
│  │                                                              │ │
│  │  Symbol  Dir  Entry    Current  P&L%   Lev  Liq.Price  Dist │ │
│  │  ──────────────────────────────────────────────────────────  │ │
│  │  ETH    LONG $3,280  $3,340  +1.83%  5x   $2,952    9.8% │ │
│  │  SOL   SHORT $155.2  $148.5  +4.31%  3x   $181.0   16.6% │ │
│  │  BTC    LONG $67.8K  $68.1K  +0.44%  4x   $61.0K   10.0% │ │
│  │                                                              │ │
│  │  ┌─ Liquidation Monitor ────────────────────────────────┐   │ │
│  │  │  All positions SAFE                                  │   │ │
│  │  │  Nearest liquidation: ETH LONG at 9.8% distance     │   │ │
│  │  │  Total margin used: $4,200 / $6,000 max (70%)       │   │ │
│  │  │  Daily P&L: +$340 (+1.4%)                           │   │ │
│  │  │  Funding cost today: -$8.40                          │   │ │
│  │  └──────────────────────────────────────────────────────┘   │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

### Trade History (`/trading-desk/history`)

```
┌──────────────────────────────────────────────────────────────────┐
│  TRADE HISTORY                                                   │
│                                                                  │
│  Filter: [ All Profiles ▼ ] [ Date Range ] [ Symbol ▼ ]        │
│                                                                  │
│  ┌─ Summary Cards ─────────────────────────────────────────────┐ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │ │
│  │  │ TOTAL    │ │ WIN RATE │ │ AVG P&L  │ │ BEST     │       │ │
│  │  │ +$2,340  │ │ 72%      │ │ +1.8%    │ │ +$420    │       │ │
│  │  │ 45 trades│ │ 32W/13L  │ │ per trade│ │ SOL LONG │       │ │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                                                  │
│  Profile  Symbol  Dir   Entry    Exit     P&L%    P&L$   Reason │
│  ────────────────────────────────────────────────────────────── │
│  FUTURES  SOL    SHORT $155.2  $148.5   +4.3%   +$89   TP2     │
│  SPOT     ETH    LONG  $3,180  $3,244   +2.0%   +$12   TARGET  │
│  FUTURES  BTC    LONG  $66.8K  $65.5K   -1.9%   -$38   STOP    │
│  SPOT     DOGE   LONG  $0.178  $0.183   +2.8%    +$5   AI_EXT  │
│  ...                                                             │
│                                                                  │
│  [ Export CSV ]                                                  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Componentes Novos

```
components/
├── trading-desk/
│   ├── shared/
│   │   ├── EngineStatusBar.tsx        # Status do engine (spot ou futures)
│   │   ├── ScannerControl.tsx         # Play/pause + scan interval
│   │   ├── ConfigSection.tsx          # Card colapsável com título + conteúdo
│   │   ├── SliderWithValue.tsx        # Slider + input numérico + unit
│   │   ├── RiskPreviewPanel.tsx       # Preview calculado em real-time
│   │   ├── ProfileBadge.tsx           # Badge "SPOT" ou "FUTURES"
│   │   └── SaveConfigBar.tsx          # Fixed bottom: Reset | Cancel | Save
│   │
│   ├── spot/
│   │   ├── SpotBuyingConfig.tsx
│   │   ├── SpotSellingConfig.tsx
│   │   ├── SpotHoldingConfig.tsx
│   │   ├── SpotDCAConfig.tsx
│   │   ├── SpotMacroFilter.tsx
│   │   └── SpotTrailingConfig.tsx
│   │
│   ├── futures/
│   │   ├── FuturesScoringConfig.tsx
│   │   ├── FuturesSizingConfig.tsx
│   │   ├── FuturesLeverageAntiLiq.tsx  # Combined: leverage caps + anti-liq visual
│   │   ├── FuturesStopTakeProfit.tsx
│   │   ├── FuturesTrailingConfig.tsx
│   │   ├── FuturesGuards.tsx           # Funding + OI + Emergency
│   │   ├── FuturesMacroGate.tsx
│   │   ├── FuturesLossLimits.tsx
│   │   └── FuturesStrategies.tsx
│   │
│   ├── positions/
│   │   ├── PositionsOverview.tsx       # Capital cards (total/spot/futures/free)
│   │   ├── SpotPositionsTable.tsx
│   │   ├── FuturesPositionsTable.tsx
│   │   ├── UnderwaterSummary.tsx       # Spot-specific underwater stats
│   │   ├── LiquidationMonitor.tsx      # Futures-specific liq distance
│   │   └── PositionDetailDrawer.tsx    # Slide-out com detalhes completos
│   │
│   └── history/
│       ├── TradeHistoryTable.tsx
│       ├── TradeDetailModal.tsx
│       ├── PnLSummaryCards.tsx
│       └── TradeExport.tsx
```

---

## Hooks Novos

```typescript
// Hook para config do trading desk (spot ou futures)
function useTradingConfig(profile: 'spot' | 'futures') {
  // SWR fetch do config_type correspondente
  // spot → "spot_engine"
  // futures → "futures_engine"
  return { config, updateConfig, resetConfig, isLoading, isSaving }
}

// Hook para status do engine
function useEngineStatus(profile: 'spot' | 'futures') {
  // WebSocket: engine running/paused, scan count, last scan time
  return { status, positions, capital, pause, resume }
}

// Hook para posições por profile
function usePositions(profile?: 'spot' | 'futures') {
  // Se profile undefined, retorna todas
  // Inclui WebSocket para P&L live
  return { positions, summary, underwaterCount, isLoading }
}

// Hook para macro regime (usado em ambos)
function useMacroRegime() {
  // Cached, atualizado a cada 30min
  return { regime, score, components, lastUpdate }
}
```

---

## API Endpoints Novos

```
# Trading Desk - Engine Control
POST   /api/trading-desk/spot/engine/start
POST   /api/trading-desk/spot/engine/pause
POST   /api/trading-desk/futures/engine/start
POST   /api/trading-desk/futures/engine/pause
GET    /api/trading-desk/spot/engine/status
GET    /api/trading-desk/futures/engine/status

# Trading Desk - Configs (via config service existente)
GET    /api/config/spot_engine
PUT    /api/config/spot_engine
GET    /api/config/futures_engine
PUT    /api/config/futures_engine

# Positions
GET    /api/positions?profile=spot&status=active
GET    /api/positions?profile=futures&status=active
GET    /api/positions/:id
GET    /api/positions/summary           # Capital allocation overview

# Trade History
GET    /api/trades?profile=all&from=&to=
GET    /api/trades/:id
GET    /api/trades/export?format=csv

# Macro Regime
GET    /api/macro/regime                # Current regime + components
GET    /api/macro/calendar              # Upcoming high-impact events
```

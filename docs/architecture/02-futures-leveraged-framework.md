# Scalpyn — Framework de Decisão: Futuros Alavancado

## Arquitetura de Dois Perfis

O sistema opera com dois perfis completamente independentes, cada um com config própria, risk engine própria, e lógica de decisão separada.

```
┌──────────────────────────────────────────────────────────────┐
│                     SCALPYN PLATFORM                         │
│                                                              │
│  ┌─────────────────────┐    ┌──────────────────────────────┐ │
│  │   PERFIL: SPOT      │    │   PERFIL: FUTUROS ALAVANCADO │ │
│  │                     │    │                              │ │
│  │ • Grid Bot          │    │ • Direcional (Long/Short)    │ │
│  │ • Só compra/venda   │    │ • Alavancagem 2x-20x        │ │
│  │ • Sem liquidação     │    │ • Liquidação possível        │ │
│  │ • Capital 100% real │    │ • Margem + emprestado        │ │
│  │ • Risk: perder %    │    │ • Risk: perder tudo (liq.)   │ │
│  │ • TP: 0.5-3%        │    │ • TP: ATR-based + structure  │ │
│  │ • Macro: filtro leve│    │ • Macro: gate obrigatório    │ │
│  │ • AI: consult. opt. │    │ • AI: scoring obrigatório    │ │
│  │ • Timeframe: 15m    │    │ • MTF: 15m + 1h + 4h        │ │
│  └─────────────────────┘    └──────────────────────────────┘ │
│                                                              │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │              SHARED INFRASTRUCTURE                       │ │
│  │  Market Data Service · Config Service · Notification     │ │
│  │  Exchange Adapters · TimescaleDB · Redis                 │ │
│  └──────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

### Diferenças Fundamentais

| Dimensão | Spot (Grid Bot) | Futuros Alavancado |
|----------|----------------|-------------------|
| Direção | Só LONG (compra/vende) | LONG e SHORT |
| Risco máximo | Perder % do capital | Liquidação total da posição |
| Alavancagem | 1x (sem) | 2x-20x (configurável) |
| Funding rate | Irrelevante | Custo contínuo (pode corroer lucro) |
| Análise pré-trade | Alpha Score + sinais | 5-Layer Institutional + Macro Gate |
| Decisão de saída | 5 camadas (grid bot) | Trailing ATR + Structure + Liquidation Guard |
| Posições simultâneas | Muitas (grid levels) | Máx. 3-5 (concentração de risco) |
| Capital por trade | Distribuído no grid | 1-2% risk por trade (Kelly fraction) |
| Correlação | Implícita (BTC guard) | Explícita (max 2 correlacionadas) |
| Macro | Filtro opcional | Gate obrigatório — sem aprovação, sem trade |

---

## Fluxo Completo de Decisão — Futuros Alavancado

```
═══════════════════════════════════════════════════════════════
PIPELINE DE DECISÃO — FUTUROS ALAVANCADO
═══════════════════════════════════════════════════════════════

  GATE 0: Portfolio Risk Check
    │ Exposure total < max? Daily loss < limit? Circuit breaker?
    │ FAIL → NO TRADE (espera reset)
    │
  GATE 1: Macro Regime Filter
    │ RISK_OFF forte? → NO TRADE (ou só SHORT se habilitado)
    │ RISK_ON → prossegue com bias LONG
    │ NEUTRAL → prossegue sem bias
    │
  GATE 2: Liquidity Gate (L1)
    │ Volume < min? Spread > max? → REJECT
    │ Score < 10 → REJECT (hard rule)
    │
  ANALYSIS: 5-Layer Institutional Scoring
    │ L1 Liquidity    → /20
    │ L2 Structure     → /20
    │ L3 Momentum      → /20
    │ L4 Volatility    → /20
    │ L5 Order Flow    → /20
    │ TOTAL            → /100
    │
  GATE 3: Score Threshold
    │ Score < 70 → NO TRADE
    │ Any layer < 8 → NO TRADE
    │ Score 70-79 → size reduzido (50-75%)
    │ Score 80-89 → size normal
    │ Score 90+ → size máximo (conviction trade)
    │
  GATE 4: Leverage-Specific Checks
    │ Funding rate adverso? → reduz size ou cancela
    │ Open Interest extremo? → reduz size
    │ Liquidation clusters próximas? → ajusta stop
    │
  EXECUTION: Entry + Risk Parameters
    │ Entry type: limit (VWAP/EMA/S&R pullback)
    │ Stop Loss: structure-based + ATR validation
    │ Take Profit: TP1/TP2/TP3 structure-based
    │ Position Size: Kelly-adjusted risk formula
    │ Leverage: calculado (não escolhido arbitrariamente)
    │
  MANAGEMENT: Position Active
    │ Trailing Stop ATR-based
    │ Partial exits em TP1/TP2
    │ Macro deterioration → emergency exit
    │ Funding drain guard
    │ Liquidation proximity alert
    │
═══════════════════════════════════════════════════════════════
```

---

## GATE 0: Portfolio Risk Check

Antes de qualquer análise, verificar se o portfolio permite um novo trade. Regras não-negociáveis do framework institucional.

```
portfolio_check():
  total_risk = soma do risco ($) de todas as posições abertas
  if total_risk > max_total_risk_pct * capital:
    → BLOCK "Portfolio risk máximo atingido"

  if daily_pnl < -daily_loss_limit_pct * capital:
    → BLOCK "Daily loss limit atingido — parar por hoje"

  if weekly_pnl < -weekly_loss_limit_pct * capital:
    → BLOCK "Weekly loss limit — reduzir sizing 50% na próxima semana"

  if consecutive_losses >= circuit_breaker_losses:
    if time_since_last_loss < circuit_breaker_pause:
      → BLOCK "Circuit breaker ativo — pausa de X minutos"

  if open_positions >= max_positions:
    → BLOCK "Máximo de posições atingido"

  if count_correlated(new_asset, open_positions) >= max_correlated:
    → BLOCK "Máximo de posições correlacionadas atingido"

  → PASS
```

**Config Scalpyn:**

```json
{
  "config_type": "risk_futures",
  "config_json": {
    "max_risk_per_trade_pct": 1.0,
    "max_risk_per_trade_conviction_pct": 2.0,
    "max_total_risk_pct": 5.0,
    "daily_loss_limit_pct": 3.0,
    "weekly_loss_limit_pct": 5.0,
    "circuit_breaker_consecutive_losses": 3,
    "circuit_breaker_pause_minutes": 60,
    "max_positions": 5,
    "max_correlated_positions": 2,
    "max_capital_deployed_pct": 60,
    "correlation_threshold": 0.7,
    "correlation_lookback_days": 30
  }
}
```

---

## GATE 1: Macro Regime Filter (OBRIGATÓRIO)

No spot, macro é filtro opcional. Em futuros alavancado, macro é **gate obrigatório** — alavancagem amplifica erros macro.

O regime é avaliado a cada `macro_update_interval` e cached em Redis.

```
macro_regime = evaluate_macro_regime():

  COMPOSIÇÃO DO REGIME:
  ┌──────────────────────────────────────────────────────┐
  │ Componente          │ Peso │ Fonte                   │
  ├──────────────────────────────────────────────────────┤
  │ BTC Trend (1D)      │ 30%  │ EMA21/50/200 + structure│
  │ DXY Direction       │ 20%  │ DXY acima/abaixo EMA21  │
  │ Funding Rate Mkt    │ 15%  │ Média funding top 10    │
  │ Liquidation Pressure│ 15%  │ Liq 24h vs média 7d     │
  │ Stablecoin Flow     │ 10%  │ USDT mcap change 7d     │
  │ VIX / Risk Appetite │ 10%  │ VIX level + direction   │
  └──────────────────────────────────────────────────────┘

  CLASSIFICAÇÃO:
  Score ponderado → regime

  STRONG_RISK_ON (score > 75):
    → Libera LONG com sizing normal
    → SHORT bloqueado (exceto scalp curto)
    → Alavancagem máxima permitida

  RISK_ON (score 55-75):
    → Libera LONG com sizing normal
    → SHORT permitido com sizing reduzido
    → Alavancagem normal

  NEUTRAL (score 40-55):
    → LONG e SHORT permitidos
    → Sizing reduzido em 25%
    → Alavancagem reduzida

  RISK_OFF (score 25-40):
    → LONG bloqueado (exceto mean reversion com score 85+)
    → SHORT liberado com sizing normal
    → Alavancagem reduzida

  STRONG_RISK_OFF (score < 25):
    → LONG bloqueado totalmente
    → SHORT com cautela (mercado pode já ter precificado)
    → Alavancagem mínima
    → Alerta: "Considere ficar 100% cash"
```

### Eventos Macro de Alto Impacto (calendário)

```
upcoming_events = get_macro_calendar():
  - FOMC decision → reduz sizing 50% nas 24h antes
  - CPI release → reduz sizing 50% nas 4h antes
  - NFP → reduz sizing 30% no dia
  - Major geopolitical event → evaluate caso a caso

  if hours_until_high_impact_event < pre_event_buffer_hours:
    → Reduz sizing OU bloqueia novos trades
    → NÃO fecha posições existentes lucrativas
    → Aperta trailing stops em posições existentes
```

**Config Scalpyn:**

```json
{
  "config_type": "macro_regime_futures",
  "config_json": {
    "enabled": true,
    "update_interval_minutes": 30,
    "weights": {
      "btc_trend": 30,
      "dxy_direction": 20,
      "funding_rate_market": 15,
      "liquidation_pressure": 15,
      "stablecoin_flow": 10,
      "vix_risk_appetite": 10
    },
    "thresholds": {
      "strong_risk_on": 75,
      "risk_on": 55,
      "neutral": 40,
      "risk_off": 25
    },
    "risk_off_allow_long_min_score": 85,
    "risk_on_allow_short": true,
    "risk_on_short_size_reduction": 0.50,
    "neutral_size_reduction": 0.25,
    "pre_event_buffer_hours": 4,
    "pre_event_size_reduction": 0.50,
    "btc_ema_periods": [21, 50, 200],
    "btc_timeframe": "1d",
    "dxy_ema_period": 21,
    "funding_extreme_positive": 0.05,
    "funding_extreme_negative": -0.03,
    "vix_elevated": 25,
    "vix_panic": 35
  }
}
```

---

## GATE 2 + ANALYSIS: 5-Layer Institutional Scoring

O core do sistema de futuros é o scoring de 5 camadas. Cada camada segue o framework da skill `institutional-trading-ai`.

### L1 — Liquidity (0-20)

```
l1_score = evaluate_liquidity(symbol):
  volume_24h = get_24h_volume(symbol)
  relative_volume = volume_now / avg_volume_20d
  spread = get_current_spread(symbol)
  book_depth = get_orderbook_depth(symbol, depth_pct=2.0)

  score = 0
  score += volume_score(volume_24h)       // 0-7 pontos
  score += rel_volume_score(relative_volume) // 0-5 pontos
  score += spread_score(spread)           // 0-4 pontos
  score += depth_score(book_depth)        // 0-4 pontos

  // HARD RULE: L1 < 10 → REJECT (não negociável)
  if score < l1_min_score:
    return REJECT

  return score
```

### L2 — Market Structure (0-20)

```
l2_score = evaluate_structure(symbol, [15m, 1h, 4h]):
  // Multi-timeframe structure analysis
  structure_15m = identify_structure(candles_15m)
  structure_1h = identify_structure(candles_1h)
  structure_4h = identify_structure(candles_4h)

  // HH/HL para LONG, LH/LL para SHORT
  trend_direction = determine_trend(structure_1h)  // primary
  alignment = check_alignment(structure_15m, structure_1h, structure_4h)

  // Key levels
  support_levels = find_support(candles_1h, lookback=50)
  resistance_levels = find_resistance(candles_1h, lookback=50)

  // Liquidity grabs (institutional tactic)
  recent_sweeps = detect_liquidity_sweeps(candles_15m, lookback=20)

  score = 0
  score += trend_clarity_score(trend_direction)   // 0-6
  score += alignment_score(alignment)             // 0-6
  score += levels_clarity_score(support, resistance) // 0-4
  score += sweep_bonus(recent_sweeps)             // 0-4

  return score, trend_direction, key_levels
```

### L3 — Momentum (0-20)

```
l3_score = evaluate_momentum(symbol):
  rsi = get_rsi(period=rsi_period)
  macd = get_macd(fast, slow, signal)
  ema9, ema21, ema200 = get_emas(symbol)
  vwap = get_vwap(symbol)

  // Divergence detection (mais importante em futuros)
  rsi_divergence = detect_divergence(price, rsi, lookback=20)
  macd_divergence = detect_divergence(price, macd.histogram, lookback=20)

  // Momentum acceleration
  momentum_accel = rsi_slope(period=5)

  score = 0
  score += rsi_score(rsi, trade_direction)        // 0-5
  score += macd_score(macd, trade_direction)       // 0-4
  score += ema_alignment_score(ema9, ema21, ema200) // 0-4
  score += vwap_score(price, vwap)                // 0-3
  score += divergence_score(rsi_div, macd_div)    // 0-4 (bônus ou penalidade)

  return score, divergences
```

### L4 — Volatility (0-20)

```
l4_score = evaluate_volatility(symbol):
  atr = get_atr(period=atr_period)
  atr_pct = atr / price * 100
  bb = get_bollinger(period=bb_period, dev=bb_deviation)
  bb_width = (bb.upper - bb.lower) / bb.middle
  bb_percentile = percentile(bb_width, lookback=100)

  // Regime classification
  if bb_percentile < squeeze_percentile:
    vol_regime = "SQUEEZE"        // alta probabilidade de breakout
  elif atr_slope > 0 and bb_width expanding:
    vol_regime = "EXPANDING"      // move em andamento
  else:
    vol_regime = "NORMAL"

  // Compression patterns
  compression = detect_compression(candles, lookback=30)

  score = 0
  score += regime_score(vol_regime, trade_type)   // 0-8
  score += atr_score(atr_pct)                     // 0-4
  score += compression_score(compression)          // 0-4
  score += bb_position_score(price, bb)            // 0-4

  return score, vol_regime, atr, atr_pct
```

### L5 — Order Flow (0-20)

```
l5_score = evaluate_order_flow(symbol):
  // Taker buy/sell ratio
  taker_ratio = get_taker_buy_sell_ratio(symbol)

  // Funding rate (CRÍTICO em futuros)
  funding = get_funding_rate(symbol)
  funding_direction = "LONG_CROWDED" if funding > extreme_pos
                      else "SHORT_CROWDED" if funding < extreme_neg
                      else "BALANCED"

  // Open Interest changes
  oi_change = get_oi_change_pct(symbol, period="4h")

  // Liquidation data
  liq_longs_24h = get_liquidations(symbol, "long", "24h")
  liq_shorts_24h = get_liquidations(symbol, "short", "24h")
  liq_ratio = liq_longs_24h / max(liq_shorts_24h, 1)

  // Large transactions (whale activity)
  whale_activity = detect_large_transactions(symbol, threshold_usd=100000)

  score = 0
  score += taker_score(taker_ratio, trade_direction)    // 0-5
  score += funding_score(funding, trade_direction)       // 0-5
  score += oi_score(oi_change, price_direction)          // 0-4
  score += liquidation_score(liq_ratio, trade_direction) // 0-3
  score += whale_score(whale_activity)                   // 0-3

  return score, funding_direction, oi_data
```

### Score Consolidado + Gate

```
total_score = l1 + l2 + l3 + l4 + l5  // 0-100

// GATE 3: Score Threshold
if total_score < min_score_to_trade:           // default: 70
  → NO TRADE

if any_layer < min_layer_score:                // default: 8
  → NO TRADE (single point of failure)

// Classification
if total_score >= conviction_threshold:        // 90+
  classification = "INSTITUTIONAL_GRADE"
  size_multiplier = 1.5                        // conviction sizing
  max_leverage_tier = "high"

elif total_score >= strong_threshold:          // 80-89
  classification = "STRONG"
  size_multiplier = 1.0
  max_leverage_tier = "normal"

elif total_score >= valid_threshold:           // 70-79
  classification = "VALID"
  size_multiplier = 0.6                        // reduced sizing
  max_leverage_tier = "conservative"

else:
  → NO TRADE
```

**Config Scalpyn:**

```json
{
  "config_type": "scoring_futures",
  "config_json": {
    "min_score_to_trade": 70,
    "min_layer_score": 8,
    "conviction_threshold": 90,
    "strong_threshold": 80,
    "valid_threshold": 70,
    "size_multipliers": {
      "institutional_grade": 1.5,
      "strong": 1.0,
      "valid": 0.6
    },
    "leverage_tiers": {
      "high": "max_allowed",
      "normal": 0.7,
      "conservative": 0.4
    },
    "l1_hard_reject": 10,
    "l1_weights": {
      "volume_24h": 7,
      "relative_volume": 5,
      "spread": 4,
      "book_depth": 4
    },
    "l2_timeframes": ["15m", "1h", "4h"],
    "l2_swing_lookback": 50,
    "l3_rsi_period": 14,
    "l3_divergence_lookback": 20,
    "l4_atr_period": 14,
    "l4_bb_period": 20,
    "l4_bb_deviation": 2.0,
    "l4_squeeze_percentile": 20,
    "l5_funding_extreme_positive": 0.05,
    "l5_funding_extreme_negative": -0.05,
    "l5_whale_threshold_usd": 100000
  }
}
```

---

## GATE 4: Leverage-Specific Checks

Checks exclusivos de futuros que não existem no perfil spot.

### 4A: Funding Rate Guard

```
funding_check(symbol, trade_direction):
  funding = get_funding_rate(symbol)

  // LONG com funding muito positivo = pagando caro + longs crowded
  if direction == LONG and funding > funding_max_for_long:
    if funding > funding_extreme:
      → BLOCK "Funding extremo positivo — longs overleveraged"
    else:
      → REDUCE size by funding_reduction_pct

  // SHORT com funding muito negativo = pagando caro + shorts crowded
  if direction == SHORT and funding < funding_min_for_short:
    if funding < -funding_extreme:
      → BLOCK "Funding extremo negativo — shorts overleveraged"
    else:
      → REDUCE size by funding_reduction_pct

  // Calcular custo esperado de funding
  expected_hold_hours = estimate_hold_duration(vol_regime, atr)
  funding_cost = abs(funding) * (expected_hold_hours / 8) * position_value
  if funding_cost > max_funding_cost_pct * expected_profit:
    → WARN "Funding pode corroer X% do lucro esperado"
```

### 4B: Open Interest Extreme

```
oi_check(symbol):
  oi = get_open_interest(symbol)
  oi_percentile = percentile(oi, lookback_days=30)

  if oi_percentile > oi_extreme_percentile:  // ex: > 95th percentile
    → REDUCE size by oi_reduction_pct
    → WARN "OI em extremo — risco de cascata de liquidações"
    → Apertar stop loss em 20%

  // OI subindo + preço caindo = divergência perigosa (shorts acumulando)
  if oi_rising and price_falling:
    if direction == LONG:
      → WARN "OI diverge do preço — cautela com LONGs"
```

### 4C: Liquidation Proximity Map

```
liquidation_check(symbol, entry, stop, direction):
  liq_clusters = get_liquidation_heatmap(symbol)

  // Verificar se existem clusters de liquidação entre entry e stop
  if liq_cluster_between(entry, stop, direction):
    → ADJUST stop para além do cluster (evitar ser arrastado)
    → Recalcular position size com novo stop

  // Verificar se a posição própria tem liquidação muito próxima
  liq_price = calculate_liquidation_price(entry, leverage, direction, margin)
  distance_to_liq = abs(entry - liq_price) / entry * 100

  if distance_to_liq < min_liquidation_distance_pct:
    → REDUCE leverage até distance_to_liq >= min
    → Se ainda insuficiente → BLOCK trade
```

**Config Scalpyn:**

```json
{
  "config_type": "leverage_checks_futures",
  "config_json": {
    "funding_guard": {
      "enabled": true,
      "funding_max_for_long": 0.03,
      "funding_min_for_short": -0.03,
      "funding_extreme": 0.05,
      "funding_reduction_pct": 0.30,
      "max_funding_cost_pct_of_profit": 0.15
    },
    "oi_guard": {
      "enabled": true,
      "oi_extreme_percentile": 95,
      "oi_reduction_pct": 0.30,
      "oi_lookback_days": 30,
      "oi_stop_tighten_pct": 0.20
    },
    "liquidation_guard": {
      "enabled": true,
      "min_liquidation_distance_pct": 15,
      "adjust_stop_beyond_cluster": true,
      "cluster_proximity_pct": 2.0
    }
  }
}
```

---

## EXECUTION: Entry + Position Sizing + Leverage

### Entry Optimization

Diferente do spot (que compra em grid levels fixos), futuros usa entry optimization institucional.

```
optimize_entry(symbol, direction, key_levels, vol_regime):

  ESTRATÉGIAS DE ENTRY (prioridade):

  1. VWAP Pullback
     → Preço overextended acima/abaixo de VWAP
     → Esperar retorno ao VWAP → entrar com confirmação
     → Melhor R:R, requer paciência

  2. EMA Pullback
     → Em uptrend: esperar toque em EMA9 ou EMA21
     → Em downtrend: esperar rally até EMA para short
     → Entrar no bounce/rejeição confirmada

  3. Support/Resistance Reaction
     → Esperar preço atingir key level do L2
     → Confirmar com wick, engulfing, ou volume drop
     → Entrar na rejeição

  4. Post-Sweep Entry
     → Detectar liquidity sweep (false breakout)
     → Entrar após preço voltar para dentro do range
     → Melhor R:R possível (stop atrás do sweep)

  5. Breakout Retest
     → NÃO perseguir breakout
     → Esperar breakout → pullback → retest do nível rompido
     → Entrar no retest hold

  REGRA: Nunca perseguir. Sempre esperar confirmação.
  ORDER TYPE: Limit order no nível calculado (não market order)
  TIMEOUT: Se limit não preencher em entry_timeout_minutes → cancelar
```

### Position Sizing (Kelly-Adjusted)

```
calculate_position_size(capital, entry, stop_loss, score, macro_regime):

  // 1. Calcular risco em $ baseado no score
  base_risk_pct = max_risk_per_trade_pct  // default: 1%
  if score >= conviction_threshold:
    risk_pct = max_risk_per_trade_conviction_pct  // default: 2%
  else:
    risk_pct = base_risk_pct * size_multipliers[classification]

  // 2. Aplicar modificadores macro
  risk_pct *= macro_size_modifier(macro_regime)

  // 3. Calcular risk em $
  risk_dollars = capital * risk_pct / 100

  // 4. Calcular distância entry→stop
  stop_distance = abs(entry - stop_loss)
  stop_distance_pct = stop_distance / entry * 100

  // 5. Position size (sem alavancagem)
  position_size_units = risk_dollars / stop_distance
  position_value = position_size_units * entry

  // 6. Calcular alavancagem necessária
  available_margin = capital * max_capital_deployed_pct / 100
  required_leverage = position_value / available_margin

  // 7. Cap de alavancagem
  max_lev = get_max_leverage(classification, macro_regime)
  if required_leverage > max_lev:
    // Reduzir position size para caber na alavancagem máxima
    position_value = available_margin * max_lev
    position_size_units = position_value / entry
    // Recalcular risco real
    actual_risk = position_size_units * stop_distance
    actual_risk_pct = actual_risk / capital * 100

  // 8. Validar liquidation distance
  liq_price = calc_liquidation(entry, required_leverage, direction)
  if liquidation_too_close(liq_price, stop_loss):
    → Reduzir leverage até safe

  return {
    position_size_units,
    position_value,
    leverage: min(required_leverage, max_lev),
    risk_dollars: actual_risk,
    risk_pct: actual_risk_pct,
    liquidation_price: liq_price
  }
```

**Princípio fundamental:** A alavancagem é CONSEQUÊNCIA do sizing e do stop, não uma escolha arbitrária. Nunca "escolher 10x" — calcular quanto precisa para respeitar o risk por trade.

### Stop Loss Placement

```
calculate_stop_loss(direction, key_levels, atr, entry):

  HIERARQUIA (usar a primeira aplicável):

  1. Structure-Based (preferido)
     LONG: abaixo do último swing low significativo
     SHORT: acima do último swing high significativo
     → Deve ter espaço suficiente para "respirar"

  2. Liquidity-Based
     LONG: abaixo de cluster de liquidação não-varrido
     SHORT: acima de cluster não-varrido
     → Usa liquidation heatmap

  3. ATR-Based (fallback)
     LONG: entry - (ATR * atr_stop_multiplier)
     SHORT: entry + (ATR * atr_stop_multiplier)
     → Default multiplier: 1.5x ATR

  VALIDAÇÃO:
  - Stop nunca mais longe que max_stop_distance_pct do entry
  - Stop nunca mais perto que min_stop_distance_pct do entry
  - Se structure-based stop > max_distance → trade inválido (R:R ruim)
  - Liquidation price deve estar ALÉM do stop (margem de segurança)
```

### Take Profit Strategy

```
calculate_take_profits(direction, entry, stop, key_levels, atr):

  risk = abs(entry - stop)

  TP1 (Conservative) = entry + risk * rr_tp1   // default: 1.5x risk
    → Ou primeiro nível S/R significativo
    → Ação: fechar tp1_exit_pct da posição (default: 35%)
    → Mover stop para breakeven

  TP2 (Target) = entry + risk * rr_tp2         // default: 2.5x risk
    → Ou segundo nível S/R
    → Ação: fechar tp2_exit_pct do restante (default: 50%)
    → Ativar trailing stop ATR no restante

  TP3 (Extended) = entry + risk * rr_tp3       // default: 4.0x risk
    → Ou nível estrutural major
    → Ação: trailing stop decide saída do restante

  // Ajuste por volatility regime
  if vol_regime == "SQUEEZE":
    // Breakouts de squeeze tendem a ser maiores
    TP2 *= squeeze_tp_multiplier  // default: 1.3x
    TP3 *= squeeze_tp_multiplier

  if vol_regime == "EXPANDING":
    // Moves em andamento podem ter menos upside restante
    TP2 *= expanding_tp_multiplier  // default: 0.85x
```

**Config Scalpyn:**

```json
{
  "config_type": "execution_futures",
  "config_json": {
    "entry": {
      "default_order_type": "limit",
      "entry_timeout_minutes": 30,
      "max_slippage_pct": 0.10,
      "strategies_priority": [
        "vwap_pullback",
        "ema_pullback",
        "sr_reaction",
        "post_sweep",
        "breakout_retest"
      ]
    },
    "stop_loss": {
      "method_priority": ["structure", "liquidity", "atr"],
      "atr_stop_multiplier": 1.5,
      "max_stop_distance_pct": 5.0,
      "min_stop_distance_pct": 0.3,
      "liq_price_safety_margin_pct": 3.0
    },
    "take_profit": {
      "rr_tp1": 1.5,
      "rr_tp2": 2.5,
      "rr_tp3": 4.0,
      "tp1_exit_pct": 35,
      "tp2_exit_pct": 50,
      "move_stop_to_breakeven_at": "tp1",
      "activate_trailing_at": "tp2",
      "squeeze_tp_multiplier": 1.3,
      "expanding_tp_multiplier": 0.85
    },
    "leverage": {
      "max_leverage_institutional": 10,
      "max_leverage_strong": 7,
      "max_leverage_valid": 4,
      "max_leverage_risk_off": 3,
      "min_liquidation_distance_from_stop_pct": 5.0
    },
    "position_sizing": {
      "method": "risk_based",
      "max_capital_per_trade_pct": 30,
      "max_capital_deployed_pct": 60
    }
  }
}
```

---

## MANAGEMENT: Posição Ativa

### Trailing Stop ATR-Based (Futuros)

Diferente do spot (faixas fixas de margem), futuros usa ATR dinâmico do framework institucional: "After TP2, trail by 1x ATR."

```
manage_trailing_stop(position, current_price, atr):

  // Antes do TP1: stop fixo (structure-based original)
  if not tp1_hit:
    trailing_stop = original_stop_loss
    return

  // Entre TP1 e TP2: stop no breakeven
  if tp1_hit and not tp2_hit:
    trailing_stop = max(entry_price, breakeven_with_fees)
    return

  // Após TP2: trailing ATR dinâmico
  if tp2_hit:
    // ATR-based trailing
    atr_trail = atr * trailing_atr_multiplier  // default: 1.0x ATR

    // High Water Mark tracking
    if direction == LONG:
      hwm = max(hwm, current_price)
      calculated_stop = hwm - atr_trail
    else:  // SHORT
      hwm = min(hwm, current_price)  // LWM for shorts
      calculated_stop = hwm + atr_trail

    // Floor: nunca abaixo do breakeven
    if direction == LONG:
      trailing_stop = max(calculated_stop, entry_price)
    else:
      trailing_stop = min(calculated_stop, entry_price)

    // Tighten em lucro alto
    unrealized_pct = calc_unrealized_pnl_pct(position)
    if unrealized_pct > tighten_above_profit_pct:
      atr_trail *= tighten_factor  // aperta trailing

  // CHECK: exit?
  if should_exit(current_price, trailing_stop, direction):
    → EXECUTE market sell
    → Log: "Trailing stop ATR hit. HWM: X, Trail: Y, Exit: Z"
```

### Emergency Exit Conditions

```
check_emergency_conditions(position):

  // 1. Macro Deterioration
  if macro_regime changed to STRONG_RISK_OFF since entry:
    if direction == LONG:
      → EXIT immediately (market order)
      → Log: "EMERGENCY: Macro regime shifted to STRONG_RISK_OFF"

  // 2. BTC Flash Crash (para altcoins)
  if symbol != "BTCUSDT":
    btc_change_1h = get_btc_change("1h")
    if abs(btc_change_1h) > btc_emergency_threshold:
      → EXIT immediately
      → Log: "EMERGENCY: BTC moved X% in 1h"

  // 3. Funding Rate Explosion
  funding = get_funding_rate(symbol)
  if direction == LONG and funding > funding_emergency:
    → EXIT (funding custo se tornaria insustentável)

  // 4. Liquidation Approaching
  distance_to_liq = calc_distance_to_liquidation(position)
  if distance_to_liq < emergency_liq_distance_pct:
    → EXIT immediately
    → Log: "EMERGENCY: Liquidation proximity X%"

  // 5. Exchange Issues
  if exchange_latency > max_latency_ms or exchange_error:
    → Attempt EXIT
    → If fails: alert via Slack/Push IMMEDIATELY
```

### Funding Rate Drain Guard

```
check_funding_drain(position):
  // Calcular funding acumulado desde entry
  total_funding_paid = sum(funding_payments since entry)
  total_funding_pct = total_funding_paid / position_value * 100

  // Se funding acumulado come mais que X% do lucro esperado
  unrealized = calc_unrealized_pnl(position)
  if total_funding_paid > max_funding_drain_pct * unrealized:
    if unrealized > 0:
      → WARN "Funding drenou X% do lucro. Considere fechar."
    else:
      → EXIT "Funding + loss combinados. Cortando posição."

  // Projeção de funding
  avg_funding_per_8h = total_funding_paid / (hours_open / 8)
  projected_24h_cost = avg_funding_per_8h * 3
  if projected_24h_cost > max_daily_funding_cost:
    → WARN "Custo projetado de funding: $X/dia"
```

**Config Scalpyn:**

```json
{
  "config_type": "management_futures",
  "config_json": {
    "trailing": {
      "method": "atr",
      "trailing_atr_multiplier": 1.0,
      "tighten_above_profit_pct": 5.0,
      "tighten_factor": 0.7
    },
    "emergency": {
      "macro_shift_exit": true,
      "btc_emergency_threshold_1h_pct": 4.0,
      "funding_emergency": 0.08,
      "emergency_liq_distance_pct": 5.0,
      "max_exchange_latency_ms": 5000
    },
    "funding_drain": {
      "enabled": true,
      "max_funding_drain_pct_of_profit": 0.25,
      "max_daily_funding_cost_usd": 50,
      "warn_on_adverse_funding": true
    },
    "partial_exits": {
      "tp1_close_pct": 35,
      "tp2_close_pct": 50,
      "trailing_remainder_pct": 15
    }
  }
}
```

---

## Estratégias Suportadas — Futuros

Cada estratégia é um padrão de trade com entry/exit rules específicas, todas configuráveis.

### 1. Momentum Breakout (LONG ou SHORT)

```
Setup: Preço rompe consolidação com volume > vol_spike_multiplier × média E ADX > adx_min
Entry: No candle de breakout ou retest do nível rompido
Stop: Abaixo do nível de breakout / último swing
Target: Measured move (altura da consolidação projetada)
Leverage tier: normal a high (momentum confirma direção)
```

### 2. Mean Reversion (Counter-trend)

```
Setup: RSI < rsi_oversold (long) ou > rsi_overbought (short)
       + preço fora da Bollinger Band
       + Z-score extremo (< -2 ou > 2)
Entry: No candle de reversão (hammer, engulfing) em S/R
Stop: Abaixo do extreme wick / sweep level
Target: VWAP ou middle Bollinger
Leverage tier: conservative (counter-trend = mais risco)
Score mínimo: 80 (exige mais confirmação)
```

### 3. Liquidity Sweep + Reversal

```
Setup: Preço varre key level (stops acionados) e reverte com volume
Entry: Primeiro candle que fecha de volta dentro do range
Stop: Abaixo do sweep wick
Target: Lado oposto do range → próximo key level
Leverage tier: normal (setup institucional clássico)
Melhor em: mercado ranging com níveis claros
```

### 4. Volatility Compression Breakout

```
Setup: Bollinger squeeze (mais estreita em 20+ candles)
       + ATR declinando
       + Volume building (divergência vol vs preço)
Entry: Primeiro candle fora do range de compressão com volume
Stop: Lado oposto do range de compressão
Target: 1.5x-2x a largura do range de compressão
Leverage tier: high (setup de alta probabilidade quando confirmado)
```

### 5. Funding Rate Exploitation (crypto-specific)

```
Setup: Funding rate extremo (> 0.05% ou < -0.05%)
       + Preço overextended (RSI extremo)
       + OI em ATH
Entry: Fade the crowd — short quando funding muito positivo
Stop: Acima do extreme
Target: Return to neutral funding / mean price
Leverage tier: conservative (contrarian, requer disciplina)
Macro requirement: pode operar em qualquer regime (funding é regime-agnostic)
```

### 6. Structure Shift (MSS Trade)

```
Setup: Market Structure Shift detectado em 1h
       + Primeiro HH após série de LH (bullish MSS)
       + OU primeiro LH após série de HH (bearish MSS)
       + Volume confirma no candle de MSS
Entry: Pullback após o MSS (entry no retest da zona de shift)
Stop: Abaixo/acima do ponto de MSS
Target: Key levels do novo regime de tendência
Leverage tier: normal (novo trend, bom R:R se timing correto)
```

**Config Scalpyn:**

```json
{
  "config_type": "strategies_futures",
  "config_json": {
    "strategies": [
      {
        "id": "momentum_breakout",
        "name": "Momentum Breakout",
        "enabled": true,
        "direction": "both",
        "min_score": 70,
        "leverage_tier": "normal",
        "params": {
          "volume_spike_multiplier": 2.0,
          "adx_min": 25,
          "consolidation_lookback": 20
        }
      },
      {
        "id": "mean_reversion",
        "name": "Mean Reversion",
        "enabled": true,
        "direction": "both",
        "min_score": 80,
        "leverage_tier": "conservative",
        "params": {
          "rsi_oversold": 30,
          "rsi_overbought": 70,
          "bollinger_deviation": 2.0,
          "zscore_threshold": 2.0
        }
      },
      {
        "id": "liquidity_sweep",
        "name": "Liquidity Sweep + Reversal",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "normal",
        "params": {
          "sweep_detection_lookback": 20,
          "min_wick_pct": 0.3,
          "volume_confirmation": true
        }
      },
      {
        "id": "volatility_compression",
        "name": "Volatility Compression Breakout",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "high",
        "params": {
          "squeeze_lookback": 20,
          "atr_declining_candles": 5,
          "target_multiplier": 1.75
        }
      },
      {
        "id": "funding_exploitation",
        "name": "Funding Rate Exploitation",
        "enabled": false,
        "direction": "both",
        "min_score": 70,
        "leverage_tier": "conservative",
        "params": {
          "funding_threshold": 0.05,
          "rsi_extreme": 75,
          "oi_percentile_min": 80
        }
      },
      {
        "id": "structure_shift",
        "name": "Market Structure Shift",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "normal",
        "params": {
          "mss_timeframe": "1h",
          "confirmation_volume_multiplier": 1.5,
          "pullback_entry": true
        }
      }
    ]
  }
}
```

---

## Notifications & Logging — Futuros

Futuros exige notificações mais agressivas que spot por causa do risco de liquidação.

```
NOTIFICATION TIERS:

  INFO (Slack channel: #futures-trades)
    → Nova posição aberta
    → TP1 atingido, stop movido para breakeven
    → TP2 atingido, trailing ativado
    → Posição fechada com lucro

  WARNING (Slack channel: #futures-alerts + Push notification)
    → Funding rate adverso acumulando
    → Macro regime mudou
    → Score deteriorou desde entry
    → OI extreme detectado
    → Posição em loss > 50% do stop

  CRITICAL (Slack channel: #futures-emergency + Push + SMS se configurado)
    → Liquidation proximity < emergency threshold
    → Emergency exit executado
    → Exchange connection lost com posição aberta
    → Posição fechada com loss > daily limit contribution
    → BTC flash crash detectado com posições alt abertas

  DAILY SUMMARY (Slack: #futures-daily)
    → P&L do dia (realizado + não realizado)
    → Trades executados com scores
    → Macro regime atual
    → Funding costs acumulados
    → Top opportunities detectadas mas não executadas (e porquê)
```

---

## Diagrama Completo — Decisão de Trade em Futuros

```
╔═══════════════════════════════════════════════════════════════╗
║               FUTURES TRADE DECISION PIPELINE                ║
╠═══════════════════════════════════════════════════════════════╣
║                                                               ║
║  ┌─ GATE 0: Portfolio Risk ─────────────────────────────────┐ ║
║  │  Daily loss limit? Circuit breaker? Max positions?       │ ║
║  │  Max correlated? Total exposure?                         │ ║
║  │  FAIL → ██ NO TRADE ██                                   │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ PASS                               ║
║  ┌─ GATE 1: Macro Regime ──────────────────────────────────┐ ║
║  │  BTC trend + DXY + Funding + Liquidations + VIX         │ ║
║  │  STRONG_RISK_OFF → só SHORT (ou cash)                   │ ║
║  │  RISK_OFF → só SHORT                                    │ ║
║  │  NEUTRAL → ambos (size -25%)                            │ ║
║  │  RISK_ON → ambos (LONG favorecido)                      │ ║
║  │  STRONG_RISK_ON → LONG full size                        │ ║
║  │  + Evento macro iminente? → reduz size                  │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ PASS                               ║
║  ┌─ GATE 2: Liquidity (L1) ───────────────────────────────┐ ║
║  │  Volume + Spread + Book depth + Relative volume         │ ║
║  │  L1 < 10 → ██ REJECT ██ (hard rule)                    │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ PASS                               ║
║  ┌─ 5-LAYER SCORING ──────────────────────────────────────┐ ║
║  │  L1 Liquidity:       ██░░░░  12/20                     │ ║
║  │  L2 Structure:       ████░░  16/20                     │ ║
║  │  L3 Momentum:        ███░░░  14/20                     │ ║
║  │  L4 Volatility:      ████░░  17/20                     │ ║
║  │  L5 Order Flow:      ███░░░  15/20                     │ ║
║  │  ─────────────────────────────────                     │ ║
║  │  TOTAL:              ██████  74/100 → VALID TRADE      │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ ≥ 70                               ║
║  ┌─ GATE 4: Leverage Checks ──────────────────────────────┐ ║
║  │  Funding rate OK? OI not extreme? Liq distance safe?   │ ║
║  │  Adjustments applied → size/leverage modified           │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ PASS                               ║
║  ┌─ EXECUTION ─────────────────────────────────────────────┐ ║
║  │  Entry: Limit @ EMA21 pullback                          │ ║
║  │  Stop: Below swing low (1.5x ATR validated)             │ ║
║  │  TP1: 1.5R → close 35% → move stop to BE               │ ║
║  │  TP2: 2.5R → close 50% → activate trailing              │ ║
║  │  TP3: 4.0R → trailing decides                           │ ║
║  │  Size: $2,400 (1% risk of $240k)                        │ ║
║  │  Leverage: 5.2x (calculated, not chosen)                │ ║
║  │  Liquidation: -19.2% from entry (safe)                  │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                          │ FILLED                             ║
║  ┌─ ACTIVE MANAGEMENT ────────────────────────────────────┐ ║
║  │  Trailing stop ATR-based (after TP2)                    │ ║
║  │  Macro monitoring (emergency exit if regime shifts)     │ ║
║  │  Funding drain guard (alert if eroding profits)         │ ║
║  │  Liquidation proximity monitor                          │ ║
║  │  BTC correlation guard (for alts)                       │ ║
║  └──────────────────────────────────────────────────────────┘ ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```

---

## Resumo: Config Types para Futuros

| config_type | Conteúdo | Pool Override? |
|-------------|----------|---------------|
| `risk_futures` | Limites de risco, circuit breaker, max positions | Sim |
| `macro_regime_futures` | Pesos macro, thresholds, pre-event rules | Não (global) |
| `scoring_futures` | Pesos das 5 layers, thresholds, multipliers | Sim |
| `leverage_checks_futures` | Funding guard, OI guard, liquidation guard | Sim |
| `execution_futures` | Entry strategies, SL/TP params, leverage caps | Sim |
| `management_futures` | Trailing, emergência, funding drain, parciais | Sim |
| `strategies_futures` | Lista de estratégias com params individuais | Sim |

Todos seguem o princípio ZERO HARDCODE: `config_service.get_config(user_id, pool_id, config_type)`.

Resolução: Pool override → User global → System defaults.

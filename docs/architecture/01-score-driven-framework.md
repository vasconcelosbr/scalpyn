# Scalpyn — Framework Adaptado: Score-Driven (Sem Grid)

## Mudança Arquitetural Fundamental

```
ANTES (Grid Bot):                    AGORA (Score-Driven):
┌─────────────────────┐              ┌─────────────────────────────────┐
│ Grid fixo por moeda  │              │ Scanner contínuo de ~100 moedas │
│ Compra em níveis     │              │ Score favorável + saldo USDT    │
│ Vende no TP do grid  │              │ → COMPRA (qualquer moeda)       │
│ 1 moeda por grid     │              │ → VENDE quando lucro atingido   │
│ Posições pré-definidas│             │ Múltiplas posições, múltiplas   │
│                      │              │ moedas, sem limite por ativo    │
└─────────────────────┘              └─────────────────────────────────┘

O bot é um CAÇADOR OPORTUNISTA:
  → Scanneia mercado continuamente
  → Identifica oportunidade (score ≥ threshold)
  → Tem saldo? → Executa
  → Não importa se é BTC, SOL, DOGE
  → Não importa se já tem posição aberta na mesma moeda
  → Cada compra é uma posição independente com entry_price próprio
```

---

## REGRAS ABSOLUTAS (Aplicam a Ambos os Perfis)

```
REGRA #1 — SCORE DRIVES EVERYTHING
  Nenhuma compra sem score ≥ threshold (GUI-configurável)
  Score é recalculado a cada ciclo do scanner
  Ranking determina prioridade quando há múltiplas oportunidades

REGRA #2 — SALDO USDT É GATE
  Sem saldo disponível → sem trade (independente do score)
  Saldo mínimo por trade configurável na GUI
  Capital reserva (% que nunca é usado) configurável

REGRA #3 — CADA POSIÇÃO É INDEPENDENTE
  Position ID único
  Entry price imutável
  Pode ter 5 posições na mesma moeda com entries diferentes
  P&L calculado individualmente por posição

REGRA #4 — ZERO HARDCODE
  Todo threshold, %, margem, limite → vem da config no DB → editável na GUI
```

---

# PERFIL 1: SPOT — Score-Driven, Never Sell at Loss

## Filosofia Central

```
╔══════════════════════════════════════════════════════════╗
║  SPOT: NUNCA VENDE EM PREJUÍZO. JAMAIS. SEM EXCEÇÃO.  ║
║                                                          ║
║  Se o preço caiu após a compra, a posição entra em       ║
║  HOLDING e permanece pelo tempo NECESSÁRIO até atingir   ║
║  o lucro mínimo configurado na GUI.                      ║
║                                                          ║
║  Pode ser horas, dias, semanas ou meses.                 ║
║  O capital fica travado até o lucro ser alcançado.        ║
╚══════════════════════════════════════════════════════════╝
```

## Fluxo Completo — SPOT

```
═══════════════════════════════════════════════════════════
CICLO DO SCANNER — SPOT (roda a cada scan_interval)
═══════════════════════════════════════════════════════════

  FASE 1: SCAN & RANK
    │
    │ Para cada moeda no universo (~100):
    │   → Calcular Alpha Score (pesos configuráveis)
    │   → Aplicar Block Rules (condições de bloqueio)
    │   → Rankear por score descendente
    │
    │ Resultado: lista ordenada de oportunidades
    │
  FASE 2: FILTROS PRÉ-COMPRA
    │
    │ Para cada moeda do ranking (top → bottom):
    │   ├─ Score ≥ buy_threshold?          → senão, skip
    │   ├─ Saldo USDT ≥ min_trade_size?   → senão, STOP (sem capital)
    │   ├─ Saldo USDT ≥ capital_per_trade? → senão, usar saldo restante
    │   ├─ Macro regime permite?           → senão, skip
    │   ├─ Liquidez OK? (volume, spread)   → senão, skip
    │   ├─ Max posições total atingido?    → senão, STOP
    │   ├─ Max exposure no ativo atingido? → senão, skip ativo
    │   └─ PASS → executar compra
    │
  FASE 3: EXECUÇÃO DA COMPRA
    │
    │ create_position(
    │   symbol, side="BUY", entry_price, quantity,
    │   score_at_entry, indicators_at_entry,
    │   status="ACTIVE", profile="spot"
    │ )
    │
    │ → Ordem de mercado (ou limit com timeout)
    │ → Entry price gravado (IMUTÁVEL)
    │ → Score + indicadores snapshot gravados
    │ → Notificação: "Compra executada: X @ $Y (Score: Z)"
    │
  FASE 4: MONITORAMENTO DE POSIÇÕES ATIVAS
    │
    │ Para cada posição ACTIVE no perfil spot:
    │   │
    │   │ lucro_pct = (price_atual - entry_price) / entry_price × 100
    │   │
    │   ├─ lucro_pct ≥ take_profit_pct?
    │   │     │
    │   │     ├─ AI Hold ativo? → trailing stop decide
    │   │     │
    │   │     ├─ Verificar se deve consultar IA
    │   │     │   (oportunidade de EXTEND detectada?)
    │   │     │     → EXTEND: ativa AI Hold + novo target
    │   │     │     → SELL: executa venda
    │   │     │
    │   │     └─ Sem AI, sem oportunidade → VENDE
    │   │           (lucro ≥ min, preço > entry → SAFE)
    │   │
    │   ├─ 0 < lucro_pct < take_profit_pct?
    │   │     → HOLD (lucro insuficiente, esperando target)
    │   │     → Checar se há sinais de EXHAUSTION/RANGING
    │   │       para venda antecipada (SÓ se lucro ≥ min_profit)
    │   │
    │   └─ lucro_pct ≤ 0?
    │         → ██ HOLDING OBRIGATÓRIO ██
    │         → NUNCA vende
    │         → Muda status para "HOLDING_UNDERWATER"
    │         → Monitora para possível DCA (se habilitado)
    │         → Log: "Posição underwater: X @ $Y, atual $Z (-W%)"
    │         → Alerta periódico se tempo em holding > threshold
    │
═══════════════════════════════════════════════════════════
```

## Lógica de Venda — SPOT (5 Camadas Adaptadas)

```
REGRA SUPREMA: Todas as camadas respeitam o piso absoluto:
  → lucro_pct ≥ min_profit_pct (GUI) E preço > entry_price
  → Se essa condição não é atendida → HOLD, período.

Posição ACTIVE (lucro > 0)
  │
  ├─ 1. RANGING? (mercado lateralizado, score composto)
  │     └─ Sim + lucro ≥ take_profit → VENDE (libera capital)
  │
  ├─ 2. EXHAUSTION? (tendência enfraquecendo)
  │     ├─ + Market Structure bearish break → VENDE se lucro ≥ min_profit
  │     └─ + Structure bullish intact → HOLD (é pullback)
  │
  ├─ 3. Oportunidade detectada + Consulta IA
  │     ├─ EXTEND → AI Hold com trailing
  │     └─ SELL → VENDE se lucro ≥ min_profit
  │
  ├─ 4. Target atingido (lucro ≥ take_profit)
  │     └─ Volatility filter OK? Liquidity OK? → VENDE
  │
  └─ 5. AI Hold ativo → Trailing Stop HWM
        └─ Stop atingido E lucro ≥ min_profit → VENDE

Posição HOLDING_UNDERWATER (lucro ≤ 0)
  │
  ├─ Preço recuperou acima de entry + min_profit?
  │     └─ Sim → muda status para ACTIVE → entra no fluxo acima
  │
  ├─ DCA habilitado?
  │     └─ Queda > dca_trigger_pct desde entry?
  │         + Score atual ≥ dca_min_score?
  │         + Saldo disponível?
  │         + Max DCA layers não atingido?
  │           → Compra adicional (média o preço de entry)
  │           → Novo entry_price = weighted average
  │           → Log: "DCA executado: nova média $X"
  │
  └─ Nenhuma das anteriores
        → HOLD indefinidamente
        → Alerta periódico ao usuário
```

## DCA Inteligente (Dollar Cost Average) — Opcional

```
O DCA é a ÚNICA forma de interagir com posições underwater no spot.
Não é automático por default — deve ser habilitado na GUI.

LÓGICA:
  if dca_enabled
     AND position.status == "HOLDING_UNDERWATER"
     AND (entry_price - current_price) / entry_price > dca_trigger_pct
     AND current_alpha_score >= dca_min_score
     AND dca_layers_used < max_dca_layers
     AND usdt_balance >= dca_amount:

    → Executar compra adicional
    → Recalcular entry_price médio ponderado:
      new_entry = (old_qty × old_entry + new_qty × new_price) / (old_qty + new_qty)
    → Incrementar dca_layers_used
    → Log: "DCA Layer {n}: +{qty} @ ${price}. New avg: ${new_entry}"

  PROTEÇÕES:
    → Cada DCA layer é menor que a anterior (reduz exposição)
      Layer 1: dca_amount × 1.0
      Layer 2: dca_amount × dca_decay_factor (ex: 0.7)
      Layer 3: dca_amount × dca_decay_factor² (ex: 0.49)
    → Max exposure total no ativo (inclui posição original + todos DCAs)
    → DCA nunca executa sem score mínimo (evita "catching falling knife")
```

## Gestão de Capital — SPOT

```
O desafio do modelo "never sell at loss" é o CAPITAL LOCK:
posições underwater travam capital que poderia gerar retorno em outro lugar.

SOLUÇÃO: Capital Allocation Engine

  total_capital = usdt_balance + sum(positions.value)

  // Capital livre para novos trades
  available = usdt_balance - capital_reserve

  // Capital travado em posições underwater
  locked_underwater = sum(p.value for p in positions if p.pnl < 0)

  // Métricas expostas na GUI:
  capital_utilization = sum(positions.value) / total_capital × 100
  underwater_ratio = locked_underwater / total_capital × 100
  opportunity_cost = estimated_return_if_capital_was_free

  // Alertas
  if underwater_ratio > underwater_alert_threshold:
    → Alerta: "X% do capital está travado em posições underwater"
    → Sugestão: "Considere habilitar DCA ou aumentar capital"

  if capital_utilization > max_capital_in_use_pct:
    → Bloqueia novas compras (mesmo com score alto)
    → Alerta: "Capital máximo em uso atingido"
```

## Config Scalpyn — SPOT

```json
{
  "config_type": "spot_engine",
  "config_json": {
    "scanner": {
      "scan_interval_seconds": 30,
      "universe_source": "dynamic",
      "buy_threshold_score": 75,
      "strong_buy_threshold": 85,
      "max_opportunities_per_scan": 3
    },
    "buying": {
      "capital_per_trade_pct": 10,
      "capital_per_trade_min_usdt": 20,
      "capital_reserve_pct": 10,
      "max_capital_in_use_pct": 80,
      "max_positions_total": 20,
      "max_positions_per_asset": 5,
      "max_exposure_per_asset_pct": 25,
      "order_type": "market",
      "limit_order_timeout_seconds": 120
    },
    "selling": {
      "take_profit_pct": 1.5,
      "min_profit_pct": 0.5,
      "never_sell_at_loss": true,
      "safety_margin_above_entry_pct": 0.3,
      "enable_ai_consultation": true,
      "ai_rate_limit_seconds": 60,
      "ai_model": "google/gemini-2.5-flash"
    },
    "holding_underwater": {
      "alert_after_hours": 24,
      "alert_repeat_interval_hours": 12,
      "track_opportunity_cost": true
    },
    "dca": {
      "enabled": false,
      "trigger_drop_pct": 5.0,
      "min_score_for_dca": 70,
      "max_dca_layers": 3,
      "dca_amount_usdt": 50,
      "dca_decay_factor": 0.7,
      "max_total_exposure_per_asset_pct": 30
    },
    "sell_flow": {
      "ranging": {
        "enabled": true,
        "detection_method": "composite_score",
        "adx_threshold": 18,
        "volume_decay_factor": 0.6,
        "rsi_neutral_range": 8,
        "min_score_to_sell": 60
      },
      "exhaustion": {
        "enabled": true,
        "require_structure_break": true,
        "htf_timeframe": "1h"
      },
      "volatility_filter": {
        "enabled": true,
        "block_sell_on_squeeze": true,
        "squeeze_percentile": 20,
        "squeeze_override_multiplier": 2.0
      },
      "trailing": {
        "method": "hwm_dynamic",
        "use_atr": true,
        "atr_period": 14,
        "atr_multiplier": 1.0,
        "margin_floor_pct": 0.4,
        "margin_ceiling_pct": 2.0,
        "tighten_above_profit_pct": 5.0,
        "tighten_factor": 0.7
      }
    },
    "macro_filter": {
      "enabled": true,
      "block_buys_on_strong_risk_off": true,
      "reduce_buys_on_risk_off_pct": 50,
      "btc_correlation_guard": true,
      "btc_dump_threshold_1h_pct": -3.0
    }
  }
}
```

---

# PERFIL 2: FUTUROS ALAVANCADO — Score-Driven + Anti-Liquidação

## Filosofia Central

```
╔═══════════════════════════════════════════════════════════╗
║  FUTUROS: ALAVANCAGEM AMPLIFICA TUDO.                    ║
║                                                           ║
║  Diferente do spot, aqui EXISTE stop loss e EXISTE perda. ║
║  A proteção é a MARGEM ANTI-LIQUIDAÇÃO:                   ║
║                                                           ║
║  → Stop loss SEMPRE ativa antes da liquidação             ║
║  → Distância mínima configurável entre stop e liquidação  ║
║  → Alavancagem é CALCULADA, nunca arbitrária              ║
║  → Se a math não fecha → trade não executa                ║
║                                                           ║
║  Pode operar LONG e SHORT.                                ║
║  Score-driven: mesma lógica de scanner do spot.           ║
╚═══════════════════════════════════════════════════════════╝
```

## Anti-Liquidation Protection System

```
═══════════════════════════════════════════════════════════
PROTEÇÃO ANTI-LIQUIDAÇÃO — 3 CAMADAS
═══════════════════════════════════════════════════════════

CAMADA 1: DESIGN — Alavancagem como consequência

  A alavancagem NUNCA é input. É output.

  Input: capital disponível, risk por trade (%), distância do stop
  Output: position size → leverage necessária

  Se a leverage necessária > max_leverage permitido:
    → Reduz position size até caber
    → Se ainda não cabe → NO TRADE

  Fórmula:
    risk_dollars = capital × max_risk_per_trade_pct / 100
    stop_distance = abs(entry - stop_loss)
    position_size = risk_dollars / stop_distance
    position_value = position_size × entry_price
    required_leverage = position_value / allocated_margin
    leverage = min(required_leverage, max_leverage_for_tier)


CAMADA 2: VALIDAÇÃO — Distância mínima stop↔liquidação

  Após calcular tudo:

    liq_price = calculate_liquidation_price(
      entry, leverage, direction, margin, maintenance_margin_rate
    )

    distance_stop_to_liq = abs(stop_loss - liq_price) / entry × 100

    if distance_stop_to_liq < min_stop_to_liq_distance_pct:
      → AJUSTE 1: Reduzir leverage
      → Recalcular até distance ≥ min
      → Se leverage cai abaixo de min_leverage → NO TRADE

    // Buffer adicional: stop nunca mais perto que X% da liquidação
    // Isso garante que MESMO com slippage, o stop executa antes da liq
    safety_buffer = liq_safety_buffer_pct  // ex: 3%

    if direction == LONG:
      max_stop_loss = liq_price × (1 + safety_buffer / 100)
      if stop_loss < max_stop_loss:
        stop_loss = max_stop_loss  // move stop para safe zone
        // Recalcular risk e size

    if direction == SHORT:
      min_stop_loss = liq_price × (1 - safety_buffer / 100)
      if stop_loss > min_stop_loss:
        stop_loss = min_stop_loss


CAMADA 3: RUNTIME — Monitoramento contínuo

  A cada ciclo (enquanto posição aberta):

    current_distance_to_liq = abs(current_price - liq_price) / current_price × 100

    ALERTAS:
    if current_distance_to_liq < alert_liq_distance_pct:
      → WARNING via Slack + Push
      → "Posição X a Y% da liquidação"

    if current_distance_to_liq < critical_liq_distance_pct:
      → CRITICAL: fechar posição IMEDIATAMENTE (market order)
      → "EMERGENCY EXIT: Liquidação iminente"
      → Prioridade máxima, ignora tudo

    if current_distance_to_liq < emergency_liq_distance_pct:
      → Já deveria ter sido fechada pelo stop
      → Se stop falhou (exchange lag): FORCE CLOSE
      → Alerta SMS/Call se configurado

  DIAGRAMA DE DISTÂNCIAS:

  LONG example (entry = $100, leverage = 10x):

    $110.00  ──── Take Profit (+10%)
    $100.00  ──── Entry Price
     $97.00  ──── Stop Loss (-3%)          ← risco controlado
     $94.00  ──── Alert Zone (-6%)         ← warning
     $92.00  ──── Critical Zone (-8%)      ← force close
     $90.00  ──── Liquidation Price (-10%) ← NUNCA chegar aqui

    Gap stop→liq = 3% (configurável: min_stop_to_liq_distance_pct)
    Safety buffer = o stop SEMPRE executa antes da liquidação

═══════════════════════════════════════════════════════════
```

## Fluxo Completo — FUTUROS

```
═══════════════════════════════════════════════════════════
CICLO DO SCANNER — FUTUROS (roda a cada scan_interval)
═══════════════════════════════════════════════════════════

  FASE 1: PRE-CHECKS
    │
    ├─ Portfolio risk check
    │   Daily loss limit? Circuit breaker? Max positions?
    │   FAIL → scanner fica idle até reset
    │
    └─ Macro regime check
        STRONG_RISK_OFF → só SHORT (ou idle)
        RISK_OFF → só SHORT permitido
        NEUTRAL → ambos, sizing -25%
        RISK_ON → ambos, LONG favorecido
        STRONG_RISK_ON → LONG full
    │
  FASE 2: SCAN & RANK (5-Layer)
    │
    │ Para cada moeda do universo:
    │   → L1 Liquidity    /20
    │   → L2 Structure     /20  (determina LONG ou SHORT)
    │   → L3 Momentum      /20
    │   → L4 Volatility    /20
    │   → L5 Order Flow    /20
    │   → TOTAL           /100
    │
    │ Filtrar:
    │   → Score ≥ min_score (70)
    │   → Nenhum layer < min_layer_score (8)
    │   → L1 ≥ 10 (hard rule)
    │   → Direção compatível com macro regime
    │
    │ Rankear por score descendente
    │
  FASE 3: LEVERAGE-SPECIFIC GATES
    │
    │ Para cada oportunidade (top → bottom):
    │   ├─ Funding rate adverso? → ajustar ou skip
    │   ├─ OI em extremo? → reduzir size ou skip
    │   ├─ Evento macro em < buffer_hours? → reduzir size
    │   └─ PASS → calcular execution params
    │
  FASE 4: POSITION SIZING + ANTI-LIQUIDAÇÃO
    │
    │ calculate_futures_trade(symbol, direction, score):
    │
    │   // 1. Stop loss (structure-based → ATR fallback)
    │   stop = find_stop_loss(direction, key_levels, atr)
    │
    │   // 2. Risk dollars
    │   risk_pct = get_risk_for_score(score)
    │   risk_dollars = capital × risk_pct / 100
    │
    │   // 3. Position size
    │   stop_distance = abs(entry - stop)
    │   position_units = risk_dollars / stop_distance
    │   position_value = position_units × entry
    │
    │   // 4. Leverage (CONSEQUÊNCIA, não escolha)
    │   margin = get_available_margin()
    │   leverage = position_value / margin
    │   leverage = min(leverage, max_leverage_for_tier)
    │
    │   // 5. ██ ANTI-LIQUIDAÇÃO ██
    │   liq_price = calc_liquidation(entry, leverage, direction)
    │   stop_to_liq = abs(stop - liq_price) / entry × 100
    │
    │   if stop_to_liq < min_stop_to_liq_distance:
    │     → Reduzir leverage iterativamente
    │     → Recalcular até safe
    │     → Se impossível → ██ NO TRADE ██
    │
    │   // 6. Take profits
    │   tp1 = entry ± (stop_distance × rr_tp1)
    │   tp2 = entry ± (stop_distance × rr_tp2)
    │   tp3 = entry ± (stop_distance × rr_tp3)
    │
    │   // 7. Validação final
    │   if all_checks_pass:
    │     → EXECUTE
    │
  FASE 5: EXECUÇÃO
    │
    │ create_position(
    │   symbol, direction, entry, stop, tp1, tp2, tp3,
    │   leverage, position_size, risk_dollars,
    │   liq_price, score, indicators, macro_regime,
    │   status="ACTIVE", profile="futures"
    │ )
    │
    │ → Limit order (ou market se urgent)
    │ → Set stop loss order na exchange
    │ → Set TP1 order na exchange
    │
  FASE 6: MANAGEMENT (posição ativa)
    │
    │ Loop contínuo:
    │
    │ ├─ 1. LIQUIDATION MONITOR (prioridade máxima)
    │ │     distance_to_liq < critical → FORCE CLOSE
    │ │     distance_to_liq < alert → WARNING
    │ │
    │ ├─ 2. STOP LOSS (sempre ativo, na exchange)
    │ │     Atingido → posição fechada com perda controlada
    │ │     → Log: "Stop hit: -X% (risk dollars: $Y)"
    │ │
    │ ├─ 3. TP1 ATINGIDO
    │ │     → Fechar tp1_exit_pct da posição
    │ │     → Mover stop para breakeven
    │ │     → Log: "TP1 hit: +X%. Closed Y%. Stop → BE"
    │ │
    │ ├─ 4. TP2 ATINGIDO
    │ │     → Fechar tp2_exit_pct do restante
    │ │     → Ativar trailing stop ATR no restante
    │ │     → Log: "TP2 hit: +X%. Trailing activated"
    │ │
    │ ├─ 5. TRAILING STOP ATR (após TP2)
    │ │     atr_trail = ATR × trailing_multiplier
    │ │     hwm = max(hwm, current_price) // para LONG
    │ │     trailing_stop = hwm - atr_trail
    │ │     trailing_stop = max(trailing_stop, breakeven)
    │ │     Atingido → fecha restante
    │ │
    │ ├─ 6. EMERGENCY CONDITIONS
    │ │     Macro → STRONG_RISK_OFF (e posição é LONG) → EXIT
    │ │     BTC flash crash (alts) → EXIT
    │ │     Funding rate explosion → EXIT
    │ │     Exchange down → attempt EXIT + alert
    │ │
    │ └─ 7. FUNDING DRAIN MONITOR
    │       Funding acumulado > X% do lucro → WARN
    │       Funding projetado insustentável → EXIT
    │
═══════════════════════════════════════════════════════════
```

## Max Loss por Trade — Futuros

```
SPOT:  max loss = 0% (nunca vende em prejuízo → holding infinito)
FUTUROS: max loss = max_risk_per_trade_pct do capital (1-2%)

  O stop loss GARANTE que a perda máxima por trade é controlada.
  A anti-liquidação GARANTE que o stop executa antes da liquidação.

  Cenários de perda em futuros:
  ┌─────────────────────────────────────────────────────┐
  │ Score 70-79 (VALID):  risk = 1.0% × 0.6 = 0.6%    │
  │ Score 80-89 (STRONG): risk = 1.0%                   │
  │ Score 90+   (CONV.):  risk = 2.0% (conviction only) │
  │                                                      │
  │ Daily loss limit: 3% → para de operar               │
  │ Weekly loss limit: 5% → reduz sizing 50%            │
  │ Circuit breaker: 3 losses seguidas → pausa 60min    │
  └─────────────────────────────────────────────────────┘

  NUNCA perder mais que max_risk em um único trade.
  NUNCA ser liquidado (stop executa antes).
  Se exchange falhar e liquidação ocorrer → alerta CRITICAL + post-mortem.
```

---

# COMPARATIVO COMPLETO: SPOT vs FUTUROS

## Decisão de Compra

| Aspecto | SPOT | FUTUROS |
|---------|------|---------|
| Trigger | Score ≥ buy_threshold | Score ≥ 70 (5-layer) |
| Direção | Só LONG | LONG e SHORT |
| Análise | Alpha Score (4 pesos) | 5-Layer Institutional (L1-L5) |
| Macro gate | Opcional (filtra, não bloqueia) | Obrigatório (bloqueia) |
| Liquidity check | Básico (volume, spread) | Full L1 (volume, spread, book, relative vol) |
| Sizing | % fixo do capital | Risk-based (stop distance → size → leverage) |
| Alavancagem | Nenhuma (1x) | Calculada (2x-20x) |
| Pode comprar mesma moeda? | Sim (posições independentes) | Sim (posições independentes) |
| Funding rate | Irrelevante | Gate (adverso → reduz ou bloqueia) |

## Decisão de Venda / Saída

| Aspecto | SPOT | FUTUROS |
|---------|------|---------|
| Vende em prejuízo? | **NUNCA** | Sim (stop loss controlado) |
| Max loss por trade | 0% (holding infinito) | 1-2% do capital |
| Stop loss | Não existe | Obrigatório (structure/ATR-based) |
| Take profit | % configurável na GUI | TP1/TP2/TP3 (R:R based) |
| Trailing stop | HWM dinâmico (após AI Hold) | ATR-based (após TP2) |
| Posição underwater | HOLD indefinidamente | Stop loss fecha com perda |
| DCA | Opcional (para recuperar underwater) | Não aplicável (stop fecha) |
| Anti-liquidação | N/A (sem alavancagem) | 3 camadas de proteção |
| Emergency exit | Não (nunca vende em loss) | Sim (macro, BTC crash, liq proximity) |

## Gestão de Capital

| Aspecto | SPOT | FUTUROS |
|---------|------|---------|
| Risco principal | Capital locked em holdings | Perda via stop loss |
| Capital reserve | % mínimo em USDT | % mínimo em margem |
| Max utilização | max_capital_in_use_pct | max_capital_deployed_pct |
| Correlated positions | Implícito (BTC guard) | Explícito (max 2 correlacionadas) |
| Daily loss limit | N/A (sem losses) | 3% → para de operar |
| Circuit breaker | N/A | 3 losses → pausa |

---

# DATA MODEL — Posições Unificadas

```sql
CREATE TABLE positions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           UUID NOT NULL REFERENCES users(id),
  pool_id           UUID REFERENCES pools(id),

  -- Identificação
  profile           VARCHAR(10) NOT NULL CHECK (profile IN ('spot', 'futures')),
  symbol            VARCHAR(20) NOT NULL,
  direction         VARCHAR(5) NOT NULL CHECK (direction IN ('LONG', 'SHORT')),

  -- Entry (imutável após criação, exceto DCA em spot)
  entry_price       DECIMAL(20,8) NOT NULL,
  quantity          DECIMAL(20,8) NOT NULL,
  entry_value_usdt  DECIMAL(20,2) NOT NULL,
  entry_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- Futures-specific
  leverage          DECIMAL(5,2),            -- NULL para spot
  margin_used       DECIMAL(20,2),           -- NULL para spot
  liquidation_price DECIMAL(20,8),           -- NULL para spot
  stop_loss_price   DECIMAL(20,8),           -- NULL para spot (never sells at loss)
  tp1_price         DECIMAL(20,8),
  tp2_price         DECIMAL(20,8),
  tp3_price         DECIMAL(20,8),

  -- Status
  status            VARCHAR(30) NOT NULL DEFAULT 'ACTIVE',
  -- SPOT:    ACTIVE, HOLDING_UNDERWATER, SELLING, CLOSED
  -- FUTURES: ACTIVE, TP1_HIT, TP2_HIT, TRAILING, STOPPED, CLOSED, LIQUIDATED

  -- Tracking
  score_at_entry    DECIMAL(5,2) NOT NULL,
  indicators_at_entry JSONB NOT NULL,
  macro_regime_at_entry VARCHAR(20),

  -- DCA tracking (spot only)
  dca_layers        INTEGER DEFAULT 0,
  original_entry_price DECIMAL(20,8),      -- antes do DCA
  total_invested    DECIMAL(20,2),

  -- AI Hold (spot only)
  ai_hold_mode      BOOLEAN DEFAULT FALSE,
  ai_target_pct     DECIMAL(5,2),
  ai_hwm_price      DECIMAL(20,8),
  ai_floor_price    DECIMAL(20,8),

  -- Trailing (futures: after TP2, spot: after AI Hold)
  trailing_active   BOOLEAN DEFAULT FALSE,
  trailing_hwm      DECIMAL(20,8),
  trailing_stop     DECIMAL(20,8),

  -- Exit
  exit_price        DECIMAL(20,8),
  exit_at           TIMESTAMPTZ,
  exit_reason       VARCHAR(50),
  -- SPOT:    TARGET_HIT, AI_TRAILING, EXHAUSTION_SELL, RANGING_SELL
  -- FUTURES: TP1, TP2, TP3, TRAILING, STOP_LOSS, EMERGENCY_MACRO,
  --          EMERGENCY_BTC, EMERGENCY_LIQ, EMERGENCY_FUNDING, LIQUIDATED

  realized_pnl      DECIMAL(20,2),
  realized_pnl_pct  DECIMAL(8,4),
  funding_cost_total DECIMAL(20,2) DEFAULT 0,  -- futures only

  -- Metadata
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índices para queries frequentes
CREATE INDEX idx_positions_user_status ON positions(user_id, status);
CREATE INDEX idx_positions_profile_status ON positions(profile, status);
CREATE INDEX idx_positions_symbol ON positions(symbol);
CREATE INDEX idx_positions_user_profile ON positions(user_id, profile, status);
```

---

# CONFIG SCHEMAS COMPLETAS

## Config: Spot Engine

```json
{
  "config_type": "spot_engine",
  "pool_overridable": true,
  "gui_page": "Settings → Spot Trading",
  "config_json": {
    "scanner": {
      "enabled": true,
      "scan_interval_seconds": 30,
      "buy_threshold_score": 75,
      "strong_buy_threshold": 85,
      "max_opportunities_per_scan": 3,
      "cooldown_after_buy_seconds": 60,
      "cooldown_per_symbol_seconds": 300
    },
    "buying": {
      "capital_per_trade_pct": 10,
      "capital_per_trade_min_usdt": 20,
      "capital_per_trade_max_usdt": 500,
      "capital_reserve_pct": 10,
      "max_capital_in_use_pct": 80,
      "max_positions_total": 20,
      "max_positions_per_asset": 5,
      "max_exposure_per_asset_pct": 25,
      "order_type": "market",
      "limit_timeout_seconds": 120,
      "max_slippage_pct": 0.15
    },
    "selling": {
      "take_profit_pct": 1.5,
      "min_profit_pct": 0.5,
      "never_sell_at_loss": true,
      "safety_margin_pct": 0.3,
      "ai_consultation_enabled": true,
      "ai_min_profit_to_consult_pct": 0.3,
      "ai_near_target_pct": 0.70,
      "ai_rate_limit_seconds": 60,
      "ai_model": "google/gemini-2.5-flash",
      "sell_flow_layers": {
        "ranging": { "enabled": true },
        "exhaustion": { "enabled": true },
        "ai_opportunity": { "enabled": true },
        "target_hit": { "enabled": true },
        "ai_trailing": { "enabled": true }
      }
    },
    "holding": {
      "never_sell_at_loss": true,
      "alert_after_hours_underwater": 24,
      "alert_repeat_hours": 12,
      "track_opportunity_cost": true,
      "show_recovery_estimate": true
    },
    "dca": {
      "enabled": false,
      "trigger_drop_pct": 5.0,
      "min_score_for_dca": 70,
      "max_layers": 3,
      "base_amount_usdt": 50,
      "decay_factor": 0.7,
      "max_total_exposure_per_asset_pct": 30,
      "require_macro_not_risk_off": true
    },
    "macro_filter": {
      "enabled": true,
      "block_buys_on_strong_risk_off": true,
      "reduce_size_on_risk_off_pct": 50,
      "btc_guard_enabled": true,
      "btc_dump_threshold_1h_pct": -3.0,
      "btc_guard_action": "reduce_targets"
    }
  }
}
```

## Config: Futures Engine

```json
{
  "config_type": "futures_engine",
  "pool_overridable": true,
  "gui_page": "Settings → Futures Trading",
  "config_json": {
    "scanner": {
      "enabled": true,
      "scan_interval_seconds": 30,
      "scoring_method": "5_layer_institutional",
      "min_score": 70,
      "min_layer_score": 8,
      "l1_hard_reject": 10,
      "max_opportunities_per_scan": 2,
      "cooldown_after_trade_seconds": 120,
      "cooldown_per_symbol_seconds": 600
    },
    "direction_logic": {
      "allow_long": true,
      "allow_short": true,
      "prefer_direction_from": "l2_structure",
      "macro_overrides_direction": true
    },
    "sizing": {
      "method": "risk_based",
      "max_risk_per_trade_pct": 1.0,
      "conviction_risk_pct": 2.0,
      "score_size_multipliers": {
        "institutional_grade_90plus": 1.5,
        "strong_80_89": 1.0,
        "valid_70_79": 0.6
      },
      "max_capital_deployed_pct": 60,
      "max_positions_total": 5,
      "max_positions_per_asset": 2,
      "max_correlated_positions": 2,
      "correlation_threshold": 0.7
    },
    "leverage": {
      "method": "calculated",
      "max_leverage_institutional": 10,
      "max_leverage_strong": 7,
      "max_leverage_valid": 4,
      "max_leverage_risk_off": 3,
      "min_leverage": 2
    },
    "anti_liquidation": {
      "min_stop_to_liq_distance_pct": 3.0,
      "liq_safety_buffer_pct": 3.0,
      "alert_liq_distance_pct": 8.0,
      "critical_liq_distance_pct": 5.0,
      "emergency_liq_distance_pct": 3.0,
      "force_close_on_critical": true,
      "recalculate_on_partial_close": true
    },
    "stop_loss": {
      "method_priority": ["structure", "liquidity_zone", "atr"],
      "atr_multiplier": 1.5,
      "max_stop_distance_pct": 5.0,
      "min_stop_distance_pct": 0.3,
      "move_to_breakeven_at": "tp1"
    },
    "take_profit": {
      "rr_tp1": 1.5,
      "rr_tp2": 2.5,
      "rr_tp3": 4.0,
      "tp1_exit_pct": 35,
      "tp2_exit_pct": 50,
      "tp3_method": "trailing",
      "squeeze_tp_multiplier": 1.3,
      "expanding_tp_multiplier": 0.85
    },
    "trailing": {
      "activate_after": "tp2",
      "method": "atr",
      "atr_multiplier": 1.0,
      "floor": "breakeven",
      "tighten_above_profit_pct": 5.0,
      "tighten_factor": 0.7
    },
    "funding_guard": {
      "enabled": true,
      "max_funding_for_long": 0.03,
      "min_funding_for_short": -0.03,
      "extreme_funding": 0.05,
      "funding_size_reduction_pct": 30,
      "max_funding_drain_pct_of_profit": 25,
      "max_daily_funding_cost_usdt": 50
    },
    "oi_guard": {
      "enabled": true,
      "extreme_percentile": 95,
      "size_reduction_pct": 30,
      "lookback_days": 30,
      "stop_tighten_pct": 20
    },
    "emergency": {
      "macro_shift_exit": true,
      "btc_crash_threshold_1h_pct": 4.0,
      "funding_emergency_rate": 0.08,
      "exchange_max_latency_ms": 5000,
      "force_close_all_on_exchange_down": false
    },
    "loss_limits": {
      "daily_loss_limit_pct": 3.0,
      "weekly_loss_limit_pct": 5.0,
      "weekly_loss_size_reduction": 0.50,
      "circuit_breaker_consecutive_losses": 3,
      "circuit_breaker_pause_minutes": 60
    },
    "macro_gate": {
      "enabled": true,
      "required": true,
      "update_interval_minutes": 30,
      "weights": {
        "btc_trend": 30,
        "dxy_direction": 20,
        "funding_market": 15,
        "liquidation_pressure": 15,
        "stablecoin_flow": 10,
        "vix": 10
      },
      "thresholds": {
        "strong_risk_on": 75,
        "risk_on": 55,
        "neutral": 40,
        "risk_off": 25
      },
      "neutral_size_reduction_pct": 25,
      "pre_event_buffer_hours": 4,
      "pre_event_size_reduction_pct": 50
    },
    "strategies": [
      {
        "id": "momentum_breakout",
        "enabled": true,
        "direction": "both",
        "min_score": 70,
        "leverage_tier": "normal"
      },
      {
        "id": "mean_reversion",
        "enabled": true,
        "direction": "both",
        "min_score": 80,
        "leverage_tier": "conservative"
      },
      {
        "id": "liquidity_sweep",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "normal"
      },
      {
        "id": "volatility_compression",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "high"
      },
      {
        "id": "funding_exploitation",
        "enabled": false,
        "direction": "both",
        "min_score": 70,
        "leverage_tier": "conservative"
      },
      {
        "id": "structure_shift",
        "enabled": true,
        "direction": "both",
        "min_score": 75,
        "leverage_tier": "normal"
      }
    ]
  }
}
```

---

# DIAGRAMA FINAL — VISÃO UNIFICADA

```
╔═══════════════════════════════════════════════════════════════════════╗
║                    SCALPYN — SCORE-DRIVEN ENGINE                     ║
╠═══════════════════════════════════════════════════════════════════════╣
║                                                                       ║
║  ┌─ SHARED: Market Data + Scanner ─────────────────────────────────┐ ║
║  │  ~100 moedas → Alpha Score + 5-Layer Score calculados           │ ║
║  │  Ranking atualizado a cada scan_interval                        │ ║
║  │  Macro regime atualizado a cada 30min                           │ ║
║  └─────────────────────────────────────────────────────────────────┘ ║
║                          │                                            ║
║            ┌─────────────┴─────────────┐                             ║
║            ▼                           ▼                             ║
║  ┌─ SPOT ENGINE ──────────┐  ┌─ FUTURES ENGINE ──────────────────┐ ║
║  │                        │  │                                    │ ║
║  │  COMPRA:               │  │  COMPRA:                          │ ║
║  │  Score ≥ 75 + saldo    │  │  5-Layer ≥ 70 + macro OK + saldo │ ║
║  │  → Market order        │  │  → Limit order (entry optimized)  │ ║
║  │  → Qualquer moeda      │  │  → LONG ou SHORT                 │ ║
║  │  → Posição independente│  │  → Leverage CALCULADA             │ ║
║  │                        │  │  → Anti-liq validado              │ ║
║  │  VENDA:                │  │                                    │ ║
║  │  ██ NUNCA EM LOSS ██   │  │  SAÍDA:                           │ ║
║  │  Lucro ≥ target → OK   │  │  Stop Loss (perda controlada)    │ ║
║  │  Lucro < 0 → HOLD      │  │  TP1 → parcial + stop→BE        │ ║
║  │  Lucro > 0 < TP → wait │  │  TP2 → parcial + trailing ATR   │ ║
║  │  DCA opcional           │  │  TP3 → trailing decide           │ ║
║  │                        │  │  Emergency exits (macro/liq/btc)  │ ║
║  │  Risco: capital locked │  │  Risco: 1-2% por trade           │ ║
║  │  Perda: 0% (holding)   │  │  Perda: max daily 3%             │ ║
║  │                        │  │                                    │ ║
║  │  ┌──────────────────┐  │  │  ┌────────────────────────────┐  │ ║
║  │  │ POSIÇÃO TYPES:   │  │  │  │ ANTI-LIQUIDATION:          │  │ ║
║  │  │ • ACTIVE         │  │  │  │ • Stop ANTES da liquidação │  │ ║
║  │  │ • HOLDING_UNDER  │  │  │  │ • Buffer de segurança 3%   │  │ ║
║  │  │ • CLOSED (profit)│  │  │  │ • Alert zone 8%            │  │ ║
║  │  └──────────────────┘  │  │  │ • Critical zone 5%         │  │ ║
║  │                        │  │  │ • Force close 3%           │  │ ║
║  └────────────────────────┘  │  │ • NUNCA ser liquidado      │  │ ║
║                              │  └────────────────────────────┘  │ ║
║                              └──────────────────────────────────┘ ║
║                                                                       ║
║  ┌─ SHARED: Notifications ─────────────────────────────────────────┐ ║
║  │  SPOT: compra, venda, holding alert, DCA exec, capital locked   │ ║
║  │  FUTURES: trade, TP hits, stop hit, liq warning, emergency,     │ ║
║  │           funding drain, daily P&L, macro shift                 │ ║
║  └─────────────────────────────────────────────────────────────────┘ ║
║                                                                       ║
╚═══════════════════════════════════════════════════════════════════════╝
```

---

# EDGE CASES & PROTEÇÕES

## Spot: Posição Underwater por Muito Tempo

```
CENÁRIO: Comprou SOL @ $180, preço caiu para $120. Holding há 30 dias.

AÇÕES AUTOMÁTICAS:
  1. Status: HOLDING_UNDERWATER
  2. Alertas periódicos (configurável)
  3. Métrica na GUI: "Capital travado: $X (Y% do portfolio)"
  4. Estimativa de recuperação: "Precisa subir Z% para atingir target"
  5. Se DCA habilitado + score OK → propõe DCA
  6. NUNCA vende automaticamente

MÉTRICAS EXPOSTAS NA GUI:
  ┌──────────────────────────────────────────┐
  │  Posições Underwater: 3                  │
  │  Capital Travado: $1,450 (14.5%)         │
  │  Maior Drawdown: SOL -33%               │
  │  Tempo Médio Underwater: 12 dias        │
  │  Custo de Oportunidade Est.: $87/mês    │
  │  Posições Ativas com Lucro: 8           │
  │  Capital Livre: $3,200                   │
  └──────────────────────────────────────────┘
```

## Futuros: Slippage no Stop Loss

```
CENÁRIO: Stop @ $97, mas executa @ $96.50 por slippage em crash.

PROTEÇÕES:
  1. Anti-liq buffer garante que mesmo com 3% de slippage,
     a liquidação NÃO é atingida
  2. Emergency monitor roda em paralelo — se stop não executou
     e preço continua caindo, force close via market order
  3. Post-trade: registrar slippage real vs esperado
  4. Se slippage médio > threshold → alerta para review
  5. Ajustar min_stop_to_liq_distance automaticamente
     baseado no slippage histórico do ativo
```

## Ambos: Múltiplas Posições na Mesma Moeda

```
CENÁRIO: 3 posições em ETH com entries diferentes

  Position 1: ETH @ $3,200 (ACTIVE, +5%)
  Position 2: ETH @ $3,500 (HOLDING_UNDERWATER, -3.5%)
  Position 3: ETH @ $3,350 (ACTIVE, +1.2%)

  REGRAS:
  - Cada uma é INDEPENDENTE
  - Position 1 pode vender (lucro ≥ target) enquanto Position 2 segue em holding
  - Exposure check: soma dos 3 ≤ max_exposure_per_asset_pct
  - Scanner pode propor 4ª compra se:
    - Exposure total ainda < max
    - Score está favorável
    - Saldo disponível

  GUI mostra:
  - Lista de posições agrupada por ativo
  - P&L individual e agregado por ativo
  - Average entry (informativo, não usado para decisão)
```

## Futuros: Long e Short Simultâneos

```
CENÁRIO: LONG ETH (structure bullish 1h) e SHORT ETH (scalp counter-trend 15m)

  PERMITIDO se:
  - São posições independentes com lógica diferente
  - Risk total (soma dos dois risks) ≤ max_total_risk
  - Cada um tem stop/TP próprio

  BLOQUEADO se:
  - hedge_protection habilitado → não permite long+short no mesmo ativo
  - Geralmente recomendado bloquear (simplifica risk management)

  Config: allow_hedge_same_asset: false (default)
```

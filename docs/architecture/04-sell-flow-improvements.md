# Grid Bot — Melhorias no Fluxo de Venda

## Diagnóstico: Gaps Identificados vs. Skills

Cruzamento do fluxo atual com 3 frameworks: **Institutional Trading AI** (5 camadas), **Scalpyn** (ZERO HARDCODE), **Global Macro Intelligence** (regime macro).

| # | Gap | Framework de Origem | Severidade |
|---|-----|-------------------|------------|
| 1 | Sem análise de volatilidade (ATR/Bollinger) nas decisões de saída | Institutional L4 | **ALTA** |
| 2 | Sem Market Structure (HH/HL, structure breaks) | Institutional L2 | **ALTA** |
| 3 | Sem contexto macro/regime de mercado | Global Macro | **ALTA** |
| 4 | Thresholds hardcoded (ADX<18, 12 candles, 0.3%, 0.5%) | Scalpyn ZERO HARDCODE | **CRÍTICA** |
| 5 | Sem Order Flow (delta volume, absorção, liquidações) | Institutional L5 | **MÉDIA** |
| 6 | Sem verificação de liquidez antes de vender | Institutional L1 | **MÉDIA** |
| 7 | Trailing stop com faixas fixas em vez de ATR-based | Institutional Risk Mgmt | **ALTA** |
| 8 | Single timeframe (só 15m) | Institutional MTF | **MÉDIA** |
| 9 | Sem correlação BTC (alt dumps em cascata) | Portfolio Mgmt | **MÉDIA** |
| 10 | RANGING detection simplista (só ADX) | Institutional L4 | **MÉDIA** |

---

## Melhoria 1: Volatility-Aware Exits (L4)

**Problema:** O fluxo ignora completamente volatilidade. Uma posição em EXHAUSTION durante um Bollinger squeeze pode estar prestes a explodir — vender aí é dinheiro na mesa.

**Implementação:**

```
NOVO: Antes de qualquer venda (exceto trailing stop), checar:

volatility_regime = classify_volatility(candles_15m)
  - SQUEEZE: BB width < percentil 20 dos últimos 100 candles → BLOQUEIA VENDA
  - EXPANDING: ATR subindo + BB alargando → permite venda normal
  - NORMAL: sem sinal forte → permite venda normal

Exceção ao bloqueio SQUEEZE:
  - Se lucro > 2x take_profit → vende mesmo em squeeze (protege lucro gordo)
  - Se posição aberta > max_hold_candles → vende (evita capital preso)
```

**Impacto:** Evita vender justo antes de breakouts. Nos seus dados, quantas vezes o bot vendeu em EXHAUSTION e o preço subiu 3%+ logo depois? Provavelmente muitas — esse é o cenário clássico de squeeze→breakout.

**Config Scalpyn (ZERO HARDCODE):**

```json
{
  "volatility_exit_filter": {
    "enabled": true,
    "bb_squeeze_percentile": 20,
    "bb_lookback_candles": 100,
    "squeeze_override_multiplier": 2.0,
    "max_hold_candles_in_squeeze": 48
  }
}
```

---

## Melhoria 2: Market Structure Classification (L2)

**Problema:** O `calculateTrendStatus` usa ADX/DI/RSI/EMA21, que são indicadores de momentum (L3). Falta a análise estrutural real — HH/HL, structure breaks, suportes/resistências.

**Implementação:**

```
NOVO: Adicionar structure_status ao fluxo:

structure = analyze_structure(candles_1h)  // timeframe superior
  - BULLISH_INTACT: HH + HL mantidos, acima de EMA200
  - BEARISH_BREAK: Lower High confirmado OU break abaixo do último HL
  - STRUCTURE_SHIFT: Primeira quebra de sequência (MSS)

Uso no fluxo:
  - EXHAUSTION + BEARISH_BREAK → venda URGENTE (confirma reversão)
  - EXHAUSTION + BULLISH_INTACT → HOLD (pullback dentro de tendência)
  - TREND_STRONG + STRUCTURE_SHIFT → alerta, apertar trailing
```

**Impacto:** Diferencia pullback saudável de reversão real. Hoje o bot trata todo "ADX caindo" igual — mas ADX cai tanto em pullbacks quanto em reversões. A estrutura é quem diferencia.

**Config Scalpyn:**

```json
{
  "market_structure": {
    "enabled": true,
    "htf_timeframe": "1h",
    "swing_lookback": 20,
    "ema_long_period": 200,
    "require_structure_break_for_exhaustion_sell": true
  }
}
```

---

## Melhoria 3: Macro Regime Filter (Global Macro)

**Problema:** O bot opera no vácuo. Um TREND_STRONG em alt durante risk-off global (DXY subindo, BTC caindo, funding negativo) é armadilha.

**Implementação:**

```
NOVO: Regime macro como filtro global (atualizado a cada 1h):

macro_regime = get_macro_regime()
  - RISK_ON: BTC acima de EMA21 diário + funding positivo + DXY caindo
  - RISK_OFF: BTC abaixo de EMA21 diário + funding negativo OU DXY subindo forte
  - NEUTRAL: sinais mistos

Impacto no fluxo:
  - RISK_OFF + qualquer sinal de venda → executa com urgência (reduz target em 30%)
  - RISK_OFF + TREND_STRONG → ignora TREND_STRONG, trata como UNKNOWN
  - RISK_ON + EXHAUSTION → aumenta tolerância (EXHAUSTION pode ser pullback)
  - AI consulta recebe macro_regime como contexto adicional
```

**Impacto:** Evita o cenário "o bot segurou posição esperando target enquanto BTC caiu 5% e arrastou todas as alts". Macro regime é o fator #1 que distingue fundos profissionais de bots retail.

**Config Scalpyn:**

```json
{
  "macro_regime": {
    "enabled": true,
    "btc_ema_period": 21,
    "btc_timeframe": "1d",
    "risk_off_target_reduction": 0.30,
    "funding_threshold_negative": -0.01,
    "dxy_lookback_hours": 24,
    "update_interval_minutes": 60
  }
}
```

---

## Melhoria 4: ZERO HARDCODE — Migrar Todos os Thresholds

**Problema CRÍTICO:** O fluxo viola o princípio fundamental do Scalpyn. Valores como ADX<18, 12 candles, 0.3%, 0.5%, 70% do target estão fixos no código.

**Mapeamento completo de valores a extrair:**

| Valor Atual | Contexto | Config Key |
|-------------|----------|------------|
| ADX < 18 | RANGING detection | `ranging.adx_threshold` |
| 12 candles | RANGING persistence | `ranging.min_candles` |
| ADX > 25 | TREND_STRONG | `trend.adx_strong_threshold` |
| ADX > 40 | RSI range expansion | `trend.adx_very_strong_threshold` |
| RSI 55-70 | TREND_STRONG range | `trend.rsi_min` / `trend.rsi_max` |
| RSI 55-85 | Extended range | `trend.rsi_max_extended` |
| ADX > 20 | EXHAUSTION floor | `exhaustion.adx_min` |
| 0.3% lucro | AI trigger floor | `ai.min_profit_to_consult` |
| 70% do target | NEAR_TARGET / SELL floor | `ai.near_target_percent` |
| 1 min rate limit | AI rate limit | `ai.rate_limit_seconds` |
| 0.5% margem segurança | Safety margin | `safety.min_margin_percent` |
| 1.5% / 0.5% margem | Trailing tiers | `trailing.margin_tiers` (array) |

**Estrutura de config proposta:**

```json
{
  "sell_flow": {
    "ranging": {
      "enabled": true,
      "adx_threshold": 18,
      "min_candles": 12,
      "timeframe": "15m"
    },
    "trend": {
      "adx_strong_threshold": 25,
      "adx_very_strong_threshold": 40,
      "rsi_min": 55,
      "rsi_max": 70,
      "rsi_max_extended": 85,
      "ema_period": 21
    },
    "exhaustion": {
      "adx_min": 20,
      "require_structure_break": false
    },
    "ai_consultation": {
      "enabled": true,
      "min_profit_to_consult": 0.3,
      "near_target_percent": 0.70,
      "rate_limit_seconds": 60,
      "model": "google/gemini-2.5-flash"
    },
    "safety": {
      "min_margin_percent": 0.5,
      "max_divergence_percent": 5.0,
      "min_profit_for_trailing_exit": 0.5
    },
    "trailing": {
      "use_atr": false,
      "margin_tiers": [
        { "profit_below": 1.5, "margin": 0.5 },
        { "profit_below": 3.0, "margin": 0.7 },
        { "profit_below": 5.0, "margin": 1.0 },
        { "profit_below": 8.0, "margin": 1.2 },
        { "profit_above": 8.0, "margin": 1.5 }
      ]
    }
  }
}
```

---

## Melhoria 5: Trailing Stop ATR-Based (Institutional Risk Mgmt)

**Problema:** O trailing stop usa faixas fixas de margem. O framework institucional recomenda ATR-based — "NEVER use arbitrary stops. Always base on structure, liquidity, or ATR."

**Implementação:**

```
ATUAL:
  margin = lookup_table(lucro_percent)  // 0.5% a 1.5% fixo

PROPOSTO (quando trailing.use_atr = true):
  atr = ATR(14, timeframe_15m)
  atr_percent = (atr / current_price) * 100

  // ATR-based com floor e ceiling configuráveis
  dynamic_margin = clamp(
    atr_percent * trailing.atr_multiplier,  // ex: 1.0x ATR
    trailing.margin_floor,                   // ex: 0.4%
    trailing.margin_ceiling                  // ex: 2.0%
  )

  // Scaling com lucro (mantém lógica existente mas sobre o ATR)
  if lucro > trailing.tighten_above_profit:
    dynamic_margin *= trailing.tighten_factor  // ex: 0.7 → aperta 30%

  trailing_stop = max(HWM * (1 - dynamic_margin/100), piso_absoluto)
```

**Vantagem:** Em moedas voláteis (DOGE, SHIB), ATR alto → margem maior → menos stop prematuro. Em moedas estáveis (BTC, ETH), ATR baixo → margem apertada → captura mais lucro.

**Config Scalpyn:**

```json
{
  "trailing": {
    "use_atr": true,
    "atr_period": 14,
    "atr_multiplier": 1.0,
    "margin_floor": 0.4,
    "margin_ceiling": 2.0,
    "tighten_above_profit": 5.0,
    "tighten_factor": 0.7
  }
}
```

---

## Melhoria 6: Liquidity Check Antes de Vender (L1)

**Problema:** O bot pode tentar vender em momento de baixa liquidez, sofrendo slippage que come o lucro.

**Implementação:**

```
NOVO: Pre-sell liquidity check (antes de executeMarketOrder):

liquidity = check_sell_liquidity(symbol)
  - spread_percent = (ask - bid) / mid_price * 100
  - relative_volume = volume_5m / avg_volume_5m_20periods

  if spread_percent > max_spread_for_sell:
    DELAY venda (retry em 1 min, max 5 retries)
    log: "Spread alto (X%), aguardando liquidez"

  if relative_volume < min_relative_volume:
    DELAY venda (condição de mercado morto)

  Exceção: se lucro estiver CAINDO (2 checks consecutivos com queda) → vende mesmo com spread alto
```

**Config Scalpyn:**

```json
{
  "liquidity_check": {
    "enabled": true,
    "max_spread_percent": 0.15,
    "min_relative_volume": 0.3,
    "max_retries": 5,
    "retry_interval_seconds": 60,
    "force_sell_on_declining_profit": true
  }
}
```

---

## Melhoria 7: BTC Correlation Guard (Portfolio Mgmt)

**Problema:** Alts são altamente correlacionadas com BTC. O framework institucional limita "maximum correlated positions: 2" e recomenda "If correlation spike detected: hedge or reduce redundant positions."

**Implementação:**

```
NOVO: BTC correlation check (roda a cada ciclo):

btc_status = get_btc_status()
  - btc_below_ema21_1h: bool
  - btc_rsi_15m: float
  - btc_price_change_1h: float
  - btc_volume_spike_sell: bool (volume vendedor > 2x média)

if btc_dump_detected(btc_status):
  Para TODAS as posições abertas:
    - Se lucro > 0: reduz target em 50%, ativa trailing imediato
    - Se lucro < 0 e < -safety_margin: vende imediatamente (cut loss)
    - Log: "BTC DUMP GUARD ativado"

btc_dump = btc_price_change_1h < -2%
         AND btc_volume_spike_sell
         AND btc_below_ema21_1h
```

**Impacto:** Proteção contra o cenário mais comum de perda em grid bots de alts: BTC cai, alt cai 2-3x mais rápido, bot ainda esperando target.

**Config Scalpyn:**

```json
{
  "btc_correlation_guard": {
    "enabled": true,
    "btc_dump_threshold_1h": -2.0,
    "require_volume_spike": true,
    "require_below_ema": true,
    "profit_target_reduction": 0.50,
    "force_sell_loss_threshold": -1.0
  }
}
```

---

## Melhoria 8: RANGING Detection Aprimorado (L4 + L3)

**Problema:** RANGING é detectado só por ADX<18 por 12 candles. Isso pega range genuíno mas também pega consolidação pré-breakout (que é oportunidade, não problema).

**Implementação:**

```
ATUAL:
  ranging = ADX < 18 por 12+ candles

PROPOSTO:
  ranging_score = 0

  if ADX < adx_threshold por min_candles:
    ranging_score += 40

  if BB_width < percentil(bb_squeeze_percentile, 100 candles):
    ranging_score -= 30  // squeeze = NÃO é ranging morto, é acumulação

  if volume_avg_12 < volume_avg_48 * 0.6:
    ranging_score += 30  // volume secando confirma mercado morto

  if abs(RSI - 50) < 8:
    ranging_score += 20  // RSI neutro confirma indecisão

  if MACD_histogram próximo de zero (abs < threshold):
    ranging_score += 10  // sem momentum algum

  is_dead_range = ranging_score >= 60
```

**Diferença chave:** Separa "mercado morto sem interesse" (vende para liberar capital) de "acumulação em range apertado" (segura para capturar breakout).

---

## Melhoria 9: Contexto Macro para a IA (Camada 3)

**Problema:** A consulta à IA recebe só dados técnicos locais. O framework Global Macro diz: "never assume crypto moves in isolation from macro."

**Implementação:**

```
ATUAL: prompt para IA contém indicadores técnicos da moeda

PROPOSTO: adicionar ao prompt:

macro_context = {
  "btc_trend": "bullish/bearish/neutral",
  "btc_dominance_direction": "rising/falling",
  "market_regime": "risk_on/risk_off/neutral",
  "funding_rate_market_avg": float,
  "total_liquidations_24h": float,
  "dxy_direction": "rising/falling/flat"  // se disponível
}

// A IA decide EXTEND/SELL com contexto completo
// Ex: EXTEND faz mais sentido em risk_on + BTC bullish
// Ex: SELL faz mais sentido em risk_off mesmo com técnico bom
```

---

## Melhoria 10: Multi-Timeframe Confirmation (HTF Filter)

**Problema:** Tudo roda em 15m. O framework institucional usa múltiplos timeframes — "conflicting signals between timeframes" reduz o score.

**Implementação:**

```
NOVO: HTF filter antes de decisões de hold/extend:

htf_aligned = check_htf_alignment(symbol)
  - trend_1h: EMA21 slope + price position
  - trend_4h: EMA21 slope + price position (opcional)

Uso:
  - TREND_STRONG (15m) + HTF bearish → downgrade para UNKNOWN
  - AI EXTEND + HTF bearish → rejeita EXTEND, procede para venda
  - EXHAUSTION (15m) + HTF bullish → adiciona tolerância (pode ser pullback)
```

**Config Scalpyn:**

```json
{
  "multi_timeframe": {
    "enabled": true,
    "htf_timeframe": "1h",
    "htf_ema_period": 21,
    "reject_extend_on_htf_bearish": true,
    "downgrade_strong_on_htf_bearish": true
  }
}
```

---

## Fluxo Revisado — Diagrama Completo

```
Posição BOUGHT
  │
  ├─ 0. PRE-FILTERS (novos)
  │     ├─ Macro Regime → ajusta targets e tolerâncias
  │     ├─ BTC Correlation Guard → dump? → emergência
  │     └─ Volatility Regime → squeeze? → bloqueia vendas (exceto emergência)
  │
  ├─ 1. RANGING? (ADX + Volume + RSI + BB — scoring, não binário)
  │     └─ Score ≥ 60 + lucro ≥ TP → VENDE
  │
  ├─ 2. Market Structure + Trend Status
  │     ├─ EXHAUSTION + BEARISH_BREAK → VENDE urgente
  │     ├─ EXHAUSTION + BULLISH_INTACT → HOLD (pullback)
  │     ├─ TREND_STRONG + HTF aligned → HOLD
  │     └─ TREND_STRONG + HTF divergente → downgrade → próxima camada
  │
  ├─ 3. Oportunidade + Consulta IA (com contexto macro)
  │     ├─ EXTEND + HTF bullish + risk_on → ativa AI Hold
  │     ├─ EXTEND + HTF bearish → rejeita → SELL
  │     └─ SELL → prossegue
  │
  ├─ 4. Target atingido?
  │     └─ Sim + liquidity check OK + preço > entry×(1+safety) → VENDE
  │
  └─ 5. AI Hold — Trailing Stop ATR-Based
        └─ ATR-dynamic margin + HWM → stop atingido + lucro ≥ min → VENDE
```

---

## Prioridade de Implementação

| Fase | Melhorias | Esforço | Impacto |
|------|-----------|---------|---------|
| **1 — Quick Wins** | #4 ZERO HARDCODE, #7 BTC Guard | Baixo | Alto |
| **2 — Core Upgrades** | #1 Volatility, #2 Market Structure, #5 ATR Trailing | Médio | Alto |
| **3 — Intelligence** | #3 Macro Regime, #9 Contexto IA, #10 MTF | Médio | Alto |
| **4 — Polish** | #6 Liquidity Check, #8 RANGING v2 | Baixo | Médio |

A Fase 1 é crítica: o ZERO HARDCODE é pré-requisito para tudo (sem ele, cada melhoria vira mais código hardcoded). O BTC Guard é o maior protetor de capital que falta.

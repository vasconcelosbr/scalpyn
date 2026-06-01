# PROMPT: Reorganização Arquitetural — ML como Scorer da Watchlist Completa (SPOT)
# Data: 2026-06-01

MUDANÇA ESTRUTURAL MAIOR. Implementa a arquitetura nova validada por auditoria:

```
Pool → hard filter ESTRUTURAL → L1 (pass-through) → captura features de TODOS
     → shadow de TODOS → ML treina espectro completo → ML score
     → L2 (reservada) → L3 (score + block rules) → trades REAIS
```

## DECISÕES DO OPERADOR (confirmadas)
- Implementar a arquitetura completa (todos os componentes)
- Incluir janela deslizante 30d (regime drift) neste prompt
- Alvo do ML: AGNÓSTICO (binário OU regressão via config) — decidir depois do teste de separabilidade do espectro completo
- ESCOPO: APENAS SPOT. Futures (L1Futuro) fica para fase posterior.

## REALIDADE FÍSICA QUE GOVERNA A ATIVAÇÃO
O ML scorer não pode treinar sobre o espectro completo até o shadow de toda a watchlist acumular dados. Portanto: **CONSTRUIR tudo agora, mas ATIVAR em ordem via flags.** O ML scorer só assume quando houver dataset. Até lá, o sistema atual (L3 com filtros) continua operando normalmente.

## REGRAS ABSOLUTAS
- ADDITIVE ONLY — não remover o pipeline atual; adicionar o novo em paralelo
- ZERO HARDCODE (tudo em config_profiles)
- Hard filter do Pool: ESTRUTURAL apenas (liquidez/spread/dados), NUNCA qualidade de setup (RSI/ADX/momentum) — não vazar sinal
- Ler código antes de alterar; informar arquivo:linha
- Aguardar confirmação entre blocos
- Sistema opera com capital real — mudanças não podem interromper execução

---

## IDs CANÔNICOS E ESCOPO

**WATCHLIST L1 SPOT** (fonte canônica do shadow do espectro completo):
- ID: `9d7a9f34-45fd-44c3-97b2-4f4af2fe9d29` ("L1")
- L1 hoje é **pass-through**: sem filtro, bloqueio ou pontuação.

**NÃO incluir nesta fase:**
- L1Futuro (`394b28e6-b867-4286-9f5e-621c8c545335`) — mercado de futuros, dinâmica distinta (leverage/funding/liquidação). Fase separada após spot validado.

**PREPARAÇÃO PARA FUTURES (sem implementar agora):**
- Usar `source='WATCHLIST_SPOT'` (não genérico `WATCHLIST`), para que futures entre depois como `WATCHLIST_FUT` e os datasets fiquem naturalmente separados. Misturar spot e futuros confundiria o ML (outcomes têm causas diferentes).

---

## PRINCÍPIO DE ATIVAÇÃO EM CAMADAS

Flags em config controlam a transição gradual (sem big-bang em produção):

```
new_arch_capture_enabled    → liga captura+shadow de toda watchlist
new_arch_ml_scorer_enabled  → liga ML como scorer (só após dados)
new_arch_l3_uses_ml_score   → L3 passa a usar o score novo
```

**ESTADO INICIAL (deploy):**
```
capture=true, ml_scorer=false, l3_uses=false
→ começa a ACUMULAR dataset do espectro completo
→ sistema atual continua decidindo (nada muda na execução)
```

**TRANSIÇÃO (semanas depois, após validar separabilidade):**
```
ml_scorer=true → ML treina sobre espectro completo
l3_uses=true   → L3 passa a consumir o score novo
```

---

## BLOCO A — HARD FILTER ESTRUTURAL NO POOL

### A.1 Localizar a origem do Pool
```bash
grep -rn "pool\|Pool\|pool_coins\|stage_0\|stage0" \
  backend/app/ --include="*.py" | grep -v "__pycache__"
```

### A.2 Implementar filtro ESTRUTURAL (ZERO HARDCODE)
CRÍTICO: só critérios de OPERABILIDADE, nunca de sinal.

**Critérios PERMITIDOS (estruturais):**
- volume_24h_usdt >= min_volume (liquidez)
- spread_pct <= max_spread (custo de transação)
- orderbook_depth_usdt >= min_depth (profundidade)
- tem histórico mínimo de candles (dados disponíveis)
- não está deslistada/suspensa (operável)

**PROIBIDO (qualidade de setup — vazaria sinal para o ML):**
- RSI/ADX em faixa, momentum, variação 24h, score
- Regra: se o critério usa uma feature do ML, NÃO entra no hard filter.

```python
def apply_structural_pool_filter(coins, config):
    f = config.get("pool_structural_filter", {})
    min_vol     = f.get("min_volume_24h_usdt")
    max_sprd    = f.get("max_spread_pct")
    min_depth   = f.get("min_orderbook_depth_usdt")
    min_candles = f.get("min_candle_history")

    kept, removed = [], []
    for c in coins:
        reasons = []
        if c.volume_24h_usdt < min_vol:   reasons.append("low_volume")
        if c.spread_pct > max_sprd:        reasons.append("high_spread")
        if c.orderbook_depth < min_depth:  reasons.append("low_depth")
        if c.candle_count < min_candles:   reasons.append("insufficient_history")
        if reasons:
            removed.append((c.symbol, reasons))
        else:
            kept.append(c)

    logger.info("POOL_STRUCTURAL_FILTER|input=%d|kept=%d|removed=%d",
                len(coins), len(kept), len(removed))
    return kept
```

### A.3 Config
```sql
UPDATE config_profiles
SET config_data = config_data || '{
    "pool_structural_filter": {
        "min_volume_24h_usdt": 1000000,
        "max_spread_pct": 0.5,
        "min_orderbook_depth_usdt": 50000,
        "min_candle_history": 200
    }
}'::jsonb
WHERE config_type = 'pool_config';
-- Valores iniciais — ajustar conforme o universo real do Gate.io
```

---

## BLOCO B — SHADOW DE TODA A WATCHLIST (captura do espectro)

Estender o Shadow Portfolio: hoje cobre aprovados+rejeitados L3. Novo: cobre TODA a watchlist L1 spot (espectro completo). **Shadow é append-only — nunca apaga dados.**

### B.1 Localizar onde shadows são criados hoje
```bash
grep -rn "create_shadow\|bulk_backfill_shadows\|_create_from_decision\|SHADOW_SOURCE" \
  backend/app/services/shadow_trade_service.py \
  --include="*.py" | grep -v "__pycache__"
```

### B.2 Novo source para watchlist completa (spot)
```python
SHADOW_SOURCE_WATCHLIST_SPOT = "WATCHLIST_SPOT"
# _VALID_SHADOW_SOURCES += ("WATCHLIST_SPOT",)
# WATCHLIST_FUT reservado para a fase futures (NÃO criar agora)
# ADITIVO aos sources existentes (L3, L3_REJECTED)
```

### B.3 Task que gera shadow de toda a watchlist L1 spot
Gated por `new_arch_capture_enabled`.

```python
async def create_watchlist_shadows(db, config):
    if not config.get("new_arch_capture_enabled", False):
        return
    # 1. Pegar o universo da watchlist L1 SPOT.
    #    ID via config (ZERO HARDCODE):
    l1_id = config.get("shadow_watchlist_l1_spot_id")
    #    L1 é pass-through (sem filtros) — universo aqui = Pool pós-hard-filter.
    # 2. Para CADA símbolo, capturar features_snapshot NO MOMENTO
    #    (mesmo formato flat de hoje, com macro viva do MDH consertado).
    # 3. Criar shadow_trade source='WATCHLIST_SPOT' com:
    #    - entrada no próximo candle open
    #    - MESMO TP/SL/timeout do trade real (config — fidelidade)
    #    - decision_id ligando ao snapshot
    # 4. shadow_trade_monitor acompanha candle-a-candle → outcome
    logger.info("WATCHLIST_SHADOWS|created=%d", n)
```

**ATENÇÃO fidelidade:** o shadow DEVE usar os mesmos TP/SL/timeout que o trade real usaria, senão o ML aprende um outcome que não corresponde ao que o L3 vai executar.

**CRÍTICO — ponto de captura:** a captura do shadow DEVE permanecer na L1, mesmo que a L2 receba filtros no futuro. O shadow vem ANTES de qualquer filtro de qualidade, sempre.

### B.4 Config do ID (ZERO HARDCODE)
```sql
UPDATE config_profiles
SET config_data = config_data || '{
    "shadow_watchlist_l1_spot_id": "9d7a9f34-45fd-44c3-97b2-4f4af2fe9d29"
}'::jsonb
WHERE config_type = 'pool_config';
```

### B.5 Captura de features no ponto certo (L1, não L3)
As features do snapshot WATCHLIST_SPOT devem ser as disponíveis na L1, não as calculadas só no L3. Confirmar que os indicadores existem nesse ponto do funil.

### B.6 Validação
```sql
SELECT source, COUNT(*), MAX(created_at)
FROM shadow_trades WHERE created_at >= NOW() - INTERVAL '1 hour'
GROUP BY source;
-- source='WATCHLIST_SPOT' deve aparecer e crescer; L3/L3_REJECTED intactos
```
Confirmar carga controlada (hard filter funcionou): WATCHLIST_SPOT count deve ser da ordem da watchlist enxuta (~80-120), não do pool bruto (~500).

---

## BLOCO C — ML SCORER AGNÓSTICO AO ALVO + JANELA DESLIZANTE

### C.1 Trainer lê do espectro completo (gated)
```python
source_filter = ("WATCHLIST_SPOT" if config.get("new_arch_ml_scorer_enabled")
                 else "L3")   # fallback ao comportamento atual
# WHERE source = :source_filter
```

### C.2 JANELA DESLIZANTE 30d (regime drift)
SUBSTITUIR a janela de 90d por janela deslizante configurável:
```python
lookback_days = config.get("ml_training_lookback_days", 30)
# WHERE created_at >= NOW() - INTERVAL ':lookback_days days'
```
- Split temporal DENTRO da janela (treina no início, testa no fim) — manter o `temporal_split` já implementado.
- Monitorar PSI: se PSI das features-chave > 0.25 entre treino e teste, logar `REGIME_DRIFT_WARNING` (o modelo pode não generalizar).

### C.3 ALVO AGNÓSTICO (binário OU regressão via config)
Permitir trocar o objetivo SEM reescrever código:
```python
ml_target = config.get("ml_target_type", "binary")  # "binary" | "regression"

if ml_target == "binary":
    y = compute_is_win_fast(rows, config)   # 0/1
    objective = "binary:logistic"
    eval_metric = "auc"
elif ml_target == "regression":
    y = rows["pnl_pct"]                       # contínuo
    objective = "reg:squarederror"
    eval_metric = "rmse"
# XGBoost params adaptam ao objective escolhido
# Score de saída: probabilidade (binary) ou EV previsto (regression)
```
DECISÃO DO ALVO fica para depois do teste de separabilidade — o código suporta ambos; só muda a flag.

### C.4 Filtro dinâmico de features (manter do prompt anterior)
`filter_trainable_features()` — exclui macro vazia (coverage < 30% ou std=0), com LOG obrigatório. Macro viva (MDH consertado) entra sozinha quando coverage subir. **Não mexer na captura — só no que entra no treino.**

### C.5 Validação (quando ml_scorer_enabled=true)
- Dataset do espectro completo: WIN e LOSS reais (não 76% WIN)
- Rodar a auditoria de separabilidade SOBRE este dataset novo:
  - se AUC honesto > 0.6 → a arquitetura nova PROVOU ter sinal
  - se AUC ~0.52 mesmo aqui → o problema é das features, não do funil

---

## BLOCO D — L3 CONSOME O SCORE NOVO (gated)

### D.1 L3 usa ml_score quando habilitado
Quando `new_arch_l3_uses_ml_score=true`:
- L3 recebe o score do ML scorer + aplica block rules
- block rules continuam (Spike, Spread) — seleção operacional
- trades REAIS saem daqui

Quando false: L3 opera como hoje (não quebra nada).

### D.2 Combined score (se aplicável)
Se mantiver o combined_score: `(alpha_score × ws) + (ml_score × wm)`. Pesos em config. Com ML scorer validado, ajustar wm conforme AUC.

---

## BLOCO E — SALVAGUARDAS

### E.1 Captura do decisions_log intacta (invariante de sempre)
Confirmar que `decisions_log.metrics` continua gravando (alimenta features_snapshot — não desligar).

### E.2 Pipeline atual intacto enquanto flags=false
Com `new_arch_*=false`, o sistema deve operar EXATAMENTE como hoje. Testar: deploy com todas as flags false → nenhuma mudança de comportamento.

### E.3 Carga monitorada
Shadow de toda watchlist aumenta carga do monitor. Confirmar que o hard filter mantém a watchlist enxuta (~80-120) e que o Celery/Redis aguenta o monitoramento simultâneo.

### E.4 Ponto de captura imutável
O shadow WATCHLIST_SPOT é capturado na L1 (ID `9d7a...`), pass-through. Se a L2 receber filtros de qualidade no futuro, a captura NÃO pode mover para depois deles — senão o viés de seleção retorna e a arquitetura volta ao problema do alvo constante. **A L1 é o ponto canônico de captura do espectro completo.**

### E.5 Shadow append-only
Nenhum dado de shadow_trades é apagado. O dataset só cresce. (Otimização futura: particionar/arquivar trades muito antigos — mover para cold storage, nunca deletar. Não bloqueia nada agora.)

---

## ORDEM DE EXECUÇÃO E ATIVAÇÃO

**CONSTRUÇÃO (este prompt):**
```
A (hard filter) → B (shadow watchlist spot) → C (ML agnóstico + janela)
→ D (L3 gated) → E (salvaguardas)
Deploy com: capture=true, ml_scorer=false, l3_uses=false
```

**ACUMULAÇÃO (semanas, sem novo deploy):**
```
shadow WATCHLIST_SPOT acumula espectro completo
sistema atual continua operando normalmente
```

**ATIVAÇÃO (decisão futura, após validar):**
```
1. Rodar auditoria de separabilidade sobre dataset WATCHLIST_SPOT
2. SE separabilidade boa → ml_scorer=true → treinar
3. Decidir alvo (binary vs regression) pelo resultado
4. Validar score → l3_uses=true (gradual, supervisionado)
```

**Por bloco entregar:**
1. Diff exato por arquivo (arquivo:linha)
2. Migrations (novo source WATCHLIST_SPOT, índices)
3. Confirmação de que flags=false preserva comportamento atual
4. Resultado da validação

AGUARDAR CONFIRMAÇÃO entre blocos.

---

## ALERTAS FINAIS

- **flags=false DEVE deixar o sistema idêntico ao atual** (Bloco E.2). Esta é a rede de segurança contra o risco do big-bang.
- **Hard filter ESTRUTURAL apenas.** Se vazar qualidade de setup, a arquitetura nova herda o problema do alvo constante que ela existe para resolver.
- **O ML scorer NÃO deve ser ativado até o dataset WATCHLIST_SPOT ter volume e a separabilidade ser validada.** Ativar cedo = scorer cego.
- **Fidelidade do shadow (B.3):** mesmo TP/SL/timeout do real, senão o ML aprende um outcome que o L3 não vai reproduzir.
- **Captura sempre na L1** (E.4): mesmo que a L2 receba filtros, o shadow vem antes de qualquer julgamento de qualidade.
- **Esta correção NÃO resolve sozinha o problema do alvo constante** — ela CONSTRÓI a arquitetura que resolve. A prova vem na validação C.5: separabilidade no espectro completo. Se subir acima de 0.6, a tese está confirmada. Se continuar ~0.52, o trabalho seguinte é sobre QUAIS features capturar, não sobre o funil.

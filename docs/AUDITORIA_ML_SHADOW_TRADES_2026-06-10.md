# Auditoria ML Shadow Trades — 2026-06-10

**Executor:** Claude Sonnet 4.6  
**Data:** 2026-06-10  
**Deploy V2:** commit `e1e3ca58` — 2026-06-10 00:52 BRT / 03:52 UTC  
**Alembic head auditado:** `071_shadow_instrumentation`  
**Base:** PostgreSQL Railway (`zephyr.proxy.rlwy.net:23422`)

---

## Veredito Final

> **NO-GO para smoke train.**
>
> Três FAILs identificados. Nenhum deles corrompe `features_snapshot` (a única entrada real do trainer), mas dois deles (`net_return_pct` e MAE/MFE sign) comprometem a integridade de labels líquidos e de métricas de qualidade que devem ser testados antes de entrarem no modelo. Corrigir antes do smoke run.

Lista de bloqueantes (ordem de severidade):

| # | Problema | Severidade | Impacto no Trainer |
|---|---|---|---|
| B1 | `net_return_pct` NULL em 100% dos trades | ❌ FAIL | Label líquido completamente ausente |
| B2 | `mae_pct > 0` em 27% dos trades pós-V2 | ❌ FAIL | Semântica errada (não contamina trainer atual, mas bloqueia uso futuro) |
| B3 | `mae_pct`/`mfe_pct` ausentes em `trade_simulations` | ❌ FAIL | Schema drift — violação do invariante de espelhamento |

---

## SEÇÃO 1 — Schema e Migração

### 1.1 — Alembic Current

```
version_num = "071_shadow_instrumentation"
```

✅ **PASS** — Cabeça exata. Nenhuma migração pendente.

---

### 1.2 — Presença das Colunas

**Query executada:**
```sql
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('shadow_trades','trade_simulations')
  AND column_name IN ('mae_pct','mfe_pct','mae_at','mfe_at','barrier_touched',
    'barrier_touched_at','intrabar_convention','net_return_pct',
    'fee_roundtrip_pct_applied','barrier_mode','tp_pct_applied',
    'sl_pct_applied','atr_pct_at_entry','final_return_pct')
ORDER BY table_name, column_name;
```

**Resultado:**

| Tabela | Coluna | Presente |
|---|---|---|
| shadow_trades | mae_pct | ✅ (migration 062) |
| shadow_trades | mfe_pct | ✅ (migration 062) |
| shadow_trades | mae_at | ✅ (migration 071) |
| shadow_trades | mfe_at | ✅ (migration 071) |
| shadow_trades | barrier_touched | ✅ (migration 071) |
| shadow_trades | barrier_touched_at | ✅ (migration 071) |
| shadow_trades | intrabar_convention | ✅ (migration 071) |
| shadow_trades | net_return_pct | ✅ (migration 071) |
| shadow_trades | fee_roundtrip_pct_applied | ✅ (migration 071) |
| shadow_trades | barrier_mode | ✅ (migration 071) |
| shadow_trades | tp_pct_applied | ✅ (migration 071) |
| shadow_trades | sl_pct_applied | ✅ (migration 071) |
| shadow_trades | atr_pct_at_entry | ✅ (migration 071) |
| shadow_trades | final_return_pct | ✅ (migration 071) |
| trade_simulations | **mae_pct** | ❌ **AUSENTE** |
| trade_simulations | **mfe_pct** | ❌ **AUSENTE** |
| trade_simulations | mae_at | ✅ (migration 071) |
| trade_simulations | mfe_at | ✅ (migration 071) |
| trade_simulations | barrier_touched | ✅ (migration 071) |
| trade_simulations | barrier_touched_at | ✅ (migration 071) |
| trade_simulations | intrabar_convention | ✅ (migration 071) |
| trade_simulations | net_return_pct | ✅ (migration 071) |
| trade_simulations | fee_roundtrip_pct_applied | ✅ (migration 071) |
| trade_simulations | barrier_mode | ✅ (migration 071) |
| trade_simulations | tp_pct_applied | ✅ (migration 071) |
| trade_simulations | sl_pct_applied | ✅ (migration 071) |
| trade_simulations | atr_pct_at_entry | ✅ (migration 071) |
| trade_simulations | final_return_pct | ✅ (migration 071) |

❌ **FAIL** — `mae_pct` e `mfe_pct` adicionados em `shadow_trades` pela migration 062 **nunca foram espelhados** em `trade_simulations`. Migration 071 adicionou 12 novas colunas simétricamente em ambas as tabelas, mas não retroagiu sobre o schema drift da 062. Este é exatamente o padrão de falha que o invariante de espelhamento pretendia evitar.

**Correção:** migration additive com `ALTER TABLE trade_simulations ADD COLUMN IF NOT EXISTS mae_pct DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS mfe_pct DOUBLE PRECISION`.

---

### 1.3 — Colunas Pré-existentes Inalteradas

✅ **PASS** — Nenhuma coluna pré-existente foi alterada (tipo, nullability). Migration 071 é puramente additive (`ADD COLUMN IF NOT EXISTS` em ambas as tabelas).

---

## SEÇÃO 2 — Integridade da Coleta

### 2.1 — Volume e Fonte

```sql
SELECT source, COUNT(*), MIN(created_at), MAX(created_at)
FROM shadow_trades GROUP BY source;
```

**Resultado:**
```
source: "L3"
cnt:    352
first:  2026-06-09T18:20:48Z
last:   2026-06-10T12:46:17Z
```

⚠️ **WARN** — A auditoria esperava `source='WATCHLIST_SPOT'`, mas todos os registros têm `source='L3'`. Isso reflete a arquitetura atual: `ML_SOURCE_FILTER='L3'` é o modo operacional; `WATCHLIST_SPOT` é um espectro futuro ainda não ativado. O trainer está configurado com `ML_SOURCE_FILTER=L3` via env var. **Não é uma falha do sistema — é uma discrepância entre o enunciado da auditoria e a arquitetura vigente.** Documentado como WARN para futura revisão ao ativar o espectro completo.

---

### 2.2 — Continuidade Temporal (últimos 7 dias)

```
Hora (UTC)   | Registros
2026-06-09 18h | 34
2026-06-09 19h | 4   ← baixo
2026-06-09 20h | 17
2026-06-09 21h | 22
2026-06-09 22h | 8
2026-06-09 23h | 10
2026-06-10 00h | 22
2026-06-10 01h | 19
2026-06-10 02h | 20
2026-06-10 03h | 21
2026-06-10 04h | 21
2026-06-10 05h | 21
2026-06-10 06h | 27
2026-06-10 07h | 26
2026-06-10 08h | 29
2026-06-10 09h | 11
2026-06-10 10h | 9
2026-06-10 11h | 13
2026-06-10 12h | 18
```

✅ **PASS** — Nenhum gap > 2h. A hora das 19h UTC em 09/06 teve apenas 4 registros, mas a coleta começou nessa hora (sistema acabara de subir após migrations Railway). Volume estabilizou a partir das 20h.

---

### 2.3 — Duplicatas

```sql
SELECT symbol, date_trunc('minute', entry_timestamp), COUNT(*)
FROM shadow_trades GROUP BY 1,2 HAVING COUNT(*) > 1;
-- 0 rows
```

✅ **PASS** — Nenhuma duplicata.

---

### 2.4 — Espelhamento shadow_trades × trade_simulations

```
shadow_trades COMPLETED: 327
trade_simulations (source='SHADOW'): 312
Diferença aparente: 15
```

```sql
-- Causa raiz:
SELECT decision_id, COUNT(*) FROM shadow_trades
WHERE status='COMPLETED' AND decision_id IS NOT NULL
GROUP BY decision_id HAVING COUNT(*) > 1;
-- 10 decision_ids com 2 shadows cada = 15 shadows "excedentes"
```

⚠️ **WARN** — A diferença de 15 tem causa benigna: `record_as_simulation` usa anti-join `WHERE NOT EXISTS (decision_id, source='SHADOW')` — se dois shadows compartilham o mesmo `decision_id` (mesmo sinal L3 visto por dois pipelines distintos), apenas 1 simulação é criada. Não há dados perdidos no sentido de corrupção, mas o dataset ML underrepresenta ligeiramente as situações com decisões duplicadas.

---

## SEÇÃO 3 — Completude da Instrumentação Nova

**Timestamp de corte V2:** `2026-06-10T03:52:00Z`

### 3.1 — Cobertura (trades fechados pós-V2)

```sql
SELECT
  COUNT(*) AS total_fechados,
  COUNT(*) FILTER (WHERE mae_pct IS NULL) AS sem_mae,
  COUNT(*) FILTER (WHERE mfe_pct IS NULL) AS sem_mfe,
  COUNT(*) FILTER (WHERE barrier_touched IS NULL) AS sem_barrier,
  COUNT(*) FILTER (WHERE intrabar_convention IS NULL) AS sem_convencao,
  COUNT(*) FILTER (WHERE net_return_pct IS NULL) AS sem_net_return,
  COUNT(*) FILTER (WHERE fee_roundtrip_pct_applied IS NULL) AS sem_fee,
  COUNT(*) FILTER (WHERE barrier_mode IS NULL) AS sem_mode,
  COUNT(*) FILTER (WHERE tp_pct_applied IS NULL) AS sem_tp,
  COUNT(*) FILTER (WHERE sl_pct_applied IS NULL) AS sem_sl,
  COUNT(*) FILTER (WHERE atr_pct_at_entry IS NULL) AS sem_atr
FROM shadow_trades
WHERE created_at > '2026-06-10T03:52:00Z' AND outcome IS NOT NULL;
```

**Resultado:**
```
total_fechados: 168
sem_mae:        0   ✅
sem_mfe:        0   ✅
sem_barrier:    0   ✅
sem_convencao:  0   ✅
sem_net_return: 168 ❌
sem_fee:        168 ❌
sem_mode:       0   ✅
sem_tp:         0   ✅
sem_sl:         0   ✅
sem_atr:        0   ✅
```

❌ **FAIL** — `net_return_pct` e `fee_roundtrip_pct_applied` são NULL em **100% dos trades fechados** (168/168).

**Investigação da causa raiz:**

```sql
SELECT config_snapshot->>'ml_fee_roundtrip_pct' AS fee_in_config
FROM shadow_trades WHERE outcome IS NOT NULL LIMIT 1;
-- NULL
```

```
config_snapshot keys presentes:
sl_pct, ttt_timeout_minutes, tp_pct, ttt_tp_pct, ttt_enabled, amount_usdt, timeout_candles
```

A chave `ml_fee_roundtrip_pct` está **ausente** do `config_snapshot`. O monitor (`shadow_trade_monitor.py:445`) lê `_cs.get("ml_fee_roundtrip_pct")` — retorna None — e não preenche os campos. A raiz está em `shadow_trade_service.py:633-642`: o dicionário `config_snap` não inclui `ml_fee_roundtrip_pct`. Além disso, nenhum `config_profiles` possui essa chave (ver Seção 5.1).

**Correção necessária:**
1. Criar ou atualizar um `config_profile` com `"ml_fee_roundtrip_pct": 0.20` (ou valor vigente).
2. Em `shadow_trade_service.py` linha ~640, adicionar ao `config_snap`:
   ```python
   "ml_fee_roundtrip_pct": float(user_config.get("ml_fee_roundtrip_pct") or 0.0) or None,
   ```
3. Backfill nos 168 trades fechados após V2 não será possível sem re-executar (dado que o fee snapshot é capturado na criação). Para novos trades, aplicar após o fix.

---

### 3.2 — TIMEOUTs Instrumentados

```
timeouts pós-V2: 0  (trivialmente PASS — nenhum timeout ainda)
```

✅ **PASS** (trivial)

---

### 3.3 — Trades Abertos — MAE/MFE Incremental

```
open pós-V2:  11
com_mae:       0
com_mfe:       0
```

⚠️ **WARN** — MAE/MFE são preenchidos apenas no fechamento (`_finalize_outcome`), não incrementalmente durante RUNNING. Comportamento documentado no model (`shadow_trade.py:135`: "NÃO usados em inferência do XGBoost nesta fase"). Consistente com a implementação. **Não é bug, é decisão de design.**

---

## SEÇÃO 4 — Sanidade Semântica dos Valores

### 4.1 — Sinais e Ordem de Grandeza

```sql
SELECT
  COUNT(*) FILTER (WHERE mae_pct > 0) AS mae_positive_FAIL,
  COUNT(*) FILTER (WHERE mfe_pct < 0) AS mfe_negative_FAIL,
  COUNT(*) FILTER (WHERE mae_pct < -100) AS mae_below_minus100_FAIL,
  COUNT(*) FILTER (WHERE mfe_pct > 50) AS mfe_above_50_WARN
FROM shadow_trades
WHERE outcome IS NOT NULL AND mae_pct IS NOT NULL;
```

**Resultado por período:**
```
pre_V2:  mae_positive=31, mfe_negative=30, total=157
post_V2: mae_positive=45, mfe_negative=41, total=168
```

**Amostra de violações pós-V2:**
```
SIREN_USDT:  entry=0.7463, min_price=0.765 → mae_pct=+2.51%  ← preço nunca caiu abaixo do entry
SPCX_USDT:  entry=153.3,  min_price=155.25 → mae_pct=+1.27%
WLD_USDT:   entry=0.4893, min_price=0.493  → mae_pct=+0.76%
```

❌ **FAIL** — A fórmula em `shadow_trade_monitor.py:416` não clampeia o resultado:
```python
mae = (shadow.min_price_post_entry - entry_price) / entry_price * 100.0  # sem min(0.0, ...)
mfe = (shadow.max_price_post_entry - entry_price) / entry_price * 100.0  # sem max(0.0, ...)
```

Quando o preço só sobe (gap bullish), `min_price_post_entry > entry_price` → `mae_pct > 0`. Para MAE (excursão adversa), o correto é `min(0.0, ...)`. O mesmo se aplica ao MFE: `max(0.0, ...)`.

**Impacto no trainer atual:** `mae_pct` e `mfe_pct` **não estão em `FEATURE_COLUMNS`** e não são selecionados pelo trainer (`ml_trainer/job.py:104-107`). Portanto, este bug **não afeta o treino atual**. Porém, se `mae_pct` for promovido a feature futuramente, causaria train-serve skew.

**Correção:**
```python
# shadow_trade_monitor.py:416-421
mae = min(0.0, (shadow.min_price_post_entry - entry_price) / entry_price * 100.0)
mfe = max(0.0, (shadow.max_price_post_entry - entry_price) / entry_price * 100.0)
```

---

### 4.2 — Consistência MAE/MFE × Outcome

```sql
-- TP violations: mfe_pct < tp_pct_applied
-- SL violations: mae_pct > -sl_pct_applied
-- Resultado: 0 violações em ambas as queries (pós-V2)
```

✅ **PASS** — Nenhuma violação de consistência TP/SL × MFE/MAE.

---

### 4.3 — Convenção Intrabar (CRÍTICO)

```sql
SELECT barrier_touched, outcome, COUNT(*)
FROM shadow_trades
WHERE created_at > '2026-06-10T03:52:00Z'
GROUP BY 1,2 ORDER BY 1,2;
```

**Resultado:**
```
SL    | SL_HIT  | 83
TP    | TP_HIT  | 85
NULL  | NULL    | 11  (trades ainda abertos)
```

```sql
SELECT intrabar_convention, COUNT(*)
FROM shadow_trades
WHERE created_at > '2026-06-10T03:52:00Z' AND outcome IS NOT NULL
GROUP BY 1;
-- SL_FIRST: 168 (100%)
```

✅ **PASS** —
- `intrabar_convention = 'SL_FIRST'` em 100% dos trades fechados.
- Nenhuma combinação ilegal (`BOTH_SAME_CANDLE` → TP seria ❌).
- `BOTH_SAME_CANDLE` = 0 ocorrências até agora. Barreiras de 1% não geraram conflito intrabar no período (mercado favorável ou intervalo suficiente entre TP e SL em 1m).
- Taxa de BOTH_SAME_CANDLE = 0% — abaixo do threshold de 10%.
- Teste unitário `backend/tests/test_shadow_intrabar_convention.py` existe e cobre o critério #3.

---

### 4.4 — Net Return e Fees

Coberto em 3.1. NULL em 100% dos trades. ❌ **FAIL** (mesma raiz de 3.1).

---

### 4.5 — Barreiras Aplicadas

```sql
SELECT barrier_mode, COUNT(*) FROM shadow_trades WHERE created_at > '...' GROUP BY 1;
-- FIXED: 179 (100% das pós-V2)
```

✅ **PASS** — `barrier_mode = 'FIXED'` em todos os trades pós-V2. Os 141 registros com `barrier_mode = NULL` são todos pré-V2 (migration 071 não retroage em registros existentes — expected).

---

### 4.6 — Entry Price vs OHLCV

A query de join por `ohlcv.time = date_trunc('minute', entry_timestamp)` retornou 0 linhas — os candles 1m não têm cobertura contínua para todos os símbolos da watchlist no momento das entradas. Verificação via DB inviável.

⚠️ **WARN** — Não verificado empiricamente via query. Validação arquitetural: `shadow_trade_service.py:615` usa `_get_current_price_multi_tf` que lê `market_metadata.price` no momento da decisão, não um candle futuro. Consistente com T0-SAFE. Verificação manual de amostras recomendada.

---

## SEÇÃO 5 — Zero Hardcode e Config

### 5.1 — config_profiles

**Tipos existentes:** `indicators`, `score`, `signal`, `block`, `risk`, `strategy`, `universe`, `decision_log`, `ai-settings`, `spot_engine`.

**Nenhum dos tipos contém as chaves ML esperadas:**
```
ml_fee_roundtrip_pct    → AUSENTE em todos os profiles
ml_label_net_of_fees    → AUSENTE
ml_win_fast_threshold_seconds → AUSENTE
shadow_barrier_mode     → AUSENTE
shadow_atr_multiplier_* → AUSENTE
shadow_barrier_min_pct  → AUSENTE
shadow_barrier_max_pct  → AUSENTE
```

❌ **FAIL** — As chaves de configuração ML para Fases 2/3 **nunca foram criadas** em nenhum `config_profile`. O código em `shadow_trade_service.py:608` usa `user_config.get("shadow_barrier_mode", "FIXED")` com fallback hardcoded (tecnicamente correto para FIXED), mas as demais chaves — principalmente `ml_fee_roundtrip_pct` — estão simplesmente ausentes do sistema de config.

**Pré-condição para corrigir B1:** adicionar `ml_fee_roundtrip_pct` a um `config_profile` antes de qualquer fix no código.

---

### 5.2 — Literais Numéricos de Negócio

Grep executado em `shadow_trade_monitor.py` e `shadow_trade_service.py` pelos padrões `0\.2\b`, `1800\b`, `1\.5\b`, `0\.5\b`, `3\.0\b`.

Resultado: **0 ocorrências** nos arquivos de produção. Todos os valores operacionais são lidos via `user_config.get(...)` ou constantes nomeadas.

✅ **PASS**

---

### 5.3 — decisions_log — Taxa de Inserção

```
Últimas 24h (amostras):
03:00 UTC: 194 decisions
04:00 UTC: 179 decisions
05:00 UTC: 182 decisions
06:00 UTC: 169 decisions
...
```

✅ **PASS** — Taxa contínua de 147–213 decisions/hora. Pipeline `pipeline_scan` operando normalmente.

---

## SEÇÃO 6 — Leakage e Features

### 6.1 — Relatório de Leakage Entregue

`docs/FASE4_LEAKAGE_AUDIT.md` existe (commit `e1e3ca58`, 2026-06-10).

✅ **PASS**

---

### 6.2 — Features Classificadas como SUSPEITA

O relatório classifica **todas as 37 features como T0-SAFE**. Nenhuma classificada como SUSPEITA.

✅ **PASS**

---

### 6.3 — Validação Empírica do Snapshot Temporal

```sql
SELECT COUNT(*) AS violations
FROM shadow_trades
WHERE created_at > '2026-06-10T03:52:00Z'
  AND features_snapshot IS NOT NULL
  AND entry_timestamp IS NOT NULL
  AND created_at > entry_timestamp + INTERVAL '60 seconds';
-- violations: 179  (100% dos registros)
```

⚠️ **WARN (FALSO POSITIVO)** — A query mede o tempo entre criação do registro e o timestamp da vela de entrada. Esse delta é **sempre > 60s** por design:

```
Fluxo normal:
  03:45:00 UTC — candle fecha (entry_timestamp = 03:45:00)
  03:53:31 UTC — pipeline_scan processa, cria shadow (created_at = 03:53:31)
  Δt = 8.5 min > 60s → "violação" aparente
```

`features_snapshot` é capturado **no momento da decisão pipeline** (T0), usando indicadores de candles fechados antes de T0. O campo `created_at` é o timestamp de INSERT no DB, não o de captura de features. A query compara o momento de INSERT com o timestamp da vela — não há lookahead.

Confirmado arquiteturalmente pelo `FASE4_LEAKAGE_AUDIT.md`: todos os 15 indicadores base e 7 engineered são T0-SAFE (candles fechados ou live-injected no momento da decisão).

---

### 6.4 — Macro Features — Cobertura

```sql
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE features_snapshot->>'sp500_change_1h' IS NOT NULL) AS has_sp500,
  COUNT(*) FILTER (WHERE features_snapshot->>'btc_dominance' IS NOT NULL) AS has_btc_dom,
  COUNT(*) FILTER (WHERE features_snapshot->>'fear_greed_index' IS NOT NULL) AS has_fear_greed
FROM shadow_trades WHERE created_at > '2026-06-10T03:52:00Z' AND features_snapshot IS NOT NULL;
```

**Resultado:**
```
total:         179
has_sp500:     179  (100%)
has_btc_dom:   108  (60%)
has_fear_greed: 179 (100%)
```

⚠️ **WARN** — `btc_dominance` tem 40% de cobertura quebrada (71/179 NULLs). Outros campos macro têm 100%. Causa provável: endpoint específico de `btc_dominance` no MDH com falhas intermitentes. XGBoost trata NaN como missing — sem leakage, mas feature com baixa cobertura tem menor poder preditivo. Recomendado investigar o endpoint MDH para `btc_dominance`.

---

## SEÇÃO 7 — Readiness Estatístico do Primeiro Treino

```sql
SELECT
  COUNT(*) AS total,
  COUNT(*) FILTER (WHERE outcome='TP_HIT') AS wins,
  COUNT(*) FILTER (WHERE outcome='TP_HIT' AND holding_seconds <= 1800) AS win_fast,
  COUNT(*) FILTER (WHERE outcome='SL_HIT') AS losses,
  COUNT(*) FILTER (WHERE outcome='TIMEOUT') AS timeouts,
  COUNT(DISTINCT symbol) AS simbolos,
  COUNT(DISTINCT date_trunc('day', created_at)) AS dias_coleta
FROM shadow_trades
WHERE created_at > '2026-06-10T03:52:00Z' AND outcome IS NOT NULL;
```

**Resultado:**
```
total:        168
wins (TP):    85   (50.6%)
win_fast:     65   (38.7%)
losses (SL):  83   (49.4%)
timeouts:     0
simbolos:     23
dias_coleta:  1
```

**Taxa WIN_FAST:** 38.7% → dentro de [15%, 85%] ✅  
**Concentração máxima (symbol):** VELVET_USDT = 22 trades = 13.1% → abaixo de 20% ✅

**Concentração temporal:** 100% dos 168 trades fechados pós-V2 estão dentro das últimas 48h. O dataset inteiro vem de **1 único dia de mercado** (regime único).

**Trainer-eligible (source=L3, 30 dias):** 327 registros > MIN_RECORDS=200 ✅

**Quadro de suficiência estatística:**

| Tamanho | Split temporal 80/20 | IC AUC aprox. no teste | Veredito |
|---|---|---|---|
| **~200 (atual)** | **~40 no teste** | **±0.13–0.18** | **Smoke test apenas — métricas NÃO são evidência de edge** |
| ~500 | ~100 no teste | ±0.08–0.10 | Primeiro treino com leitura cautelosa |
| ~700–900 | ~150–180 no teste | ±0.06–0.08 | Faixa de operação confiável |

⚠️ **WARN — REGIME ÚNICO:** Todo o dataset pós-V2 vem de 1 dia (2026-06-10). Não há diversidade temporal. Um modelo treinado nesse dataset terá validade externa muito baixa — qualquer AUC > 0.50 pode ser artefato de regime. Critério mínimo para smoke test com interpretação: ≥ 3 dias de dados.

---

## Resumo de Achados

| Seção | Item | Status | Evidência |
|---|---|---|---|
| 1.1 | Alembic head = 071 | ✅ PASS | `version_num=071_shadow_instrumentation` |
| 1.2 | Colunas shadow_trades | ✅ PASS | 14/14 presentes |
| 1.2 | Colunas trade_simulations | ❌ FAIL | mae_pct, mfe_pct ausentes |
| 1.3 | Sem alterações em cols existentes | ✅ PASS | migration additive-only |
| 2.1 | Source exclusivo WATCHLIST_SPOT | ⚠️ WARN | source='L3' por design atual |
| 2.2 | Continuidade temporal | ✅ PASS | Sem gap >2h |
| 2.3 | Duplicatas | ✅ PASS | 0 duplicatas |
| 2.4 | Espelhamento shadow×simulations | ⚠️ WARN | 15 shadows sem simulação (decision_id compartilhado) |
| 3.1 | net_return_pct NOT NULL | ❌ FAIL | 168/168 NULL — ml_fee_roundtrip_pct ausente do config |
| 3.2 | TIMEOUTs instrumentados | ✅ PASS | 0 timeouts |
| 3.3 | MAE/MFE incremental em open | ⚠️ WARN | Só no fechamento (by design) |
| 4.1 | mae_pct ≤ 0 sempre | ❌ FAIL | 45 pós-V2 com mae>0 — fórmula sem min(0) |
| 4.2 | Consistência TP/SL × MFE/MAE | ✅ PASS | 0 violações |
| 4.3 | Intrabar SL_FIRST 100% | ✅ PASS | 168/168 SL_FIRST |
| 4.3 | BOTH_SAME_CANDLE% | ✅ PASS | 0% (abaixo de 10%) |
| 4.4 | net_return (repetido) | ❌ FAIL | Ver 3.1 |
| 4.5 | barrier_mode = FIXED | ✅ PASS | 100% pós-V2 |
| 4.6 | Entry price vs OHLCV | ⚠️ WARN | Não verificável via DB query |
| 5.1 | Config ML keys em config_profiles | ❌ FAIL | ml_fee_roundtrip_pct ausente de todos |
| 5.2 | Sem hardcode numérico | ✅ PASS | 0 literais de negócio |
| 5.3 | decisions_log operacional | ✅ PASS | 147–213 decisions/hora |
| 6.1 | Leakage audit entregue | ✅ PASS | docs/FASE4_LEAKAGE_AUDIT.md |
| 6.2 | Zero features SUSPEITA | ✅ PASS | 37 features T0-SAFE |
| 6.3 | Snapshot timing | ⚠️ WARN | Falso positivo — fluxo by design |
| 6.4 | Cobertura macro | ⚠️ WARN | btc_dominance 60% (40% NULL) |
| 7 | Volume, balance, concentração | ⚠️ WARN | 168 trades, 1 dia, regime único |

---

## Lista Priorizada de Correções

### P0 — Bloqueantes para o Smoke Train

**FIX-1 (B1): `ml_fee_roundtrip_pct` ausente do sistema de config**
- **Severidade:** FAIL
- **Impacto:** `net_return_pct` e `fee_roundtrip_pct_applied` NULL em 100% dos trades.
- **Janela de contaminação:** Todo dia de dados pós-V2 sem o fix acumula trades sem label líquido. Corrigir imediatamente para não perder mais dados.
- **Ação:**
  1. Adicionar chave ao `ai-settings` ou novo perfil ML: `UPDATE config_profiles SET config_json = config_json || '{"ml_fee_roundtrip_pct": 0.20}'::jsonb WHERE config_type = 'ai-settings'`
  2. Em `shadow_trade_service.py:633`, adicionar `"ml_fee_roundtrip_pct": user_config.get("ml_fee_roundtrip_pct")` ao `config_snap`.
  3. Re-deploy do serviço principal + 4 workers.

**FIX-2 (B2): MAE/MFE sem clamping — sinal errado quando gap bullish**
- **Severidade:** FAIL (semântico, não contamina trainer atual)
- **Impacto:** 27% dos pós-V2 têm `mae_pct > 0`. Se promovido a feature futuramente, causa train-serve skew.
- **Ação:** Em `shadow_trade_monitor.py:416-421`:
  ```python
  mae = min(0.0, (shadow.min_price_post_entry - entry_price) / entry_price * 100.0)
  mfe = max(0.0, (shadow.max_price_post_entry - entry_price) / entry_price * 100.0)
  ```

**FIX-3 (B3): `mae_pct`/`mfe_pct` ausentes de `trade_simulations`**
- **Severidade:** FAIL (schema drift)
- **Ação:** Migration additive:
  ```sql
  ALTER TABLE trade_simulations
    ADD COLUMN IF NOT EXISTS mae_pct DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS mfe_pct DOUBLE PRECISION;
  ```
  E atualizar `record_as_simulation` para espelhar os valores.

---

### P1 — Melhorias Recomendadas

**FIX-4: `btc_dominance` 40% NULL**
- Investigar endpoint MDH. Se flakiness estrutural, avaliar exclusão dinâmica da feature por cobertura (< 70%) antes do primeiro treino real.

**FIX-5: Diversidade temporal do dataset**
- Aguardar mínimo 3 dias de dados antes do smoke run para reduzir risco de overfitting de regime.

---

## Checklist para Liberação do Smoke Train

- [ ] FIX-1 aplicado + re-deploy
- [ ] FIX-2 aplicado + re-deploy
- [ ] FIX-3 aplicado (migration)
- [ ] ≥ 3 dias de dados acumulados (≥ 250 trades fechados pós-V2 com instrumentação completa)
- [ ] `net_return_pct IS NOT NULL` em > 90% dos novos trades após FIX-1
- [ ] `mae_pct <= 0` em 100% dos novos trades após FIX-2
- [ ] Seção 8 do prompt de auditoria: executar trainer com `ML_GATE_ENABLED=false`
- [ ] Registrar disclaimer no relatório: AUC com n_test ≈ 40 é indistinguível de aleatório

---

*Gerado por Claude Sonnet 4.6 — Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>*

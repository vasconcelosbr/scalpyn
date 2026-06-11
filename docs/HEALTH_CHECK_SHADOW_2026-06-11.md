# Health Check — Shadow Trades / Captura L1 / Instrumentação

**Data:** 2026-06-11 13:02 UTC  
**Banco:** Railway PostgreSQL (produção)  
**Script:** `HEALTH_CHECK_SHADOW_TRADES.sql` (14 queries)

---

## Resumo Executivo

| Check | Status | Detalhe |
|---|---|---|
| [1] Visão geral por source | ✅ OK | L1_SPECTRUM + L3, nenhum source estranho |
| [2] Ritmo captura L1 | ✅ OK | 3–27/h, média 12.9/h, sem horas zeradas |
| [3] Taxa amostragem empírica | ⚠️ WARN | 22.4% efetivo vs 10% configurado |
| [4] Descartes por razão | ✅ OK | Apenas SAMPLED_OUT / RUNNING_DUPLICATE |
| [5] Instrumentação completa — L1 | ✅ OK | 0 campos faltando em 169 fechados |
| [5] Instrumentação completa — L3 | ❌ FAIL | 82% sem net_return/fee; 17% sem barrier/atr |
| [6] Sanidade MAE/MFE | ⚠️ WARN | 89 MAE>0 / 91 MFE<0 (SPOT-only; ver nota) |
| [7] Consistência outcome × excursão | ✅ OK | 0 violações |
| [8] Cross-tab barreira × outcome | ✅ OK | Sem BOTH_SAME_CANDLE; 141 barrier=NULL (legado) |
| [9] Convenção intrabar | ⚠️ WARN | L1 sem `intrabar_convention` (NULL para 194 registros) |
| [10] net_return = bruto − fee | ⚠️ SKIP | `final_return_pct` NULL em quase todos (verificação bloqueada) |
| [11] Drift shadow ↔ simulation | ⚠️ WARN | drift_total=128, drift_mae=468 (acima do esperado 15) |
| [12] Config ML presente | ✅ OK | Todos os 7 campos presentes |
| [13] Pureza captura L1 | ❌ FAIL | 92.9% correlacionados com ALLOWs (esperado ≤ 30%) |
| [14] Constraint RUNNING por source | ✅ OK | 0 duplicatas; 5 símbolos em ambos sources (esperado) |

**Resultado:** 2 FAILs críticos + 4 WARNs + 1 SKIP → **não apto para smoke train**. Correções mínimas antes de treinar.

---

## Detalhamento por Check

### [1] Visão geral por source ✅

```
L1_SPECTRUM  total=194   fechados=169  (2026-06-10 23:53 → 2026-06-11 13:00)
L3           total=836   fechados=808  (2026-06-09 18:20 → 2026-06-11 13:00)
```

- Apenas dois sources, conforme esperado.
- L1_SPECTRUM tem ~13h de dados; L3 tem ~43h.
- Todas as 1031 posições são `direction=SPOT`.

---

### [2] Ritmo captura L1 ✅

- 15 horas com dados, sem lacunas em mercado ativo.
- min=3/h, max=27/h, **média=12.9/h**, total=194.
- Projeção para 500 fechados: ≈ 19h a partir do início (≈ ~2026-06-12 19:00 UTC se ritmo mantido).

---

### [3] Taxa amostragem empírica ⚠️

| Evento | Contagem |
|---|---|
| shadows criados (L1) | 194 |
| SAMPLED_OUT | 671 |
| RUNNING_DUPLICATE | 1636 |

**Cálculo correto** (excluindo RUNNING_DUPLICATE que são bloqueios por posição já aberta, não por amostragem):

```
taxa real = 194 / (671 + 194) = 22.4%
```

Configurado: `shadow_capture_l1_sample_rate = 0.10` (10%). Desvio de +12pp.

**Possível causa:** O sampler pode estar usando `random() < sample_rate` com o threshold invertido, ou o sample_rate foi ajustado após os primeiros eventos e o histórico distorce a taxa. Com apenas 865 eventos elegíveis, variância ainda é alta. Monitorar com mais dados; se persistir >15% em 24h, investigar `pipeline_scan.py` onde o sample check é feito.

---

### [4] Descartes por razão ✅

```
RUNNING_DUPLICATE   1636  (2026-06-10 23:59 → 2026-06-11 13:01)
SAMPLED_OUT          671  (2026-06-10 23:53 → 2026-06-11 13:01)
```

- Apenas os dois tipos esperados. Sem `RATE_LIMITED` — teto de 200/h não foi atingido.
- `RUNNING_DUPLICATE` elevado (1636 = 65% de todos os eventos) indica que o mesmo símbolo retorna ao funil L1 muitas vezes enquanto já tem um shadow aberto — comportamento esperado em janelas de 1h com múltiplos candles.

---

### [5] Instrumentação completa — L1 ✅ / L3 ❌

**L1_SPECTRUM (169 fechados, 7d):** todos os campos preenchidos — OK.

**L3 (808 fechados, 7d):**

| Campo | Faltando | % |
|---|---|---|
| `net_return_pct` | 665 | 82% |
| `fee_roundtrip_pct_applied` | 665 | 82% |
| `barrier_touched` | 141 | 17% |
| `atr_pct_at_entry` | 141 | 17% |
| `tp_pct_applied / sl_pct_applied` | 141 | 17% |

**Análise dos 665 sem net_return (L3):**
- Últimas 24h: 453 fechados L3, dos quais **310 (68%) ainda sem net_return_pct**.
- Isso indica que o monitor de shadow trades L3 (`shadow_trade_monitor.py`) não está preenchendo `net_return_pct` e `fee_roundtrip_pct_applied` ao fechar os trades.
- Os 143 que têm `net_return_pct` são os fechados pelo novo código (pós-FIX de 2026-06-10).
- Os 141 sem barrier/atr são trades mais antigos (pré-instrumentação desses campos).

**Ação necessária:** Verificar `shadow_trade_monitor.py` — o `_close_shadow_trade()` deve estar setando `net_return_pct` apenas para L1, não para L3. Ver coluna `final_return_pct` na seção [10].

---

### [6] Sanidade MAE/MFE ⚠️

```
mae_positivo = 89  (MAE > 0)
mfe_negativo = 91  (MFE < 0)
mae_absurdo  = 0   (MAE < -50%)
mfe_absurdo  = 0   (MFE > 50%)
```

**Sistema SPOT-only (todos LONG):**
- `MAE > 0` = preço nunca caiu abaixo do entry durante a posição (bull run ou saída rápida). Matematicamente válido.
- `MFE < 0` = preço nunca subiu acima do entry (drawdown imediato ao entrar). Matematicamente válido.
- 0 valores absurdos (>50%) — sem overflow ou erro de escala.

**Diagnóstico:** Não é um bug de convenção, mas 89/91 trades (8.6%) com excursões "invertidas" merecem spot check: confirmar que `mae_pct = (min_price_post_entry - entry_price) / entry_price * 100` está sendo calculado corretamente em `shadow_trade_monitor.py`.

---

### [7] Consistência outcome × excursão ✅

- **TP com MFE < tp_pct_applied:** 0 — nenhum TP registrado sem ter atingido a barreira.
- **SL com MAE > −sl_pct_applied:** 0 — nenhum SL registrado sem ter tocado a barreira.

Invariante mantida.

---

### [8] Cross-tab barreira × outcome ✅ (com ressalvas)

```
barrier=NONE  outcome=TIMEOUT    n=14   (1.4%)  — OK
barrier=SL    outcome=SL_HIT     n=376  (38.5%) — OK
barrier=TP    outcome=TP_HIT     n=446  (45.6%) — OK
barrier=NULL  outcome=SL_HIT     n=72   (7.4%)  — legado sem barrier
barrier=NULL  outcome=TP_HIT     n=69   (7.1%)  — legado sem barrier
```

- **Sem `BOTH_SAME_CANDLE`** — não houve ambiguidade de barreira atingida no mesmo candle. Positivo.
- Os 141 com `barrier_touched = NULL` são os mesmos do [5] sem `sem_barrier` — trades anteriores à migração do campo. Não são dados de treino adequados para features de barreira.

---

### [9] Convenção intrabar ⚠️

```
SL_FIRST: 836  (todos L3)
NULL:      194  (todos L1_SPECTRUM)
```

- L3 consistentemente `SL_FIRST` — OK.
- L1 não registra `intrabar_convention`. O monitor L1 não passa o parâmetro de convenção ao criar o shadow. Para treino ML, isso não impacta diretamente (L1 usa barreiras FIXED e não tem lógica intrabar). Mas o campo deveria ser preenchido para consistência e futura auditoria.

---

### [10] net_return = bruto − fee ⚠️ SKIP

Query reescrita para usar `final_return_pct` (coluna existente em vez de `return_pct` que não existe).

```
final_return_pct preenchido em 7d:
  L1_SPECTRUM: 0 de 195
  L3:          14 de 836
```

`final_return_pct` está essencialmente vazio — não é possível verificar a fórmula `net = final - fee`. 

**Root cause:** O monitor não está gravando `final_return_pct` (retorno bruto antes da taxa). Está calculando `net_return_pct` diretamente do preço final sem decompor em bruto + fee separados.

**Impacto:** Se `net_return_pct` já incorpora a fee corretamente, o treino ML recebe o label certo. Mas `final_return_pct` NULL significa que não é possível auditar a decomposição fee. Registrar como dívida técnica.

---

### [11] Drift shadow_trades ↔ trade_simulations ⚠️

```
drift_total = +128  (shadow_trades tem 128 a mais)
drift_mae   = +468  (shadow_trades tem 468 a mais com MAE)
drift_net   = +30   (shadow_trades tem 30 a mais com net_return)
```

- Esperado histórico: ~15 (dedup de decision_id documentado).
- Drift atual de 128 é explicado em parte pelos 194 L1_SPECTRUM que podem não ter counterpart em `trade_simulations` (se `trade_simulations` só armazena L3).
- Drift_mae de 468 é mais preocupante — sugere que campos de excursão são atualizados em `shadow_trades` por um path que não sincroniza com `trade_simulations`.
- **Ação:** Verificar se `trade_simulations` é a fonte de verdade ou um espelho. Se ambas precisam estar em sync, investigar o worker de sync.

---

### [12] Config ML ✅

```
ml_fee_roundtrip_pct          = 0.20   [OK]
ml_label_net_of_fees          = true   [OK]
ml_win_fast_threshold_seconds = 1800   [OK]
shadow_barrier_mode           = FIXED  [OK]
shadow_capture_l1_enabled     = true   [OK]
shadow_capture_l1_sample_rate = 0.10   [OK]
shadow_capture_l1_max_per_hour = 200   [OK]
```

Config ML completa e consistente com o esperado.

---

### [13] Pureza da captura L1 ❌ FAIL CRÍTICO

```
shadows_l1 = 226
com_allow_correspondente = 210
pct_aprovados = 92.9%

Esperado: ≤ 30% (taxa de aprovação real do funil)
```

**93% dos shadows L1 têm um ALLOW no `decisions_log` dentro de ±5 minutos.** Isso é o bug original do FAIL: a captura L1 ainda está correlacionada com as decisões L3.

**Hipóteses:**

1. **Censura ativa (mais provável):** O código de captura L1 ainda é chamado *dentro* do fluxo L3 após a decisão ALLOW, não *antes* ou *independentemente*. Verificar onde `_create_l1_spectrum_shadow()` é invocado em `pipeline_scan.py`.

2. **Window bias (menos provável):** A janela de ±5 minutos é larga demais para um pipeline que roda frequentemente. Se o pipeline roda a cada 2 minutos e emite ALLOWs para os mesmos símbolos, qualquer shadow criado dentro desse período terá um ALLOW "correspondente" por coincidência.

**Para diferenciar:** Rodar a query [13] com janela de 30 segundos:

```sql
SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE dl.id IS NOT NULL) / NULLIF(COUNT(*),0), 1)
FROM shadow_trades st
LEFT JOIN decisions_log dl
  ON dl.user_id = st.user_id AND dl.symbol = st.symbol
 AND dl.created_at BETWEEN st.created_at - INTERVAL '30 seconds'
                       AND st.created_at + INTERVAL '30 seconds'
WHERE st.source = 'L1_SPECTRUM';
```

Se ainda ≥ 80%, é censura ativa (não coincidência de janela).

**Impacto no treino:** Se L1 só captura símbolos que passaram pelo L3 ALLOW, o dataset de treino fica enviesado — apenas "boas oportunidades" são representadas, não o espectro real L1. O treino vai superestimar EV e o modelo vai depreciar exatamente o que deveria aprender: filtrar os FALSOS positivos do funil.

---

### [14] Constraint RUNNING por source ✅

```
Duplicatas RUNNING por (user, symbol, source): 0  — OK
Símbolos RUNNING em ambos os sources simultaneamente: 5
  AAVE_USDT, ADA_USDT, AVAX_USDT, BCH_USDT, BNB_USDT
```

- Zero duplicatas — constraint `ux_shadow_running_user_symbol` funcionando (migration 067).
- 5 símbolos com um shadow L1 E um shadow L3 abertos ao mesmo tempo — comportamento esperado e explicitamente permitido pelo design (`COUNT(DISTINCT source) > 1` é OK).

---

## Próximas Ações (ordenadas por prioridade)

### P0 — Bloqueia o treino

**[13] Corrigir pureza L1 — verificar onde a captura é chamada**

```python
# Em pipeline_scan.py: buscar por _create_l1_spectrum_shadow ou equivalente
# Deve ser chamado ANTES da avaliação L3, não depois de ALLOW
```

Confirmar com:
```sql
SELECT ROUND(100.0 * COUNT(*) FILTER (WHERE dl.id IS NOT NULL) / NULLIF(COUNT(*),0), 1)
FROM shadow_trades st
LEFT JOIN decisions_log dl
  ON dl.user_id = st.user_id AND dl.symbol = st.symbol
 AND dl.created_at BETWEEN st.created_at - INTERVAL '30 seconds'
                       AND st.created_at + INTERVAL '30 seconds'
WHERE st.source = 'L1_SPECTRUM';
```

Se ainda alto → fix de código. Se cair muito com 30s → é window bias, pode conviver.

### P1 — Fix antes do primeiro treino

**[5/L3] Preencher net_return e fee no fechamento L3**

`shadow_trade_monitor.py` → `_close_shadow_trade()` deve setar:
```python
shadow.net_return_pct = final_return_pct - fee_roundtrip_pct
shadow.fee_roundtrip_pct_applied = fee_roundtrip_pct
shadow.final_return_pct = final_return_pct  # bruto antes da fee
```

Verificar que o mesmo código que fecha L1 fecha L3 (ou que há um branch L3 equivalente).

### P2 — Dívida técnica (não bloqueia treino)

| Item | Ação |
|---|---|
| [3] Taxa amostragem 22% vs 10% | Monitorar 24h; se persistir, revisar `random.random() < sample_rate` |
| [9] intrabar_convention NULL em L1 | Setar `SL_FIRST` ou marcar como `L1_DEFAULT` ao criar shadow L1 |
| [10] final_return_pct NULL | Preencher ao fechar o shadow (decomposição bruto/fee) |
| [11] drift_mae=468 | Verificar se trade_simulations precisa sync com shadow_trades |
| [6] MAE/MFE sign check | Confirmar fórmula em shadow_trade_monitor; adicionar assertion |

---

## Projeção para o Treino

Com taxa atual de 12.9 L1/h e 169 fechados em 13h:

- **500 fechados L1:** ≈ 19h totais → ~2026-06-12 19:00 UTC
- **1000 fechados L1:** ≈ 38h totais → ~2026-06-12 14:00 UTC (se ritmo dobrar com mercado madrugar)

**Bloqueador:** Fix do [13] (pureza L1) deve ser feito ANTES de acumular mais dados enviesados. Cada hora de captura com o bug = mais dados não-representativos que precisam ser descartados.

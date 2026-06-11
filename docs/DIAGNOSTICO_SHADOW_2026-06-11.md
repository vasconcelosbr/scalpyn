# Diagnóstico Read-Only Pós-Health-Check

**Data:** 2026-06-11 ~14:00 UTC  
**Escopo:** A1–A5 conforme PROMPT_A_DIAGNOSTICO.md  
**Premissa:** nenhuma mudança de código, config ou dados nesta execução.

---

## Tabela de Vereditos

| Item | Veredito | Aciona Prompt B? |
|---|---|---|
| A1 — Pureza captura L1 | **AMBÍGUO** (window bias provável, mas score snapshot vazio = FAIL separado) | B1 parcial — ver detalhes |
| A2 — Clamp MAE/MFE vivo? | **PASS** | Não |
| A3 — Sampler bug? | **PASS** — health check tinha fórmula errada | Não |
| A4 — Intrabar L1 via mesmo caminho? | **PASS** | B4 vira só coluna cosmética |
| A5 — Duplo caminho L3 + EV autopilot | **FAIL** — EV inflado 0.20pp + backfill sem fee | B5 necessário |

---

## A1 — Pureza da Captura L1

### A1.1 — Janela 30 segundos

```
shadows_l1 = 204   com_allow_30s = 83   pct = 40.7%
```

Com janela de 5min (health check): 92.9%.  
Com janela de 30s: **40.7%**.

Queda de 52pp ao reduzir a janela é evidência de **window bias**, não de censura ativa. Com o pipeline L3 rodando a cada ~1–2 min e emitindo ALLOWs para os mesmos símbolos da L1 watchlist, qualquer shadow criado em ±5min tem alta probabilidade de encontrar um ALLOW "correspondente" por coincidência — especialmente em mercados com alta taxa de aprovação.

**Critério do prompt:** pct_30s ≥ 80% = censura ativa. Resultado 40.7% < 80% → **não é censura**.

O pct_30s de 40.7% ainda está acima do limite de 30% esperado para capturas "puras". Mas o critério AMBÍGUO se aplica: não é claramente censura nem claramente puro.

### A1.2 — Distribuição de scores

```
features_snapshot de L1_SPECTRUM:
  com_features = 0 de 198 não-nulos
  Todas as linhas: features_snapshot = {}  (dict vazio)
```

Os shadows L1 têm `features_snapshot` **completamente vazio**. A causa está em `create_l1_spectrum_shadows` (shadow_trade_service.py linha 1388–1403):

```python
asset = assets_by_symbol.get(symbol, {})
flat_ind = _flatten_analysis_snapshot(asset.get("analysis_snapshot") or {})
```

O `assets_by_symbol` vem de `{a["symbol"]: a for a in passed}` onde `passed` é a lista de assets L1. Os assets L1 **não têm** `analysis_snapshot` preenchido — esse campo só é populado no estágio de análise L2/L3, após os indicadores serem calculados. No estágio L1, os assets só têm filtros estruturais.

**Consequência direta:** os 169+ shadows L1 fechados não têm features para alimentar o ML. Um `DatasetBuilder` operando sobre L1_SPECTRUM encontrará apenas NaNs/zeros para todos os 37 indicadores.

**Veredito A1:** AMBÍGUO na pureza (40.7% com 30s, provavelmente window bias). FAIL separado e mais grave: features_snapshot vazio em todos os L1 shadows — **o dataset de treino L1 é inutilizável como está**.

### A1.3 — Rastreamento de código

**pipeline_scan.py linha 2692–2714:**
```python
# L1_SPECTRUM capture — after upsert, before continue.
# Pureza invariant: no quality conditionals between here
# and shadow creation (only structural: sampling + reentry).
if effective_level == "L1":
    await create_l1_spectrum_shadows(
        symbols=[a["symbol"] for a in passed],   # ← L1-passed symbols
        execution_id=str(execution_id),
        assets_by_symbol={a["symbol"]: a for a in passed},
        ...
    )
    continue  # ← nunca chega ao bloco L3
```

A captura ocorre APÓS `evaluate_rejections(stage="L1")` e ANTES de qualquer avaliação L2/L3. O `continue` impede que o mesmo watchlist run chegue ao bloco L3. L1 e L3 são **pipelines separados** (watchlists distintas) rodando no mesmo universo de símbolos.

A variável `passed` contém símbolos que limparam o filtro L1 (filtro estrutural/posicional, não de qualidade). Isso é semanticamente correto para L1_SPECTRUM — não é um subconjunto de aprovados L3.

**A correlação de 40.7% com ALLOW em 30s** vem de: ambos os pipelines (L1 e L3) rodando no mesmo universo de símbolos com overlapping temporal. Não é censura.

---

## A2 — Clamp MAE/MFE Vivo

### Query segmentada (TS_FIX = 2026-06-10 18:45:57 UTC)

```
pos_fix=False  source=L3   mae_positivo=89  mfe_negativo=91  fechados=425
pos_fix=True   source=L1   mae_positivo=0   mfe_negativo=0   fechados=172
pos_fix=True   source=L3   mae_positivo=0   mfe_negativo=0   fechados=385
```

Todas as 89+91 violações são pré-fix. Pós-fix: **0 violações em ambos os sources**.

**Veredito A2: PASS.** O clamp está funcionando em todos os caminhos de fechamento. Os dados legados (pré-fix) têm 21% de violações e devem ser excluídos do dataset de treino. O filtro a aplicar no DatasetBuilder: `created_at > '2026-06-10 18:45:57+00'`.

---

## A3 — Bug do Sampler

### Causa raiz do erro no health check

O health check usou:
```sql
COUNT(*) FROM shadow_capture_skips WHERE skip_reason='SAMPLED_OUT'  -- retornou 671
```

Mas SAMPLED_OUT é logado **uma linha por ciclo** com N simbólico no campo `symbol`:
```python
# shadow_trade_service.py linha 1343-1356
"sym": f"[{len(sampled_out)} symbols]"   # ex: "[25 symbols]"
```

Já RUNNING_DUPLICATE é logado **um por símbolo** (linha 1422-1430).

### Taxa real (calculada corretamente)

```
total_sampled_out_symbols = SUM(N extraído de "[N symbols]") = 16,796
ciclos = 689
shadows_created = 199
running_dup = 1,687

sampled = 199 + 1,687 = 1,886  (symbols that passed the hash check)
total = 1,886 + 16,796 = 18,682

real_sample_rate = 1,886 / 18,682 = 10.1%  ✓
```

O sampler implementa corretamente `hash % 10000 < 1000` (shadow_trade_service.py linhas 1332–1336):
```python
_h = int(hashlib.sha256(f"{symbol}:{execution_id}".encode()).hexdigest(), 16) % 10000
if _h < int(sample_rate * 10000):  # int(0.10 * 10000) = 1000
    sampled.append(symbol)
else:
    sampled_out.append(symbol)
```

**Veredito A3: PASS.** Taxa empírica 10.1% ≈ configurado 10%. O W ARN do health check era resultado de uma fórmula de cálculo incorreta, não de um bug no código.

---

## A4 — Convenção Intrabar no Caminho L1

### Evidência empírica

```
L1 FECHADOS (pós-fix):  intrabar_convention = SL_FIRST, count = 174
```

### Rastreamento de código

Existe **um único caminho de fechamento** para todos os shadows (L1 e L3): `_finalize_outcome()` em `shadow_trade_monitor.py` (linha 384). Esta função:

1. Resolve outcome (TP_HIT / SL_HIT / TIMEOUT)
2. Preenche `barrier_touched` (TP / SL / NONE / BOTH_SAME_CANDLE)
3. Define `shadow.intrabar_convention = "SL_FIRST"` (linha 441) — **incondicional, todos os sources**
4. Calcula `net_return_pct` / `fee_roundtrip_pct_applied` do config_snapshot

O loop candle (linha 1090–1110) implementa SL_FIRST explicitamente:
```python
# SL antes de TP na mesma candle — convenção conservadora (SL_FIRST).
_sl_hit = c["low"] <= sl
_tp_hit = c["high"] >= tp
if _sl_hit:
    if _tp_hit:
        shadow.barrier_touched = "BOTH_SAME_CANDLE"  # ambas tocadas
    outcome = "SL_HIT"   # SL_FIRST: SL vence
    break
if _tp_hit:
    outcome = "TP_HIT"
    break
```

### Por que health check [9] mostrou NULL para 194 L1

O health check rodou às 13:02 UTC, quando a maioria dos L1 shadows ainda estava em status RUNNING (captura iniciou às 23:53 UTC do dia anterior). `intrabar_convention` só é setado no fechamento (`_finalize_outcome`). Shadows RUNNING têm a coluna NULL por design.

**Veredito A4: PASS.** SL_FIRST aplicado em 100% dos L1 fechados. Health check [9] era artefato de timing (shadows abertos). B4 vira apenas `intrabar_convention` cosmético para novos shadows — não há viés de label.

---

## A5 — Duplo Caminho L3 + EV do Autopilot

### Caminhos de criação de shadow L3

Existem **4 caminhos** que criam shadows L3, mas apenas **1** propaga `ml_fee_roundtrip_pct` para o config_snapshot:

| Caminho | Arquivo | ml_fee no config_snapshot? |
|---|---|---|
| `create_shadows_for_new_decisions` (inline após decisions_log) | shadow_trade_service.py linha 982 | ✅ SIM (fix B1, linha 1059) |
| `create_shadows_for_rejected_decisions` (L3_REJECTED inline) | shadow_trade_service.py linha 1103 | ✅ SIM (linha 1180) |
| `safe_backfill_watchlist_shadows` (monitor backfill) | shadow_trade_service.py linha 1446 | ❌ NÃO — user_config passado pelo caller sem ml_fee |
| `safe_bulk_create_from_user_skip` (gate de capital) | shadow_trade_service.py linha 756 | ❌ NÃO — sem carregamento de ML config |

### Evidência quantitativa

```
Pré-fix (antes 2026-06-10 18:46 UTC):
  L3 total=425  fechados=425  com_net=0  (0%)

Pós-fix:
  L3 total=414  fechados=386  com_net=145  (38%)
  com fee in config_snapshot: 149/414  (36%)
  → 265/414 criados via backfill/gate (sem fee no snapshot)
```

Os 145 com net_return são os criados via `create_shadows_for_new_decisions` (inline) que já fecharam. Os 265 restantes foram criados por backfill e nunca terão net_return (mesmo quando fecharem).

### EV do Autopilot usa pnl_pct, não net_return_pct

```python
# autopilot_engine.py linha 212:
AVG(pnl_pct) AS ev
```

O EV calculado pelo Autopilot é **gross** (antes da taxa de 0.20%). O EV líquido real é `EV_gross - 0.20pp`. Portanto:

- EV reportado = −0.XX% (hipotético)
- EV líquido real ≈ reportado − 0.20%
- Trigger de mutação: `approved_ev < ev_min_threshold_pct = 0.0%`

Com o trigger em 0.0%, um EV bruto de +0.10% (= líquido −0.10%) NÃO aciona mutação, embora devesse. O sistema está subestimando a degradação real em 0.20pp.

**Veredito A5: FAIL (dois problemas):**

1. **EV inflado 0.20pp no Autopilot**: usa `pnl_pct` (bruto) em vez de `net_return_pct` (líquido). O trigger `ev_min_threshold_pct = 0.0%` deveria ser `0.20%` para compensar, ou o EV query deve trocar para `net_return_pct`.

2. **Backfill path sem fee propagation**: `safe_backfill_watchlist_shadows` não carrega ML config → `ml_fee_roundtrip_pct` ausente no config_snapshot → `net_return_pct` nunca set ao fechar. 36% dos pós-fix L3 têm fee; 64% não têm.

---

## Síntese — O Que o Prompt B Deve Corrigir

### B1 — Features snapshot vazio em L1 (PRIORIDADE MÁXIMA)

`create_l1_spectrum_shadows` usa `assets_by_symbol[symbol]["analysis_snapshot"]` mas os assets L1 não têm indicadores calculados. O shadow L1 precisa ler os indicadores do banco (via `indicators_provider.get_merged_indicators`) assim como o `create_watchlist_spot_shadows` já faz (linha 2053–2062 do shadow_trade_service).

Sem esse fix, o dataset L1 é inutilizável (37 features = NaN/zero).

### B5a — Autopilot EV: trocar coluna ou ajustar threshold

Opção 1: trocar `AVG(pnl_pct)` por `AVG(COALESCE(net_return_pct, pnl_pct - 0.20))` na query do autopilot_engine.py (linha ~212). Menos disruptivo enquanto nem todos os shadows têm net_return.

Opção 2: ajustar `ev_min_threshold_pct` de `0.0` para `0.20` no seed_autopilot_guardrails.sql. Mantém pnl_pct mas corrige o threshold.

### B5b — Backfill path propagar ml_fee

`safe_backfill_watchlist_shadows` precisa carregar ML config antes de montar user_config, idêntico ao que `create_shadows_for_new_decisions` já faz (linhas 1029–1038 + 1059).

### Não-itens para Prompt B

| Item | Razão |
|---|---|
| A2 MAE/MFE | PASS — fix já funciona |
| A3 sampler | PASS — código correto, health check estava errado |
| A4 intrabar | PASS — SL_FIRST aplicado; NULL no health check era timing artefact |
| A1 pureza | AMBÍGUO mas não censura — B1 (features) é o fix que importa |
| L3 pré-fix sem net_return | Legado irrecuperável; filtrar por `created_at > '2026-06-10 18:46+00'` no DatasetBuilder |

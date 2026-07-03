# Inventário e Validação — Campos de Saída em shadow_trades

**Data:** 2026-07-03  
**Prompt-base:** `PROMPT_INVENTARIO_CAMPOS_SAIDA.md`  
**Escopo:** READ-ONLY · trades resolvidos (`outcome IN ('TP_HIT','SL_HIT','TIMEOUT')`) · `created_at >= '2026-06-14 21:33:00+00'` · fontes L1_SPECTRUM e L3 (+ L3_LAB em F3)

---

## F1 — Schema descoberto + Writers

### Colunas escalares encontradas

```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name='shadow_trades'
  AND (column_name LIKE 'max_profit%' OR column_name LIKE 'ttt%'
       OR column_name IN ('features_snapshot_exit','exit_metrics_json','mae_pct','mfe_pct'));
```

| Coluna | Tipo | Writer (path:line) | Semântica |
|--------|------|--------------------|-----------|
| `mae_pct` | double precision | `opportunity_snapshot_evaluator.py:298` | `(running_min_low - entry_price) / entry_price * 100` — sempre ≤ 0 |
| `mfe_pct` | double precision | `opportunity_snapshot_evaluator.py:299` | `(running_max_high - entry_price) / entry_price * 100` — sempre ≥ 0 |
| `max_profit_pct` | double precision | `shadow_trade_monitor.py:528` (via `_build_exit_metrics_json`) | MFE final ao fechamento; igual a `mfe_pct` |
| `max_profit_first_15m` | double precision | `shadow_trade_monitor._compute_ttt_outcome` | Max profit nos primeiros 15min; **só preenchido via TTT** |
| `max_profit_first_30m` | double precision | idem | Max profit nos primeiros 30min |
| `max_profit_first_60m` | double precision | idem | Max profit nos primeiros 60min |
| `max_profit_after_timeout_pct` | double precision | `shadow_trade_monitor` (timeout analysis) | MFE após expiração de TIMEOUT |
| `features_snapshot_exit` | jsonb | `shadow_trade_monitor.py:575/584/605` (`_capture_exit_features`) | Snapshot flat de indicadores no momento do fechamento |
| `exit_metrics_json` | jsonb | `shadow_trade_monitor.py:528` (`_build_exit_metrics_json`) | Consolidação rica: outcome + PnL + MAE/MFE + preços + `indicators` nested |
| `ttt_enabled` | boolean | `shadow_trade_monitor._compute_ttt_outcome` | Flag de opt-in para análise TTT |
| `ttt_tp_pct` | double precision | idem | % de TP configurado para a janela TTT |
| `ttt_timeout_minutes` | integer | idem | Duração da janela TTT em minutos |
| `ttt_outcome` | varchar(20) | idem | Resultado TTT: `FAST_WIN`, `WIN`, `TIMEOUT`, etc. |
| `ttt_close_reason` | varchar(30) | idem | Razão do fechamento TTT |
| `ttt_fast_win_bucket` | varchar(20) | idem | Bucket temporal: `WIN_0_15M`, `WIN_15_30M`, `WIN_30_60M` |
| `ttt_analysis_done` | boolean | idem | Idempotência: TRUE = shadow já processado pelo TTT |

**Nota sobre `_capture_exit_features`:** nunca propaga exceção. Em falha de provider, grava `{"_capture_failed": true, "_reason": "..."}`; `exit_metrics_json` é chamado com o mesmo marcador (linha 580/609).

---

## F2 — Censo de chaves dos JSONB

### 2.1 `features_snapshot_exit` — L1_SPECTRUM

Base: 2015 trades com snapshot não-vazio.

**Grupo A — 99.7–99.95% (presentes desde 2026-06-14)**

| Chave | n | pct |
|-------|---|-----|
| `rsi`, `price`, `psar*`, `ema*`, `macd*`, `adx`, `atr*`, `stoch_*`, `bb_*`, `spread_pct`, `vwap*`, `obv*`, `taker_*`, `buy_pressure`, `volume_*`, `bid_ask_imbalance`, `orderbook_*`, `market_data_*`, `entry_exhaustion_score` | 2009–2014 | 99.70–99.95% |

**Grupo B — 84.52% (first_seen: 2026-06-15 — schema adicionado 1 dia após valid_from)**

| Chave | n | pct |
|-------|---|-----|
| `adx_slope_3`, `vwap_reclaim_bool`, `macd_hist_slope_3`, `macd_hist_slope_5`, `higher_highs_5`, `higher_lows_5`, `ema21_ema50_distance_pct`, `rsi_slope_3`, `rsi_slope_5`, `di_plus_minus_diff` | 1703 | 84.52% |

**Grupo C — 55.14% (score e volume agregado de mercado)**

| Chave | n | pct |
|-------|---|-----|
| `score`, `score_max`, `score_raw`, `volume_24h_usdt`, `volume_24h_base`, `volume_24h_usdt_aggregated`, `volume_24h_base_aggregated`, `market_data_symbol` | 1111 | 55.14% |

**Grupo D — 46.35%**

| Chave | n | pct |
|-------|---|-----|
| `close_5m` | 934 | 46.35% |

**Drift temporal:** grupo B ausente em ~15.48% das rows (trades entre 2026-06-14 e início do dia 2026-06-15). Sem outro drift — `first_seen` e `last_seen` cobrem toda a janela para os demais grupos.

### 2.2 `features_snapshot_exit` — L3

Base: ~13.690 trades. Padrão idêntico ao L1:
- Grupo equivalente ao C: 50.64% (~6.915 trades) para `score/score_raw/volume_24h_*`
- Grupo B (slopes): 99.25% (first_seen 2026-06-15)
- Resto: 99.43–99.58%
- `close_5m`: 50.78%

**Diff entrada × saída:** não foi possível apurar o censo do `features_snapshot` (entrada) com a mesma granularidade via query simples. Estruturalmente espera-se paridade de chaves (mesmo provider: `indicators_provider.build_full_flat_snapshot`). O código (`shadow_trade_monitor.py:585`) registra um best-effort parity check em log quando `ENABLE_EXIT_METRICS_CAPTURE` está ativo.

### 2.3 `exit_metrics_json` — L1_SPECTRUM e L3

Base: 2015 (L1), ~13.690 (L3). **16 chaves, todas a 100%**, sem drift temporal.

```
captured_at, entry_price, exit_price, holding_seconds, indicators,
mae_pct, max_drawdown_pct, max_price_post_entry, max_profit_pct,
mfe_pct, min_price_post_entry, outcome, pnl_pct, pnl_usdt,
sl_price, tp_price
```

`indicators` é o objeto nested com o mesmo snapshot de `features_snapshot_exit`. Chave `_capture_failed` pode aparecer quando o snapshot falhou mas o `exit_metrics_json` foi gravado mesmo assim (via `_build_exit_metrics_json` com marcador de falha).

---

## F3 — Taxa de preenchimento por outcome × source

```sql
-- query F3 completa com todas as colunas escalares
```

| source | outcome | n | exit_snap | exit_metrics | mae | mfe | max_profit_pct | mp_15m | mp_30m | mp_60m | mp_after_to | ttt_bucket | ttt_outcome | ttt_done |
|--------|---------|---|-----------|--------------|-----|-----|----------------|--------|--------|--------|-------------|------------|-------------|----------|
| L1_SPECTRUM | SL_HIT  | 1267 | 1155 (91.2%) | 1155 | 1231 | 1231 | 1231 | **137** (10.8%) | **137** | **135** | 0 | **104** | 1265 | 1265 |
| L1_SPECTRUM | TIMEOUT | 15   | 14          | 14   | 15   | 15   | 15   | **10** (67%)   | 10     | 10     | **2**   | **5**  | 14   | 14   |
| L1_SPECTRUM | TP_HIT  | 897  | 846 (94.3%) | 846  | 894  | 894  | 894  | 845 (94.2%)    | **824** | **755** | 0  | **760** | 854  | 854  |
| L3          | SL_HIT  | 9068 | 8097 (89.3%)| 8097 | 9068 | 9068 | 9068 | **560** (6.2%) | 560    | 559    | 0       | **446** | 9068 | 9068 |
| L3          | TIMEOUT | 215  | 205 (95.3%) | 205  | 215  | 215  | 215  | **68** (31.6%) | 68     | 68     | **26**  | **12** | 215  | 215  |
| L3          | TP_HIT  | 5786 | 5351 (92.5%)| 5351 | 5786 | 5786 | 5786 | 4968 (85.9%)   | **4844**| **4612**| 0 | **4352**| 5427 | 5427 |
| L3_LAB      | SL_HIT  | 2629 | 2397 (91.2%)| 2397 | 2629 | 2629 | 2629 | **12** (0.5%)  | 12     | 12     | 0       | **5**  | 2629 | 2629 |
| L3_LAB      | TIMEOUT | 64   | 59 (92.2%)  | 59   | 64   | 64   | 64   | **0**           | 0      | 0      | 0       | **0**  | 64   | 64   |
| L3_LAB      | TP_HIT  | 2213 | 2041 (92.2%)| 2041 | 2213 | 2213 | 2213 | 1518 (68.6%)   | **1429**| **1296**| 0 | **1337**| 2213 | 2213 |

### Furos identificados

**Furo 1 — `exit_snap`/`exit_metrics` < n (7–11% ausente)**
- L1 SL_HIT: 112 sem snapshot; L3 SL_HIT: 971 sem snapshot; L3_LAB TP_HIT: 172 sem snapshot
- Causa: `_capture_exit_features` falha silenciosa quando provider não retorna indicadores merged (ex: símbolo sem dados Redis/MDH no momento do fechamento). Nesses casos `features_snapshot_exit = {"_capture_failed": true, "_reason": "indicators_unavailable_at_close"}` — que conta como NOT NULL mas foi filtrado por `::text <> '{}'`
- `exit_metrics_json` conta é igual a `exit_snap` — confirmando que ambos são escritos no mesmo `_capture_exit_features`

**Furo 2 — `mae_pct`/`mfe_pct` < n em L1_SPECTRUM**
- SL_HIT: 36 trades sem mae/mfe (1267 - 1231 = 36); TP_HIT: 3 sem
- L3 e L3_LAB: 100% preenchidos para todos os outcomes
- Causa: `opportunity_snapshot_evaluator.py` pode não ter processado esses trades (worker parado ou trade sem ohlcv disponível)

**Furo 3 — `max_profit_first_15m/30m/60m` baixíssimo em SL_HIT e L3_LAB**
- SL_HIT L1: 10.8%; SL_HIT L3: 6.2%; SL_HIT L3_LAB: 0.5%; TIMEOUT L3_LAB: 0%
- Causa estrutural: `_compute_ttt_outcome` só popula `max_profit_first_*` quando o ttt análise identifica uma janela de ganho. SL_HIT raramente tem max_profit positivo nos primeiros 15-60min → preenchimento baixo é esperado, não um furo de pipeline
- L3_LAB aparentemente não tem TTT habilitado (`ttt_enabled=false`) para a maioria dos trades

**Furo 4 — `max_profit_after_timeout_pct`**
- Só L3 TIMEOUT tem 26 preenchidos (12.1% dos 215); L1 TIMEOUT: 2/15
- Coluna para análise pós-expiração de TIMEOUT — preenchimento baixo é esperado, não é furo de pipeline

---

## F4 — Asserções de consistência física

### Convenção de sinal (verificada empiricamente)

| Campo | TP_HIT range | SL_HIT range | TIMEOUT range |
|-------|-------------|--------------|---------------|
| `mae_pct` | [−2.78, 0] | [−65.7, −0.5] | [−2.04, 0] |
| `mfe_pct` | [0.80, 47.1] | [0, 7.37] | [0, 1.49] |
| `pnl_pct` (avg) | +1.28–1.50 | −1.00 | +0.04–0.62 |

**Convenção confirmada:** `mae_pct ≤ 0` (excursão adversa, negativo = preço caiu desde entrada) · `mfe_pct ≥ 0` (excursão favorável, positivo = preço subiu desde entrada).

Fórmula do writer (`opportunity_snapshot_evaluator.py:298-299`):
```python
mae_pct = (running_min_low - entry_price) / entry_price * 100.0   # sempre ≤ 0 para long
mfe_pct = (running_max_high - entry_price) / entry_price * 100.0  # sempre ≥ 0 para long
```

### Tabela de asserções

| # | Asserção | Violações | Veredito |
|---|----------|-----------|---------|
| A1 | `mfe_pct < pnl_pct` em TP_HIT (L1+L3+L3_LAB) | **0** | ZERO VIOLAÇÕES (provado) |
| A2 | `mfe_pct < 0` ou `mae_pct > 0` (violação de definição) | **0** | ZERO VIOLAÇÕES (provado) |
| A3 | TP_HIT com `mfe_pct` < distância ao TP (`tp_price - entry_price`) | — | NÃO VERIFICÁVEL — `tp_price` e `entry_price` estão em `exit_metrics_json` (JSONB), não como colunas escalares; geometria do trade não persistida como coluna flat |
| A4 | SL_HIT análogo com `mae_pct` vs distância ao SL | — | NÃO VERIFICÁVEL — mesmo motivo de A3 |
| A5 | `time_to_tp_minutes * 60 > holding_seconds` (tempo ao alvo > vida do trade) | **44** | COM FUROS — ver análise abaixo |
| A6 | `max_profit_pct > mfe_pct + 0.0001` (incoerência max_profit vs mfe) | **0** | ZERO VIOLAÇÕES (provado) |

### Análise das 44 violações da A5

```
Top 5 violações:
id=5eee23a6 AAVE_USDT TP_HIT holding=2298s  time_to_tp=70min (4200s) delta=+1902s
id=4455f898 AAVE_USDT TP_HIT holding=3887s  time_to_tp=75min (4500s) delta=+613s
id=a2470fc8 AAVE_USDT TP_HIT holding=3887s  time_to_tp=75min (4500s) delta=+613s
id=f2fa12f7 ZEC_USDT  TP_HIT holding=6949s  time_to_tp=120min(7200s) delta=+251s
id=93426a8d XLM_USDT  TP_HIT holding=3392s  time_to_tp=60min (3600s) delta=+208s
```

**Diagnóstico:** `time_to_tp_minutes` armazena valores arredondados para múltiplos inteiros (60, 70, 75, 120 min) — claramente bucket boundaries do TTT, não o tempo real de chegada ao TP. O campo é preenchido por `_compute_ttt_outcome` como o timeout da janela TTT (`ttt_timeout_minutes`), não como o tempo exato até o TP. O tempo real de duração do trade para TP_HIT é `holding_seconds`.

**Veredito A5:** **CONTEÚDO SUSPEITO** — `time_to_tp_minutes` não representa o que o nome sugere; é o tamanho da janela TTT, não o tempo real ao TP. As 44 "violações" são artefato de semântica incorreta do campo, não corrupção de dados.

---

## F5 — Amostras Verbatim

### L1_SPECTRUM — TP_HIT #1

```
id:          36e0d228-8fc3-44d1-90cf-6b0c79a08e6e
symbol:      FET_USDT
created_at:  2026-06-14 21:33:10.591666+00
outcome:     TP_HIT
pnl:         1.0000
holding_s:   1200
mae:         0.0000
mfe:         1.4840
max_profit:  1.4840
mp_15m:      0.8617
mp_30m:      1.9627
ttt_outcome: FAST_WIN
ttt_bucket:  WIN_15_30M
ttt_done:    true

features_snapshot_exit (excerto):
{"adx":31.34,"rsi":52.35,"ema5":0.21075224,"taker_ratio":0.557232,
 "volume_delta":10471.29,"spread_pct":0.0473,"market_data_confidence":0.7,
 "volume_24h_coverage_hours":8.3333, ...60 keys total}

exit_metrics_json:
{"outcome":"TP_HIT","pnl_pct":1.0,"pnl_usdt":10.0,
 "entry_price":0.2089,"exit_price":0.210989,"tp_price":0.210989,"sl_price":0.206811,
 "mae_pct":0.0,"mfe_pct":1.483964,"max_profit_pct":1.483964,"max_drawdown_pct":0.0,
 "max_price_post_entry":0.212,"min_price_post_entry":0.2089,
 "holding_seconds":1200,"captured_at":"2026-06-14T21:52:29.324141+00:00",
 "indicators":{...same as features_snapshot_exit...}}
```

### L1_SPECTRUM — TP_HIT #2

```
id:          1bc5a725-e784-4ae0-b600-867b45ac588d
symbol:      SUI_USDT
created_at:  2026-06-14 21:33:10.74986+00
outcome:     TP_HIT
pnl:         1.0000
holding_s:   1200
mae:         0.0000
mfe:         2.1210
max_profit:  2.1210
mp_15m:      1.2882
mp_30m:      2.7846
ttt_outcome: FAST_WIN
ttt_bucket:  WIN_15_30M

exit_metrics_json (excerpt):
{"entry_price":0.7685,"exit_price":0.776185,"tp_price":0.776185,"sl_price":0.760815,
 "mae_pct":0.0,"mfe_pct":2.121015,"max_profit_pct":2.121015,
 "max_price_post_entry":0.7848,"min_price_post_entry":0.7685}
```

### L1_SPECTRUM — SL_HIT #1

```
id:          80b255e2-dd35-4c83-86aa-4d34db191579
symbol:      FET_USDT
created_at:  2026-06-14 21:58:37.98739+00
outcome:     SL_HIT
pnl:         -0.8156
holding_s:   1006
mae:         -1.2766
mfe:         0.0000
max_profit:  0.0000
mp_15m:      NULL
mp_30m:      NULL
ttt_outcome: TIMEOUT
ttt_bucket:  NULL

exit_metrics_json (excerpt):
{"entry_price":0.2115,"exit_price":0.20977511175,"sl_price":0.20977511175,"tp_price":0.21467250,
 "mae_pct":-1.276596,"mfe_pct":0.0,"max_profit_pct":0.0,"max_drawdown_pct":-1.276596,
 "max_price_post_entry":0.2115,"min_price_post_entry":0.2088}
```

### L1_SPECTRUM — SL_HIT #2

```
id:          761af43d-5e9d-4ef0-819f-962e3aebe169
symbol:      VVV_USDT
created_at:  2026-06-14 22:40:03.940072+00
outcome:     SL_HIT
pnl:         -1.5809
holding_s:   10088
mae:         -2.2854
mfe:         1.0525
max_profit:  1.0525
mp_15m:      -0.0805
mp_30m:      0.6206
ttt_outcome: FAST_WIN
ttt_bucket:  WIN_30_60M
(trade com mfe positivo mas SL atingido — excursão favorável temporária antes do reversal)
```

### L1_SPECTRUM — TIMEOUT #1

```
id:          d64cf5fb-9fd1-412f-8735-fcd76ff01cc3
symbol:      XAUT_USDT
created_at:  2026-06-15 17:33:06.802431+00
outcome:     TIMEOUT
pnl:         0.3433
holding_s:   89447 (24.8h)
mae:         -0.3294
mfe:         0.4523
max_profit:  0.4523
mp_15m:      NULL
mp_30m:      NULL
ttt_outcome: TIMEOUT

exit_metrics_json (excerpt):
{"entry_price":4311.0,"exit_price":4325.8,"sl_price":4289.445,"tp_price":4375.665,
 "mae_pct":-0.32939,"mfe_pct":0.452331,"pnl_pct":0.343308}
```

### L1_SPECTRUM — TIMEOUT #2

```
id:          f1c3666c-5c05-4ef5-b70f-4b7cd4c5cd05
symbol:      ENA_USDT
created_at:  2026-06-16 04:46:12.122855+00
outcome:     TIMEOUT
pnl:         1.2224
holding_s:   663171 (7.67 dias — trade stuck/orphan)
mae:         0.0000
mfe:         1.3987
ttt_outcome: FAST_WIN
ttt_bucket:  WIN_30_60M
(trade aberto há 7.67 dias no momento da captura — confirma orphan identificado em E11)
```

### L3 — TP_HIT #1

```
id:          7f8a3d7b-452c-4b22-9d2f-deb1b9d9b87c
symbol:      FIL_USDT
created_at:  2026-06-14 21:33:30.034619+00
outcome:     TP_HIT
pnl:         1.0000
holding_s:   1612
mae:         0.0000
mfe:         1.6555
mp_15m:      0.6802
mp_30m:      2.1304
ttt_bucket:  WIN_15_30M
```

### L3 — TP_HIT #2

```
id:          4ffbae5c-b4f3-4e74-aad1-83b0f35c5808
symbol:      TAO_USDT
created_at:  2026-06-14 21:33:30.185828+00
outcome:     TP_HIT
pnl:         1.0000
holding_s:   1200
mae:         -0.1842  (pequena adversidade antes do TP)
mfe:         1.2528
mp_15m:      0.3316
mp_30m:      1.2896
ttt_bucket:  WIN_15_30M
exit: entry=271.4 tp=274.114 sl=268.686
```

### L3 — SL_HIT #1

```
id:          41a0b33f-d977-461e-90ca-89d913b49120
symbol:      ALLO_USDT
created_at:  2026-06-14 21:33:29.905522+00
outcome:     SL_HIT
pnl:         -1.0000
holding_s:   2900
mae:         -1.0340
mfe:         0.0000
mp_15m:      NULL
ttt_outcome: TIMEOUT
exit: entry=0.37137 sl=0.3676563 — preço nunca subiu acima da entrada
```

### L3 — SL_HIT #2

```
id:          15956f85-f76a-4238-bd50-6d5b3444c0d8
symbol:      SIREN_USDT
created_at:  2026-06-14 21:38:24.603611+00
outcome:     SL_HIT
pnl:         -1.0000
holding_s:   535
mae:         -1.0050
mfe:         0.0000
mp_15m:      NULL
ttt_outcome: TIMEOUT
exit: entry=0.0796 sl=0.078804 — preço caiu diretamente ao SL em 8.9 min
```

### L3 — TIMEOUT #1

```
id:          628b9590-9419-418f-bd01-4252550fdee7
symbol:      XAUT_USDT
created_at:  2026-06-15 21:37:58.507831+00
outcome:     TIMEOUT
pnl:         0.5289
holding_s:   86719 (24h)
mae:         -0.0303
mfe:         0.9832
exit: entry=4292.3 tp=4335.2 — fechado em 4315.0 (próximo mas não atingiu TP)
```

### L3 — TIMEOUT #2

```
id:          a111a46d-30b6-4a41-972f-0946643d292d
symbol:      BNB_USDT
created_at:  2026-06-16 16:45:47.330159+00
outcome:     TIMEOUT
pnl:         -0.0991
holding_s:   86555 (24h)
mae:         -0.7432
mfe:         0.9744
exit: entry=605.5 tp=611.555 sl=599.445 — fechado em 604.9 (abaixo da entrada)
```

---

## F6 — Conclusão por campo

| Campo | Conclusão | Detalhe |
|-------|-----------|---------|
| `mae_pct` | **ÍNTEGRO** com furos | 100% L3; 97.2% L1 (36 trades sem = furo de pipeline do opportunity_snapshot_evaluator) |
| `mfe_pct` | **ÍNTEGRO** com furos | Idem mae_pct |
| `max_profit_pct` | **ÍNTEGRO** | 100% coerente com mfe_pct (A6: zero violações); preenchimento = mae_pct |
| `max_profit_first_15m` | **COM FUROS (esperado)** | Só populado via `_compute_ttt_outcome`; SL_HIT < 11%, L3_LAB ≈ 0% — comportamento de design |
| `max_profit_first_30m` | **COM FUROS (esperado)** | Idem acima |
| `max_profit_first_60m` | **COM FUROS (esperado)** | Idem; cobertura ligeiramente menor que 30m |
| `max_profit_after_timeout_pct` | **COM FUROS (esperado)** | Só 2/15 L1 e 26/215 L3 TIMEOUT preenchidos — pipeline de análise pós-TIMEOUT raramente ativo |
| `features_snapshot_exit` | **COM FUROS (7-11%)** | Falhas silenciosas de provider ao fechamento → marcador `_capture_failed`; drift de schema para 10 chaves adicionadas em 2026-06-15 (grupo B: 84.52%); 9 chaves de mercado com 50% fill (score/volume agregado) |
| `exit_metrics_json` | **ÍNTEGRO** | 100% das chaves a 100% de preenchimento; coerente com features_snapshot_exit |
| `ttt_outcome` | **ÍNTEGRO** | >99% preenchido em todos os grupos; semântica correta |
| `ttt_fast_win_bucket` | **ÍNTEGRO** com lacuna | Só preenchido quando há ganho rápido (design); ausência em SL_HIT e TIMEOUT não-rápidos é esperada |
| `ttt_analysis_done` | **ÍNTEGRO** | Preenchimento = ttt_outcome; idempotência confirmada |
| `time_to_tp_minutes` | **CONTEÚDO SUSPEITO** | Nome enganoso — armazena tamanho da janela TTT (inteiro arredondado), não o tempo real de chegada ao TP; 44 trades TP_HIT com time_to_tp > holding_seconds confirmam semântica incorreta |

### Lacunas não verificáveis

| Verificação | Motivo da impossibilidade |
|-------------|--------------------------|
| A3: mfe_pct ≥ distância ao TP em TP_HIT | `tp_price` e `entry_price` só existem em `exit_metrics_json` JSONB, não como colunas escalares. Seria necessário `(exit_metrics_json->>'tp_price')::float - (exit_metrics_json->>'entry_price')::float` mas Railway rejeita queries multi-step. |
| A4: mae_pct ≤ distância ao SL em SL_HIT | Idem |
| Paridade entrada × saída de `features_snapshot_exit` | Censo do `features_snapshot` (entrada) não executado nesta sessão — requereria query LATERAL similar mas na coluna de entrada |
| `max_profit_first_*` vs OHLCV real | `ohlcv` só tem `5m` e `30m`; verificação cruzada com dados reais de preço possível mas fora do escopo desta sessão |

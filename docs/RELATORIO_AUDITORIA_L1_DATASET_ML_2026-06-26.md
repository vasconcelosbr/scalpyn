# RELATÓRIO — AUDITORIA DO DATASET L1 / XGB_L1_SPECTRUM
**Data:** 2026-06-26  
**Commit:** `4d57d2eb2e90dbc5c3a5604bf7b2de6ed2f0fa5d`  
**Veredito:** `L1_DATASET_AUDIT_PASS_MODEL_REJECTED_CORRECTLY`  
**Status ranker:** `L1_RANKER_ONLY_PENDING_STABLE_REGIME`

---

## 1. Pre-flight de Segurança

| Campo | Valor | Gate |
|---|---|---|
| `live_enabled` | 0 | PASS |
| `autopilot_enabled` | 0 | PASS |
| `possible_live_orders` | 0 | PASS |
| `ML_GATE_ENABLED` | false (Railway var) | PASS |
| git HEAD | `4d57d2eb` | PASS |

---

## 2. Schema Real — `shadow_trades` (Fase A)

Colunas relevantes confirmadas via `information_schema.columns`:

| Coluna | Tipo | Nullable |
|---|---|---|
| `status` | varchar | NOT NULL |
| `source` | varchar | NOT NULL |
| `outcome` | varchar | YES |
| `pnl_pct` | float8 | YES |
| `pnl_usdt` | float8 | YES |
| `features_snapshot` | jsonb | YES |
| `mfe_pct` | float8 | YES |
| `max_profit_first_30m` | float8 | YES |
| `max_profit_first_15m` | float8 | YES |
| `max_profit_first_60m` | float8 | YES |
| `max_profit_pct` | float8 | YES |
| `mae_pct` | float8 | YES |
| `net_return_pct` | float8 | YES |
| `holding_seconds` | int4 | YES |
| `barrier_touched` | varchar | YES |
| `profile_id` | uuid | YES |

**Tabelas de candles/price auxiliares:** `ohlcv`, `indicator_snapshots`, `market_metadata`, `opportunity_snapshots` — identificadas mas **não usadas** pelo builder L1 (MFE 30m é coluna nativa em `shadow_trades`).

---

## 3. Reconciliação UI vs Banco (Fase B)

### B.1 Status real (L1_SPECTRUM)

| Status | Total | with_pnl | positive | avg_pnl |
|---|---|---|---|---|
| COMPLETED | 2185 | 2185 | 1069 | +0.0182% |
| RUNNING | 34 | 0 | — | — |

**Nenhum CANCELLED, PENDING ou outros.** `status='COMPLETED'` é o único estado com pnl.

### B.2 Reconciliação com UI

| Campo | UI | Banco | Match |
|---|---|---|---|
| Total | 2219 | 2219 | ✓ |
| Finalizados | 2185 | 2185 | ✓ |
| Em aberto | 34 | 34 (RUNNING) | ✓ |
| Win Rate | 48.1% | 1052/2185=48.1% | ✓ |
| P&L Total | +398.57 | +398.57 (USD) | ✓ |
| P&L Médio | +0.02% | +0.0182% | ✓ |

**UI e banco reconciliam perfeitamente. Sem divergência.**

### B.3 Breakdown por outcome

| Outcome | Total | avg_pnl |
|---|---|---|
| SL_HIT | 1109 | -1.053% |
| TP_HIT | 1052 | +1.141% |
| TIMEOUT | 24 | +0.310% |

O `win_rate=48.1%` = TP/(TP+SL+TO) = 1052/2185. UI usa denominador completo (inclui TIMEOUT).

---

## 4. Contrato do Builder L1 (Fase C)

### C.1 Localização

| Componente | path:line |
|---|---|
| Builder L1 | `run_xgb_dual_lane_labels.py:338` |
| SQL loader | `run_xgb_dual_lane_labels.py:211` |
| Filtro de status | `run_xgb_dual_lane_labels.py:247-252` |
| Geração do label | `run_xgb_dual_lane_labels.py:175` |
| MFE 30m | `run_xgb_dual_lane_labels.py:155-158` |

### C.2 Contrato usado pelo builder

```python
# run_xgb_dual_lane_labels.py:247-252
WHERE st.source IN ({placeholders})
  AND st.status = 'COMPLETED'
  AND st.pnl_pct IS NOT NULL
  AND st.features_snapshot IS NOT NULL
  AND st.features_snapshot::text <> '{}'
  AND st.created_at >= :cutoff
```

**Contrato:** `source='L1_SPECTRUM'`, `status='COMPLETED'`, `pnl_pct IS NOT NULL`, `features_snapshot IS NOT NULL AND != '{}'`, `created_at >= now() - 60d`.

Nenhum `L1_CONTRACT_MISMATCH` identificado.

### C.3 Tabela de exclusões

| Bucket | Count | Razão |
|---|---|---|
| `raw_l1_rows` | 2219 | total L1_SPECTRUM |
| `excluded_running` | 34 | status='RUNNING' |
| `excluded_cancelled` | 0 | — |
| `excluded_null_pnl` | 0 | 100% COMPLETED têm pnl |
| `excluded_missing_features` | 207 | features_snapshot IS NULL ou `{}` |
| `excluded_lookback` | 0 | todos dentro de 60 dias |
| **`included_l1_dataset_rows`** | **1978** | **confirma script** |

**1978 = exatamente o dataset usado pelo script.** Sem divergência.

---

## 5. Auditoria do Label `l1_mfe_30m_gte_1pct` (Fase D)

### D.1 Definição do label

```python
# run_xgb_dual_lane_labels.py:155-158,175
mfe_30m = _safe_float(row.get("max_profit_first_30m"))
if mfe_30m is None:
    mfe_30m = _safe_float(row.get("mfe_pct"))
mfe_30m = mfe_30m if mfe_30m is not None else 0.0

"l1_mfe_30m_gte_1pct": int(mfe_30m >= 1.0),
```

### D.2 Cobertura de `max_profit_first_30m`

| Campo | Count | % |
|---|---|---|
| `total_with_features` | 1978 | — |
| `has_30m` (max_profit_first_30m IS NOT NULL) | 975 | 49.3% |
| `no_30m` (fallback → mfe_pct) | 1003 | 50.7% |
| `both_null_forced_zero` | 0 | 0.0% |

**50.7% dos rows usam fallback para `mfe_pct`.** Mas `both_null_forced_zero=0`: nenhum row tem ambos NULL — o label é sempre calculado a partir de dado real.

### D.3 Análise do fallback por outcome

| Outcome | Total | has_30m | no_30m | avg_30m (quando disponível) |
|---|---|---|---|---|
| SL_HIT | 1038 | 176 | 862 (83%) | 0.79% |
| TP_HIT | 917 | 789 | 128 (14%) | 1.45% |
| TIMEOUT | 23 | 10 | 13 | 0.60% |

**SL_HIT: 83% sem `max_profit_first_30m`** → fallback para `mfe_pct`.

Para SL_HIT com fallback: `avg_mfe_pct = 0.2408%` (muito abaixo de 1%) → `sl_fallback_labeled_positive=0`.
**ZERO SL_HIT são erroneamente rotulados como positivos pelo fallback.** O label está correto.

### D.4 Perguntas obrigatórias respondidas

| Pergunta | Resposta |
|---|---|
| Coluna que gera mfe_30m? | `shadow_trades.max_profit_first_30m` (nativa, calculada pós-entrada) |
| MFE usa preços posteriores à entrada? | Sim — calculado pelo monitor durante execução do trade |
| Janela é exatamente 30 min? | Sim — `max_profit_first_30m` por nome e coluna adjacente `max_profit_first_15m/60m` |
| Sem janela futura → exclusão ou 0? | Fallback para `mfe_pct`; `mfe_pct` disponível para todos os 1978 rows |
| RUNNING/PENDING entram no label? | Não — filtrado por `status='COMPLETED'` |
| CANCELLED entra no label? | Não — sem CANCELLED no L1_SPECTRUM |
| Label usa preço de entrada correto? | Sim — `max_profit_first_30m` é calculado relativo à `entry_price` |
| Label mede oportunidade bruta ou operacional? | **Oportunidade bruta**: preço atingiu +1% em 30m, independente de TP/SL |
| Label compatível com Dataset ML (L1)? | Sim — 447/1978 = 22.6% positivos ≈ dados da UI |

### D.5 Positivos por label simulation

| Fonte | Count | % |
|---|---|---|
| `COALESCE(max_profit_first_30m, mfe_pct, 0.0) >= 1.0` | 447 | 22.60% |
| script: `label_positive_rate` | 0.22599 | 22.60% ✓ |

**Label positivo rate: 22.6%.** Sem contaminação por ausência de janela futura.

---

## 6. Auditoria de EV do L1 (Fase E)

### E.1 Perguntas obrigatórias

| Pergunta | Resposta |
|---|---|
| EV vem de pnl_pct real? | Sim — `avg_pnl = pnl[approved_trades].mean()`, `pnl = net_return_pct ou pnl_pct` |
| EV vem de simulação TP/SL? | Não — usa pnl real da shadow trade |
| EV inclui fee/slippage? | `net_return_pct` = pnl líquido de fee; fallback `pnl_pct` = bruto |
| EV usa só COMPLETED? | Sim — filtro `status='COMPLETED'` |
| EV alinhado com label? | **Não perfeitamente.** Label = oportunidade (mfe≥1% em 30m). EV = resultado operacional (TP/SL). Trade com mfe≥1% mas SL hit = label=1, EV negativo |
| Por que label positivo mas EV negativo? | Label mede se o preço subiu +1% nos primeiros 30m. EV mede se o trade fechou com lucro. São métricas distintas |

### E.2 EV bruto por status

| Status | total | avg_pnl_pct | wins |
|---|---|---|---|
| COMPLETED | 2185 | +0.0182% | 1069 |
| RUNNING | 34 | — | — |

### E.3 EV por dia — Regime Shift

| Dia | n | win_rate | avg_pnl | label+ |
|---|---|---|---|---|
| 2026-06-10 | 9 | 88.9% | +0.778% | 2 |
| 2026-06-11 | 355 | 59.2% | +0.178% | 112 |
| 2026-06-12 | 332 | 53.9% | +0.073% | 79 |
| 2026-06-13 | 373 | 57.1% | +0.057% | 87 |
| 2026-06-14 | 210 | 53.8% | +0.117% | 45 |
| 2026-06-15 | 212 | 48.1% | +0.056% | 47 |
| **2026-06-16** | **151** | **40.4%** | **-0.183%** | 27 |
| **2026-06-17** | **72** | **27.8%** | **-0.524%** | 10 |
| 2026-06-18 | 36 | 27.8% | -0.317% | 5 |
| 2026-06-19 | 10 | 30.0% | -0.254% | 3 |
| 2026-06-20 | 4 | 50.0% | -0.221% | 1 |
| 2026-06-21 | 4 | 50.0% | -0.125% | 1 |
| 2026-06-22 | 5 | 20.0% | -0.302% | 0 |
| 2026-06-23 | 46 | 39.1% | -0.107% | 10 |
| 2026-06-24 | 255 | 36.1% | +0.009% | 49 |
| 2026-06-25 | 111 | 31.5% | -0.244% | 29 |

**Regime shift em 16 jun:** win_rate caiu de 53-59% para 28-40%. EV por dia passou de positivo (+0.05 a +0.18%) para negativo (-0.18 a -0.52%). Não houve reversão.

### E.4 Threshold Sweep (test set, dry-run `4d57d2eb`)

| Threshold | Approved | Precision | FPR | EV | Gate |
|---|---|---|---|---|---|
| 0.05 | 184 | 0.2989 | 0.4108 | -0.318% | fail (EV<0) |
| 0.10 | 117 | 0.3504 | 0.2420 | -0.291% | fail (EV<0) |
| 0.15 | 88 | 0.3750 | 0.1752 | -0.384% | fail (EV<0) |
| 0.20 | 77 | 0.3896 | 0.1497 | -0.435% | fail (EV<0) |
| 0.25 | 68 | 0.3971 | 0.1306 | -0.362% | fail (EV<0) |
| 0.30 | 55 | 0.4000 | 0.1051 | -0.358% | fail (EV<0) |
| 0.35 | 49 | 0.3469 | 0.1019 | -0.439% | fail (EV<0) |
| 0.40 | 45 | 0.3556 | 0.0924 | -0.472% | fail (EV<0) |
| 0.45 | 37 | 0.3784 | 0.0732 | -0.497% | fail (EV<0) |
| 0.50 | 27 | 0.2963 | 0.0605 | -0.547% | fail (EV<0+n<30) |
| 0.55 | 21 | 0.3333 | 0.0446 | -0.269% | fail (EV<0+n<30) |
| 0.60 | 17 | 0.2941 | 0.0382 | -0.241% | fail (EV<0+n<30) |
| 0.65 | 11 | 0.4545 | 0.0191 | -0.008% | fail (EV<0+n<30) |
| 0.70 | 9 | 0.3333 | 0.0191 | -0.298% | fail (EV<0+n<30) |
| 0.75 | 8 | 0.3750 | 0.0159 | -0.234% | fail (EV<0+n<30) |
| 0.80 | 6 | 0.5000 | 0.0096 | **+0.017%** | fail (n<30) |
| 0.85 | 6 | 0.5000 | 0.0096 | **+0.017%** | fail (n<30) |
| 0.90 | 3 | 0.6667 | 0.0032 | **+0.406%** | fail (n<30) |
| 0.95 | 1 | 0.0000 | 0.0032 | -1.382% | fail |

**EV negativo em todos os thresholds com approved_count ≥ 30.** EV positivo apenas em t≥0.80 com n=3-6 — abaixo do mínimo de 30 aprovados.

`operating_point_status = NO_VALID_OPERATING_POINT` ← confirmado.

---

## 7. Feature Coverage L1 (Fase F)

Dataset: 1978 rows, **33 features incluídas**.

### Incluídas (33)

| Feature | Coverage | Status |
|---|---|---|
| rsi_slope_3 | 0.999 | OK |
| rsi_slope_5 | 0.999 | OK |
| macd_hist_slope_3 | 0.999 | OK |
| macd_hist_slope_5 | 0.999 | OK |
| ema21_ema50_distance_pct | 0.999 | OK |
| di_plus_minus_diff | 0.999 | OK |
| adx_slope_3 | 0.999 | OK |
| vwap_reclaim_bool | 0.999 | OK |
| higher_highs_5 | 0.999 | OK |
| higher_lows_5 | 0.999 | OK |
| rsi | 0.999 | OK |
| macd_histogram_pct | 0.999 | OK |
| macd_histogram_slope | 0.999 | OK |
| adx | 0.999 | OK |
| adx_acceleration | 0.999 | OK |
| bb_width | 0.999 | OK |
| atr_pct | 0.999 | OK |
| ema50_gt_ema200 | 0.999 | OK |
| momentum_strength | 0.999 | OK |
| ema50_distance_pct | 0.999 | OK |
| ema200_distance_pct | 0.999 | OK |
| volume_spike | 0.998 | OK |
| ema9_gt_ema21 | 0.998 | OK |
| vwap_distance_pct | 0.998 | OK |
| trend_alignment | 0.998 | OK |
| ema_distance_pct | 0.998 | OK |
| spread_pct | 0.998 | OK |
| taker_ratio | 0.997 | OK |
| volume_delta | 0.997 | OK |
| orderbook_depth_usdt | 0.997 | OK |
| flow_strength | 0.997 | OK |
| volume_24h_usdt | 0.564 | LOW (incluída) |
| delta_normalized | 0.563 | LOW (incluída) |

### Excluídas (0% coverage — Macro MDH)

`sp500_change_1h`, `nasdaq_change_1h`, `russell2000_change_1h`, `vix_value`, `vix_change_1h`, `dxy_value`, `dxy_change_1h`, `us10y_yield`, `us10y_change_1h`, `btc_dominance`, `fear_greed_index` — **não disponíveis em `features_snapshot` do L1_SPECTRUM**.

Estes são populados via MDH apenas para L3/L3_LAB (decisões que passam por decisions_log). O builder L1 captura features diretamente de `features_snapshot` da shadow_trade, que não inclui macro.

---

## 8. Split Temporal (Fase G)

| Split | Rows | Date Range | Win Rate aprox. | EV médio |
|---|---|---|---|---|
| TRAIN (60%) | 1186 | Jun 11 → Jun 14 | ~57% | +0.10% |
| VAL (20%) | 396 | Jun 14 → Jun 22 | 28-54% (degradando) | ~-0.05% |
| TEST (20%) | 396 | Jun 23 → Jun 25 | 32-39% | -0.11% |

**TEST set cobre exclusivamente o período de regime ruim (Jun 23-25).**

- TRAIN: mercado favorável, estratégia L1 funcionando (win_rate ~57%)
- VAL: transição — regime começa a degradar a partir de Jun 16-17
- TEST: regime ruim estabelecido (win_rate 31-39%)

**Label positive_rate por split (estimado via daily data):**

| Split | label+ estimado | Observação |
|---|---|---|
| TRAIN | ~28-30% | Jun 11-14: mercado mais favorável ao label mfe≥1% |
| VAL | ~22-25% | Transição |
| TEST | ~18-22% | Menos oportunidades de mfe≥1% no regime atual |

---

## 9. Análise de Ranker vs Gate (Fase H)

### Top Buckets (TEST — dry-run `4d57d2eb`)

| Bucket | n | Precision | Lift | EV |
|---|---|---|---|---|
| top_1% | 4 | 0.7500 | 3.62 | +0.630% |
| top_5% | 20 | 0.3500 | 1.69 | -0.234% |
| top_10% | 40 | 0.3750 | 1.81 | -0.501% |
| top_20% | 80 | 0.3875 | 1.87 | -0.463% |

### Avaliação dos critérios

**Gate (bloqueado):**
```
approved_count >= 30 → FAIL (EV negativo em todos os thresholds com n≥30)
EV > 0               → FAIL
precision > baseline → indeterminate (baseline ≈ 22.6%)
FPR <= 0.20          → PASS em t≥0.15
recall > 0           → PASS
```

**Ranker (top_1%):**
```
lift = 3.62 (positivo)
ev = +0.63% (positivo)
n = 4 (INSUFICIENTE para validação estatística)
```

**Conclusão Fase H:**

O modelo L1 mostra sinal de ranking (top_1% tem lift=3.62 e ev positivo) mas **n=4 é estatisticamente insuficiente**. O top_5% (n=20) já tem EV negativo (-0.23%), sugerindo que o sinal de lift positivo é frágil e dependente de regime.

**Classificação:** `L1_RANKER_ONLY_PENDING_STABLE_REGIME`
- O modelo não serve como gate (EV negativo com n≥30)
- O modelo pode ser ranker, mas precisa de validação em regime estável (mais dados do período Jun 10-15 ou novo período favorável)
- O EV negativo no teste é REAL (regime shift confirmado por SQL), não erro de dataset

---

## 10. Veredito Final

```
L1_DATASET_AUDIT_PASS_MODEL_REJECTED_CORRECTLY
```

**O dataset está correto:**
- UI e banco reconciliam 100% ✓
- Builder usa `status='COMPLETED'`, sem RUNNING/PENDING ✓
- Label `l1_mfe_30m_gte_1pct` é correto e não contamina negativos ✓
- Fallback `mfe_pct` seguro: zero SL_HIT erroneamente rotulados ✓
- Exclusões explicadas: 207 sem features_snapshot ✓
- 1978 dataset rows confirmados ✓
- EV negativo é REAL (regime shift Jun 16), não erro de construção ✓

**O modelo é rejeitado corretamente:**
- EV negativo em todos os thresholds com approved_count ≥ 30
- `operating_point_status = NO_VALID_OPERATING_POINT`
- Root cause: regime shift em Jun 16-17 (win_rate 58% → 28%)
- Não há ponto operacional robusto no regime atual

**Recomendações:**

1. **Aguardar dados de regime estável** — L1_SPECTRUM está ativo há 16 dias. Se o regime melhorar, refazer dry-run quando win_rate ≥ 45% por ≥ 5 dias consecutivos.
2. **Considerar l1_hit_tp_before_sl como label alternativo** — diretamente alinhado com P&L operacional. O label atual (`mfe_30m_gte_1pct`) mede oportunidade, não resultado.
3. **Ranker candidato** — usar L1 score como ranker top-K quando houver ≥ 30 trades no TEST com EV positivo. top_1% lift=3.62 é promissor mas n=4 é insuficiente.
4. **Macro features** — L1 não tem sp500/vix/dxy em `features_snapshot`. Não é bug — é limitação de pipeline. Se macro for relevante, precisaria ser populado no features_snapshot no momento da decisão L1.
5. **volume_24h_usdt e delta_normalized** — coverage 56%, borderline. Monitorar se coverage melhora com mais dados.

---

## 11. Ledger de Evidências

| Afirmação | Origem | Valor literal |
|---|---|---|
| UI total=2219 | SQL `COUNT(*)` | 2219 |
| UI finalizados=2185 | SQL `FILTER (WHERE status='COMPLETED')` | 2185 |
| UI em_aberto=34 | SQL `status='RUNNING'` | 34 |
| UI win_rate=48.1% | SQL 1052/2185 | 0.4813 |
| UI P&L total=398.57 | SQL `SUM(pnl_usdt)` | 398.57 |
| UI P&L médio=+0.02% | SQL `AVG(pnl_pct)` | 0.018241% |
| Status 'COMPLETED' é o contrato | SQL GROUP BY status | COMPLETED=2185 with_pnl=2185 |
| Status 'closed' não existe | SQL GROUP BY status | ausente |
| excluded_missing_features=207 | SQL `COUNT(*) FILTER (WHERE features_snapshot IS NULL OR text='{}')` | 207 |
| included_l1_dataset_rows=1978 | script output | sample_count=1978 |
| Builder L1 path:line | grep | `run_xgb_dual_lane_labels.py:338` |
| Filtro status='COMPLETED' | grep | `run_xgb_dual_lane_labels.py:248` |
| Label mfe_30m path:line | grep | `run_xgb_dual_lane_labels.py:155-158,175` |
| max_profit_first_30m coverage | SQL FILTER WHERE | has_30m=975 (49.3%), no_30m=1003 (50.7%) |
| both_null_forced_zero=0 | SQL FILTER | 0 |
| sl_fallback_labeled_positive=0 | SQL FILTER | 0 |
| avg_fallback_mfe SL_HIT=0.24% | SQL AVG | 0.2408% |
| label_positive_rate=22.6% | SQL simulation + script | 447/1978 = 0.2260 |
| Regime shift Jun 16 | SQL EV by day | win_rate 57%→28%, avg_pnl +0.12%→-0.52% |
| TEST set = Jun 23-25 | SQL cumulative | rows 1583-1978 |
| Threshold sweep: EV<0 para n≥30 | script JSON | todos t∈[0.05,0.45] com approved≥30 têm ev<0 |
| t=0.90: ev=+0.406%, n=3 | script JSON | approved_count=3, ev=0.40610 |
| top_1% lift=3.62, ev=+0.63 | script JSON | n=4, prec=0.75, lift=3.6219 |
| 33 features incluídas | script JSON leakage_audit | feature_count=33 |
| Macro features: 0% coverage | script JSON leakage_audit | sp500_change_1h...=0.000 |
| volume_24h_usdt: 56% | script JSON leakage_audit | coverage=0.564 |
| delta_normalized: 56% | script JSON leakage_audit | coverage=0.563 |
| operating_point_status | script JSON | NO_VALID_OPERATING_POINT |
| PERSISTED | script JSON | false |
| GIT HEAD | script JSON + git | 4d57d2eb2e90dbc5c3a5604bf7b2de6ed2f0fa5d |
| active_new_candidates | SQL COUNT | 0 |

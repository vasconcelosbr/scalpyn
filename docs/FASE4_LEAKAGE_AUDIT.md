# Fase 4 — Auditoria de Leakage nas Features do Trainer XGBoost

**Data:** 2026-06-10  
**Escopo:** todas as features que entram em `FEATURE_COLUMNS` + `MACRO_FEATURE_COLUMNS`  
**Metodologia:** T0 = timestamp da decisão L3 em `pipeline_scan.py` (momento em que `decisions_to_log` é persistido).  
A entrada do trade ocorre em T0+ε (próximo candle open, ≤60s).

---

## Legenda

| Classificação | Significado |
|---|---|
| **T0-SAFE** | Calculável estritamente com dados disponíveis até T0 |
| **SUSPEITA** | Dado posterior a T0 ou potencialmente contaminado; investigar antes de usar |

---

## Features Microestrutura (indicadores merged + live-injection)

Fonte canônica: `decisions_log.metrics["indicators_snapshot"]`  
Builder: `backend/app/tasks/pipeline_scan.py:1116` → `build_indicators_snapshot(merged, keys=consumed_keys)`  
Overlay live-injection: `pipeline_scan.py:1122–1136` (Gap B fix)

| Feature | Classificação | Fonte / Razão |
|---|---|---|
| `taker_ratio` | **T0-SAFE** | Live-injected (`_inject_live_order_flow`) com orderbook lido em T0. `pipeline_scan.py:1122` overlay garante valor decision-time. |
| `volume_delta` | **T0-SAFE** | Idem — live-injected, último agregado de 1m fechado antes de T0. |
| `rsi` | **T0-SAFE** | `merged.values` — computado de candles fechados antes de T0. |
| `macd_histogram_pct` | **T0-SAFE** | `merged.values`, normalizado por `close` do último candle fechado. |
| `macd_histogram_slope` | **T0-SAFE** | `merged.values`, normalizado por `close`. `feature_extractor.py:196–201`. |
| `adx` | **T0-SAFE** | `merged.values` — indicador laggard, candles fechados. |
| `adx_acceleration` | **T0-SAFE** | `merged.values` — diferença ADX[t]-ADX[t-1], candles fechados. |
| `spread_pct` | **T0-SAFE** | Live-injected — orderbook bid/ask em T0. |
| `volume_spike` | **T0-SAFE** | `merged.values` — razão volume relativo a média histórica, candles fechados. |
| `bb_width` | **T0-SAFE** | `merged.values` — Bollinger width, candles fechados. |
| `ema9_gt_ema21` | **T0-SAFE** | `merged.values` — boolean de candle fechado. |
| `ema50_gt_ema200` | **T0-SAFE** | `merged.values` — boolean de candle fechado. |
| `volume_24h_usdt` | **T0-SAFE** | `merged.values` — rolling 24h de candles fechados; log1p em `feature_extractor.py:183`. |
| `orderbook_depth_usdt` | **T0-SAFE** | Live-injected — profundidade do orderbook em T0. |
| `vwap_distance_pct` | **T0-SAFE** | Live-injected — VWAP acumulado até T0; warm-up guard nullifica se `vwap_candle_count < 12` (`feature_extractor.py:177`). |

---

## Features Engineered

Todas derivadas de inputs T0-SAFE listados acima.

| Feature | Classificação | Fonte / Razão |
|---|---|---|
| `flow_strength` | **T0-SAFE** | `taker_ratio * volume_delta` — `feature_extractor.py:205`. |
| `trend_alignment` | **T0-SAFE** | `ema9_gt_ema21 + ema50_gt_ema200` — `feature_extractor.py:211–217`. |
| `momentum_strength` | **T0-SAFE** | `macd_histogram_pct * adx` — `feature_extractor.py:219`. |
| `delta_normalized` | **T0-SAFE** | `volume_delta / volume_24h_usdt` — `feature_extractor.py:222`. |
| `ema_distance_pct` | **T0-SAFE** | `(ema9 - ema21) / ema21 * 100` — usa EMAs de candles fechados. `feature_extractor.py:224–228`. |
| `ema50_distance_pct` | **T0-SAFE** | `(close - ema50) / ema50 * 100` — `close` = último candle fechado antes de T0. `feature_extractor.py:231–232`. |
| `ema200_distance_pct` | **T0-SAFE** | `(close - ema200) / ema200 * 100` — idem. `feature_extractor.py:234–235`. |

---

## Macro Features (Market Data Hub)

Fonte: `backend/app/tasks/pipeline_scan.py:3040–3048`  
Timing: embedadas em `decisions_to_log` **após** a captura de `indicators_snapshot`, imediatamente antes de `_persist_decision_logs`. MDH data tem tolerância de staleness de 300s (`macro_features.py:56`).

| Feature | Classificação | Fonte / Razão |
|---|---|---|
| `sp500_change_1h` | **T0-SAFE** | MDH fetched na mesma execução de pipeline_scan que gerou a decisão; dado com ≤5min de staleness. |
| `nasdaq_change_1h` | **T0-SAFE** | Idem. |
| `russell2000_change_1h` | **T0-SAFE** | Idem. |
| `vix_value` | **T0-SAFE** | Idem. |
| `vix_change_1h` | **T0-SAFE** | Idem. |
| `dxy_value` | **T0-SAFE** | Idem. |
| `dxy_change_1h` | **T0-SAFE** | Idem. |
| `us10y_yield` | **T0-SAFE** | Idem. |
| `us10y_change_1h` | **T0-SAFE** | Idem. |
| `btc_dominance` | **T0-SAFE** | Idem. |
| `btc_dominance_change` | **T0-SAFE** | Idem. |
| `crypto_market_cap_change` | **T0-SAFE** | Idem. |
| `crypto_volume_change` | **T0-SAFE** | Idem. |
| `fear_greed_index` | **T0-SAFE** | Índice diário — atualizado uma vez por dia; sempre disponível antes de T0. |
| `macro_context_available` | **T0-SAFE** | Flag boolean de disponibilidade; sem conteúdo preditivo de preço. |

---

## Riscos Latentes (não são leakage direto — são riscos de divergência ou query errada)

### RISCO-1: `trade_simulations.features_snapshot` contém indicadores de SAÍDA

**Arquivo:** `backend/app/services/shadow_trade_service.py:1463–1470` (função `record_as_simulation`)

```python
exit_snap = shadow.features_snapshot_exit
if isinstance(exit_snap, dict) and exit_snap and not exit_snap.get("_capture_failed"):
    features_for_sim = exit_snap  # ← indicadores no momento do FECHAMENTO
else:
    features_for_sim = shadow.features_snapshot or {}
```

**Impacto:** Se o trainer for modificado para usar `SELECT features_snapshot FROM trade_simulations WHERE source='SHADOW'`, obteria indicadores pós-trade em vez de entrada. Seria leakage grave (análogo ao AUC 0.239 do ARROW v27).

**Status atual:** O trainer em `ml_trainer/job.py:104–116` lê de `shadow_trades.features_snapshot` diretamente — **não está afetado**. Risco é latente.

**Recomendação:** Renomear a coluna em `trade_simulations` para `features_snapshot_exit` para eliminar ambiguidade, ou adicionar comentário `-- exit-time snapshot` na migration.

---

### RISCO-2: Gap B — registros pré-fix têm skew de snapshot

**Arquivo:** `backend/app/tasks/pipeline_scan.py:1090–1136`

Registros criados **antes** do Gap B fix (data do commit não identificada nesta auditoria) podem ter valores de `taker_ratio`, `volume_delta`, `spread_pct`, `vwap_distance_pct` de `merged.values` (stale DB snapshot, ≤30s de atraso) em vez dos valores live-injected que de fato guiaram a decisão.

**Impacto:** Train-serve skew — features de treino não batem com features de inferência para esses registros. Reduz qualidade do modelo mas não é leakage direcional.

**Recomendação:** Excluir registros `shadow_trades.created_at < <data do Gap B fix>` do treino via `TRAIN_EXCLUDE_FROM`/`TRAIN_EXCLUDE_TO` (já suportado em `ml_trainer/job.py:90–100`).

---

### RISCO-3: Macro features não capturadas em decisões BEFORE macro_client deploy

**Arquivo:** `backend/app/services/shadow_trade_service.py:347–350` (`_build_features_snapshot`)

```python
for key in MACRO_FEATURE_COLUMNS:
    if key not in flat and key in metrics:
        flat[key] = metrics[key]
```

Registros de shadow_trades criados antes do deploy do embed macro (`pipeline_scan.py:3040`) têm macro features ausentes (NaN no trainer). XGBoost trata `NaN` como missing — comportamento correto, sem leakage.

**Classificação:** **T0-SAFE** (NaN é legítimo, não contamina).

---

## Campos NÃO em FEATURE_COLUMNS que são query-SELECTed no trainer

Esses campos são incluídos no SELECT de `ml_trainer/job.py:104–107` mas **não são passados para `extract_features`** — são usados apenas para construção do label ou como metadado:

| Campo | Uso | Risco |
|---|---|---|
| `pnl_pct` | Label fallback (Tier 2) quando `ttt_outcome=NULL` | Depende de dados pós-T0 por definição — é o label, não feature. ✓ |
| `outcome` | Documentado mas não usado em `build_training_dataframe` | Nenhum. |
| `ttt_outcome` | Label primário (Tier 1) | É o label, não feature. ✓ |
| `ttt_fast_win_bucket` | Metadado de análise | Não usado como feature. |
| `time_to_tp_minutes` | SELECTed, não usado | Seria leakage severo se entrar em FEATURE_COLUMNS. **Nunca adicionar.** |
| `elapsed_minutes` | SELECTed, não usado | Idem. |
| `profit_velocity` | SELECTed, não usado | Idem. |

---

## Resumo

**Nenhuma feature em `FEATURE_COLUMNS` apresenta leakage direto** com a implementação atual.  
O maior risco é **latente** (RISCO-1): se o query do trainer migrar de `shadow_trades` para `trade_simulations`, os indicadores de saída entrariam como features de entrada.  
Ação imediata sugerida: proteger a query com comentário explícito e/ou renomear a coluna de features em `trade_simulations`.

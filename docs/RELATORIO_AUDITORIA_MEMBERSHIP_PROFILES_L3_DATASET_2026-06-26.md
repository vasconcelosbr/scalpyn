# RELATORIO_AUDITORIA_MEMBERSHIP_PROFILES_L3_DATASET_2026-06-26

## 1. Veredito

`L3_PROFILE_DATASET_MEMBERSHIP_AUDIT_PASS`

Todos os 15 profiles criticos vistos no Relatorio Executivo foram mapeados para `profile_id`, aparecem no contrato SQL L3, entram no dataframe final do builder XGB L3 e aparecem no `profile_breakdown` e no `threshold_by_profile_json` do ultimo modelo L3 persistido.

Achado secundario: o ultimo artefato persistido em `ml_models` ainda mostra varios profiles maduros como `cold_start` porque o JSON de thresholds registra `trade_count` do split de teste, nao o total completo do dataframe. Isso nao remove os profiles do dataset; e uma divergencia de classificacao do artefato persistido anterior ao deploy atual.

## 2. Pre-flight read-only

| Checagem | Origem | Valor literal |
|---|---|---:|
| live_trading_enabled=true | SQL preflight `profiles` | 0 |
| auto_pilot_enabled=true | SQL preflight `profiles` | 0 |
| total_profiles | SQL preflight `profiles` | 109 |
| possible_live_orders | SQL preflight `orders` | 0 |
| ML_GATE_ENABLED | `railway variable list` em 6 servicos | false em `scalpyn`, `scalpyn-worker-structural`, `scalpyn-worker-execution`, `scalpyn-worker-micro`, `scalpyn-worker-compute`, `scalpyn-beat` |
| git HEAD | `git rev-parse HEAD` | 4d57d2eb2e90dbc5c3a5604bf7b2de6ed2f0fa5d |

## 3. Schema descoberto

| Tabela | Colunas relevantes encontradas |
|---|---|
| `profiles` | `id`, `user_id`, `name`, `created_at`, `is_shadow_only` |
| `shadow_trades` | `id`, `user_id`, `outcome`, `pnl_pct`, `pnl_usdt`, `status`, `source`, `features_snapshot`, `created_at`, `profile_id`, `profile_name`, `watchlist_id`, `model_lane` |
| `config_profiles` | `id`, `user_id`, `created_at` |
| `watchlists` | NAO DISPONIVEL |
| `ml_models` | `id`, `version`, `status`, `hyperparams`, `train_samples`, `val_samples`, `test_samples`, `created_at`, `profile_id`, `model_scope`, `model_lane` |
| `decisions_log` | `id`, `metrics`, `user_id`, `created_at`, `outcome`, `pnl_pct`, `profile_id`, `profile_name`, `model_lane` |

## 4. Fontes de verdade usadas

| Item | Evidencia |
|---|---|
| `backend/app/api/shadow_trades.py:907` | /api/shadow-trades/profile-report groups profiles p LEFT JOIN shadow_trades st ON st.profile_id=p.id and user_id |
| `backend/scripts/run_xgb_dual_lane_labels.py:211` | load_shadow_rows filters source, status=COMPLETED, pnl_pct IS NOT NULL, features_snapshot present, created_at >= lookback, profile_id required for L3 |
| `backend/scripts/run_xgb_dual_lane_labels.py:359` | build_xgb_l3_profile_dataset builds final df from valid rows with profile_id |
| `backend/scripts/run_xgb_dual_lane_labels.py:798` | persist_candidate stores profile_breakdown and threshold_by_profile_json in ml_models.hyperparams |

## 5. Query que reproduz o Relatorio Executivo

O endpoint de UI usa `profiles p LEFT JOIN shadow_trades st ON st.profile_id = p.id AND st.user_id = p.user_id`, agrupa por `p.id,p.name,p.is_shadow_only`, inclui `p.is_shadow_only=TRUE OR COUNT(st.id)>0`, e ordena para renderizacao no frontend. Para reconciliacao L3, a auditoria tambem executou agrupamento restrito a `st.source IN ('L3','L3_LAB')`.

Top L3 por SQL:

| profile_name | profile_id | raw_rows | open_rows | completed_with_pnl | win_rate |
|---|---|---:|---:|---:|---:|
| L3_PULLBACK_TENDENCIA_V4 | `None` | 2198 | 238 | 1921 | 0.4872 |
| L3_ANTI_EXAUSTAO_V3 | `2b70dc42-1edd-4603-bc54-0403cd1e2f54` | 613 | 3 | 464 | 0.4009 |
| L3_TREND_CONSERVADOR_V3 | `a565150d-74da-4308-914e-d586a37cdf99` | 589 | 0 | 513 | 0.3996 |
| L3_EARLY_PULLBACK_V3 | `7e2a14d7-20ec-4a64-b7e6-ebaf39ac6578` | 397 | 0 | 302 | 0.3245 |
| L3_TREND_CONSERVADOR_V3 | `a565150d-74da-4308-914e-d586a37cdf99` | 393 | 26 | 367 | 0.3842 |
| L3_VOLATILIDADE_MODERADA_V3 | `5bdbefc4-4500-4eaa-8f1a-b9be1973b7e7` | 339 | 2 | 333 | 0.4114 |
| L3_HIGH_LIQUIDITY_V3 | `eb4958e6-e338-4652-9894-b153913ee206` | 337 | 0 | 211 | 0.2275 |
| L3_ANTI_EXAUSTAO_V3 | `2b70dc42-1edd-4603-bc54-0403cd1e2f54` | 337 | 21 | 316 | 0.3608 |
| L3_ML_PRIORITY_V4 | `44d2a3bf-5a5f-49fb-99e4-7df945b8f333` | 335 | 0 | 176 | 0.2614 |
| L3_VOLATILIDADE_MODERADA_V3 | `5bdbefc4-4500-4eaa-8f1a-b9be1973b7e7` | 317 | 14 | 303 | 0.3432 |
| L3_MEAN_REVERSION_CONTROLADO_V3 | `e44f3ad2-c536-48f1-85aa-62c63686ee27` | 316 | 1 | 291 | 0.4158 |
| macd_hist_lte_0_AND_ema50_gt_ema200_false | `5da37177-7f0f-4f0b-b3e0-ff651025be37` | 236 | 15 | 221 | 0.3937 |
| vol_spike_gte_1_5_AND_ema50_gt_ema200_false | `7b560f2a-3aa6-492b-80ee-04ad8b60c39b` | 214 | 4 | 210 | 0.3810 |
| macd_hist_lte_0_AND_ema50_gt_ema200_false | `5da37177-7f0f-4f0b-b3e0-ff651025be37` | 183 | 0 | 183 | 0.3825 |
| L3_BREAKOUT_V3 | `33ed9391-ada9-4dc4-8bc7-f10b0dbcd05a` | 171 | 2 | 169 | 0.3787 |

## 6. Membership final por profile critico

| Profile UI | profile_id | UI total | SQL completed | builder included | in_profile_breakdown | in_threshold_json | status final | motivo |
|---|---|---:|---:|---:|---|---|---|---|
| L3_TREND_CONSERVADOR_V3 | `a565150d-74da-4308-914e-d586a37cdf99` | 1448 | 1318 | 1318 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=approved_candidate; reason=passes_all_criteria; threshold_trade_count=317 |
| L3_ANTI_EXAUSTAO_V3 | `2b70dc42-1edd-4603-bc54-0403cd1e2f54` | 1443 | 1238 | 1238 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=insufficient_operating_sample; reason=approved_count < 30; threshold_trade_count=327 |
| L3_VOLATILIDADE_MODERADA_V3 | `5bdbefc4-4500-4eaa-8f1a-b9be1973b7e7` | 760 | 714 | 714 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=insufficient_operating_sample; reason=approved_count < 30; threshold_trade_count=185 |
| L3_MEAN_REVERSION_CONTROLADO_V3 | `e44f3ad2-c536-48f1-85aa-62c63686ee27` | 722 | 664 | 664 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=approved_candidate; reason=passes_all_criteria; threshold_trade_count=136 |
| L3_EARLY_PULLBACK_V3 | `7e2a14d7-20ec-4a64-b7e6-ebaf39ac6578` | 548 | 431 | 431 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=75 |
| L3_HIGH_LIQUIDITY_V3 | `eb4958e6-e338-4652-9894-b153913ee206` | 458 | 305 | 305 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=10 |
| macd_hist_lte_0_AND_ema50_gt_ema200_false | `5da37177-7f0f-4f0b-b3e0-ff651025be37` | 419 | 404 | 404 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=84 |
| L3_ML_PRIORITY_V4 | `44d2a3bf-5a5f-49fb-99e4-7df945b8f333` | 359 | 193 | 193 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=1 |
| vol_spike_gte_1_5_AND_ema50_gt_ema200_false | `7b560f2a-3aa6-492b-80ee-04ad8b60c39b` | 305 | 301 | 301 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=65 |
| L3_BREAKOUT_V3 | `33ed9391-ada9-4dc4-8bc7-f10b0dbcd05a` | 274 | 272 | 272 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=64 |
| vol_spike_gt_2_5_AND_vol_spike_gte_1_5 | `561db244-b0eb-4cac-b1f7-fe29213a0e75` | 263 | 260 | 260 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=59 |
| bb_0_050_0_080_AND_ema50_gt_ema200_false | `20756610-707a-4b88-b5b2-0f287274960f` | 248 | 239 | 239 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=82 |
| macd_hist_lte_0_AND_adx_gte_35 | `a40cdbfe-b361-4953-91f1-2d4cc93ab424` | 248 | 246 | 246 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=44 |
| adx_gte_35_AND_ema50_gt_ema200_false | `10d6d5ae-9fdf-41fc-a99f-d9be9a138bd7` | 233 | 228 | 228 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=29 |
| L3_TREND_FORTE_V3 | `9bf292a1-ec81-4c94-b501-499644caefea` | 222 | 201 | 201 | sim | sim | IN_DATASET | builder final df inclui profile; threshold_status=cold_start; reason=trade_count < 100; threshold_trade_count=30 |

## 7. Dataset bruto vs builder

| Medida | Origem | Valor literal |
|---|---|---:|
| builder loaded_rows | `load_shadow_rows()` | 9226 |
| builder final_dataset_rows | `build_xgb_l3_profile_dataset()` | 9226 |
| builder profile_breakdown_count | `DatasetBundle.profile_breakdown` | 45 |
| builder excluded_count | `DatasetBundle.excluded_count` | 0 |
| feature_columns_count | `DatasetBundle.feature_columns` | 24 |
| L3 NULL profile raw_rows | SQL `shadow_trades` | 2198 |
| L3 NULL profile completed_with_pnl | SQL `shadow_trades` | 1921 |

O contrato SQL amplo (`source IN ('L3','L3_LAB')`, `status='COMPLETED'`, `pnl_pct IS NOT NULL`, `profile_id IS NOT NULL`) esta satisfeito para os 15 profiles. O builder atual adiciona filtros operacionais de `features_snapshot` presente e `created_at >= lookback 60d`; para os 15 profiles, `builder_sql_eligible_60d` bate com `builder included`.

## 8. Model registry

| Campo | Valor literal |
|---|---|
| latest version | `xgb_l3_profile_20260626_165116` |
| latest status | `candidate` |
| model_lane | `XGB_L3_PROFILE` |
| train/val/test | `5535/1845/1846` |
| profile_breakdown_count | `45` |
| threshold_by_profile_count | `39` |

Todos os 15 `profile_id` criticos aparecem no `profile_breakdown` e no `threshold_by_profile_json`. A divergencia e semantica: o JSON de thresholds usa o campo `trade_count` do conjunto de teste em varios casos, produzindo `cold_start` mesmo com `completed_with_pnl` e `builder_included` acima de 100.

## 9. Fragmentacao por watchlist

| Profile | watchlists distintos | maior shard completed_with_pnl | status |
|---|---:|---:|---|
| L3_TREND_CONSERVADOR_V3 | 42 | 513 | fragmentado, mas builder agrupa por profile_id |
| L3_ANTI_EXAUSTAO_V3 | 42 | 464 | fragmentado, mas builder agrupa por profile_id |
| L3_VOLATILIDADE_MODERADA_V3 | 39 | 333 | fragmentado, mas builder agrupa por profile_id |
| L3_MEAN_REVERSION_CONTROLADO_V3 | 42 | 291 | fragmentado, mas builder agrupa por profile_id |
| L3_EARLY_PULLBACK_V3 | 35 | 302 | fragmentado, mas builder agrupa por profile_id |
| L3_HIGH_LIQUIDITY_V3 | 30 | 211 | fragmentado, mas builder agrupa por profile_id |
| macd_hist_lte_0_AND_ema50_gt_ema200_false | 2 | 221 | fragmentado, mas builder agrupa por profile_id |
| L3_ML_PRIORITY_V4 | 15 | 176 | fragmentado, mas builder agrupa por profile_id |
| vol_spike_gte_1_5_AND_ema50_gt_ema200_false | 2 | 210 | fragmentado, mas builder agrupa por profile_id |
| L3_BREAKOUT_V3 | 2 | 169 | fragmentado, mas builder agrupa por profile_id |
| vol_spike_gt_2_5_AND_vol_spike_gte_1_5 | 2 | 166 | fragmentado, mas builder agrupa por profile_id |
| bb_0_050_0_080_AND_ema50_gt_ema200_false | 2 | 159 | fragmentado, mas builder agrupa por profile_id |
| macd_hist_lte_0_AND_adx_gte_35 | 2 | 153 | fragmentado, mas builder agrupa por profile_id |
| adx_gte_35_AND_ema50_gt_ema200_false | 2 | 141 | fragmentado, mas builder agrupa por profile_id |
| L3_TREND_FORTE_V3 | 29 | 126 | fragmentado, mas builder agrupa por profile_id |

## 10. Reconciliation UI vs SQL vs builder

Para os 15 profiles da tela, `UI total` do prompt bate com `SQL raw_rows` retornado por `shadow_trades` L3/L3_LAB. `SQL completed_with_pnl` remove abertos e linhas sem PnL. `builder included` bate com `builder_sql_eligible_60d`, pois esses profiles possuem `features_snapshot` valido no lookback.

## 11. Ledger de evidencias

| Afirmacao | Origem | Valor literal |
|---|---|---|
| Safety precheck live/autopilot zerado | SQL preflight | live_enabled=0; autopilot_enabled=0 |
| Nenhuma ordem potencialmente live | SQL preflight | possible_live_orders=0 |
| ML gate desligado | Railway variable list | ML_GATE_ENABLED=false nos 6 servicos auditados |
| Builder L3 final inclui linhas | `backend/scripts/run_xgb_dual_lane_labels.py:211,359` | final_dataset_rows=9226; profile_breakdown_count=45 |
| Ultimo modelo L3 contem profile_breakdown | SQL `ml_models.hyperparams` | version=xgb_l3_profile_20260626_165116; profile_breakdown_count=45 |
| Ultimo modelo L3 contem threshold JSON | SQL `ml_models.hyperparams` | threshold_by_profile_count=39 |
| Evidencia bruta | arquivo local gerado | `C:\tmp\l3_membership_audit_output.json` |

## 12. Conclusao

Nao ha profile critico da UI faltando no dataset L3/XGBoost atual. Nao ha `MISSING_MAPPING`, `MISSING_FROM_DATASET` ou `WATCHLIST_FRAGMENTATION` bloqueante para os 15 profiles listados no prompt. A acao posterior recomendada, fora deste prompt read-only, e reemitir/persistir artefatos L3 depois do deploy atual para que `threshold_by_profile_json` deixe de refletir contagens de split de teste como maturidade de profile.

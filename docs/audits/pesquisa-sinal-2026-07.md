# Pesquisa de Sinal — Chaves Ignoradas + Benchmark ATR

Data da execução: 2026-07-05 12:19:08 UTC
Modo: READ-ONLY contra Railway Postgres; nenhuma escrita de sistema. Este arquivo é o único artefato gravado.

## Escopo e Proveniência

- Conexão: `production_psycopg2_ok` via serviço Railway `Postgres` / `DATABASE_PUBLIC_URL` (segredo omitido).
- População analisada: `source=L1_SPECTRUM`, outcomes fechados, `pnl_pct` e `features_snapshot` presentes, `created_at >= 2026-06-14T21:33:10.277143+00:00`, `created_at >= NOW() - INTERVAL '30 days'`.
- Gate amplo: `2501`; população analítica: `2501`; janela `2026-06-14 21:33:10.591666+00:00` até `2026-07-05 11:35:09.647612+00:00`. [query]
- `FEATURE_COLUMNS`: `33`; metadados `_ *`: `['_directional_backfill', '_features_captured_at', '_features_coverage', '_oldest_indicator_age_s']`; ML_EXCLUDED_FIELDS: `['score', 'score_classification', 'score_components', 'score_max', 'score_normalized', 'score_raw', 'signal_direction']`. [código/query]
- Config ML: `ml_dataset_valid_from=2026-06-14 21:33:10.277143+00`, `ml_win_fast_threshold_seconds=14400.0`, `ml_fee_roundtrip_pct=0.2`, `ml_retrain_min_eligible_rows=2800`, `ml_feature_exclusion_apply=True`, `ml_feature_exclusion_candidates_proposed=['trend_alignment', 'ema50_gt_ema200']`. [config: ml]
- Contrato L1 vigente: required=`25`, optional=`8`. [config: ml]

Referências de código usadas:
- `backend/app/ml/feature_extractor.py:17-65 FEATURE_COLUMNS base (33)`
- `backend/app/ml/feature_extractor.py:97-105 ML_EXCLUDED_FIELDS`
- `backend/app/ml/feature_extractor.py:386-390 label v2: TP_HIT + holding_seconds`
- `ml_trainer/job.py:669-685 query global do trainer`
- `backend/app/services/ml_challenger_service.py:540-565 query lane L1 por usuário`

Schema validado em `information_schema.columns`:
| column_name | data_type |
| --- | --- |
| outcome | character varying |
| pnl_pct | double precision |
| holding_seconds | integer |
| features_snapshot | jsonb |
| created_at | timestamp with time zone |
| completed_at | timestamp with time zone |
| mae_pct | double precision |
| mfe_pct | double precision |
| max_profit_first_30m | double precision |
| net_return_pct | double precision |
| fee_roundtrip_pct_applied | double precision |
| atr_pct_at_entry | double precision |

## P1 — Auditoria das Chaves Ignoradas

Censo: `94` chaves distintas; `33` no feature set; `4` metadados `_ *`; diff ignorado analisado: `64` chaves. [query/calc]

### P1.1/P1.2 — Inventário e Triagem
| chave | classe | n_non_null | fill | tipo_dom | first_seen | last_seen | triagem |
| --- | --- | --- | --- | --- | --- | --- | --- |
| atr | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| atr_percent | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_DUP_atr_pct_1.000 |
| bb_lower | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| bb_middle | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| bb_upper | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| bid_ask_imbalance | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| buy_pressure | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_DUP_taker_ratio_1.000 |
| close | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| close_5m | PRECO/INDICADOR | 1142 | 45.7% | number | 2026-06-14 21:38 | 2026-07-05 10:34 | DESCARTADA_FILL_LT_50 |
| di_minus | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| di_plus | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema10 | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema200 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema21 | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema30 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema5 | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema50 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema9 | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema9_distance_pct | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema9_gt_ema50 | PRECO/INDICADOR | 2424 | 96.9% | boolean | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| ema_full_alignment | PRECO/INDICADOR | 2424 | 96.9% | boolean | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| entry_exhaustion_score | METADADO | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd_histogram | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd_histogram_mean_10 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd_histogram_prev | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd_histogram_std_10 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| macd_signal | PRECO/INDICADOR | 2426 | 97.0% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| macd_signal_line | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| market_data_confidence | METADADO | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_CONSTANTE |
| market_data_source | METADADO | 2424 | 96.9% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| market_data_symbol | METADADO | 1301 | 52.0% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| obv | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| obv_slope_5 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| orderbook_pressure | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| price | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar_af | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar_distance_pct | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar_ep | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar_reversal | PRECO/INDICADOR | 2426 | 97.0% | boolean | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| psar_signal | PRECO/INDICADOR | 2426 | 97.0% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| psar_trend | PRECO/INDICADOR | 2426 | 97.0% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| rsi_12 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_DUP_rsi_0.995 |
| rsi_24 | PRECO/INDICADOR | 2426 | 97.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_DUP_rsi_0.968 |
| rsi_6 | PRECO/INDICADOR | 2422 | 96.8% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| score | METADADO | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_METADATA_LEAKAGE |
| score_max | METADADO | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_METADATA_LEAKAGE |
| score_raw | METADADO | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_METADATA_LEAKAGE |
| stoch_d | OUTRO | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| stoch_k | OUTRO | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_buy_volume | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_sell_volume | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_source | MICROESTRUTURA | 2424 | 96.9% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| taker_window | MICROESTRUTURA | 2424 | 96.9% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| volume_24h_base | PRECO/INDICADOR | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_24h_base_aggregated | PRECO/INDICADOR | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_24h_candles | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_24h_coverage_hours | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_24h_usdt_aggregated | PRECO/INDICADOR | 1301 | 52.0% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_last_candle_base | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| volume_last_candle_usdt | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| vwap | PRECO/INDICADOR | 2423 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| vwap_candle_count | PRECO/INDICADOR | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |

### Microestrutura
| chave | classe | n_non_null | fill | tipo_dom | first_seen | last_seen | triagem |
| --- | --- | --- | --- | --- | --- | --- | --- |
| bid_ask_imbalance | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| buy_pressure | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_DUP_taker_ratio_1.000 |
| orderbook_pressure | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_buy_volume | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_sell_volume | MICROESTRUTURA | 2424 | 96.9% | number | 2026-06-14 21:33 | 2026-07-05 11:35 | OK |
| taker_source | MICROESTRUTURA | 2424 | 96.9% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |
| taker_window | MICROESTRUTURA | 2424 | 96.9% | string | 2026-06-14 21:33 | 2026-07-05 11:35 | DESCARTADA_NAO_NUMERICA |

Veredito microestrutura: `4` chaves sobreviveram à triagem inicial; hipótese nº 1 não morre por ausência/cobertura. [calc]

### P1.3/P1.4 — AUC por Janela e Veredito
Nota: `diag_mp30` foi marcado SUSPEITO na pré-validação (r=0.6255, n=165), portanto não entra no veredito P1. [query/calc]
| chave | classe | fill | AUC label J1 | AUC label J2 | AUC clean J1 | AUC clean J2 | AUC mp30 J1 | AUC mp30 J2 | veredito |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| atr | PRECO/INDICADOR | 97.0% | 0.5009 | 0.4942 | 0.4713 | 0.5154 | NA | NA | FRACA |
| bb_lower | PRECO/INDICADOR | 97.0% | 0.4495 | 0.4506 | 0.4638 | 0.4920 | NA | NA | INSTAVEL |
| bb_middle | PRECO/INDICADOR | 97.0% | 0.4520 | 0.4518 | 0.4628 | 0.4928 | NA | NA | FRACA |
| bb_upper | PRECO/INDICADOR | 97.0% | 0.4545 | 0.4535 | 0.4628 | 0.4939 | NA | NA | FRACA |
| bid_ask_imbalance | MICROESTRUTURA | 96.9% | 0.4812 | 0.5077 | 0.4924 | 0.5056 | NA | NA | FRACA |
| close | PRECO/INDICADOR | 97.0% | 0.4529 | 0.4514 | 0.4637 | 0.4927 | NA | NA | FRACA |
| di_minus | PRECO/INDICADOR | 97.0% | 0.4127 | 0.4831 | 0.4329 | 0.4652 | NA | NA | INSTAVEL |
| di_plus | PRECO/INDICADOR | 97.0% | 0.5654 | 0.4968 | 0.5743 | 0.4986 | NA | NA | INSTAVEL |
| ema10 | PRECO/INDICADOR | 96.9% | 0.4526 | 0.4517 | 0.4633 | 0.4931 | NA | NA | FRACA |
| ema200 | PRECO/INDICADOR | 97.0% | 0.4488 | 0.4530 | 0.4629 | 0.4933 | NA | NA | INSTAVEL |
| ema21 | PRECO/INDICADOR | 96.9% | 0.4523 | 0.4518 | 0.4629 | 0.4931 | NA | NA | FRACA |
| ema30 | PRECO/INDICADOR | 97.0% | 0.4510 | 0.4517 | 0.4625 | 0.4926 | NA | NA | FRACA |
| ema5 | PRECO/INDICADOR | 96.9% | 0.4528 | 0.4518 | 0.4635 | 0.4931 | NA | NA | FRACA |
| ema50 | PRECO/INDICADOR | 97.0% | 0.4500 | 0.4520 | 0.4624 | 0.4926 | NA | NA | INSTAVEL |
| ema9 | PRECO/INDICADOR | 96.9% | 0.4527 | 0.4517 | 0.4634 | 0.4931 | NA | NA | FRACA |
| ema9_distance_pct | PRECO/INDICADOR | 96.9% | 0.5596 | 0.5203 | 0.5471 | 0.5288 | NA | NA | INSTAVEL |
| ema9_gt_ema50 | PRECO/INDICADOR | 96.9% | 0.5698 | 0.4900 | 0.5639 | 0.5116 | NA | NA | INSTAVEL |
| ema_full_alignment | PRECO/INDICADOR | 96.9% | 0.5693 | 0.4908 | 0.5581 | 0.5022 | NA | NA | INSTAVEL |
| entry_exhaustion_score | METADADO | 97.0% | 0.5143 | 0.4647 | 0.5236 | 0.4943 | NA | NA | FRACA |
| macd | PRECO/INDICADOR | 97.0% | 0.5715 | 0.4801 | 0.5542 | 0.4966 | NA | NA | INSTAVEL |
| macd_histogram | PRECO/INDICADOR | 97.0% | 0.5438 | 0.5019 | 0.5466 | 0.5098 | NA | NA | FRACA |
| macd_histogram_mean_10 | PRECO/INDICADOR | 97.0% | 0.5394 | 0.4999 | 0.5114 | 0.4912 | NA | NA | FRACA |
| macd_histogram_prev | PRECO/INDICADOR | 97.0% | 0.5375 | 0.4967 | 0.5403 | 0.5074 | NA | NA | FRACA |
| macd_histogram_std_10 | PRECO/INDICADOR | 97.0% | 0.5025 | 0.4860 | 0.4742 | 0.5075 | NA | NA | FRACA |
| macd_signal_line | PRECO/INDICADOR | 97.0% | 0.5631 | 0.4757 | 0.5522 | 0.4919 | NA | NA | INSTAVEL |
| obv | PRECO/INDICADOR | 97.0% | 0.5303 | 0.4824 | 0.5194 | 0.5073 | NA | NA | FRACA |
| obv_slope_5 | PRECO/INDICADOR | 97.0% | 0.5632 | 0.5315 | 0.5464 | 0.5335 | NA | NA | INSTAVEL |
| orderbook_pressure | MICROESTRUTURA | 96.9% | 0.4812 | 0.5077 | 0.4924 | 0.5056 | NA | NA | FRACA |
| price | PRECO/INDICADOR | 97.0% | 0.4529 | 0.4514 | 0.4637 | 0.4927 | NA | NA | FRACA |
| psar | PRECO/INDICADOR | 97.0% | 0.4534 | 0.4531 | 0.4632 | 0.4940 | NA | NA | FRACA |
| psar_af | PRECO/INDICADOR | 97.0% | 0.5235 | 0.4695 | 0.5356 | 0.4758 | NA | NA | FRACA |
| psar_distance_pct | PRECO/INDICADOR | 97.0% | 0.6033 | 0.6277 | 0.5122 | 0.5549 | NA | NA | INSTAVEL |
| psar_ep | PRECO/INDICADOR | 97.0% | 0.4531 | 0.4513 | 0.4644 | 0.4927 | NA | NA | FRACA |
| psar_reversal | PRECO/INDICADOR | 97.0% | 0.4870 | 0.5018 | 0.4810 | 0.5036 | NA | NA | FRACA |
| rsi_6 | PRECO/INDICADOR | 96.8% | 0.5942 | 0.5211 | 0.5683 | 0.5256 | NA | NA | INSTAVEL |
| stoch_d | OUTRO | 96.9% | 0.5566 | 0.5185 | 0.5524 | 0.5059 | NA | NA | INSTAVEL |
| stoch_k | OUTRO | 96.9% | 0.5717 | 0.5225 | 0.5543 | 0.5128 | NA | NA | INSTAVEL |
| taker_buy_volume | MICROESTRUTURA | 96.9% | 0.5657 | 0.5473 | 0.5412 | 0.5050 | NA | NA | INSTAVEL |
| taker_sell_volume | MICROESTRUTURA | 96.9% | 0.5542 | 0.5401 | 0.5288 | 0.4932 | NA | NA | INSTAVEL |
| volume_24h_base | PRECO/INDICADOR | 52.0% | 0.5769 | 0.5480 | 0.5707 | 0.5073 | NA | NA | INSTAVEL |
| volume_24h_base_aggregated | PRECO/INDICADOR | 52.0% | 0.5846 | 0.5468 | 0.5695 | 0.5064 | NA | NA | INSTAVEL |
| volume_24h_candles | PRECO/INDICADOR | 96.9% | 0.5209 | 0.5235 | 0.5207 | 0.5219 | NA | NA | FRACA |
| volume_24h_coverage_hours | PRECO/INDICADOR | 96.9% | 0.5191 | 0.5239 | 0.5189 | 0.5234 | NA | NA | FRACA |
| volume_24h_usdt_aggregated | PRECO/INDICADOR | 52.0% | 0.4808 | 0.5083 | 0.4823 | 0.5125 | NA | NA | FRACA |
| volume_last_candle_base | PRECO/INDICADOR | 96.9% | 0.5657 | 0.5449 | 0.5150 | 0.5052 | NA | NA | INSTAVEL |
| volume_last_candle_usdt | PRECO/INDICADOR | 96.9% | 0.5368 | 0.5031 | 0.4806 | 0.5066 | NA | NA | FRACA |
| vwap | PRECO/INDICADOR | 96.9% | 0.4527 | 0.4530 | 0.4634 | 0.4938 | NA | NA | FRACA |
| vwap_candle_count | PRECO/INDICADOR | 96.9% | 0.5198 | 0.5243 | 0.5197 | 0.5234 | NA | NA | FRACA |

`contract_inclusion_proposal`: `[]`. [calc]

## P2 — Benchmark Determinístico ATR Pré-Registrado
Regra fixa: `atr_pct < 1.0`; cobertura ATR `2501/2501`; fee líquida de config `0.2` pp por trade. [config/query/calc]
| coorte | n | win_rate_v2 | EV_liq_medio_pct | SE | IC95_bootstrap | trades_dia |
| --- | --- | --- | --- | --- | --- | --- |
| ATR<1.0 | 1728 | 24.94% | -0.1575 | 0.0254 | [-0.2060, -0.1085] | 83.95 |
| ATR>=1.0 | 773 | 44.11% | -0.3774 | 0.0708 | [-0.5161, -0.2400] | 37.72 |
| OPERAR_TUDO_ATR_DISPONIVEL | 2501 | 30.87% | -0.2255 | 0.0281 | [-0.2800, -0.1721] | 121.50 |
| OPERAR_TUDO_ELEGIVEL | 2501 | 30.87% | -0.2255 | 0.0281 | [-0.2800, -0.1721] | 121.50 |

### Estabilidade por Quinzena
| janela | n_atr | n_regra | win_rate_v2_regra | EV_liq_regra | IC95_bootstrap |
| --- | --- | --- | --- | --- | --- |
| Q1 2026-06-14..2026-06-29 | 1326 | 869 | 21.40% | -0.2204 | [-0.2854, -0.1518] |
| Q2 2026-06-29..2026-07-14 | 1175 | 859 | 28.52% | -0.0938 | [-0.1698, -0.0215] |
| Q3 2026-07-14..2026-07-29 | 0 | 0 | NA | NA | NA |

### EXPLORATÓRIO — Curva EV x Threshold ATR
Este grid é exploratório e não altera a regra pré-registrada.
| atr_threshold | n | win_rate_v2 | EV_liq_medio_pct | IC95_bootstrap |
| --- | --- | --- | --- | --- |
| 0.25 | 369 | 14.91% | -0.1584 | [-0.2480, -0.0615] |
| 0.50 | 1199 | 21.43% | -0.1251 | [-0.1782, -0.0686] |
| 0.75 | 1524 | 23.82% | -0.1332 | [-0.1817, -0.0788] |
| 1.00 | 1728 | 24.94% | -0.1575 | [-0.2106, -0.1118] |
| 1.25 | 1897 | 25.83% | -0.1629 | [-0.2149, -0.1157] |
| 1.50 | 2027 | 26.79% | -0.1635 | [-0.2091, -0.1205] |
| 1.75 | 2144 | 28.17% | -0.1644 | [-0.2215, -0.1195] |
| 2.00 | 2219 | 28.53% | -0.1679 | [-0.2240, -0.1224] |
| 2.25 | 2261 | 28.79% | -0.1764 | [-0.2306, -0.1254] |
| 2.50 | 2294 | 29.12% | -0.1807 | [-0.2349, -0.1244] |
| 2.75 | 2321 | 29.47% | -0.1809 | [-0.2352, -0.1343] |
| 3.00 | 2339 | 29.50% | -0.1849 | [-0.2315, -0.1307] |
| 3.25 | 2357 | 29.74% | -0.1869 | [-0.2499, -0.1385] |
Veredito P2: **REFUTADA**. [calc]

## P3 — Painel de Labels Diagnósticos
Pré-validação `max_profit_first_30m` vs `mfe_pct` em `holding_seconds <= 1800`: r=`0.6255`, n=`165`. Fonte `mp_30m`: `SUSPEITA_NAO_USAR`. [query/calc]

### Base Rates
| alvo | n_valid | positivos | base_rate |
| --- | --- | --- | --- |
| label_v2 | 2501 | 772 | 30.87% |
| diag_cleanwin | 2501 | 810 | 32.39% |
| diag_mp30 | 1111 | 462 | 41.58% |

### Concordância com label v2
| diagnostico | v2=0 diag=0 | v2=0 diag=1 | v2=1 diag=0 | v2=1 diag=1 | corr |
| --- | --- | --- | --- | --- | --- |
| diag_cleanwin | 1567 | 162 | 124 | 648 | 0.7361 |

### AUC Univariada das 33 Features
| feature | AUC label_v2 | AUC diag_cleanwin | AUC diag_mp30 |
| --- | --- | --- | --- |
| atr_pct | 0.6470 | 0.5570 | SUSPEITA_NAO_USADO |
| bb_width | 0.6351 | 0.5437 | SUSPEITA_NAO_USADO |
| spread_pct | 0.5953 | 0.5203 | SUSPEITA_NAO_USADO |
| orderbook_depth_usdt | 0.4120 | 0.4742 | SUSPEITA_NAO_USADO |
| taker_ratio | 0.5319 | 0.5481 | SUSPEITA_NAO_USADO |
| ema50_distance_pct | 0.5241 | 0.5383 | SUSPEITA_NAO_USADO |
| volume_delta | 0.5235 | 0.5381 | SUSPEITA_NAO_USADO |
| flow_strength | 0.5228 | 0.5370 | SUSPEITA_NAO_USADO |
| rsi | 0.5255 | 0.5327 | SUSPEITA_NAO_USADO |
| rsi_slope_5 | 0.5264 | 0.5320 | SUSPEITA_NAO_USADO |
| ema21_ema50_distance_pct | 0.5115 | 0.5320 | SUSPEITA_NAO_USADO |
| macd_histogram_slope | 0.5284 | 0.5293 | SUSPEITA_NAO_USADO |
| di_plus_minus_diff | 0.5223 | 0.5291 | SUSPEITA_NAO_USADO |
| rsi_slope_3 | 0.5172 | 0.5255 | SUSPEITA_NAO_USADO |
| momentum_strength | 0.5110 | 0.5224 | SUSPEITA_NAO_USADO |
| macd_histogram_pct | 0.5109 | 0.5209 | SUSPEITA_NAO_USADO |
| macd_hist_slope_3 | 0.5037 | 0.5206 | SUSPEITA_NAO_USADO |
| ema_distance_pct | 0.5109 | 0.5201 | SUSPEITA_NAO_USADO |
| ema200_distance_pct | 0.5187 | 0.5199 | SUSPEITA_NAO_USADO |
| delta_normalized | 0.5197 | 0.5197 | SUSPEITA_NAO_USADO |
| vwap_distance_pct | 0.5027 | 0.5174 | SUSPEITA_NAO_USADO |
| higher_lows_5 | 0.5161 | 0.5120 | SUSPEITA_NAO_USADO |
| ema9_gt_ema21 | 0.5159 | 0.5140 | SUSPEITA_NAO_USADO |
| trend_alignment | 0.5097 | 0.5121 | SUSPEITA_NAO_USADO |
| adx | 0.4899 | 0.4884 | SUSPEITA_NAO_USADO |
| macd_hist_slope_5 | 0.4984 | 0.5106 | SUSPEITA_NAO_USADO |
| higher_highs_5 | 0.5096 | 0.5095 | SUSPEITA_NAO_USADO |
| volume_spike | 0.5077 | 0.4989 | SUSPEITA_NAO_USADO |
| adx_acceleration | 0.4953 | 0.5070 | SUSPEITA_NAO_USADO |
| adx_slope_3 | 0.4930 | 0.5025 | SUSPEITA_NAO_USADO |
| volume_24h_usdt | 0.4992 | 0.5044 | SUSPEITA_NAO_USADO |
| ema50_gt_ema200 | 0.4978 | 0.5031 | SUSPEITA_NAO_USADO |
| vwap_reclaim_bool | 0.5028 | 0.5022 | SUSPEITA_NAO_USADO |
Veredito P3: **NAO** — features fracas contra v2 não acordam materialmente contra os diagnósticos. Features destacadas por diagnóstico: `[]`. [calc]

## Matriz de Decisão Pós-Veredito
| linha pré-registrada | próximo prompt/recomendação |
| --- | --- |
| Retreino nº 2 APPROVED pelo gate | Usar esta pesquisa apenas como restrição adicional N+1; não promover chave por P1 isolado. |
| SINAL INSUFICIENTE + P1 promissoras | Abrir ciclo N+1 com `contract_inclusion_proposal`; holdout futuro como juiz. |
| SINAL INSUFICIENTE + P1 vazio/fraco + P2 viável | Produto pode operar por política determinística ATR enquanto ML fica em pausa ou como baseline obrigatório a bater. |
| INCONCLUSIVO / amostra ou IC insuficiente | Aguardar 5k-6k elegíveis; preparar contrato N+1 sem alterar config nem consumir holdout. |

## Limites Epistêmicos
- Esta análise usa toda a população elegível pós-fronteira, inclusive linhas que podem compor test set futuro; os resultados são descritivos.
- AUC por janela com caudas pequenas deve ser lida com IC operacional ~±0,04-0,05.
- P2 usa regra ATR pré-registrada fixa; o grid é apenas forma da curva.
- `diag_mp30` só pode ser usado se a pré-validação permanecer r>=0,8.
- O mérito de qualquer contrato N+1 só pode ser provado pelo gate do ciclo N+1 em holdout próprio.

## Ledger de Evidências
| número/reportado | origem | valor literal/fórmula |
| --- | --- | --- |
| production_psycopg2_ok | [query] conexão psycopg2 readonly | Railway Postgres via DATABASE_PUBLIC_URL; segredo omitido |
| gate_eligible | [query] COUNT shadow_trades | 2501 |
| analysis_eligible | [query] COUNT com pnl/snapshot | 2501 |
| feature_count | [código] FEATURE_COLUMNS | 33 |
| ignored_diff_count | [calc] all_keys - features - meta_* | 94 - 33 - 4 = 64 |
| ml_fee_roundtrip_pct | [config: ml] | 0.2 |
| P2_verdict | [calc] EV/IC ATR<1.0 | REFUTADA |
| mp30_prevalidation | [calc] Pearson(max_profit_first_30m,mfe_pct | holding<=1800) | r=0.6255 n=165 |
| P3_verdict | [calc] comparação AUC features x targets | NAO |
| contract_inclusion_proposal | [calc] P1 vereditos PROMISSORA | [] |

## Runner Output Verbatim
```json
{
  "now_utc": "2026-07-05T12:19:08.416279+00:00",
  "valid_from": "2026-06-14T21:33:10.277143+00:00",
  "rows": 2501,
  "all_keys": 94,
  "feature_columns": 33,
  "meta_keys": [
    "_directional_backfill",
    "_features_captured_at",
    "_features_coverage",
    "_oldest_indicator_age_s"
  ],
  "ignored_keys": 64,
  "shortlist": [
    "atr",
    "bb_lower",
    "bb_middle",
    "bb_upper",
    "bid_ask_imbalance",
    "close",
    "di_minus",
    "di_plus",
    "ema10",
    "ema200",
    "ema21",
    "ema30",
    "ema5",
    "ema50",
    "ema9",
    "ema9_distance_pct",
    "ema9_gt_ema50",
    "ema_full_alignment",
    "entry_exhaustion_score",
    "macd",
    "macd_histogram",
    "macd_histogram_mean_10",
    "macd_histogram_prev",
    "macd_histogram_std_10",
    "macd_signal_line",
    "obv",
    "obv_slope_5",
    "orderbook_pressure",
    "price",
    "psar",
    "psar_af",
    "psar_distance_pct",
    "psar_ep",
    "psar_reversal",
    "rsi_6",
    "stoch_d",
    "stoch_k",
    "taker_buy_volume",
    "taker_sell_volume",
    "volume_24h_base",
    "volume_24h_base_aggregated",
    "volume_24h_candles",
    "volume_24h_coverage_hours",
    "volume_24h_usdt_aggregated",
    "volume_last_candle_base",
    "volume_last_candle_usdt",
    "vwap",
    "vwap_candle_count"
  ],
  "proposal": [],
  "p2_verdict": "REFUTADA",
  "p3_verdict": "NAO",
  "mp30_r": 0.6254508024023805,
  "mp30_n": 165
}
```

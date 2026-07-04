# Evidencias de qualidade de dados do pipeline ML - 2026-07

Auditoria read-only executada em 2026-07-02 sobre codigo local e banco Railway usado pelos scripts locais.

Fronteira limpa usada quando aplicavel: `2026-06-14 21:33:00+00`.

## Sumario executivo

| Q | Veredito | Confianca | Acao habilitada |
|---|---|---|---|
| Q1 | H0 parcial: trainer global le `shadow_trades` com `source='L1_SPECTRUM'`; porem `ml_dataset_valid_from` e pulado para L1 | ALTA | Manter decisao de usar `shadow_trades/L1_SPECTRUM`; decidir se a fronteira limpa deve ser obrigatoria tambem para L1 |
| Q2 | H0: `ml_experiment_labels` aparece so em migracao/schema; nao e consumida pelo trainer | CERTEZA | Tratar como sink legado/paralelo, nao fonte de treino |
| Q3 | H0: achado "100% timeout" foi artefato; fonte real tem `SL_HIT`, `TP_HIT`, poucos `TIMEOUT` | CERTEZA | Nao corrigir gerador de labels por colapso de outcome |
| Q4 | H1: implementacao nao e literalmente `outcome='TP_HIT' AND holding_seconds <= T`; usa `ttt_fast_win_bucket` ou PnL+holding | CERTEZA | Corrigir/alinhar contrato de label antes de novos benchmarks |
| Q5 | H0: `TP_HIT` tem PnL medio positivo e `SL_HIT` negativo | CERTEZA | Nao investigar inversao label/PnL em massa |
| Q6 | H0 quanto ao base rate: label real ~22%; threshold e calibrado, nao default fixo 0.5 | ALTA | Priorizar calibracao/EV e contrato de label; rebalanceamento extremo nao e a primeira hipotese |
| Q7 | H0: coverage derivado pelo extractor tem mediana alta; readiness report nao guarda lista nominal | ALTA | Nao tratar como "70% missing em tudo"; melhorar persistencia nominal do report |
| Q8 | H0: XGBoost recebe NaN nativo; nao ha imputacao no trainer canonico | CERTEZA | Manter NaN nativo; ignorar `fillna(0)` em modulos legados fora do caminho canonico |
| Q9 | H0: as 15 features mortas de v3 foram removidas dos modelos 33 features atuais | CERTEZA | Nao reabrir dead features antigas; validar so se macro-features voltarem |
| Q10 | INDECIDIVEL COM DADOS OBSERVACIONAIS quanto ao efeito macro; comparabilidade e baixa por feature set/status/metadata | MEDIA | Fazer experimento pareado pre-registrado macro on/off |
| Q11 | H0 provavel: modelo 7.082 e L3, sem range persistido, e dataset L3 tem dados pre-fronteira disponiveis | MEDIA | Nao usar v53 como benchmark limpo ate haver range/dataset hash audivel |
| Q12 | H0: `decisions_log.score` e score do pipeline, probabilidade ML fica em campos/payload separados | CERTEZA | Reclassificar "score saturation" como achado do Score Engine, nao do XGBoost |

## Regressoes P0

- Q4: contrato de label divergente. A docstring diz `outcome == 'TP_HIT' AND holding_seconds <= win_fast_threshold_s`, mas o codigo executado usa `ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M')` quando presente e fallback `pnl_pct > 0.96 AND holding_ok`.
- Q1/Q11: `ml_dataset_valid_from` existe no config, mas o trainer global explicitamente pula o filtro quando `ML_SOURCE_FILTER != 'L3'`. Isso nao poe `decisions_log` no treino, mas viola a fronteira limpa se ela deveria valer para todo treino.

## Registro completo por questao

### Q1 - Quais tabelas alimentam de fato o trainer?

HIPOTESES: H0 = trainer le shadow trades `L1_SPECTRUM` + labels do simulador. | H1 = trainer le `ml_experiment_labels`, `decisions_log` ou outra fonte.

EVIDENCIA COLETADA:

[E1] Comando:

```bash
rg -n "ml_experiment_labels|ml_training_dataset|L1_SPECTRUM|decisions_log|FROM\s+shadow" backend/ --type py -g '!*test*' -g '!*migration*'
```

Output relevante verbatim:

```text
backend/app\ml\dataset_policy.py:339:                FROM shadow_trades
backend/app\ml\dataset_policy.py:439:                FROM shadow_trades
backend/app\ml\dataset_policy.py:482:            FROM shadow_trades
backend/app\ml\dataset_policy.py:523:                FROM shadow_trades
backend/app\ml\prediction_service.py:14:VALID_MODEL_LANES = frozenset({"L1_SPECTRUM", "L3_PROFILE"})
backend/app\services\decision_orchestrator.py:68:            WHERE model_lane = 'L1_SPECTRUM'
backend/app\services\shadow_trade_service.py:121:# L1_SPECTRUM — fonte exclusiva de treino do ML (migration 073+).
backend/app\services\shadow_trade_service.py:124:SHADOW_SOURCE_L1_SPECTRUM = "L1_SPECTRUM"
```

[E2] `ml_trainer/job.py:116`:

```text
116:ML_SOURCE_FILTER         = os.getenv("ML_SOURCE_FILTER", "L1_SPECTRUM")
```

[E3] `ml_trainer/job.py:578-622`:

```text
578:    # B4: ml_dataset_valid_from â€” exclude pre-fix L3 shadows where features_snapshot was empty.
579:    # Only applied for ML_SOURCE_FILTER='L3' (the source that had the snapshot bug).
580:    # For other sources (L1_SPECTRUM etc.) the features_snapshot::text <> '{}' filter
581:    # already guarantees quality â€” applying valid_from would only waste valid records.
582:    valid_from_clause = ""
583:    valid_from_params: dict = {}
584:    if _dataset_valid_from and ML_SOURCE_FILTER == "L3":
585:        valid_from_clause = "AND created_at >= :valid_from"
586:        valid_from_params = {"valid_from": _dataset_valid_from}
597:    with engine.connect() as conn:
602:        result = conn.execute(text(f"""
603:            SELECT
604:                symbol, source, pnl_pct, net_return_pct, holding_seconds, outcome,
605:                features_snapshot, created_at,
606:                ttt_outcome, ttt_fast_win_bucket,
607:                time_to_tp_minutes, elapsed_minutes, profit_velocity
608:            FROM shadow_trades
609:            WHERE source = :source_filter
610:              AND outcome IN ('TP_HIT', 'SL_HIT', 'TIMEOUT')
611:              AND pnl_pct IS NOT NULL
612:              AND features_snapshot IS NOT NULL
613:              AND features_snapshot::text <> '{{}}'
614:              AND created_at >= (:dataset_query_cutoff - CAST(:days AS interval))
615:              AND created_at <= :dataset_query_cutoff
616:              {exclude_clause}
617:              {valid_from_clause}
618:              {cutoff_clause}
619:            ORDER BY created_at ASC
620:        """), {"days": f"{DAYS_LOOKBACK} days", "source_filter": ML_SOURCE_FILTER,
621:               "dataset_query_cutoff": dataset_query_cutoff,
622:               **exclude_params, **valid_from_params, **cutoff_params})
```

[E4] `backend/app/tasks/celery_app.py:200,411`:

```text
200:    "app.tasks.profile_intelligence_job.train_ml_challengers_for_user": {"queue": QUEUE_STRUCTURAL_COMPUTE},
411:    "app.tasks.profile_intelligence_job.train_ml_challengers_for_user": {
```

VEREDITO: H0 parcial.

CONFIANCA: ALTA.

ACAO HABILITADA: Confirmar se a fronteira `ml_dataset_valid_from` deve ser aplicada a L1; nao ha evidencia de `decisions_log` no caminho do trainer global.

### Q2 - `ml_experiment_labels` e consumida por alguem?

HIPOTESES: H0 = sink legado/paralelo. | H1 = participa do treino ou gates de promocao.

EVIDENCIA COLETADA:

[E1] Comando:

```bash
rg -n "ml_experiment_labels" backend/ frontend/ --type py --type ts -g '!*migration*'
```

Output verbatim:

```text
backend/alembic\versions\000_baseline_prod_schema.py:29:    op.execute(sa.text('CREATE SEQUENCE IF NOT EXISTS ml_experiment_labels_id_seq'))
backend/alembic\versions\000_baseline_prod_schema.py:379:        CREATE TABLE IF NOT EXISTS ml_experiment_labels (
backend/alembic\versions\000_baseline_prod_schema.py:380:          id bigint NOT NULL DEFAULT nextval('ml_experiment_labels_id_seq'::regclass),
backend/alembic\versions\000_baseline_prod_schema.py:1603:            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_shadow_trade_id_key' AND connamespace = 'public'::regnamespace) THEN
backend/alembic\versions\000_baseline_prod_schema.py:1604:                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_shadow_trade_id_key UNIQUE (shadow_trade_id);
backend/alembic\versions\000_baseline_prod_schema.py:1873:            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ml_experiment_labels_pkey' AND connamespace = 'public'::regnamespace) THEN
backend/alembic\versions\000_baseline_prod_schema.py:1874:                ALTER TABLE ml_experiment_labels ADD CONSTRAINT ml_experiment_labels_pkey PRIMARY KEY (id);
```

Classificacao: todas as ocorrencias sao schema/migracao; nenhuma LEITURA-TREINO, LEITURA-UI ou LEITURA-RELATORIO encontrada.

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Remover dependencia mental de `ml_experiment_labels` para diagnostico do trainer.

### Q3 - A distribuicao real de outcomes colapsou?

HIPOTESES: H0 = distribuicao normal; achado 100% timeout foi artefato. | H1 = fonte real tem outcome unico/colapsado.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT outcome, COUNT(*) FROM ml_experiment_labels GROUP BY outcome ORDER BY 2 DESC;
```

Output verbatim:

```json
[
  {"outcome": "TP_HIT", "count": 798},
  {"outcome": "SL_HIT", "count": 621}
]
```

[E2] SQL:

```sql
SELECT outcome, COUNT(*) FROM shadow_trades
WHERE created_at >= '2026-06-14 21:33:00+00' AND source = 'L1_SPECTRUM'
GROUP BY outcome ORDER BY 2 DESC;
```

Output verbatim:

```json
[
  {"outcome": "SL_HIT", "count": 1205},
  {"outcome": "TP_HIT", "count": 833},
  {"outcome": null, "count": 23},
  {"outcome": "TIMEOUT", "count": 14}
]
```

[E3] SQL:

```sql
SELECT DISTINCT outcome FROM shadow_trades WHERE source = 'L1_SPECTRUM' AND outcome != UPPER(outcome);
```

Output verbatim:

```json
[]
```

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Nao executar correcao por collapse de outcomes; investigar queries antigas com case/fonte errados.

### Q4 - A implementacao de WIN_FAST no codigo esta correta?

HIPOTESES: H0 = `outcome='TP_HIT' AND holding_seconds <= T`. | H1 = divergencia.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT config_json->>'ml_win_fast_threshold_seconds' AS ml_win_fast_threshold_seconds
FROM config_profiles WHERE config_type = 'ml';
```

Output verbatim:

```json
[
  {"ml_win_fast_threshold_seconds": "14400"}
]
```

[E2] `ml_trainer/job.py:461-463`:

```text
461:    _fee_roundtrip_pct = _ml_cfg.get("ml_fee_roundtrip_pct")
462:    _label_net_of_fees = bool(_ml_cfg.get("ml_label_net_of_fees", False))
463:    _win_fast_threshold_s = int(_ml_cfg.get("ml_win_fast_threshold_seconds", 1800))
```

[E3] `backend/app/ml/feature_extractor.py:382-409`:

```text
382:        # Target: is_win_fast label
383:        # PRIMARY: ttt_fast_win_bucket when ttt_analysis_done=True (is_tp_4h_v1).
386:        # FALLBACK: pnl_pct > 0.96% AND holding_s <= win_fast_threshold_s for
390:        ttt_bucket = r.get("ttt_fast_win_bucket")
391:        if ttt_bucket is not None:
392:            features["is_win_fast"] = 1 if ttt_bucket in ("WIN_0_15M", "WIN_15_30M") else 0
396:            holding_s = r.get("holding_seconds")
397:            holding_ok = holding_s is not None and holding_s <= win_fast_threshold_s
407:                    features["is_win_fast"] = 1 if (pnl_val > _WIN_THRESHOLD and holding_ok) else 0
409:                features["is_win_fast"] = 1 if (pnl_val > _WIN_THRESHOLD and holding_ok) else 0
```

VEREDITO: H1.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Decidir se o contrato correto e TTT bucket/PnL ou simulator `outcome`; alinhar codigo, docs e label_version antes de comparar modelos.

### Q5 - Labels sao consistentes com PnL?

HIPOTESES: H0 = `TP_HIT` positivo, `SL_HIT` negativo. | H1 = inconsistencia.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT outcome, COUNT(*) AS n, ROUND(AVG(pnl_pct)::numeric,4) AS avg_pnl,
       ROUND(AVG(holding_seconds)::numeric,0) AS avg_hold
FROM shadow_trades
WHERE created_at >= '2026-06-14 21:33:00+00' AND source = 'L1_SPECTRUM'
GROUP BY outcome ORDER BY outcome;
```

Output verbatim:

```json
[
  {"outcome": "SL_HIT", "n": 1205, "avg_pnl": "-1.1143", "avg_hold": "26708"},
  {"outcome": "TIMEOUT", "n": 14, "avg_pnl": "0.6177", "avg_hold": "154053"},
  {"outcome": "TP_HIT", "n": 833, "avg_pnl": "1.4970", "avg_hold": "16120"},
  {"outcome": null, "n": 23, "avg_pnl": null, "avg_hold": null}
]
```

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Nao priorizar correcao de inversao label/PnL.

### Q6 - Qual o base rate real e qual threshold gera as metricas?

HIPOTESES: H0 = base rate ~20-30% e threshold/calibracao explicam metricas. | H1 = desbalanceamento extremo.

EVIDENCIA COLETADA:

[E1] SQL, label conforme implementacao atual:

```sql
SELECT CASE WHEN ttt_fast_win_bucket IS NOT NULL
            THEN CASE WHEN ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M') THEN 1 ELSE 0 END
            ELSE CASE WHEN pnl_pct > 0.96 AND holding_seconds IS NOT NULL AND holding_seconds <= 14400 THEN 1 ELSE 0 END
       END AS label,
       COUNT(*), ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND features_snapshot IS NOT NULL AND features_snapshot::text <> '{}'
  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT') AND pnl_pct IS NOT NULL
GROUP BY label ORDER BY label;
```

Output verbatim:

```json
[
  {"label": 0, "count": 2383, "pct": "77.75"},
  {"label": 1, "count": 682, "pct": "22.25"}
]
```

[E2] SQL pos-fronteira:

```sql
SELECT CASE WHEN ttt_fast_win_bucket IS NOT NULL
            THEN CASE WHEN ttt_fast_win_bucket IN ('WIN_0_15M','WIN_15_30M') THEN 1 ELSE 0 END
            ELSE CASE WHEN pnl_pct > 0.96 AND holding_seconds IS NOT NULL AND holding_seconds <= 14400 THEN 1 ELSE 0 END
       END AS label,
       COUNT(*), ROUND(100.0*COUNT(*)/SUM(COUNT(*)) OVER (),2) AS pct
FROM shadow_trades
WHERE source = 'L1_SPECTRUM'
  AND created_at >= '2026-06-14 21:33:00+00'
  AND features_snapshot IS NOT NULL AND features_snapshot::text <> '{}'
  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT') AND pnl_pct IS NOT NULL
GROUP BY label ORDER BY label;
```

Output verbatim:

```json
[
  {"label": 0, "count": 1610, "pct": "78.46"},
  {"label": 1, "count": 442, "pct": "21.54"}
]
```

[E3] `backend/app/ml/trainer.py:473-489`:

```text
473:                proba_test = self.model.predict_proba(X_test)[:, 1]
475:                # Audit P0-06: Calibrate threshold on VALIDATION set, not test set.
478:                proba_val = self.model.predict_proba(X_val)[:, 1]
482:                calibrated_threshold = _calibrate_threshold(
483:                    y_val.to_numpy(), proba_val, pnl_values=pnl_val
484:                )
485:                pred_test = (proba_test >= calibrated_threshold).astype(int)
488:                    precision = float(precision_score(y_test, pred_test, zero_division=0))
489:                    recall = float(recall_score(y_test, pred_test, zero_division=0))
```

[E4] SQL:

```sql
SELECT COUNT(*) FROM ml_predictions
WHERE win_fast_probability IS NOT NULL AND created_at >= NOW() - INTERVAL '7 days';
```

Output verbatim:

```json
[
  {"count": 1261}
]
```

[E5] Logs locais:

```text
logs.txt Length=0
rg -n "NaN|Infinity|inf|jsonb|raw_model_output|win_fast_probability" logs.txt backend -g '*.log'
<sem output; exit code 1>
```

VEREDITO: H0.

CONFIANCA: ALTA.

ACAO HABILITADA: Gerar curva threshold x EV a partir de `ml_predictions.win_fast_probability`; nao iniciar por rebalanceamento extremo.

### Q7 - Coverage e features

HIPOTESES: H0 = min baixo e cauda ruim; mediana alta. | H1 = coverage baixo generalizado.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT id, dataset_id, total_features, dead_features, dead_feature_ratio, min_coverage, readiness_status, created_at
FROM ml_dataset_readiness_reports ORDER BY created_at DESC LIMIT 5;
```

Output verbatim:

```json
[
  {"id": "394b452c-1cb4-4df3-b58d-37b8c0e5bd04", "dataset_id": "cd285faa84aa446695f110784f4b5b74", "total_features": 33, "dead_features": 0, "dead_feature_ratio": "0.0", "min_coverage": "0.3", "readiness_status": "ready", "created_at": "2026-06-30 19:30:46.820751+00:00"},
  {"id": "fd0e3ac7-3c4f-4cf6-9fb1-363306744907", "dataset_id": "eb29c126d331419fa0df1d51873162ff", "total_features": 33, "dead_features": 0, "dead_feature_ratio": "0.0", "min_coverage": "0.3", "readiness_status": "ready", "created_at": "2026-06-30 19:30:32.942755+00:00"},
  {"id": "6b9b84c5-59ad-4a21-a57a-96891426c14c", "dataset_id": "1de9e330722247ef8e48d990ca8250e1", "total_features": 48, "dead_features": 15, "dead_feature_ratio": "0.3125", "min_coverage": "0.3", "readiness_status": "ready", "created_at": "2026-06-30 03:16:49.531414+00:00"}
]
```

[E2] Comando derivado pelo mesmo extractor usado no treino (`extract_features`) sobre `shadow_trades/L1_SPECTRUM`, janela `2026-06-11` a `2026-06-25`:

```json
{
  "summary": {
    "min": 0.5552,
    "p25": 0.9985,
    "median": 0.999,
    "p75": 0.9995,
    "max": 0.9995,
    "lt_0_5": [],
    "lt_0_3": []
  }
}
```

Coverage nominal completo:

```json
[
  {"feature": "taker_ratio", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "volume_delta", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "rsi", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "macd_histogram_pct", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "macd_histogram_slope", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "adx", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "adx_acceleration", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "spread_pct", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "volume_spike", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "bb_width", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "atr_pct", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "ema9_gt_ema21", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "ema50_gt_ema200", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "volume_24h_usdt", "rows": 1967, "present": 1092, "coverage": 0.5552},
  {"feature": "orderbook_depth_usdt", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "vwap_distance_pct", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "flow_strength", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "trend_alignment", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "momentum_strength", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "delta_normalized", "rows": 1967, "present": 1092, "coverage": 0.5552},
  {"feature": "ema_distance_pct", "rows": 1967, "present": 1964, "coverage": 0.9985},
  {"feature": "ema50_distance_pct", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "ema200_distance_pct", "rows": 1967, "present": 1965, "coverage": 0.999},
  {"feature": "rsi_slope_3", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "rsi_slope_5", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "macd_hist_slope_3", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "macd_hist_slope_5", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "ema21_ema50_distance_pct", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "di_plus_minus_diff", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "adx_slope_3", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "vwap_reclaim_bool", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "higher_highs_5", "rows": 1967, "present": 1966, "coverage": 0.9995},
  {"feature": "higher_lows_5", "rows": 1967, "present": 1966, "coverage": 0.9995}
]
```

VEREDITO: H0.

CONFIANCA: ALTA.

ACAO HABILITADA: Persistir coverage nominal no readiness report; nao tratar como missing generalizado.

### Q8 - Como o trainer trata missing values?

HIPOTESES: H0 = NaN nativo do XGBoost. | H1 = imputacao.

EVIDENCIA COLETADA:

[E1] Comando:

```bash
rg -n "fillna|imputer|SimpleImputer|missing|np\.nan" backend/ --type py -g '!*test*' | rg -i "ml|train|feature|dataset"
```

Output relevante verbatim:

```text
backend/app\ml\trainer.py:302:        # Task #324 ? preserve NaN. XGBoost handles missing values natively;
backend/app\ml\trainer.py:303:        # fillna(0.0) collapses "missing" and "true zero" (e.g. taker_ratio=0
backend/app\ml\trainer.py:384:                # Task #324 ? NaN preserved natively. NEVER fillna upstream.
backend/app\ml\trainer.py:385:                "missing": float("nan"),
backend/app\ml\trainer.py:444:            "missing": float("nan"),
backend/app\ml\dataset_builder.py:5:    "This module uses fillna(0), raw EMAs, and incorrect label alignment. See Audit Sprint 4.",
backend/app\ml\model_loader.py:5:    "This module loads from local files and uses fillna(0). See Audit Sprint 4.",
```

[E2] `backend/app/ml/trainer.py:302-306`:

```text
302:        # Task #324 â€” preserve NaN. XGBoost handles missing values natively;
303:        # fillna(0.0) collapses "missing" and "true zero" (e.g. taker_ratio=0
304:        # = 100% sells) into the same semantic class, sabotaging splits.
305:        X_train = train_df[feature_cols].astype("float32")
306:        X_val   = val_df[feature_cols].astype("float32")
```

[E3] `backend/app/ml/trainer.py:384-445`:

```text
384:                # Task #324 â€” NaN preserved natively. NEVER fillna upstream.
385:                "missing": float("nan"),
411:            m = xgb.XGBClassifier(**params, early_stopping_rounds=20)
412:            m.fit(
413:                X_train, y_train,
414:                eval_set=[(X_val, y_val)],
444:            "missing": float("nan"),
```

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Manter NaN nativo no caminho canonico; tratar `dataset_builder.py` e `model_loader.py` como legado se nao forem usados no trainer atual.

### Q9 - As 15 dead features do dataset v3 ainda existem no dataset atual?

HIPOTESES: H0 = removidas. | H1 = parte ainda presente.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT version, feature_count, feature_columns_json
FROM ml_models WHERE version IN ('51','56','57','52') ORDER BY version;
```

Output verbatim resumido por lista:

```json
{
  "v52_48_features": [
    "taker_ratio","volume_delta","rsi","macd_histogram_pct","macd_histogram_slope","adx","adx_acceleration","spread_pct","volume_spike","bb_width","atr_pct","ema9_gt_ema21","ema50_gt_ema200","volume_24h_usdt","orderbook_depth_usdt","vwap_distance_pct","flow_strength","trend_alignment","momentum_strength","delta_normalized","ema_distance_pct","ema50_distance_pct","ema200_distance_pct","rsi_slope_3","rsi_slope_5","macd_hist_slope_3","macd_hist_slope_5","ema21_ema50_distance_pct","di_plus_minus_diff","adx_slope_3","vwap_reclaim_bool","higher_highs_5","higher_lows_5","sp500_change_1h","nasdaq_change_1h","russell2000_change_1h","vix_value","vix_change_1h","dxy_value","dxy_change_1h","us10y_yield","us10y_change_1h","btc_dominance","btc_dominance_change","crypto_market_cap_change","crypto_volume_change","fear_greed_index","macro_context_available"
  ],
  "v57_33_features": [
    "taker_ratio","volume_delta","rsi","macd_histogram_pct","macd_histogram_slope","adx","adx_acceleration","spread_pct","volume_spike","bb_width","atr_pct","ema9_gt_ema21","ema50_gt_ema200","volume_24h_usdt","orderbook_depth_usdt","vwap_distance_pct","flow_strength","trend_alignment","momentum_strength","delta_normalized","ema_distance_pct","ema50_distance_pct","ema200_distance_pct","rsi_slope_3","rsi_slope_5","macd_hist_slope_3","macd_hist_slope_5","ema21_ema50_distance_pct","di_plus_minus_diff","adx_slope_3","vwap_reclaim_bool","higher_highs_5","higher_lows_5"
  ],
  "dead_features_inferred_as_v52_minus_v57": [
    "sp500_change_1h","nasdaq_change_1h","russell2000_change_1h","vix_value","vix_change_1h","dxy_value","dxy_change_1h","us10y_yield","us10y_change_1h","btc_dominance","btc_dominance_change","crypto_market_cap_change","crypto_volume_change","fear_greed_index","macro_context_available"
  ],
  "intersection_with_current_v57": []
}
```

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Nao corrigir dataset atual por dead macro-features antigas.

### Q10 - Os 3 modelos com macro-features sao comparaveis aos 60 sem?

HIPOTESES: H0 = nao comparaveis. | H1 = comparaveis exceto macro on/off.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT version, status, created_at, train_samples, val_samples, test_samples,
       roc_auc, precision_score, recall_score, decision_threshold,
       source_filter, model_lane, target_window_seconds, macro_features_enabled,
       train_from, train_to, dataset_query_cutoff
FROM ml_models WHERE macro_features_enabled = true ORDER BY created_at;
```

Output verbatim:

```json
[
  {"version": "51", "status": "rejected", "created_at": "2026-06-30 03:16:49.563583+00:00", "train_samples": 1350, "val_samples": 269, "test_samples": 297, "roc_auc": 0.6461891208096142, "precision_score": 0.4166666666666667, "recall_score": 0.053763440860215055, "decision_threshold": 0.5078701877593994, "source_filter": "L1_SPECTRUM", "model_lane": null, "target_window_seconds": 14400, "macro_features_enabled": true, "train_from": "2026-06-11 00:00:00+00:00", "train_to": "2026-06-25 00:00:00+00:00", "dataset_query_cutoff": "2026-06-29 23:16:41.132588+00:00"},
  {"version": "56", "status": "rejected", "created_at": "2026-06-30 19:30:32.986698+00:00", "train_samples": 1325, "val_samples": 267, "test_samples": 291, "roc_auc": 0.663855150697256, "precision_score": 0.5, "recall_score": 0.22807017543859648, "decision_threshold": 0.5014168524742126, "source_filter": "L1_SPECTRUM", "model_lane": null, "target_window_seconds": 14400, "macro_features_enabled": true, "train_from": "2026-06-11 00:00:00+00:00", "train_to": "2026-06-25 00:00:00+00:00", "dataset_query_cutoff": "2026-06-30 15:30:24.613184+00:00"},
  {"version": "57", "status": "rejected", "created_at": "2026-06-30 19:30:46.858864+00:00", "train_samples": 1325, "val_samples": 267, "test_samples": 291, "roc_auc": 0.6893837156995052, "precision_score": 0.4473684210526316, "recall_score": 0.2982456140350877, "decision_threshold": 0.5069197201728821, "source_filter": "L1_SPECTRUM", "model_lane": null, "target_window_seconds": 14400, "macro_features_enabled": true, "train_from": "2026-06-11 00:00:00+00:00", "train_to": "2026-06-25 00:00:00+00:00", "dataset_query_cutoff": "2026-06-30 15:30:39.883498+00:00"}
]
```

[E2] SQL:

```sql
SELECT version, status, created_at, train_samples, val_samples, test_samples,
       roc_auc, precision_score, recall_score, decision_threshold,
       source_filter, model_lane, target_window_seconds, macro_features_enabled,
       train_from, train_to, dataset_query_cutoff
FROM ml_models
WHERE macro_features_enabled = false
  AND created_at BETWEEN '2026-06-29 00:00:00+00' AND '2026-07-01 00:00:00+00'
ORDER BY created_at DESC LIMIT 5;
```

Output verbatim:

```json
[
  {"version": "55", "status": "candidate", "created_at": "2026-06-30 18:20:01.025818+00:00", "train_samples": 2903, "val_samples": 968, "test_samples": 968, "roc_auc": 0.5668857773175454, "precision_score": 0.15432098765432098, "recall_score": 0.5639097744360902, "decision_threshold": 0.49109587026305646, "source_filter": "L3_LAB", "model_lane": "L3_LAB_PROFILE", "target_window_seconds": 1800, "macro_features_enabled": false, "train_from": null, "train_to": null, "dataset_query_cutoff": null},
  {"version": "54", "status": "candidate", "created_at": "2026-06-30 17:30:22.676926+00:00", "train_samples": 2902, "val_samples": 968, "test_samples": 968, "roc_auc": 0.588948244389563, "precision_score": 0.14246196403872752, "recall_score": 0.7686567164179104, "decision_threshold": 0.3753038570025795, "source_filter": "L3_LAB", "model_lane": "L3_LAB_PROFILE", "target_window_seconds": 1800, "macro_features_enabled": false, "train_from": null, "train_to": null, "dataset_query_cutoff": null},
  {"version": "53", "status": "candidate", "created_at": "2026-06-30 12:08:48.442507+00:00", "train_samples": 7082, "val_samples": 2361, "test_samples": 2361, "roc_auc": 0.7205116741635569, "precision_score": 0.392887383573243, "recall_score": 0.7307086614173228, "decision_threshold": 0.1951936547890525, "source_filter": "L3", "model_lane": "L3_PROFILE", "target_window_seconds": 14400, "macro_features_enabled": false, "train_from": null, "train_to": null, "dataset_query_cutoff": null},
  {"version": "52", "status": "active", "created_at": "2026-06-30 12:08:25.879649+00:00", "train_samples": 1484, "val_samples": 495, "test_samples": 495, "roc_auc": 0.7119591638308216, "precision_score": 0.36693548387096775, "recall_score": 0.7520661157024794, "decision_threshold": 0.31651997372558044, "source_filter": "L1_SPECTRUM", "model_lane": "L1_SPECTRUM", "target_window_seconds": 14400, "macro_features_enabled": false, "train_from": null, "train_to": null, "dataset_query_cutoff": null}
]
```

AVISO EPISTEMICO: mesmo que os grupos fossem comparaveis, `n=3` nao permite concluir efeito causal de macro-features. A certeza sobre efeito causal so e atingivel por experimento pareado pre-registrado, mesmo periodo/dataset/target/fonte/split/seeds, variando apenas macro on/off.

VEREDITO: INDECIDIVEL COM DADOS OBSERVACIONAIS quanto ao efeito; H0 quanto a comparabilidade.

CONFIANCA: MEDIA.

ACAO HABILITADA: Rodar experimento pareado macro on/off.

### Q11 - O modelo de 7.082 samples respeita a fronteira limpa?

HIPOTESES: H0 = treinou com dados pre-fronteira. | H1 = respeitou fronteira.

EVIDENCIA COLETADA:

[E1] SQL:

```sql
SELECT id, version, created_at, train_samples, roc_auc, source_filter, model_lane,
       target_window_seconds, train_from, train_to, dataset_query_cutoff, notes
FROM ml_models WHERE train_samples = 7082;
```

Output verbatim:

```json
[
  {
    "id": "06077ec8-9b09-4a27-936a-d858bcff1d69",
    "version": "53",
    "created_at": "2026-06-30 12:08:48.442507+00:00",
    "train_samples": 7082,
    "roc_auc": 0.7205116741635569,
    "source_filter": "L3",
    "model_lane": "L3_PROFILE",
    "target_window_seconds": 14400,
    "train_from": null,
    "train_to": null,
    "dataset_query_cutoff": null,
    "notes": "Challenger catboost | lane=L3_PROFILE | user_id=8080110c-ee9d-4a2b-a53f-6bef86dd8867 | label=is_tp_4h_v1 | win_threshold_s=14400 | roc_auc=0.7205 | prec=0.3929 | rec=0.7307 | fpr=0.4154 | test_roc=0.5620 | test_prec=0.2407 | test_rec=0.7213 | n_test=2361 | v53 | trained_by=MLChallengerService"
  }
]
```

[E2] SQL:

```sql
SELECT COUNT(*) FILTER (WHERE created_at < '2026-06-14 21:33:00+00') AS pre_boundary,
       COUNT(*) FILTER (WHERE created_at >= '2026-06-14 21:33:00+00') AS post_boundary,
       COUNT(*) AS total
FROM shadow_trades
WHERE source = 'L3'
  AND outcome IN ('TP_HIT','SL_HIT','TIMEOUT')
  AND pnl_pct IS NOT NULL
  AND features_snapshot IS NOT NULL
  AND features_snapshot::text <> '{}';
```

Output verbatim:

```json
[
  {"pre_boundary": 1457, "post_boundary": 14429, "total": 15886}
]
```

[E3] SQL L1 solicitado pelo prompt:

```sql
SELECT DATE(created_at) AS d, COUNT(*) FROM shadow_trades
WHERE source = 'L1_SPECTRUM' AND created_at >= '2026-06-14 21:33:00+00'
GROUP BY 1 ORDER BY 1;
```

Output verbatim:

```json
[
  {"d": "2026-06-14", "count": 25},
  {"d": "2026-06-15", "count": 206},
  {"d": "2026-06-16", "count": 150},
  {"d": "2026-06-17", "count": 71},
  {"d": "2026-06-18", "count": 36},
  {"d": "2026-06-19", "count": 10},
  {"d": "2026-06-20", "count": 4},
  {"d": "2026-06-21", "count": 4},
  {"d": "2026-06-22", "count": 5},
  {"d": "2026-06-23", "count": 46},
  {"d": "2026-06-24", "count": 255},
  {"d": "2026-06-25", "count": 142},
  {"d": "2026-06-26", "count": 3},
  {"d": "2026-06-27", "count": 83},
  {"d": "2026-06-28", "count": 85},
  {"d": "2026-06-29", "count": 224},
  {"d": "2026-06-30", "count": 166},
  {"d": "2026-07-01", "count": 329},
  {"d": "2026-07-02", "count": 231}
]
```

Calculo derivado L1: soma pos-fronteira em E3 = 2075 rows brutas. A media diaria de 2026-06-14 a 2026-07-02 = 2075 / 19 = 109.2 rows/dia. Para 5.000 L1 limpos, faltariam 2925 rows, ~26.8 dias nesse ritmo.

VEREDITO: H0 provavel / INDECIDIVEL com dados persistidos.

CONFIANCA: MEDIA.

ACAO HABILITADA: Nao usar v53 como benchmark limpo ate persistir range/dataset hash audivel; se v53 foi CatBoost L3, auditar `MLChallengerService` especificamente.

### Q12 - O que `decisions_log` armazena como score?

HIPOTESES: H0 = score do pipeline. | H1 = probabilidade do modelo.

EVIDENCIA COLETADA:

[E1] Comando:

```bash
rg -n "decisions_log" backend/ --type py -g '!*test*' -g '!*migration*' | rg -i "insert|score|metrics|decision"
```

Output relevante verbatim:

```text
backend/app\tasks\pipeline_scan.py:1451:        score = (processed.get("score") or {}).get("total_score", 0)
backend/app\tasks\pipeline_scan.py:1467:            "score": score,
backend/app\tasks\pipeline_scan.py:1609:            score=decision.get("score"),
backend/app\tasks\pipeline_scan.py:3351:                                    # Embed probability so it reaches decisions_log
backend/app\tasks\pipeline_scan.py:3353:                                        _d["metrics"]["win_fast_probability"] = _prob
backend/app\tasks\pipeline_scan.py:3354:                                        _d["metrics"]["ml_threshold"] = _ml.get("threshold_used")
```

[E2] `backend/app/tasks/pipeline_scan.py:1448-1473`:

```text
1448:        started_at = datetime.now(timezone.utc)
1449:        processed = engine.evaluate_asset(asset)
1450:        latency_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)
1451:        score = (processed.get("score") or {}).get("total_score", 0)
1463:        decisions.append({
1464:            "symbol": asset.get("symbol"),
1467:            "score": score,
1473:            "metrics": _decision_metrics(asset, processed),
```

[E3] `backend/app/tasks/pipeline_scan.py:1605-1628`:

```text
1605:        rows.append(DecisionLog(
1606:            symbol=decision["symbol"],
1607:            strategy=decision["strategy"],
1608:            timeframe=decision.get("timeframe"),
1609:            score=decision.get("score"),
1610:            decision=decision["decision"],
1615:            metrics=m or None,
1624:            ranking_id=_uuid_or_none(decision.get("ranking_id")),
1625:            model_id=_uuid_or_none(decision.get("model_id")),
1626:            model_version=decision.get("model_version"),
1627:            model_lane=decision.get("model_lane"),
1628:            probability=decision.get("probability"),
```

[E4] `backend/app/tasks/pipeline_scan.py:3340-3356`:

```text
3340:                                    _d["ranking_id"] = _ml_gate_scores[_sym]["ranking_id"]
3341:                                    _d["model_id"] = _ml.get("model_id")
3342:                                    _d["model_version"] = _ml.get("model_version")
3343:                                    _d["model_lane"] = "L3_PROFILE"
3344:                                    _d["probability"] = _prob
3345:                                    _d["threshold_used"] = _ml.get("threshold_used")
3351:                                    # Embed probability so it reaches decisions_log
3352:                                    if isinstance(_d.get("metrics"), dict):
3353:                                        _d["metrics"]["win_fast_probability"] = _prob
3354:                                        _d["metrics"]["ml_threshold"] = _ml.get("threshold_used")
3355:                                        _d["metrics"]["ml_model_id"] = _ml.get("model_id")
3356:                                        _d["metrics"]["ml_model_type"] = "xgboost"
```

VEREDITO: H0.

CONFIANCA: CERTEZA.

ACAO HABILITADA: Reclassificar analises de saturacao de `decisions_log.score` como analises do Score Engine/pipeline, nao da probabilidade XGBoost.

## Questoes nao respondidas

- Q7: `ml_dataset_readiness_reports` nao contem JSONB nominal por feature, apenas `total_features`, `dead_features`, `dead_feature_ratio`, `min_coverage` e status. A cobertura nominal foi reconstruida read-only com o mesmo `extract_features` do trainer. Para responder estritamente "Do JSONB do report", e necessario persistir coverage por feature no report.
- Q11: `ml_models` v53 nao persiste `train_from`, `train_to`, `dataset_query_cutoff` nem `dataset_hash`. A evidencia disponivel mostra que a fonte L3 possui linhas pre-fronteira, mas nao prova quais ids exatos entraram no treino v53. Necessario persistir range/dataset ids ou localizar logs de treino correspondentes.

## Limites epistemicos

- Esta auditoria nao prova efeito causal de macro-features. Mesmo com 3 modelos macro, comparacoes observacionais confundem feature set, fonte, split, status, codigo e seeds.
- Esta auditoria nao executou correcoes, migracoes, updates nem retraining.
- Experimento necessario para macro-features: dataset fixo, ids fixos, target fixo, split fixo, seeds fixas, mesmo algoritmo/hyperparam search budget, rodadas pareadas macro off/on, metricas pre-registradas em validacao e teste.
- Experimento necessario para v53: reconstituir ou persistir ids do dataset usado; sem isso, qualquer conclusao sobre "dataset maior = melhor" fica ancorada em evidencia incompleta.

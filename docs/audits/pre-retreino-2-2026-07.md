# Pre-Retreino 2 Audit

Data: 2026-07-03

## H1 - ml_dataset_valid_from fail-closed

- Criado helper compartilhado `app.ml.dataset_config.parse_required_ml_dataset_valid_from`.
- `ml_trainer/job.py` agora aborta se o config `ml_dataset_valid_from` estiver ausente, ilegivel ou invalido.
- `MLChallengerService` usa o mesmo parser; nao ha mais parser local divergente.
- Frontier de producao confirmado: `2026-06-14 21:33:10.277143+00`.
- Contagem L1 30d no mesmo filtro do trainer:
  - Sem frontier obrigatorio: `3230`
  - Com `created_at >= ml_dataset_valid_from`: `2217`
  - `dataset_query_cutoff`: `2026-07-03 16:59:24.269503+00:00`
- Build do dataframe L1 com correcao C:
  - `records=2217`
  - `df_rows=2217`
  - `rows_with_backfill_neutralized=354`
  - `feature_count=33`

## H2 - Modelos ativos zerados

- `ml_trainer.job._transition_model_status` passou a aceitar `notes_append`.
- v52 foi rebaixado por esse ponto unico para `rejected`.
- v50 ja estava `rejected`.
- Producao apos transicao:
  - `active_models=0`
  - v50: `rejected`, lane `L3_PROFILE`
  - v52: `rejected`, lane `L1_SPECTRUM`
- Loader sem modelo ativo:
  - `get_model(model_lane="L1_SPECTRUM")` bloqueou com `NoEligibleModelError`
  - reason: `NO_ELIGIBLE_MODEL_FOR_LANE`

## H3 - Shadows abertos e force-close

- Nova migration aplicada em producao: `128_shadow_force_close`.
- Config ativa:
  - `shadow_max_open_age_hours=18`
  - `shadow_force_close_policy=TIMEOUT_LAST_KNOWN_PRICE`
- Monitor corrigido:
  - batch regular ordena por `created_at ASC, id ASC`, nao por UUID puro.
  - force-close usa idade desde `created_at`.
  - marcador persiste em `exit_metrics_json`, fora de `features_snapshot`.
- Antes do drain das fontes alvo:
  - L1_SPECTRUM: `1` aberto em `18-48h`, `23` em `<18h`
  - L3: `1` aberto em `18-48h`, `137` em `<18h`
  - L3_LAB: `0` PENDING/RUNNING
- Drain alvo executado pelo caminho do monitor (`_advance_shadow` + sim/capture pos-commit):
  - L1 `7704ee7a-fcff-40b7-876d-18690d9b5041`, `RAIN_USDT`, `TIMEOUT`, force age `23.4778h`
  - L3 `8563eeb8-e7e7-448d-a94a-7b08549e1f17`, `RAIN_USDT`, `TIMEOUT`, force age `21.9440h`
- Depois do drain:
  - L1_SPECTRUM: `24` abertos, todos `<18h`
  - L3: `139` abertos, todos `<18h`
  - L3_LAB: `0` PENDING/RUNNING
- Base rate 30d antes de qualquer novo retreino:
  - L1_SPECTRUM: TP `1596`, SL `1827`, TIMEOUT `29`, total `3452`, TP rate `46.23%`
  - L3: TP `6809`, SL `9793`, TIMEOUT `235`, total `16837`, TP rate `40.44%`
  - L3_LAB: TP `2213`, SL `2629`, TIMEOUT `64`, total `4906`, TP rate `45.11%`

## H4 - Estabilidade de features Junho x Julho

- Analise read-only sobre L1 pos-frontier:
  - Junho pos-frontier: `1515` registros
  - Julho: `702` registros no dataframe avaliado
  - Features avaliadas: `33`
- Config gravada apenas como proposta:
  - `ml_feature_exclusion_candidates_proposed=[]`
  - `ml_feature_stability_reviewed_at=now()`
- Nenhuma feature atingiu criterio de exclusao automatica.
- Features em observacao por delta de AUC:
  - `trend_alignment`: AUC junho `0.5351`, julho `0.4237`, delta `0.1114`
  - `bb_width`: AUC junho `0.6628`, julho `0.5597`, delta `0.1031`
  - `ema50_gt_ema200`: AUC junho `0.5277`, julho `0.4320`, delta `0.0957`

## Verificacoes

- `python -m pytest backend\tests\test_ml_dataset_config.py backend\tests\test_ml_directional_features.py -q`: `10 passed`
- `python -m py_compile backend\app\ml\dataset_config.py backend\app\services\ml_challenger_service.py backend\app\tasks\shadow_trade_monitor.py ml_trainer\job.py backend\alembic\versions\128_shadow_force_close_policy.py`: OK
- `python -m py_compile backend\app\tasks\shadow_trade_monitor.py`: OK apos ajuste do marker persistente.

# T0A — Baseline pré-correção do dataset ML

Status: CONCLUÍDA

## Identidade do universo

- `audit_run_id`: `2fc54bb5-7a6e-4bb6-8c40-d213899516f5` [query]
- `query_cutoff UTC`: `2026-07-12T04:26:11.753960Z` [query]
- `git_commit`: `feb8ca156f7fc9a5d9392b1ad84d3dba2847dd49` [comando: `git rev-parse HEAD`]
- `alembic_revision`: `132_calibration_orchestration_v2` [query]
- `config_profiles SHA-256`: `4d95aab39122a9fc1c2a9fc574e05147c7d6ce98c3d1ae81b9716bab36dd4f98` [calc: SHA-256 do JSON canônico de todas as linhas de `config_profiles`, ordenadas por `config_type,id`]

Todas as consultas de `shadow_trades` foram executadas na mesma transação `REPEATABLE READ`, com `created_at <= :query_cutoff`, encerrada por `ROLLBACK` [query].

## Configuração ML literal usada no baseline

```json
{
  "ml_dataset_valid_from": "2026-07-01T00:00:00+00:00",
  "ml_l3_dataset_valid_from": "2026-07-11T03:21:06+00:00",
  "ml_forward_scoring_enabled": false,
  "shadow_capture_l1_enabled": true,
  "shadow_capture_l3_simulated_enabled": true,
  "shadow_skip_log_enabled": true
}
```

Fonte: `[config: ml]`, linha ativa `id=4e445c54-3a00-4478-98c5-3336ee6fb425`.

```json
{
  "auto_retrain_enabled": false,
  "ml_enabled": true
}
```

Fonte: `[config: ai-settings]`, linha ativa `id=45fa69ec-ab41-400b-98fc-15ffe47453e4`.

```json
{
  "enable_anthropic_explanations": false,
  "enable_association_rules": true,
  "enable_catboost": true,
  "enable_dynamic_combinations": true,
  "enable_lightgbm": true,
  "enable_optuna": false
}
```

Fonte: `[config: profile_intelligence]`, linha ativa `id=7ad7c8cc-207a-4853-a140-5ddffb33c9a1`.

## Contagens por source e outcome

| source | NULL | SL_HIT | TIMEOUT | TP_HIT |
|---|---:|---:|---:|---:|
| L1_SPECTRUM | 11 [query] | 2.583 [query] | 46 [query] | 1.975 [query] |
| L3 | 55 [query] | 11.276 [query] | 301 [query] | 7.951 [query] |
| L3_LAB | 724 [query] | 3.323 [query] | 102 [query] | 3.225 [query] |
| L3_REJECTED | 617 [query] | 45.249 [query] | 788 [query] | 29.970 [query] |
| L3_SIMULATED | 8 [query] | 1.364 [query] | 36 [query] | 875 [query] |

## Dimensões do universo

- `raw_n=110.479` [query]
- `effective_n=38.517` [query: `COUNT(DISTINCT sha256(features_snapshot::text))`; contrato legado pré-T0B]
- `positive_count=43.996` [query: `outcome IN ('TP_HIT','WIN')`]
- `negative_count=63.795` [query: `outcome IN ('SL_HIT','LOSS')`]
- `distinct_profiles=31` [query]
- `distinct_symbols=60` [query]

Cross-check: `43.996 + 63.795 + 1.260 TIMEOUT + 1.428 NULL = 110.479` [calc: soma das contagens literais por outcome].

## Distribuição do contrato de barreira

| barrier_mode | tp_pct_applied | timeout_candles | ttt_timeout_minutes | N |
|---|---:|---:|---:|---:|
| ATR_DYNAMIC | 1,5 | 1.440 | 180 | 93.320 [query] |
| FIXED | 0,6 | 1.440 | 180 | 1.876 [query] |
| FIXED | 0,8 | 1.440 | 180 | 5.592 [query] |
| FIXED | 1,0 | 1.440 | 180 | 8.820 [query] |
| FIXED | 1,5 | 1.440 | 180 | 730 [query] |
| NULL | NULL | 1.440 | 180 | 141 [query] |

Cross-check: soma dos contratos `=110.479` [calc].

## Distribuição de lineage

| lineage_status | eligible_for_training | N |
|---|---|---:|
| EXACT | true | 3.184 [query] |
| INVALID_FEATURES | false | 248 [query] |
| NULL | false | 106.995 [query] |
| VERSION_IDS_UNRESOLVED | false | 52 [query] |

Cross-check: soma do lineage `=110.479` [calc].

## Critério de aceite

- Mesmo cutoff em todas as queries: ATENDIDO.
- Comparações futuras vinculadas ao mesmo universo: ATENDIDO pelo `audit_run_id` e `query_cutoff UTC` congelados.
- `dataset_hash`, `split_hash` e `feature_schema_version` em D0–D8: CONTRATO REGISTRADO; validação operacional ocorrerá quando os treinos diagnósticos forem executados.
- Escrita no banco/config: NÃO OCORREU.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| raw_n=110.479 | [query] | `COUNT(*) = 110479` |
| effective_n=38.517 | [query] | `COUNT(DISTINCT sha256(features_snapshot::text)) = 38517` |
| positive_count=43.996 | [query] | `outcome IN ('TP_HIT','WIN') = 43996` |
| negative_count=63.795 | [query] | `outcome IN ('SL_HIT','LOSS') = 63795` |
| distinct_profiles=31 | [query] | `COUNT(DISTINCT profile_id) = 31` |
| distinct_symbols=60 | [query] | `COUNT(DISTINCT symbol) = 60` |
| lineage NULL=106.995 | [query] | `lineage_status IS NULL AND eligible_for_training=false = 106995` |
| config hash | [calc] | `sha256(canonical config_profiles JSON)=4d95aab39122a9fc1c2a9fc574e05147c7d6ce98c3d1ae81b9716bab36dd4f98` |


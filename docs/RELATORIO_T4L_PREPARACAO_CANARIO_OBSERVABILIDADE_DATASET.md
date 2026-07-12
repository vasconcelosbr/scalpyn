# T4L — preparação do canário, observabilidade e dataset

## Resumo executivo
Canário read-only, health token-gated, contrato oficial fail-closed, lineage, lanes, guards de registry, metodologia e rollback foram preparados. A decisão desta fase é `READY_FOR_T4F_CANARY_EXECUTION`; isso não significa deploy, dataset pronto ou modelo aprovado.

## Contratos
- amostra operacional máxima: 50 registros novos após `native_capture_start_at`;
- hash canônico SHA-256 sobre JSON ordenado, Unicode preservado e não-finitos rejeitados;
- lineage por `L1_SPECTRUM`, `L3`, `L3_REJECTED`, `L3_LAB`, `L3_SIMULATED`;
- identidade ativa sem profile: user/symbol/source; com profile: profile/symbol/source;
- dataset retorna zero sem fronteira oficial;
- XGBoost=L1; LightGBM/CatBoost=L3.

## Observabilidade
Endpoint interno: `GET /api/system/internal/ml/native-capture/health`, protegido por `DIAGNOSTICS_BEARER_TOKEN`. Métricas incluem total, válidos, inválidos, hash, lineage, legado e última captura. Alertas preparados: taxa zero, validade abaixo de 99,5%, hash/lineage/futuro/legado maiores que zero; não ativados nesta fase.

## Comandos read-only
`python -m scripts.audit_native_capture_canary --dry-run`; `audit_official_ml_dataset`; `audit_ml_lane_coverage`; `audit_model_approval_readiness`. Todos abrem transação read-only e encerram com rollback.

## Histórico e aprovação
Os 83.982 registros do período informado permanecem fora do dataset oficial: 0 elegíveis comprovados, 80.498 research-only e 3.484 inválidos [prompt]. Treino e aprovação permanecem bloqueados.

## Testes
Testes cobrem fail-closed, hash, não-finitos, lineage, lanes e registry guards. Evidência final deve ser anexada pelo commit de execução.

## Ledger
| número | origem | valor literal |
|---|---|---|
| limite canário | [prompt] | 50 |
| histórico total | [prompt] | 83.982 |
| histórico oficial comprovado | [prompt] | 0 |

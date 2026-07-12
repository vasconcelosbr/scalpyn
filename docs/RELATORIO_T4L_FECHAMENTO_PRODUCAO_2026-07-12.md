# T4L — Fechamento de produção

## Resultado

```text
T4L_PRODUCTION_READY_COLLECTION_IN_PROGRESS
```

O contrato de captura nativa point-in-time está implantado, observável e
fail-closed em produção. A coleta oficial começou em
`2026-07-12T18:21:57Z` [config: Railway]. Nenhum histórico anterior foi
promovido e nenhuma aprovação de modelo foi realizada.

## Evidências

- commit implantado: `ec4ebf5` [git];
- revisão do banco: `133_native_feature_capture` [query];
- colunas confirmadas: `capture_contract_version` e
  `feature_extractor_version` [query];
- trigger imutável confirmado: `trg_shadow_native_capture_immutable`, estado
  `O` [query];
- API, beat e quatro workers backend: `SUCCESS` e ativos [deploy];
- `/api/health`: `status=ok` [HTTP];
- endpoint protegido de captura:
  `NATIVE_CAPTURE_COLLECTION_IN_PROGRESS` [HTTP];
- capturas posteriores ao marco: `0` [query];
- registros legados no dataset oficial: `0` [query].

## Incidente resolvido

O primeiro snapshot Railway omitiu arquivos Python novos e o boot encontrou
`Can't locate revision identified by '133_native_feature_capture'`. O banco
permaneceu íntegro. Todos os runtimes foram republicados com contexto completo
(`--no-gitignore`). Os seis deployments finais foram inspecionados e não
apresentaram nenhuma das assinaturas críticas auditadas de Alembic, import ou
schema.

## Estado do canário

O mecanismo está pronto e coletando passivamente. A ausência inicial de amostra
é tratada como coleta em andamento, não como aprovação. Treino, promoção e ML
Gate permanecem bloqueados até existir amostra nativa suficiente e validada.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | QUERY/COMANDO | VALOR LITERAL |
|---|---|---|---|
| revisão de produção=133 | [query] | `select version_num from alembic_version` | `133_native_feature_capture` |
| colunas nativas confirmadas=2 | [query] | `information_schema.columns` | `capture_contract_version`; `feature_extractor_version` |
| trigger nativo confirmado=1 | [query] | `pg_trigger` | `trg_shadow_native_capture_immutable`, `O` |
| testes direcionados aprovados=39 | [test] | `pytest` captura/governança/calibração/EV | `39 passed` |
| runtimes backend finais saudáveis=6 | [deploy] | `railway service status --all --json` | seis serviços com `SUCCESS`, `stopped=false` |
| correspondências críticas nos logs finais=0 | [deploy] | busca nas últimas 200 linhas de cada deployment | `critical_matches: 0` em seis deployments |
| capturas nativas após o marco=0 | [query] | auditor canário read-only | `total_native: 0` |
| legado no dataset oficial=0 | [query] | auditor canário read-only | `legacy_rows_in_official_dataset: 0` |
| promoções de modelo=0 | [operação] | escopo executado | nenhuma promoção executada |

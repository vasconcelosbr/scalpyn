# Relatório de resolução das pendências de aceite — MTF Spot

Emitido em `[query] 2026-09-05T17:02:14.430202-03:00`, com produção verificada após o commit `[git] f5bcebf22f88bf3c4eb76481b54ecba8cd69c49b`.

## Veredito

- **Backend técnico: ACEITO.** Código, migrações, API, filas, coletores e produtores estão implantados e foram verificados em produção.
- **Aceite técnico consolidado: PARCIAL.** O frontend está publicado e responde, mas a prova visual da sessão autenticada ficou **NÃO CONFIRMADA** porque o controlador de navegador não iniciou no host.
- **Aceite observacional: CONFIG_REQUIRED / DRAFT.** O servidor contém `[query] 0` políticas MTF aprovadas e `[query] 0` execuções de calibração. Os profiles permanecem DRAFT e nenhum threshold foi inventado.
- **Efeito operacional: DESABILITADO.** A configuração persistida retornou `[query] enabled=false`; não houve ativação SHADOW e as ordens reais continuam no caminho legado.

## Escopo implementado

Foram concluídos:

- contratos versionados para L1/1h, L2/15min e confirmação L3/5min, com validação de identidade temporal, integridade, hash, candle fechado, fonte, expiração e warmup;
- direção L1 neutra em empates, inclinação de EMAs, estrutura confirmada e DMI completo;
- máquina de estados L2 persistida e determinística por símbolo, versão de profile e candle fechado;
- confirmação L3 que falha fechado quando a identidade temporal ou a fonte não são comprováveis;
- isolamento observacional: falhas MTF persistem `WAIT` sem interromper o caminho legado;
- dataset point-in-time, folds cronológicos, embargo, holdout, comparação com baseline, manifesto e hashes reproduzíveis;
- governança de política e execução de calibração no servidor, impedindo que um JSON cliente se autodeclare aprovado;
- importação MTF limitada a DRAFT/SHADOW, com SHADOW condicionado a execução `PASSED` vinculada no servidor;
- auditoria de runtime e interface para profiles, hashes, cobertura e motivos de bloqueio;
- consulta de cobertura limitada por identidade, índice aditivo e auditoria de produção read-only.

## Defeitos encontrados durante a prova e corrigidos

1. O upsert OHLCV não correspondia à identidade única real de produção. Foi alinhado a `(time, symbol, exchange, timeframe)`.
2. O worker isolado `research_ohlcv` não importava o módulo dos novos coletores. O registro foi acrescentado e coberto por teste.
3. O coletor pedia exatamente o warmup, mas a resposta incluía o candle aberto. O limite passou de `[config] 201` necessários para `[calc: 201 + 1] 202` solicitados, preservando `[query] 201` fechados por símbolo.
4. A deduplicação podia suprimir um cálculo e ainda registrar `compute_enqueued=true`. O resultado agora persiste `compute_task_id` e só declara enfileiramento quando o ID existe.

Commits desta etapa, em ordem, `[git] 6`:

- `8759e50` — fechamento das lacunas de aceite;
- `a40a151` — identidade do upsert OHLCV;
- `cf4946d` — isolamento dos coletores na fila de pesquisa;
- `5721751` — registro dos coletores no worker isolado;
- `9c7270f` — headroom do candle aberto;
- `f5bcebf` — verdade operacional da deduplicação.

## Evidência de produção

### Coletores e produtores

O canário final observou:

- L1/1h: `[query] target_symbols=65`, `[query] successful_symbols=65`, `[query] failed_symbols=0`, `[query] closed_rows_submitted=13065`, `[query] required_warmup_candles=201`, `[query] fetch_limit=202`, com `compute_task_id` real;
- L2/15min: `[query] target_symbols=65`, `[query] successful_symbols=65`, `[query] failed_symbols=0`, `[query] closed_rows_submitted=13065`, `[query] required_warmup_candles=201`, `[query] fetch_limit=202`;
- produtor 1h: `[query] computed=65`, `[query] skipped=0`, `producer_version=mtf_indicator_producer_v1`;
- produtor 15min: `[query] computed=65`, `[query] skipped=0`, `producer_version=mtf_indicator_producer_v1`;
- hash da configuração usado por ambos: `[query] 6f8e413697fbd48c833e04cf71c2afc255118d04aef20d4fb5a49de0e4528418`;
- espera observada no repasse 1h para o compute: `[query] queue_wait_s=0.01`.

A execução 15min também provou a semântica corrigida de retry: enquanto existia um lock órfão de um deploy anterior, retornou `[query] compute_task_id=null` e `[query] compute_enqueued=false`, sem alegar sucesso inexistente. Após a coleta completa, o cálculo canário foi executado e produziu todos os símbolos.

### Banco, cobertura e integridade

- Migração ativa: `[query] 217_indicator_identity_idx`.
- Índice de identidade: `[query] ix_indicators_identity_latest` presente.
- Custo total estimado da consulta de cobertura: `[query] 15228342.96` antes e `[query] 1892.74` depois.
- Snapshots governados no corte final: `[query] 65` em 15min e `[query] 130` em 1h.
- Símbolos cobertos: `[query] 65` em cada timeframe produzido.
- Candles abertos na janela auditada: `[query] 0` em 5min, `[query] 0` em 15min e `[query] 0` em 1h.
- `ingested_at` ausente: `[query] 0` nos três timeframes.
- Proveniência histórica ausente na janela auditada: `[query] 5525` linhas em 15min, `[query] 0` em 1h e `[query] 0` em 5min. As linhas 15min sem o contrato v2 ficam excluídas de uma futura calibração point-in-time; não serão tratadas como historicamente disponíveis.
- Nenhum `DiskFullError` ou `No space left on device` foi localizado nos logs dos deployments finais.

As latências brutas observadas ainda não autorizam margem de validade: `[query] mediana=1679.863015s, p95=1894.611329s` em 15min e `[query] mediana=6086.120765s, p95=7034.422803s` em 1h. Esses valores misturam duração do candle e disponibilidade e precisam de uma população observacional própria antes de virar configuração.

### Deployments finais

Todos retornaram `[query] SUCCESS`:

| Superfície | Deployment |
|---|---|
| API Railway | `59e3baf8-5db6-474d-8a72-2f3eedfd570c` |
| Scheduler/beat | `d5129b7d-bd4d-4e65-a9b8-c59c4fa12b10` |
| Worker compute | `13f3243d-620f-4611-a7d3-411f19364668` |
| Worker structural | `2561201c-5bce-4605-ab91-ddbd86565a8e` |
| Worker research OHLCV | `340d38b4-fdb3-428e-a0d7-e5cc7f923ba4` |

A API retornou `[query] HTTP 200` em `/api/health`, executou `alembic upgrade head` com sucesso e publicou as rotas de proposta/aprovação, calibração, auditoria, ativação e desativação MTF.

O frontend Vercel está `[query] READY` no deployment `dpl_DDeFA7WfoGAgDMZ74fJREpFar5QH`, com `/settings/strategies` retornando `[query] HTTP 200`. A árvore rastreada de `frontend/` no commit implantado e no commit final é idêntica: `[git] e903edb7c6b984a8f576ccb0fc1eb1c638487d5c`.

## Regressão

- Backend completo anterior aos hotfixes operacionais: `[query] 2368 passed`, `[query] 5 skipped`.
- Testes HTTP com serviços locais isolados: `[query] 13 passed`.
- Regressão final focal de MTF, Celery, roteamento e OHLCV após todos os hotfixes: `[query] 83 passed`.
- Frontend: `[query] 83 passed`; lint com `[query] exit code 0`; build com `[query] 44` rotas.
- Auditoria npm de produção: `[query] 0` vulnerabilidades.
- Migrações em banco limpo: upgrade, downgrade e novo upgrade concluídos; head final `[query] 217_indicator_identity_idx`.
- Grafo do projeto atualizado após as alterações.

## Matriz requisito × evidência

| Requisito | Estado | Evidência |
|---|---|---|
| Worktree limpo e checkout do usuário preservado | PASS | `[git] HEAD=origin/main=f5bcebf`; preflight de fonte `PASS` |
| Contratos L1/L2/L3 e compatibilidade anterior | PASS | testes de contratos e regressão focal `[query] 83 passed` |
| L2 persistente, replay/retry/restart | PASS técnico | implementação transacional e testes determinísticos; execução observacional aguarda policy |
| Provider sem fallback temporal | PASS | resolução por símbolo/mercado/timeframe/grupo e testes fail-closed |
| Coletores 1h/15min encadeados | PASS | canários de coleta e compute com `[query] 65/65` símbolos |
| Warmup, finitude e dependências transitivas | PASS | produtor final `[query] skipped=0` nos dois timeframes |
| Isolamento do legado | PASS | runtime `[query] enabled=false`; `operational_effect` não habilitado |
| Dataset/calibrador completo | PASS técnico | serviço point-in-time, manifesto, hashes, folds e holdout implementados |
| Política estatística aprovada | PENDENTE | servidor `[query] policies=0`; proposta permanece `PENDING_HUMAN_APPROVAL` |
| Calibração aprovada | PENDENTE | servidor `[query] runs=0`; profiles permanecem DRAFT |
| Infraestrutura PostgreSQL/filas | PASS com observação | índice aplicado, consulta limitada e producers concluídos; fila de pesquisa deve continuar monitorada na janela observacional |
| Backend em produção | PASS | deployments Railway `SUCCESS`, schema e health verificados |
| Frontend publicado | PASS | Vercel `READY`, rota publicada e árvore fonte equivalente |
| Interface autenticada | NÃO CONFIRMADO | controlador de navegador falhou ao carregar os assets do kernel no host; HTTP/bundle não substituíram prova visual |
| SHADOW observacional | NÃO INICIADO | bloqueado corretamente por ausência de policy e run `PASSED` |
| Ordens reais inalteradas | PASS | MTF desabilitado; promoção operacional fora do escopo |

## Artefatos entregues

- `artifacts/mtf/mtf-calibration-policy-proposal.json` — proposta CONFIG_REQUIRED, sem parâmetros inventados;
- `artifacts/mtf/profile-mtf-l1-1h-draft.json` — profile L1 DRAFT;
- `artifacts/mtf/profile-mtf-l2-15m-draft.json` — profile L2 DRAFT;
- `artifacts/mtf/profiles-mtf-l1-1h-l2-15m-draft.json` — pacote conjunto DRAFT;
- `artifacts/mtf/acceptance-20260905/deploy-ledger.jsonl` — registro do deploy backend;
- este relatório.

## Pendências legítimas para aceite observacional

1. Acumular snapshots L1/L2 point-in-time sob `spot_mtf_closed_ohlcv_v2` e medir a disponibilidade real sem usar linhas recuperadas posteriormente.
2. Executar o piloto de poder/estabilidade para propor mínimo amostral, folds, embargo, holdout, orçamento, confiança, margem de validade e janela de observação. Enquanto não houver evidência, esses campos permanecem `null`.
3. Submeter a política preenchida ao endpoint autenticado e obter aprovação humana explícita.
4. Executar a calibração; somente um run persistido `PASSED` pode emitir profiles SHADOW.
5. Repetir a validação visual com sessão autenticada quando o controlador de navegador estiver disponível.
6. Só então importar os profiles aprovados, executar a prévia e habilitar `operational_effect:false`.

## Rollback

1. Acionar o endpoint autenticado de desativação MTF e confirmar `enabled=false`. Esse já é o estado atual.
2. Se houver interferência no legado, restaurar o código base `[git] 2b424a472b222142007fd99a0cfb0f5097426905` nos serviços afetados. Não apagar snapshots, decisões, manifests ou runs.
3. Para o frontend, promover novamente `frontend-ozddgiusx-ricardovasconcelos-1177s-projects.vercel.app`, correspondente ao código base.
4. Revalidar health, schema, workers, scheduler e caminho legado antes de encerrar o incidente.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| commit final | `[git]` | `f5bcebf22f88bf3c4eb76481b54ecba8cd69c49b` |
| testes backend finais focais | `[query] pytest` | `83 passed in 4.41s` |
| testes backend completos | `[query] pytest` | `2368 passed, 5 skipped, 4 warnings` |
| testes HTTP | `[query] pytest` | `13 passed` |
| testes frontend | `[query] npm test` | `83 pass` |
| build frontend | `[query] next build` | `44 routes` |
| vulnerabilidades npm produção | `[query] npm audit --omit=dev` | `total: 0` |
| símbolos L1 produzidos | `[query] logs Railway` | `computed: 65; skipped: 0` |
| símbolos L2 produzidos | `[query] logs Railway` | `computed: 65; skipped: 0` |
| warmup/fetch | `[config] + [calc]` | `required_warmup_candles: 201; fetch_limit: 202 = 201 + 1` |
| linhas fechadas submetidas por timeframe | `[query] logs Railway` | `closed_rows_submitted: 13065` |
| políticas/runs MTF | `[query] PostgreSQL` | `mtf_policy_count: 0; mtf_run_count: 0` |
| snapshots governados | `[query] PostgreSQL` | `15m: 65; 1h: 130` |
| integridade OHLCV | `[query] PostgreSQL` | `open_rows: 0; missing_ingested_at: 0` nos três timeframes |
| proveniência ausente | `[query] PostgreSQL` | `15m: 5525; 1h: 0; 5m: 0` |
| plano de cobertura | `[query] EXPLAIN` | `Total Cost: 1892.74; Plan Rows: 186` |
| plano anterior de cobertura | `[query] EXPLAIN` | `Total Cost: 15228342.96` |
| health API | `[query] HTTPS` | `status_code: 200; body: {"status":"ok","version":"0.2.0"}` |
| deployment Vercel | `[query] vercel inspect` | `status: Ready; id: dpl_DDeFA7WfoGAgDMZ74fJREpFar5QH` |
| rota frontend | `[query] vercel curl` | `HTTP/1.1 200 OK` |


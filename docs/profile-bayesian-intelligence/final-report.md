# Relatório final — Profile Bayesian Intelligence

## Resultado

O módulo Profile Bayesian Intelligence foi implementado como uma vertical isolada, aditiva e fail-closed. Ele fornece:

- construção de snapshots imutáveis e point-in-time;
- análise Bayesiana hierárquica com posterior, shrinkage e diagnósticos;
- classificação de evidência por indicador;
- estudos de otimização com restrições e penalidades de robustez;
- criação de candidatos somente pelo workflow shadow já existente;
- APIs autenticadas e user-scoped;
- filas e imagem de worker dedicadas;
- interface integrada em `/profile-intelligence`;
- trilha de auditoria e documentação operacional.

Na fase de implementação, nenhuma migração foi aplicada a banco, nenhum flag
foi habilitado, nenhum perfil foi alterado e nenhum candidato foi criado. O
rollout de produção é executado separadamente, mantendo todas as flags false.

## Arquivos criados

### Banco e runtime

- `backend/alembic/versions/137_profile_bayesian.py`
- `backend/requirements-profile-bayesian.txt`
- `backend/Dockerfile.profile-bayesian`
- `backend/start-profile-bayesian-worker.sh`

### Backend

- `backend/app/api/profile_bayesian_intelligence.py`
- `backend/app/tasks/profile_bayesian_intelligence.py`
- `backend/app/profile_bayesian/__init__.py`
- `backend/app/profile_bayesian/analysis_service.py`
- `backend/app/profile_bayesian/audit.py`
- `backend/app/profile_bayesian/candidate_adapter.py`
- `backend/app/profile_bayesian/config.py`
- `backend/app/profile_bayesian/data_contract.py`
- `backend/app/profile_bayesian/dataset_builder.py`
- `backend/app/profile_bayesian/diagnostics.py`
- `backend/app/profile_bayesian/evidence_grading.py`
- `backend/app/profile_bayesian/hierarchical_model.py`
- `backend/app/profile_bayesian/metrics.py`
- `backend/app/profile_bayesian/posterior_analyzer.py`
- `backend/app/profile_bayesian/result_serializer.py`
- `backend/app/profile_bayesian/schemas.py`
- `backend/app/profile_bayesian/optimization/__init__.py`
- `backend/app/profile_bayesian/optimization/candidate_ranker.py`
- `backend/app/profile_bayesian/optimization/constraints.py`
- `backend/app/profile_bayesian/optimization/objective.py`
- `backend/app/profile_bayesian/optimization/optuna_runner.py`
- `backend/app/profile_bayesian/optimization/robustness_penalties.py`
- `backend/app/profile_bayesian/optimization/search_space.py`
- `backend/app/profile_bayesian/validation/__init__.py`
- `backend/app/profile_bayesian/validation/concentration_checks.py`
- `backend/app/profile_bayesian/validation/overfit_checks.py`
- `backend/app/profile_bayesian/validation/profile_replay_adapter.py`
- `backend/app/profile_bayesian/validation/temporal_split.py`
- `backend/app/profile_bayesian/validation/walk_forward.py`

### Frontend e testes

- `frontend/components/profile-intelligence/BayesianIntelligencePanel.tsx`
- `backend/tests/test_profile_bayesian_intelligence.py`

### Documentação

- `docs/profile-bayesian-intelligence/architecture.md`
- `docs/profile-bayesian-intelligence/data-contract.md`
- `docs/profile-bayesian-intelligence/statistical-model.md`
- `docs/profile-bayesian-intelligence/optimization.md`
- `docs/profile-bayesian-intelligence/operations.md`
- `docs/profile-bayesian-intelligence/rollback.md`
- `docs/profile-bayesian-intelligence/non-interference.md`
- `docs/profile-bayesian-intelligence/non-interference-hashes.json`
- `docs/profile-bayesian-intelligence/final-report.md`

## Arquivos modificados

| Arquivo | Motivo | Impacto | Risco |
|---|---|---|---|
| `backend/app/main.py` | Registrar o novo router | Expõe somente as novas rotas autenticadas | Baixo; registro aditivo |
| `backend/app/tasks/celery_app.py` | Registrar tarefas, filas e rotas dedicadas | Isola análise e otimização do tráfego operacional | Médio; mitigado por roteamento explícito e testes |
| `frontend/app/profile-intelligence/page.tsx` | Adicionar a aba Bayesian Intelligence | Torna o novo painel acessível na rota existente | Baixo; fluxo adjacente preservado |

O arquivo não rastreado `docs/audits/auditoria-captura-features-l3-2026-07-24.md` já existia no checkout e não foi alterado.

## Migração

A revisão `137_profile_bayesian`, dependente de `136_l1_lane_contract_v2`, cria nove tabelas `[query]`:

- `profile_bayesian_dataset_snapshots`
- `profile_bayesian_analysis_runs`
- `profile_bayesian_indicator_effects`
- `profile_bayesian_diagnostics`
- `profile_optimization_studies`
- `profile_optimization_trials`
- `profile_optimization_trial_metrics`
- `profile_bayesian_candidate_links`
- `profile_bayesian_audit_events`

As tabelas possuem chaves estrangeiras, unicidade de snapshot por identidade do
dataset, índices para perfil/usuário/status/tempo e downgrade reverso em ordem
segura. O SQL offline foi renderizado com exit code `0` `[query]`. A revisão foi
aplicada em PostgreSQL/TimescaleDB descartável, revertida e reaplicada; o
downgrade deixou `0` tabelas do módulo `[query]` e o re-upgrade terminou em
`137_profile_bayesian` `[query]`.

## Contratos e integrações reutilizados

- autenticação e escopo de usuário do backend existente;
- `config_profiles` como fonte de configuração;
- trades/decisões persistidos como fonte point-in-time;
- workflow existente `ProfileIntelligenceAutopilotService.create_candidate_from_calibration_proposal`;
- entidades e política de candidatos shadow da Calibration Evolution;
- Redis/Celery existentes, com filas exclusivas;
- padrões atuais da página Profile Intelligence.

O adaptador não chama caminhos de treino, promoção, carregamento de modelo ou execução de ordens.

## Segurança e fail-closed

- Flags globais e por operação ficam desligadas por padrão.
- Falta de dependências probabilísticas, dados, permissões, política, diagnósticos ou replay válido interrompe o fluxo.
- A análise registra dataset, versão do contrato, configuração, seed e artefatos.
- A otimização não aceita métricas ausentes, não fabrica replay e não cria candidato sem estudo persistido e aprovado.
- O endpoint geral de replay encontrado no repositório é um stub. O adaptador retorna `existing_profile_replay_engine_is_stub`; portanto, estudo e candidato permanecem bloqueados até existir uma integração de replay real.
- Criação de candidato delega ao workflow shadow existente; não altera o perfil ativo.
- O worker dedicado não inicia API, beat, trading workers nem migrações.

## Verificações executadas

| Verificação | Evidência literal | Resultado |
|---|---|---|
| Testes do módulo, roteamento e contrato L1 ativo | `26 passed` `[query]` | Aprovado |
| Testes do frontend | `pass 23` `[query]` | Aprovado |
| TypeScript | comando `npx tsc --noEmit`, exit code `0` `[query]` | Aprovado |
| Build Next.js | comando `npm run build`, exit code `0` `[query]` | Aprovado |
| Compilação Python | comando `python -m compileall`, exit code `0` `[query]` | Aprovado |
| Cabeça Alembic | `137_profile_bayesian (head)` `[query]` | Aprovado |
| SQL offline da revisão | exit code `0` `[query]` | Aprovado |
| Migração online descartável | `TABLE_COUNT=9` `[query]` | Aprovado |
| Downgrade descartável | `TABLES_AFTER_DOWNGRADE=0` `[query]` | Aprovado |
| Re-upgrade descartável | `REVISION_AFTER_REUPGRADE=137_profile_bayesian` `[query]` | Aprovado |
| Imagem científica | PyMC `5.16.2`, ArviZ `0.18.0`, PyTensor `2.25.4` `[query]` | Aprovado |
| Boot do worker | processo `ready` consumindo somente `profile_bayesian` e `profile_optimization` `[query]` | Aprovado |
| Imports/rotas sem PyMC no processo da API | script de importação, exit code `0` `[query]` | Aprovado |
| Hashes dos módulos protegidos | `HASH_MISMATCH_COUNT=0` `[query]` | Aprovado |
| Diff dos módulos protegidos | `PROTECTED_DIFF_COUNT=0` `[query]` | Aprovado |
| Diff staged | `0` arquivos `[query]` | Nenhuma alteração staged |
| Testes legados ML ampliados | `91 passed, 12 failed` `[query]` | Baseline inconclusivo; ver pendências |
| Atualização do grafo | `Code graph updated` `[query]` | Aprovado |

## Pendências e bloqueadores

### Bloqueadores para ativação

- O replay canônico de perfil ainda não existe além do stub observado. Sem ele, métricas de otimização, walk-forward e robustez não podem ser validadas com evidência real.
- Flags, recursos do worker e política operacional precisam ser aprovados antes de qualquer análise real.

### Testes legados inconclusivos

Nos testes ML ampliados do baseline produtivo, doze falhas `[query]`
permaneceram em contratos legados de captura, barreira, mocks de persistência,
promotion gate e certificação.

Os caminhos protegidos apresentaram zero divergências de hash `[query]` e zero arquivos em diff `[query]`. Assim, essas falhas são registradas como pré-existentes/inconclusivas, não como prova de regressão causada pelo módulo.

### Não bloqueadores

- O build exibiu o aviso já existente do Recharts sobre dimensão negativa durante renderização estática.
- `ruff` não está instalado no ambiente; a checagem não foi executada.

## Próximos passos seguros

- implementar ou conectar um replay canônico read-only com contrato temporal explícito;
- habilitar somente a análise para um perfil controlado;
- validar posterior, convergência, cobertura e auditoria;
- só então considerar estudo, candidato shadow, replay e shadow run, cada etapa sob flag própria.

## Ledger de Evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| tabelas criadas=`9` | `[query]` inspeção da revisão Alembic | nove chamadas `op.create_table` |
| exit code do SQL offline=`0` | `[query]` Alembic | `0` |
| tabelas após upgrade descartável=`9` | `[query]` PostgreSQL | `TABLE_COUNT=9` |
| tabelas após downgrade descartável=`0` | `[query]` PostgreSQL | `TABLES_AFTER_DOWNGRADE=0` |
| testes backend selecionados aprovados=`26` | `[query]` pytest | `26 passed` |
| testes frontend aprovados=`23` | `[query]` npm test | `pass 23` |
| exit code TypeScript=`0` | `[query]` processo | `0` |
| exit code build=`0` | `[query]` processo | `0` |
| exit code compileall=`0` | `[query]` processo | `0` |
| divergências de hash=`0` | `[query]` comparação SHA-256 | `HASH_MISMATCH_COUNT=0` |
| arquivos protegidos em diff=`0` | `[query]` git diff | `PROTECTED_DIFF_COUNT=0` |
| arquivos staged=`0` | `[query]` git diff --cached | `0` |
| testes ML baseline aprovados=`91` | `[query]` pytest | `91 passed, 12 failed` |
| testes ML baseline falhos=`12` | `[query]` pytest | `91 passed, 12 failed` |

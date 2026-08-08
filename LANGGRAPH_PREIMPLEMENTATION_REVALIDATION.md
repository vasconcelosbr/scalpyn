# LangGraph Pre-Implementation Revalidation

Data local: `[environment] 2026-08-07 America/Sao_Paulo`  
Modo: `READ_ONLY_REVALIDATION / NO_PROVIDER_CALL / NO_PRODUCTION_MUTATION`  
Autoridade máxima planejada: `ANALYSIS_ONLY / PROPOSAL_ONLY / CANDIDATE_ONLY / SHADOW_ONLY`

## 1. Escopo congelado

Esta fase adicionará LangGraph como runtime opcional sobre `backend/app/ai_orchestration`. Ela não substituirá SQLAlchemy, FastAPI, Celery, os contratos sistêmicos, os módulos de domínio, os gates de ML ou a persistência canônica. Não há autorização implícita para live trading, Auto-Pilot, promoção de modelo, ordem real, alteração de TP/SL/sizing ou correção da configuração Spot.

## 2. Revisão fonte e ancestral implantado

- Worktree nova: `C:\Users\ricar\Documents\Codex\2026-08-08\scalpyn-langgraph-runtime`.
- Branch: `codex/langgraph-systemic-runtime-20260808`.
- HEAD/base: `[query] d53f475c5cc703e761258afbd2575be0fbdc6072`.
- Commit funcional ancestral implantado: `[query] 0d712e03a1b9399f49aeb62379e4682bf2cc2ade`.
- `origin/main`: `[query] 6c4020c958b9ab8c8e3edf5d5cffa2b8072f39d8`.
- A branch nova acompanha `origin/codex/systemic-ai-foundation-phase1-20260807`, não o checkout original dirty.

## 3. Checkout original preservado

O checkout original continua na branch `feat/l3-lab-barrier-v2`, HEAD `[query] fc53f543c952de4b0799514f76fe7cfc80e27406`, com alterações do usuário. Nenhum reset, checkout destrutivo, stage, commit ou edição será aplicado nele.

## 4. Higiene da worktree de implementação

A nova worktree foi criada limpa a partir do remoto da fundação. Após validar Vercel, o `.env.local` temporário criado pela CLI foi removido sem leitura de conteúdo; `git status --short` voltou vazio `[query]`. `.vercel` permanece ignorado e apenas vincula localmente o projeto correto.

## 5. Estado implantado antes da mudança

Railway produção, projeto `Scalpyn`, ambiente `production`:

- API `scalpyn`: deployment `[query] cc3504b6-321b-4436-8c25-80f59c41c983`, `SUCCESS`.
- worker compute: `[query] 0d5d8cd5-2212-499e-99fe-c37488b0baaa`, `SUCCESS`.
- worker structural: `[query] 43752f3e-526f-4ddd-86b4-674f6a9a0a53`, `SUCCESS`.
- worker execution: `[query] a8190d5a-cb5f-4468-9f7e-35ecb2da6ef5`, `SUCCESS`.
- Beat: `[query] b0ee107f-d29b-46d0-a3f0-66ccf7016eb1`, `SUCCESS`.
- API health: `[runtime] {"status":"ok","version":"0.2.0"}`.
- schema health: `[runtime] schema_ok=true; checked_count=32; missing=[]`.

Vercel projeto `scalpyn`:

- CLI: `[query] 58.7.1`.
- autenticação: `[query] ricardovasconcelos-1177`.
- `vercel env pull` de produção: `[query] sucesso; arquivo temporário com 4856 bytes; removido sem inspeção`.
- deployment operacional: `[query] dpl_8q7uNekZ2nZT8sq8YqYYJUZHt1Xi`, target `production`, `READY`.
- alias verificado: `https://scalpyn.vercel.app`.

## 6. Banco de produção antes da mudança

O probe usou conexão read-only e não imprimiu DSN/segredo.

- Alembic head: `[query] 147_systemic_ai_foundation`.
- PostgreSQL: `[query] 18.4 (Debian 18.4-1.pgdg13+1)`.
- tabelas sistêmicas existentes: `[query] 19`.
- prompts aprovados: `[query] 4`.
- linhas nas demais tabelas sistêmicas: `[query] 0` em cada tabela retornada.
- colunas bridge existentes: `[query] 16`.
- profiles: `[query] active=53; live_trading_enabled=0; auto_pilot_enabled=0; shadow_only=0`.

## 7. Dependências oficiais e política de pin

Versões solicitadas foram confirmadas no índice oficial PyPI:

- `langgraph==1.2.9` `[official index]`, Python `>=3.10`; a página informa release mais nova, mas a implementação manterá o pin exigido e auditável.
- `langgraph-checkpoint-postgres==3.1.0` `[official index]`, Python `>=3.10`.
- `psycopg[binary,pool]==3.3.4` `[official index]`; os extras `binary` e `pool` são publicados.

O checkpointer oficial exige `.setup()` inicial, `autocommit=True`, `row_factory=dict_row` quando a conexão é manual, e recomenda `LANGGRAPH_STRICT_MSGPACK=true`. Não serão adicionados `langchain`, `langsmith` ou provider SDKs novos sem necessidade comprovada.

## 8. Runtime local e compatibilidade

- Python local: `[query] 3.14.3`.
- Node local: `[query] v24.11.0`.
- npm local: `[query] 11.6.1`.
- O backend já usa FastAPI, SQLAlchemy async, Alembic, asyncpg, psycopg2 e Celery. LangGraph/Postgres saver será aditivo.
- A imagem Railway precisa ser validada com os pins e `pip check`; compatibilidade não será inferida apenas do ambiente Windows.

## 9. Fundação sistêmica existente

`AIOrchestrationService` já impõe tenant, resolução de provider/model, prompt aprovado, dataset canônico, bundle, budget, invariantes, lease, adapter e persistência. LangGraph chamará essa fachada; nodes não chamarão providers diretamente nem duplicarão decisões de domínio.

## 10. Entrypoints legados a migrar

Os quatro entrypoints atuais revalidados são:

1. Co-Pilot (`POST /api/copilot/chat`).
2. AI Critic (`app.tasks.profile_intelligence_job.feedback_loop`).
3. explicação de suggestion (`POST /api/profile-intelligence/suggestions/{suggestion_id}/explain`).
4. Shadow Detailed AI (`POST /api/shadow-trade-analysis/jobs`).

A adoção será protegida por `AI_ORCHESTRATION_RUNTIME=native|langgraph`, default `native`, e por flags independentes.

## 11. Chamadas diretas a provider

A busca atual ainda encontra chamadas de provider em adapters centrais e no serviço de gestão/validação de chaves. O critério final permitirá chamadas somente em `backend/app/ai_orchestration/provider_adapters`, no catálogo/validação explícita de credenciais e em fakes de teste. Nenhum node LangGraph terá SDK/HTTP de provider.

## 12. Provider/model real

O valor histórico `claude-fable-5` continua sem prova de existir no catálogo real do tenant e deverá falhar fechado. A consulta real do catálogo e qualquer canary pago dependem da chave tenant-scoped e de aprovação explícita de custo. Não haverá fallback silencioso; `configured_model` deverá ser igual ao `effective_model` ou a execução terminalizará com erro tipado.

## 13. Migração planejada

Se o head permanecer `147_systemic_ai_foundation`, será criada somente a revisão `148_langgraph_runtime`. Ela criará o schema dedicado `langgraph_runtime` e as tabelas canônicas `ai_graph_definitions`, `ai_graph_runs`, `ai_graph_interrupts`, `ai_graph_events` e `ai_graph_runtime_metadata`, com constraints, FKs e índices tenant-safe. Não haverá backfill de linhagem inventada.

## 14. Checkpointer planejado

- Produção/staging: `AsyncPostgresSaver` apenas fora de testes.
- Testes: saver controlado/in-memory; nenhuma dependência de banco externo.
- `python -m app.ai_orchestration.langgraph.bootstrap_checkpointer` será o único bootstrap autorizado.
- `.setup()` não rodará no request path nem no startup da API.
- `search_path` será fixado/fail-closed no schema dedicado.
- Estado serializado conterá somente IDs, hashes, enums e JSON seguro; nunca secrets, DSNs, ORM objects ou datasets brutos.

## 15. Segurança e autoridade

Todo graph run exigirá tenant derivado da autenticação, thread ID server-side, idempotência, limite de payload, event trail e autoridade máxima não-live. `LIVE_WRITE`, promoção de modelo, live trading, ordens e alterações reais de risco serão negados em código. Approve/reject/edit só poderá operar change sets candidate/shadow e será idempotente.

## 16. Flags, fila e serviço dedicado

As seis variáveis abaixo estão ausentes na API de produção antes da implementação `[query]`:

- `AI_ORCHESTRATION_RUNTIME`
- `LANGGRAPH_RUNTIME_ENABLED`
- `LANGGRAPH_ENTRYPOINTS_ENABLED`
- `LANGGRAPH_REGENERATIVE_SHADOW_ENABLED`
- `LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED`
- `LANGGRAPH_STRICT_MSGPACK`

O código usará defaults fail-closed; staging começará com flags LangGraph `false`. Será adicionada a fila `ai_orchestration` e, somente após staging, o serviço Railway `scalpyn-worker-ai-orchestration` com concorrência conservadora.

## 17. Baseline de testes

- suite da fundação: `[test] 27 passed in 1.88s`.
- coleção global: `[test] interrompida após 146 testes coletados em 2.98s`.
- causa literal: `backend/scripts/run_catboost_retrain.py` executa `_parse_args()` no import; o argparse recebe argumentos do pytest e lança `SystemExit: 2`.

Essa falha será corrigida cirurgicamente antes do gate global. Nenhum skip crítico será aceito.

## 18. Staging obrigatório

Será usado ambiente Railway isolado novo ou cópia isolada comprovadamente limpa, com banco sem dados de negócio. Ordem: prova de backup/restore, `148`, bootstrap do checkpointer, API, worker dedicado, frontend preview, fake canary, UI sintética, interrupt/resume e regenerative shadow. Real provider canary continuará bloqueado sem autorização de custo.

## 19. Produção, checkpoint e rollback

Antes de qualquer mutação em produção serão apresentados exatamente: mudanças, riscos, custos, testes, evidências de staging, rollback e ações propostas. Rollback incluirá desligar flags para `native`, reverter frontend/API/worker ao artifact anterior e downgrade `148 -> 147` somente se seguro e explicitamente aprovado. O checkpointer em produção exige backup e checkpoint humano.

## 20. Arquivos previstos e critérios de saída

Principais adições previstas:

- `backend/app/ai_orchestration/langgraph/` (state, reducers, registry, graphs, nodes, runtime, checkpoint, bootstrap e API service).
- `backend/alembic/versions/148_langgraph_runtime.py`.
- models/schemas/API `ai_graph_runs` e inclusão em `backend/app/main.py`.
- tasks e routing em `backend/app/tasks/celery_app.py`.
- frontend `Intelligence Runs` com timeline/interrupts.
- testes LangGraph, tenant, idempotência, interrupt/resume, recovery, migration e API.
- lock/SBOM, manifests, Mermaid e ledgers exigidos.

Critério de sucesso: runtime LangGraph comprovado em staging e, após checkpoint, produção com flags controladas, persistência canônica, worker terminal `SUCCESS`, API/rotas verificadas e zero expansão de autoridade. Sem prova, o veredito será um dos estados bloqueados definidos no roteiro, nunca um sucesso inferido.

## Ledger de evidências numéricas

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| fundação tests=27 | `[test]` | `27 passed in 1.88s` |
| collection before failure=146 | `[test]` | `146 tests collected in 2.98s` |
| schema checked=32 | `[runtime]` | `checked_count=32; missing=[]` |
| systemic tables=19 | `[query]` | lista retornada pelo probe read-only |
| approved prompts=4 | `[query]` | quatro chaves `@1.0.0` retornadas |
| bridge columns=16 | `[query]` | lista retornada pelo probe read-only |
| profiles active/live/autopilot/shadow=53/0/0/0 | `[query]` | `profile_invariants` literal |
| Vercel CLI=58.7.1 | `[query]` | `vercel --version` |
| Railway CLI=5.29.0 | `[query]` | `railway --version` |


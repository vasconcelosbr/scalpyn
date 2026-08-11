# Scalpyn Analysis Chat — Preimplementation Revalidation

Status: `REVALIDATED_BEFORE_CODE_CHANGE`

Captured at: `2026-08-11` `[system clock]`

## Source and workspace

- Clean implementation worktree: `C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn\.codex_tmp\analysis-chat-20260811` `[git status]`
- Branch: `codex/analysis-chat-staging` `[git status]`
- HEAD: `d317e9ed4dac38b7e3c7a75a44d474ab8173377b` `[git rev-parse HEAD]`
- Baseline commit subject: `feat: display intelligence run analysis results` `[git log -1]`
- The user's original checkout remains separate and was not modified by this preparation. `[worktree inspection]`

## Mandatory artifacts

Read completely from the current baseline or the prior clean Intelligence Runs audit worktree:

- `SCALPYN_INTELLIGENCE_RUNS_CORRECTION_REPORT.md`
- `SCALPYN_INTELLIGENCE_RUNS_CORRECTION_EVIDENCE_LEDGER.md`
- `scalpyn_intelligence_runs_correction_test_matrix.md`
- `scalpyn_intelligence_runs_staging_fake_result.json`
- `SCALPYN_INTELLIGENCE_RUNS_CONTRACT_AUDIT.md`
- `scalpyn_intelligence_runs_contract_matrix.md`
- `scalpyn_intelligence_runs_graph_contracts.md`
- `scalpyn_intelligence_runs_node_responsibilities.md`
- `scalpyn_intelligence_runs_module_tool_linkage.json`
- `SCALPYN_SYSTEMIC_MULTI_MODULE_IMPLEMENTATION_REPORT.md`

`SCALPYN_AI_IMPLEMENTATION_CONSTRAINTS.md`: `NÃO DISPONÍVEL` after a bounded repository/worktree search. No requirements were inferred from a missing file.

## Migration and schema baseline

- Local Alembic head: `156_intelligence_run_intents` `[alembic heads]`
- Staging Alembic head: `156_intelligence_run_intents` `[read-only SELECT alembic_version]`
- Next additive revision selected: `157_analysis_chat` `[derived from verified head]`
- Staging contains `ai_requests`, `ai_results`, `ai_graph_runs`, and `config_profiles`. `[information_schema SELECT]`
- Staging does not contain `ai_analysis_conversations`. `[information_schema SELECT]`
- Completed staging graph runs: `50` `[read-only SELECT count(*)]`
- Completed staging AI results: `29` `[read-only SELECT count(*)]`

The current canonical request/result contract is:

- `AIRequestRecord`: tenant, requester, origin module/view, analysis mode, authority, question hash, correlation, model resolution, prompt version, dataset snapshot, configuration bundle, immutable request JSON, creation time. `[backend/app/models/systemic_ai.py]`
- `AIResultRecord`: tenant, request, status, validated result JSON, terminal reason, completion time. `[backend/app/models/systemic_ai.py]`
- `AIRequestIntent`: provider-transport intent remains separate and is not extended for chat. `[backend/app/ai_orchestration/contracts.py]`
- `ai_budget_reservations`: one auditable reservation per request with intent, provider/model, token/cost reservation, transport state, reconciliation and release. `[backend/app/models/systemic_ai.py]`

Required additive request links are currently absent and will be nullable/backward compatible: `request_kind`, `conversation_id`, `message_id`, `parent_analysis_run_id`. `[model/schema inspection]`

## Runtime and revisions

- LangGraph: `1.2.9` `[backend/requirements-langgraph.lock]`
- langchain-core: `1.5.3` `[backend/requirements-langgraph.lock]`
- PostgreSQL checkpointer package: `3.1.2` `[backend/requirements-langgraph.lock]`
- Staging API deployment: `24cde904-55f5-4649-994d-14e5fe6af834`, status `SUCCESS`, image `sha256:b838d1dbdc520d2250aece26daffee12d55fbb33fbbe541b327b20af3c1c6838`. `[railway deployment list]`
- Staging worker deployment: `d12a65ff-e4c7-407f-847a-96ff54cf8f4f`, status `SUCCESS`, image `sha256:5ec4acfe6fb190ac829ad98e8ca86694f9ac4d6c6475c577bf2cfd40ac332768`. `[railway deployment list]`
- Production Vercel alias `scalpyn.vercel.app` currently resolves to deployment `dpl_8X8iJky7suuNFGRtdf31S7tzdQWg`, project `scalpyn`, target `production`, status `Ready`. `[vercel inspect]`
- The existing `frontend/.vercel/project.json` links to the different project `frontend`; preview deployment must therefore be explicitly linked/scoped to `scalpyn`, never inferred from that file. `[project.json + vercel inspect]`

## Current Intelligence Runs surface

- Backend router: `backend/app/api/ai_graphs.py`, mounted from `backend/app/main.py`. `[source inspection]`
- Frontend route: `frontend/app/intelligence-runs/page.tsx`. `[source inspection]`
- The baseline already renders persisted analysis results, run facts, graph events, lineage, provider/model, usage/cost and budget reconciliation. `[source inspection + baseline test]`
- Chat routes, conversation models, SSE and chat UI do not exist at this baseline. `[bounded source search]`

## Prompt, graph, evidence and tool contracts

- Prompts are immutable rows in `ai_prompt_versions` with input/output schemas, tool/provider policies and content hash. `[model inspection]`
- Graph definitions are immutable rows in `ai_graph_definitions` and must match the code registry definition/hash. `[model/service inspection]`
- Existing graph state is strict JSON/MsgPack-safe and the PostgreSQL checkpointer stores runtime checkpoints separately from application authorization/audit metadata. `[langgraph runtime inspection]`
- Original datasets, configuration bundles, results and evidence are referenced by IDs; the chat implementation must not copy or mutate their raw content. `[contract audit + model inspection]`
- Existing module tools declare side-effect authority; frozen chat must call none, while refresh is restricted to `side_effect=NONE`. `[tool linkage artifact + runtime inspection]`

## Gates and feature flags

Staging API and worker environment snapshot:

```json
{
  "AI_ORCHESTRATION_RUNTIME": "langgraph",
  "LANGGRAPH_RUNTIME_ENABLED": true,
  "LANGGRAPH_ENTRYPOINTS_ENABLED": true,
  "LANGGRAPH_STRICT_MSGPACK": true,
  "LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED": true,
  "LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED": false,
  "AI_MODULE_INTELLIGENCE_RUNS_ENABLED": true
}
```

`[railway variable read-only snapshot]`

Production API and worker keep both fake and real canary flags false. `[railway variable read-only snapshot]`

Staging active `ai_provider_runtime` profiles: `0` `[read-only SELECT]`. Therefore `normal_analysis_provider_enabled` is fail-closed by schema/default and no normal provider execution is authorized.

The chat flags will be stored in the governed tenant config registry (`config_type=ai_analysis_chat_runtime`) with all rollout capabilities false when the profile is absent. No production flag will be changed in this implementation run.

## Test baseline

- Focused backend contract baseline: `28 passed`, `5 skipped`, `0 failed`; one Python/Pydantic compatibility warning. `[pytest output]`
- Frontend unit baseline: `24 passed`, `0 failed`. `[npm test output]`
- Dependency install audit reported `1 low severity vulnerability`. `[npm ci output]`
- Full backend/frontend suites, typecheck, lint, production build and authenticated browser proof remain implementation exit checks, not baseline claims.

## Staging provider/model/budget readiness

- Fake provider canary is enabled only in staging and the previous Intelligence Runs fake proof completed without real transport, tokens or cost. `[staging fake artifact + Railway flags]`
- Real provider canary is disabled in staging and production. `[Railway flags]`
- Normal provider has no active staging runtime profile and therefore remains blocked. `[read-only SELECT]`
- Each chat turn must create its own request/job/graph run/budget reservation/result/usage record; fake execution will reconcile a zero-cost reservation without transport. `[design constraint derived from existing contracts]`
- A real-provider turn is explicitly outside this run and requires a new human checkpoint containing provider/model, token estimate, maximum cost and exact canary scope.

## Implementation invariants accepted

1. The parent graph run/result/dataset/bundle/evidence remain immutable.
2. The chat owns a distinct conversation and thread identity.
3. `request_intent` remains one of the three existing transport intents.
4. Natural language cannot expand `data_mode` or authority.
5. Frozen mode executes no new tools.
6. Read-only refresh accepts only registered `side_effect=NONE` tools.
7. Child analysis and proposal require explicit confirmation and separate audit links.
8. Proposal output is draft/candidate-only and cannot write live configuration.
9. Every turn is tenant-scoped, idempotent and budget-audited.
10. Production deployment and any real-provider transport are forbidden before a new explicit approval.

## Evidence ledger

| NUMBER REPORTED | ORIGIN | LITERAL SOURCE VALUE |
|---|---|---|
| migration head | `[alembic heads + staging SELECT]` | `156_intelligence_run_intents` |
| completed graph runs | `[staging SELECT]` | `50` |
| completed results | `[staging SELECT]` | `29` |
| LangGraph version | `[requirements lock]` | `1.2.9` |
| langchain-core version | `[requirements lock]` | `1.5.3` |
| backend baseline | `[pytest]` | `28 passed; 5 skipped; 0 failed` |
| frontend baseline | `[npm test]` | `24 passed; 0 failed` |
| active provider runtime profiles | `[staging SELECT]` | `0` |

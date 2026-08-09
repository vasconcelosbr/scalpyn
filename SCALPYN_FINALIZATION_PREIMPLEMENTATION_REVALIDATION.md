# Scalpyn Finalization Pre-implementation Revalidation

Date: 2026-08-08/09 (America/Sao_Paulo)  
Scope: final LangGraph multimodule remediation, staging-first, production read-only.

## Executive state

The supplied baseline is stale in two material ways:

- `[CODE_PROVEN]` the Preset IA direct-provider boundary had already been migrated to `SystemicLangGraphBridge`; the domain service no longer imports or invokes a provider SDK directly.
- `[PRODUCTION_RUNTIME_PROVEN: prior deployment evidence revalidated before this remediation]` the analysis-only production release had already occurred before this worktree was created. This remediation performs no production mutation.

The finalization gate is not yet complete. Real-provider cost approval, a real staging worker kill/restart, deployed authenticated UI evidence, the seven origin canaries, and the four legacy bridge canaries still require runtime execution.

## Git and isolation

- Original checkout: `C:\Users\ricar\Default Directory\ARQUIVOS - Documentos\SCALPYN\scalpyn\scalpyn`
- Original checkout state: dirty and preserved; no edits performed by this remediation.
- Isolated worktree: `C:\Users\ricar\Documents\Codex\2026-08-08\scalpyn-final-langgraph-remediation`
- Branch: `codex/final-langgraph-remediation-20260808`
- Baseline HEAD: `b32e5268dc755881834a361a9f97763de908bc1c` `[query: git rev-parse]`
- Original checkout HEAD observed: `fc53f543...` `[query: git rev-parse; abbreviated output]`
- `origin/main` observed: `6c4020c...` `[query: git rev-parse; abbreviated output]`

## Required-source review

All implementation, evidence, canary, adoption, Social, ML, Risk/Strategies, regenerative, memory, crash/resume, authenticated UI, provider and gap artifacts present in the clean source were read in full. The post-implementation audit set was read from the prior clean audit worktree because it was absent from this worktree.

The following requested files were not found in the inspected worktrees and remain `[NOT_PROVEN]` as standalone artifacts:

- `SCALPYN_AI_IMPLEMENTATION_CONSTRAINTS.md`
- `scalpyn_ai_data_contract_matrix.md`
- `scalpyn_ai_module_integration_matrix.md`

The repository knowledge-graph query returned an unrelated CatBoost/PRD subgraph and was insufficient for this remediation; direct code and migration inspection was therefore used.

## Runtime and dependency baseline

- Python: `3.14.3` `[query: python --version]`
- LangGraph: `1.2.9` `[query: installed package metadata]`
- LangGraph PostgreSQL checkpointer: `3.1.2` `[query: installed package metadata]`
- psycopg: `3.3.4` `[query: installed package metadata]`
- Celery: `5.6.3` `[query: installed package metadata]`
- SQLAlchemy: `2.0.50` `[query: installed package metadata]`
- Alembic: `1.18.4` `[query: installed package metadata]`
- `pip check`: `No broken requirements found.` `[query: pip check]`
- Required dev dependency `lupa`: declared in `backend/requirements-dev.txt`, initially absent, installed as `2.8` `[query: pip install output]`.

## Alembic and DR

- Single migration head: `150_multimodule_hardening` `[query: alembic heads]`.
- Linear tail: `148_langgraph_runtime -> 149_multimodule_langgraph -> 150_multimodule_hardening` `[query: alembic history]`.
- Fresh-database online restore exposed a real historical collision: the consolidated baseline already creates `ml_models.target_window_seconds` and `ml_models.dataset_contract_id`; migration `b2780092b9ca` attempted to add them again.
- Migration `148_langgraph_runtime` used `bulk_insert` with Python JSONB values, which Alembic cannot literal-render offline.
- Remediation preserves the material schema/data contract by using idempotent column creation with the application-compatible string contract IDs and deterministic JSONB SQL literals.
- Online upgrade on isolated database `scalpyn_final_test_20260809_0245` reached migration head after remediation `[SCHEMA_PROVEN]`.
- A second from-empty restore on `scalpyn_final_test_20260809_0310` reached `150_multimodule_hardening` with exit `0` after separating the migration 150 conflict guard and seed insert into individual prepared statements `[SCHEMA_PROVEN]`.
- `alembic upgrade head --sql` rendered the complete chain through migration 150 with exit `0` `[OFFLINE_DR_PROVEN]`.

## Backend test gate

Initial full run (`backend-full-before.xml`):

- collected=`1614` `[query: pytest]`
- passed=`1531` `[query: pytest]`
- failed=`71` `[query: pytest]`
- errors=`12` `[query: pytest]`
- skipped=`0` `[query: pytest]`

After the first remediation cycle (`backend-full-after-2.xml`):

- passed=`1582` `[query: pytest]`
- failed=`20` `[query: pytest]`
- errors=`12` `[query: pytest]`
- warnings=`3` `[query: pytest]`

The remaining Windows-host failures were isolated to database and external-API environment contracts. In the appropriate Docker database network, the database group completed with `26 passed` `[query: pytest in isolated Docker network]`.

No test was skipped, xfailed, deleted, or weakened generically. Obsolete fixtures were aligned to already-shipped contracts: deterministic score, ATR dynamic v2, separated ingestion/trading authority, explicit aggregated volume names, current simulation skip semantics, and mandatory price columns.

## Provider boundary

Static scan terms:

`Anthropic|AsyncAnthropic|OpenAI|AsyncOpenAI|messages.create|chat.completions|GenerativeModel`

Allowed locations:

- central HTTP/provider adapters;
- central Anthropic SDK adapter;
- central Copilot transport;
- explicit provider-key validation endpoint/service;
- tests and fakes.

Domain services use the systemic bridge. `preset_ia_service.py` contains no direct SDK/HTTP provider call. A deployment-blocking static test is present and passes. Broader centralization of every model default and prompt string remains subject to the final scan/report.

## Staging topology

- Railway project ID: `a3af94be-bbb5-413b-a1bd-c1f0a5db0ee5` `[query: Railway project status]`
- Environment: `systemic-ai-staging-20260807` `[query: Railway environment status]`
- API service ID: `e366856b-677a-4bc9-b4b0-93ed03dfc7af` `[query: Railway service status]`
- Worker service ID: `10bd1a86-e559-4229-afc2-4a60624b7b5c` `[query: Railway service status]`
- PostgreSQL service ID: `c77013b1-5eef-4d98-8d44-3775e63c2d40` `[query: Railway service status]`
- Redis service ID: `e7657f1a-86b7-4a5b-9007-9a308e530f71` `[query: Railway service status]`
- API and worker reported `SUCCESS/RUNNING` at revalidation time `[query: Railway deployment status]`; functional canaries are still required.
- Systemic runtime, module, regenerative, fake-canary and strict-msgpack flags were enabled in staging; real-provider canary was disabled `[query: environment key presence/boolean values]`.
- No OpenAI, Anthropic or Gemini provider key was present on the staging API/worker services at revalidation time `[query: environment key-name presence only; values never printed]`.

## Model/provider and cost checkpoint

No real provider call has been made by this remediation. The required model-catalog resolution, token estimate, maximum output, worst-case cost, reservation and tool/dataset authority must be presented before a single staging call. Until explicit cost approval:

`REAL_PROVIDER_PROVEN=NO`

## Spot safety

No decision has been made on the Spot invariant or its configuration precedence. No TP, SL, sizing, Global Risk, Strategies or Spot exit setting was changed.

`AI_SPOT_AUTHORITY_BLOCKED`

## Credential incident

Prior evidence says the exposed canary credential was rotated, but revocation, artifact/history scans, browser storage cleanup and post-rotation non-use have not yet been re-proven in this worktree.

Status: `MITIGATED_WITH_RESIDUAL_RISK` `[NOT_PROVEN for closure]`.

## Production safety

No production database write, migration, environment change, deployment, restart, canary, graph run, model action or order was performed by this remediation.

`PRODUCTION_MUTATIONS_BEFORE_APPROVAL=0`

## Evidence ledger

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| backend inicial collected=1614 | `[query: pytest]` | `collected 1614 items` |
| backend inicial failed=71 | `[query: pytest]` | `71 failed` |
| backend inicial errors=12 | `[query: pytest]` | `12 errors` |
| backend após ciclo passed=1582 | `[query: pytest]` | `1582 passed` |
| backend após ciclo failed=20 | `[query: pytest]` | `20 failed` |
| backend após ciclo errors=12 | `[query: pytest]` | `12 errors` |
| DB focused passed=26 | `[query: pytest Docker]` | `26 passed in 7.00s` |
| backend final passed=1614 | `[query: pytest Docker]` | `1614 passed, 6 warnings in 69.50s` |
| backend final failed=0 | `[query: pytest Docker]` | `BACKEND_FULL_EXIT=0` |
| frontend tests passed=23 | `[query: node test]` | `pass 23; fail 0; skipped 0` |
| frontend lint errors=0 | `[query: eslint]` | `435 problems (0 errors, 435 warnings)` |
| Alembic online exit=0 | `[query: alembic isolated DB]` | `ALEMBIC_ONLINE_EXIT=0` |
| Alembic offline exit=0 | `[query: alembic --sql]` | `ALEMBIC_OFFLINE_EXIT=0` |
| Alembic head=150 | `[query: alembic]` | `150_multimodule_hardening (head)` |
| produção mutações=0 | `[ABERTO: reconciliation final]` | nenhum comando de mutação de produção nesta remediação |

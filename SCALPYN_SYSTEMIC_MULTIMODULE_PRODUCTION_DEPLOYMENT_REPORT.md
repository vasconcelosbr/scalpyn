# Scalpyn — Systemic Multi-Module Production Deployment Report

## Resultado

Status: `DEPLOYED_HEALTHY_ANALYSIS_ONLY_WITH_RESIDUAL_GATES`.

The systemic multi-module implementation is active in production. Railway API and dedicated LangGraph worker are healthy, PostgreSQL is at the expected migration head, and the operational Vercel alias serves the new build [deployment + production probes]. AI authority remains analysis/proposal/candidate/Shadow only.

## Escopo implantado

| Surface | Evidence | Final state |
|---|---|---|
| PostgreSQL | [query: production Alembic] | `150_multimodule_hardening (head)` |
| Railway API | [deployment: Railway] | `4ff3c107-2431-4427-84b1-5ee91f00fdeb` — `SUCCESS` |
| LangGraph worker | [deployment: Railway] | `7e307ac1-9f68-49d0-a1a6-85e001f2d7f1` — `SUCCESS` |
| Vercel frontend | [deployment: Vercel] | `dpl_8GTy7i92msxdsG1irBkEspnfg44d` — `READY` |
| Operational alias | [inspection: Vercel] | `https://scalpyn.vercel.app` points to the deployment above |

## Feature flags de produção

Literal readback from both the API and LangGraph worker [config: Railway production]:

```text
LANGGRAPH_RUNTIME_ENABLED=true
LANGGRAPH_ENTRYPOINTS_ENABLED=true
LANGGRAPH_REGENERATIVE_SHADOW_ENABLED=false
LANGGRAPH_REAL_PROVIDER_CANARY_ENABLED=false
LANGGRAPH_FAKE_PROVIDER_CANARY_ENABLED=false
AI_MODULE_STRATEGY_PROFILES_ENABLED=true
AI_MODULE_ML_MODELS_ENABLED=true
AI_MODULE_SHADOW_ENABLED=true
AI_MODULE_SCORE_ENGINE_ENABLED=true
AI_MODULE_GLOBAL_RISK_ENABLED=true
AI_MODULE_STRATEGIES_ENABLED=true
AI_MODULE_SOCIAL_SCORE_ENABLED=true
AI_MODULE_MARKET_REGIME_ENABLED=true
AI_MODULE_INTELLIGENCE_RUNS_ENABLED=true
AI_MODULE_AUDIT_MEMORY_ENABLED=true
```

These values enable the control plane and module entrypoints while keeping regenerative execution and provider canaries disabled.

## Saúde e rotas

- Railway API startup completed after `alembic upgrade head` and critical schema validation [logs: final API deployment].
- Dedicated worker connected to Redis, subscribed only to `ai_orchestration`, with concurrency `1`, and reached ready state [logs: final worker deployment].
- Direct Railway and Vercel-proxied `/api/health`: HTTP `200` [probe: production].
- Direct Railway and Vercel-proxied `/api/health/schema`: HTTP `200` [probe: production].
- Unauthenticated `/api/ai/graphs/definitions` and `/api/ai/graphs/capabilities`: HTTP `401`, expected authorization boundary [probe: production].
- Public Vercel routes `/`, `/intelligence-runs`, `/ml-models`, `/settings/risk`, `/settings/strategies` and `/settings/social-score`: HTTP `200` [probe: Vercel production].
- Vercel post-deploy logs contained `0` error-level entries and `0` HTTP 5xx entries in the inspected window [logs: Vercel].

## Reconciliação read-only

Literal frozen query after rollout [query: production database]:

```json
{"migration":"150_multimodule_hardening","module_statuses":[["APPROVED",10]],"model_approvals":0,"tool_evidence":0,"graph_runs":0,"orders_total":0}
```

Interpretation [inference]: the schema and module registry are present, but deployment itself did not execute a provider, approve a model, run an AI graph, persist AI tool evidence, or create an order.

## Incidente e recuperação

Updating Railway flags triggered source-linked deployment `4613bf70-1e3f-47d7-a92c-1ae1685df335` from stale `origin/main` [deployment: Railway]. Although the platform marked the build `SUCCESS`, runtime returned HTTP `502`; logs stated `Can't locate revision identified by '150_multimodule_hardening'` [logs: Railway].

The current source was immediately re-uploaded. Final API deployment `4ff3c107-2431-4427-84b1-5ee91f00fdeb` completed migration and startup, and both health routes returned HTTP `200` [deployment + production probes]. Production is healthy now. Source-control reconciliation remains mandatory before the next automatic rebuild.

## Verificações não executadas

- Real-provider canary: `NÃO REALIZADO`; provider/model/pricing/cost approval was not supplied.
- Fake-provider production canary: `NÃO REALIZADO`; the production fake-canary flag remains disabled.
- Model approval/promotion: `NÃO REALIZADO`.
- Live AI order or live configuration mutation: `NÃO REALIZADO`.
- Real worker kill/restart crash-resume test: `NÃO REALIZADO`.
- Authenticated production Intelligence Runs browser flow: `NÃO VERIFICADO`.

## Riscos residuais

1. Full backend suite remains red: `68` failures and `12` errors in `1606` collected tests [query: `.codex-evidence/full-backend.xml`].
2. Existing execution worker emitted missed heartbeat messages at `02:07:49`, `02:09:49` and `02:11:54` [logs: Railway]. It remained active and was not restarted because it serves real trading.
3. Current production source is ahead of source-linked `origin/main`; a future automatic rebuild can repeat the migration mismatch until a controlled merge/release.
4. Full-history Alembic offline SQL rendering still fails on immutable historical revision `148`, while runtime upgrade to `150_multimodule_hardening` passed.

## Rollback operacional

- Frontend: promote the prior known-good Vercel deployment to the `scalpyn.vercel.app` alias [procedure: Vercel].
- API/worker: redeploy the prior known-good source only with database compatibility verified first [procedure: Railway].
- Database: do not downgrade blindly. Migrations `149` and `150` must be evaluated against persisted rows and application compatibility before any downgrade [migration guardrail].
- Safety fallback: disable `LANGGRAPH_ENTRYPOINTS_ENABLED` and `LANGGRAPH_RUNTIME_ENABLED` on both API and worker, then manually redeploy the current compatible source to avoid the stale-main auto-build path [procedure: Railway].

## Ledger de evidências numéricas

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| migration head=`150_multimodule_hardening` | [query: production Alembic] | `150_multimodule_hardening (head)` |
| approved modules=`10` | [query: production database] | `module_statuses=[["APPROVED",10]]` |
| model approvals=`0` | [query: production database] | `"model_approvals":0` |
| AI tool evidence=`0` | [query: production database] | `"tool_evidence":0` |
| graph runs=`0` | [query: production database] | `"graph_runs":0` |
| orders=`0` | [query: production database] | `"orders_total":0` |
| worker concurrency=`1` | [logs: Railway worker] | `concurrency: 1` |
| public/proxy health=`200` | [probe: production routes] | HTTP status `200` |
| protected unauthenticated API=`401` | [probe: production routes] | HTTP status `401` |
| Vercel inspected errors=`0` | [logs: Vercel] | error entries `0` |
| Vercel inspected 5xx=`0` | [logs: Vercel] | 5xx entries `0` |
| focused tests=`78` passed | [query: focused pytest] | `78 passed` |
| full backend failures/errors=`68`/`12` | [query: JUnit] | `tests=1606; failures=68; errors=12` |

Every absent value is reported as `NÃO VERIFICADO`, `NÃO REALIZADO` or `NÃO DISPONÍVEL`; no value is inferred as a fact.

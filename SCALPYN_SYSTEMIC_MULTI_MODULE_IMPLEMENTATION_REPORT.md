# Scalpyn — Systemic Multi-Module Intelligence Implementation Report

## 1. Veredito

`PRODUCTION_ANALYSIS_ONLY_DEPLOYED_WITH_RESIDUAL_GATES`.

The systemic multi-module runtime is deployed in production with analysis-only authority. The production API, dedicated LangGraph worker, database migrations and Vercel frontend are healthy [deployment: final production verification]. Completion remains gated by the non-green full backend suite, the absence of an approved real-provider canary, missing process-kill crash/resume proof, recurring execution-worker heartbeat misses and source-control reconciliation.

## 2. Baseline e código implantado

The implementation originated from clean baseline `fa586ff8cd006ac790e9ee431f6698fd838cc530` [git]. The deployed worktree includes implementation commit `c86faffd4b9ad35601d384b4c0d8093b5b4a29f2`, provider-boundary fix `09b8cb47722a79237db803210c863dee65a32696` and Vercel packaging fix `5aea8d2ba6694aab92a6ed2f0ecedb7d696ef8c3` [git].

## 3. Segurança e autoridade

Authority remains capped at analysis/proposal/candidate/Shadow. There is no live AI tool. Tenant scope, typed schemas, row/time bounds, human approval gates, output hashes, fake/real-provider separation and Spot blocking are enforced. Legacy `preset_ia_service.py` provider calls now pass through `SystemicLangGraphBridge` and the central adapter boundary [code + focused tests].

Production flags explicitly keep regenerative Shadow and both provider canaries disabled [config: Railway production]. No provider call, model approval, graph run, AI tool evidence or AI order was created during the rollout [query: production post-deploy probe].

## 4. Arquitetura

Module entrypoint → Intelligence Runs API → orchestration service → LangGraph → typed tools/policies/contextual memory → canonical PostgreSQL records.

## 5. Capability registry

The registry contains `10` approved immutable tenant-scoped modules [code: module registry].

## 6. Module contracts

- Strategy Profiles: read tools plus human-approved candidate version creation; no live pointer switch.
- ML Models: read-only metrics/contracts/drift/authority; no training or promotion authority.
- Shadow Portfolio: frozen datasets, deterministic comparison and human-gated experiments.
- Score Engine: read/explain/validate plus candidate versions only.
- Global Risk and Strategies: read-only hard vetoes; no policy mutation.
- Social Score and Market Regime: read-only contextual evidence with provenance and missingness preserved.
- Audit/Version/Experiment Memory: durable contextual memory for completed Shadow decisions.
- Intelligence Runs: authenticated control plane for lineage, evidence, authority, prompts, datasets and bundles.

## 7. Graphs e entrypoints

Four approved v2 graphs are versioned and exported as Mermaid [code: graph registry]. Four legacy bridges and seven module UI actions are implemented and statically tested [code + focused tests]. Only `shadow_portfolio` has end-to-end staging runtime proof; individual execution of every entrypoint remains open.

## 8. Staging proof

The staging canary proved frozen datasets, complete configuration lineage, fake-provider resolution, durable interrupts, contextual Decision Memory, candidate version-on-change and zero orders [query: staging canary]. A real worker process kill/restart remains `NÃO VERIFICADO`.

## 9. Test gates

Focused backend verification passed with `78` tests and `0` failures [query: focused pytest]. The historical full backend collection remains red with `68` failures and `12` errors in `1606` collected tests [query: `.codex-evidence/full-backend.xml`]. The full-history Alembic offline renderer still fails on immutable historical migration `148`; actual runtime upgrades through the new migrations passed [command evidence].

## 10. Produção

- Database migration: `150_multimodule_hardening (head)` [query: production Alembic].
- Railway API deployment: `4ff3c107-2431-4427-84b1-5ee91f00fdeb`, status `SUCCESS` [deployment: Railway].
- Railway LangGraph worker deployment: `7e307ac1-9f68-49d0-a1a6-85e001f2d7f1`, status `SUCCESS` [deployment: Railway].
- Vercel deployment: `dpl_8GTy7i92msxdsG1irBkEspnfg44d`, status `READY`, alias `https://scalpyn.vercel.app` [deployment: Vercel].
- Direct and Vercel-proxied `/api/health` and `/api/health/schema`: HTTP `200` [probe: production routes].
- Protected graph definitions/capabilities without authentication: HTTP `401`, expected [probe: production authorization].
- Production runtime state after rollout: `10` module rows in `APPROVED`; `0` model approvals; `0` AI tool evidence; `0` graph runs; `0` orders [query: production post-deploy probe].

The first API rebuild triggered by the feature-flag update used stale source-linked `origin/main` and returned HTTP `502` because it could not locate migration `150_multimodule_hardening` [logs: Railway deployment `4613bf70-1e3f-47d7-a92c-1ae1685df335`]. The current source was re-uploaded immediately, restoring the healthy final deployment above [deployment + route probes].

## 11. Riscos residuais

See `scalpyn_remaining_risks_after_multimodule.md`. The two most urgent operational items are controlled reconciliation of the deployed commits into the source-linked main branch, and investigation of recurring missed heartbeat messages for the existing execution worker without disrupting real-trading services.

## 12. Evidência de implantação

See `SCALPYN_SYSTEMIC_MULTIMODULE_PRODUCTION_DEPLOYMENT_REPORT.md` and `SCALPYN_SYSTEMIC_MULTI_MODULE_EVIDENCE_LEDGER.md`.


# Scalpyn — Systemic Multi-Module Intelligence Implementation Report

## 1. Veredito

`PARTIALLY_IMPLEMENTED_TEST_GATE_FAILED`.

The staging systemic and regenerative Shadow runtime is proven, but acceptance is blocked by the non-green full backend suite, absent real-provider cost approval, absent real process crash/restart proof, and incomplete protected-preview UI proof. Production was not changed.

## 2. Baseline revalidado

The clean worktree started at `fa586ff8cd006ac790e9ee431f6698fd838cc530`. Production read-only probes showed migration `148_langgraph_runtime`, empty systemic runtime tables, no live/Auto-Pilot profiles and no AI-created orders at the frozen probe time [query: production read-only probe].

## 3. Segurança

Authority is capped at analysis/proposal/candidate/Shadow. There is no live tool. Tenant scope, typed schemas, row/time bounds, human approval gates, output hashes, fake/real-provider separation and Spot blocking are enforced. The staging canary credential was rotated after browser exposure and re-applied by a fresh successful canary. Legacy `preset_ia_service.py` provider calls now pass through `SystemicLangGraphBridge` and the central adapter boundary.

## 4. Arquitetura

Module entrypoint → Intelligence Runs API → orchestration service → LangGraph → typed tools/policies/contextual memory → canonical PostgreSQL records.

## 5. Module Capability Registry

`10` approved immutable tenant-scoped modules [code: module registry].

## 6. Strategy Profiles

Read tools plus human-approved candidate version creation; no live pointer switch.

## 7. ML Models

Read-only metrics/contracts/drift/authority. No training or promotion authority.

## 8. Shadow Portfolio

Frozen datasets, deterministic comparison and human-gated Shadow experiments.

## 9. Score Engine

Read/explain/validate plus new candidate versions only.

## 10. Global Risk

Read-only hard veto; no candidate or policy mutation.

## 11. Strategies

Read-only hard veto; Spot invariant remains blocked.

## 12. Social Score

Read-only, provenance/freshness/missingness preserving, sanitized and not injected into ML.

## 13. Market Regime

Read-only context used in root-cause and memory fingerprints.

## 14. Audit/Version/Experiment Memory

Final Shadow decisions persist completed contextual memory with mutation/context fingerprints.

## 15. Intelligence Runs

Authenticated local frontend against staging displayed list, timeline, lineage, authority, model, prompt, dataset, bundle, tool count and cost. Vercel preview remained protected.

## 16. Graphs

Four approved v2 graphs are versioned and exported as Mermaid [code: graph registry].

## 17. Entrypoint adoption

Four legacy bridges and seven module UI actions are implemented and statically tested [code + focused tests]. Only `shadow_portfolio` received end-to-end staging runtime proof.

## 18. Canonical datasets

Final dataset `e95e774e-ad5b-43fb-89e6-aa5d989c8946` passed quality and remained immutable [query: staging canary].

## 19. Configuration bundles

Final bundle `9b5c6dc3-b52a-4646-8c0e-18b5e0b76f09` carried complete lineage [query: staging canary].

## 20. Provider/model

Fake staging provider only: configured/effective `fake-analysis-v1`, cost `0` USD [query: ai_usage]. Real provider is not proven and was intentionally not called without cost approval.

## 21. Data quality

Missing module rows remain `NO_DATA`; they are not coerced to zero. Candidate-capable dataset quality passed.

## 22. Versioning/rollback

Each A/B/C run created one candidate version [query: graph event]. Rollback remains version-on-change; no in-place live rollback occurred.

## 23. Regenerative cycle

Runs A/B/C completed after three durable interrupts each [query: staging canary]. B reused A; C used a different context and did not inherit the block [query: staging canary].

## 24. Decision Memory

Contextual reuse and isolation are proven by persisted memory IDs and event payloads.

## 25. Crash/resume

Checkpoints and interrupt/resume are proven. A real worker process kill/restart is `NÃO VERIFICADO`.

## 26. Frontend

Tests, typecheck and production build passed. Lint has zero errors and classified warnings.

## 27. Tests/security

Focused tests passed. The full collection remains red and blocks completion. npm audit has no high/critical findings. The provider-boundary scan still finds direct calls in the legacy Preset IA domain service. The full-history Alembic offline renderer fails on immutable historical migration `148`; the actual new-migration cycle and staging upgrade passed.

## 28. Staging

Railway API and dedicated `ai_orchestration` worker are healthy. The worker imports only orchestration tasks [logs: dedicated worker]. Final canary created zero orders [query: staging orders] and no live writes.

## 29. Production

Not deployed. Mandatory checkpoint pending.

## 30. Gap closure

See `scalpyn_gap_closure_after_multimodule.json`.

## 31. Remaining risks

See `scalpyn_remaining_risks_after_multimodule.md`.

## 32. Evidence ledger

See `SCALPYN_SYSTEMIC_MULTI_MODULE_EVIDENCE_LEDGER.md`.

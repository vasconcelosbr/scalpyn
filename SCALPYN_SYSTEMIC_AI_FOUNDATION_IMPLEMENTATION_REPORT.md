# Scalpyn Systemic AI Foundation — Implementation Report

## Verdict

`SYSTEMIC_AI_FOUNDATION_STAGING_PROVEN`

The additive foundation, reversible database migration and an analysis-only persistent canary passed in an isolated Railway staging database built from the production schema without business rows. Production promotion remains a separate recorded checkpoint.

## Revision and environment

- Worktree: `C:\Users\ricar\Documents\Codex\2026-08-07\scalpyn-systemic-ai-foundation`.
- Branch: `codex/systemic-ai-foundation-phase1-20260807`.
- Base production revision observed before rollout: `146_l3_1200_validation` `[query]`.
- New single head: `147_systemic_ai_foundation` `[test]`.
- Staging: Railway environment `systemic-ai-staging-20260807`, database `Postgres-OarF`.

## Safety posture

- The production audit before promotion was read-only.
- The staging canary used one synthetic inactive tenant and a fake in-process adapter; external provider calls=`0` `[query]`.
- Its authority was `ANALYSIS_ONLY`; live trading, model promotion and real-risk mutation were false.
- `LIVE_WRITE` was denied with `TOOL_SIDE_EFFECT_DENIED` `[query]`.
- Staging live-trading and Auto-Pilot counts were unchanged before/after `[query]`.

## Foundation delivered

`backend/app/ai_orchestration/` now contains immutable request/result/error contracts, authenticated tenant context, provider/model and prompt registries, canonical frozen datasets, configuration bundles, job leases, budget admission/reconciliation, sanitizer, reliability classification, tool governance, invariant validation, persistence and observability contracts.

The control path is fail-closed: tenant mismatch, unknown model, missing approved prompt, blocked dataset, invariant conflict, absent budget or missing tenant key stops execution before the provider.

## Database and lineage

Revision 147 adds the systemic request ledger, approved prompt registry, model resolutions, bundles, datasets, jobs, results, usage, budgets, tool audits and regenerative decision-memory tables. Legacy bridge columns are nullable, so no historical tenant or lineage identifier is fabricated.

The production-schema staging copy passed `146 → 147 → 146 → 147` `[query]`. The final probe found the revision-147 schema, and the approved-prompt trigger rejected mutation `[query]`.

## Persistent canary evidence

The synthetic request completed and persisted one record in each of the following groups `[query]`: model resolution, configuration bundle, dataset snapshot, request, job, result, usage and tool-call audit.

The negative gates returned:

- cross-tenant request: `TENANT_SCOPE_MISSING` `[query]`;
- unknown model: `MODEL_UNKNOWN` `[query]`;
- live-write tool: `TOOL_SIDE_EFFECT_DENIED` `[query]`;
- stale lease: recovered with `STALE_JOB_RECOVERED` `[query]`.

The dataset quality result was `PASS` and the result status was `COMPLETED` `[query]`. IDs and hashes are preserved in `scalpyn_systemic_ai_staging_canary.json`.

## Entrypoint integration

- Profile suggestion explanation uses validated tenant keys, explicit budget and the shared HTTP adapter.
- Shadow detailed analysis validates models before queueing, persists tenant/lease/terminal metadata and exposes configured/effective model and prompt trace.
- AI Critic scopes queries and provider keys by tenant.
- Co-Pilot removes environment-key fallback and uses tenant-scoped validated keys plus the shared provider transport.
- Profile optimization creates immutable candidates; champion/runtime and live flags remain unchanged.

The common foundation is complete and staging-proven. Full canonical persistence adoption by every legacy entrypoint remains an explicitly documented follow-up.

## Provider/model truth

Known models are explicitly catalogued; aliases must be versioned and valid. An unknown model is rejected before queueing. The observed Profile Intelligence value `"claude-fable-5"` `[config: profile_intelligence]` remains intentionally unresolved and therefore fails closed—there is no silent fallback.

## Invariants

The validator prohibits live trading authority, model promotion and real-risk mutation. Candidate-only optimization cannot change champion/runtime. Spot authority is blocked when its effective configuration conflicts with never-sell-at-loss. This implementation does not reconcile or alter human-owned trading configuration.

## Frontend

The Shadow Detailed Report surfaces analysis authority, configured/effective model, prompt version, attempt and terminal state. Candidate optimization copy explicitly states that a new immutable candidate is created and champion/runtime stay unchanged. Legacy `PENDING`/`FAILED` states remain display-compatible.

## Validation

- Critical backend: `63 passed in 6.35s` `[test]`.
- Named foundation suite: `27 passed in 1.06s` `[test]`, skipped=`0` `[test]`.
- Frontend: passed=`23`, failed=`0` `[test]`.
- Production build/TypeScript: generated pages=`43` `[test]`.
- Changed component lint: exit=`0` `[test]`.
- Compile, JSON parse and `git diff --check`: pass `[test]`.
- Graph refresh: nodes=`15461`, edges=`21968`, communities=`1175` `[test]`.

The global frontend lint debt and backend import-time CatBoost collection failure predate this change and are recorded in `scalpyn_systemic_ai_test_results.json`.

## Remaining risks

The authoritative list is `scalpyn_systemic_ai_remaining_risks.md`. The material open items are complete canonical adoption in legacy entrypoints, explicit reconciliation of the invalid provider model, historical dataset conflicts, the human-owned Spot invariant and post-deploy authenticated UI/runtime evidence.

## Production rollout plan

1. Commit and push the reviewed branch.
2. Apply revision 147 explicitly to production and verify the head/tables/bridges read-only.
3. Deploy the affected Railway API/workers and verify health plus bounded logs.
4. Link the clean frontend to Vercel project `scalpyn`, publish a candidate, verify critical routes and promote its alias.
5. Re-query live/Auto-Pilot invariants and record deployment identifiers in this report.

## Evidence ledger

See `SCALPYN_SYSTEMIC_AI_FOUNDATION_EVIDENCE_LEDGER.md`. Every reported number is either literal test/query output or marked unavailable.

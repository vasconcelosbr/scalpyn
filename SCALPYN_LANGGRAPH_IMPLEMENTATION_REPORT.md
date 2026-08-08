# Scalpyn LangGraph implementation report

Verdict: `PARTIALLY_IMPLEMENTED_BLOCKED_BY_PROVIDER`.

## Outcome

LangGraph was added above the canonical systemic AI foundation without replacing canonical database records, Celery or provider adapters. The implementation contains an exact hash lock, dedicated PostgreSQL checkpoint schema, immutable registry, four graphs, canonical run/event/interrupt records, five durable tasks on the isolated `ai_orchestration` queue, tenant-safe APIs, human interrupts, crash recovery, event reconciliation, observability and the authenticated `/intelligence-runs` page.

The controlled production deployment is installed but inert. Migration `148`, the checkpointer, the API, an isolated worker and the frontend are deployed. Runtime, entrypoints, regeneration and provider canaries remain explicitly false. No model champion, Auto-Pilot state, order or real position was changed. `LIVE_WRITE` remains denied.

## Dependency result

- `langgraph==1.2.9` `[config]`.
- `langgraph-checkpoint-postgres==3.1.2` `[config]`; upgraded from the prompt's reference patch after the upstream security fix became available.
- `psycopg[binary,pool]==3.3.4` `[config]`.
- Hash-locked resolution, `pip check` and scoped security audit passed `[test]`.

## Staging result

- Railway API deployment `34489a41-da33-46a6-9e8e-ca036e6d877c`: `SUCCESS` `[staging]`.
- Railway worker deployment `d438715c-b5ca-4546-a212-43c578c41c8b`: `SUCCESS`, consuming only `ai_orchestration` `[staging]`.
- Health is `ok`; schema gate checked 32 items with none missing `[staging]`.
- Analysis run `340cbd93-18e3-44b2-aebd-3d0da8c5fc35`: `COMPLETED`, `ANALYSIS_ONLY`, 18 events `[staging]`.
- Regenerative run `64e00a75-d41c-41dc-b396-ea49dc29ba70`: `COMPLETED`, `SHADOW_ONLY`, 24 events and all three interrupts resolved `[staging]`.
- Actual worker-restart recovery run `1ca42bb8-a0e5-4705-9423-4cc34df99a1c`: `COMPLETED`, with zero duplicate event keys and zero duplicate completed nodes `[staging]`.
- Protected Vercel preview deployment `dpl_6kzMikShU243HmJX2HKHwpgAaXuU`: `READY`; authenticated `/intelligence-runs` proof completed `[staging]`.
- Fake provider cost was USD 0. The real-provider canary was not run because separate cost approval is absent `[ABERTO]`.

## Controlled production result

- Verified backup: `2491960498` bytes, `1086` restore entries, full archive read `OK`, SHA-256 `3d2966588b76fcc9960e3308f004d6f7ec5d90fc7641e1bb43eb0f949bdb6ae2` `[backup]`.
- Migration head: `147_systemic_ai_foundation` to `148_langgraph_runtime` `[query]`.
- Checkpointer bootstrap: `COMPLETED`, four internal tables, four approved definitions and strict MessagePack `[query]`.
- Railway API `92a4a71b-18d7-49c7-9fd9-eb91b976b7ab`: `SUCCESS`; health and schema are green `[production]`.
- Railway worker `b799b590-a114-4f31-8abe-41c846672fcd`: `SUCCESS`; only `ai_orchestration`, concurrency one, no beat `[production]`.
- Vercel `dpl_EHYzvWjfaPU6MVEtbzgPmQ8YXwsh`: `READY`, aliased to `https://scalpyn.vercel.app` `[production]`.
- `/intelligence-runs`, `/settings/social-score`, `/settings/risk` and `/settings/strategies` return HTTP 200 `[production]`.
- Canonical reconciliation: zero graph runs/events/interrupts; 53 active profiles; zero live-trading, Auto-Pilot and shadow-only profiles `[query]`.
- Authenticated `/intelligence-runs` proof completed on the production alias. The page showed runtime `langgraph`, entrypoints and provider canary disabled, strict checkpoints, `live write: denied` and zero records `[authenticated browser]`.
- No application-origin console error was observed; one error came from the separate `vercel.live` feedback overlay bundle and did not prevent the page from rendering `[browser console]`.

## Tests

- Mandatory LangGraph suite: 39 passed `[test]`.
- Critical dependency/runtime suite: 87 passed `[test]`.
- Backend collection: 1573 collected, zero collection errors `[test]`.
- Frontend: 23 tests passed, typecheck passed, production build passed with `/intelligence-runs` among 44 routes `[test]`.
- Global lint remains at 371 errors and 62 warnings in legacy code `[test]`.
- Full backend execution stopped at 20 failures after 278 passes; the captured failures are legacy migration-path, stale CatBoost fixtures and database-dependent cases `[test]`.

## Why the verdict is not PROVEN

The prompt's acceptance criteria still require a separately approved real-provider canary, a human Spot-invariant decision and globally green lint/full tests. Controlled deployment, persistent checkpoints, inert production reconciliation and authenticated production UI are proven; those remaining items are not.

## Rollback

Keep all flags false to make the new paths inert. For production, take a database backup before migration, retain the previous API/worker/frontend deployment identifiers, and roll application services back first if health fails. Migration downgrade deletes checkpoint history and therefore requires explicit confirmation plus the backup; canonical audit records should be retained.

## Ledger de evidências

See `SCALPYN_LANGGRAPH_EVIDENCE_LEDGER.md` for the complete claim and numeric-source mapping.

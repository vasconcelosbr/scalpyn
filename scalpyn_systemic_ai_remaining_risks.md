# Scalpyn Systemic AI — Remaining Risks

1. **Full canonical adoption remains incomplete.** The legacy entrypoints share controlled provider transports and tenant/model/budget checks, but do not all persist the entire canonical request → dataset → bundle → job → result lifecycle through `AIOrchestrationService`.
2. **The current Profile Intelligence model fails closed.** Production configuration contains `claude-fable-5` `[config: profile_intelligence]`, which is absent from the approved catalog. It requires an explicit validated mapping or configuration change; no fallback was invented.
3. **Historical dataset conflicts remain.** Previously observed duplicates and conflicting event identities were not corrected. Proposal/change-set authority remains blocked when those conflicts enter a canonical dataset.
4. **The Spot invariant remains human-owned.** Production exposed `"never_sell_at_loss": false` and AI consultation disabled `[config: spot_engine]`. The systemic validator blocks AI authority for the conflict; this rollout does not alter the trading configuration.
5. **No external provider was called by the canary.** Auth, credit, rate-limit and malformed-response handling remain deterministic test evidence, not a billable runtime call.
6. **Repository-wide quality debt predates this change.** Global frontend lint and backend global collection have documented unrelated failures; the changed component and the critical suites pass.
7. **Authenticated end-to-end UI evidence is pending until the production frontend is promoted.** The staging database canary proves persistence and guards, while the user-facing route still requires post-deploy verification.

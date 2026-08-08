# Remaining risks

- `BLOCKER`: the real-provider analysis-only canary is not run because separate cost approval and an explicit cost ceiling are absent.
- `BLOCKER`: Spot invariant semantics remain a separate human decision; no active Spot configuration was changed.
- `BLOCKER`: the new production page still needs authenticated browser proof after the user's saved session expired; no credential was accessed or changed.
- `HIGH`: global ESLint has a legacy baseline of 371 errors and 62 warnings; the changed page and navigation pass scoped lint, TypeScript and production build.
- `HIGH`: the full backend suite stopped at 20 failures after 278 passes; failures are legacy migration-path, stale CatBoost-fixture and database-dependent cases. Global collection and the critical runtime suite pass.
- `HIGH`: the global frontend dependency audit has ten existing findings and requires a separate upgrade campaign.
- `MEDIUM`: provider execution remains paid and gated. The zero-cost canary proves orchestration, checkpointing and safety, not provider availability.
- `MEDIUM`: historical production event conflicts are mapped by contract but not migrated.
- `MEDIUM`: automatic checkpoint deletion remains disabled pending an approved retention policy.

The prior checkpoint-postgres advisory is remediated by the exact upgrade to `3.1.2`; the 40-package scoped lock audit reports no known vulnerabilities. No remaining risk was converted into a production-proof claim.

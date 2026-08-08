# Remaining risks

- `BLOCKER`: production checkpoint has not been approved. No production migration/deploy/flag activation is allowed yet.
- `BLOCKER`: real-provider analysis-only canary is not run because cost approval is missing.
- `BLOCKER`: Spot invariant semantics remain a separate human decision.
- `HIGH`: global ESLint has legacy errors; the new page and navigation pass scoped lint, TypeScript and production build.
- `HIGH`: the full backend suite stops on legacy migration-path, CatBoost-fixture and database-dependent failures; global collection and the critical runtime suite pass.
- `HIGH`: `CVE-2026-71433` is reported package-wide. Current Saver-only usage is classified not applicable with mitigations, but must be rechecked before production.
- `HIGH`: ten existing npm audit findings require a separate dependency-upgrade campaign.
- `MEDIUM`: provider execution remains paid and gated. The zero-cost canary consumes a canonical pre-persisted fake result and cannot prove provider availability.
- `MEDIUM`: historical production event conflicts are mapped by contract but not migrated.
- `MEDIUM`: automatic checkpoint deletion is intentionally disabled pending an approved retention policy.

No risk above was converted into a “proven” production claim.

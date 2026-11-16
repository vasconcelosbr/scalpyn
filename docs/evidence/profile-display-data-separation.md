# Profile display and stale-data separation

The spot L3 watchlist now has a read-only `/watchlists/{id}/profile-status` projection with disjoint Approved, Rejected, and Aguardando dados populations. Rejection comes only from predicates authored in the current profile. Missing, expired, mismatched-version, or incomplete evidence is pending data. Scoring and global execution policy are not profile-display vetoes.

The projection checks the immutable decision lineage and canonical profile hash, preserves configured Boolean logic, and does not extend indicator TTL. Both payload and items explicitly deny execution authority. Existing assets, public authorization, consolidation, exchange-write guard, profiles, and execution policies are unchanged.

The frontend reads the populations atomically, labels pending data separately, and does not display a fabricated flow restriction on a profile rejection. Expanded profile counters use the same response as the tabs.

Validation before release:
- [command output] Isolated Python overlay in the Railway API container, with dummy DB/Redis connection settings for tests: `80 passed, 8 warnings in 2.00s`. Suites: profile display, public authorization, rejected snapshot, recovery exchange guard. The test dependencies and source overlay are confined to `/tmp/profile-status-341fdf2b496a40edb7202265c02cac2b`.
- [command output] Frontend rendering, row identity, and live-candidate regressions: `tests 6`, `pass 6`, `fail 0`.
- [command output] `npx tsc --noEmit` exit code zero; `npm run build` completed successfully.
- [read-only query] Candidate classifier ran against actual M5 persisted decision evidence in production. RSI values below the configured veto moved to Approved, expired evidence to Aguardando dados. No profile or financial records were changed.
- [read-only query] Every active L3 profile's entry-trigger conditions matched the effective runtime merge. Production `RECOVERY_OBSERVATION_ONLY` was confirmed enabled.

Release completion additionally requires canonical main source guard, terminal Railway/Vercel status, authenticated endpoint/UI verification, runtime order-block verification, and append-only deployment ledger evidence. Final provider IDs and captured runtime output are retained in the local release evidence.

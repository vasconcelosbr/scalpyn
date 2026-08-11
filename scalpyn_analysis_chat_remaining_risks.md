# Remaining Risks

1. **Child execution incomplete:** confirmation returns a typed limitation; no child run is created.
2. **Authenticated UI not interactively proven:** protected preview route and build are proven, browser behavior is not.
3. **Read-only breadth intentionally narrow:** initial allowlist contains only `market_regime.get_current`; a requested seven-day window is explicitly reported as not materialized.
4. **Proposal validation has no staging data:** both validators returned `NO_DATA`, so the second human gate remains pending and no candidate exists.
5. **Real provider not proven:** no provider transport, token usage or paid cost claim is made.
6. **Rate limiting:** endpoint bounds and one-active-message concurrency exist; integration with the canonical platform rate limiter remains open.
7. **Integration-test depth:** focused contract/systemic suites passed, but no new disposable PostgreSQL/Redis end-to-end suite was added.

# Final remaining risks

1. `REAL_PROVIDER_PROVEN` is absent: staging has no validated provider key, model approval or budget policy, and no priced call was authorized.
2. The four legacy bridges are statically adopted but remain `0/4` for runtime E2E proof because their real-provider gate is unavailable.
3. Three development credentials removed from `.replit` remain in Git history without external revocation proof.
4. The staging API logs `ENABLE_GATE_WS` disabled. This is expected for the isolated LangGraph environment but means Gate websocket ingestion is not part of this staging acceptance.
5. `npm audit` retains one low-severity indirect `esbuild` development-server advisory; high and critical counts are zero.
6. The backend suite reports five dependency/test-infrastructure warnings. Tests pass without skips, but the warnings remain tracked debt.
7. Production has not been mutated or revalidated by this remediation.

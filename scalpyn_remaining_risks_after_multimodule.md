# Remaining Risks After Multi-Module Production Deployment

1. Backend global gate: `68` failures and `12` errors in `1606` collected tests [query: `.codex-evidence/full-backend.xml`]. Focused multimodule tests passed, but the global gate prevents a fully green verdict.
2. Real provider: not run. Explicit provider, model, pricing and cost approval is still required before any authenticated canary.
3. Crash/resume: durable checkpoints and interrupt/resume are proven, but a real worker process kill/restart is `NÃO VERIFICADO`.
4. Authenticated production UI: public routes and API proxy health are proven; an authenticated browser run through the production Intelligence Runs UI was not executed.
5. Entrypoint coverage: only `shadow_portfolio` has end-to-end staging runtime proof; every legacy/module entrypoint was not invoked individually.
6. Execution-worker heartbeat: the dedicated LangGraph worker logged missed heartbeat observations for the existing execution worker at `02:07:49`, `02:09:49` and `02:11:54` [logs: Railway production]. The execution worker remained `SUCCESS` and active; it was not restarted because it handles real-trading work.
7. Source reconciliation: production is healthy from a manual upload, but the deployed commits are ahead of source-linked `origin/main`. A future automatic rebuild can repeat the migration-revision HTTP `502` incident until the commits are merged under a controlled release [deployment incident: Railway].
8. Alembic offline rendering: full-history `upgrade head --sql` fails while rendering immutable historical migration `148` JSONB literals [command evidence: `audit_evidence/db/alembic-upgrade-head.sql`]. Runtime migration through `150_multimodule_hardening` succeeded [query: production Alembic].
9. Frontend residual: `435` warnings with `0` lint errors [query: `.codex-evidence/tests/frontend-lint.txt`]. Dependency audit reports `1` low, `0` high and `0` critical advisories [query: `.codex-evidence/tests/npm-audit.json`].

No residual item authorizes live AI writes, model promotion, order placement, or mutation of Risk, Strategies, Spot, TP/SL or sizing configuration.

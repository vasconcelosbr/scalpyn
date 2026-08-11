# Rollout Plan

1. Complete child-run creation with its own dataset, bundle, model resolution, reservation, result and usage.
2. Add authenticated browser/Playwright proof and canonical rate limiting.
3. Re-run all local suites and staging fake canaries; require the full acceptance matrix.
4. Present one real-provider staging turn with provider/model, token estimate, hard cost cap and explicit approval. Do not retry a fail-closed call without fresh approval.
5. After a separate production approval, apply additive migration, deploy API/worker, deploy the `scalpyn` Vercel project, keep all chat flags false, and verify routes/health.
6. Enable chat for one tenant with frozen mode first. Read-only may follow; keep child/proposal disabled initially.
7. Reconcile messages, reservations, usage, orders, candidates and live-write ledgers before widening.

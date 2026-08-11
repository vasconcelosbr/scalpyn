# Rollback Plan

Trigger on cross-tenant access, wrong parent linkage, provider call without reservation, unexpected side effect, cost anomaly, stream loop, checkpoint corruption or secret exposure.

1. Disable the tenant-governed chat/stream/read-only/child/proposal flags.
2. Stop accepting new messages; safely terminalize or cancel active turns.
3. Restore the previous API, worker and frontend deployments.
4. Keep additive schema, conversations, evidence and checkpoints for audit.
5. Reconcile requests, jobs, runs, messages, reservations and usage.
6. Verify orders, trades, positions, profiles, candidates and ML promotions remain unchanged.

# Scalpyn production rollout plan

Status: `BLOCKED_PENDING_HUMAN_CHECKPOINT`.

Prerequisites:

1. Configure and validate one provider key for a synthetic staging tenant.
2. Create an expiring model approval and a bounded budget policy.
3. Present provider, catalog model, input estimate, maximum output, worst-case cost and reservation.
4. Receive explicit cost approval.
5. Prove one analysis-only real-provider canary with configured model equal to effective model, persisted tokens and persisted cost.
6. Prove the four legacy bridges at runtime.
7. Close the historical credential revocation residual risk.
8. Receive a second explicit production rollout approval.

Production order after approval:

1. Backup and restore proof.
2. Migration.
3. API.
4. Dedicated `ai_orchestration` worker.
5. Frontend project `scalpyn` and alias `scalpyn.vercel.app`.
6. Health, logs and authenticated UI proof.
7. Keep all feature flags false.
8. One analysis-only production canary.
9. Gradual read-only/shadow-only activation.
10. Safety reconciliation and monitoring.

Remain disabled: live trading, Auto-Pilot mutation, ML promotion, `LIVE_WRITE`, Spot mutation and real orders.

Rollback: disable entrypoints and module flags, stop the dedicated worker, restore the prior immutable API/frontend deployments, and reconcile orders, champions, profiles and configs before any re-enable.

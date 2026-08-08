# Authenticated UI evidence

Status: `PENDING_STAGING_DEPLOYMENT`.

Local build proves the `/intelligence-runs` route compiles and prerenders. The page uses the shared authenticated API client, shows tenant-scoped runs, execution trace, interrupts, approve/reject/edit controls and the explicit `live write: denied` envelope `[test/build]`.

An authenticated screenshot against isolated staging is still required. Local compilation alone is not user-facing production proof.

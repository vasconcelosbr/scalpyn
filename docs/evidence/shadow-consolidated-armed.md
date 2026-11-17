# Shadow Portfolio — L3 Consolidado / Aprovados

New simulated captures accept validated current ARMED and TRIGGERED observations from the consolidated profile display. The external assets endpoint independently returns TRIGGERED only. Configuration is `spot_engine.shadow.approved_source`, editable in strategy settings.

The membership asset belongs to the L3 profile's live L2 parent. Capture validates that parent, user and symbol, retaining the L3 profile/watchlist as trade lineage. Requiring that asset to belong to the L3 itself incorrectly discarded every eligible capture: normal L3 scans write decisions, not L3 asset rows. Capture now uses the actual parent asset and logs missing-parent or active-approved-trade exclusions.

`config_snapshot.l3_consolidated.entry_signal` freezes ARMED or TRIGGERED at entry. The list/detail API exposes `entry_signal`; the Portfolio column “Origem do sinal” renders Armado or Gatilho. Older consolidated snapshots may supply their frozen `mtf.status`; unrelated history stays unlabeled. No current market state is used to infer historical origin.

Historical rows retain their original source and economics. Open historical approved trades block new consolidated captures for the same tenant/symbol/direction. The shared final writer guard disables legacy approved capture after switching. Legacy outbox events receive terminal `SHADOW_SOURCE_REPLACED` disposition.

A PostgreSQL advisory transaction lock and unique observation/active-source indexes prevent duplicate captures. A consumed observation cannot be reused after closure. Eligibility is revalidated after obtaining the current Gate ask-depth quote. Profile/watchlist lineage and the observation analysis are frozen in the new trade. Exit rules remain the configured existing policy. The observation is never real-order authority, and the new source is excluded from automatic training/simulation ingestion.

Portfolio list, summary, executive report, detailed report and profile performance accept the selected origins. Default approved views combine the preserved history with Consolidado; row/detail/export identity distinguishes the origins. Canonical ranking settings used outside these views are unchanged.

## Verification

Commands were executed in the isolated implementation worktree. Literal command outputs:

```text
PostgreSQL capture/rollover/outbox suite:
7 passed in 4.88s
Focused capture, ARMED, quote, outbox, report, performance, continuation suite (before adding the terminal outbox/schema tests):
118 passed, 1 skipped in 7.74s
Additional canonical Shadow, TP, handler, measurement, monitor, lineage suite:
1 failed, 106 passed in 4.51s
Frontend origin-filter tests:
tests 2 / pass 2 / fail 0
Frontend: npx --no-install tsc --noEmit — exit 0
Frontend: npm run build — exit 0
Alembic:
249_shadow_consolidated_armed (head)
Production schema auditor before push:
alembic_version.version_num : 248_mtf_history
critical columns            : 43/43 present
OK — no drift detected.  Safe to push.
```

The additional-suite failure is the pre-existing source-text assertion `test_on_demand_l3_decisions_persist_profile_lineage`, which cannot locate `_build_on_demand_l3_decision_metrics` in the expected window. Both that test file and `watchlists.py` match the base commit. No behavior or assertion was weakened.

Migration replay was exercised against an isolated local PostgreSQL schema. Production remains unchanged until the clean-main release and guarded source switch.

| Reported values | Origin | Literal evidence |
| --- | --- | --- |
| Test outcomes and migration heads above | [query: command] | stdout blocks above |
| Production schema presence above | [query: audit_prod_schema] | stdout block above |

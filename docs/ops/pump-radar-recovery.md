# PUMP radar recovery contract

This repair applies to radar-enabled spot pools. Generic discovery and POOLSPOT
retain their existing policy. Trading thresholds, profiles, exit rules and
monitor quotas are unchanged.

| Feed state | Open Shadow | Collection | Watchlists | New Shadow |
| --- | --- | --- | --- | --- |
| Present in usable feed | No | Active | Normal evaluation | Existing entry rules |
| Absent, last sighting within `radar_min_hold_seconds` | Either | Active, not held | Normal evaluation (open Shadow does not hide it) | Existing duplicate guard (`ACTIVE_TRADE_ALREADY_EXISTS`) |
| Absent from usable feed | Yes, any source | Active, held | Hidden at every level | Blocked |
| Returns to usable feed | Yes | Active, no longer held | Normal evaluation, including rejection | Existing duplicate guard |
| Absent from usable feed | No | Pool membership removed | Hidden | Blocked |
| Feed unavailable | Either | Preserve known radar membership and open trades | Hidden | Blocked |

`radar_feed_health` in pool overrides is worker-owned. Missing health fails
closed until the first sync. Incomplete pages, malformed responses and request
failures are not valid empty selections. A successful, complete `minute-signals`
response for Gate spot defines the current selection, including an empty list.
Its shared `market_data_enabled` and `coverage_status` envelope fields do not
govern membership: the provider was observed publishing a fresh `ACTIVE` minute
signal while those fields read `false` and `PARTIAL`. Requiring enabled collection
or full market coverage incorrectly suppressed that signal. The consumer uses
`data` for membership and requires `has_more=false` for complete pagination.

Reconciliation locks the pool, prefers an existing radar row when merging
duplicate symbols, preserves already-granted operator permissions, and keeps
all symbols with open Shadows. Invalid non-radar residue without open Shadows
can be removed independently of feed availability. Shadow IDs, history and
profile configuration are never edited. All eligibility reads are scoped to
the tenant and market, and final Shadow creation serializes with the same pool
lock to reject stale candidates.

Before publication, save a read-only per-symbol preview including pool row IDs,
origins, open Shadow IDs and proposed action. After publication compare it with
persisted membership, continued indicator collection, monitor progression and
authenticated watchlist output. A healthy empty response and provider outage
have different reconciliation behavior.

The monitor's optional ATR query uses a typed timestamp cutoff and an inner
savepoint. Its fast scan captures the Shadow ID before a rollback expires ORM
attributes. The EMA editor only removes period/parameters when the feature
producer explicitly encodes its calculation identity in the indicator name.

Focused tests include `test_radar_auto_discover.py`,
`test_pipeline_scan_held_position_visibility.py`, `test_radar_feed_health.py`,
`test_shadow_monitor_postgres_recovery.py`, `test_pump_ema9_block_boundary.py`
and the frontend condition/catalog suites. PostgreSQL tests require an isolated
loopback database named `scalpyn_monitor_test`; they create disposable schemas.

Production release requires canonical main-source parity, terminal provider
status, runtime evidence and authenticated UI verification under the deployment
source guard. No database migration or rollback is part of this repair.

## Minimum hold (2026-10-08)

Minute-signals stay in the feed for 1–3 minutes, while the POOL/L1/L2 snapshots
are refreshed by a 300 s scan that runs for 169 s+. Without the pre-#220 grace,
PUMP signals were removed from every layer before reaching L3 (the REALTIME
chain was unaffected: `radar_enabled=false` and a 300 s Pump Monitor hold).
`radar_min_hold_seconds` (pool override, edited in the pool page, default 300,
range 0–3600, 0 = strict) keeps a radar member a candidate until its last
sighting is older than the window, with or without an open Shadow. Absence never
refreshes `radar_last_seen_at`; outages, exclusions and the Pump Monitor feed are
unchanged. A second Shadow for a running symbol is still refused by the
consolidation rule, and the L3 Consolidado re-presents the existing Shadow.

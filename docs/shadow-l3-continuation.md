# Shadow L3 continuation

The independent `shadow_l3_exit_policy` config is edited in Global Risk Configuration.
It defaults to OBSERVE with economic parameters unset. No example thresholds are seeded.
The existing global risk toggle still controls its original consumer. The spot-engine
trailing snapshot still controls pre-TP protection.

## Release and activation

1. Apply additive migration `219_shadow_l3_continuation`; verify the evidence tables.
2. Publish API (public-trades handler), structural/compute/execution consumers, research
   candle collector and beat from the same canonical commit, then the frontend.
3. Save the default observation config through the authenticated configuration API.
   Verify an independently loaded config and newly created L3 frozen snapshots.
4. Verify raw public-trade ingestion, deduplication, decision envelopes, UI refresh,
   absence of real-order effects and unchanged historical shadow rows.
5. Collect the configured prospective observation horizon, including after the baseline
   and candidate exits. A baseline exit must never censor the comparison horizon.
6. Export evidence and replay complete candidate configurations. Choose a chronological
   development/validation boundary before selecting a candidate. Trades crossing the
   boundary are excluded. Missing horizons, fees or evidence remain missing, not zero.
7. Review support, net outcomes, giveback, gap fills, premature exits, holding times
   and missing-data cases. Reports preserve their empirical status.
8. Per the subsequent operator instruction, a complete policy may be saved as APPLY
   immediately through the authenticated config API. Calibration is not a prerequisite.
   The config audit log records the actor and exact values. No PASS report is fabricated.

No strategy calibration or production activation is implied by software tests. Synthetic
test parameters are not suggested trading settings. Sample-size and acceptance criteria
must be registered with the calibration report before candidate selection.

## Temporal semantics

- Delta is buy base volume minus sell base volume. Taker ratio uses buy/total.
- Normalized Delta uses (buy-sell)/total. Normalized CVD slope uses the signed sum
  over the CVD window divided by executed volume in that window. They are correlated
  flow checks, not independently weighted votes. CVD level sums deduplicated trades
  since entry, not rolling-window deltas.
- Public trades retain exchange, market, symbol, exchange trade id, occurrence time,
  application receipt time, persistence time, side, base volume and unit.
- Each decision saves nonoverlapping minute buckets and the candle/structure inputs.
  Replay respects original receipt times. Missing data never becomes a bearish signal.
- A TP touch can be extended only by persistent evidence already available at that
  candle's open. Current-candle evidence cannot justify its own earlier TP touch.
- Previously effective floors are checked first. A new floor is effective no earlier
  than its evidence availability. Gaps fill at the observed open, not at the nominal floor.
- Weakness exits require persistent deterioration AND confirmed loss of a higher low.
  The pivot requires its right-hand candles to have closed and arrived.
- Signal exits are queued for the first subsequent observable open. They do not fill
  at a past close or a newly calculated floor. Missing next-open evidence stays pending.
- A floor can only rise. Weakness selects the tighter ATR distance; recovery cannot
  loosen protection. The operational horizon bounds observation, not profit targets.
- APPLY shadows are excluded from fixed-barrier training at creation and finalization,
  including TP/SL exits, and are not copied into fixed-barrier trade simulations.

## Evidence and recovery

Public-trade frames for watched L3 symbols enter a Redis stream with no lossy trim.
Workers acknowledge only after PostgreSQL commits. The unique exchange trade identity
makes a crash after commit and before acknowledgement safe. No candle-based CVD fallback.
Row locks serialize state advancement. Candle timestamps and decision primary keys make
retries idempotent. Closed-candle collection dispatches evaluation; beat provides recovery.
Per-policy evaluation cadence and bounded batches keep observation off live-order queues.
Raw retention preserves unfinished horizons; decision envelopes remain append-only.

Read-only tools (from backend, DATABASE_PUBLIC_URL supplied securely):

```
python -m scripts.shadow_l3_replay export --user-id USER_UUID --output evidence.json
python -m scripts.shadow_l3_replay replay --input evidence.json --policy candidate.json --split-at ISO_UTC --output report.json
```

The UI exposes candidate state distinctly from actual outcome in OBSERVE. Portfolio
list and summary refresh together, reject stale responses, and use persisted exit price
for closed trades. API responses include the policy version/hash and evaluation time.

Rollback: select LEGACY for new admissions. Keep this version's evaluator and tables
deployed for already enrolled trades. The migration intentionally refuses destructive
downgrade; there is no historical relabeling or snapshot rewrite.

## Readiness of this implementation

Local evaluator, PostgreSQL persistence, restart, concurrent advancement, configuration,
legacy trailing and profile-merge regressions pass. The frontend production build passes.
These checks do not establish production deployment or authenticated UI acceptance.

The operator subsequently authorized publication despite the pre-existing L3 contract
finding and requested immediate application. The finding remains documented separately;
it has not been repaired by this feature. Economic parameters must still be complete,
and temporal/data-quality checks and pre-existing trade snapshots remain enforced.

## Initial operator-authorized candidate

The operator requested immediate application and authorized preparation of an initial
shadow configuration. `shadow-l3-initial-policy.json` is that complete candidate. It is
NOT_CALIBRATED, not an empirically validated optimum, and is not a schema default.
Deployment does not modify historical/open snapshots. The JSON is saved through the
config service for the authenticated operator after publication and verified independently.
All economic values remain editable in Global Risk Configuration.

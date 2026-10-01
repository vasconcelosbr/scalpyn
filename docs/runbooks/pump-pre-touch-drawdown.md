# Pump pre-touch adverse excursion

## Frozen label contract

`pump_gross_touch_v4` adds `pre_touch_policy` with value
`reference_to_first_touch_strict_timestamp_v1`. It requires the existing exact
trade resolution and endpoint policy. The specification hash changes. Neither
the opportunity schema nor the scoring, risk, target, horizon or cost policies
change. Existing v2/v3 observations continue using their frozen specifications;
the labeler does not add these fields to old contracts. No historical backfill,
queue reconstruction, migration or additional market collection is needed.

Each target has its own `pre_touch` JSON object. Its signed
`mae_before_touch_pct` is the minimum return relative to the immutable decision
reference strictly before the first observed touch timestamp, including the
reference itself (zero). `drawdown_before_touch_pct` is its nonnegative
magnitude. This is an adverse excursion from the reference, not a peak-to-trough
drawdown. The existing top-level `mae_pct` still describes the complete horizon.
Later losses cannot change the earlier target's excursion.

The target must have an observed touch and certified coverage through it. Gaps
before or at its timestamp produce unknown, never zero. Gaps after it can leave
the prefix known while whole-horizon MAE stays unknown. A lower-than-target
trade sharing the touch timestamp makes prefix ordering unknown: sorting by
trade ID does not establish execution sequence. Every such metric records
`order_ambiguous` and a reason. No touch in a complete window is `not_reached`;
no observed touch with gaps is unknown. A pending horizon has no calculated
metric. These meanings are displayed independently for both gross targets.

## Resource and release sequence

The calculation uses the same already bounded raw archive and one pass per
target through its price prefix. No Gate requests, DB reads, new queue, new
worker, schedule or resource limits are introduced. The existing atomic insert
persists the additive JSON fields. More output bytes are expected; CPU-only
benchmarks are insufficient to establish the four-second transaction budget.
Run the full TEMP PostgreSQL fixture and retain timeouts as failed evidence.

Before publishing, establish capacity of the current release, record the
pre-release observation cutoff, compare resources and test the new transaction
cost. The unique monitor must retain outage/failure evidence; never reset
backlog, delete pending pairs or combine interrupted runs into a stability pass.
Merge through the normal protected PR/CI path and build only canonical clean
main. Deploy code with V3 configuration still active. Confirm all consuming
services' terminal provider state and runtime before activating V4 for future
observations. Update the separate daily ML image from the same verified source
without executing another daily training run or changing its budget/cron.

Activate only the Pump label version/policy through the existing audited
ConfigService path, retaining all other configuration. Record activation time,
old/new label specification hashes and the first persisted V4 observation.
Pending V3 pairs remain eligible under V3; report producer contracts and mature
cohorts separately. Continue the same aggregate queue monitor after activation,
and separately verify persisted V4 fields, gaps and throughput. A before/after
restart or contract boundary must not be described as uninterrupted stability.
Authenticated feature QA remains required for frontend acceptance.

Rollback is a audited configuration pointer change to the prior full frozen
label specification, using fresh current configuration for the other fields.
Remove the V4-only policy key rather than retaining a null key with a different
hash. Keep V4 observations/labels and allow their pending horizons to drain
under the deployed compatible consumer. No history or raw archive is deleted.
Avoid rolling the consumer back to code that does not support V4 while its
queue remains pending. ML inference, contribution and Pool connection stay off.

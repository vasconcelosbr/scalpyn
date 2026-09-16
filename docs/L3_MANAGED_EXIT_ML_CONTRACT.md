# Prospective L3 managed-exit ML target

`l3_managed_net_v1` learns whether the realized return under the frozen L3 exit policy exceeds the explicitly configured round-trip fees and slippage. This contract does not change the trading policy, authorize an order, approve a model, or enable training.

## Activation

The optional `ml.ml_l3_managed_exit` object requires all of: version, policy_hash, trailing_hash, fee_roundtrip_pct, slippage_roundtrip_pct, max_holding_seconds, barrier_contract_version. No cost is inferred. Absence keeps the existing target. Invalid or partial definitions are rejected by config validation. The object is available in the Strategies settings projection and its import/export flow.

Apply through the audited configuration service, preserving other keys, invalidating cache and reading back from a fresh process. Before activation, compare hashes with the current APPLY policy and trailing configuration, fees with the existing ML fee, and barrier version with the active contract. Deployment alone does not activate this target.

## Population and evidence

Only newly created source=L3 captures can freeze this identity. They require exact feature lineage, a matching profile execution contract and ALLOW authorization. They remain ineligible while awaiting an outcome. Historical and other-lane records are never relabelled, and legacy training explicitly excludes managed captures.

At closure, the finalizer replays the persisted one-minute decision sequence against the frozen policy. Missing candles, ambiguous entry-boundary barrier touches, invalid availability, changed state, incorrect returns and unproven closure exclude the sample. Warmup is price-only as specified by the policy; subsequent decisions require VALID flow evidence. Supported outcomes are TP_HIT, SL_HIT, TIMEOUT, TRAILING_STOP and FLOW_STRUCTURE_EXIT. The exit price and ordering remain those of the existing evaluator.

The binary label is `gross_return_pct - fee_roundtrip_pct - slippage_roundtrip_pct > 0`. Results outside the configured holding horizon are censored from ML without forcing a position to close. Intrinsic closure certification does not bypass measurement: the trainer and readiness still require the latest measurement at the query cutoff to be READY/OK. Maturity is `max(entry + horizon, label_available_at) + embargo`. Purging includes label availability, and temporal/event splits and promotion gates remain enforced.

Artifacts and registry definitions include this identity. Inference rejects a selected artifact with a different policy, trailing identity or economic contract. Cost or policy changes create a separate population; they do not make an old model valid for the new target.

## Acceptance and rollback

Require source parity, terminal provider deployment states, authenticated readiness display and a natural future capture through authorization, frozen identity, persisted exit replay, measurement, maturity and trainer selection. Synthetic unit scenarios are not production capture evidence. Keep training and promotion disabled pending sufficient certified data and separate approval.

Removing the optional configuration through the audited service stops freezing the new target on subsequent captures. Already frozen captures retain their immutable target; the legacy loader excludes them. Do not rewrite historical rows or weaken eligibility to complete acceptance.

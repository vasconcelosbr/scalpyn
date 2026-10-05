# Pump directional research

Pump ML now asks whether the known endpoint price is above or below the captured
reference, conditioned on the configured point-in-time indicators. It no longer
uses a fixed favorable-touch target as the scheduled research objective. Existing
touch labels and raw archives remain intact for historical exploration.

## Target and compatibility

`pump_endpoint_direction_v1` derives the binary outcome from each archived
`endpoint_return_pct`: strictly positive is up; strictly negative is down; exact
zero is excluded from binary fitting and separately counted as flat in the UI.
Unknown, incomplete, nonnumeric or missing endpoint outcomes are excluded. The
reference must be a positive numeric `gate_best_ask_v1` price. Direction is gross
movement relative to this ask, not realized profit, mid-price trend or a trailing
exit outcome. Bid/ask effects can contribute to small negative movements.

The directional event is conditional on a known, nonzero endpoint. Future
extensions to a neutral class or a different price benchmark require a versioned
target contract; no arbitrary gain threshold is introduced here. Horizons follow
the existing configured archive horizons rather than a user-selected gain/time
example. Endpoint age/coverage rules remain those of each frozen label contract.

Certified listing epoch, feature dictionary, producer, label and cost hashes stay
strict compatibility boundaries. The selector uses a read-only repeatable-read
snapshot and bounds observations and labels by that snapshot. Missing numeric
features are never filled with zero. Numeric reference validation applies again
before fitting. Legacy touch readers also exclude labels whose status is unknown.

## Sampling, fitting and evaluation

The daily singleton shares its existing total row, runtime, CPU and artifact
budgets across the configured horizons, with sequential subprocess fits. Each
horizon receives a temporal quota; sparse buckets are left sparse. Query batch
size is independent of sample membership. The process-wide container timeout
remains in force and each fit subprocess has the remaining shared deadline.

Four chronological cohorts separate training, validation, calibration and test.
Purge/embargo and crossing-episode exclusion remain conservative. Both directions
must exist in every cohort; unsupported horizons record a blocked reason instead
of producing a model. Each episode receives equal total fitting/calibration weight.
XGBoost learns joint feature interactions; calibration fits clipped model logits
using only the calibration cohort.

Test output includes row-weighted and episode-weighted accuracy, AUC, average
precision, Brier and log loss, actual reliability frequencies, support and paired
episode bootstrap loss intervals. Baselines include fixed train prevalence,
calibration prevalence and the fixed always-up/down rules. This prevents a class
shift or an always-down model from being mistaken for useful indicator ranking.
Intervals are conditional on the tested period and do not establish independence
across market regimes. Test results never tune parameters or promote a model.

Research options are returned in Pump configuration and editable through its
existing JSON configuration UI. Defaults retain the existing core numeric feature
set and bounded model capacity. They are provisional engineering settings, not
validated trading/admission thresholds. The existing feature dictionary constrains
changes to `research.features`.

## Artifacts and per-asset inference

Native artifacts use `pump_directional/<experiment-hash>`, isolated from legacy
touch artifacts and all Shadow resources. The manifest freezes the directional
event, horizon, features, preprocessing, selection, temporal cuts and evaluation
options. Native inference checks numeric inputs and training feature ranges,
loads the matching logit calibrator and reports conditional model contributions.
Those contributions are explanations of the joint model, not independent
confirmations or causal indicator effects. Range checks alone cannot certify
support for every new combination.

The offline preview explicitly separates research estimates from the effective
direction, score and probability: effective fields stay null and applied delta
stays zero. Public per-asset responses abstain without an independently validated
directional model, including stale-feed handling. The radar displays “Sem nota
validada”; descriptive tables separately display up/down/flat/unknown counts and
identify their sampled scope. A frequency or research ordinal score is not a
validated predictive probability. No activation path or auto-promotion is added.

## Verification and rollout

Focused tests cover endpoint vs touch semantics, zero/unknown outcomes, invalid
references, crossing episodes, weighting, native fit/serialization/inference,
baselines, absten­tion and range checks. The Pump CI job installs the worker's
pinned ML dependencies for this native artifact round-trip.

Before production rollout, review retrospective metrics and independent-period
limitations, storage headroom, canonical source and authenticated UI evidence.
Do not increase the logical storage cap alone when physical headroom is small.
No Shadow, live admission, order execution, sale policy, historical deletion,
new credentials or paid resource changes are part of this implementation.

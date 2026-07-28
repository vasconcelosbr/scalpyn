# Profile Bayesian Intelligence — Statistical Model

## Version 2 estimand

`analysis_only_v2` estimates one coherent quantity: the standardized
incremental lift in net expected return above the configured practical ROPE.
The recorded `net_return_pct` already includes the row-level round-trip fee;
the ROPE represents the additional materiality margin required by policy.

The decomposition is:

1. hierarchical multinomial outcome (`SL_HIT`, `TIMEOUT`, `TP_HIT`), with
   `SL_HIT` as the reference class;
2. Student-t net-return magnitude conditional on outcome;
3. net EV reconstructed as `sum(p_outcome * E[net_return | outcome])`.

The model includes standardized entry indicators and non-centered random
effects for profile, symbol, regime, source, and temporal block. Groups with a
single observed level are omitted rather than weakly identified. Outcome
residual geometry is shared so a sparse `TIMEOUT` class cannot create an
independent scale funnel.

## Pre-fit gates

Before PyMC sampling, the worker:

- builds discovery/validation/holdout boundaries from timestamps, not row
  indices;
- derives embargo as the maximum target horizon plus maximum feature lookback;
- computes a one-sided minimum detectable net-EV lift at the policy probability;
- checks sample, outcome, symbol/day concentration, and effective symbol/day
  counts;
- measures feature coverage by entry day × entry ATR bucket;
- measures association between feature missingness and outcome;
- retains only features that pass the same quality policy in discovery and
  validation;
- keeps the final holdout sealed.

`atr_pct_at_entry` is sourced from its dedicated immutable entry column and is
the authoritative ATR control. `btc_change_1h_pct` is offered as an entry-time
market control but is excluded, with evidence, when it fails coverage policy.
Temporal-block effects remain mandatory.

## Priors and predictive checks

All group effects are non-centered. PnL priors use narrower scales appropriate
to percentage returns. Prior predictive PnL must remain inside the configured
absolute plausibility bound before posterior sampling starts; the worker does
not relax that bound to make a run pass.

Both outcome and conditional-PnL models persist prior predictive, posterior,
log-likelihood, and posterior predictive groups. ArviZ records:

- maximum R-hat;
- minimum bulk/tail MCMC effective sample size;
- divergence count;
- worst-R-hat and lowest-ESS parameters;
- posterior predictive comparison;
- prior predictive status;
- credible intervals and warnings.

Only `VALID` and `VALID_WITH_WARNINGS` can produce associations. Discovery and
validation are fitted independently, and stable-window grading requires
directional agreement. The final holdout is not used for fitting or grading.

## Evidence language

Outputs describe association, uncertainty, MDE, net-EV lift, and practical
equivalence. They do not claim causality or guaranteed profit. The analysis has
no authority to train ML, mutate an active profile, affect trading, create a
candidate, or promote a model.

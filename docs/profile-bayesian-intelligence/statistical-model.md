# Profile Bayesian Intelligence — Statistical Model

## Models

Two separate hierarchical models are implemented:

1. Bernoulli-logit probability of TP.
2. Student-t regression of net PnL after costs when available.

Both models support global, profile, symbol, and regime effects plus
standardized entry indicators. Group effects use partial pooling. PyMC is
imported lazily only inside the analysis worker.

## Missingness and scaling

The matrix preparation step:

- applies a persisted minimum-coverage policy;
- removes constants;
- standardizes finite values;
- centers missing values after standardization;
- adds an explicit missingness feature;
- never imputes a missing raw indicator to zero.

## Diagnostics

ArviZ produces:

- maximum R-hat;
- minimum bulk/tail effective sample size;
- divergence count;
- posterior predictive comparison;
- credible intervals;
- warnings and convergence status.

Permitted statuses are `VALID`, `VALID_WITH_WARNINGS`,
`INSUFFICIENT_EVIDENCE`, `NOT_CONVERGED`, and `FAILED`. Only the first two
can produce recommendations, and warning-grade runs are penalized during
evidence grading.

## Evidence language

Outputs describe association, probability, uncertainty, and interval estimates.
They do not claim causality or guaranteed profit.

Each indicator result separates TP association, PnL association, posterior
probability, credible interval, direct sample count, shared sample count,
effective sample size, evidence grade, diagnostic status, and recommendation.

# Profile Bayesian Intelligence — Optimization

## Search-space contract

The search space is derived from the current profile configuration and the
persisted `authorized_search_space` policy. Every path must resolve to a numeric
current value. Its range is the intersection of:

- the policy's absolute minimum and maximum;
- the policy's maximum absolute delta around the current value.

Unbounded, unresolved, non-numeric, or unauthorized parameters fail closed.

## Objective

The implementation retains every objective component and penalty:

- net OOS expectancy;
- OOS profit factor;
- temporal stability;
- symbol diversity;
- regime consistency;
- SL rate;
- drawdown;
- concentration;
- IS-to-OOS degradation;
- configuration complexity;
- trial-volume penalty.

Weights are loaded from `config_profiles`; they are not embedded as trading
thresholds in code.

## Hard constraints

Trials record missing metrics and violations explicitly. A trial is invalid if
it fails any configured trade, symbol, day, concentration, drawdown, expectancy,
profit-factor, degradation, regime, or operational-policy gate.

## Current rollout limitation

The repository does not yet contain a trustworthy general profile replay engine.
The visible backoffice replay endpoint is a stub. Therefore optimization studies
are persisted for audit but fail closed with
`existing_profile_replay_engine_is_stub`; no fabricated trial metrics or Pareto
front are generated.

The pure Optuna/TPE runner is implemented and can be connected when
`ProfileReplayAdapter` receives a reproducible, historical, side-effect-free
engine.

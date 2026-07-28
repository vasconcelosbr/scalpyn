# Profile Bayesian Intelligence — Operations

## Feature flags

All flags default to false:

```text
PROFILE_BAYESIAN_ENABLED
PROFILE_BAYESIAN_ANALYSIS_ENABLED
PROFILE_BAYESIAN_OPTIMIZATION_ENABLED
PROFILE_BAYESIAN_CANDIDATE_CREATION_ENABLED
PROFILE_BAYESIAN_SHADOW_SUBMISSION_ENABLED
PROFILE_BAYESIAN_AUTO_PROMOTION_ENABLED
```

Automatic promotion remains false regardless of the environment.

## Worker isolation

Use dedicated services that consume only:

```text
profile_bayesian
profile_optimization
```

Install `requirements-profile-bayesian.txt` only in those services. Do not add
PyMC or ArviZ to the API, trading, scanner, collection, or ML-serving workers.
Build the dedicated service with `Dockerfile.profile-bayesian`. The default
`PROFILE_BAYESIAN_WORKER_QUEUES=profile_bayesian,profile_optimization` consumes
both analytical queues with one process. Any override is validated so it cannot
subscribe to a trading queue. The image starts only Celery: it does not run API,
beat, migrations, scanners, or trading tasks.

The image starts `profile_bayesian_celery_app`, which imports only the Bayesian
task module. Its requirements intentionally exclude the generic trading and ML
runtime (XGBoost, LightGBM, CatBoost, Gate.io and MLflow), keeping the
scientific worker independent and inside the production builder budget.

The API creates an idempotent run and dispatches it. PostgreSQL advisory locks
and unique keys prevent concurrent duplicate processing. Failures update the run
to `FAILED`, release transaction-level locks, and are returned as data; they are
not propagated into trading tasks.

## Policy

Create an active `config_profiles` row with `config_type=profile_bayesian`.
The policy must explicitly provide every limit, sampler setting, evidence gate,
authorized search dimension, objective weight, temporal split, and permission.
An incomplete policy returns `PROFILE_BAYESIAN_POLICY_MISSING`.

The Profile Intelligence UI exposes a guarded activation button. It imports the
versioned `analysis_only_v1` template, validates the complete nested policy, and
persists it through `ConfigService`, including a `config_audit_log` entry. The
runtime never reads implicit defaults: after activation it reads the persisted
per-user `config_profiles.config_json`.

The same UI can import or edit the JSON policy. The backend rejects incomplete
objects, invalid ranges, unsafe permissions, non-empty optimization search
spaces, and non-zero optimization/candidate limits while the policy mode is
`analysis_only`.

The activation preset permits only:

```text
profile_bayesian.view
profile_bayesian.run_analysis
```

It explicitly denies optimization, candidate creation, replay, shadow
submission, approval, ML training, profile mutation, trading decisions, and
automatic activation.

## Progressive activation

1. Deploy code and migration with flags false.
2. Configure policy and verify `/api/profile-intelligence/bayesian/status`.
3. Enable only the module flag and inspect read-only status.
4. Enable analysis for one test profile and validate dataset hashes.
5. Validate convergence and provider-free scientific runtime.
6. Keep optimization and candidate flags false until replay is implemented.
7. Enable draft candidate creation with a bounded test scope.
8. Enable manual shadow submission only after replay evidence.

No step enables automatic promotion.

## Observability

Metrics include analysis totals, outcomes, durations, sampling time,
divergences, non-convergence, studies, trials, valid trials, failed trials,
generated candidates, and rejected candidates.

Structured logs carry analysis run, study, candidate, profile, dataset, and task
identifiers where applicable.

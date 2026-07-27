# Profile Bayesian Intelligence — Non-Interference Evidence

## ML and trading components identified

The audit identified the following protected areas:

- `backend/app/ml/feature_extractor.py`;
- `backend/app/ml/trainer.py`;
- `backend/app/ml/prediction_service.py`;
- `backend/app/ml/model_loader.py`;
- `backend/app/ml/gcs_model_loader.py`;
- `backend/app/ml/promotion_gate.py`;
- `backend/app/ml/intelligence_gate.py`;
- `backend/app/tasks/pipeline_scan.py`;
- `backend/app/services/decision_orchestrator.py`;
- `backend/app/services/ml_challenger_service.py`;
- ML model registry, evidence, feature-contract, dataset, artifact, gate, serving,
  and promotion paths;
- order, execution, scanner, collection, and anti-liquidation paths.

## Files preserved

No protected ML/trading file above is modified by this implementation. Baseline
and post-change SHA-256 hashes are compared during validation.

The only shared runtime files changed are:

- API router registration in `backend/app/main.py`;
- Celery task registration/routing in `backend/app/tasks/celery_app.py`;
- the existing Profile Intelligence page to add a new tab.

No existing task route is replaced.

## Table boundary

The module reads `shadow_trades` and profile lineage. It writes only:

- `profile_bayesian_*`;
- `profile_optimization_*`;
- an existing shadow candidate through the existing adapter, and only after the
  candidate is replay-validated and the shadow flag is enabled.

It does not write Bayesian outputs to ML feature, dataset, label, registry,
artifact, prediction, gate, or model tables.

## Import boundary

The new package has no imports from `app.ml`. PyMC and ArviZ are lazy imports
inside model/diagnostic functions. The API can start without either dependency.

## Behavioral guarantees

- no L1/L3 prediction call;
- no feature-order or feature-count mutation;
- no model artifact or registry mutation;
- no training, promotion, or schedule trigger;
- no real-time score or gate participation;
- no active profile mutation;
- no order creation;
- no automatic activation.

The replay adapter explicitly refuses the repository's stub and cannot mark a
candidate validated.

## Regression evidence required before rollout

- single Alembic head and clean history;
- migration upgrade/downgrade against an isolated PostgreSQL database;
- unit tests for contracts, hashes, grading, constraints, temporal splits,
  state guards, idempotency, and dependency isolation;
- existing Celery routing invariant tests;
- existing ML prediction/feature-contract regression tests;
- frontend typecheck, tests, and production build;
- identical protected-file hashes before and after;
- live flags verified false after inactive deploy.

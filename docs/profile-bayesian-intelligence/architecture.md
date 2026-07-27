# Profile Bayesian Intelligence — Architecture

## Architectural audit

The existing Profile Intelligence domain is distributed across:

- `backend/app/api/profile_intelligence.py` and
  `backend/app/api/profile_intelligence_live.py`;
- `backend/app/services/profile_intelligence_*`;
- `backend/app/models/profile_intelligence.py` and
  `backend/app/models/profile_intelligence_autopilot.py`;
- `backend/app/api/calibration_evolution_v2.py` and
  `backend/app/services/calibration_orchestrator_v2.py`;
- `frontend/app/profile-intelligence/page.tsx`.

The existing safe candidate path creates an immutable, shadow-only profile and
an isolated L3 watchlist through
`ProfileIntelligenceAutopilotService.create_candidate_from_calibration_proposal`.
It preserves the incumbent and requires human approval before any activation.

The repository's general endpoint `POST /api/backoffice/replay/run` is a stub.
It is not used as validation evidence. `ProfileReplayAdapter` returns
`REPLAY_FAILED` with `existing_profile_replay_engine_is_stub` until a real,
historical, state-free profile replay contract exists.

## Components reused

- JWT authentication and user scoping.
- `config_profiles` and `ConfigService` for GUI-editable policy.
- Profile and profile-version lineage.
- Existing Profile Intelligence autopilot shadow-candidate workflow.
- Existing Profile Intelligence audit conventions.
- Celery dispatch deduplication.
- Redis broker and PostgreSQL persistence.
- Existing `/profile-intelligence` operator surface.

## Components added

```text
app/profile_bayesian/
├── point-in-time data contract and dataset builder
├── lazy PyMC hierarchical models
├── ArviZ diagnostics and posterior analysis
├── evidence grading
├── bounded Optuna utilities
├── temporal and overfit validation
├── fail-closed replay adapter
├── existing-candidate adapter
└── audit and observability
```

The API and Celery task modules are thin adapters. Statistical dependencies are
listed only in `requirements-profile-bayesian.txt`, for dedicated workers.

## Authority boundary

| Authority | Value |
|---|---|
| Analysis | allowed |
| Recommendation | allowed |
| Candidate creation | feature-flagged |
| Active profile mutation | denied |
| Trading decision | denied |
| Order execution | denied |
| ML training | denied |
| ML promotion | denied |
| Automatic activation | denied |

`PROFILE_BAYESIAN_AUTO_PROMOTION_ENABLED` is deliberately ignored by the code
and always resolves to false.

## Incremental implementation plan

1. Ship schema and code with every flag disabled.
2. Configure a complete `profile_bayesian` policy in `config_profiles`.
3. Enable module read-only status and dataset construction for a test profile.
4. Install optional scientific dependencies only in a dedicated analysis worker.
5. Enable controlled analysis and validate diagnostics.
6. Keep optimization blocked until the replay adapter is backed by a trusted engine.
7. Enable candidate drafts only after analysis evidence is validated.
8. Enable manual shadow submission only after replay integration is complete.

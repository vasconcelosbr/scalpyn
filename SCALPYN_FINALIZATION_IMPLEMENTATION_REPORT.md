# Scalpyn final LangGraph remediation report

Overall verdict: `PARTIALLY_COMPLETE_PROVIDER_NOT_PROVEN`.

## Completed

- Clean isolated worktree and immutable remediation commits. `[E-GIT][CODE_PROVEN]`
- Backend global suite: `1614 passed`, `0 failed`, `0 errors`, `0 skipped`. `[E-TEST][TEST_PROVEN]`
- Initial `83` nonpassing cases classified and closed. `[E-TEST][TEST_PROVEN]`
- Provider boundary centralized; direct domain-provider calls outside the control plane: `0`. `[E-CODE][TEST_PROVEN]`
- Alembic single head, offline SQL generation and fresh restore proof. `[E-DB][SCHEMA_PROVEN]`
- Dedicated staging API and worker deployed successfully from backend revision `3b7b600`. `[E-DEPLOY][STAGING_RUNTIME_PROVEN]`
- Real worker restart followed by durable checkpoint resume. `[E-LOG][STAGING_RUNTIME_PROVEN]`
- Seven origin modules completed E2E with no duplicate event keys and no orders. `[E-CANARY][STAGING_RUNTIME_PROVEN]`
- Regenerative runs A/B/C completed; B reused contextual memory and C did not cross context. `[E-CANARY][STAGING_RUNTIME_PROVEN]`
- Protected Vercel preview passed authenticated UI, human-gate, duplicate-submit and cross-tenant tests. `[E-UI][AUTHENTICATED_UI_PROVEN]`
- Spot remains fail-closed with `AI_SPOT_AUTHORITY_BLOCKED`. `[E-CODE][CODE_PROVEN]`
- Frontend: `23` tests passed, typecheck passed, lint errors `0`, warning delta `0`, npm high/critical `0`. `[E-TEST][TEST_PROVEN]`
- Browser sessions, Vercel local environment files and generated build output were cleaned after evidence capture. `[E-SEC][TEST_PROVEN]`

## Open blockers

- Real provider canary: not run. Provider, model, token estimate and cost remain `NÃO DISPONÍVEL` until a key, approval and budget policy exist and the operator approves cost. `[NOT_PROVEN]`
- Four legacy bridges: static adoption `4/4`; runtime E2E `0/4`. `[E-CODE][TEST_PROVEN]` and `[NOT_PROVEN]`
- Credential incident: mitigated, but historical `.replit` secrets lack authoritative revocation proof. `[E-SEC][NOT_PROVEN]`
- Explicit production rollout approval has not been given for this checkpoint. `[NOT_PROVEN]`

## Staging deployments

- API deployment: `0210b4b7-226f-48b1-b679-8173cc3b792b`, status `SUCCESS`, image `sha256:4f143e12b11e5c7df60a1643a5ef5153678e36ca4227195c6c157a612053ee2f`. `[E-DEPLOY][STAGING_RUNTIME_PROVEN]`
- Worker deployment: `a0305952-30a9-4d38-9709-2fe72473ee6e`, status `SUCCESS`, image `sha256:3fd7826fa0bcd1830dffe0e9a0c65c756df54943a880496b533f588c0b8040e6`. `[E-DEPLOY][STAGING_RUNTIME_PROVEN]`
- Worker queue: `ai_orchestration`, concurrency `1`, ready hostname `celery@celery-eff92b30`. `[E-LOG][STAGING_RUNTIME_PROVEN]`
- API health: HTTP `200`, body `{"status":"ok","version":"0.2.0"}`. `[E-API][STAGING_RUNTIME_PROVEN]`
- Vercel preview: `dpl_GpGdtjf1TpmxcPMAtpbKCENCdGLN`, target `preview`, status `Ready`. `[E-DEPLOY][AUTHENTICATED_UI_PROVEN]`

## Safety

Orders created by AI in the measured staging runs: `0`. Live writes: `0`. Production mutations in this remediation: `0`. `[E-CANARY][STAGING_RUNTIME_PROVEN]`

`PRODUCTION_MUTATIONS_BEFORE_APPROVAL=0`

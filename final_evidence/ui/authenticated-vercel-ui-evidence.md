# Scalpyn authenticated Vercel UI evidence

Status: `AUTHENTICATED_UI_DEPLOYMENT_PROVEN`.

Deployment under test:

- Vercel project: `scalpyn`
- Deployment ID: `dpl_GpGdtjf1TpmxcPMAtpbKCENCdGLN`
- Target: `preview`
- Status: `Ready`
- URL: `https://scalpyn-qmyj2f8jy-ricardovasconcelos-1177s-projects.vercel.app`
- Backend: isolated Railway environment `systemic-ai-staging-20260807`
- Protected route check: `/intelligence-runs` returned HTTP `200` through Vercel automation access.

Authenticated browser proof:

- Login completed with the staging canary account; no credential value was captured.
- The `Intelligence Runs` list rendered `53 records` at capture time.
- Run `e5cbaac3-f25a-418b-b606-c3dc8e6019b7` displayed a durable execution trace, `SHADOW_ONLY`, runtime `langgraph`, provider canary `disabled`, configured/effective `fake/fake-analysis-v1`, prompt `systemic-multimodule@2.0.0`, dataset lineage, bundle lineage, tool/memory counts, cost surface, interrupts and `GRAPH_COMPLETED`.
- The page visibly displayed `LIVE WRITE: DENIED` and `CHECKPOINT STRICT`.

Human-gate proof:

- Synthetic run: `24146319-eab7-4fb4-9829-0bbc44f0dc58`.
- Before the first approval, the button existed and was enabled.
- Immediately after the first click, the button was disabled.
- A second submit attempt was rejected by the UI control.
- The next interrupt was rejected, the run completed, duplicate event keys remained `0`, and orders created remained `0`.

Cross-tenant proof:

- A disabled synthetic tenant and real foreign run `9bfc6fa0-4ef4-410b-8b3f-1db18a07d4e7` were created only for this staging test.
- From the authenticated canary tenant, the deployment returned HTTP `404` for the foreign run's `/context`, `/interrupts`, and `/timeline` endpoints.
- The foreign run ID did not render in the DOM.
- The synthetic tenant, request, and run were then removed; remaining counts were `0|0|0`.

Security handling:

- Browser logout returned to `/login` with `Sign In` visible.
- Browser sessions were finalized and the automation runtime holding the password was reset.
- Generated `.env.local` files were removed.
- Screenshot: `final_evidence/ui/intelligence-runs-authenticated-redacted.jpg`.

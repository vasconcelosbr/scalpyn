# Authenticated UI evidence

Status: `PROVEN_STAGING_PREVIEW`.

- Vercel project: `scalpyn`.
- Protected preview deployment: `dpl_6kzMikShU243HmJX2HKHwpgAaXuU` (`READY`).
- Route verified after authenticated login: `/intelligence-runs`.
- Selected run: `a6a0b87c-64d2-4039-a2cf-b4d139afa1c8`.
- The screen rendered the full timeline, all three interrupt/resume stages, `SHADOW_ONLY`, runtime `langgraph`, enabled staging entrypoints, disabled real-provider canary, strict checkpoints and `live write: denied` `[authenticated browser verification]`.
- The preview backend was the isolated Railway staging API. Four tenant-scoped canary runs were visible at verification time `[authenticated browser verification]`.
- The only browser-console error was an unrelated Google GSI/FedCM token retrieval warning; the LangGraph route and API requests completed `[browser console]`.

The temporary Vercel automation bypass used for verification was revoked. Deployment protection remains enabled. This proves staging UI behavior, not the production alias.

## Production deployment

Status: `PROVEN_PRODUCTION_AUTHENTICATED`.

Deployment `dpl_EHYzvWjfaPU6MVEtbzgPmQ8YXwsh` is `READY` and aliased to `https://scalpyn.vercel.app`. After the user completed login, the authenticated browser rendered `/intelligence-runs` for `Ricardo T.` with the `Admin` role `[authenticated browser verification]`.

- The production navigation contains `Intelligence Runs`, and the page heading and orchestration ledger rendered successfully `[authenticated browser verification]`.
- The visible authority envelope reports runtime `langgraph`, entrypoints `disabled`, provider canary `disabled`, `checkpoint strict` and `live write: denied` `[authenticated browser verification]`.
- The page reports `0 records` and `Nenhuma execução registrada`, consistent with the production database reconciliation `[authenticated browser verification]`.
- No application-origin console error was observed. One `InvalidNodeTypeError` originated from the `vercel.live` feedback overlay bundle; the Scalpyn page and authenticated content remained rendered `[browser console]`.

No password was read, inferred, reset or submitted. No form, execution, configuration or live-trading control was activated during verification.

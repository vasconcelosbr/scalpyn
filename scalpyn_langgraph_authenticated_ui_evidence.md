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

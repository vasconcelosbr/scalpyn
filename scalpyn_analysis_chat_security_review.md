# Analysis Chat Security Review

- Tenant derives only from the access token; body tenant is not accepted.
- Cross-tenant conversation and parent access returned 404/404 [API].
- Graph authority is fixed to `ANALYSIS_ONLY`.
- Frozen mode has no tools. Initial read-only mode has one `NONE` tool. Proposal validators are `NONE`.
- User text cannot populate the allowlist or authority fields.
- Assistant content is rendered as plain text; no dangerous HTML rendering path was added.
- Checkpoint state rejects secret-bearing keys and stores bounded IDs/summaries rather than raw datasets.
- Provider-disabled failure is typed `PROVIDER_BLOCKED`, transport false, reservation released.
- No real provider or production mutation was authorized.

Open items: authenticated interactive UI proof, API rate-limit integration with the project's canonical limiter, complete child-run creation with a fresh dataset/bundle, and full end-to-end integration tests against ephemeral PostgreSQL/Redis.

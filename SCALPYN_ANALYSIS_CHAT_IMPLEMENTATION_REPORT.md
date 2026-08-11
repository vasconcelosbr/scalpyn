# Scalpyn Analysis Chat — Implementation Report

## Verdict

`PARTIALLY_IMPLEMENTED_CHAT_RUNTIME_NOT_PROVEN`

The frozen chat, read-only refresh, provider-blocked path, SSE/reconnect, summary, proposal gates, tenant isolation and zero-mutation controls were proven in staging. The complete runtime seal is withheld because confirmed child analysis currently fails safe with `CHILD_ANALYSIS_REQUIRES_FRESH_DATASET_AND_BUNDLE` instead of creating a new run. Authenticated browser interaction was not proven; the protected preview route itself returned HTTP 200 through the Vercel CLI.

## Implemented

- Additive Alembic revision `157_analysis_chat` [query].
- Separate conversation/message/evidence records with retention-safe `RESTRICT` lineage.
- Nullable canonical request links: `request_kind`, `conversation_id`, `message_id`, `parent_analysis_run_id`.
- Immutable graph `analysis-chat-v1@1.0.0`, hash `c1753398733152b7ce78556bd02f09e6ccbc03d67247c6fda992e689d82961c2` [query].
- Four approved immutable prompts, listed in `scalpyn_analysis_chat_prompt_registry.json`.
- Tenant-safe REST API, authenticated SSE and `Last-Event-ID` reconnect.
- Governed modes: frozen, bounded read-only, child confirmation and proposal draft.
- Budget reservation created at turn acceptance; reconciled on fake success or released on pre-transport block.
- UI panel rendered only for eligible completed runs and persisted results.

## Defects found by staging canaries

1. Run acquisition referenced `final_state` before execution. Fixed and covered by regression.
2. Tail nodes regressed a completed message to `STREAMING`. Fixed and covered by regression.
3. Conversation-scoped graph thread collided with the unique run thread constraint on the second turn. Fixed with a deterministic per-message checkpoint thread beneath the canonical conversation thread.
4. Read-only tool IDs were not copied to the message. Fixed; staging returned one persisted tool-call ID [API].
5. Human-gated turns lacked a reservation until after the interrupt. Fixed so every accepted turn is immediately auditable.

## Deliberate stops

- Real provider: not called.
- Production: not deployed and no flags enabled.
- Child run: not fabricated from the parent snapshot; remains a typed limitation.
- Candidate/Shadow/live proposal application: not performed.

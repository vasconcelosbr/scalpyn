# Analysis Chat Architecture

The terminal Intelligence Run remains immutable. A conversation points to its parent run/result and owns `analysis-chat:<conversation_id>`. Each message creates a distinct request, job, budget reservation and graph run. The graph run receives a collision-free UUID5 checkpoint thread derived from the conversation thread and message ID.

Frozen mode reads only persisted parent result/evidence. Read-only mode uses a strict single-tool allowlist in the initial rollout. Child and proposal modes interrupt before progression. Proposal validation uses Global Risk and Strategies read-only validators and stops at a second human gate. No route owns live-write authority.

Canonical final state is PostgreSQL. SSE replays persisted graph events after `Last-Event-ID`; tokens are emitted as bounded coalesced events. The UI renders assistant content as plain text.

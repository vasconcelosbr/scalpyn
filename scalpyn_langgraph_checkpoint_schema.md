# LangGraph checkpoint schema

## Boundary

- Dedicated PostgreSQL schema: `langgraph_runtime` `[config: migration 148]`.
- Setup is one-shot through `python -m app.ai_orchestration.langgraph.bootstrap_checkpointer`; request and startup paths never call `setup()` `[code]`.
- Runtime uses `AsyncPostgresSaver`; `InMemorySaver` appears only in tests `[test]`.
- `search_path` is fail-closed to `langgraph_runtime, public` `[code]`.
- Strict serializer uses `JsonPlusSerializer(allowed_msgpack_modules=None)` and requires `LANGGRAPH_STRICT_MSGPACK=true` `[config]`.

## Canonical application tables

| Table | Purpose | Tenant boundary |
|---|---|---|
| `ai_graph_definitions` | Immutable approved graph versions and hashes | Global approved registry |
| `ai_graph_runs` | Request, job, lease, server thread and terminal state | `tenant_id` plus canonical request |
| `ai_graph_interrupts` | Human decisions, actor and protected edit policy | `tenant_id` plus run |
| `ai_graph_events` | Idempotent timeline | `tenant_id` plus run |
| `ai_graph_runtime_metadata` | Setup version evidence | Secret-free metadata only |

## State allowlist

Checkpoint state carries IDs, hashes and bounded JSON. It rejects key names containing credentials, authorization values, cookies, DSNs, passwords, secrets and bearer/access/refresh tokens `[test: test_checkpoint_state_contains_no_secrets]`. Token counts such as `tokens_input` are allowed because they are usage facts, not credentials.

## Retention and privacy

No automatic deletion is scheduled `[config]`. Administrative operations are exposed by `checkpoint_admin.py`: tenant-scoped list, metadata inspection and deletion. Deletion requires an active admin/superuser, explicit policy approval ID, reason and `--execute-delete`; canonical graph audit rows are retained `[code]`.

| Classification | Examples | Checkpoint policy |
|---|---|---|
| `PUBLIC_INTERNAL` | graph key, version, node name | Allowed |
| `TENANT_CONFIDENTIAL` | canonical IDs, evidence references, bounded analysis output | Allowed within server-generated thread |
| `SECRET` | provider key, JWT, password, database URL | Prohibited |
| `PROHIBITED_IN_CHECKPOINT` | raw credentials, cookies, authorization headers, uncontrolled objects | Rejected before write |

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| migration head=`148_langgraph_runtime` | `[test] alembic heads` | `148_langgraph_runtime (head)` |
| canonical tables=`5` | `[config: migration 148]` | five explicit `op.create_table` calls |

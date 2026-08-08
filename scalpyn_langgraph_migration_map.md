# LangGraph migration map

`147_systemic_ai_foundation -> 148_langgraph_runtime` `[config: Alembic]`.

Migration `148_langgraph_runtime` is additive. It creates the dedicated checkpoint schema and five canonical graph tables, seeds four immutable graph definitions, adds constraints/indexes and installs an approved-definition immutability trigger. It does not alter live trading configuration, profile pointers, model champions or orders.

Offline SQL was generated in `LANGGRAPH_MIGRATION_148_OFFLINE.sql` and the code registry was compared with migration seed rows. The comparison returned `registry_migration_match=4` `[test]`.

Rollback drops only graph canonical tables, the graph immutability trigger/function and the dedicated checkpoint schema. Because `DROP SCHEMA ... CASCADE` deletes checkpoint history, rollback requires a backup and an explicit retention decision.

## Deployment order

1. `[procedure]` backup/restore proof;
2. `[procedure]` `alembic upgrade head`;
3. `[procedure]` one-shot checkpointer bootstrap;
4. `[procedure]` API;
5. `[procedure]` isolated `ai_orchestration` worker;
6. `[procedure]` frontend;
7. `[procedure]` flags remain false until health and reconciliation.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| seeded definitions=`4` | `[test] registry_migration_match` | `registry_migration_match=4` |
| offline SQL bytes=`21888` | `[command] Get-Item` | `Length 21888` |

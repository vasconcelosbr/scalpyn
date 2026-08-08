# Scalpyn LangGraph evidence ledger

| Claim | Evidence | Status |
|---|---|---|
| Exact primary dependencies installed | `backend/requirements.txt`, PyPI verification, pip dry-run report | PROVEN LOCAL |
| Complete transitive lock with hashes | `backend/requirements-langgraph.lock`, `--require-hashes` dry run | PROVEN LOCAL |
| SBOM generated | `scalpyn_langgraph_sbom.cdx.json` | PROVEN LOCAL |
| Dedicated migration head | `alembic heads` returned `148_langgraph_runtime (head)` | PROVEN LOCAL |
| Registry equals migration seed | comparison returned `registry_migration_match=4` | PROVEN LOCAL |
| Strict state rejects secrets | mandatory named test passed | PROVEN LOCAL |
| Restart does not repeat completed node | MemorySaver crash/resume unit test passed | PROVEN LOCAL ONLY |
| PostgreSQL restart recovery | staging crash test | NOT YET VERIFIED |
| Full backend collection no CatBoost import crash | `1573 tests collected` | PROVEN LOCAL |
| Critical suite | `85 passed` | PROVEN LOCAL |
| Frontend production route | Next production build lists `/intelligence-runs` | PROVEN LOCAL |
| Authenticated UI | staging screenshot | NOT YET VERIFIED |
| AsyncPostgresSaver operational | staging migration/bootstrap/run | NOT YET VERIFIED |
| Fake analysis canary | isolated staging | NOT YET VERIFIED |
| Real provider/model canary | paid analysis-only request | NOT AUTHORIZED |
| Regenerative SHADOW_ONLY cycle | isolated staging with three interrupts | NOT YET VERIFIED |
| Production deployment | post-checkpoint only | NOT EXECUTED |
| No live mutation | flags default false and no prod action | PROVEN THROUGH CHECKPOINT |

## Numeric evidence

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| `langgraph=1.2.9` | `[config: requirements]` | `langgraph==1.2.9` |
| `checkpoint-postgres=3.1.0` | `[config: requirements]` | `langgraph-checkpoint-postgres==3.1.0` |
| `psycopg=3.3.4` | `[config: requirements]` | `psycopg[binary,pool]==3.3.4` |
| critical pass=`85` | `[test]` | `85 passed` |
| collection=`1573` | `[test]` | `1573 tests collected` |
| frontend pass=`23` | `[test]` | `pass 23` |
| cost=`NÃO DISPONÍVEL` | `[ABERTO]` | real provider canary not authorized |

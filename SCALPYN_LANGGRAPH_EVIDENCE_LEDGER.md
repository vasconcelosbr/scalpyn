# Scalpyn LangGraph evidence ledger

| Claim | Evidence | Status |
|---|---|---|
| Exact dependencies and hash lock | requirements, resolution report, SBOM and `--require-hashes` dry run | PROVEN LOCAL |
| Scoped dependency security | 40-package `pip-audit` after upgrade to checkpoint-postgres 3.1.2 | PROVEN LOCAL |
| Dedicated migration and immutable registry | migration head 148 and four approved definitions | PROVEN STAGING |
| AsyncPostgresSaver and strict serialization | setup, persisted checkpoints and staging executions | PROVEN STAGING |
| PostgreSQL recovery after worker restart | run `1ca42bb8-a0e5-4705-9423-4cc34df99a1c` resumed to completion | PROVEN STAGING |
| Fake analysis canary | run `340cbd93-18e3-44b2-aebd-3d0da8c5fc35` | PROVEN STAGING |
| Regenerative SHADOW_ONLY cycle | run `64e00a75-d41c-41dc-b396-ea49dc29ba70`, three resolved interrupts | PROVEN STAGING |
| Authenticated UI | protected Vercel preview `/intelligence-runs` | PROVEN STAGING |
| Real provider/model canary | paid analysis-only request | NOT AUTHORIZED |
| Spot invariant resolution | separate semantic decision and immutable configuration version | BLOCKED HUMAN DECISION |
| Global lint/full suite | recorded legacy failures | NOT GREEN |
| Production deployment | backup, migration, API, isolated worker and Vercel deployment | PROVEN CONTROLLED / FLAGS FALSE |
| Production authenticated UI | authenticated `/intelligence-runs`; runtime `langgraph`, entrypoints/provider canary disabled and live-write denied | PROVEN PRODUCTION |
| Live mutation | live-write denial, shadow-only authority and unchanged production | NOT EXECUTED |

## Numeric evidence

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| `langgraph=1.2.9` | `[config: requirements]` | `langgraph==1.2.9` |
| `checkpoint-postgres=3.1.2` | `[config: requirements]` | `langgraph-checkpoint-postgres==3.1.2` |
| `psycopg=3.3.4` | `[config: requirements]` | `psycopg[binary,pool]==3.3.4` |
| mandatory pass=`39` | `[test]` | `39 passed, 1 warning` |
| critical pass=`87` | `[test]` | `87 passed, 1 warning` |
| collection=`1573` | `[test]` | `1573 tests collected; 0 collection errors` |
| frontend pass=`23` | `[test]` | `pass 23; fail 0` |
| schema checks=`32` | `[staging]` | `schema_ok=true; checked_count=32; missing=[]` |
| latest analysis events=`18` | `[staging]` | `event_count=18` |
| latest regenerative events=`24` | `[staging]` | `event_count=24` |
| resolved interrupts=`3` | `[staging]` | `CANDIDATE_APPROVAL, SHADOW_EVIDENCE, FINAL_DECISION: RESOLVED` |
| fake-provider cost=`USD 0` | `[staging]` | `cost_usd="0"` |
| real-provider cost=`NÃO DISPONÍVEL` | `[ABERTO]` | separate cost approval not granted |
| backup size=`2491960498` bytes | `[backup]` | full archive read `OK` |
| backup restore entries=`1086` | `[backup]` | `restore_list_entries=1086` |
| production schema checks=`32` | `[production]` | `schema_ok=true; checked_count=32; missing=[]` |
| production graph runs=`0` | `[query]` | `graph_runs=0` |
| production live profiles=`0` | `[query]` | `live_trading_profiles=0` |

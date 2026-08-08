# Scalpyn Systemic AI Foundation — Evidence Ledger

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| production pre-rollout head=`146_l3_1200_validation` | `[query]` production probe | `alembic_head: ["146_l3_1200_validation"]` |
| local head=`147_systemic_ai_foundation` | `[test]` Alembic | `147_systemic_ai_foundation (head)` |
| systemic staging tables=`19` | `[query]` final staging probe | nineteen `ai_*`/regenerative tables returned |
| reversible staging cycles=`3` | `[query]` migration commands | `upgrade 146→147`; `downgrade 147→146`; `upgrade 146→147` |
| synthetic canary table groups=`8` | `[query]` staging canary | each of bundle,dataset,job,resolution,request,result,tool-audit,usage=`1` |
| external provider calls=`0` | `[query]` staging canary | `"external_provider_calls": 0` |
| fake adapter calls=`1` | `[query]` staging canary | `"fake_adapter_calls": 1` |
| live-trading before/after=`0/0` | `[query]` staging canary | `live_trading_enabled_before: 0; after: 0` |
| Auto-Pilot before/after=`0/0` | `[query]` staging canary | `autopilot_enabled_before: 0; after: 0` |
| critical backend=`63 passed` | `[test]` focused pytest | `63 passed in 6.35s` |
| named foundation=`27 passed` | `[test]` focused pytest | `27 passed in 1.06s` |
| frontend unit=`23 passed; 0 failed` | `[test]` Node runner | `tests 23; pass 23; fail 0` |
| changed component lint=`0` | `[test]` ESLint | process exit `0` |
| frontend pages=`43` | `[test]` Next build | `Generating static pages ... (43/43)` |
| offline SQL=`25037 characters` | `[test]` Alembic offline capture | `OFFLINE_SQL_CHARS=25037` |
| graph nodes=`15461` | `[test]` graph update | `15461 nodes` |
| graph edges=`21968` | `[test]` graph update | `21968 edges` |
| graph communities=`1175` | `[test]` graph update | `1175 communities` |
| global frontend lint=`433` | `[test]` prior repository lint | `433 problems (371 errors, 62 warnings)` |
| backend global collection=`146 items` | `[test]` prior repository pytest | `collected 146 items`, then import-time `SystemExit` |
| active production profiles=`53` | `[query]` pre-rollout read-only aggregate | `active=53; live_trading_enabled=false; auto_pilot_enabled=false; is_shadow_only=false` |
| physical duplicate groups=`38` | `[query]` pre-rollout read-only audit | `physical_duplicate_groups=38` |
| extra duplicate rows=`44` | `[query]` pre-rollout read-only audit | `extra_rows=44` |
| conflicting event groups=`787` | `[query]` pre-rollout read-only audit | `conflicting_event_groups=787` |
| conflicting affected rows=`2049` | `[query]` pre-rollout read-only audit | `affected_rows=2049` |
| Spot invariant | `[config: spot_engine]` production JSON | `"selling":{"never_sell_at_loss":false}; "enable_ai_consultation":false` |
| Profile Intelligence model | `[config: profile_intelligence]` production JSON | `"ai_model":"claude-fable-5"; "ai_provider":"anthropic"` |

| approved production prompts=`4` | `[query]` post-rollout probe | four versioned keys at `1.0.0` |
| other systemic production rows=`0` | `[query]` post-rollout probe | all eighteen non-prompt systemic tables=`0` |
| production health=`200` | `[query]` HTTP | `/api/health` returned `200` |
| schema checked=`32`; missing=`0` | `[query]` HTTP | `schema_ok=true; checked_count=32; missing=[]` |
| Railway deployments=`5 SUCCESS` | `[query]` deployment list | API, compute, structural, execution and beat=`SUCCESS` |
| Vercel critical routes=`6/6 HTTP 200` | `[query]` protected deployment curl | six listed routes returned `200` |
| production profiles after rollout=`53` | `[query]` post-rollout probe | `active=53; live=0; autopilot=0; shadow_only=0` |

Authenticated UI screenshot remains `NÃO DISPONÍVEL`: both available browser sessions redirected to `/login` and no credentials were supplied or inspected.


# Scalpyn Systemic Multi-Module Evidence Ledger

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| módulos registrados=10 | [code: module_registry] | `count=10` |
| tools registradas=81 | [code: domain_tools] | `count=81` |
| tool evidence staging=10 | [query: staging canary] | `tool_evidence_count=10` |
| checkpoints analysis=28 | [query: checkpoint saver] | `checkpoint_count=28` |
| checkpoints Run A=23 | [query: checkpoint saver] | `checkpoint_count=23` |
| checkpoints Run B=23 | [query: checkpoint saver] | `checkpoint_count=23` |
| checkpoints Run C=23 | [query: checkpoint saver] | `checkpoint_count=23` |
| ordens criadas=0 | [query: staging orders] | `orders_created_during_canary=0` |
| custo fake=0 USD | [query: ai_usage] | `cost_usd=0` |
| focused backend=76 passed | [query: JUnit] | `failures=0; errors=0` |
| full backend=1606 collected | [query: JUnit] | `failures=68; errors=12; skipped=0` |
| frontend tests=23 passed | [query: node test] | `pass 23; fail 0` |
| lint errors=0 | [query: eslint] | `435 problems (0 errors, 435 warnings)` |
| npm high/critical=0/0 | [query: npm audit] | `high=0; critical=0; low=1` |
| production graph runs=0 | [query: Q-LG-004] | `SCALPYN_SYSTEMIC_MODULES_PREIMPLEMENTATION_REVALIDATION.md: data=[]; row_count=0` |
| production live profiles=0/53 | [query: Q-LG-025] | `live_trading_enabled 0 total 53` |
| production Auto-Pilot profiles=0/53 | [query: Q-LG-025] | `auto_pilot_enabled 0 total 53` |
| production orders=0 | [query: Q-LG-026] | `row_count=0` |

Every other absent number is `NÃO DISPONÍVEL` or explicitly `NÃO VERIFICADO`.

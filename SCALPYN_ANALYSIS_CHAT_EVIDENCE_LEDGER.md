# Analysis Chat Evidence Ledger

| NUMBER / CLAIM | ORIGIN | LITERAL SOURCE VALUE |
|---|---|---|
| migration head | [query] staging `alembic_version` | `157_analysis_chat` |
| backend focused tests | [command] pytest | `155 passed, 1 warning` |
| frontend tests | [command] Node test runner | `28 pass, 0 fail` |
| preview route | [command] `vercel curl` | `HTTP/1.1 200 OK`; `X-Matched-Path: /intelligence-runs` |
| frozen evidence refs | [API] staging assistant payload | `10` |
| frozen new tools | [API] staging assistant payload | `0` |
| read-only tool calls | [API] staging assistant payload | `1` |
| stream events | [API] SSE capture | `41` events; `2` token events; `2` completed events |
| reconnect replay | [API] SSE capture | `0` replayed IDs |
| tenant denial | [API] cross-tenant JWT | conversation `404`; parent run `404` |
| budgets | [query] staging reservation aggregate | reconciled `5`; released `2`; tokens `0`; cost `0E-8` |
| cancellation | [query] staged interrupted turn | job `CANCELLED`; graph `CANCELLED`; reservation `RELEASED` |
| proposal validators | [query] tool audit | Global Risk `NONE/COMPLETED/NO_DATA`; Strategies `NONE/COMPLETED/NO_DATA` |
| orders | [query] since first chat canary | `0` |
| trades | [query] since first chat canary | `0` |
| position lifecycle | [query] since first chat canary | `0` |
| trade decisions | [query] since first chat canary | `0` |
| profile candidates | [query] since first chat canary | `0` |
| profiles updated | [query] since first chat canary | `0` |
| ML promotions | [query] since first chat canary | `0` |

All canary counts use the literal staging start `2026-08-11T19:57:18.893672+00:00` [query]. No statistical claim is made; these are runtime reconciliation counts.

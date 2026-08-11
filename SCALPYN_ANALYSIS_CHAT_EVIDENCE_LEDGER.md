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
| production API deploy | [Railway] deployment terminal state | `d3d27b24-b37a-4bae-9100-7d23e3e0c5f2`; `SUCCESS` |
| production worker deploy | [Railway] deployment terminal state | `4207770e-cc5d-41a0-845b-a56eec485276`; `SUCCESS` |
| production frontend deploy | [Vercel] deployment inspection | `dpl_DytQCMEQudmtyDMfu6ZJJjMajS3g`; `READY`; `production` |
| production migration head | [query] `alembic_version` | `157_analysis_chat` |
| production critical schema | [command] schema auditor/startup gate | `32/32 present` |
| production chat rows | [query] conversation/message/evidence counts | `0`; `0`; `0` |
| production chat AI requests | [query] `ai_requests.request_kind LIKE 'ANALYSIS_CHAT%'` | `0` |
| production runtime configs | [query] active `ai_analysis_chat_runtime` | `0` |
| production provider flags | [Railway config] API and worker | fake `false`; real `false` |
| production UI route | [HTTP] `https://scalpyn.vercel.app/intelligence-runs` | `200`; deployment `dpl_DytQCMEQudmtyDMfu6ZJJjMajS3g` |
| protected API route | [HTTP] frontend proxy and Railway API without JWT | `401`; `401` |

All canary counts use the literal staging start `2026-08-11T19:57:18.893672+00:00` [query]. No statistical claim is made; these are runtime reconciliation counts.

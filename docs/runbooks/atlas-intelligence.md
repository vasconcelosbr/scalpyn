# ATLAS Intelligence — isolated SHADOW_ONLY release

## Scope and source parity

Portal `/atlas` extends the existing `pool-archive-dashboard` Railway service, its login and private archive. All new code lives under `integrations/pool-dashboard`. No Scalpyn backend/router/JWT/trading/profile/worker/database/frontend contract changes. The daily collector and its original source snapshot remain unchanged. The archive at `/` continues to work.

The supplied ATLAS specification is the acceptance baseline, especially sections 8–15. The runtime uses Claude Managed Agents via the pinned official Python SDK. No toolset, web tools, skills, MCP servers, vaults, credentials, or multiagent delegation are granted to those agents. Independent deterministic orchestration submits one session per agent; QUANT runs Python only. Nothing in the portal starts inference.

## Portal and API

* `/atlas`: editorial synthesis, source coverage, top findings, upcoming events and specialist status.
* `/atlas/agentes` and `/atlas/agentes/{code}`: all eight stages, full outputs including rejected output, model/prompt hashes/session identifiers, timestamps, usage and limitations.
* `/atlas/noticias`, `/atlas/evidencias/{id}`: immutable feed evidence versions, source excerpts (not copied full articles), citations and finding associations.
* `/atlas/ativos`, `/atlas/ativos/{assetId}`: every observed watchlist member, risk/coverage, original indicators by timeframe and historical decision snapshot kept separate.
* `/atlas/decisoes`: final editorial conclusion and proposed directives. No approval/application button.
* `/atlas/historico`: every cycle, failed/blocked included; immutable published report JSON/Markdown and descriptive risk comparison.
* `/atlas/operacao`: source health, agent states, budget and concrete gaps.

Filters and selected run/report persist in URL. Empty, stale, partial, unavailable and rejected states are explicitly different. Text is rendered via DOM textContent; model HTML is never executed. The portal and authenticated `/api/v1/atlas` use the same stored report body and report_id. `/api/v1/atlas/openapi.json` is authenticated. Reports, agents, risks, asset risk history, directives, health, runs and evidence endpoints are read-only. Pagination uses stable ordering and explicit cursors.

Service clients are configured via `ATLAS_API_CLIENTS_JSON`: array of objects with `id`, SHA-256 `sha256`, and explicit scopes (`reports:read`, `risks:read`, `directives:read`, `ack:write`). No service consumer is provisioned by default. Never configure a plaintext token in that JSON. Session login is owner-only and cannot ACK. Ingestion keys and Scalpyn JWTs cannot authenticate ATLAS API reads. The public shell contains no data.

ACKs accept only shadow SIMULATED/REJECTED, require idempotency, reference a published nonexpired directive, and never call Scalpyn. No claim of an actual consumer simulation is made without feedback. An actual Scalpyn Risk Policy Engine remains outside this release.

## Runtime configuration

Existing volume, archive authentication and snapshot ingestion variables remain required.

| Variable | Meaning |
|---|---|
| `ATLAS_ENABLED` | `true` enables scheduler; otherwise off |
| `ANTHROPIC_API_KEY` | Backend secret, valid Managed Agents access required |
| `ATLAS_DAILY_BUDGET_USD` | Required explicit decimal daily budget; user authorized `5` |
| `ATLAS_HOUR_LOCAL` | Daily hour in America/Sao_Paulo; default `19` |
| `ATLAS_MODEL_HAIKU/SONNET/OPUS` | Defaults `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`; models/access verified before any session |
| `ATLAS_SESSION_TIMEOUT_SECONDS` | Default `900`; uncertainty stops subsequent sessions |
| `ATLAS_MAX_CONTEXT_BYTES` | Default `250000`; oversized contexts fail visibly, never silently truncate |
| `ATLAS_QUANT_MAX_AGE_SECONDS` | Explicit freshness ceiling, default `3600`; never applies to trading |
| `ATLAS_EVIDENCE_LOOKBACK_DAYS` | Default `7`; feed chronology, not a complete scheduled-event calendar |
| `ATLAS_MAX_PROPOSED_PENALTY/PRIORITY` | No default: proposals needing a numerical overlay fail validation unless bounds configured |
| `ATLAS_API_CLIENTS_JSON` | Optional hashed, scoped consumer tokens |
| `ATLAS_API_RATE_PER_MINUTE` | Per-consumer bound, default `60` |

Daily sequence: freeze archive/source inputs → QUANT → MACRO/GEO/SECURITY/TOKEN/MAPPER → AUDITOR → ATLAS → validate references/identities/policies → atomic publication. Missing key/models/budget produces a visible blocked cycle with actual QUANT output, not fabricated AI analysis. Model IDs in the specification are not assumed to be available to this account.

SQLite unique day and `(run_id,code)` prevent concurrent double execution across processes. Each stage is claimed durably BEFORE network submission; provider SDK retries are disabled. A crash/ambiguous response never starts a replacement session. Restart marks unfinished work INTERRUPTED. At most one replica owns the volume. No additional Railway service is necessary.

Budget is reserved before each session, split across seven model stages, and actual provider `usage.list_cost` is retained. Unknown metering never frees funds and blocks subsequent inference. **Provider limitation:** Managed Agents checks budget between model requests. A final request may exceed its session cap; the configured daily admission budget cannot guarantee a hard billing ceiling. Do not advertise this as an exact invoice cap. Infrastructure cost is separate and is not estimated. Account-level spend controls can provide a separate billing safeguard.

## Operator commands

Run inside the deployed dashboard container; never paste keys into command arguments or logs:

```sh
python -m atlas.runner --preflight
python -m atlas.runner
python -m atlas.runner --resume-blocked
```

The final command only resumes a configuration-blocked cycle where **no model stage ever started**, preserving frozen inputs and audit history. It cannot repeat completed/failed/uncertain inference. It is not exposed to the browser. Daily reads do not call Anthropic.

## Persistence, migrations and rollback

`/data/atlas.sqlite3` is separate from `/data/dashboard.sqlite3`. Additive migration `1` is transactional and tracked in `migrations`: cycles, agents, resources, reports, evidence, feedback, audit, operational. Original snapshots are referenced by exact SHA-256 and frozen into cycle inputs. Report hashes and chained audit hashes detect accidental changes. They are not a cryptographic proof against a privileged database administrator.

Before any future schema upgrade, take a SQLite online backup. There are no Scalpyn migrations. Rolling application code back leaves both databases and historical records intact; disable `ATLAS_ENABLED`, deploy the previous canonical archive release, retain the ATLAS volume data. No destructive down migration is run against historical reports. Logical rollback is removal of the additive module, with its independent database retained. Corrected reports require a new versioned publication implementation; this release never silently edits a published report.

## Concrete source gaps (must remain visible)

* Current integration supplies active persisted watchlist members, not an independently authenticated Pool membership snapshot. `pool_total`, pool_only/watchlist_only/matched remain null. Never claim complete Pool coverage.
* Project/chain/contract identities are not supplied. Asset-specific direct news associations cannot be verified by ticker alone and are rejected as DIRECT.
* FED and The Block connectors are fixed-origin RSS readers with bounded size, XML entity protection, no redirects and no automatic article fetching. Other specialist/official/calendar connectors in the specification do not yet have validated contracts. Feed failures remain UNAVAILABLE. Article excerpts/publication dates are source-reported, not independent truth.
* QUANT conserves input values and per-timeframe readings. Missing values stay null; no market-indicator recomputation, P&L simulation, causal conclusion or current-price claim is made.
* No report exists until a real provider execution passes validation. Failed/blocked/rejected stages are visible individually, and never promoted to latest. Last successful reports remain available with age/expiration.
* Forecast accuracy and false-positive rates require subsequent observations and labeled outcomes; they remain unavailable rather than invented.

## Verification

Run `python -m unittest discover -s integrations/pool-dashboard -v`, collector regression suite, `node --check integrations/pool-dashboard/static/atlas.js`, diff scope review and canonical release guard. Tests use explicit synthetic fixtures and fake providers; they never generate production reports. Validate real runtime source digest, authenticated navigation, filters, report/export parity and public denial after deployment. Real Managed Agents access and completed production sessions require separate runtime evidence, not just tests.

Official implementation references verified during development:
* https://platform.claude.com/docs/en/managed-agents/agent-setup
* https://platform.claude.com/docs/en/managed-agents/sessions
* https://platform.claude.com/docs/en/managed-agents/session-observability
* https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration

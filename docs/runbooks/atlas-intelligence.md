# ATLAS Intelligence — isolated SHADOW_ONLY release

## Scope and source parity

Portal `/atlas` extends the existing `pool-archive-dashboard` Railway service, its login and private archive. The portal code lives under `integrations/pool-dashboard`. The additive authenticated `/api/integrations/radar/watchlists/{watchlist_id}/pool-context` export reads the owned source Pool and all persisted membership rows in the same REPEATABLE READ READ ONLY transaction as the unchanged watchlist export. Existing JWT/assets/trading/profile/worker contracts remain unchanged. The daily collector and its original source snapshot remain unchanged. The archive at `/` continues to work.

The supplied ATLAS specification is the acceptance baseline, especially sections 8–15. Historical v1 cycles used Claude Managed Agents. New cycles use a single bounded Anthropic Messages request per predefined role through the pinned official SDK: Managed session caps can overshoot by one request and cannot enforce the operator dollar ceiling. This is an intentional execution adaptation to the stricter user budget; the same role/model/prompt, immutable outputs and audit stages remain. No toolset, web tools, skills, MCP servers, vaults, credentials, or multiagent delegation are granted to those agents. Independent deterministic orchestration submits one request per agent; QUANT runs Python only. Nothing in the portal starts inference.

## Portal and API

* `/atlas`: editorial synthesis, source coverage, top findings, upcoming events and specialist status.
* `/atlas/agentes` and `/atlas/agentes/{code}`: all eight stages, full outputs including rejected output, model/prompt hashes/session identifiers, timestamps, usage and limitations.
* `/atlas/noticias`, `/atlas/evidencias/{id}`: immutable feed evidence versions, source excerpts (not copied full articles), citations and finding associations.
* `/atlas/ativos`, `/atlas/ativos/{assetId}`: every active source Pool member (historical v1 cycles retain their watchlist universe), risk/coverage, original indicators by timeframe and historical decision snapshot kept separate.
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
| `ANTHROPIC_API_KEY` | Backend secret; model Messages access required for new cycles |
| `ATLAS_DAILY_BUDGET_USD` | Required explicit decimal daily budget; user authorized `5` |
| `ATLAS_HOUR_LOCAL` | Daily hour in America/Sao_Paulo; default `19` |
| `ATLAS_MODEL_HAIKU/SONNET/OPUS` | Defaults `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`; models/access verified before any session |
| `ATLAS_SESSION_TIMEOUT_SECONDS` | Default `900`; uncertainty stops subsequent sessions |
| `ATLAS_MAX_CONTEXT_BYTES` | Default `750000`; oversized contexts fail visibly, never silently truncate |
| `ATLAS_QUANT_MAX_AGE_SECONDS` | Explicit freshness ceiling, default `3600`; never applies to trading |
| `ATLAS_EVIDENCE_LOOKBACK_DAYS` | Default `7`; feed chronology, not a complete scheduled-event calendar |
| `ATLAS_MAX_PROPOSED_PENALTY/PRIORITY` | No default: proposals needing a numerical overlay fail validation unless bounds configured |
| `ATLAS_API_CLIENTS_JSON` | Optional hashed, scoped consumer tokens |
| `ATLAS_API_RATE_PER_MINUTE` | Per-consumer bound, default `60` |

Daily sequence: freeze archive/source inputs → QUANT → MACRO/GEO/SECURITY/TOKEN/MAPPER → AUDITOR → ATLAS → validate references/identities/policies → atomic publication. Missing key/models/budget produces a visible blocked cycle with actual QUANT output, not fabricated AI analysis. Model IDs in the specification are not assumed to be available to this account.

SQLite unique `(day,revision)` and `(run_id,code)` prevent concurrent double execution across processes. Each stage is claimed durably BEFORE network submission; provider SDK retries are disabled. A crash/ambiguous response never starts a replacement session. Restart marks unfinished work INTERRUPTED. At most one replica owns the volume. No additional Railway service is necessary.

Budget is shared across all revisions on the business day. Prior provider list costs (integer US cents) and conservative new usage calculations are counted together. Unknown started-session cost blocks later inference. New requests count input tokens first, reserve a margin, and bound output with `max_tokens`, without tools, premium routing, cache writes, retries or follow-ups. Prices come from required `ATLAS_MODEL_RATES_JSON` (input/output USD per million tokens); use the upper Haiku context tier. Actual tokens and configured rates are retained and labeled as calculated costs, not provider invoices. The allocation gives more room to the auditor and final synthesis; rounding is upward to cents. Review prices before model changes. Infrastructure and taxes are outside the model budget.

New required variables: `ATLAS_POOL_CONTEXT_URL` (exact readonly route on verified backend origin `https://scalpyn-production.up.railway.app`; avoids the Vercel proxy response-size limit without truncating or modifying the frontend), `ATLAS_SCALPYN_READ_KEY` (same narrow allowlisted service key, stored as a Railway secret), and `ATLAS_MODEL_RATES_JSON`. The dashboard now holds this read-only export key; it cannot authenticate existing JWT routes or mutations. No JWT/exchange credential is used for source collection. `ATLAS_MAX_OUTPUT_TOKENS` defaults to 24000. Full indicators remain in QUANT and the portal; editorial prompts explicitly project scalar metrics, identity and every reading timestamp/status to avoid an unbounded duplicate payload.

Pricing checked 2026-10-15: https://platform.claude.com/docs/en/about-claude/pricing . Reviewed configuration:
```json
{"claude-haiku-5-5":{"input":0.5,"output":2.5},"claude-sonnet-5-5":{"input":2,"output":10},"claude-opus-5-5":{"input":4,"output":20}}
```

## Operator commands

Run inside the deployed dashboard container; never paste keys into command arguments or logs:

```sh
python -m atlas.runner --preflight
python -m atlas.runner
python -m atlas.runner --resume-blocked
# Only after explicit operator authorization for an additional cycle:
python -m atlas.runner --revalidate-authorized
```

The resume command only resumes a configuration-blocked cycle where **no model stage ever started**, preserving frozen inputs and audit history. It cannot repeat completed/failed/uncertain inference. It is not exposed to the browser. Daily reads do not call Anthropic.

## Persistence, migrations and rollback

`/data/atlas.sqlite3` is separate from `/data/dashboard.sqlite3`. Additive migration `1` is transactional and tracked in `migrations`: cycles, agents, resources, reports, evidence, feedback, audit, operational. Original snapshots are referenced by exact SHA-256 and frozen into cycle inputs. Report hashes and chained audit hashes detect accidental changes. They are not a cryptographic proof against a privileged database administrator.

Before any future schema upgrade, take a SQLite online backup. There are no Scalpyn migrations. Rolling application code back leaves both databases and historical records intact; disable `ATLAS_ENABLED`, deploy the previous canonical archive release, retain the ATLAS volume data. No destructive down migration is run against historical reports. Logical rollback is removal of the additive module, with its independent database retained. Migration 2 takes an online backup at atlas.pre-revisions.sqlite3, rebuilds only the local cycles uniqueness constraint, and preserves foreign keys, inputs, agents and reports. The CLI authorized revision is idempotent (revision 1), while the scheduler only claims revision 0. Never restore the backup over newer history; retain both files on rollback. This release never silently edits a published report.

## Concrete source gaps (must remain visible)

* Authenticated source Pool membership is complete for the query snapshot, with active/inactive rows, pool_only/watchlist_only/matched reconciliation and market isolation. This does not prove complete risk coverage. Failed source contracts stop collection rather than fall back to a smaller watchlist universe.
* Gate public pair/currency metadata establishes exchange identity, issuer label, chain/address fields and status, not independent verification of every project. DIRECT requires exact verified asset references.
* Fixed-origin connectors: FED, The Block (single media report, never corroborated), SEC, ECB and BLS RSS; BEA and FOMC official calendars (date-only when time zone/hour unproven); OFAC official notices; Gate public announcement pagination and metadata; Solana official incidents. Source failures remain UNAVAILABLE; raw source records/excerpts and hashes are persisted. Gate announcement POST is a public read query, not a trading endpoint.
* Unlock/vesting schedules are not universally available from these public sources. Gate supply/migration announcements do not establish vesting. No paid DefiLlama/Tokenomist subscription is provisioned. These missing domains stay explicit; no invented dates, amounts, severity or NO_ALERT.
* Server computes reading ages/statuses. Calibrated editorial severity is not configured, so all new numeric/graded risk labels remain UNKNOWN. Literal quotes are required for findings. MAPPER accounts for every evidence ID, including OUT_OF_SCOPE/INSUFFICIENT. Partial data alone is not an audit veto; unsupported claims are.
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

## Authorized interrupted-request recovery

The operator explicitly authorized recovery of the first interrupted bounded MACRO request on 2026-10-15, still within the same daily USD 5 budget. `--recover-authorized` creates revision 2 only when revision 1 is INTERRUPTED and its sole started model stage is bounded MACRO. It reuses the exact frozen inputs and hash, preserves the interrupted rows, and charges the full prior `reserved_cents` against new admission capacity. This is an upper bound, not invented actual usage. Unknown Managed/session costs still block all inference. The CLI is idempotent and unavailable over HTTP. Subsequent failures do not imply permission for further retries.

The complete Pool payload increased memory demand. QUANT no longer duplicates indicator readings inside its Pool identity object; the original complete export remains frozen. Dashboard service memory ceiling is 1 GB; actual usage is metered separately from the model budget. Retain unknown/stale distinctions and check runtime memory during collection, inference and authenticated UI reads.

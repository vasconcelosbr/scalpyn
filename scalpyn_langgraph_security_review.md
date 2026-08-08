# LangGraph security review

## Result

The new runtime is fail-closed and limited to `ANALYSIS_ONLY`, `PROPOSAL_ONLY`, `CANDIDATE_ONLY` and `SHADOW_ONLY`. `LIVE_WRITE` is absent from the authority enum and explicitly denied in API capabilities, candidate/change-set payloads and invariant validation `[code]`.

Controls implemented:

- canonical tenant checks on runs, requests, model resolution, datasets, bundles, events and interrupts `[code]`;
- server-generated UUID thread IDs that are not returned by the API `[code]`;
- strict checkpoint serialization and secret-key rejection `[test]`;
- dedicated PostgreSQL schema with no checkpointer setup in request/startup paths `[code]`;
- provider invocation only after authorization, prompt, dataset, bundle, quality and read-only-tool gates `[test]`;
- no provider credential in graph state or event payloads `[test]`;
- protected lineage identifiers cannot be changed by human edits `[test]`;
- external tracing disabled by default `[config]`;
- checkpoint deletion disabled by default and guarded by approved retention policy `[code]`.

## Dependency advisory

The initial scan reported `CVE-2026-71433` for `langgraph-checkpoint-postgres==3.1.0` `[security scan]`. Before the production checkpoint, PyPI published `3.1.2`; the exact pin, hash lock, dependency resolution report and SBOM were updated, and the critical suite was repeated `[external verification][test]`.

Classification: `REMEDIATED_BY_UPGRADE`. The scoped audit of all 40 hash-locked LangGraph dependencies reports no known vulnerabilities. Scalpyn uses `AsyncPostgresSaver`, not the Store API, and canonical tenant authorization remains outside checkpoint namespaces.

## Open security risks

- The global frontend dependency audit reports ten existing findings from `npm ci` `[command]`; no forced major update was applied.
- Global ESLint remains non-green because of legacy code debt `[test]`.
- The real-provider canary has not run because separate paid-cost approval is absent `[ABERTO]`.
- The full backend environment contains legacy dependency findings outside the isolated LangGraph lock; production deploy must continue using the repository's normal dependency-remediation process `[ABERTO]`.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| scoped Python audit findings=`0` | `[security scan] pip-audit` | `No known vulnerabilities found` |
| scoped Python packages=`40` | `[security scan] pip-audit` | `dependencies=40` |
| npm findings=`10` | `[command] npm ci` | `10 vulnerabilities (2 low, 8 high)` |
| critical dependency/runtime tests=`87` | `[test] pytest` | `87 passed` |

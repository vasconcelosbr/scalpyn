# LangGraph security review

## Result

The new runtime is fail-closed and limited to `ANALYSIS_ONLY`, `PROPOSAL_ONLY`, `CANDIDATE_ONLY` and `SHADOW_ONLY`. `LIVE_WRITE` is absent from the authority enum and is explicitly denied in API capabilities, candidate/change-set payloads and invariant validation `[code]`.

Controls implemented:

- canonical tenant check on run, request, model resolution, dataset, bundle, event and interrupt `[code]`;
- server-generated UUID thread IDs not returned by the API `[code]`;
- strict checkpoint serializer and secret-key rejection `[test]`;
- dedicated PostgreSQL schema and no setup in request/startup paths `[code]`;
- provider work only after authorization, prompt, dataset, bundle, quality and read-only-tool gates `[test]`;
- no provider credential enters state or event payload `[test]`;
- human edits cannot change protected lineage identifiers `[test]`;
- no external tracing enabled by default `[config]`;
- checkpoint retention deletion disabled by default and guarded by admin policy approval `[code]`.

## Dependency advisory

`pip-audit` reports `CVE-2026-71433` for `langgraph-checkpoint-postgres==3.1.0` `[security scan]`. The advisory describes prefix matching in `PostgresStore`/`AsyncPostgresStore` namespace search. Scalpyn uses `AsyncPostgresSaver`, never the Store API, and does not use checkpoint namespaces as the tenant authorization boundary. The fixed version named by the advisory, `3.1.1`, was not available from PyPI at validation time; PyPI still exposed `3.1.0` as latest `[external verification]`.

Classification: `NOT_APPLICABLE_TO_CURRENT_USAGE_WITH_MITIGATIONS`, not “no finding.” Revalidate availability immediately before production approval. If `3.1.1` becomes available and remains compatible, update the exact pin, hashes, SBOM and staging evidence before production.

## Open security risks

- Global frontend dependency audit reports ten existing findings from `npm ci` `[command]`; no forced update was applied.
- Global ESLint remains non-green because of legacy code debt `[test]`.
- Real-provider canary has not run because paid-cost approval is absent `[ABERTO]`.
- Authenticated staging UI evidence is pending staging deployment `[ABERTO]`.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| Python audit findings=`1` | `[security scan] pip-audit` | `Found 1 known vulnerability in 1 package` |
| npm findings=`10` | `[command] npm ci` | `10 vulnerabilities (2 low, 8 high)` |
| critical checkpoint-secret tests=`1` | `[test] named mandatory test` | `test_checkpoint_state_contains_no_secrets PASSED` |

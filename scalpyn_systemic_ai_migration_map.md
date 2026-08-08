# Scalpyn Systemic AI — Migration Map

## Chain

- Base revision: `146_l3_1200_validation` `[query]`.
- New revision and only local head: `147_systemic_ai_foundation` `[test]`.
- Strategy: additive schema only; no historical row backfill and no synthetic tenant or lineage IDs.

## Core additions

The revision creates approved prompt versions with an immutability trigger; provider aliases and resolutions; configuration bundles; canonical dataset snapshots; requests, jobs, results, usage, budgets and tool-call audits; and the regenerative ledger tables for hypotheses, change sets, regeneration runs, experiments, decision memory, context/mutation fingerprints and causal evidence.

## Legacy bridges

- Nullable tenant/request fields are added to Shadow analysis jobs, Profile AI reviews and suggestions.
- Lease, attempt, retry and terminal-error fields are added to Shadow analysis jobs.
- A nullable configuration-bundle reference is added to Shadow trades.
- No legacy row receives an invented identifier.

## Rollback and staging proof

The downgrade removes only objects and bridge columns created by revision 147, in reverse dependency order. It does not change business data.

The isolated Railway staging database was built from a production schema-only archive with no rows and without restoring extensions. The verified sequence was:

1. stamp at `146_l3_1200_validation` `[query]`;
2. upgrade to `147_systemic_ai_foundation` `[query]`;
3. verify all new tables and bridge columns `[query]`;
4. downgrade to revision 146 and verify their removal `[query]`;
5. upgrade again to revision 147 and run the persistent canary `[query]`.

The approved-prompt trigger rejected a content mutation inside a rolled-back savepoint `[query]`. Offline generation also passed with `25037` characters `[test]`.

## Production checkpoint

Before production promotion, the read-only probe reported revision `146_l3_1200_validation`, both required extensions installed, no systemic tables and no revision-147 bridge columns `[query]`. Production migration is recorded separately after execution; no downgrade is authorized there.


# Scalpyn Multi-Module Data Contracts

## CanonicalAnalysisDataset

The existing immutable dataset record is extended with `origin_module`, `module_context_refs`, and `context_manifest`. It freezes source labels, event/outcome contracts, time window, filters, exclusions, row/query/dataset hashes, bundle reference, quality state, and findings. It never physically merges every domain table.

## ConfigurationBundle

The bundle keeps profile, score, risk, strategy, Spot, exit, feature, label, ML lane/model, and market-regime lineage. Social Score remains temporal evidence in dataset/context, not an ML feature or configuration mutation.

## AnalysisContextManifest

The manifest records requested/consulted modules, tools, freshness, quality, evidence IDs, conflicts, and vetoes. Typed tool evidence is persisted separately with an output hash and tenant/request scope.

## Recommendation envelope

Structured recommendations declare target module/path, rationale, evidence, expected effect, risks, confidence, side-effect class, approval requirement, and rollback. `LIVE_WRITE` is rejected; ML, Risk and Strategies mutations are rejected; Spot authority remains blocked.

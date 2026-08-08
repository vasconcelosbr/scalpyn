# Canonical event identity reconciliation

`event_reconciliation.py` groups observations by the explicit event identity and preserves every source observation in a sorted history with a content hash. Conflicting outcomes receive `BLOCK_CONFLICTING_OUTCOMES`; no canonical outcome is selected without an actor-attributed human resolution `[test]`.

When a resolution exists, the selected source must be present in preserved history. The result stores actor, reason and timestamp while retaining both the selected and non-selected source observations. This is lossless reconciliation, not deletion or overwrite.

The dataset quality gate blocks proposal/candidate authority for unresolved conflicts. Analysis-only inspection remains possible so operators can diagnose the conflict without granting mutation authority.

Production conflict rows were not mutated in this implementation `[NÃO REALIZADO]`. A data migration requires a separately reviewed mapping of the historical identities found by the prior forensic audit.

## Ledger de evidências

| NÚMERO REPORTADO | ORIGEM | VALOR LITERAL DA FONTE |
|---|---|---|
| conflict tests=`2` | `[test] mandatory LangGraph suite` | unresolved block and history-preservation tests passed |

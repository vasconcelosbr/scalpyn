# Shadow trades immutable reconciliation

`audit_shadow_trades_immutable_signature.py` compares a read-only isolated
backup restore with the current database for the same historical cutoff. It
never writes to either database and exits `2` when a primary key or immutable
value differs.

Run it only as an explicit release/audit gate because both databases are read
in full through server-side cursors:

```powershell
python backend/scripts/audit_shadow_trades_immutable_signature.py `
  --baseline-url $env:PI_RESTORE_DATABASE_URL `
  --current-url $env:DATABASE_PUBLIC_URL `
  --cutoff 2026-07-21T23:51:37Z `
  --output work/pi-shadow-signature.json
```

The URLs must refer to different database instances; the baseline must be a
restore in an isolated environment, never production.

## Column classification

| Group | May mature? | Writers / reason | Consumers |
|---|---:|---|---|
| identity, source, symbol, user, creation time | no | all INSERT writers in `shadow_trade_service.py` | UI, loaders, audit |
| native feature snapshot, extractor/schema/hash, event/snapshot IDs | no | INSERT writers; protected by migration `133_native_feature_capture` | official L1/L3 ML loaders |
| profile/ranking/watchlist snapshot at entry | no | pipeline/Strategy Lab INSERT writers | lineage and lane segregation |
| status, outcome, exit, PnL, MAE/MFE, holding time | yes | `shadow_trade_monitor.py` | labels and portfolio UI |
| TTT and timeout analytics | yes | `ttt_analyzer.py`, `shadow_timeout_analyzer.py` | offline label analysis |
| lineage confidence/source/resolved timestamp | yes for legacy only | `shadow_lineage_backfill.py`; new native rows are exact at INSERT | lineage audit |
| priority/orchestrator fields | yes | `decision_orchestrator.py` | ranking diagnostics |
| eligibility/label resolution | yes | monitor/auditors after outcome maturity | official dataset gate |

The immutable signature is the release gate. The lifecycle signature is
informational and the report includes per-column counts so every normal
maturation can be attributed to a writer.

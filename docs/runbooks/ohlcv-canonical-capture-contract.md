# OHLCV canonical capture contract

## Scope

`gate_ohlcv_canonical_v1` is the canonical Gate.io closed-candle contract for
`1m`, `5m` and `30m`. Its closed destination is `ohlcv`; the current mutable
candle remains in `ohlcv_live`. Canonical writes must preserve
`is_closed = true`, use the contract version as provenance and never overwrite
an existing `(exchange, market_type, symbol, timeframe, time)` row.

The database constraint `ck_ohlcv_closed` prevents `is_closed` values other
than true in `ohlcv`. It proves the value persisted in the column; it does not
retroactively prove that historical candles were fetched after Gate finalized
them.

## `ingested_at` forensic boundary

Migration `214_ohlcv_canonical_cutover` added `ingested_at` with
`DEFAULT clock_timestamp()`. PostgreSQL therefore populated pre-existing rows
with the migration time, because the original ingestion timestamp did not
exist. For those rows, `ingested_at` is a schema-backfill timestamp and must
not be interpreted as collection latency or original arrival time.

`ingested_at` is forensically meaningful only when all of the following hold:

- `capture_contract_version = 'gate_ohlcv_canonical_v1'` or a documented later
  canonical contract;
- the row was written by that contract rather than relabelled retroactively;
- the analysis separately checks the contract's `valid_from`.

Any latency or chronology query using `ingested_at` must apply those filters.
Legacy rows marked `legacy_collect_market_data_untrusted` remain readable but
must be excluded from such calculations.

## Reproducible R1 invariants

Run `backend/scripts/audit_r1_invariants.py` with an explicit timezone-aware
`--cutoff` chosen from a settled historical instant. The script rejects a
future cutoff. Terminal membership is bounded by `completed_at`, the time when
the terminal database transition was written, rather than the earlier market
event in `label_resolved_at`. Reusing the same cutoff must reproduce the same
population unless a protected terminal field was changed.

## Cutover ordering restriction

When Git auto-deploy and CLI snapshot deployment coexist, the new contract must
be active before the old writer stops. Stopping the writer first can create a
gap between the code transition and the persisted `valid_from`. A future
cutover plan must prove this order explicitly; this runbook does not automate
or authorize a new cut.

## Deferred security remediation

Status: `DEFERRED_BY_OPERATOR` on `2026-09-05`.

A local investigation scratchpad contains temporary Python scripts with direct
production PostgreSQL DSNs. No credential value belongs in this repository or
in an audit report. Before the next security acceptance, an authorized
operator must:

1. rotate the affected production database credential;
2. remove the temporary local scripts through a recoverable, scoped procedure;
3. verify that no live process still depends on the old credential;
4. run a repository/history secret scan and record only redacted findings.

This item is deliberately documented but not executed in the R1 audit-contract
correction. It remains an open security action and does not silently become a
passed gate.
